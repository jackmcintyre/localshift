#!/usr/bin/env python3
"""replay_plan_baseline.py — is the planner's output the same as it was?

The regression gate for changes that are meant to change nothing: dead-code
removal, refactors, renames. Every tracked capture under ``simulations/`` is
replayed through the real planning path and the plan it produces is compared
with a checked-in baseline (``simulations/plan-baseline/``). Any difference is
printed and the run exits non-zero.

Why not the test suite alone: the planning path catches broadly and carries
on. A removed constructor argument once made the inline optimizer raise on
every cycle; the facade logged a warning, the suite stayed green, and one
scenario assertion was the only thing that noticed. A plan that silently
stops existing, or silently changes, shows up here as a diff.

What is replayed
----------------
``ComputationEngine.compute_derived_values``: the same call the coordinator
makes each cycle. That runs the load forecast, the price signals, the slot
builder (through the facade's injected class reference), the DP solve and the
mode assignment, and it is what writes the fields compared below. Nothing is
called on the planner directly, so a break anywhere on that path is seen.
Each cycle is given a decision grant, as the state machine gives one when the
price or window context moves, so the commanded mode recorded is the one the
facade commits for that plan rather than the mode the capture started in.

Each capture runs under two arms, so both families of planner behaviour are
covered:

  captured   the configuration recorded with the capture
  strict     the same inputs with strict demand-window entry and the live
             tuning of 2026-09-08 (``STRICT_OVERRIDES``). The hard target
             floor, the urgency window, the pre-charge backstops and the
             next-window tail only run with strict entry.

What is compared
----------------
Per arm: whether a plan exists at all, the commanded mode, every scalar the
planner publishes (the result and summary dicts, less the fields listed in
``VOLATILE_KEYS``), a set of derived coordinator fields, any swallowed
failure the run tripped (``tests/swallow_tripwire.py``), and one row per plan
slot: action, reason, projected SOC, grid energy, and the slot inputs the
planner saw (solar, load, prices). Solve times, cycle ids and timestamps of
the run itself are excluded.

Why the output is stable
------------------------
A run is byte-identical to the last one, in any capture order:

* every clock the planning path reads is pinned to the capture's own instant
  (``pinned_clock``), including two it reads directly off the wall clock, and
  Home Assistant's default time zone and the process's own are pinned to the
  live system's, Australia/Sydney;
* every (capture, arm) gets a fresh engine and fresh coordinator data, so
  nothing carries over between replays;
* a fresh ``CoordinatorData`` reports a forecast horizon of 0.0 hours, which
  live never sees (the previous cycle's value carries over) and which
  collapses the cheap-price percentile and applies the short-horizon penalty
  at full strength. The horizon is seeded from the price feed and the cycle
  is run twice; the second pass is the one recorded;
* floats are rounded to six places and keys are sorted before writing.

Usage
-----
  uv run scripts/replay_plan_baseline.py                 compare, exit 1 on any diff
  uv run scripts/replay_plan_baseline.py --write         regenerate the baseline
  uv run scripts/replay_plan_baseline.py --only 2026-10-12 --only sunny-day
  uv run scripts/replay_plan_baseline.py --list

A PR that is meant to leave plans alone runs the first form and must exit 0.
A PR that means to change plans runs ``--write`` and commits the baseline
diff, which is then the reviewable statement of what changed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from homeassistant.util import dt as dt_util  # noqa: E402

from custom_components.localshift.computation_engine import (  # noqa: E402
    ComputationEngine,
)
from custom_components.localshift.const import BatteryMode  # noqa: E402
from custom_components.localshift.coordinator import CoordinatorData  # noqa: E402
from custom_components.localshift.pricing.types import ForecastSlot  # noqa: E402
from tests import swallow_tripwire  # noqa: E402

SIMULATIONS = REPO_ROOT / "simulations"
BASELINE_DIR = SIMULATIONS / "plan-baseline"
CAPTURE_DIRS = ("scenarios", "replay", "replay-nodw", "replay-1119", "replay-1120")

FORMAT_VERSION = 1
# The zone the live system runs in, and so the one every replay runs in.
LIVE_TIME_ZONE = ZoneInfo("Australia/Sydney")
_REAL_DATETIME = datetime

# Strict demand-window entry on the live tuning of 2026-09-08 (read off
# sensor.localshift_optimizer_summary and the number entities that day; the
# same values scripts/replay_no_dw.py replays under).
STRICT_OVERRIDES: dict[str, Any] = {
    "battery_target": 95,
    "minimum_target_soc": 5,
    "allow_dw_entry_under_target": False,
    "cheap_price_percentile": 50,
    "max_pre_charge_price": 0.20,
    "min_cycle_saving": 0.25,
    "min_hold_saving": 0,
    "export_price_margin": 0.10,
    "switching_penalty": 0.08,
    "switching_penalty_per_kwh": 0.4,
    "target_penalty": 0.1,
    "precharge_runway_margin_min": 15,
    "demand_window_start": "15:00:00",
    "demand_window_end": "21:00:00",
}
STRICT_SWITCHES: dict[str, bool] = {
    "allow_dw_entry_under_target": False,
    "demand_window_block": True,
}
ARMS: dict[str, tuple[dict[str, Any], dict[str, bool]]] = {
    "captured": ({}, {}),
    "strict": (STRICT_OVERRIDES, STRICT_SWITCHES),
}

# Options every replay starts from, before the capture's own overrides.
BASE_OPTIONS: dict[str, Any] = {
    "battery_target": 90,
    "cheap_price_percentile": 40,
    "cheap_price_deadband": 0.02,
    "max_pre_charge_price": 0.30,
    "forecast_lookahead_hours": 8,
    "demand_window_start": "18:00:00",
    "demand_window_end": "22:00:00",
    "load_weight_recent": 0.7,
    "minimum_target_soc": 15,
    "spike_price_percentile": 80,
    "optimizer_enabled": True,
    "optimizer_control_mode": "active",
}
BASE_SWITCHES: dict[str, bool] = {
    "automation_enabled": True,
    "spike_discharge_enabled": True,
    "demand_window_block": True,
    "manual_override": False,
    "allow_dw_entry_under_target": False,
    "spike_discharge_conservative": False,
}
ENTITY_IDS: dict[str, str] = {
    "teslemetry_soc": "sensor.tesla_powerwall_soc",
    "teslemetry_operation_mode": "sensor.tesla_powerwall_operation_mode",
    "teslemetry_backup_reserve": "sensor.tesla_powerwall_backup_reserve",
    "teslemetry_grid_power": "sensor.tesla_powerwall_grid_power",
    "teslemetry_load_power": "sensor.tesla_load_power",
    "teslemetry_solar_power": "sensor.tesla_solar_power",
    "solcast_today": "sensor.solcast_today",
    "solcast_tomorrow": "sensor.solcast_tomorrow",
    "general_forecast": "sensor.amber_general_forecast",
    "feed_in_forecast": "sensor.amber_feed_in_forecast",
}

# Published by the planner but a property of the run, not of the plan.
VOLATILE_KEYS = frozenset({
    "cycle_id",
    "cycle_timestamp_iso",
    "computed_at",
    "solve_time_seconds",
})
# Echoes of the inputs: large, and already pinned by the capture file itself.
ECHO_KEYS = frozenset({"config_options"})

# Derived coordinator fields worth holding still alongside the plan. Read with
# getattr, so a field that stops existing reads as None and shows as a diff.
DATA_FIELDS = (
    "demand_window_active",
    "effective_cheap_price",
    "planner_threshold_used",
    "cheap_charge_stop_price",
    "solar_can_reach_target",
    "solar_can_reach_target_in_dw",
    "boost_charge_needed",
    "boost_charge_active",
    "optimizer_precharge_backstop_active",
    "planner_export_floor_pct",
    "negative_fit_window_open",
    "forecast_horizon_hours",
    "debug_plan_mode_pending",
    "debug_mode_source",
    "debug_forecast_slot_found",
    "optimizer_last_apply_status",
    "optimizer_apply_plan",
)

# One row per plan slot, in this column order.
DECISION_COLUMNS = (
    "timestamp_iso",
    "slot_interval_minutes",
    "action",
    "reason_code",
    "predicted_soc_pct",
    "grid_import_kwh",
    "grid_export_kwh",
    "solar_kwh",
    "consumption_kwh",
    "buy_price",
    "sell_price",
    "export_floor_pct",
)

FLOAT_PLACES = 6


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------


def _price_slot(entry: dict[str, Any]) -> ForecastSlot:
    start = datetime.fromisoformat(entry["start_time"])
    if "duration" in entry:
        minutes = entry["duration"]
    else:
        end = datetime.fromisoformat(entry["end_time"])
        minutes = int((end - start).total_seconds() / 60)
    is_spike = entry.get("is_spike", False)
    if "spike_status" in entry:
        is_spike = entry["spike_status"] == "spike"
    return ForecastSlot(
        start_time=start,
        duration=minutes,
        per_kwh=entry["per_kwh"],
        is_spike=is_spike,
        source_type="test",
        estimate=entry.get("estimate"),
    )


def _coordinator_data(payload: dict[str, Any]) -> CoordinatorData:
    data = CoordinatorData()
    data.soc = payload.get("soc", 50.0)
    data.operation_mode = payload.get("operation_mode", "autonomous")
    data.backup_reserve = payload.get("backup_reserve", 50)
    data.grid_power_kw = payload.get("grid_power_kw", 0.0)
    data.load_power_kw = payload.get("load_power_kw", 0.5)
    data.solar_power_kw = payload.get("solar_power_kw", 0.0)
    data.battery_power_kw = payload.get("battery_power_kw", 0.0)
    data.general_price = payload.get("general_price", 0.25)
    data.feed_in_price = payload.get("feed_in_price", 0.08)
    data.price_spike = payload.get("price_spike", False)
    data.manual_override = payload.get("manual_override", False)
    data.solcast_today = payload.get("solcast_today", [])
    data.solcast_tomorrow = payload.get("solcast_tomorrow", [])
    data.general_forecast = [
        _price_slot(e) for e in payload.get("general_forecast", [])
    ]
    data.feed_in_forecast = [
        _price_slot(e) for e in payload.get("feed_in_forecast", [])
    ]
    data.target_reached_today = payload.get("target_reached_today", False)
    data.forecast_ready = True
    data.forecast_status = "ready"
    return data


def _seed_horizon(data: CoordinatorData, payload: dict[str, Any]) -> None:
    """Give the first pass the horizon live would carry over from the last cycle."""
    feed = payload.get("general_forecast") or []
    if not feed:
        return
    first = datetime.fromisoformat(feed[0]["start_time"])
    last = datetime.fromisoformat(feed[-1]["start_time"])
    if "duration" in feed[-1]:
        last_minutes = float(feed[-1]["duration"])
    elif "end_time" in feed[-1]:
        last_end = datetime.fromisoformat(feed[-1]["end_time"])
        last_minutes = (last_end - last).total_seconds() / 60.0
    else:
        last_minutes = 30.0
    span_h = (last - first).total_seconds() / 3600.0 + last_minutes / 60.0
    data.forecast_horizon_hours = max(0.0, span_h)


def _hass(payload: dict[str, Any]) -> MagicMock:
    hass = MagicMock()
    hass.states = MagicMock()
    hass.data = {}

    def get_state(entity_id: str) -> MagicMock:
        state = MagicMock()
        state.state = "unknown"
        if "solcast_today" in entity_id:
            state.attributes = {"detailedForecast": payload.get("solcast_today", [])}
        elif "solcast_tomorrow" in entity_id:
            state.attributes = {"detailedForecast": payload.get("solcast_tomorrow", [])}
        elif "general_forecast" in entity_id:
            state.attributes = {"forecasts": payload.get("general_forecast", [])}
        elif "feed_in_forecast" in entity_id:
            state.attributes = {"forecasts": payload.get("feed_in_forecast", [])}
        else:
            state.attributes = {}
        return state

    hass.states.get = get_state
    return hass


class _PinnedDatetimeMeta(type):
    """Keeps ``isinstance(x, datetime)`` true for ordinary datetimes."""

    def __instancecheck__(cls, instance: Any) -> bool:
        return isinstance(instance, _REAL_DATETIME)

    def __subclasscheck__(cls, subclass: type) -> bool:
        return issubclass(subclass, _REAL_DATETIME)


def _pinned_datetime(instant: datetime) -> type:
    class PinnedDatetime(_REAL_DATETIME, metaclass=_PinnedDatetimeMeta):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            if tz is None:
                # Naive "local" time: the capture's own wall clock, not the
                # wall clock of whichever machine is replaying it.
                return instant.replace(tzinfo=None)
            return instant.astimezone(tz)

    return PinnedDatetime


@contextmanager
def _process_time_zone(name: str) -> Iterator[None]:
    """Run the block with the C library's local zone set to ``name``."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


@contextmanager
def pinned_clock(instant: datetime) -> Iterator[None]:
    """Make every clock the planning path reads say ``instant``.

    Three clocks are read on that path, and a replay that leaves any of them on
    the wall clock gives an answer that depends on when it was run:

    * ``dt_util.now`` (most of the engine);
    * ``dt_util.utcnow``, which stamps the plan's ``cycle_timestamp_iso``;
    * ``datetime.now(UTC)`` called directly, which the safety gate measures
      that stamp's age against and the apply path uses to find the current
      slot. Every localshift module that has imported the ``datetime`` class
      gets a pinned stand-in, wherever those calls live now or move to.

    Home Assistant's default time zone is pinned as well, to the zone the live
    system runs in (``LIVE_TIME_ZONE``). It is a process-wide global that
    anything may set, and it reaches the plan in one place: the slot builder
    converts slot times to ``ha_timezone`` to decide which fall inside the
    demand window, and falls back to this default when a capture carries no
    ``ha_timezone`` of its own. Two captures do not (the inverted-night
    scenarios); left on the suite's UTC default they are replayed in a state
    live can never be in, and plan a 7 kWh grid import that Sydney does not.
    The process's own time zone is pinned to the same zone, for the length of
    the replay. ``SlotBuilder.build_slots`` converts "now" with a bare
    ``astimezone()``, which is the zone of whichever machine is running, and
    when the price feed does not cover "now" that value becomes the timestamp
    of the synthetic first slot. One capture is in that state
    (replay-1120/2026-10-04T213040), and without this pin its slot 0 reads
    ``21:30:00+11:00`` on a laptop in Sydney and ``10:30:00+00:00`` on CI.
    """
    pinned = _pinned_datetime(instant)
    with ExitStack() as stack:
        stack.enter_context(patch.object(dt_util, "DEFAULT_TIME_ZONE", LIVE_TIME_ZONE))
        dt_util.get_default_time_zone.cache_clear()
        stack.callback(dt_util.get_default_time_zone.cache_clear)
        stack.enter_context(patch("homeassistant.util.dt.now", return_value=instant))
        stack.enter_context(
            patch("homeassistant.util.dt.utcnow", return_value=instant.astimezone(UTC))
        )
        for name, module in list(sys.modules.items()):
            if (
                name.startswith("custom_components.localshift")
                and module is not None
                and vars(module).get("datetime") is _REAL_DATETIME
            ):
                stack.enter_context(patch.object(module, "datetime", pinned))
        stack.enter_context(_process_time_zone(LIVE_TIME_ZONE.key))
        yield


def replay(scenario: dict[str, Any], arm: str) -> dict[str, Any]:
    """Replay one capture under one arm; return the canonical record of its plan."""
    arm_options, arm_switches = ARMS[arm]
    payload = scenario["input"]
    test_time = datetime.fromisoformat(payload["test_time"])

    options = dict(BASE_OPTIONS)
    options.update(scenario.get("config_overrides", {}))
    options.update(arm_options)
    switches = dict(BASE_SWITCHES)
    switches.update(scenario.get("switch_states", {}))
    switches.update(arm_switches)

    entry = MagicMock()
    entry.data = {}
    entry.options = options

    data = _coordinator_data(payload)
    start_mode = (
        BatteryMode(payload["active_mode"]) if "active_mode" in payload else None
    )
    if start_mode is not None:
        data.active_mode = start_mode
    _seed_horizon(data, payload)

    engine = ComputationEngine(
        _hass(payload),
        entry,
        lambda key: ENTITY_IDS.get(key, f"sensor.{key}"),
        lambda key: switches.get(key, False),
    )
    # No load history: the forecast falls back to the recent-load figure, as it
    # does in every other replay harness here.
    history = engine._history_fetcher
    history._historical_load_cache = {}
    history._historical_load_sample_counts = {}
    history._historical_load_source = "none"
    history._recent_load_1hr_kw = payload.get("load_power_kw", 0.5)

    hits = swallow_tripwire.begin()
    try:
        with pinned_clock(test_time):
            for _ in range(2):
                # Pass 1 warms the fields live carries over from the previous
                # cycle; pass 2 is the one recorded. Both start from the mode
                # the capture was in and hold a decision grant, so the mode
                # the facade commits is the plan's, not the frozen start mode.
                if start_mode is not None:
                    data.active_mode = start_mode
                data.mode_decision_allowed = True
                engine.compute_derived_values(data)
    finally:
        swallow_tripwire.end(hits)

    return _record(data, hits)


def _record(data: CoordinatorData, hits: list[Any]) -> dict[str, Any]:
    result = getattr(data, "optimizer_result", None)
    summary = getattr(data, "optimizer_summary", None) or {}
    decisions = getattr(data, "optimizer_decisions", None) or []
    mode = getattr(data, "active_mode", None)
    skip = VOLATILE_KEYS | ECHO_KEYS
    record = {
        "optimizer_result_present": result is not None,
        "commanded_mode": getattr(mode, "value", mode),
        "slot0_action": decisions[0].get("action") if decisions else None,
        "swallowed_failures": sorted({hit.key for hit in hits}),
        "result": {k: v for k, v in (result or {}).items() if k not in skip},
        "summary": {k: v for k, v in summary.items() if k not in skip},
        "data": {name: getattr(data, name, None) for name in DATA_FIELDS},
        "decisions": [[d.get(col) for col in DECISION_COLUMNS] for d in decisions],
    }
    return _canonical(record)


def _canonical(value: Any) -> Any:
    """Reduce a value to stable JSON: sorted keys, rounded floats, no objects."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        rounded = round(value, FLOAT_PLACES)
        return 0.0 if rounded == 0 else rounded
    if isinstance(value, dict):
        return {str(k): _canonical(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(v) for v in value), key=str)
    if isinstance(value, datetime):
        return value.isoformat()
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float)):
        return enum_value
    return str(value)


# --------------------------------------------------------------------------
# Baseline files
# --------------------------------------------------------------------------


def tracked_captures() -> list[Path]:
    """Capture files this baseline covers: tracked ones only.

    Some capture directories hold gitignored days that exist on one machine and
    not another; a baseline over those could not be checked anywhere else.
    Outside a git checkout every capture on disk is used.
    """
    patterns = [f"simulations/{name}/*.json" for name in CAPTURE_DIRS]
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "ls-files", "--", *patterns],  # noqa: S607
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        paths = [REPO_ROOT / line for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        paths = []
    if not paths:
        paths = [
            p for name in CAPTURE_DIRS for p in (SIMULATIONS / name).glob("*.json")
        ]
    return sorted(paths)


def baseline_path(capture: Path, baseline_dir: Path = BASELINE_DIR) -> Path:
    return baseline_dir / capture.parent.name / capture.name


def build(capture: Path) -> dict[str, Any]:
    """Replay one capture under every arm."""
    scenario = json.loads(capture.read_text())
    return {
        "format": FORMAT_VERSION,
        "capture": capture.relative_to(REPO_ROOT).as_posix(),
        "decision_columns": list(DECISION_COLUMNS),
        "arms": {arm: replay(scenario, arm) for arm in ARMS},
    }


def dumps(document: dict[str, Any]) -> str:
    """Serialize a baseline document: readable, and one plan slot per line."""
    lines = ["{"]
    lines.append(f' "format": {json.dumps(document["format"])},')
    lines.append(f' "capture": {json.dumps(document["capture"])},')
    lines.append(f' "decision_columns": {json.dumps(document["decision_columns"])},')
    lines.append(' "arms": {')
    arms = document["arms"]
    for arm_index, (arm, record) in enumerate(arms.items()):
        lines.append(f"  {json.dumps(arm)}: {{")
        keys = [k for k in record if k != "decisions"]
        for key in keys:
            body = json.dumps(record[key], indent=1, sort_keys=True)
            body = body.replace("\n", "\n   ")
            lines.append(f"   {json.dumps(key)}: {body},")
        rows = record["decisions"]
        if rows:
            lines.append('   "decisions": [')
            for row_index, row in enumerate(rows):
                comma = "," if row_index < len(rows) - 1 else ""
                lines.append(f"    {json.dumps(row)}{comma}")
            lines.append("   ]")
        else:
            lines.append('   "decisions": []')
        lines.append("  }" + ("," if arm_index < len(arms) - 1 else ""))
    lines.append(" }")
    lines.append("}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


def _diff_value(path: str, old: Any, new: Any, out: list[str]) -> None:
    if old == new:
        return
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            if key not in new:
                out.append(f"{path}.{key}: {json.dumps(old[key])} -> (removed)")
            elif key not in old:
                out.append(f"{path}.{key}: (absent) -> {json.dumps(new[key])}")
            else:
                _diff_value(f"{path}.{key}", old[key], new[key], out)
        return
    out.append(f"{path}: {json.dumps(old)} -> {json.dumps(new)}")


def _diff_decisions(
    old: list[list[Any]], new: list[list[Any]], columns: list[str], out: list[str]
) -> None:
    if len(old) != len(new):
        out.append(f"decisions: {len(old)} slots -> {len(new)} slots")
    changed = 0
    for index, (before, after) in enumerate(zip(old, new, strict=False)):
        if before == after:
            continue
        changed += 1
        parts = [
            f"{name} {json.dumps(b)} -> {json.dumps(a)}"
            for name, b, a in zip(columns, before, after, strict=False)
            if b != a
        ]
        if len(before) != len(after):
            parts.append(f"{len(before)} columns -> {len(after)} columns")
        stamp = before[0] if before else "?"
        out.append(f"slot {index} ({stamp}): " + "; ".join(parts))
    if changed:
        out.append(f"{changed} of {min(len(old), len(new))} plan slots differ")


def diff(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Human-readable differences between a baseline document and a fresh one."""
    out: list[str] = []
    for key in ("format", "capture", "decision_columns"):
        if expected.get(key) != actual.get(key):
            out.append(
                f"{key}: {json.dumps(expected.get(key))} -> {json.dumps(actual.get(key))}"
            )
    columns = list(actual.get("decision_columns") or DECISION_COLUMNS)
    old_arms, new_arms = expected.get("arms", {}), actual.get("arms", {})
    for arm in sorted(set(old_arms) | set(new_arms)):
        if arm not in new_arms or arm not in old_arms:
            out.append(f"[{arm}] arm {'removed' if arm in old_arms else 'added'}")
            continue
        lines: list[str] = []
        old, new = old_arms[arm], new_arms[arm]
        for key in sorted((set(old) | set(new)) - {"decisions"}):
            _diff_value(key, old.get(key), new.get(key), lines)
        _diff_decisions(
            old.get("decisions", []), new.get("decisions", []), columns, lines
        )
        out.extend(f"[{arm}] {line}" for line in lines)
    return out


def compare(capture: Path, baseline_dir: Path = BASELINE_DIR) -> list[str]:
    """Differences between one capture's baseline and a fresh replay of it."""
    path = baseline_path(capture, baseline_dir)
    if not path.exists():
        return [f"no baseline at {path.relative_to(REPO_ROOT)}: run --write"]
    return diff(json.loads(path.read_text()), build(capture))


def orphans(captures: list[Path], baseline_dir: Path = BASELINE_DIR) -> list[Path]:
    """Baseline files whose capture is no longer tracked."""
    wanted = {baseline_path(c, baseline_dir) for c in captures}
    found = set(baseline_dir.glob("*/*.json")) if baseline_dir.exists() else set()
    return sorted(found - wanted)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

MAX_LINES_PER_CAPTURE = 40


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _write(captures: list[Path], stale: list[Path]) -> int:
    for capture in captures:
        path = baseline_path(capture)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = dumps(build(capture))
        if not path.exists() or path.read_text() != text:
            path.write_text(text)
            print(f"wrote    {_rel(path)}")
    for path in stale:
        path.unlink()
        print(f"removed  {_rel(path)}")
    print(f"baseline covers {len(captures)} capture(s) x {len(ARMS)} arms")
    return 0


def _check(captures: list[Path], stale: list[Path], full_diff: bool) -> int:
    failed = 0
    for capture in captures:
        lines = compare(capture)
        if not lines:
            continue
        failed += 1
        print(f"DIFFERS  {_rel(capture)}")
        shown = lines if full_diff else lines[:MAX_LINES_PER_CAPTURE]
        for line in shown:
            print(f"    {line}")
        if len(shown) < len(lines):
            print(f"    ... {len(lines) - len(shown)} more (--full-diff)")
    for path in stale:
        failed += 1
        print(f"ORPHAN   {_rel(path)}: its capture is gone")
    if failed:
        print(
            f"\n{failed} problem(s) across {len(captures)} capture(s). If the "
            "change was meant to alter plans, regenerate with --write and commit "
            "the diff; otherwise the change is not behaviour-neutral."
        )
        return 1
    print(f"plans identical: {len(captures)} capture(s) x {len(ARMS)} arms")
    return 0


def quiet_logging() -> None:
    """Keep the planner's narration off the terminal.

    It logs every solve at INFO and WARNING, and none of that is the result,
    so the package's records are absorbed here instead of reaching stderr.
    Swallowed failures are not lost with them: the tripwire notices those at
    the log call itself (``tests/swallow_tripwire.py``), not from records.
    """
    package_logger = logging.getLogger("custom_components.localshift")
    package_logger.addHandler(logging.NullHandler())
    package_logger.propagate = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare planner output with the checked-in replay baseline."
    )
    parser.add_argument(
        "--write", action="store_true", help="regenerate the baseline instead"
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="TEXT",
        help="only captures whose path contains TEXT (repeatable)",
    )
    parser.add_argument(
        "--list", action="store_true", help="list the captures and exit"
    )
    parser.add_argument(
        "--full-diff", action="store_true", help="do not truncate a capture's diff"
    )
    args = parser.parse_args(argv)
    quiet_logging()

    everything = tracked_captures()
    captures = [
        c
        for c in everything
        if not args.only or any(text in c.as_posix() for text in args.only)
    ]
    if not captures:
        print("no captures matched", file=sys.stderr)
        return 2
    if args.list:
        for capture in captures:
            print(_rel(capture))
        return 0

    # A partial run cannot tell an orphan from a capture it was told to skip.
    stale = [] if args.only else orphans(everything)
    if args.write:
        return _write(captures, stale)
    return _check(captures, stale, args.full_diff)


if __name__ == "__main__":
    raise SystemExit(main())

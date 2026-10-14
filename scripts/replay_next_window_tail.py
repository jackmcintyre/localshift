#!/usr/bin/env python3
"""replay_next_window_tail.py — the plan tail before a window past the horizon.

Issue #1033. The price feed is 24 hours long, so in the afternoon the horizon
ends at or just short of tomorrow's demand window, and the plan's tail drained
to the SOC floor because nothing asked for charge before it. The tail is now
planned a second time against that window (``DPPlanner._plan_next_window_tail``).

Each captured instant runs twice through
``ComputationEngine.compute_derived_values`` on byte-identical inputs:

  before   this branch with the tail re-plan switched off
  after    this branch

Both arms are this branch. ``before`` is NOT the code on main: it differs from
``after`` only in ``DPPlanner._plan_next_window_tail`` being a no-op, so the
comparison isolates the tail and says nothing about any other difference
between this branch and main.

Instants come from two places:

  captured  simulations/replay-1120 (six instants a day, 2026-10-02 to 10-11)
            and simulations/replay-1119 (2026-10-12 afternoon), as recorded.
  spliced   one more instant per day, half an hour before the afternoon
            capture, the pre-window case no capture covers. The feed is the
            12:30-stamped capture's entries from 14:30 (stamp time) on,
            continued by the same day's 15:05-stamped capture where the first
            runs out, cut 24 hours later (the feed carries 48 half-hours from
            the current one). SOC and load are the later capture's. Real
            prices, assembled instant.

File stamps are UTC+11 throughout, but daylight saving only started on
2026-10-04. Instants are therefore labelled with their Australia/Sydney local
time, which is what the demand window runs on: the files stamped 15:05 on 10-02
and 10-03 are 14:05 local, and the instant spliced at stamp 14:35 on those two
days is 13:35 local.

The run checks its own headline claims and exits non-zero if any fails: slot 0
and the commanded mode never change, no decision before the tail start
changes, and an instant whose tail was not applied is identical in both arms.

Solar is measured, not forecast (see export_replay_days.py), and a capture has
none for the following day. Tomorrow's solar is therefore the next day's
measured series where that day was captured, scaled by ``--solar-scale`` to
walk the same prices from a solar-surplus tomorrow into a deficit one. With no
next-day capture tomorrow's solar is zero.

Usage
-----
  uv run scripts/replay_next_window_tail.py
  uv run scripts/replay_next_window_tail.py --solar-scale 1.0 0.25 --json out.json
  uv run scripts/replay_next_window_tail.py --only 15:05 --detail

``--only`` matches the file stamp, not the local time.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import ExitStack
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from replay_no_dw import (  # noqa: E402
    ACTION_LETTER,
    LIVE_OVERRIDES,
    LIVE_SWITCHES,
    _seed_horizon,
)

from custom_components.localshift.computation_engine import (  # noqa: E402
    ComputationEngine,
)
from custom_components.localshift.engine.core import DPPlanner  # noqa: E402
from tests.test_scenarios import (  # noqa: E402
    create_mock_entry,
    create_mock_get_entity_id,
    create_mock_get_switch_state,
    setup_coordinator_data,
    setup_mock_hass,
)

CAPTURE_DIRS = (
    REPO_ROOT / "simulations" / "replay-1120",
    REPO_ROOT / "simulations" / "replay-1119",
)
DW_START = time(15, 0)
DW_END = time(21, 0)
OVERNIGHT_END = time(6, 0)
CHARGE_ACTIONS = ("charge_grid_normal", "charge_grid_boost")
LOCAL_TZ = ZoneInfo("Australia/Sydney")


def _no_tail(self: DPPlanner, inputs: Any, main: Any) -> Any:
    """The ``before`` arm: this branch's main solve with no tail re-plan."""
    return main


def _local_label(scenario: dict[str, Any], spliced: bool = False) -> str:
    """The instant in Australia/Sydney local time, e.g. '2026-10-02 14:05'.

    The file stamps are UTC+11 on every day, including the two before daylight
    saving started, so the stamp is an hour ahead of the clock the demand
    window runs on for those days.
    """
    when = datetime.fromisoformat(scenario["input"]["test_time"]).astimezone(LOCAL_TZ)
    return f"{when:%Y-%m-%d %H:%M}" + (" spliced" if spliced else "")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _tomorrow_solar(path: Path, scale: float) -> list[dict[str, Any]]:
    """The next captured day's measured solar, scaled. Empty when not captured."""
    nxt = (date.fromisoformat(path.stem[:10]) + timedelta(days=1)).isoformat()
    candidates = sorted(path.parent.glob(f"{nxt}T*.json"))
    if not candidates:
        return []
    series = _load(candidates[-1])["input"]["solcast_today"]
    return [
        {
            key: (value * scale if key.startswith("pv_estimate") else value)
            for key, value in entry.items()
        }
        for entry in series
    ]


def _spliced_1435(day: str, directory: Path) -> dict[str, Any] | None:
    """An instant at stamp 14:35 from the day's 12:30 and 15:05 stamped captures.

    Stamp time, not local: 14:35+11:00 is 14:35 local from 2026-10-04 and 13:35
    local on the two days before it. On those days the afternoon capture is
    14:05 local, so the spliced instant is still the half hour before it, and
    its feed ends half an hour earlier (a gap of 90 minutes against the
    capture's 60 on 10-02).
    """
    early = directory / f"{day}T123040.json"
    late = directory / f"{day}T150540.json"
    if not early.exists() or not late.exists():
        return None
    scenario = _load(late)
    early_input = _load(early)["input"]
    payload = scenario["input"]
    now = datetime.fromisoformat(payload["test_time"]).replace(
        hour=14, minute=35, second=40
    )
    feed_start = now.replace(minute=30, second=0)
    feed_end = feed_start + timedelta(hours=24)
    for key in ("general_forecast", "feed_in_forecast"):
        first = [
            e
            for e in early_input[key]
            if datetime.fromisoformat(e["start_time"]) >= feed_start
        ]
        handover = datetime.fromisoformat(first[-1]["start_time"])
        rest = [
            e
            for e in payload[key]
            if handover < datetime.fromisoformat(e["start_time"]) < feed_end
        ]
        payload[key] = first + rest
    payload["test_time"] = now.isoformat()
    for key in ("general_forecast_sensor_updated", "feed_in_forecast_sensor_updated"):
        if key in payload:
            payload[key] = now.isoformat()
    return scenario


def run(scenario: dict[str, Any], tail: bool) -> dict[str, Any]:
    """Replay one instant with the tail re-plan on or off."""
    payload = dict(scenario["input"])
    test_time = datetime.fromisoformat(payload["test_time"])
    data = setup_coordinator_data(payload)
    hass = setup_mock_hass(payload)
    _seed_horizon(data, payload)

    overrides = dict(scenario.get("config_overrides", {}))
    overrides.update(LIVE_OVERRIDES)
    switches = dict(scenario.get("switch_states", {}))
    switches.update(LIVE_SWITCHES)

    engine = ComputationEngine(
        hass,
        create_mock_entry(overrides),
        create_mock_get_entity_id(),
        create_mock_get_switch_state(switches),
    )
    recent_load = payload.get("load_power_kw", 0.5)
    patches = [
        patch("homeassistant.util.dt.now", return_value=test_time),
        patch.object(engine, "_get_historical_hourly_averages", return_value={}),
        patch.object(engine._history_fetcher, "_historical_load_cache", {}),
        patch.object(engine._history_fetcher, "_historical_load_sample_counts", {}),
        patch.object(engine._history_fetcher, "_historical_load_source", "none"),
        patch.object(engine._history_fetcher, "_recent_load_1hr_kw", recent_load),
    ]
    if not tail:
        patches.append(patch.object(DPPlanner, "_plan_next_window_tail", _no_tail))

    solve_times: list[float] = []
    real_plan = DPPlanner.plan

    def _timed_plan(self: DPPlanner, inputs: Any) -> Any:
        result = real_plan(self, inputs)
        solve_times.append(result.solve_time_seconds)
        captured["inputs"] = inputs
        captured["result"] = result
        return result

    captured: dict[str, Any] = {}
    patches.append(patch.object(DPPlanner, "plan", _timed_plan))

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        # Two passes: the first warms the fields live carries over from the
        # previous cycle (see replay_no_dw._seed_horizon); the second is reported.
        engine.compute_derived_values(data)
        engine.compute_derived_values(data)

    decisions = getattr(data, "optimizer_decisions", None) or []
    result = captured["result"]
    return {
        "decisions": decisions,
        "net_cost": (data.optimizer_result or {}).get("projected_net_cost"),
        "solve_s": solve_times[-1],
        "gap_min": captured["inputs"].next_demand_window_gap_min,
        "status": result.next_window_tail_status,
        "tail_start": result.next_window_tail_start_idx,
        "required": result.next_window_required_soc_pct,
        "active_mode": str(getattr(data, "active_mode", "")),
    }


def _charge_kwh(decisions: list[dict[str, Any]], overnight_only: bool) -> float:
    total = 0.0
    for dec in decisions:
        if dec.get("action") not in CHARGE_ACTIONS:
            continue
        when = datetime.fromisoformat(dec["timestamp_iso"]).time()
        if overnight_only and not (when >= DW_END or when < OVERNIGHT_END):
            continue
        total += float(dec.get("grid_import_kwh") or 0.0)
    return total


def _charge_span(decisions: list[dict[str, Any]]) -> str | None:
    """Earliest and latest time of day of a grid-charge slot, e.g. '09:30-14:30'."""
    times = sorted(
        datetime.fromisoformat(dec["timestamp_iso"]).strftime("%H:%M")
        for dec in decisions
        if dec.get("action") in CHARGE_ACTIONS
    )
    return f"{times[0]}-{times[-1]}" if times else None


def _timeline(decisions: list[dict[str, Any]]) -> str:
    out = []
    for dec in decisions:
        when = datetime.fromisoformat(dec["timestamp_iso"]).time()
        letter = ACTION_LETTER.get(dec.get("action", "hold"), "?")
        out.append("_" if DW_START <= when < DW_END and letter == "." else letter)
    return "".join(out)


def compare(label: str, scenario: dict[str, Any]) -> dict[str, Any]:
    before = run(scenario, tail=False)
    after = run(scenario, tail=True)
    b, a = before["decisions"], after["decisions"]
    split = after["tail_start"]
    head = len(a) if split is None else split
    return {
        "instant": label,
        "slots": len(a),
        "gap_min": after["gap_min"],
        "status": after["status"],
        "tail_start": split,
        "tail_start_before": before["tail_start"],
        "required": after["required"],
        "identical": a == b,
        "head_identical": a[:head] == b[:head],
        "slot0_before": b[0]["action"] if b else None,
        "slot0_after": a[0]["action"] if a else None,
        "mode_before": before["active_mode"],
        "mode_after": after["active_mode"],
        "end_soc_before": b[-1]["predicted_soc_pct"] if b else None,
        "end_soc_after": a[-1]["predicted_soc_pct"] if a else None,
        "charge_kwh_before": round(_charge_kwh(b, False), 2),
        "charge_kwh_after": round(_charge_kwh(a, False), 2),
        "overnight_kwh_before": round(_charge_kwh(b, True), 2),
        "overnight_kwh_after": round(_charge_kwh(a, True), 2),
        "charge_span_before": _charge_span(b),
        "charge_span_after": _charge_span(a),
        "cost_before": before["net_cost"],
        "cost_after": after["net_cost"],
        "solve_s_before": round(before["solve_s"], 3),
        "solve_s_after": round(after["solve_s"], 3),
        "timeline_before": _timeline(b),
        "timeline_after": _timeline(a),
    }


HEADER = (
    f"{'instant (local)':<26}{'gap':>6}{'status':>20}{'tail@':>6}{'req':>6}"
    f"{'same':>6}{'head=':>6}"
    f"{'endSOC':>14}{'chg kWh':>14}{'o/night':>12}{'cost $':>18}{'solve s':>14}"
)


def _fmt(value: Any, spec: str) -> str:
    return format(value, spec) if isinstance(value, (int, float)) else "-"


def _print_row(row: dict[str, Any]) -> None:
    def pair(key: str, spec: str) -> str:
        return f"{_fmt(row[key + '_before'], spec)}>{_fmt(row[key + '_after'], spec)}"

    print(
        f"{row['instant']:<26}"
        f"{_fmt(row['gap_min'], '.0f'):>6}"
        f"{row['status'] or '-':>20}"
        f"{_fmt(row['tail_start'], 'd'):>6}"
        f"{_fmt(row['required'], '.0f'):>6}"
        f"{'yes' if row['identical'] else 'no':>6}"
        f"{'yes' if row['head_identical'] else 'NO':>6}"
        f"{pair('end_soc', '.1f'):>14}"
        f"{pair('charge_kwh', '.2f'):>14}"
        f"{pair('overnight_kwh', '.1f'):>12}"
        f"{pair('cost', '.3f'):>18}"
        f"{pair('solve_s', '.2f'):>14}"
    )


def _instants(scale: float, only: str | None) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, str, dict[str, Any]]] = []
    for directory in CAPTURE_DIRS:
        for path in sorted(directory.glob("*.json")):
            scenario = _load(path)
            scenario["input"]["solcast_tomorrow"] = _tomorrow_solar(path, scale)
            out.append((path.stem, _local_label(scenario), scenario))
        for day in sorted({p.stem[:10] for p in directory.glob("*.json")}):
            scenario = _spliced_1435(day, directory)
            if scenario is None:
                continue
            late = directory / f"{day}T150540.json"
            scenario["input"]["solcast_tomorrow"] = _tomorrow_solar(late, scale)
            out.append((f"{day}T143540", _local_label(scenario, True), scenario))
    out.sort(key=lambda item: item[1])
    if only:
        stamp = only.replace(":", "")
        out = [item for item in out if f"T{stamp}" in item[0]]
    return [(label, scenario) for _, label, scenario in out]


def violations(rows: list[dict[str, Any]]) -> list[str]:
    """Every instant that breaks a headline claim. Empty means all hold."""
    out: list[str] = []
    for row in rows:
        name = row["instant"]
        applied = row["status"] == "applied"
        checks = (
            (
                row["slot0_before"] != row["slot0_after"],
                f"slot-0 action {row['slot0_before']} -> {row['slot0_after']}",
            ),
            (
                row["mode_before"] != row["mode_after"],
                f"commanded mode {row['mode_before']} -> {row['mode_after']}",
            ),
            (not row["head_identical"], "a decision before the tail start changed"),
            (
                row["tail_start_before"] is not None,
                "the 'before' arm re-planned a tail",
            ),
            (
                row["tail_start"] is not None and row["tail_start"] < 1,
                f"tail starts at index {row['tail_start']}",
            ),
            (
                applied != (row["tail_start"] is not None),
                f"status {row['status']} with tail start {row['tail_start']}",
            ),
            (
                not applied and not row["identical"],
                f"status {row['status']} but the plan changed",
            ),
            (
                not applied and row["cost_before"] != row["cost_after"],
                f"status {row['status']} but the projected cost changed",
            ),
        )
        out += [f"{name}: {message}" for failed, message in checks if failed]
    return out


def _summarise(rows: list[dict[str, Any]]) -> None:
    changed = [r for r in rows if not r["identical"]]
    same = [r for r in rows if r["identical"]]
    statuses: dict[str, int] = {}
    for r in rows:
        statuses[str(r["status"])] = statuses.get(str(r["status"]), 0) + 1
    print(
        f"\n{len(rows)} instants: {len(changed)} with a changed plan, "
        f"{len(same)} identical with the tail on or off"
    )
    print("tail status: " + ", ".join(f"{k} {v}" for k, v in sorted(statuses.items())))
    print(
        "decisions before the tail changed in "
        f"{sum(1 for r in rows if not r['head_identical'])} instants; "
        "slot-0 action changed in "
        f"{sum(1 for r in rows if r['slot0_before'] != r['slot0_after'])}; "
        "commanded mode changed in "
        f"{sum(1 for r in rows if r['mode_before'] != r['mode_after'])}"
    )
    for name, group in (("changed", changed), ("identical", same)):
        if not group:
            continue
        n = len(group)
        print(
            f"{name:<11} n={n:<3} "
            f"overnight charge kWh {sum(r['overnight_kwh_before'] for r in group):.2f}"
            f" -> {sum(r['overnight_kwh_after'] for r in group):.2f}   "
            f"mean cost {sum(r['cost_before'] for r in group) / n:.3f}"
            f" -> {sum(r['cost_after'] for r in group) / n:.3f}   "
            f"mean solve {sum(r['solve_s_before'] for r in group) / n:.3f}s"
            f" -> {sum(r['solve_s_after'] for r in group) / n:.3f}s   "
            f"max solve {max(r['solve_s_before'] for r in group):.3f}s"
            f" -> {max(r['solve_s_after'] for r in group):.3f}s"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--solar-scale",
        type=float,
        nargs="+",
        default=[1.0],
        help="multiply tomorrow's measured solar by this; several values run "
        "one after another (default 1.0)",
    )
    parser.add_argument("--only", help="only instants at this time, e.g. 15:05")
    parser.add_argument("--detail", action="store_true", help="print plan timelines")
    parser.add_argument("--json", help="also write full results here")
    args = parser.parse_args()

    results: dict[str, list[dict[str, Any]]] = {}
    broken: list[str] = []
    for scale in args.solar_scale:
        rows: list[dict[str, Any]] = []
        print(f"\ntomorrow's solar x {scale}")
        print(HEADER)
        print("-" * len(HEADER))
        for label, scenario in _instants(scale, args.only):
            row = compare(label, scenario)
            rows.append(row)
            _print_row(row)
            if args.detail and not row["identical"]:
                print(f"    before {row['timeline_before']}")
                print(f"    after  {row['timeline_after']}")
                print(f"    charge slots {row['charge_span_after']}")
        _summarise(rows)
        results[str(scale)] = rows
        broken += [f"x{scale} {line}" for line in violations(rows)]

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2, default=str) + "\n")
        print(f"\nfull results -> {args.json}")

    if broken:
        print(f"\nFAILED: {len(broken)} violation(s) of the headline claims")
        for line in broken:
            print(f"  {line}")
        return 1
    print(
        "\nOK: slot 0 and the commanded mode unchanged, no decision before the "
        "tail start changed, and every instant whose tail was not applied is "
        "identical in both arms"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

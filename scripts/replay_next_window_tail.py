#!/usr/bin/env python3
"""replay_next_window_tail.py — the plan tail before a window past the horizon.

Issue #1033. The price feed is 24 hours long, so in the afternoon the horizon
ends at or just short of tomorrow's demand window, and the plan's tail drained
to the SOC floor because nothing asked for charge before it. The tail is now
planned a second time against that window (``DPPlanner._plan_next_window_tail``).

Each captured instant runs twice through
``ComputationEngine.compute_derived_values`` on byte-identical inputs:

  before   the tail re-plan switched off (main)
  after    this branch

Instants come from two places:

  captured  simulations/replay-1120 (six instants a day, 2026-10-02 to 10-11)
            and simulations/replay-1119 (2026-10-12 afternoon), as recorded.
  spliced   a 14:35 instant per day, the pre-window case no capture covers. The
            feed is the 12:30 capture's entries from 14:30 on, continued by the
            same day's 15:05 capture where the first runs out, cut at 14:00 the
            next day (the feed carries 48 half-hours from the current one). SOC
            and load are the 15:05 capture's. Real prices, assembled instant.

Solar is measured, not forecast (see export_replay_days.py), and a capture has
none for the following day. Tomorrow's solar is therefore the next day's
measured series where that day was captured, scaled by ``--solar-scale`` to
walk the same prices from a solar-surplus tomorrow into a deficit one. With no
next-day capture tomorrow's solar is zero.

Usage
-----
  uv run scripts/replay_next_window_tail.py
  uv run scripts/replay_next_window_tail.py --solar-scale 0.25 --json out.json
  uv run scripts/replay_next_window_tail.py --only 15:05 --detail
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


def _no_tail(self: DPPlanner, inputs: Any, main: Any) -> Any:
    """The ``before`` arm: the main solve's plan, as on main."""
    return main


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
    """A 14:35 instant assembled from the day's 12:30 and 15:05 captures."""
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
    payload["adaptive_params"] = {}
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
        "tail_start": split,
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
    f"{'instant':<26}{'gap':>6}{'tail@':>6}{'req':>6}{'same':>6}{'head=':>6}"
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
    out: list[tuple[str, dict[str, Any]]] = []
    for directory in CAPTURE_DIRS:
        for path in sorted(directory.glob("*.json")):
            scenario = _load(path)
            scenario["input"]["solcast_tomorrow"] = _tomorrow_solar(path, scale)
            out.append((path.stem, scenario))
        for day in sorted({p.stem[:10] for p in directory.glob("*.json")}):
            scenario = _spliced_1435(day, directory)
            if scenario is None:
                continue
            late = directory / f"{day}T150540.json"
            scenario["input"]["solcast_tomorrow"] = _tomorrow_solar(late, scale)
            out.append((f"{day}T143540 spliced", scenario))
    out.sort(key=lambda item: item[0])
    if only:
        stamp = only.replace(":", "")
        out = [item for item in out if f"T{stamp}" in item[0]]
    return out


def _summarise(rows: list[dict[str, Any]]) -> None:
    changed = [r for r in rows if not r["identical"]]
    same = [r for r in rows if r["identical"]]
    print(
        f"\n{len(rows)} instants: {len(changed)} with a re-planned tail, "
        f"{len(same)} identical to main"
    )
    print(
        "decisions before the tail changed in "
        f"{sum(1 for r in rows if not r['head_identical'])} instants; "
        "slot-0 action changed in "
        f"{sum(1 for r in rows if r['slot0_before'] != r['slot0_after'])}; "
        "commanded mode changed in "
        f"{sum(1 for r in rows if r['mode_before'] != r['mode_after'])}"
    )
    for name, group in (("re-planned", changed), ("identical", same)):
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
        default=1.0,
        help="multiply tomorrow's measured solar by this (default 1.0)",
    )
    parser.add_argument("--only", help="only instants at this time, e.g. 15:05")
    parser.add_argument("--detail", action="store_true", help="print plan timelines")
    parser.add_argument("--json", help="also write full results here")
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    print(f"tomorrow's solar x {args.solar_scale}")
    print(HEADER)
    print("-" * len(HEADER))
    for label, scenario in _instants(args.solar_scale, args.only):
        row = compare(label, scenario)
        rows.append(row)
        _print_row(row)
        if args.detail and not row["identical"]:
            print(f"    before {row['timeline_before']}")
            print(f"    after  {row['timeline_after']}")
            print(f"    charge slots {row['charge_span_after']}")
    _summarise(rows)

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2, default=str) + "\n")
        print(f"\nfull results -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

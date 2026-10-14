#!/usr/bin/env python3
"""replay_spike_export.py — does the default mode export on a spike, and only then?

Issue #1120. In self_consumption mode, export was admitted only when the sell
price beat the SAME interval's buy price, which Amber's prices never do. This
branch also admits a genuine spike, judged against the later rebuy price
(``engine/spike_export.py``). Two questions, one harness:

  spike      the 2026-10-12 captures (simulations/replay-1119), replayed in
             self_consumption mode: do the confirmed $10+ and $24 intervals
             export, and the sub-$1 intervals between them hold?
  controls   ordinary days (simulations/replay-1120, several instants a day):
             does the plan gain any export slot or any mode transition?

Each instant runs twice through ``ComputationEngine.compute_derived_values``:

  base     the base branch: spike admission switched off
  branch   this branch

``run`` also takes a third arm, ``switch_off``: this branch with
``switch.localshift_spike_discharge_enabled`` OFF. That is the owner's kill
switch for the rule and must reproduce ``base`` exactly (the tests hold it to
that); ``base`` removes the code, ``switch_off`` goes through the switch.

Inputs are byte-identical across the two arms. Read the solar caveat in
export_replay_days.py before quoting absolute numbers; the comparison between
arms is what this is for.

Usage
-----
  uv run scripts/replay_spike_export.py capture --days 9
  uv run scripts/replay_spike_export.py replay
  uv run scripts/replay_spike_export.py replay --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

SPIKE_DIR = REPO_ROOT / "simulations" / "replay-1119"
CONTROL_DIR = REPO_ROOT / "simulations" / "replay-1120"
# Morning, solar peak, demand-window start, evening peak (two), late evening.
CONTROL_TIMES = ("07:00:40", "12:30:40", "15:05:40", "17:30:40", "19:00:40", "21:30:40")
# A control day is one with no feed-in price above this anywhere in any of its
# captured horizons. An ordinary evening peak is well under it.
ORDINARY_MAX_SELL = 1.0


# --------------------------------------------------------------------------
# Capture (read-only: Home Assistant history API)
# --------------------------------------------------------------------------


def capture(days: int, out_dir: Path) -> int:
    import export_replay_days as erd
    import replay_slot0_price as rsp

    now = datetime.now(erd.AEST).astimezone()
    instants = []
    for back in range(1, days + 1):
        day = (now - timedelta(days=back)).date()
        for clock in CONTROL_TIMES:
            instants.append(f"{day.isoformat()}T{clock}{now.strftime('%z')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    rsp.capture(instants, out_dir)

    # Compact the files: 50-odd pretty-printed horizons is over a megabyte.
    for path in out_dir.glob("*.json"):
        scenario = json.loads(path.read_text())
        scenario["name"] = scenario["name"].replace("replay-1119", "replay-1120")
        path.write_text(json.dumps(scenario, separators=(",", ":")) + "\n")
    return 0


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------


def run(
    scenario: dict[str, Any], arm: str, optimization_mode: str = "self_consumption"
) -> dict[str, Any]:
    """Run one captured instant under one arm (self_consumption by default)."""
    from replay_no_dw import LIVE_OVERRIDES, LIVE_SWITCHES, _seed_horizon

    from custom_components.localshift.computation_engine import ComputationEngine
    from custom_components.localshift.const import BatteryMode
    from custom_components.localshift.engine import core
    from tests.test_scenarios import (
        create_mock_entry,
        create_mock_get_entity_id,
        create_mock_get_switch_state,
        setup_coordinator_data,
        setup_mock_hass,
    )

    payload = dict(scenario["input"])
    test_time = datetime.fromisoformat(payload["test_time"])
    mode = BatteryMode(payload.get("active_mode", "self_consumption"))

    data = setup_coordinator_data(payload)
    data.active_mode = mode
    hass = setup_mock_hass(payload)
    _seed_horizon(data, payload)

    overrides = dict(scenario.get("config_overrides", {}))
    overrides.update(LIVE_OVERRIDES)
    overrides["optimization_mode"] = optimization_mode
    switches = dict(scenario.get("switch_states", {}))
    switches.update(LIVE_SWITCHES)
    if arm == "switch_off":
        # The kill switch, through the same resolver production uses.
        switches["spike_discharge_enabled"] = False

    engine = ComputationEngine(
        hass,
        create_mock_entry(overrides),
        create_mock_get_entity_id(),
        create_mock_get_switch_state(switches),
    )

    patches = [
        patch("homeassistant.util.dt.now", return_value=test_time),
        patch.object(engine, "_get_historical_hourly_averages", return_value={}),
        patch.object(engine._history_fetcher, "_historical_load_cache", {}),
        patch.object(engine._history_fetcher, "_historical_load_sample_counts", {}),
        patch.object(engine._history_fetcher, "_historical_load_source", "none"),
        patch.object(
            engine._history_fetcher,
            "_recent_load_1hr_kw",
            payload.get("load_power_kw", 0.5),
        ),
    ]
    if arm == "base":
        # The base branch has no spike admission: the flag is never set, so
        # feasible_actions is left with the same-slot test alone.
        patches.append(
            patch.object(core, "apply_spike_export_admission", lambda *a, **k: None)
        )

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        engine.compute_derived_values(data)
        data.active_mode = mode
        engine.compute_derived_values(data)

    decisions = getattr(data, "optimizer_decisions", None) or []
    summary = getattr(data, "optimizer_summary", None) or {}
    actions = [d.get("action") for d in decisions]
    charge_buys = [
        d.get("buy_price") or 0.0
        for d in decisions
        if str(d.get("action", "")).startswith("charge_grid")
    ]
    return {
        "actions": actions,
        "charge_slots": len(charge_buys),
        "max_charge_buy": max(charge_buys) if charge_buys else None,
        "dw_entry_soc_pct": summary.get("dw_entry_soc_pct"),
        "terminal_shortfall_pct": summary.get("terminal_shortfall_pct"),
        "export_slots": actions.count("export_proactive"),
        "transitions": sum(
            1 for a, b in zip(actions, actions[1:], strict=False) if a != b
        ),
        "buy": decisions[0].get("buy_price") if decisions else None,
        "sell": decisions[0].get("sell_price") if decisions else None,
        "mode": getattr(data.active_mode, "value", str(data.active_mode)),
        "spike_export": summary.get("spike_export") or {},
        "slot0_price": summary.get("slot0_price") or {},
    }


def _max_sell(scenario: dict[str, Any]) -> float:
    return max(e["per_kwh"] for e in scenario["input"]["feed_in_forecast"])


def replay_spike(in_dir: Path) -> list[dict[str, Any]]:
    rows = []
    print("2026-10-12 spike, replayed in self_consumption mode")
    print(
        f"{'instant':<10}{'confirmed':<11}{'sell':>9}{'buy':>9}{'later spike':>12}"
        f"{'rebuy':>8}{'min sell':>10}{'base':>18}{'branch':>18}{'skip debounce':>15}"
    )
    for path in sorted(in_dir.glob("*.json")):
        scenario = json.loads(path.read_text())
        base = run(scenario, "base")
        branch = run(scenario, "branch")
        spike = branch["spike_export"]
        at = datetime.fromisoformat(scenario["input"]["test_time"])
        confirmed = branch["slot0_price"].get("sell_estimate") is False
        print(
            f"{at.strftime('%H:%M:%S'):<10}{'yes' if confirmed else 'no':<11}"
            f"{branch['sell']:>9.4f}{branch['buy']:>9.4f}"
            f"{spike.get('later_spike_sell') or 0:>12.2f}"
            f"{spike.get('rebuy_price') or 0:>8.2f}{spike.get('min_sell') or 0:>10.2f}"
            f"{base['actions'][0]:>18}{branch['actions'][0]:>18}"
            f"{spike.get('skip_debounce')!s:>15}"
        )
        rows.append({
            "instant": scenario["input"]["test_time"],
            "base": base,
            "branch": branch,
        })
    return rows


def replay_controls(dirs: list[Path]) -> tuple[list[dict[str, Any]], int]:
    by_day: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for in_dir in dirs:
        for path in sorted(in_dir.glob("*.json")):
            scenario = json.loads(path.read_text())
            day = scenario["input"]["test_time"][:10]
            by_day.setdefault(day, []).append((path, scenario))

    rows: list[dict[str, Any]] = []
    failures = 0
    print("\nOrdinary-day controls, self_consumption mode")
    print(
        f"{'day':<12}{'instants':>9}{'max sell':>10}{'slots':>7}"
        f"{'slot0 spike':>12}{'export base':>13}{'export branch':>15}"
        f"{'trans base':>12}{'trans branch':>14}{'plans differ':>14}"
    )
    for day in sorted(by_day):
        scenarios = by_day[day]
        max_sell = max(_max_sell(s) for _, s in scenarios)
        control = max_sell < ORDINARY_MAX_SELL
        total = {k: 0 for k in ("slots", "admitted", "eb", "en", "tb", "tn", "differ")}
        for _, scenario in scenarios:
            base = run(scenario, "base")
            branch = run(scenario, "branch")
            total["slots"] += len(branch["actions"])
            total["admitted"] += int(bool(branch["spike_export"].get("admitted")))
            total["eb"] += base["export_slots"]
            total["en"] += branch["export_slots"]
            total["tb"] += base["transitions"]
            total["tn"] += branch["transitions"]
            differ = (
                base["actions"] != branch["actions"] or base["mode"] != branch["mode"]
            )
            total["differ"] += int(differ)
        failed = bool(
            total["differ"] or total["en"] > total["eb"] or total["tn"] > total["tb"]
        )
        failures += int(failed and control)
        print(
            f"{day:<12}{len(scenarios):>9}{max_sell:>10.2f}{total['slots']:>7}"
            f"{total['admitted']:>12}{total['eb']:>13}{total['en']:>15}"
            f"{total['tb']:>12}{total['tn']:>14}{total['differ']:>14}"
            f"{'' if control else '   not a control: a spike is forecast'}"
        )
        if not control:
            continue
        rows.append({
            "day": day,
            "instants": len(scenarios),
            "max_sell": max_sell,
            **total,
        })
    print(
        f"\n{len(rows)} control day(s), {sum(r['instants'] for r in rows)} instants, "
        f"{sum(r['slots'] for r in rows)} plan slots: "
        f"{'FAIL' if failures else 'no new export slot, no new transition'}"
    )
    return rows, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capture", help="capture ordinary-day control instants")
    cap.add_argument("--days", type=int, default=9)
    cap.add_argument("--out", default=str(CONTROL_DIR))
    rep = sub.add_parser("replay", help="replay the spike day and the controls")
    rep.add_argument("--json", default=None)
    args = parser.parse_args()

    if args.cmd == "capture":
        return capture(args.days, Path(args.out))

    spike = replay_spike(SPIKE_DIR)
    controls, failures = replay_controls([
        CONTROL_DIR,
        REPO_ROOT / "simulations" / "replay",
        REPO_ROOT / "simulations" / "replay-nodw",
    ])
    if args.json:
        Path(args.json).write_text(
            json.dumps({"spike": spike, "controls": controls}, indent=2) + "\n"
        )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

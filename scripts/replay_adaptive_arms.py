#!/usr/bin/env python3
"""replay_adaptive_arms.py — Slice 0 gate for removing the adaptive offsets.

Replays each captured real day (``simulations/replay/*.json``, written by
scripts/export_replay_days.py) through the optimizer three times, changing only
the adaptive-parameter offsets, and tabulates what each arm plans.

The arms:

  current      what live actually runs: cheap_price_bias pinned at its +5 bound
               by the contextual ratchet, plus the learned SOC offsets
  learned      the learned base without the ratchet (cheap_price_bias +3)
  zero         no offsets at all — what removing the layer gives you

Why this exists: live has never once run without a +3 to +5 c/kWh inflation of
the grid-charge price gate, so deleting the learning layer is a behavioural
change, not just a code deletion. The decision rule is in the plan: if `zero`
holds DW entry within 1pp of `current` on every day, the offsets go outright;
if any day loses more than that, the equivalent has to be re-exposed as a
visible operator knob first.

Inputs are byte-identical across arms, so this is a paired comparison. Read the
solar caveat in export_replay_days.py before quoting absolute numbers.

Usage
-----
  scripts/replay_adaptive_arms.py
  scripts/replay_adaptive_arms.py --dir simulations/replay --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.localshift.computation_engine import (  # noqa: E402
    ComputationEngine,
)
from tests.test_scenarios import (  # noqa: E402
    create_mock_entry,
    create_mock_get_entity_id,
    create_mock_get_switch_state,
    setup_coordinator_data,
    setup_mock_hass,
)

# The offsets live ran under, read off sensor.localshift_forecast_diagnostics
# (adaptive_params_values) on 2026-09-04.
ARMS: dict[str, dict[str, float]] = {
    "current": {
        "cheap_price_bias": 5.0,
        "solar_confidence_factor": 1.0,
        "overnight_drain_safety_margin": -0.5,
        "grid_charge_soc_headroom": -0.5,
        "export_threshold_adjustment": 0.0,
        "consumption_forecast_bias": 0.0,
    },
    "learned": {
        "cheap_price_bias": 3.0,
        "solar_confidence_factor": 1.0,
        "overnight_drain_safety_margin": -0.5,
        "grid_charge_soc_headroom": -0.5,
        "export_threshold_adjustment": 0.0,
        "consumption_forecast_bias": 0.0,
    },
    "zero": {},
}


def run_arm(scenario: dict[str, Any], offsets: dict[str, float]) -> dict[str, Any]:
    """Run one scenario under one set of adaptive offsets."""
    payload = dict(scenario["input"])
    payload["adaptive_params"] = offsets

    test_time = datetime.fromisoformat(payload["test_time"])
    data = setup_coordinator_data(payload)
    hass = setup_mock_hass(payload)
    entry = create_mock_entry(scenario.get("config_overrides", {}))
    engine = ComputationEngine(
        hass,
        entry,
        create_mock_get_entity_id(),
        create_mock_get_switch_state(scenario.get("switch_states", {})),
    )

    recent_load = payload.get("load_power_kw", 0.5)
    with (
        patch("homeassistant.util.dt.now", return_value=test_time),
        patch.object(engine, "_get_historical_hourly_averages", return_value={}),
        patch.object(engine._history_fetcher, "_historical_load_cache", {}),
        patch.object(engine._history_fetcher, "_historical_load_sample_counts", {}),
        patch.object(engine._history_fetcher, "_historical_load_source", "none"),
        patch.object(engine._history_fetcher, "_recent_load_1hr_kw", recent_load),
    ):
        engine.compute_derived_values(data)

    # Costs come off data.optimizer_result. The SOC trajectory has to be read
    # from data.optimizer_decisions rather than the summary: the summary's
    # dw_entry_soc_pct is only computed when solar CANNOT reach target
    # (engine/core.py:728), so on good days it is legitimately absent — which
    # is precisely when we still need to know what the plan intends to do.
    result = data.optimizer_result or {}
    summary = getattr(data, "optimizer_summary", None) or {}
    decisions = getattr(data, "optimizer_decisions", None) or []

    dw_start = _dw_start(scenario, payload["test_time"])
    dw_soc = None
    precharge_kwh = 0.0
    charge_slots = 0
    for dec in decisions:
        stamp = dec.get("timestamp_iso")
        if not stamp:
            continue
        when = datetime.fromisoformat(stamp)
        if when <= dw_start:
            soc = dec.get("predicted_soc_pct")
            if isinstance(soc, (int, float)):
                dw_soc = soc
            if dec.get("grid_charge"):
                charge_slots += 1
                precharge_kwh += float(dec.get("grid_import_kwh") or 0.0)

    return {
        "success": bool(result.get("success")),
        # Plan-derived, present on every day.
        "dw_soc_planned": round(dw_soc, 2)
        if isinstance(dw_soc, (int, float))
        else None,
        "precharge_kwh": round(precharge_kwh, 3),
        "precharge_slots": charge_slots,
        # Engine-reported; absent on solar-capable days by design.
        "dw_entry_soc_pct": summary.get("dw_entry_soc_pct"),
        "peak_soc_pct": summary.get("peak_soc_pct"),
        "initial_soc_pct": summary.get("initial_soc_pct"),
        "projected_net_cost": result.get("projected_net_cost"),
        "terminal_shortfall_pct": result.get("terminal_shortfall_pct"),
        "projected_import_kwh": result.get("projected_import_kwh"),
        "projected_export_kwh": result.get("projected_export_kwh"),
        "effective_cheap_price": getattr(data, "effective_cheap_price", None),
    }


def _dw_start(scenario: dict[str, Any], test_time: str) -> datetime:
    """Demand-window start on the scenario's own day, from its config."""
    when = datetime.fromisoformat(test_time)
    raw = scenario.get("config_overrides", {}).get("demand_window_start", "15:00:00")
    hour, minute = (int(part) for part in raw.split(":")[:2])
    return when.replace(hour=hour, minute=minute, second=0, microsecond=0)


def fmt(value: Any, spec: str = "6.2f") -> str:
    return format(value, spec) if isinstance(value, (int, float)) else "   n/a"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="simulations/replay")
    parser.add_argument("--json", help="also write full results here")
    args = parser.parse_args()

    paths = sorted(Path(args.dir).glob("*.json"))
    if not paths:
        sys.exit(
            f"ERROR: no replay scenarios in {args.dir} — run export_replay_days.py"
        )

    results: dict[str, dict[str, Any]] = {}
    print(
        f"{'day':<12} {'arm':<9} {'SOC@DW':>7} {'net cost':>9} "
        f"{'shortfall':>9} {'precharge':>10} {'slots':>6}"
    )
    print("-" * 68)
    for path in paths:
        scenario = json.loads(path.read_text())
        day = path.stem
        results[day] = {}
        for arm, offsets in ARMS.items():
            try:
                out = run_arm(scenario, offsets)
            except Exception as exc:  # noqa: BLE001 - report, keep going
                out = {"error": f"{type(exc).__name__}: {exc}"}
            results[day][arm] = out
            if "error" in out:
                print(f"{day:<12} {arm:<9} ERROR {out['error'][:44]}")
                continue
            print(
                f"{day:<12} {arm:<9} {fmt(out['dw_soc_planned'], '7.2f')} "
                f"{fmt(out['projected_net_cost'], '9.4f')} "
                f"{fmt(out['terminal_shortfall_pct'], '9.2f')} "
                f"{fmt(out['precharge_kwh'], '10.3f')} "
                f"{out['precharge_slots']:>6}"
            )
        print("-" * 68)

    report_verdict(results)

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2) + "\n")
        print(f"\nfull results -> {args.json}")
    return 0


def report_verdict(results: dict[str, dict[str, Any]]) -> None:
    """Apply the plan's decision rule and print the supporting deltas."""
    print("\nzero vs current (the decision rule: |delta| <= 1pp DW entry on every day)")
    worst = 0.0
    breaches: list[str] = []
    incomparable: list[str] = []
    for day, arms in results.items():
        cur = arms.get("current", {}).get("dw_soc_planned")
        zero = arms.get("zero", {}).get("dw_soc_planned")
        if not isinstance(cur, (int, float)) or not isinstance(zero, (int, float)):
            incomparable.append(day)
            print(f"  {day}: INCOMPARABLE (the plan had no slot at or before DW start)")
            continue
        delta = zero - cur
        worst = max(worst, abs(delta))
        flag = "  <-- breach" if abs(delta) > 1.0 else ""
        if flag:
            breaches.append(day)
        print(
            f"  {day}: current={cur:6.2f}  zero={zero:6.2f}  delta={delta:+6.2f}pp{flag}"
        )

    # Cost and shortfall move even when DW entry does not, so report them too.
    print("\nsecondary: zero - current")
    for day, arms in results.items():
        cur, zero = arms.get("current", {}), arms.get("zero", {})
        pairs = []
        for label, key in (
            ("cost", "projected_net_cost"),
            ("shortfall", "terminal_shortfall_pct"),
            ("precharge_kwh", "precharge_kwh"),
        ):
            a, b = cur.get(key), zero.get(key)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                pairs.append(f"{label} {b - a:+.4f}")
        print(f"  {day}: {'  '.join(pairs) or 'incomparable'}")

    comparable = len(results) - len(incomparable)
    print(
        f"\ncomparable days: {comparable}/{len(results)};  worst |delta| = {worst:.2f}pp"
    )
    if incomparable:
        print(
            f"VERDICT: INCONCLUSIVE — {len(incomparable)} day(s) produced no DW-entry "
            f"projection ({', '.join(incomparable)}). Fix the harness before deciding."
        )
    elif breaches:
        print(f"VERDICT: {len(breaches)} day(s) breach 1pp ({', '.join(breaches)}).")
        print("         Re-expose the bias as a visible knob before removing it.")
    else:
        print("VERDICT: within 1pp on every day — the offsets can be removed outright.")


if __name__ == "__main__":
    raise SystemExit(main())

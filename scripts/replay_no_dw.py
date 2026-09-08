#!/usr/bin/env python3
"""replay_no_dw.py — what does the planner do when the demand window is gone?

Replays each captured real day (``simulations/replay*/*.json``, written by
scripts/export_replay_days.py) through the optimizer under arms that differ
only in whether the demand window exists and how grid charging is admitted:

  dw              what live runs today: 15:00-21:00 demand window every day,
                  95% target, strict entry (allow_dw_entry_under_target=False),
                  cheap price at the 50th percentile, $0.25/kWh cycle hurdle
  no_dw           the demand window removed from planning entirely — no slot
                  is flagged as DW, so there is no terminal target, no hard
                  floor, no water level, no urgency window, no grid-import ban
  no_dw_p25       as no_dw, with the cheap percentile back at its 25 default
  no_dw_p100 / no_dw_mcs0 / no_dw_open / no_dw_p100_mcs10
                  diagnostic: open the cheap-price gate, the cycle hurdle, or
                  both, to find which gate blocks a price-shaped pre-charge
  gateless_mcs25 / gateless_mcs10 / gateless_mcs0
                  the cheap-price admission gate removed outright, so every
                  non-DW slot offers grid charge and the DP's cost function
                  alone decides — "trust the rate all day", literally — at
                  three cycle hurdles

Why: the tariff's demand charge is seasonal, and the shoulder season has just
started. The planner has never run without a deadline, so before a live
switch exists this measures what "trust the rate all day" actually does on
the days that just happened. Results: simulations/replay-nodw/README.md.

Inputs are byte-identical across arms — a paired comparison. Read the solar
caveat in export_replay_days.py before quoting absolute numbers: solar is
measured, not forecast, so every arm has perfect solar foresight.

Usage
-----
  uv run scripts/replay_no_dw.py
  uv run scripts/replay_no_dw.py --arms dw,no_dw,gateless_mcs10 --json out.json
  uv run scripts/replay_no_dw.py --day 2026-09-07 --detail
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from contextlib import ExitStack
from datetime import datetime, time
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.localshift.computation_engine import (  # noqa: E402
    ComputationEngine,
)
from custom_components.localshift.engine import (
    constraints as _constraints,  # noqa: E402
)
from custom_components.localshift.engine.slots import SlotBuilder  # noqa: E402
from tests.test_scenarios import (  # noqa: E402
    create_mock_entry,
    create_mock_get_entity_id,
    create_mock_get_switch_state,
    setup_coordinator_data,
    setup_mock_hass,
)

# Live config on 2026-09-08 (sensor.localshift_optimizer_summary config_options
# plus the number entities), applied on top of each scenario's own overrides so
# the "dw" arm is what live would plan today, not what it planned at capture.
LIVE_OVERRIDES: dict[str, Any] = {
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
LIVE_SWITCHES: dict[str, bool] = {
    "allow_dw_entry_under_target": False,
    "demand_window_block": True,
}

DW_START = time(15, 0)
DW_END = time(21, 0)

ARMS: dict[str, dict[str, Any]] = {
    "dw": {"no_dw": False, "overrides": {}},
    "no_dw": {"no_dw": True, "overrides": {}},
    "no_dw_p25": {"no_dw": True, "overrides": {"cheap_price_percentile": 25}},
    # Diagnostic arms: which gate is stopping a price-shaped pre-charge once the
    # demand window is gone? Open the cheap-price gate, the min-cycle-saving
    # gate, or both.
    "no_dw_p100": {"no_dw": True, "overrides": {"cheap_price_percentile": 100}},
    "no_dw_mcs0": {"no_dw": True, "overrides": {"min_cycle_saving": 0}},
    "no_dw_open": {
        "no_dw": True,
        "overrides": {"cheap_price_percentile": 100, "min_cycle_saving": 0},
    },
    # Cheap gate open, but a hurdle at roughly the round-trip loss on a 10c spread.
    "no_dw_p100_mcs10": {
        "no_dw": True,
        "overrides": {"cheap_price_percentile": 100, "min_cycle_saving": 0.10},
    },
    # Gateless: the cheap-price admission gate is removed entirely, so every
    # non-DW slot offers grid charge and the DP's cost function alone decides.
    # This is what "trust the rate all day" means literally. The cycle hurdle
    # (min_cycle_saving) is the only remaining anti-cycling protection.
    "gateless_mcs25": {"no_dw": True, "gateless": True, "overrides": {}},
    "gateless_mcs10": {
        "no_dw": True,
        "gateless": True,
        "overrides": {"min_cycle_saving": 0.10},
    },
    "gateless_mcs0": {
        "no_dw": True,
        "gateless": True,
        "overrides": {"min_cycle_saving": 0.0},
    },
}

ACTION_LETTER = {
    "hold": ".",
    "hold_strict": "s",
    "charge_grid_normal": "C",
    "charge_grid_boost": "B",
    "export_proactive": "X",
}


def _cheap_threshold_gateless(
    config: Any, slot_idx: int, terminal_penalty_idx: Any
) -> float:
    """Every price is 'cheap': admission is left to the DP's cost function."""
    return 10.0


_ORIG_PROCESS = SlotBuilder._process_single_slot


def _process_single_slot_no_dw(self: SlotBuilder, *args: Any, **kwargs: Any):
    """SlotBuilder._process_single_slot with every demand-window flag cleared.

    This is the one place the planner learns that a demand window exists: the
    DP, the terminal penalty, the hard floor, the water level, the urgency
    window and the import ban all key off ``is_demand_window_slot`` /
    ``is_demand_window_entry``. Clearing them here is equivalent to a tariff
    with no demand charge, without touching any of that machinery.
    """
    ctx, counts, _in_dw = _ORIG_PROCESS(self, *args, **kwargs)
    ctx.is_demand_window_slot = False
    ctx.is_demand_window_entry = False
    return ctx, counts, False


def _seed_horizon(data: Any, payload: dict[str, Any]) -> None:
    """Seed the price horizon BEFORE the first pass.

    A fresh CoordinatorData carries forecast_horizon_hours=0.0 and the
    cheap-price calculation reads it before the optimizer facade writes the
    real span; the 0.0 collapses the cheap percentile to a tenth of its
    configured value (horizon/24 floored at 0.1). Live never sees that because
    the previous cycle's value persists — and the calculator's EMA hysteresis
    means a wrong first value is only partly corrected by later passes, so the
    seed has to be right first time.
    """
    gf = payload.get("general_forecast") or []
    if not gf:
        return
    first = datetime.fromisoformat(gf[0]["start_time"])
    last = datetime.fromisoformat(gf[-1]["start_time"])
    span_h = (last - first).total_seconds() / 3600.0 + float(
        gf[-1].get("duration", 30)
    ) / 60.0
    data.forecast_horizon_hours = max(0.0, span_h)


def run_arm(scenario: dict[str, Any], arm: dict[str, Any]) -> dict[str, Any]:
    """Run one scenario under one arm and extract the plan's behaviour."""
    payload = dict(scenario["input"])
    payload["adaptive_params"] = {}  # learning layer retired 2026-09-04

    test_time = datetime.fromisoformat(payload["test_time"])
    data = setup_coordinator_data(payload)
    hass = setup_mock_hass(payload)
    _seed_horizon(data, payload)

    overrides = dict(scenario.get("config_overrides", {}))
    overrides.update(LIVE_OVERRIDES)
    overrides.update(arm["overrides"])
    switches = dict(scenario.get("switch_states", {}))
    switches.update(LIVE_SWITCHES)
    if arm["no_dw"]:
        switches["demand_window_block"] = False

    entry = create_mock_entry(overrides)
    engine = ComputationEngine(
        hass,
        entry,
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
    if arm["no_dw"]:
        patches.append(
            patch.object(
                SlotBuilder, "_process_single_slot", _process_single_slot_no_dw
            )
        )
    if arm.get("gateless"):
        patches.append(
            patch.object(
                _constraints, "cheap_threshold_for_slot", _cheap_threshold_gateless
            )
        )

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        # Two passes: the first warms the cycle-lagged coordinator fields that
        # live carries over from the previous cycle (OptimizerConfig's
        # forecast_horizon_hours feeds the short-horizon uncertainty penalty);
        # the second is the one reported.
        engine.compute_derived_values(data)
        engine.compute_derived_values(data)

    result = data.optimizer_result or {}
    summary = getattr(data, "optimizer_summary", None) or {}
    decisions = getattr(data, "optimizer_decisions", None) or []

    return _extract(result, summary, decisions, data)


class _Tally:
    """Accumulates plan behaviour over the decision list."""

    def __init__(self) -> None:
        self.charge_kwh = 0.0
        self.charge_slots = 0
        self.charge_prices: list[float] = []
        self.charge_in_dw_kwh = 0.0
        self.import_in_dw_kwh = 0.0
        self.import_pre_dw_kwh = 0.0
        self.import_post_dw_kwh = 0.0
        self.export_slots = 0
        self.export_kwh = 0.0
        self.soc_at_dw_start: float | None = None
        self.soc_at_dw_end: float | None = None
        self.min_soc: float | None = None
        self.end_soc: float | None = None
        self.timeline: list[str] = []
        self.first_day: datetime | None = None

    def add(self, dec: dict[str, Any]) -> None:
        stamp = dec.get("timestamp_iso")
        if not stamp:
            return
        when = datetime.fromisoformat(stamp)
        if self.first_day is None:
            self.first_day = when
        # The DW that the "dw" arm plans for is the first 15:00 on/after slot 0.
        same_day = when.date() == self.first_day.date()
        in_dw = same_day and DW_START <= when.time() < DW_END
        before_dw = same_day and when.time() < DW_START
        imp = float(dec.get("grid_import_kwh") or 0.0)

        self._add_soc(dec.get("predicted_soc_pct"), before_dw, in_dw)
        self._add_flows(dec, imp, before_dw, in_dw)

        letter = ACTION_LETTER.get(dec.get("action", "hold"), "?")
        self.timeline.append("_" if in_dw and letter == "." else letter)

    def _add_soc(self, soc: Any, before_dw: bool, in_dw: bool) -> None:
        if not isinstance(soc, (int, float)):
            return
        self.min_soc = soc if self.min_soc is None else min(self.min_soc, soc)
        self.end_soc = soc
        if before_dw:
            self.soc_at_dw_start = soc
        if in_dw:
            self.soc_at_dw_end = soc

    def _add_flows(
        self, dec: dict[str, Any], imp: float, before_dw: bool, in_dw: bool
    ) -> None:
        if dec.get("grid_charge"):
            self.charge_slots += 1
            self.charge_kwh += imp
            self.charge_prices.append(float(dec.get("buy_price") or 0.0))
            if in_dw:
                self.charge_in_dw_kwh += imp
        if in_dw:
            self.import_in_dw_kwh += imp
        elif before_dw:
            self.import_pre_dw_kwh += imp
        else:
            self.import_post_dw_kwh += imp
        if dec.get("proactive_export"):
            self.export_slots += 1
            self.export_kwh += float(dec.get("grid_export_kwh") or 0.0)


def _slot_row(dec: dict[str, Any]) -> dict[str, Any]:
    terms = dec.get("objective_terms") or {}
    return {
        "t": (dec.get("timestamp_iso") or "")[11:16],
        "buy": dec.get("buy_price"),
        "sell": dec.get("sell_price"),
        "solar": dec.get("solar_kwh"),
        "cons": dec.get("consumption_kwh"),
        "act": dec.get("action"),
        "why": dec.get("reason_code"),
        "soc": dec.get("predicted_soc_pct"),
        "imp": dec.get("grid_import_kwh"),
        "exp": dec.get("grid_export_kwh"),
        "futile": terms.get("futile_cycling_penalty"),
        "net": terms.get("net_cost"),
    }


def _extract(
    result: dict[str, Any],
    summary: dict[str, Any],
    decisions: list[dict[str, Any]],
    data: Any,
) -> dict[str, Any]:
    t = _Tally()
    for dec in decisions:
        t.add(dec)
    prices = t.charge_prices
    return {
        "success": bool(result.get("success")),
        "projected_net_cost": result.get("projected_net_cost"),
        "projected_import_kwh": result.get("projected_import_kwh"),
        "projected_export_kwh": result.get("projected_export_kwh"),
        "terminal_shortfall_pct": result.get("terminal_shortfall_pct"),
        "dw_entry_soc_pct": summary.get("dw_entry_soc_pct"),
        "initial_soc_pct": summary.get("initial_soc_pct"),
        "soc_at_15": t.soc_at_dw_start,
        "soc_at_21": t.soc_at_dw_end,
        "min_soc": t.min_soc,
        "end_soc": t.end_soc,
        "charge_slots": t.charge_slots,
        "charge_kwh": round(t.charge_kwh, 3),
        "charge_price_max": round(max(prices), 4) if prices else None,
        "charge_price_mean": round(sum(prices) / len(prices), 4) if prices else None,
        "charge_in_dw_kwh": round(t.charge_in_dw_kwh, 3),
        "import_pre_dw_kwh": round(t.import_pre_dw_kwh, 3),
        "import_in_dw_kwh": round(t.import_in_dw_kwh, 3),
        "import_post_dw_kwh": round(t.import_post_dw_kwh, 3),
        "export_slots": t.export_slots,
        "export_kwh": round(t.export_kwh, 3),
        "effective_cheap_price": getattr(data, "effective_cheap_price", None),
        "base_cheap_price": getattr(data, "base_cheap_price", None),
        "timeline": "".join(t.timeline),
        "n_slots": len(decisions),
        "slots": [_slot_row(dec) for dec in decisions],
    }


def fmt(value: Any, spec: str = "6.2f") -> str:
    return format(value, spec) if isinstance(value, (int, float)) else "   n/a"


HEADER = (
    f"{'day':<11}{'arm':<17}{'cost':>8}{'imp':>7}{'exp':>7}"
    f"{'SOC15':>7}{'SOC21':>7}{'min':>6}{'chg':>5}{'chgkWh':>8}"
    f"{'maxP':>7}{'impDW':>7}{'impPost':>8}{'cheap':>7}"
)


def _print_row(day: str, name: str, out: dict[str, Any]) -> None:
    if "error" in out:
        print(f"{day:<11}{name:<17}ERROR {out['error'][:60]}")
        return
    print(
        f"{day:<11}{name:<17}"
        f"{fmt(out['projected_net_cost'], '8.3f')}"
        f"{fmt(out['projected_import_kwh'], '7.2f')}"
        f"{fmt(out['projected_export_kwh'], '7.2f')}"
        f"{fmt(out['soc_at_15'], '7.1f')}"
        f"{fmt(out['soc_at_21'], '7.1f')}"
        f"{fmt(out['min_soc'], '6.1f')}"
        f"{out['charge_slots']:>5}"
        f"{fmt(out['charge_kwh'], '8.2f')}"
        f"{fmt(out['charge_price_max'], '7.3f')}"
        f"{fmt(out['import_in_dw_kwh'], '7.2f')}"
        f"{fmt(out['import_post_dw_kwh'], '8.2f')}"
        f"{fmt(out['effective_cheap_price'], '7.3f')}"
    )


def _print_detail(results: dict[str, dict[str, Any]]) -> None:
    for day, by_arm in results.items():
        for name, out in by_arm.items():
            if "slots" not in out:
                continue
            print(f"\n=== {day} {name} ===")
            print(
                f"{'t':<6}{'buy':>7}{'sell':>7}{'solar':>7}{'cons':>7}"
                f"{'act':<20}{'why':<28}{'soc':>6}{'imp':>6}{'exp':>6}{'futile':>8}"
            )
            for s in out["slots"]:
                print(
                    f"{s['t']:<6}{fmt(s['buy'], '7.3f')}{fmt(s['sell'], '7.3f')}"
                    f"{fmt(s['solar'], '7.2f')}{fmt(s['cons'], '7.2f')}"
                    f"{str(s['act']):<20}{str(s['why']):<28}"
                    f"{fmt(s['soc'], '6.1f')}{fmt(s['imp'], '6.2f')}"
                    f"{fmt(s['exp'], '6.2f')}{fmt(s['futile'], '8.3f')}"
                )


def _print_timelines(results: dict[str, dict[str, Any]]) -> None:
    print(
        "\ntimelines (slot 0 = capture time; '.' hold  '_' hold inside 15-21"
        "  C/B grid charge  X export)"
    )
    for day, by_arm in results.items():
        for name, out in by_arm.items():
            if "timeline" in out:
                print(f"  {day} {name:<16} {out['timeline']}")
        print()


def _print_deltas(results: dict[str, dict[str, Any]], a_name: str, b_name: str) -> None:
    print(f"{b_name} minus {a_name}, per day")
    tot = {"cost": 0.0, "chg": 0.0}
    for day, by_arm in results.items():
        a, b = by_arm.get(a_name, {}), by_arm.get(b_name, {})
        if not a or not b or "error" in a or "error" in b:
            print(f"  {day}: incomparable")
            continue
        dc = (b.get("projected_net_cost") or 0) - (a.get("projected_net_cost") or 0)
        dk = (b.get("charge_kwh") or 0) - (a.get("charge_kwh") or 0)
        tot["cost"] += dc
        tot["chg"] += dk
        print(
            f"  {day}: cost {dc:+.3f}  grid-charge kWh {dk:+.2f}  "
            f"SOC@15 {fmt(a.get('soc_at_15'), '5.1f')} -> "
            f"{fmt(b.get('soc_at_15'), '5.1f')}  "
            f"import 15-21 {fmt(a.get('import_in_dw_kwh'), '4.2f')} -> "
            f"{fmt(b.get('import_in_dw_kwh'), '4.2f')}"
        )
    print(f"  total: cost {tot['cost']:+.3f}  grid-charge kWh {tot['chg']:+.2f}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="simulations/replay-nodw")
    parser.add_argument("--json", help="also write full results here")
    parser.add_argument(
        "--arms", default="dw,no_dw,no_dw_p25", help="comma-separated subset of arms"
    )
    parser.add_argument("--day", help="only this day (file stem, e.g. 2026-09-07)")
    parser.add_argument(
        "--detail", action="store_true", help="dump every slot of every arm"
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    arms = {k: ARMS[k] for k in args.arms.split(",")}

    paths = sorted(Path(args.dir).glob("*.json"))
    if args.day:
        paths = [p for p in paths if p.stem == args.day]
    if not paths:
        sys.exit(
            f"ERROR: no replay scenarios in {args.dir} — run export_replay_days.py"
        )

    results: dict[str, dict[str, Any]] = {}
    print(HEADER)
    print("-" * len(HEADER))
    for path in paths:
        scenario = json.loads(path.read_text())
        day = path.stem
        results[day] = {}
        for name, arm in arms.items():
            try:
                out = run_arm(scenario, arm)
            except Exception as exc:  # noqa: BLE001 - report, keep going
                out = {
                    "error": f"{type(exc).__name__}: {exc}",
                    "trace": traceback.format_exc(),
                }
            results[day][name] = out
            _print_row(day, name, out)
        print("-" * len(HEADER))

    if args.detail:
        _print_detail(results)
    _print_timelines(results)
    baseline = next(iter(arms))
    for other in list(arms)[1:]:
        _print_deltas(results, baseline, other)

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2, default=str) + "\n")
        print(f"\nfull results -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

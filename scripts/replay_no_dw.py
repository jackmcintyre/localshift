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
  block           the slice 1 design (docs/PRICE_BLOCK_TARGET.md): no clock
                  demand window, switch.localshift_price_block_target ON, so
                  the detected expensive block sets the deadline and the
                  load-sized target; block_min_spread $0.08, cycle hurdle $0.25

Whenever both ``dw`` and ``block`` run, the slice 1 gate is printed: per day,
whether the block plan is identical to, below or above ``dw``, and how much the
block arm grid-charges overnight (21:00-06:00, the #800 sawtooth check).

``--flap`` runs the block arm over several captures of one day in time order
and carries the block's state (adopted block, pending change, remembered
trough) from each capture to the next, as live carries it from plan to plan.
The verdict is read from that carried run; the same captures planned cold, each
alone, are printed beside it.

``--flap DIR`` runs the block arm over captures of the SAME day taken at
different hours (scripts/export_replay_days.py --date D --time 09:00,11:00,...)
in time order and reports, per consecutive pair, whether the block's entry time
and the committed slot-0 action changed. PASS when the entry moves by at most
one 30-minute slot between consecutive captures and the slot-0 action never
goes charge / hold / charge.

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
  uv run scripts/replay_no_dw.py --arms dw,no_dw,block     # slice 1 gate
  uv run scripts/replay_no_dw.py --flap simulations/replay-nodw-flap
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
from custom_components.localshift.engine.slots import (  # noqa: E402
    PRICE_BLOCK_STATE_FIELDS,
    SlotBuilder,
)
from custom_components.localshift.engine.target_block import (  # noqa: E402
    remember_trough,
)
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
# Overnight, for the #800 sawtooth check: from the end of the evening until
# solar could plausibly be charging the battery again.
OVERNIGHT_START = time(21, 0)
OVERNIGHT_END = time(6, 0)

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
    # Slice 1 of docs/PRICE_BLOCK_TARGET.md: the live config with the clock
    # window gone and the price block in its place. The switch clears the clock
    # window's flags itself; no_dw also takes the window out of everything
    # upstream of the slots, so nothing the block arm sees depends on 15:00.
    "block": {
        "no_dw": True,
        "switches": {"price_block_target": True},
        "overrides": {"block_min_spread": 0.08, "min_cycle_saving": 0.25},
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


def run_arm(
    scenario: dict[str, Any],
    arm: dict[str, Any],
    previous_entry_iso: str | None = None,
    block_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one scenario under one arm and extract the plan's behaviour.

    ``previous_entry_iso`` seeds the price block's hysteresis, as if the plan
    before this one had entered the block at that time. ``block_state`` seeds
    everything the price block carries between plans
    (``PRICE_BLOCK_STATE_FIELDS``), as an earlier run returned it under
    ``price_block_state``.
    """
    payload = dict(scenario["input"])
    payload["adaptive_params"] = {}  # learning layer retired 2026-09-04

    test_time = datetime.fromisoformat(payload["test_time"])
    data = setup_coordinator_data(payload)
    hass = setup_mock_hass(payload)
    _seed_horizon(data, payload)
    if previous_entry_iso is not None:
        data.target_block_entry_iso = previous_entry_iso
    for name, value in (block_state or {}).items():
        setattr(data, name, list(value) if isinstance(value, list) else value)

    overrides = dict(scenario.get("config_overrides", {}))
    overrides.update(LIVE_OVERRIDES)
    overrides.update(arm["overrides"])
    switches = dict(scenario.get("switch_states", {}))
    switches.update(LIVE_SWITCHES)
    if arm["no_dw"]:
        switches["demand_window_block"] = False
    switches.update(arm.get("switches", {}))

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


def _is_overnight(when: datetime) -> bool:
    clock = when.time()
    return clock >= OVERNIGHT_START or clock < OVERNIGHT_END


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
        self.overnight_charge_kwh = 0.0
        self.overnight_charge_slots = 0
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
        if dec.get("grid_charge") and _is_overnight(when):
            self.overnight_charge_kwh += imp
            self.overnight_charge_slots += 1

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
        "overnight_charge_kwh": round(t.overnight_charge_kwh, 3),
        "overnight_charge_slots": t.overnight_charge_slots,
        "slot0_action": decisions[0].get("action") if decisions else None,
        "target_block_active": bool(summary.get("target_block_active")),
        "target_block_entry": summary.get("target_block_entry"),
        "target_block_end": summary.get("target_block_end"),
        "target_block_target_pct": summary.get("target_block_target_pct"),
        "target_block_needed_kwh": summary.get("target_block_needed_kwh"),
        "target_block_trough_price": summary.get("target_block_trough_price"),
        "target_block_trough_at": summary.get("target_block_trough_at"),
        "target_block_pending_change": summary.get("target_block_pending_change"),
        "target_block_pending_entry": summary.get("target_block_pending_entry"),
        "target_block_pending_since": summary.get("target_block_pending_since"),
        "price_block_state": {
            name: getattr(data, name, None) for name in PRICE_BLOCK_STATE_FIELDS
        },
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


def _delta_pairs(arm_names: list[str]) -> list[tuple[str, str]]:
    """(a, b) pairs to print as "b minus a": the first arm against every other,
    plus block minus dw whenever both ran, whichever order they were given in.
    """
    baseline = arm_names[0]
    pairs = [(baseline, other) for other in arm_names[1:]]
    gate_pair = ("dw", "block")
    if set(gate_pair) <= set(arm_names) and gate_pair not in pairs:
        pairs.append(gate_pair)
    return pairs


def _plan_of(out: dict[str, Any]) -> list[tuple[Any, ...]]:
    """What a plan does slot by slot, without the reason text."""
    return [
        (s.get("t"), s.get("act"), s.get("soc"), s.get("imp"), s.get("exp"))
        for s in out.get("slots", [])
    ]


def _relation(dw: dict[str, Any], block: dict[str, Any], delta: float) -> str:
    if delta > 0:
        return "above"
    if delta < 0:
        return "below"
    return (
        "identical" if _plan_of(dw) == _plan_of(block) else "equal cost, different plan"
    )


def gate_row(day: str, by_arm: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One day of the slice 1 gate: the block arm against the dw arm.

    ``ok`` is the part of the acceptance that holds on every day: the block
    costs no more than ``dw`` and grid-charges nothing overnight. Whether a day
    that had to be ``identical`` was, is read off ``relation``.
    """
    dw, block = by_arm["dw"], by_arm["block"]
    if "error" in dw or "error" in block:
        return {"day": day, "relation": "incomparable", "ok": False}
    dw_cost = dw.get("projected_net_cost")
    block_cost = block.get("projected_net_cost")
    if dw_cost is None or block_cost is None:
        return {"day": day, "relation": "incomparable", "ok": False}
    delta = block_cost - dw_cost
    overnight = float(block.get("overnight_charge_kwh") or 0.0)
    return {
        "day": day,
        "dw": dw_cost,
        "block": block_cost,
        "delta": delta,
        "relation": _relation(dw, block, delta),
        "overnight_charge_kwh": overnight,
        "block_entry": block.get("target_block_entry"),
        "block_end": block.get("target_block_end"),
        "ok": delta <= 0 and overnight == 0,
    }


def gate_rows(results: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Gate rows for every day on which both the dw and block arms ran."""
    return [
        gate_row(day, by_arm)
        for day, by_arm in results.items()
        if "dw" in by_arm and "block" in by_arm
    ]


def _clock(stamp: Any) -> str:
    return datetime.fromisoformat(stamp).strftime("%H:%M") if stamp else "-"


def _print_gate(results: dict[str, dict[str, Any]]) -> bool | None:
    """Print the slice 1 gate. None when the dw and block arms did not both run."""
    rows = gate_rows(results)
    if not rows:
        return None
    print("\nslice 1 gate: block against dw (docs/PRICE_BLOCK_TARGET.md)")
    for row in rows:
        if row["relation"] == "incomparable":
            print(f"  {row['day']}: incomparable  FAIL")
            continue
        print(
            f"  {row['day']}: dw {row['dw']:.3f}  block {row['block']:.3f}  "
            f"delta {row['delta']:+.3f}  {row['relation']:<27}"
            f"block {_clock(row['block_entry'])}-{_clock(row['block_end'])}  "
            f"overnight grid-charge {row['overnight_charge_kwh']:.2f} kWh  "
            f"{'ok' if row['ok'] else 'FAIL'}"
        )
    passed = all(row["ok"] for row in rows)
    identical = sum(row["relation"] == "identical" for row in rows)
    print(
        f"slice 1 gate: {'PASS' if passed else 'FAIL'} "
        f"({len(rows)} day(s): {identical} identical to dw, "
        f"{sum(row['relation'] == 'below' for row in rows)} below, "
        f"{sum(row['relation'] == 'above' for row in rows)} above)"
    )
    return passed


# ---------------------------------------------------------------------------
# Flap test: the same day captured at several hours (#1111)
# ---------------------------------------------------------------------------

FLAP_DIR = "simulations/replay-nodw-flap"
FLAP_SLOT_MINUTES = 30  # the block's entry sits on the 30-minute part of the horizon


def load_flap_days(directory: Path) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """Captures in ``directory`` grouped by local day, each day in time order.

    Ordered by each capture's own ``test_time``, not by file name.
    """
    stamped = []
    for path in sorted(Path(directory).glob("*.json")):
        scenario = json.loads(path.read_text())
        at = datetime.fromisoformat(scenario["input"]["test_time"])
        stamped.append((at, path.name, scenario))
    stamped.sort(key=lambda item: item[0])
    days: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for at, name, scenario in stamped:
        days.setdefault(at.date().isoformat(), []).append((name, scenario))
    return days


def past_prices(scenario: dict[str, Any]) -> list[tuple[str, float]]:
    """Prices the capture itself records for intervals before its capture time.

    A capture is the price sensor's state at one instant. Its forecast list
    starts at the interval the sensor was last updated in, so it opens with the
    five-minute interval(s) that had ended by the capture time, at the price
    live saw as current. That is the only past price a capture holds: usually
    one interval, a few when the sensor was a little stale, and nothing from
    earlier in the day.
    """
    captured_at = datetime.fromisoformat(scenario["input"]["test_time"])
    past = []
    for row in scenario["input"].get("general_forecast") or []:
        if datetime.fromisoformat(row["start_time"]) < captured_at:
            past.append((row["start_time"], float(row["per_kwh"])))
    return past


def _with_past_prices(
    state: dict[str, Any], scenario: dict[str, Any]
) -> dict[str, Any]:
    """``state`` with the capture's own past prices added to the trough memory.

    Live plans every minute and would have observed these as its current slot.
    Skipped while the memory is suspended inside a block, as live skips them.
    """
    if state.get("target_block_trough_resume_iso"):
        return state
    history = list(state.get("target_block_trough") or [])
    for stamp, price in past_prices(scenario):
        history = remember_trough(history, stamp, price)
    return {**state, "target_block_trough": history}


def flap_points(captures: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Run the block arm on each capture of one day, in the order given.

    Each capture is planned twice, and the ``dw`` arm once for the cost beside
    it (``dw_cost``, and ``slot0_action_dw`` as the control for the slot-0
    criterion: the captures start from the SOC live reached under the clock
    window, so an alternation the ``dw`` arm shows too is not the block's).

    ``entry`` / ``slot0_action`` are a cold start: the detector sees only that
    capture and remembers nothing, which is how #1111 measured it.

    The ``*_carried`` fields are the run the verdict is read from. The block's
    state (``PRICE_BLOCK_STATE_FIELDS``: the adopted block, a pending change
    and its clock, the remembered trough) is handed from each capture to the
    next, as live hands it from plan to plan. There is no price history before
    the first capture, so the first one starts cold apart from the past price
    it records itself (``past_prices``); nothing earlier is assumed.
    """
    arm = ARMS["block"]
    points: list[dict[str, Any]] = []
    state: dict[str, Any] = {}
    for name, scenario in captures:
        cold = run_arm(scenario, arm)
        held = run_arm(scenario, arm, block_state=_with_past_prices(state, scenario))
        dw = run_arm(scenario, ARMS["dw"])
        state = held["price_block_state"]
        points.append({
            "capture": name,
            "at": scenario["input"]["test_time"],
            "soc": scenario["input"].get("soc"),
            "slot0_price": held["slots"][0]["buy"] if held["slots"] else None,
            "slot0_action": cold["slot0_action"],
            "entry": cold["target_block_entry"],
            "end": cold["target_block_end"],
            "target_pct": cold["target_block_target_pct"],
            "cost": cold["projected_net_cost"],
            "charge_kwh": cold["charge_kwh"],
            "entry_carried": held["target_block_entry"],
            "end_carried": held["target_block_end"],
            "slot0_action_carried": held["slot0_action"],
            "target_pct_carried": held["target_block_target_pct"],
            "cost_carried": held["projected_net_cost"],
            "charge_kwh_carried": held["charge_kwh"],
            "charge_price_max_carried": held["charge_price_max"],
            "trough_price": held["target_block_trough_price"],
            "trough_at": held["target_block_trough_at"],
            "pending_change": held["target_block_pending_change"],
            "pending_entry": held["target_block_pending_entry"],
            "pending_since": held["target_block_pending_since"],
            "dw_cost": dw["projected_net_cost"],
            "dw_charge_kwh": dw["charge_kwh"],
            "slot0_action_dw": dw["slot0_action"],
        })
    return points


def _is_charge(action: Any) -> bool:
    return str(action).startswith("charge_grid")


def _charge_resumes(actions: list[Any]) -> bool:
    """True when grid charging stops and then starts again: charge / hold / charge."""
    runs = 0
    previous = False
    for action in actions:
        charging = _is_charge(action)
        if charging and not previous:
            runs += 1
        previous = charging
    return runs > 1


def _entry_move_minutes(a: str | None, b: str | None) -> int | None:
    """Minutes the entry moved from ``a`` to ``b``; None if a block came or went."""
    if a is None and b is None:
        return 0
    if a is None or b is None:
        return None
    delta = datetime.fromisoformat(b) - datetime.fromisoformat(a)
    return int(delta.total_seconds() // 60)


def _flap_pair(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    moved = _entry_move_minutes(a["entry"], b["entry"])
    return {
        "from": a["at"],
        "to": b["at"],
        "from_entry": a["entry"],
        "to_entry": b["entry"],
        "entry_moved_min": moved,
        "entry_ok": moved is not None and abs(moved) <= FLAP_SLOT_MINUTES,
        "from_action": a["slot0_action"],
        "to_action": b["slot0_action"],
        "action_changed": a["slot0_action"] != b["slot0_action"],
    }


def _pair_label(pair: dict[str, Any]) -> str:
    return f"{_clock(pair['from'])} -> {_clock(pair['to'])}"


def flap_verdict(points: list[dict[str, Any]]) -> dict[str, Any]:
    """PASS / FAIL / INCONCLUSIVE for one day's captures.

    PASS: between every consecutive pair the block's entry moves by at most one
    slot, and the slot-0 action never goes charge / hold / charge. A block that
    appears or disappears between two captures has moved by more than a slot.
    INCONCLUSIVE: fewer than two captures, or no capture found a block, so
    there was no boundary to flap.
    """
    ordered = sorted(points, key=lambda p: datetime.fromisoformat(p["at"]))
    pairs = [_flap_pair(a, b) for a, b in zip(ordered, ordered[1:], strict=False)]
    alternates = _charge_resumes([p["slot0_action"] for p in ordered])

    reasons = []
    for pair in pairs:
        if pair["entry_ok"]:
            continue
        moved = pair["entry_moved_min"]
        what = (
            "block appeared or disappeared"
            if moved is None
            else f"block entry moved {moved:+d} min, more than one slot"
        )
        reasons.append(f"{_pair_label(pair)}: {what}")
    if alternates:
        sequence = " / ".join(str(p["slot0_action"]) for p in ordered)
        reasons.append(f"slot-0 action goes charge / hold / charge: {sequence}")

    if len(ordered) < 2 or all(p["entry"] is None for p in ordered):
        verdict = "INCONCLUSIVE"
    else:
        verdict = "FAIL" if reasons else "PASS"
    return {
        "verdict": verdict,
        "pairs": pairs,
        "action_alternates": alternates,
        "reasons": reasons,
    }


def flap_carried_verdict(points: list[dict[str, Any]]) -> dict[str, Any]:
    """``flap_verdict`` on the run that carried the block's state between captures."""
    return flap_verdict([
        {**p, "entry": p["entry_carried"], "slot0_action": p["slot0_action_carried"]}
        for p in points
    ])


def _block_span(entry: Any, end: Any) -> str:
    return f"{_clock(entry)}-{_clock(end)}"


def _pending_text(point: dict[str, Any]) -> str:
    change = point["pending_change"]
    if not change:
        return "-"
    target = "" if change == "disappear" else f" {_clock(point['pending_entry'])}"
    return f"{change}{target} since {_clock(point['pending_since'])}"


def _print_flap_day(day: str, points: list[dict[str, Any]]) -> str:
    """Print one day and return its verdict, which is the carried run's."""
    cold = flap_verdict(points)
    carried = flap_carried_verdict(points)
    print(f"\nflap test {day} (block arm, {len(points)} capture(s))")
    print(
        f"  {'capture':<8}{'SOC':>6}  {'cold: block':<13}{'slot-0':<20}"
        f"{'carried: block':<16}{'slot-0':<20}{'target':>7}{'cost':>8}"
        f"{'chg kWh':>9}{'dw cost':>9}  trough, pending"
    )
    for p in points:
        trough = (
            f"{p['trough_price'] * 100:.1f}c at {_clock(p['trough_at'])}"
            if isinstance(p["trough_price"], (int, float))
            else "none"
        )
        print(
            f"  {_clock(p['at']):<8}{fmt(p['soc'], '6.1f')}  "
            f"{_block_span(p['entry'], p['end']):<13}"
            f"{str(p['slot0_action']):<20}"
            f"{_block_span(p['entry_carried'], p['end_carried']):<16}"
            f"{str(p['slot0_action_carried']):<20}"
            f"{fmt(p['target_pct_carried'], '7.1f')}"
            f"{fmt(p['cost_carried'], '8.3f')}"
            f"{fmt(p['charge_kwh_carried'], '9.2f')}"
            f"{fmt(p['dw_cost'], '9.3f')}  "
            f"{trough}, {_pending_text(p)}"
        )
    for pair in carried["pairs"]:
        moved = pair["entry_moved_min"]
        print(
            f"  {_pair_label(pair)}: entry {_clock(pair['from_entry'])} -> "
            f"{_clock(pair['to_entry'])} "
            f"({'n/a' if moved is None else format(moved, '+d') + ' min'}, "
            f"{'ok' if pair['entry_ok'] else 'FAIL'})  "
            f"slot-0 {pair['from_action']} -> {pair['to_action']} "
            f"({'changed' if pair['action_changed'] else 'same'})"
        )
    for reason in carried["reasons"]:
        print(f"  reason: {reason}")
    dw_actions = [p["slot0_action_dw"] for p in points]
    print(
        "  control, dw arm slot-0 goes charge / hold / charge: "
        f"{'yes' if _charge_resumes(dw_actions) else 'no'} "
        f"({' / '.join(ACTION_LETTER.get(a, '?') for a in dw_actions)})"
    )
    print(f"  cold start, each capture alone: {cold['verdict']}")
    for reason in cold["reasons"]:
        print(f"    cold: {reason}")
    print(f"flap test {day}: {carried['verdict']}")
    return carried["verdict"]


def run_flap(directory: Path) -> bool:
    """Run the flap test on every day captured in ``directory``. True if all PASS.

    The verdict is the one with the block's state carried between captures.
    """
    days = load_flap_days(directory)
    if not days:
        print(f"flap captures not present in {directory}")
        return False
    verdicts = [_print_flap_day(day, flap_points(caps)) for day, caps in days.items()]
    return all(verdict == "PASS" for verdict in verdicts)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
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
    parser.add_argument(
        "--flap",
        nargs="?",
        const=FLAP_DIR,
        metavar="DIR",
        help=f"flap test over same-day captures in DIR (default {FLAP_DIR})",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = _parse_args()
    if args.flap:
        return 0 if run_flap(Path(args.flap)) else 1
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
    for baseline, other in _delta_pairs(list(arms)):
        _print_deltas(results, baseline, other)
    _print_gate(results)

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2, default=str) + "\n")
        print(f"\nfull results -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

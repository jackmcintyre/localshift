"""Spike-event pre-charge funding.

Built from the live 2026-08-05 horizon, where a forecast $1.65/kWh print at 07:30
was met at the 10% SOC floor: the plan imported straight through the whole
05:30-09:00 block, which carried 71% of the projected day cost. Root cause was
not a bad price signal — it was that ``feasible_actions`` never offered the DP a
charge action in those overnight slots, because the only thing that widens the
cheap-price gate is the demand-window funder and a morning spike is not a demand
window.

The flat-morning case is the load-bearing test. A naive widening (admit charging
ahead of any dearer interval) reproduces the #800 overnight sawtooth exactly —
measured on this fixture: six consecutive overnight charge slots plus proactive
export, import $1.08 -> $2.51. Everything here that asserts inertness exists to
stop that shipping.
"""

from __future__ import annotations

import pytest

from custom_components.localshift.engine.optimizer_dp import (
    DPPlanner,
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)
from custom_components.localshift.engine.spike_event import (
    find_funding_slots,
    required_spread,
)

CHARGE_ACTIONS = {PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST}

# Live buy prices, 2026-08-05 13:17 Sydney (sensor.localshift_forecast_prices).
# 10 x 5-min slots from 13:15, then 30-min slots through to 13:00 next day.
_BUY = [
    0.12,
    0.12,
    0.12,
    0.12,
    0.12,
    0.12,
    0.12,
    0.12,
    0.12,
    0.12,
    0.13,
    0.13,
    0.13,
    0.14,
    0.19,
    0.25,
    0.29,
    0.33,
    0.26,
    0.29,
    0.35,
    0.34,
    0.41,
    0.34,
    0.40,
    0.27,
    0.36,
    0.23,
    0.24,
    0.21,
    0.20,
    0.21,
    0.20,
    0.19,
    0.18,
    0.17,
    0.18,
    0.19,
    0.18,
    0.19,
    0.21,
    0.31,
    0.61,
    0.76,
    1.65,
    0.63,
    0.26,
    0.40,
    0.20,
    0.17,
    0.15,
    0.14,
    0.12,
    0.12,
    0.11,
    0.20,
]
SPIKE_IDX = 44  # 07:30, $1.65
MORNING_BLOCK = range(40, 48)  # 05:30-09:00
DW_ENTRY, DW_EXIT = 11, 23  # today's 15:00-21:00 demand window

# Load/solar back-derived from the live plan's objective terms; cross-checks
# against the observed 100% -> 10% battery excursion (12.15 kWh) to within 4%.
_LOAD_KW = {
    0: 0.64,
    1: 0.64,
    2: 0.62,
    3: 0.62,
    4: 0.62,
    5: 0.64,
    6: 0.64,
    7: 0.82,
    8: 0.62,
    9: 0.60,
    10: 0.60,
    11: 0.60,
    12: 0.60,
    13: 0.70,
    14: 0.70,
    15: 0.90,
    16: 1.10,
    17: 1.45,
    18: 1.35,
    19: 1.50,
    20: 1.45,
    21: 1.40,
    22: 1.30,
    23: 0.90,
}
_SOLAR_KW = {
    6: 0.0,
    7: 0.05,
    8: 0.55,
    9: 1.40,
    10: 2.40,
    11: 3.10,
    12: 3.30,
    13: 3.10,
    14: 2.40,
    15: 1.50,
    16: 0.70,
    17: 0.10,
}
_FLAT_MORNING = {41: 0.22, 42: 0.24, 43: 0.26, 44: 0.25, 45: 0.23}


def _hour_of(idx: int) -> int:
    """Wall-clock hour for a slot index, matching the live slot schedule."""
    if idx < 10:
        return 13
    minutes = 14 * 60 + 30 + (idx - 10) * 30
    return (minutes // 60) % 24


def _slots(price_overrides: dict[int, float] | None = None) -> list[SlotContext]:
    overrides = price_overrides or {}
    out = []
    for idx, base in enumerate(_BUY):
        buy = overrides.get(idx, base)
        minutes = 5 if idx < 10 else 30
        hours = minutes / 60.0
        hour = _hour_of(idx)
        out.append(
            SlotContext(
                slot_index=idx,
                timestamp_iso=f"2026-08-05T{hour:02d}:00:00",
                slot_interval_minutes=minutes,
                buy_price=buy,
                sell_price=round(max(0.0, min(buy - 0.08, buy * 0.87)), 4),
                solar_kwh=round(_SOLAR_KW.get(hour, 0.0) * hours, 4),
                consumption_kwh=round(_LOAD_KW[hour] * hours, 4),
                is_demand_window_entry=(idx == DW_ENTRY),
                is_demand_window_slot=(DW_ENTRY <= idx <= DW_EXIT),
            )
        )
    return out


def _config(**overrides) -> OptimizerConfig:
    base = dict(
        battery_capacity_kwh=13.5,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=95.0,
        optimization_mode="self_consumption",
        switching_penalty=0.08,
        target_shortfall_penalty_per_pct=0.10,
        min_cycle_saving=0.25,
        max_precharge_price=0.20,
        effective_cheap_price=0.16,
        base_cheap_price=0.16,
        soc_bins=100,
    )
    base.update(overrides)
    return OptimizerConfig(**base)


def _plan(price_overrides=None, initial_soc=57.225, **config_overrides):
    return DPPlanner().plan(
        OptimizerInputs(
            cycle_id="spike-test",
            initial_soc_pct=initial_soc,
            slots=_slots(price_overrides),
            config=_config(**config_overrides),
        )
    )


def _charges(result):
    return [d.slot_index for d in result.decisions if d.action in CHARGE_ACTIONS]


def _block_import(result):
    return sum(
        result.decisions[i].objective_terms.import_cost
        for i in MORNING_BLOCK
        if result.decisions[i].objective_terms
    )


# --- detection -----------------------------------------------------------------------


def test_required_spread_grosses_up_for_round_trip_losses():
    """min_cycle_saving is per kWh cycled; a bought kWh only returns ~87% of itself."""
    cfg = _config(
        min_cycle_saving=0.25, charge_efficiency=0.92, discharge_efficiency=0.95
    )
    assert required_spread(cfg) == pytest.approx(0.25 / (0.92 * 0.95), rel=1e-9)
    assert required_spread(cfg) > 0.25


def test_detects_funding_slots_on_the_live_spike_horizon():
    assert find_funding_slots(_slots(), _config())


def test_detects_nothing_when_the_morning_is_ordinary():
    """The #800 guard: an ordinary morning must qualify NOTHING."""
    assert find_funding_slots(_slots(_FLAT_MORNING), _config()) == frozenset()


def test_demand_window_slots_are_never_funding_slots():
    """Grid import is forbidden inside the DW, and DW energy has its own funder."""
    funding = find_funding_slots(_slots(), _config())
    assert not any(DW_ENTRY <= i <= DW_EXIT for i in funding)


def test_inert_outside_self_consumption_mode():
    assert (
        find_funding_slots(_slots(), _config(optimization_mode="arbitrage"))
        == frozenset()
    )


def test_inert_when_min_cycle_saving_is_disabled():
    """Without an operator-stated cycle bar there is no qualification test to apply."""
    assert find_funding_slots(_slots(), _config(min_cycle_saving=0.0)) == frozenset()


# --- planning ------------------------------------------------------------------------


def test_baseline_reproduces_the_incident():
    """Without the feature the plan sits at the floor and imports through the spike."""
    result = _plan(spike_precharge_enabled=False)
    assert result.success
    assert result.decisions[SPIKE_IDX].action == PlannerAction.HOLD
    assert result.decisions[SPIKE_IDX].predicted_soc_pct == pytest.approx(10.0, abs=0.5)
    assert result.decisions[SPIKE_IDX].objective_terms.import_cost > 0.5


def test_spike_funding_serves_the_spike_from_the_battery():
    enabled = _plan()
    baseline = _plan(spike_precharge_enabled=False)

    assert enabled.spike_funding_accepted
    assert enabled.projected_net_cost < baseline.projected_net_cost
    # The battery, not the grid, covers the $1.65 slot.
    assert enabled.decisions[SPIKE_IDX].objective_terms.import_cost == pytest.approx(
        0.0, abs=1e-6
    )
    assert _block_import(enabled) < _block_import(baseline) / 2


def test_spike_funding_adds_minimal_charging():
    """Widening eligibility must not turn into promiscuous overnight charging.

    Many slots qualify; the DP is expected to take very few. A regression here is
    the #800 sawtooth returning.
    """
    added = set(_charges(_plan())) - set(_charges(_plan(spike_precharge_enabled=False)))
    assert len(added) <= 2, f"expected a minimal top-up, got {sorted(added)}"
    assert all(i < SPIKE_IDX for i in added)


def test_ordinary_morning_plan_is_untouched():
    """The regression that matters: no spike -> byte-identical plan."""
    enabled = _plan(_FLAT_MORNING)
    baseline = _plan(_FLAT_MORNING, spike_precharge_enabled=False)
    assert [d.action for d in enabled.decisions] == [
        d.action for d in baseline.decisions
    ]
    assert enabled.projected_net_cost == pytest.approx(baseline.projected_net_cost)
    assert enabled.spike_funding_slot_count == 0
    assert not enabled.spike_funding_accepted


def test_kill_switch_restores_legacy_behaviour_exactly():
    off = _plan(spike_precharge_enabled=False)
    assert off.spike_funding_slot_count == 0
    assert not off.spike_funding_accepted
    assert off.decisions[SPIKE_IDX].objective_terms.import_cost > 0.5


def test_guard_never_accepts_a_worse_plan():
    """The core safety property.

    Qualification alone is not safe — a 200-scenario sweep found ~4% of firing
    scenarios came out WORSE than the baseline (worst $0.49), because the
    min-cycle-saving gate makes this DP non-monotone: enlarging the feasible set can
    change which plan wins. The guard removes that by construction, so this must hold
    for every scenario, not merely the happy path.
    """
    for overrides in (None, _FLAT_MORNING, {SPIKE_IDX: 0.30}, {SPIKE_IDX: 5.00}):
        for soc in (15.0, 57.225, 90.0):
            enabled = _plan(overrides, initial_soc=soc)
            baseline = _plan(overrides, initial_soc=soc, spike_precharge_enabled=False)
            assert enabled.projected_net_cost <= baseline.projected_net_cost + 1e-9, (
                f"guard admitted a worse plan (overrides={overrides}, soc={soc})"
            )


def test_low_soc_does_not_trigger_panic_buying():
    """Starting near the floor must not turn the widening into indiscriminate charging."""
    enabled = _plan(initial_soc=12.0)
    baseline = _plan(initial_soc=12.0, spike_precharge_enabled=False)
    assert enabled.projected_net_cost <= baseline.projected_net_cost
    added = set(_charges(enabled)) - set(_charges(baseline))
    assert len(added) <= 2, f"expected a bounded top-up, got {sorted(added)}"


def test_tie_is_distinguishable_from_a_guard_rejection():
    """``accepted=False`` alone must not be read as "the guard caught a bad plan".

    ~40% of qualifying cycles end in a dead tie (the DP declines the extra option).
    Conflating those with genuine rejections turns a benign outcome into what looks
    like a 40% failure rate in the field, so the delta carries the real signal.
    """
    accepted = _plan()
    assert accepted.spike_funding_accepted
    assert accepted.spike_funding_net_cost_delta > 0

    # Nothing qualifies -> no verdict to report at all.
    inert = _plan(_FLAT_MORNING)
    assert inert.spike_funding_slot_count == 0
    assert inert.spike_funding_net_cost_delta == 0.0


def test_guard_records_a_non_positive_delta_when_it_declines():
    """Whenever the guard keeps the baseline, the delta must justify that choice."""
    for overrides in (None, _FLAT_MORNING, {SPIKE_IDX: 0.30}):
        for soc in (15.0, 57.225, 90.0):
            result = _plan(overrides, initial_soc=soc)
            if result.spike_funding_slot_count and not result.spike_funding_accepted:
                assert result.spike_funding_net_cost_delta <= 0

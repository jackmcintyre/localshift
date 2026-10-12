"""Load-sized export floor.

Battery export may not drain below what the house is forecast to need for the
rest of the elevated-price period, times a margin. The floor is a discharge
floor for the EXPORT action only: HOLD still serves load beneath it, and it
never unlocks grid charging. Consult docs/PLANNING_MODEL.md for the constraint
model this plugs into.
"""

from __future__ import annotations

import pytest

from custom_components.localshift.const import (
    EXPORT_FLOOR_ELEVATED_PRICE_FACTOR,
    EXPORT_FLOOR_LOAD_MARGIN,
)
from custom_components.localshift.engine.constraints import feasible_actions
from custom_components.localshift.engine.export_floor import (
    apply_export_floor,
    compute_export_floor_pct_by_slot,
)
from custom_components.localshift.engine.optimizer_dp import (
    DPPlanner,
    OptimizerInputs,
)
from custom_components.localshift.engine.transitions import _transition_export
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)


def _slot(
    idx: int = 0,
    buy: float = 0.20,
    sell: float = 0.10,
    solar: float = 0.0,
    consumption: float = 0.0,
    interval_minutes: int = 30,
    export_floor_pct: float = 0.0,
) -> SlotContext:
    return SlotContext(
        slot_index=idx,
        timestamp_iso=f"2026-10-12T{idx:02d}:00:00",
        slot_interval_minutes=interval_minutes,
        buy_price=buy,
        sell_price=sell,
        solar_kwh=solar,
        consumption_kwh=consumption,
        export_floor_pct=export_floor_pct,
    )


def _config(**overrides) -> OptimizerConfig:
    base = dict(
        battery_capacity_kwh=10.0,
        demand_window_target_soc_pct=80.0,
        soc_bins=100,
        optimization_mode="arbitrage",
        min_soc_pct=5.0,
        max_soc_pct=100.0,
        discharge_rate_kw=5.0,
        discharge_efficiency=1.0,
    )
    base.update(overrides)
    return OptimizerConfig(**base)


def _evening(prices: list[float], load_kwh: float = 0.5) -> list[SlotContext]:
    """Half-hour slots with the given buy prices and a flat load."""
    return [
        _slot(idx=i, buy=p, sell=max(0.0, p - 0.1), consumption=load_kwh)
        for i, p in enumerate(prices)
    ]


# ---------------------------------------------------------------------------
# compute_export_floor_pct_by_slot
# ---------------------------------------------------------------------------


def test_flat_prices_leave_the_floor_at_the_discharge_floor():
    slots = _evening([0.20] * 8)
    floors = compute_export_floor_pct_by_slot(slots, _config())
    assert floors == [5.0] * 8


def test_floor_covers_later_elevated_load_with_margin():
    # Median is 0.20, so the four $1.00 slots are elevated.
    slots = _evening([1.00, 1.00, 1.00, 1.00] + [0.20] * 8)
    floors = compute_export_floor_pct_by_slot(slots, _config())

    # Slot 0 must leave slots 1-3: 3 x 0.5 kWh x margin on a 10 kWh pack.
    expected = 5.0 + 1.5 * EXPORT_FLOOR_LOAD_MARGIN / 10.0 * 100.0
    assert floors[0] == pytest.approx(expected)
    # The floor releases as the period runs out.
    assert floors[0] > floors[1] > floors[2] > floors[3]
    # The last elevated slot has nothing left to protect.
    assert floors[3] == pytest.approx(5.0)
    assert floors[4:] == [5.0] * 8


def test_elevated_threshold_is_relative_to_the_median():
    just_under = 0.20 * EXPORT_FLOOR_ELEVATED_PRICE_FACTOR - 0.01
    slots = _evening([just_under] * 3 + [0.20] * 8)
    floors = compute_export_floor_pct_by_slot(slots, _config())
    assert floors == [5.0] * 11


def test_solar_covering_load_needs_no_reserve():
    slots = _evening([1.00] * 4 + [0.20] * 8)
    for s in slots[:4]:
        s.solar_kwh = 2.0
    floors = compute_export_floor_pct_by_slot(slots, _config())
    assert floors[0] == pytest.approx(5.0)


def test_discharge_efficiency_raises_the_floor():
    slots = _evening([1.00] * 4 + [0.20] * 8)
    lossless = compute_export_floor_pct_by_slot(slots, _config())
    lossy = compute_export_floor_pct_by_slot(slots, _config(discharge_efficiency=0.5))
    assert lossy[0] - 5.0 == pytest.approx((lossless[0] - 5.0) * 2.0)


def test_short_price_dip_does_not_end_the_period():
    # One normal half-hour inside the peak: still one elevated period.
    slots = _evening([1.00, 1.00, 0.20, 1.00, 1.00] + [0.20] * 8)
    floors = compute_export_floor_pct_by_slot(slots, _config())
    # Slot 0 protects slots 1, 3 and 4 (the dip slot itself is cheap to buy).
    expected = 5.0 + 1.5 * EXPORT_FLOOR_LOAD_MARGIN / 10.0 * 100.0
    assert floors[0] == pytest.approx(expected)


def test_a_separate_later_peak_is_not_protected():
    # Two hours of normal prices: tomorrow's peak is not tonight's problem.
    slots = _evening([1.00, 1.00] + [0.20] * 4 + [1.00, 1.00] + [0.20] * 8)
    floors = compute_export_floor_pct_by_slot(slots, _config())
    expected = 5.0 + 0.5 * EXPORT_FLOOR_LOAD_MARGIN / 10.0 * 100.0
    assert floors[0] == pytest.approx(expected)


def test_extreme_sell_price_releases_the_floor():
    # 2026-10-12: a $24 feed-in price must not be held back for $3 evening load.
    slots = _evening([26.0] + [3.00] * 4 + [0.20] * 8)
    slots[0].sell_price = 24.0
    floors = compute_export_floor_pct_by_slot(slots, _config())
    assert floors[0] == pytest.approx(5.0)
    # The $3 slots still protect each other.
    assert floors[1] > 5.0


def test_later_slot_at_half_the_sell_price_is_still_protected():
    slots = _evening([2.10, 1.00, 1.00] + [0.20] * 8)
    slots[0].sell_price = 2.00
    floors = compute_export_floor_pct_by_slot(slots, _config())
    expected = 5.0 + 1.0 * EXPORT_FLOOR_LOAD_MARGIN / 10.0 * 100.0
    assert floors[0] == pytest.approx(expected)


def test_floor_is_capped_at_max_soc():
    slots = _evening([1.00] * 12 + [0.20] * 14, load_kwh=3.0)
    floors = compute_export_floor_pct_by_slot(slots, _config())
    assert max(floors) == pytest.approx(100.0)


def test_zero_margin_disables_the_floor():
    slots = _evening([1.00] * 4 + [0.20] * 8)
    floors = compute_export_floor_pct_by_slot(
        slots, _config(export_floor_load_margin=0.0)
    )
    assert floors == [5.0] * 12


def test_away_floor_is_the_base():
    slots = _evening([0.20] * 4)
    floors = compute_export_floor_pct_by_slot(
        slots, _config(away_reserve_floor_pct=30.0)
    )
    assert floors == [30.0] * 4


def test_apply_writes_the_floor_onto_each_slot():
    slots = _evening([1.00] * 4 + [0.20] * 8)
    apply_export_floor(slots, _config())
    assert slots[0].export_floor_pct > 5.0
    assert slots[-1].export_floor_pct == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# feasible_actions and the EXPORT transition
# ---------------------------------------------------------------------------


def test_export_is_infeasible_at_or_below_the_slot_floor():
    slot = _slot(sell=1.0, export_floor_pct=40.0)
    assert PlannerAction.EXPORT_PROACTIVE not in feasible_actions(40.0, slot, _config())
    assert PlannerAction.EXPORT_PROACTIVE in feasible_actions(41.0, slot, _config())


def test_export_transition_stops_at_the_slot_floor():
    # 5 kW for 30 min could shed 25 points; the floor allows 10.
    slot = _slot(sell=1.0, export_floor_pct=40.0)
    next_soc, grid_import, grid_export = _transition_export(50.0, slot, _config())
    assert next_soc == pytest.approx(40.0)
    assert grid_import == 0.0
    assert grid_export == pytest.approx(1.0)


def test_export_transition_without_a_slot_floor_is_unchanged():
    slot = _slot(sell=1.0)
    next_soc, _, grid_export = _transition_export(50.0, slot, _config())
    assert next_soc == pytest.approx(25.0)
    assert grid_export == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def _spike_evening() -> list[SlotContext]:
    """Four expensive half-hours with load, then a long normal night."""
    prices = [1.50, 1.40, 1.30, 1.20] + [0.20] * 20
    return [
        _slot(idx=i, buy=p, sell=p - 0.10, consumption=0.5)
        for i, p in enumerate(prices)
    ]


def _plan(config: OptimizerConfig):
    result = DPPlanner().plan(
        OptimizerInputs(
            cycle_id="export-floor",
            slots=_spike_evening(),
            initial_soc_pct=100.0,
            config=config,
        )
    )
    assert result.success
    return result


def test_planner_keeps_the_house_supplied_through_the_peak():
    result = _plan(_config())

    peak = result.decisions[:4]
    assert any(d.action == PlannerAction.EXPORT_PROACTIVE for d in peak)
    # Forecast load through the peak is met from the battery, not the grid.
    assert sum(d.grid_import_kwh for d in peak) == pytest.approx(0.0, abs=1e-6)
    # The margin is still held going into the last expensive slot, and is only
    # released there, once nothing dear is left to protect.
    assert peak[-2].predicted_soc_pct > 5.0 + 1.0


def test_planner_without_the_floor_drains_to_minimum():
    result = _plan(_config(export_floor_load_margin=0.0))
    assert min(d.predicted_soc_pct for d in result.decisions[:4]) == pytest.approx(
        5.0, abs=1.5
    )


def test_planner_publishes_the_current_floor():
    config = _config()
    _plan(config)
    assert config.export_floor_pct_now > 5.0

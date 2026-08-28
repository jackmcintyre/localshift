"""Tests for ``cost.py`` stage_cost and terminal_cost."""

import pytest

from custom_components.localshift.engine.cost import stage_cost, terminal_cost
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)


def test_stage_cost_negative_fit_real_cost():
    """Negative sell_price produces negative export revenue (real cost)."""
    config = OptimizerConfig(optimization_mode="arbitrage")
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.10,
        sell_price=-0.05,
        solar_kwh=0.0,
        consumption_kwh=0.0,
    )
    terms = stage_cost(
        action=PlannerAction.EXPORT_PROACTIVE,
        grid_import_kwh=0.0,
        grid_export_kwh=2.0,
        slot=slot,
        config=config,
    )
    assert terms.export_revenue == 2.0 * (-0.05)
    assert terms.net_cost > 0


def test_stage_cost_positive_fit_positive_revenue():
    """Positive sell_price produces positive export revenue (net_cost < 0 = profit)."""
    config = OptimizerConfig(optimization_mode="arbitrage")
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.10,
        sell_price=0.12,  # Must be positive to be profitable
        solar_kwh=0.0,
        consumption_kwh=0.0,
    )
    terms = stage_cost(
        action=PlannerAction.EXPORT_PROACTIVE,
        grid_import_kwh=0.0,
        grid_export_kwh=2.0,
        slot=slot,
        config=config,
    )
    assert terms.export_revenue == 2.0 * 0.12
    # Revenue ($0.24) exceeds cycle penalty ($0.16), so net_cost < 0 (profit)
    assert terms.net_cost < 0


def test_stage_cost_charge_includes_uncertainty_and_futile_penalties():
    """Grid charge applies uncertainty and futile-cycling penalties."""
    config = OptimizerConfig(
        optimization_mode="self_consumption",
        forecast_horizon_hours=10.0,  # below 20h threshold -> uncertainty penalty
    )
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.20,
        sell_price=0.05,
        solar_kwh=0.0,
        consumption_kwh=0.4,
    )
    terms = stage_cost(
        action=PlannerAction.CHARGE_GRID_NORMAL,
        grid_import_kwh=1.0,
        grid_export_kwh=0.0,
        slot=slot,
        config=config,
        futile_cycling_penalty_factor=1.0,
    )

    assert terms.import_cost == 0.20
    assert terms.uncertainty_penalty > 0.0
    assert terms.futile_cycling_penalty > 0.0


def test_stage_cost_boost_charge_branch_covered():
    """Boost charge follows the same uncertainty branch."""
    config = OptimizerConfig(
        optimization_mode="self_consumption",
        forecast_horizon_hours=5.0,
    )
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.18,
        sell_price=0.04,
        solar_kwh=0.0,
        consumption_kwh=0.3,
    )
    terms = stage_cost(
        action=PlannerAction.CHARGE_GRID_BOOST,
        grid_import_kwh=0.8,
        grid_export_kwh=0.0,
        slot=slot,
        config=config,
    )

    assert terms.uncertainty_penalty > 0.0


def test_stage_cost_self_consumption_value_is_soc_capped():
    """SOC cap limits self-consumption credit when available energy is low."""
    config = OptimizerConfig(
        optimization_mode="self_consumption",
        min_soc_pct=10.0,
        battery_capacity_kwh=13.5,
        discharge_rate_kw=5.0,
    )
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.30,
        sell_price=0.05,
        solar_kwh=0.0,
        consumption_kwh=3.0,  # large demand
    )

    # soc_pct only 11% gives tiny usable battery energy above min SOC.
    terms = stage_cost(
        action=PlannerAction.HOLD,
        grid_import_kwh=0.0,
        grid_export_kwh=0.0,
        slot=slot,
        config=config,
        soc_pct=11.0,
    )

    assert terms.self_consumption_value > 0.0
    # credit must be bounded; not full net-load credit (3.0 * 0.30 = 0.90)
    assert terms.self_consumption_value < 0.90


def test_stage_cost_self_consumption_value_zero_when_no_net_load():
    """No positive net load means no self-consumption credit."""
    config = OptimizerConfig(optimization_mode="self_consumption")
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.20,
        sell_price=0.05,
        solar_kwh=1.0,
        consumption_kwh=0.2,
    )

    terms = stage_cost(
        action=PlannerAction.HOLD,
        grid_import_kwh=0.0,
        grid_export_kwh=0.0,
        slot=slot,
        config=config,
        soc_pct=80.0,
    )
    assert terms.self_consumption_value == 0.0


def test_stage_cost_switching_penalty_applied_on_switch():
    """Switch flag adds switching penalty."""
    config = OptimizerConfig(optimization_mode="arbitrage", switching_penalty=0.123)
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.10,
        sell_price=0.10,
        solar_kwh=0.0,
        consumption_kwh=0.0,
    )
    terms = stage_cost(
        action=PlannerAction.HOLD,
        grid_import_kwh=0.0,
        grid_export_kwh=0.0,
        slot=slot,
        config=config,
        is_switch=True,
    )
    assert terms.switching_penalty == 0.123


def test_stage_cost_switching_penalty_scale_aware_floor_dominates():
    """When switching_penalty_per_kwh is set, the effective penalty is the max of
    the flat knob and the slot-energy-scaled floor. Per #919: the flat $0.08
    knob was too small relative to Amber price jitter to damp SC↔X flips."""
    config = OptimizerConfig(
        switching_penalty=0.08,
        switching_penalty_per_kwh=0.40,
        charge_rate_kw=5.0,
        discharge_rate_kw=5.0,
    )
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T12:00:00",
        slot_interval_minutes=15,  # 0.25 h => 5.0 * 0.25 = 1.25 kWh
        buy_price=0.10,
        sell_price=0.06,
        solar_kwh=0.0,
        consumption_kwh=0.0,
    )
    terms = stage_cost(
        action=PlannerAction.HOLD,
        grid_import_kwh=0.0,
        grid_export_kwh=0.0,
        slot=slot,
        config=config,
        is_switch=True,
    )
    # 0.40 $/kWh * 5 kW * 0.25 h = $0.50 > flat $0.08 => floor wins.
    assert terms.switching_penalty == pytest.approx(0.50)


def test_stage_cost_switching_penalty_flat_dominates_when_per_kwh_off():
    """When per-kWh floor is zero, existing flat-knob behaviour is preserved
    exactly (regression guard for tests that set switching_penalty directly)."""
    config = OptimizerConfig(
        switching_penalty=0.123,
        switching_penalty_per_kwh=0.0,
    )
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.10,
        sell_price=0.10,
        solar_kwh=0.0,
        consumption_kwh=0.0,
    )
    terms = stage_cost(
        action=PlannerAction.HOLD,
        grid_import_kwh=0.0,
        grid_export_kwh=0.0,
        slot=slot,
        config=config,
        is_switch=True,
    )
    assert terms.switching_penalty == pytest.approx(0.123)


def test_stage_cost_switching_penalty_slot_duration_scales():
    """Effective penalty scales with slot duration: same $/kWh floor but shorter
    slot carries proportionally less hurdle (preserves the $/kWh semantics)."""
    config = OptimizerConfig(
        switching_penalty=0.08,
        switching_penalty_per_kwh=0.40,
        charge_rate_kw=5.0,
        discharge_rate_kw=5.0,
    )
    # 5-min Amber slot: 5.0 kW * (5/60) h = 0.4167 kWh => 0.40 * 0.4167 ≈ 0.1667
    slot_5min = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T12:00:00",
        slot_interval_minutes=5,
        buy_price=0.10,
        sell_price=0.06,
        solar_kwh=0.0,
        consumption_kwh=0.0,
    )
    terms_5 = stage_cost(
        action=PlannerAction.HOLD,
        grid_import_kwh=0.0,
        grid_export_kwh=0.0,
        slot=slot_5min,
        config=config,
        is_switch=True,
    )
    assert terms_5.switching_penalty == pytest.approx(0.40 * 5.0 * 5.0 / 60.0)

    # 15-min slot should be exactly 3x the 5-min hurdle.
    slot_15 = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T12:00:00",
        slot_interval_minutes=15,
        buy_price=0.10,
        sell_price=0.06,
        solar_kwh=0.0,
        consumption_kwh=0.0,
    )
    terms_15 = stage_cost(
        action=PlannerAction.HOLD,
        grid_import_kwh=0.0,
        grid_export_kwh=0.0,
        slot=slot_15,
        config=config,
        is_switch=True,
    )
    assert terms_15.switching_penalty == pytest.approx(3.0 * terms_5.switching_penalty)


def test_stage_cost_no_switch_ignores_floor():
    """When is_switch=False the flat and per-kWh knobs are both ignored."""
    config = OptimizerConfig(
        switching_penalty=0.08,
        switching_penalty_per_kwh=0.40,
        charge_rate_kw=5.0,
        discharge_rate_kw=5.0,
    )
    slot = SlotContext(
        slot_index=1,
        timestamp_iso="2026-01-03T12:15:00",
        slot_interval_minutes=15,
        buy_price=0.10,
        sell_price=0.06,
        solar_kwh=0.0,
        consumption_kwh=0.0,
    )
    terms = stage_cost(
        action=PlannerAction.CHARGE_GRID_NORMAL,
        grid_import_kwh=1.0,
        grid_export_kwh=0.0,
        slot=slot,
        config=config,
        is_switch=False,
    )
    assert terms.switching_penalty == 0.0


def test_terminal_cost_shortfall_and_no_shortfall():
    """Terminal penalty is linear in shortfall and zero when target met."""
    config = OptimizerConfig(target_shortfall_penalty_per_pct=0.015)

    assert terminal_cost(final_soc_pct=92.0, target_soc_pct=90.0, config=config) == 0.0
    assert terminal_cost(final_soc_pct=80.0, target_soc_pct=90.0, config=config) == 0.15


def test_hold_strict_transition_preserves_soc_on_deficit():
    """HOLD_STRICT preserves SOC by importing load deficit from grid."""
    from custom_components.localshift.engine.transitions import transition
    from custom_components.localshift.engine.types import OptimizerConfig, PlannerAction, SlotContext

    config = OptimizerConfig(min_hold_saving=0.10)
    slot = SlotContext(
        slot_index=0,
        slot_interval_minutes=30,
        timestamp_iso="2024-01-01T00:00:00+00:00",
        buy_price=0.15,
        sell_price=0.07,
        solar_kwh=0.0,
        consumption_kwh=0.5,  # 0.5 kWh deficit
    )
    next_soc, grid_import, grid_export = transition(50.0, PlannerAction.HOLD_STRICT, slot, config)
    # SOC should be preserved (no discharge)
    assert next_soc == 50.0
    # Entire deficit imported from grid
    assert grid_import == 0.5
    assert grid_export == 0.0


def test_hold_strict_transition_absorbs_solar_surplus():
    """HOLD_STRICT still absorbs solar surplus into battery."""
    from custom_components.localshift.engine.transitions import transition
    from custom_components.localshift.engine.types import OptimizerConfig, PlannerAction, SlotContext

    config = OptimizerConfig(min_hold_saving=0.10, battery_capacity_kwh=13.5)
    slot = SlotContext(
        slot_index=0,
        slot_interval_minutes=30,
        timestamp_iso="2024-01-01T00:00:00+00:00",
        buy_price=0.15,
        sell_price=0.07,
        solar_kwh=1.0,
        consumption_kwh=0.3,  # net +0.7 kWh surplus
    )
    next_soc, grid_import, grid_export = transition(50.0, PlannerAction.HOLD_STRICT, slot, config)
    # SOC should increase (solar absorbed)
    assert next_soc > 50.0
    assert grid_import == 0.0
    # Some surplus may be exported if battery full
    assert grid_export >= 0.0


def test_hold_strict_min_saving_gate_blocks_when_saving_below_threshold():
    """HOLD_STRICT is blocked when cost saving vs HOLD is positive but below threshold."""
    from custom_components.localshift.engine.core import _min_hold_saving_blocks
    from custom_components.localshift.engine.types import OptimizerConfig, PlannerAction

    config = OptimizerConfig(min_hold_saving=0.50)
    # HOLD_STRICT costs $0.10 LESS than HOLD (saving=0.1), but threshold is $0.50
    # saving=0.1 is positive but < 0.50 -> blocked
    assert _min_hold_saving_blocks(
        PlannerAction.HOLD_STRICT, total_cost=0.8, hold_total_cost=0.9, config=config
    ) is True


def test_hold_strict_min_saving_gate_allows_when_saving_above_threshold():
    """HOLD_STRICT is allowed when cost saving vs HOLD exceeds min_hold_saving."""
    from custom_components.localshift.engine.core import _min_hold_saving_blocks
    from custom_components.localshift.engine.types import OptimizerConfig, PlannerAction

    config = OptimizerConfig(min_hold_saving=0.05)
    # saving = 0.10 > 0.05*1.0 = 0.05 -> NOT blocked
    assert _min_hold_saving_blocks(
        PlannerAction.HOLD_STRICT, total_cost=0.8, hold_total_cost=0.9, config=config
    ) is False


def test_hold_strict_min_saving_gate_disabled_at_zero():
    """Gate is inactive when min_hold_saving is 0.0."""
    from custom_components.localshift.engine.core import _min_hold_saving_blocks
    from custom_components.localshift.engine.types import OptimizerConfig, PlannerAction

    config = OptimizerConfig(min_hold_saving=0.0)
    assert _min_hold_saving_blocks(
        PlannerAction.HOLD_STRICT, total_cost=0.8, hold_total_cost=0.9, config=config
    ) is False


def test_hold_strict_not_a_charge_action():
    """HOLD_STRICT is not in _GRID_CHARGE_ACTIONS and is not blocked by min_cycle_saving."""
    from custom_components.localshift.engine.core import _GRID_CHARGE_ACTIONS
    from custom_components.localshift.engine.types import PlannerAction

    assert PlannerAction.HOLD_STRICT not in _GRID_CHARGE_ACTIONS

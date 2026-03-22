"""Tests for ``cost.py`` stage_cost and terminal_cost."""

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
        sell_price=0.12,  # Must exceed cycle_penalty ($0.08) to be profitable
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
    assert terms.cycle_penalty == config.cycle_penalty_per_kwh
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
    # credit must be bounded; not full net-load credit (3.0 * (0.30-0.08)=0.66)
    assert terms.self_consumption_value < 0.66


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


def test_terminal_cost_shortfall_and_no_shortfall():
    """Terminal penalty is linear in shortfall and zero when target met."""
    config = OptimizerConfig(target_shortfall_penalty_per_pct=0.015)

    assert terminal_cost(final_soc_pct=92.0, target_soc_pct=90.0, config=config) == 0.0
    assert terminal_cost(final_soc_pct=80.0, target_soc_pct=90.0, config=config) == 0.15

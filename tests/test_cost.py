"""Tests for cost.py stage_cost and terminal_cost."""

from custom_components.localshift.engine.cost import stage_cost
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

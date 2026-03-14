"""Constraint unit tests for negative-FIT avoidance."""
from __future__ import annotations

import pytest

from custom_components.localshift.engine.constraints import feasible_actions
from custom_components.localshift.engine.types import (
    NegativeFitAvoidanceContext,
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)


@pytest.fixture
def default_config() -> OptimizerConfig:
    """Reusable default optimizer config."""
    return OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        soc_bins=20,
        optimization_mode="self_consumption",
        export_price_margin=0.02,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
    )


def _make_test_slot(sell_price: float, is_demand_window_slot: bool = False) -> SlotContext:
    """Minimal slot for constraint tests."""
    return SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.10,
        sell_price=sell_price,
        solar_kwh=0.0,
        consumption_kwh=0.0,
        is_demand_window_slot=is_demand_window_slot,
    )


def test_feasible_actions_positive_fit_before_negative(default_config):
    """Allows EXPORT_PROACTIVE at positive FIT before first negative-FIT window."""
    slot = _make_test_slot(sell_price=0.08)
    context = NegativeFitAvoidanceContext(
        first_negative_fit_slot_idx=5,
        conservative_overflow_kwh=10.0,
        allowed_headroom_pct=5.0,
        temporary_floor_pct=75.0,
    )
    actions = feasible_actions(
        soc_pct=90.0,
        slot=slot,
        config=default_config,
        slot_idx=0,
        slots=None,
        terminal_penalty_idx=None,
        negative_fit_avoidance_context=context,
    )
    assert PlannerAction.EXPORT_PROACTIVE in actions


def test_feasible_actions_negative_fit_floor_blocked(default_config):
    """Blocks EXPORT_PROACTIVE at sell_price <= 0 (hard floor)."""
    slot = _make_test_slot(sell_price=0.0)
    context = NegativeFitAvoidanceContext(
        first_negative_fit_slot_idx=5,
        conservative_overflow_kwh=10.0,
        allowed_headroom_pct=5.0,
        temporary_floor_pct=75.0,
    )
    actions = feasible_actions(
        soc_pct=90.0,
        slot=slot,
        config=default_config,
        slot_idx=0,
        slots=None,
        terminal_penalty_idx=None,
        negative_fit_avoidance_context=context,
    )
    assert PlannerAction.EXPORT_PROACTIVE not in actions


def test_feasible_actions_temporary_floor_enforced(default_config):
    """Blocks EXPORT_PROACTIVE when SOC would fall below temporary_floor_pct."""
    slot = _make_test_slot(sell_price=0.08)
    context = NegativeFitAvoidanceContext(
        first_negative_fit_slot_idx=5,
        conservative_overflow_kwh=10.0,
        allowed_headroom_pct=5.0,
        temporary_floor_pct=75.0,
    )
    actions = feasible_actions(
        soc_pct=74.0,
        slot=slot,
        config=default_config,
        slot_idx=0,
        slots=None,
        terminal_penalty_idx=None,
        negative_fit_avoidance_context=context,
    )
    assert PlannerAction.EXPORT_PROACTIVE not in actions


def test_feasible_actions_normal_rules_after_negative_window(default_config):
    """Uses normal rules for slots at/after first negative-FIT window."""
    slot = _make_test_slot(sell_price=0.08)
    context = NegativeFitAvoidanceContext(
        first_negative_fit_slot_idx=2,
        conservative_overflow_kwh=10.0,
        allowed_headroom_pct=5.0,
        temporary_floor_pct=75.0,
    )
    actions = feasible_actions(
        soc_pct=90.0,
        slot=slot,
        config=default_config,
        slot_idx=2,
        slots=None,
        terminal_penalty_idx=None,
        negative_fit_avoidance_context=context,
    )
    min_profitable = max(0.0, slot.buy_price) + default_config.export_price_margin
    if slot.sell_price >= min_profitable:
        assert PlannerAction.EXPORT_PROACTIVE in actions
    else:
        assert PlannerAction.EXPORT_PROACTIVE not in actions

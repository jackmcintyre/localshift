"""Constraint unit tests for negative-FIT avoidance."""

from __future__ import annotations

# Reuse broad constraints coverage from engine-level test module so the
# hook-scoped coverage check reflects full constraints behavior.
from tests.engine.test_constraints import *  # noqa: F403

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


def _make_test_slot(
    sell_price: float, is_demand_window_slot: bool = False
) -> SlotContext:
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


def _make_context(
    risk_start: int = 5,
    risk_end: int = 10,
    recovery_by_slot: list[float] | None = None,
    floor_by_slot: list[float] | None = None,
) -> NegativeFitAvoidanceContext:
    """Create a valid NegativeFitAvoidanceContext for tests."""
    if recovery_by_slot is None:
        recovery_by_slot = [50.0] * 15
    if floor_by_slot is None:
        floor_by_slot = [20.0] * 15
    return NegativeFitAvoidanceContext(
        risk_window_start_idx=risk_start,
        risk_window_end_idx=risk_end,
        required_headroom_kwh=5.0,
        recovery_deadline_idx=14,
        conservative_recovery_kwh_by_slot=tuple(recovery_by_slot),
        recoverability_floor_pct_by_slot=tuple(floor_by_slot),
    )


def test_feasible_actions_positive_fit_before_risk_window(default_config):
    """Allows EXPORT_PROACTIVE at positive FIT before risk window when SOC above floor."""
    slot = _make_test_slot(sell_price=0.08)
    context = _make_context(risk_start=5, recovery_by_slot=[20.0] * 15)
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


def test_feasible_actions_negative_fit_blocked(default_config):
    """Blocks EXPORT_PROACTIVE at sell_price <= 0 (hard floor)."""
    slot = _make_test_slot(sell_price=0.0)
    context = _make_context()
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


def test_feasible_actions_recoverability_floor_enforced(default_config):
    """Blocks EXPORT_PROACTIVE when SOC would fall below recoverability floor."""
    slot = _make_test_slot(sell_price=0.08)
    context = _make_context(
        risk_start=5,
        recovery_by_slot=[5.0] * 15,
        floor_by_slot=[40.0] * 15,
    )
    actions = feasible_actions(
        soc_pct=30.0,
        slot=slot,
        config=default_config,
        slot_idx=0,
        slots=None,
        terminal_penalty_idx=None,
        negative_fit_avoidance_context=context,
    )
    assert PlannerAction.EXPORT_PROACTIVE not in actions


def test_feasible_actions_normal_rules_during_risk_window(default_config):
    """Uses normal rules for slots during risk window (at/after risk start)."""
    slot = _make_test_slot(sell_price=0.08)
    context = _make_context(risk_start=2)
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


def test_feasible_actions_context_none_uses_normal_rules(default_config):
    """When context is None, uses normal proactive export rules."""
    slot = _make_test_slot(sell_price=0.08)
    actions = feasible_actions(
        soc_pct=90.0,
        slot=slot,
        config=default_config,
        slot_idx=0,
        slots=None,
        terminal_penalty_idx=None,
        negative_fit_avoidance_context=None,
    )
    min_profitable = max(0.0, slot.buy_price) + default_config.export_price_margin
    if slot.sell_price >= min_profitable:
        assert PlannerAction.EXPORT_PROACTIVE in actions
    else:
        assert PlannerAction.EXPORT_PROACTIVE not in actions


def test_feasible_actions_dw_requires_min_net_benefit_in_avoidance(default_config):
    """Demand-window export in avoidance mode requires minimum net benefit."""
    slot = _make_test_slot(sell_price=0.14, is_demand_window_slot=True)
    slot.buy_price = 0.13
    context = _make_context(risk_start=5, recovery_by_slot=[20.0] * 15)
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


def test_feasible_actions_dw_allows_export_above_min_net_benefit(default_config):
    """Demand-window export is allowed in avoidance mode when net benefit is high."""
    slot = _make_test_slot(sell_price=0.16, is_demand_window_slot=True)
    slot.buy_price = 0.13
    context = _make_context(risk_start=5, recovery_by_slot=[20.0] * 15)
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

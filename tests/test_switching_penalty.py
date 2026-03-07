"""Tests for mode switching disincentives and trigger refinements.

Issue #524: Reduce Frequent Battery Mode Switching.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.computation_engine_lib.optimizer_dp import (
    DPPlanner,
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)
from custom_components.localshift.const import (
    BatteryMode,
)
from custom_components.localshift.coordinator_data import CoordinatorData
from custom_components.localshift.state_machine import StateMachine

# ---------------------------------------------------------------------------
# DP Planner Switching Penalty Tests
# ---------------------------------------------------------------------------


def test_dp_planner_applies_switching_penalty():
    """Test that DPPlanner adds switching_penalty when action differs from current."""
    config = OptimizerConfig(
        switching_penalty=0.50,  # High penalty to make it obvious
        cycle_penalty_per_kwh=0.0,
        soc_bins=10,
    )

    # Create a slot with neutral prices (no incentive to charge or export)
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=60,
        buy_price=0.10,  # Neutral price
        sell_price=0.06,
        solar_kwh=0.1,
        consumption_kwh=0.1,
    )

    planner = DPPlanner()

    # Case 1: Current action is HOLD. HOLD should be preferred due to no switching penalty.
    inputs_current_hold = OptimizerInputs(
        cycle_id="test_no_switch",
        initial_soc_pct=50.0,
        slots=[slot],
        current_action=PlannerAction.HOLD,
        config=config,
    )
    result_no_switch = planner.plan(inputs_current_hold)

    # Case 2: Current action is CHARGE_GRID_NORMAL. HOLD is "better" but incurs penalty.
    # Raw cost of HOLD: import_cost(0.0) - revenue(0.0) - sc_value(0.0) + cycle(0.0) = 0
    # Raw cost of CHARGE: import(0.1) - rev(0.0) - scv(0.0) + cycle(0.0) = 0.1
    # With switching penalty for HOLD: 0 + 0.5 = 0.5
    # So CHARGE (0.1) < HOLD (0.5) ==> CHARGE should win when current=HOLD.

    # For a fair test: make HOLD and CHARGE nearly equivalent by making import_price 0.0
    slot_no_charge_cost = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=60,
        buy_price=0.0,  # No import cost
        sell_price=0.0,
        solar_kwh=0.0,
        consumption_kwh=1.0,  # Force decision based on grid interaction
    )

    # When current is CHARGE, switching to HOLD costs penalty.
    inputs_current_charge = OptimizerInputs(
        cycle_id="test_switch_penalty",
        initial_soc_pct=50.0,
        slots=[slot_no_charge_cost],
        current_action=PlannerAction.CHARGE_GRID_NORMAL,
        config=config,
    )
    result_with_penalty = planner.plan(inputs_current_charge)

    # When current action is CHARGE and both actions are otherwise neutral,
    # HOLD pays a penalty of 0.50. So planner should select CHARGE instead.
    assert result_with_penalty.decisions[0].action == PlannerAction.CHARGE_GRID_NORMAL
    # The penalty for switching from CHARGE in first slot should be 0 (since we're staying).
    # When we are looking at what action to take in the first slot, if that action differs
    # from current_action, it pays the penalty on that transition.
    # So if current_action=CHARGE and decision=HOLD -> HOLD decision gets big penalty for switching.
    # If current_action=HOLD and decision=HOLD -> no switching penalty -> HOLD wins slightly
    # If current_action=HOLD and decision=CHARGE -> CHARGE decision gets penalty, possibly loses
    # The penalty is added to the cost of the *slot* where the action differs from current.

    # Let's construct the opposite case to be clearer.
    # If we are currently HOLDING and we could CHARGE, but the switching penalty is bigger
    # than the benefit of CHARGING, it should stay in HOLD.

    slot_better_charge = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=60,
        buy_price=0.05,  # Cheaper, attractive for charging
        sell_price=0.06,  # Slightly positive for exporting
        solar_kwh=0.01,
        consumption_kwh=1.0,
    )

    # Simulate situation where HOLD has cost of 0.0, CHARGE has cost of -0.01 (negative = benefit)
    # So without penalty: CHARGE wins.
    # With penalty = 0.50 for a switch from HOLD -> CHARGE, the cost becomes -0.01 + 0.50 = 0.49.
    # So HOLD (0.0) beats CHARGE (0.49) because of the penalty.
    inputs_hold_current_should_prefer_hold = OptimizerInputs(
        cycle_id="test_penalize_switch",
        initial_soc_pct=50.0,
        slots=[slot_better_charge],
        current_action=PlannerAction.HOLD,  # Currently we are holding
        config=config,
    )
    result_penalize_switch = planner.plan(inputs_hold_current_should_prefer_hold)
    # Despite grid charging being "cheaper", the penalty should make HOLD preferred.
    # Since the cost differential is very small (-0.01 vs 0.0), the penalty of 0.5 will dominate.
    assert result_penalize_switch.decisions[0].action == PlannerAction.HOLD


def test_objective_terms_includes_switching_penalty():
    """Test that ObjectiveTerms serializes switching_penalty correctly."""
    from custom_components.localshift.computation_engine_lib.optimizer_dp import (
        ObjectiveTerms,
    )

    terms = ObjectiveTerms(import_cost=0.10, switching_penalty=0.05)
    assert terms.net_cost == pytest.approx(0.15)
    d = terms.to_dict()
    assert d["switching_penalty"] == 0.05
    assert d["net_cost"] == pytest.approx(0.15)


# ---------------------------------------------------------------------------
# State Machine Minimum Duration Tests (Dry Run)
# ---------------------------------------------------------------------------

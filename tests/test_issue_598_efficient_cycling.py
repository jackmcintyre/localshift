"""
tests/test_issue_598_efficient_cycling.py — Tests for Issue #598

Prevent unnecessary overnight battery cycling by:
1. Adding round-trip efficiency penalty to CHARGE actions
2. Using actual slot buy_price for self_consumption_value

These changes make the cost function accurately reflect the economics
so the DP naturally avoids marginal short-term cycles.
"""

from __future__ import annotations

import pytest

from custom_components.localshift.computation_engine_lib.optimizer_dp import (
    DPPlanner,
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    PlannerReasonCode,
    SlotContext,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def issue_598_config() -> OptimizerConfig:
    """Config matching the issue scenario."""
    return OptimizerConfig(
        battery_capacity_kwh=13.5,
        charge_efficiency=0.90,
        discharge_efficiency=0.85,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        cycle_penalty_per_kwh=0.05,
        self_consumption_value_per_kwh=0.15,  # Will be overridden by actual price
        effective_cheap_price=0.10,
        demand_window_target_soc_pct=80.0,
        soc_bins=20,
    )


@pytest.fixture
def issue_598_slots() -> list[SlotContext]:
    """
    Recreate the issue scenario:
    - 04:30 (slot 0): cheap price $0.150, solar=0, load=0.175
    - 05:00 (slot 1): price $0.160, solar=0, load=0.173
    - 05:30 (slot 2): price $0.160, solar=0, load=0.173
    - 06:00 (slot 3): price $0.160, solar=0, load=0.185
    - No solar in horizon, no demand window tonight
    """
    slots = []
    base_time = "2026-01-03T04:30:00"
    for i in range(4):
        timestamp = f"2026-01-03T{(4 + i // 2):02d}:{(30 if i % 2 == 0 else 0):02d}:00"
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=timestamp,
                slot_interval_minutes=30,
                buy_price=0.15 if i == 0 else 0.16,
                sell_price=0.10,
                solar_kwh=0.0,
                consumption_kwh=0.175 if i == 0 else 0.173,
                is_demand_window_entry=False,
                is_demand_window_slot=False,
            )
        )
    return slots


# ---------------------------------------------------------------------------
# Unit tests for efficiency penalty
# ---------------------------------------------------------------------------


def test_efficiency_penalty_calculation_round_trip():
    """Test that efficiency penalty accounts for full round-trip loss."""
    config = OptimizerConfig(
        charge_efficiency=0.90,
        discharge_efficiency=0.85,
        battery_capacity_kwh=13.5,
    )

    # Create a simple CHARGE slot
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T04:30:00",
        slot_interval_minutes=30,
        buy_price=0.150,
        sell_price=0.10,
        solar_kwh=0.0,
        consumption_kwh=0.0,
    )

    # Simulate grid import of 2.0 kWh
    next_soc, grid_import, grid_export = DPPlanner._transition_charge_grid(
        soc_pct=10.0,
        slot=slot,
        config=config,
        charge_rate_kw=config.charge_rate_kw,
    )

    # Compute stage cost
    stage = DPPlanner.stage_cost(
        action=PlannerAction.CHARGE_GRID_NORMAL,
        grid_import_kwh=grid_import,
        grid_export_kwh=grid_export,
        slot=slot,
        config=config,
        soc_pct=10.0,
        is_switch=False,
    )

    # Round-trip efficiency = 0.9 * 0.85 = 0.765
    # Efficiency loss factor = 1 - 0.765 = 0.235
    expected_loss_kwh = grid_import * 0.235
    expected_penalty = expected_loss_kwh * slot.buy_price

    assert stage.efficiency_penalty == pytest.approx(expected_penalty, abs=0.001)


def test_efficiency_penalty_zero_when_no_charge():
    """Test that efficiency penalty is zero for non-charge actions."""
    config = OptimizerConfig()
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T00:00:00",
        slot_interval_minutes=30,
        buy_price=0.20,
        sell_price=0.10,
        solar_kwh=0.0,
        consumption_kwh=0.5,
    )

    # HOLD action with discharge (net_kwh < 0)
    stage = DPPlanner.stage_cost(
        action=PlannerAction.HOLD,
        grid_import_kwh=0.1,
        grid_export_kwh=0.0,
        slot=slot,
        config=config,
        soc_pct=50.0,
        is_switch=False,
    )

    assert stage.efficiency_penalty == 0.0


def test_self_consumption_value_uses_actual_slot_price():
    """Test that self_consumption_value is based on current slot buy_price."""
    config = OptimizerConfig(
        discharge_efficiency=0.85,
        discharge_rate_kw=5.0,
        min_soc_pct=10.0,
    )

    # Create a HOLD slot with load deficit (battery will discharge)
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T05:00:00",
        slot_interval_minutes=30,
        buy_price=0.160,  # This is the price to avoid
        sell_price=0.10,
        solar_kwh=0.0,
        consumption_kwh=0.3,  # 0.3 kWh load in 30 min
    )

    # Current SOC high enough to discharge
    stage = DPPlanner.stage_cost(
        action=PlannerAction.HOLD,
        grid_import_kwh=0.0,
        grid_export_kwh=0.0,
        slot=slot,
        config=config,
        soc_pct=50.0,
        is_switch=False,
    )

    # Battery should discharge to cover some load
    expected_self_consumption = 0.3 * slot.buy_price

    assert stage.self_consumption_value == pytest.approx(
        expected_self_consumption, abs=0.001
    )


# ---------------------------------------------------------------------------
# Integration tests for issue #598 scenario
# ---------------------------------------------------------------------------


def test_issue_598_overnight_unnecessary_charging(
    issue_598_config: OptimizerConfig,
    issue_598_slots: list[SlotContext],
):
    """
    Test that optimizer chooses HOLD over wasteful overnight charging.

    Scenario from issue #598:
    - SOC at 10% (floor) at 04:30
    - No demand window tonight (terminal_penalty_idx = None)
    - Cheap price $0.150/kWh at 04:30, then $0.160/kWh
    - Solar forecast: none

    Expected: NO charging at 04:30. The optimizer should HOLD and import
    directly for load, rather than charging and immediately discharging.

    This is the key test that verifies the fix for Issue #598.
    """
    inputs = OptimizerInputs(
        initial_soc_pct=10.0,
        current_action=None,
        slots=issue_598_slots,
        cycle_id="test_598",
    )

    result = DPPlanner(issue_598_config).plan(inputs)

    assert result.success, f"Solve failed: {result.error_message}"
    assert len(result.decisions) == 4

    first_decision = result.decisions[0]

    # The first slot (04:30) should be HOLD, not CHARGE
    assert first_decision.action == PlannerAction.HOLD, (
        f"Expected HOLD at 04:30, got {first_decision.action.value}. "
        f"Overnight charging at cheap price followed by immediate discharge "
        f"should be prevented by efficiency penalties."
    )

    # Reason should be IDLE or similar (not CHEAP_IMPORT_WINDOW)
    assert first_decision.reason_code in (
        PlannerReasonCode.IDLE,
        PlannerReasonCode.SOC_FLOOR_CONSTRAINT,
    ), f"Unexpected reason: {first_decision.reason_code.value}"

    # There should be no grid import in the first slot (or minimal for load only)
    # Actually, HOLD with load deficit will import directly
    # This is more efficient than charging then discharging
    assert first_decision.grid_import_kwh > 0, "Should import for load directly"

    # Total net cost should be lower than the CHARGE path
    # (we could verify by manually computing with old cost function)


def test_beneficial_charging_still_allowed(
    issue_598_config: OptimizerConfig,
):
    """
    Test that charging is still allowed when there's a legitimate reason.

    Scenario: Need to reach high SOC for demand window tomorrow morning.
    - Charge at cheap price $0.12/kWh
    - Demand window at 08:00 (next day) with target SOC 80%
    - SOC at 04:30 is 20%
    - No solar forecast
    - Prices rise to $0.25/kWh by 07:00

    Expected: Should charge at 04:30-06:00 to reach target before DW.
    """
    # Create slots for overnight leading to DW
    slots = []
    for i in range(12):  # 6 hours, 30-min slots
        hour = 4 + i // 2
        # Prices: cheap at $0.08 (< effective_cheap_price of $0.10), then rise
        price = 0.08 if hour < 6 else 0.15 if hour < 7 else 0.25
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=f"2026-01-03T{hour:02d}:{(30 if i % 2 == 0 else 0):02d}:00",
                slot_interval_minutes=30,
                buy_price=price,
                sell_price=0.08,
                solar_kwh=0.0,
                consumption_kwh=0.15,  # modest load
                is_demand_window_entry=(hour == 8),  # DW at 08:00
                is_demand_window_slot=(hour >= 8),
            )
        )

    inputs = OptimizerInputs(
        initial_soc_pct=20.0,
        current_action=None,
        slots=slots,
        cycle_id="test_beneficial",
    )

    result = DPPlanner(issue_598_config).plan(inputs)

    assert result.success

    # Find the index of the first demand window entry slot
    dw_entry_idx = next(
        i for i, s in enumerate(inputs.slots) if s.is_demand_window_entry
    )

    # Check that there is at least one charging action before the DW entry
    pre_dw_charges = sum(
        1
        for d in result.decisions
        if d.slot_index < dw_entry_idx
        and d.action
        in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
    )
    assert pre_dw_charges >= 1, (
        "Should include charging before demand window to build SOC"
    )

    # SOC should increase before demand window
    # (Could add detailed SOC trajectory checks if needed)


def test_price_spread_justifies_charging(
    issue_598_config: OptimizerConfig,
):
    """
    Test that significant price spread can justify charging despite efficiency loss.

    Scenario:
    - Charge at very cheap $0.05/kWh
    - Discharge during expensive peak at $0.30/kWh
    - Even with 23.5% round-trip loss, the spread is attractive.

    Expected: Charging is allowed and found optimal.
    """
    slots = []
    # 2 charge slots at $0.05
    for i in range(2):
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=f"2026-01-03T02:{30 if i == 0 else 0}:00",
                slot_interval_minutes=30,
                buy_price=0.05,
                sell_price=0.03,
                solar_kwh=0.0,
                consumption_kwh=0.1,
                is_demand_window_entry=False,
                is_demand_window_slot=False,
            )
        )
    # 2 discharge slots at $0.30
    for i in range(2, 4):
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=f"2026-01-03T03:{30 if i == 2 else 0}:00",
                slot_interval_minutes=30,
                buy_price=0.30,
                sell_price=0.25,
                solar_kwh=0.0,
                consumption_kwh=0.3,
                is_demand_window_entry=False,
                is_demand_window_slot=False,
            )
        )

    inputs = OptimizerInputs(
        initial_soc_pct=20.0,
        current_action=None,
        slots=slots,
        cycle_id="test_spread",
    )

    result = DPPlanner(issue_598_config).plan(inputs)

    assert result.success

    # Should see charging in the cheap slots
    cheap_actions = [d.action for d in result.decisions[:2]]
    assert any(
        a in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
        for a in cheap_actions
    ), "Should charge at very cheap prices"


def test_round_trip_penalty_makes_marginal_cycles_uneconomical():
    """
    Test that round-trip efficiency penalty prevents uneconomical cycling.

    Scenario:
    - Charge at $0.14/kWh
    - Discharge at $0.15/kWh (only $0.01 spread)
    - Even with $0.05/kWh cycle degradation, total cost > benefit.

    Expected: No charging. HOLD and import directly when needed.
    """
    config = OptimizerConfig(
        charge_efficiency=0.90,
        discharge_efficiency=0.85,
        cycle_penalty_per_kwh=0.05,
    )

    # 3-hour horizon: charge at 04:30 ($0.14), discharge at 05:00-06:00 ($0.15)
    slots = []
    for i in range(6):  # 6 slots, 30 min each
        price = 0.14 if i == 0 else 0.15
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=f"2026-01-03T{(4 + i // 2):02d}:{(30 if i % 2 == 0 else 0):02d}:00",
                slot_interval_minutes=30,
                buy_price=price,
                sell_price=0.08,
                solar_kwh=0.0,
                consumption_kwh=0.15,
            )
        )

    inputs = OptimizerInputs(
        initial_soc_pct=10.0,
        current_action=None,
        slots=slots,
        cycle_id="test_marginal",
    )

    result = DPPlanner(config).plan(inputs)

    assert result.success
    assert result.decisions[0].action == PlannerAction.HOLD, (
        "HOLD should be cheaper than cycling with minimal spread"
    )

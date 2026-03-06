"""Test solar opportunity penalty - soft penalty approach per PLANNING_MODEL.md.

Tests the optimizer's ability to apply economic penalties for grid charging
when future solar could charge the battery for free. This is a soft penalty,
not a hard constraint, allowing the DP to make nuanced economic decisions.
"""

import pytest

from custom_components.localshift.computation_engine_lib.optimizer_dp import (
    DPPlanner,
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)


def test_solar_opportunity_penalty_applies_when_solar_sufficient():
    """Test penalty applies at night when future solar > 10% of battery.

    Scenario: Nighttime, no immediate solar, future solar surplus > 1.35 kWh
    Expected: Penalty > 0 to discourage grid charging
    """
    config = OptimizerConfig(
        optimization_mode="self_consumption",
        battery_capacity_kwh=13.5,
    )

    # Current slot: 8 PM, no solar
    current_slot = SlotContext(
        slot_index=0,
        timestamp_iso="2024-01-01T20:00:00",
        slot_interval_minutes=30,
        buy_price=0.11,
        sell_price=0.04,
        consumption_kwh=0.5,
        solar_kwh=0.0,  # Nighttime
    )

    # Future slots: Tomorrow has 15 kWh solar surplus (> 10% of battery)
    future_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2024-01-02T{6 + i:02d}:00:00",
            slot_interval_minutes=60,
            buy_price=0.15,
            sell_price=0.08,
            consumption_kwh=0.5,
            solar_kwh=2.0,  # High solar
        )
        for i in range(10)
    ]

    all_slots = [current_slot] + future_slots

    # Calculate penalty directly
    penalty = DPPlanner._compute_solar_opportunity_penalty(
        None,  # self (not used in static method)
        action=PlannerAction.CHARGE_GRID_NORMAL,
        grid_import_kwh=1.0,
        slot=current_slot,
        slot_idx=0,
        slots=all_slots,
        config=config,
        terminal_penalty_idx=None,
    )

    # Penalty should apply
    assert penalty > 0.0
    # Should be $0.03/kWh × 1.0 kWh = $0.03
    assert abs(penalty - 0.03) < 0.001


def test_solar_opportunity_penalty_zero_when_no_future_solar():
    """Test penalty is zero when no future solar available.

    Scenario: Nighttime, no solar in remaining horizon
    Expected: No penalty (grid charging is the only option)
    """
    config = OptimizerConfig(battery_capacity_kwh=13.5)

    current_slot = SlotContext(
        slot_index=0,
        timestamp_iso="2024-01-01T20:00:00",
        slot_interval_minutes=30,
        buy_price=0.11,
        sell_price=0.04,
        consumption_kwh=0.5,
        solar_kwh=0.0,
    )

    # Future slots: No solar (cloudy day)
    future_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2024-01-02T{6 + i:02d}:00:00",
            slot_interval_minutes=60,
            buy_price=0.15,
            sell_price=0.08,
            consumption_kwh=0.5,
            solar_kwh=0.0,  # No solar
        )
        for i in range(10)
    ]

    all_slots = [current_slot] + future_slots

    penalty = DPPlanner._compute_solar_opportunity_penalty(
        None,
        action=PlannerAction.CHARGE_GRID_NORMAL,
        grid_import_kwh=1.0,
        slot=current_slot,
        slot_idx=0,
        slots=all_slots,
        config=config,
        terminal_penalty_idx=None,
    )

    # No penalty when no solar available
    assert penalty == 0.0


def test_solar_opportunity_penalty_zero_during_daytime():
    """Test penalty doesn't apply during daytime with solar active.

    Scenario: Daytime slot with solar_kwh > 0
    Expected: No penalty (allow beneficial daytime charging)
    """
    config = OptimizerConfig(battery_capacity_kwh=13.5)

    # Daytime slot with solar active
    current_slot = SlotContext(
        slot_index=0,
        timestamp_iso="2024-01-01T14:00:00",
        slot_interval_minutes=30,
        buy_price=0.11,
        sell_price=0.04,
        consumption_kwh=0.5,
        solar_kwh=1.5,  # Solar is active
    )

    future_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2024-01-01T{14 + i:02d}:00:00",
            slot_interval_minutes=60,
            buy_price=0.15,
            sell_price=0.08,
            consumption_kwh=0.5,
            solar_kwh=2.0,
        )
        for i in range(4)
    ]

    all_slots = [current_slot] + future_slots

    penalty = DPPlanner._compute_solar_opportunity_penalty(
        None,
        action=PlannerAction.CHARGE_GRID_NORMAL,
        grid_import_kwh=1.0,
        slot=current_slot,
        slot_idx=0,
        slots=all_slots,
        config=config,
        terminal_penalty_idx=None,
    )

    # No penalty during daytime with solar active
    assert penalty == 0.0


def test_solar_opportunity_penalty_zero_with_demand_window():
    """Test penalty doesn't apply when demand window is active.

    Scenario: Demand window exists (terminal_penalty_idx is set)
    Expected: No penalty (terminal cost drives charging decisions)
    """
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
    )

    current_slot = SlotContext(
        slot_index=0,
        timestamp_iso="2024-01-01T20:00:00",
        slot_interval_minutes=30,
        buy_price=0.11,
        sell_price=0.04,
        consumption_kwh=0.5,
        solar_kwh=0.0,
    )

    future_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2024-01-02T{6 + i:02d}:00:00",
            slot_interval_minutes=60,
            buy_price=0.15,
            sell_price=0.08,
            consumption_kwh=0.5,
            solar_kwh=2.0,
        )
        for i in range(10)
    ]

    all_slots = [current_slot] + future_slots

    penalty = DPPlanner._compute_solar_opportunity_penalty(
        None,
        action=PlannerAction.CHARGE_GRID_NORMAL,
        grid_import_kwh=1.0,
        slot=current_slot,
        slot_idx=0,
        slots=all_slots,
        config=config,
        terminal_penalty_idx=5,  # Demand window active
    )

    # No penalty when demand window exists
    assert penalty == 0.0


def test_solar_opportunity_penalty_zero_when_solar_marginal():
    """Test penalty is zero when future solar is marginal (< 10% battery).

    Scenario: Future solar surplus < 1.35 kWh (10% of 13.5 kWh battery)
    Expected: No penalty (solar not significant enough to rely on)
    """
    config = OptimizerConfig(battery_capacity_kwh=13.5)

    current_slot = SlotContext(
        slot_index=0,
        timestamp_iso="2024-01-01T20:00:00",
        slot_interval_minutes=30,
        buy_price=0.11,
        sell_price=0.04,
        consumption_kwh=0.5,
        solar_kwh=0.0,
    )

    # Future slots: Minimal solar (< 1.35 kWh total)
    future_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2024-01-02T{6 + i:02d}:00:00",
            slot_interval_minutes=60,
            buy_price=0.15,
            sell_price=0.08,
            consumption_kwh=0.5,
            solar_kwh=0.2,  # Very low solar (cloudy)
        )
        for i in range(4)
    ]

    all_slots = [current_slot] + future_slots

    penalty = DPPlanner._compute_solar_opportunity_penalty(
        None,
        action=PlannerAction.CHARGE_GRID_NORMAL,
        grid_import_kwh=1.0,
        slot=current_slot,
        slot_idx=0,
        slots=all_slots,
        config=config,
        terminal_penalty_idx=None,
    )

    # No penalty when solar is insufficient
    assert penalty == 0.0


def test_solar_opportunity_penalty_scales_with_import():
    """Test penalty scales proportionally with grid import amount.

    Expected: 2 kWh import should have 2x penalty of 1 kWh import
    """
    config = OptimizerConfig(battery_capacity_kwh=13.5)

    current_slot = SlotContext(
        slot_index=0,
        timestamp_iso="2024-01-01T20:00:00",
        slot_interval_minutes=30,
        buy_price=0.11,
        sell_price=0.04,
        consumption_kwh=0.5,
        solar_kwh=0.0,
    )

    future_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2024-01-02T{6 + i:02d}:00:00",
            slot_interval_minutes=60,
            buy_price=0.15,
            sell_price=0.08,
            consumption_kwh=0.5,
            solar_kwh=2.0,
        )
        for i in range(10)
    ]

    all_slots = [current_slot] + future_slots

    # Test 1 kWh import
    penalty_1kwh = DPPlanner._compute_solar_opportunity_penalty(
        None,
        action=PlannerAction.CHARGE_GRID_NORMAL,
        grid_import_kwh=1.0,
        slot=current_slot,
        slot_idx=0,
        slots=all_slots,
        config=config,
        terminal_penalty_idx=None,
    )

    # Test 2 kWh import
    penalty_2kwh = DPPlanner._compute_solar_opportunity_penalty(
        None,
        action=PlannerAction.CHARGE_GRID_NORMAL,
        grid_import_kwh=2.0,
        slot=current_slot,
        slot_idx=0,
        slots=all_slots,
        config=config,
        terminal_penalty_idx=None,
    )

    # Penalty should scale linearly
    assert abs(penalty_2kwh - 2 * penalty_1kwh) < 0.001


def test_solar_opportunity_penalty_zero_for_non_charge_actions():
    """Test penalty is zero for non-charging actions.

    Expected: Only CHARGE_GRID_NORMAL and CHARGE_GRID_BOOST trigger penalty
    """
    config = OptimizerConfig(battery_capacity_kwh=13.5)

    current_slot = SlotContext(
        slot_index=0,
        timestamp_iso="2024-01-01T20:00:00",
        slot_interval_minutes=30,
        buy_price=0.11,
        sell_price=0.04,
        consumption_kwh=0.5,
        solar_kwh=0.0,
    )

    future_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2024-01-02T{6 + i:02d}:00:00",
            slot_interval_minutes=60,
            buy_price=0.15,
            sell_price=0.08,
            consumption_kwh=0.5,
            solar_kwh=2.0,
        )
        for i in range(10)
    ]

    all_slots = [current_slot] + future_slots

    # Test HOLD action
    penalty_hold = DPPlanner._compute_solar_opportunity_penalty(
        None,
        action=PlannerAction.HOLD,
        grid_import_kwh=0.0,
        slot=current_slot,
        slot_idx=0,
        slots=all_slots,
        config=config,
        terminal_penalty_idx=None,
    )

    assert penalty_hold == 0.0

    # Test EXPORT action
    penalty_export = DPPlanner._compute_solar_opportunity_penalty(
        None,
        action=PlannerAction.EXPORT_PROACTIVE,
        grid_import_kwh=0.0,
        slot=current_slot,
        slot_idx=0,
        slots=all_slots,
        config=config,
        terminal_penalty_idx=None,
    )

    assert penalty_export == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

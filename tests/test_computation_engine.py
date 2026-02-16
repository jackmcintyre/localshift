"""Unit tests for ComputationEngine."""
import pytest
from datetime import datetime, time, timedelta
from unittest.mock import MagicMock, patch

from custom_components.amber_powerwall.computation_engine import (
    ComputationEngine,
    BatteryMode,
)
from custom_components.amber_powerwall.coordinator_data import CoordinatorData

from .conftest import now

@pytest.mark.parametrize(
    "operation_mode, backup_reserve, expected",
    [
        ("autonomous", 10, True),    # Low reserve, autonomous mode
        ("autonomous", 50, False),   # Normal reserve, autonomous mode
        ("backup", 50, True),         # Any reserve, backup mode
        ("backup", 99, True),         # High reserve, backup mode
        ("autonomous", 99, False),    # High reserve, autonomous mode
    ],
)
def test_force_discharge_active(
    computation_engine, coordinator_data, operation_mode, backup_reserve, expected
):
    """Test force_discharge_active detection."""
    coordinator_data.operation_mode = operation_mode
    coordinator_data.backup_reserve = backup_reserve

    computation_engine.compute_derived_values(coordinator_data)

    assert coordinator_data.force_discharge_active == expected

@pytest.mark.parametrize(
    "operation_mode, backup_reserve, expected",
    [
        ("backup", 50, True),         # Backup mode
        ("autonomous", 99, True),     # High reserve, autonomous mode
        ("autonomous", 50, False),    # Normal reserve, autonomous mode
        ("autonomous", 10, False),    # Low reserve, autonomous mode
    ],
)
def test_force_charge_active(
    computation_engine, coordinator_data, operation_mode, backup_reserve, expected
):
    """Test force_charge_active detection."""
    coordinator_data.operation_mode = operation_mode
    coordinator_data.backup_reserve = backup_reserve

    computation_engine.compute_derived_values(coordinator_data)

    assert coordinator_data.force_charge_active == expected

@pytest.mark.parametrize(
    "operation_mode, backup_reserve, expected",
    [
        ("autonomous", 99, True),     # High reserve, autonomous mode
        ("backup", 50, False),        # Backup mode
        ("autonomous", 50, False),    # Normal reserve, autonomous mode
    ],
)
def test_boost_charge_active(
    computation_engine, coordinator_data, operation_mode, backup_reserve, expected
):
    """Test boost_charge_active detection."""
    coordinator_data.operation_mode = operation_mode
    coordinator_data.backup_reserve = backup_reserve

    computation_engine.compute_derived_values(coordinator_data)

    assert coordinator_data.boost_charge_active == expected

@pytest.mark.parametrize(
    "now_time, dw_start, dw_end, expected",
    [
        (time(17, 0), time(18, 0), time(22, 0), False),  # Before DW
        (time(18, 0), time(18, 0), time(22, 0), True),   # At DW start
        (time(20, 0), time(18, 0), time(22, 0), True),   # During DW
        (time(22, 0), time(18, 0), time(22, 0), False),  # At DW end
        (time(23, 0), time(18, 0), time(22, 0), False),  # After DW
    ],
)
def test_demand_window_active(
    computation_engine, coordinator_data, now_time, dw_start, dw_end, expected
):
    """Test demand_window_active detection."""
    # Mock current time
    with patch("homeassistant.util.dt.now", return_value=datetime.combine(datetime.today(), now_time)):
        # Mock config options
        computation_engine.entry.options["demand_window_start"] = dw_start.strftime("%H:%M:%S")
        computation_engine.entry.options["demand_window_end"] = dw_end.strftime("%H:%M:%S")

        # Mock switch state
        computation_engine._get_switch_state = MagicMock(return_value=True)

        computation_engine.compute_derived_values(coordinator_data)

        assert coordinator_data.demand_window_active == expected

def test_effective_cheap_price_no_solar_gap(computation_engine, coordinator_data):
    """Test effective_cheap_price when solar can reach target."""
    coordinator_data.soc = 95.0
    coordinator_data.solar_can_reach_target = True
    coordinator_data.general_price = 0.25
    coordinator_data.feed_in_price = 0.08

    computation_engine.compute_derived_values(coordinator_data)

    # Should use base percentile price
    assert coordinator_data.effective_cheap_price == pytest.approx(0.15, rel=0.01)

def test_effective_cheap_price_with_solar_gap(computation_engine, coordinator_data):
    """Test effective_cheap_price when solar cannot reach target."""
    coordinator_data.soc = 50.0
    coordinator_data.solar_can_reach_target = False
    coordinator_data.general_price = 0.25
    coordinator_data.feed_in_price = 0.08

    computation_engine.compute_derived_values(coordinator_data)

    # Should use urgency-based calculation
    assert coordinator_data.effective_cheap_price > 0.15

def test_active_mode_automation_disabled(computation_engine, coordinator_data):
    """Test active_mode when automation is disabled."""
    computation_engine._get_switch_state = MagicMock(return_value=False)

    computation_engine.compute_derived_values(coordinator_data)

    assert coordinator_data.active_mode == BatteryMode.SELF_CONSUMPTION

@pytest.mark.parametrize(
    "grid_charge_boost, grid_charge, proactive_export, price_spike, "
    "demand_window_active, manual_override, expected_mode",
    [
        (True, False, False, False, False, False, BatteryMode.BOOST_CHARGING),
        (False, True, False, False, False, False, BatteryMode.GRID_CHARGING),
        (False, False, True, False, False, False, BatteryMode.PROACTIVE_EXPORT),
        (False, False, False, True, False, False, BatteryMode.SPIKE_DISCHARGE),
        (False, False, False, False, True, False, BatteryMode.DEMAND_BLOCK),
        (False, False, False, False, False, True, BatteryMode.MANUAL),
        (False, False, False, False, False, False, BatteryMode.SELF_CONSUMPTION),
    ],
)
def test_active_mode_forecast_driven(
    computation_engine, coordinator_data,
    grid_charge_boost, grid_charge, proactive_export, price_spike,
    demand_window_active, manual_override, expected_mode
):
    """Test active_mode forecast-driven logic."""
    # Mock forecast entry
    coordinator_data.daily_forecast = [{
        "grid_charge_boost": grid_charge_boost,
        "grid_charge": grid_charge,
        "proactive_export": proactive_export,
    }]

    # Mock conditions
    coordinator_data.price_spike = price_spike
    coordinator_data.demand_window_active = demand_window_active
    coordinator_data.manual_override = manual_override

    computation_engine.compute_derived_values(coordinator_data)

    assert coordinator_data.active_mode == expected_mode

def test_decision_log_mode_change(computation_engine, coordinator_data):
    """Test decision log when mode changes."""
    # First run - initial state
    computation_engine.compute_derived_values(coordinator_data)
    initial_log_length = len(coordinator_data.decision_log)

    # Change conditions to trigger mode change
    coordinator_data.soc = 30.0
    coordinator_data.general_price = 0.1

    computation_engine.compute_derived_values(coordinator_data)
    new_log_length = len(coordinator_data.decision_log)

    assert new_log_length == initial_log_length + 1
    assert coordinator_data.decision_log[-1]["reason"].startswith("Mode changed")

def test_decision_log_periodic_update(computation_engine, coordinator_data):
    """Test decision log periodic updates."""
    # First run - initial state
    computation_engine.compute_derived_values(coordinator_data)
    initial_log_length = len(coordinator_data.decision_log)

    # No mode change, but should still log periodically
    coordinator_data.soc = 50.1  # Small change, not enough for mode change

    computation_engine.compute_derived_values(coordinator_data)
    new_log_length = len(coordinator_data.decision_log)

    assert new_log_length == initial_log_length + 1
    assert coordinator_data.decision_log[-1]["reason"].startswith("Status update")

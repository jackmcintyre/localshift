"""Tests for misc.py sensors.

Tests cover:
- ExcessSolarSensor: native_value, extra_state_attributes
- LoadShiftSignalSensor: native_value, extra_state_attributes, icon variations
"""

from datetime import datetime
from unittest.mock import MagicMock

from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.sensors.misc import (
    ExcessSolarSensor,
    LoadShiftSignalSensor,
)


def create_mock_coordinator_with_data(**kwargs) -> tuple[MagicMock, CoordinatorData]:
    """Create a mock coordinator with CoordinatorData for testing."""
    data = CoordinatorData()
    for key, value in kwargs.items():
        setattr(data, key, value)
    mock_coordinator = MagicMock()
    mock_coordinator.data = data
    return mock_coordinator, data


class TestExcessSolarSensor:
    """Tests for ExcessSolarSensor."""

    def test_native_value(self):
        """Test native_value returns rounded excess_until_battery_full_kwh."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            excess_until_battery_full_kwh=12.567
        )
        mock_entry = MagicMock()

        sensor = ExcessSolarSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 12.57

    def test_extra_state_attributes(self):
        """Test extra_state_attributes contains all excess solar fields."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            excess_solar_current_hour_kwh=2.5,
            excess_solar_next_2h_kwh=5.0,
            excess_solar_next_4h_kwh=8.0,
            excess_until_battery_full_kwh=12.5,
            excess_until_negative_fit_kwh=15.0,
            time_until_battery_full_minutes=120,
            negative_fit_window_start=datetime(2026, 3, 13, 14, 0),
            negative_fit_window_duration_minutes=60,
            can_add_load_now=True,
            safe_additional_load_kw=2.5,
            load_shift_confidence="high",
            current_excess_rate_kw=3.2,
        )
        mock_entry = MagicMock()

        sensor = ExcessSolarSensor(mock_coordinator, mock_entry)
        attrs = sensor.extra_state_attributes

        assert attrs["excess_current_hour_kwh"] == 2.5
        assert attrs["excess_next_2h_kwh"] == 5.0
        assert attrs["excess_next_4h_kwh"] == 8.0
        assert attrs["excess_until_battery_full_kwh"] == 12.5
        assert attrs["excess_until_negative_fit_kwh"] == 15.0
        assert attrs["time_until_battery_full_minutes"] == 120
        assert attrs["negative_fit_window_start"] == "2026-03-13T14:00:00"
        assert attrs["negative_fit_window_duration_minutes"] == 60
        assert attrs["can_add_load_now"] is True
        assert attrs["safe_additional_load_kw"] == 2.5
        assert attrs["forecast_confidence"] == "high"
        assert attrs["current_excess_rate_kw"] == 3.2

    def test_extra_state_attributes_none_window_start(self):
        """Test extra_state_attributes handles None negative_fit_window_start."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            excess_solar_current_hour_kwh=0,
            excess_solar_next_2h_kwh=0,
            excess_solar_next_4h_kwh=0,
            excess_until_battery_full_kwh=0,
            excess_until_negative_fit_kwh=0,
            time_until_battery_full_minutes=0,
            negative_fit_window_start=None,
            negative_fit_window_duration_minutes=0,
            can_add_load_now=False,
            safe_additional_load_kw=0,
            load_shift_confidence="low",
            current_excess_rate_kw=0,
        )
        mock_entry = MagicMock()

        sensor = ExcessSolarSensor(mock_coordinator, mock_entry)
        attrs = sensor.extra_state_attributes

        assert attrs["negative_fit_window_start"] is None


class TestLoadShiftSignalSensor:
    """Tests for LoadShiftSignalSensor."""

    def test_native_value(self):
        """Test native_value returns load_shift_signal."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            load_shift_signal="INCREASE_LOAD"
        )
        mock_entry = MagicMock()

        sensor = LoadShiftSignalSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == "INCREASE_LOAD"

    def test_extra_state_attributes(self):
        """Test extra_state_attributes contains all signal fields."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            load_shift_signal="INCREASE_LOAD",
            load_shift_recommended_kw=2.5,
            load_shift_recommended_duration_minutes=60,
            load_shift_reason="Excess solar available",
            load_shift_confidence="high",
            current_excess_rate_kw=3.2,
            grid_charge_risk=False,
            safe_additional_load_kw=2.5,
        )
        mock_entry = MagicMock()

        sensor = LoadShiftSignalSensor(mock_coordinator, mock_entry)
        attrs = sensor.extra_state_attributes

        assert attrs["recommended_additional_kw"] == 2.5
        assert attrs["recommended_duration_minutes"] == 60
        assert attrs["signal_reason"] == "Excess solar available"
        assert attrs["signal_confidence"] == "high"
        assert attrs["current_excess_rate_kw"] == 3.2
        assert attrs["grid_charge_risk"] is False
        assert attrs["safe_additional_load_kw"] == 2.5

    def test_icon_increase_load(self):
        """Test icon returns arrow-up-bold for INCREASE_LOAD."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            load_shift_signal="INCREASE_LOAD"
        )
        mock_entry = MagicMock()

        sensor = LoadShiftSignalSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor.icon == "mdi:arrow-up-bold"

    def test_icon_reduce_load(self):
        """Test icon returns arrow-down-bold for REDUCE_LOAD."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            load_shift_signal="REDUCE_LOAD"
        )
        mock_entry = MagicMock()

        sensor = LoadShiftSignalSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor.icon == "mdi:arrow-down-bold"

    def test_icon_maintain_load(self):
        """Test icon returns check-circle for MAINTAIN_LOAD."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            load_shift_signal="MAINTAIN_LOAD"
        )
        mock_entry = MagicMock()

        sensor = LoadShiftSignalSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor.icon == "mdi:check-circle"

    def test_icon_hold(self):
        """Test icon returns pause-circle for HOLD."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            load_shift_signal="HOLD"
        )
        mock_entry = MagicMock()

        sensor = LoadShiftSignalSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor.icon == "mdi:pause-circle"

    def test_icon_unknown(self):
        """Test icon returns pause-circle for unknown signal."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            load_shift_signal="UNKNOWN_SIGNAL"
        )
        mock_entry = MagicMock()

        sensor = LoadShiftSignalSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor.icon == "mdi:pause-circle"

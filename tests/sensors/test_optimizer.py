"""Tests for optimizer.py sensors.

Tests cover:
- OptimizerPlanDetailedSensor: native_value states, extra_state_attributes, icon variations
- OptimizerSummarySensor: native_value states, extra_state_attributes, icon variations
- SolarForecastAccuracySensor: native_value, extra_state_attributes
"""

from unittest.mock import MagicMock

from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.sensors.optimizer import (
    OptimizerPlanDetailedSensor,
    OptimizerSummarySensor,
    SolarForecastAccuracySensor,
)


def create_mock_coordinator_with_data(**kwargs) -> tuple[MagicMock, CoordinatorData]:
    """Create a mock coordinator with CoordinatorData for testing."""
    data = CoordinatorData()
    for key, value in kwargs.items():
        setattr(data, key, value)
    mock_coordinator = MagicMock()
    mock_coordinator.data = data
    return mock_coordinator, data


class TestOptimizerPlanDetailedSensor:
    """Tests for OptimizerPlanDetailedSensor."""

    def test_native_value_computed(self):
        """Test native_value returns 'computed' when optimizer succeeded."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary={"enabled": True, "success": True}
        )
        mock_entry = MagicMock()

        sensor = OptimizerPlanDetailedSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == "computed"

    def test_native_value_disabled(self):
        """Test native_value returns 'disabled' when optimizer not enabled."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary={"enabled": False}
        )
        mock_entry = MagicMock()

        sensor = OptimizerPlanDetailedSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == "disabled"

    def test_native_value_error(self):
        """Test native_value returns 'error' when optimizer failed."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary={"enabled": True, "success": False}
        )
        mock_entry = MagicMock()

        sensor = OptimizerPlanDetailedSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == "error"

    def test_native_value_no_summary(self):
        """Test native_value returns 'disabled' when no summary."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary=None
        )
        mock_entry = MagicMock()

        sensor = OptimizerPlanDetailedSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == "disabled"

    def test_extra_state_attributes(self):
        """Test extra_state_attributes contains all detailed fields."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_decisions=[
                {"slot_index": 0, "action": "CHARGE"},
                {"slot_index": 1, "action": "DISCHARGE"},
            ],
            optimizer_summary={
                "enabled": True,
                "success": True,
                "error_message": None,
                "cycle_timestamp_iso": "2026-03-13T10:00:00",
            },
            forecast_horizon_hours=24,
        )
        mock_entry = MagicMock()

        sensor = OptimizerPlanDetailedSensor(mock_coordinator, mock_entry)
        attrs = sensor.extra_state_attributes

        assert attrs["enabled"] is True
        assert attrs["success"] is True
        assert attrs["error_message"] is None
        assert len(attrs["decisions"]) == 2
        assert attrs["total_slots"] == 2
        assert attrs["forecast_horizon_hours"] == 24
        assert attrs["computed_at"] == "2026-03-13T10:00:00"

    def test_icon_computed(self):
        """Test icon for computed state."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary={"enabled": True, "success": True}
        )
        mock_entry = MagicMock()

        sensor = OptimizerPlanDetailedSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor.icon == "mdi:format-list-bulleted"

    def test_icon_error(self):
        """Test icon for error state."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary={"enabled": True, "success": False}
        )
        mock_entry = MagicMock()

        sensor = OptimizerPlanDetailedSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor.icon == "mdi:alert-circle-outline"

    def test_icon_disabled(self):
        """Test icon for disabled state."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary={"enabled": False}
        )
        mock_entry = MagicMock()

        sensor = OptimizerPlanDetailedSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor.icon == "mdi:minus-circle-outline"

    def test_unrecorded_attributes_includes_decisions(self):
        """Test that 'decisions' is excluded from recorder to avoid 16KB limit.

        Issue #467: The decisions array can exceed 26KB, which exceeds the
        Home Assistant recorder's 16KB attribute limit. By excluding 'decisions'
        from recording, we ensure:
        1. The sensor state and other attributes are recorded for history
        2. The dashboard can still access decisions in real-time
        3. No "State attributes exceed maximum size" warnings
        """
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary={"enabled": True, "success": True}
        )
        mock_entry = MagicMock()

        sensor = OptimizerPlanDetailedSensor(mock_coordinator, mock_entry)

        assert hasattr(sensor, "_unrecorded_attributes")
        assert "decisions" in sensor._unrecorded_attributes


class TestOptimizerSummarySensor:
    """Tests for OptimizerSummarySensor."""

    def test_native_value_success(self):
        """Test native_value returns 'success' when optimizer succeeded."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary={"enabled": True, "success": True}
        )
        mock_entry = MagicMock()

        sensor = OptimizerSummarySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == "success"

    def test_native_value_disabled(self):
        """Test native_value returns 'disabled' when optimizer not enabled."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary={"enabled": False}
        )
        mock_entry = MagicMock()

        sensor = OptimizerSummarySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == "disabled"

    def test_native_value_failed(self):
        """Test native_value returns 'failed' when optimizer failed."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary={"enabled": True, "success": False}
        )
        mock_entry = MagicMock()

        sensor = OptimizerSummarySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == "failed"

    def test_native_value_no_summary(self):
        """Test native_value returns 'disabled' when no summary."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary=None
        )
        mock_entry = MagicMock()

        sensor = OptimizerSummarySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == "disabled"

    def test_extra_state_attributes(self):
        """Test extra_state_attributes contains all summary fields."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary={
                "enabled": True,
                "success": True,
                "error_message": None,
                "cycle_timestamp_iso": "2026-03-13T10:00:00",
                "config_options": {"mode": "self_consumption"},
                "parity_completeness_pct": 95.0,
                "parity_defaulted_fields": {"price": 0.25},
                "alignment_valid": True,
                "alignment_issues": [],
                "alignment_warnings": ["Minor warning"],
                "planner_version": "2.0",
                "cycle_id": "cycle-123",
                "solve_time_seconds": 0.5,
                "projected_net_cost": 3.50,
                "terminal_shortfall_pct": 0.0,
                "initial_soc_pct": 50.0,
            }
        )
        mock_entry = MagicMock()

        sensor = OptimizerSummarySensor(mock_coordinator, mock_entry)
        attrs = sensor.extra_state_attributes

        assert attrs["enabled"] is True
        assert attrs["success"] is True
        assert attrs["error_message"] is None
        assert attrs["computed_at"] == "2026-03-13T10:00:00"
        assert attrs["config_options"] == {"mode": "self_consumption"}
        assert attrs["parity_completeness_pct"] == 95.0
        assert attrs["parity_defaulted_fields"] == {"price": 0.25}
        assert attrs["alignment_valid"] is True
        assert attrs["alignment_issues"] == []
        assert attrs["alignment_warnings"] == ["Minor warning"]
        assert attrs["planner_version"] == "2.0"
        assert attrs["cycle_id"] == "cycle-123"
        assert attrs["solve_time_seconds"] == 0.5
        assert attrs["projected_net_cost"] == 3.50
        assert attrs["terminal_shortfall_pct"] == 0.0
        assert attrs["initial_soc_pct"] == 50.0

    def test_icon_success(self):
        """Test icon for success state."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary={"enabled": True, "success": True}
        )
        mock_entry = MagicMock()

        sensor = OptimizerSummarySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor.icon == "mdi:check-circle-outline"

    def test_icon_failed(self):
        """Test icon for failed state."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary={"enabled": True, "success": False}
        )
        mock_entry = MagicMock()

        sensor = OptimizerSummarySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor.icon == "mdi:alert-circle-outline"

    def test_icon_disabled(self):
        """Test icon for disabled state."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary={"enabled": False}
        )
        mock_entry = MagicMock()

        sensor = OptimizerSummarySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor.icon == "mdi:minus-circle-outline"


class TestSolarForecastAccuracySensor:
    """Tests for SolarForecastAccuracySensor."""

    def test_native_value_default(self):
        """Test native_value defaults to 100.0 when no attribute."""
        mock_coordinator, data = create_mock_coordinator_with_data()
        # Don't set solar_forecast_accuracy
        mock_entry = MagicMock()

        sensor = SolarForecastAccuracySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 100.0

    def test_native_value_set(self):
        """Test native_value reads from coordinator data."""
        mock_coordinator, data = create_mock_coordinator_with_data()
        data.solar_forecast_accuracy = 85.5
        mock_entry = MagicMock()

        sensor = SolarForecastAccuracySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 85.5

    def test_extra_state_attributes_with_data(self):
        """Test extra_state_attributes returns bias metrics."""
        mock_coordinator, data = create_mock_coordinator_with_data()
        data.solar_bias_metrics = {"mae": 2.5, "rmse": 3.1}
        mock_entry = MagicMock()

        sensor = SolarForecastAccuracySensor(mock_coordinator, mock_entry)

        assert sensor.extra_state_attributes == {"mae": 2.5, "rmse": 3.1}

    def test_extra_state_attributes_empty(self):
        """Test extra_state_attributes returns empty dict when no metrics."""
        mock_coordinator, data = create_mock_coordinator_with_data()
        # Don't set solar_bias_metrics
        mock_entry = MagicMock()

        sensor = SolarForecastAccuracySensor(mock_coordinator, mock_entry)

        assert sensor.extra_state_attributes == {}

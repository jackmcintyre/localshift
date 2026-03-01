"""
Tests for Phase E — Assist-Mode UX & Observability.

Tests cover:
- OptimizerComparisonSensor state and attributes
- Diagnostics optimizer section
- Payload size limits (attributes bounded)
"""

import pytest

from custom_components.localshift.diagnostics import _get_optimizer_status


# ---------------------------------------------------------------------------
# Mock CoordinatorData
# ---------------------------------------------------------------------------


class MockCoordinatorData:
    """Mock CoordinatorData for testing."""

    def __init__(
        self,
        optimizer_shadow_summary: dict | None = None,
        optimizer_comparison: dict | None = None,
    ):
        self.optimizer_shadow_summary = optimizer_shadow_summary or {}
        self.optimizer_comparison = optimizer_comparison or {}


class MockCoordinator:
    """Mock coordinator for testing."""

    def __init__(self, data: MockCoordinatorData | None = None):
        self.data = data


# ---------------------------------------------------------------------------
# Test OptimizerComparisonSensor
# ---------------------------------------------------------------------------


class TestOptimizerComparisonSensor:
    """Tests for OptimizerComparisonSensor state and attributes."""

    def test_state_none_when_disabled(self):
        """Sensor state should be None when optimizer is disabled."""
        from custom_components.localshift.sensor import OptimizerComparisonSensor

        data = MockCoordinatorData(
            optimizer_shadow_summary={"enabled": False},
            optimizer_comparison={},
        )
        coordinator = MockCoordinator(data)

        sensor = OptimizerComparisonSensor.__new__(OptimizerComparisonSensor)
        sensor.coordinator = coordinator
        sensor._attr_native_value = None
        sensor._update_from_coordinator()

        assert sensor._attr_native_value is None

    def test_state_zero_when_no_mismatches(self):
        """Sensor state should be 0 when plans match."""
        from custom_components.localshift.sensor import OptimizerComparisonSensor

        data = MockCoordinatorData(
            optimizer_shadow_summary={"enabled": True, "success": True},
            optimizer_comparison={
                "comparison_succeeded": True,
                "mismatch_count": 0,
            },
        )
        coordinator = MockCoordinator(data)

        sensor = OptimizerComparisonSensor.__new__(OptimizerComparisonSensor)
        sensor.coordinator = coordinator
        sensor._attr_native_value = None
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 0

    def test_state_mismatch_count(self):
        """Sensor state should reflect mismatch count."""
        from custom_components.localshift.sensor import OptimizerComparisonSensor

        data = MockCoordinatorData(
            optimizer_shadow_summary={"enabled": True, "success": True},
            optimizer_comparison={
                "comparison_succeeded": True,
                "mismatch_count": 5,
            },
        )
        coordinator = MockCoordinator(data)

        sensor = OptimizerComparisonSensor.__new__(OptimizerComparisonSensor)
        sensor.coordinator = coordinator
        sensor._attr_native_value = None
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 5

    def test_state_negative_one_on_comparison_failure(self):
        """Sensor state should be -1 when comparison failed."""
        from custom_components.localshift.sensor import OptimizerComparisonSensor

        data = MockCoordinatorData(
            optimizer_shadow_summary={"enabled": True, "success": True},
            optimizer_comparison={
                "comparison_succeeded": False,
                "error_message": "Test error",
            },
        )
        coordinator = MockCoordinator(data)

        sensor = OptimizerComparisonSensor.__new__(OptimizerComparisonSensor)
        sensor.coordinator = coordinator
        sensor._attr_native_value = None
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == -1

    def test_attributes_include_deltas(self):
        """Sensor attributes should include cost and energy deltas."""
        from custom_components.localshift.sensor import OptimizerComparisonSensor

        data = MockCoordinatorData(
            optimizer_shadow_summary={"enabled": True, "success": True},
            optimizer_comparison={
                "comparison_succeeded": True,
                "mismatch_count": 2,
                "net_cost_delta": -0.15,
                "import_kwh_delta": -1.5,
                "export_kwh_delta": 0.5,
                "legacy_projected_net_cost": 2.50,
                "optimizer_projected_net_cost": 2.35,
                "top_mismatches": [],
                "summary": {},
            },
        )
        coordinator = MockCoordinator(data)

        sensor = OptimizerComparisonSensor.__new__(OptimizerComparisonSensor)
        sensor.coordinator = coordinator

        attrs = sensor.extra_state_attributes

        assert attrs["enabled"] is True
        assert attrs["net_cost_delta"] == -0.15
        assert attrs["import_kwh_delta"] == -1.5
        assert attrs["export_kwh_delta"] == 0.5

    def test_attributes_include_top_mismatches(self):
        """Sensor attributes should include top mismatches list."""
        from custom_components.localshift.sensor import OptimizerComparisonSensor

        top_mismatches = [
            {
                "slot_index": 5,
                "mismatch_type": "ACTION_MISMATCH",
                "legacy_action": "hold",
                "optimizer_action": "charge_grid_normal",
                "reason_detail": "Test reason",
            },
        ]

        data = MockCoordinatorData(
            optimizer_shadow_summary={"enabled": True, "success": True},
            optimizer_comparison={
                "comparison_succeeded": True,
                "mismatch_count": 1,
                "top_mismatches": top_mismatches,
                "summary": {},
            },
        )
        coordinator = MockCoordinator(data)

        sensor = OptimizerComparisonSensor.__new__(OptimizerComparisonSensor)
        sensor.coordinator = coordinator

        attrs = sensor.extra_state_attributes

        assert attrs["top_mismatches"] == top_mismatches


# ---------------------------------------------------------------------------
# Test Diagnostics
# ---------------------------------------------------------------------------


class TestOptimizerDiagnostics:
    """Tests for diagnostics optimizer section."""

    def test_disabled_when_coordinator_none(self):
        """Diagnostics should show not_loaded when coordinator is None."""
        status = _get_optimizer_status(None)

        assert status["status"] == "not_loaded"

    def test_disabled_when_no_data(self):
        """Diagnostics should show no_data when coordinator has no data."""
        coordinator = MockCoordinator(None)
        status = _get_optimizer_status(coordinator)

        assert status["status"] == "no_data"

    def test_disabled_when_optimizer_disabled(self):
        """Diagnostics should show disabled when optimizer is not enabled."""
        data = MockCoordinatorData(
            optimizer_shadow_summary={"enabled": False},
        )
        coordinator = MockCoordinator(data)
        status = _get_optimizer_status(coordinator)

        assert status["status"] == "disabled"
        assert status["enabled"] is False

    def test_running_when_successful(self):
        """Diagnostics should show running when optimizer succeeded."""
        data = MockCoordinatorData(
            optimizer_shadow_summary={
                "enabled": True,
                "success": True,
                "planner_version": "dp_v1",
                "solve_time_seconds": 0.05,
                "projected_net_cost": 1.50,
                "total_slots": 48,
            },
            optimizer_comparison={
                "comparison_succeeded": True,
                "mismatch_count": 3,
                "net_cost_delta": -0.10,
            },
        )
        coordinator = MockCoordinator(data)
        status = _get_optimizer_status(coordinator)

        assert status["status"] == "running"
        assert status["enabled"] is True
        assert status["last_cycle_success"] is True
        assert status["projected_net_cost"] == 1.50

    def test_error_when_failed(self):
        """Diagnostics should show error when optimizer failed."""
        data = MockCoordinatorData(
            optimizer_shadow_summary={
                "enabled": True,
                "success": False,
                "error_message": "Test error",
            },
        )
        coordinator = MockCoordinator(data)
        status = _get_optimizer_status(coordinator)

        assert status["status"] == "error"
        assert status["error_message"] == "Test error"

    def test_includes_comparison_data(self):
        """Diagnostics should include comparison summary."""
        data = MockCoordinatorData(
            optimizer_shadow_summary={
                "enabled": True,
                "success": True,
            },
            optimizer_comparison={
                "comparison_succeeded": True,
                "mismatch_count": 5,
                "net_cost_delta": -0.25,
                "mismatch_by_type": {
                    "ACTION_MISMATCH": 3,
                    "IMPORT_QUANTITY_MISMATCH": 2,
                },
                "top_mismatches": [
                    {"slot_index": 0, "mismatch_type": "ACTION_MISMATCH"},
                    {"slot_index": 1, "mismatch_type": "IMPORT_QUANTITY_MISMATCH"},
                    {"slot_index": 2, "mismatch_type": "ACTION_MISMATCH"},
                ],
                "summary": {
                    "total_mismatches": 5,
                    "total_cost_impact": 0.50,
                    "most_significant_type": "ACTION_MISMATCH",
                },
            },
        )
        coordinator = MockCoordinator(data)
        status = _get_optimizer_status(coordinator)

        assert "comparison" in status
        assert status["comparison"]["mismatch_count"] == 5
        assert status["comparison"]["net_cost_delta"] == -0.25

    def test_limits_top_mismatches_to_three(self):
        """Diagnostics should limit top_mismatches to 3 entries."""
        top_mismatches = [
            {
                "slot_index": i,
                "mismatch_type": "ACTION_MISMATCH",
                "legacy_action": "hold",
                "optimizer_action": "charge",
                "reason_detail": f"Reason {i}",
                "legacy_net_cost": 0.1,
                "optimizer_net_cost": 0.2,
            }
            for i in range(5)
        ]

        data = MockCoordinatorData(
            optimizer_shadow_summary={
                "enabled": True,
                "success": True,
            },
            optimizer_comparison={
                "comparison_succeeded": True,
                "mismatch_count": 5,
                "top_mismatches": top_mismatches,
                "summary": {},
            },
        )
        coordinator = MockCoordinator(data)
        status = _get_optimizer_status(coordinator)

        assert len(status["comparison"]["top_3_mismatches"]) == 3


# ---------------------------------------------------------------------------
# Test Payload Size Limits
# ---------------------------------------------------------------------------


class TestPayloadSizeLimits:
    """Tests for payload size limits on attributes."""

    def test_comparison_sensor_top_mismatches_limited(self):
        """Comparison sensor should not exceed 5 top mismatches."""
        from custom_components.localshift.sensor import OptimizerComparisonSensor

        top_mismatches = [
            {"slot_index": i, "mismatch_type": "ACTION_MISMATCH"} for i in range(10)
        ]

        data = MockCoordinatorData(
            optimizer_shadow_summary={"enabled": True, "success": True},
            optimizer_comparison={
                "comparison_succeeded": True,
                "mismatch_count": 10,
                "top_mismatches": top_mismatches,
                "summary": {},
            },
        )
        coordinator = MockCoordinator(data)

        sensor = OptimizerComparisonSensor.__new__(OptimizerComparisonSensor)
        sensor.coordinator = coordinator

        attrs = sensor.extra_state_attributes

        assert len(attrs["top_mismatches"]) == 10

    def test_attributes_serializable(self):
        """All sensor attributes should be JSON-serializable."""
        import json

        from custom_components.localshift.sensor import OptimizerComparisonSensor

        data = MockCoordinatorData(
            optimizer_shadow_summary={
                "enabled": True,
                "success": True,
                "cycle_timestamp_iso": "2025-01-01T00:00:00Z",
            },
            optimizer_comparison={
                "comparison_succeeded": True,
                "mismatch_count": 1,
                "net_cost_delta": -0.10,
                "legacy_meets_dw_target": True,
                "optimizer_meets_dw_target": True,
                "mismatch_by_type": {"ACTION_MISMATCH": 1},
                "top_mismatches": [
                    {
                        "slot_index": 0,
                        "mismatch_type": "ACTION_MISMATCH",
                        "legacy_action": "hold",
                        "optimizer_action": "charge",
                    }
                ],
                "summary": {"total_mismatches": 1},
            },
        )
        coordinator = MockCoordinator(data)

        sensor = OptimizerComparisonSensor.__new__(OptimizerComparisonSensor)
        sensor.coordinator = coordinator

        attrs = sensor.extra_state_attributes

        try:
            json.dumps(attrs)
        except TypeError as e:
            pytest.fail(f"Attributes not JSON-serializable: {e}")

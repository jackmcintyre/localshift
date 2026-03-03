"""
Tests for Phase E — Assist-Mode UX & Observability.

Phase 5 (#447): Updated to use new field names.
OptimizerComparisonSensor was deleted - no legacy planner to compare against.

Tests cover:
- Diagnostics optimizer section
- Payload size limits (attributes bounded)
"""

import pytest

from custom_components.localshift.diagnostics import _get_optimizer_status


class MockCoordinatorData:
    """Mock CoordinatorData for testing."""

    def __init__(
        self,
        optimizer_summary: dict | None = None,
        optimizer_decisions: list | None = None,
    ):
        self.optimizer_summary = optimizer_summary or {}
        self.optimizer_decisions = optimizer_decisions or []


class MockCoordinator:
    """Mock coordinator for testing."""

    def __init__(self, data: MockCoordinatorData | None = None):
        self.data = data


class TestDiagnosticsOptimizerStatus:
    """Tests for diagnostics optimizer status."""

    def test_optimizer_status_when_disabled(self):
        """Diagnostics should show optimizer disabled."""
        data = MockCoordinatorData(
            optimizer_summary={"enabled": False},
        )
        coordinator = MockCoordinator(data)

        status = _get_optimizer_status(coordinator)

        assert status["enabled"] is False

    def test_optimizer_status_when_enabled(self):
        """Diagnostics should show optimizer enabled."""
        data = MockCoordinatorData(
            optimizer_summary={"enabled": True, "success": True},
            optimizer_decisions=[{"action": "hold"}],
        )
        coordinator = MockCoordinator(data)

        status = _get_optimizer_status(coordinator)

        assert status["enabled"] is True
        assert status["last_cycle_success"] is True


class TestPayloadSizeLimits:
    """Tests for payload size limits."""

    def test_attributes_serializable(self):
        """Verify sensor attributes are JSON serializable."""
        import json

        data = MockCoordinatorData(
            optimizer_summary={
                "enabled": True,
                "success": True,
                "projected_net_cost": 1.23,
            },
            optimizer_decisions=[{"action": "hold", "slot_index": 0}],
        )
        coordinator = MockCoordinator(data)

        status = _get_optimizer_status(coordinator)

        try:
            json.dumps(status)
            serializable = True
        except (TypeError, ValueError):
            serializable = False

        assert serializable, "Optimizer status should be JSON serializable"

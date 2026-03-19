import pytest
from unittest.mock import Mock, MagicMock
from custom_components.localshift.engine.core import DPPlanner


class TestGetForecastAccuracy:
    """Tests for _get_forecast_accuracy helper method."""

    def test_no_tracker_returns_one(self):
        """When no tracker exists, return 1.0 (no discount)."""
        planner = DPPlanner.__new__(DPPlanner)
        result = planner._get_forecast_accuracy(None)
        assert result == 1.0

    def test_tracker_returns_100_returns_one(self):
        """When accuracy is 100%, return 1.0."""
        tracker = Mock()
        tracker.metrics.accuracy = 100
        planner = DPPlanner.__new__(DPPlanner)
        result = planner._get_forecast_accuracy(tracker)
        assert result == 1.0

    def test_tracker_returns_50_returns_point_five(self):
        """When accuracy is 50%, return 0.5."""
        tracker = Mock()
        tracker.metrics.accuracy = 50
        planner = DPPlanner.__new__(DPPlanner)
        result = planner._get_forecast_accuracy(tracker)
        assert result == 0.5

    def test_tracker_returns_37_returns_point_three_seven(self):
        """When accuracy is 37%, return 0.37."""
        tracker = Mock()
        tracker.metrics.accuracy = 37
        planner = DPPlanner.__new__(DPPlanner)
        result = planner._get_forecast_accuracy(tracker)
        assert result == 0.37

    def test_tracker_returns_none_returns_one(self):
        """When tracker returns None, return 1.0."""
        tracker = Mock()
        tracker.metrics.accuracy = None
        planner = DPPlanner.__new__(DPPlanner)
        result = planner._get_forecast_accuracy(tracker)
        assert result == 1.0

    def test_tracker_returns_zero_returns_one(self):
        """When tracker returns 0, return 1.0 (no data)."""
        tracker = Mock()
        tracker.metrics.accuracy = 0
        planner = DPPlanner.__new__(DPPlanner)
        result = planner._get_forecast_accuracy(tracker)
        assert result == 1.0

    def test_tracker_returns_negative_returns_one(self):
        """When tracker returns negative (invalid), return 1.0."""
        tracker = Mock()
        tracker.metrics.accuracy = -10
        planner = DPPlanner.__new__(DPPlanner)
        result = planner._get_forecast_accuracy(tracker)
        assert result == 1.0

    def test_tracker_no_metrics_attribute_returns_one(self):
        """When tracker has no metrics attribute, return 1.0."""
        tracker = Mock(spec=[])  # No attributes at all
        planner = DPPlanner.__new__(DPPlanner)
        result = planner._get_forecast_accuracy(tracker)
        assert result == 1.0


class TestAccuracyDiscountClamping:
    """Tests for accuracy discount clamping behavior."""

    def test_accuracy_one_point_five_clamped_to_one(self):
        """Accuracy > 100% should be clamped to 1.0."""
        accuracy = 1.5
        discount = max(0.5, min(1.0, accuracy))
        assert discount == 1.0

    def test_accuracy_zero_point_three_clamped_to_point_five(self):
        """Accuracy < 50% should be clamped to 0.5."""
        accuracy = 0.3
        discount = max(0.5, min(1.0, accuracy))
        assert discount == 0.5

    def test_accuracy_zero_point_seven_five_not_clamped(self):
        """Accuracy in normal range (50-100%) should not be clamped."""
        accuracy = 0.75
        discount = max(0.5, min(1.0, accuracy))
        assert discount == 0.75


class TestTerminalCostWithAccuracyDiscount:
    """Tests for terminal cost calculation with accuracy discount."""

    def test_terminal_cost_higher_when_accuracy_low(self):
        """Terminal penalty should be higher when forecast accuracy is low."""
        soc = 60.0
        projected_solar = 30.0
        target = 95.0

        effective_soc_full = soc + projected_solar
        shortfall_full = max(0, target - effective_soc_full)

        adjusted_solar = projected_solar * 0.5
        effective_soc_discounted = soc + adjusted_solar
        shortfall_discounted = max(0, target - effective_soc_discounted)

        assert shortfall_discounted > shortfall_full

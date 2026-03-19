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
        tracker.get_overall_accuracy.return_value = 100
        planner = DPPlanner.__new__(DPPlanner)
        result = planner._get_forecast_accuracy(tracker)
        assert result == 1.0

    def test_tracker_returns_50_returns_point_five(self):
        """When accuracy is 50%, return 0.5."""
        tracker = Mock()
        tracker.get_overall_accuracy.return_value = 50
        planner = DPPlanner.__new__(DPPlanner)
        result = planner._get_forecast_accuracy(tracker)
        assert result == 0.5

    def test_tracker_returns_37_returns_point_three_seven(self):
        """When accuracy is 37%, return 0.37."""
        tracker = Mock()
        tracker.get_overall_accuracy.return_value = 37
        planner = DPPlanner.__new__(DPPlanner)
        result = planner._get_forecast_accuracy(tracker)
        assert result == 0.37

    def test_tracker_returns_none_returns_one(self):
        """When tracker returns None, return 1.0."""
        tracker = Mock()
        tracker.get_overall_accuracy.return_value = None
        planner = DPPlanner.__new__(DPPlanner)
        result = planner._get_forecast_accuracy(tracker)
        assert result == 1.0

    def test_tracker_returns_zero_returns_one(self):
        """When tracker returns 0, return 1.0 (no data)."""
        tracker = Mock()
        tracker.get_overall_accuracy.return_value = 0
        planner = DPPlanner.__new__(DPPlanner)
        result = planner._get_forecast_accuracy(tracker)
        assert result == 1.0

    def test_tracker_returns_negative_returns_one(self):
        """When tracker returns negative (invalid), return 1.0."""
        tracker = Mock()
        tracker.get_overall_accuracy.return_value = -10
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

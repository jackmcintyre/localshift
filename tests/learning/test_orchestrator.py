"""Tests for LearningOrchestrator.

This file aggregates tests from test_learning_integration.py for coverage.
"""

from unittest.mock import MagicMock

import pytest

from custom_components.localshift.coordinator.data import (
    CoordinatorData,
    PerformanceMetrics,
)

# Import all orchestrator-related tests from integration test file
from tests.learning.test_learning_integration import (  # noqa: F401
    TestLearningOrchestratorForecastCorrections,
)


def _make_orchestrator():
    from custom_components.localshift.learning.orchestrator import LearningOrchestrator

    hass = MagicMock()
    hass.async_create_task = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {}
    return LearningOrchestrator(hass, entry, get_switch_state=lambda k: False)


class TestOrchestratorWeatherWeightPropagation:
    @pytest.fixture
    def orchestrator(self):
        return _make_orchestrator()

    def test_orchestrator_passes_weather_weight_to_optimize(self, orchestrator):
        mock_optimizer = MagicMock()
        mock_optimizer.should_update.return_value = True
        mock_optimizer.optimize.return_value = MagicMock()
        orchestrator.param_optimizer = mock_optimizer

        mock_tracker = MagicMock()
        mock_tracker._completed_decisions = [MagicMock() for _ in range(60)]
        mock_tracker.save_pending = False
        mock_tracker.get_recent_decisions.return_value = []
        mock_tracker.get_daily_summary.return_value = PerformanceMetrics()
        mock_tracker.get_decision_log.return_value = []
        mock_tracker.backfill_outcomes = MagicMock()
        orchestrator.decision_tracker = mock_tracker

        data = CoordinatorData()
        data.weather_anomaly_weight = 0.3
        data.performance_metrics = PerformanceMetrics()

        orchestrator.update_medium_tick(data)

        assert mock_optimizer.optimize.called
        call_kwargs = mock_optimizer.optimize.call_args.kwargs
        assert call_kwargs.get("weather_weight") == pytest.approx(0.3)

    def test_orchestrator_passes_normal_weight_when_no_anomaly(self, orchestrator):
        mock_optimizer = MagicMock()
        mock_optimizer.should_update.return_value = True
        mock_optimizer.optimize.return_value = MagicMock()
        orchestrator.param_optimizer = mock_optimizer

        mock_tracker = MagicMock()
        mock_tracker._completed_decisions = [MagicMock() for _ in range(60)]
        mock_tracker.save_pending = False
        mock_tracker.get_recent_decisions.return_value = []
        mock_tracker.get_daily_summary.return_value = PerformanceMetrics()
        mock_tracker.get_decision_log.return_value = []
        mock_tracker.backfill_outcomes = MagicMock()
        orchestrator.decision_tracker = mock_tracker

        data = CoordinatorData()
        data.performance_metrics = PerformanceMetrics()

        orchestrator.update_medium_tick(data)

        call_kwargs = mock_optimizer.optimize.call_args.kwargs
        assert call_kwargs.get("weather_weight") == pytest.approx(1.0)

"""Tests for LearningOrchestrator.

This file aggregates tests from test_learning_integration.py for coverage.
"""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from homeassistant.util import dt as dt_util

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


def _close_scheduled_coros(hass):
    """Close any un-awaited coroutines passed to the mock async_create_task."""
    for call in hass.async_create_task.call_args_list:
        coro = call.args[0]
        if hasattr(coro, "close"):
            coro.close()


def _scheduled_task_names(hass):
    return [call.args[1] for call in hass.async_create_task.call_args_list]


class TestDeriveLearningStatus:
    @pytest.mark.parametrize(
        ("count", "expected"),
        [
            (49, "observing"),
            (50, "tuning"),
            (99, "tuning"),
            (100, "optimizing"),
        ],
    )
    def test_boundaries(self, count, expected):
        from custom_components.localshift.learning.orchestrator import (
            LearningOrchestrator,
        )

        assert LearningOrchestrator._derive_learning_status(count) == expected


class TestMaybeRunPatternAnalysis:
    def _wire(self, orchestrator, *, last_analysis_time, decision_count=60):
        orchestrator.pattern_analyzer = MagicMock(last_analysis_time=last_analysis_time)
        orchestrator.decision_tracker = MagicMock(
            get_recent_decisions=MagicMock(return_value=[1] * decision_count)
        )

    def test_fires_when_never_run(self):
        orchestrator = _make_orchestrator()
        self._wire(orchestrator, last_analysis_time=None)

        orchestrator.maybe_run_pattern_analysis(CoordinatorData())

        assert "localshift_pattern_analysis" in _scheduled_task_names(orchestrator.hass)
        _close_scheduled_coros(orchestrator.hass)

    def test_fires_when_older_than_interval(self):
        orchestrator = _make_orchestrator()
        self._wire(
            orchestrator,
            last_analysis_time=dt_util.now() - timedelta(days=8),
        )

        orchestrator.maybe_run_pattern_analysis(CoordinatorData())

        assert "localshift_pattern_analysis" in _scheduled_task_names(orchestrator.hass)
        _close_scheduled_coros(orchestrator.hass)

    def test_does_not_fire_when_fresh(self):
        orchestrator = _make_orchestrator()
        self._wire(orchestrator, last_analysis_time=dt_util.now() - timedelta(days=1))

        orchestrator.maybe_run_pattern_analysis(CoordinatorData())

        assert orchestrator.hass.async_create_task.call_count == 0

    def test_interval_varies_with_status(self):
        # 4 days elapsed: due while "optimizing" (interval 3), not while "observing" (7).
        orchestrator = _make_orchestrator()
        self._wire(orchestrator, last_analysis_time=dt_util.now() - timedelta(days=4))

        observing = CoordinatorData()
        observing.learning_status = "observing"
        orchestrator.maybe_run_pattern_analysis(observing)
        assert orchestrator.hass.async_create_task.call_count == 0

        optimizing = CoordinatorData()
        optimizing.learning_status = "optimizing"
        orchestrator.maybe_run_pattern_analysis(optimizing)
        assert "localshift_pattern_analysis" in _scheduled_task_names(orchestrator.hass)
        _close_scheduled_coros(orchestrator.hass)

    def test_throttle_blocks_second_attempt(self):
        orchestrator = _make_orchestrator()
        self._wire(orchestrator, last_analysis_time=None)

        orchestrator.maybe_run_pattern_analysis(CoordinatorData())
        first_count = orchestrator.hass.async_create_task.call_count
        # Immediately try again — the 1h reservation should block re-scheduling.
        orchestrator.maybe_run_pattern_analysis(CoordinatorData())

        assert orchestrator.hass.async_create_task.call_count == first_count
        _close_scheduled_coros(orchestrator.hass)

    def test_simulated_restart_schedules_overdue_analysis(self):
        """Headline: a fresh orchestrator's first medium tick runs overdue analysis.

        Persisted last_analysis_time 8 days old + >=50 persisted decisions means
        the old per-restart counter would never have triggered — this does.
        """
        orchestrator = _make_orchestrator()
        orchestrator.pattern_analyzer = MagicMock(
            last_analysis_time=dt_util.now() - timedelta(days=8)
        )
        orchestrator.param_optimizer = None
        orchestrator.optimization_controller = None

        tracker = MagicMock()
        tracker.backfill_outcomes = MagicMock()
        tracker.get_daily_summary.return_value = PerformanceMetrics()
        tracker.get_decision_log.return_value = []
        tracker.save_pending = False
        tracker._completed_decisions = [MagicMock() for _ in range(60)]
        tracker.get_recent_decisions.return_value = [1] * 60
        orchestrator.decision_tracker = tracker

        data = CoordinatorData()
        data.performance_metrics = PerformanceMetrics()
        orchestrator.update_medium_tick(data)

        assert "localshift_pattern_analysis" in _scheduled_task_names(orchestrator.hass)
        _close_scheduled_coros(orchestrator.hass)


class TestRestoreRuntimeState:
    def test_status_and_report_restored(self):
        orchestrator = _make_orchestrator()

        orchestrator.decision_tracker = MagicMock(
            get_recent_decisions=MagicMock(return_value=[1] * 120)
        )

        bias = MagicMock()
        bias.to_dict.return_value = {"bias": 1}
        report = MagicMock()
        report.get_summary.return_value = {"summary": 1}
        report.biases_detected = [bias]

        analysis_time = dt_util.now() - timedelta(days=2)
        orchestrator.pattern_analyzer = MagicMock(
            get_last_report=MagicMock(return_value=report),
            last_analysis_time=analysis_time,
        )
        orchestrator.param_optimizer = MagicMock(set_bias_corrections=MagicMock())

        data = CoordinatorData()
        orchestrator.restore_runtime_state(data)

        assert data.learning_status == "optimizing"
        assert data.pattern_report_summary == {"summary": 1}
        assert data.active_bias_corrections == [{"bias": 1}]
        orchestrator.param_optimizer.set_bias_corrections.assert_called_once_with(
            [bias]
        )
        assert data.last_pattern_analysis == analysis_time.isoformat()

    def test_no_report_derives_status_only(self):
        orchestrator = _make_orchestrator()
        orchestrator.decision_tracker = MagicMock(
            get_recent_decisions=MagicMock(return_value=[1] * 10)
        )
        orchestrator.pattern_analyzer = MagicMock(
            get_last_report=MagicMock(return_value=None),
            last_analysis_time=None,
        )

        data = CoordinatorData()
        orchestrator.restore_runtime_state(data)

        assert data.learning_status == "observing"
        assert data.last_pattern_analysis is None

"""Tests for LearningOrchestrator."""

import asyncio
from datetime import UTC, datetime, timedelta
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.coordinator.data import (
    CoordinatorData,
    PerformanceMetrics,
)


def _make_orchestrator():
    from custom_components.localshift.learning.orchestrator import LearningOrchestrator

    hass = MagicMock()
    hass.async_create_task = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {}
    return LearningOrchestrator(hass, entry, get_switch_state=lambda k: False)


@pytest.mark.asyncio
async def test_async_initialize_loads_components(monkeypatch):
    from custom_components.localshift.learning import orchestrator as module
    import sys
    import types

    class DummyTracker:
        def __init__(self, *args, **_kwargs) -> None:
            self.completed_count = 0

        async def async_load(self) -> None:
            return None

    class DummyOptimizer:
        def __init__(self, *args, **_kwargs) -> None:
            return None

        async def async_load(self) -> None:
            return None

    class DummyAnalyzer:
        def __init__(self, *args, **_kwargs) -> None:
            return None

        async def async_load(self) -> None:
            return None

    class DummyController:
        def __init__(self, *args, **_kwargs) -> None:
            self.learning_enabled = False

        async def async_load(self) -> None:
            return None

        def set_learning_enabled(self, enabled: bool) -> None:
            self.learning_enabled = enabled

    class DummyForecast:
        def __init__(self, *args, **_kwargs) -> None:
            return None

        @classmethod
        def from_dict(cls, _data):
            return cls()

        def to_dict(self) -> dict:
            return {}

    class DummyLearner:
        def __init__(self, *args, **_kwargs) -> None:
            return None

        async def async_load(self) -> None:
            return None

    class DummyCounterfactual:
        def __init__(self, *args, **_kwargs) -> None:
            return None

    store = AsyncMock()
    store.async_load.return_value = {"ok": True}

    outcomes_module = types.SimpleNamespace(DecisionOutcomeTracker=DummyTracker)
    params_module = types.SimpleNamespace(ParameterOptimizer=DummyOptimizer)
    pattern_module = types.SimpleNamespace(PatternAnalyzer=DummyAnalyzer)
    opt_module = types.SimpleNamespace(OptimizationController=DummyController)
    sys.modules["custom_components.localshift.engine.outcomes"] = outcomes_module
    sys.modules["custom_components.localshift.engine.parameters"] = params_module
    sys.modules["custom_components.localshift.engine.pattern_analyzer"] = pattern_module
    sys.modules["custom_components.localshift.engine.optimization_controller"] = (
        opt_module
    )
    monkeypatch.setattr(module, "ForecastCorrectionProvider", DummyForecast)
    monkeypatch.setattr(module, "ChargeRateLearner", DummyLearner)
    monkeypatch.setattr(module, "CounterfactualEvaluator", DummyCounterfactual)
    monkeypatch.setattr(module, "Store", MagicMock(return_value=store))

    orchestrator = _make_orchestrator()
    await orchestrator.async_initialize()

    assert orchestrator.decision_tracker is not None
    assert orchestrator.param_optimizer is not None
    assert orchestrator.pattern_analyzer is not None
    assert orchestrator.optimization_controller is not None
    assert orchestrator.charge_rate_learner is not None


def test_attach_state_machine_sets_decision_tracker():
    orchestrator = _make_orchestrator()
    orchestrator.decision_tracker = MagicMock()
    state_machine = MagicMock()

    orchestrator.attach_state_machine(state_machine)

    assert state_machine._decision_tracker is orchestrator.decision_tracker


class TestOrchestratorChargeRateLearning:
    def test_schedule_charge_rate_update_skips_recent(self, monkeypatch):
        orchestrator = _make_orchestrator()
        orchestrator.decision_tracker = MagicMock()
        orchestrator.charge_rate_learner = MagicMock()
        now = datetime(2024, 1, 1)
        monkeypatch.setattr(
            "custom_components.localshift.learning.orchestrator.dt_util.now",
            lambda: now,
        )
        orchestrator._last_charge_rate_update = now - timedelta(hours=12)

        data = CoordinatorData()
        orchestrator._schedule_charge_rate_update(data)

        orchestrator.hass.async_create_task.assert_not_called()

    def test_schedule_charge_rate_update_skips_recent_attempt(self, monkeypatch):
        orchestrator = _make_orchestrator()
        orchestrator.decision_tracker = MagicMock()
        orchestrator.charge_rate_learner = MagicMock()
        now = datetime(2024, 1, 1)
        monkeypatch.setattr(
            "custom_components.localshift.learning.orchestrator.dt_util.now",
            lambda: now,
        )
        orchestrator._last_charge_rate_attempt = now - timedelta(minutes=30)

        data = CoordinatorData()
        orchestrator._schedule_charge_rate_update(data)

        orchestrator.hass.async_create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_orchestrator_updates_charge_rate_curves(self):
        from custom_components.localshift.learning.orchestrator import (
            LearningOrchestrator,
        )

        hass = MagicMock()
        scheduled: list = []

        def _capture_task(coro, _name=None):
            scheduled.append(coro)
            return MagicMock()

        hass.async_create_task = _capture_task

        entry = MagicMock()
        entry.entry_id = "test_entry"
        orchestrator = LearningOrchestrator(
            hass, entry, get_switch_state=lambda k: True
        )

        mock_tracker = MagicMock()
        mock_tracker.save_pending = False
        mock_tracker._completed_decisions = []
        mock_tracker.backfill_outcomes = MagicMock()
        mock_tracker.get_daily_summary.return_value = PerformanceMetrics()
        mock_tracker.get_decision_log.return_value = []
        mock_tracker.get_recent_decisions.return_value = [MagicMock()]
        orchestrator.decision_tracker = mock_tracker

        curve_normal = MagicMock()
        curve_boost = MagicMock()
        charge_rate_learner = MagicMock()
        history_point = (datetime.now(), 1.0)
        charge_rate_learner.async_fetch_history = AsyncMock(
            return_value=([history_point], [history_point])
        )
        charge_rate_learner.update_from_history.return_value = True
        charge_rate_learner.get_curve.side_effect = lambda regime: (
            curve_normal if regime == "normal" else curve_boost
        )
        charge_rate_learner.diagnostics = {"labeled_sample_ratio": 1.0}
        charge_rate_learner.async_save = AsyncMock()
        orchestrator.charge_rate_learner = charge_rate_learner

        data = CoordinatorData()
        data.performance_metrics = PerformanceMetrics()

        orchestrator.update_medium_tick(data)

        assert scheduled
        await scheduled[0]

        for task in scheduled[1:]:
            if asyncio.iscoroutine(task):
                await task

        assert data.learning_enabled is True
        assert data.charge_rate_curves["normal"] is curve_normal
        assert data.charge_rate_curves["boost"] is curve_boost

    @pytest.mark.asyncio
    async def test_async_update_charge_rate_skips_without_learner(self):
        orchestrator = _make_orchestrator()
        orchestrator.decision_tracker = MagicMock()
        orchestrator.charge_rate_learner = None

        data = CoordinatorData()
        await orchestrator._async_update_charge_rate(data)

    @pytest.mark.asyncio
    async def test_async_update_charge_rate_skips_missing_history(self):
        orchestrator = _make_orchestrator()
        orchestrator.decision_tracker = MagicMock()
        learner = MagicMock()
        learner.async_fetch_history = AsyncMock(return_value=([], []))
        orchestrator.charge_rate_learner = learner

        data = CoordinatorData()
        await orchestrator._async_update_charge_rate(data)

        assert orchestrator._last_charge_rate_update is None

    @pytest.mark.asyncio
    async def test_async_update_charge_rate_skips_when_no_curves(self):
        orchestrator = _make_orchestrator()
        orchestrator.decision_tracker = MagicMock()
        learner = MagicMock()
        history_point = (datetime.now(), 1.0)
        learner.async_fetch_history = AsyncMock(
            return_value=([history_point], [history_point])
        )
        learner.update_from_history.return_value = True
        learner.get_curve.return_value = None
        orchestrator.charge_rate_learner = learner

        data = CoordinatorData()
        await orchestrator._async_update_charge_rate(data)

        assert orchestrator._last_charge_rate_update is None
        assert getattr(data, "charge_rate_curves", None) is None

    @pytest.mark.asyncio
    async def test_async_update_charge_rate_skips_when_not_updated(self):
        orchestrator = _make_orchestrator()
        orchestrator.decision_tracker = MagicMock()
        learner = MagicMock()
        history_point = (datetime.now(), 1.0)
        learner.async_fetch_history = AsyncMock(
            return_value=([history_point], [history_point])
        )
        learner.update_from_history.return_value = False
        orchestrator.charge_rate_learner = learner

        data = CoordinatorData()
        await orchestrator._async_update_charge_rate(data)

        assert orchestrator._last_charge_rate_update is None


class TestOrchestratorLifecycle:
    @pytest.mark.asyncio
    async def test_async_save_all_handles_failures(self):
        orchestrator = _make_orchestrator()
        orchestrator.decision_tracker = MagicMock(
            async_save=AsyncMock(), completed_count=0
        )
        orchestrator.param_optimizer = MagicMock(async_save=AsyncMock())
        orchestrator.pattern_analyzer = MagicMock(async_save=AsyncMock())
        orchestrator.optimization_controller = MagicMock(async_save=AsyncMock())

        bad_saver = AsyncMock(side_effect=RuntimeError("boom"))
        orchestrator._forecast_corrections = MagicMock(to_dict=MagicMock())
        orchestrator._async_save_forecast_corrections = bad_saver

        await orchestrator.async_save_all()

    @pytest.mark.asyncio
    async def test_async_save_forecast_corrections_persists(self):
        orchestrator = _make_orchestrator()
        store = MagicMock(async_save=AsyncMock())
        orchestrator._forecast_corrections_store = store
        orchestrator._forecast_corrections = MagicMock(
            to_dict=MagicMock(return_value={"ok": True})
        )

        await orchestrator._async_save_forecast_corrections()

        store.async_save.assert_called_once_with({"ok": True})

    @pytest.mark.asyncio
    async def test_save_component_failure_path(self):
        orchestrator = _make_orchestrator()

        async def _fails():
            raise RuntimeError("boom")

        result = await orchestrator._save_component(_fails, "fail", False)
        assert result is False

    def test_handle_midnight_reset_triggers_pattern_analysis(self):
        orchestrator = _make_orchestrator()

        def _consume(coro, _name=None):
            coro.close()
            return MagicMock()

        orchestrator.hass.async_create_task = MagicMock(side_effect=_consume)
        orchestrator.decision_tracker = MagicMock(async_save=AsyncMock())
        orchestrator.param_optimizer = MagicMock(async_save=AsyncMock())
        orchestrator.pattern_analyzer = MagicMock(async_save=AsyncMock())
        orchestrator._days_since_pattern_analysis = 7

        data = CoordinatorData()
        data.learning_status = "observing"
        orchestrator.handle_midnight_reset(data)

        assert orchestrator.hass.async_create_task.called

    def test_handle_periodic_save_schedules_task(self):
        orchestrator = _make_orchestrator()

        def _consume(coro, _name=None):
            coro.close()
            return MagicMock()

        orchestrator.hass.async_create_task = MagicMock(side_effect=_consume)

        orchestrator.handle_periodic_save()

        orchestrator.hass.async_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_pattern_analysis_updates_status(self):
        orchestrator = _make_orchestrator()
        orchestrator.decision_tracker = MagicMock()
        orchestrator.decision_tracker.get_recent_decisions.return_value = [
            MagicMock()
        ] * 60

        report = MagicMock()
        report.get_summary.return_value = "summary"
        report.biases_detected = [MagicMock()]
        report.data_points_analyzed = 60
        report.biases_detected[0].to_dict.return_value = {"bias": True}

        orchestrator.pattern_analyzer = MagicMock()
        orchestrator.pattern_analyzer.analyze.return_value = report

        orchestrator.param_optimizer = MagicMock()

        data = CoordinatorData()
        data.performance_metrics = PerformanceMetrics()

        await orchestrator._run_pattern_analysis(data)

        assert data.learning_status == "tuning"

    @pytest.mark.asyncio
    async def test_run_pattern_analysis_sets_optimizing(self):
        orchestrator = _make_orchestrator()
        orchestrator.decision_tracker = MagicMock()
        orchestrator.decision_tracker.get_recent_decisions.return_value = [
            MagicMock()
        ] * 60

        report = MagicMock()
        report.get_summary.return_value = "summary"
        report.biases_detected = [MagicMock()]
        report.data_points_analyzed = 120
        report.biases_detected[0].to_dict.return_value = {"bias": True}

        orchestrator.pattern_analyzer = MagicMock()
        orchestrator.pattern_analyzer.analyze.return_value = report
        orchestrator.param_optimizer = MagicMock()

        data = CoordinatorData()
        data.performance_metrics = PerformanceMetrics()

        await orchestrator._run_pattern_analysis(data)

        assert data.learning_status == "optimizing"

    @pytest.mark.asyncio
    async def test_run_pattern_analysis_sets_observing(self):
        orchestrator = _make_orchestrator()
        orchestrator.decision_tracker = MagicMock()
        orchestrator.decision_tracker.get_recent_decisions.return_value = [
            MagicMock()
        ] * 60

        report = MagicMock()
        report.get_summary.return_value = "summary"
        report.biases_detected = []
        report.data_points_analyzed = 20

        orchestrator.pattern_analyzer = MagicMock()
        orchestrator.pattern_analyzer.analyze.return_value = report
        orchestrator.param_optimizer = MagicMock()

        data = CoordinatorData()
        data.performance_metrics = PerformanceMetrics()

        await orchestrator._run_pattern_analysis(data)

        assert data.learning_status == "observing"

    def test_update_medium_tick_sets_adaptive_params(self):
        orchestrator = _make_orchestrator()

        def _consume(coro, _name=None):
            coro.close()
            return MagicMock()

        orchestrator.hass.async_create_task = MagicMock(side_effect=_consume)
        mock_tracker = MagicMock()
        mock_tracker.save_pending = False
        mock_tracker._completed_decisions = [MagicMock()] * 60
        mock_tracker.backfill_outcomes = MagicMock()
        mock_tracker.get_daily_summary.return_value = PerformanceMetrics()
        mock_tracker.get_decision_log.return_value = []
        mock_tracker.get_recent_decisions.return_value = [MagicMock()]
        orchestrator.decision_tracker = mock_tracker

        optimizer = MagicMock()
        optimizer.should_update.return_value = True
        optimizer.optimize.return_value = {"cheap_price_bias": 1.0}
        orchestrator.param_optimizer = optimizer

        controller = MagicMock()
        controller.evaluate.return_value = {"cheap_price_bias": 1.0}
        controller.weights.to_dict.return_value = {}
        controller.get_active_adjustments.return_value = []
        orchestrator.optimization_controller = controller

        evaluator = MagicMock()
        evaluator.evaluate_daily.return_value = MagicMock()
        evaluator.update_performance_metrics.return_value = PerformanceMetrics()
        orchestrator._counterfactual_evaluator = evaluator

        data = CoordinatorData()
        data.performance_metrics = PerformanceMetrics()
        data.weather_anomaly_weight = 0.5

        orchestrator.update_medium_tick(data)

        assert data.adaptive_params is not None

    def test_update_medium_tick_saves_pending(self):
        orchestrator = _make_orchestrator()

        def _consume(coro, _name=None):
            coro.close()
            return MagicMock()

        orchestrator.hass.async_create_task = MagicMock(side_effect=_consume)

        mock_tracker = MagicMock()
        mock_tracker.save_pending = True
        mock_tracker._completed_decisions = []
        mock_tracker.backfill_outcomes = MagicMock()
        mock_tracker.get_daily_summary.return_value = PerformanceMetrics()
        mock_tracker.get_decision_log.return_value = []
        mock_tracker.get_recent_decisions.return_value = []
        mock_tracker.async_save = AsyncMock()
        mock_tracker.clear_save_pending = MagicMock()
        orchestrator.decision_tracker = mock_tracker

        controller = MagicMock()
        controller.evaluate.return_value = {}
        controller.weights.to_dict.return_value = {}
        controller.get_active_adjustments.return_value = ["x"]
        orchestrator.optimization_controller = controller

        data = CoordinatorData()
        data.performance_metrics = PerformanceMetrics()

        orchestrator.update_medium_tick(data)

        assert mock_tracker.clear_save_pending.called


def test_forecast_corrections_property_returns_value():
    orchestrator = _make_orchestrator()
    orchestrator._forecast_corrections = MagicMock()

    assert orchestrator.forecast_corrections is orchestrator._forecast_corrections

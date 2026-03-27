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
from custom_components.localshift.const import (
    CONF_POWER_SIGN_OVERRIDE,
    CONF_TESLEMETRY_BATTERY_POWER,
    CONF_TESLEMETRY_SOC,
    POWER_SIGN_POSITIVE,
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
    monkeypatch.setitem(
        sys.modules,
        "custom_components.localshift.engine.outcomes",
        outcomes_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "custom_components.localshift.engine.parameters",
        params_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "custom_components.localshift.engine.pattern_analyzer",
        pattern_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "custom_components.localshift.engine.optimization_controller",
        opt_module,
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


def test_attach_state_machine_skips_without_tracker():
    orchestrator = _make_orchestrator()
    orchestrator.decision_tracker = None
    state_machine = MagicMock()
    state_machine._decision_tracker = "sentinel"

    orchestrator.attach_state_machine(state_machine)

    assert state_machine._decision_tracker == "sentinel"


@pytest.mark.asyncio
async def test_async_save_forecast_corrections_skips_when_none():
    orchestrator = _make_orchestrator()
    orchestrator._forecast_corrections = None
    orchestrator._forecast_corrections_store = AsyncMock()

    await orchestrator._async_save_forecast_corrections()

    orchestrator._forecast_corrections_store.async_save.assert_not_called()


@pytest.mark.asyncio
async def test_async_invalidate_charge_rate_curves_no_learner():
    orchestrator = _make_orchestrator()
    orchestrator.charge_rate_learner = None

    await orchestrator.async_invalidate_charge_rate_curves()


@pytest.mark.asyncio
async def test_async_invalidate_charge_rate_curves_configures_and_resets():
    orchestrator = _make_orchestrator()
    orchestrator.entry.data = {
        CONF_TESLEMETRY_BATTERY_POWER: "sensor.battery_power",
        CONF_TESLEMETRY_SOC: "sensor.soc",
        CONF_POWER_SIGN_OVERRIDE: POWER_SIGN_POSITIVE,
    }
    learner = MagicMock()
    learner.configure = MagicMock()
    learner.async_invalidate = AsyncMock()
    orchestrator.charge_rate_learner = learner
    orchestrator._last_charge_rate_update = datetime.now(UTC)
    orchestrator._last_charge_rate_attempt = datetime.now(UTC)

    await orchestrator.async_invalidate_charge_rate_curves()

    learner.configure.assert_called_once_with(
        power_entity_id="sensor.battery_power",
        soc_entity_id="sensor.soc",
        power_sign_override=POWER_SIGN_POSITIVE,
    )
    learner.async_invalidate.assert_awaited_once()
    assert orchestrator._last_charge_rate_update is None
    assert orchestrator._last_charge_rate_attempt is None


def test_handle_midnight_reset_schedules_pattern_analysis():
    orchestrator = _make_orchestrator()
    orchestrator.decision_tracker = MagicMock()
    orchestrator.decision_tracker.async_save = AsyncMock()
    orchestrator.param_optimizer = MagicMock()
    orchestrator.param_optimizer.async_save = AsyncMock()
    orchestrator.pattern_analyzer = MagicMock()
    orchestrator.pattern_analyzer.async_save = AsyncMock()
    orchestrator._days_since_pattern_analysis = 6

    def _consume(coro, _name=None):
        if hasattr(coro, "close"):
            coro.close()
        return MagicMock()

    orchestrator.hass.async_create_task = MagicMock(side_effect=_consume)

    data = CoordinatorData()
    data.learning_status = "observing"

    orchestrator.handle_midnight_reset(data)

    assert orchestrator._days_since_pattern_analysis == 0
    assert orchestrator.hass.async_create_task.call_count >= 3


def test_get_pattern_analysis_interval_default():
    orchestrator = _make_orchestrator()

    assert orchestrator._get_pattern_analysis_interval("unknown") == 7


@pytest.mark.asyncio
async def test_run_pattern_analysis_skips_without_analyzer():
    orchestrator = _make_orchestrator()
    orchestrator.pattern_analyzer = None
    orchestrator.decision_tracker = MagicMock()
    orchestrator.decision_tracker.get_recent_decisions = MagicMock()

    await orchestrator._run_pattern_analysis(CoordinatorData())

    orchestrator.decision_tracker.get_recent_decisions.assert_not_called()


@pytest.mark.asyncio
async def test_run_pattern_analysis_skips_when_insufficient_decisions():
    orchestrator = _make_orchestrator()
    orchestrator.pattern_analyzer = MagicMock()
    orchestrator.pattern_analyzer.analyze = MagicMock()
    orchestrator.decision_tracker = MagicMock()
    orchestrator.decision_tracker.get_recent_decisions = MagicMock(return_value=[1])

    await orchestrator._run_pattern_analysis(CoordinatorData())

    orchestrator.pattern_analyzer.analyze.assert_not_called()


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
        orchestrator._last_charge_rate_attempt = now - timedelta(minutes=5)

        data = CoordinatorData()
        orchestrator._schedule_charge_rate_update(data)

        orchestrator.hass.async_create_task.assert_not_called()

    def test_schedule_charge_rate_update_allows_after_backoff(self, monkeypatch):
        orchestrator = _make_orchestrator()
        orchestrator.decision_tracker = MagicMock()
        orchestrator.charge_rate_learner = MagicMock()
        now = datetime(2024, 1, 1)
        monkeypatch.setattr(
            "custom_components.localshift.learning.orchestrator.dt_util.now",
            lambda: now,
        )
        orchestrator._last_charge_rate_attempt = now - timedelta(minutes=10)

        def _consume(coro, _name=None):
            coro.close()
            return MagicMock()

        orchestrator.hass.async_create_task = MagicMock(side_effect=_consume)

        data = CoordinatorData()
        orchestrator._schedule_charge_rate_update(data)

        orchestrator.hass.async_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_orchestrator_updates_charge_rate_curves(self):
        from custom_components.localshift.learning.orchestrator import (
            LearningOrchestrator,
        )

        hass = MagicMock()
        scheduled: list = []

        def _capture_task(coro, _name=None):
            task = asyncio.create_task(coro)
            scheduled.append(task)
            return task

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
        await asyncio.gather(*scheduled)

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
        orchestrator._last_charge_rate_attempt = datetime(2024, 1, 1)

        data = CoordinatorData()
        await orchestrator._async_update_charge_rate(data)

        assert orchestrator._last_charge_rate_update is None
        assert orchestrator._last_charge_rate_attempt is not None

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

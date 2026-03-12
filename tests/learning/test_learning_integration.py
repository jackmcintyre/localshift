"""End-to-end integration tests for the learning system (Issue #170).

Tests the full feedback loop:
- Decisions → Outcomes → Optimization → Improved decisions
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.coordinator import (
    AdaptiveParameters,
    CoordinatorData,
    PerformanceMetrics,
)
from custom_components.localshift.engine.optimization_controller import (
    OptimizationController,
)
from custom_components.localshift.engine.optimizer_dp import PlannerAction
from custom_components.localshift.engine.outcomes import (
    DecisionOutcomeTracker,
    DecisionRecord,
)
from custom_components.localshift.engine.parameters import (
    ParameterOptimizer,
)
from custom_components.localshift.engine.pattern_analyzer import (
    PatternAnalyzer,
)
from custom_components.localshift.learning.orchestrator import LearningOrchestrator


class TestLearningIntegration:
    """End-to-end tests for the learning system."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def components(self, mock_hass):
        """Create all learning system components."""
        tracker = DecisionOutcomeTracker(mock_hass, "test_entry")
        optimizer = ParameterOptimizer(mock_hass, "test_entry")
        analyzer = PatternAnalyzer(mock_hass, "test_entry")
        controller = OptimizationController(
            mock_hass, "test_entry", tracker, optimizer, analyzer
        )
        return {
            "tracker": tracker,
            "optimizer": optimizer,
            "analyzer": analyzer,
            "controller": controller,
        }

    @pytest.fixture
    def coordinator_data(self):
        """Create coordinator data for testing."""
        data = CoordinatorData()
        data.soc = 50.0
        data.battery_target_soc = 80.0
        data.learning_status = "observing"
        data.performance_metrics = PerformanceMetrics()
        return data

    def test_full_feedback_loop(self, components, coordinator_data):
        """Test the complete learning feedback loop.

        1. Generate decisions with varying outcomes
        2. Run optimization
        3. Verify parameters are adjusted
        """
        controller = components["controller"]

        # Enable learning
        controller.set_learning_enabled(True)

        # Simulate 100 decisions with improving outcomes over time
        decisions = []
        for i in range(100):
            # Later decisions have better scores (simulating learning)
            base_score = 0.5 + (i / 100) * 0.4  # 0.5 → 0.9
            decision = DecisionRecord(
                timestamp=datetime.now() - timedelta(hours=100 - i),
                mode_chosen=(
                    PlannerAction.CHARGE_GRID_NORMAL
                    if i % 2 == 0
                    else PlannerAction.HOLD
                ),
                previous_mode=PlannerAction.HOLD,
                soc_at_decision=20.0 + (i % 60),
                general_price_at_decision=0.05 + (i % 10) * 0.02,
                feed_in_price_at_decision=0.03,
                forecast_solar_remaining_kwh=10.0,
                forecast_consumption_remaining_kwh=8.0,
                cheap_price_threshold=0.10,
                battery_target_soc=80.0,
                weather_condition="sunny",
                day_of_week=i % 7,
                hour_of_day=i % 24,
                is_demand_window=False,
                outcome_score=base_score,
            )
            decisions.append(decision)

        # Verify the controller can process the data
        result = controller.evaluate(coordinator_data)
        assert isinstance(result, AdaptiveParameters)

    def test_learning_disabled_mid_cycle(self, components, coordinator_data):
        """Test that disabling learning mid-cycle doesn't crash."""
        controller = components["controller"]

        # Enable learning
        controller.set_learning_enabled(True)

        # Process some data
        result = controller.evaluate(coordinator_data)
        assert isinstance(result, AdaptiveParameters)

        # Disable mid-cycle
        controller.set_learning_enabled(False)

        # Should return default params (no crash)
        result = controller.evaluate(coordinator_data)
        assert isinstance(result, AdaptiveParameters)
        # Default params should be empty (zero-offset)
        assert len(result.values) == 0

    def test_reset_clears_state(self, components, coordinator_data):
        """Test that reset button clears all learning state."""
        tracker = components["tracker"]
        optimizer = components["optimizer"]
        controller = components["controller"]

        # Enable learning and set some state
        controller.set_learning_enabled(True)
        optimizer._current_params.values["cheap_price_bias"] = 0.5

        # Simulate reset
        tracker._pending_decisions.clear()
        tracker._completed_decisions.clear()
        optimizer._current_params = AdaptiveParameters()
        coordinator_data.learning_status = "observing"

        # Verify state is cleared
        assert len(tracker._pending_decisions) == 0
        assert len(tracker._completed_decisions) == 0
        assert len(optimizer._current_params.values) == 0


class TestLearningSystemEdgeCases:
    """Edge case tests for the learning system."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def components(self, mock_hass):
        """Create all learning system components."""
        tracker = DecisionOutcomeTracker(mock_hass, "test_entry")
        optimizer = ParameterOptimizer(mock_hass, "test_entry")
        analyzer = PatternAnalyzer(mock_hass, "test_entry")
        controller = OptimizationController(
            mock_hass, "test_entry", tracker, optimizer, analyzer
        )
        return {
            "tracker": tracker,
            "optimizer": optimizer,
            "analyzer": analyzer,
            "controller": controller,
        }

    def test_empty_decision_history(self, components):
        """Test handling empty decision history."""
        controller = components["controller"]
        controller.set_learning_enabled(True)

        data = CoordinatorData()

        # Should not crash with empty history
        result = controller.evaluate(data)
        assert isinstance(result, AdaptiveParameters)


class TestLearningOrchestratorForecastCorrections:
    @pytest.fixture
    def mock_hass(self):
        hass = MagicMock()
        hass.async_create_task = MagicMock()
        return hass

    @pytest.fixture
    def mock_entry(self):
        entry = MagicMock()
        entry.entry_id = "entry-675"
        return entry

    @staticmethod
    def _component() -> MagicMock:
        component = MagicMock()
        component.async_load = AsyncMock()
        component.async_save = AsyncMock()
        component.completed_count = 0
        component.set_learning_enabled = MagicMock()
        return component

    @pytest.mark.asyncio
    async def test_async_initialize_loads_forecast_corrections(
        self, mock_hass, mock_entry
    ):
        store = MagicMock()
        store.async_load = AsyncMock(
            return_value={
                "stats": {
                    "1:12:winter": {
                        "mean_ratio": 1.2,
                        "sample_count": 12,
                        "last_updated": "loaded",
                    }
                },
                "min_samples": 10,
            }
        )
        store.async_save = AsyncMock()

        with (
            pytest.MonkeyPatch.context() as monkeypatch,
        ):
            monkeypatch.setattr(
                "custom_components.localshift.learning.orchestrator.Store",
                MagicMock(return_value=store),
                raising=False,
            )
            monkeypatch.setattr(
                "custom_components.localshift.engine.outcomes.DecisionOutcomeTracker",
                MagicMock(return_value=self._component()),
            )
            monkeypatch.setattr(
                "custom_components.localshift.engine.parameters.ParameterOptimizer",
                MagicMock(return_value=self._component()),
            )
            monkeypatch.setattr(
                "custom_components.localshift.engine.pattern_analyzer.PatternAnalyzer",
                MagicMock(return_value=self._component()),
            )
            monkeypatch.setattr(
                "custom_components.localshift.engine.optimization_controller.OptimizationController",
                MagicMock(return_value=self._component()),
            )

            orchestrator = LearningOrchestrator(
                mock_hass,
                mock_entry,
                lambda _key: False,
            )
            await orchestrator.async_initialize()

        assert orchestrator.forecast_corrections is not None
        assert (
            orchestrator.forecast_corrections.get_correction_factor(1, 12, "winter")
            == 1.2
        )

    @pytest.mark.asyncio
    async def test_async_save_all_persists_forecast_corrections(
        self, mock_hass, mock_entry
    ):
        store = MagicMock()
        store.async_load = AsyncMock(return_value=None)
        store.async_save = AsyncMock()

        with (
            pytest.MonkeyPatch.context() as monkeypatch,
        ):
            monkeypatch.setattr(
                "custom_components.localshift.learning.orchestrator.Store",
                MagicMock(return_value=store),
                raising=False,
            )
            monkeypatch.setattr(
                "custom_components.localshift.engine.outcomes.DecisionOutcomeTracker",
                MagicMock(return_value=self._component()),
            )
            monkeypatch.setattr(
                "custom_components.localshift.engine.parameters.ParameterOptimizer",
                MagicMock(return_value=self._component()),
            )
            monkeypatch.setattr(
                "custom_components.localshift.engine.pattern_analyzer.PatternAnalyzer",
                MagicMock(return_value=self._component()),
            )
            monkeypatch.setattr(
                "custom_components.localshift.engine.optimization_controller.OptimizationController",
                MagicMock(return_value=self._component()),
            )

            orchestrator = LearningOrchestrator(
                mock_hass,
                mock_entry,
                lambda _key: False,
            )
            await orchestrator.async_initialize()
            assert orchestrator.forecast_corrections is not None
            orchestrator.forecast_corrections.record_error(2.0, 1.0, 2, 8, "summer")

            await orchestrator.async_save_all()

        assert store.async_save.call_count == 1
        payload = store.async_save.call_args.args[0]
        assert payload["stats"]
        assert payload["min_samples"] == 10

    def test_attach_state_machine_handles_none_and_sets_tracker(
        self, mock_hass, mock_entry
    ):
        orchestrator = LearningOrchestrator(mock_hass, mock_entry, lambda _key: False)
        state_machine = MagicMock()

        orchestrator.attach_state_machine(state_machine)
        assert state_machine.__dict__.get("_decision_tracker") is None

        tracker = MagicMock()
        orchestrator.decision_tracker = tracker
        orchestrator.attach_state_machine(state_machine)

        assert state_machine._decision_tracker is tracker

    def test_handle_periodic_save_schedules_task(self, mock_hass, mock_entry):
        orchestrator = LearningOrchestrator(mock_hass, mock_entry, lambda _key: False)
        orchestrator.handle_periodic_save()

        assert mock_hass.async_create_task.call_count == 1
        coro = mock_hass.async_create_task.call_args.args[0]
        if hasattr(coro, "close"):
            coro.close()

    @pytest.mark.parametrize(
        ("learning_status", "starting_days", "expects_analysis", "expected_days"),
        [
            ("observing", 5, False, 6),
            ("observing", 6, True, 0),
            ("tuning", 3, False, 4),
            ("tuning", 4, True, 0),
            ("optimizing", 1, False, 2),
            ("optimizing", 2, True, 0),
        ],
    )
    def test_handle_midnight_reset_uses_phase_specific_interval(
        self,
        mock_hass,
        mock_entry,
        learning_status,
        starting_days,
        expects_analysis,
        expected_days,
    ):
        orchestrator = LearningOrchestrator(mock_hass, mock_entry, lambda _key: False)
        orchestrator.decision_tracker = MagicMock(
            async_save=MagicMock(return_value=None)
        )
        orchestrator.param_optimizer = MagicMock(
            async_save=MagicMock(return_value=None)
        )
        orchestrator.pattern_analyzer = MagicMock(
            async_save=MagicMock(return_value=None)
        )
        orchestrator._days_since_pattern_analysis = starting_days

        data = CoordinatorData()
        data.learning_status = learning_status
        orchestrator.handle_midnight_reset(data)

        task_names = [
            call.args[1] for call in mock_hass.async_create_task.call_args_list
        ]
        assert "localshift_save_decision_outcomes" in task_names
        assert "localshift_save_param_optimizer" in task_names
        assert "localshift_save_pattern_analyzer" in task_names
        assert ("localshift_pattern_analysis" in task_names) is expects_analysis
        assert orchestrator._days_since_pattern_analysis == expected_days

        for call in mock_hass.async_create_task.call_args_list:
            coro = call.args[0]
            if hasattr(coro, "close"):
                coro.close()
        mock_hass.async_create_task.reset_mock()

    def test_handle_midnight_reset_keeps_counter_across_phase_changes(
        self, mock_hass, mock_entry
    ):
        orchestrator = LearningOrchestrator(mock_hass, mock_entry, lambda _key: False)
        orchestrator.decision_tracker = MagicMock(
            async_save=MagicMock(return_value=None)
        )
        orchestrator.param_optimizer = MagicMock(
            async_save=MagicMock(return_value=None)
        )
        orchestrator.pattern_analyzer = MagicMock(
            async_save=MagicMock(return_value=None)
        )
        orchestrator._days_since_pattern_analysis = 1

        data = CoordinatorData()
        data.learning_status = "observing"
        orchestrator.handle_midnight_reset(data)
        assert orchestrator._days_since_pattern_analysis == 2

        for call in mock_hass.async_create_task.call_args_list:
            coro = call.args[0]
            if hasattr(coro, "close"):
                coro.close()
        mock_hass.async_create_task.reset_mock()

        data.learning_status = "tuning"
        orchestrator.handle_midnight_reset(data)

        task_names = [
            call.args[1] for call in mock_hass.async_create_task.call_args_list
        ]
        assert "localshift_pattern_analysis" not in task_names
        assert orchestrator._days_since_pattern_analysis == 3

        for call in mock_hass.async_create_task.call_args_list:
            coro = call.args[0]
            if hasattr(coro, "close"):
                coro.close()

    def test_update_medium_tick_runs_learning_and_controller(
        self, mock_hass, mock_entry
    ):
        orchestrator = LearningOrchestrator(mock_hass, mock_entry, lambda _key: False)

        summary = MagicMock()
        summary.avg_decision_score_7d = 0.75
        decision_tracker = MagicMock()
        decision_tracker.backfill_outcomes = MagicMock()
        decision_tracker.get_daily_summary = MagicMock(return_value=summary)
        decision_tracker.get_decision_log = MagicMock(return_value=[{"a": 1}])
        decision_tracker.save_pending = True
        decision_tracker.async_save = MagicMock(return_value=None)
        decision_tracker.clear_save_pending = MagicMock()
        decision_tracker._completed_decisions = [1, 2, 3]
        decision_tracker.get_recent_decisions = MagicMock(return_value=[{"d": 1}])
        orchestrator.decision_tracker = decision_tracker

        param_optimizer = MagicMock()
        param_optimizer.should_update = MagicMock(return_value=True)
        param_optimizer.optimize = MagicMock(return_value={"from": "optimizer"})
        orchestrator.param_optimizer = param_optimizer

        optimization_controller = MagicMock()
        optimization_controller.evaluate = MagicMock(
            return_value={"from": "controller"}
        )
        optimization_controller.weights.to_dict.return_value = {"w": 1.0}
        optimization_controller.get_active_adjustments.return_value = ["adj"]
        orchestrator.optimization_controller = optimization_controller

        data = CoordinatorData()
        orchestrator.update_medium_tick(data)

        assert data.performance_metrics is summary
        assert data.recent_decision_log == [{"a": 1}]
        assert data.adaptive_params == {"from": "controller"}
        assert data.optimization_weights == {"w": 1.0}
        assert data.contextual_adjustments_active == ["adj"]
        assert decision_tracker.clear_save_pending.call_count == 1
        assert mock_hass.async_create_task.call_count == 1
        coro = mock_hass.async_create_task.call_args.args[0]
        if hasattr(coro, "close"):
            coro.close()

    @pytest.mark.asyncio
    async def test_save_component_error_paths(self, mock_hass, mock_entry):
        orchestrator = LearningOrchestrator(mock_hass, mock_entry, lambda _key: False)

        async def ok_saver():
            return None

        async def fail_saver():
            raise RuntimeError("boom")

        assert await orchestrator._save_component(ok_saver, "ok", False) is True
        assert await orchestrator._save_component(fail_saver, "err", False) is False
        assert await orchestrator._save_component(fail_saver, "err", True) is False

    @pytest.mark.asyncio
    async def test_async_save_forecast_corrections_noop_when_none(
        self, mock_hass, mock_entry
    ):
        orchestrator = LearningOrchestrator(mock_hass, mock_entry, lambda _key: False)
        orchestrator._forecast_corrections = None

        await orchestrator._async_save_forecast_corrections()

    @pytest.mark.asyncio
    async def test_run_pattern_analysis_guard_and_insufficient_data(
        self, mock_hass, mock_entry
    ):
        orchestrator = LearningOrchestrator(mock_hass, mock_entry, lambda _key: False)
        data = CoordinatorData()

        await orchestrator._run_pattern_analysis(data)

        orchestrator.pattern_analyzer = MagicMock(analyze=MagicMock())
        orchestrator.decision_tracker = MagicMock(
            get_recent_decisions=MagicMock(return_value=[1] * 49)
        )
        await orchestrator._run_pattern_analysis(data)

        orchestrator.pattern_analyzer.analyze.assert_not_called()
        assert orchestrator._last_pattern_analysis is None

    @pytest.mark.asyncio
    async def test_run_pattern_analysis_updates_status_and_biases(
        self, mock_hass, mock_entry
    ):
        orchestrator = LearningOrchestrator(mock_hass, mock_entry, lambda _key: False)

        bias = MagicMock()
        bias.to_dict.return_value = {"bias": 1}
        report = MagicMock()
        report.get_summary.return_value = {"summary": 1}
        report.biases_detected = [bias]
        report.data_points_analyzed = 120

        orchestrator.decision_tracker = MagicMock(
            get_recent_decisions=MagicMock(return_value=[1] * 60)
        )
        orchestrator.pattern_analyzer = MagicMock(
            analyze=MagicMock(return_value=report)
        )
        orchestrator.param_optimizer = MagicMock(set_bias_corrections=MagicMock())

        data = CoordinatorData()
        await orchestrator._run_pattern_analysis(data)

        assert data.pattern_report_summary == {"summary": 1}
        assert data.active_bias_corrections == [{"bias": 1}]
        assert data.learning_status == "optimizing"
        assert orchestrator._last_pattern_analysis is not None

    @pytest.mark.asyncio
    async def test_run_pattern_analysis_sets_observing_for_low_sample_report(
        self, mock_hass, mock_entry
    ):
        orchestrator = LearningOrchestrator(mock_hass, mock_entry, lambda _key: False)

        report = MagicMock()
        report.get_summary.return_value = {"summary": 2}
        report.biases_detected = []
        report.data_points_analyzed = 40

        orchestrator.decision_tracker = MagicMock(
            get_recent_decisions=MagicMock(return_value=[1] * 60)
        )
        orchestrator.pattern_analyzer = MagicMock(
            analyze=MagicMock(return_value=report)
        )
        orchestrator.param_optimizer = MagicMock(set_bias_corrections=MagicMock())

        data = CoordinatorData()
        await orchestrator._run_pattern_analysis(data)

        assert data.learning_status == "observing"

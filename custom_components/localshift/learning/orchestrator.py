"""Learning subsystem orchestration and persistence."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import SWITCH_ENABLE_LEARNING
from ..engine.counterfactual import CounterfactualEvaluator
from ..forecast.corrections import ForecastCorrectionProvider

_LOGGER = logging.getLogger(__name__)


class LearningOrchestrator:
    """Manage learning initialization, persistence, and periodic updates."""

    _PATTERN_ANALYSIS_INTERVALS = {
        "observing": 7,
        "tuning": 5,
        "optimizing": 3,
    }

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        get_switch_state,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._get_switch_state = get_switch_state
        self._entry_id = entry.entry_id

        self.decision_tracker: Any | None = None
        self.param_optimizer: Any | None = None
        self.pattern_analyzer: Any | None = None
        self.optimization_controller: Any | None = None

        # Throttle for pattern-analysis scheduling. In-memory only: prevents
        # double-scheduling while the task runs and re-attempting every 5 min on
        # a fresh install with too few decisions. Due-ness itself is driven by
        # the pattern analyzer's persisted last_analysis_time, so this resetting
        # to None on restart is harmless.
        self._next_analysis_attempt: datetime | None = None
        self._forecast_corrections: ForecastCorrectionProvider | None = None
        self._forecast_corrections_store = Store(
            hass,
            version=1,
            key=f"localshift.forecast_corrections.{self._entry_id}",
        )
        self._counterfactual_evaluator: CounterfactualEvaluator | None = None

    async def async_initialize(self) -> None:
        """Initialize learning components and load persisted state."""
        from ..engine.optimization_controller import (
            OptimizationController,
        )
        from ..engine.outcomes import (
            DecisionOutcomeTracker,
        )
        from ..engine.parameters import ParameterOptimizer
        from ..engine.pattern_analyzer import PatternAnalyzer

        decision_tracker = DecisionOutcomeTracker(self.hass, self.entry.entry_id)
        await decision_tracker.async_load()
        self.decision_tracker = decision_tracker

        param_optimizer = ParameterOptimizer(self.hass, self.entry.entry_id)
        await param_optimizer.async_load()
        self.param_optimizer = param_optimizer

        pattern_analyzer = PatternAnalyzer(self.hass, self.entry.entry_id)
        await pattern_analyzer.async_load()
        self.pattern_analyzer = pattern_analyzer

        optimization_controller = OptimizationController(
            self.hass,
            self.entry.entry_id,
            decision_tracker,
            param_optimizer,
            pattern_analyzer,
        )
        await optimization_controller.async_load()
        self.optimization_controller = optimization_controller

        learning_enabled = self._get_switch_state(SWITCH_ENABLE_LEARNING)
        optimization_controller.set_learning_enabled(learning_enabled)

        self._forecast_corrections = ForecastCorrectionProvider()
        stored_corrections = await self._forecast_corrections_store.async_load()
        if stored_corrections:
            self._forecast_corrections = ForecastCorrectionProvider.from_dict(
                stored_corrections
            )

        self._counterfactual_evaluator = CounterfactualEvaluator()

    def attach_state_machine(self, state_machine) -> None:
        """Wire decision tracker into state machine."""
        if state_machine is None or self.decision_tracker is None:
            return
        state_machine._decision_tracker = self.decision_tracker

    async def async_save_all(self) -> None:
        """Save all learning system data to storage."""
        saved_components = []

        operations: list[tuple] = []
        if self.decision_tracker is not None:
            operations.append((
                self.decision_tracker.async_save,
                f"decisions:{self.decision_tracker.completed_count}",
                "Failed to save decision tracker",
                False,
            ))
        if self.param_optimizer is not None:
            operations.append((
                self.param_optimizer.async_save,
                "param_optimizer",
                "Failed to save parameter optimizer",
                False,
            ))
        if self.pattern_analyzer is not None:
            operations.append((
                self.pattern_analyzer.async_save,
                "pattern_analyzer",
                "Failed to save pattern analyzer",
                False,
            ))
        if self.optimization_controller is not None:
            operations.append((
                self.optimization_controller.async_save,
                "optimization_controller",
                "Failed to save optimization controller",
                False,
            ))
        if self._forecast_corrections is not None:
            operations.append((
                self._async_save_forecast_corrections,
                "forecast_corrections",
                "Failed to save forecast corrections",
                True,
            ))

        for saver, label, error_message, use_exception in operations:
            if await self._save_component(saver, error_message, use_exception):
                saved_components.append(label)

        if saved_components:
            _LOGGER.info("Learning data saved: %s", ", ".join(saved_components))

    async def _save_component(
        self,
        saver,
        error_message: str,
        use_exception: bool,
    ) -> bool:
        try:
            await saver()
            return True
        except Exception as err:
            if use_exception:
                _LOGGER.exception(error_message)
            else:
                _LOGGER.error("%s: %s", error_message, err)
            return False

    async def _async_save_forecast_corrections(self) -> None:
        if self._forecast_corrections is None:
            return
        await self._forecast_corrections_store.async_save(
            self._forecast_corrections.to_dict()
        )

    def handle_midnight_reset(self, data) -> None:
        """Handle learning-specific midnight reset tasks."""
        if self.decision_tracker is not None:
            self.hass.async_create_task(
                self.decision_tracker.async_save(),
                "localshift_save_decision_outcomes",
            )

        if self.param_optimizer is not None:
            self.hass.async_create_task(
                self.param_optimizer.async_save(),
                "localshift_save_param_optimizer",
            )

        # Pattern analysis is no longer triggered here. The old in-memory day
        # counter reset on every restart (the system restarts every 1-2 days),
        # so it never reached the 7-day interval and analysis never ran. It is
        # now scheduled from update_medium_tick based on the persisted
        # last_analysis_time (see maybe_run_pattern_analysis).
        if self.pattern_analyzer is not None:
            self.hass.async_create_task(
                self.pattern_analyzer.async_save(),
                "localshift_save_pattern_analyzer",
            )

    def _get_pattern_analysis_interval(self, learning_status: str) -> int:
        return self._PATTERN_ANALYSIS_INTERVALS.get(learning_status, 7)

    @staticmethod
    def _derive_learning_status(decision_count: int) -> str:
        """Derive learning status from how many decisions are available.

        Equivalent to the thresholds applied inside _run_pattern_analysis:
        report.data_points_analyzed == len(decisions) and the same 720h decision
        window is used, so deriving from the count at startup is identical.
        """
        if decision_count >= 100:
            return "optimizing"
        if decision_count >= 50:
            return "tuning"
        return "observing"

    def maybe_run_pattern_analysis(self, data) -> None:
        """Schedule pattern analysis when it is due, based on persisted state.

        Due when the analyzer has never run, or when more than the
        status-dependent interval (7/5/3 days) has elapsed since the persisted
        last_analysis_time. A short in-memory throttle prevents double-scheduling
        while the task runs and stops a too-few-decisions install from retrying
        every 5 minutes.
        """
        if self.pattern_analyzer is None or self.decision_tracker is None:
            return

        now = dt_util.now()
        if (
            self._next_analysis_attempt is not None
            and now < self._next_analysis_attempt
        ):
            return

        last = self.pattern_analyzer.last_analysis_time
        interval_days = self._get_pattern_analysis_interval(
            getattr(data, "learning_status", "observing")
        )
        due = last is None or (now - last) >= timedelta(days=interval_days)
        if not due:
            return

        # Reserve a 1h window so the running task is not re-scheduled. If the run
        # skips for too few decisions it pushes this out to 24h itself.
        self._next_analysis_attempt = now + timedelta(hours=1)
        self.hass.async_create_task(
            self._run_pattern_analysis(data),
            "localshift_pattern_analysis",
        )

    def restore_runtime_state(self, data) -> None:
        """Re-derive ephemeral learning state after a restart.

        learning_status, the last pattern report summary, and active bias
        corrections are not persisted as such — but everything needed to
        reconstruct them is. This re-derives them from the persisted decision
        deque and pattern report so sensors render correctly and bias
        corrections (which only lived in memory) survive a restart.
        """
        if self.decision_tracker is not None:
            recent = self.decision_tracker.get_recent_decisions(hours=720)
            data.learning_status = self._derive_learning_status(len(recent))

        if self.pattern_analyzer is None:
            return

        report = self.pattern_analyzer.get_last_report()
        if report is not None:
            data.pattern_report_summary = report.get_summary()
            data.active_bias_corrections = [
                bc.to_dict() for bc in report.biases_detected
            ]
            if self.param_optimizer is not None and report.biases_detected:
                self.param_optimizer.set_bias_corrections(report.biases_detected)

        last = self.pattern_analyzer.last_analysis_time
        data.last_pattern_analysis = last.isoformat() if last is not None else None

    def update_medium_tick(self, data) -> None:
        """Run learning and monitoring tasks on medium tick."""
        if self.decision_tracker is not None:
            self.decision_tracker.backfill_outcomes(data)

            data.performance_metrics = self.decision_tracker.get_daily_summary(data)
            data.recent_decision_log = self.decision_tracker.get_decision_log(limit=20)

            if self.decision_tracker.save_pending:
                self.hass.async_create_task(
                    self.decision_tracker.async_save(),
                    "localshift_save_decision_outcomes",
                )
                self.decision_tracker.clear_save_pending()

            if self.param_optimizer is not None:
                completed_count = len(self.decision_tracker._completed_decisions)
                if self.param_optimizer.should_update(completed_count):
                    decisions = self.decision_tracker.get_recent_decisions(hours=168)
                    current_7d_score = data.performance_metrics.avg_decision_score_7d
                    data.adaptive_params = self.param_optimizer.optimize(
                        decisions,
                        current_7d_score,
                        weather_weight=data.weather_anomaly_weight,
                    )

            if self._counterfactual_evaluator is not None:
                decisions = self.decision_tracker.get_recent_decisions(hours=24)
                daily_result = self._counterfactual_evaluator.evaluate_daily(
                    decisions, data
                )
                data.performance_metrics = (
                    self._counterfactual_evaluator.update_performance_metrics(
                        data.performance_metrics, daily_result
                    )
                )

        if self.optimization_controller is not None:
            data.adaptive_params = self.optimization_controller.evaluate(data)
            data.optimization_weights = self.optimization_controller.weights.to_dict()
            active_adjustments = self.optimization_controller.get_active_adjustments()
            data.contextual_adjustments_active = active_adjustments
            if active_adjustments:
                _LOGGER.debug(
                    "Contextual overrides applied: %d adjustments active",
                    len(active_adjustments),
                )

        # Schedule pattern analysis if it is due. handle_medium_tick skips during
        # the startup grace period, so the first post-grace tick (~5-35 min after
        # a restart) runs any overdue analysis — startup catch-up for free.
        self.maybe_run_pattern_analysis(data)

    async def _run_pattern_analysis(self, data) -> None:
        """Run weekly pattern analysis to generate bias corrections."""
        if self.pattern_analyzer is None or self.decision_tracker is None:
            return

        decisions = self.decision_tracker.get_recent_decisions(hours=720)

        if len(decisions) < 50:
            _LOGGER.info(
                "Pattern analysis skipped: only %d decisions (need 50+)",
                len(decisions),
            )
            # Back off so a fresh install does not retry every medium tick.
            self._next_analysis_attempt = dt_util.now() + timedelta(hours=24)
            return

        report = self.pattern_analyzer.analyze(decisions)
        # Persist the new last_analysis_time immediately so a crash before the
        # next 5-min periodic save does not lose the "analysis ran" timestamp.
        await self.pattern_analyzer.async_save()

        data.pattern_report_summary = report.get_summary()
        data.active_bias_corrections = [bc.to_dict() for bc in report.biases_detected]
        data.learning_status = self._derive_learning_status(report.data_points_analyzed)

        if self.param_optimizer is not None and report.biases_detected:
            self.param_optimizer.set_bias_corrections(report.biases_detected)

        last = self.pattern_analyzer.last_analysis_time
        data.last_pattern_analysis = last.isoformat() if last is not None else None
        _LOGGER.info(
            "Pattern analysis complete: %d decisions analyzed, %d biases detected",
            report.data_points_analyzed,
            len(report.biases_detected),
        )

        _LOGGER.info(
            "Pattern analysis complete: %d decisions analyzed, %d biases detected",
            report.data_points_analyzed,
            len(report.biases_detected),
        )

    @property
    def forecast_corrections(self) -> ForecastCorrectionProvider | None:
        return self._forecast_corrections

"""Learning subsystem orchestration and persistence."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from ..const import SWITCH_ENABLE_LEARNING

if TYPE_CHECKING:
    from .computation_engine_lib.decision_outcome_tracker import DecisionOutcomeTracker
    from .computation_engine_lib.optimization_controller import OptimizationController
    from .computation_engine_lib.parameter_optimizer import ParameterOptimizer
    from .computation_engine_lib.pattern_analyzer import PatternAnalyzer

_LOGGER = logging.getLogger(__name__)


class LearningOrchestrator:
    """Manage learning initialization, persistence, and periodic updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        get_switch_state,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._get_switch_state = get_switch_state

        self.decision_tracker: DecisionOutcomeTracker | None = None
        self.param_optimizer: ParameterOptimizer | None = None
        self.pattern_analyzer: PatternAnalyzer | None = None
        self.optimization_controller: OptimizationController | None = None

        self._last_pattern_analysis: datetime | None = None
        self._days_since_pattern_analysis = 0

    async def async_initialize(self) -> None:
        """Initialize learning components and load persisted state."""
        from .computation_engine_lib.decision_outcome_tracker import (
            DecisionOutcomeTracker,
        )
        from .computation_engine_lib.optimization_controller import (
            OptimizationController,
        )
        from .computation_engine_lib.parameter_optimizer import ParameterOptimizer
        from .computation_engine_lib.pattern_analyzer import PatternAnalyzer

        self.decision_tracker = DecisionOutcomeTracker(self.hass, self.entry.entry_id)
        await self.decision_tracker.async_load()

        self.param_optimizer = ParameterOptimizer(self.hass, self.entry.entry_id)
        await self.param_optimizer.async_load()

        self.pattern_analyzer = PatternAnalyzer(self.hass, self.entry.entry_id)
        await self.pattern_analyzer.async_load()

        if (
            self.decision_tracker is not None
            and self.param_optimizer is not None
            and self.pattern_analyzer is not None
        ):
            self.optimization_controller = OptimizationController(
                self.hass,
                self.entry.entry_id,
                self.decision_tracker,
                self.param_optimizer,
                self.pattern_analyzer,
            )
            await self.optimization_controller.async_load()

            learning_enabled = self._get_switch_state(SWITCH_ENABLE_LEARNING)
            self.optimization_controller.set_learning_enabled(learning_enabled)

    def attach_state_machine(self, state_machine) -> None:
        """Wire decision tracker into state machine."""
        if state_machine is None or self.decision_tracker is None:
            return
        state_machine._decision_tracker = self.decision_tracker

    async def async_save_all(self) -> None:
        """Save all learning system data to storage."""
        saved_components = []

        if self.decision_tracker is not None:
            try:
                await self.decision_tracker.async_save()
                saved_components.append(
                    f"decisions:{self.decision_tracker.completed_count}"
                )
            except Exception as e:
                _LOGGER.error("Failed to save decision tracker: %s", e)

        if self.param_optimizer is not None:
            try:
                await self.param_optimizer.async_save()
                saved_components.append("param_optimizer")
            except Exception as e:
                _LOGGER.error("Failed to save parameter optimizer: %s", e)

        if self.pattern_analyzer is not None:
            try:
                await self.pattern_analyzer.async_save()
                saved_components.append("pattern_analyzer")
            except Exception as e:
                _LOGGER.error("Failed to save pattern analyzer: %s", e)

        if self.optimization_controller is not None:
            try:
                await self.optimization_controller.async_save()
                saved_components.append("optimization_controller")
            except Exception as e:
                _LOGGER.error("Failed to save optimization controller: %s", e)

        if saved_components:
            _LOGGER.info("Learning data saved: %s", ", ".join(saved_components))

    def handle_periodic_save(self) -> None:
        """Schedule a periodic save of learning data."""
        self.hass.async_create_task(
            self.async_save_all(),
            "localshift_periodic_learning_save",
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

        self._days_since_pattern_analysis += 1
        if (
            self.pattern_analyzer is not None
            and self.decision_tracker is not None
            and self._days_since_pattern_analysis >= 7
        ):
            self._days_since_pattern_analysis = 0
            self.hass.async_create_task(
                self._run_pattern_analysis(data),
                "localshift_pattern_analysis",
            )

        if self.pattern_analyzer is not None:
            self.hass.async_create_task(
                self.pattern_analyzer.async_save(),
                "localshift_save_pattern_analyzer",
            )

    def update_medium_tick(self, data) -> None:
        """Run learning and monitoring tasks on medium tick."""
        if self.decision_tracker is not None:
            self.decision_tracker.backfill_outcomes(data)

            data.performance_metrics = self.decision_tracker.get_daily_summary()
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
            return

        report = self.pattern_analyzer.analyze(decisions)

        data.pattern_report_summary = report.get_summary()
        data.active_bias_corrections = [bc.to_dict() for bc in report.biases_detected]

        total_samples = report.data_points_analyzed
        if total_samples >= 100:
            data.learning_status = "optimizing"
        elif total_samples >= 50:
            data.learning_status = "tuning"
        else:
            data.learning_status = "observing"

        if self.param_optimizer is not None and report.biases_detected:
            self.param_optimizer.set_bias_corrections(report.biases_detected)
            _LOGGER.info(
                "Pattern analysis complete: %d bias corrections applied",
                len(report.biases_detected),
            )

        self._last_pattern_analysis = datetime.now()

        _LOGGER.info(
            "Pattern analysis complete: %d decisions analyzed, %d biases detected",
            report.data_points_analyzed,
            len(report.biases_detected),
        )

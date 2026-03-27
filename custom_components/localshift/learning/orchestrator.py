"""Learning subsystem orchestration and persistence."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_TESLEMETRY_BATTERY_POWER,
    CONF_TESLEMETRY_SOC,
    SWITCH_ENABLE_LEARNING,
)
from ..engine.counterfactual import CounterfactualEvaluator
from ..forecast.corrections import ForecastCorrectionProvider
from .charge_rate import ChargeRateLearner

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

        self._last_pattern_analysis: datetime | None = None
        self._days_since_pattern_analysis = 0
        self._last_charge_rate_update: datetime | None = None
        self._forecast_corrections: ForecastCorrectionProvider | None = None
        self._forecast_corrections_store = Store(
            hass,
            version=1,
            key=f"localshift.forecast_corrections.{self._entry_id}",
        )
        self._counterfactual_evaluator: CounterfactualEvaluator | None = None
        self._last_charge_rate_attempt: datetime | None = None
        self.charge_rate_learner: ChargeRateLearner | None = None

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

        power_entity_id = self.entry.options.get(
            CONF_TESLEMETRY_BATTERY_POWER, ""
        ) or self.entry.data.get(CONF_TESLEMETRY_BATTERY_POWER, "")
        soc_entity_id = self.entry.options.get(
            CONF_TESLEMETRY_SOC, ""
        ) or self.entry.data.get(CONF_TESLEMETRY_SOC, "")
        charge_rate_learner = ChargeRateLearner(
            self.hass,
            self.entry.entry_id,
            power_entity_id=power_entity_id,
            soc_entity_id=soc_entity_id,
        )
        await charge_rate_learner.async_load()
        self.charge_rate_learner = charge_rate_learner

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
        analysis_interval = self._get_pattern_analysis_interval(
            getattr(data, "learning_status", "observing")
        )
        if (
            self.pattern_analyzer is not None
            and self.decision_tracker is not None
            and self._days_since_pattern_analysis >= analysis_interval
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

    def _get_pattern_analysis_interval(self, learning_status: str) -> int:
        return self._PATTERN_ANALYSIS_INTERVALS.get(learning_status, 7)

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

        self._schedule_charge_rate_update(data)

    def _schedule_charge_rate_update(self, data) -> None:
        if self.charge_rate_learner is None or self.decision_tracker is None:
            return
        now = dt_util.now() or datetime.now()
        if self._last_charge_rate_update is not None:
            if now - self._last_charge_rate_update < timedelta(days=1):
                return
        if self._last_charge_rate_attempt is not None:
            if now - self._last_charge_rate_attempt < timedelta(hours=1):
                return
        self._last_charge_rate_attempt = now
        self.hass.async_create_task(
            self._async_update_charge_rate(data),
            "localshift_charge_rate_update",
        )

    async def _async_update_charge_rate(self, data) -> None:
        if self.charge_rate_learner is None or self.decision_tracker is None:
            return

        decisions = self.decision_tracker.get_recent_decisions(hours=24 * 30)
        (
            power_history,
            soc_history,
        ) = await self.charge_rate_learner.async_fetch_history()
        if not power_history or not soc_history:
            self._last_charge_rate_attempt = None
            return

        updated = self.charge_rate_learner.update_from_history(
            power_history,
            soc_history,
            decisions,
        )
        if not updated:
            self._last_charge_rate_attempt = None
            return

        curve_normal = self.charge_rate_learner.get_curve("normal")
        curve_boost = self.charge_rate_learner.get_curve("boost")
        if curve_normal is None and curve_boost is None:
            self._last_charge_rate_attempt = None
            return

        data.charge_rate_curves = {
            "normal": curve_normal,
            "boost": curve_boost,
        }
        data.charge_rate_diagnostics = self.charge_rate_learner.diagnostics
        data.learning_enabled = self._get_switch_state(SWITCH_ENABLE_LEARNING)

        self._last_charge_rate_update = dt_util.now() or datetime.now()

        self.hass.async_create_task(
            self.charge_rate_learner.async_save(),
            "localshift_save_charge_rate_curves",
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

    @property
    def forecast_corrections(self) -> ForecastCorrectionProvider | None:
        return self._forecast_corrections

"""Decision telemetry and forecast-correction persistence.

Replaces ``LearningOrchestrator``, which coordinated the parameter-learning
layer (Thompson-sampling parameter optimizer, pattern analyzer, contextual
controller, counterfactual evaluator). That layer was retired: over 75 days it
moved its own quality metric by +0.32 points against 3.0 points of daily noise,
while its contextual adjustments compounded tick-over-tick into a standing
+5 c/kWh inflation of the grid-charge price gate that nothing had asked for.
A ten-day offline replay confirmed removing its offsets changes planned
demand-window entry by at most 0.31pp — see simulations/replay/README.md.

What survives, and why:

- **Decision records.** Modes chosen, the conditions at the time, and the
  measured outcome. Nothing consumes them to change behaviour; they are kept
  because they are the only durable asset the retired layer produced, and any
  future attempt at learning would need exactly this history to start from.
- **Forecast corrections.** ``ForecastCorrectionProvider`` feeds the load
  forecaster (forecast/load.py) and is independent of parameter learning; it
  was only ever stored here because the orchestrator happened to own the Store.

``learning_status`` is still derived and published, but it now reports only how
much telemetry has accumulated, not a learning stage.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ..forecast.corrections import ForecastCorrectionProvider

_LOGGER = logging.getLogger(__name__)


class DecisionTelemetry:
    """Own the decision-outcome record set and the forecast corrections store."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._entry_id = entry.entry_id

        self.decision_tracker: Any | None = None
        self._forecast_corrections: ForecastCorrectionProvider | None = None
        self._forecast_corrections_store = Store(
            hass,
            version=1,
            key=f"localshift.forecast_corrections.{self._entry_id}",
        )

    async def async_initialize(self) -> None:
        """Load persisted decision records and forecast corrections."""
        from ..engine.outcomes import DecisionOutcomeTracker

        tracker = DecisionOutcomeTracker(self.hass, self._entry_id)
        await tracker.async_load()
        self.decision_tracker = tracker

        self._forecast_corrections = ForecastCorrectionProvider()
        stored = await self._forecast_corrections_store.async_load()
        if stored:
            self._forecast_corrections = ForecastCorrectionProvider.from_dict(stored)

    def attach_state_machine(self, state_machine) -> None:
        """Wire the decision tracker into the state machine."""
        if state_machine is None or self.decision_tracker is None:
            return
        state_machine._decision_tracker = self.decision_tracker

    def restore_runtime_state(self, data) -> None:
        """Re-derive the published status from the persisted record set."""
        if self.decision_tracker is None:
            return
        recent = self.decision_tracker.get_recent_decisions(hours=720)
        data.learning_status = self._derive_learning_status(len(recent))

    def update_medium_tick(self, data) -> None:
        """Score completed decisions and refresh the published telemetry."""
        if self.decision_tracker is None:
            return

        self.decision_tracker.backfill_outcomes(data)
        data.performance_metrics = self.decision_tracker.get_daily_summary(data)
        data.recent_decision_log = self.decision_tracker.get_decision_log(limit=20)

        if self.decision_tracker.save_pending:
            self.hass.async_create_task(
                self.decision_tracker.async_save(),
                "localshift_save_decision_outcomes",
            )
            self.decision_tracker.clear_save_pending()

    def handle_midnight_reset(self, data) -> None:
        """Flush the record set at the day boundary."""
        _ = data
        if self.decision_tracker is not None:
            self.hass.async_create_task(
                self.decision_tracker.async_save(),
                "localshift_save_decision_outcomes",
            )

    async def async_save_all(self) -> None:
        """Persist the record set and the forecast corrections."""
        saved: list[str] = []

        if self.decision_tracker is not None:
            try:
                await self.decision_tracker.async_save()
                saved.append(f"decisions:{self.decision_tracker.completed_count}")
            except Exception as err:  # noqa: BLE001 - persistence must not break the tick
                _LOGGER.error("Failed to save decision tracker: %s", err)

        if self._forecast_corrections is not None:
            try:
                await self._forecast_corrections_store.async_save(
                    self._forecast_corrections.to_dict()
                )
                saved.append("forecast_corrections")
            except Exception:
                _LOGGER.exception("Failed to save forecast corrections")

        if saved:
            _LOGGER.info("Telemetry saved: %s", ", ".join(saved))

    @staticmethod
    def _derive_learning_status(decision_count: int) -> str:
        """Report how much telemetry has accumulated.

        Thresholds retained from the retired learning layer so the sensor's
        values stay stable for anyone whose dashboard or automation reads them.
        """
        if decision_count >= 100:
            return "optimizing"
        if decision_count >= 50:
            return "tuning"
        return "observing"

    @property
    def forecast_corrections(self) -> ForecastCorrectionProvider | None:
        return self._forecast_corrections

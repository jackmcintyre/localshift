"""Periodic task scheduling for LocalShift coordinator.

Responsibilities:
- FAST tick (1 min): state machine evaluation, automation readiness
- MEDIUM tick (5 min): entity health, learning tasks, load refresh
- SLOW tick (30 min): weather forecast, forecast accuracy
- Daily events: midnight reset, daily summary
- Solar backfill tracking
- Cost accumulation
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.core import Event, callback

if TYPE_CHECKING:  # pragma: no cover
    from .coordinator import LocalShiftCoordinator

_LOGGER = logging.getLogger(__name__)


class TickScheduler:
    """Manages periodic task execution for coordinator."""

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
    ) -> None:
        """Initialize tick scheduler.

        Args:
            coordinator: Parent coordinator instance
        """
        self._coordinator = coordinator

    @callback
    def handle_state_change(self, _event: Event) -> None:
        """Handle a state change from a monitored entity."""
        if self._coordinator._evaluation_dispatcher is None:
            return

        self._coordinator._evaluation_dispatcher.on_state_change(_event)

    @callback
    def handle_periodic_tick(self, now: datetime) -> None:
        """Handle the 1-minute periodic re-evaluation.

        DEPRECATED: This method is kept for backward compatibility.
        New tiered handlers are used instead.
        """
        # Delegate to fast tick for backward compatibility
        self.handle_fast_tick(now)

    @callback
    def handle_fast_tick(self, now: datetime) -> None:
        """Handle FAST tier periodic tasks (1 minute).

        Checks automation ready state and triggers immediate optimizer evaluation
        when it transitions from not-ready to ready (Issue #478).

        Dispatches to state machine for mode transition evaluation regardless of
        price changes (Issue #622 - legacy price gate removed).
        """
        # Read raw entity values now — needed for cost accumulation
        if self._coordinator._entity_monitor is not None:
            self._coordinator._entity_monitor.read_all_external_state()

        # Cost accumulation uses the raw state we just read (sync, no lock needed)
        self._accumulate_costs()

        # Skip evaluation dispatch during startup grace period
        if self._is_in_startup_grace():
            _LOGGER.debug(
                "Skipping state machine evaluation during startup grace period"
            )
            return

        # Issue #478: Check if automation just became ready during startup
        # Triggers immediate evaluation when transitioning from not-ready to ready
        if self._coordinator._evaluation_dispatcher is not None:
            self._coordinator._evaluation_dispatcher.maybe_trigger_on_startup_ready(
                lambda: (
                    self._coordinator.data.automation_ready
                    if self._coordinator.data
                    else False
                )
            )

        # Issue #622: Always dispatch to StateMachine
        # StateMachine gates mode transitions based on price fingerprint
        # This ensures optimizer runs every minute for plan updates
        if self._coordinator._evaluation_dispatcher is not None:
            self._coordinator._evaluation_dispatcher.on_fast_tick(now)

    @callback
    def handle_medium_tick(self, now: datetime) -> None:
        """Handle MEDIUM tier periodic tasks (5 minutes).

        Learning and monitoring tasks that don't need minute-level accuracy:
        - Entity health check
        - Load data refresh
        - Decision backfill
        - Weather learning
        - Baseline calculation
        """
        # Read raw entity values
        if self._coordinator._entity_monitor is not None:
            self._coordinator._entity_monitor.read_all_external_state()

        # Skip expensive operations during startup grace period
        if self._is_in_startup_grace():
            _LOGGER.debug("Skipping medium tick operations during startup grace period")
            return

        # Check entity health
        if self._coordinator._entity_monitor is not None:
            self._coordinator._entity_monitor.check_entity_health()

        # Refresh load data (historical and recent)
        if self._coordinator._computation_engine is not None:
            from ..const import CONF_TESLEMETRY_LOAD_POWER

            load_entity_id = self._coordinator._get_entity_id(
                CONF_TESLEMETRY_LOAD_POWER
            )
            self._coordinator.hass.async_create_task(
                self._coordinator._computation_engine.async_get_recent_load_1hr(
                    load_entity_id
                ),
                "localshift_fetch_recent_load",
            )
            self._coordinator.hass.async_create_task(
                self._coordinator._computation_engine.async_get_historical_hourly_averages(
                    load_entity_id
                ),
                "localshift_fetch_historical_load",
            )

        if self._coordinator._learning_orchestrator is not None:
            self._coordinator._learning_orchestrator.update_medium_tick(
                self._coordinator.data
            )

        # Backfill solar forecast accuracy for completed periods (Issue #378)
        if (
            hasattr(self._coordinator, "solar_accuracy_tracker")
            and self._coordinator.solar_accuracy_tracker is not None
        ):
            pass

        # Update solar bias metrics from tracker (Issue #378)
        if (
            hasattr(self._coordinator, "solar_accuracy_tracker")
            and self._coordinator.solar_accuracy_tracker is not None
        ):
            self._coordinator.data.solar_bias_metrics = (
                self._coordinator.solar_accuracy_tracker.metrics.to_dict()
            )
            self._coordinator.data.solar_forecast_accuracy = (
                self._coordinator.solar_accuracy_tracker.metrics.accuracy
            )

        # Learn from current temperature/load for weather correlation
        if self._coordinator._computation_engine is not None:
            self._coordinator.hass.async_create_task(
                self._coordinator._computation_engine.async_learn_weather_sample(
                    self._coordinator.data
                ),
                "localshift_weather_learning",
            )

        _LOGGER.debug("Medium tick completed: learning and monitoring tasks")

    @callback
    def handle_slow_tick(self, now: datetime) -> None:
        """Handle SLOW tier periodic tasks (30 minutes).

        Slow-changing data tasks:
        - Weather forecast refresh
        - Forecast accuracy metrics
        - Forecast history save
        """
        # Refresh temperature forecast from weather entity (Issue #135)
        if self._coordinator._entity_monitor is not None:
            self._coordinator.hass.async_create_task(
                self._coordinator._entity_monitor.refresh_weather_forecast(),
                "localshift_weather_forecast",
            )

        # Refresh weather forecast
        if self._coordinator._computation_engine is not None:
            self._coordinator.hass.async_create_task(
                self._coordinator._computation_engine.async_compute_forecast_accuracy(
                    self._coordinator.data
                ),
                "localshift_forecast_accuracy",
            )
            # Save forecast history periodically (Issue #131)
            self._coordinator.hass.async_create_task(
                self._coordinator._computation_engine.async_save_forecast_history(
                    self._coordinator.data
                ),
                "localshift_save_forecast_history",
            )
            # Save accuracy metrics periodically (Issue #706)
            self._coordinator.hass.async_create_task(
                self._coordinator._computation_engine.async_save_accuracy_metrics(
                    self._coordinator.data
                ),
                "localshift_save_accuracy_metrics",
            )

        # Backfill actual solar energy for completed periods (Issue #513)
        self._backfill_solar_actual()

        _LOGGER.debug("Slow tick completed: weather forecast and accuracy metrics")

    @callback
    def handle_midnight_reset(self, now: datetime) -> None:
        """Reset cost accumulators and daily target flag at midnight.

        Called when the daily clock ticks past midnight. Resets all cost
        accumulators (battery_savings, battery_charge_cost, solar_yield_value,
        grid_export_revenue) and the target_reached flag.

        Notifies listeners and logs the reset for debugging.
        """
        self._coordinator.data.grid_import_cost = 0.0
        self._coordinator.data.grid_export_revenue = 0.0
        self._coordinator.data.battery_savings = 0.0
        self._coordinator.data.battery_charge_cost = 0.0
        self._coordinator.data.target_reached_today = False

        if self._coordinator._learning_orchestrator is not None:
            self._coordinator._learning_orchestrator.handle_midnight_reset(
                self._coordinator.data
            )

        self._coordinator._notify_listeners()
        _LOGGER.info("Midnight reset: cost accumulators and target flag")

    @callback
    def handle_daily_summary(self, now: datetime) -> None:
        """Send daily summary notification at demand window end.

        Replaces YAML A15 (localshift_daily_summary).
        """
        from ..const import SWITCH_AUTOMATION_ENABLED

        if not self._coordinator.get_switch_state(SWITCH_AUTOMATION_ENABLED):
            return

        self._coordinator.hass.async_create_task(
            self._send_daily_summary(),
            "localshift_daily_summary",
        )

    async def _send_daily_summary(self) -> None:
        """Send daily summary notification.

        Called by handle_daily_summary to send end-of-day notification.
        """
        if self._coordinator._notification_service is None:
            return

        await self._coordinator._notification_service.send_daily_summary(
            self._coordinator.data
        )

    def _is_in_startup_grace(self) -> bool:
        """Check if we're still in the startup grace period.

        Returns True if the state machine has an active startup grace period,
        False otherwise. Used to skip expensive operations during initialization
        when entities may not be populated yet (Issue #551).
        """
        if self._coordinator._state_machine is None:
            return True
        return self._coordinator._state_machine._startup_grace_until is not None

    def _accumulate_costs(self) -> None:
        """Accumulate per-minute energy costs from current power and price."""
        if self._coordinator._cost_tracker is not None:
            self._coordinator._cost_tracker.accumulate_costs(self._coordinator.data)

    def _backfill_solar_actual(self) -> None:
        """Backfill actual solar energy for completed 30-min periods.

        Calculates energy produced since last tick using integrated power,
        then calls backfill_actual() on the tracker for completed periods.
        """
        if not hasattr(self._coordinator, "solar_accuracy_tracker"):
            return

        tracker = getattr(self._coordinator, "solar_accuracy_tracker", None)
        if tracker is None:
            return

        if getattr(self._coordinator.data, "boost_charge_active", False) is True:
            return

        from homeassistant.util import dt as dt_util

        now = dt_util.now()
        current_power = self._coordinator.data.solar_power_kw

        if self._coordinator._last_solar_power_timestamp is None:
            self._coordinator._last_solar_power_timestamp = now
            self._coordinator._last_solar_power_kw = current_power
            return

        time_delta_hours = (
            now - self._coordinator._last_solar_power_timestamp
        ).total_seconds() / 3600.0
        if time_delta_hours < 0.01:
            return

        avg_power_kw = (self._coordinator._last_solar_power_kw + current_power) / 2.0
        energy_kwh = avg_power_kw * time_delta_hours

        if energy_kwh > 0.001 and current_power > 0.01:
            now_local = now.astimezone()
            period_start = now_local.replace(
                minute=(now_local.minute // 30) * 30, second=0, microsecond=0
            )
            tracker.backfill_actual(period_start, energy_kwh)

        self._coordinator._last_solar_power_timestamp = now
        self._coordinator._last_solar_power_kw = current_power

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

    # Solar backfill period geometry / robustness thresholds.
    _PERIOD_SECONDS = 1800.0  # 30-min accuracy period
    # A flushed period must be at least this fraction covered by integration
    # intervals, else it is discarded rather than recorded as a full actual.
    # Guards the restart case: the baseline re-establishes mid-period, so that
    # period only accumulates partial energy (Issue: partial-coverage poisoning).
    _MIN_COVERAGE_FRACTION = 0.9
    # Intervals longer than this (e.g. an event-loop stall or a long gap) are
    # not integrated — one giant trapezoid prorated across many periods would
    # produce several plausible-looking but garbage samples. We re-baseline and
    # skip integration; the spanned periods fall below the coverage gate.
    _MAX_INTEGRATION_SECONDS = 900.0  # 15 min

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
    ) -> None:
        """Initialize tick scheduler.

        Args:
            coordinator: Parent coordinator instance
        """
        self._coordinator = coordinator

        # Solar energy tracking for backfill (Issue #513)
        # Moved from coordinator — only TickScheduler reads/writes these
        self._last_solar_power_kw: float | None = None
        self._last_solar_power_timestamp: datetime | None = None

        # Accumulate-and-flush state for solar backfill attribution.
        # Energy integrated each medium tick is prorated into these wall-clock
        # 30-min buckets (keyed by ISO period_start) and flushed once the
        # period's end is in the past — so energy lands on the period it was
        # produced in, not the period that just started.
        self._period_energy_accum: dict[str, float] = {}  # ISO period_start -> kWh
        self._period_boost_flags: dict[str, bool] = {}  # ISO period_start -> boost seen
        self._period_coverage_accum: dict[str, float] = {}  # ISO -> seconds covered

    @callback
    def handle_state_change(self, _event: Event) -> None:
        """Handle a state change from a monitored entity."""
        if self._coordinator.evaluation_dispatcher is None:
            return

        self._coordinator.evaluation_dispatcher.on_state_change(_event)

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
        if self._coordinator.entity_monitor is not None:
            self._coordinator.entity_monitor.read_all_external_state()

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
        if self._coordinator.evaluation_dispatcher is not None:
            self._coordinator.evaluation_dispatcher.maybe_trigger_on_startup_ready(
                lambda: (
                    self._coordinator.data.automation_ready
                    if self._coordinator.data
                    else False
                )
            )

        # Issue #622: Always dispatch to StateMachine
        # StateMachine gates mode transitions based on price fingerprint
        # This ensures optimizer runs every minute for plan updates
        if self._coordinator.evaluation_dispatcher is not None:
            self._coordinator.evaluation_dispatcher.on_fast_tick(now)

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
        if self._coordinator.entity_monitor is not None:
            self._coordinator.entity_monitor.read_all_external_state()

        # Skip expensive operations during startup grace period
        if self._is_in_startup_grace():
            _LOGGER.debug("Skipping medium tick operations during startup grace period")
            return

        # Check entity health
        if self._coordinator.entity_monitor is not None:
            self._coordinator.entity_monitor.check_entity_health()

        # Refresh load data (historical and recent)
        if self._coordinator.computation_engine is not None:
            from ..const import CONF_TESLEMETRY_LOAD_POWER

            load_entity_id = self._coordinator.get_entity_id(CONF_TESLEMETRY_LOAD_POWER)
            self._coordinator.hass.async_create_task(
                self._coordinator.computation_engine.async_get_recent_load_1hr(
                    load_entity_id
                ),
                "localshift_fetch_recent_load",
            )
            self._coordinator.hass.async_create_task(
                self._coordinator.computation_engine.async_get_historical_hourly_averages(
                    load_entity_id
                ),
                "localshift_fetch_historical_load",
            )

        if self._coordinator.learning_orchestrator is not None:
            self._coordinator.learning_orchestrator.update_medium_tick(
                self._coordinator.data
            )

        # Backfill solar forecast accuracy for completed periods (Issue #378).
        # Runs on the 5-min cadence: shorter trapezoid integration is far more
        # accurate for fast-changing solar power and makes boundary proration
        # nearly exact.
        self._backfill_solar_actual()

        # Update solar bias metrics from tracker (Issue #378)
        if (
            hasattr(self._coordinator, "solar_accuracy_tracker")
            and self._coordinator.solar_accuracy_tracker is not None
        ):
            self._coordinator.data.solar_bias_metrics = (
                self._coordinator.solar_accuracy_tracker.get_status_dict()
            )
            self._coordinator.data.solar_forecast_accuracy = (
                self._coordinator.solar_accuracy_tracker.reported_accuracy()
            )

        # Learn from current temperature/load for weather correlation
        if self._coordinator.computation_engine is not None:
            self._coordinator.hass.async_create_task(
                self._coordinator.computation_engine.async_learn_weather_sample(
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
        if self._coordinator.entity_monitor is not None:
            self._coordinator.hass.async_create_task(
                self._coordinator.entity_monitor.refresh_weather_forecast(),
                "localshift_weather_forecast",
            )

        # Refresh weather forecast
        if self._coordinator.computation_engine is not None:
            self._coordinator.hass.async_create_task(
                self._coordinator.computation_engine.async_compute_forecast_accuracy(
                    self._coordinator.data
                ),
                "localshift_forecast_accuracy",
            )
            # Save forecast history periodically (Issue #131)
            self._coordinator.hass.async_create_task(
                self._coordinator.computation_engine.async_save_forecast_history(
                    self._coordinator.data
                ),
                "localshift_save_forecast_history",
            )
            # Save accuracy metrics periodically (Issue #706)
            self._coordinator.hass.async_create_task(
                self._coordinator.computation_engine.async_save_accuracy_metrics(
                    self._coordinator.data
                ),
                "localshift_save_accuracy_metrics",
            )

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
        # Issue #868: reset the daily energy accumulators alongside the cost
        # accumulators so the performance-metric ratios start fresh each day.
        self._coordinator.data.grid_import_kwh_today = 0.0
        self._coordinator.data.grid_export_kwh_today = 0.0
        self._coordinator.data.grid_to_battery_kwh_today = 0.0
        self._coordinator.data.soc_gain_during_grid_charge_kwh_today = 0.0
        self._coordinator.data.export_while_battery_not_full_kwh_today = 0.0

        if self._coordinator.learning_orchestrator is not None:
            self._coordinator.learning_orchestrator.handle_midnight_reset(
                self._coordinator.data
            )

        self._coordinator.notify_listeners()
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
        if self._coordinator.notification_service is None:
            return

        await self._coordinator.notification_service.send_daily_summary(
            self._coordinator.data
        )

    def _is_in_startup_grace(self) -> bool:
        """Check if we're still in the startup grace period.

        Returns True if the state machine has an active startup grace period,
        False otherwise. Used to skip expensive operations during initialization
        when entities may not be populated yet (Issue #551).
        """
        if self._coordinator.state_machine is None:
            return True
        return self._coordinator.state_machine.startup_grace_until is not None

    def _accumulate_costs(self) -> None:
        """Accumulate per-minute energy costs from current power and price."""
        if self._coordinator.cost_tracker is not None:
            self._coordinator.cost_tracker.accumulate_costs(self._coordinator.data)

    def _backfill_solar_actual(self) -> None:
        """Accumulate solar energy into wall-clock periods and flush completed ones.

        Each medium tick integrates solar power since the previous tick
        (trapezoid) and prorates that energy across every 30-min wall-clock
        period the interval overlaps. A period is flushed to the tracker once
        its end is in the past, so energy produced 09:30-10:00 is attributed to
        the 09:30 period and flushed at the first tick after 10:00 — the
        attribution fix — instead of to the 10:00 period that just started.

        Boost intervals continue to accumulate (the daily ~3pm boost no longer
        orphans pending forecasts); their periods are flagged so the tracker
        excludes them from metrics. The dusk power gate is gone — whether a
        record is meaningful is decided at flush time by the tracker.

        Two robustness guards keep partial/garbage samples out of the (now
        persisted) learning store: a period must be >=90% covered by integration
        intervals to be recorded (else it is discarded — handles the restart
        re-baseline mid-period), and intervals longer than 15 min are not
        integrated at all (an event-loop stall would otherwise smear one
        trapezoid across many periods as plausible-looking garbage).
        """
        tracker = getattr(self._coordinator, "solar_accuracy_tracker", None)
        if tracker is None:
            return

        from datetime import timedelta

        from homeassistant.util import dt as dt_util

        now = dt_util.now()
        current_power = self._coordinator.data.solar_power_kw

        if self._last_solar_power_timestamp is None:
            self._last_solar_power_timestamp = now
            self._last_solar_power_kw = current_power
            return

        assert self._last_solar_power_kw is not None  # nosec B101 — type narrowing, not assertion

        last_ts = self._last_solar_power_timestamp
        interval_seconds = (now - last_ts).total_seconds()
        if interval_seconds / 3600.0 < 0.01:
            # Interval too short to integrate; keep the baseline for next tick.
            return

        # Pathological gap (stall / long pause): re-baseline without integrating.
        # The periods spanned by the gap stay below the coverage gate and are
        # discarded at flush, rather than recording a smeared trapezoid.
        if interval_seconds <= self._MAX_INTEGRATION_SECONDS:
            boost_active = bool(
                getattr(self._coordinator.data, "boost_charge_active", False)
            )
            energy_kwh = ((self._last_solar_power_kw + current_power) / 2.0) * (
                interval_seconds / 3600.0
            )

            # Prorate this interval's energy across every 30-min wall-clock period
            # it overlaps, accumulating both energy and covered seconds. Keys are
            # the local :00/:30 floor serialized with .isoformat(), matching the
            # pending-forecast keys recorded by optimizer_facade.
            start_local = last_ts.astimezone()
            end_local = now.astimezone()
            period_start = start_local.replace(
                minute=(start_local.minute // 30) * 30, second=0, microsecond=0
            )
            while period_start < end_local:
                period_end = period_start + timedelta(minutes=30)
                overlap_seconds = (
                    min(end_local, period_end) - max(start_local, period_start)
                ).total_seconds()
                if overlap_seconds > 0:
                    key = period_start.isoformat()
                    self._period_energy_accum[key] = self._period_energy_accum.get(
                        key, 0.0
                    ) + energy_kwh * (overlap_seconds / interval_seconds)
                    self._period_coverage_accum[key] = (
                        self._period_coverage_accum.get(key, 0.0) + overlap_seconds
                    )
                    if boost_active:
                        self._period_boost_flags[key] = True
                period_start = period_end

        # Advance the integration baseline.
        self._last_solar_power_timestamp = now
        self._last_solar_power_kw = current_power

        # Flush every period whose end is now in the past (record or discard).
        self._flush_completed_periods(tracker, now.astimezone())

        # Drop pendings that never received an actual (e.g. restart mid-period).
        tracker.evict_stale_pendings()

    def _flush_completed_periods(self, tracker, now_local: datetime) -> None:
        """Flush accumulated periods whose end is in the past.

        A period is recorded only if it is at least _MIN_COVERAGE_FRACTION
        covered by integration intervals; otherwise it is discarded (its
        pending forecast is later cleaned by evict_stale_pendings) so a
        partially-observed period is not recorded as a full-period actual.
        """
        from datetime import timedelta

        min_coverage = self._MIN_COVERAGE_FRACTION * self._PERIOD_SECONDS
        for key in list(self._period_energy_accum.keys()):
            period_start = datetime.fromisoformat(key)
            if period_start + timedelta(minutes=30) > now_local:
                continue
            kwh = self._period_energy_accum.pop(key)
            coverage = self._period_coverage_accum.pop(key, 0.0)
            is_boost = self._period_boost_flags.pop(key, False)
            if coverage < min_coverage:
                _LOGGER.debug(
                    "Discarding partially-covered solar period %s (%.0f%% covered)",
                    period_start.strftime("%H:%M"),
                    100.0 * coverage / self._PERIOD_SECONDS,
                )
                continue
            tracker.backfill_actual(period_start, kwh, is_boost=is_boost)

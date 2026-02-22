"""Coordinator for the LocalShift integration.

Subscribes to external entity state changes (Teslemetry, pricing, Solcast),
coordinates internal state updates, and triggers automation logic.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)

from .const import (
    CONF_DEMAND_WINDOW_END,
    CONF_NOTIFY_SERVICE,
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    CONF_PRICING_PRICE_SPIKE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_TESLEMETRY_BACKUP_RESERVE,
    CONF_TESLEMETRY_BATTERY_POWER,
    CONF_TESLEMETRY_GRID_POWER,
    CONF_TESLEMETRY_LOAD_POWER,
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
    CONF_TESLEMETRY_SOLAR_POWER,
    CONF_THERMAL_MODE_DECISION_TIME,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_THERMAL_MODE_DECISION_TIME,
    SWITCH_DEFAULTS,
)
from .coordinator_data import CoordinatorData

if TYPE_CHECKING:
    from .battery_controller import BatteryController
    from .computation_engine import ComputationEngine
    from .cost_tracker import CostTracker
    from .entity_validator import EntityValidator
    from .notification_service import NotificationService
    from .state_machine import StateMachine
    from .state_reader import StateReader
    from .thermal_manager import ThermalManager


_LOGGER = logging.getLogger(__name__)

# How often the coordinator re-evaluates (matches A16/A9 cadence)
PERIODIC_INTERVAL = timedelta(minutes=1)

# Solcast startup retry configuration
SOLCAST_STARTUP_RETRY_DELAY = timedelta(seconds=30)
SOLCAST_MAX_STARTUP_RETRIES = 3


class LocalShiftCoordinator:
    """Central coordinator: reads external entities, computes state, drives battery.

    This is NOT a DataUpdateCoordinator (we don't poll an API). Instead we
    subscribe to HA entity state changes and run a periodic 1-minute tick.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator."""
        self.hass = hass
        self.entry = entry
        self.data = CoordinatorData()
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsub_state: CALLBACK_TYPE | None = None
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._unsub_midnight: CALLBACK_TYPE | None = None
        self._unsub_daily_summary: CALLBACK_TYPE | None = None
        self._unsub_thermal_mode_decision: CALLBACK_TYPE | None = None
        self._update_callbacks: list[CALLBACK_TYPE] = []

        # Switch state bridge — switches read/write via these methods
        self._switch_states: dict[str, bool] = dict(SWITCH_DEFAULTS)

        # Helper modules (initialized in async_start)
        self._state_reader: StateReader | None = None
        self._cost_tracker: CostTracker | None = None
        self._battery_controller: BatteryController | None = None
        self._notification_service: NotificationService | None = None
        self._computation_engine: ComputationEngine | None = None
        self._state_machine: StateMachine | None = None
        self._entity_validator: EntityValidator | None = None
        self._thermal_manager: ThermalManager | None = None

        # Solcast startup retry tracking
        self._solcast_retry_count: int = 0
        self._solcast_ready: bool = False

    # ------------------------------------------------------------------
    # Entity ID helpers (read from config entry data)
    # ------------------------------------------------------------------

    @property
    def entity_ids(self) -> dict[str, str]:
        """Return the configured external entity IDs."""
        return self.entry.data

    def _get_entity_id(self, key: str) -> str:
        """Get a configured external entity ID by config key.

        For notify_service, checks options first (new location) then data
        (old location for backward compatibility).

        Returns default from DEFAULT_ENTITY_IDS if key not found.
        """
        # Special handling for notify_service - check options first
        if key == CONF_NOTIFY_SERVICE:
            if key in self.entry.options:
                return self.entry.options[key]
            if key in self.entry.data:
                return self.entry.data[key]
            return ""

        if key in self.entry.data:
            return self.entry.data[key]

        # Fallback to default if key not in entry data
        from .const import DEFAULT_ENTITY_IDS

        return DEFAULT_ENTITY_IDS.get(key, "")

    # ------------------------------------------------------------------
    # Options helpers (read from config entry options)
    # ------------------------------------------------------------------

    def get_option(self, key: str, default: Any = None) -> Any:
        """Get a user-configurable option value."""
        return self.entry.options.get(key, default)

    # ------------------------------------------------------------------
    # Switch state bridge
    # ------------------------------------------------------------------

    def get_switch_state(self, key: str) -> bool:
        """Get a switch state by key."""
        return self._switch_states.get(key, SWITCH_DEFAULTS.get(key, False))

    def set_switch_state(self, key: str, value: bool) -> None:
        """Set a switch state and trigger re-evaluation."""
        self._switch_states[key] = value

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Start listening to entity changes and periodic timer."""
        # Initialize helper modules
        from .battery_controller import BatteryController
        from .computation_engine import ComputationEngine
        from .cost_tracker import CostTracker
        from .entity_validator import EntityValidator
        from .notification_service import NotificationService
        from .state_machine import StateMachine
        from .state_reader import StateReader

        self._state_reader = StateReader(self.hass, self.entry)
        self._cost_tracker = CostTracker(self.hass)
        self._battery_controller = BatteryController(self.hass, self._get_entity_id)
        self._notification_service = NotificationService(
            self.hass, self.entry, self._get_entity_id, self.get_switch_state
        )
        self._computation_engine = ComputationEngine(
            self.hass, self.entry, self._get_entity_id, self.get_switch_state
        )
        self._state_machine = StateMachine(
            self._battery_controller,
            self._notification_service,
            self.get_switch_state,
            self.get_option,
        )
        self._entity_validator = EntityValidator(self.hass, self._get_entity_id)

        # Initialize ThermalManager for HVAC-aware load correlation (Issue #137)
        from .thermal_manager import ThermalManager

        self._thermal_manager = ThermalManager(
            self.hass,
            self.entry,
            self._get_entity_id,
            self.get_switch_state,
            self.get_option,
        )

        # Collect all external entity IDs to watch
        # NOTE: We don't watch CONF_TESLEMETRY_ALLOW_EXPORT because we change it
        # programmatically and don't want to trigger re-evaluation loops
        monitored_entities = [
            self._get_entity_id(CONF_TESLEMETRY_OPERATION_MODE),
            self._get_entity_id(CONF_TESLEMETRY_BACKUP_RESERVE),
            self._get_entity_id(CONF_TESLEMETRY_SOC),
            self._get_entity_id(CONF_TESLEMETRY_GRID_POWER),
            self._get_entity_id(CONF_TESLEMETRY_BATTERY_POWER),
            self._get_entity_id(CONF_TESLEMETRY_SOLAR_POWER),
            self._get_entity_id(CONF_TESLEMETRY_LOAD_POWER),
            # NOT monitoring allow_export - changes programmatically
            self._get_entity_id(CONF_PRICING_GENERAL_PRICE),
            self._get_entity_id(CONF_PRICING_FEED_IN_PRICE),
            self._get_entity_id(CONF_PRICING_GENERAL_FORECAST),
            self._get_entity_id(CONF_PRICING_FEED_IN_FORECAST),
            self._get_entity_id(CONF_PRICING_PRICE_SPIKE),
            self._get_entity_id(CONF_SOLCAST_FORECAST_TODAY),
            self._get_entity_id(CONF_SOLCAST_FORECAST_TOMORROW),
        ]

        # Subscribe to state changes
        self._unsub_state = async_track_state_change_event(
            self.hass, monitored_entities, self._handle_state_change
        )

        # 1-minute periodic tick (cost accumulation, DW checks, re-evaluation)
        self._unsub_timer = async_track_time_interval(
            self.hass, self._handle_periodic_tick, PERIODIC_INTERVAL
        )

        # Midnight reset (replaces A12): reset cost accumulators + target flag
        self._unsub_midnight = async_track_time_change(
            self.hass, self._handle_midnight_reset, hour=0, minute=0, second=0
        )

        # Daily summary notification (replaces A15): fires at DW end time
        dw_end = self._parse_time_option(
            CONF_DEMAND_WINDOW_END, DEFAULT_DEMAND_WINDOW_END
        )
        self._unsub_daily_summary = async_track_time_change(
            self.hass,
            self._handle_daily_summary,
            hour=dw_end.hour,
            minute=dw_end.minute,
            second=0,
        )

        # Daily thermal mode determination (Issue #140): fires at decision time
        decision_time = self._parse_time_option(
            CONF_THERMAL_MODE_DECISION_TIME, DEFAULT_THERMAL_MODE_DECISION_TIME
        )
        self._unsub_thermal_mode_decision = async_track_time_change(
            self.hass,
            self._handle_thermal_mode_decision,
            hour=decision_time.hour,
            minute=decision_time.minute,
            second=0,
        )

        # Read initial state and compute
        self._read_all_external_state()

        # Fetch historical load data in background (runs in thread pool, won't block)
        load_entity_id = self._get_entity_id(CONF_TESLEMETRY_LOAD_POWER)
        await self._computation_engine.async_get_historical_hourly_averages(
            load_entity_id
        )

        # Also fetch recent 1-hour load average for weighted forecasting
        await self._computation_engine.async_get_recent_load_1hr(load_entity_id)

        # Initialize weather correlation for temperature-based load prediction
        await self._computation_engine.async_initialize_weather_correlation()

        # Initialize forecast history storage and load persisted history (Issue #131)
        await self._computation_engine.async_initialize_forecast_history_storage()
        await self._computation_engine.async_load_forecast_history(self.data)

        # Wait for Solcast data to be ready before computing forecasts
        # This prevents errors when Solcast hasn't initialized yet
        await self._wait_for_solcast_and_compute()

        # Startup grace: wait 30 s for entities to populate before acting
        self._state_machine.set_startup_grace(30)

        inferred_mode = self._state_machine.infer_current_hardware_mode(self.data)

        _LOGGER.info(
            "LocalShift coordinator started, monitoring %d entities, inferred mode: %s",
            len(monitored_entities),
            inferred_mode.value,
        )

    async def async_stop(self) -> None:
        """Stop listening and clean up."""
        for unsub in (
            self._unsub_state,
            self._unsub_timer,
            self._unsub_midnight,
            self._unsub_daily_summary,
        ):
            if unsub:
                unsub()
        self._unsub_state = None
        self._unsub_timer = None
        self._unsub_midnight = None
        self._unsub_daily_summary = None

        # (backlog-med-004) Clean up historical load cache on shutdown
        if self._computation_engine is not None:
            self._computation_engine.clear_historical_cache()

        _LOGGER.info("LocalShift coordinator stopped")

    # ------------------------------------------------------------------
    # Entity update subscription (for sensor/binary_sensor entities)
    # ------------------------------------------------------------------

    @callback
    def async_add_listener(self, update_callback: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Register a callback that fires when data changes.

        Returns a callable to unsubscribe.
        """
        self._update_callbacks.append(update_callback)

        @callback
        def remove_listener() -> None:
            self._update_callbacks.remove(update_callback)

        return remove_listener

    @callback
    def _notify_listeners(self) -> None:
        """Notify all registered entity listeners of new data."""
        for cb in self._update_callbacks:
            cb()

    # ------------------------------------------------------------------
    # State reading
    # ------------------------------------------------------------------

    def _read_all_external_state(self) -> None:
        """Read current state of all monitored external entities."""
        if self._state_reader is None:
            return
        self._state_reader.read_all_external_state(self.data)

    def _check_entity_health(self) -> None:
        """Check health of all tracked entities and update data.

        Populates integration status, errors, and warnings in CoordinatorData
        for sensors to expose to users.
        """
        if self._entity_validator is None:
            return

        # Check all entities
        self._entity_validator.check_all_entities()

        # Update coordinator data with health status
        self.data.integration_status = self._entity_validator.status.value
        self.data.integration_status_message = (
            self._entity_validator.get_user_friendly_message()
        )
        self.data.entity_errors = self._entity_validator.errors
        self.data.entity_warnings = self._entity_validator.warnings
        self.data.required_entities_healthy = all(
            self._entity_validator.get_required_entities_status().values()
        )

        # Get detailed health summary
        health_summary = self._entity_validator.get_health_summary()
        self.data.entity_health = health_summary.get("entities", {})
        self.data.last_entity_check = health_summary.get("last_check", "")

        # Log any new errors
        if self.data.entity_errors:
            for error in self.data.entity_errors:
                _LOGGER.warning("Entity health error: %s", error)

        # Log warnings at debug level
        if self.data.entity_warnings:
            for warning in self.data.entity_warnings:
                _LOGGER.debug("Entity health warning: %s", warning)

    def _check_solcast_ready(self) -> bool:
        """Check if Solcast forecast data is available and valid.

        Returns True if Solcast data is ready, False otherwise.
        """
        # Check if today's forecast has valid data
        today_entity = self._get_entity_id(CONF_SOLCAST_FORECAST_TODAY)
        today_state = self.hass.states.get(today_entity)

        if today_state is None:
            _LOGGER.debug("Solcast today entity not found: %s", today_entity)
            return False

        if today_state.state in ("unknown", "unavailable", None, ""):
            _LOGGER.debug(
                "Solcast today entity state is %s, waiting for data",
                today_state.state,
            )
            return False

        # Check if the forecast attribute has actual forecast data
        forecast_data = today_state.attributes.get("detailedForecast")
        if not forecast_data or not isinstance(forecast_data, list):
            _LOGGER.debug("Solcast today forecast attribute is empty or invalid")
            return False

        # Check if we have at least some forecast entries
        if len(forecast_data) == 0:
            _LOGGER.debug("Solcast today forecast has no entries")
            return False

        _LOGGER.info(
            "Solcast forecast data is ready (%d entries for today)",
            len(forecast_data),
        )
        return True

    async def _wait_for_solcast_and_compute(self) -> None:
        """Wait for Solcast data to be ready, then compute derived values.

        This is called at startup and retries if Solcast data is not immediately available.
        """
        if self._check_solcast_ready():
            self._solcast_ready = True
            _LOGGER.info("Solcast data available, proceeding with forecast computation")
            self._compute_derived_values()
            self._notify_listeners()
            return

        # Solcast not ready - check if we can retry
        if self._solcast_retry_count >= SOLCAST_MAX_STARTUP_RETRIES:
            _LOGGER.warning(
                "Solcast data not available after %d retries. "
                "Forecast will use 0 kWh solar until Solcast provides data. "
                "Check Solcast integration status.",
                SOLCAST_MAX_STARTUP_RETRIES,
            )
            # Still compute with whatever data we have
            self._compute_derived_values()
            self._notify_listeners()
            return

        self._solcast_retry_count += 1
        _LOGGER.info(
            "Solcast data not ready yet (attempt %d/%d), retrying in %d seconds",
            self._solcast_retry_count,
            SOLCAST_MAX_STARTUP_RETRIES,
            SOLCAST_STARTUP_RETRY_DELAY.total_seconds(),
        )

        # Schedule a retry
        self.hass.async_create_task(
            self._retry_solcast_check(),
            "localshift_solcast_retry",
        )

    async def _retry_solcast_check(self) -> None:
        """Retry checking Solcast data after a delay."""
        import asyncio

        await asyncio.sleep(SOLCAST_STARTUP_RETRY_DELAY.total_seconds())

        # Re-read state before checking
        self._read_all_external_state()

        await self._wait_for_solcast_and_compute()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    @callback
    def _handle_state_change(self, _event: Event) -> None:
        """Handle a state change from a monitored entity."""
        if self._state_machine is None:
            return

        # Skip re-evaluation if we're in the middle of a mode transition
        # This prevents feedback loops when we programmatically change entities
        if self._state_machine.in_mode_transition:
            _LOGGER.debug("Skipping re-evaluation during mode transition")
            return

        # Read raw entity values immediately so sensors reflect the new state.
        # Derived-value computation (_compute_derived_values) is intentionally
        # NOT called here — it happens inside the evaluate lock in
        # _evaluate_state_machine() so that queued evaluations always use
        # fresh post-transition state rather than a stale snapshot.
        self._read_all_external_state()
        self._notify_listeners()

        self.hass.async_create_task(
            self._evaluate_state_machine(),
            "localshift_evaluate_state_change",
        )

    @callback
    def _handle_periodic_tick(self, now: datetime) -> None:
        """Handle the 1-minute periodic re-evaluation."""
        # Read raw entity values now — needed for cost accumulation below.
        self._read_all_external_state()

        # Check entity health periodically
        self._check_entity_health()

        # Refresh load data periodically (every ~5 minutes)
        # This is async so we fire it and forget - it will update the cache
        if self._computation_engine is not None:
            load_entity_id = self._get_entity_id(CONF_TESLEMETRY_LOAD_POWER)
            self.hass.async_create_task(
                self._computation_engine.async_get_recent_load_1hr(load_entity_id),
                "localshift_fetch_recent_load",
            )
            # Also refresh historical hourly averages (7-day profile)
            # This ensures the cache is always populated even after restarts
            self.hass.async_create_task(
                self._computation_engine.async_get_historical_hourly_averages(
                    load_entity_id
                ),
                "localshift_fetch_historical_load",
            )

        # Cost accumulation uses the raw state we just read (sync, no lock needed)
        self._accumulate_costs()

        # Learn from current temperature/load for weather correlation
        # This runs asynchronously and won't block the periodic tick
        if self._computation_engine is not None:
            self.hass.async_create_task(
                self._computation_engine.async_learn_weather_sample(self.data),
                "localshift_weather_learning",
            )
            # Refresh temperature forecast from weather entity (Issue #135)
            # Uses modern weather.get_forecasts service with 30-min cache
            self.hass.async_create_task(
                self._refresh_weather_forecast(),
                "localshift_weather_forecast",
            )
            # Compute forecast accuracy metrics (Issue #37 Phase 2)
            self.hass.async_create_task(
                self._computation_engine.async_compute_forecast_accuracy(self.data),
                "localshift_forecast_accuracy",
            )
            # Save forecast history periodically (Issue #131)
            self.hass.async_create_task(
                self._computation_engine.async_save_forecast_history(self.data),
                "localshift_save_forecast_history",
            )

        # Learn HVAC power from climate state changes (Issue #137)
        # This separates thermal load from baseline for grid charging decisions
        if self._thermal_manager is not None:
            self.hass.async_create_task(
                self._learn_hvac_power(),
                "localshift_hvac_learning",
            )
            # Calculate and pass baseline load to computation engine
            # This completes the Issue #137 feedback loop fix
            baseline = self._calculate_baseline_load()
            if baseline and self._computation_engine is not None:
                self._computation_engine.set_baseline_load(baseline)

            # Evaluate pre-conditioning (Issue #63 Phase 4)
            # Pre-heat or pre-cool before demand window
            self.hass.async_create_task(
                self._evaluate_preconditioning(),
                "localshift_preconditioning",
            )

            # Evaluate solar tapering (Issue #141 Phase 5)
            # Consume excess solar by adjusting HVAC setpoints
            self.hass.async_create_task(
                self._evaluate_solar_taper(),
                "localshift_solar_taper",
            )

        # Derived-value computation and listener notification happen inside the
        # evaluate lock so that back-to-back periodic ticks don't concurrently
        # mutate shared data or operate on stale post-transition state.
        self.hass.async_create_task(
            self._evaluate_state_machine(),
            "localshift_evaluate_periodic",
        )

    @callback
    def _handle_midnight_reset(self, now: datetime) -> None:
        """Reset daily cost accumulators and target flag at midnight.

        Replaces YAML A12 (localshift_reset_target_reached).
        Also unlocks daily thermal mode for re-determination.
        """
        self.data.grid_import_cost = 0.0
        self.data.grid_export_revenue = 0.0
        self.data.battery_savings = 0.0
        self.data.battery_charge_cost = 0.0
        self.data.target_reached_today = False

        # Unlock daily thermal mode for new decision at decision time
        self.data.daily_mode_locked = False
        self.data.daily_mode_determined_at = ""

        self._notify_listeners()
        _LOGGER.info(
            "Midnight reset: cost accumulators, target flag, and thermal mode unlocked"
        )

    @callback
    def _handle_daily_summary(self, now: datetime) -> None:
        """Send daily summary notification at demand window end.

        Replaces YAML A15 (localshift_daily_summary).
        """
        from .const import SWITCH_AUTOMATION_ENABLED

        if not self.get_switch_state(SWITCH_AUTOMATION_ENABLED):
            return

        self.hass.async_create_task(
            self._send_daily_summary(),
            "localshift_daily_summary",
        )

    @callback
    def _handle_thermal_mode_decision(self, now: datetime) -> None:
        """Determine daily thermal mode from weather forecast.

        Fires at thermal_mode_decision_time (default 06:00) to decide
        today's HVAC operating mode: HEAT, COOL, DRY, or OFF.

        The mode is locked until the next day's decision time.
        """
        if self._thermal_manager is None:
            return

        if not self._thermal_manager.is_enabled():
            _LOGGER.debug("Thermal management disabled, skipping mode decision")
            return

        # Get weather temperature forecast
        temp_forecast = self.data.weather_temperature_forecast

        # Get current humidity if available
        humidity = None
        from .const import CONF_WEATHER_ENTITY

        weather_entity = self._get_entity_id(CONF_WEATHER_ENTITY)
        weather_state = self.hass.states.get(weather_entity)
        if weather_state is not None:
            humidity = weather_state.attributes.get("humidity")

        # Determine mode from forecast
        mode = self._thermal_manager.determine_daily_mode(temp_forecast, humidity)

        # Update coordinator data
        self.data.daily_thermal_mode = mode
        self.data.daily_mode_locked = True
        self.data.daily_mode_determined_at = now.isoformat()

        _LOGGER.info(
            "Daily thermal mode determined: %s (locked until tomorrow)",
            mode.value,
        )

        # Notify listeners of the mode change
        self._notify_listeners()

    # ------------------------------------------------------------------
    # Computation
    # ------------------------------------------------------------------

    def _compute_derived_values(self) -> None:
        """Compute all derived sensor/binary_sensor values from raw state."""
        if self._computation_engine is not None:
            self._computation_engine.compute_derived_values(self.data)

    # ------------------------------------------------------------------
    # Cost tracking
    # ------------------------------------------------------------------

    def _accumulate_costs(self) -> None:
        """Accumulate per-minute energy costs from current power and price."""
        if self._cost_tracker is not None:
            self._cost_tracker.accumulate_costs(self.data)

    async def _send_daily_summary(self) -> None:
        """Send end-of-day summary notification."""
        if self._notification_service is not None:
            await self._notification_service.send_daily_summary(self.data)
        _LOGGER.info("Daily summary notification sent")

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    async def async_evaluate_state_machine(self) -> None:
        """Compare desired mode with commanded mode and execute transitions.

        Public method for external triggers (e.g., options update).
        """
        await self._evaluate_state_machine()

    async def async_recompute_and_evaluate(self) -> None:
        """Public method for triggering recomputation and state evaluation.

        Called by switch and number platforms when configuration changes.
        Encapsulates the pattern: compute derived values → notify listeners → evaluate state machine.
        """
        self._compute_derived_values()
        self._notify_listeners()
        await self.async_evaluate_state_machine()

    def reschedule_daily_summary_timer(self) -> None:
        """Reschedule the daily summary timer with current demand_window_end.

        Called when options are updated to pick up new notification time
        without requiring a restart.
        """
        # Unsubscribe existing timer if present
        if self._unsub_daily_summary is not None:
            self._unsub_daily_summary()
            self._unsub_daily_summary = None

        # Schedule new timer with updated time
        dw_end = self._parse_time_option(
            CONF_DEMAND_WINDOW_END, DEFAULT_DEMAND_WINDOW_END
        )
        self._unsub_daily_summary = async_track_time_change(
            self.hass,
            self._handle_daily_summary,
            hour=dw_end.hour,
            minute=dw_end.minute,
            second=0,
        )
        _LOGGER.info(
            "Daily summary timer rescheduled for %02d:%02d",
            dw_end.hour,
            dw_end.minute,
        )

    async def _evaluate_state_machine(self) -> None:
        """Compare desired mode with commanded mode and execute transitions."""
        if self._state_machine is not None and self._computation_engine is not None:
            await self._state_machine.evaluate_state_machine(
                self.data,
                self._computation_engine,
                read_state_func=self._read_all_external_state,
                notify_func=self._notify_listeners,
            )

    # ------------------------------------------------------------------
    # Button handlers (delegated to battery_controller)
    # ------------------------------------------------------------------

    async def async_set_self_consumption(self) -> None:
        """Set battery to self consumption mode."""
        if self._battery_controller is not None:
            await self._battery_controller.set_self_consumption(self.data, False)

    async def async_set_force_charge(self) -> None:
        """Set battery to force charge mode."""
        if self._battery_controller is not None:
            await self._battery_controller.set_force_charge(self.data, False)

    async def async_set_boost_charge(self) -> None:
        """Set battery to boost charge mode."""
        if self._battery_controller is not None:
            await self._battery_controller.set_boost_charge(self.data, False)

    async def async_set_force_discharge(self) -> None:
        """Set battery to force discharge mode."""
        if self._battery_controller is not None:
            await self._battery_controller.set_force_discharge(self.data, False)

    async def async_set_manual_override(self) -> None:
        """Set manual override mode."""
        self.data.manual_override = True
        if self._state_machine is not None:
            self._state_machine.set_manual_override_timestamp()
        if self._battery_controller is not None:
            # Don't issue any commands in manual override
            _LOGGER.info("Manual override activated - user controls battery")

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _parse_time_option(self, key: str, default: str) -> time:
        """Parse a time string option (HH:MM:SS) into a time object."""
        time_str = str(self.get_option(key, default))
        parts = time_str.split(":")
        try:
            return time(
                int(parts[0]),
                int(parts[1]) if len(parts) > 1 else 0,
                int(parts[2]) if len(parts) > 2 else 0,
            )
        except (ValueError, IndexError):
            d_parts = default.split(":")
            return time(int(d_parts[0]), int(d_parts[1]), int(d_parts[2]))

    async def async_clear_historical_cache(self) -> None:
        """Clear historical load cache to force forecast refresh."""
        if self._computation_engine is not None:
            self._computation_engine.clear_historical_cache()
            _LOGGER.info("Historical load cache cleared")

    async def async_send_notification(self, title: str, message: str) -> None:
        """Send a notification via the notification service."""
        if self._notification_service is not None:
            await self._notification_service.send_notification(title, message)

    async def _refresh_weather_forecast(self) -> None:
        """Refresh temperature forecast from weather entity.

        Uses the modern weather.get_forecasts service (HA 2024.3+) with caching.
        Updates CoordinatorData with the latest forecast for use by sensors.
        """
        if self._computation_engine is None:
            return

        forecasts = await self._computation_engine.async_refresh_weather_forecast()

        if forecasts is not None:
            # Update CoordinatorData with the forecast data

            self.data.weather_temperature_forecast = {}
            for forecast in forecasts:
                hour = forecast.slot_time.hour
                temperature = forecast.temperature
                if temperature is not None:
                    self.data.weather_temperature_forecast[hour] = temperature

            _LOGGER.debug(
                "Updated weather forecast: %d hours of temperature data",
                len(self.data.weather_temperature_forecast),
            )

    async def _learn_hvac_power(self) -> None:
        """Learn HVAC power from climate state changes.

        This is the key method that solves Issue #137 by learning how much
        power the HVAC system uses when it changes state. This learned power
        is then used to separate HVAC load from baseline consumption.
        """
        if self._thermal_manager is None:
            return

        # Get current load power for learning
        load_entity_id = self._get_entity_id(CONF_TESLEMETRY_LOAD_POWER)
        load_state = self.hass.states.get(load_entity_id)
        current_load_kw = 0.0
        if load_state is not None:
            try:
                current_load_kw = float(load_state.state) / 1000.0  # W to kW
            except (ValueError, TypeError):
                pass

        # Learn from current state (synchronous method)
        self._thermal_manager.learn_hvac_power(
            data=self.data,
            current_load_kw=current_load_kw,
            timestamp=datetime.now(),
        )

        # Update coordinator data with learned HVAC power summary
        self.data.learned_hvac_power = self._thermal_manager.get_learned_power_summary()

        _LOGGER.debug(
            "HVAC learning: total load = %.2f kW, learned entities = %d",
            current_load_kw,
            len(self.data.learned_hvac_power),
        )

    def _calculate_baseline_load(self) -> dict[int, float]:
        """Calculate baseline load profile for Issue #137 feedback loop fix.

        This estimates the non-HVAC baseline consumption by subtracting
        learned HVAC power from historical averages. This baseline is then
        used for grid charging decisions, preventing unnecessary charging
        when HVAC is running.

        Returns:
            Dict of hour -> baseline load in kW, or empty dict if unavailable.
        """
        if self._thermal_manager is None:
            return {}

        if not self._thermal_manager.is_enabled():
            return {}

        if self._computation_engine is None:
            return {}

        # Get historical hourly averages
        load_entity_id = self._get_entity_id(CONF_TESLEMETRY_LOAD_POWER)
        hourly_avg_kw = self._computation_engine._get_historical_hourly_averages(
            load_entity_id
        )

        if not hourly_avg_kw:
            return {}

        # Get daily thermal mode
        daily_mode = self.data.daily_thermal_mode

        # Estimate baseline from historical
        baseline = self._thermal_manager.estimate_baseline_from_historical(
            historical_avg_kw=hourly_avg_kw,
            daily_mode=daily_mode,
        )

        # Store in coordinator data for diagnostics
        self.data.baseline_load_kw = baseline

        if baseline:
            _LOGGER.info(
                "Baseline load calculated: %d hours, avg=%.2f kW",
                len(baseline),
                sum(baseline.values()) / len(baseline),
            )

        return baseline

    async def _evaluate_preconditioning(self) -> None:
        """Evaluate pre-conditioning before demand window.

        Pre-heats or pre-cools the home before the demand window starts,
        using battery power to shift thermal load away from peak pricing.

        Issue #63 Phase 4.
        """
        if self._thermal_manager is None:
            return

        if not self._thermal_manager.is_enabled():
            return

        # Get demand window times
        from .const import CONF_DEMAND_WINDOW_START, DEFAULT_DEMAND_WINDOW_START

        dw_start = self._parse_time_option(
            CONF_DEMAND_WINDOW_START, DEFAULT_DEMAND_WINDOW_START
        )
        dw_end = self._parse_time_option(
            CONF_DEMAND_WINDOW_END, DEFAULT_DEMAND_WINDOW_END
        )

        # Evaluate pre-conditioning
        is_active, setpoint_offset = self._thermal_manager.evaluate_preconditioning(
            data=self.data,
            now=datetime.now(),
            demand_window_start=dw_start,
            demand_window_end=dw_end,
        )

        if is_active and setpoint_offset != 0.0:
            _LOGGER.info(
                "Pre-conditioning active: offset=%.1f°C",
                setpoint_offset,
            )
            # Apply setpoint adjustment to controlled climate entities
            await self._thermal_manager.async_apply_climate_control(
                self.data, setpoint_offset
            )

    async def _evaluate_solar_taper(self) -> None:
        """Evaluate solar tapering to consume excess solar.

        Adjusts HVAC setpoints to consume excess solar generation
        instead of exporting to grid at low FIT prices.

        Issue #141 Phase 5.
        """
        if self._thermal_manager is None:
            return

        if not self._thermal_manager.is_enabled():
            return

        if not self._thermal_manager.is_solar_taper_enabled():
            return

        # Get excess solar and load shift signal
        excess_solar_kw = self.data.current_excess_rate_kw
        load_shift_signal = self.data.load_shift_signal

        # Evaluate solar taper
        is_active, setpoint_offset = self._thermal_manager.evaluate_solar_taper(
            data=self.data,
            excess_solar_kw=excess_solar_kw,
            load_shift_signal=load_shift_signal,
        )

        if is_active and setpoint_offset != 0.0:
            _LOGGER.info(
                "Solar taper active: excess=%.2f kW, offset=%.1f°C",
                excess_solar_kw,
                setpoint_offset,
            )
            # Apply setpoint adjustment to controlled climate entities
            await self._thermal_manager.async_apply_climate_control(
                self.data, setpoint_offset
            )

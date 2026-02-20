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
    DEFAULT_DEMAND_WINDOW_END,
    SWITCH_DEFAULTS,
)
from .coordinator_data import CoordinatorData

if TYPE_CHECKING:
    from .battery_controller import BatteryController
    from .computation_engine import ComputationEngine
    from .cost_tracker import CostTracker
    from .notification_service import NotificationService
    from .state_machine import StateMachine
    from .state_reader import StateReader


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
        """
        self.data.grid_import_cost = 0.0
        self.data.grid_export_revenue = 0.0
        self.data.battery_savings = 0.0
        self.data.battery_charge_cost = 0.0
        self.data.target_reached_today = False
        self._notify_listeners()
        _LOGGER.info("Midnight reset: cost accumulators and target flag cleared")

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

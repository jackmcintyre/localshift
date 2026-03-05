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

from .const import (
    CONF_BATTERY_TARGET,
    CONF_DEMAND_WINDOW_END,
    CONF_NOTIFY_SERVICE,
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    CONF_PRICING_PRICE_SPIKE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_TESLEMETRY_LOAD_POWER,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_DEMAND_WINDOW_END,
    SWITCH_DEFAULTS,
    BatteryMode,
)
from .coordinator_data import CoordinatorData
from .evaluation_dispatcher import EvaluationDispatcher
from .forecast_bootstrapper import ForecastBootstrapper
from .learning_orchestrator import LearningOrchestrator
from .subscription_manager import SubscriptionManager

if TYPE_CHECKING:
    from .battery_controller import BatteryController
    from .computation_engine import ComputationEngine
    from .cost_tracker import CostTracker
    from .entity_validator import EntityValidator
    from .notification_service import NotificationService
    from .state_machine import StateMachine
    from .state_reader import StateReader


_LOGGER = logging.getLogger(__name__)

# Tiered periodic task intervals (Issue #291)
# FAST: Time-sensitive control tasks (1 minute)
PERIODIC_INTERVAL_FAST = timedelta(minutes=1)
# MEDIUM: Learning and monitoring tasks (5 minutes)
PERIODIC_INTERVAL_MEDIUM = timedelta(minutes=5)
# SLOW: Slow-changing data tasks (30 minutes)
PERIODIC_INTERVAL_SLOW = timedelta(minutes=30)

# Legacy interval kept for backward compatibility
PERIODIC_INTERVAL = PERIODIC_INTERVAL_FAST

# How often to save learning data to storage (prevents data loss on restart)
LEARNING_SAVE_INTERVAL = timedelta(minutes=5)

# Solcast startup retry configuration
SOLCAST_STARTUP_RETRY_DELAY = timedelta(seconds=30)
SOLCAST_MAX_STARTUP_RETRIES = 3

# Stale price detection threshold (Issue #291)
# If price sensor hasn't updated in this time, trigger state machine evaluation
STALE_PRICE_THRESHOLD = timedelta(minutes=10)


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

        # Decision outcome tracker for learning system (Issue #170 Phase 1)
        self.decision_tracker = None

        # Parameter optimizer for learning system (Issue #170 Phase 2)
        self.param_optimizer = None

        # Pattern analyzer for learning system (Issue #170 Phase 3)
        self.pattern_analyzer = None

        # Optimization controller for learning system (Issue #170 Phase 4)
        self.optimization_controller = None

        # Orchestrators
        self._learning_orchestrator: LearningOrchestrator | None = None
        self._forecast_bootstrapper: ForecastBootstrapper | None = None
        self._evaluation_dispatcher: EvaluationDispatcher | None = None
        self._subscription_manager: SubscriptionManager | None = None

        # Solar energy tracking for backfill (Issue #513)
        self._last_solar_power_kw: float = 0.0
        self._last_solar_power_timestamp: datetime | None = None

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

        self._entity_validator = EntityValidator(self.hass, self._get_entity_id)
        self._state_reader = StateReader(self.hass, self.entry, self._entity_validator)
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
            self._entity_validator,
            decision_tracker=None,  # Will be set after tracker is initialized
        )

        self._learning_orchestrator = LearningOrchestrator(
            self.hass,
            self.entry,
            self.get_switch_state,
        )
        await self._learning_orchestrator.async_initialize()

        self.decision_tracker = self._learning_orchestrator.decision_tracker
        self.param_optimizer = self._learning_orchestrator.param_optimizer
        self.pattern_analyzer = self._learning_orchestrator.pattern_analyzer
        self.optimization_controller = (
            self._learning_orchestrator.optimization_controller
        )

        # Initialize solar forecast accuracy tracker (Issue #378)
        from .computation_engine_lib.solar_accuracy import SolarAccuracyTracker

        self.solar_accuracy_tracker = SolarAccuracyTracker(
            self.hass, self.entry.entry_id
        )
        await self.solar_accuracy_tracker.async_load()

        # Wire solar accuracy tracker to computation engine for forecasting (Issue #513)
        if self._computation_engine is not None:
            self._computation_engine.set_solar_accuracy_tracker(
                self.solar_accuracy_tracker
            )

        if self._learning_orchestrator is not None:
            self._learning_orchestrator.attach_state_machine(self._state_machine)

        # Set battery target SOC in coordinator data for decision scoring
        self.data.battery_target_soc = float(
            self.get_option(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET)
        )

        # Collect all external entity IDs to watch
        # NOTE: We don't watch CONF_TESLEMETRY_ALLOW_EXPORT because we change it
        # programmatically and don't want to trigger re-evaluation loops
        #
        # Issue #284: Only monitor entities that affect mode decisions:
        # - Price entities: trigger mode decisions when prices change
        # - Solcast entities: trigger forecast recomputation when solar forecast changes
        # - SOC: triggers target stop when battery reaches target
        #
        # NOT monitored (handled by periodic tick instead):
        # - GRID/BATTERY/SOLAR/LOAD_POWER: only used for cost tracking (1-min tick)
        # - OPERATION_MODE/BACKUP_RESERVE: outputs, health-checked in 1-min tick
        monitored_entities = [
            # Price entities - trigger mode decisions on price changes
            self._get_entity_id(CONF_PRICING_GENERAL_PRICE),
            self._get_entity_id(CONF_PRICING_FEED_IN_PRICE),
            self._get_entity_id(CONF_PRICING_GENERAL_FORECAST),
            self._get_entity_id(CONF_PRICING_FEED_IN_FORECAST),
            self._get_entity_id(CONF_PRICING_PRICE_SPIKE),
            # Solcast entities - trigger forecast recomputation
            self._get_entity_id(CONF_SOLCAST_FORECAST_TODAY),
            self._get_entity_id(CONF_SOLCAST_FORECAST_TOMORROW),
            # SOC - trigger target stop when battery reaches target
            # self._get_entity_id(CONF_TESLEMETRY_SOC),  # REMOVED (Issue #524) - handled by 1-min periodic tick instead
        ]

        self._evaluation_dispatcher = EvaluationDispatcher(
            self.hass,
            self._get_entity_id,
            self._read_all_external_state,
            self._notify_listeners,
            self._evaluate_state_machine,
            self._state_machine,
            STALE_PRICE_THRESHOLD,
        )

        dw_end = self._parse_time_option(
            CONF_DEMAND_WINDOW_END,
            DEFAULT_DEMAND_WINDOW_END,
        )
        self._subscription_manager = SubscriptionManager(
            self.hass,
            self._handle_state_change,
            self._handle_fast_tick,
            self._handle_medium_tick,
            self._handle_slow_tick,
            self._handle_midnight_reset,
            self._handle_daily_summary,
            self._handle_learning_save,
            PERIODIC_INTERVAL_FAST,
            PERIODIC_INTERVAL_MEDIUM,
            PERIODIC_INTERVAL_SLOW,
            LEARNING_SAVE_INTERVAL,
        )
        self._subscription_manager.start(monitored_entities, dw_end)

        # Read initial state and compute
        self._read_all_external_state()

        # Issue #349: Check if automation is ready before proceeding
        # This validates that all required inputs are populated
        if self._state_reader is not None:
            self._state_reader.check_automation_ready(self.data)

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

        self._forecast_bootstrapper = ForecastBootstrapper(
            self.hass,
            self.data,
            self._get_entity_id,
            self._read_all_external_state,
            self._compute_derived_values,
            self._notify_listeners,
            self._evaluate_state_machine,
            SOLCAST_STARTUP_RETRY_DELAY,
            SOLCAST_MAX_STARTUP_RETRIES,
        )

        # Wait for Solcast data to be ready before computing forecasts
        # This prevents errors when Solcast hasn't initialized yet
        await self._forecast_bootstrapper.wait_for_solcast_and_compute()

        # Refresh weather forecast (startup catch-up)
        await self._refresh_weather_forecast()

        # Startup grace: wait 30 s for entities to populate before acting
        self._state_machine.set_startup_grace(30)

        inferred_mode = self._state_machine.infer_current_hardware_mode(self.data)

        _LOGGER.info(
            "LocalShift coordinator started, monitoring %d entities, inferred mode: %s",
            len(monitored_entities),
            inferred_mode.value,
        )

        # Log if forecast was computed during startup
        if (
            self._forecast_bootstrapper is not None
            and self._forecast_bootstrapper.forecast_computed_on_startup
        ):
            _LOGGER.info(
                "Initial forecast computed successfully on startup (after %d Solcast retries)",
                self._forecast_bootstrapper.retry_count,
            )

    async def async_stop(self) -> None:
        """Stop listening and clean up.

        Saves all learning data to storage before shutdown to prevent data loss.
        """
        if self._subscription_manager is not None:
            self._subscription_manager.stop()

        # Save all learning data to storage before shutdown
        await self._save_learning_data()

        # (backlog-med-004) Clean up historical load cache on shutdown
        if self._computation_engine is not None:
            self._computation_engine.clear_historical_cache()

        _LOGGER.info("LocalShift coordinator stopped")

    async def _save_learning_data(self) -> None:
        """Save all learning system data to storage.

        Called on shutdown and periodically to prevent data loss.
        """
        if self._learning_orchestrator is not None:
            await self._learning_orchestrator.async_save_all()

    @callback
    def _handle_learning_save(self, now: datetime) -> None:
        """Periodic save of learning data to prevent data loss on restart.

        Fires every 5 minutes to ensure data is persisted even if HA
        restarts unexpectedly.
        """
        if self._learning_orchestrator is not None:
            self._learning_orchestrator.handle_periodic_save()

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

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    @callback
    def _handle_state_change(self, _event: Event) -> None:
        """Handle a state change from a monitored entity."""
        if self._evaluation_dispatcher is None:
            return

        self._evaluation_dispatcher.on_state_change(_event)

    @callback
    def _handle_periodic_tick(self, now: datetime) -> None:
        """Handle the 1-minute periodic re-evaluation.

        DEPRECATED: This method is kept for backward compatibility.
        New tiered handlers (_handle_fast_tick, _handle_medium_tick, _handle_slow_tick)
        are used instead. See async_start() for timer subscriptions.
        """
        # Delegate to fast tick for backward compatibility
        self._handle_fast_tick(now)

    @callback
    def _handle_fast_tick(self, now: datetime) -> None:
        """Handle FAST tier periodic tasks (1 minute).

        Time-sensitive control tasks that need minute-level accuracy:
        - Cost accumulation (power × time needs minute accuracy)
        - Stale price check (safety net if price sensor stops updating)
        """
        # Read raw entity values now — needed for cost accumulation
        self._read_all_external_state()

        # Cost accumulation uses the raw state we just read (sync, no lock needed)
        self._accumulate_costs()

        if self._evaluation_dispatcher is not None:
            self._evaluation_dispatcher.on_fast_tick(now)

    @callback
    def _handle_medium_tick(self, now: datetime) -> None:
        """Handle MEDIUM tier periodic tasks (5 minutes).

        Learning and monitoring tasks that don't need minute-level accuracy:
        - Entity health check
        - Load data refresh
        - Decision backfill
        - Weather learning
        - Baseline calculation
        """
        # Read raw entity values
        self._read_all_external_state()

        # Check entity health
        self._check_entity_health()

        # Refresh load data (historical and recent)
        if self._computation_engine is not None:
            load_entity_id = self._get_entity_id(CONF_TESLEMETRY_LOAD_POWER)
            self.hass.async_create_task(
                self._computation_engine.async_get_recent_load_1hr(load_entity_id),
                "localshift_fetch_recent_load",
            )
            self.hass.async_create_task(
                self._computation_engine.async_get_historical_hourly_averages(
                    load_entity_id
                ),
                "localshift_fetch_historical_load",
            )

        if self._learning_orchestrator is not None:
            self._learning_orchestrator.update_medium_tick(self.data)

        # Backfill solar forecast accuracy for completed periods (Issue #378)
        if (
            hasattr(self, "solar_accuracy_tracker")
            and self.solar_accuracy_tracker is not None
        ):
            # Record forecasts for upcoming periods (this would happen when slots are created elsewhere)
            # But we can't do it here without major refactor

            # For updating solar bias metrics from tracker and handling backfills if needed
            # (backfills would happen somewhere when we have historical energy data)
            pass

        # Update solar bias metrics from tracker (Issue #378)
        if (
            hasattr(self, "solar_accuracy_tracker")
            and self.solar_accuracy_tracker is not None
        ):
            self.data.solar_bias_metrics = self.solar_accuracy_tracker.metrics.to_dict()
            self.data.solar_forecast_accuracy = (
                self.solar_accuracy_tracker.metrics.accuracy
            )

        # Learn from current temperature/load for weather correlation
        if self._computation_engine is not None:
            self.hass.async_create_task(
                self._computation_engine.async_learn_weather_sample(self.data),
                "localshift_weather_learning",
            )

        _LOGGER.debug("Medium tick completed: learning and monitoring tasks")

    @callback
    def _handle_slow_tick(self, now: datetime) -> None:
        """Handle SLOW tier periodic tasks (30 minutes).

        Slow-changing data tasks:
        - Weather forecast refresh
        - Forecast accuracy metrics
        - Forecast history save
        """
        # Refresh temperature forecast from weather entity (Issue #135)
        if self._computation_engine is not None:
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

        # Backfill actual solar energy for completed periods (Issue #513)
        self._backfill_solar_actual()

        _LOGGER.debug("Slow tick completed: weather forecast and accuracy metrics")

    def _backfill_solar_actual(self) -> None:
        """Backfill actual solar energy for completed 30-min periods.

        Calculates energy produced since last tick using integrated power,
        then calls backfill_actual() on the tracker for completed periods.
        """
        if not hasattr(self, "solar_accuracy_tracker"):
            return

        tracker = getattr(self, "solar_accuracy_tracker", None)
        if tracker is None:
            return

        from homeassistant.util import dt as dt_util

        now = dt_util.now()
        current_power = self.data.solar_power_kw

        if self._last_solar_power_timestamp is None:
            self._last_solar_power_timestamp = now
            self._last_solar_power_kw = current_power
            return

        time_delta_hours = (
            now - self._last_solar_power_timestamp
        ).total_seconds() / 3600.0
        if time_delta_hours < 0.01:
            return

        avg_power_kw = (self._last_solar_power_kw + current_power) / 2.0
        energy_kwh = avg_power_kw * time_delta_hours

        if energy_kwh > 0.001 and current_power > 0.01:
            now_local = now.astimezone()
            period_start = now_local.replace(
                minute=(now_local.minute // 30) * 30, second=0, microsecond=0
            )
            tracker.backfill_actual(period_start, energy_kwh)

        self._last_solar_power_timestamp = now
        self._last_solar_power_kw = current_power

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

        if self._learning_orchestrator is not None:
            self._learning_orchestrator.handle_midnight_reset(self.data)

        self._notify_listeners()
        _LOGGER.info("Midnight reset: cost accumulators and target flag")

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

        # Run shadow optimizer after legacy forecast is computed (Issue #403 Phase 1)
        # This is non-invasive - it only populates shadow_* fields in CoordinatorData

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

    def reset_entity_tracking_on_options_change(self) -> None:
        """Reset entity tracking when options change.

        This is called when the user reconfigures the integration via options flow.
        It resets tracking for entities that may have changed (e.g., weather_entity)
        to clear broken status and allow recovery without restart.
        """
        if self._entity_validator is None:
            return

        # Reset tracking for weather entity (most commonly reconfigured optional entity)
        from .const import CONF_WEATHER_ENTITY

        self._entity_validator.reset_entity_tracking(CONF_WEATHER_ENTITY)

        _LOGGER.info("Reset entity tracking for options change")

    def reschedule_daily_summary_timer(self) -> None:
        """Reschedule the daily summary timer with current demand_window_end.

        Called when options are updated to pick up new notification time
        without requiring a restart.
        """
        if self._subscription_manager is None:
            return

        dw_end = self._parse_time_option(
            CONF_DEMAND_WINDOW_END,
            DEFAULT_DEMAND_WINDOW_END,
        )
        self._subscription_manager.reschedule_daily_summary(dw_end)

    async def _evaluate_state_machine(self) -> None:
        """Compare desired mode with commanded mode and execute transitions."""
        if self._state_machine is not None and self._computation_engine is not None:
            await self._state_machine.evaluate_state_machine(
                self.data,
                self._computation_engine,
                read_state_func=self._read_all_external_state,
                notify_func=self._notify_listeners,
                check_automation_ready_func=self._state_reader.check_automation_ready
                if self._state_reader is not None
                else None,
            )

    # ------------------------------------------------------------------
    # Battery mode control (for select entity - Issue #382)
    # ------------------------------------------------------------------

    async def async_set_battery_mode(self, mode: BatteryMode) -> bool:
        """Set battery to a specific mode.

        Used by the select entity for manual mode control.
        Returns True if successful, False otherwise.

        Args:
            mode: The BatteryMode to set.

        Returns:
            True if the mode was set successfully, False otherwise.
        """
        if self._battery_controller is None:
            _LOGGER.error("Battery controller not available")
            return False

        dry_run = self.get_switch_state("dry_run")
        success = False

        if mode == BatteryMode.SELF_CONSUMPTION:
            success = await self._battery_controller.set_self_consumption(
                self.data, dry_run
            )
        elif mode == BatteryMode.GRID_CHARGING:
            battery_target = float(
                self.get_option(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET)
            )
            success = await self._battery_controller.set_force_charge(
                self.data, dry_run, target_soc=battery_target
            )
        elif mode == BatteryMode.BOOST_CHARGING:
            success = await self._battery_controller.set_boost_charge(
                self.data, dry_run
            )
        elif mode == BatteryMode.SPIKE_DISCHARGE:
            # Check if conservative mode is enabled and use spike_reserve_soc if available
            reserve_soc = (
                self.data.spike_reserve_soc
                if self.data.spike_in_conservative_mode
                else None
            )
            success = await self._battery_controller.set_force_discharge(
                self.data, dry_run, reserve_soc=reserve_soc
            )
        elif mode == BatteryMode.PROACTIVE_EXPORT:
            success = await self._battery_controller.set_proactive_export(
                self.data, dry_run
            )
        else:
            _LOGGER.warning("Unsupported battery mode: %s", mode)
            return False

        if success:
            _LOGGER.info("Battery mode set to %s (dry_run=%s)", mode.value, dry_run)
            # Update commanded mode in state machine
            if self._state_machine is not None:
                self._state_machine.set_commanded_mode(mode)
        else:
            _LOGGER.error("Failed to set battery mode to %s", mode.value)

        return success

    async def async_set_self_consumption(self) -> None:
        """Set battery to self consumption mode.

        Used by automation switch when automation is disabled.
        """
        if self._battery_controller is not None:
            await self._battery_controller.set_self_consumption(self.data, False)

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

    async def _refresh_weather_forecast(self) -> None:
        """Refresh temperature forecast from weather entity.

        Uses the modern weather.get_forecasts service (HA 2024.3+) with caching.
        Updates CoordinatorData with the latest forecast for use by sensors.
        """
        if self._computation_engine is None:
            _LOGGER.debug(
                "Computation engine not initialized, skipping weather forecast"
            )
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

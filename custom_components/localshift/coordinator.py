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

from .computation_engine_lib.decision_outcome_tracker import DecisionOutcomeTracker
from .computation_engine_lib.optimization_controller import OptimizationController
from .computation_engine_lib.parameter_optimizer import ParameterOptimizer
from .computation_engine_lib.pattern_analyzer import PatternAnalyzer
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
        self._unsub_state: CALLBACK_TYPE | None = None
        self._unsub_timer: CALLBACK_TYPE | None = (
            None  # Legacy - kept for compatibility
        )
        self._unsub_midnight: CALLBACK_TYPE | None = None
        self._unsub_daily_summary: CALLBACK_TYPE | None = None
        self._unsub_learning_save: CALLBACK_TYPE | None = None
        # Tiered periodic task timers (Issue #291)
        self._unsub_timer_fast: CALLBACK_TYPE | None = None
        self._unsub_timer_medium: CALLBACK_TYPE | None = None
        self._unsub_timer_slow: CALLBACK_TYPE | None = None
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
        self.decision_tracker: DecisionOutcomeTracker | None = None

        # Parameter optimizer for learning system (Issue #170 Phase 2)
        self.param_optimizer: ParameterOptimizer | None = None

        # Pattern analyzer for learning system (Issue #170 Phase 3)
        self.pattern_analyzer: PatternAnalyzer | None = None

        # Optimization controller for learning system (Issue #170 Phase 4)
        self.optimization_controller: OptimizationController | None = None

        # Pattern analysis tracking
        self._last_pattern_analysis: datetime | None = None
        self._days_since_pattern_analysis: int = 0

        # Solcast startup retry tracking
        self._solcast_retry_count: int = 0
        self._solcast_ready: bool = False
        self._forecast_computed_on_startup: bool = False

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

        # Initialize decision outcome tracker for learning system (Issue #170 Phase 1)
        self.decision_tracker = DecisionOutcomeTracker(self.hass, self.entry.entry_id)
        await self.decision_tracker.async_load()

        # Initialize parameter optimizer for learning system (Issue #170 Phase 2)
        self.param_optimizer = ParameterOptimizer(self.hass, self.entry.entry_id)
        await self.param_optimizer.async_load()

        # Initialize pattern analyzer for learning system (Issue #170 Phase 3)
        self.pattern_analyzer = PatternAnalyzer(self.hass, self.entry.entry_id)
        await self.pattern_analyzer.async_load()

        # Initialize optimization controller for learning system (Issue #170 Phase 4)
        # Requires all three learning components to be initialized first
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

            # Sync learning enabled state from switch
            from .const import SWITCH_ENABLE_LEARNING

            learning_enabled = self.get_switch_state(SWITCH_ENABLE_LEARNING)
            self.optimization_controller.set_learning_enabled(learning_enabled)

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

        # Wire the decision tracker to the state machine
        self._state_machine._decision_tracker = self.decision_tracker

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

        # Subscribe to state changes
        self._unsub_state = async_track_state_change_event(
            self.hass, monitored_entities, self._handle_state_change
        )

        # Tiered periodic task timers (Issue #291)
        # FAST: 1-minute - time-sensitive control tasks
        self._unsub_timer_fast = async_track_time_interval(
            self.hass, self._handle_fast_tick, PERIODIC_INTERVAL_FAST
        )
        # MEDIUM: 5-minute - learning and monitoring tasks
        self._unsub_timer_medium = async_track_time_interval(
            self.hass, self._handle_medium_tick, PERIODIC_INTERVAL_MEDIUM
        )
        # SLOW: 30-minute - slow-changing data tasks
        self._unsub_timer_slow = async_track_time_interval(
            self.hass, self._handle_slow_tick, PERIODIC_INTERVAL_SLOW
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

        # Periodic learning data save (every 5 minutes) to prevent data loss on restart
        self._unsub_learning_save = async_track_time_interval(
            self.hass,
            self._handle_learning_save,
            LEARNING_SAVE_INTERVAL,
        )

        # Read initial state and compute
        self._read_all_external_state()

        # Issue #349: Check if automation is ready before proceeding
        # This validates that all required inputs are populated
        # Issue #551: Suppress warning during startup grace to reduce log noise
        if self._state_reader is not None:
            self._state_reader.check_automation_ready(self.data, suppress_warning=True)

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
        if self._forecast_computed_on_startup:
            _LOGGER.info(
                "Initial forecast computed successfully on startup (after %d Solcast retries)",
                self._solcast_retry_count,
            )

    async def async_stop(self) -> None:
        """Stop listening and clean up.

        Saves all learning data to storage before shutdown to prevent data loss.
        """
        # Unsubscribe all timers (including tiered timers from Issue #291)
        for unsub in (
            self._unsub_state,
            self._unsub_timer,  # Legacy
            self._unsub_midnight,
            self._unsub_daily_summary,
            self._unsub_learning_save,
            self._unsub_timer_fast,
            self._unsub_timer_medium,
            self._unsub_timer_slow,
        ):
            if unsub:
                unsub()
        self._unsub_state = None
        self._unsub_timer = None
        self._unsub_midnight = None
        self._unsub_daily_summary = None
        self._unsub_learning_save = None
        self._unsub_timer_fast = None
        self._unsub_timer_medium = None
        self._unsub_timer_slow = None

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
        saved_components = []

        # Save decision outcomes
        if self.decision_tracker is not None:
            try:
                await self.decision_tracker.async_save()
                saved_components.append(
                    f"decisions:{self.decision_tracker.completed_count}"
                )
            except Exception as e:
                _LOGGER.error("Failed to save decision tracker: %s", e)

        # Save parameter optimizer
        if self.param_optimizer is not None:
            try:
                await self.param_optimizer.async_save()
                saved_components.append("param_optimizer")
            except Exception as e:
                _LOGGER.error("Failed to save parameter optimizer: %s", e)

        # Save pattern analyzer
        if self.pattern_analyzer is not None:
            try:
                await self.pattern_analyzer.async_save()
                saved_components.append("pattern_analyzer")
            except Exception as e:
                _LOGGER.error("Failed to save pattern analyzer: %s", e)

        # Save optimization controller
        if self.optimization_controller is not None:
            try:
                await self.optimization_controller.async_save()
                saved_components.append("optimization_controller")
            except Exception as e:
                _LOGGER.error("Failed to save optimization controller: %s", e)

        if saved_components:
            _LOGGER.info("Learning data saved: %s", ", ".join(saved_components))

    @callback
    def _handle_learning_save(self, now: datetime) -> None:
        """Periodic save of learning data to prevent data loss on restart.

        Fires every 5 minutes to ensure data is persisted even if HA
        restarts unexpectedly.
        """
        self.hass.async_create_task(
            self._save_learning_data(),
            "localshift_periodic_learning_save",
        )

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
        Also updates forecast_ready and forecast_status in CoordinatorData (Issue #319).
        """
        # Check if today's forecast has valid data
        today_entity = self._get_entity_id(CONF_SOLCAST_FORECAST_TODAY)
        today_state = self.hass.states.get(today_entity)

        if today_state is None:
            _LOGGER.debug("Solcast today entity not found: %s", today_entity)
            self.data.forecast_ready = False
            self.data.forecast_status = "stale"
            return False

        if today_state.state in ("unknown", "unavailable", None, ""):
            _LOGGER.debug(
                "Solcast today entity state is %s, waiting for data",
                today_state.state,
            )
            self.data.forecast_ready = False
            self.data.forecast_status = "stale"
            return False

        # Check if the forecast attribute has actual forecast data
        forecast_data = today_state.attributes.get("detailedForecast")
        if not forecast_data or not isinstance(forecast_data, list):
            _LOGGER.debug("Solcast today forecast attribute is empty or invalid")
            self.data.forecast_ready = False
            self.data.forecast_status = "partial"
            return False

        # Check if we have at least some forecast entries
        if len(forecast_data) == 0:
            _LOGGER.debug("Solcast today forecast has no entries")
            self.data.forecast_ready = False
            self.data.forecast_status = "partial"
            return False

        # Check if we have enough entries for meaningful forecasting (at least 4 hours = 8 entries)
        if len(forecast_data) < 8:
            _LOGGER.debug(
                "Solcast today forecast has only %d entries (need 8+ for full forecast)",
                len(forecast_data),
            )
            self.data.forecast_ready = True  # Partial but usable
            self.data.forecast_status = "partial"
            return True

        _LOGGER.info(
            "Solcast forecast data is ready (%d entries for today)",
            len(forecast_data),
        )
        self.data.forecast_ready = True
        self.data.forecast_status = "ready"
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

            # Trigger immediate state machine evaluation for fast battery control on startup
            await self._evaluate_state_machine()

            self._forecast_computed_on_startup = True
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

            # Trigger state machine evaluation even with incomplete data
            await self._evaluate_state_machine()

            self._forecast_computed_on_startup = True
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

        # Skip state machine evaluation during startup grace period
        # This prevents errors when entities haven't populated yet (Issue #551)
        if self._is_in_startup_grace():
            _LOGGER.debug(
                "Skipping state machine evaluation during startup grace period"
            )
            return

        # Check for stale price sensor (Issue #291)
        # If price hasn't updated in 10+ minutes, trigger state machine evaluation
        stale_price = self._check_stale_price()

        # Trigger state machine evaluation if price is stale
        # This is a safety net - normally state changes trigger evaluation
        if stale_price:
            _LOGGER.info("Stale price detected, triggering state machine evaluation")
            self.hass.async_create_task(
                self._evaluate_state_machine(),
                "localshift_evaluate_stale_price",
            )
        else:
            # Trigger state machine evaluation periodically (every minute)
            # This replaces the reactive SOC triggers (Issue #524)
            self.hass.async_create_task(
                self._evaluate_state_machine(),
                "localshift_evaluate_periodic",
            )

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

        # Skip expensive operations during startup grace period
        # This prevents errors when entities haven't populated yet (Issue #551)
        if self._is_in_startup_grace():
            _LOGGER.debug("Skipping medium tick operations during startup grace period")
            return

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

        # Backfill decision outcomes and update performance metrics (Issue #170 Phase 1)
        if self.decision_tracker is not None:
            self.decision_tracker.backfill_outcomes(self.data)

            self.data.performance_metrics = self.decision_tracker.get_daily_summary()
            self.data.recent_decision_log = self.decision_tracker.get_decision_log(
                limit=20
            )

            # Save decisions if backfill occurred
            if self.decision_tracker.save_pending:
                self.hass.async_create_task(
                    self.decision_tracker.async_save(),
                    "localshift_save_decision_outcomes",
                )
                self.decision_tracker.clear_save_pending()

            # Run parameter optimization (Issue #170 Phase 2)
            if self.param_optimizer is not None:
                completed_count = len(self.decision_tracker._completed_decisions)
                if self.param_optimizer.should_update(completed_count):
                    decisions = self.decision_tracker.get_recent_decisions(hours=168)
                    current_7d_score = (
                        self.data.performance_metrics.avg_decision_score_7d
                    )
                    self.data.adaptive_params = self.param_optimizer.optimize(
                        decisions, current_7d_score
                    )

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

        # Apply contextual overrides from OptimizationController (Issue #449 Phase 7)
        # This merges real-time adjustments on top of Thompson-sampled base params
        if self.optimization_controller is not None:
            self.data.adaptive_params = self.optimization_controller.evaluate(self.data)
            # Update observability fields for sensors
            self.data.optimization_weights = (
                self.optimization_controller.weights.to_dict()
            )
            # Get and update active adjustments
            active_adjustments = self.optimization_controller.get_active_adjustments()
            self.data.contextual_adjustments_active = active_adjustments
            if active_adjustments:
                _LOGGER.debug(
                    "Contextual overrides applied: %d adjustments active",
                    len(active_adjustments),
                )

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

    def _is_in_startup_grace(self) -> bool:
        """Check if we're still in the startup grace period.

        Returns True if the state machine has an active startup grace period,
        False otherwise. Used to skip expensive operations during initialization
        when entities may not be populated yet (Issue #551).
        """
        if self._state_machine is None:
            return False
        return self._state_machine._startup_grace_until is not None

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

    def _check_stale_price(self) -> bool:
        """Check if price sensor hasn't updated in STALE_PRICE_THRESHOLD.

        This is a safety net that triggers state machine evaluation if the
        price sensor stops updating. Normally, state changes trigger evaluation
        automatically, but if the sensor becomes stale, we need to catch it.

        Returns:
            True if price sensor is stale, False otherwise.
        """
        price_entity = self._get_entity_id(CONF_PRICING_GENERAL_PRICE)
        price_state = self.hass.states.get(price_entity)

        if price_state is None:
            _LOGGER.debug("Price entity not found: %s", price_entity)
            return False

        if price_state.state in ("unknown", "unavailable", None, ""):
            _LOGGER.debug("Price entity state is invalid: %s", price_state.state)
            return False

        # Check last_updated time
        if price_state.last_updated:
            from homeassistant.util import dt as dt_util

            now = dt_util.now()
            age = now - price_state.last_updated

            if age > STALE_PRICE_THRESHOLD:
                _LOGGER.warning(
                    "Price sensor %s is stale (last updated %s ago). "
                    "This may indicate an issue with the pricing integration.",
                    price_entity,
                    age,
                )
                return True

        return False

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

        # Save decision outcomes to storage (Issue #170 Phase 1)
        if self.decision_tracker is not None:
            self.hass.async_create_task(
                self.decision_tracker.async_save(),
                "localshift_save_decision_outcomes",
            )

        # Save parameter optimizer state (Issue #170 Phase 2)
        if self.param_optimizer is not None:
            self.hass.async_create_task(
                self.param_optimizer.async_save(),
                "localshift_save_param_optimizer",
            )

        # Run weekly pattern analysis (Issue #170 Phase 3)
        # Analyze patterns every 7 days to generate bias corrections
        self._days_since_pattern_analysis += 1
        if (
            self.pattern_analyzer is not None
            and self.decision_tracker is not None
            and self._days_since_pattern_analysis >= 7
        ):
            self._days_since_pattern_analysis = 0
            self.hass.async_create_task(
                self._run_pattern_analysis(),
                "localshift_pattern_analysis",
            )

        # Save pattern analyzer state (Issue #170 Phase 3)
        if self.pattern_analyzer is not None:
            self.hass.async_create_task(
                self.pattern_analyzer.async_save(),
                "localshift_save_pattern_analyzer",
            )

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

    async def _run_pattern_analysis(self) -> None:
        """Run weekly pattern analysis to generate bias corrections.

        Issue #170 Phase 3: Analyzes decision outcomes by dimension buckets
        to identify systematic biases and generate corrections for the
        parameter optimizer.
        """
        if self.pattern_analyzer is None or self.decision_tracker is None:
            return

        # Get recent decisions for analysis (last 30 days)
        decisions = self.decision_tracker.get_recent_decisions(hours=720)

        if len(decisions) < 50:
            _LOGGER.info(
                "Pattern analysis skipped: only %d decisions (need 50+)",
                len(decisions),
            )
            return

        # Run analysis (PatternAnalyzer.analyze only takes decisions)
        report = self.pattern_analyzer.analyze(decisions)

        # Update coordinator data with results
        self.data.pattern_report_summary = report.get_summary()
        self.data.active_bias_corrections = [
            bc.to_dict() for bc in report.biases_detected
        ]

        # Update learning status based on data quality
        total_samples = report.data_points_analyzed
        if total_samples >= 100:
            self.data.learning_status = "optimizing"
        elif total_samples >= 50:
            self.data.learning_status = "tuning"
        else:
            self.data.learning_status = "observing"

        # Pass bias corrections to parameter optimizer
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

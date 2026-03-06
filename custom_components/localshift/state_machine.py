"""State machine for battery mode evaluation and transitions."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import (
    BACKUP_RESERVE_MAX_VALID,
    CONF_BATTERY_TARGET,
    CONF_MANUAL_OVERRIDE_TIMEOUT,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_MANUAL_OVERRIDE_TIMEOUT,
    TESLEMETRY_EXPORT_BATTERY_OK,
    TESLEMETRY_EXPORT_PV_ONLY,
    BatteryMode,
)
from .coordinator_data import CoordinatorData

if TYPE_CHECKING:
    from .battery_controller import BatteryController
    from .computation_engine import ComputationEngine
    from .computation_engine_lib.decision_outcome_tracker import DecisionOutcomeTracker
    from .entity_validator import EntityValidator
    from .notification_service import NotificationService


_LOGGER = logging.getLogger(__name__)

# Tesla Override Detection Constants
# When Tesla activates Storm Watch, Grid Events, or VPP events, they set
# backup_reserve to 80% and operation_mode to self_consumption, ignoring
# external API commands until the event ends.
TESLA_OVERRIDE_RESERVE = 80.0  # Reserve level that indicates Tesla control
TESLA_OVERRIDE_RESERVE_TOLERANCE = 1.0  # Tolerance for reserve comparison
TESLA_OVERRIDE_COOLDOWN = timedelta(minutes=30)  # Extended cooldown during override


@dataclass
class ModeConfig:
    """Complete configuration for a mode transition.

    Contains all parameters needed for a mode, ensuring atomic updates
    to both Tesla hardware state and internal tracking state.
    """

    # Tesla hardware state (set via battery_controller)
    operation_mode: str
    backup_reserve: int | float
    export_mode: str
    grid_charging_allowed: bool

    # Internal tracking state (for health checks and sensors)
    self_consumption_reserve: float | None = None
    grid_charging_reserve: int | None = None
    proactive_export_reserve: float | None = None


class StateMachine:
    """Manages battery mode state machine evaluation and transitions."""

    # Minimum time a mode must be active before allowing transition (Issue #279)
    # This prevents rapid cycling that triggers the learning system's cycling penalty
    _MIN_MODE_DURATION: timedelta = timedelta(minutes=5)

    def __init__(
        self,
        battery_controller: BatteryController,
        notification_service: NotificationService,
        get_switch_state_func: Callable[[str], bool],
        get_option_func: Callable[[str, Any], Any],
        entity_validator: EntityValidator,
        decision_tracker: DecisionOutcomeTracker | None = None,
    ) -> None:
        """Initialize the state machine.

        Args:
            battery_controller: Battery controller instance
            notification_service: Notification service instance
            get_switch_state_func: Function to get switch states
            get_option_func: Function to get configuration options
            entity_validator: Entity validator instance for availability checks
            decision_tracker: Decision outcome tracker for learning system (Issue #170 Phase 1)
        """
        self._battery_controller = battery_controller
        self._notification_service = notification_service
        self._get_switch_state = get_switch_state_func
        self._get_option = get_option_func
        self.entity_validator = entity_validator
        self._decision_tracker = decision_tracker
        self._commanded_mode: BatteryMode = BatteryMode.SELF_CONSUMPTION
        self._mode_desired_since: dict[BatteryMode, datetime] = {}
        self._startup_grace_until: datetime | None = None
        self._evaluate_lock = asyncio.Lock()
        self._in_mode_transition: bool = False
        self._manual_override_set_at: datetime | None = None
        # Track when current mode was established (Issue #279)
        self._mode_established_at: datetime | None = None
        # Track dynamic reserve for PROACTIVE_EXPORT mode
        self._proactive_export_reserve: float | None = None
        # Track grid charging target reserve (clamped for Tesla firmware compatibility)
        self._grid_charging_reserve: int | None = None
        # Track self_consumption reserve (preserve_soc when set, otherwise 10)
        # Issue #375: Health check was using hardcoded 10, causing false mismatches
        self._self_consumption_reserve: float | None = None
        # Cooldown for health-check corrections (prevents command spam when
        # Teslemetry cloud lags in reflecting a legitimate transition)
        self._last_health_correction: datetime | None = None
        self._MIN_CORRECTION_INTERVAL = timedelta(minutes=5)
        # Flag to skip debounce on first transition after startup grace
        self._skip_next_debounce: bool = False
        # Track last successful transition for health check intelligence
        self._last_successful_transition: datetime | None = None
        # Grace period after successful transition before health checks trigger corrections
        self._TRANSITION_GRACE_PERIOD = timedelta(seconds=30)
        # Tesla Override Detection
        # When Tesla activates Storm Watch, Grid Events, or VPP events, they take
        # control of the Powerwall and ignore external API commands.
        self._tesla_override_detected: bool = False
        self._tesla_override_detected_at: datetime | None = None
        self._tesla_override_released_at: datetime | None = (
            None  # Track when Tesla released control
        )

    def _detect_tesla_override(self, data: CoordinatorData) -> bool:
        """Detect if Tesla has taken control (Storm Watch, Grid Event, VPP).

        Indicators: operation_mode=self_consumption, backup_reserve≈80%

        Args:
            data: Coordinator data with current hardware state

        Returns:
            True if Tesla override is detected, False otherwise.
        """
        # Tesla override signature: self_consumption mode with 80% reserve
        # This combination is set by Tesla during Storm Watch, Grid Events, VPP
        if data.operation_mode == "self_consumption":
            reserve = data.backup_reserve
            if (
                reserve is not None
                and abs(reserve - TESLA_OVERRIDE_RESERVE)
                < TESLA_OVERRIDE_RESERVE_TOLERANCE
            ):
                return True
        return False

    def is_tesla_override_active(self) -> bool:
        """Check if Tesla override is currently active.

        Returns:
            True if Tesla override is detected, False otherwise.
        """
        return self._tesla_override_detected

    def _get_mode_config(
        self, target: BatteryMode, data: CoordinatorData
    ) -> ModeConfig | None:
        """Get complete configuration for a mode transition.

        Returns None for MANUAL mode (no commands, no state tracking).
        """
        if target == BatteryMode.MANUAL:
            return None

        op_mode = "self_consumption"
        backup_reserve = 10.0
        export_mode = TESLEMETRY_EXPORT_PV_ONLY
        grid_charging = False
        sc_reserve: float | None = None
        gc_reserve: int | None = None
        pe_reserve: float | None = None

        if target in (BatteryMode.SELF_CONSUMPTION, BatteryMode.DEMAND_BLOCK):
            backup_reserve = (
                data.preserve_soc if data.preserve_soc is not None else 10.0
            )
            sc_reserve = backup_reserve

        elif target == BatteryMode.GRID_CHARGING:
            op_mode = "backup"
            battery_target = float(
                self._get_option(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET)
            )
            if battery_target <= BACKUP_RESERVE_MAX_VALID:
                backup_reserve = int(battery_target)
            elif battery_target >= 100:
                backup_reserve = 100
            else:
                backup_reserve = BACKUP_RESERVE_MAX_VALID
            grid_charging = True
            gc_reserve = int(backup_reserve)

        elif target == BatteryMode.BOOST_CHARGING:
            op_mode = "autonomous"
            backup_reserve = 100.0
            grid_charging = True

        elif target == BatteryMode.SPIKE_DISCHARGE:
            op_mode = "autonomous"
            min_target_soc = float(self._get_option("minimum_target_soc", 10.0))
            backup_reserve = (
                data.spike_reserve_soc
                if data.spike_in_conservative_mode
                and data.spike_reserve_soc is not None
                else min_target_soc
            )
            export_mode = TESLEMETRY_EXPORT_BATTERY_OK

        elif target == BatteryMode.PROACTIVE_EXPORT:
            op_mode = "autonomous"
            backup_reserve = max(4.0, data.soc - 5.0)
            export_mode = TESLEMETRY_EXPORT_BATTERY_OK
            pe_reserve = backup_reserve

        elif target == BatteryMode.HOLD:
            min_soc = float(self._get_option("minimum_target_soc", 10.0))
            # Issue #559 Root Cause 4: read fresh SOC from hardware to avoid stale
            # value during transitions.  The coordinator's cached SOC can lag by
            # minutes, causing the reserve to drop before the API updates.
            fresh_soc = self._battery_controller.read_fresh_soc()
            if fresh_soc is not None:
                backup_reserve = max(min_soc, fresh_soc)
                _LOGGER.info(
                    "HOLD mode: using fresh SOC %.1f%% (cached was %.1f%%), reserve=%.1f%%",
                    fresh_soc,
                    data.soc,
                    backup_reserve,
                )
            else:
                # Fallback to cached value if fresh read fails
                backup_reserve = max(min_soc, data.soc)
                _LOGGER.warning(
                    "HOLD mode: fresh SOC read failed, using cached %.1f%%, reserve=%.1f%%",
                    data.soc,
                    backup_reserve,
                )
            sc_reserve = backup_reserve

        return ModeConfig(
            operation_mode=op_mode,
            backup_reserve=backup_reserve,
            export_mode=export_mode,
            grid_charging_allowed=grid_charging,
            self_consumption_reserve=sc_reserve,
            grid_charging_reserve=gc_reserve,
            proactive_export_reserve=pe_reserve,
        )

    def infer_current_hardware_mode(self, data: CoordinatorData) -> BatteryMode:
        """Infer the current battery mode from Teslemetry hardware state.

        Used at startup to sync commanded mode so we don't issue
        a redundant command on the first evaluation.
        """
        if data.force_discharge_active:
            return BatteryMode.SPIKE_DISCHARGE
        if data.boost_charge_active:
            return BatteryMode.BOOST_CHARGING
        if data.force_charge_active:
            return BatteryMode.GRID_CHARGING
        return BatteryMode.SELF_CONSUMPTION

    def get_debounce_for_transition(
        self, _from_mode: BatteryMode, to_mode: BatteryMode
    ) -> timedelta:
        """Return the required debounce duration for a mode transition.

        All transitions are immediate except PROACTIVE_EXPORT (2-min debounce).
        Hysteresis in computation_engine prevents oscillation.
        """
        # (backlog-high-021) PROACTIVE_EXPORT needs debounce to prevent rapid cycling
        # when forecast oscillates near the export threshold
        if to_mode == BatteryMode.PROACTIVE_EXPORT:
            return timedelta(minutes=2)

        # All other transitions: immediate (hysteresis prevents oscillation)
        return timedelta(0)

    def _handle_startup_grace_period(
        self, data: CoordinatorData, now: datetime
    ) -> bool:
        """Handle startup grace period logic.

        Returns True if evaluation should return early.
        """
        if self._startup_grace_until is None:
            return False

        if now < self._startup_grace_until:
            _LOGGER.debug("State machine in startup grace period, skipping")
            return True

        self._startup_grace_until = None

        # Issue #349: Check if automation is ready before inferring mode
        # At startup, entities may not be populated, leading to incorrect mode inference
        if not data.automation_ready:
            _LOGGER.warning(
                "Startup grace ended but automation not ready - missing: %s. "
                "Staying in SELF_CONSUMPTION mode until inputs are valid.",
                ", ".join(data.automation_ready_missing)
                if data.automation_ready_missing
                else "unknown",
            )
            # Stay in SELF_CONSUMPTION mode - don't infer from potentially stale hardware state
            self._commanded_mode = BatteryMode.SELF_CONSUMPTION
            self._skip_next_debounce = True
            return True

        self._commanded_mode = self.infer_current_hardware_mode(data)
        # Skip debounce on first transition after startup to quickly
        # correct any mismatch between hardware state and desired mode
        self._skip_next_debounce = True
        _LOGGER.info(
            "Startup grace ended, inferred mode: %s (skip_next_debounce=True)",
            self._commanded_mode.value,
        )
        return False

    def _handle_automation_disabled(self) -> bool:
        """Handle automation disabled state.

        Returns True if evaluation should return early.
        """
        if self._get_switch_state("automation_enabled"):
            return False

        self._commanded_mode = BatteryMode.MANUAL
        self._mode_desired_since.clear()
        return True

    async def _handle_manual_override_timeout(
        self, data: CoordinatorData, now: datetime
    ) -> None:
        """Handle automatic manual override timeout clearing."""
        if not data.manual_override or self._manual_override_set_at is None:
            return

        timeout_hours = float(
            self._get_option(
                CONF_MANUAL_OVERRIDE_TIMEOUT,
                DEFAULT_MANUAL_OVERRIDE_TIMEOUT,
            )
        )
        if timeout_hours <= 0:
            return

        elapsed = now - self._manual_override_set_at
        if elapsed < timedelta(hours=timeout_hours):
            return

        _LOGGER.info(
            "Manual override timeout (%.1f hours) elapsed, clearing",
            timeout_hours,
        )
        data.manual_override = False
        self._manual_override_set_at = None
        # Send notification about manual override timeout
        await self._notification_service.send_manual_override_timeout_notification(
            data, timeout_hours
        )
        # Do NOT call compute_derived_values() again here.
        # A full recompute already ran at the top of this lock
        # (Item 5 fix).
        # desired remains MANUAL this cycle; the next periodic tick
        # (at most 1 minute away) will recompute the correct mode.

    async def _handle_soc_monitoring(self, data: CoordinatorData) -> bool:
        """Handle SOC-based charge target enforcement.

        Returns True if a transition was executed and evaluation should return.
        """
        if self._commanded_mode not in (
            BatteryMode.GRID_CHARGING,
            BatteryMode.BOOST_CHARGING,
        ):
            return False

        battery_target = float(
            self._get_option(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET)
        )

        # Determine if SOC monitoring is needed:
        # - BOOST_CHARGING: always (uses reserve=100)
        # - GRID_CHARGING: only when target is 81-99% (reserve clamped to 80)
        needs_soc_monitoring = self._commanded_mode == BatteryMode.BOOST_CHARGING or (
            self._commanded_mode == BatteryMode.GRID_CHARGING
            and BACKUP_RESERVE_MAX_VALID < battery_target < 100
        )

        if not (
            needs_soc_monitoring and data.soc is not None and data.soc >= battery_target
        ):
            return False

        _LOGGER.info(
            "SOC %.1f%% reached battery target %.0f%% — stopping %s, transitioning to SELF_CONSUMPTION",
            data.soc,
            battery_target,
            self._commanded_mode.value,
        )
        transition_success = await self._execute_mode_transition(
            data, BatteryMode.SELF_CONSUMPTION
        )
        if transition_success:
            old_mode = self._commanded_mode
            self._commanded_mode = BatteryMode.SELF_CONSUMPTION
            self._mode_desired_since.clear()
            await self._notification_service.send_transition_notification(
                old_mode, BatteryMode.SELF_CONSUMPTION, data
            )
        return True

    def _should_defer_for_minimum_duration(
        self, desired: BatteryMode, now: datetime
    ) -> bool:
        """Check if minimum mode duration requires deferring transition."""
        if self._mode_established_at is None:
            return False

        time_in_current_mode = now - self._mode_established_at
        if time_in_current_mode >= self._MIN_MODE_DURATION:
            return False

        _LOGGER.info(
            "Mode %s active for %s, need %s minimum — deferring transition to %s",
            self._commanded_mode.value,
            time_in_current_mode,
            self._MIN_MODE_DURATION,
            desired.value,
        )
        return True

    def _get_debounce_duration(self, desired: BatteryMode) -> timedelta:
        """Get debounce duration for a desired transition."""
        if self._skip_next_debounce:
            self._skip_next_debounce = False
            _LOGGER.info("Skipping debounce (first transition after startup)")
            return timedelta(0)

        return self.get_debounce_for_transition(self._commanded_mode, desired)

    def _handle_debounce_timing(
        self, desired: BatteryMode, now: datetime, debounce: timedelta
    ) -> bool:
        """Handle debounce tracking for desired transitions.

        Returns True if evaluation should return early.
        """
        # Clear timers for modes no longer desired.
        # Prevents debounce bypass when prices oscillate: if GRID_CHARGING was
        # desired at t=0, flipped away at t=2min, then desired again at t=3min,
        # the old t=0 timer would make the debounce appear nearly satisfied.
        # Clearing stale timers ensures the full debounce is always served from
        # a continuous period of desire.
        for mode in list(self._mode_desired_since.keys()):
            if mode != desired:
                self._mode_desired_since.pop(mode, None)

        if desired not in self._mode_desired_since:
            # First time this mode is (continuously) desired — start the timer
            self._mode_desired_since[desired] = now
            if debounce > timedelta(0):
                _LOGGER.info(
                    "Mode %s desired, debounce %s starts now",
                    desired.value,
                    debounce,
                )
                return True

        desired_since = self._mode_desired_since[desired]
        elapsed = now - desired_since

        if elapsed < debounce:
            _LOGGER.info(
                "Mode %s desired for %s, need %s — waiting",
                desired.value,
                elapsed,
                debounce,
            )
            return True

        return False

    async def evaluate_state_machine(
        self,
        data: CoordinatorData,
        computation_engine: ComputationEngine,
        read_state_func: Callable[[], None] | None = None,
        notify_func: Callable[[], None] | None = None,
        check_automation_ready_func: Callable[[CoordinatorData], Any] | None = None,
    ) -> None:
        """Compare desired mode with commanded mode and execute transitions.

        Handles debounce, command issuance, flag management, and notifications.

        ``read_state_func`` and ``notify_func`` are called inside the lock so
        that queued evaluations always operate on fresh post-transition state
        rather than a stale snapshot captured before the previous transition
        completed.  This eliminates the race condition where a second
        evaluation could immediately revert a transition because it was working
        from pre-transition hardware state.

        """
        async with self._evaluate_lock:
            # DIAGNOSTIC: Log current state machine state at INFO level
            _LOGGER.info(
                "State machine evaluate: desired=%s, commanded=%s, hardware_op=%s",
                data.active_mode.value if hasattr(data, "active_mode") else "unknown",
                self._commanded_mode.value,
                data.operation_mode,
            )
            # Re-read external state and recompute derived values while holding
            # the lock.  If multiple evaluations were queued, each one now
            # operates on hardware state that reflects any transitions made by
            # the previous evaluation — preventing stale-state reversions.
            if read_state_func is not None:
                read_state_func()
            # Issue #468: Re-check automation ready after reading fresh state
            # If automation_ready was set False from stale startup data,
            # re-checking now ensures we use fresh values after state refresh
            # Issue #551: Suppress warning during startup grace period
            if not data.automation_ready and check_automation_ready_func is not None:
                in_grace = self._startup_grace_until is not None
                check_automation_ready_func(data, suppress_warning=in_grace)
            computation_engine.compute_derived_values(data)

            # Notify listeners with fresh computed data regardless of which
            # code path is taken below (transition, debounce, no-change, etc.).
            # The try/finally guarantees notify_func() is always called after
            # compute_derived_values(), even on early returns.
            try:
                now = dt_util.now()
                desired = data.active_mode

                # --- Startup grace period (30 s) ---
                if self._handle_startup_grace_period(data, now):
                    return

                # --- Automation disabled ---
                if self._handle_automation_disabled():
                    return

                # --- Auto-clear manual override after timeout ---
                await self._handle_manual_override_timeout(data, now)

                # --- No change needed ---
                if desired == self._commanded_mode:
                    self._mode_desired_since.clear()
                    # Reset skip_next_debounce flag to prevent it persisting
                    # incorrectly across evaluation cycles (Issue #340)
                    self._skip_next_debounce = False

                    # --- SOC-based charge target enforcement ---
                    # For BOOST_CHARGING: Always uses autonomous+100, so SOC monitoring stops at target.
                    # For GRID_CHARGING: Uses backup mode with clamped reserve (80 for targets 81-99),
                    # so SOC monitoring is only needed when target is in 81-99% range.
                    if await self._handle_soc_monitoring(data):
                        return

                    # --- Periodic health check (every minute) ---
                    # Verify hardware state matches commanded state
                    # This catches drift from manual changes, power outages, etc.
                    if not self._get_switch_state("dry_run"):
                        await self._perform_health_check(data)

                    return

                # --- Minimum mode duration check (Issue #279) ---
                # Prevent rapid cycling by requiring current mode to be active for minimum duration
                # This prevents the learning system's cycling penalty from being triggered
                if self._should_defer_for_minimum_duration(desired, now):
                    return

                # --- Debounce tracking ---
                # Skip debounce if flag is set (first transition after startup grace)
                debounce = self._get_debounce_duration(desired)
                if self._handle_debounce_timing(desired, now, debounce):
                    return

                # --- Debounce satisfied — execute transition ---
                desired_since = self._mode_desired_since[desired]
                elapsed = now - desired_since
                old_mode = self._commanded_mode
                _LOGGER.info(
                    "State machine transition: %s → %s (desired for %s)",
                    old_mode.value,
                    desired.value,
                    elapsed,
                )

                # --- Check entity availability before transition ---
                if not self.entity_validator.should_allow_automation():
                    _LOGGER.warning(
                        "Automation blocked: Required entities unavailable. Maintaining current mode %s.",
                        old_mode.value,
                    )
                    # Do not transition, keep current mode
                    return

                # Execute the transition and check if it succeeded
                transition_success = await self._execute_mode_transition(data, desired)

                # Only update commanded_mode if transition was successful
                if not transition_success:
                    _LOGGER.warning(
                        "Mode transition from %s to %s failed - keeping previous commanded mode",
                        old_mode.value,
                        desired.value,
                    )
                    # Send notification about failed transition
                    await (
                        self._notification_service.send_transition_failed_notification(
                            desired, data
                        )
                    )
                    # Clear only the failed mode's timer so it will retry on next evaluation
                    # Keep other mode timers intact - they may be needed if forecast flips back
                    self._mode_desired_since.pop(desired, None)
                    return

                self._commanded_mode = desired
                self._mode_desired_since.clear()

                # Record decision for learning system (Issue #170 Phase 1)
                if self._decision_tracker is not None and not self._get_switch_state(
                    "dry_run"
                ):
                    self._decision_tracker.record_decision(data, desired, old_mode)

                # Send notification
                await self._notification_service.send_transition_notification(
                    old_mode, desired, data
                )

            finally:
                # Always notify listeners after compute_derived_values(), so
                # sensors reflect fresh computed state on every code path.
                if notify_func is not None:
                    notify_func()

    async def _execute_mode_transition(
        self, data: CoordinatorData, target: BatteryMode
    ) -> bool:
        """Issue battery commands and set state flags for *target* mode.

        Returns:
            True if transition completed successfully, False otherwise.
        """
        dry_run = self._get_switch_state("dry_run")

        # Set flag to prevent re-evaluation during mode transition
        self._in_mode_transition = True
        transition_success = True

        try:
            _LOGGER.info(
                "Executing mode transition to %s (dry_run=%s)", target.value, dry_run
            )
            config = self._get_mode_config(target, data)
            if config is None:
                _LOGGER.info("Manual mode transition completed (no commands)")
                transition_success = True
            else:
                if target in (BatteryMode.SELF_CONSUMPTION, BatteryMode.DEMAND_BLOCK):
                    transition_success = (
                        await self._battery_controller.set_self_consumption(
                            data, dry_run, preserve_soc=config.backup_reserve
                        )
                    )

                elif target == BatteryMode.GRID_CHARGING:
                    battery_target = float(
                        self._get_option(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET)
                    )
                    transition_success = (
                        await self._battery_controller.set_force_charge(
                            data, dry_run, target_soc=battery_target
                        )
                    )

                elif target == BatteryMode.BOOST_CHARGING:
                    transition_success = (
                        await self._battery_controller.set_boost_charge(data, dry_run)
                    )

                elif target == BatteryMode.SPIKE_DISCHARGE:
                    reserve_soc = (
                        data.spike_reserve_soc
                        if data.spike_in_conservative_mode
                        else None
                    )
                    transition_success = (
                        await self._battery_controller.set_force_discharge(
                            data, dry_run, reserve_soc=reserve_soc
                        )
                    )

                elif target == BatteryMode.PROACTIVE_EXPORT:
                    transition_success = (
                        await self._battery_controller.set_proactive_export(
                            data, dry_run
                        )
                    )

                elif target == BatteryMode.HOLD:
                    transition_success = (
                        await self._battery_controller.set_self_consumption(
                            data, dry_run, preserve_soc=config.backup_reserve
                        )
                    )

                if transition_success:
                    self._self_consumption_reserve = config.self_consumption_reserve
                    self._grid_charging_reserve = config.grid_charging_reserve
                    self._proactive_export_reserve = config.proactive_export_reserve
                    _LOGGER.info(
                        "%s mode transition completed (reserve=%s)",
                        target.value,
                        config.backup_reserve,
                    )
                else:
                    _LOGGER.error("%s mode transition FAILED", target.value)

        except Exception as e:
            _LOGGER.error(
                "Exception during mode transition to %s: %s",
                target.value,
                e,
                exc_info=True,
            )
            transition_success = False
            # Note: We still clear _in_mode_transition in the finally block
            # so the state machine can retry the transition on the next evaluation
        finally:
            # Always clear the flag, even if an exception occurs
            _LOGGER.debug("Mode transition flag cleared, allowing re-evaluation")
            self._in_mode_transition = False

        # Track successful transition time for health check grace period
        if transition_success:
            from homeassistant.util import dt as dt_util  # noqa: PLC0415

            transition_time = dt_util.now()
            # Track when mode was established for minimum duration check (Issue #279)
            self._mode_established_at = transition_time

            if not dry_run:
                self._last_successful_transition = transition_time

                # Issue #501: Record implementation timestamp and calculate lag
            if data.decision_timestamp is not None and data.decision_mode == target:
                data.implementation_timestamp = transition_time
                lag_seconds = (
                    transition_time - data.decision_timestamp
                ).total_seconds()
                data.decision_lag_seconds = lag_seconds
                from_mode = data.active_mode.value if data.active_mode else "unknown"
                to_mode = target.value
                decision_mode_value = (
                    data.decision_mode.value if data.decision_mode else "unknown"
                )

                # Add to history
                history_entry = {
                    "from_mode": from_mode,
                    "to_mode": to_mode,
                    "lag_seconds": round(lag_seconds, 2),
                    "decision_time": data.decision_timestamp.isoformat(),
                    "implementation_time": transition_time.isoformat(),
                }
                data.decision_lag_history.append(history_entry)
                if len(data.decision_lag_history) > 50:
                    data.decision_lag_history = data.decision_lag_history[-50:]

                _LOGGER.info(
                    "Decision lag: %s → %s completed in %.2fs",
                    decision_mode_value,
                    to_mode,
                    lag_seconds,
                )

                # Clear decision tracking fields
                data.decision_timestamp = None
                data.decision_mode = None

            _LOGGER.debug(
                "Recorded successful transition to %s at %s",
                target.value,
                transition_time.strftime("%H:%M:%S"),
            )

        return transition_success

    def _get_expected_state_for_mode(
        self, mode: BatteryMode
    ) -> tuple[str, int, str, bool]:
        """Get the expected hardware state for a given mode.

        Returns:
            Tuple of (operation_mode, backup_reserve, export_mode, grid_charging_allowed)
        """
        if mode == BatteryMode.SELF_CONSUMPTION:
            # Use tracked reserve (preserve_soc when set, otherwise 10)
            reserve = (
                int(self._self_consumption_reserve)
                if self._self_consumption_reserve is not None
                else 10
            )
            return ("self_consumption", reserve, TESLEMETRY_EXPORT_PV_ONLY, False)
        elif mode == BatteryMode.DEMAND_BLOCK:
            # Demand block uses same reserve tracking as self_consumption
            reserve = (
                int(self._self_consumption_reserve)
                if self._self_consumption_reserve is not None
                else 10
            )
            return ("self_consumption", reserve, TESLEMETRY_EXPORT_PV_ONLY, False)
        elif mode == BatteryMode.GRID_CHARGING:
            # Grid charging uses backup mode for 3.3 kW rate.
            # Reserve is clamped for Tesla firmware compatibility (81-99% → 80).
            # The actual reserve is tracked in _grid_charging_reserve.
            # Grid charging must be enabled for this mode.
            return ("backup", -1, TESLEMETRY_EXPORT_PV_ONLY, True)  # reserve is dynamic
        elif mode == BatteryMode.BOOST_CHARGING:
            # Boost charging needs grid charging enabled for fast charging
            return ("autonomous", 100, TESLEMETRY_EXPORT_PV_ONLY, True)
        elif mode == BatteryMode.SPIKE_DISCHARGE:
            # Discharge modes don't need grid charging
            return ("autonomous", 10, TESLEMETRY_EXPORT_BATTERY_OK, False)
        elif mode == BatteryMode.PROACTIVE_EXPORT:
            # Reserve is dynamic (max(4, SOC-5)), so use 10 as expected for health check
            # The actual reserve will be set based on current SOC
            # Export modes don't need grid charging
            return ("autonomous", 10, TESLEMETRY_EXPORT_BATTERY_OK, False)
        elif mode == BatteryMode.HOLD:
            # HOLD uses self_consumption mode with elevated reserve (preserve_soc)
            # The actual reserve is tracked in _self_consumption_reserve
            reserve = (
                int(self._self_consumption_reserve)
                if self._self_consumption_reserve is not None
                else 10
            )
            return ("self_consumption", reserve, TESLEMETRY_EXPORT_PV_ONLY, False)
        else:  # MANUAL or unknown
            return ("", -1, "", False)

    def _handle_tesla_override_state(
        self, data: CoordinatorData, now: datetime
    ) -> bool:
        """Handle Tesla override detection and cooldown.

        Returns True if health checks should be skipped.
        """
        # --- Tesla Override Detection ---
        # Check if Tesla has taken control (Storm Watch, Grid Event, VPP)
        if self._detect_tesla_override(data):
            if not self._tesla_override_detected:
                # First time detecting Tesla override
                self._tesla_override_detected = True
                self._tesla_override_detected_at = now
                _LOGGER.warning(
                    "[TESLA OVERRIDE] Detected Tesla has taken control of Powerwall "
                    "(Storm Watch, Grid Event, or VPP active). Hardware state: "
                    "op=%s, reserve=%.1f%%. Yielding control until event ends.",
                    data.operation_mode,
                    data.backup_reserve,
                )
            else:
                # Already in Tesla override mode - check if we should log status
                if self._tesla_override_detected_at is not None:
                    duration = now - self._tesla_override_detected_at
                    _LOGGER.info(
                        "[TESLA OVERRIDE] Still active for %s. Skipping health check corrections.",
                        duration,
                    )
            # Skip all health check corrections while Tesla has control
            return True

        # Tesla override has ended
        if self._tesla_override_detected:
            duration = (
                now - self._tesla_override_detected_at
                if self._tesla_override_detected_at
                else timedelta(0)
            )
            _LOGGER.info(
                "[TESLA OVERRIDE] Tesla has released control after %s. "
                "Applying %s cooldown before resuming health checks.",
                duration,
                TESLA_OVERRIDE_COOLDOWN,
            )
            self._tesla_override_detected = False
            self._tesla_override_released_at = now
            self._tesla_override_detected_at = None

        # --- Tesla Override Cooldown ---
        # After Tesla releases control, wait for cooldown period before resuming
        if self._tesla_override_released_at is None:
            return False

        time_since_release = now - self._tesla_override_released_at
        if time_since_release < TESLA_OVERRIDE_COOLDOWN:
            remaining = (TESLA_OVERRIDE_COOLDOWN - time_since_release).total_seconds()
            _LOGGER.debug(
                "[HEALTH CHECK] Skipping - in Tesla override cooldown (%.0fs remaining)",
                remaining,
            )
            return True

        # Cooldown period has elapsed, clear the timestamp
        _LOGGER.info(
            "[TESLA OVERRIDE] Cooldown period elapsed, resuming normal health checks"
        )
        self._tesla_override_released_at = None
        return False

    def _should_skip_transition_grace(self, now: datetime) -> bool:
        """Check if health checks should be skipped during transition grace."""
        if self._last_successful_transition is None:
            return False

        time_since_transition = now - self._last_successful_transition
        if time_since_transition >= self._TRANSITION_GRACE_PERIOD:
            return False

        remaining = (
            self._TRANSITION_GRACE_PERIOD - time_since_transition
        ).total_seconds()
        _LOGGER.debug(
            "[HEALTH CHECK] Skipping - in transition grace period (%.0fs remaining)",
            remaining,
        )
        return True

    def _should_skip_health_check(self, data: CoordinatorData, now: datetime) -> bool:
        """Check if health check should be skipped."""
        # Skip health check during manual override - user is in control
        if data.manual_override:
            _LOGGER.debug("[HEALTH CHECK] Skipping - manual override active")
            return True

        if self._handle_tesla_override_state(data, now):
            return True

        if self._should_skip_transition_grace(now):
            return True

        return False

    async def _perform_health_check(self, data: CoordinatorData) -> None:
        """Verify hardware state matches commanded mode.

        This runs every minute to detect drift from manual changes,
        power outages, or other issues that might cause the hardware
        state to diverge from what we think it is.

        If drift is detected, we attempt to correct it, unless Tesla
        has taken control (Storm Watch, Grid Event, VPP).

        SKIPPED when manual_override is True - user is in control and
        health checks would fight against their manual commands.
        """
        now = dt_util.now()
        if self._should_skip_health_check(data, now):
            return

        expected_op, expected_reserve, expected_export, expected_grid_charging = (
            self._get_expected_state_for_mode(self._commanded_mode)
        )

        # Skip if we don't have expected values
        if not expected_op:
            return

        # For PROACTIVE_EXPORT, use the tracked dynamic reserve
        if (
            self._commanded_mode == BatteryMode.PROACTIVE_EXPORT
            and self._proactive_export_reserve is not None
        ):
            expected_reserve = int(self._proactive_export_reserve)

        # For GRID_CHARGING, use the tracked clamped reserve
        if (
            self._commanded_mode == BatteryMode.GRID_CHARGING
            and self._grid_charging_reserve is not None
        ):
            expected_reserve = self._grid_charging_reserve

        # Use quick verification from battery controller
        is_valid = await self._battery_controller.verify_current_state(
            expected_operation_mode=expected_op,
            expected_backup_reserve=expected_reserve,
            expected_export_mode=expected_export,
            expected_grid_charging_allowed=expected_grid_charging,
        )

        if not is_valid:
            # Check if we're in cooldown period
            if (
                self._last_health_correction is not None
                and now - self._last_health_correction < self._MIN_CORRECTION_INTERVAL
            ):
                remaining = (
                    self._MIN_CORRECTION_INTERVAL - (now - self._last_health_correction)
                ).total_seconds()
                _LOGGER.debug(
                    "[HEALTH CHECK] Mismatch detected but correction cooldown active (%.0fs remaining)",
                    remaining,
                )
                return

            # Log the mismatch with detailed diagnostics
            last_transition_str = (
                self._last_successful_transition.strftime("%H:%M:%S")
                if self._last_successful_transition
                else "never"
            )
            last_correction_str = (
                self._last_health_correction.strftime("%H:%M:%S")
                if self._last_health_correction
                else "never"
            )
            _LOGGER.warning(
                "[HEALTH CHECK] State mismatch detected for commanded mode %s. "
                "Last successful transition: %s, last correction: %s. Attempting correction...",
                self._commanded_mode.value,
                last_transition_str,
                last_correction_str,
            )

            # Attempt to correct the state
            correction_success = await self._execute_mode_transition(
                data, self._commanded_mode
            )
            self._last_health_correction = now

            if correction_success:
                _LOGGER.info(
                    "[HEALTH CHECK] Correction successful for mode %s",
                    self._commanded_mode.value,
                )
            else:
                _LOGGER.error(
                    "[HEALTH CHECK] Correction FAILED for mode %s - will retry after cooldown",
                    self._commanded_mode.value,
                )

            # Collect mismatch details for intelligent notification filtering
            # Issue #394: Skip notification if only grid_charging flipped (Tesla cloud sync)
            grid_charging_entity = self._battery_controller._get_entity_id(  # noqa: SLF001
                "teslemetry_allow_charging_from_grid"
            )
            actual_grid_charging = self._battery_controller._read_bool(  # noqa: SLF001
                grid_charging_entity
            )

            mismatch_details = {
                "operation_mode": data.operation_mode != expected_op,
                "backup_reserve": abs((data.backup_reserve or 0) - expected_reserve)
                >= 1,
                "grid_charging_allowed": actual_grid_charging != expected_grid_charging,
            }

            # Send notification about health check correction (may be suppressed)
            await self._notification_service.send_health_correction_notification(
                self._commanded_mode, data, mismatch_details
            )

    def set_startup_grace(self, grace_seconds: int = 30) -> None:
        """Set startup grace period to wait for entities to populate."""
        self._startup_grace_until = dt_util.now() + timedelta(seconds=grace_seconds)

    def set_commanded_mode(self, mode: BatteryMode) -> None:
        """Set the commanded mode directly (used by manual button presses).

        This prevents race conditions where the health check might try to
        "correct" the mode after a button press changes hardware state
        but before the next evaluation cycle.

        Args:
            mode: The battery mode to set as commanded.
        """
        self._commanded_mode = mode
        self._mode_desired_since.clear()
        _LOGGER.info(
            "Commanded mode set directly to %s (manual button press)",
            mode.value,
        )

    @property
    def in_mode_transition(self) -> bool:
        """Check if currently in a mode transition."""
        return self._in_mode_transition

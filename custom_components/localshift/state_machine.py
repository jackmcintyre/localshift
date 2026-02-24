"""State machine for battery mode evaluation and transitions."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

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


class StateMachine:
    """Manages battery mode state machine evaluation and transitions."""

    def __init__(
        self,
        battery_controller: BatteryController,
        notification_service: NotificationService,
        get_switch_state_func: callable,
        get_option_func: callable,
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
        # Track dynamic reserve for PROACTIVE_EXPORT mode
        self._proactive_export_reserve: float | None = None
        # Track grid charging target reserve (clamped for Tesla firmware compatibility)
        self._grid_charging_reserve: int | None = None
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

    async def evaluate_state_machine(
        self,
        data: CoordinatorData,
        computation_engine: ComputationEngine,
        read_state_func: callable | None = None,
        notify_func: callable | None = None,
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
            computation_engine.compute_derived_values(data)

            # Notify listeners with fresh computed data regardless of which
            # code path is taken below (transition, debounce, no-change, etc.).
            # The try/finally guarantees notify_func() is always called after
            # compute_derived_values(), even on early returns.
            try:
                now = dt_util.now()
                desired = data.active_mode

                # --- Startup grace period (30 s) ---
                if self._startup_grace_until is not None:
                    if now < self._startup_grace_until:
                        _LOGGER.debug("State machine in startup grace period, skipping")
                        return
                    self._startup_grace_until = None
                    self._commanded_mode = self.infer_current_hardware_mode(data)
                    # Skip debounce on first transition after startup to quickly
                    # correct any mismatch between hardware state and desired mode
                    self._skip_next_debounce = True
                    _LOGGER.info(
                        "Startup grace ended, inferred mode: %s (skip_next_debounce=True)",
                        self._commanded_mode.value,
                    )

                # --- Automation disabled ---
                if not self._get_switch_state("automation_enabled"):
                    self._commanded_mode = BatteryMode.MANUAL
                    self._mode_desired_since.clear()
                    return

                # --- Auto-clear manual override after timeout ---
                if data.manual_override and self._manual_override_set_at is not None:
                    timeout_hours = float(
                        self._get_option(
                            CONF_MANUAL_OVERRIDE_TIMEOUT,
                            DEFAULT_MANUAL_OVERRIDE_TIMEOUT,
                        )
                    )
                    if timeout_hours > 0:
                        elapsed = now - self._manual_override_set_at
                        if elapsed >= timedelta(hours=timeout_hours):
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
                            # (Item 5 fix).  A second nested compute would double-fire
                            # the forecast tracker age-check and potentially append a
                            # duplicate decision log entry.
                            # Instead, flag the forecast tracker to force a recompute
                            # on the next evaluation cycle so that active_mode is
                            # re-derived without the manual_override flag set.
                            computation_engine._forecast_change_tracker._last_forecast_time = None  # noqa: SLF001
                            # desired remains MANUAL this cycle; the next periodic tick
                            # (at most 1 minute away) will recompute the correct mode.

                # --- No change needed ---
                if desired == self._commanded_mode:
                    self._mode_desired_since.clear()

                    # --- SOC-based charge target enforcement ---
                    # For BOOST_CHARGING: Always uses autonomous+100, so SOC monitoring stops at target.
                    # For GRID_CHARGING: Uses backup mode with clamped reserve (80 for targets 81-99),
                    # so SOC monitoring is only needed when target is in 81-99% range.
                    if self._commanded_mode in (
                        BatteryMode.GRID_CHARGING,
                        BatteryMode.BOOST_CHARGING,
                    ):
                        battery_target = float(
                            self._get_option(
                                CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET
                            )
                        )
                        # Determine if SOC monitoring is needed:
                        # - BOOST_CHARGING: always (uses reserve=100)
                        # - GRID_CHARGING: only when target is 81-99% (reserve clamped to 80)
                        needs_soc_monitoring = (
                            self._commanded_mode == BatteryMode.BOOST_CHARGING
                            or (
                                self._commanded_mode == BatteryMode.GRID_CHARGING
                                and BACKUP_RESERVE_MAX_VALID < battery_target < 100
                            )
                        )
                        if (
                            needs_soc_monitoring
                            and data.soc is not None
                            and data.soc >= battery_target
                        ):
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
                            return

                    # --- Periodic health check (every minute) ---
                    # Verify hardware state matches commanded state
                    # This catches drift from manual changes, power outages, etc.
                    if not self._get_switch_state("dry_run"):
                        await self._perform_health_check(data)

                    return

                # --- Debounce tracking ---
                # Skip debounce if flag is set (first transition after startup grace)
                if self._skip_next_debounce:
                    debounce = timedelta(0)
                    self._skip_next_debounce = False
                    _LOGGER.info("Skipping debounce (first transition after startup)")
                else:
                    debounce = self.get_debounce_for_transition(
                        self._commanded_mode, desired
                    )

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
                        return

                desired_since = self._mode_desired_since[desired]
                elapsed = now - desired_since

                if elapsed < debounce:
                    _LOGGER.info(
                        "Mode %s desired for %s, need %s — waiting",
                        desired.value,
                        elapsed,
                        debounce,
                    )
                    return

                # --- Debounce satisfied — execute transition ---
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

                # Clear proactive export reserve when leaving that mode
                if desired != BatteryMode.PROACTIVE_EXPORT:
                    self._proactive_export_reserve = None

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

            if target == BatteryMode.SELF_CONSUMPTION:
                transition_success = (
                    await self._battery_controller.set_self_consumption(data, dry_run)
                )
                if transition_success:
                    _LOGGER.info("Self consumption mode transition completed")
                else:
                    _LOGGER.error("Self consumption mode transition FAILED")

            elif target == BatteryMode.DEMAND_BLOCK:
                # Demand block is self_consumption with extra protection
                transition_success = (
                    await self._battery_controller.set_self_consumption(data, dry_run)
                )
                if transition_success:
                    _LOGGER.info("Demand block mode transition completed")
                else:
                    _LOGGER.error("Demand block mode transition FAILED")

            elif target == BatteryMode.GRID_CHARGING:
                # Get battery target for grid charging
                battery_target = float(
                    self._get_option(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET)
                )
                # Calculate clamped reserve for Tesla firmware compatibility
                if battery_target <= BACKUP_RESERVE_MAX_VALID:
                    reserve = int(battery_target)
                elif battery_target >= 100:
                    reserve = 100
                else:
                    reserve = BACKUP_RESERVE_MAX_VALID  # 81-99% clamped to 80

                transition_success = await self._battery_controller.set_force_charge(
                    data, dry_run, target_soc=battery_target
                )
                if transition_success:
                    # Track the reserve for health checks
                    self._grid_charging_reserve = reserve
                    _LOGGER.info(
                        "Grid charging mode transition completed (target=%.0f%%, reserve=%d%%)",
                        battery_target,
                        reserve,
                    )
                else:
                    _LOGGER.error("Grid charging mode transition FAILED")

            elif target == BatteryMode.BOOST_CHARGING:
                transition_success = await self._battery_controller.set_boost_charge(
                    data, dry_run
                )
                if transition_success:
                    _LOGGER.info("Boost charging mode transition completed")
                else:
                    _LOGGER.error("Boost charging mode transition FAILED")

            elif target == BatteryMode.SPIKE_DISCHARGE:
                # Check if conservative mode is enabled and use spike_reserve_soc if available
                reserve_soc = (
                    data.spike_reserve_soc if data.spike_in_conservative_mode else None
                )
                transition_success = await self._battery_controller.set_force_discharge(
                    data, dry_run, reserve_soc=reserve_soc
                )
                if transition_success:
                    _LOGGER.info(
                        "Spike discharge mode transition completed (reserve=%s)",
                        reserve_soc,
                    )
                else:
                    _LOGGER.error("Spike discharge mode transition FAILED")

            elif target == BatteryMode.PROACTIVE_EXPORT:
                transition_success = (
                    await self._battery_controller.set_proactive_export(data, dry_run)
                )
                if transition_success:
                    # Track the dynamic reserve for health checks
                    self._proactive_export_reserve = max(4.0, (data.soc or 0.0) - 5.0)
                    _LOGGER.info(
                        "Proactive export mode transition completed (throttled reserve=%s)",
                        self._proactive_export_reserve,
                    )
                else:
                    _LOGGER.error("Proactive export mode transition FAILED")
                    self._proactive_export_reserve = None

            elif target == BatteryMode.MANUAL:
                pass  # No command — user is controlling manually
                _LOGGER.info("Manual mode transition completed (no commands)")

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
        if transition_success and not dry_run:
            transition_time = dt_util.now()
            self._last_successful_transition = transition_time
            _LOGGER.debug(
                "Recorded successful transition to %s at %s",
                target.value,
                transition_time.strftime("%H:%M:%S"),
            )

        return transition_success

    def _get_expected_state_for_mode(self, mode: BatteryMode) -> tuple[str, int, str]:
        """Get the expected hardware state for a given mode.

        Returns:
            Tuple of (operation_mode, backup_reserve, export_mode)
        """
        if mode == BatteryMode.SELF_CONSUMPTION:
            return ("self_consumption", 10, TESLEMETRY_EXPORT_PV_ONLY)
        elif mode == BatteryMode.DEMAND_BLOCK:
            return ("self_consumption", 10, TESLEMETRY_EXPORT_PV_ONLY)
        elif mode == BatteryMode.GRID_CHARGING:
            # Grid charging uses backup mode for 3.3 kW rate.
            # Reserve is clamped for Tesla firmware compatibility (81-99% → 80).
            # The actual reserve is tracked in _grid_charging_reserve.
            return ("backup", -1, TESLEMETRY_EXPORT_PV_ONLY)  # reserve is dynamic
        elif mode == BatteryMode.BOOST_CHARGING:
            return ("autonomous", 100, TESLEMETRY_EXPORT_PV_ONLY)
        elif mode == BatteryMode.SPIKE_DISCHARGE:
            return ("autonomous", 10, TESLEMETRY_EXPORT_BATTERY_OK)
        elif mode == BatteryMode.PROACTIVE_EXPORT:
            # Reserve is dynamic (max(4, SOC-5)), so use 10 as expected for health check
            # The actual reserve will be set based on current SOC
            return ("autonomous", 10, TESLEMETRY_EXPORT_BATTERY_OK)
        else:  # MANUAL or unknown
            return ("", -1, "")

    async def _perform_health_check(self, data: CoordinatorData) -> None:
        """Verify hardware state matches commanded mode.

        This runs every minute to detect drift from manual changes,
        power outages, or other issues that might cause the hardware
        state to diverge from what we think it is.

        If drift is detected, we attempt to correct it, unless Tesla
        has taken control (Storm Watch, Grid Event, VPP).
        """
        now = dt_util.now()

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
            return
        else:
            # Tesla override has ended
            if self._tesla_override_detected:
                duration = (
                    now - self._tesla_override_detected_at
                    if self._tesla_override_detected_at
                    else timedelta(0)
                )
                _LOGGER.info(
                    "[TESLA OVERRIDE] Tesla has released control after %s. Resuming normal health checks.",
                    duration,
                )
                self._tesla_override_detected = False
                self._tesla_override_detected_at = None

        # Skip health check during transition grace period
        # This prevents false positives when Tesla API is still propagating
        if self._last_successful_transition is not None:
            time_since_transition = now - self._last_successful_transition
            if time_since_transition < self._TRANSITION_GRACE_PERIOD:
                _LOGGER.debug(
                    "[HEALTH CHECK] Skipping - in transition grace period (%.0fs remaining)",
                    (
                        self._TRANSITION_GRACE_PERIOD - time_since_transition
                    ).total_seconds(),
                )
                return

        expected_op, expected_reserve, expected_export = (
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

            # Send notification about health check correction
            await self._notification_service.send_health_correction_notification(
                self._commanded_mode, data
            )

    def set_startup_grace(self, grace_seconds: int = 30) -> None:
        """Set startup grace period to wait for entities to populate."""
        self._startup_grace_until = dt_util.now() + timedelta(seconds=grace_seconds)

    def set_manual_override_timestamp(self) -> None:
        """Record when manual override was set for auto-clear timeout."""
        self._manual_override_set_at = dt_util.now()

    def clear_manual_override_timestamp(self) -> None:
        """Clear manual override timestamp when returning to automated control."""
        self._manual_override_set_at = None

    @property
    def in_mode_transition(self) -> bool:
        """Check if currently in a mode transition."""
        return self._in_mode_transition

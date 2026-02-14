"""State machine for battery mode evaluation and transitions."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

from .const import (
    CONF_MANUAL_OVERRIDE_TIMEOUT,
    DEFAULT_MANUAL_OVERRIDE_TIMEOUT,
    TESLEMETRY_EXPORT_BATTERY_OK,
    TESLEMETRY_EXPORT_PV_ONLY,
    BatteryMode,
)
from .coordinator_data import CoordinatorData

if TYPE_CHECKING:
    from .battery_controller import BatteryController
    from .computation_engine import ComputationEngine
    from .notification_service import NotificationService


_LOGGER = logging.getLogger(__name__)


class StateMachine:
    """Manages battery mode state machine evaluation and transitions."""

    def __init__(
        self,
        battery_controller: BatteryController,
        notification_service: NotificationService,
        get_switch_state_func: callable,
        get_option_func: callable,
    ) -> None:
        """Initialize the state machine.

        Args:
            battery_controller: Battery controller instance
            notification_service: Notification service instance
            get_switch_state_func: Function to get switch states
            get_option_func: Function to get configuration options
        """
        self._battery_controller = battery_controller
        self._notification_service = notification_service
        self._get_switch_state = get_switch_state_func
        self._get_option = get_option_func
        self._commanded_mode: BatteryMode = BatteryMode.SELF_CONSUMPTION
        self._mode_desired_since: dict[BatteryMode, datetime] = {}
        self._startup_grace_until: datetime | None = None
        self._evaluate_lock = asyncio.Lock()
        self._in_mode_transition: bool = False
        self._manual_override_set_at: datetime | None = None

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
        if data.hold_mode:
            if data.solar_export_hold:
                return BatteryMode.SOLAR_EXPORT_HOLD
            return BatteryMode.HOLD
        return BatteryMode.SELF_CONSUMPTION

    def get_debounce_for_transition(
        self, from_mode: BatteryMode, to_mode: BatteryMode
    ) -> timedelta:
        """Return the required debounce duration for a mode transition.

        Matches the YAML ``for: minutes: N`` patterns:
        - Spike / demand window / manual → immediate
        - Solar export hold → 2 minutes (A17/A18)
        - All price-driven transitions → 5 minutes (A3/A4/A10/A11)
        """
        # Immediate: high-priority or safety transitions
        if to_mode in (
            BatteryMode.SPIKE_DISCHARGE,
            BatteryMode.DEMAND_BLOCK,
            BatteryMode.MANUAL,
        ):
            return timedelta(0)
        # Immediate: leaving high-priority modes
        if from_mode in (BatteryMode.SPIKE_DISCHARGE, BatteryMode.DEMAND_BLOCK):
            return timedelta(0)
        # Immediate: holding for spike (forecast-based, not price jitter)
        if to_mode == BatteryMode.HOLDING_FOR_SPIKE:
            return timedelta(0)
        # Solar export hold: 2 minutes
        if (
            to_mode == BatteryMode.SOLAR_EXPORT_HOLD
            or from_mode == BatteryMode.SOLAR_EXPORT_HOLD
        ):
            return timedelta(minutes=2)
        # All other (price-driven): 5 minutes
        return timedelta(minutes=5)

    async def evaluate_state_machine(
        self,
        data: CoordinatorData,
        computation_engine: ComputationEngine,
    ) -> None:
        """Compare desired mode with commanded mode and execute transitions.

        Called after compute_derived_values() on every state change and
        periodic tick. Handles debounce, command issuance, flag management,
        and notifications.
        """
        async with self._evaluate_lock:
            now = dt_util.now()
            desired = data.active_mode

            # --- Startup grace period (30 s) ---
            if self._startup_grace_until is not None:
                if now < self._startup_grace_until:
                    _LOGGER.debug("State machine in startup grace period, skipping")
                    return
                self._startup_grace_until = None
                self._commanded_mode = self.infer_current_hardware_mode(data)
                _LOGGER.info(
                    "Startup grace ended, inferred mode: %s",
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
                        # Re-evaluate now that override is cleared
                        computation_engine.compute_derived_values(data)
                        desired = data.active_mode

            # --- No change needed ---
            if desired == self._commanded_mode:
                self._mode_desired_since.clear()

                # --- Periodic health check (every minute) ---
                # Verify hardware state matches commanded state
                # This catches drift from manual changes, power outages, etc.
                if not self._get_switch_state("dry_run"):
                    await self._perform_health_check(data)

                return

            # --- Debounce tracking ---
            debounce = self.get_debounce_for_transition(self._commanded_mode, desired)

            if desired not in self._mode_desired_since:
                # First time this mode is desired — start the timer
                self._mode_desired_since.clear()
                self._mode_desired_since[desired] = now
                if debounce > timedelta(0):
                    _LOGGER.debug(
                        "Mode %s desired, debounce %s starts now",
                        desired.value,
                        debounce,
                    )
                    return

            desired_since = self._mode_desired_since[desired]
            elapsed = now - desired_since

            if elapsed < debounce:
                _LOGGER.debug(
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

            # Execute the transition and check if it succeeded
            transition_success = await self._execute_mode_transition(data, desired)

            # Only update commanded_mode if transition was successful
            if not transition_success:
                _LOGGER.warning(
                    "Mode transition from %s to %s failed - keeping previous commanded mode",
                    old_mode.value,
                    desired.value,
                )
                # Clear the debounce timer so it will retry on next evaluation
                self._mode_desired_since.clear()
                return

            self._commanded_mode = desired
            self._mode_desired_since.clear()

            # Clear hold_mode flag when transitioning away from hold modes
            # This prevents flag from persisting and causing unintended
            # hold mode re-entry
            if desired not in (
                BatteryMode.HOLD,
                BatteryMode.SOLAR_EXPORT_HOLD,
                BatteryMode.HOLDING_FOR_SPIKE,
            ):
                data.hold_mode = False

            # Send notification
            await self._notification_service.send_transition_notification(
                old_mode, desired, data
            )

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

            elif target == BatteryMode.HOLD:
                data.solar_export_hold = False
                transition_success = await self._battery_controller.set_hold(
                    data, dry_run
                )
                if transition_success:
                    _LOGGER.info("Hold mode transition completed")
                else:
                    _LOGGER.error("Hold mode transition FAILED")

            elif target == BatteryMode.SOLAR_EXPORT_HOLD:
                data.solar_export_hold = True
                transition_success = await self._battery_controller.set_hold(
                    data, dry_run
                )
                if transition_success:
                    _LOGGER.info("Solar export hold mode transition completed")
                else:
                    _LOGGER.error("Solar export hold mode transition FAILED")

            elif target == BatteryMode.HOLDING_FOR_SPIKE:
                data.solar_export_hold = False
                transition_success = await self._battery_controller.set_hold(
                    data, dry_run
                )
                if transition_success:
                    _LOGGER.info("Holding for spike mode transition completed")
                else:
                    _LOGGER.error("Holding for spike mode transition FAILED")

            elif target == BatteryMode.GRID_CHARGING:
                transition_success = await self._battery_controller.set_force_charge(
                    data, dry_run
                )
                if transition_success:
                    _LOGGER.info("Grid charging mode transition completed")
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
                transition_success = await self._battery_controller.set_force_discharge(
                    data, dry_run
                )
                if transition_success:
                    _LOGGER.info("Spike discharge mode transition completed")
                else:
                    _LOGGER.error("Spike discharge mode transition FAILED")

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
        elif mode in (BatteryMode.HOLD, BatteryMode.HOLDING_FOR_SPIKE):
            # For hold modes, we can't know exact reserve without data,
            # so skip reserve check but verify operation and export
            return ("self_consumption", -1, TESLEMETRY_EXPORT_PV_ONLY)
        elif mode == BatteryMode.SOLAR_EXPORT_HOLD:
            return ("self_consumption", -1, TESLEMETRY_EXPORT_PV_ONLY)
        elif mode == BatteryMode.GRID_CHARGING:
            return ("backup", 10, TESLEMETRY_EXPORT_PV_ONLY)
        elif mode == BatteryMode.BOOST_CHARGING:
            return ("autonomous", 100, TESLEMETRY_EXPORT_PV_ONLY)
        elif mode == BatteryMode.SPIKE_DISCHARGE:
            return ("autonomous", 10, TESLEMETRY_EXPORT_BATTERY_OK)
        else:  # MANUAL or unknown
            return ("", -1, "")

    async def _perform_health_check(self, data: CoordinatorData) -> None:
        """Verify hardware state matches commanded mode.

        This runs every minute to detect drift from manual changes,
        power outages, or other issues that might cause the hardware
        state to diverge from what we think it is.

        If drift is detected, we attempt to correct it.
        """
        expected_op, expected_reserve, expected_export = (
            self._get_expected_state_for_mode(self._commanded_mode)
        )

        # Skip if we don't have expected values
        if not expected_op:
            return

        # Use quick verification from battery controller
        is_valid = await self._battery_controller.verify_current_state(
            expected_operation_mode=expected_op,
            expected_backup_reserve=expected_reserve,
            expected_export_mode=expected_export,
        )

        if not is_valid:
            _LOGGER.warning(
                "Health check failed: hardware state doesn't match commanded mode %s. "
                "Attempting to correct...",
                self._commanded_mode.value,
            )
            # Attempt to correct the drift
            await self._execute_mode_transition(data, self._commanded_mode)

    def set_startup_grace(self, grace_seconds: int = 30) -> None:
        """Set startup grace period to wait for entities to populate."""
        self._startup_grace_until = dt_util.now() + timedelta(seconds=grace_seconds)

    def set_manual_override_timestamp(self) -> None:
        """Record when manual override was set for auto-clear timeout."""
        self._manual_override_set_at = dt_util.now()

    @property
    def in_mode_transition(self) -> bool:
        """Check if currently in a mode transition."""
        return self._in_mode_transition

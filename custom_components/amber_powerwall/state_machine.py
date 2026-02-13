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

            await self._execute_mode_transition(data, desired)
            self._commanded_mode = desired
            self._mode_desired_since.clear()

            # Send notification
            await self._notification_service.send_transition_notification(
                old_mode, desired, data
            )

    async def _execute_mode_transition(
        self, data: CoordinatorData, target: BatteryMode
    ) -> None:
        """Issue battery commands and set state flags for *target* mode."""
        dry_run = self._get_switch_state("dry_run")

        # Set flag to prevent re-evaluation during mode transition
        self._in_mode_transition = True

        try:
            if target == BatteryMode.SELF_CONSUMPTION:
                await self._battery_controller.set_self_consumption(data, dry_run)

            elif target == BatteryMode.DEMAND_BLOCK:
                # Demand block is self_consumption with extra protection
                await self._battery_controller.set_self_consumption(data, dry_run)

            elif target == BatteryMode.HOLD:
                data.solar_export_hold = False
                await self._battery_controller.set_hold(data, dry_run)

            elif target == BatteryMode.SOLAR_EXPORT_HOLD:
                data.solar_export_hold = True
                await self._battery_controller.set_hold(data, dry_run)

            elif target == BatteryMode.HOLDING_FOR_SPIKE:
                data.solar_export_hold = False
                await self._battery_controller.set_hold(data, dry_run)

            elif target == BatteryMode.GRID_CHARGING:
                await self._battery_controller.set_force_charge(data, dry_run)

            elif target == BatteryMode.BOOST_CHARGING:
                await self._battery_controller.set_boost_charge(data, dry_run)

            elif target == BatteryMode.SPIKE_DISCHARGE:
                await self._battery_controller.set_force_discharge(data, dry_run)

            elif target == BatteryMode.MANUAL:
                pass  # No command — user is controlling manually
        finally:
            # Always clear the flag, even if an exception occurs
            self._in_mode_transition = False

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

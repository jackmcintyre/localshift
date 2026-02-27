"""Battery control functionality for Tesla Powerwall."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from .const import (
    BACKUP_RESERVE_MAX_VALID,
    TESLEMETRY_EXPORT_BATTERY_OK,
    TESLEMETRY_EXPORT_PV_ONLY,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator_data import CoordinatorData


_LOGGER = logging.getLogger(__name__)

# Transition timeouts per mode (seconds)
# Boost charging needs longer due to Tesla API behavior with autonomous mode
TRANSITION_TIMEOUTS = {
    "autonomous": 15,  # autonomous mode takes longer to propagate
    "backup": 10,
    "self_consumption": 10,
}


class BatteryController:
    """Controls Tesla Powerwall battery modes."""

    def __init__(
        self,
        hass: HomeAssistant,
        get_entity_id_func: callable,
    ) -> None:
        """Initialize the battery controller.

        Args:
            hass: Home Assistant instance
            get_entity_id_func: Function to get entity IDs by config key
        """
        self.hass = hass
        self._get_entity_id = get_entity_id_func

    async def _set_export_mode(self, mode: str) -> bool:
        """Set the Teslemetry allow_export mode (pv_only or battery_ok).

        Returns:
            True if successful, False otherwise.
        """
        entity_id = self._get_entity_id("teslemetry_allow_export")
        start_time = time.monotonic()
        _LOGGER.info("[TRANSITION] Setting export mode: %s → %s", entity_id, mode)

        try:
            await self.hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": entity_id, "option": mode},
                blocking=True,
            )
            elapsed = time.monotonic() - start_time
            _LOGGER.info("[TRANSITION] Export mode set to %s in %.2fs", mode, elapsed)
            return True
        except Exception as e:
            elapsed = time.monotonic() - start_time
            _LOGGER.error(
                "[TRANSITION] Failed to set export mode to %s after %.2fs: %s",
                mode,
                elapsed,
                e,
                exc_info=True,
            )
            return False

    async def _set_operation_mode(self, mode: str) -> bool:
        """Set the Teslemetry operation mode.

        Returns:
            True if successful, False otherwise.
        """
        entity_id = self._get_entity_id("teslemetry_operation_mode")
        start_time = time.monotonic()
        _LOGGER.info("[TRANSITION] Setting operation mode: %s → %s", entity_id, mode)

        try:
            await self.hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": entity_id, "option": mode},
                blocking=True,
            )
            elapsed = time.monotonic() - start_time
            _LOGGER.info(
                "[TRANSITION] Operation mode set to %s in %.2fs", mode, elapsed
            )
            return True
        except Exception as e:
            elapsed = time.monotonic() - start_time
            _LOGGER.error(
                "[TRANSITION] Failed to set operation mode to %s after %.2fs: %s",
                mode,
                elapsed,
                e,
                exc_info=True,
            )
            return False

    async def _set_backup_reserve(self, value: int | float) -> bool:
        """Set the Teslemetry backup reserve percentage.

        Returns:
            True if successful, False otherwise.
        """
        entity_id = self._get_entity_id("teslemetry_backup_reserve")
        start_time = time.monotonic()
        _LOGGER.info("[TRANSITION] Setting backup reserve: %s → %s", entity_id, value)

        try:
            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": entity_id, "value": value},
                blocking=True,
            )
            elapsed = time.monotonic() - start_time
            _LOGGER.info(
                "[TRANSITION] Backup reserve set to %s in %.2fs", value, elapsed
            )
            return True
        except Exception as e:
            elapsed = time.monotonic() - start_time
            _LOGGER.error(
                "[TRANSITION] Failed to set backup reserve to %s after %.2fs: %s",
                value,
                elapsed,
                e,
                exc_info=True,
            )
            return False

    async def _set_grid_charging_allowed(self, allowed: bool) -> bool:
        """Set whether grid charging is allowed.

        Controls the switch.my_home_allow_charging_from_grid entity.
        This must be OFF in self-consumption mode to prevent unwanted grid charging.
        This must be ON in force_charge/boost_charge modes to enable grid charging.

        Args:
            allowed: True to allow grid charging, False to disable.

        Returns:
            True if successful, False otherwise.
        """
        entity_id = self._get_entity_id("teslemetry_allow_charging_from_grid")
        start_time = time.monotonic()
        service = "turn_on" if allowed else "turn_off"
        _LOGGER.info(
            "[TRANSITION] Setting grid charging allowed: %s → %s", entity_id, allowed
        )

        try:
            await self.hass.services.async_call(
                "switch",
                service,
                {"entity_id": entity_id},
                blocking=True,
            )
            elapsed = time.monotonic() - start_time
            _LOGGER.info(
                "[TRANSITION] Grid charging allowed set to %s in %.2fs",
                allowed,
                elapsed,
            )
            return True
        except Exception as e:
            elapsed = time.monotonic() - start_time
            _LOGGER.error(
                "[TRANSITION] Failed to set grid charging allowed to %s after %.2fs: %s",
                allowed,
                elapsed,
                e,
                exc_info=True,
            )
            return False

    async def set_self_consumption(
        self, data: CoordinatorData, dry_run: bool = False
    ) -> bool:
        """Set battery to self consumption mode (reserve=10, self_consumption).

        Returns:
            True if successful, False otherwise.
        """
        # Note: manual_override is managed by button handlers and state machine
        # Self-consumption is the default automated mode, so we don't set manual_override here

        if dry_run:
            _LOGGER.info("DRY RUN: set_self_consumption")
            return True

        _LOGGER.info("Setting battery to self consumption mode")

        # Disable grid charging first - this is critical for self-consumption mode
        # Without this, the battery will charge from grid even in self-consumption mode
        if not await self._set_grid_charging_allowed(False):
            _LOGGER.error(
                "Aborting self_consumption mode: Failed to disable grid charging"
            )
            return False

        # Set allow_export to pv_only first (don't allow battery to export)
        if not await self._set_export_mode(TESLEMETRY_EXPORT_PV_ONLY):
            _LOGGER.error("Aborting self_consumption mode: Failed to set export mode")
            return False

        if not await self._set_operation_mode("self_consumption"):
            _LOGGER.error(
                "Aborting self_consumption mode: Failed to set operation mode"
            )
            return False

        if not await self._set_backup_reserve(10):
            _LOGGER.error(
                "Aborting self_consumption mode: Failed to set backup reserve"
            )
            return False

        # Validate transition completed successfully
        if not await self.validate_transition(
            expected_operation_mode="self_consumption",
            expected_backup_reserve=10,
            expected_export_mode=TESLEMETRY_EXPORT_PV_ONLY,
            expected_grid_charging_allowed=False,
            timeout=10,
        ):
            _LOGGER.error("Self consumption mode validation failed")
            return False

        _LOGGER.info(
            "Successfully completed self_consumption mode transition with validation"
        )
        return True

    @staticmethod
    def _clamp_backup_reserve(target: float) -> int:
        """Clamp backup reserve for Tesla firmware compatibility.

        Tesla's July 2025 firmware silently resets backup reserve values
        81-99% to 80%. Valid values are 0-80% or 100%.

        Args:
            target: Desired backup reserve percentage

        Returns:
            Clamped reserve value that Tesla firmware will accept.
        """
        if target <= BACKUP_RESERVE_MAX_VALID:
            # 0-80%: Tesla accepts these values directly
            return int(target)
        elif target >= 100:
            # 100%: Tesla accepts this value
            return 100
        else:
            # 81-99%: Clamp to 80% (Tesla would reset anyway)
            # SOC monitoring in state machine will stop at actual target
            return BACKUP_RESERVE_MAX_VALID

    async def set_force_charge(
        self,
        data: CoordinatorData,
        dry_run: bool = False,
        target_soc: float | None = None,
    ) -> bool:
        """Set battery to force charge mode (backup, reserve=target).

        Uses backup mode for 3.3 kW grid charging. The Powerwall naturally
        stops charging when SOC reaches the backup reserve level.

        For target 81-99%, reserve is clamped to 80% due to Tesla firmware
        restriction. SOC monitoring in state machine handles the gap.

        Args:
            data: Coordinator data
            dry_run: If True, log action without executing
            target_soc: Target SOC to charge to. If None, uses battery_target config.

        Returns:
            True if successful, False otherwise.
        """
        # Note: manual_override is managed by button handlers and state machine
        # This method can be called manually (via button) or automatically (via state machine)

        # Get target SOC - use provided value or fall back to battery_target config
        if target_soc is None:
            # Import here to avoid circular import

            # This will be set by state machine which has access to get_option
            # For now, use 100 as safe default
            target_soc = 100.0

        # Clamp reserve for Tesla firmware compatibility
        reserve = self._clamp_backup_reserve(target_soc)

        if dry_run:
            _LOGGER.info(
                "DRY RUN: set_force_charge (target=%.0f%%, reserve=%d%%)",
                target_soc,
                reserve,
            )
            return True

        # Capture initial state for diagnostics
        initial_state = self._get_hardware_state_snapshot()
        transition_start = time.monotonic()
        _LOGGER.info(
            "[TRANSITION] Starting force charge mode (target=%.0f%%, reserve=%d%%) | Initial state: op=%s, reserve=%s, export=%s, grid_charging=%s",
            target_soc,
            reserve,
            initial_state["operation_mode"],
            initial_state["backup_reserve"],
            initial_state["export_mode"],
            initial_state.get("grid_charging_allowed", "unknown"),
        )

        # Enable grid charging first - required for force charge mode
        if not await self._set_grid_charging_allowed(True):
            _LOGGER.error("Aborting force charge mode: Failed to enable grid charging")
            return False

        # Set allow_export to pv_only first (don't allow battery to export)
        if not await self._set_export_mode(TESLEMETRY_EXPORT_PV_ONLY):
            _LOGGER.error("Aborting force charge mode: Failed to set export mode")
            return False

        # Set backup reserve to target (or clamped value)
        if not await self._set_backup_reserve(reserve):
            _LOGGER.error("Aborting force charge mode: Failed to set backup reserve")
            return False

        # Use backup mode for 3.3 kW charging
        if not await self._set_operation_mode("backup"):
            _LOGGER.error("Aborting force charge mode: Failed to set operation mode")
            return False

        timeout = TRANSITION_TIMEOUTS.get("backup", 10)

        # Validate transition completed successfully
        if not await self.validate_transition(
            expected_operation_mode="backup",
            expected_backup_reserve=reserve,
            expected_export_mode=TESLEMETRY_EXPORT_PV_ONLY,
            timeout=timeout,
        ):
            final_state = self._get_hardware_state_snapshot()
            elapsed = time.monotonic() - transition_start
            _LOGGER.error(
                "[TRANSITION] Force charge FAILED after %.2fs | Final state: op=%s, reserve=%s, export=%s",
                elapsed,
                final_state["operation_mode"],
                final_state["backup_reserve"],
                final_state["export_mode"],
            )
            return False

        elapsed = time.monotonic() - transition_start
        _LOGGER.info(
            "[TRANSITION] Force charge SUCCESS in %.2fs (target=%.0f%%, reserve=%d%%)",
            elapsed,
            target_soc,
            reserve,
        )
        return True

    def _get_hardware_state_snapshot(self) -> dict:
        """Capture current hardware state for diagnostic logging.

        Returns:
            Dict with operation_mode, backup_reserve, export_mode, and grid_charging_allowed.
        """
        operation_mode_entity = self._get_entity_id("teslemetry_operation_mode")
        backup_reserve_entity = self._get_entity_id("teslemetry_backup_reserve")
        export_mode_entity = self._get_entity_id("teslemetry_allow_export")
        grid_charging_entity = self._get_entity_id(
            "teslemetry_allow_charging_from_grid"
        )

        return {
            "operation_mode": self._read_str(operation_mode_entity),
            "backup_reserve": self._read_float(backup_reserve_entity, -1),
            "export_mode": self._read_str(export_mode_entity),
            "grid_charging_allowed": self._read_bool(grid_charging_entity),
        }

    def _read_bool(self, entity_id: str, default: bool = False) -> bool:
        """Read a boolean value from an entity's state (switch on/off)."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        return state.state == "on"

    async def set_boost_charge(
        self, data: CoordinatorData, dry_run: bool = False
    ) -> bool:
        """Set battery to boost charge mode (autonomous, reserve=100).

        Returns:
            True if successful, False otherwise.
        """
        # Note: manual_override is managed by button handlers and state machine
        # This method can be called manually (via button) or automatically (via state machine)

        if dry_run:
            _LOGGER.info("DRY RUN: set_boost_charge")
            return True

        # Capture initial state for diagnostics
        initial_state = self._get_hardware_state_snapshot()
        transition_start = time.monotonic()
        _LOGGER.info(
            "[TRANSITION] Starting boost charge mode | Initial state: op=%s, reserve=%s, export=%s, grid_charging=%s",
            initial_state["operation_mode"],
            initial_state["backup_reserve"],
            initial_state["export_mode"],
            initial_state.get("grid_charging_allowed", "unknown"),
        )

        # Enable grid charging first - required for boost charge mode
        if not await self._set_grid_charging_allowed(True):
            _LOGGER.error("Aborting boost charge mode: Failed to enable grid charging")
            return False

        # Set allow_export to pv_only first (don't allow battery to export)
        if not await self._set_export_mode(TESLEMETRY_EXPORT_PV_ONLY):
            _LOGGER.error("Aborting boost charge mode: Failed to set export mode")
            return False

        if not await self._set_backup_reserve(100):
            _LOGGER.error("Aborting boost charge mode: Failed to set backup reserve")
            return False

        if not await self._set_operation_mode("autonomous"):
            _LOGGER.error("Aborting boost charge mode: Failed to set operation mode")
            return False

        # Use extended timeout for autonomous mode (15s instead of 10s)
        # Tesla API takes longer to propagate autonomous mode changes
        timeout = TRANSITION_TIMEOUTS.get("autonomous", 15)

        # Validate transition completed successfully
        if not await self.validate_transition(
            expected_operation_mode="autonomous",
            expected_backup_reserve=100,
            expected_export_mode=TESLEMETRY_EXPORT_PV_ONLY,
            timeout=timeout,
        ):
            final_state = self._get_hardware_state_snapshot()
            elapsed = time.monotonic() - transition_start
            _LOGGER.error(
                "[TRANSITION] Boost charge FAILED after %.2fs | Final state: op=%s, reserve=%s, export=%s",
                elapsed,
                final_state["operation_mode"],
                final_state["backup_reserve"],
                final_state["export_mode"],
            )
            return False

        elapsed = time.monotonic() - transition_start
        _LOGGER.info("[TRANSITION] Boost charge SUCCESS in %.2fs", elapsed)
        return True

    def _get_minimum_target_soc(self) -> float:
        """Read the minimum target SOC from the configured entity.

        Returns:
            Minimum target SOC percentage (default 10 if entity unavailable).
        """
        entity_id = self._get_entity_id("minimum_target_soc")
        return self._read_float(entity_id, default=10.0)

    async def set_force_discharge(
        self,
        data: CoordinatorData,
        dry_run: bool = False,
        reserve_soc: float | None = None,
    ) -> bool:
        """Set battery to force discharge mode (autonomous, reserve=minimum_target).

        Relies on the Tesla Energy Plan dummy tariff (high sell price
        6am-midnight) to incentivise the Powerwall to export to grid.

        Args:
            data: Coordinator data
            dry_run: If True, log action without executing
            reserve_soc: Optional override for reserve SOC. If None, uses minimum_target_soc.

        Returns:
            True if successful, False otherwise.
        """
        # Note: manual_override is managed by button handlers and state machine
        # This method can be called manually (via button) or automatically (via state machine)

        # Get minimum target SOC for reserve, or use override if provided
        minimum_target = (
            reserve_soc if reserve_soc is not None else self._get_minimum_target_soc()
        )

        if dry_run:
            _LOGGER.info("DRY RUN: set_force_discharge (reserve=%s)", minimum_target)
            return True

        # Capture initial state for diagnostics
        initial_state = self._get_hardware_state_snapshot()
        transition_start = time.monotonic()
        _LOGGER.info(
            "[TRANSITION] Starting force discharge mode (reserve=%s) | Initial state: op=%s, reserve=%s, export=%s",
            minimum_target,
            initial_state["operation_mode"],
            initial_state["backup_reserve"],
            initial_state["export_mode"],
        )

        # Set allow_export to battery_ok first (allow battery to export to grid)
        if not await self._set_export_mode(TESLEMETRY_EXPORT_BATTERY_OK):
            _LOGGER.error("Aborting force discharge mode: Failed to set export mode")
            return False

        if not await self._set_backup_reserve(minimum_target):
            _LOGGER.error("Aborting force discharge mode: Failed to set backup reserve")
            return False

        if not await self._set_operation_mode("autonomous"):
            _LOGGER.error("Aborting force discharge mode: Failed to set operation mode")
            return False

        # Use extended timeout for autonomous mode (15s instead of 10s)
        timeout = TRANSITION_TIMEOUTS.get("autonomous", 15)

        # Validate transition completed successfully
        if not await self.validate_transition(
            expected_operation_mode="autonomous",
            expected_backup_reserve=minimum_target,
            expected_export_mode=TESLEMETRY_EXPORT_BATTERY_OK,
            timeout=timeout,
        ):
            final_state = self._get_hardware_state_snapshot()
            elapsed = time.monotonic() - transition_start
            _LOGGER.error(
                "[TRANSITION] Force discharge FAILED after %.2fs | Final state: op=%s, reserve=%s, export=%s",
                elapsed,
                final_state["operation_mode"],
                final_state["backup_reserve"],
                final_state["export_mode"],
            )
            return False

        elapsed = time.monotonic() - transition_start
        _LOGGER.info("[TRANSITION] Force discharge SUCCESS in %.2fs", elapsed)
        return True

    async def set_proactive_export(
        self, data: CoordinatorData, dry_run: bool = False
    ) -> bool:
        """Set battery to proactive export mode with dynamic throttling.

        Uses dynamic reserve = max(4, SOC-5) to throttle discharge rate.
        This limits export to ~5% of battery capacity per session,
        creating a "trickle export" rather than massive dump at 8kW.

        Returns:
            True if successful, False otherwise.
        """
        # Note: manual_override is managed by button handlers and state machine
        # This method is only called automatically (via state machine)

        # Dynamic reserve for throttling: SOC - 5%, minimum 4%
        current_soc = data.soc
        reserve = max(4.0, current_soc - 5.0)

        if dry_run:
            _LOGGER.info(
                "DRY RUN: set_proactive_export (reserve=%s, SOC=%s)",
                reserve,
                current_soc,
            )
            return True

        # Capture initial state for diagnostics
        initial_state = self._get_hardware_state_snapshot()
        transition_start = time.monotonic()
        _LOGGER.info(
            "[TRANSITION] Starting proactive export mode (reserve=%s, SOC=%s) | Initial state: op=%s, reserve=%s, export=%s",
            reserve,
            current_soc,
            initial_state["operation_mode"],
            initial_state["backup_reserve"],
            initial_state["export_mode"],
        )

        # Set allow_export to battery_ok (allow battery to export to grid)
        if not await self._set_export_mode(TESLEMETRY_EXPORT_BATTERY_OK):
            _LOGGER.error("Aborting proactive export: Failed to set export mode")
            return False

        if not await self._set_backup_reserve(reserve):
            _LOGGER.error(
                "Aborting proactive export: Failed to set backup reserve to %s",
                reserve,
            )
            return False

        if not await self._set_operation_mode("autonomous"):
            _LOGGER.error("Aborting proactive export: Failed to set operation mode")
            return False

        # Use extended timeout for autonomous mode (15s instead of 10s)
        timeout = TRANSITION_TIMEOUTS.get("autonomous", 15)

        # Validate transition completed successfully
        if not await self.validate_transition(
            expected_operation_mode="autonomous",
            expected_backup_reserve=reserve,
            expected_export_mode=TESLEMETRY_EXPORT_BATTERY_OK,
            timeout=timeout,
        ):
            final_state = self._get_hardware_state_snapshot()
            elapsed = time.monotonic() - transition_start
            _LOGGER.error(
                "[TRANSITION] Proactive export FAILED after %.2fs | Final state: op=%s, reserve=%s, export=%s",
                elapsed,
                final_state["operation_mode"],
                final_state["backup_reserve"],
                final_state["export_mode"],
            )
            return False

        elapsed = time.monotonic() - transition_start
        _LOGGER.info(
            "[TRANSITION] Proactive export SUCCESS in %.2fs (reserve=%s)",
            elapsed,
            reserve,
        )
        return True

    def _read_float(self, entity_id: str, default: float = 0.0) -> float:
        """Read a float value from an entity's state."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default

    def _read_str(self, entity_id: str, default: str = "") -> str:
        """Read a string value from an entity's state."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        return str(state.state)

    async def validate_transition(
        self,
        expected_operation_mode: str,
        expected_backup_reserve: float | int,
        expected_export_mode: str | None = None,
        expected_grid_charging_allowed: bool | None = None,
        timeout: int = 10,
    ) -> bool:
        """Validate that hardware state matches expected values after transition.

        Args:
            expected_operation_mode: Expected Teslemetry operation mode
            expected_backup_reserve: Expected backup reserve percentage
            expected_export_mode: Optional expected allow_export mode
            expected_grid_charging_allowed: Optional expected grid charging switch state
            timeout: Maximum seconds to wait for validation (default: 10)

        Returns:
            True if validation passes, False otherwise.
        """
        validation_start = time.monotonic()
        _LOGGER.info(
            "[VALIDATION] Starting validation: op=%s, reserve=%s, export=%s, grid_charging=%s, timeout=%ds",
            expected_operation_mode,
            expected_backup_reserve,
            expected_export_mode,
            expected_grid_charging_allowed,
            timeout,
        )

        operation_mode_entity = self._get_entity_id("teslemetry_operation_mode")
        backup_reserve_entity = self._get_entity_id("teslemetry_backup_reserve")
        export_mode_entity = self._get_entity_id("teslemetry_allow_export")
        grid_charging_entity = self._get_entity_id(
            "teslemetry_allow_charging_from_grid"
        )

        first_failure_logged = False
        last_operation_mode = None

        for attempt in range(timeout):
            await asyncio.sleep(1)

            # Read current hardware state
            current_operation_mode = self._read_str(operation_mode_entity)
            current_backup_reserve = self._read_float(backup_reserve_entity, -1)
            current_export_mode = (
                self._read_str(export_mode_entity) if expected_export_mode else None
            )
            current_grid_charging = (
                self._read_bool(grid_charging_entity)
                if expected_grid_charging_allowed is not None
                else None
            )

            # Track operation mode changes for diagnostics
            if current_operation_mode != last_operation_mode:
                elapsed = time.monotonic() - validation_start
                _LOGGER.info(
                    "[VALIDATION] t=%.1fs: operation_mode changed %s → %s",
                    elapsed,
                    last_operation_mode,
                    current_operation_mode,
                )
                last_operation_mode = current_operation_mode

            _LOGGER.debug(
                "Validation attempt %d/%d: operation_mode=%s, backup_reserve=%s, export_mode=%s, grid_charging=%s",
                attempt + 1,
                timeout,
                current_operation_mode,
                current_backup_reserve,
                current_export_mode,
                current_grid_charging,
            )

            # Check if state matches expectations
            matches_operation = current_operation_mode == expected_operation_mode
            matches_reserve = abs(current_backup_reserve - expected_backup_reserve) < 1
            matches_export = (
                current_export_mode == expected_export_mode
                if expected_export_mode
                else True
            )
            matches_grid_charging = (
                current_grid_charging == expected_grid_charging_allowed
                if expected_grid_charging_allowed is not None
                else True
            )

            if (
                matches_operation
                and matches_reserve
                and matches_export
                and matches_grid_charging
            ):
                elapsed = time.monotonic() - validation_start
                _LOGGER.info(
                    "[VALIDATION] SUCCESS after %.1fs (attempt %d/%d)",
                    elapsed,
                    attempt + 1,
                    timeout,
                )
                return True

            # Log failure only once (first failure) to avoid log flooding
            if not first_failure_logged:
                _LOGGER.warning(
                    "[VALIDATION] State mismatch at attempt %d: "
                    "expected (op=%s, reserve=%s, export=%s, grid_charging=%s), "
                    "actual (op=%s, reserve=%s, export=%s, grid_charging=%s)",
                    attempt + 1,
                    expected_operation_mode,
                    expected_backup_reserve,
                    expected_export_mode,
                    expected_grid_charging_allowed,
                    current_operation_mode,
                    current_backup_reserve,
                    current_export_mode,
                    current_grid_charging,
                )
                first_failure_logged = True

            # If operation mode matches but reserve/export don't, Tesla has accepted the command
            # The reserve/export will sync shortly - give more time
            if matches_operation and not (matches_reserve and matches_export):
                _LOGGER.debug(
                    "Operation mode matched, waiting for reserve/export to sync..."
                )

        # Final check: if operation mode is correct, consider it a success
        # Tesla may lag in updating reserve, but the mode command went through
        final_operation_mode = self._read_str(operation_mode_entity)
        elapsed = time.monotonic() - validation_start
        if final_operation_mode == expected_operation_mode:
            _LOGGER.info(
                "[VALIDATION] ACCEPTED via operation_mode match after %.1fs (reserve/export may lag)",
                elapsed,
            )
            return True

        _LOGGER.error(
            "[VALIDATION] FAILED after %.1fs (%d attempts): "
            "expected (op=%s, reserve=%s, export=%s, grid_charging=%s), "
            "actual (op=%s)",
            elapsed,
            timeout,
            expected_operation_mode,
            expected_backup_reserve,
            expected_export_mode,
            expected_grid_charging_allowed,
            final_operation_mode,
        )
        return False

    async def verify_current_state(
        self,
        expected_operation_mode: str,
        expected_backup_reserve: float | int,
        expected_export_mode: str | None = None,
        expected_grid_charging_allowed: bool | None = None,
    ) -> bool:
        """Verify current hardware state matches expected values.

        This is a quick health check (no waiting) to detect drift.

        Args:
            expected_operation_mode: Expected Teslemetry operation mode
            expected_backup_reserve: Expected backup reserve percentage
            expected_export_mode: Optional expected allow_export mode
            expected_grid_charging_allowed: Optional expected grid charging switch state

        Returns:
            True if state matches expectations, False otherwise.
        """
        operation_mode_entity = self._get_entity_id("teslemetry_operation_mode")
        backup_reserve_entity = self._get_entity_id("teslemetry_backup_reserve")
        export_mode_entity = self._get_entity_id("teslemetry_allow_export")
        grid_charging_entity = self._get_entity_id(
            "teslemetry_allow_charging_from_grid"
        )

        # Read current hardware state
        current_operation_mode = self._read_str(operation_mode_entity)
        current_backup_reserve = self._read_float(backup_reserve_entity, -1)
        current_export_mode = (
            self._read_str(export_mode_entity) if expected_export_mode else None
        )
        current_grid_charging = (
            self._read_bool(grid_charging_entity)
            if expected_grid_charging_allowed is not None
            else None
        )

        # Check if state matches expectations
        matches_operation = current_operation_mode == expected_operation_mode
        matches_reserve = abs(current_backup_reserve - expected_backup_reserve) < 1
        matches_export = (
            current_export_mode == expected_export_mode
            if expected_export_mode
            else True
        )
        matches_grid_charging = (
            current_grid_charging == expected_grid_charging_allowed
            if expected_grid_charging_allowed is not None
            else True
        )

        # DIAGNOSTIC: Always log at INFO level for visibility
        _LOGGER.info(
            "Health check verify: expected=(op=%s, reserve=%s, export=%s, grid_charging=%s), actual=(op=%s, reserve=%s, export=%s, grid_charging=%s), match=%s",
            expected_operation_mode,
            expected_backup_reserve,
            expected_export_mode,
            expected_grid_charging_allowed,
            current_operation_mode,
            current_backup_reserve,
            current_export_mode,
            current_grid_charging,
            matches_operation
            and matches_reserve
            and matches_export
            and matches_grid_charging,
        )

        if (
            matches_operation
            and matches_reserve
            and matches_export
            and matches_grid_charging
        ):
            return True

        # State mismatch detected
        _LOGGER.warning(
            "State mismatch detected: "
            "expected (operation_mode=%s, backup_reserve=%s, export_mode=%s, grid_charging=%s), "
            "actual (operation_mode=%s, backup_reserve=%s, export_mode=%s, grid_charging=%s)",
            expected_operation_mode,
            expected_backup_reserve,
            expected_export_mode,
            expected_grid_charging_allowed,
            current_operation_mode,
            current_backup_reserve,
            current_export_mode,
            current_grid_charging,
        )
        return False

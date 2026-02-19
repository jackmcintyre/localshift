"""Battery control functionality for Tesla Powerwall."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .const import (
    TESLEMETRY_EXPORT_BATTERY_OK,
    TESLEMETRY_EXPORT_PV_ONLY,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator_data import CoordinatorData


_LOGGER = logging.getLogger(__name__)


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
        _LOGGER.info("Setting export mode: %s → %s", entity_id, mode)

        try:
            await self.hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": entity_id, "option": mode},
                blocking=True,
            )
            _LOGGER.info("Successfully set export mode to %s", mode)
            return True
        except Exception as e:
            _LOGGER.error("Failed to set export mode to %s: %s", mode, e, exc_info=True)
            return False

    async def _set_operation_mode(self, mode: str) -> bool:
        """Set the Teslemetry operation mode.

        Returns:
            True if successful, False otherwise.
        """
        entity_id = self._get_entity_id("teslemetry_operation_mode")
        _LOGGER.info("Setting operation mode: %s → %s", entity_id, mode)

        try:
            await self.hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": entity_id, "option": mode},
                blocking=True,
            )
            _LOGGER.info("Successfully set operation mode to %s", mode)
            return True
        except Exception as e:
            _LOGGER.error(
                "Failed to set operation mode to %s: %s", mode, e, exc_info=True
            )
            return False

    async def _set_backup_reserve(self, value: int | float) -> bool:
        """Set the Teslemetry backup reserve percentage.

        Returns:
            True if successful, False otherwise.
        """
        entity_id = self._get_entity_id("teslemetry_backup_reserve")
        _LOGGER.info("Setting backup reserve: %s → %s", entity_id, value)

        try:
            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": entity_id, "value": value},
                blocking=True,
            )
            _LOGGER.info("Successfully set backup reserve to %s", value)
            return True
        except Exception as e:
            _LOGGER.error(
                "Failed to set backup reserve to %s: %s", value, e, exc_info=True
            )
            return False

    async def set_self_consumption(
        self, data: CoordinatorData, dry_run: bool = False
    ) -> bool:
        """Set battery to self consumption mode (reserve=10, self_consumption).

        Returns:
            True if successful, False otherwise.
        """
        data.manual_override = False

        if dry_run:
            _LOGGER.info("DRY RUN: set_self_consumption")
            return True

        _LOGGER.info("Setting battery to self consumption mode")

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
            timeout=10,
        ):
            _LOGGER.error("Self consumption mode validation failed")
            return False

        _LOGGER.info(
            "Successfully completed self_consumption mode transition with validation"
        )
        return True

    async def set_force_charge(
        self, data: CoordinatorData, dry_run: bool = False
    ) -> bool:
        """Set battery to force charge mode (backup).

        Returns:
            True if successful, False otherwise.
        """
        data.manual_override = False

        if dry_run:
            _LOGGER.info("DRY RUN: set_force_charge")
            return True

        _LOGGER.info("Setting battery to force charge mode")

        # Set allow_export to pv_only first (don't allow battery to export)
        if not await self._set_export_mode(TESLEMETRY_EXPORT_PV_ONLY):
            _LOGGER.error("Aborting force charge mode: Failed to set export mode")
            return False

        # Set backup reserve to 10% (ensures consistent state for health checks)
        if not await self._set_backup_reserve(10):
            _LOGGER.error("Aborting force charge mode: Failed to set backup reserve")
            return False

        if not await self._set_operation_mode("backup"):
            _LOGGER.error("Aborting force charge mode: Failed to set operation mode")
            return False

        # Validate transition completed successfully
        if not await self.validate_transition(
            expected_operation_mode="backup",
            expected_backup_reserve=10,  # Default reserve for backup mode
            expected_export_mode=TESLEMETRY_EXPORT_PV_ONLY,
            timeout=10,
        ):
            _LOGGER.error("Force charge mode validation failed")
            return False

        _LOGGER.info(
            "Successfully completed force charge mode transition with validation"
        )
        return True

    async def set_boost_charge(
        self, data: CoordinatorData, dry_run: bool = False
    ) -> bool:
        """Set battery to boost charge mode (autonomous, reserve=100).

        Returns:
            True if successful, False otherwise.
        """
        data.manual_override = False

        if dry_run:
            _LOGGER.info("DRY RUN: set_boost_charge")
            return True

        _LOGGER.info("Setting battery to boost charge mode")

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

        # Validate transition completed successfully
        if not await self.validate_transition(
            expected_operation_mode="autonomous",
            expected_backup_reserve=100,
            expected_export_mode=TESLEMETRY_EXPORT_PV_ONLY,
            timeout=10,
        ):
            _LOGGER.error("Boost charge mode validation failed")
            return False

        _LOGGER.info(
            "Successfully completed boost charge mode transition with validation"
        )
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
        data.manual_override = False

        # Get minimum target SOC for reserve, or use override if provided
        minimum_target = (
            reserve_soc if reserve_soc is not None else self._get_minimum_target_soc()
        )

        if dry_run:
            _LOGGER.info("DRY RUN: set_force_discharge (reserve=%s)", minimum_target)
            return True

        _LOGGER.info(
            "Setting battery to force discharge mode (reserve=%s)", minimum_target
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

        # Validate transition completed successfully
        if not await self.validate_transition(
            expected_operation_mode="autonomous",
            expected_backup_reserve=minimum_target,
            expected_export_mode=TESLEMETRY_EXPORT_BATTERY_OK,
            timeout=10,
        ):
            _LOGGER.error("Force discharge mode validation failed")
            return False

        _LOGGER.info(
            "Successfully completed force discharge mode transition with validation"
        )
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
        data.manual_override = False

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

        _LOGGER.info(
            "Setting battery to proactive export mode (reserve=%s, SOC=%s) - throttled export",
            reserve,
            current_soc,
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

        # Validate transition completed successfully
        if not await self.validate_transition(
            expected_operation_mode="autonomous",
            expected_backup_reserve=reserve,
            expected_export_mode=TESLEMETRY_EXPORT_BATTERY_OK,
            timeout=10,
        ):
            _LOGGER.error("Proactive export mode validation failed")
            return False

        _LOGGER.info(
            "Successfully completed proactive export mode transition with validation (reserve=%s)",
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
        timeout: int = 10,
    ) -> bool:
        """Validate that hardware state matches expected values after transition.

        Args:
            expected_operation_mode: Expected Teslemetry operation mode
            expected_backup_reserve: Expected backup reserve percentage
            expected_export_mode: Optional expected allow_export mode
            timeout: Maximum seconds to wait for validation (default: 10)

        Returns:
            True if validation passes, False otherwise.
        """
        _LOGGER.info(
            "Validating transition: operation_mode=%s, backup_reserve=%s, export_mode=%s",
            expected_operation_mode,
            expected_backup_reserve,
            expected_export_mode,
        )

        operation_mode_entity = self._get_entity_id("teslemetry_operation_mode")
        backup_reserve_entity = self._get_entity_id("teslemetry_backup_reserve")
        export_mode_entity = self._get_entity_id("teslemetry_allow_export")

        first_failure_logged = False

        for attempt in range(timeout):
            await asyncio.sleep(1)

            # Read current hardware state
            current_operation_mode = self._read_str(operation_mode_entity)
            current_backup_reserve = self._read_float(backup_reserve_entity, -1)
            current_export_mode = (
                self._read_str(export_mode_entity) if expected_export_mode else None
            )

            _LOGGER.debug(
                "Validation attempt %d/%d: operation_mode=%s, backup_reserve=%s, export_mode=%s",
                attempt + 1,
                timeout,
                current_operation_mode,
                current_backup_reserve,
                current_export_mode,
            )

            # Check if state matches expectations
            matches_operation = current_operation_mode == expected_operation_mode
            matches_reserve = abs(current_backup_reserve - expected_backup_reserve) < 1
            matches_export = (
                current_export_mode == expected_export_mode
                if expected_export_mode
                else True
            )

            if matches_operation and matches_reserve and matches_export:
                _LOGGER.info(
                    "Transition validation successful after %d seconds", attempt + 1
                )
                return True

            # Log failure only once (first failure) to avoid log flooding
            if not first_failure_logged:
                _LOGGER.warning(
                    "Transition validation - state mismatch: "
                    "expected (operation_mode=%s, backup_reserve=%s, export_mode=%s), "
                    "actual (operation_mode=%s, backup_reserve=%s, export_mode=%s)",
                    expected_operation_mode,
                    expected_backup_reserve,
                    expected_export_mode,
                    current_operation_mode,
                    current_backup_reserve,
                    current_export_mode,
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
        if final_operation_mode == expected_operation_mode:
            _LOGGER.info(
                "Transition validated by operation_mode=%s (reserve/export may lag)",
                final_operation_mode,
            )
            return True

        _LOGGER.error(
            "Transition validation failed after %d seconds: "
            "expected (operation_mode=%s, backup_reserve=%s, export_mode=%s), "
            "actual (operation_mode=%s)",
            timeout,
            expected_operation_mode,
            expected_backup_reserve,
            expected_export_mode,
            final_operation_mode,
        )
        return False

    async def verify_current_state(
        self,
        expected_operation_mode: str,
        expected_backup_reserve: float | int,
        expected_export_mode: str | None = None,
    ) -> bool:
        """Verify current hardware state matches expected values.

        This is a quick health check (no waiting) to detect drift.

        Args:
            expected_operation_mode: Expected Teslemetry operation mode
            expected_backup_reserve: Expected backup reserve percentage
            expected_export_mode: Optional expected allow_export mode

        Returns:
            True if state matches expectations, False otherwise.
        """
        operation_mode_entity = self._get_entity_id("teslemetry_operation_mode")
        backup_reserve_entity = self._get_entity_id("teslemetry_backup_reserve")
        export_mode_entity = self._get_entity_id("teslemetry_allow_export")

        # Read current hardware state
        current_operation_mode = self._read_str(operation_mode_entity)
        current_backup_reserve = self._read_float(backup_reserve_entity, -1)
        current_export_mode = (
            self._read_str(export_mode_entity) if expected_export_mode else None
        )

        # Check if state matches expectations
        matches_operation = current_operation_mode == expected_operation_mode
        matches_reserve = abs(current_backup_reserve - expected_backup_reserve) < 1
        matches_export = (
            current_export_mode == expected_export_mode
            if expected_export_mode
            else True
        )

        # DIAGNOSTIC: Always log at INFO level for visibility
        _LOGGER.info(
            "Health check verify: expected=(op=%s, reserve=%s, export=%s), actual=(op=%s, reserve=%s, export=%s), match=%s",
            expected_operation_mode,
            expected_backup_reserve,
            expected_export_mode,
            current_operation_mode,
            current_backup_reserve,
            current_export_mode,
            matches_operation and matches_reserve and matches_export,
        )

        if matches_operation and matches_reserve and matches_export:
            return True

        # State mismatch detected
        _LOGGER.warning(
            "State mismatch detected: "
            "expected (operation_mode=%s, backup_reserve=%s, export_mode=%s), "
            "actual (operation_mode=%s, backup_reserve=%s, export_mode=%s)",
            expected_operation_mode,
            expected_backup_reserve,
            expected_export_mode,
            current_operation_mode,
            current_backup_reserve,
            current_export_mode,
        )
        return False

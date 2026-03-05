"""Transition validation for Tesla Powerwall state changes."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


_LOGGER = logging.getLogger(__name__)


class TransitionValidator:
    """Validates Tesla Powerwall state transitions."""

    def __init__(self, hass: HomeAssistant, get_entity_id_func: callable) -> None:
        self.hass = hass
        self._get_entity_id = get_entity_id_func

    def get_hardware_state_snapshot(self) -> dict:
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
            "operation_mode": self.read_str(operation_mode_entity),
            "backup_reserve": self.read_float(backup_reserve_entity, -1),
            "export_mode": self.read_str(export_mode_entity),
            "grid_charging_allowed": self.read_bool(grid_charging_entity),
        }

    def read_bool(self, entity_id: str, default: bool = False) -> bool:
        """Read a boolean value from an entity's state (switch on/off)."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        return state.state == "on"

    def read_float(self, entity_id: str, default: float = 0.0) -> float:
        """Read a float value from an entity's state."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default

    def read_str(self, entity_id: str, default: str = "") -> str:
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
            current_operation_mode = self.read_str(operation_mode_entity)
            current_backup_reserve = self.read_float(backup_reserve_entity, -1)
            current_export_mode = (
                self.read_str(export_mode_entity) if expected_export_mode else None
            )
            current_grid_charging = (
                self.read_bool(grid_charging_entity)
                if expected_grid_charging_allowed is not None
                else None
            )

            # Track operation mode changes for diagnostics
            if current_operation_mode != last_operation_mode:
                elapsed = time.monotonic() - validation_start
                _LOGGER.info(
                    "[VALIDATION] t=%.1fs: operation_mode changed %s -> %s",
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

        # Final check: operation_mode fallback is ONLY allowed when grid_charging is not critical
        # Issue #375: grid_charging must be validated - it's critical for self_consumption mode
        final_operation_mode = self.read_str(operation_mode_entity)
        final_grid_charging = (
            self.read_bool(grid_charging_entity)
            if expected_grid_charging_allowed is not None
            else None
        )
        elapsed = time.monotonic() - validation_start

        # Check if grid_charging matches (only if it was explicitly expected)
        grid_charging_matches = (
            final_grid_charging == expected_grid_charging_allowed
            if expected_grid_charging_allowed is not None
            else True
        )

        if final_operation_mode == expected_operation_mode:
            if grid_charging_matches:
                # Operation mode AND grid_charging both match - accept with warning about reserve/export
                _LOGGER.info(
                    "[VALIDATION] ACCEPTED via operation_mode + grid_charging match after %.1fs (reserve/export may lag)",
                    elapsed,
                )
                return True

            # Operation mode matches but grid_charging doesn't - this is a FAILURE
            # Grid charging control is critical for preventing unwanted grid charging
            _LOGGER.error(
                "[VALIDATION] FAILED after %.1fs: operation_mode matched but grid_charging MISMATCH "
                "(expected=%s, actual=%s). Grid charging control is critical for %s mode.",
                elapsed,
                expected_grid_charging_allowed,
                final_grid_charging,
                expected_operation_mode,
            )
            return False

        _LOGGER.error(
            "[VALIDATION] FAILED after %.1fs (%d attempts): "
            "expected (op=%s, reserve=%s, export=%s, grid_charging=%s), "
            "actual (op=%s, grid_charging=%s)",
            elapsed,
            timeout,
            expected_operation_mode,
            expected_backup_reserve,
            expected_export_mode,
            expected_grid_charging_allowed,
            final_operation_mode,
            final_grid_charging,
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
        current_operation_mode = self.read_str(operation_mode_entity)
        current_backup_reserve = self.read_float(backup_reserve_entity, -1)
        current_export_mode = (
            self.read_str(export_mode_entity) if expected_export_mode else None
        )
        current_grid_charging = (
            self.read_bool(grid_charging_entity)
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

"""Service client for Tesla Powerwall control via HA services."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from .const import TESLEMETRY_EXPORT_BATTERY_OK, TESLEMETRY_EXPORT_PV_ONLY

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


_LOGGER = logging.getLogger(__name__)


class PowerwallServiceClient:
    """Encapsulates HA service calls for Powerwall control."""

    def __init__(self, hass: HomeAssistant, get_entity_id_func: callable) -> None:
        self.hass = hass
        self._get_entity_id = get_entity_id_func

    async def set_export_mode(self, mode: str) -> bool:
        """Set the Teslemetry allow_export mode (pv_only or battery_ok).

        Returns:
            True if successful, False otherwise.

        """
        if mode not in (TESLEMETRY_EXPORT_PV_ONLY, TESLEMETRY_EXPORT_BATTERY_OK):
            _LOGGER.warning("[TRANSITION] Unknown export mode requested: %s", mode)

        entity_id = self._get_entity_id("teslemetry_allow_export")
        start_time = time.monotonic()
        _LOGGER.info("[TRANSITION] Setting export mode: %s -> %s", entity_id, mode)

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
        except Exception as e:  # noqa: BLE001 - HA service calls can raise any exception
            elapsed = time.monotonic() - start_time
            _LOGGER.error(
                "[TRANSITION] Failed to set export mode to %s after %.2fs: %s",
                mode,
                elapsed,
                e,
                exc_info=True,
            )
            return False

    async def set_operation_mode(self, mode: str) -> bool:
        """Set the Teslemetry operation mode.

        Returns:
            True if successful, False otherwise.

        """
        entity_id = self._get_entity_id("teslemetry_operation_mode")
        start_time = time.monotonic()
        _LOGGER.info("[TRANSITION] Setting operation mode: %s -> %s", entity_id, mode)

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
        except Exception as e:  # noqa: BLE001 - HA service calls can raise any exception
            elapsed = time.monotonic() - start_time
            _LOGGER.error(
                "[TRANSITION] Failed to set operation mode to %s after %.2fs: %s",
                mode,
                elapsed,
                e,
                exc_info=True,
            )
            return False

    async def set_backup_reserve(self, value: int | float) -> bool:
        """Set the Teslemetry backup reserve percentage.

        Returns:
            True if successful, False otherwise.

        """
        entity_id = self._get_entity_id("teslemetry_backup_reserve")
        start_time = time.monotonic()
        _LOGGER.info("[TRANSITION] Setting backup reserve: %s -> %s", entity_id, value)

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
        except Exception as e:  # noqa: BLE001 - HA service calls can raise any exception
            elapsed = time.monotonic() - start_time
            _LOGGER.error(
                "[TRANSITION] Failed to set backup reserve to %s after %.2fs: %s",
                value,
                elapsed,
                e,
                exc_info=True,
            )
            return False

    async def set_grid_charging_allowed(self, allowed: bool) -> bool:
        """Set whether grid charging is allowed.

        Controls the switch.my_home_allow_charging_from_grid entity.
        This must be OFF in self-consumption mode to prevent unwanted grid charging.
        This must be ON in force_charge/boost_charge modes to enable grid charging.

        Issue #375: Added retry logic with verification to ensure the switch
        state actually changes, as Teslemetry/Tesla API may have propagation delays.

        Args:
            allowed: True to allow grid charging, False to disable.

        Returns:
            True if successful, False otherwise.

        """
        entity_id = self._get_entity_id("teslemetry_allow_charging_from_grid")
        service = "turn_on" if allowed else "turn_off"

        # Issue #375: Retry logic with verification
        max_retries = 3
        retry_delay = 2.0  # seconds between retries

        for attempt in range(max_retries):
            start_time = time.monotonic()
            _LOGGER.info(
                "[TRANSITION] Setting grid charging allowed: %s -> %s (attempt %d/%d)",
                entity_id,
                allowed,
                attempt + 1,
                max_retries,
            )

            try:
                await self.hass.services.async_call(
                    "switch",
                    service,
                    {"entity_id": entity_id},
                    blocking=True,
                )
                elapsed = time.monotonic() - start_time

                # Verify the state actually changed
                await asyncio.sleep(1.0)  # Wait for state to propagate
                actual_state = self._read_bool(entity_id)

                if actual_state == allowed:
                    _LOGGER.info(
                        "[TRANSITION] Grid charging allowed set to %s in %.2fs (verified)",
                        allowed,
                        elapsed,
                    )
                    return True

                _LOGGER.warning(
                    "[TRANSITION] Grid charging switch not reflected after %.2fs: "
                    "expected=%s, actual=%s (attempt %d/%d)",
                    elapsed,
                    allowed,
                    actual_state,
                    attempt + 1,
                    max_retries,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)

            except Exception as e:  # noqa: BLE001 - HA service calls can raise any exception
                elapsed = time.monotonic() - start_time
                _LOGGER.error(
                    "[TRANSITION] Failed to set grid charging allowed to %s after %.2fs: %s (attempt %d/%d)",
                    allowed,
                    elapsed,
                    e,
                    attempt + 1,
                    max_retries,
                    exc_info=True,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)

        _LOGGER.error(
            "[TRANSITION] Grid charging allowed FAILED after %d attempts: expected=%s",
            max_retries,
            allowed,
        )
        return False

    def _read_bool(self, entity_id: str, default: bool = False) -> bool:
        """Read a boolean value from an entity's state (switch on/off)."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        return state.state == "on"

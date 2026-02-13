"""Battery control functionality for Tesla Powerwall."""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING

from .const import (
    TESLEMETRY_EXPORT_BATTERY_OK,
    TESLEMETRY_EXPORT_PV_ONLY,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator_data import CoordinatorData


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

    async def _set_export_mode(self, mode: str) -> None:
        """Set the Teslemetry allow_export mode (pv_only or battery_ok)."""
        await self.hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": self._get_entity_id("teslemetry_allow_export"),
                "option": mode,
            },
        )

    async def _set_operation_mode(self, mode: str) -> None:
        """Set the Teslemetry operation mode."""
        await self.hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": self._get_entity_id("teslemetry_operation_mode"),
                "option": mode,
            },
        )

    async def _set_backup_reserve(self, value: int | float) -> None:
        """Set the Teslemetry backup reserve percentage."""
        await self.hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": self._get_entity_id("teslemetry_backup_reserve"),
                "value": value,
            },
        )

    async def set_self_consumption(
        self, data: CoordinatorData, dry_run: bool = False
    ) -> None:
        """Set battery to self consumption mode (reserve=10, self_consumption)."""
        data.manual_override = False
        data.hold_mode = False
        data.solar_export_hold = False

        if dry_run:
            return

        # Set allow_export to pv_only first (don't allow battery to export)
        await self._set_export_mode(TESLEMETRY_EXPORT_PV_ONLY)
        await asyncio.sleep(5)
        await self._set_operation_mode("self_consumption")
        await asyncio.sleep(5)
        await self._set_backup_reserve(10)
        await asyncio.sleep(5)

    async def set_hold(self, data: CoordinatorData, dry_run: bool = False) -> None:
        """Set battery to hold mode (reserve=floor(SOC), self_consumption)."""
        data.hold_mode = True

        # Re-read SOC just before setting reserve to ensure accuracy
        current_soc = self._read_float(self._get_entity_id("teslemetry_soc"))
        reserve = max(10, min(100, math.floor(current_soc)))

        if dry_run:
            return

        # Set allow_export to pv_only first (don't allow battery to export)
        await self._set_export_mode(TESLEMETRY_EXPORT_PV_ONLY)
        await asyncio.sleep(5)
        await self._set_backup_reserve(reserve)
        await asyncio.sleep(5)
        await self._set_operation_mode("self_consumption")

    async def set_force_charge(
        self, data: CoordinatorData, dry_run: bool = False
    ) -> None:
        """Set battery to force charge mode (backup)."""
        data.hold_mode = False
        data.solar_export_hold = False

        if dry_run:
            return

        # Set allow_export to pv_only first (don't allow battery to export)
        await self._set_export_mode(TESLEMETRY_EXPORT_PV_ONLY)
        await asyncio.sleep(5)
        await self._set_operation_mode("backup")

    async def set_boost_charge(
        self, data: CoordinatorData, dry_run: bool = False
    ) -> None:
        """Set battery to boost charge mode (autonomous, reserve=100)."""
        data.hold_mode = False
        data.solar_export_hold = False

        if dry_run:
            return

        # Set allow_export to pv_only first (don't allow battery to export)
        await self._set_export_mode(TESLEMETRY_EXPORT_PV_ONLY)
        await asyncio.sleep(5)
        await self._set_backup_reserve(100)
        await asyncio.sleep(5)
        await self._set_operation_mode("autonomous")

    async def set_force_discharge(
        self, data: CoordinatorData, dry_run: bool = False
    ) -> None:
        """Set battery to force discharge mode (autonomous, reserve=10).

        Relies on the Tesla Energy Plan dummy tariff (high sell price
        6am-midnight) to incentivise the Powerwall to export to grid.
        """
        data.hold_mode = False
        data.solar_export_hold = False

        if dry_run:
            return

        # Set allow_export to battery_ok first (allow battery to export to grid)
        await self._set_export_mode(TESLEMETRY_EXPORT_BATTERY_OK)
        await asyncio.sleep(5)
        await self._set_backup_reserve(10)
        await asyncio.sleep(5)
        await self._set_operation_mode("autonomous")

    def _read_float(self, entity_id: str, default: float = 0.0) -> float:
        """Read a float value from an entity's state."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default

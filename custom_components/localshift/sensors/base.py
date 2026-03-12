from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import LocalShiftCoordinator


class LocalShiftSensorBase(SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
        entry: ConfigEntry,
    ) -> None:
        self.coordinator = coordinator
        self._entry = entry
        self._unsub: Any = None

    @property
    def device_info(self) -> DeviceInfo:
        from ..const import DOMAIN

        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="LocalShift",
            manufacturer="Custom",
            model="Solar Battery Automation",
            sw_version="0.0.2",
        )

    async def async_added_to_hass(self) -> None:
        self._unsub = self.coordinator.async_add_listener(
            self._handle_coordinator_update
        )
        self._update_from_coordinator()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_from_coordinator()
        self.async_write_ha_state()

    def _update_from_coordinator(self) -> None:
        """Pull latest values from coordinator.data. Override in subclasses."""

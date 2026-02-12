"""Switch platform for the Amber Powerwall integration.

Provides user-facing toggle switches for:
- Automation Enabled (master toggle)
- Spike Discharge Enabled
- Dry Run
- Demand Window Block
"""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    SWITCH_AUTOMATION_ENABLED,
    SWITCH_DEFAULTS,
    SWITCH_DEMAND_WINDOW_BLOCK,
    SWITCH_DRY_RUN,
    SWITCH_ICONS,
    SWITCH_NAMES,
    SWITCH_SPIKE_DISCHARGE_ENABLED,
)
from .coordinator import AmberPowerwallCoordinator

_LOGGER = logging.getLogger(__name__)

SWITCH_KEYS = [
    SWITCH_AUTOMATION_ENABLED,
    SWITCH_SPIKE_DISCHARGE_ENABLED,
    SWITCH_DRY_RUN,
    SWITCH_DEMAND_WINDOW_BLOCK,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Amber Powerwall switch entities."""
    coordinator: AmberPowerwallCoordinator = entry.runtime_data

    entities = [
        AmberPowerwallSwitch(coordinator, entry, key) for key in SWITCH_KEYS
    ]

    async_add_entities(entities)


class AmberPowerwallSwitch(SwitchEntity):
    """A toggle switch for automation features."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AmberPowerwallCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Initialise the switch."""
        self.coordinator = coordinator
        self._entry = entry
        self._key = key
        self._is_on = SWITCH_DEFAULTS[key]

        self._attr_unique_id = f"amber_powerwall_{key}"
        self._attr_name = SWITCH_NAMES[key]
        self._attr_icon = SWITCH_ICONS[key]

        # Sync initial default state to coordinator's switch state bridge
        self.coordinator.set_switch_state(key, self._is_on)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information to link all entities under one device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Amber Powerwall",
            manufacturer="Custom",
            model="Solar Battery Automation",
            sw_version="0.1.0",
        )

    @property
    def is_on(self) -> bool:
        """Return True if the switch is on."""
        return self._is_on

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the switch on."""
        self._is_on = True
        self.coordinator.set_switch_state(self._key, True)
        self.async_write_ha_state()

        if self._key == SWITCH_AUTOMATION_ENABLED:
            _LOGGER.info("Amber Powerwall automation enabled")

        # Re-evaluate derived values and trigger state machine
        self.coordinator._compute_derived_values()
        self.coordinator._notify_listeners()
        await self.coordinator.async_evaluate_state_machine()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the switch off."""
        self._is_on = False
        self.coordinator.set_switch_state(self._key, False)
        self.async_write_ha_state()

        if self._key == SWITCH_AUTOMATION_ENABLED:
            _LOGGER.info(
                "Amber Powerwall automation disabled, "
                "returning to self consumption"
            )
            await self.coordinator.async_set_self_consumption()

        # Re-evaluate derived values and trigger state machine
        self.coordinator._compute_derived_values()
        self.coordinator._notify_listeners()
        await self.coordinator.async_evaluate_state_machine()

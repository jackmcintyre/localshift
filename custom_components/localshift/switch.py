"""Switch platform for the LocalShift integration.

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
    SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET,
    SWITCH_AUTOMATION_ENABLED,
    SWITCH_DEFAULTS,
    SWITCH_DEMAND_WINDOW_BLOCK,
    SWITCH_DRY_RUN,
    SWITCH_ICONS,
    SWITCH_NAMES,
    SWITCH_NOTIFY_ALERTS,
    SWITCH_NOTIFY_DAILY_SUMMARY,
    SWITCH_NOTIFY_MANUAL_ACTIONS,
    SWITCH_NOTIFY_TRANSITIONS,
    SWITCH_SOLAR_TAPER_ENABLED,
    SWITCH_SPIKE_DISCHARGE_CONSERVATIVE,
    SWITCH_SPIKE_DISCHARGE_ENABLED,
    SWITCH_THERMAL_MANAGEMENT_ENABLED,
)
from .coordinator import LocalShiftCoordinator

_LOGGER = logging.getLogger(__name__)

# Switch state persistence keys in options
SWITCH_STATE_PREFIX = "switch_state_"

SWITCH_KEYS = [
    SWITCH_AUTOMATION_ENABLED,
    SWITCH_SPIKE_DISCHARGE_ENABLED,
    SWITCH_SPIKE_DISCHARGE_CONSERVATIVE,
    SWITCH_DRY_RUN,
    SWITCH_DEMAND_WINDOW_BLOCK,
    SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET,
    SWITCH_NOTIFY_TRANSITIONS,
    SWITCH_NOTIFY_DAILY_SUMMARY,
    SWITCH_NOTIFY_MANUAL_ACTIONS,
    SWITCH_NOTIFY_ALERTS,
    # Thermal management switches (Issue #137)
    SWITCH_THERMAL_MANAGEMENT_ENABLED,
    SWITCH_SOLAR_TAPER_ENABLED,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LocalShift switch entities."""
    coordinator: LocalShiftCoordinator = entry.runtime_data

    entities = [LocalShiftSwitch(coordinator, entry, key) for key in SWITCH_KEYS]

    async_add_entities(entities)


class LocalShiftSwitch(SwitchEntity):
    """A toggle switch for automation features."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Initialise the switch."""
        self.coordinator = coordinator
        self._entry = entry
        self._key = key

        # Load persisted state from options, or use default
        option_key = f"{SWITCH_STATE_PREFIX}{key}"
        self._is_on = self._entry.options.get(option_key, SWITCH_DEFAULTS[key])

        self._attr_unique_id = f"localshift_{key}"
        self._attr_name = SWITCH_NAMES[key]
        self._attr_icon = SWITCH_ICONS[key]

        # Sync initial state to coordinator's switch state bridge
        self.coordinator.set_switch_state(key, self._is_on)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information to link all entities under one device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="LocalShift",
            manufacturer="Custom",
            model="Solar Battery Automation",
            sw_version="0.0.2",
        )

    @property
    def is_on(self) -> bool:
        """Return True if the switch is on."""
        return self._is_on

    async def async_turn_on(self, **_kwargs) -> None:
        """Turn the switch on."""
        self._is_on = True
        self.coordinator.set_switch_state(self._key, True)
        self.async_write_ha_state()

        # Persist state to config entry options
        option_key = f"{SWITCH_STATE_PREFIX}{self._key}"
        new_options = {**self._entry.options, option_key: True}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)

        if self._key == SWITCH_AUTOMATION_ENABLED:
            _LOGGER.info("LocalShift automation enabled")

        # Re-evaluate derived values and trigger state machine
        await self.coordinator.async_recompute_and_evaluate()

    async def async_turn_off(self, **_kwargs) -> None:
        """Turn the switch off."""
        self._is_on = False
        self.coordinator.set_switch_state(self._key, False)
        self.async_write_ha_state()

        # Persist state to config entry options
        option_key = f"{SWITCH_STATE_PREFIX}{self._key}"
        new_options = {**self._entry.options, option_key: False}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)

        if self._key == SWITCH_AUTOMATION_ENABLED:
            _LOGGER.info(
                "LocalShift automation disabled, returning to self consumption"
            )
            await self.coordinator.async_set_self_consumption()
            # Send notification about automation being disabled
            if self.coordinator._notification_service is not None:
                await self.coordinator._notification_service.send_automation_disabled_notification(
                    self.coordinator.data
                )

        # Re-evaluate derived values and trigger state machine
        await self.coordinator.async_recompute_and_evaluate()

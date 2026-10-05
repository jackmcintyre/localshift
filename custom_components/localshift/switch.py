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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
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
    SWITCH_NOTIFICATIONS_ENABLED,
    SWITCH_SPIKE_DISCHARGE_CONSERVATIVE,
    SWITCH_SPIKE_DISCHARGE_ENABLED,
    SWITCH_STALE_SOLAR_CONSERVATIVE,
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
    SWITCH_STALE_SOLAR_CONSERVATIVE,
    SWITCH_NOTIFICATIONS_ENABLED,  # Consolidated notification toggle (Issue #214)
]

# Issue #787: entity_category per switch. automation_enabled and
# demand_window_block are primary operational controls users flip routinely,
# so they stay visible with no category. Everything else here is a tuning
# knob or a mode-of-operation toggle -> CONFIG. Keep this beside SWITCH_KEYS
# (not in const.py) since it is presentation, not domain config.
SWITCH_CATEGORIES: dict[str, EntityCategory | None] = {
    SWITCH_AUTOMATION_ENABLED: None,
    SWITCH_SPIKE_DISCHARGE_ENABLED: EntityCategory.CONFIG,
    SWITCH_SPIKE_DISCHARGE_CONSERVATIVE: EntityCategory.CONFIG,
    SWITCH_DRY_RUN: EntityCategory.CONFIG,
    SWITCH_DEMAND_WINDOW_BLOCK: None,
    SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET: EntityCategory.CONFIG,
    SWITCH_STALE_SOLAR_CONSERVATIVE: EntityCategory.CONFIG,
    SWITCH_NOTIFICATIONS_ENABLED: EntityCategory.CONFIG,
}


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
    """A toggle switch for automation features.

    The coordinator's switch-state bridge is the single source of truth: the
    entity holds no copy of its own, so a writer that goes through the bridge
    without touching this entity (the battery-mode select flipping
    automation_enabled) still shows up here on the next coordinator update.
    """

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

        self._attr_unique_id = f"localshift_{key}"
        self._attr_name = SWITCH_NAMES[key]
        self._attr_icon = SWITCH_ICONS[key]
        self._attr_entity_category = SWITCH_CATEGORIES.get(key)

        # Seed the coordinator's switch state bridge from the persisted
        # option, or the default
        option_key = f"{SWITCH_STATE_PREFIX}{key}"
        self.coordinator.set_switch_state(
            key, self._entry.options.get(option_key, SWITCH_DEFAULTS[key])
        )

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
        return self.coordinator.get_switch_state(self._key)

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        self.async_write_ha_state()

    async def async_turn_on(self, **_kwargs) -> None:
        """Turn the switch on."""
        self.coordinator.set_switch_state(self._key, True)
        self.async_write_ha_state()

        # Persist state to config entry options
        option_key = f"{SWITCH_STATE_PREFIX}{self._key}"
        new_options = {**self._entry.options, option_key: True}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)

        if self._key == SWITCH_AUTOMATION_ENABLED:
            _LOGGER.info("LocalShift automation enabled")

        # Persisting the option above fires the entry update listener
        # (_async_options_updated in __init__.py), which is the single
        # recompute trigger for entity writes (issue #975).

    async def async_turn_off(self, **_kwargs) -> None:
        """Turn the switch off."""
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

        # Persisting the option above fires the entry update listener
        # (_async_options_updated in __init__.py), which is the single
        # recompute trigger for entity writes (issue #975).

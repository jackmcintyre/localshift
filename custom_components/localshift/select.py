"""Select platform for the LocalShift integration.

Provides a select entity for battery mode control:
- When automation is enabled: select shows current automated mode
- When user changes select: automation automatically disables, user's choice is honored
- When user re-enables automation: system takes control of select immediately

Issue #382: Replaces manual control buttons with a single select entity.
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    SELECT_BATTERY_MODE,
    SELECT_ICONS,
    SELECT_NAMES,
    SELECT_OPTIONS,
    SWITCH_AUTOMATION_ENABLED,
    BatteryMode,
)
from .coordinator import LocalShiftCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LocalShift select entities."""
    coordinator: LocalShiftCoordinator = entry.runtime_data

    entities = [BatteryModeSelect(coordinator, entry)]

    _LOGGER.info("Setting up %d LocalShift select entities", len(entities))
    async_add_entities(entities)


class BatteryModeSelect(SelectEntity):
    """Select entity for battery mode control.

    When the user changes this select:
    1. Automation is automatically disabled
    2. The selected mode is applied to the battery
    3. The mode persists until automation is re-enabled

    When automation is re-enabled:
    1. The select updates to show the automated mode
    2. State machine takes control of battery mode
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the select entity."""
        self.coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"localshift_{SELECT_BATTERY_MODE}"
        self._attr_name = SELECT_NAMES[SELECT_BATTERY_MODE]
        self._attr_icon = SELECT_ICONS[SELECT_BATTERY_MODE]
        self._attr_options = SELECT_OPTIONS[SELECT_BATTERY_MODE]
        self._previous_mode: str = "self_consumption"
        self._internal_update: bool = False

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
    def current_option(self) -> str | None:
        """Return the current selected option."""
        # Map BatteryMode to select option
        mode = self.coordinator.data.active_mode
        if mode == BatteryMode.MANUAL:
            # During manual mode, show the previous commanded mode
            return self._previous_mode
        elif mode == BatteryMode.DEMAND_BLOCK:
            # Demand block is a variant of self consumption
            return "self_consumption"
        elif mode in (
            BatteryMode.SELF_CONSUMPTION,
            BatteryMode.GRID_CHARGING,
            BatteryMode.BOOST_CHARGING,
            BatteryMode.SPIKE_DISCHARGE,
            BatteryMode.PROACTIVE_EXPORT,
        ):
            return mode.value
        return "self_consumption"

    async def async_select_option(self, option: str) -> None:
        """Handle selection of a new option.

        This is called when the user changes the select.
        It disables automation and applies the selected mode.
        """
        _LOGGER.info("Battery mode select changed to: %s", option)

        # Store the previous mode in case we need to revert
        old_mode = self.current_option
        self._previous_mode = option

        # Disable automation when user manually selects a mode
        if self.coordinator.get_switch_state(SWITCH_AUTOMATION_ENABLED):
            _LOGGER.info("Disabling automation due to manual mode selection")
            # Update the switch state directly (without triggering turn_off callback)
            self.coordinator.set_switch_state(SWITCH_AUTOMATION_ENABLED, False)
            # Persist the switch state
            option_key = f"switch_state_{SWITCH_AUTOMATION_ENABLED}"
            new_options = {**self._entry.options, option_key: False}
            self.hass.config_entries.async_update_entry(
                self._entry, options=new_options
            )

        # Convert option string to BatteryMode
        try:
            target_mode = BatteryMode(option)
        except ValueError:
            _LOGGER.error("Invalid battery mode selected: %s", option)
            return

        # Apply the mode via coordinator
        success = await self.coordinator.async_set_battery_mode(target_mode)

        if not success:
            _LOGGER.warning(
                "Failed to apply battery mode %s, reverting select to %s",
                option,
                old_mode,
            )
            # Revert to previous mode on failure
            self._previous_mode = old_mode or "self_consumption"
            self._internal_update = True
            self.async_write_ha_state()
            self._internal_update = False
            return

        # Send notification about manual mode change
        if self.coordinator._notification_service is not None:
            await (
                self.coordinator._notification_service.send_manual_action_notification(
                    f"Manual {target_mode.display_name}", self.coordinator.data
                )
            )

        # Update the entity state
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        # Skip if this is an internal update (we already wrote state)
        if self._internal_update:
            return

        # Update the previous mode tracking when automation is active
        if self.coordinator.get_switch_state(SWITCH_AUTOMATION_ENABLED):
            mode = self.coordinator.data.active_mode
            if mode != BatteryMode.MANUAL and mode != BatteryMode.DEMAND_BLOCK:
                self._previous_mode = mode.value

        self.async_write_ha_state()

    def set_mode_from_automation(self, mode: BatteryMode) -> None:
        """Update the select to show the automated mode.

        Called by the state machine when automation changes the mode.
        This is an internal update that should not trigger async_select_option.
        """
        if mode in (
            BatteryMode.SELF_CONSUMPTION,
            BatteryMode.GRID_CHARGING,
            BatteryMode.BOOST_CHARGING,
            BatteryMode.SPIKE_DISCHARGE,
            BatteryMode.PROACTIVE_EXPORT,
        ):
            self._previous_mode = mode.value
            self._internal_update = True
            self.async_write_ha_state()
            self._internal_update = False

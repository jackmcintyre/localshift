"""Select platform for the LocalShift integration.

Provides select entities for:
- Battery mode manual control
- Optimizer objective mode (self-consumption vs arbitrage)
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_OPTIMIZATION_MODE,
    DEFAULT_OPTIMIZATION_MODE,
    DOMAIN,
    SELECT_BATTERY_MODE,
    SELECT_ICONS,
    SELECT_NAMES,
    SELECT_OPTIMIZATION_MODE,
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

    entities = [
        BatteryModeSelect(coordinator, entry),
        OptimizationModeSelect(coordinator, entry),
    ]

    _LOGGER.info("Setting up %d LocalShift select entities", len(entities))
    async_add_entities(entities)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    """Build device info for LocalShift entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="LocalShift",
        manufacturer="Custom",
        model="Solar Battery Automation",
        sw_version="0.0.2",
    )


class BatteryModeSelect(SelectEntity):
    """Select entity for battery mode control."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize battery mode select."""
        self.coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"localshift_{SELECT_BATTERY_MODE}"
        self._attr_name = SELECT_NAMES[SELECT_BATTERY_MODE]
        self._attr_icon = SELECT_ICONS[SELECT_BATTERY_MODE]
        self._attr_options = SELECT_OPTIONS[SELECT_BATTERY_MODE]
        self._previous_mode: str = "self_consumption"
        self._internal_update: bool = False
        self._last_committed_mode: str | None = None
        self._update_count: int = 0
        self._change_count: int = 0

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return _device_info(self._entry)

    @property
    def current_option(self) -> str | None:
        """Return the current selected option."""
        mode = self.coordinator.data.active_mode
        if mode == BatteryMode.MANUAL:
            return self._previous_mode
        if mode == BatteryMode.DEMAND_BLOCK:
            return "self_consumption"
        if mode in (
            BatteryMode.SELF_CONSUMPTION,
            BatteryMode.GRID_CHARGING,
            BatteryMode.BOOST_CHARGING,
            BatteryMode.SPIKE_DISCHARGE,
            BatteryMode.PROACTIVE_EXPORT,
        ):
            return mode.value
        return "self_consumption"

    async def async_select_option(self, option: str) -> None:
        """Handle selection of a new battery mode."""
        _LOGGER.info("Battery mode select changed to: %s", option)

        old_mode = self.current_option
        self._previous_mode = option

        if self.coordinator.get_switch_state(SWITCH_AUTOMATION_ENABLED):
            _LOGGER.info("Disabling automation due to manual mode selection")
            self.coordinator.set_switch_state(SWITCH_AUTOMATION_ENABLED, False)
            option_key = f"switch_state_{SWITCH_AUTOMATION_ENABLED}"
            new_options = {**self._entry.options, option_key: False}
            self.hass.config_entries.async_update_entry(
                self._entry, options=new_options
            )

        try:
            target_mode = BatteryMode(option)
        except ValueError:
            _LOGGER.error("Invalid battery mode selected: %s", option)
            return

        success = await self.coordinator.async_set_battery_mode(target_mode)
        if not success:
            _LOGGER.warning(
                "Failed to apply battery mode %s, reverting select to %s",
                option,
                old_mode,
            )
            self._previous_mode = old_mode or "self_consumption"
            self._internal_update = True
            self.async_write_ha_state()
            self._internal_update = False
            return

        if self.coordinator._notification_service is not None:
            await (
                self.coordinator._notification_service.send_manual_action_notification(
                    f"Manual {target_mode.display_name}", self.coordinator.data
                )
            )

        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        if self._internal_update:
            return

        self._update_count += 1
        current_mode = self.current_option

        if self.coordinator.get_switch_state(SWITCH_AUTOMATION_ENABLED):
            mode = self.coordinator.data.active_mode
            if mode != BatteryMode.MANUAL and mode != BatteryMode.DEMAND_BLOCK:
                current_mode = mode.value

        if current_mode != self._last_committed_mode:
            self._change_count += 1
            _LOGGER.debug(
                "Battery mode select changed: %s → %s (update #%d, change #%d)",
                self._last_committed_mode,
                current_mode,
                self._update_count,
                self._change_count,
            )
            self._last_committed_mode = current_mode
            self._previous_mode = current_mode or "self_consumption"
            self.async_write_ha_state()
        else:
            if self._update_count % 60 == 0:
                _LOGGER.debug(
                    "Battery mode select: %d updates, %d changes (mode stable at %s)",
                    self._update_count,
                    self._change_count,
                    current_mode,
                )


class OptimizationModeSelect(SelectEntity):
    """Select entity for optimizer objective mode."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize optimization mode select."""
        self.coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"localshift_{SELECT_OPTIMIZATION_MODE}"
        self._attr_name = SELECT_NAMES[SELECT_OPTIMIZATION_MODE]
        self._attr_icon = SELECT_ICONS[SELECT_OPTIMIZATION_MODE]
        self._attr_options = SELECT_OPTIONS[SELECT_OPTIMIZATION_MODE]

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return _device_info(self._entry)

    @property
    def current_option(self) -> str | None:
        """Return the current optimization mode option."""
        option = self._entry.options.get(CONF_OPTIMIZATION_MODE)
        if option in self._attr_options:
            return option
        return DEFAULT_OPTIMIZATION_MODE

    async def async_select_option(self, option: str) -> None:
        """Persist selected optimization mode and trigger recompute."""
        if option not in self._attr_options:
            _LOGGER.error("Invalid optimization mode selected: %s", option)
            return

        _LOGGER.info("Optimization mode select changed to: %s", option)
        new_options = {**self._entry.options, CONF_OPTIMIZATION_MODE: option}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)

        self.async_write_ha_state()
        await self.coordinator.async_recompute_and_evaluate()

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        self.async_write_ha_state()

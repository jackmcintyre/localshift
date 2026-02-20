"""Button platform for the LocalShift integration.

Provides manual mode control buttons:
- Force Charge
- Force Discharge
- Boost Charge (5kW)
- Return to Self Consumption
- Update Forecast
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BUTTON_BOOST_CHARGE,
    BUTTON_FORCE_CHARGE,
    BUTTON_FORCE_DISCHARGE,
    BUTTON_ICONS,
    BUTTON_NAMES,
    BUTTON_SELF_CONSUMPTION,
    BUTTON_UPDATE_FORECAST,
    DOMAIN,
)
from .coordinator import LocalShiftCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LocalShift button entities."""
    coordinator: LocalShiftCoordinator = entry.runtime_data

    entities = [
        ForceChargeButton(coordinator, entry),
        ForceDischargeButton(coordinator, entry),
        BoostChargeButton(coordinator, entry),
        SelfConsumptionButton(coordinator, entry),
        UpdateForecastButton(coordinator, entry),
    ]

    _LOGGER.info("Setting up %d LocalShift button entities", len(entities))
    for entity in entities:
        _LOGGER.debug("Registering button entity: %s", entity.unique_id)

    async_add_entities(entities)


class LocalShiftButtonBase(ButtonEntity):
    """Base class for manual mode buttons."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Initialise the button."""
        self.coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"localshift_{key}"
        self._attr_name = BUTTON_NAMES[key]
        self._attr_icon = BUTTON_ICONS[key]

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


class ForceChargeButton(LocalShiftButtonBase):
    """Manually force charge the battery."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, BUTTON_FORCE_CHARGE)

    async def async_press(self) -> None:
        """Handle button press."""
        self.coordinator.data.manual_override = True
        if self.coordinator._state_machine is not None:
            self.coordinator._state_machine.set_manual_override_timestamp()
        await self.coordinator.async_set_force_charge()
        if self.coordinator._notification_service is not None:
            await (
                self.coordinator._notification_service.send_manual_action_notification(
                    "Manual Force Charge", self.coordinator.data
                )
            )


class ForceDischargeButton(LocalShiftButtonBase):
    """Manually force discharge the battery."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, BUTTON_FORCE_DISCHARGE)

    async def async_press(self) -> None:
        """Handle button press."""
        self.coordinator.data.manual_override = True
        if self.coordinator._state_machine is not None:
            self.coordinator._state_machine.set_manual_override_timestamp()
        await self.coordinator.async_set_force_discharge()
        if self.coordinator._notification_service is not None:
            await (
                self.coordinator._notification_service.send_manual_action_notification(
                    "Manual Force Discharge", self.coordinator.data
                )
            )


class BoostChargeButton(LocalShiftButtonBase):
    """Manually boost charge the battery at 5kW."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, BUTTON_BOOST_CHARGE)

    async def async_press(self) -> None:
        """Handle button press."""
        self.coordinator.data.manual_override = True
        if self.coordinator._state_machine is not None:
            self.coordinator._state_machine.set_manual_override_timestamp()
        await self.coordinator.async_set_boost_charge()
        if self.coordinator._notification_service is not None:
            await (
                self.coordinator._notification_service.send_manual_action_notification(
                    "Manual Boost Charge", self.coordinator.data
                )
            )


class SelfConsumptionButton(LocalShiftButtonBase):
    """Return to normal self consumption mode."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, BUTTON_SELF_CONSUMPTION)

    async def async_press(self) -> None:
        """Handle button press."""
        await self.coordinator.async_set_self_consumption()
        if self.coordinator._notification_service is not None:
            await (
                self.coordinator._notification_service.send_manual_action_notification(
                    "Manual Self Consumption", self.coordinator.data
                )
            )


class UpdateForecastButton(LocalShiftButtonBase):
    """Force forecast update and clear historical load cache."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, BUTTON_UPDATE_FORECAST)

    async def async_press(self) -> None:
        """Handle button press."""
        # Clear historical cache to force refresh
        await self.coordinator.async_clear_historical_cache()

        # Trigger coordinator refresh to regenerate forecast
        await self.coordinator.async_evaluate_state_machine()

        if self.coordinator._notification_service is not None:
            await (
                self.coordinator._notification_service.send_manual_action_notification(
                    "Forecast Update", self.coordinator.data
                )
            )

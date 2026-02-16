"""Button platform for the Amber Powerwall integration.

Provides manual mode control buttons:
- Force Charge
- Force Discharge
- Hold Battery
- Boost Charge (5kW)
- Return to Self Consumption
"""

from __future__ import annotations

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
from .coordinator import AmberPowerwallCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Amber Powerwall button entities."""
    coordinator: AmberPowerwallCoordinator = entry.runtime_data

    entities = [
        ForceChargeButton(coordinator, entry),
        ForceDischargeButton(coordinator, entry),
        # HoldButton removed - Hold mode no longer exists
        BoostChargeButton(coordinator, entry),
        SelfConsumptionButton(coordinator, entry),
        UpdateForecastButton(coordinator, entry),
    ]

    async_add_entities(entities)


class AmberPowerwallButtonBase(ButtonEntity):
    """Base class for manual mode buttons."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AmberPowerwallCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Initialise the button."""
        self.coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"amber_powerwall_{key}"
        self._attr_name = BUTTON_NAMES[key]
        self._attr_icon = BUTTON_ICONS[key]

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


class ForceChargeButton(AmberPowerwallButtonBase):
    """Manually force charge the battery."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, BUTTON_FORCE_CHARGE)

    async def async_press(self) -> None:
        """Handle button press."""
        self.coordinator.data.manual_override = True
        if self.coordinator._state_machine is not None:
            self.coordinator._state_machine.set_manual_override_timestamp()
        await self.coordinator.async_set_force_charge()
        await self.coordinator.async_send_notification(
            "Powerwall: Manual Force Charge",
            f"Manual force charge started. Battery at "
            f"{self.coordinator.data.soc:.0f}%.",
        )


class ForceDischargeButton(AmberPowerwallButtonBase):
    """Manually force discharge the battery."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, BUTTON_FORCE_DISCHARGE)

    async def async_press(self) -> None:
        """Handle button press."""
        self.coordinator.data.manual_override = True
        if self.coordinator._state_machine is not None:
            self.coordinator._state_machine.set_manual_override_timestamp()
        await self.coordinator.async_set_force_discharge()
        await self.coordinator.async_send_notification(
            "Powerwall: Manual Force Discharge",
            f"Manual force discharge started. Battery at "
            f"{self.coordinator.data.soc:.0f}%.",
        )


class BoostChargeButton(AmberPowerwallButtonBase):
    """Manually boost charge the battery at 5kW."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, BUTTON_BOOST_CHARGE)

    async def async_press(self) -> None:
        """Handle button press."""
        self.coordinator.data.manual_override = True
        if self.coordinator._state_machine is not None:
            self.coordinator._state_machine.set_manual_override_timestamp()
        await self.coordinator.async_set_boost_charge()
        await self.coordinator.async_send_notification(
            "Powerwall: Manual Boost Charge",
            f"Manual boost charge (5kW) started. Battery at "
            f"{self.coordinator.data.soc:.0f}%.",
        )


class SelfConsumptionButton(AmberPowerwallButtonBase):
    """Return to normal self consumption mode."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, BUTTON_SELF_CONSUMPTION)

    async def async_press(self) -> None:
        """Handle button press."""
        await self.coordinator.async_set_self_consumption()
        await self.coordinator.async_send_notification(
            "Powerwall: Manual Self Consumption",
            f"Returned to self consumption. Battery at "
            f"{self.coordinator.data.soc:.0f}%.",
        )


class UpdateForecastButton(AmberPowerwallButtonBase):
    """Force forecast update and clear historical load cache."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, BUTTON_UPDATE_FORECAST)

    async def async_press(self) -> None:
        """Handle button press."""
        # Clear historical cache to force refresh
        await self.coordinator.async_clear_historical_cache()

        # Trigger coordinator refresh to regenerate forecast
        await self.coordinator.async_evaluate_state_machine()

        await self.coordinator.async_send_notification(
            "Powerwall: Forecast Update",
            "Historical load cache cleared. Forecast will regenerate.",
        )

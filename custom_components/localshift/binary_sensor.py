"""Binary sensor platform for the LocalShift integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import LocalShiftCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LocalShift binary sensor entities."""
    coordinator: LocalShiftCoordinator = entry.runtime_data

    entities: list[BinarySensorEntity] = [
        ForecastSpikeWithinWindowSensor(coordinator, entry),
        ForceDischargeActiveSensor(coordinator, entry),
        ForceChargeActiveSensor(coordinator, entry),
        BoostChargeActiveSensor(coordinator, entry),
        ForecastExpensivePeriodSensor(coordinator, entry),
        SolarCanReachTargetSensor(coordinator, entry),
        BoostChargeNeededSensor(coordinator, entry),
        DemandWindowActiveSensor(coordinator, entry),
    ]

    async_add_entities(entities)


class LocalShiftBinarySensorBase(BinarySensorEntity):
    """Base class for LocalShift binary sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialise sensor."""
        self.coordinator = coordinator
        self._entry = entry
        self._unsub: Any = None

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

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        self._unsub = self.coordinator.async_add_listener(
            self._handle_coordinator_update
        )
        self._update_from_coordinator()

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from coordinator updates."""
        if self._unsub:
            self._unsub()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_from_coordinator()
        self.async_write_ha_state()

    def _update_from_coordinator(self) -> None:
        """Pull latest values from coordinator.data. Override in subclasses."""


# ---------------------------------------------------------------------------
# Binary sensor implementations
# ---------------------------------------------------------------------------


class ForecastSpikeWithinWindowSensor(LocalShiftBinarySensorBase):
    """Whether a price spike is forecast within the lookahead window."""

    _attr_unique_id = "localshift_price_spike_coming"
    _attr_name = "Price Spike Coming"
    _attr_icon = "mdi:flash-alert-outline"

    def _update_from_coordinator(self) -> None:
        self._attr_is_on = self.coordinator.data.forecast_spike_within_window

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return max forecast price within the lookahead window."""
        return {
            "max_forecast_price": self.coordinator.data.max_forecast_price,
            "max_buy_forecast_price": self.coordinator.data.max_buy_forecast_price,
        }


class ForceDischargeActiveSensor(LocalShiftBinarySensorBase):
    """Whether battery is currently force discharging."""

    _attr_unique_id = "localshift_discharge_forced"
    _attr_name = "Discharge Forced"
    _attr_icon = "mdi:battery-arrow-down"

    def _update_from_coordinator(self) -> None:
        self._attr_is_on = self.coordinator.data.force_discharge_active


class ForceChargeActiveSensor(LocalShiftBinarySensorBase):
    """Whether battery is currently force charging (backup mode)."""

    _attr_unique_id = "localshift_charge_forced"
    _attr_name = "Charge Forced"
    _attr_icon = "mdi:battery-charging"

    def _update_from_coordinator(self) -> None:
        self._attr_is_on = self.coordinator.data.force_charge_active


class BoostChargeActiveSensor(LocalShiftBinarySensorBase):
    """Whether battery is currently boost charging (5kW)."""

    _attr_unique_id = "localshift_charge_boost"
    _attr_name = "Charge Boost"
    _attr_icon = "mdi:battery-charging-high"

    def _update_from_coordinator(self) -> None:
        self._attr_is_on = self.coordinator.data.boost_charge_active


class ForecastExpensivePeriodSensor(LocalShiftBinarySensorBase):
    """Whether an expensive period is forecast within lookahead."""

    _attr_unique_id = "localshift_price_expensive_coming"
    _attr_name = "Price Expensive Coming"
    _attr_icon = "mdi:currency-usd"

    def _update_from_coordinator(self) -> None:
        self._attr_is_on = self.coordinator.data.forecast_expensive_period_coming


class SolarCanReachTargetSensor(LocalShiftBinarySensorBase):
    """Whether solar forecast can fill battery to target by demand window."""

    _attr_unique_id = "localshift_solar_can_reach_target"
    _attr_name = "Solar Can Reach Target"
    _attr_icon = "mdi:white-balance-sunny"

    def _update_from_coordinator(self) -> None:
        self._attr_is_on = self.coordinator.data.solar_can_reach_target


class BoostChargeNeededSensor(LocalShiftBinarySensorBase):
    """Whether 3.3kW charge rate is insufficient (need 5kW boost)."""

    _attr_unique_id = "localshift_charge_boost_needed"
    _attr_name = "Charge Boost Needed"
    _attr_icon = "mdi:speedometer"

    def _update_from_coordinator(self) -> None:
        self._attr_is_on = self.coordinator.data.boost_charge_needed


class DemandWindowActiveSensor(LocalShiftBinarySensorBase):
    """Whether the demand window is currently active."""

    _attr_unique_id = "localshift_demand_window"
    _attr_name = "Demand Window"

    @property
    def icon(self) -> str:
        """Return icon based on state."""
        return "mdi:clock-alert" if self._attr_is_on else "mdi:clock-outline"

    def _update_from_coordinator(self) -> None:
        self._attr_is_on = self.coordinator.data.demand_window_active

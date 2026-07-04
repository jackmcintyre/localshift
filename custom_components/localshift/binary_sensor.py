"""Binary sensor platform for the LocalShift integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

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
        # Excess solar load shifting sensor (backlog-high-017)
        ExcessSolarAvailableSensor(coordinator, entry),
        # Tesla Override Detection sensor
        TeslaOverrideActiveSensor(coordinator, entry),
        # Amber Express demand window (Issue #300)
        AmberExpressDemandWindowSensor(coordinator, entry),
        # Optimizer SOC underprepared (Issue #891)
        OptimizerSocUnderpreparedSensor(coordinator, entry),
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


# ---------------------------------------------------------------------------
# Excess Solar Load Shifting Binary Sensor (backlog-high-017)
# ---------------------------------------------------------------------------


class ExcessSolarAvailableSensor(LocalShiftBinarySensorBase):
    """Simple ON/OFF trigger for basic automations - excess solar available."""

    _attr_unique_id = "localshift_excess_solar_available"
    _attr_name = "Excess Solar Available"

    @property
    def icon(self) -> str:
        """Return icon based on state."""
        return (
            "mdi:solar-power-variant"
            if self._attr_is_on
            else "mdi:solar-power-variant-outline"
        )

    def _update_from_coordinator(self) -> None:
        self._attr_is_on = self.coordinator.data.excess_solar_available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return current excess details."""
        d = self.coordinator.data
        return {
            "current_excess_kw": round(d.current_excess_rate_kw, 2),
            "battery_soc": round(d.soc, 1),
            "battery_charging": d.battery_power_kw < -0.1,
            "can_add_load_now": d.can_add_load_now,
            "safe_additional_load_kw": round(d.safe_additional_load_kw, 1),
        }


# ---------------------------------------------------------------------------
# Tesla Override Detection Binary Sensor
# ---------------------------------------------------------------------------


class TeslaOverrideActiveSensor(LocalShiftBinarySensorBase):
    """Whether Tesla has taken control of the Powerwall (Storm Watch, Grid Event, VPP).

    When Tesla activates Storm Watch, Grid Events, or VPP events, they set
    backup_reserve to 80% and operation_mode to self_consumption, ignoring
    external API commands until the event ends. This sensor provides visibility
    into when Tesla has control.
    """

    _attr_unique_id = "localshift_tesla_override_active"
    _attr_name = "Tesla Override Active"

    @property
    def icon(self) -> str:
        """Return icon based on state."""
        return "mdi:shield-alert" if self._attr_is_on else "mdi:shield-check"

    def _update_from_coordinator(self) -> None:
        """Update state from state machine's Tesla override detection."""
        if self.coordinator._state_machine is not None:
            self._attr_is_on = (
                self.coordinator._state_machine.is_tesla_override_active()
            )
        else:
            self._attr_is_on = False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return override context from coordinator data."""
        if self._attr_is_on:
            description = "Tesla has taken control (Storm Watch, Grid Event, or VPP)"
        else:
            description = "Tesla is not overriding control"

        attributes: dict[str, Any] = {
            "operation_mode": self.coordinator.data.operation_mode,
            "backup_reserve": self.coordinator.data.backup_reserve,
            "grid_services_active": self.coordinator.data.grid_services_active,
            "storm_watch_active": self.coordinator.data.storm_watch_active,
            "description": description,
        }

        state_machine = self.coordinator._state_machine
        if state_machine is not None:
            info = state_machine.get_tesla_override_info()
            detected_at = info.get("detected_at")
            duration_minutes = None
            if self._attr_is_on and detected_at is not None:
                duration_minutes = round(
                    (dt_util.now() - detected_at).total_seconds() / 60.0, 1
                )
            attributes.update({
                "detected_at": detected_at.isoformat() if detected_at else None,
                "duration_minutes": duration_minutes,
                "corroborated": info.get("corroborated"),
                "last_probe_at": (
                    info["last_probe_at"].isoformat()
                    if info.get("last_probe_at")
                    else None
                ),
            })

        return attributes


# ---------------------------------------------------------------------------
# Amber Express Demand Window Binary Sensor (Issue #300)
# ---------------------------------------------------------------------------


class AmberExpressDemandWindowSensor(LocalShiftBinarySensorBase):
    """Demand window status from Amber Express."""

    _attr_unique_id = "localshift_amber_demand_window"
    _attr_name = "Amber Demand Window"

    @property
    def icon(self) -> str:
        """Return icon based on state."""
        return "mdi:clock-alert" if self._attr_is_on else "mdi:clock-check"

    def _update_from_coordinator(self) -> None:
        """Update state from coordinator data."""
        self._attr_is_on = self.coordinator.data.demand_window_amber


# ---------------------------------------------------------------------------
# Optimizer SOC Underprepared Binary Sensor (Issue #891)
# ---------------------------------------------------------------------------


class OptimizerSocUnderpreparedSensor(LocalShiftBinarySensorBase):
    """Whether the battery entered the demand window far below target.

    Surfaces ``data.optimizer_soc_underprepared`` (set by
    ``OptimizerFacade._warn_soc_divergence`` when a demand window is active
    but the live SOC is more than 20 points below target). The 2026-06-30
    silent miss saw the battery enter the DW at ~10% while the summary
    reported a healthy DW-entry SOC; this sensor makes that failure mode
    visible to dashboards and automations without tailing logs.
    """

    _attr_unique_id = "localshift_optimizer_soc_underprepared"
    _attr_name = "Optimizer SOC Underprepared"

    @property
    def icon(self) -> str:
        """Return icon based on state."""
        return "mdi:alert" if self._attr_is_on else "mdi:check-circle"

    def _update_from_coordinator(self) -> None:
        """Update state from coordinator data.

        Defaults to False when the flag is not yet populated (e.g. before the
        first optimizer cycle has run).
        """
        self._attr_is_on = bool(
            getattr(self.coordinator.data, "optimizer_soc_underprepared", False)
        )

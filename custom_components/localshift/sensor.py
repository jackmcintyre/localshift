"""Sensor platform for LocalShift integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MINIMUM_TARGET_SOC, DEFAULT_MINIMUM_TARGET_SOC, DOMAIN
from .coordinator import LocalShiftCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LocalShift sensor entities."""
    coordinator: LocalShiftCoordinator = entry.runtime_data

    entities: list[SensorEntity] = [
        EffectiveCheapPriceSensor(coordinator, entry),
        CheapChargeStopPriceSensor(coordinator, entry),
        SolarWeightedAvgFITSensor(coordinator, entry),
        ActiveModeSensor(coordinator, entry),
        SolarBatteryForecastSensor(coordinator, entry),
        GridImportPowerSensor(coordinator, entry),
        GridExportPowerSensor(coordinator, entry),
        NetElectricityCostSensor(coordinator, entry),
        DecisionLogSensor(coordinator, entry),
        ForecastHistorySensor(coordinator, entry),
        DailyForecastSensor(coordinator, entry),
        MinimumTargetSOCSensor(coordinator, entry),
    ]

    async_add_entities(entities)


class LocalShiftSensorBase(SensorEntity):
    """Base class for LocalShift sensors."""

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
        # Set initial value
        self._update_from_coordinator()

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from coordinator updates."""
        if self._unsub:
            self._unsub()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        self._update_from_coordinator()
        self.async_write_ha_state()

    def _update_from_coordinator(self) -> None:
        """Pull latest values from coordinator.data. Override in subclasses."""


# ---------------------------------------------------------------------------
# Sensor implementations
# ---------------------------------------------------------------------------


class EffectiveCheapPriceSensor(LocalShiftSensorBase):
    """Dynamic cheap price threshold (urgency-adjusted)."""

    _attr_unique_id = "localshift_price_cheap_effective"
    _attr_name = "Price Cheap Effective"
    _attr_icon = "mdi:tag-outline"
    _attr_native_unit_of_measurement = "$/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = round(self.coordinator.data.effective_cheap_price, 4)


class CheapChargeStopPriceSensor(LocalShiftSensorBase):
    """Effective threshold + deadband."""

    _attr_unique_id = "localshift_price_cheap_charge_stop"
    _attr_name = "Price Cheap Charge Stop"
    _attr_icon = "mdi:tag-arrow-up-outline"
    _attr_native_unit_of_measurement = "$/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = round(
            self.coordinator.data.cheap_charge_stop_price, 4
        )


class SolarWeightedAvgFITSensor(LocalShiftSensorBase):
    """Solar-production-weighted average feed-in tariff."""

    _attr_unique_id = "localshift_solar_weighted_avg_fit"
    _attr_name = "Solar Weighted Avg FIT"
    _attr_icon = "mdi:solar-power-variant"
    _attr_native_unit_of_measurement = "$/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = round(self.coordinator.data.solar_weighted_avg_fit, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        return {
            "total_solar_remaining_kwh": round(
                self.coordinator.data.solar_remaining_kwh, 2
            ),
        }


class ActiveModeSensor(LocalShiftSensorBase):
    """Current battery automation mode."""

    _attr_unique_id = "localshift_battery_mode"
    _attr_name = "Battery Mode"
    _attr_icon = "mdi:battery-sync"

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = self.coordinator.data.active_mode.value


class SolarBatteryForecastSensor(LocalShiftSensorBase):
    """Solar battery SOC forecast with detailed attributes."""

    _attr_unique_id = "localshift_forecast_battery"
    _attr_name = "Forecast Battery"
    _attr_icon = "mdi:chart-line"
    _attr_native_unit_of_measurement = "%"

    def _update_from_coordinator(self) -> None:
        forecast = self.coordinator.data.solar_battery_forecast
        self._attr_native_value = forecast.get("predicted_soc", 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return full forecast as attributes."""
        return self.coordinator.data.solar_battery_forecast


class GridImportPowerSensor(LocalShiftSensorBase):
    """Grid import power (always >= 0)."""

    _attr_unique_id = "localshift_power_grid_import"
    _attr_name = "Power Grid Import"
    _attr_icon = "mdi:transmission-tower-import"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = round(self.coordinator.data.grid_import_power_kw, 3)


class GridExportPowerSensor(LocalShiftSensorBase):
    """Grid export power (always >= 0)."""

    _attr_unique_id = "localshift_power_grid_export"
    _attr_name = "Power Grid Export"
    _attr_icon = "mdi:transmission-tower-export"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = round(self.coordinator.data.grid_export_power_kw, 3)


class NetElectricityCostSensor(LocalShiftSensorBase):
    """Net electricity cost today (import cost - export revenue)."""

    _attr_unique_id = "localshift_cost_electricity_net"
    _attr_name = "Cost Electricity Net"
    _attr_icon = "mdi:cash-register"
    _attr_native_unit_of_measurement = "$"
    _attr_state_class = SensorStateClass.TOTAL

    def _update_from_coordinator(self) -> None:
        d = self.coordinator.data
        self._attr_native_value = round(d.grid_import_cost - d.grid_export_revenue, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return cost breakdown."""
        d = self.coordinator.data
        return {
            "grid_import_cost": round(d.grid_import_cost or 0.0, 2),
            "grid_export_revenue": round(d.grid_export_revenue or 0.0, 2),
            "battery_savings": round(d.battery_savings or 0.0, 2),
            "battery_charge_cost": round(d.battery_charge_cost or 0.0, 2),
        }


class DecisionLogSensor(LocalShiftSensorBase):
    """Battery mode change decision log."""

    _attr_unique_id = "localshift_decision_log"
    _attr_name = "Decision Log"
    _attr_icon = "mdi:history"

    def _update_from_coordinator(self) -> None:
        log = self.coordinator.data.decision_log
        if log:
            self._attr_native_value = log[-1].get("reason", "")
        else:
            self._attr_native_value = "No decisions yet"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return latest decision fields and recent history."""
        log = self.coordinator.data.decision_log
        attrs: dict[str, Any] = {"history": log[-10:]}
        if log:
            latest = log[-1]
            attrs["reason"] = latest.get("reason", "")
            attrs["soc"] = latest.get("soc")
            attrs["buy_price"] = latest.get("buy_price")
            attrs["sell_price"] = latest.get("sell_price")
            attrs["timestamp"] = latest.get("timestamp")
        return attrs


class ForecastHistorySensor(LocalShiftSensorBase):
    """Historical forecast predictions for planned vs actual comparison."""

    _attr_unique_id = "localshift_forecast_history"
    _attr_name = "Forecast History"
    _attr_icon = "mdi:chart-line-variant"

    def _update_from_coordinator(self) -> None:
        """Update with count of stored predictions."""
        self._attr_native_value = len(self.coordinator.data.forecast_history)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return forecast history as attributes."""
        return {"history": self.coordinator.data.forecast_history}


class DailyForecastSensor(LocalShiftSensorBase):
    """Full 24-hour forecast with hourly breakdown."""

    _attr_unique_id = "localshift_forecast_daily"
    _attr_name = "Forecast Daily"
    _attr_icon = "mdi:chart-bar"

    def _update_from_coordinator(self) -> None:
        """Update with count of hourly entries."""
        self._attr_native_value = len(self.coordinator.data.daily_forecast)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return daily forecast with hourly breakdown."""
        sample_counts = self.coordinator.data.consumption_hourly_sample_counts
        profile_kw = self.coordinator.data.consumption_hourly_profile_kw
        source_counts = self.coordinator.data.forecast_consumption_source_counts

        # Debug: Include key 15-min slots for debugging grid import/export
        # Show first 24 slots + every top of hour (to capture midday, evening etc)
        debug_15min = []

        for idx, slot in enumerate(self.coordinator.data.daily_forecast):
            hour = slot.get("hour", 0)
            minute = slot.get("minute", 0)
            # Include: first 24 slots OR any slot at top of hour (minute == 0)
            if idx < 24 or minute == 0:
                debug_15min.append(
                    {
                        "hour": hour,
                        "minute": minute,
                        "soc": slot.get("predicted_soc"),
                        "solar": slot.get("solar_kwh"),
                        "load": slot.get("consumption_kwh"),
                        "net": slot.get("net_kwh"),
                        "grid_in": slot.get("grid_import_kwh"),
                        "grid_out": slot.get("grid_export_kwh"),
                        "proactive_export": slot.get("proactive_export", False),
                        "export_amt": slot.get("export_amount_kwh", 0.0),
                    }
                )

        # Calculate totals for diagnostics
        total_grid_import = sum(slot.get("grid_in", 0) or 0 for slot in debug_15min)
        total_grid_export = sum(slot.get("grid_out", 0) or 0 for slot in debug_15min)

        return {
            # NOTE: We intentionally avoid exposing the full 96-slot 15-min forecast
            # here because it can exceed the recorder 16KB attribute limit.
            # Instead we expose a compact hourly summary + a light-weight SOC series.
            "debug_15min_slots": debug_15min,
            "debug_total_grid_import_kwh": round(total_grid_import, 3),
            "debug_total_grid_export_kwh": round(total_grid_export, 3),
            "forecast_hourly": self.coordinator.data.daily_forecast_hourly,
            "soc_series_15min": self.coordinator.data.daily_forecast_soc_15min,
            "forecast_15min_slots": len(self.coordinator.data.daily_forecast),
            "solcast_today_entries": len(self.coordinator.data.solcast_today),
            "solcast_tomorrow_entries": len(self.coordinator.data.solcast_tomorrow),
            "current_load_kw": round(self.coordinator.data.load_power_kw, 3),
            "consumption_source": self.coordinator.data.consumption_source,
            "consumption_statistic_id": self.coordinator.data.consumption_statistic_id,
            "consumption_profile_hours": self.coordinator.data.consumption_profile_hours,
            "consumption_fallback_hours": self.coordinator.data.consumption_fallback_hours,
            "forecast_consumption_source_counts": dict(source_counts),
            "consumption_hourly_sample_counts": {
                str(hour): count for hour, count in sorted(sample_counts.items())
            },
            "consumption_hourly_profile_kw": {
                str(hour): value for hour, value in sorted(profile_kw.items())
            },
            "recent_load_1hr_kw": round(self.coordinator.data.recent_load_1hr_kw, 3),
            "recent_load_1hr_statistic_id": self.coordinator.data.recent_load_1hr_statistic_id,
            "recent_load_1hr_samples": self.coordinator.data.recent_load_1hr_samples,
            "recent_load_1hr_last_error": self.coordinator.data.recent_load_1hr_last_error,
            "consumption_weighting": round(
                self.coordinator.data.consumption_weighting, 2
            ),
            "allow_export": self.coordinator.data.allow_export,
        }


class MinimumTargetSOCSensor(LocalShiftSensorBase):
    """Minimum target SOC for discharge modes (base reserve)."""

    _attr_unique_id = "localshift_target_soc_minimum"
    _attr_name = "Target SOC Minimum"
    _attr_icon = "mdi:battery-charging-20"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        """Read minimum target SOC from config options."""
        self._attr_native_value = float(
            self._entry.options.get(CONF_MINIMUM_TARGET_SOC, DEFAULT_MINIMUM_TARGET_SOC)
        )

"""Sensor platform for LocalShift integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
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
        NetElectricityCostSensor(coordinator, entry),
        DecisionLogSensor(coordinator, entry),
        ForecastHistorySensor(coordinator, entry),
        DailyForecastSensor(coordinator, entry),
        # New split sensors for Issue #37 (forecast history collection)
        ForecastPricesSensor(coordinator, entry),
        ForecastGridSensor(coordinator, entry),
        ForecastDiagnosticsSensor(coordinator, entry),
        MinimumTargetSOCSensor(coordinator, entry),
        # Excess solar load shifting sensors (backlog-high-017)
        ExcessSolarSensor(coordinator, entry),
        LoadShiftSignalSensor(coordinator, entry),
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
    """Full 24-hour forecast with SOC, solar, and consumption data.

    This sensor provides the core forecast data for dashboards and history.
    Split from the original monolithic sensor to stay under 16KB limit (Issue #37).
    """

    _attr_unique_id = "localshift_forecast_daily"
    _attr_name = "Forecast Daily"
    _attr_icon = "mdi:chart-bar"

    def _update_from_coordinator(self) -> None:
        """Update with count of forecast slots."""
        self._attr_native_value = len(self.coordinator.data.daily_forecast)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return daily forecast with descriptive key names for clarity."""
        # Build forecast slots with descriptive keys
        # Each slot contains the essential time-series data for dashboards
        forecast_slots = []
        for slot in self.coordinator.data.daily_forecast:
            ts = slot.get("timestamp", "")
            forecast_slots.append(
                {
                    "time": ts[11:16] if len(ts) >= 16 else "",  # "HH:MM"
                    "hour": slot.get("hour", 0),
                    "minute": slot.get("minute", 0),
                    "predicted_soc": slot.get("predicted_soc"),
                    "solar_kwh": slot.get("solar_kwh"),
                    "consumption_kwh": slot.get("consumption_kwh"),
                    "net_kwh": slot.get("net_kwh"),
                    "buy_price": slot.get("buy_price"),
                    "sell_price": slot.get("sell_price"),
                }
            )

        # SOC series for graphing (lightweight format)
        soc_series = []
        for slot in self.coordinator.data.daily_forecast_soc_15min:
            if len(slot) >= 2:
                soc_series.append({"time": slot[0], "soc": slot[1]})

        return {
            # Core forecast data (96 slots × 8 fields ≈ 8KB)
            "forecast_slots": forecast_slots,
            # SOC time series for graphing
            "soc_series": soc_series,
            # Summary counts
            "slot_count": len(self.coordinator.data.daily_forecast),
            "solcast_today_entries": len(self.coordinator.data.solcast_today),
            "solcast_tomorrow_entries": len(self.coordinator.data.solcast_tomorrow),
            # Hourly summary for quick reference
            "forecast_hourly": self.coordinator.data.daily_forecast_hourly,
        }


class ForecastPricesSensor(LocalShiftSensorBase):
    """Price forecast data for history collection.

    Split from DailyForecastSensor to stay under 16KB limit (Issue #37).
    Provides buy/sell price time series for analysis and dashboards.
    """

    _attr_unique_id = "localshift_forecast_prices"
    _attr_name = "Forecast Prices"
    _attr_icon = "mdi:currency-usd"

    def _update_from_coordinator(self) -> None:
        """Update with current effective cheap price."""
        self._attr_native_value = round(self.coordinator.data.effective_cheap_price, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return price forecast data."""
        # Build price time series with descriptive keys
        buy_prices = []
        sell_prices = []

        for slot in self.coordinator.data.daily_forecast:
            ts = slot.get("timestamp", "")
            time_str = ts[11:16] if len(ts) >= 16 else ""
            buy_prices.append(
                {
                    "time": time_str,
                    "hour": slot.get("hour", 0),
                    "minute": slot.get("minute", 0),
                    "price": slot.get("buy_price"),
                }
            )
            sell_prices.append(
                {
                    "time": time_str,
                    "hour": slot.get("hour", 0),
                    "minute": slot.get("minute", 0),
                    "price": slot.get("sell_price"),
                }
            )

        return {
            # Price time series (96 slots each ≈ 3KB total)
            "buy_prices": buy_prices,
            "sell_prices": sell_prices,
            # Price thresholds
            "effective_cheap_price": round(
                self.coordinator.data.effective_cheap_price, 4
            ),
            "cheap_charge_stop_price": round(
                self.coordinator.data.cheap_charge_stop_price, 4
            ),
            # Forecast cost totals (rest of today)
            "forecast_import_cost": round(
                self.coordinator.data.forecast_import_cost or 0.0, 2
            ),
            "forecast_export_revenue": round(
                self.coordinator.data.forecast_export_revenue or 0.0, 2
            ),
            "forecast_net_cost": round(
                self.coordinator.data.forecast_net_cost or 0.0, 2
            ),
            "forecast_grid_charge_cost": round(
                self.coordinator.data.forecast_grid_charge_cost or 0.0, 2
            ),
            "forecast_proactive_export_revenue": round(
                self.coordinator.data.forecast_proactive_export_revenue or 0.0, 2
            ),
        }


class ForecastGridSensor(LocalShiftSensorBase):
    """Grid interaction forecast data for history collection.

    Split from DailyForecastSensor to stay under 16KB limit (Issue #37).
    Provides grid import/export time series for analysis and dashboards.
    """

    _attr_unique_id = "localshift_forecast_grid"
    _attr_name = "Forecast Grid"
    _attr_icon = "mdi:transmission-tower"

    def _update_from_coordinator(self) -> None:
        """Update with total forecast grid import."""
        total_import = sum(
            slot.get("grid_import_kwh", 0) or 0
            for slot in self.coordinator.data.daily_forecast
        )
        self._attr_native_value = round(total_import, 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return grid interaction forecast data."""
        # Build grid interaction time series with descriptive keys
        grid_interaction = []
        total_import = 0.0
        total_export = 0.0
        grid_charge_slots = 0
        proactive_export_slots = 0

        for slot in self.coordinator.data.daily_forecast:
            ts = slot.get("timestamp", "")
            time_str = ts[11:16] if len(ts) >= 16 else ""
            import_kwh = slot.get("grid_import_kwh", 0) or 0
            export_kwh = slot.get("grid_export_kwh", 0) or 0
            is_grid_charge = slot.get("grid_charge", False)
            is_proactive_export = slot.get("proactive_export", False)

            total_import += import_kwh
            total_export += export_kwh
            if is_grid_charge:
                grid_charge_slots += 1
            if is_proactive_export:
                proactive_export_slots += 1

            grid_interaction.append(
                {
                    "time": time_str,
                    "hour": slot.get("hour", 0),
                    "minute": slot.get("minute", 0),
                    "grid_import_kwh": round(import_kwh, 4),
                    "grid_export_kwh": round(export_kwh, 4),
                    "grid_charge": is_grid_charge,
                    "grid_charge_boost": slot.get("grid_charge_boost", False),
                    "proactive_export": is_proactive_export,
                    "export_amount_kwh": round(
                        slot.get("export_amount_kwh", 0) or 0, 4
                    ),
                }
            )

        return {
            # Grid interaction time series (96 slots × 8 fields ≈ 4KB)
            "grid_interaction": grid_interaction,
            # Summary totals
            "total_grid_import_kwh": round(total_import, 3),
            "total_grid_export_kwh": round(total_export, 3),
            "grid_charge_slots": grid_charge_slots,
            "proactive_export_slots": proactive_export_slots,
        }


class ForecastDiagnosticsSensor(LocalShiftSensorBase):
    """Diagnostic and debug data for the forecast system.

    Split from DailyForecastSensor to stay under 16KB limit (Issue #37).
    Contains consumption profiles, weather correlation, and debug fields.
    """

    _attr_unique_id = "localshift_forecast_diagnostics"
    _attr_name = "Forecast Diagnostics"
    _attr_icon = "mdi:bug-outline"

    def _update_from_coordinator(self) -> None:
        """Update with consumption source."""
        self._attr_native_value = self.coordinator.data.consumption_source

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic and debug data."""
        sample_counts = self.coordinator.data.consumption_hourly_sample_counts
        profile_kw = self.coordinator.data.consumption_hourly_profile_kw
        source_counts = self.coordinator.data.forecast_consumption_source_counts

        return {
            # Consumption profile information
            "consumption_source": self.coordinator.data.consumption_source,
            "consumption_statistic_id": self.coordinator.data.consumption_statistic_id,
            "consumption_profile_hours": self.coordinator.data.consumption_profile_hours,
            "consumption_fallback_hours": self.coordinator.data.consumption_fallback_hours,
            "consumption_weighting": round(
                self.coordinator.data.consumption_weighting, 2
            ),
            "forecast_consumption_source_counts": dict(source_counts),
            "consumption_hourly_sample_counts": {
                str(hour): count for hour, count in sorted(sample_counts.items())
            },
            "consumption_hourly_profile_kw": {
                str(hour): value for hour, value in sorted(profile_kw.items())
            },
            # Day-of-week aware consumption profiles (issue-60)
            "consumption_profile_type": self.coordinator.data.consumption_profile_type,
            "forecast_profile_selected": self.coordinator.data.forecast_profile_selected,
            "weekday_sample_counts": {
                str(hour): count
                for hour, count in sorted(
                    self.coordinator.data.weekday_sample_counts.items()
                )
            },
            "weekend_sample_counts": {
                str(hour): count
                for hour, count in sorted(
                    self.coordinator.data.weekend_sample_counts.items()
                )
            },
            "weekday_hourly_profile_kw": {
                str(hour): value
                for hour, value in sorted(
                    self.coordinator.data.weekday_hourly_profile_kw.items()
                )
            },
            "weekend_hourly_profile_kw": {
                str(hour): value
                for hour, value in sorted(
                    self.coordinator.data.weekend_hourly_profile_kw.items()
                )
            },
            # Recent load data
            "recent_load_1hr_kw": round(self.coordinator.data.recent_load_1hr_kw, 3),
            "recent_load_1hr_statistic_id": self.coordinator.data.recent_load_1hr_statistic_id,
            "recent_load_1hr_samples": self.coordinator.data.recent_load_1hr_samples,
            "recent_load_1hr_last_error": self.coordinator.data.recent_load_1hr_last_error,
            "current_load_kw": round(self.coordinator.data.load_power_kw, 3),
            # Debug fields for troubleshooting mode decisions
            "debug_forecast_slot_found": self.coordinator.data.debug_forecast_slot_found,
            "debug_forecast_slot_time": self.coordinator.data.debug_forecast_slot_time,
            "debug_first_forecast_slot_time": self.coordinator.data.debug_first_forecast_slot_time,
            "debug_time_gap_seconds": round(
                self.coordinator.data.debug_time_gap_seconds, 1
            ),
            "debug_mode_source": self.coordinator.data.debug_mode_source,
            # Export permission
            "allow_export": self.coordinator.data.allow_export,
            # Weather correlation visibility (Issue #61)
            "weather_entity_id": self.coordinator.data.weather_entity_id,
            "weather_temperature_current": self.coordinator.data.weather_temperature_current,
            "weather_temperature_forecast": {
                str(hour): temp
                for hour, temp in sorted(
                    self.coordinator.data.weather_temperature_forecast.items()
                )
            },
            "weather_condition": self.coordinator.data.weather_condition,
            "weather_correlation_confidence": self.coordinator.data.weather_correlation_confidence,
            "weather_adjustment_applied": self.coordinator.data.weather_adjustment_applied,
            "weather_learning_enabled": self.coordinator.data.weather_learning_enabled,
            "weather_cooling_coefficient": round(
                self.coordinator.data.weather_cooling_coefficient, 4
            ),
            "weather_heating_coefficient": round(
                self.coordinator.data.weather_heating_coefficient, 4
            ),
            "weather_sample_count": self.coordinator.data.weather_sample_count,
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


# ---------------------------------------------------------------------------
# Excess Solar Load Shifting Sensors (backlog-high-017)
# ---------------------------------------------------------------------------


class ExcessSolarSensor(LocalShiftSensorBase):
    """Forecasted excess solar energy available for discretionary loads."""

    _attr_unique_id = "localshift_excess_solar_kwh"
    _attr_name = "Excess Solar"
    _attr_icon = "mdi:solar-power"
    _attr_native_unit_of_measurement = "kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        """Update with total excess until battery full."""
        self._attr_native_value = round(
            self.coordinator.data.excess_until_battery_full_kwh, 2
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return detailed excess solar attributes."""
        d = self.coordinator.data
        return {
            "excess_current_hour_kwh": round(d.excess_solar_current_hour_kwh, 2),
            "excess_next_2h_kwh": round(d.excess_solar_next_2h_kwh, 2),
            "excess_next_4h_kwh": round(d.excess_solar_next_4h_kwh, 2),
            "excess_until_battery_full_kwh": round(d.excess_until_battery_full_kwh, 2),
            "excess_until_negative_fit_kwh": round(d.excess_until_negative_fit_kwh, 2),
            "time_until_battery_full_minutes": d.time_until_battery_full_minutes,
            "negative_fit_window_start": (
                d.negative_fit_window_start.isoformat()
                if d.negative_fit_window_start
                else None
            ),
            "negative_fit_window_duration_minutes": d.negative_fit_window_duration_minutes,
            "can_add_load_now": d.can_add_load_now,
            "safe_additional_load_kw": round(d.safe_additional_load_kw, 1),
            "forecast_confidence": d.load_shift_confidence,
            "current_excess_rate_kw": round(d.current_excess_rate_kw, 2),
        }


class LoadShiftSignalSensor(LocalShiftSensorBase):
    """Actionable signal for automations indicating what to do with discretionary loads."""

    _attr_unique_id = "localshift_load_shift_signal"
    _attr_name = "Load Shift Signal"
    _attr_icon = "mdi:transfer"

    def _update_from_coordinator(self) -> None:
        """Update with current load shift signal."""
        self._attr_native_value = self.coordinator.data.load_shift_signal

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return signal details."""
        d = self.coordinator.data
        return {
            "recommended_additional_kw": round(d.load_shift_recommended_kw, 1),
            "recommended_duration_minutes": d.load_shift_recommended_duration_minutes,
            "signal_reason": d.load_shift_reason,
            "signal_confidence": d.load_shift_confidence,
            "current_excess_rate_kw": round(d.current_excess_rate_kw, 2),
            "grid_charge_risk": d.grid_charge_risk,
            "safe_additional_load_kw": round(d.safe_additional_load_kw, 1),
        }

    @property
    def icon(self) -> str:
        """Return icon based on signal state."""
        signal = self._attr_native_value
        if signal == "INCREASE_LOAD":
            return "mdi:arrow-up-bold"
        elif signal == "REDUCE_LOAD":
            return "mdi:arrow-down-bold"
        elif signal == "MAINTAIN_LOAD":
            return "mdi:check-circle"
        else:  # HOLD
            return "mdi:pause-circle"

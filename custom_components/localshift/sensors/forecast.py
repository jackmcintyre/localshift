from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorStateClass
from homeassistant.util import dt as dt_util

from ..pricing.types import ForecastSlot
from .base import LocalShiftSensorBase

if TYPE_CHECKING:
    pass


def _get_slot_attr(slot: Any, key: str, default: Any = None) -> Any:
    """Get attribute from dict or ForecastSlot object.

    Issue #300: Handles both dict and ForecastSlot types for backwards compatibility.
    """
    if isinstance(slot, dict):
        return slot.get(key, default)
    elif isinstance(slot, ForecastSlot):
        return getattr(slot, key, default)
    else:
        return default


class SolarBatteryForecastSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_forecast_battery"
    _attr_name = "Forecast Battery"
    _attr_icon = "mdi:chart-line"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        forecast = self.coordinator.data.solar_battery_forecast
        self._attr_native_value = forecast.get("predicted_soc", 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = dict(self.coordinator.data.solar_battery_forecast)
        from custom_components.localshift.forecast.analysis_resolver import (
            ConfidenceResolver,
        )

        resolver = ConfidenceResolver(
            getattr(self.coordinator.data, "solcast_analysis_today", None),
            getattr(self.coordinator.data, "solcast_analysis_tomorrow", None),
            absent_confidence=getattr(self.coordinator.data, "solar_absent_confidence", 1.0),
        )
        confidence = resolver.get_confidence(dt_util.now())
        attrs["solar_confidence_used"] = confidence
        attrs["solar_blend_applied"] = confidence < 1.0
        return attrs


class NetElectricityCostSensor(LocalShiftSensorBase):
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
        d = self.coordinator.data
        return {
            "grid_import_cost": round(d.grid_import_cost or 0.0, 2),
            "grid_export_revenue": round(d.grid_export_revenue or 0.0, 2),
            "battery_savings": round(d.battery_savings or 0.0, 2),
            "battery_charge_cost": round(d.battery_charge_cost or 0.0, 2),
        }


class DecisionLogSensor(LocalShiftSensorBase):
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
    _attr_unique_id = "localshift_forecast_history"
    _attr_name = "Forecast History"
    _attr_icon = "mdi:chart-line-variant"

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = len(self.coordinator.data.forecast_history)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"history": self.coordinator.data.forecast_history}


class OptimizerPlanSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_optimizer_plan"
    _attr_name = "Optimizer Plan"
    _attr_icon = "mdi:chart-bar"
    _unrecorded_attributes = frozenset({"slots"})

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = len(self.coordinator.data.optimizer_decisions or [])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        decisions = self.coordinator.data.optimizer_decisions or []
        d = self.coordinator.data

        slots = []
        for dec in decisions:
            slots.append({
                "slot_idx": dec.get("slot_index"),
                "action": dec.get("action"),
                "reason_code": dec.get("reason_code"),
                "objective_terms": dec.get("objective_terms", {}),
            })

        return {
            "slots": slots,
            "total_slots": len(slots),
            "forecast_horizon_hours": d.forecast_horizon_hours,
            "planner": "DP_OPTIMIZER",
        }


class ForecastPricesSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_forecast_prices"
    _attr_name = "Forecast Prices"
    _attr_icon = "mdi:currency-usd"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = round(self.coordinator.data.effective_cheap_price, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        decisions = d.optimizer_decisions or []

        buy_prices = []
        sell_prices = []

        if decisions:
            for dec in decisions:
                ts = dec.get("timestamp_iso", "")
                time_str = ts[11:16] if len(ts) >= 16 else ""
                buy_prices.append({
                    "time": time_str,
                    "price": dec.get("buy_price"),
                })
                sell_prices.append({
                    "time": time_str,
                    "price": dec.get("sell_price"),
                })
        else:
            # Issue #300: Use normalized ForecastSlot fields (start_time, per_kwh)
            # Handle both dict and ForecastSlot types
            for slot in d.general_forecast:
                ts = _get_slot_attr(slot, "start_time", "")
                if isinstance(ts, datetime):
                    ts = ts.isoformat()
                time_str = ts[11:16] if len(ts) >= 16 else ""
                buy_prices.append({
                    "time": time_str,
                    "price": _get_slot_attr(slot, "per_kwh"),
                })
            for slot in d.feed_in_forecast:
                ts = _get_slot_attr(slot, "start_time", "")
                if isinstance(ts, datetime):
                    ts = ts.isoformat()
                time_str = ts[11:16] if len(ts) >= 16 else ""
                sell_prices.append({
                    "time": time_str,
                    "price": _get_slot_attr(slot, "per_kwh"),
                })

        return {
            "buy_prices": buy_prices,
            "sell_prices": sell_prices,
            "effective_cheap_price": round(d.effective_cheap_price, 4),
            "cheap_charge_stop_price": round(d.cheap_charge_stop_price, 4),
            "planner_threshold_used": round(d.planner_threshold_used, 4)
            if d.planner_threshold_used is not None
            else None,
            "forecast_import_cost": round(d.forecast_import_cost or 0.0, 2),
            "forecast_export_revenue": round(d.forecast_export_revenue or 0.0, 2),
            "forecast_net_cost": round(d.forecast_net_cost or 0.0, 2),
            "forecast_grid_charge_cost": round(d.forecast_grid_charge_cost or 0.0, 2),
            "forecast_proactive_export_revenue": round(
                d.forecast_proactive_export_revenue or 0.0, 2
            ),
        }


class OptimizerPlanGridSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_optimizer_plan_grid"
    _attr_name = "Optimizer Plan Grid"
    _attr_icon = "mdi:transmission-tower"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        summary = self.coordinator.data.optimizer_summary or {}
        self._attr_native_value = round(summary.get("projected_net_cost", 0.0), 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        decisions = d.optimizer_decisions or []
        summary = d.optimizer_summary or {}

        projected_import = summary.get("projected_import_kwh", 0.0)
        projected_export = summary.get("projected_export_kwh", 0.0)
        projected_net_cost = summary.get("projected_net_cost", 0.0)

        action_counts: dict[str, int] = {}
        for dec in decisions:
            action = dec.get("action", "UNKNOWN")
            action_counts[action] = action_counts.get(action, 0) + 1

        return {
            "projected_import_kwh": round(projected_import, 3),
            "projected_export_kwh": round(projected_export, 3),
            "projected_net_cost": round(projected_net_cost, 3),
            "action_breakdown": action_counts,
            "planner": "DP_OPTIMIZER",
        }


class ForecastDiagnosticsSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_forecast_diagnostics"
    _attr_name = "Forecast Diagnostics"
    _attr_icon = "mdi:bug-outline"

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = self.coordinator.data.consumption_source

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sample_counts = self.coordinator.data.consumption_hourly_sample_counts
        profile_kw = self.coordinator.data.consumption_hourly_profile_kw
        source_counts = self.coordinator.data.forecast_consumption_source_counts

        return {
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
            "recent_load_1hr_kw": round(self.coordinator.data.recent_load_1hr_kw, 3),
            "recent_load_1hr_statistic_id": self.coordinator.data.recent_load_1hr_statistic_id,
            "recent_load_1hr_samples": self.coordinator.data.recent_load_1hr_samples,
            "recent_load_1hr_last_error": self.coordinator.data.recent_load_1hr_last_error,
            "current_load_kw": round(self.coordinator.data.load_power_kw, 3),
            "debug_forecast_slot_found": self.coordinator.data.debug_forecast_slot_found,
            "debug_forecast_slot_time": self.coordinator.data.debug_forecast_slot_time,
            "debug_first_forecast_slot_time": self.coordinator.data.debug_first_forecast_slot_time,
            "debug_time_gap_seconds": round(
                self.coordinator.data.debug_time_gap_seconds, 1
            ),
            "debug_mode_source": self.coordinator.data.debug_mode_source,
            "allow_export": self.coordinator.data.allow_export,
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
            "weather_avg_cooling_slope": round(
                self.coordinator.data.weather_avg_cooling_slope, 4
            ),
            "weather_avg_heating_slope": round(
                self.coordinator.data.weather_avg_heating_slope, 4
            ),
            "weather_avg_r_squared": round(
                self.coordinator.data.weather_avg_r_squared, 4
            ),
            "weather_sample_count": self.coordinator.data.weather_sample_count,
            "load_forecast_slots_sample": {
                "count": len(self.coordinator.data.load_forecast_slots),
                "first_5": [
                    round(v, 3) for v in self.coordinator.data.load_forecast_slots[:5]
                ]
                if self.coordinator.data.load_forecast_slots
                else [],
                "noon_idx_4": round(self.coordinator.data.load_forecast_slots[4], 3)
                if len(self.coordinator.data.load_forecast_slots) > 4
                else None,
                "noon_idx_5": round(self.coordinator.data.load_forecast_slots[5], 3)
                if len(self.coordinator.data.load_forecast_slots) > 5
                else None,
                "idx_8": round(self.coordinator.data.load_forecast_slots[8], 3)
                if len(self.coordinator.data.load_forecast_slots) > 8
                else None,
            },
            "adaptive_params_values": dict(self.coordinator.data.adaptive_params.values)
            if self.coordinator.data.adaptive_params
            else {},
        }


class MinimumTargetSOCSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_target_soc_minimum"
    _attr_name = "Target SOC Minimum"
    _attr_icon = "mdi:battery-charging-20"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        from ..const import CONF_MINIMUM_TARGET_SOC, DEFAULT_MINIMUM_TARGET_SOC

        self._attr_native_value = float(
            self._entry.options.get(CONF_MINIMUM_TARGET_SOC, DEFAULT_MINIMUM_TARGET_SOC)
        )

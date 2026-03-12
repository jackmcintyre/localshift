from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorStateClass

from .base import LocalShiftSensorBase

if TYPE_CHECKING:
    pass


class ExcessSolarSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_excess_solar_kwh"
    _attr_name = "Excess Solar"
    _attr_icon = "mdi:solar-power"
    _attr_native_unit_of_measurement = "kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = round(
            self.coordinator.data.excess_until_battery_full_kwh, 2
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
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
    _attr_unique_id = "localshift_load_shift_signal"
    _attr_name = "Load Shift Signal"
    _attr_icon = "mdi:transfer"

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = self.coordinator.data.load_shift_signal

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
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
        signal = self._attr_native_value
        if signal == "INCREASE_LOAD":
            return "mdi:arrow-up-bold"
        elif signal == "REDUCE_LOAD":
            return "mdi:arrow-down-bold"
        elif signal == "MAINTAIN_LOAD":
            return "mdi:check-circle"
        else:
            return "mdi:pause-circle"

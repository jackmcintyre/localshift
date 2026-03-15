from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorStateClass

from .base import LocalShiftSensorBase

if TYPE_CHECKING:
    pass


class EffectiveCheapPriceSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_price_cheap_effective"
    _attr_name = "Price Cheap Effective"
    _attr_icon = "mdi:tag-outline"
    _attr_native_unit_of_measurement = "$/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = round(self.coordinator.data.effective_cheap_price, 4)


class CheapChargeStopPriceSensor(LocalShiftSensorBase):
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
    _attr_unique_id = "localshift_solar_weighted_avg_fit"
    _attr_name = "Solar Weighted Avg FIT"
    _attr_icon = "mdi:solar-power-variant"
    _attr_native_unit_of_measurement = "$/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = round(self.coordinator.data.solar_weighted_avg_fit, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "total_solar_remaining_kwh": round(
                self.coordinator.data.solar_remaining_kwh, 2
            ),
        }


# ---------------------------------------------------------------------------
# Comparison Sensors (Issue #300)
# ---------------------------------------------------------------------------


class ComparisonResultSensor(LocalShiftSensorBase):
    """Sensor showing primary vs shadow decision match status."""

    _attr_unique_id = "localshift_comparison_result"
    _attr_name = "Comparison Result"
    _attr_icon = "mdi:compare"

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = (
            "match" if self.coordinator.data.comparison_match else "mismatch"
        )


class PriceDeltaSensor(LocalShiftSensorBase):
    """Sensor showing price difference between primary and shadow sources."""

    _attr_unique_id = "localshift_price_delta"
    _attr_name = "Price Delta"
    _attr_icon = "mdi:currency-usd"
    _attr_native_unit_of_measurement = "$/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = round(self.coordinator.data.price_delta, 4)

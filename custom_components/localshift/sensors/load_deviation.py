from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorStateClass

from .base import LocalShiftSensorBase


class LoadDeviationSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_load_deviation"
    _attr_name = "Load Deviation"
    _attr_icon = "mdi:chart-bell-curve-cumulative"
    _attr_native_unit_of_measurement = "kW"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        diagnostics = self.coordinator.data.load_deviation_diagnostics
        self._attr_native_value = round(diagnostics.get("deviation_kw", 0.0), 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self.coordinator.data.load_deviation_diagnostics)

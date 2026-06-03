from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorStateClass

from .base import LocalShiftSensorBase


class CloudEventSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_cloud_event"
    _attr_name = "Cloud Event"
    _attr_icon = "mdi:weather-partly-cloudy"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        diagnostics = self.coordinator.data.cloud_event_diagnostics
        self._attr_native_value = round(diagnostics.get("ratio") or 0.0, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self.coordinator.data.cloud_event_diagnostics)

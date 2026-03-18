from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.localshift.sensors import CloudEventSensor


def test_cloud_event_sensor_reports_ratio_as_native_value():
    coordinator = MagicMock()
    coordinator.data.cloud_event_diagnostics = {
        "status": "triggered",
        "event_type": "onset_moderate",
        "triggered": True,
        "actual_kw": 2.0,
        "forecast_kw": 5.0,
        "ratio": 0.4,
    }

    sensor = CloudEventSensor(coordinator, MagicMock())
    sensor._update_from_coordinator()

    assert sensor.native_value == 0.4
    assert sensor.extra_state_attributes == coordinator.data.cloud_event_diagnostics


def test_cloud_event_sensor_defaults_to_zero_when_no_ratio():
    coordinator = MagicMock()
    coordinator.data.cloud_event_diagnostics = {
        "status": "no_forecast",
        "triggered": False,
        "ratio": None,
    }

    sensor = CloudEventSensor(coordinator, MagicMock())
    sensor._update_from_coordinator()

    assert sensor.native_value == 0.0

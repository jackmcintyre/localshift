from unittest.mock import MagicMock

from custom_components.localshift.sensors import LoadDeviationSensor


def test_load_deviation_sensor_reports_state_and_attributes():
    coordinator = MagicMock()
    coordinator.data.load_deviation_diagnostics = {
        "status": "triggered",
        "deviation_kw": 1.25,
        "actual_kw": 4.0,
        "forecast_kw": 2.75,
        "breach_type": "sustained",
        "triggered": True,
    }

    sensor = LoadDeviationSensor(coordinator, MagicMock())
    sensor._update_from_coordinator()

    assert sensor.native_value == 1.25
    assert sensor.extra_state_attributes == coordinator.data.load_deviation_diagnostics

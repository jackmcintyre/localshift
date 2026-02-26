"""Test that sensors have correct state_class for statistics.

Issue #266: Add state_class to forecast sensors for statistics support.
"""

from unittest.mock import Mock

import pytest
from homeassistant.components.sensor import SensorStateClass

from custom_components.localshift.sensor import (
    ForecastAccuracySensor,
    ForecastGridSensor,
    ForecastPricesSensor,
    SolarBatteryForecastSensor,
)


@pytest.mark.parametrize(
    "sensor_class,expected_state_class",
    [
        (SolarBatteryForecastSensor, SensorStateClass.MEASUREMENT),
        (ForecastAccuracySensor, SensorStateClass.MEASUREMENT),
        (ForecastGridSensor, SensorStateClass.MEASUREMENT),
        (ForecastPricesSensor, SensorStateClass.MEASUREMENT),
    ],
)
def test_forecast_sensors_have_state_class(sensor_class, expected_state_class):
    """Verify forecast sensors have state_class for statistics."""
    # Create a mock coordinator and entry
    mock_coordinator = Mock()
    mock_coordinator.data = Mock()
    mock_entry = Mock()

    sensor = sensor_class(mock_coordinator, mock_entry)

    assert sensor._attr_state_class == expected_state_class


def test_forecast_battery_sensor_has_measurement_state_class():
    """Verify SolarBatteryForecastSensor has MEASUREMENT state_class."""
    mock_coordinator = Mock()
    mock_coordinator.data = Mock()
    mock_entry = Mock()

    sensor = SolarBatteryForecastSensor(mock_coordinator, mock_entry)

    assert sensor._attr_state_class == SensorStateClass.MEASUREMENT


def test_forecast_accuracy_sensor_has_measurement_state_class():
    """Verify ForecastAccuracySensor has MEASUREMENT state_class."""
    mock_coordinator = Mock()
    mock_coordinator.data = Mock()
    mock_entry = Mock()

    sensor = ForecastAccuracySensor(mock_coordinator, mock_entry)

    assert sensor._attr_state_class == SensorStateClass.MEASUREMENT


def test_forecast_grid_sensor_has_measurement_state_class():
    """Verify ForecastGridSensor has MEASUREMENT state_class."""
    mock_coordinator = Mock()
    mock_coordinator.data = Mock()
    mock_entry = Mock()

    sensor = ForecastGridSensor(mock_coordinator, mock_entry)

    assert sensor._attr_state_class == SensorStateClass.MEASUREMENT


def test_forecast_prices_sensor_has_measurement_state_class():
    """Verify ForecastPricesSensor has MEASUREMENT state_class."""
    mock_coordinator = Mock()
    mock_coordinator.data = Mock()
    mock_entry = Mock()

    sensor = ForecastPricesSensor(mock_coordinator, mock_entry)

    assert sensor._attr_state_class == SensorStateClass.MEASUREMENT

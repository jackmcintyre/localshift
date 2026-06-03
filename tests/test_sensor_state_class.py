"""Test that sensors have correct state_class for statistics.

Issue #266: Add state_class to forecast sensors for statistics support.
Issue #703: OptimizerAdvantageSensor must use TOTAL for monetary device_class.
"""

from unittest.mock import Mock

import pytest
from homeassistant.components.sensor import SensorStateClass

from custom_components.localshift.sensor import (
    ForecastAccuracySensor,
    ForecastPricesSensor,
    OptimizerAdvantageSensor,
    OptimizerPlanGridSensor,
    SolarBatteryForecastSensor,
)


@pytest.mark.parametrize(
    "sensor_class,expected_state_class",
    [
        (SolarBatteryForecastSensor, SensorStateClass.MEASUREMENT),
        (ForecastAccuracySensor, SensorStateClass.MEASUREMENT),
        (OptimizerPlanGridSensor, SensorStateClass.MEASUREMENT),
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


def test_optimizer_plan_grid_sensor_has_measurement_state_class():
    """Verify OptimizerPlanGridSensor has MEASUREMENT state_class."""
    mock_coordinator = Mock()
    mock_coordinator.data = Mock()
    mock_entry = Mock()

    sensor = OptimizerPlanGridSensor(mock_coordinator, mock_entry)

    assert sensor._attr_state_class == SensorStateClass.MEASUREMENT


def test_forecast_prices_sensor_has_measurement_state_class():
    """Verify ForecastPricesSensor has MEASUREMENT state_class."""
    mock_coordinator = Mock()
    mock_coordinator.data = Mock()
    mock_entry = Mock()

    sensor = ForecastPricesSensor(mock_coordinator, mock_entry)

    assert sensor._attr_state_class == SensorStateClass.MEASUREMENT


def test_optimizer_advantage_sensor_uses_total_state_class():
    mock_coordinator = Mock()
    mock_coordinator.data = Mock()
    mock_entry = Mock()

    sensor = OptimizerAdvantageSensor(mock_coordinator, mock_entry)

    assert sensor._attr_state_class == SensorStateClass.TOTAL

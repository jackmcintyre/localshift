import pytest


class TestForecastModule:
    def test_import(self):
        from custom_components.localshift.sensors.forecast import (
            SolarBatteryForecastSensor,
        )

        assert SolarBatteryForecastSensor is not None

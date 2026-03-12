import pytest


class TestBaseModule:
    def test_import(self):
        from custom_components.localshift.sensors.base import LocalShiftSensorBase

        assert LocalShiftSensorBase is not None

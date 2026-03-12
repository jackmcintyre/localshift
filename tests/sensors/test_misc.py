import pytest


class TestMiscModule:
    def test_import(self):
        from custom_components.localshift.sensors.misc import ExcessSolarSensor

        assert ExcessSolarSensor is not None

import pytest


class TestStatusModule:
    def test_import(self):
        from custom_components.localshift.sensors.status import IntegrationStatusSensor

        assert IntegrationStatusSensor is not None

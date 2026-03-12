import pytest


class TestOptimizerModule:
    def test_import(self):
        from custom_components.localshift.sensors.optimizer import (
            OptimizerPlanDetailedSensor,
        )

        assert OptimizerPlanDetailedSensor is not None

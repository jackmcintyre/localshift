import pytest


class TestLearningModule:
    def test_import(self):
        from custom_components.localshift.sensors.learning import LearningStatusSensor

        assert LearningStatusSensor is not None

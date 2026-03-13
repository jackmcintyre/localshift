from unittest.mock import Mock

import pytest
from homeassistant.components.sensor import SensorStateClass

from custom_components.localshift.sensors.learning import (
    DecisionQualitySensor,
    LearningDecisionHistorySensor,
    LearningStatusSensor,
    OptimizerAdvantageSensor,
)


def _make_coordinator(overrides: dict | None = None) -> Mock:
    metrics = Mock()
    metrics.total_decisions_today = 10
    metrics.avg_decision_score_today = 0.75
    metrics.avg_decision_score_7d = 0.80
    metrics.cost_trend = "stable"
    metrics.mode_durations_today = {"grid_charging": 120}
    metrics.mode_cost_attribution = {"grid_charging": 0.12, "self_consumption": 0.05}
    metrics.grid_charge_efficiency = 0.92
    metrics.export_loss_ratio = 0.03
    metrics.unnecessary_grid_charge_kwh = 1.5
    metrics.optimizer_advantage_daily = 1.23
    metrics.optimizer_advantage_7d = 8.61
    metrics.optimizer_advantage_daily_avg = 1.23
    metrics.optimizer_advantage_percent = 18.5
    metrics.counterfactual_tou_cost = 9.50
    metrics.counterfactual_actual_cost = 8.27
    metrics.counterfactual_degrading = False

    data = Mock()
    data.learning_status = "optimizing"
    data.performance_metrics = metrics
    data.optimization_weights = {"cost": 0.7, "comfort": 0.3}
    data.contextual_adjustments_active = True
    data.recent_decision_log = [{"mode": "grid_charging"} for _ in range(25)]

    coordinator = Mock()
    coordinator.data = data
    return coordinator


def _make_sensor(cls, status: str = "optimizing"):
    coordinator = _make_coordinator()
    coordinator.data.learning_status = status
    entry = Mock()
    return cls(coordinator, entry)


class TestLearningStatusSensor:
    def test_update_from_coordinator(self):
        sensor = _make_sensor(LearningStatusSensor)
        sensor._update_from_coordinator()
        assert sensor._attr_native_value == "optimizing"

    def test_extra_state_attributes(self):
        sensor = _make_sensor(LearningStatusSensor)
        sensor._update_from_coordinator()
        attrs = sensor.extra_state_attributes
        assert attrs["total_decisions_today"] == 10
        assert attrs["avg_decision_score_today"] == pytest.approx(0.75, abs=0.001)
        assert attrs["cost_trend"] == "stable"
        assert "mode_cost_attribution" in attrs
        assert "optimization_weights" in attrs
        assert attrs["contextual_adjustments_active"] is True

    def test_icon_optimizing(self):
        sensor = _make_sensor(LearningStatusSensor, status="optimizing")
        sensor._update_from_coordinator()
        assert sensor.icon == "mdi:brain"

    def test_icon_tuning(self):
        sensor = _make_sensor(LearningStatusSensor, status="tuning")
        sensor._update_from_coordinator()
        assert sensor.icon == "mdi:tune"

    def test_icon_observing(self):
        sensor = _make_sensor(LearningStatusSensor, status="observing")
        sensor._update_from_coordinator()
        assert sensor.icon == "mdi:eye"

    def test_icon_unknown(self):
        sensor = _make_sensor(LearningStatusSensor, status="unknown")
        sensor._update_from_coordinator()
        assert sensor.icon == "mdi:brain-off"


class TestDecisionQualitySensor:
    def test_update_from_coordinator(self):
        sensor = _make_sensor(DecisionQualitySensor)
        sensor._update_from_coordinator()
        assert sensor._attr_native_value == pytest.approx(75.0, abs=0.1)

    def test_extra_state_attributes(self):
        sensor = _make_sensor(DecisionQualitySensor)
        sensor._update_from_coordinator()
        attrs = sensor.extra_state_attributes
        assert attrs["total_decisions_today"] == 10
        assert "avg_score_7d" in attrs
        assert "grid_charge_efficiency" in attrs
        assert "export_loss_ratio" in attrs
        assert "unnecessary_grid_charge_kwh" in attrs

    def test_state_class_is_measurement(self):
        sensor = _make_sensor(DecisionQualitySensor)
        assert sensor._attr_state_class == SensorStateClass.MEASUREMENT


class TestLearningDecisionHistorySensor:
    def test_update_from_coordinator(self):
        sensor = _make_sensor(LearningDecisionHistorySensor)
        sensor._update_from_coordinator()
        assert sensor._attr_native_value == 25

    def test_extra_state_attributes_returns_last_20(self):
        sensor = _make_sensor(LearningDecisionHistorySensor)
        sensor._update_from_coordinator()
        attrs = sensor.extra_state_attributes
        assert "decisions" in attrs
        assert len(attrs["decisions"]) == 20


class TestOptimizerAdvantageSensor:
    def test_update_from_coordinator(self):
        sensor = _make_sensor(OptimizerAdvantageSensor)
        sensor._update_from_coordinator()
        assert sensor._attr_native_value == pytest.approx(1.23, abs=0.01)

    def test_extra_state_attributes(self):
        sensor = _make_sensor(OptimizerAdvantageSensor)
        sensor._update_from_coordinator()
        attrs = sensor.extra_state_attributes
        assert attrs["advantage_7d"] == pytest.approx(8.61, abs=0.01)
        assert attrs["advantage_percent"] == pytest.approx(18.5, abs=0.1)
        assert attrs["tou_cost"] == pytest.approx(9.50, abs=0.01)
        assert attrs["actual_cost"] == pytest.approx(8.27, abs=0.01)
        assert attrs["degrading"] is False

    def test_state_class_is_total(self):
        sensor = _make_sensor(OptimizerAdvantageSensor)
        assert sensor._attr_state_class == SensorStateClass.TOTAL


class TestImport:
    def test_import(self):
        from custom_components.localshift.sensors.learning import LearningStatusSensor

        assert LearningStatusSensor is not None

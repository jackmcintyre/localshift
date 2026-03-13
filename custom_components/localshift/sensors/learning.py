from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from .base import LocalShiftSensorBase

if TYPE_CHECKING:
    pass


class LearningStatusSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_learning_status"
    _attr_name = "Learning Status"
    _attr_icon = "mdi:brain"

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = self.coordinator.data.learning_status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        metrics = d.performance_metrics
        return {
            "total_decisions_today": metrics.total_decisions_today,
            "avg_decision_score_today": round(metrics.avg_decision_score_today, 3),
            "avg_decision_score_7d": round(metrics.avg_decision_score_7d, 3),
            "cost_trend": metrics.cost_trend,
            "mode_durations_today": metrics.mode_durations_today,
            "mode_cost_attribution": {
                k: round(v, 3) for k, v in metrics.mode_cost_attribution.items()
            },
            "optimization_weights": d.optimization_weights,
            "contextual_adjustments_active": d.contextual_adjustments_active,
        }

    @property
    def icon(self) -> str:
        status = self._attr_native_value
        if status == "optimizing":
            return "mdi:brain"
        elif status == "tuning":
            return "mdi:tune"
        elif status == "observing":
            return "mdi:eye"
        else:
            return "mdi:brain-off"


class DecisionQualitySensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_decision_quality"
    _attr_name = "Decision Quality"
    _attr_icon = "mdi:chart-line"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        score = self.coordinator.data.performance_metrics.avg_decision_score_today
        self._attr_native_value = round(score * 100, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        metrics = self.coordinator.data.performance_metrics
        return {
            "total_decisions_today": metrics.total_decisions_today,
            "avg_score_7d": round(metrics.avg_decision_score_7d * 100, 1),
            "cost_trend": metrics.cost_trend,
            "grid_charge_efficiency": round(metrics.grid_charge_efficiency * 100, 1),
            "export_loss_ratio": round(metrics.export_loss_ratio * 100, 1),
            "unnecessary_grid_charge_kwh": round(
                metrics.unnecessary_grid_charge_kwh, 2
            ),
            "mode_durations_today": metrics.mode_durations_today,
            "mode_cost_attribution": {
                k: round(v, 3) for k, v in metrics.mode_cost_attribution.items()
            },
        }


class LearningDecisionHistorySensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_learning_decision_history"
    _attr_name = "Learning Decision History"
    _attr_icon = "mdi:history"

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = len(self.coordinator.data.recent_decision_log)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "decisions": self.coordinator.data.recent_decision_log[-20:],
        }


class OptimizerAdvantageSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_optimizer_advantage"
    _attr_name = "Optimizer Advantage"
    _attr_icon = "mdi:scale-balance"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "$"
    _attr_state_class = SensorStateClass.TOTAL

    def _update_from_coordinator(self) -> None:
        metrics = self.coordinator.data.performance_metrics
        self._attr_native_value = round(metrics.optimizer_advantage_daily, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        metrics = self.coordinator.data.performance_metrics
        return {
            "advantage_7d": round(metrics.optimizer_advantage_7d, 2),
            "advantage_daily_avg": round(metrics.optimizer_advantage_daily_avg, 2),
            "advantage_percent": round(metrics.optimizer_advantage_percent, 1),
            "tou_cost": round(metrics.counterfactual_tou_cost, 2),
            "actual_cost": round(metrics.counterfactual_actual_cost, 2),
            "degrading": metrics.counterfactual_degrading,
        }

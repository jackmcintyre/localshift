from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorStateClass

from .base import LocalShiftSensorBase

if TYPE_CHECKING:
    pass


class IntegrationStatusSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_integration_status"
    _attr_name = "Integration Status"
    _attr_icon = "mdi:check-circle"

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = self.coordinator.data.integration_status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        return {
            "message": d.integration_status_message,
            "error_count": len(d.entity_errors),
            "warning_count": len(d.entity_warnings),
            "required_entities_healthy": d.required_entities_healthy,
            "errors": d.entity_errors,
            "warnings": d.entity_warnings,
            "last_check": d.last_entity_check,
        }

    @property
    def icon(self) -> str:
        status = self._attr_native_value
        if status == "ok":
            return "mdi:check-circle"
        elif status == "degraded":
            return "mdi:alert-circle"
        else:
            return "mdi:close-circle"


class EntityHealthSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_entity_health"
    _attr_name = "Entity Health"
    _attr_icon = "mdi:heart-pulse"

    def _update_from_coordinator(self) -> None:
        dep_health = self.coordinator.data.entity_health
        ls_health = self.coordinator.data.localshift_entity_health
        all_entities = {**dep_health, **ls_health}
        healthy_count = sum(1 for e in all_entities.values() if e.get("status") == "ok")
        total_count = len(all_entities)
        self._attr_native_value = f"{healthy_count}/{total_count}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dep_health = self.coordinator.data.entity_health
        ls_health = self.coordinator.data.localshift_entity_health
        return {
            "entities": dep_health,
            "dependencies": dep_health,
            "localshift_entities": ls_health,
            "errors": self.coordinator.data.entity_errors,
            "warnings": self.coordinator.data.entity_warnings,
            "summary": {
                "dependencies": {
                    "total": len(dep_health),
                    "healthy": sum(
                        1 for e in dep_health.values() if e.get("status") == "ok"
                    ),
                },
                "localshift": {
                    "total": len(ls_health),
                    "healthy": sum(
                        1 for e in ls_health.values() if e.get("status") == "ok"
                    ),
                    "by_category": {
                        "required": sum(
                            1
                            for e in ls_health.values()
                            if e.get("category") == "required"
                            and e.get("status") == "ok"
                        ),
                        "optional": sum(
                            1
                            for e in ls_health.values()
                            if e.get("category") == "optional"
                            and e.get("status") == "ok"
                        ),
                    },
                },
            },
        }


class ForecastAccuracySensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_forecast_accuracy"
    _attr_name = "Forecast Accuracy"
    _attr_icon = "mdi:target"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        accuracy = self.coordinator.data.forecast_accuracy_soc_1h
        self._attr_native_value = round(accuracy, 1) if accuracy is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        return {
            "soc_error_15min": round(d.forecast_error_soc_15min, 1),
            "soc_error_1h": round(d.forecast_error_soc_1h, 1),
            "soc_error_4h": round(d.forecast_error_soc_4h, 1),
            "soc_accuracy_15min": round(d.forecast_accuracy_soc_15min, 1)
            if d.forecast_accuracy_soc_15min is not None
            else None,
            "soc_accuracy_1h": round(d.forecast_accuracy_soc_1h, 1)
            if d.forecast_accuracy_soc_1h is not None
            else None,
            "soc_accuracy_4h": round(d.forecast_accuracy_soc_4h, 1)
            if d.forecast_accuracy_soc_4h is not None
            else None,
            "buy_price_error_1h": round(d.forecast_error_buy_price_1h, 4),
            "sell_price_error_1h": round(d.forecast_error_sell_price_1h, 4),
            "comparisons_made": d.forecast_comparisons_made,
            "last_comparison_time": d.forecast_last_comparison_time,
            "first_prediction_time": d.forecast_first_prediction_time,
            "history_count": d.forecast_history_count,
        }


class ForecastStatusSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_forecast_status"
    _attr_name = "Forecast Status"
    _attr_icon = "mdi:weather-sunny"

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = self.coordinator.data.forecast_status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        return {
            "forecast_ready": d.forecast_ready,
            "solcast_today_entries": len(d.solcast_today),
            "solcast_tomorrow_entries": len(d.solcast_tomorrow),
            "debug_mode_source": d.debug_mode_source,
        }

    @property
    def icon(self) -> str:
        status = self._attr_native_value
        if status == "ready":
            return "mdi:check-circle"
        elif status == "partial":
            return "mdi:alert-circle"
        elif status == "stale":
            return "mdi:close-circle"
        else:
            return "mdi:weather-sunny-alert"


class AutomationReadySensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_automation_ready"
    _attr_name = "Automation Ready"
    _attr_icon = "mdi:check-decagram"

    def _update_from_coordinator(self) -> None:
        if self.coordinator.data.automation_ready:
            self._attr_native_value = "ready"
        else:
            self._attr_native_value = "not_ready"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        return {
            "automation_ready": d.automation_ready,
            "status_checks": d.automation_ready_status,
            "missing_inputs": d.automation_ready_missing,
            "soc": d.soc,
            "operation_mode": d.operation_mode,
            "backup_reserve": d.backup_reserve,
            "prices_available": d.prices_available,
            "forecast_status": d.forecast_status,
        }

    @property
    def icon(self) -> str:
        if self._attr_native_value == "ready":
            return "mdi:check-decagram"
        else:
            return "mdi:decagram-outline"


class ExtendedForecastAccuracySensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_extended_forecast_accuracy"
    _attr_name = "Extended Forecast Accuracy"
    _attr_icon = "mdi:chart-timeline-variant"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        acc = self.coordinator.data.extended_accuracy_metrics.accuracy_24h
        self._attr_native_value = round(acc, 1) if acc is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        m = self.coordinator.data.extended_accuracy_metrics
        return {
            "accuracy_24h": round(m.accuracy_24h, 1)
            if m.accuracy_24h is not None
            else None,
            "accuracy_7d": round(m.accuracy_7d, 1)
            if m.accuracy_7d is not None
            else None,
            "accuracy_30d": round(m.accuracy_30d, 1)
            if m.accuracy_30d is not None
            else None,
            "bias": round(m.bias, 2),
            "mape": round(m.mape, 2),
            "sample_count": m.sample_count,
            "last_updated": m.last_updated.isoformat() if m.last_updated else None,
        }


class DecisionLagSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_decision_lag"
    _attr_name = "Decision Lag"
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "s"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        lag = self.coordinator.data.decision_lag_seconds
        if lag is not None:
            self._attr_native_value = round(lag, 2)
        else:
            self._attr_native_value = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        history = d.decision_lag_history or []

        if history:
            lag_values = [h["lag_seconds"] for h in history]
            avg_lag = sum(lag_values) / len(lag_values)
            max_lag = max(lag_values)
            min_lag = min(lag_values)
        else:
            avg_lag = None
            max_lag = None
            min_lag = None

        return {
            "current_lag": round(d.decision_lag_seconds, 2)
            if d.decision_lag_seconds is not None
            else None,
            "last_transition": history[-1] if history else None,
            "history": history[-20:],
            "avg_lag_24h": round(avg_lag, 2) if avg_lag is not None else None,
            "max_lag_24h": round(max_lag, 2) if max_lag is not None else None,
            "min_lag_24h": round(min_lag, 2) if min_lag is not None else None,
            "total_transitions": len(history),
            "decision_timestamp": d.decision_timestamp.isoformat()
            if d.decision_timestamp
            else None,
            "implementation_timestamp": d.implementation_timestamp.isoformat()
            if d.implementation_timestamp
            else None,
        }

    @property
    def icon(self) -> str:
        if self.coordinator.data.decision_timestamp is not None:
            return "mdi:timer-sand"
        return "mdi:timer-outline"

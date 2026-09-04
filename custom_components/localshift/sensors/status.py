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
    _unrecorded_attributes = frozenset({
        "entities",
        "dependencies",
        "localshift_entities",
        "orphaned_entities",
        "errors",
        "warnings",
    })

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
        orphans = self.coordinator.data.orphaned_localshift_entities
        return {
            "entities": dep_health,
            "dependencies": dep_health,
            "localshift_entities": ls_health,
            "orphaned_entities": orphans,
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
                "orphaned_count": len(orphans),
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


class DecisionLagSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_decision_lag"
    _attr_name = "Decision Lag"
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "s"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        d = self.coordinator.data
        # Issue #917: native value is ALWAYS numeric. A None would render the
        # HA state as "unknown", which the entity validator counts as a
        # consecutive failure — 10 of them mark the entity broken during quiet
        # periods. 0.0 means "never measured"; STALE (which does not count) is
        # then the worst status the sensor can reach.
        if d.physical_response_lag_seconds is not None:
            self._attr_native_value = round(d.physical_response_lag_seconds, 2)
        elif d.decision_lag_seconds is not None:
            self._attr_native_value = round(d.decision_lag_seconds, 2)
        else:
            self._attr_native_value = 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        history = d.decision_lag_history or []

        command_lags = [
            h["command_lag"] for h in history if h.get("command_lag") is not None
        ]
        physical_lags = [
            h["physical_lag"] for h in history if h.get("physical_lag") is not None
        ]

        def _round_or_none(values: list[float]) -> float | None:
            return round(sum(values) / len(values), 2) if values else None

        return {
            "command_lag_seconds": round(d.decision_lag_seconds, 2)
            if d.decision_lag_seconds is not None
            else None,
            "physical_lag_seconds": round(d.physical_response_lag_seconds, 2)
            if d.physical_response_lag_seconds is not None
            else None,
            "physical_lag_observable": d.physical_response_watch is not None
            or bool(history and history[-1].get("observable")),
            "physical_response_timed_out": d.physical_response_timed_out,
            "current_lag": round(d.decision_lag_seconds, 2)
            if d.decision_lag_seconds is not None
            else None,
            "last_transition": history[-1] if history else None,
            "history": history[-20:],
            "avg_lag_24h": _round_or_none(command_lags),
            "max_lag_24h": round(max(command_lags), 2) if command_lags else None,
            "min_lag_24h": round(min(command_lags), 2) if command_lags else None,
            "avg_physical_lag": _round_or_none(physical_lags),
            "total_transitions": len(history),
            "decision_timestamp": d.decision_timestamp.isoformat()
            if d.decision_timestamp
            else None,
            "command_completion_timestamp": d.command_completion_timestamp.isoformat()
            if d.command_completion_timestamp
            else None,
            # Issue #510 slice 1 (measurement only): boundary-lag telemetry.
            # History windowed to 20 for attribute-size parity with `history`
            # above; the full 200-entry window stays in CoordinatorData.
            "boundary_lag_seconds": round(d.boundary_lag_seconds, 2)
            if d.boundary_lag_seconds is not None
            else None,
            "boundary_lag_history": (d.boundary_lag_history or [])[-20:],
            "anticipated_transitions_today": d.anticipated_transitions_today,
            "anticipation_corrections_today": d.anticipation_corrections_today,
        }

    @property
    def icon(self) -> str:
        if self.coordinator.data.decision_timestamp is not None:
            return "mdi:timer-sand"
        return "mdi:timer-outline"


class LearningDecisionHistorySensor(LocalShiftSensorBase):
    """Recent mode decisions and their measured outcomes.

    Decision telemetry, not learning: nothing consumes these records to change
    behaviour. Kept because the record set is the only durable asset the
    retired parameter-learning layer produced, and any future attempt would
    need it. The unique_id predates the rename and must not change.
    """

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

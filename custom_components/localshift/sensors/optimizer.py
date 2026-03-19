from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorStateClass
from homeassistant.util import dt as dt_util

from .base import LocalShiftSensorBase

if TYPE_CHECKING:
    pass


class OptimizerPlanDetailedSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_optimizer_plan_detailed"
    _attr_name = "Optimizer Plan Detailed"
    _attr_icon = "mdi:format-list-bulleted"
    _unrecorded_attributes = frozenset({"decisions"})

    def _update_from_coordinator(self) -> None:
        summary = self.coordinator.data.optimizer_summary or {}
        if not summary or not summary.get("enabled", False):
            self._attr_native_value = "disabled"
        elif not summary.get("success", False):
            self._attr_native_value = "error"
        else:
            self._attr_native_value = "computed"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        decisions = d.optimizer_decisions or []
        summary = d.optimizer_summary or {}
        from custom_components.localshift.forecast.analysis_resolver import (
            ConfidenceResolver,
        )

        resolver = ConfidenceResolver(
            getattr(d, "solcast_analysis_today", None),
            getattr(d, "solcast_analysis_tomorrow", None),
        )
        now = dt_util.now()
        confidences = [
            resolver.get_confidence(now + timedelta(hours=i)) for i in range(24)
        ]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 1.0

        return {
            "enabled": summary.get("enabled", False),
            "success": summary.get("success", False),
            "error_message": summary.get("error_message"),
            "decisions": decisions,
            "total_slots": len(decisions),
            "forecast_horizon_hours": d.forecast_horizon_hours,
            "computed_at": summary.get("cycle_timestamp_iso")
            or summary.get("computed_at"),
            "solar_confidence_avg": avg_confidence,
            "solar_confidence_regime": (
                "high"
                if avg_confidence >= 0.7
                else "medium"
                if avg_confidence >= 0.4
                else "low"
            ),
            "solar_blend_applied": avg_confidence < 1.0,
        }

    @property
    def icon(self) -> str:
        status = self._attr_native_value
        if status == "computed":
            return "mdi:format-list-bulleted"
        elif status == "error":
            return "mdi:alert-circle-outline"
        else:
            return "mdi:minus-circle-outline"


class OptimizerSummarySensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_optimizer_summary"
    _attr_name = "Optimizer Summary"
    _attr_icon = "mdi:chart-box-outline"

    def _update_from_coordinator(self) -> None:
        summary = self.coordinator.data.optimizer_summary or {}
        if not summary or not summary.get("enabled", False):
            self._attr_native_value = "disabled"
        elif summary.get("success", False):
            self._attr_native_value = "success"
        else:
            self._attr_native_value = "failed"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        summary = self.coordinator.data.optimizer_summary or {}
        d = self.coordinator.data
        from custom_components.localshift.forecast.analysis_resolver import (
            ConfidenceResolver,
        )

        resolver = ConfidenceResolver(
            getattr(d, "solcast_analysis_today", None),
            getattr(d, "solcast_analysis_tomorrow", None),
        )
        now = dt_util.now()
        confidences = [
            resolver.get_confidence(now + timedelta(hours=i)) for i in range(24)
        ]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 1.0

        return {
            "enabled": summary.get("enabled", False),
            "success": summary.get("success", False),
            "error_message": summary.get("error_message"),
            "computed_at": summary.get("cycle_timestamp_iso")
            or summary.get("computed_at"),
            "config_options": summary.get("config_options", {}),
            "parity_completeness_pct": summary.get("parity_completeness_pct"),
            "parity_defaulted_fields": summary.get("parity_defaulted_fields", {}),
            "alignment_valid": summary.get("alignment_valid"),
            "alignment_issues": summary.get("alignment_issues", []),
            "alignment_warnings": summary.get("alignment_warnings", []),
            "planner_version": summary.get("planner_version"),
            "cycle_id": summary.get("cycle_id"),
            "solve_time_seconds": summary.get("solve_time_seconds"),
            "projected_net_cost": summary.get("projected_net_cost"),
            "terminal_shortfall_pct": summary.get("terminal_shortfall_pct"),
            "initial_soc_pct": summary.get("initial_soc_pct"),
            "peak_soc_pct": summary.get("peak_soc_pct"),
            "dw_entry_soc_pct": summary.get("dw_entry_soc_pct"),
            "projected_solar_gain_pct": summary.get("projected_solar_gain_pct"),
            "forecast_accuracy": summary.get("forecast_accuracy"),
            "accuracy_discount_factor": summary.get("accuracy_discount_factor"),
            "adjusted_solar_gain_pct": summary.get("adjusted_solar_gain_pct"),
            "effective_soc_at_terminal": summary.get("effective_soc_at_terminal"),
            "solar_confidence_avg": avg_confidence,
            "solar_confidence_regime": (
                "high"
                if avg_confidence >= 0.7
                else "medium"
                if avg_confidence >= 0.4
                else "low"
            ),
            "solar_blend_applied": avg_confidence < 1.0,
        }

    @property
    def icon(self) -> str:
        status = self._attr_native_value
        if status == "success":
            return "mdi:check-circle-outline"
        elif status == "failed":
            return "mdi:alert-circle-outline"
        else:
            return "mdi:minus-circle-outline"


class SolarForecastAccuracySensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_solar_forecast_accuracy"
    _attr_name = "Solar Forecast Accuracy"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:solar-power-variant"

    def _update_from_coordinator(self) -> None:
        self._attr_native_value = getattr(
            self.coordinator.data, "solar_forecast_accuracy", 100.0
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return getattr(self.coordinator.data, "solar_bias_metrics", {})

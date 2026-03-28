from __future__ import annotations

from datetime import timedelta
import math
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.util import dt as dt_util

from custom_components.localshift.const import BatteryMode

from .base import LocalShiftSensorBase

MODE_RATE_MAX_ROWS_PER_MODE = 48
MODE_RATE_MAX_TOTAL_ROWS = 120
MODE_RATE_STALE_MINUTES = 1440
MODE_RATE_REQUIRED_KEYS = tuple(mode.value for mode in BatteryMode)


def _is_mode_analysis_stale(payload: dict[str, Any]) -> bool:
    generated_at_raw = payload.get("generated_at")
    if not isinstance(generated_at_raw, str) or not generated_at_raw:
        return True

    generated_at = dt_util.parse_datetime(generated_at_raw)
    now = dt_util.now()
    if generated_at is None or now is None:
        return True

    return (dt_util.as_utc(now) - dt_util.as_utc(generated_at)) > timedelta(
        minutes=MODE_RATE_STALE_MINUTES
    )


def _sanitize_mode_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []

    sanitized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        soc = row.get("soc")
        n = row.get("n")
        charge_kw = row.get("charge_kw")
        discharge_kw = row.get("discharge_kw")
        if not isinstance(soc, (int, float)):
            continue
        if not isinstance(n, (int, float)):
            continue
        if not isinstance(charge_kw, (int, float)):
            continue
        if not isinstance(discharge_kw, (int, float)):
            continue

        soc_value = float(soc)
        n_value = float(n)
        if not math.isfinite(soc_value) or not math.isfinite(n_value):
            continue
        if not soc_value.is_integer() or not n_value.is_integer():
            continue

        soc_int = int(soc_value)
        n_int = int(n_value)

        if not (0 <= soc_int <= 100):
            continue
        if n_int <= 0:
            continue
        if not math.isfinite(float(charge_kw)) or not math.isfinite(
            float(discharge_kw)
        ):
            continue

        sanitized.append({
            "soc": soc_int,
            "n": n_int,
            "charge_kw": float(charge_kw),
            "discharge_kw": float(discharge_kw),
        })

    sanitized.sort(key=lambda row: row["soc"])
    return sanitized[:MODE_RATE_MAX_ROWS_PER_MODE]


def _sanitize_bins_by_mode(
    bins_by_mode: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    sparse_bins: dict[str, list[dict[str, Any]]] = {}
    remaining_rows = MODE_RATE_MAX_TOTAL_ROWS

    for mode in MODE_RATE_REQUIRED_KEYS:
        if remaining_rows <= 0:
            sparse_bins[mode] = []
            continue

        rows = _sanitize_mode_rows(bins_by_mode.get(mode))
        limited_rows = rows[:remaining_rows]
        sparse_bins[mode] = limited_rows
        remaining_rows -= len(limited_rows)

    return sparse_bins


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


class ChargeRateModeAnalysisSensor(LocalShiftSensorBase):
    _attr_unique_id = "localshift_charge_rate_mode_analysis"
    _attr_name = "Charge Rate Mode Analysis"
    _attr_icon = "mdi:chart-timeline-variant"

    def _update_from_coordinator(self) -> None:
        payload = self.coordinator.data.charge_rate_mode_analysis
        if (
            isinstance(payload, dict)
            and payload
            and not _is_mode_analysis_stale(payload)
        ):
            self._attr_native_value = "ready"
        else:
            self._attr_native_value = "stale"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        payload = self.coordinator.data.charge_rate_mode_analysis
        payload_dict = payload if isinstance(payload, dict) else {}
        raw_bins = payload_dict.get("soc_bins_1pct_by_mode")
        bins_by_mode = raw_bins if isinstance(raw_bins, dict) else {}
        sparse_bins = _sanitize_bins_by_mode(bins_by_mode)
        return {
            "generated_at": payload_dict.get("generated_at"),
            "method": payload_dict.get("method")
            if isinstance(payload_dict.get("method"), dict)
            else {},
            "window": payload_dict.get("window")
            if isinstance(payload_dict.get("window"), dict)
            else {},
            "soc_bins_1pct_by_mode": sparse_bins,
        }

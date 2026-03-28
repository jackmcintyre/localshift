from datetime import timedelta
from unittest.mock import Mock

import pytest
from homeassistant.components.sensor import SensorStateClass
from homeassistant.util import dt as dt_util

from custom_components.localshift.sensors.learning import (
    MODE_RATE_MAX_ROWS_PER_MODE,
    MODE_RATE_MAX_TOTAL_ROWS,
    ChargeRateModeAnalysisSensor,
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
    now = dt_util.now()
    data.charge_rate_mode_analysis = {
        "generated_at": now.isoformat() if now is not None else None,
        "method": {"soc_bin_pct": 1, "resample": "1m"},
        "window": {"history_window_days": 14},
        "soc_bins_1pct_by_mode": {},
    }

    if overrides:
        for key, value in overrides.items():
            setattr(data, key, value)

    coordinator = Mock()
    coordinator.data = data
    return coordinator


def _make_sensor(cls, status: str = "optimizing", overrides: dict | None = None):
    coordinator = _make_coordinator(overrides)
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


class TestChargeRateModeAnalysisSensor:
    def test_update_from_coordinator_ready_when_fresh_payload(self):
        sensor = _make_sensor(ChargeRateModeAnalysisSensor)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == "ready"
        assert sensor._attr_native_value in {"ready", "stale"}

    def test_update_from_coordinator_stale_when_old_payload(self):
        now = dt_util.now()
        assert now is not None
        stale_payload = {
            "generated_at": (now - timedelta(minutes=1441)).isoformat(),
            "soc_bins_1pct_by_mode": {},
        }
        sensor = _make_sensor(
            ChargeRateModeAnalysisSensor,
            overrides={"charge_rate_mode_analysis": stale_payload},
        )

        sensor._update_from_coordinator()

        assert sensor._attr_native_value == "stale"

    def test_update_from_coordinator_stale_when_generated_at_missing_or_invalid(self):
        missing_generated_payload = {
            "generated_at": None,
            "soc_bins_1pct_by_mode": {},
        }
        invalid_generated_payload = {
            "generated_at": "not-a-timestamp",
            "soc_bins_1pct_by_mode": {},
        }

        missing_sensor = _make_sensor(
            ChargeRateModeAnalysisSensor,
            overrides={"charge_rate_mode_analysis": missing_generated_payload},
        )
        invalid_sensor = _make_sensor(
            ChargeRateModeAnalysisSensor,
            overrides={"charge_rate_mode_analysis": invalid_generated_payload},
        )

        missing_sensor._update_from_coordinator()
        invalid_sensor._update_from_coordinator()

        assert missing_sensor._attr_native_value == "stale"
        assert invalid_sensor._attr_native_value == "stale"

    def test_extra_state_attributes_include_required_mode_keys(self):
        payload = {
            "generated_at": "2026-03-28T00:00:00+00:00",
            "method": {"soc_bin_pct": 1, "resample": "1m"},
            "window": {"history_window_days": 14},
            "soc_bins_1pct_by_mode": {
                "self_consumption": [
                    {"soc": 45, "n": 3, "charge_kw": 3.2, "discharge_kw": 0.0}
                ]
            },
        }
        sensor = _make_sensor(
            ChargeRateModeAnalysisSensor,
            overrides={"charge_rate_mode_analysis": payload},
        )

        attrs = sensor.extra_state_attributes

        assert "soc_bins_1pct_by_mode" in attrs
        bins_by_mode = attrs["soc_bins_1pct_by_mode"]
        assert "spike_discharge" in bins_by_mode
        assert isinstance(bins_by_mode["spike_discharge"], list)

    def test_extra_state_attributes_sparse_and_capped(self):
        rows = [
            {"soc": index, "n": 1, "charge_kw": 3.0, "discharge_kw": 0.0}
            for index in range(MODE_RATE_MAX_ROWS_PER_MODE + 5)
        ] + [{"soc": 99, "n": 0, "charge_kw": 4.0, "discharge_kw": 0.0}]
        payload = {
            "generated_at": "2026-03-28T00:00:00+00:00",
            "soc_bins_1pct_by_mode": {
                "grid_charging": rows,
            },
        }
        sensor = _make_sensor(
            ChargeRateModeAnalysisSensor,
            overrides={"charge_rate_mode_analysis": payload},
        )

        attrs = sensor.extra_state_attributes
        mode_rows = attrs["soc_bins_1pct_by_mode"]["grid_charging"]

        assert len(mode_rows) == MODE_RATE_MAX_ROWS_PER_MODE
        assert all(row["n"] > 0 for row in mode_rows)

    def test_extra_state_attributes_drop_non_sparse_invalid_rows(self):
        payload = {
            "generated_at": "2026-03-28T00:00:00+00:00",
            "soc_bins_1pct_by_mode": {
                "grid_charging": [
                    "bad-row",
                    {"soc": "x", "n": 2, "charge_kw": 2.5, "discharge_kw": 0.0},
                    {"soc": 10, "n": 2, "charge_kw": "bad", "discharge_kw": 0.0},
                    {"soc": 11, "n": 2, "charge_kw": 2.5, "discharge_kw": "bad"},
                    {"soc": 12, "n": 2, "charge_kw": 2.5, "discharge_kw": 0.0},
                ]
            },
        }
        sensor = _make_sensor(
            ChargeRateModeAnalysisSensor,
            overrides={"charge_rate_mode_analysis": payload},
        )

        attrs = sensor.extra_state_attributes
        mode_rows = attrs["soc_bins_1pct_by_mode"]["grid_charging"]

        assert mode_rows == [{"soc": 12, "n": 2, "charge_kw": 2.5, "discharge_kw": 0.0}]

    def test_extra_state_attributes_drop_out_of_range_and_non_finite_rows(self):
        payload = {
            "generated_at": "2026-03-28T00:00:00+00:00",
            "soc_bins_1pct_by_mode": {
                "grid_charging": [
                    {"soc": -1, "n": 2, "charge_kw": 2.5, "discharge_kw": 0.0},
                    {"soc": 101, "n": 2, "charge_kw": 2.5, "discharge_kw": 0.0},
                    {"soc": True, "n": 2, "charge_kw": 2.5, "discharge_kw": 0.0},
                    {"soc": 12, "n": True, "charge_kw": 2.5, "discharge_kw": 0.0},
                    {
                        "soc": float("nan"),
                        "n": 2,
                        "charge_kw": 2.5,
                        "discharge_kw": 0.0,
                    },
                    {
                        "soc": float("inf"),
                        "n": 2,
                        "charge_kw": 2.5,
                        "discharge_kw": 0.0,
                    },
                    {
                        "soc": 12.5,
                        "n": 2,
                        "charge_kw": 2.5,
                        "discharge_kw": 0.0,
                    },
                    {
                        "soc": 13,
                        "n": float("nan"),
                        "charge_kw": 2.5,
                        "discharge_kw": 0.0,
                    },
                    {
                        "soc": 14,
                        "n": float("inf"),
                        "charge_kw": 2.5,
                        "discharge_kw": 0.0,
                    },
                    {
                        "soc": 15,
                        "n": 2.3,
                        "charge_kw": 2.5,
                        "discharge_kw": 0.0,
                    },
                    {
                        "soc": 10,
                        "n": 2,
                        "charge_kw": float("nan"),
                        "discharge_kw": 0.0,
                    },
                    {
                        "soc": 11,
                        "n": 2,
                        "charge_kw": 2.5,
                        "discharge_kw": float("inf"),
                    },
                    {"soc": 12, "n": 2, "charge_kw": 2.5, "discharge_kw": 0.0},
                ]
            },
        }
        sensor = _make_sensor(
            ChargeRateModeAnalysisSensor,
            overrides={"charge_rate_mode_analysis": payload},
        )

        attrs = sensor.extra_state_attributes
        mode_rows = attrs["soc_bins_1pct_by_mode"]["grid_charging"]

        assert mode_rows == [{"soc": 12, "n": 2, "charge_kw": 2.5, "discharge_kw": 0.0}]

    def test_extra_state_attributes_apply_total_row_cap(self):
        long_rows = [
            {"soc": index % 100, "n": 1, "charge_kw": 3.0, "discharge_kw": 0.0}
            for index in range(MODE_RATE_MAX_ROWS_PER_MODE + 20)
        ]
        payload = {
            "generated_at": "2026-03-28T00:00:00+00:00",
            "soc_bins_1pct_by_mode": {
                "self_consumption": long_rows,
                "grid_charging": long_rows,
                "boost_charging": long_rows,
            },
        }
        sensor = _make_sensor(
            ChargeRateModeAnalysisSensor,
            overrides={"charge_rate_mode_analysis": payload},
        )

        attrs = sensor.extra_state_attributes
        bins_by_mode = attrs["soc_bins_1pct_by_mode"]
        total_rows = sum(len(rows) for rows in bins_by_mode.values())

        assert total_rows == MODE_RATE_MAX_TOTAL_ROWS

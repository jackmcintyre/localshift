"""Tests for sensors module - covers all sensor classes."""

from unittest.mock import MagicMock

import pytest

from custom_components.localshift.sensors import (
    EffectiveCheapPriceSensor,
    CheapChargeStopPriceSensor,
    SolarWeightedAvgFITSensor,
    SolarBatteryForecastSensor,
    NetElectricityCostSensor,
    DecisionLogSensor,
    ForecastHistorySensor,
    OptimizerPlanSensor,
    ForecastPricesSensor,
    OptimizerPlanGridSensor,
    ForecastDiagnosticsSensor,
    MinimumTargetSOCSensor,
    ExcessSolarSensor,
    LoadShiftSignalSensor,
    ForecastAccuracySensor,
    IntegrationStatusSensor,
    EntityHealthSensor,
    LearningStatusSensor,
    DecisionQualitySensor,
    LearningDecisionHistorySensor,
    DecisionLagSensor,
    ExtendedForecastAccuracySensor,
    ForecastStatusSensor,
    AutomationReadySensor,
    OptimizerPlanDetailedSensor,
    OptimizerSummarySensor,
    SolarForecastAccuracySensor,
)


class Fixtures:
    @pytest.fixture
    def mock_coordinator(self):
        coordinator = MagicMock()
        coordinator.data = self._make_mock_data()
        return coordinator

    @pytest.fixture
    def mock_entry(self):
        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.options = {"minimum_target_soc": 20}
        return entry

    def _make_mock_data(self):
        data = MagicMock()
        data.effective_cheap_price = 0.15
        data.cheap_charge_stop_price = 0.18
        data.solar_weighted_avg_fit = 0.05
        data.solar_remaining_kwh = 5.5
        data.solar_battery_forecast = {"predicted_soc": 75}
        data.grid_import_cost = 2.50
        data.grid_export_revenue = 1.20
        data.battery_savings = 3.00
        data.battery_charge_cost = 0.80
        data.decision_log = [
            {
                "reason": "cheap_price",
                "soc": 50,
                "buy_price": 0.10,
                "sell_price": 0.05,
                "timestamp": "2024-01-01T10:00:00",
            }
        ]
        data.forecast_history = [{"time": "2024-01-01"}, {"time": "2024-01-02"}]
        data.optimizer_decisions = [
            {
                "slot_index": 0,
                "action": "CHARGE",
                "reason_code": "CHEAP",
                "objective_terms": {},
                "timestamp_iso": "2024-01-01T10:00:00",
                "buy_price": 0.10,
                "sell_price": 0.05,
            }
        ]
        data.forecast_horizon_hours = 24
        data.general_forecast = []
        data.feed_in_forecast = []
        data.forecast_import_cost = 5.0
        data.forecast_export_revenue = 2.0
        data.forecast_net_cost = 3.0
        data.forecast_grid_charge_cost = 1.0
        data.forecast_proactive_export_revenue = 0.5
        data.optimizer_summary = {
            "enabled": True,
            "success": True,
            "projected_import_kwh": 10.0,
            "projected_export_kwh": 5.0,
            "projected_net_cost": 2.0,
        }
        data.consumption_source = "history"
        data.consumption_statistic_id = "sensor.consumption"
        data.consumption_profile_hours = 24
        data.consumption_fallback_hours = 0
        data.consumption_hourly_sample_counts = {12: 30, 13: 25}
        data.consumption_hourly_profile_kw = {12: 1.5, 13: 1.8}
        data.forecast_consumption_source_counts = {"history": 100}
        data.consumption_profile_type = "weekday"
        data.forecast_profile_selected = "weekday"
        data.weekday_sample_counts = {12: 20}
        data.weekend_sample_counts = {12: 10}
        data.weekday_hourly_profile_kw = {12: 1.5}
        data.weekend_hourly_profile_kw = {12: 1.2}
        data.recent_load_1hr_kw = 1.5
        data.recent_load_1hr_statistic_id = "sensor.load_1hr"
        data.recent_load_1hr_samples = 60
        data.recent_load_1hr_last_error = None
        data.load_power_kw = 2.0
        data.debug_forecast_slot_found = True
        data.debug_forecast_slot_time = "2024-01-01T14:00:00"
        data.debug_first_forecast_slot_time = "2024-01-01T10:00:00"
        data.debug_time_gap_seconds = 300
        data.debug_mode_source = "automatic"
        data.allow_export = True
        data.weather_entity_id = "weather.home"
        data.weather_temperature_current = 25.0
        data.weather_temperature_forecast = {14: 28, 15: 30}
        data.weather_condition = "sunny"
        data.weather_correlation_confidence = 0.8
        data.weather_adjustment_applied = True
        data.weather_learning_enabled = True
        data.weather_cooling_coefficient = 0.1
        data.weather_heating_coefficient = 0.05
        data.weather_sample_count = 100
        data.load_forecast_slots = [1.0, 1.2, 1.5, 1.3, 1.1, 1.0, 0.9, 0.8, 0.7]
        data.adaptive_params = MagicMock()
        data.adaptive_params.values = {"param1": 1.0, "param2": 2.0}
        data.excess_until_battery_full_kwh = 3.5
        data.excess_solar_current_hour_kwh = 1.0
        data.excess_solar_next_2h_kwh = 2.0
        data.excess_solar_next_4h_kwh = 3.0
        data.excess_until_negative_fit_kwh = 5.0
        data.time_until_battery_full_minutes = 60
        data.negative_fit_window_start = None
        data.negative_fit_window_duration_minutes = 0
        data.can_add_load_now = True
        data.safe_additional_load_kw = 1.5
        data.load_shift_confidence = 0.85
        data.current_excess_rate_kw = 2.0
        data.load_shift_signal = "INCREASE_LOAD"
        data.load_shift_recommended_kw = 1.5
        data.load_shift_recommended_duration_minutes = 60
        data.load_shift_reason = "excess_solar"
        data.grid_charge_risk = "low"
        data.forecast_accuracy_soc_1h = 85.0
        data.forecast_error_soc_15min = 2.0
        data.forecast_error_soc_1h = 5.0
        data.forecast_error_soc_4h = 10.0
        data.forecast_accuracy_soc_15min = 90.0
        data.forecast_accuracy_soc_4h = 80.0
        data.forecast_error_buy_price_1h = 0.01
        data.forecast_error_sell_price_1h = 0.005
        data.forecast_comparisons_made = 50
        data.forecast_last_comparison_time = "2024-01-01T12:00:00"
        data.forecast_first_prediction_time = "2024-01-01T00:00:00"
        data.forecast_history_count = 100
        data.integration_status = "ok"
        data.integration_status_message = "All systems operational"
        data.entity_errors = []
        data.entity_warnings = []
        data.required_entities_healthy = True
        data.last_entity_check = "2024-01-01T12:00:00"
        data.entity_health = {"sensor.soc": {"status": "ok"}}
        data.localshift_entity_health = {
            "sensor.test": {"status": "ok", "category": "required"}
        }
        data.learning_status = "optimizing"
        data.optimization_weights = {"cost": 0.8, "comfort": 0.2}
        data.contextual_adjustments_active = True
        data.performance_metrics = MagicMock()
        data.performance_metrics.total_decisions_today = 10
        data.performance_metrics.avg_decision_score_today = 0.85
        data.performance_metrics.avg_decision_score_7d = 0.80
        data.performance_metrics.cost_trend = "decreasing"
        data.performance_metrics.mode_durations_today = {
            "self_consumption": 300,
            "grid_charging": 60,
        }
        data.performance_metrics.mode_cost_attribution = {
            "self_consumption": 1.5,
            "grid_charging": 0.5,
        }
        data.performance_metrics.grid_charge_efficiency = 0.90
        data.performance_metrics.export_loss_ratio = 0.05
        data.performance_metrics.unnecessary_grid_charge_kwh = 0.1
        data.recent_decision_log = [{"time": "2024-01-01T10:00:00", "score": 0.9}]
        data.decision_lag_seconds = 2.5
        data.decision_lag_history = [
            {"lag_seconds": 2.5, "timestamp": "2024-01-01T10:00:00"}
        ]
        data.decision_timestamp = None
        data.implementation_timestamp = None
        data.extended_accuracy_metrics = MagicMock()
        data.extended_accuracy_metrics.accuracy_24h = 85.0
        data.extended_accuracy_metrics.accuracy_7d = 82.0
        data.extended_accuracy_metrics.accuracy_30d = 80.0
        data.extended_accuracy_metrics.bias = 1.5
        data.extended_accuracy_metrics.mape = 10.0
        data.extended_accuracy_metrics.sample_count = 100
        data.extended_accuracy_metrics.last_updated = None
        data.forecast_status = "ready"
        data.forecast_ready = True
        data.solcast_today = [{"time": "2024-01-01T10:00:00"}] * 10
        data.solcast_tomorrow = [{"time": "2024-01-02T10:00:00"}] * 10
        data.automation_ready = True
        data.automation_ready_status = {"check1": True, "check2": True}
        data.automation_ready_missing = []
        data.soc = 75.0
        data.operation_mode = "Self Consumption"
        data.backup_reserve = 20
        data.prices_available = True
        data.solar_forecast_accuracy = 85.0
        data.solar_bias_metrics = {"bias": 0.5}
        return data


class TestPricingSensors(Fixtures):
    def test_effective_cheap_price(self, mock_coordinator, mock_entry):
        sensor = EffectiveCheapPriceSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 0.15

    def test_cheap_charge_stop_price(self, mock_coordinator, mock_entry):
        sensor = CheapChargeStopPriceSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 0.18

    def test_solar_weighted_avg_fit(self, mock_coordinator, mock_entry):
        sensor = SolarWeightedAvgFITSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 0.05
        attrs = sensor.extra_state_attributes
        assert attrs["total_solar_remaining_kwh"] == 5.5


class TestForecastSensors(Fixtures):
    def test_solar_battery_forecast(self, mock_coordinator, mock_entry):
        sensor = SolarBatteryForecastSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 75

    def test_net_electricity_cost(self, mock_coordinator, mock_entry):
        sensor = NetElectricityCostSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 1.30
        attrs = sensor.extra_state_attributes
        assert attrs["grid_import_cost"] == 2.50

    def test_decision_log(self, mock_coordinator, mock_entry):
        sensor = DecisionLogSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == "cheap_price"
        attrs = sensor.extra_state_attributes
        assert "history" in attrs

    def test_forecast_history(self, mock_coordinator, mock_entry):
        sensor = ForecastHistorySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 2

    def test_optimizer_plan(self, mock_coordinator, mock_entry):
        sensor = OptimizerPlanSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 1
        attrs = sensor.extra_state_attributes
        assert attrs["total_slots"] == 1

    def test_forecast_prices(self, mock_coordinator, mock_entry):
        sensor = ForecastPricesSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 0.15

    def test_optimizer_plan_grid(self, mock_coordinator, mock_entry):
        sensor = OptimizerPlanGridSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 2.0

    def test_forecast_diagnostics(self, mock_coordinator, mock_entry):
        sensor = ForecastDiagnosticsSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == "history"

    def test_minimum_target_soc(self, mock_coordinator, mock_entry):
        sensor = MinimumTargetSOCSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 20


class TestMiscSensors(Fixtures):
    def test_excess_solar(self, mock_coordinator, mock_entry):
        sensor = ExcessSolarSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 3.5

    def test_load_shift_signal(self, mock_coordinator, mock_entry):
        sensor = LoadShiftSignalSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == "INCREASE_LOAD"
        assert sensor.icon == "mdi:arrow-up-bold"


class TestStatusSensors(Fixtures):
    def test_forecast_accuracy(self, mock_coordinator, mock_entry):
        sensor = ForecastAccuracySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 85.0
        attrs = sensor.extra_state_attributes
        assert attrs["soc_error_1h"] == 5.0

    def test_integration_status(self, mock_coordinator, mock_entry):
        sensor = IntegrationStatusSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == "ok"
        assert sensor.icon == "mdi:check-circle"

    def test_entity_health(self, mock_coordinator, mock_entry):
        sensor = EntityHealthSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == "2/2"
        attrs = sensor.extra_state_attributes
        assert "entities" in attrs

    def test_forecast_status(self, mock_coordinator, mock_entry):
        sensor = ForecastStatusSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == "ready"
        assert sensor.icon == "mdi:check-circle"

    def test_automation_ready(self, mock_coordinator, mock_entry):
        sensor = AutomationReadySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == "ready"
        assert sensor.icon == "mdi:check-decagram"

    def test_decision_lag(self, mock_coordinator, mock_entry):
        sensor = DecisionLagSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 2.5
        attrs = sensor.extra_state_attributes
        assert attrs["avg_lag_24h"] == 2.5

    def test_extended_forecast_accuracy(self, mock_coordinator, mock_entry):
        sensor = ExtendedForecastAccuracySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 85.0
        attrs = sensor.extra_state_attributes
        assert attrs["accuracy_7d"] == 82.0


class TestLearningSensors(Fixtures):
    def test_learning_status(self, mock_coordinator, mock_entry):
        sensor = LearningStatusSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == "optimizing"
        assert sensor.icon == "mdi:brain"

    def test_decision_quality(self, mock_coordinator, mock_entry):
        sensor = DecisionQualitySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 85.0

    def test_learning_decision_history(self, mock_coordinator, mock_entry):
        sensor = LearningDecisionHistorySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 1


class TestOptimizerSensors(Fixtures):
    def test_optimizer_plan_detailed(self, mock_coordinator, mock_entry):
        sensor = OptimizerPlanDetailedSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == "computed"

    def test_optimizer_summary(self, mock_coordinator, mock_entry):
        sensor = OptimizerSummarySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == "success"
        assert sensor.icon == "mdi:check-circle-outline"

    def test_solar_forecast_accuracy(self, mock_coordinator, mock_entry):
        sensor = SolarForecastAccuracySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()
        assert sensor.native_value == 85.0
        attrs = sensor.extra_state_attributes
        assert attrs == {"bias": 0.5}

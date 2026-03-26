"""Tests for forecast.py sensors.

Tests cover:
- SolarBatteryForecastSensor: native_value, extra_state_attributes
- NetElectricityCostSensor: native_value, extra_state_attributes
- DecisionLogSensor: native_value, extra_state_attributes, empty log handling
- ForecastHistorySensor: native_value, extra_state_attributes
- OptimizerPlanSensor: native_value, extra_state_attributes
- ForecastPricesSensor: native_value, extra_state_attributes, with/without decisions
- OptimizerPlanGridSensor: native_value, extra_state_attributes
- ForecastDiagnosticsSensor: native_value, extra_state_attributes
- MinimumTargetSOCSensor: native_value
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from custom_components.localshift.coordinator.data import (
    AdaptiveParameters,
    CoordinatorData,
)
from custom_components.localshift.forecast.solcast_analysis import (
    ConfidenceInterval,
    SolcastAnalysis,
)
from custom_components.localshift.sensors.forecast import (
    DecisionLogSensor,
    ForecastDiagnosticsSensor,
    ForecastHistorySensor,
    ForecastPricesSensor,
    MinimumTargetSOCSensor,
    NetElectricityCostSensor,
    OptimizerPlanGridSensor,
    OptimizerPlanSensor,
    SolarBatteryForecastSensor,
)


def create_mock_coordinator_with_data(**kwargs) -> tuple[MagicMock, CoordinatorData]:
    """Create a mock coordinator with CoordinatorData for testing."""
    data = CoordinatorData()
    for key, value in kwargs.items():
        setattr(data, key, value)
    mock_coordinator = MagicMock()
    mock_coordinator.data = data
    return mock_coordinator, data


class TestSolarBatteryForecastSensor:
    """Tests for SolarBatteryForecastSensor."""

    def test_native_value_with_forecast(self):
        """Test native_value extracts predicted_soc from forecast."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            solar_battery_forecast={"predicted_soc": 75.5, "other_key": "value"}
        )
        mock_entry = MagicMock()

        sensor = SolarBatteryForecastSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 75.5

    def test_native_value_missing_predicted_soc(self):
        """Test native_value defaults to 0 when predicted_soc missing."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            solar_battery_forecast={"other_key": "value"}
        )
        mock_entry = MagicMock()

        sensor = SolarBatteryForecastSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 0

    def test_native_value_empty_forecast(self):
        """Test native_value defaults to 0 when forecast empty."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            solar_battery_forecast={}
        )
        mock_entry = MagicMock()

        sensor = SolarBatteryForecastSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 0

    def test_extra_state_attributes(self):
        """Test extra_state_attributes returns full forecast dict."""
        forecast = {"predicted_soc": 75.5, "hours_to_target": 4, "solar_kwh": 12.5}
        mock_coordinator, data = create_mock_coordinator_with_data(
            solar_battery_forecast=forecast
        )
        mock_entry = MagicMock()

        sensor = SolarBatteryForecastSensor(mock_coordinator, mock_entry)

        attrs = sensor.extra_state_attributes
        assert attrs["predicted_soc"] == 75.5
        assert attrs["hours_to_target"] == 4
        assert attrs["solar_kwh"] == 12.5
        assert attrs["solar_confidence_used"] == 1.0
        assert attrs["solar_blend_applied"] is False

    def test_extra_state_attributes_adds_confidence_diagnostics(self):
        forecast = {"predicted_soc": 75.5}
        analysis = SolcastAnalysis(
            entity_id="sensor.today",
            last_updated=datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc),
            day_confidence=0.2,
            day_spread_kwh=0.0,
            estimate10_kwh=0.0,
            estimate90_kwh=0.0,
            intervals=[
                ConfidenceInterval(
                    period_start=datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc),
                    spread_kwh=0.0,
                    confidence=0.2,
                )
            ],
        )
        mock_coordinator, _ = create_mock_coordinator_with_data(
            solar_battery_forecast=forecast,
            solcast_analysis_today=analysis,
        )
        sensor = SolarBatteryForecastSensor(mock_coordinator, MagicMock())

        with patch(
            "custom_components.localshift.sensors.forecast.dt_util.now",
            return_value=datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc),
        ):
            attrs = sensor.extra_state_attributes

        assert attrs["solar_confidence_used"] == 0.2
        assert attrs["solar_blend_applied"] is True


class TestNetElectricityCostSensor:
    """Tests for NetElectricityCostSensor."""

    def test_native_value_positive(self):
        """Test native_value when import exceeds export."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            grid_import_cost=10.50, grid_export_revenue=3.25
        )
        mock_entry = MagicMock()

        sensor = NetElectricityCostSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 7.25

    def test_native_value_negative(self):
        """Test native_value when export exceeds import."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            grid_import_cost=2.00, grid_export_revenue=5.75
        )
        mock_entry = MagicMock()

        sensor = NetElectricityCostSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == -3.75

    def test_extra_state_attributes(self):
        """Test extra_state_attributes contains all cost fields."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            grid_import_cost=10.0,
            grid_export_revenue=5.0,
            battery_savings=3.0,
            battery_charge_cost=2.0,
        )
        mock_entry = MagicMock()

        sensor = NetElectricityCostSensor(mock_coordinator, mock_entry)

        attrs = sensor.extra_state_attributes
        assert attrs["grid_import_cost"] == 10.0
        assert attrs["grid_export_revenue"] == 5.0
        assert attrs["battery_savings"] == 3.0
        assert attrs["battery_charge_cost"] == 2.0

    def test_extra_state_attributes_none_values(self):
        """Test extra_state_attributes handles None values."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            grid_import_cost=10.0,
            grid_export_revenue=5.0,
            battery_savings=None,
            battery_charge_cost=None,
        )
        mock_entry = MagicMock()

        sensor = NetElectricityCostSensor(mock_coordinator, mock_entry)

        attrs = sensor.extra_state_attributes
        assert attrs["battery_savings"] == 0.0
        assert attrs["battery_charge_cost"] == 0.0


class TestDecisionLogSensor:
    """Tests for DecisionLogSensor."""

    def test_native_value_with_decisions(self):
        """Test native_value returns latest reason."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            decision_log=[
                {"reason": "First decision"},
                {"reason": "Second decision"},
                {"reason": "Latest decision"},
            ]
        )
        mock_entry = MagicMock()

        sensor = DecisionLogSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == "Latest decision"

    def test_native_value_empty_log(self):
        """Test native_value when log is empty."""
        mock_coordinator, data = create_mock_coordinator_with_data(decision_log=[])
        mock_entry = MagicMock()

        sensor = DecisionLogSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == "No decisions yet"

    def test_native_value_missing_reason(self):
        """Test native_value when reason key is missing."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            decision_log=[{"other_key": "value"}]
        )
        mock_entry = MagicMock()

        sensor = DecisionLogSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == ""

    def test_extra_state_attributes_with_decisions(self):
        """Test extra_state_attributes includes latest decision details."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            decision_log=[
                {"reason": "Old", "soc": 40},
                {
                    "reason": "Latest",
                    "soc": 50,
                    "buy_price": 0.25,
                    "sell_price": 0.08,
                    "timestamp": "2026-03-13T10:00:00",
                },
            ]
        )
        mock_entry = MagicMock()

        sensor = DecisionLogSensor(mock_coordinator, mock_entry)
        attrs = sensor.extra_state_attributes

        assert attrs["reason"] == "Latest"
        assert attrs["soc"] == 50
        assert attrs["buy_price"] == 0.25
        assert attrs["sell_price"] == 0.08
        assert attrs["timestamp"] == "2026-03-13T10:00:00"
        assert len(attrs["history"]) == 2

    def test_extra_state_attributes_truncates_history(self):
        """Test extra_state_attributes truncates history to 10 items."""
        decision_log = [{"reason": f"Decision {i}"} for i in range(15)]
        mock_coordinator, data = create_mock_coordinator_with_data(
            decision_log=decision_log
        )
        mock_entry = MagicMock()

        sensor = DecisionLogSensor(mock_coordinator, mock_entry)
        attrs = sensor.extra_state_attributes

        assert len(attrs["history"]) == 10


class TestForecastHistorySensor:
    """Tests for ForecastHistorySensor."""

    def test_native_value(self):
        """Test native_value returns count of history items."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            forecast_history=[{"a": 1}, {"b": 2}, {"c": 3}]
        )
        mock_entry = MagicMock()

        sensor = ForecastHistorySensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 3

    def test_extra_state_attributes(self):
        """Test extra_state_attributes returns history list."""
        history = [{"slot": 1}, {"slot": 2}]
        mock_coordinator, data = create_mock_coordinator_with_data(
            forecast_history=history
        )
        mock_entry = MagicMock()

        sensor = ForecastHistorySensor(mock_coordinator, mock_entry)

        assert sensor.extra_state_attributes == {"history": history}


class TestOptimizerPlanSensor:
    """Tests for OptimizerPlanSensor."""

    def test_native_value_with_decisions(self):
        """Test native_value returns count of decisions."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_decisions=[{"action": "CHARGE"}, {"action": "DISCHARGE"}]
        )
        mock_entry = MagicMock()

        sensor = OptimizerPlanSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 2

    def test_native_value_none_decisions(self):
        """Test native_value handles None decisions."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_decisions=None
        )
        mock_entry = MagicMock()

        sensor = OptimizerPlanSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 0

    def test_extra_state_attributes(self):
        """Test extra_state_attributes contains slots and metadata."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_decisions=[
                {
                    "slot_index": 0,
                    "action": "CHARGE",
                    "reason_code": "CHEAP",
                    "objective_terms": {"cost": -0.5},
                }
            ],
            forecast_horizon_hours=24,
        )
        mock_entry = MagicMock()

        sensor = OptimizerPlanSensor(mock_coordinator, mock_entry)
        attrs = sensor.extra_state_attributes

        assert len(attrs["slots"]) == 1
        assert attrs["slots"][0]["slot_idx"] == 0
        assert attrs["slots"][0]["action"] == "CHARGE"
        assert attrs["total_slots"] == 1
        assert attrs["forecast_horizon_hours"] == 24
        assert attrs["planner"] == "DP_OPTIMIZER"

    def test_unrecorded_attributes_includes_slots(self):
        """Test that 'slots' is excluded from recorder to avoid 16KB limit.

        Issue #467: The slots array can exceed 16KB with many slots.
        """
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_decisions=[{"action": "CHARGE"}]
        )
        mock_entry = MagicMock()

        sensor = OptimizerPlanSensor(mock_coordinator, mock_entry)

        assert hasattr(sensor, "_unrecorded_attributes")
        assert "slots" in sensor._unrecorded_attributes


class TestForecastPricesSensor:
    """Tests for ForecastPricesSensor."""

    def test_native_value(self):
        """Test native_value returns rounded effective_cheap_price."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            effective_cheap_price=0.12345
        )
        mock_entry = MagicMock()

        sensor = ForecastPricesSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 0.1235

    def test_extra_state_attributes_with_decisions(self):
        """Test extra_state_attributes extracts prices from decisions."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            effective_cheap_price=0.10,
            cheap_charge_stop_price=0.12,
            optimizer_decisions=[
                {
                    "timestamp_iso": "2026-03-13T10:00:00",
                    "buy_price": 0.25,
                    "sell_price": 0.08,
                },
                {
                    "timestamp_iso": "2026-03-13T10:30:00",
                    "buy_price": 0.30,
                    "sell_price": 0.05,
                },
            ],
            forecast_import_cost=5.0,
            forecast_export_revenue=2.0,
            forecast_net_cost=3.0,
            forecast_grid_charge_cost=1.0,
            forecast_proactive_export_revenue=0.5,
        )
        mock_entry = MagicMock()

        sensor = ForecastPricesSensor(mock_coordinator, mock_entry)
        attrs = sensor.extra_state_attributes

        assert len(attrs["buy_prices"]) == 2
        assert attrs["buy_prices"][0]["time"] == "10:00"
        assert attrs["buy_prices"][0]["price"] == 0.25
        assert attrs["sell_prices"][0]["price"] == 0.08

    def test_extra_state_attributes_without_decisions(self):
        """Test extra_state_attributes falls back to forecast data."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            effective_cheap_price=0.10,
            cheap_charge_stop_price=0.12,
            optimizer_decisions=[],
            general_forecast=[
                {"start_time": "2026-03-13T10:00:00", "per_kwh": 0.25},
                {"start_time": "2026-03-13T10:30:00", "per_kwh": 0.30},
            ],
            feed_in_forecast=[
                {"start_time": "2026-03-13T10:00:00", "per_kwh": 0.08},
            ],
        )
        mock_entry = MagicMock()

        sensor = ForecastPricesSensor(mock_coordinator, mock_entry)
        attrs = sensor.extra_state_attributes

        assert len(attrs["buy_prices"]) == 2
        assert attrs["buy_prices"][0]["time"] == "10:00"
        assert attrs["buy_prices"][0]["price"] == 0.25
        assert len(attrs["sell_prices"]) == 1


class TestOptimizerPlanGridSensor:
    """Tests for OptimizerPlanGridSensor."""

    def test_native_value(self):
        """Test native_value returns projected_net_cost."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary={"projected_net_cost": 5.123}
        )
        mock_entry = MagicMock()

        sensor = OptimizerPlanGridSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 5.123

    def test_native_value_no_summary(self):
        """Test native_value when no summary."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_summary=None
        )
        mock_entry = MagicMock()

        sensor = OptimizerPlanGridSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 0.0

    def test_extra_state_attributes(self):
        """Test extra_state_attributes contains projection data."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            optimizer_decisions=[
                {"action": "CHARGE"},
                {"action": "CHARGE"},
                {"action": "DISCHARGE"},
            ],
            optimizer_summary={
                "projected_import_kwh": 10.5,
                "projected_export_kwh": 5.25,
                "projected_net_cost": 3.75,
            },
        )
        mock_entry = MagicMock()

        sensor = OptimizerPlanGridSensor(mock_coordinator, mock_entry)
        attrs = sensor.extra_state_attributes

        assert attrs["projected_import_kwh"] == 10.5
        assert attrs["projected_export_kwh"] == 5.25
        assert attrs["projected_net_cost"] == 3.75
        assert attrs["action_breakdown"] == {"CHARGE": 2, "DISCHARGE": 1}
        assert attrs["planner"] == "DP_OPTIMIZER"


class TestForecastDiagnosticsSensor:
    """Tests for ForecastDiagnosticsSensor."""

    def test_native_value(self):
        """Test native_value returns consumption_source."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            consumption_source="recorder_statistics"
        )
        mock_entry = MagicMock()

        sensor = ForecastDiagnosticsSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == "recorder_statistics"

    def test_extra_state_attributes(self):
        """Test extra_state_attributes contains all diagnostic fields."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            consumption_source="recorder",
            consumption_statistic_id="sensor.energy",
            consumption_profile_hours=168,
            consumption_fallback_hours=24,
            forecast_consumption_source_counts={"recorder": 10, "fallback": 2},
            consumption_hourly_sample_counts={0: 5, 1: 6},
            consumption_hourly_profile_kw={0: 0.5, 1: 0.6},
            consumption_profile_type="weekday_weekend",
            forecast_profile_selected="weekday",
            weekday_sample_counts={0: 3, 1: 4},
            weekend_sample_counts={0: 2, 1: 2},
            weekday_hourly_profile_kw={0: 0.6, 1: 0.7},
            weekend_hourly_profile_kw={0: 0.4, 1: 0.5},
            recent_load_1hr_kw=0.55,
            recent_load_1hr_statistic_id="sensor.load",
            recent_load_1hr_samples=10,
            recent_load_1hr_last_error="",
            load_power_kw=0.5,
            debug_forecast_slot_found=True,
            debug_forecast_slot_time="10:00",
            debug_first_forecast_slot_time="09:00",
            debug_time_gap_seconds=120.5,
            debug_mode_source="forecast",
            allow_export="yes",
            weather_entity_id="weather.home",
            weather_temperature_current=25.0,
            weather_temperature_forecast={0: 25.0, 1: 26.0},
            weather_condition="sunny",
            weather_correlation_confidence="high",
            weather_adjustment_applied=True,
            weather_learning_enabled=True,
            weather_avg_cooling_slope=0.05,
            weather_avg_heating_slope=0.03,
            weather_avg_r_squared=0.42,
            weather_sample_count=100,
            load_forecast_slots=[0.5] * 96,
            adaptive_params=AdaptiveParameters(values={"cheap_price_percentile": 0.25}),
        )
        mock_entry = MagicMock()

        sensor = ForecastDiagnosticsSensor(mock_coordinator, mock_entry)
        attrs = sensor.extra_state_attributes

        assert attrs["consumption_source"] == "recorder"
        assert attrs["consumption_statistic_id"] == "sensor.energy"
        assert attrs["consumption_profile_hours"] == 168
        assert attrs["allow_export"] == "yes"
        assert attrs["weather_avg_cooling_slope"] == 0.05
        assert attrs["weather_avg_heating_slope"] == 0.03
        assert attrs["weather_avg_r_squared"] == 0.42

    def test_extra_state_attributes_none_adaptive_params(self):
        """Test extra_state_attributes handles None adaptive_params."""
        mock_coordinator, data = create_mock_coordinator_with_data(
            consumption_source="unknown",
            consumption_hourly_sample_counts={},
            consumption_hourly_profile_kw={},
            forecast_consumption_source_counts={},
            weekday_sample_counts={},
            weekend_sample_counts={},
            weekday_hourly_profile_kw={},
            weekend_hourly_profile_kw={},
            weather_temperature_forecast={},
            load_forecast_slots=[],
            adaptive_params=None,
        )
        mock_entry = MagicMock()

        sensor = ForecastDiagnosticsSensor(mock_coordinator, mock_entry)
        attrs = sensor.extra_state_attributes

        assert attrs["adaptive_params_values"] == {}


class TestMinimumTargetSOCSensor:
    """Tests for MinimumTargetSOCSensor."""

    def test_native_value_from_options(self):
        """Test native_value reads from entry options."""
        mock_coordinator, data = create_mock_coordinator_with_data()
        mock_entry = MagicMock()
        mock_entry.options = {"minimum_target_soc": 25}

        sensor = MinimumTargetSOCSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 25.0

    def test_native_value_default(self):
        """Test native_value uses default when not in options."""
        mock_coordinator, data = create_mock_coordinator_with_data()
        mock_entry = MagicMock()
        mock_entry.options = {}

        sensor = MinimumTargetSOCSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        # Default is defined in const.py, typically 20
        assert isinstance(sensor._attr_native_value, float)

"""Unit tests for ComputationEngine."""

from datetime import datetime, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.computation_engine import BatteryMode
from custom_components.localshift.const import CONF_WEATHER_LEARNING_ENABLED


@pytest.mark.parametrize(
    "operation_mode, backup_reserve, expected",
    [
        ("autonomous", 10, True),  # Low reserve, autonomous mode
        ("autonomous", 50, False),  # Normal reserve, autonomous mode
        ("backup", 50, False),  # Backup mode - force_discharge only for autonomous
        (
            "backup",
            99,
            False,
        ),  # High reserve, backup mode - force_discharge only for autonomous
        ("autonomous", 99, False),  # High reserve, autonomous mode
    ],
)
def test_force_discharge_active(
    computation_engine, coordinator_data, operation_mode, backup_reserve, expected
):
    """Test force_discharge_active detection."""
    coordinator_data.operation_mode = operation_mode
    coordinator_data.backup_reserve = backup_reserve

    computation_engine.compute_derived_values(coordinator_data)

    assert coordinator_data.force_discharge_active == expected


@pytest.mark.parametrize(
    "operation_mode, backup_reserve, expected",
    [
        ("backup", 50, True),  # Backup mode
        ("autonomous", 100, True),  # High reserve (100+), autonomous mode
        ("autonomous", 50, False),  # Normal reserve, autonomous mode
        ("autonomous", 10, False),  # Low reserve, autonomous mode
    ],
)
def test_force_charge_active(
    computation_engine, coordinator_data, operation_mode, backup_reserve, expected
):
    """Test force_charge_active detection."""
    coordinator_data.operation_mode = operation_mode
    coordinator_data.backup_reserve = backup_reserve

    computation_engine.compute_derived_values(coordinator_data)

    assert coordinator_data.force_charge_active == expected


@pytest.mark.parametrize(
    "operation_mode, backup_reserve, expected",
    [
        ("autonomous", 100, True),  # High reserve (100+), autonomous mode
        ("backup", 50, False),  # Backup mode
        ("autonomous", 50, False),  # Normal reserve, autonomous mode
    ],
)
def test_boost_charge_active(
    computation_engine, coordinator_data, operation_mode, backup_reserve, expected
):
    """Test boost_charge_active detection."""
    coordinator_data.operation_mode = operation_mode
    coordinator_data.backup_reserve = backup_reserve

    computation_engine.compute_derived_values(coordinator_data)

    assert coordinator_data.boost_charge_active == expected


@pytest.mark.parametrize(
    "now_time, dw_start, dw_end, expected",
    [
        (time(17, 0), time(18, 0), time(22, 0), False),  # Before DW
        (time(18, 0), time(18, 0), time(22, 0), True),  # At DW start
        (time(20, 0), time(18, 0), time(22, 0), True),  # During DW
        (time(22, 0), time(18, 0), time(22, 0), False),  # At DW end
        (time(23, 0), time(18, 0), time(22, 0), False),  # After DW
    ],
)
def test_demand_window_active(
    computation_engine, coordinator_data, now_time, dw_start, dw_end, expected
):
    """Test demand_window_active detection."""
    # Mock current time
    with patch(
        "homeassistant.util.dt.now",
        return_value=datetime.combine(datetime.today(), now_time),
    ):
        # Mock config options
        computation_engine.entry.options["demand_window_start"] = dw_start.strftime(
            "%H:%M:%S"
        )
        computation_engine.entry.options["demand_window_end"] = dw_end.strftime(
            "%H:%M:%S"
        )

        # Mock switch state
        computation_engine._get_switch_state = MagicMock(return_value=True)

        computation_engine.compute_derived_values(coordinator_data)

        assert coordinator_data.demand_window_active == expected


def test_effective_cheap_price_no_solar_gap(computation_engine, coordinator_data):
    """Test effective_cheap_price when solar can reach target."""
    coordinator_data.soc = 95.0
    coordinator_data.solar_can_reach_target = True
    coordinator_data.general_price = 0.25
    coordinator_data.feed_in_price = 0.08
    # target_reached_today must be True to use base price
    coordinator_data.target_reached_today = True

    computation_engine.compute_derived_values(coordinator_data)

    # When solar_can_reach_target is True AND target_reached_today is True,
    # should use base percentile price (falls back to max_precharge_price = 0.30
    # when forecast data not in lookahead window)
    # The effective_cheap_price is just the base (0.30), cheap_charge_stop_price adds deadband
    assert coordinator_data.effective_cheap_price == pytest.approx(0.30, rel=0.01)


def test_effective_cheap_price_with_solar_gap(computation_engine, coordinator_data):
    """Test effective_cheap_price when solar cannot reach target."""
    coordinator_data.soc = 50.0
    coordinator_data.solar_can_reach_target = False
    coordinator_data.general_price = 0.25
    coordinator_data.feed_in_price = 0.08

    computation_engine.compute_derived_values(coordinator_data)

    # Should use urgency-based calculation
    assert coordinator_data.effective_cheap_price > 0.15


def test_active_mode_automation_disabled(computation_engine, coordinator_data):
    """Test active_mode when automation is disabled (Phase 4, #441)."""
    computation_engine._get_switch_state = MagicMock(return_value=False)

    # With automation disabled, active_mode should be SELF_CONSUMPTION
    computation_engine.compute_derived_values(coordinator_data)

    assert coordinator_data.active_mode == BatteryMode.SELF_CONSUMPTION


@pytest.mark.parametrize(
    "grid_charge_boost, grid_charge, proactive_export, price_spike, "
    "demand_window_active, manual_override, expected_mode",
    [
        # Forecast-driven modes: set grid_import_kwh=0 to prevent activation
        # (tests the logic when forecast flags are set but conditions not met)
        (True, False, False, False, False, False, BatteryMode.SELF_CONSUMPTION),
        (False, True, False, False, False, False, BatteryMode.SELF_CONSUMPTION),
        (False, False, True, False, False, False, BatteryMode.SELF_CONSUMPTION),
        # spike_discharge and demand_block need specific switch states not available in test
        (False, False, False, True, False, False, BatteryMode.SELF_CONSUMPTION),
        (False, False, False, False, True, False, BatteryMode.SELF_CONSUMPTION),
        # Manual override works correctly
        (False, False, False, False, False, True, BatteryMode.MANUAL),
        # Default case
        (False, False, False, False, False, False, BatteryMode.SELF_CONSUMPTION),
    ],
)
def test_active_mode_forecast_driven(
    computation_engine,
    coordinator_data,
    grid_charge_boost,
    grid_charge,
    proactive_export,
    price_spike,
    demand_window_active,
    manual_override,
    expected_mode,
):
    """Test active_mode forecast-driven logic."""
    # Mock time to 16:00 (16:0) so we can match the forecast entry
    test_time = datetime(2026, 2, 16, 16, 0, 0)
    with patch(
        "homeassistant.util.dt.now",
        return_value=test_time,
    ):
        # Mock forecast entry with hour/minute fields for matching
        # Set grid_import_kwh=0 to prevent forecast-driven modes from activating
        # (this tests the logic when forecast flags are set but conditions not met)
        test_time_iso = test_time.isoformat()
        coordinator_data.daily_forecast = [
            {
                "timestamp": test_time_iso,
                "hour": 16,
                "minute": 0,
                "grid_charge_boost": grid_charge_boost,
                "grid_charge": grid_charge,
                "proactive_export": proactive_export,
                "grid_import_kwh": 0.0,  # Prevent activation of forecast-driven modes
                "export_amount_kwh": 0.0,  # Prevent proactive export activation
                "predicted_soc": 95.0,  # SOC above target to prevent proactive export
                "buy_price": 0.30,  # High buy price to prevent grid charging
                "sell_price": 0.05,  # Low sell price to prevent proactive export
            }
        ]

        # Mock conditions
        coordinator_data.price_spike = price_spike
        coordinator_data.manual_override = manual_override

        # Mock switch state - for demand_block test we need demand_window_block = True
        # Also need to mock spike_discharge_enabled for price_spike test case
        def mock_switch_state(key):
            if key == "demand_window_block" and demand_window_active:
                return True
            if key == "automation_enabled":
                return True
            # spike_discharge_enabled must be False for the price_spike test case
            # to ensure it stays in SELF_CONSUMPTION, not SPIKE_DISCHARGE
            if key == "spike_discharge_enabled":
                return False
            return False

        computation_engine._get_switch_state = MagicMock(side_effect=mock_switch_state)

        # Run computation (no forecast computer in Phase 4)
        computation_engine.compute_derived_values(coordinator_data)

        assert coordinator_data.active_mode == expected_mode


def test_decision_log_mode_change(computation_engine, coordinator_data):
    """Test decision log when mode changes."""
    # Set up initial state with automation enabled
    coordinator_data.soc = 50.0
    coordinator_data.general_price = 0.25
    coordinator_data.feed_in_price = 0.08

    # First run - initial state (should log initial status)
    computation_engine.compute_derived_values(coordinator_data)
    initial_log_length = len(coordinator_data.decision_log)

    # Second run - should log a status update (no mode change expected in this test setup)
    # The decision log should have at least one entry from the first run
    assert initial_log_length >= 1
    # Check that there's a valid entry
    assert "reason" in coordinator_data.decision_log[-1]


def test_decision_log_periodic_update(computation_engine, coordinator_data):
    """Test decision log periodic updates."""
    # Set up initial state
    coordinator_data.soc = 50.0
    coordinator_data.general_price = 0.25
    coordinator_data.feed_in_price = 0.08

    # First run - initial state
    computation_engine.compute_derived_values(coordinator_data)
    initial_log_length = len(coordinator_data.decision_log)

    # Should have logged the initial state
    assert initial_log_length >= 1
    # First entry should be initial status
    assert "reason" in coordinator_data.decision_log[-1]


# =============================================================================
# COMPUTE DAILY 15-MIN FORECAST TESTS
# =============================================================================


class TestLoadForecastSlots:
    """Tests for load_forecast_slots (Issue #441 Phase 1)."""

    def test_load_forecast_slots_populated_before_forecast(
        self, computation_engine, coordinator_data
    ):
        """Test that load_forecast_slots has 96 entries after compute_derived_values()."""
        from custom_components.localshift.engine.slot_schedule import (
            TOTAL_SLOTS,
        )

        # Ensure we have valid data
        coordinator_data.load_power_kw = 0.5

        with patch.object(
            computation_engine,
            "_get_historical_hourly_averages",
            return_value={10: 0.5, 11: 0.6},
        ):
            computation_engine.compute_derived_values(coordinator_data)

        # Verify load_forecast_slots is populated
        assert hasattr(coordinator_data, "load_forecast_slots")
        assert len(coordinator_data.load_forecast_slots) == TOTAL_SLOTS
        assert all(
            isinstance(v, float) and v >= 0
            for v in coordinator_data.load_forecast_slots
        )


# =============================================================================
# ACCURACY METRICS PERSISTENCE TESTS (Issue #706)
# =============================================================================


class TestAccuracyMetricsPersistence:
    """Tests for AccuracyMetricsStore delegation methods (Issue #706)."""

    @pytest.mark.asyncio
    async def test_async_initialize_accuracy_metrics_storage(self, computation_engine):
        """Test that initialize delegates to the accuracy metrics store."""
        store_mock = AsyncMock()
        computation_engine._accuracy_metrics_store = store_mock

        await computation_engine.async_initialize_accuracy_metrics_storage()

        store_mock.async_initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_load_accuracy_metrics(
        self, computation_engine, coordinator_data
    ):
        """Test that load delegates to the accuracy metrics store with data."""
        store_mock = AsyncMock()
        computation_engine._accuracy_metrics_store = store_mock

        await computation_engine.async_load_accuracy_metrics(coordinator_data)

        store_mock.async_load.assert_awaited_once_with(coordinator_data)

    @pytest.mark.asyncio
    async def test_async_save_accuracy_metrics(
        self, computation_engine, coordinator_data
    ):
        """Test that save delegates to the accuracy metrics store with data."""
        store_mock = AsyncMock()
        computation_engine._accuracy_metrics_store = store_mock

        await computation_engine.async_save_accuracy_metrics(coordinator_data)

        store_mock.async_save.assert_awaited_once_with(coordinator_data)


class TestComputationEngineDelegations:
    def test_forecast_profile_selected_weekend(
        self, computation_engine, coordinator_data
    ):
        """Weekday/weekend profile should select weekend on Saturday."""
        computation_engine._history_fetcher.get_weekday_profile = MagicMock(
            return_value=({}, {})
        )
        computation_engine._history_fetcher.get_weekend_profile = MagicMock(
            return_value=({}, {})
        )
        computation_engine._history_fetcher.get_profile_source = MagicMock(
            return_value="weekday_weekend"
        )
        computation_engine._history_fetcher._historical_load_cache = {}
        computation_engine._history_fetcher._historical_load_sample_counts = {}
        computation_engine._history_fetcher._historical_load_source = "weekday_weekend"
        computation_engine._history_fetcher._recent_load_1hr_kw = 0.0
        computation_engine._history_fetcher._recent_load_1hr_statistic_id = "stat"
        computation_engine._history_fetcher._recent_load_1hr_samples = 0
        computation_engine._history_fetcher._recent_load_1hr_last_error = ""
        computation_engine._forecast_pipeline.compute_load_forecast_slots = MagicMock()
        computation_engine._forecast_pipeline.compute_solar_battery_forecast = (
            MagicMock()
        )
        computation_engine._forecast_pipeline.compute_solar_weighted_avg_fit = (
            MagicMock()
        )
        computation_engine._forecast_pipeline.compute_excess_solar_signals = MagicMock()
        computation_engine._price_signals.compute_effective_cheap_price = MagicMock()
        computation_engine._price_signals.scan_forecast_for_spike = MagicMock(
            return_value=False
        )
        computation_engine._price_signals.max_forecast_price = MagicMock(
            return_value=0.0
        )
        computation_engine._price_signals.analyze_spike = MagicMock()
        computation_engine._optimizer_facade.run_inline = MagicMock(
            side_effect=lambda data, **kwargs: setattr(data, "optimizer_decisions", [])
        )
        coordinator_data.consumption_profile_type = "weekday_weekend"
        coordinator_data.effective_cheap_price = 0.1
        coordinator_data.general_forecast = []
        coordinator_data.feed_in_forecast = []
        coordinator_data.active_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.general_price = 0.25
        coordinator_data.feed_in_price = 0.08
        coordinator_data.soc = 50.0

        saturday = datetime(2026, 2, 14, 10, 0, 0)
        with patch("homeassistant.util.dt.now", return_value=saturday):
            computation_engine.compute_derived_values(coordinator_data)

        assert coordinator_data.forecast_profile_selected == "weekend"

    def test_forecast_profile_selected_combined(
        self, computation_engine, coordinator_data
    ):
        """Non weekday/weekend profile should pass through."""
        computation_engine._history_fetcher.get_weekday_profile = MagicMock(
            return_value=({}, {})
        )
        computation_engine._history_fetcher.get_weekend_profile = MagicMock(
            return_value=({}, {})
        )
        computation_engine._history_fetcher.get_profile_source = MagicMock(
            return_value="combined"
        )
        computation_engine._history_fetcher._historical_load_cache = {}
        computation_engine._history_fetcher._historical_load_sample_counts = {}
        computation_engine._history_fetcher._historical_load_source = "combined"
        computation_engine._history_fetcher._recent_load_1hr_kw = 0.0
        computation_engine._history_fetcher._recent_load_1hr_statistic_id = "stat"
        computation_engine._history_fetcher._recent_load_1hr_samples = 0
        computation_engine._history_fetcher._recent_load_1hr_last_error = ""
        computation_engine._forecast_pipeline.compute_load_forecast_slots = MagicMock()
        computation_engine._forecast_pipeline.compute_solar_battery_forecast = (
            MagicMock()
        )
        computation_engine._forecast_pipeline.compute_solar_weighted_avg_fit = (
            MagicMock()
        )
        computation_engine._forecast_pipeline.compute_excess_solar_signals = MagicMock()
        computation_engine._price_signals.compute_effective_cheap_price = MagicMock()
        computation_engine._price_signals.scan_forecast_for_spike = MagicMock(
            return_value=False
        )
        computation_engine._price_signals.max_forecast_price = MagicMock(
            return_value=0.0
        )
        computation_engine._price_signals.analyze_spike = MagicMock()
        computation_engine._optimizer_facade.run_inline = MagicMock(
            side_effect=lambda data, **kwargs: setattr(data, "optimizer_decisions", [])
        )
        coordinator_data.consumption_profile_type = "combined"
        coordinator_data.effective_cheap_price = 0.1
        coordinator_data.general_forecast = []
        coordinator_data.feed_in_forecast = []
        coordinator_data.active_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.general_price = 0.25
        coordinator_data.feed_in_price = 0.08
        coordinator_data.soc = 50.0

        with patch(
            "homeassistant.util.dt.now", return_value=datetime(2026, 2, 12, 10, 0, 0)
        ):
            computation_engine.compute_derived_values(coordinator_data)

        assert coordinator_data.forecast_profile_selected == "combined"

    def test_decision_log_skips_when_unpopulated(
        self, computation_engine, coordinator_data
    ):
        """Decision log should skip when sensor data is still zeroed."""
        computation_engine._forecast_pipeline.compute_load_forecast_slots = MagicMock()
        computation_engine._forecast_pipeline.compute_solar_battery_forecast = (
            MagicMock()
        )
        computation_engine._forecast_pipeline.compute_solar_weighted_avg_fit = (
            MagicMock()
        )
        computation_engine._forecast_pipeline.compute_excess_solar_signals = MagicMock()
        computation_engine._price_signals.compute_effective_cheap_price = MagicMock()
        computation_engine._price_signals.scan_forecast_for_spike = MagicMock(
            return_value=False
        )
        computation_engine._price_signals.max_forecast_price = MagicMock(
            return_value=0.0
        )
        computation_engine._price_signals.analyze_spike = MagicMock()
        computation_engine._optimizer_facade.run_inline = MagicMock(
            side_effect=lambda data, **kwargs: setattr(data, "optimizer_decisions", [])
        )
        coordinator_data.general_price = 0.0
        coordinator_data.feed_in_price = 0.0
        coordinator_data.soc = 0.0
        coordinator_data.decision_log = []

        with patch(
            "homeassistant.util.dt.now", return_value=datetime(2026, 2, 12, 10, 0, 0)
        ):
            computation_engine.compute_derived_values(coordinator_data)

        assert coordinator_data.decision_log == []

    def test_decision_log_periodic_update(self, computation_engine, coordinator_data):
        """Decision log should append on periodic update."""
        computation_engine._forecast_pipeline.compute_load_forecast_slots = MagicMock()
        computation_engine._forecast_pipeline.compute_solar_battery_forecast = (
            MagicMock()
        )
        computation_engine._forecast_pipeline.compute_solar_weighted_avg_fit = (
            MagicMock()
        )
        computation_engine._forecast_pipeline.compute_excess_solar_signals = MagicMock()
        computation_engine._price_signals.compute_effective_cheap_price = MagicMock()
        computation_engine._price_signals.scan_forecast_for_spike = MagicMock(
            return_value=False
        )
        computation_engine._price_signals.max_forecast_price = MagicMock(
            return_value=0.0
        )
        computation_engine._price_signals.analyze_spike = MagicMock()
        computation_engine._optimizer_facade.run_inline = MagicMock(
            side_effect=lambda data, **kwargs: setattr(data, "optimizer_decisions", [])
        )
        coordinator_data.general_price = 0.25
        coordinator_data.feed_in_price = 0.08
        coordinator_data.soc = 50.0
        coordinator_data.active_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.decision_log = []
        computation_engine._previous_active_mode = BatteryMode.SELF_CONSUMPTION
        computation_engine._last_decision_log_time = datetime(2026, 2, 12, 9, 50, 0)

        with patch(
            "homeassistant.util.dt.now", return_value=datetime(2026, 2, 12, 10, 0, 0)
        ):
            computation_engine.compute_derived_values(coordinator_data)

        assert len(coordinator_data.decision_log) == 1

    def test_add_to_decision_log_trims(self, computation_engine, coordinator_data):
        """Decision log should retain the most recent 50 entries."""
        coordinator_data.active_mode = BatteryMode.SELF_CONSUMPTION
        coordinator_data.general_price = 0.25
        coordinator_data.feed_in_price = 0.08
        coordinator_data.soc = 50.0
        coordinator_data.decision_log = [{"timestamp": str(i)} for i in range(55)]

        computation_engine._add_to_decision_log(
            coordinator_data, datetime(2026, 2, 12, 10, 0, 0), mode_change=False
        )

        assert len(coordinator_data.decision_log) == 50

    def test_parse_time_option_invalid_falls_back(self, computation_engine):
        """Invalid time strings should fall back to defaults."""
        computation_engine.entry.options["invalid_time"] = "bad"

        parsed = computation_engine._parse_time_option("invalid_time", "18:00:00")

        assert parsed == time(18, 0, 0)

    def test_compute_solar_battery_forecast_delegates(
        self, computation_engine, coordinator_data
    ):
        """Solar battery forecast wrapper should pass target_pct to pipeline."""
        computation_engine.entry.options["battery_target"] = 77.0
        computation_engine._forecast_pipeline.compute_solar_battery_forecast = (
            MagicMock()
        )

        computation_engine._compute_solar_battery_forecast(
            coordinator_data,
            datetime(2026, 2, 12, 10, 0, 0),
            target_hour=18,
            before_dw=True,
            after_dw=False,
        )

        computation_engine._forecast_pipeline.compute_solar_battery_forecast.assert_called_once()
        _, kwargs = (
            computation_engine._forecast_pipeline.compute_solar_battery_forecast.call_args
        )
        assert kwargs["target_pct"] == 77.0

    def test_compute_load_forecast_slots_delegates(
        self, computation_engine, coordinator_data
    ):
        """Load forecast slots wrapper should call pipeline."""
        computation_engine._forecast_pipeline.compute_load_forecast_slots = MagicMock()

        computation_engine._compute_load_forecast_slots(
            coordinator_data,
            datetime(2026, 2, 12, 10, 0, 0),
            historical_avg_kw={10: 0.5},
            recent_load_kw=0.6,
        )

        computation_engine._forecast_pipeline.compute_load_forecast_slots.assert_called_once()

    def test_compute_effective_cheap_price_wrappers(
        self, computation_engine, coordinator_data
    ):
        """Effective cheap price wrappers should call price signals."""
        computation_engine._price_signals.compute_effective_cheap_price_preliminary = (
            MagicMock()
        )
        computation_engine._price_signals.compute_effective_cheap_price = MagicMock()

        computation_engine._compute_effective_cheap_price_preliminary(
            coordinator_data,
            datetime(2026, 2, 12, 10, 0, 0),
            before_dw=True,
            target_hour=18,
            target_pct=80.0,
        )
        computation_engine._compute_effective_cheap_price(
            coordinator_data,
            datetime(2026, 2, 12, 10, 0, 0),
            before_dw=True,
            target_hour=18,
        )

        computation_engine._price_signals.compute_effective_cheap_price_preliminary.assert_called_once()
        computation_engine._price_signals.compute_effective_cheap_price.assert_called_once()

    def test_history_fetcher_properties(self, computation_engine):
        """History fetcher properties should expose cached values."""
        computation_engine._history_fetcher._historical_load_cache = {1: 0.2}
        computation_engine._history_fetcher._historical_load_sample_counts = {1: 3}
        computation_engine._history_fetcher._historical_load_source = "weekday"
        computation_engine._history_fetcher._recent_load_1hr_kw = 0.4
        computation_engine._history_fetcher._recent_load_1hr_statistic_id = "stat_id"
        computation_engine._history_fetcher._recent_load_1hr_samples = 7
        computation_engine._history_fetcher._recent_load_1hr_last_error = ""

        assert computation_engine._historical_load_cache == {1: 0.2}
        assert computation_engine._historical_load_sample_counts == {1: 3}
        assert computation_engine._historical_load_source == "weekday"
        assert computation_engine._recent_load_1hr_kw == 0.4
        assert computation_engine._recent_load_1hr_statistic_id == "stat_id"
        assert computation_engine._recent_load_1hr_samples == 7
        assert computation_engine._recent_load_1hr_last_error == ""

    def test_get_profile_for_day_delegates(self, computation_engine):
        """Profile selection should delegate to history fetcher."""
        computation_engine._history_fetcher.get_profile_for_day = MagicMock(
            return_value=({1: 0.2}, {1: 3}, "weekday")
        )
        result = computation_engine._get_profile_for_day(datetime(2026, 2, 12))
        assert result == ({1: 0.2}, {1: 3}, "weekday")

    def test_helper_wrappers(self, computation_engine):
        """Utility wrappers should return delegated values."""
        with patch(
            "custom_components.localshift.computation_engine.parse_forecast_dt",
            return_value=datetime(2026, 2, 12, 10, 0, 0),
        ) as mock_parse:
            assert computation_engine._parse_forecast_dt("2026-02-12T10:00:00")
            mock_parse.assert_called_once()

        with patch(
            "custom_components.localshift.computation_engine.sum_solar_before_target",
            return_value=3.5,
        ) as mock_sum:
            assert (
                computation_engine._sum_solar_before_target(
                    [], datetime(2026, 2, 12), 18
                )
                == 3.5
            )
            mock_sum.assert_called_once()

        with patch(
            "custom_components.localshift.computation_engine.scan_forecast_for_spike",
            return_value=True,
        ) as mock_scan:
            assert computation_engine._scan_forecast_for_spike(
                [], datetime(2026, 2, 12), datetime(2026, 2, 12, 12, 0, 0)
            )
            mock_scan.assert_called_once()

        with patch(
            "custom_components.localshift.computation_engine.max_forecast_price",
            return_value=0.42,
        ) as mock_max:
            assert (
                computation_engine._max_forecast_price(
                    [], datetime(2026, 2, 12), datetime(2026, 2, 12, 12, 0, 0)
                )
                == 0.42
            )
            mock_max.assert_called_once()

        with patch(
            "custom_components.localshift.computation_engine.percentile",
            return_value=0.25,
        ) as mock_percentile:
            assert computation_engine._percentile([0.1, 0.2, 0.3], 50) == 0.25
            mock_percentile.assert_called_once()

    def test_clear_historical_cache_delegates(self, computation_engine):
        """Historical cache clear should delegate to history fetcher."""
        computation_engine._history_fetcher.clear_historical_cache = MagicMock()

        computation_engine.clear_historical_cache()

        computation_engine._history_fetcher.clear_historical_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_forecast_history_storage_delegates(
        self, computation_engine, coordinator_data
    ):
        """Forecast history storage should delegate to store methods."""
        store_mock = AsyncMock()
        computation_engine._forecast_history_store = store_mock

        await computation_engine.async_initialize_forecast_history_storage()
        await computation_engine.async_load_forecast_history(coordinator_data)
        await computation_engine.async_save_forecast_history(coordinator_data)

        store_mock.async_initialize.assert_awaited_once()
        store_mock.async_load.assert_awaited_once_with(coordinator_data)
        store_mock.async_save.assert_awaited_once_with(coordinator_data)

    @pytest.mark.asyncio
    async def test_async_compute_forecast_accuracy(
        self, computation_engine, coordinator_data
    ):
        """Forecast accuracy compute should delegate to engine."""
        accuracy_mock = AsyncMock()
        computation_engine._forecast_accuracy = accuracy_mock

        await computation_engine.async_compute_forecast_accuracy(coordinator_data)

        accuracy_mock.compute_forecast_accuracy.assert_awaited_once_with(
            coordinator_data
        )

    def test_analyze_spike_delegates(self, computation_engine, coordinator_data):
        """Spike analysis should delegate to price signals."""
        computation_engine._price_signals.analyze_spike = MagicMock()

        computation_engine._analyze_spike(
            coordinator_data, datetime(2026, 2, 12, 10, 0, 0)
        )

        computation_engine._price_signals.analyze_spike.assert_called_once()

    def test_compute_excess_solar_signals_delegates(
        self, computation_engine, coordinator_data
    ):
        """Excess solar signal computation should delegate to forecast pipeline."""
        computation_engine._forecast_pipeline.compute_excess_solar_signals = MagicMock()

        computation_engine._compute_excess_solar_signals(
            coordinator_data, datetime(2026, 2, 12, 10, 0, 0)
        )

        computation_engine._forecast_pipeline.compute_excess_solar_signals.assert_called_once()

    def test_populate_weather_diagnostics_delegates(
        self, computation_engine, coordinator_data
    ):
        """Weather diagnostics should delegate to diagnostics engine."""
        computation_engine._weather_diagnostics.populate_weather_diagnostics = (
            MagicMock()
        )

        computation_engine._populate_weather_diagnostics(coordinator_data)

        computation_engine._weather_diagnostics.populate_weather_diagnostics.assert_called_once()


class TestWeatherCorrelation:
    @pytest.mark.asyncio
    async def test_weather_correlation_init_disabled(self, computation_engine):
        """Initialization should skip when learning disabled."""
        computation_engine.entry.options[CONF_WEATHER_LEARNING_ENABLED] = False

        with patch(
            "custom_components.localshift.computation_engine.WeatherCorrelation"
        ) as mock_wc:
            await computation_engine.async_initialize_weather_correlation()

        mock_wc.assert_not_called()
        assert computation_engine.weather_correlation is None

    @pytest.mark.asyncio
    async def test_weather_correlation_init_success(self, computation_engine):
        """Initialization should wire weather correlation on success."""
        computation_engine.entry.options[CONF_WEATHER_LEARNING_ENABLED] = True
        wc_instance = MagicMock()
        wc_instance.async_initialize = AsyncMock()
        computation_engine._load_forecaster.set_weather_correlation = MagicMock()

        with patch(
            "custom_components.localshift.computation_engine.WeatherCorrelation",
            return_value=wc_instance,
        ):
            await computation_engine.async_initialize_weather_correlation()

        wc_instance.async_initialize.assert_awaited_once()
        computation_engine._load_forecaster.set_weather_correlation.assert_called_once_with(
            wc_instance
        )
        assert computation_engine.weather_correlation == wc_instance

    @pytest.mark.asyncio
    async def test_weather_correlation_init_failure(self, computation_engine):
        """Initialization failures should reset correlation to None."""
        computation_engine.entry.options[CONF_WEATHER_LEARNING_ENABLED] = True
        computation_engine._load_forecaster.set_weather_correlation = MagicMock()

        with patch(
            "custom_components.localshift.computation_engine.WeatherCorrelation",
            side_effect=Exception("boom"),
        ):
            await computation_engine.async_initialize_weather_correlation()

        computation_engine._load_forecaster.set_weather_correlation.assert_called_once_with(
            None
        )
        assert computation_engine.weather_correlation is None

    @pytest.mark.asyncio
    async def test_learn_weather_sample_skips_when_disabled(
        self, computation_engine, coordinator_data
    ):
        """Learning should skip when disabled or correlation missing."""
        computation_engine._weather_correlation = None
        await computation_engine.async_learn_weather_sample(coordinator_data)

        computation_engine._weather_correlation = MagicMock()
        computation_engine.entry.options[CONF_WEATHER_LEARNING_ENABLED] = False
        await computation_engine.async_learn_weather_sample(coordinator_data)

    @pytest.mark.asyncio
    async def test_learn_weather_sample_skips_invalid_values(
        self, computation_engine, coordinator_data
    ):
        """Learning should skip invalid temperature or load."""
        computation_engine.entry.options[CONF_WEATHER_LEARNING_ENABLED] = True
        computation_engine._weather_correlation = MagicMock()
        coordinator_data.weather_temperature_current = 0.0
        coordinator_data.load_power_kw = 1.0

        await computation_engine.async_learn_weather_sample(coordinator_data)

        coordinator_data.weather_temperature_current = 10.0
        coordinator_data.load_power_kw = 0.0

        await computation_engine.async_learn_weather_sample(coordinator_data)

    @pytest.mark.asyncio
    async def test_learn_weather_sample_saves_hourly(
        self, computation_engine, coordinator_data
    ):
        """Learning should record samples and save once per hour."""
        computation_engine.entry.options[CONF_WEATHER_LEARNING_ENABLED] = True
        wc_instance = MagicMock()
        wc_instance.async_save = AsyncMock()
        computation_engine._weather_correlation = wc_instance
        coordinator_data.weather_temperature_current = 22.0
        coordinator_data.load_power_kw = 1.5
        computation_engine._last_weather_save_hour = 9

        with patch(
            "homeassistant.util.dt.now", return_value=datetime(2026, 2, 12, 10, 0, 0)
        ):
            await computation_engine.async_learn_weather_sample(coordinator_data)

        wc_instance.learn_from_sample.assert_called_once()
        wc_instance.async_save.assert_awaited_once()
        assert computation_engine._last_weather_save_hour == 10

    @pytest.mark.asyncio
    async def test_refresh_weather_forecast_branches(self, computation_engine):
        """Forecast refresh should handle None, disabled, success, and errors."""
        computation_engine._weather_correlation = None
        assert await computation_engine.async_refresh_weather_forecast() is None

        computation_engine._weather_correlation = MagicMock()
        computation_engine.entry.options[CONF_WEATHER_LEARNING_ENABLED] = False
        assert await computation_engine.async_refresh_weather_forecast() is None

        computation_engine.entry.options[CONF_WEATHER_LEARNING_ENABLED] = True
        wc_instance = MagicMock()
        wc_instance.async_get_temperature_forecast = AsyncMock(return_value=["f"])
        computation_engine._weather_correlation = wc_instance

        assert await computation_engine.async_refresh_weather_forecast() == ["f"]

        wc_instance.async_get_temperature_forecast = AsyncMock(
            side_effect=Exception("boom")
        )
        assert await computation_engine.async_refresh_weather_forecast() is None


# =============================================================================
# Threshold Consistency Tests (Fix: planner/UI threshold mismatch)
# =============================================================================


def test_final_effective_cheap_price_reflects_optimizer_solar_reach(
    computation_engine, coordinator_data
):
    """Final effective_cheap_price must reflect optimizer's solar_can_reach_target, not the preliminary guess.

    Scenario: preliminary pass assumes solar_cannot_reach_target (urgency pricing),
    but the optimizer's own solar simulation finds that solar CAN reach target.
    The final effective_cheap_price must use the base percentile price (not urgency),
    so the displayed threshold matches what the optimizer actually used.

    Regression test for: optimizer runs with preliminary threshold (~$0.18 urgency-adjusted),
    then final price is recomputed to ~$0.10 base percentile. UI shows $0.10
    but plan was computed at $0.18 — making charges above $0.10 appear wrong.
    """
    coordinator_data.soc = 50.0
    coordinator_data.general_price = 0.25
    coordinator_data.feed_in_price = 0.05
    coordinator_data.target_reached_today = False
    coordinator_data.solcast_today = []
    coordinator_data.solcast_tomorrow = []

    original_run_inline = computation_engine._optimizer_facade.run_inline

    def mock_run_inline(data, *args, **kwargs):
        # Override effective_cheap_price BEFORE optimizer runs, simulating
        # the preliminary pass having set it to a high urgency value
        data.effective_cheap_price = 0.30  # urgency-adjusted (preliminary)
        data.optimizer_decisions = []
        # The optimizer finds solar CAN reach target
        # Step 7 will recompute to base percentile ~0.04
        return original_run_inline(data, *args, **kwargs)

    computation_engine._optimizer_facade.run_inline = mock_run_inline

    try:
        computation_engine.compute_derived_values(coordinator_data)
    finally:
        computation_engine._optimizer_facade.run_inline = original_run_inline

    # planner_threshold_used must capture the optimizer's threshold (preliminary = 0.30)
    assert coordinator_data.planner_threshold_used == pytest.approx(0.30, abs=0.01), (
        f"planner_threshold_used ({coordinator_data.planner_threshold_used}) "
        f"should be the optimizer's threshold (0.30), "
        f"not the recomputed final value"
    )


def test_cheap_charge_stop_price_uses_final_effective_threshold(
    computation_engine, coordinator_data
):
    """cheap_charge_stop_price must be based on the final effective_cheap_price, not the preliminary.

    The stop price is effective_cheap_price + deadband. If the final effective_cheap_price
    differs from the preliminary, the stop price must reflect the final value.
    """
    coordinator_data.soc = 50.0
    coordinator_data.general_price = 0.25
    coordinator_data.feed_in_price = 0.05
    coordinator_data.target_reached_today = False
    coordinator_data.solcast_today = []
    coordinator_data.solcast_tomorrow = []

    original_run_inline = computation_engine._optimizer_facade.run_inline

    def mock_run_inline(data, *args, **kwargs):
        data.solar_can_reach_target = True
        data.optimizer_decisions = []
        return original_run_inline(data, *args, **kwargs)

    computation_engine._optimizer_facade.run_inline = mock_run_inline

    try:
        computation_engine.compute_derived_values(coordinator_data)
    finally:
        computation_engine._optimizer_facade.run_inline = original_run_inline

    from custom_components.localshift.const import DEFAULT_CHEAP_PRICE_DEADBAND

    expected_stop = (
        coordinator_data.effective_cheap_price + DEFAULT_CHEAP_PRICE_DEADBAND
    )
    assert coordinator_data.cheap_charge_stop_price == pytest.approx(
        expected_stop, abs=0.001
    ), (
        f"cheap_charge_stop_price ({coordinator_data.cheap_charge_stop_price}) "
        f"should be effective_cheap_price ({coordinator_data.effective_cheap_price}) "
        f"+ deadband ({DEFAULT_CHEAP_PRICE_DEADBAND}) = {expected_stop}"
    )


# =============================================================================


# =============================================================================
# Fresh-SOC refresh before the optimizer (2026-06-30 silent pre-charge miss)
# =============================================================================


def test_refresh_soc_for_optimizer_uses_fresh_read(
    computation_engine, coordinator_data
):
    """The optimizer must plan from a fresh live SOC, not a stale cached value.

    Regression for the 2026-06-30 incident: a high cached SOC while the pack had
    drained to ~10% caused the planner to skip pre-charge.
    """
    from types import SimpleNamespace

    from custom_components.localshift.const import CONF_TESLEMETRY_SOC

    coordinator_data.soc = 95.0  # stale, cached high
    soc_entity = computation_engine._get_entity_id(CONF_TESLEMETRY_SOC)
    computation_engine.hass.states = {soc_entity: SimpleNamespace(state="10.0")}

    computation_engine._refresh_soc_for_optimizer(coordinator_data)

    assert coordinator_data.soc == 10.0


def test_refresh_soc_keeps_cached_when_unavailable(
    computation_engine, coordinator_data
):
    """An unavailable SOC entity must not clobber the cached value with garbage."""
    from types import SimpleNamespace

    from custom_components.localshift.const import CONF_TESLEMETRY_SOC

    coordinator_data.soc = 42.0
    soc_entity = computation_engine._get_entity_id(CONF_TESLEMETRY_SOC)
    computation_engine.hass.states = {soc_entity: SimpleNamespace(state="unavailable")}

    computation_engine._refresh_soc_for_optimizer(coordinator_data)

    assert coordinator_data.soc == 42.0

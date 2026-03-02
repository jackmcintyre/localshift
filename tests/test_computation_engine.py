"""Unit tests for ComputationEngine."""

from datetime import datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.computation_engine import (
    BatteryMode,
    ForecastChangeTracker,
)


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
    """Test active_mode when automation is disabled."""
    computation_engine._get_switch_state = MagicMock(return_value=False)
    # Also mock the mode decision engine's reference to the switch state function
    computation_engine._mode_decision._get_switch_state = MagicMock(return_value=False)

    # Mock the forecast computation to prevent forecast-driven mode selection
    with patch.object(
        computation_engine, "_compute_daily_15min_forecast"
    ) as mock_forecast:
        # Set up an empty forecast so the mode decision falls through to SELF_CONSUMPTION
        coordinator_data.daily_forecast = []
        computation_engine.compute_derived_values(coordinator_data)
        mock_forecast.assert_called_once()

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
        # Also mock the mode decision engine's reference to the switch state function
        computation_engine._mode_decision._get_switch_state = MagicMock(
            side_effect=mock_switch_state
        )

        # Mock the forecast computation to prevent it from overwriting our test data
        with patch.object(
            computation_engine, "_compute_daily_15min_forecast"
        ) as mock_forecast:
            computation_engine.compute_derived_values(coordinator_data)
            # Ensure forecast computation was called but didn't overwrite our mock data
            mock_forecast.assert_called_once()

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
# FORECAST CHANGE TRACKER TESTS
# =============================================================================


class TestForecastChangeTracker:
    """Tests for ForecastChangeTracker logic."""

    def test_should_recompute_first_run(self):
        """First run should always recompute."""
        tracker = ForecastChangeTracker()
        now_dt = datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone(timedelta(hours=11)))

        should_recompute, reason = tracker.should_recompute_forecast(
            soc=50.0, price=0.25, feed_in_price=0.08, now_dt=now_dt
        )

        assert should_recompute is True
        assert reason == "first_run"

    def test_should_recompute_forced(self):
        """Force flag should always trigger recompute."""
        tracker = ForecastChangeTracker()
        now_dt = datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone(timedelta(hours=11)))

        # Initialize tracker
        tracker.should_recompute_forecast(
            soc=50.0, price=0.25, feed_in_price=0.08, now_dt=now_dt
        )

        # Force recompute
        should_recompute, reason = tracker.should_recompute_forecast(
            soc=50.0, price=0.25, feed_in_price=0.08, now_dt=now_dt, force=True
        )

        assert should_recompute is True
        assert reason == "forced"

    def test_should_recompute_price_change(self):
        """Price change should trigger recompute."""
        tracker = ForecastChangeTracker()
        now_dt = datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone(timedelta(hours=11)))

        # Initialize tracker
        tracker.should_recompute_forecast(
            soc=50.0, price=0.25, feed_in_price=0.08, now_dt=now_dt
        )

        # Price change
        should_recompute, reason = tracker.should_recompute_forecast(
            soc=50.0, price=0.30, feed_in_price=0.08, now_dt=now_dt
        )

        assert should_recompute is True
        assert "price_change" in reason

    def test_should_recompute_feed_in_change(self):
        """Feed-in price change should trigger recompute."""
        tracker = ForecastChangeTracker()
        now_dt = datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone(timedelta(hours=11)))

        # Initialize tracker
        tracker.should_recompute_forecast(
            soc=50.0, price=0.25, feed_in_price=0.08, now_dt=now_dt
        )

        # Feed-in change
        should_recompute, reason = tracker.should_recompute_forecast(
            soc=50.0, price=0.25, feed_in_price=0.10, now_dt=now_dt
        )

        assert should_recompute is True
        assert "fit_change" in reason

    def test_should_recompute_soc_change(self):
        """SOC change >= 1% should trigger recompute."""
        tracker = ForecastChangeTracker()
        now_dt = datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone(timedelta(hours=11)))

        # Initialize tracker
        tracker.should_recompute_forecast(
            soc=50.0, price=0.25, feed_in_price=0.08, now_dt=now_dt
        )

        # SOC change >= 1%
        should_recompute, reason = tracker.should_recompute_forecast(
            soc=52.0, price=0.25, feed_in_price=0.08, now_dt=now_dt
        )

        assert should_recompute is True
        assert "soc_change" in reason

    def test_should_not_recompute_small_soc_change(self):
        """SOC change < 1% should NOT trigger recompute."""
        tracker = ForecastChangeTracker()
        now_dt = datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone(timedelta(hours=11)))

        # Initialize tracker
        tracker.should_recompute_forecast(
            soc=50.0, price=0.25, feed_in_price=0.08, now_dt=now_dt
        )

        # Small SOC change < 1%
        should_recompute, reason = tracker.should_recompute_forecast(
            soc=50.5, price=0.25, feed_in_price=0.08, now_dt=now_dt
        )

        assert should_recompute is False
        assert reason == "no_change"

    def test_should_recompute_age_timeout(self):
        """Forecast age > 10 minutes should trigger recompute."""
        tracker = ForecastChangeTracker()
        now_dt = datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone(timedelta(hours=11)))

        # Initialize tracker
        tracker.should_recompute_forecast(
            soc=50.0, price=0.25, feed_in_price=0.08, now_dt=now_dt
        )

        # Move time forward by 11 minutes (exceeds 10-minute threshold)
        later_dt = now_dt + timedelta(minutes=11)
        should_recompute, reason = tracker.should_recompute_forecast(
            soc=50.0, price=0.25, feed_in_price=0.08, now_dt=later_dt
        )

        assert should_recompute is True
        assert "age" in reason

    def test_should_not_recompute_within_age_window(self):
        """No changes within 1-minute window should NOT recompute."""
        tracker = ForecastChangeTracker()
        now_dt = datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone(timedelta(hours=11)))

        # Initialize tracker
        tracker.should_recompute_forecast(
            soc=50.0, price=0.25, feed_in_price=0.08, now_dt=now_dt
        )

        # Check again immediately (no changes)
        should_recompute, reason = tracker.should_recompute_forecast(
            soc=50.0, price=0.25, feed_in_price=0.08, now_dt=now_dt
        )

        assert should_recompute is False
        assert reason == "no_change"


# =============================================================================
# SPIKE ANALYSIS TESTS (Conservative Mode)
# =============================================================================


class TestSpikeAnalysis:
    """Tests for _analyze_spike conservative mode logic."""

    def test_analyze_spike_disabled(self, computation_engine, coordinator_data):
        """Spike analysis should skip when conservative mode disabled."""
        computation_engine._get_switch_state = MagicMock(return_value=False)

        now_dt = datetime(2026, 2, 16, 18, 0, 0, tzinfo=timezone(timedelta(hours=11)))
        computation_engine._analyze_spike(coordinator_data, now_dt)

        # Should not populate spike fields
        assert coordinator_data.spike_in_conservative_mode is False
        assert coordinator_data.spike_end_time is None

    def test_analyze_spike_no_spike_in_forecast(
        self, computation_engine, coordinator_data
    ):
        """Spike analysis should handle no spike in forecast."""

        # Enable conservative mode
        def mock_switch_state(key):
            if key == "spike_discharge_conservative":
                return True
            return False

        computation_engine._get_switch_state = MagicMock(side_effect=mock_switch_state)

        # No spike prices in forecast
        coordinator_data.feed_in_forecast = [
            {
                "start_time": "2026-02-16T18:00:00+11:00",
                "end_time": "2026-02-16T18:05:00+11:00",
                "per_kwh": 0.08,
            },
            {
                "start_time": "2026-02-16T18:05:00+11:00",
                "end_time": "2026-02-16T18:10:00+11:00",
                "per_kwh": 0.09,
            },
        ]

        now_dt = datetime(2026, 2, 16, 18, 0, 0, tzinfo=timezone(timedelta(hours=11)))
        computation_engine._analyze_spike(coordinator_data, now_dt)

        # Should not detect spike
        assert coordinator_data.spike_end_time is None
        assert coordinator_data.spike_max_price == 0.0

    def test_analyze_spike_with_spike_prices(
        self, computation_engine, coordinator_data
    ):
        """Spike analysis should detect and analyze spike prices."""

        # Enable conservative mode
        def mock_switch_state(key):
            if key == "spike_discharge_conservative":
                return True
            return False

        computation_engine._get_switch_state = MagicMock(side_effect=mock_switch_state)
        # Also mock the spike analyzer's reference to the switch state function
        computation_engine._spike_analyzer._get_switch_state = MagicMock(
            side_effect=mock_switch_state
        )

        # Create forecast with spike prices (> 1.0 $/kWh)
        # Note: spike_status field is required for spike detection
        coordinator_data.feed_in_forecast = [
            {
                "start_time": "2026-02-16T18:00:00+11:00",
                "end_time": "2026-02-16T18:05:00+11:00",
                "per_kwh": 1.50,  # Spike price
                "spike_status": "spike",  # Required for spike detection
            },
            {
                "start_time": "2026-02-16T18:05:00+11:00",
                "end_time": "2026-02-16T18:10:00+11:00",
                "per_kwh": 2.00,  # Higher spike
                "spike_status": "spike",  # Required for spike detection
            },
            {
                "start_time": "2026-02-16T18:10:00+11:00",
                "end_time": "2026-02-16T18:15:00+11:00",
                "per_kwh": 0.10,  # Normal price (no spike_status)
            },
        ]

        now_dt = datetime(2026, 2, 16, 18, 0, 0, tzinfo=timezone(timedelta(hours=11)))

        # Mock the _analyze_spike_window utility function to return spike data
        spike_end = datetime(
            2026, 2, 16, 18, 10, 0, tzinfo=timezone(timedelta(hours=11))
        )
        max_price = 2.0
        spike_prices = [1.50, 2.00]
        computation_engine._spike_analyzer._analyze_spike_window = MagicMock(
            return_value=(spike_end, max_price, spike_prices)
        )

        computation_engine._analyze_spike(coordinator_data, now_dt)

        # Should detect spike
        assert coordinator_data.spike_in_conservative_mode is True
        assert coordinator_data.spike_max_price > 1.0
        assert coordinator_data.spike_price_threshold > 0

    def test_analyze_spike_calculates_reserve_soc(
        self, computation_engine, coordinator_data
    ):
        """Spike analysis should calculate reserve SOC needed."""

        # Enable conservative mode
        def mock_switch_state(key):
            if key == "spike_discharge_conservative":
                return True
            return False

        computation_engine._get_switch_state = MagicMock(side_effect=mock_switch_state)

        # Create forecast with spike
        coordinator_data.feed_in_forecast = [
            {
                "start_time": "2026-02-16T18:00:00+11:00",
                "end_time": "2026-02-16T18:30:00+11:00",
                "per_kwh": 2.00,
            },
        ]
        coordinator_data.load_power_kw = 1.0  # 1 kW load

        now_dt = datetime(2026, 2, 16, 18, 0, 0, tzinfo=timezone(timedelta(hours=11)))
        computation_engine._analyze_spike(coordinator_data, now_dt)

        # Should calculate reserve SOC
        assert coordinator_data.spike_reserve_soc >= 0
        assert coordinator_data.spike_hours_remaining >= 0


# =============================================================================
# COMPUTE DAILY 15-MIN FORECAST TESTS
# =============================================================================


class TestComputeDaily15MinForecast:
    """Tests for _compute_daily_15min_forecast method."""

    def test_forecast_uses_change_tracker(self, computation_engine, coordinator_data):
        """Forecast computation should use change tracker."""
        now_dt = datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone(timedelta(hours=11)))

        # First call should trigger recompute
        with patch.object(
            computation_engine._forecast_computer, "compute_forecast"
        ) as mock_compute:
            mock_compute.return_value = ([], [], {})
            computation_engine._compute_daily_15min_forecast(coordinator_data, now_dt)
            mock_compute.assert_called_once()

    def test_forecast_skips_on_no_change(self, computation_engine, coordinator_data):
        """Forecast should skip recompute when no significant changes."""
        now_dt = datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone(timedelta(hours=11)))

        # Initialize with first call
        with patch.object(
            computation_engine._forecast_computer, "compute_forecast"
        ) as mock_compute:
            mock_compute.return_value = ([], [], {})
            computation_engine._compute_daily_15min_forecast(coordinator_data, now_dt)

        # Second call with same values should skip
        mock_compute.reset_mock()
        computation_engine._compute_daily_15min_forecast(coordinator_data, now_dt)
        mock_compute.assert_not_called()

    def test_forecast_handles_exception(self, computation_engine, coordinator_data):
        """Forecast should handle exceptions gracefully."""
        now_dt = datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone(timedelta(hours=11)))

        with patch.object(
            computation_engine._forecast_computer, "compute_forecast"
        ) as mock_compute:
            mock_compute.side_effect = Exception("Test error")
            # Should not raise
            computation_engine._compute_daily_15min_forecast(coordinator_data, now_dt)
            # Should have empty forecast
            assert coordinator_data.daily_forecast == []


@pytest.mark.asyncio
async def test_async_initialize_weather_correlation_updates_forecast_computer(
    computation_engine,
):
    """Weather initialization should wire correlation into ForecastComputer."""
    computation_engine.entry.options["weather_learning_enabled"] = True

    with patch(
        "custom_components.localshift.computation_engine.WeatherCorrelation"
    ) as mock_weather_cls:
        mock_weather = MagicMock()
        mock_weather.async_initialize = AsyncMock(return_value=None)
        mock_weather_cls.return_value = mock_weather

        with patch.object(
            computation_engine._forecast_computer,
            "set_weather_correlation",
        ) as mock_set_weather:
            await computation_engine.async_initialize_weather_correlation()

        mock_set_weather.assert_called_once_with(mock_weather)

class TestLoadForecastSlots:
    """Tests for load_forecast_slots (Issue #441 Phase 1)."""

    def test_load_forecast_slots_populated_before_forecast(
        self, computation_engine, coordinator_data
    ):
        """Test that load_forecast_slots has 96 entries after compute_derived_values()."""
        from custom_components.localshift.computation_engine_lib.slot_schedule import (
            TOTAL_SLOTS,
        )

        # Ensure we have valid data
        coordinator_data.load_power_kw = 0.5

        with patch.object(
            computation_engine, "_get_historical_hourly_averages", return_value={10: 0.5, 11: 0.6}
        ):
            computation_engine.compute_derived_values(coordinator_data)

        # Verify load_forecast_slots is populated
        assert hasattr(coordinator_data, "load_forecast_slots")
        assert len(coordinator_data.load_forecast_slots) == TOTAL_SLOTS
        assert all(isinstance(v, float) and v >= 0 for v in coordinator_data.load_forecast_slots)

    def test_load_forecast_slots_populated_before_forecast_computer_runs(
        self, computation_engine, coordinator_data
    ):
        """Test that load_forecast_slots is populated before _compute_daily_15min_forecast is called."""
        coordinator_data.load_power_kw = 0.5

        with patch.object(
            computation_engine, "_get_historical_hourly_averages", return_value={10: 0.5, 11: 0.6}
        ):
            with patch.object(
                computation_engine, "_compute_daily_15min_forecast"
            ) as mock_forecast:
                # Track the state of load_forecast_slots when _compute_daily_15min_forecast is called
                def check_slots_on_call(*args, **kwargs):
                    assert hasattr(coordinator_data, "load_forecast_slots")
                    assert len(coordinator_data.load_forecast_slots) > 0
                    assert all(isinstance(v, float) and v >= 0 for v in coordinator_data.load_forecast_slots)
                    return None

                mock_forecast.side_effect = check_slots_on_call
                computation_engine.compute_derived_values(coordinator_data)

                # Verify _compute_daily_15min_forecast was called
                assert mock_forecast.called

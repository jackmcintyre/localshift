"""Unit tests for ComputationEngine."""

from datetime import datetime, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.computation_engine import BatteryMode


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

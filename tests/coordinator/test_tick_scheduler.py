"""Tests for TickScheduler."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.coordinator.tick_scheduler import TickScheduler


async def test_tick_scheduler_initialization(coordinator):
    """Test TickScheduler can be instantiated."""
    scheduler = TickScheduler(coordinator)

    assert scheduler is not None
    assert scheduler._coordinator is coordinator


@pytest.mark.asyncio
async def test_handle_state_change(coordinator):
    """Test handle_state_change delegates to evaluation dispatcher."""
    scheduler = TickScheduler(coordinator)
    mock_event = MagicMock()

    # Mock evaluation dispatcher
    coordinator._evaluation_dispatcher = MagicMock()
    coordinator._evaluation_dispatcher.on_state_change = MagicMock()

    scheduler.handle_state_change(mock_event)

    coordinator._evaluation_dispatcher.on_state_change.assert_called_once_with(
        mock_event
    )


@pytest.mark.asyncio
async def test_handle_state_change_no_dispatcher(coordinator):
    """Test handle_state_change handles missing dispatcher gracefully."""
    scheduler = TickScheduler(coordinator)
    coordinator._evaluation_dispatcher = None

    # Should not raise
    scheduler.handle_state_change(MagicMock())


@pytest.mark.asyncio
async def test_handle_periodic_tick(coordinator):
    """Test handle_periodic_tick delegates to handle_fast_tick."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock handle_fast_tick
    scheduler.handle_fast_tick = MagicMock()

    scheduler.handle_periodic_tick(now)

    scheduler.handle_fast_tick.assert_called_once_with(now)


@pytest.mark.asyncio
async def test_handle_fast_tick(coordinator):
    """Test handle_fast_tick reads state, accumulates costs, and dispatches evaluation."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock dependencies
    coordinator._entity_monitor = MagicMock()
    coordinator._entity_monitor.read_all_external_state = MagicMock()
    coordinator._cost_tracker = MagicMock()
    coordinator._cost_tracker.accumulate_costs = MagicMock()
    coordinator._evaluation_dispatcher = MagicMock()
    coordinator._evaluation_dispatcher.maybe_trigger_on_startup_ready = MagicMock()
    coordinator._evaluation_dispatcher.on_fast_tick = MagicMock()
    coordinator._state_machine = MagicMock()
    coordinator._state_machine.startup_grace_until = None
    coordinator.data = MagicMock()
    coordinator.data.automation_ready = True

    scheduler.handle_fast_tick(now)

    coordinator._entity_monitor.read_all_external_state.assert_called_once()
    coordinator._cost_tracker.accumulate_costs.assert_called_once_with(coordinator.data)
    coordinator._evaluation_dispatcher.on_fast_tick.assert_called_once_with(now)


@pytest.mark.asyncio
async def test_handle_fast_tick_startup_grace(coordinator):
    """Test handle_fast_tick skips evaluation during startup grace period."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock dependencies
    coordinator._entity_monitor = MagicMock()
    coordinator._entity_monitor.read_all_external_state = MagicMock()
    coordinator._cost_tracker = MagicMock()
    coordinator._cost_tracker.accumulate_costs = MagicMock()
    coordinator._evaluation_dispatcher = MagicMock()
    coordinator._evaluation_dispatcher.on_fast_tick = MagicMock()
    coordinator._state_machine = MagicMock()
    coordinator._state_machine.startup_grace_until = (
        datetime.now()
    )  # Active grace period
    coordinator.data = MagicMock()

    scheduler.handle_fast_tick(now)

    coordinator._entity_monitor.read_all_external_state.assert_called_once()
    coordinator._cost_tracker.accumulate_costs.assert_called_once_with(coordinator.data)
    # Should NOT dispatch evaluation during startup grace
    coordinator._evaluation_dispatcher.on_fast_tick.assert_not_called()


@pytest.mark.asyncio
async def test_handle_medium_tick(coordinator):
    """Test handle_medium_tick performs learning and monitoring tasks."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock dependencies
    coordinator._entity_monitor = MagicMock()
    coordinator._entity_monitor.read_all_external_state = MagicMock()
    coordinator._entity_monitor.check_entity_health = MagicMock()
    coordinator._state_machine = MagicMock()
    coordinator._state_machine.startup_grace_until = None  # No grace period
    coordinator._learning_orchestrator = MagicMock()
    coordinator._learning_orchestrator.update_medium_tick = MagicMock()
    coordinator.data = MagicMock()
    coordinator.data.solar_bias_metrics = {}
    coordinator.data.solar_forecast_accuracy = 0.0

    scheduler.handle_medium_tick(now)

    coordinator._entity_monitor.read_all_external_state.assert_called_once()
    coordinator._entity_monitor.check_entity_health.assert_called_once()
    coordinator._learning_orchestrator.update_medium_tick.assert_called_once_with(
        coordinator.data
    )


@pytest.mark.asyncio
async def test_handle_medium_tick_startup_grace(coordinator):
    """Test handle_medium_tick skips operations during startup grace period."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock dependencies
    coordinator._entity_monitor = MagicMock()
    coordinator._entity_monitor.read_all_external_state = MagicMock()
    coordinator._entity_monitor.check_entity_health = MagicMock()
    coordinator._state_machine = MagicMock()
    coordinator._state_machine.startup_grace_until = (
        datetime.now()
    )  # Active grace period

    scheduler.handle_medium_tick(now)

    coordinator._entity_monitor.read_all_external_state.assert_called_once()
    # Should NOT check health during startup grace
    coordinator._entity_monitor.check_entity_health.assert_not_called()


@pytest.mark.asyncio
async def test_handle_slow_tick(coordinator):
    """Test handle_slow_tick refreshes weather and computes metrics."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock dependencies
    coordinator._entity_monitor = MagicMock()
    coordinator._entity_monitor.refresh_weather_forecast = AsyncMock()
    coordinator.hass = MagicMock()
    coordinator.hass.async_create_task = MagicMock(
        side_effect=lambda coro, name=None: (
            coro.close() if hasattr(coro, "close") else None
        )
    )

    # Mock backfill method
    scheduler._backfill_solar_actual = MagicMock()

    scheduler.handle_slow_tick(now)

    # Should schedule async task for weather refresh
    assert coordinator.hass.async_create_task.call_count >= 1
    # Should schedule backfill
    scheduler._backfill_solar_actual.assert_called_once()


@pytest.mark.asyncio
async def test_handle_midnight_reset(coordinator):
    """Test handle_midnight_reset resets cost accumulators and flags."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock dependencies
    coordinator.data = MagicMock()
    coordinator.data.grid_import_cost = 100.0
    coordinator.data.grid_export_revenue = 50.0
    coordinator.data.battery_savings = 25.0
    coordinator.data.battery_charge_cost = 10.0
    coordinator.data.target_reached_today = True
    coordinator._learning_orchestrator = MagicMock()
    coordinator._learning_orchestrator.handle_midnight_reset = MagicMock()
    coordinator.notify_listeners = MagicMock()

    scheduler.handle_midnight_reset(now)

    assert coordinator.data.grid_import_cost == 0.0
    assert coordinator.data.grid_export_revenue == 0.0
    assert coordinator.data.battery_savings == 0.0
    assert coordinator.data.battery_charge_cost == 0.0
    assert coordinator.data.target_reached_today is False
    coordinator._learning_orchestrator.handle_midnight_reset.assert_called_once_with(
        coordinator.data
    )
    coordinator.notify_listeners.assert_called_once()


@pytest.mark.asyncio
async def test_handle_daily_summary(coordinator):
    """Test handle_daily_summary sends daily summary when automation enabled."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock dependencies
    coordinator.hass = MagicMock()
    coordinator.hass.async_create_task = MagicMock(
        side_effect=lambda coro, name=None: (
            coro.close() if hasattr(coro, "close") else None
        )
    )
    coordinator.get_switch_state = MagicMock(return_value=True)

    # Mock _send_daily_summary
    scheduler._send_daily_summary = AsyncMock()

    scheduler.handle_daily_summary(now)

    coordinator.hass.async_create_task.assert_called_once()


@pytest.mark.asyncio
async def test_handle_daily_summary_automation_disabled(coordinator):
    """Test handle_daily_summary skips when automation disabled."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock dependencies
    coordinator.hass = MagicMock()
    coordinator.hass.async_create_task = MagicMock()
    coordinator.get_switch_state = MagicMock(return_value=False)

    scheduler.handle_daily_summary(now)

    # Should NOT create task when automation disabled
    coordinator.hass.async_create_task.assert_not_called()


@pytest.mark.asyncio
async def test_is_in_startup_grace(coordinator):
    """Test _is_in_startup_grace checks state machine grace period."""
    scheduler = TickScheduler(coordinator)

    # Test when state machine has active grace period
    coordinator._state_machine = MagicMock()
    coordinator._state_machine.startup_grace_until = datetime.now()

    assert scheduler._is_in_startup_grace() is True

    # Test when state machine grace period expired
    coordinator._state_machine.startup_grace_until = None

    assert scheduler._is_in_startup_grace() is False

    # Test when no state machine
    coordinator._state_machine = None

    assert scheduler._is_in_startup_grace() is True


@pytest.mark.asyncio
async def test_accumulate_costs(coordinator):
    """Test _accumulate_costs delegates to cost tracker."""
    scheduler = TickScheduler(coordinator)

    # Mock dependencies
    coordinator._cost_tracker = MagicMock()
    coordinator._cost_tracker.accumulate_costs = MagicMock()
    coordinator.data = MagicMock()

    scheduler._accumulate_costs()

    coordinator._cost_tracker.accumulate_costs.assert_called_once_with(coordinator.data)


@pytest.mark.asyncio
async def test_accumulate_costs_no_tracker(coordinator):
    """Test _accumulate_costs handles missing cost tracker gracefully."""
    scheduler = TickScheduler(coordinator)
    coordinator._cost_tracker = None

    # Should not raise
    scheduler._accumulate_costs()


@pytest.mark.asyncio
async def test_handle_medium_tick_with_computation_engine(coordinator):
    """Test handle_medium_tick with computation engine tasks."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock dependencies
    coordinator._entity_monitor = MagicMock()
    coordinator._entity_monitor.read_all_external_state = MagicMock()
    coordinator._state_machine = MagicMock()
    coordinator._state_machine.startup_grace_until = None  # No grace period
    coordinator._computation_engine = MagicMock()
    coordinator._computation_engine.async_get_recent_load_1hr = AsyncMock()
    coordinator._computation_engine.async_get_historical_hourly_averages = AsyncMock()
    coordinator._computation_engine.async_learn_weather_sample = AsyncMock()
    coordinator.get_entity_id = MagicMock(return_value="sensor.load")
    coordinator.hass = MagicMock()
    coordinator.hass.async_create_task = MagicMock(
        side_effect=lambda coro, name=None: (
            coro.close() if hasattr(coro, "close") else None
        )
    )
    coordinator.data = MagicMock()

    scheduler.handle_medium_tick(now)

    # Should create async tasks for load refresh and weather learning
    assert coordinator.hass.async_create_task.call_count >= 2


@pytest.mark.asyncio
async def test_handle_slow_tick_with_computation_engine(coordinator):
    """Test handle_slow_tick with computation engine tasks."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock dependencies
    coordinator._entity_monitor = MagicMock()
    coordinator._entity_monitor.refresh_weather_forecast = AsyncMock()
    coordinator._computation_engine = MagicMock()
    coordinator._computation_engine.async_compute_forecast_accuracy = AsyncMock()
    coordinator._computation_engine.async_save_forecast_history = AsyncMock()
    coordinator._computation_engine.async_save_accuracy_metrics = AsyncMock()
    coordinator.hass = MagicMock()
    coordinator.hass.async_create_task = MagicMock(
        side_effect=lambda coro, name=None: (
            coro.close() if hasattr(coro, "close") else None
        )
    )
    coordinator.data = MagicMock()

    # Mock backfill method
    scheduler._backfill_solar_actual = MagicMock()

    scheduler.handle_slow_tick(now)

    # Should create async tasks for forecast accuracy and history
    assert coordinator.hass.async_create_task.call_count >= 3


@pytest.mark.asyncio
async def test_send_daily_summary(coordinator):
    """Test _send_daily_summary sends notification."""
    scheduler = TickScheduler(coordinator)

    # Mock notification service
    coordinator._notification_service = MagicMock()
    coordinator._notification_service.send_daily_summary = AsyncMock()
    coordinator.data = MagicMock()

    await scheduler._send_daily_summary()

    coordinator._notification_service.send_daily_summary.assert_called_once_with(
        coordinator.data
    )


@pytest.mark.asyncio
async def test_send_daily_summary_no_service(coordinator):
    """Test _send_daily_summary handles missing notification service."""
    scheduler = TickScheduler(coordinator)
    coordinator._notification_service = None

    # Should not raise
    await scheduler._send_daily_summary()


@pytest.mark.asyncio
async def test_backfill_solar_actual_no_tracker_attribute(coordinator):
    """Test _backfill_solar_actual handles missing tracker attribute."""
    scheduler = TickScheduler(coordinator)

    # Ensure no solar_accuracy_tracker attribute
    if hasattr(coordinator, "solar_accuracy_tracker"):
        delattr(coordinator, "solar_accuracy_tracker")

    # Should not raise
    scheduler._backfill_solar_actual()


@pytest.mark.asyncio
async def test_backfill_solar_actual_tracker_none(coordinator):
    """Test _backfill_solar_actual handles None tracker."""
    scheduler = TickScheduler(coordinator)
    coordinator.solar_accuracy_tracker = None

    # Should not raise
    scheduler._backfill_solar_actual()


@pytest.mark.asyncio
async def test_backfill_solar_actual_first_call(coordinator):
    """Test _backfill_solar_actual initializes timestamp on first call."""
    scheduler = TickScheduler(coordinator)

    # Mock tracker
    coordinator.solar_accuracy_tracker = MagicMock()
    coordinator.solar_accuracy_tracker.backfill_actual = MagicMock()
    scheduler._last_solar_power_timestamp = None
    scheduler._last_solar_power_kw = None
    coordinator.data = MagicMock()
    coordinator.data.solar_power_kw = 5.0

    scheduler._backfill_solar_actual()

    # Should initialize timestamp and power, but NOT call backfill_actual
    assert scheduler._last_solar_power_timestamp is not None
    assert scheduler._last_solar_power_kw == 5.0
    coordinator.solar_accuracy_tracker.backfill_actual.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_solar_actual_small_time_delta(coordinator):
    """Test _backfill_solar_actual skips backfill for very small time deltas."""
    from homeassistant.util import dt as dt_util

    scheduler = TickScheduler(coordinator)

    # Mock tracker
    coordinator.solar_accuracy_tracker = MagicMock()
    coordinator.solar_accuracy_tracker.backfill_actual = MagicMock()

    # Set very recent timestamp (less than 0.01 hours ago)
    now = dt_util.now()
    scheduler._last_solar_power_timestamp = now - timedelta(seconds=1)
    scheduler._last_solar_power_kw = 5.0
    coordinator.data = MagicMock()
    coordinator.data.solar_power_kw = 5.1

    scheduler._backfill_solar_actual()

    # Should NOT call backfill_actual due to small time delta
    coordinator.solar_accuracy_tracker.backfill_actual.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_solar_actual_zero_energy(coordinator):
    """Test _backfill_solar_actual skips backfill for zero energy."""
    from homeassistant.util import dt as dt_util

    scheduler = TickScheduler(coordinator)

    # Mock tracker
    coordinator.solar_accuracy_tracker = MagicMock()
    coordinator.solar_accuracy_tracker.backfill_actual = MagicMock()

    # Set timestamp 1 hour ago with zero power
    now = dt_util.now()
    scheduler._last_solar_power_timestamp = now - timedelta(hours=1)
    scheduler._last_solar_power_kw = 0.0
    coordinator.data = MagicMock()
    coordinator.data.solar_power_kw = 0.0

    scheduler._backfill_solar_actual()

    # Should NOT call backfill_actual due to zero energy
    coordinator.solar_accuracy_tracker.backfill_actual.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_solar_actual_success(coordinator):
    """Test _backfill_solar_actual successfully backfills energy."""
    from homeassistant.util import dt as dt_util

    scheduler = TickScheduler(coordinator)

    # Mock tracker
    coordinator.solar_accuracy_tracker = MagicMock()
    coordinator.solar_accuracy_tracker.backfill_actual = MagicMock()

    # Set timestamp 30 minutes ago with solar power
    now = dt_util.now()
    scheduler._last_solar_power_timestamp = now - timedelta(minutes=30)
    scheduler._last_solar_power_kw = 4.0
    coordinator.data = MagicMock()
    coordinator.data.solar_power_kw = 6.0

    scheduler._backfill_solar_actual()

    # Should call backfill_actual with calculated energy
    coordinator.solar_accuracy_tracker.backfill_actual.assert_called_once()
    # Verify energy calculation: avg_power * time = (4+6)/2 * 0.5 = 2.5 kWh
    args = coordinator.solar_accuracy_tracker.backfill_actual.call_args[0]
    assert args[1] > 2.0  # Energy should be around 2.5 kWh

    # Verify timestamp updated
    assert scheduler._last_solar_power_kw == 6.0


@pytest.mark.asyncio
async def test_backfill_solar_actual_skips_during_boost(coordinator):
    """Boost charging periods should not contaminate solar accuracy backfill."""
    from homeassistant.util import dt as dt_util

    scheduler = TickScheduler(coordinator)

    coordinator.solar_accuracy_tracker = MagicMock()
    coordinator.solar_accuracy_tracker.backfill_actual = MagicMock()

    now = dt_util.now()
    scheduler._last_solar_power_timestamp = now - timedelta(minutes=30)
    scheduler._last_solar_power_kw = 4.0
    coordinator.data = MagicMock()
    coordinator.data.solar_power_kw = 6.0
    coordinator.data.boost_charge_active = True

    scheduler._backfill_solar_actual()

    coordinator.solar_accuracy_tracker.backfill_actual.assert_not_called()


@pytest.mark.asyncio
async def test_handle_medium_tick_with_solar_tracker(coordinator):
    """Test handle_medium_tick updates solar bias metrics."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock dependencies
    coordinator._entity_monitor = MagicMock()
    coordinator._entity_monitor.read_all_external_state = MagicMock()
    coordinator._state_machine = MagicMock()
    coordinator._state_machine.startup_grace_until = None  # No grace period

    # Mock solar accuracy tracker
    coordinator.solar_accuracy_tracker = MagicMock()
    coordinator.solar_accuracy_tracker.metrics = MagicMock()
    coordinator.solar_accuracy_tracker.metrics.to_dict = MagicMock(
        return_value={"bias": 0.1}
    )
    coordinator.solar_accuracy_tracker.metrics.accuracy = 0.95

    coordinator.data = MagicMock()
    coordinator.data.solar_bias_metrics = None
    coordinator.data.solar_forecast_accuracy = None

    scheduler.handle_medium_tick(now)

    # Should update solar bias metrics
    assert coordinator.data.solar_bias_metrics == {"bias": 0.1}
    assert coordinator.data.solar_forecast_accuracy == 0.95


@pytest.mark.asyncio
async def test_handle_slow_tick_no_entity_monitor(coordinator):
    """Test handle_slow_tick handles missing entity monitor."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock dependencies
    coordinator._entity_monitor = None
    coordinator._computation_engine = None

    # Mock backfill method
    scheduler._backfill_solar_actual = MagicMock()

    # Should not raise
    scheduler.handle_slow_tick(now)


@pytest.mark.asyncio
async def test_handle_midnight_reset_no_learning_orchestrator(coordinator):
    """Test handle_midnight_reset handles missing learning orchestrator."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock dependencies
    coordinator.data = MagicMock()
    coordinator.data.grid_import_cost = 100.0
    coordinator.data.grid_export_revenue = 50.0
    coordinator.data.battery_savings = 25.0
    coordinator.data.battery_charge_cost = 10.0
    coordinator.data.target_reached_today = True
    coordinator._learning_orchestrator = None
    coordinator.notify_listeners = MagicMock()

    scheduler.handle_midnight_reset(now)

    # Should still reset values
    assert coordinator.data.grid_import_cost == 0.0
    assert coordinator.data.grid_export_revenue == 0.0
    assert coordinator.data.battery_savings == 0.0
    assert coordinator.data.battery_charge_cost == 0.0
    assert coordinator.data.target_reached_today is False
    coordinator.notify_listeners.assert_called_once()


@pytest.mark.asyncio
async def test_handle_fast_tick_no_entity_monitor(coordinator):
    """Test handle_fast_tick handles missing entity monitor."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock dependencies
    coordinator._entity_monitor = None
    coordinator._cost_tracker = MagicMock()
    coordinator._cost_tracker.accumulate_costs = MagicMock()
    coordinator._evaluation_dispatcher = MagicMock()
    coordinator._evaluation_dispatcher.maybe_trigger_on_startup_ready = MagicMock()
    coordinator._evaluation_dispatcher.on_fast_tick = MagicMock()
    coordinator._state_machine = MagicMock()
    coordinator._state_machine.startup_grace_until = None  # No grace period
    coordinator.data = MagicMock()
    coordinator.data.automation_ready = True

    # Should not raise
    scheduler.handle_fast_tick(now)

    # Should still accumulate costs and dispatch evaluation
    coordinator._cost_tracker.accumulate_costs.assert_called_once()
    coordinator._evaluation_dispatcher.on_fast_tick.assert_called_once_with(now)


@pytest.mark.asyncio
async def test_handle_medium_tick_no_entity_monitor(coordinator):
    """Test handle_medium_tick handles missing entity monitor."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock dependencies
    coordinator._entity_monitor = None
    coordinator._state_machine = MagicMock()
    coordinator._state_machine.startup_grace_until = None  # No grace period
    coordinator._learning_orchestrator = None
    coordinator.data = MagicMock()

    # Should not raise
    scheduler.handle_medium_tick(now)

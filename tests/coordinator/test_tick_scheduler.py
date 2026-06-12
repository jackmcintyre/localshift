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
async def test_handle_medium_tick_drives_solar_backfill(coordinator):
    """Solar backfill now runs on the medium (5-min) tick, not the slow tick."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    coordinator._entity_monitor = MagicMock()
    coordinator._entity_monitor.read_all_external_state = MagicMock()
    coordinator._entity_monitor.check_entity_health = MagicMock()
    coordinator._state_machine = MagicMock()
    coordinator._state_machine.startup_grace_until = None
    coordinator._learning_orchestrator = None
    coordinator._computation_engine = None
    coordinator.data = MagicMock()

    scheduler._backfill_solar_actual = MagicMock()

    scheduler.handle_medium_tick(now)

    scheduler._backfill_solar_actual.assert_called_once()


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

    # Backfill now runs on the medium tick, not the slow tick.
    scheduler._backfill_solar_actual = MagicMock()

    scheduler.handle_slow_tick(now)

    # Should schedule async task for weather refresh
    assert coordinator.hass.async_create_task.call_count >= 1
    # Slow tick no longer backfills solar accuracy (moved to medium tick)
    scheduler._backfill_solar_actual.assert_not_called()


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
    # Issue #868: the daily energy accumulators must reset on this path too.
    coordinator.data.grid_import_kwh_today = 5.0
    coordinator.data.grid_export_kwh_today = 3.0
    coordinator.data.grid_to_battery_kwh_today = 2.0
    coordinator.data.soc_gain_during_grid_charge_kwh_today = 1.5
    coordinator.data.export_while_battery_not_full_kwh_today = 1.0
    coordinator._learning_orchestrator = MagicMock()
    coordinator._learning_orchestrator.handle_midnight_reset = MagicMock()
    coordinator.notify_listeners = MagicMock()

    scheduler.handle_midnight_reset(now)

    assert coordinator.data.grid_import_cost == 0.0
    assert coordinator.data.grid_export_revenue == 0.0
    assert coordinator.data.battery_savings == 0.0
    assert coordinator.data.battery_charge_cost == 0.0
    assert coordinator.data.target_reached_today is False
    assert coordinator.data.grid_import_kwh_today == 0.0
    assert coordinator.data.grid_export_kwh_today == 0.0
    assert coordinator.data.grid_to_battery_kwh_today == 0.0
    assert coordinator.data.soc_gain_during_grid_charge_kwh_today == 0.0
    assert coordinator.data.export_while_battery_not_full_kwh_today == 0.0
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


def _ha_tz():
    from homeassistant.util import dt as dt_util

    return dt_util.now().tzinfo


def _t(hour, minute, second=0):
    """A tz-aware datetime in the HA default timezone."""
    return datetime(2026, 6, 11, hour, minute, second, tzinfo=_ha_tz())


def _expected_period(ts):
    """Floor a timestamp to its local :00/:30 period, matching the scheduler."""
    local = ts.astimezone()
    return local.replace(minute=(local.minute // 30) * 30, second=0, microsecond=0)


def _setup_tracker(coordinator, *, power=5.0, boost=False):
    """Wire a mock solar accuracy tracker + data onto the coordinator."""
    tracker = MagicMock()
    tracker.backfill_actual = MagicMock()
    tracker.evict_stale_pendings = MagicMock()
    coordinator.solar_accuracy_tracker = tracker
    coordinator.data = MagicMock()
    coordinator.data.solar_power_kw = power
    coordinator.data.boost_charge_active = boost
    return tracker


def _drive(scheduler, coordinator, ticks):
    """Drive _backfill_solar_actual across a sequence of (time, power[, boost]) ticks.

    Mirrors real 5-min medium-tick cadence so a period accumulates full coverage
    over several ticks (the coverage gate requires >=90% of the 30 min).
    """
    from unittest.mock import patch

    for tick in ticks:
        now = tick[0]
        coordinator.data.solar_power_kw = tick[1]
        if len(tick) > 2:
            coordinator.data.boost_charge_active = tick[2]
        with patch("homeassistant.util.dt.now", return_value=now):
            scheduler._backfill_solar_actual()


def _five_min_ticks(start_h, start_m, end_h, end_m, power):
    """Baseline tick at start, then 5-min ticks through end (inclusive)."""
    ticks = [(_t(start_h, start_m), power)]
    minute = start_m
    hour = start_h
    while (hour, minute) < (end_h, end_m):
        minute += 5
        if minute >= 60:
            minute -= 60
            hour += 1
        ticks.append((_t(hour, minute), power))
    return ticks


@pytest.mark.asyncio
async def test_backfill_attribution_lands_on_producing_period(coordinator):
    """Energy produced 09:30-10:00 lands on the 09:30 period, not the 10:00 one.

    Headline regression: driven at the real 5-min cadence, the fully-covered
    09:30 period is flushed at the first tick after 10:00; the just-started
    10:00 period is NOT.
    """
    scheduler = TickScheduler(coordinator)
    tracker = _setup_tracker(coordinator, power=5.0)

    # Baseline 09:30, then 5-min ticks through 10:00 -> 09:30 period fully covered.
    _drive(scheduler, coordinator, _five_min_ticks(9, 30, 10, 0, 5.0))

    tracker.backfill_actual.assert_called_once()
    call = tracker.backfill_actual.call_args
    assert call.args[0] == _expected_period(_t(9, 30))
    # 5 kW for 30 min = 2.5 kWh.
    assert call.args[1] == pytest.approx(2.5, rel=1e-3)
    assert call.kwargs["is_boost"] is False
    # The 10:00 period has not started accumulating yet.
    assert _expected_period(_t(10, 0)).isoformat() not in scheduler._period_energy_accum


@pytest.mark.asyncio
async def test_backfill_partial_coverage_after_restart_discarded(coordinator):
    """A period whose baseline re-establishes mid-period is discarded, not recorded.

    Headline regression for the partial-coverage poisoning fix: a restart leaves
    the 10:00 period only ~33% covered, so it must NOT be recorded as a full
    actual (which would persist understated into the learning store).
    """
    scheduler = TickScheduler(coordinator)
    tracker = _setup_tracker(coordinator, power=5.0)

    # Baseline lands at 10:20 (post-restart grace), then ticks to past 10:30.
    _drive(
        scheduler,
        coordinator,
        [(_t(10, 20), 5.0), (_t(10, 25), 5.0), (_t(10, 30), 5.0)],
    )

    # The 10:00 period (only 10:20-10:30 covered = 33%) is discarded.
    tracker.backfill_actual.assert_not_called()
    assert _expected_period(_t(10, 0)).isoformat() not in scheduler._period_energy_accum


@pytest.mark.asyncio
async def test_backfill_proration_splits_across_boundary(coordinator):
    """A boundary-spanning interval splits energy/coverage proportionally.

    Baseline 09:56 -> tick 10:02 (6 min): 4 min in the 09:30 period, 2 min in the
    10:00 period. The 10:00 period (still pending) keeps its 1/3 share.
    """
    scheduler = TickScheduler(coordinator)
    tracker = _setup_tracker(coordinator, power=5.0)

    _drive(scheduler, coordinator, [(_t(9, 56), 5.0), (_t(10, 2), 5.0)])

    # total = 5 kW * (6/60)h = 0.5 kWh; 10:00 period overlap = 2 of 6 min -> 1/3.
    later_key = _expected_period(_t(10, 0)).isoformat()
    assert scheduler._period_energy_accum[later_key] == pytest.approx(0.5 / 3, rel=1e-3)
    assert scheduler._period_coverage_accum[later_key] == pytest.approx(120.0, rel=1e-3)
    # The 09:30 period (only 4 min covered) is discarded, not recorded.
    tracker.backfill_actual.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_during_boost_flags_and_consumes_pending(coordinator):
    """Boost intervals keep accumulating; the flush tags the period as boost.

    Regression for the pending-forecast orphan leak: the pending IS consumed
    (backfill_actual called) even during the daily ~3pm boost.
    """
    scheduler = TickScheduler(coordinator)
    tracker = _setup_tracker(coordinator, power=5.0, boost=True)

    _drive(scheduler, coordinator, _five_min_ticks(14, 0, 14, 30, 5.0))

    tracker.backfill_actual.assert_called_once()
    assert tracker.backfill_actual.call_args.kwargs["is_boost"] is True


@pytest.mark.asyncio
async def test_backfill_zero_power_still_flushes(coordinator):
    """Zero-power completed periods still flush; the tracker decides meaningfulness.

    Regression for the removed `current_power > 0.01` gate — declining-to-zero
    dusk periods are no longer dropped at the scheduler level.
    """
    scheduler = TickScheduler(coordinator)
    tracker = _setup_tracker(coordinator, power=0.0)

    _drive(scheduler, coordinator, _five_min_ticks(18, 30, 19, 0, 0.0))

    # The fully-covered dusk period is flushed with ~0 energy (tracker gate decides).
    tracker.backfill_actual.assert_called_once()
    assert tracker.backfill_actual.call_args.args[1] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_backfill_long_interval_rebaselines_without_integrating(coordinator):
    """An interval longer than the cap (15 min) re-baselines without integrating.

    Guards against an event-loop stall smearing one trapezoid across many periods.
    """
    scheduler = TickScheduler(coordinator)
    tracker = _setup_tracker(coordinator, power=5.0)
    scheduler._last_solar_power_timestamp = _t(10, 0)
    scheduler._last_solar_power_kw = 5.0

    _drive(scheduler, coordinator, [(_t(10, 20), 5.0)])  # 20-min gap > 15-min cap

    tracker.backfill_actual.assert_not_called()
    assert scheduler._period_energy_accum == {}
    # Baseline is advanced so the next normal interval integrates cleanly.
    assert scheduler._last_solar_power_timestamp == _t(10, 20)


@pytest.mark.asyncio
async def test_backfill_evicts_stale_pendings(coordinator):
    """evict_stale_pendings runs on each integrating tick."""
    scheduler = TickScheduler(coordinator)
    tracker = _setup_tracker(coordinator, power=5.0)
    scheduler._last_solar_power_timestamp = _t(10, 0)
    scheduler._last_solar_power_kw = 5.0

    _drive(scheduler, coordinator, [(_t(10, 5), 5.0)])

    tracker.evict_stale_pendings.assert_called_once()


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

    # Mock solar accuracy tracker. The medium tick now publishes the richer
    # get_status_dict() payload (metrics + activation/pending/boost status).
    coordinator.solar_accuracy_tracker = MagicMock()
    coordinator.solar_accuracy_tracker.get_status_dict = MagicMock(
        return_value={"bias": 0.1, "correction_active": False}
    )
    coordinator.solar_accuracy_tracker.metrics = MagicMock()
    coordinator.solar_accuracy_tracker.metrics.accuracy = 0.95

    coordinator.data = MagicMock()
    coordinator.data.solar_bias_metrics = None
    coordinator.data.solar_forecast_accuracy = None

    scheduler.handle_medium_tick(now)

    # Should publish the status dict and the accuracy scalar.
    assert coordinator.data.solar_bias_metrics == {
        "bias": 0.1,
        "correction_active": False,
    }
    assert coordinator.data.solar_forecast_accuracy == 0.95


@pytest.mark.asyncio
async def test_handle_slow_tick_no_entity_monitor(coordinator):
    """Test handle_slow_tick handles missing entity monitor."""
    scheduler = TickScheduler(coordinator)
    now = datetime.now()

    # Mock dependencies
    coordinator._entity_monitor = None
    coordinator._computation_engine = None

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

"""State machine half of Issue #1097: never sit in an export that is not exporting.

Two behaviours:

* the guard in ``_evaluate_core`` refuses to enter (or stay in) PROACTIVE_EXPORT
  while Tesla's tariff or the watchdog hold-off says export is unavailable;
* the watchdog in ``_handle_stable_mode`` abandons PROACTIVE_EXPORT when the
  battery is charging off the grid, whatever the grid-charging switch claims.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import (
    EXPORT_INVERSION_CONFIRM_SECONDS,
    EXPORT_INVERSION_HOLDOFF_MINUTES,
    BatteryMode,
)
from custom_components.localshift.state import machine as machine_module
from custom_components.localshift.state.machine import StateMachine

AEDT = timezone(timedelta(hours=11))
# 2026-10-05: export was commanded at 21:03 and the grid charge began ~21:22.
T0 = datetime(2026, 10, 5, 21, 23, tzinfo=AEDT)
TESLA_OFF_PEAK = (
    datetime(2026, 10, 5, 21, 0, tzinfo=AEDT),
    datetime(2026, 10, 6, 4, 0, tzinfo=AEDT),
)


@pytest.fixture
def mock_battery_controller():
    controller = MagicMock()
    controller.set_self_consumption = AsyncMock(return_value=True)
    controller.set_proactive_export = AsyncMock(return_value=True)
    controller.set_proactive_export_reserve = AsyncMock(return_value=True)
    controller.verify_current_state = AsyncMock(return_value=True)
    controller.read_fresh_soc = MagicMock(return_value=None)
    return controller


@pytest.fixture
def mock_notification_service():
    service = MagicMock()
    service.send_transition_notification = AsyncMock()
    service.send_transition_failed_notification = AsyncMock()
    service.send_health_correction_notification = AsyncMock()
    return service


@pytest.fixture
def machine(mock_battery_controller, mock_notification_service, mock_entity_validator):
    return StateMachine(
        mock_battery_controller,
        mock_notification_service,
        lambda key: key == "automation_enabled",  # automation on, dry_run off
        lambda key, default=None: default,
        mock_entity_validator,
    )


@pytest.fixture
def exporting(machine):
    machine._commanded_mode = BatteryMode.PROACTIVE_EXPORT
    machine._proactive_export_reserve = 65.0
    return machine


def _grid_charging(data) -> None:
    """The live reading: battery -5.0 kW, grid +5.56 kW, no solar."""
    data.soc = 72.3
    data.battery_power_kw = -5.0
    data.grid_power_kw = 5.56
    data.solar_power_kw = 0.0


def _at(when: datetime):
    return patch.object(machine_module.dt_util, "now", return_value=when)


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_sighting_arms_but_does_not_abandon(
    exporting, coordinator_data, mock_battery_controller
):
    _grid_charging(coordinator_data)

    with _at(T0):
        abandoned = await exporting._abandon_inverted_export(coordinator_data)

    assert abandoned is False
    assert exporting._commanded_mode == BatteryMode.PROACTIVE_EXPORT
    assert coordinator_data.export_suppressed_until is None
    mock_battery_controller.set_self_consumption.assert_not_awaited()


@pytest.mark.asyncio
async def test_sustained_grid_charge_abandons_export(
    exporting, coordinator_data, mock_battery_controller
):
    _grid_charging(coordinator_data)
    confirmed = T0 + timedelta(seconds=EXPORT_INVERSION_CONFIRM_SECONDS)

    with _at(T0):
        await exporting._abandon_inverted_export(coordinator_data)
    with _at(confirmed):
        abandoned = await exporting._abandon_inverted_export(coordinator_data)

    assert abandoned is True
    assert exporting._commanded_mode == BatteryMode.SELF_CONSUMPTION
    mock_battery_controller.set_self_consumption.assert_awaited_once()
    assert coordinator_data.export_suppressed_until == confirmed + timedelta(
        minutes=EXPORT_INVERSION_HOLDOFF_MINUTES
    )


@pytest.mark.asyncio
async def test_abandoning_lets_the_planner_redecide_immediately(
    exporting, coordinator_data
):
    """Otherwise the stale export decision is held until the next price tick."""
    exporting._last_evaluated_fingerprint = "held-export-decision"
    _grid_charging(coordinator_data)

    with _at(T0):
        await exporting._abandon_inverted_export(coordinator_data)
    with _at(T0 + timedelta(seconds=EXPORT_INVERSION_CONFIRM_SECONDS)):
        await exporting._abandon_inverted_export(coordinator_data)

    assert exporting._last_evaluated_fingerprint is None


@pytest.mark.asyncio
async def test_reading_inside_the_confirm_window_does_not_abandon(
    exporting, coordinator_data
):
    _grid_charging(coordinator_data)

    with _at(T0):
        await exporting._abandon_inverted_export(coordinator_data)
    with _at(T0 + timedelta(seconds=EXPORT_INVERSION_CONFIRM_SECONDS - 1)):
        abandoned = await exporting._abandon_inverted_export(coordinator_data)

    assert abandoned is False
    assert exporting._commanded_mode == BatteryMode.PROACTIVE_EXPORT


@pytest.mark.asyncio
async def test_recovery_resets_the_confirm_window(exporting, coordinator_data):
    """A blip that clears must not count toward a later, separate one."""
    _grid_charging(coordinator_data)
    with _at(T0):
        await exporting._abandon_inverted_export(coordinator_data)

    coordinator_data.battery_power_kw = 3.0  # discharging again
    coordinator_data.grid_power_kw = -2.5
    with _at(T0 + timedelta(seconds=60)):
        await exporting._abandon_inverted_export(coordinator_data)

    _grid_charging(coordinator_data)
    with _at(T0 + timedelta(seconds=EXPORT_INVERSION_CONFIRM_SECONDS + 30)):
        abandoned = await exporting._abandon_inverted_export(coordinator_data)

    assert abandoned is False
    assert exporting._commanded_mode == BatteryMode.PROACTIVE_EXPORT


@pytest.mark.parametrize(
    ("battery_power_kw", "grid_power_kw", "why"),
    [
        (3.1, -2.8, "exporting, as on 2026-09-30 05:25"),
        (0.0, 0.5, "idle with the house on the grid: wasteful, not a grid charge"),
        (-2.0, -1.5, "absorbing surplus solar while the site still exports"),
        (-0.4, 0.6, "below the threshold"),
    ],
)
@pytest.mark.asyncio
async def test_other_power_flows_do_not_abandon(
    exporting, coordinator_data, battery_power_kw, grid_power_kw, why
):
    coordinator_data.battery_power_kw = battery_power_kw
    coordinator_data.grid_power_kw = grid_power_kw

    with _at(T0):
        await exporting._abandon_inverted_export(coordinator_data)
    with _at(T0 + timedelta(minutes=10)):
        abandoned = await exporting._abandon_inverted_export(coordinator_data)

    assert abandoned is False, why
    assert coordinator_data.export_suppressed_until is None


@pytest.mark.parametrize(
    "mode", [BatteryMode.BOOST_CHARGING, BatteryMode.GRID_CHARGING]
)
@pytest.mark.asyncio
async def test_grid_charging_in_a_charge_mode_is_left_alone(
    machine, coordinator_data, mode
):
    machine._commanded_mode = mode
    _grid_charging(coordinator_data)

    with _at(T0):
        await machine._abandon_inverted_export(coordinator_data)
    with _at(T0 + timedelta(minutes=10)):
        abandoned = await machine._abandon_inverted_export(coordinator_data)

    assert abandoned is False
    assert machine._commanded_mode == mode


@pytest.mark.asyncio
async def test_stable_export_runs_the_watchdog_before_the_health_check(
    exporting, coordinator_data
):
    """An abandoned export must not then be 'corrected' back by the health check."""
    exporting._perform_health_check = AsyncMock()
    exporting._step_proactive_export_reserve = AsyncMock(return_value=False)
    _grid_charging(coordinator_data)

    with _at(T0):
        await exporting._handle_stable_mode(coordinator_data)
    with _at(T0 + timedelta(seconds=EXPORT_INVERSION_CONFIRM_SECONDS)):
        await exporting._handle_stable_mode(coordinator_data)

    assert exporting._commanded_mode == BatteryMode.SELF_CONSUMPTION
    assert exporting._perform_health_check.await_count == 1  # first tick only
    assert exporting._step_proactive_export_reserve.await_count == 1


# ---------------------------------------------------------------------------
# Guard in _evaluate_core
# ---------------------------------------------------------------------------


def _route_only(machine: StateMachine) -> None:
    """Replace the two branches so a test can see which one the guard picked."""
    machine._handle_stable_mode = AsyncMock()
    machine._handle_desired_mode_transition = AsyncMock()


@pytest.mark.asyncio
async def test_export_is_not_entered_during_tesla_off_peak(machine, coordinator_data):
    _route_only(machine)
    machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
    coordinator_data.active_mode = BatteryMode.PROACTIVE_EXPORT
    coordinator_data.export_blocked_periods = [TESLA_OFF_PEAK]

    with _at(datetime(2026, 10, 5, 21, 0, 35, tzinfo=AEDT)):
        await machine._evaluate_core(coordinator_data, MagicMock())

    machine._handle_stable_mode.assert_awaited_once()
    machine._handle_desired_mode_transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_running_export_is_left_when_tesla_off_peak_begins(
    machine, coordinator_data
):
    _route_only(machine)
    machine._commanded_mode = BatteryMode.PROACTIVE_EXPORT
    coordinator_data.active_mode = BatteryMode.PROACTIVE_EXPORT
    coordinator_data.export_blocked_periods = [TESLA_OFF_PEAK]
    boundary = datetime(2026, 10, 5, 21, 0, 5, tzinfo=AEDT)

    with _at(boundary):
        await machine._evaluate_core(coordinator_data, MagicMock())

    machine._handle_desired_mode_transition.assert_awaited_once_with(
        coordinator_data, BatteryMode.SELF_CONSUMPTION, boundary
    )
    machine._handle_stable_mode.assert_not_awaited()


@pytest.mark.asyncio
async def test_export_is_entered_during_tesla_on_peak(machine, coordinator_data):
    _route_only(machine)
    machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
    coordinator_data.active_mode = BatteryMode.PROACTIVE_EXPORT
    coordinator_data.export_blocked_periods = [TESLA_OFF_PEAK]
    on_peak = datetime(2026, 10, 6, 5, 30, tzinfo=AEDT)

    with _at(on_peak):
        await machine._evaluate_core(coordinator_data, MagicMock())

    machine._handle_desired_mode_transition.assert_awaited_once_with(
        coordinator_data, BatteryMode.PROACTIVE_EXPORT, on_peak
    )


@pytest.mark.asyncio
async def test_export_is_not_re_entered_during_the_holdoff(machine, coordinator_data):
    """The planner may still be holding its export decision after the watchdog trips."""
    _route_only(machine)
    machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
    coordinator_data.active_mode = BatteryMode.PROACTIVE_EXPORT
    coordinator_data.export_suppressed_until = T0 + timedelta(minutes=60)

    with _at(T0 + timedelta(minutes=59)):
        await machine._evaluate_core(coordinator_data, MagicMock())
    machine._handle_desired_mode_transition.assert_not_awaited()

    with _at(T0 + timedelta(minutes=60)):
        await machine._evaluate_core(coordinator_data, MagicMock())
    machine._handle_desired_mode_transition.assert_awaited_once()


@pytest.mark.asyncio
async def test_other_modes_are_untouched_during_tesla_off_peak(
    machine, coordinator_data
):
    _route_only(machine)
    machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
    coordinator_data.active_mode = BatteryMode.GRID_CHARGING
    coordinator_data.export_blocked_periods = [TESLA_OFF_PEAK]
    night = datetime(2026, 10, 5, 23, 0, tzinfo=AEDT)

    with _at(night):
        await machine._evaluate_core(coordinator_data, MagicMock())

    machine._handle_desired_mode_transition.assert_awaited_once_with(
        coordinator_data, BatteryMode.GRID_CHARGING, night
    )

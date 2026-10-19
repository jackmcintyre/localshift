"""A stopped state machine does nothing more (Issue #1149).

The coordinator checks its own stopped flag before it calls the state machine,
but an evaluation can already be past that check, waiting for the evaluation
lock behind a transition, when the entry is unloaded. The state machine has to
refuse it once the lock comes free.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.state.machine import StateMachine


@pytest.fixture
def controller():
    return MagicMock()


@pytest.fixture
def machine(controller):
    sm = StateMachine(
        controller,
        MagicMock(),
        lambda key: True,
        lambda key, default=None: default,
        MagicMock(),
    )
    sm._evaluate_core = AsyncMock()
    return sm


async def _queue_behind_the_lock(machine, engine, read_state, notify):
    """Hold the lock as a transition would and queue an evaluation behind it."""
    await machine._evaluate_lock.acquire()
    waiter = asyncio.ensure_future(
        machine.evaluate_state_machine(
            CoordinatorData(), engine, read_state_func=read_state, notify_func=notify
        )
    )
    await asyncio.sleep(0)
    assert not waiter.done()
    return waiter


async def test_queued_evaluation_runs_when_not_stopped(machine, controller):
    """Control: the waiter evaluates once the lock comes free."""
    engine, read_state, notify = MagicMock(), MagicMock(), MagicMock()
    waiter = await _queue_behind_the_lock(machine, engine, read_state, notify)

    machine._evaluate_lock.release()
    await waiter

    machine._evaluate_core.assert_awaited_once()
    engine.compute_derived_values.assert_called_once()


async def test_evaluation_waiting_on_the_lock_at_stop_does_nothing(machine, controller):
    engine, read_state, notify = MagicMock(), MagicMock(), MagicMock()
    waiter = await _queue_behind_the_lock(machine, engine, read_state, notify)

    machine.stop()
    machine._evaluate_lock.release()
    await waiter

    machine._evaluate_core.assert_not_awaited()
    engine.compute_derived_values.assert_not_called()
    read_state.assert_not_called()
    notify.assert_not_called()
    assert controller.mock_calls == []


def _correction_is_due(machine) -> None:
    machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
    machine._last_health_correction = None
    machine.is_tesla_override_active = MagicMock(return_value=False)
    # Issue #1208: the listener now honours dry run, which this fixture's
    # all-on switch lambda would otherwise enable.
    machine._get_switch_state = lambda key: key != "dry_run"


async def test_reactive_grid_charging_correction_writes_when_running(machine):
    """Control: the correction does turn the switch off."""
    _correction_is_due(machine)
    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    await machine._reactive_correct_grid_charging(hass, "switch.grid_charging")

    hass.services.async_call.assert_awaited_once()


async def test_reactive_grid_charging_correction_after_stop_writes_nothing(machine):
    _correction_is_due(machine)
    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    machine.stop()
    await machine._reactive_correct_grid_charging(hass, "switch.grid_charging")

    hass.services.async_call.assert_not_called()
    assert machine._last_health_correction is None

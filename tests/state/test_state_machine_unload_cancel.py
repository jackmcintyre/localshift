"""Unload cancels a transition in flight (Issue #1152).

A transition can run for 40-45 s: the grid-charging step with its retries,
validation, the 10 s reserve settle, then one reserve re-write and more
polling. Before #1152 an unload only stopped NEW evaluations, so the old
instance kept writing for the rest of that time, next to the new one. The
decision (Jack, 15 Oct): abort, and let the new instance's health check
reconcile whatever was half-applied. Unload must not wait for it.

These run a real self-consumption transition through the real
BatteryController, service client and validator, against a fake Home
Assistant whose entities take the values written to them. The transition is
held at the post-acceptance reserve settle, which is where the late reserve
re-write in the issue comes from.

The swallowed-failure tripwire (tests/swallow_tripwire.py) is armed for every
test here: the cancel must pass through the transition's ``except Exception``
rather than be logged and reported as a failed transition.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import (
    TESLEMETRY_EXPORT_BATTERY_OK,
    VALIDATION_RESERVE_SETTLE_SECONDS,
    BatteryMode,
)
from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.integration.controller import BatteryController
from custom_components.localshift.state.machine import StateMachine

_ENTITIES = {
    "teslemetry_operation_mode": "select.operation_mode",
    "teslemetry_backup_reserve": "number.backup_reserve",
    "teslemetry_allow_export": "select.allow_export",
    "teslemetry_allow_charging_from_grid": "switch.grid_charging",
    "teslemetry_soc": "sensor.soc",
}
_RESERVE = _ENTITIES["teslemetry_backup_reserve"]


class _FakeHass:
    """Entities that take the values written to them, and a log of writes."""

    def __init__(self) -> None:
        self.values = {
            "select.operation_mode": "autonomous",
            _RESERVE: "80",
            "select.allow_export": TESLEMETRY_EXPORT_BATTERY_OK,
            "switch.grid_charging": "on",
            "sensor.soc": "50",
        }
        self.writes: list[tuple[str, str, object]] = []
        self.states = MagicMock()
        self.states.get = self._get
        self.services = MagicMock()
        self.services.async_call = AsyncMock(side_effect=self._call)

    def _get(self, entity_id):
        if entity_id not in self.values:
            return None
        return MagicMock(state=self.values[entity_id])

    async def _call(self, domain, service, data, blocking=False):
        entity_id = data["entity_id"]
        if domain == "switch":
            value = "on" if service == "turn_on" else "off"
        else:
            value = data["option"] if domain == "select" else data["value"]
        self.values[entity_id] = str(value)
        self.writes.append((domain, entity_id, value))


@pytest.fixture
def hass():
    return _FakeHass()


@pytest.fixture
def machine(hass):
    controller = BatteryController(hass, _ENTITIES.get, lambda _k, d=None: d)
    sm = StateMachine(
        controller,
        MagicMock(),
        lambda key: False,  # dry_run off: the writes are real
        lambda key, default=None: default,
        MagicMock(),
    )

    async def _evaluate_core(data, _engine):
        await sm._execute_mode_transition(data, BatteryMode.SELF_CONSUMPTION)

    sm._evaluate_core = _evaluate_core
    return sm


@pytest.fixture
def settle():
    """Hold the transition at the reserve settle until the test releases it.

    Every other sleep (validation polling, the grid-charging verify) yields
    once and returns, so the transition gets to the settle straight away.
    """
    real_sleep = asyncio.sleep
    reached, release = asyncio.Event(), asyncio.Event()

    async def _sleep(delay, *_args, **_kwargs):
        if delay == VALIDATION_RESERVE_SETTLE_SECONDS:
            reached.set()
            await release.wait()
        else:
            await real_sleep(0)

    with patch("custom_components.localshift.state.validator.asyncio.sleep", _sleep):
        yield reached, release


async def _transition_held_at_the_settle(machine, hass, settle):
    """Start an evaluation and let it run until the reserve settle."""
    reached, _release = settle
    data = CoordinatorData()
    data.soc = 50.0
    task = asyncio.ensure_future(machine.evaluate_state_machine(data, MagicMock()))
    await asyncio.wait_for(reached.wait(), timeout=5)
    assert machine.in_mode_transition
    # The transition's four writes have landed and been accepted.
    assert [w[1] for w in hass.writes] == [
        "switch.grid_charging",
        "select.allow_export",
        "select.operation_mode",
        _RESERVE,
    ]
    return task


async def test_without_unload_the_transition_runs_to_the_reserve_rewrite(
    machine, hass, settle
):
    """Control: the normal path is unchanged.

    Tesla reverts the reserve during the settle and the transition re-writes
    it. This is the late write the unload must prevent.
    """
    task = await _transition_held_at_the_settle(machine, hass, settle)
    accepted_reserve = hass.writes[-1]

    hass.values[_RESERVE] = "80"  # Tesla reverts the reserve
    settle[1].set()
    await asyncio.wait_for(task, timeout=5)

    assert not task.cancelled()
    assert hass.writes[-1] == accepted_reserve
    assert len(hass.writes) == 5
    assert machine._commanded_mode is BatteryMode.SELF_CONSUMPTION
    assert not machine.in_mode_transition


async def test_stop_cancels_a_transition_held_at_the_reserve_settle(
    machine, hass, settle
):
    task = await _transition_held_at_the_settle(machine, hass, settle)
    writes_at_stop = list(hass.writes)

    machine.stop()
    hass.values[_RESERVE] = "80"  # Tesla reverts the reserve, as in the control
    settle[1].set()
    await asyncio.wait({task}, timeout=5)

    assert task.cancelled()
    assert hass.writes == writes_at_stop  # no re-write, nothing else either
    # Nothing the dead instance holds is left set.
    assert not machine.in_mode_transition
    assert not machine._evaluate_lock.locked()


async def test_stop_returns_without_waiting_for_the_transition(machine, hass, settle):
    """stop() only requests the cancel; the settle is never released here."""
    task = await _transition_held_at_the_settle(machine, hass, settle)

    machine.stop()
    assert not task.done()  # stop() itself did not wait

    await asyncio.wait({task}, timeout=5)
    assert task.cancelled()
    assert len(hass.writes) == 4

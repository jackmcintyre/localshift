"""Nothing evaluates on a coordinator after it is unloaded (Issue #1149).

A reload or removal unloads the config entry and builds a new coordinator. The
old one must go quiet: a timer armed in its last seconds, or a task already
queued, must not run the old state machine next to the new one, because both
would be commanding the same battery.

These go through the real ``async_start`` and ``async_stop``. Home Assistant's
timer is replaced by a small fake so the test can move the clock.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.localshift.const import STATE_CHANGE_COALESCE_SECONDS

_SUBSCRIPTIONS = "custom_components.localshift.services.subscription_manager"
_DISPATCHER = "custom_components.localshift.services.evaluation_dispatcher"


class _FakeTimers:
    """Stand-in for ``async_call_later``: arm, cancel, advance the clock."""

    def __init__(self) -> None:
        self._timers: list[dict] = []
        self._elapsed = 0.0

    def call_later(self, _hass, delay, action):
        timer = {"due": self._elapsed + delay, "action": action, "cancelled": False}
        self._timers.append(timer)

        def _cancel() -> None:
            timer["cancelled"] = True

        return _cancel

    @property
    def armed(self) -> list[dict]:
        return [t for t in self._timers if not t["cancelled"]]

    def advance(self, seconds: float) -> int:
        """Move the clock on and fire what is due. Returns how many fired."""
        self._elapsed += seconds
        due = [t for t in self.armed if t["due"] <= self._elapsed]
        for timer in due:
            timer["cancelled"] = True
            timer["action"](dt_util.now())
        return len(due)

    def fire_despite_cancel(self) -> None:
        """Fire every timer, cancelled or not.

        Models the race where the callback was already handed to the event
        loop when the cancel ran.
        """
        for timer in self._timers:
            timer["action"](dt_util.now())


@pytest.mark.usefixtures("mock_storage", "mock_recorder")
class TestUnloadStopsEvaluation:
    @pytest.fixture(autouse=True)
    def _frozen_clock(self):
        frozen = datetime(2026, 10, 13, 16, 40, tzinfo=dt_util.DEFAULT_TIME_ZONE)
        with patch("homeassistant.util.dt.now", return_value=frozen):
            yield

    @pytest.fixture
    def timers(self):
        fake = _FakeTimers()
        with patch(f"{_DISPATCHER}.async_call_later", side_effect=fake.call_later):
            yield fake

    @staticmethod
    async def _start(coordinator):
        """Run the real async_start.

        Returns the state-change handler the coordinator subscribed (the one
        Home Assistant would call on a price or forecast update), the list
        queued tasks land in, and a spy on the state machine's evaluation.
        """
        store = MagicMock()
        store.async_load = AsyncMock(return_value=None)
        store.async_save = AsyncMock()
        with (
            patch(f"{_SUBSCRIPTIONS}.async_track_state_change_event") as track_state,
            patch(f"{_SUBSCRIPTIONS}.async_track_time_interval"),
            patch(f"{_SUBSCRIPTIONS}.async_track_time_change"),
            patch(
                "custom_components.localshift.utils.daily_totals_store.Store",
                return_value=store,
            ),
        ):
            await coordinator.async_start()
        on_state_change = track_state.call_args.args[2]

        # Keep queued tasks instead of dropping them, so the test can run them.
        tasks: list = []
        coordinator.hass.async_create_task = MagicMock(
            side_effect=lambda coro, name=None: tasks.append(coro)
        )
        machine = coordinator._state_machine
        evaluate = AsyncMock(wraps=machine.evaluate_state_machine)
        machine.evaluate_state_machine = evaluate
        coordinator.hass.services.async_call.reset_mock()
        return on_state_change, tasks, evaluate

    @staticmethod
    async def _run(tasks: list) -> None:
        while tasks:
            await tasks.pop(0)

    async def test_armed_timer_evaluates_while_running(self, coordinator, timers):
        """Control: without an unload the armed timer does evaluate."""
        on_state_change, tasks, evaluate = await self._start(coordinator)

        on_state_change(MagicMock())
        assert len(timers.armed) == 1
        assert timers.advance(STATE_CHANGE_COALESCE_SECONDS + 1) == 1
        await self._run(tasks)

        evaluate.assert_awaited_once()

    async def test_unload_cancels_the_armed_coalesce_timer(self, coordinator, timers):
        on_state_change, tasks, evaluate = await self._start(coordinator)
        on_state_change(MagicMock())
        assert len(timers.armed) == 1

        await coordinator.async_stop()

        assert timers.armed == []
        assert timers.advance(STATE_CHANGE_COALESCE_SECONDS + 1) == 0
        await self._run(tasks)
        evaluate.assert_not_awaited()
        coordinator.hass.services.async_call.assert_not_called()

    async def test_callback_that_beats_the_cancel_does_not_evaluate(
        self, coordinator, timers
    ):
        on_state_change, tasks, evaluate = await self._start(coordinator)
        on_state_change(MagicMock())

        await coordinator.async_stop()
        timers.fire_despite_cancel()
        await self._run(tasks)

        evaluate.assert_not_awaited()
        coordinator.hass.services.async_call.assert_not_called()

    async def test_state_change_after_unload_arms_nothing(self, coordinator, timers):
        on_state_change, tasks, evaluate = await self._start(coordinator)

        await coordinator.async_stop()
        on_state_change(MagicMock())

        assert timers.armed == []
        await self._run(tasks)
        evaluate.assert_not_awaited()

    async def test_task_queued_before_unload_does_not_evaluate(
        self, coordinator, timers
    ):
        """A periodic evaluation queued just before the unload, run after it."""
        _, tasks, evaluate = await self._start(coordinator)
        # No price entity, so the stale-price check has nothing to compare.
        coordinator.hass.states.get.return_value = None
        # The dispatch the one-minute tick makes once startup grace is over.
        coordinator._evaluation_dispatcher.on_fast_tick(dt_util.now())
        assert tasks

        await coordinator.async_stop()
        await self._run(tasks)

        evaluate.assert_not_awaited()
        coordinator.hass.services.async_call.assert_not_called()

    async def test_solcast_retry_after_unload_does_not_evaluate(
        self, coordinator, timers
    ):
        """The startup Solcast retry sleeps, then evaluates. It is not
        cancelled on unload, so the evaluation itself has to refuse."""
        _, tasks, evaluate = await self._start(coordinator)
        bootstrapper = coordinator._forecast_bootstrapper
        # Out of retries: the next pass computes and evaluates straight away.
        bootstrapper._retry_count = bootstrapper._max_retries

        await coordinator.async_stop()
        await bootstrapper.wait_for_solcast_and_compute()
        await self._run(tasks)

        evaluate.assert_not_awaited()
        coordinator.hass.services.async_call.assert_not_called()

    async def test_watch_listener_is_not_subscribed_again_after_unload(
        self, coordinator, timers
    ):
        """An evaluation that was mid-flight at unload ends by maintaining the
        physical-response watch. It must not subscribe a new listener."""
        await self._start(coordinator)
        watch = MagicMock()
        watch.timeout_at = datetime(
            2026, 10, 13, 16, 45, tzinfo=dt_util.DEFAULT_TIME_ZONE
        )
        coordinator.data.physical_response_watch = watch

        await coordinator.async_stop()
        with patch(
            "custom_components.localshift.coordinator.coordinator"
            ".async_track_state_change_event"
        ) as track:
            coordinator._check_physical_response_watch()

        track.assert_not_called()
        assert coordinator._unsub_battery_power_listener is None

    async def test_stop_before_start_is_safe(self, coordinator):
        await coordinator.async_stop()

        await coordinator._evaluate_state_machine()

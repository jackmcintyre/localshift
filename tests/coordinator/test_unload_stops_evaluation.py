"""Nothing evaluates on a coordinator after it is unloaded (Issue #1149).

A reload or removal unloads the config entry and builds a new coordinator. The
old one must go quiet: a timer armed in its last seconds, a task already
queued, or an evaluation waiting its turn must not run the old state machine
next to the new one, because both would be commanding the same battery.

The other half matters as much: a coordinator that is still the live one must
never be left stopped. Home Assistant shutting down, or a platform that
refuses to unload, must leave it evaluating.

These go through the real ``async_start``, ``async_stop`` and
``async_unload_entry``. Home Assistant's timer is replaced by a small fake so
the test can move the clock.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.localshift import _async_options_updated, async_unload_entry
from custom_components.localshift.const import STATE_CHANGE_COALESCE_SECONDS

_SUBSCRIPTIONS = "custom_components.localshift.services.subscription_manager"
_DISPATCHER = "custom_components.localshift.services.evaluation_dispatcher"
_COORDINATOR_LOG = "custom_components.localshift.coordinator.coordinator"


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


class _Base:
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
        return on_state_change, tasks, evaluate

    @staticmethod
    async def _run(tasks: list) -> None:
        while tasks:
            await tasks.pop(0)

    async def _assert_still_evaluates(self, on_state_change, tasks, evaluate, timers):
        """A price update still reaches the state machine."""
        on_state_change(MagicMock())
        assert timers.advance(STATE_CHANGE_COALESCE_SECONDS + 1) == 1
        await self._run(tasks)
        evaluate.assert_awaited_once()


@pytest.mark.usefixtures("mock_storage", "mock_recorder")
class TestUnloadStopsEvaluation(_Base):
    async def test_armed_timer_evaluates_while_running(self, coordinator, timers):
        """Control: without an unload the armed timer does evaluate."""
        on_state_change, tasks, evaluate = await self._start(coordinator)

        await self._assert_still_evaluates(on_state_change, tasks, evaluate, timers)

    async def test_unload_cancels_the_armed_coalesce_timer(self, coordinator, timers):
        on_state_change, tasks, evaluate = await self._start(coordinator)
        on_state_change(MagicMock())
        assert len(timers.armed) == 1

        await coordinator.async_stop()

        assert timers.armed == []
        assert timers.advance(STATE_CHANGE_COALESCE_SECONDS + 1) == 0
        await self._run(tasks)
        evaluate.assert_not_awaited()

    async def test_callback_that_beats_the_cancel_does_not_evaluate(
        self, coordinator, timers
    ):
        on_state_change, tasks, evaluate = await self._start(coordinator)
        on_state_change(MagicMock())

        await coordinator.async_stop()
        timers.fire_despite_cancel()
        await self._run(tasks)

        evaluate.assert_not_awaited()

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

    async def test_evaluation_waiting_on_the_lock_at_unload_does_nothing(
        self, coordinator, timers
    ):
        """A tick during a transition queues an evaluation behind the lock.

        It is already past the coordinator's stopped check when the unload
        happens, so the state machine has to refuse it when the lock frees.
        """
        _, _, evaluate = await self._start(coordinator)
        machine = coordinator._state_machine
        machine._evaluate_core = AsyncMock()
        controller = MagicMock()
        machine._battery_controller = controller
        engine = coordinator._computation_engine

        await machine._evaluate_lock.acquire()  # the transition in flight
        waiter = asyncio.ensure_future(coordinator._evaluate_state_machine())
        await asyncio.sleep(0)
        evaluate.assert_awaited_once()
        assert not waiter.done()

        with patch.object(engine, "compute_derived_values") as compute:
            await coordinator.async_stop()
            machine._evaluate_lock.release()
            await waiter

        machine._evaluate_core.assert_not_awaited()
        compute.assert_not_called()
        assert controller.mock_calls == []

    async def test_unload_aborts_the_transition_in_flight_without_waiting(
        self, coordinator, timers
    ):
        """Issue #1152: the transition holding the lock at unload is cancelled.

        The unload returns while the transition is still paused, and the old
        instance writes nothing after that. Whatever it left half-applied, the
        new instance's health check corrects.
        """
        await self._start(coordinator)
        hass, entry = coordinator.hass, coordinator.entry
        entry.runtime_data = coordinator
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        machine = coordinator._state_machine
        paused, resume = asyncio.Event(), asyncio.Event()
        late_write = AsyncMock()

        async def _transition(_data, _engine):
            paused.set()
            await resume.wait()  # the 10 s reserve settle, say
            await late_write()  # the post-acceptance reserve re-write

        machine._evaluate_core = _transition
        with patch.object(coordinator._computation_engine, "compute_derived_values"):
            in_flight = asyncio.ensure_future(coordinator._evaluate_state_machine())
            await asyncio.wait_for(paused.wait(), timeout=5)

            # Never resumed before this returns: the unload does not wait.
            unloaded = await asyncio.wait_for(
                async_unload_entry(hass, entry), timeout=5
            )
            resume.set()
            await asyncio.wait({in_flight}, timeout=5)

        assert unloaded is True
        assert in_flight.cancelled()
        late_write.assert_not_awaited()

    async def test_solcast_retry_that_wakes_after_unload_ends_the_chain(
        self, coordinator, timers, caplog
    ):
        """The startup Solcast retry sleeps, then recomputes and evaluates.
        Nothing cancels it on unload, so it has to stop itself."""
        _, tasks, evaluate = await self._start(coordinator)
        bootstrapper = coordinator._forecast_bootstrapper
        bootstrapper._retry_delay = timedelta(0)
        # Out of retries: the pass after the sleep would compute and evaluate.
        bootstrapper._retry_count = bootstrapper._max_retries
        engine = coordinator._computation_engine

        await coordinator.async_stop()
        with (
            patch.object(engine, "compute_derived_values") as compute,
            caplog.at_level("INFO"),
        ):
            await bootstrapper._retry_solcast_check()
            await self._run(tasks)

        compute.assert_not_called()
        evaluate.assert_not_awaited()
        assert "Solcast data not available" not in caplog.text

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


@pytest.mark.usefixtures("mock_storage", "mock_recorder")
class TestOptionsUpdateAroundUnload(_Base):
    """Home Assistant removes the options listener only after the unload
    returns, so an update can still reach the old coordinator."""

    async def test_options_update_while_running_re_arms_and_evaluates(
        self, coordinator, timers
    ):
        """Control."""
        _, _, evaluate = await self._start(coordinator)
        coordinator.entry.runtime_data = coordinator

        with patch(f"{_SUBSCRIPTIONS}.async_track_time_change") as track:
            await _async_options_updated(coordinator.hass, coordinator.entry)

        track.assert_called_once()
        evaluate.assert_awaited_once()

    async def test_options_update_after_unload_subscribes_nothing(
        self, coordinator, timers
    ):
        """Otherwise the old instance would send a stale daily summary every
        day until Home Assistant restarts: nothing is left to cancel it."""
        _, _, evaluate = await self._start(coordinator)
        coordinator.entry.runtime_data = coordinator

        await coordinator.async_stop()
        with patch(f"{_SUBSCRIPTIONS}.async_track_time_change") as track:
            await _async_options_updated(coordinator.hass, coordinator.entry)

        track.assert_not_called()
        assert coordinator._subscription_manager._unsub_daily_summary is None
        evaluate.assert_not_awaited()


@pytest.mark.usefixtures("mock_storage", "mock_recorder")
class TestALiveCoordinatorIsNeverLeftStopped(_Base):
    async def test_home_assistant_shutdown_does_not_stop_the_coordinator(
        self, coordinator, timers
    ):
        """Shutdown saves; it does not unload. If the shutdown is abandoned
        or slow, the battery must still be under control."""
        on_state_change, tasks, evaluate = await self._start(coordinator)

        await coordinator._handle_homeassistant_stop(MagicMock())

        assert coordinator._stopped is False
        assert coordinator._state_machine._stopped is False
        await self._assert_still_evaluates(on_state_change, tasks, evaluate, timers)

    async def test_platforms_that_refuse_to_unload_leave_it_running(
        self, coordinator, timers
    ):
        """Home Assistant keeps the entry when the unload fails. Stopping the
        coordinator first would leave it loaded with no battery control."""
        on_state_change, tasks, evaluate = await self._start(coordinator)
        hass, entry = coordinator.hass, coordinator.entry
        entry.runtime_data = coordinator
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

        assert await async_unload_entry(hass, entry) is False

        assert coordinator._stopped is False
        await self._assert_still_evaluates(on_state_change, tasks, evaluate, timers)

    async def test_platform_unload_that_raises_leaves_it_running(
        self, coordinator, timers
    ):
        on_state_change, tasks, evaluate = await self._start(coordinator)
        hass, entry = coordinator.hass, coordinator.entry
        entry.runtime_data = coordinator
        hass.config_entries.async_unload_platforms = AsyncMock(
            side_effect=RuntimeError("platform teardown failed")
        )

        with pytest.raises(RuntimeError):
            await async_unload_entry(hass, entry)

        assert coordinator._stopped is False
        await self._assert_still_evaluates(on_state_change, tasks, evaluate, timers)

    async def test_successful_unload_stops_it_after_the_platforms(
        self, coordinator, timers
    ):
        on_state_change, tasks, evaluate = await self._start(coordinator)
        hass, entry = coordinator.hass, coordinator.entry
        entry.runtime_data = coordinator
        stopped_during_platform_unload: list[bool] = []

        async def _unload_platforms(*_args):
            stopped_during_platform_unload.append(coordinator._stopped)
            return True

        hass.config_entries.async_unload_platforms = AsyncMock(
            side_effect=_unload_platforms
        )
        on_state_change(MagicMock())

        assert await async_unload_entry(hass, entry) is True

        assert stopped_during_platform_unload == [False]
        assert coordinator._stopped is True
        assert timers.advance(STATE_CHANGE_COALESCE_SECONDS + 1) == 0
        await self._run(tasks)
        evaluate.assert_not_awaited()


@pytest.mark.usefixtures("mock_storage", "mock_recorder")
class TestAStoppedCoordinatorIsVisibleInTheLog(_Base):
    async def test_first_refused_evaluation_warns_once(
        self, coordinator, timers, caplog
    ):
        """A coordinator that is stopped but still being asked to evaluate is
        either a harmless straggler or a live instance wrongly stopped. One
        warning makes the second case findable; more would be noise."""
        await self._start(coordinator)
        await coordinator.async_stop()

        with caplog.at_level("DEBUG", logger=_COORDINATOR_LOG):
            for _ in range(3):
                await coordinator._evaluate_state_machine()

        refusals = [
            r
            for r in caplog.records
            if r.name == _COORDINATOR_LOG and "coordinator is stopped" in r.getMessage()
        ]
        assert [r.levelname for r in refusals] == ["WARNING", "DEBUG", "DEBUG"]

    async def test_stopping_is_logged_before_the_final_save(
        self, coordinator, timers, caplog
    ):
        await self._start(coordinator)
        logged_when_saving: list[bool] = []
        coordinator._daily_totals_store._store.async_save.side_effect = (
            lambda *_a, **_k: logged_when_saving.append(
                "LocalShift coordinator stopping" in caplog.text
            )
        )

        with caplog.at_level("INFO", logger=_COORDINATOR_LOG):
            await coordinator.async_stop()

        assert logged_when_saving == [True]

"""A failed async_start leaves nothing subscribed behind it (Issue #1148).

Home Assistant does not unload an entry whose setup raised, so async_stop never
runs for it. If async_start fails after it has registered the shutdown listener
and the timers, those outlive the coordinator: the timers keep firing on it, and
at the next shutdown it saves its stores over the files a later, working
coordinator writes. The setup must tear them down itself, and must not save,
because a start that failed part way may not have loaded every store.

These go through the real ``async_setup_entry`` and ``async_start``, with a
late step of the start made to raise.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift import async_setup_entry

_SUBSCRIPTIONS = "custom_components.localshift.services.subscription_manager"


class _Unsubs:
    """Hand out one unsubscribe double per subscription and remember them."""

    def __init__(self) -> None:
        self.handles: list[MagicMock] = []

    def subscribe(self, *_args, **_kwargs) -> MagicMock:
        handle = MagicMock()
        self.handles.append(handle)
        return handle


@pytest.mark.usefixtures("mock_storage", "mock_recorder")
class TestFailedStartTeardown:
    async def _setup(self, coordinator, *, fail: bool):
        """Run async_setup_entry on ``coordinator``, failing late if asked.

        Returns the timer unsubscribes, the shutdown-listener unsubscribe, and
        the daily totals store double.
        """
        hass = coordinator.hass
        entry = coordinator.entry
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        stop_unsub = MagicMock()
        hass.bus.async_listen_once = MagicMock(return_value=stop_unsub)
        store = MagicMock()
        store.async_load = AsyncMock(return_value=None)
        store.async_save = AsyncMock()
        unsubs = _Unsubs()
        # The weather refresh is one of the last steps of async_start, after
        # the shutdown listener and every timer are in place.
        late_step = AsyncMock(
            side_effect=RuntimeError("late failure") if fail else None
        )
        with (
            patch(
                "custom_components.localshift.LocalShiftCoordinator",
                return_value=coordinator,
            ),
            patch(
                f"{_SUBSCRIPTIONS}.async_track_state_change_event",
                side_effect=unsubs.subscribe,
            ),
            patch(
                f"{_SUBSCRIPTIONS}.async_track_time_interval",
                side_effect=unsubs.subscribe,
            ),
            patch(
                f"{_SUBSCRIPTIONS}.async_track_time_change",
                side_effect=unsubs.subscribe,
            ),
            patch(
                "custom_components.localshift.utils.daily_totals_store.Store",
                return_value=store,
            ),
            patch.object(coordinator, "_refresh_weather_forecast", late_step),
        ):
            if fail:
                with pytest.raises(RuntimeError, match="late failure"):
                    await async_setup_entry(hass, entry)
            else:
                assert await async_setup_entry(hass, entry) is True
        late_step.assert_awaited_once()
        return unsubs.handles, stop_unsub, store

    async def test_failed_start_removes_the_shutdown_listener(self, coordinator):
        _, stop_unsub, _ = await self._setup(coordinator, fail=True)

        stop_unsub.assert_called_once()
        assert coordinator._unsub_homeassistant_stop is None

    async def test_failed_start_removes_every_timer(self, coordinator):
        handles, _, _ = await self._setup(coordinator, fail=True)

        # State changes, three tick intervals, midnight, daily summary and
        # the learning save.
        assert len(handles) == 7
        for handle in handles:
            handle.assert_called_once()

    async def test_failed_start_stops_the_coordinator(self, coordinator):
        await self._setup(coordinator, fail=True)

        assert coordinator._stopped is True

    async def test_failed_start_saves_nothing(self, coordinator):
        """A part-loaded start must not write its stores back."""
        _, _, store = await self._setup(coordinator, fail=True)

        store.async_save.assert_not_awaited()

    async def test_successful_start_keeps_listener_and_timers(self, coordinator):
        """Control: a start that completes leaves everything subscribed."""
        handles, stop_unsub, _ = await self._setup(coordinator, fail=False)

        stop_unsub.assert_not_called()
        assert coordinator._unsub_homeassistant_stop is stop_unsub
        assert len(handles) == 7
        for handle in handles:
            handle.assert_not_called()
        assert coordinator._stopped is False

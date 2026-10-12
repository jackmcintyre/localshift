"""Coordinator wiring for the daily totals snapshot (Issue #1102).

The store itself is covered in tests/utils/test_daily_totals_store.py. These
tests pin *when* the coordinator restores and saves: restore during
async_start, save on the existing 5-minute cadence, on unload, and on Home
Assistant shutdown (which does not unload config entries).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.util import dt as dt_util

_SUBSCRIPTIONS = "custom_components.localshift.services.subscription_manager"


def _wire(coordinator) -> MagicMock:
    """Give the coordinator a totals store double and a cost tracker."""
    coordinator._daily_totals_store = MagicMock()
    coordinator._daily_totals_store.async_save = AsyncMock()
    coordinator._cost_tracker = MagicMock()
    return coordinator._daily_totals_store


class TestDailyTotalsSave:
    async def test_periodic_save_covers_daily_totals(self, coordinator):
        """The 5-minute learning-save path also writes the day's totals."""
        store = _wire(coordinator)

        await coordinator._save_learning_data()

        store.async_save.assert_awaited_once_with(
            coordinator.data, coordinator._cost_tracker
        )

    async def test_daily_totals_saved_even_if_telemetry_save_raises(self, coordinator):
        """A failing learning save must not cost the day's totals."""
        store = _wire(coordinator)
        coordinator._decision_telemetry = MagicMock()
        coordinator._decision_telemetry.async_save_all = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        with pytest.raises(RuntimeError):
            await coordinator._save_learning_data()

        store.async_save.assert_awaited_once()

    async def test_async_stop_saves_daily_totals(self, coordinator):
        """Unload and reload persist the totals before the coordinator goes."""
        store = _wire(coordinator)

        await coordinator.async_stop()

        store.async_save.assert_awaited_once_with(
            coordinator.data, coordinator._cost_tracker
        )

    async def test_save_tolerates_failed_startup(self, coordinator):
        """No store or tracker yet (async_start never completed): a no-op."""
        assert coordinator._daily_totals_store is None
        assert coordinator._cost_tracker is None

        await coordinator._save_learning_data()
        await coordinator.async_stop()

    async def test_homeassistant_stop_saves_daily_totals(self, coordinator):
        """Shutdown does not unload entries, so it needs its own save."""
        store = _wire(coordinator)
        coordinator._unsub_homeassistant_stop = MagicMock()

        await coordinator._handle_homeassistant_stop(MagicMock())

        store.async_save.assert_awaited_once_with(
            coordinator.data, coordinator._cost_tracker
        )
        # The one-shot listener has already removed itself.
        assert coordinator._unsub_homeassistant_stop is None

    async def test_async_stop_removes_homeassistant_stop_listener(self, coordinator):
        unsub = MagicMock()
        coordinator._unsub_homeassistant_stop = unsub

        await coordinator.async_stop()

        unsub.assert_called_once()
        assert coordinator._unsub_homeassistant_stop is None


@pytest.mark.usefixtures("mock_storage", "mock_recorder")
class TestDailyTotalsRestoreOnStart:
    async def _start(self, coordinator, stored) -> list[str]:
        """Run async_start with ``stored`` in the totals store.

        Returns the order in which the restore and the timer subscriptions
        happened.
        """
        order: list[str] = []

        async def _load():
            order.append("restore")
            return stored

        store = MagicMock()
        store.async_load = AsyncMock(side_effect=_load)
        store.async_save = AsyncMock()
        with (
            patch(f"{_SUBSCRIPTIONS}.async_track_state_change_event"),
            patch(
                f"{_SUBSCRIPTIONS}.async_track_time_interval",
                side_effect=lambda *a, **k: order.append("timers") or MagicMock(),
            ),
            patch(f"{_SUBSCRIPTIONS}.async_track_time_change"),
            patch(
                "custom_components.localshift.utils.daily_totals_store.Store",
                return_value=store,
            ),
        ):
            await coordinator.async_start()
        return order

    async def test_async_start_restores_todays_totals(self, coordinator):
        stored = {
            "date": dt_util.now().date().isoformat(),
            "totals": {"grid_import_cost": 1.14, "grid_import_kwh_today": 4.2},
        }

        await self._start(coordinator, stored)

        assert coordinator.data.grid_import_cost == pytest.approx(1.14)
        assert coordinator.data.grid_import_kwh_today == pytest.approx(4.2)

    async def test_restore_happens_before_any_tick_can_fire(self, coordinator):
        """A tick ahead of the restore would be overwritten by it."""
        order = await self._start(coordinator, None)

        assert order[0] == "restore"
        assert "timers" in order

    async def test_async_start_survives_corrupt_snapshot(self, coordinator):
        await self._start(coordinator, {"date": "garbage", "totals": []})

        assert coordinator._cost_tracker is not None
        assert coordinator.data.grid_import_cost == 0.0

    async def test_async_start_registers_homeassistant_stop_listener(self, coordinator):
        await self._start(coordinator, None)

        coordinator.hass.bus.async_listen_once.assert_called_once_with(
            EVENT_HOMEASSISTANT_STOP, coordinator._handle_homeassistant_stop
        )
        assert coordinator._unsub_homeassistant_stop is not None

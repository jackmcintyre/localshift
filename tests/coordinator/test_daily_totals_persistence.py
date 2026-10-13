"""Coordinator wiring for the daily totals snapshot (Issue #1102).

The store itself is covered in tests/utils/test_daily_totals_store.py. These
tests pin *when* the coordinator restores and saves: restore during
async_start, save on the existing 5-minute cadence, on unload, and on Home
Assistant shutdown (which does not unload config entries).

Issue #1142 widened the unload and shutdown saves to every store, and added
the demand-window entry record to the snapshot.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
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


# ─── Issue #1142: one final save for every store, and the entry record ──────

_COORDINATOR = "custom_components.localshift.coordinator.coordinator"
_STORE_NAMES = [
    "daily totals",
    "decision telemetry",
    "solar accuracy",
    "forecast history",
    "accuracy metrics",
    "weather correlation",
]
_FIVE_MINUTE_STORES = _STORE_NAMES[:3]
_SLOWER_STORES = _STORE_NAMES[3:]


def _wire_all(coordinator) -> dict[str, AsyncMock]:
    """Give the coordinator a double for every store the final save covers."""
    totals = _wire(coordinator)
    coordinator._decision_telemetry = MagicMock()
    coordinator._decision_telemetry.async_save_all = AsyncMock()
    coordinator.solar_accuracy_tracker = MagicMock()
    coordinator.solar_accuracy_tracker.async_save = AsyncMock()
    engine = MagicMock()
    engine.async_save_forecast_history = AsyncMock()
    engine.async_save_accuracy_metrics = AsyncMock()
    engine.async_save_weather_correlation = AsyncMock()
    coordinator._computation_engine = engine
    coordinator._forecast_stores_loaded = True
    return {
        "daily totals": totals.async_save,
        "decision telemetry": coordinator._decision_telemetry.async_save_all,
        "solar accuracy": coordinator.solar_accuracy_tracker.async_save,
        "forecast history": engine.async_save_forecast_history,
        "accuracy metrics": engine.async_save_accuracy_metrics,
        "weather correlation": engine.async_save_weather_correlation,
    }


def _final_save_warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Final save")
    ]


class TestFinalSave:
    def test_store_names_match_the_wiring(self, coordinator):
        assert list(_wire_all(coordinator)) == _STORE_NAMES

    async def test_shutdown_saves_every_store(self, coordinator):
        saves = _wire_all(coordinator)

        await coordinator._handle_homeassistant_stop(MagicMock())

        for name, save in saves.items():
            assert save.await_count == 1, name
        saves["forecast history"].assert_awaited_once_with(coordinator.data)
        saves["accuracy metrics"].assert_awaited_once_with(coordinator.data)

    async def test_unload_saves_every_store(self, coordinator):
        saves = _wire_all(coordinator)

        await coordinator.async_stop()

        for name, save in saves.items():
            assert save.await_count == 1, name

    async def test_daily_totals_are_saved_first(self, coordinator):
        saves = _wire_all(coordinator)
        order: list[str] = []
        for name, save in saves.items():
            save.side_effect = lambda *a, _name=name, **k: order.append(_name)

        await coordinator._handle_homeassistant_stop(MagicMock())

        assert order == _STORE_NAMES

    @pytest.mark.parametrize("failing", _STORE_NAMES)
    async def test_every_store_is_attempted_when_one_raises(
        self, coordinator, caplog, failing
    ):
        saves = _wire_all(coordinator)
        saves[failing].side_effect = RuntimeError("boom")

        with caplog.at_level("WARNING"):
            await coordinator._handle_homeassistant_stop(MagicMock())

        for name, save in saves.items():
            assert save.await_count == 1, name
        warnings = _final_save_warnings(caplog)
        assert len(warnings) == 1
        assert failing in warnings[0]
        assert "boom" in warnings[0]

    async def test_every_store_is_attempted_when_all_of_them_raise(
        self, coordinator, caplog
    ):
        saves = _wire_all(coordinator)
        for save in saves.values():
            save.side_effect = RuntimeError("boom")

        with caplog.at_level("WARNING"):
            await coordinator._handle_homeassistant_stop(MagicMock())

        for name, save in saves.items():
            assert save.await_count == 1, name
        assert len(_final_save_warnings(caplog)) == len(_STORE_NAMES)

    @pytest.mark.parametrize("hanging", _STORE_NAMES)
    async def test_a_store_that_hangs_cannot_hang_shutdown(
        self, coordinator, caplog, hanging
    ):
        saves = _wire_all(coordinator)

        async def _never(*_args, **_kwargs):
            await asyncio.Event().wait()

        saves[hanging].side_effect = _never

        with (
            patch(f"{_COORDINATOR}.FINAL_SAVE_STORE_TIMEOUT", 0.01),
            caplog.at_level("WARNING"),
        ):
            await asyncio.wait_for(
                coordinator._handle_homeassistant_stop(MagicMock()), timeout=5
            )

        for name, save in saves.items():
            assert save.await_count == 1, name
        warnings = _final_save_warnings(caplog)
        assert len(warnings) == 1
        assert hanging in warnings[0]

    async def test_slower_stores_are_skipped_until_they_have_been_loaded(
        self, coordinator
    ):
        """A stop mid-startup must not write empty data over what is stored."""
        saves = _wire_all(coordinator)
        coordinator._forecast_stores_loaded = False

        await coordinator._handle_homeassistant_stop(MagicMock())

        for name in _FIVE_MINUTE_STORES:
            assert saves[name].await_count == 1, name
        for name in _SLOWER_STORES:
            assert saves[name].await_count == 0, name

    async def test_final_save_tolerates_failed_startup(self, coordinator):
        """Nothing wired at all (async_start never ran): a no-op."""
        assert coordinator._computation_engine is None

        await coordinator._handle_homeassistant_stop(MagicMock())

    async def test_shutdown_and_unload_together_do_not_interleave(self, coordinator):
        """Both can fire. Each run is a whole pass; the second waits its turn."""
        saves = _wire_all(coordinator)
        events: list[str] = []

        def _recorder(name):
            async def _save(*_args, **_kwargs):
                events.append(f"start {name}")
                await asyncio.sleep(0)
                events.append(f"end {name}")

            return _save

        for name, save in saves.items():
            save.side_effect = _recorder(name)

        await asyncio.gather(
            coordinator._handle_homeassistant_stop(MagicMock()),
            coordinator.async_stop(),
        )

        one_pass = [
            f"{edge} {name}" for name in _STORE_NAMES for edge in ("start", "end")
        ]
        assert events == one_pass + one_pass

    async def test_periodic_save_is_unchanged(self, coordinator):
        """The 5-minute save keeps its own three stores; the others stay on
        their own, slower ticks."""
        saves = _wire_all(coordinator)

        await coordinator._save_learning_data()

        for name in _FIVE_MINUTE_STORES:
            assert saves[name].await_count == 1, name
        for name in _SLOWER_STORES:
            assert saves[name].await_count == 0, name


@pytest.mark.usefixtures("mock_storage", "mock_recorder")
class TestRealStartPath:
    """Through the real async_start: restore, first evaluation, shutdown."""

    _start = TestDailyTotalsRestoreOnStart._start

    @staticmethod
    def _snapshot(at) -> dict:
        return {
            "date": at.date().isoformat(),
            "totals": {"grid_import_cost": 1.14},
            "dw_entry": {
                "dw_entry_actual_date": at.date().isoformat(),
                "dw_entry_actual_at": at.isoformat(),
                "dw_entry_actual_soc_pct": 64.0,
                "dw_entry_actual_target_pct": 95.0,
                "dw_entry_actual_shortfall_pct": 31.0,
            },
        }

    async def test_entry_record_survives_start_and_the_first_evaluation(
        self, coordinator, caplog
    ):
        at = dt_util.now()

        with caplog.at_level("INFO"):
            await self._start(coordinator, self._snapshot(at))
            # The date latch runs here with a blank reset date.
            coordinator._compute_derived_values()

        data = coordinator.data
        assert data.last_target_reset_date == at.date()
        assert data.grid_import_cost == pytest.approx(1.14)
        assert data.dw_entry_actual_soc_pct == 64.0
        assert data.dw_entry_actual_at == at
        assert data.dw_entry_actual_target_pct == 95.0
        assert data.dw_entry_actual_shortfall_pct == 31.0
        assert data.dw_entry_actual_date == at.date()
        assert "ENTERED UNDER TARGET" not in caplog.text
        assert "entry SOC not recorded" not in caplog.text

    async def test_yesterdays_entry_record_is_not_restored(self, coordinator):
        yesterday = dt_util.now() - timedelta(days=1)

        await self._start(coordinator, self._snapshot(yesterday))
        coordinator._compute_derived_values()

        assert coordinator.data.grid_import_cost == 0.0
        assert coordinator.data.dw_entry_actual_soc_pct is None
        assert coordinator.data.dw_entry_actual_date is None

    async def test_old_snapshot_without_the_record_still_restores_totals(
        self, coordinator
    ):
        snapshot = self._snapshot(dt_util.now())
        del snapshot["dw_entry"]

        await self._start(coordinator, snapshot)
        coordinator._compute_derived_values()

        assert coordinator.data.grid_import_cost == pytest.approx(1.14)
        assert coordinator.data.dw_entry_actual_soc_pct is None

    async def test_shutdown_event_after_a_real_start_saves_every_store(
        self, coordinator
    ):
        """The listener async_start registered, driven as the bus would."""
        at = dt_util.now()
        await self._start(coordinator, self._snapshot(at))
        listener = coordinator.hass.bus.async_listen_once.call_args.args[1]
        engine = coordinator._computation_engine
        totals_store = coordinator._daily_totals_store._store

        with (
            patch.object(
                coordinator._decision_telemetry, "async_save_all", AsyncMock()
            ) as telemetry_save,
            patch.object(
                coordinator.solar_accuracy_tracker, "async_save", AsyncMock()
            ) as solar_save,
            patch.object(
                engine, "async_save_forecast_history", AsyncMock()
            ) as history_save,
            patch.object(
                engine, "async_save_accuracy_metrics", AsyncMock()
            ) as metrics_save,
            patch.object(
                engine, "async_save_weather_correlation", AsyncMock()
            ) as weather_save,
        ):
            await listener(MagicMock())

        telemetry_save.assert_awaited_once_with()
        solar_save.assert_awaited_once_with()
        history_save.assert_awaited_once_with(coordinator.data)
        metrics_save.assert_awaited_once_with(coordinator.data)
        weather_save.assert_awaited_once_with()
        # The restored entry record goes back out with the totals.
        saved = totals_store.async_save.await_args.args[0]
        assert saved["totals"]["grid_import_cost"] == pytest.approx(1.14)
        assert saved["dw_entry"] == self._snapshot(at)["dw_entry"]

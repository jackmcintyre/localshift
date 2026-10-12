"""Tests for DailyTotalsStore (Issue #1102).

The day-to-date cost and energy totals live in memory on CoordinatorData. A
Home Assistant restart used to start them again from zero, dropping the day's
figures until midnight. The store snapshots them, dated, and restores the
snapshot at startup only when it belongs to the current local day.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.utils.costs import (
    DAILY_ACCUMULATOR_FIELDS,
    CostTracker,
)
from custom_components.localshift.utils.daily_totals_store import DailyTotalsStore

SYDNEY = ZoneInfo("Australia/Sydney")
NOW_PATH = "custom_components.localshift.utils.costs.dt_util.now"


def _at(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=SYDNEY)


def _totals(**overrides: float) -> dict[str, float]:
    totals = {
        "grid_import_cost": 1.14,
        "grid_export_revenue": 0.34,
        "battery_savings": 0.80,
        "battery_charge_cost": 0.25,
        "grid_import_kwh_today": 4.2,
        "grid_export_kwh_today": 3.1,
        "solar_kwh_today": 9.5,
        "grid_to_battery_kwh_today": 1.5,
        "soc_gain_during_grid_charge_kwh_today": 1.3,
        "export_while_battery_not_full_kwh_today": 0.4,
    }
    totals.update(overrides)
    return totals


@pytest.fixture
def mock_store():
    """HA Store double: empty on load, no-op on save."""
    store = MagicMock()
    store.async_load = AsyncMock(return_value=None)
    store.async_save = AsyncMock()
    return store


@pytest.fixture
def totals_store(mock_store):
    with patch(
        "custom_components.localshift.utils.daily_totals_store.Store",
        return_value=mock_store,
    ):
        return DailyTotalsStore(MagicMock(), "entry123")


@pytest.fixture
def data():
    return CoordinatorData()


def _tracker(now: datetime) -> CostTracker:
    with patch(NOW_PATH, return_value=now):
        return CostTracker(MagicMock())


def _assert_all_zero(data: CoordinatorData) -> None:
    for key in DAILY_ACCUMULATOR_FIELDS:
        assert getattr(data, key) == 0.0, key


def test_snapshot_fixture_covers_every_persisted_field():
    """The fixture above must not drift from the persisted field list."""
    assert set(_totals()) == set(DAILY_ACCUMULATOR_FIELDS)


def test_store_key_is_per_entry():
    with patch(
        "custom_components.localshift.utils.daily_totals_store.Store"
    ) as store_cls:
        hass = MagicMock()
        DailyTotalsStore(hass, "entry123")
    store_cls.assert_called_once_with(
        hass, version=1, key="localshift.daily_totals.entry123"
    )


class TestRestore:
    async def test_restores_same_day_snapshot(self, totals_store, mock_store, data):
        """The 13 Oct case: restart at 07:30 keeps the day's $1.14."""
        now = _at(2026, 10, 13, 7, 30)
        tracker = _tracker(now)
        mock_store.async_load.return_value = {
            "date": "2026-10-13",
            "totals": _totals(),
        }

        with patch(NOW_PATH, return_value=now):
            restored = await totals_store.async_restore(data, tracker)

        assert restored is True
        for key, value in _totals().items():
            assert getattr(data, key) == pytest.approx(value), key
        assert data.grid_import_cost - data.grid_export_revenue == pytest.approx(0.80)

    async def test_discards_previous_day_snapshot(
        self, totals_store, mock_store, data, caplog
    ):
        """A restart spanning midnight must not leak yesterday into today."""
        now = _at(2026, 10, 14, 0, 2)
        tracker = _tracker(now)
        mock_store.async_load.return_value = {
            "date": "2026-10-13",
            "totals": _totals(),
        }

        with patch(NOW_PATH, return_value=now), caplog.at_level(logging.INFO):
            restored = await totals_store.async_restore(data, tracker)

        assert restored is False
        _assert_all_zero(data)
        assert "2026-10-13" in caplog.text

    async def test_discards_future_dated_snapshot(self, totals_store, mock_store, data):
        """A clock that moved backwards is treated like any other date mismatch."""
        now = _at(2026, 10, 13, 9, 0)
        tracker = _tracker(now)
        mock_store.async_load.return_value = {
            "date": "2026-10-14",
            "totals": _totals(),
        }

        with patch(NOW_PATH, return_value=now):
            assert await totals_store.async_restore(data, tracker) is False
        _assert_all_zero(data)

    @pytest.mark.parametrize(
        ("saved_date", "now"),
        [
            # DST start in Sydney (4 Oct 2026, 02:00 -> 03:00): a 23-hour day.
            ("2026-10-04", _at(2026, 10, 4, 3, 30)),
            # DST end in Sydney (5 Apr 2026, 03:00 -> 02:00): a 25-hour day,
            # 02:30 happens twice.
            ("2026-04-05", datetime(2026, 4, 5, 2, 30, tzinfo=SYDNEY, fold=1)),
        ],
    )
    async def test_restores_across_dst_change_within_the_day(
        self, totals_store, mock_store, data, saved_date, now
    ):
        """The comparison is on the local calendar date, not on elapsed hours."""
        tracker = _tracker(now)
        mock_store.async_load.return_value = {"date": saved_date, "totals": _totals()}

        with patch(NOW_PATH, return_value=now):
            assert await totals_store.async_restore(data, tracker) is True
        assert data.grid_import_cost == pytest.approx(1.14)

    async def test_missing_snapshot_starts_at_zero(
        self, totals_store, mock_store, data
    ):
        now = _at(2026, 10, 13)
        mock_store.async_load.return_value = None

        with patch(NOW_PATH, return_value=now):
            restored = await totals_store.async_restore(data, _tracker(now))

        assert restored is False
        _assert_all_zero(data)

    @pytest.mark.parametrize(
        "stored",
        [
            "not a dict",
            ["2026-10-13"],
            {},
            {"date": "2026-10-13"},
            {"totals": _totals()},
            {"date": "not-a-date", "totals": _totals()},
            {"date": 20261013, "totals": _totals()},
            {"date": "2026-10-13", "totals": "nope"},
            {"date": "2026-10-13", "totals": _totals(grid_import_cost="1.14")},
            {"date": "2026-10-13", "totals": _totals(grid_import_cost=None)},
            {"date": "2026-10-13", "totals": _totals(grid_import_cost=True)},
            {"date": "2026-10-13", "totals": _totals(solar_kwh_today=float("nan"))},
            {"date": "2026-10-13", "totals": _totals(solar_kwh_today=float("inf"))},
            {"date": "2026-10-13", "totals": _totals(grid_import_kwh_today=-1.0)},
        ],
    )
    async def test_corrupt_snapshot_starts_at_zero(
        self, totals_store, mock_store, data, caplog, stored
    ):
        """Any unusable snapshot is dropped whole: no partial restore, no raise."""
        now = _at(2026, 10, 13)
        mock_store.async_load.return_value = stored

        with patch(NOW_PATH, return_value=now), caplog.at_level(logging.WARNING):
            restored = await totals_store.async_restore(data, _tracker(now))

        assert restored is False
        _assert_all_zero(data)
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1

    async def test_load_error_starts_at_zero(
        self, totals_store, mock_store, data, caplog
    ):
        """A storage read failure must never propagate into setup."""
        now = _at(2026, 10, 13)
        mock_store.async_load.side_effect = OSError("disk gone")

        with patch(NOW_PATH, return_value=now), caplog.at_level(logging.WARNING):
            restored = await totals_store.async_restore(data, _tracker(now))

        assert restored is False
        _assert_all_zero(data)
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1

    async def test_field_absent_from_snapshot_restores_as_zero(
        self, totals_store, mock_store, data
    ):
        """A snapshot written before a total existed still restores the rest."""
        now = _at(2026, 10, 13)
        totals = _totals()
        del totals["solar_kwh_today"]
        mock_store.async_load.return_value = {"date": "2026-10-13", "totals": totals}

        with patch(NOW_PATH, return_value=now):
            assert await totals_store.async_restore(data, _tracker(now)) is True

        assert data.solar_kwh_today == 0.0
        assert data.grid_import_cost == pytest.approx(1.14)

    async def test_restore_leaves_control_inputs_alone(
        self, totals_store, mock_store, data
    ):
        """Only display totals are restored; the target latch is not."""
        now = _at(2026, 10, 13)
        mock_store.async_load.return_value = {
            "date": "2026-10-13",
            "totals": _totals(),
            "target_reached_today": True,
        }

        with patch(NOW_PATH, return_value=now):
            await totals_store.async_restore(data, _tracker(now))

        assert data.target_reached_today is False


class TestFirstTickAfterRestore:
    async def test_first_tick_adds_exactly_one_minute(
        self, totals_store, mock_store, data
    ):
        """No double-count and no outage gap booked as energy.

        Accumulation integrates instantaneous power over one minute per tick,
        so the first tick after a restore adds one minute regardless of how
        long Home Assistant was down.
        """
        now = _at(2026, 10, 13, 7, 30)
        tracker = _tracker(now)
        mock_store.async_load.return_value = {
            "date": "2026-10-13",
            "totals": _totals(),
        }
        data.grid_power_kw = 3.0
        data.battery_power_kw = 0.0
        data.solar_power_kw = 0.0
        data.general_price = 0.30
        data.soc = 50.0

        with patch(NOW_PATH, return_value=now):
            await totals_store.async_restore(data, tracker)
            tracker.accumulate_costs(data)

        assert data.grid_import_cost == pytest.approx(1.14 + 3.0 * 0.30 / 60)
        assert data.grid_import_kwh_today == pytest.approx(4.2 + 3.0 / 60)
        assert data.grid_export_revenue == pytest.approx(0.34)

    async def test_soc_baseline_is_reseeded(self, totals_store, mock_store, data):
        """SOC that moved during the outage is not booked to the first tick."""
        now = _at(2026, 10, 13, 7, 30)
        tracker = _tracker(now)
        # A stale baseline, as if the tracker had already sampled before restore.
        tracker._last_soc_pct = 40.0
        mock_store.async_load.return_value = {
            "date": "2026-10-13",
            "totals": _totals(),
        }
        # Grid-charging, with SOC 30 points above the stale baseline.
        data.grid_power_kw = 5.0
        data.battery_power_kw = -5.0
        data.general_price = 0.10
        data.soc = 70.0

        with patch(NOW_PATH, return_value=now):
            await totals_store.async_restore(data, tracker)
            tracker.accumulate_costs(data)

        assert data.soc_gain_during_grid_charge_kwh_today == pytest.approx(1.3)
        assert tracker._last_soc_pct == 70.0

    async def test_midnight_reset_still_clears_restored_totals(
        self, totals_store, mock_store, data
    ):
        now = _at(2026, 10, 13, 23, 50)
        tracker = _tracker(now)
        mock_store.async_load.return_value = {
            "date": "2026-10-13",
            "totals": _totals(),
        }
        with patch(NOW_PATH, return_value=now):
            await totals_store.async_restore(data, tracker)

        with patch(NOW_PATH, return_value=_at(2026, 10, 14, 0, 0)):
            tracker.reset_daily_accumulators(data)

        _assert_all_zero(data)
        assert tracker.totals_date == date(2026, 10, 14)

    async def test_missed_midnight_does_not_carry_restored_totals_forward(
        self, totals_store, mock_store, data
    ):
        """Restored at 23:59:59, midnight timer armed too late to fire.

        The first tick of the new day must start the day from zero rather than
        keep adding to yesterday's restored totals.
        """
        before = _at(2026, 10, 13, 23, 59)
        tracker = _tracker(before)
        mock_store.async_load.return_value = {
            "date": "2026-10-13",
            "totals": _totals(),
        }
        with patch(NOW_PATH, return_value=before):
            await totals_store.async_restore(data, tracker)

        data.grid_power_kw = 3.0
        data.general_price = 0.30
        with patch(NOW_PATH, return_value=_at(2026, 10, 14, 0, 1)):
            tracker.accumulate_costs(data)

        assert data.grid_import_cost == pytest.approx(3.0 * 0.30 / 60)
        assert data.grid_export_revenue == 0.0
        assert tracker.totals_date == date(2026, 10, 14)


class TestSave:
    async def test_saves_dated_snapshot(self, totals_store, mock_store, data):
        now = _at(2026, 10, 13, 7, 29)
        tracker = _tracker(now)
        for key, value in _totals().items():
            setattr(data, key, value)

        await totals_store.async_save(data, tracker)

        mock_store.async_save.assert_awaited_once_with({
            "date": "2026-10-13",
            "totals": _totals(),
        })

    async def test_snapshot_is_dated_by_the_day_the_totals_belong_to(
        self, totals_store, mock_store, data
    ):
        """A save that lands just after midnight, ahead of the reset.

        The totals are still yesterday's, so the snapshot must say so; dating
        it from the wall clock would let a restart restore them into today.
        """
        tracker = _tracker(_at(2026, 10, 13, 23, 58))
        data.grid_import_cost = 1.14

        with patch(NOW_PATH, return_value=_at(2026, 10, 14, 0, 0)):
            await totals_store.async_save(data, tracker)

        saved = mock_store.async_save.await_args.args[0]
        assert saved["date"] == "2026-10-13"

    async def test_save_error_is_swallowed(
        self, totals_store, mock_store, data, caplog
    ):
        """A failed write must not break shutdown or the other periodic saves."""
        mock_store.async_save.side_effect = OSError("disk full")

        with caplog.at_level(logging.WARNING):
            await totals_store.async_save(data, _tracker(_at(2026, 10, 13)))

        assert "disk full" in caplog.text

    async def test_round_trip(self, totals_store, mock_store, data):
        """What is saved before a restart is what comes back after it."""
        now = _at(2026, 10, 13, 7, 29)
        for key, value in _totals().items():
            setattr(data, key, value)
        await totals_store.async_save(data, _tracker(now))
        mock_store.async_load.return_value = mock_store.async_save.await_args.args[0]

        fresh = CoordinatorData()
        later = _at(2026, 10, 13, 7, 31)
        with patch(NOW_PATH, return_value=later):
            assert await totals_store.async_restore(fresh, _tracker(later)) is True

        for key, value in _totals().items():
            assert getattr(fresh, key) == pytest.approx(value), key


class TestPersistedFieldsMatchTheDailyReset:
    def test_every_persisted_field_is_cleared_by_the_daily_reset(self, data):
        """A total that is restored but never reset would grow without bound."""
        tracker = _tracker(_at(2026, 10, 13))
        for key in DAILY_ACCUMULATOR_FIELDS:
            setattr(data, key, 5.0)

        tracker.reset_daily_accumulators(data)

        _assert_all_zero(data)

"""Tests for DailyTotalsStore (Issue #1102).

The day-to-date cost and energy totals live in memory on CoordinatorData. A
Home Assistant restart used to start them again from zero, dropping the day's
figures until midnight. The store snapshots them, dated, and restores the
snapshot at startup only when it belongs to the current local day.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.utils import daily_totals_store
from custom_components.localshift.utils.costs import (
    DAILY_ACCUMULATOR_FIELDS,
    DAILY_DOLLAR_FIELDS,
    DAILY_KWH_FIELDS,
    MAX_DAILY_TOTAL_DOLLARS,
    MAX_DAILY_TOTAL_KWH,
    CostTracker,
)
from custom_components.localshift.utils.daily_totals_store import (
    DW_ENTRY_RECORD_FIELDS,
    DW_ENTRY_SNAPSHOT_KEY,
    DailyTotalsStore,
)

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

    @pytest.mark.parametrize("latch_before", [True, False])
    async def test_restore_never_writes_the_target_latch(
        self, totals_store, mock_store, data, latch_before
    ):
        """target_reached_today is a control input and is not restorable.

        Keys the restore does not know are ignored on purpose, at the top level
        and inside ``totals``, rather than rejecting the snapshot: a snapshot
        written by a newer version (one more total) must still restore the
        totals this version does know after a downgrade. Ignored means ignored:
        a ``target_reached_today`` smuggled in either place must not reach the
        latch, in either direction, and no unknown key becomes an attribute.
        """
        now = _at(2026, 10, 13)
        data.target_reached_today = latch_before
        smuggled = not latch_before
        totals: dict = _totals()
        totals["target_reached_today"] = smuggled
        totals["some_future_total_kwh"] = 3.0
        mock_store.async_load.return_value = {
            "date": "2026-10-13",
            "totals": totals,
            "target_reached_today": smuggled,
            "some_future_key": 1,
        }

        with patch(NOW_PATH, return_value=now):
            restored = await totals_store.async_restore(data, _tracker(now))

        assert restored is True
        assert data.grid_import_cost == pytest.approx(1.14)
        assert data.target_reached_today is latch_before
        assert not hasattr(data, "some_future_total_kwh")
        assert not hasattr(data, "some_future_key")


class TestSignedDollarTotals:
    """Dollar totals are signed; kWh totals are not.

    Buy and feed-in prices are stored raw (state/reader.py) and both go
    negative in this system, so each of the four dollar totals can legitimately
    end a day below zero. Rejecting that would discard the whole snapshot on
    exactly the days the totals are most interesting.
    """

    def test_field_groups_partition_the_persisted_fields(self):
        assert set(DAILY_DOLLAR_FIELDS) | set(DAILY_KWH_FIELDS) == set(
            DAILY_ACCUMULATOR_FIELDS
        )
        assert not set(DAILY_DOLLAR_FIELDS) & set(DAILY_KWH_FIELDS)
        assert len(DAILY_ACCUMULATOR_FIELDS) == 10

    @pytest.mark.parametrize("field", DAILY_DOLLAR_FIELDS)
    async def test_negative_dollar_total_round_trips(
        self, totals_store, mock_store, data, field
    ):
        """Saved negative, restarted, restored negative, with the rest intact."""
        now = _at(2026, 10, 13, 13, 0)
        expected = _totals(**{field: -2.37})
        for key, value in expected.items():
            setattr(data, key, value)
        await totals_store.async_save(data, _tracker(now))
        mock_store.async_load.return_value = mock_store.async_save.await_args.args[0]

        fresh = CoordinatorData()
        later = _at(2026, 10, 13, 13, 2)
        with patch(NOW_PATH, return_value=later):
            assert await totals_store.async_restore(fresh, _tracker(later)) is True

        for key, value in expected.items():
            assert getattr(fresh, key) == pytest.approx(value), key
        assert getattr(fresh, field) < 0

    @pytest.mark.parametrize("field", DAILY_KWH_FIELDS)
    async def test_negative_kwh_total_is_rejected(
        self, totals_store, mock_store, data, field
    ):
        """Every kWh total only ever has non-negative amounts added to it."""
        now = _at(2026, 10, 13)
        mock_store.async_load.return_value = {
            "date": "2026-10-13",
            "totals": _totals(**{field: -0.5}),
        }

        with patch(NOW_PATH, return_value=now):
            assert await totals_store.async_restore(data, _tracker(now)) is False
        _assert_all_zero(data)


class TestImplausibleTotals:
    """A value no single day could produce rejects the snapshot whole."""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("grid_import_cost", 1e300),
            ("grid_export_revenue", -1e300),
            ("battery_savings", MAX_DAILY_TOTAL_DOLLARS * 1.01),
            ("battery_charge_cost", -MAX_DAILY_TOTAL_DOLLARS * 1.01),
            ("solar_kwh_today", 1e300),
            ("grid_import_kwh_today", MAX_DAILY_TOTAL_KWH * 1.01),
            # An int too large for a float must be rejected, not overflow.
            ("grid_export_kwh_today", 10**400),
            ("grid_import_cost", -(10**400)),
        ],
        ids=[
            "dollars-1e300",
            "dollars-minus-1e300",
            "dollars-just-over",
            "dollars-just-under-negative",
            "kwh-1e300",
            "kwh-just-over",
            "kwh-oversized-int",
            "dollars-oversized-negative-int",
        ],
    )
    async def test_out_of_range_total_is_rejected(
        self, totals_store, mock_store, data, caplog, field, value
    ):
        now = _at(2026, 10, 13)
        mock_store.async_load.return_value = {
            "date": "2026-10-13",
            "totals": _totals(**{field: value}),
        }

        with patch(NOW_PATH, return_value=now), caplog.at_level(logging.WARNING):
            assert await totals_store.async_restore(data, _tracker(now)) is False

        _assert_all_zero(data)
        assert f"invalid {field}" in caplog.text

    def test_oversized_int_raises_value_error(self, data):
        """The documented failure type, not an OverflowError from math."""
        now = _at(2026, 10, 13)
        tracker = _tracker(now)
        snapshot = {"date": "2026-10-13", "totals": _totals(solar_kwh_today=10**400)}
        with patch(NOW_PATH, return_value=now), pytest.raises(ValueError):
            tracker.restore_daily_totals(data, snapshot)

    async def test_totals_at_the_bounds_are_accepted(
        self, totals_store, mock_store, data
    ):
        now = _at(2026, 10, 13)
        mock_store.async_load.return_value = {
            "date": "2026-10-13",
            "totals": _totals(
                grid_import_cost=MAX_DAILY_TOTAL_DOLLARS,
                grid_export_revenue=-MAX_DAILY_TOTAL_DOLLARS,
                solar_kwh_today=MAX_DAILY_TOTAL_KWH,
                grid_export_kwh_today=0,
            ),
        }

        with patch(NOW_PATH, return_value=now):
            assert await totals_store.async_restore(data, _tracker(now)) is True
        assert data.grid_export_revenue == -MAX_DAILY_TOTAL_DOLLARS
        assert data.solar_kwh_today == MAX_DAILY_TOTAL_KWH


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

    @pytest.mark.parametrize("latch", [True, False])
    def test_rollover_guard_does_not_touch_the_target_latch(self, data, latch):
        """The guard in accumulate_costs resets totals only.

        target_reached_today is a control input (engine/price_calculator.py).
        Its date-change clear belongs to computation_engine and to the midnight
        handler; the cost tracker's own tick must never write it.
        """
        tracker = _tracker(_at(2026, 10, 13, 23, 59))
        for key in DAILY_ACCUMULATOR_FIELDS:
            setattr(data, key, 5.0)
        data.target_reached_today = latch
        # Idle: nothing accumulates on the tick, so the totals show the reset.
        data.grid_power_kw = 0.0
        data.battery_power_kw = 0.0
        data.solar_power_kw = 0.0

        with patch(NOW_PATH, return_value=_at(2026, 10, 14, 0, 1)):
            tracker.accumulate_costs(data)

        _assert_all_zero(data)
        assert tracker.totals_date == date(2026, 10, 14)
        assert data.target_reached_today is latch

    def test_rollover_guard_drops_the_soc_baseline(self, data):
        """A stale pre-midnight baseline must not book its gain to the new day."""
        tracker = _tracker(_at(2026, 10, 13, 23, 59))
        tracker._last_soc_pct = 40.0
        data.grid_power_kw = 5.0
        data.battery_power_kw = -5.0
        data.general_price = 0.10
        data.soc = 70.0

        with patch(NOW_PATH, return_value=_at(2026, 10, 14, 0, 1)):
            tracker.accumulate_costs(data)

        assert data.soc_gain_during_grid_charge_kwh_today == 0.0
        assert tracker._last_soc_pct == 70.0

    def test_midnight_handler_still_clears_the_target_latch(self, data):
        """What midnight does is unchanged: totals, baseline and the latch.

        Driven through the real TickScheduler.handle_midnight_reset with a real
        CostTracker, so this pins the production path rather than the method.
        """
        from custom_components.localshift.coordinator.tick_scheduler import (
            TickScheduler,
        )

        tracker = _tracker(_at(2026, 10, 13, 23, 59))
        tracker._last_soc_pct = 63.0
        for key in DAILY_ACCUMULATOR_FIELDS:
            setattr(data, key, 5.0)
        data.target_reached_today = True
        coordinator = MagicMock()
        coordinator.data = data
        coordinator.cost_tracker = tracker
        midnight = _at(2026, 10, 14, 0, 0)

        with patch(NOW_PATH, return_value=midnight):
            TickScheduler(coordinator).handle_midnight_reset(midnight)

        _assert_all_zero(data)
        assert data.target_reached_today is False
        assert tracker._last_soc_pct is None
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
            # Issue #1142: no demand-window entry recorded yet at 07:29.
            DW_ENTRY_SNAPSHOT_KEY: None,
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

    # Fields the daily reset clears that are deliberately NOT persisted. Adding
    # a name here is a decision that the field may be lost on restart; say why.
    _RESET_BUT_NOT_PERSISTED = frozenset({
        # Control input: engine/price_calculator.py reads it to set the
        # effective cheap price for pre-charge. Restoring it would change
        # charging behaviour after a restart (Issue #1102, out of scope).
        "target_reached_today",
    })

    def test_every_field_the_daily_reset_clears_is_persisted(self):
        """The reverse guard: a new accumulator cannot silently miss the store.

        Every scalar field on CoordinatorData is set away from its default, the
        daily reset is run, and whatever it changed must be either persisted or
        on the commented exclusion list above. (The midnight handler zeroes two
        more counters itself, anticipated_transitions_today and
        anticipation_corrections_today; they are not the cost tracker's and are
        outside this guard.)
        """
        data = CoordinatorData()
        before: dict[str, object] = {}
        for field in dataclasses.fields(CoordinatorData):
            current = getattr(data, field.name)
            if isinstance(current, bool):
                marked: object = not current
            elif isinstance(current, (int, float)):
                marked = current + 7
            else:
                continue
            setattr(data, field.name, marked)
            before[field.name] = marked
        assert set(DAILY_ACCUMULATOR_FIELDS) <= set(before)
        assert self._RESET_BUT_NOT_PERSISTED <= set(before)

        _tracker(_at(2026, 10, 13)).reset_daily_accumulators(data)

        cleared = {
            name for name, value in before.items() if getattr(data, name) != value
        }
        unaccounted = (
            cleared - set(DAILY_ACCUMULATOR_FIELDS) - self._RESET_BUT_NOT_PERSISTED
        )
        assert not unaccounted, (
            f"reset_daily_accumulators clears {sorted(unaccounted)} but they are "
            "not in DAILY_ACCUMULATOR_FIELDS, so a restart loses them. Persist "
            "them, or add them to _RESET_BUT_NOT_PERSISTED with the reason."
        )
        # The exclusion list must not go stale either.
        assert self._RESET_BUT_NOT_PERSISTED <= cleared
        assert not self._RESET_BUT_NOT_PERSISTED & set(DAILY_ACCUMULATOR_FIELDS)


# ─── Demand-window entry record rides the same dated snapshot (Issue #1142) ──


def _entry_record(**overrides: object) -> dict[str, object]:
    """The 27 Jul case: entered at 64% against a 95% target."""
    record: dict[str, object] = {
        "dw_entry_actual_date": "2026-10-13",
        "dw_entry_actual_at": "2026-10-13T15:00:20+11:00",
        "dw_entry_actual_soc_pct": 64.0,
        "dw_entry_actual_target_pct": 95.0,
        "dw_entry_actual_shortfall_pct": 31.0,
    }
    record.update(overrides)
    return record


def _set_entry_record(data: CoordinatorData, at: datetime) -> None:
    data.dw_entry_actual_soc_pct = 64.0
    data.dw_entry_actual_at = at
    data.dw_entry_actual_target_pct = 95.0
    data.dw_entry_actual_shortfall_pct = 31.0
    data.dw_entry_actual_date = at.date()


def _assert_entry_blank(data: CoordinatorData) -> None:
    for key in DW_ENTRY_RECORD_FIELDS:
        assert getattr(data, key) is None, key


class TestDwEntryRecord:
    async def test_round_trips_within_the_day(self, totals_store, mock_store, data):
        """A restart at 16:40 keeps the entry recorded at 15:00."""
        captured_at = _at(2026, 10, 13, 15, 0)
        _set_entry_record(data, captured_at)
        await totals_store.async_save(data, _tracker(_at(2026, 10, 13, 16, 39)))
        mock_store.async_load.return_value = mock_store.async_save.await_args.args[0]

        fresh = CoordinatorData()
        later = _at(2026, 10, 13, 16, 41)
        with patch(NOW_PATH, return_value=later):
            assert await totals_store.async_restore(fresh, _tracker(later)) is True

        assert fresh.dw_entry_actual_soc_pct == 64.0
        assert fresh.dw_entry_actual_target_pct == 95.0
        assert fresh.dw_entry_actual_shortfall_pct == 31.0
        assert fresh.dw_entry_actual_at == captured_at
        assert fresh.dw_entry_actual_at.utcoffset() == captured_at.utcoffset()
        assert fresh.dw_entry_actual_date == date(2026, 10, 13)

    async def test_saved_snapshot_is_json_and_keeps_the_totals_shape(
        self, totals_store, mock_store, data
    ):
        """The record is a new top-level key; "date" and "totals" are as
        before, which is what lets a version without this code still read it."""
        _set_entry_record(data, _at(2026, 10, 13, 15, 0))

        await totals_store.async_save(data, _tracker(_at(2026, 10, 13, 16, 0)))

        saved = mock_store.async_save.await_args.args[0]
        assert set(saved) == {"date", "totals", DW_ENTRY_SNAPSHOT_KEY}
        assert set(saved["totals"]) == set(DAILY_ACCUMULATOR_FIELDS)
        assert saved[DW_ENTRY_SNAPSHOT_KEY] == {
            "dw_entry_actual_date": "2026-10-13",
            "dw_entry_actual_at": "2026-10-13T15:00:00+11:00",
            "dw_entry_actual_soc_pct": 64.0,
            "dw_entry_actual_target_pct": 95.0,
            "dw_entry_actual_shortfall_pct": 31.0,
        }
        # Home Assistant's Store writes JSON.
        assert json.loads(json.dumps(saved)) == saved

    async def test_blank_record_stays_blank(self, totals_store, mock_store, data):
        """No entry was recorded before the restart, so none is invented."""
        await totals_store.async_save(data, _tracker(_at(2026, 10, 13, 16, 39)))
        saved = mock_store.async_save.await_args.args[0]
        assert saved[DW_ENTRY_SNAPSHOT_KEY] is None
        mock_store.async_load.return_value = saved

        fresh = CoordinatorData()
        later = _at(2026, 10, 13, 16, 41)
        with patch(NOW_PATH, return_value=later):
            assert await totals_store.async_restore(fresh, _tracker(later)) is True

        _assert_entry_blank(fresh)

    async def test_discarded_with_a_previous_day_snapshot(
        self, totals_store, mock_store, data
    ):
        now = _at(2026, 10, 14, 0, 2)
        mock_store.async_load.return_value = {
            "date": "2026-10-13",
            "totals": _totals(),
            DW_ENTRY_SNAPSHOT_KEY: _entry_record(),
        }

        with patch(NOW_PATH, return_value=now):
            assert await totals_store.async_restore(data, _tracker(now)) is False

        _assert_entry_blank(data)
        _assert_all_zero(data)

    async def test_previous_day_record_in_a_same_day_snapshot_is_discarded(
        self, totals_store, mock_store, data
    ):
        """The record carries its own date, so yesterday's entry cannot ride in
        on a snapshot stamped today (a save between midnight and the first
        evaluation, which is what clears the record)."""
        now = _at(2026, 10, 14, 0, 2)
        mock_store.async_load.return_value = {
            "date": "2026-10-14",
            "totals": _totals(),
            DW_ENTRY_SNAPSHOT_KEY: _entry_record(),
        }

        with patch(NOW_PATH, return_value=now):
            assert await totals_store.async_restore(data, _tracker(now)) is True

        _assert_entry_blank(data)
        assert data.grid_import_cost == pytest.approx(1.14)

    async def test_snapshot_without_the_record_still_restores_totals(
        self, totals_store, mock_store, data, caplog
    ):
        """Upgrade: a snapshot written before this change has no record key."""
        now = _at(2026, 10, 13, 16, 41)
        mock_store.async_load.return_value = {"date": "2026-10-13", "totals": _totals()}

        with patch(NOW_PATH, return_value=now), caplog.at_level(logging.WARNING):
            assert await totals_store.async_restore(data, _tracker(now)) is True

        for key, value in _totals().items():
            assert getattr(data, key) == pytest.approx(value), key
        _assert_entry_blank(data)
        assert not caplog.records

    @pytest.mark.parametrize(
        "record",
        [
            "not a dict",
            [],
            {},
            _entry_record(dw_entry_actual_date=None),
            _entry_record(dw_entry_actual_date="garbage"),
            _entry_record(dw_entry_actual_at=None),
            _entry_record(dw_entry_actual_at="garbage"),
            # No timezone: the sensor publishes this value as-is.
            _entry_record(dw_entry_actual_at="2026-10-13T15:00:20"),
            # The timestamp is not on the day the record claims.
            _entry_record(dw_entry_actual_at="2026-10-12T15:00:20+11:00"),
            _entry_record(dw_entry_actual_at="2026-10-14T00:00:00+11:00"),
            _entry_record(dw_entry_actual_soc_pct=None),
            _entry_record(dw_entry_actual_soc_pct="64"),
            _entry_record(dw_entry_actual_soc_pct=True),
            _entry_record(dw_entry_actual_soc_pct=float("nan")),
            _entry_record(dw_entry_actual_soc_pct=-1.0),
            _entry_record(dw_entry_actual_soc_pct=640.0),
            _entry_record(dw_entry_actual_target_pct=float("inf")),
            _entry_record(dw_entry_actual_shortfall_pct=-0.5),
        ],
    )
    async def test_malformed_record_is_dropped_whole_and_totals_survive(
        self, totals_store, mock_store, data, caplog, record
    ):
        now = _at(2026, 10, 13, 16, 41)
        mock_store.async_load.return_value = {
            "date": "2026-10-13",
            "totals": _totals(),
            DW_ENTRY_SNAPSHOT_KEY: record,
        }

        with patch(NOW_PATH, return_value=now), caplog.at_level(logging.WARNING):
            assert await totals_store.async_restore(data, _tracker(now)) is True

        _assert_entry_blank(data)
        assert data.grid_import_cost == pytest.approx(1.14)
        assert "demand-window entry record" in caplog.text

    async def test_restoring_the_record_never_writes_the_target_latch(
        self, totals_store, mock_store, data
    ):
        """target_reached_today and its reset date are control state."""
        now = _at(2026, 10, 13, 16, 41)
        mock_store.async_load.return_value = {
            "date": "2026-10-13",
            "totals": _totals(),
            DW_ENTRY_SNAPSHOT_KEY: _entry_record(),
        }
        before = {
            field.name: getattr(data, field.name)
            for field in dataclasses.fields(CoordinatorData)
        }

        with patch(NOW_PATH, return_value=now):
            assert await totals_store.async_restore(data, _tracker(now)) is True

        changed = {
            name for name, value in before.items() if getattr(data, name) != value
        }
        assert changed == set(DAILY_ACCUMULATOR_FIELDS) | set(DW_ENTRY_RECORD_FIELDS)

    async def test_record_that_cannot_be_snapshotted_does_not_cost_the_totals(
        self, totals_store, mock_store, data, caplog
    ):
        """The save-side twin of the malformed-record restore test."""
        for key, value in _totals().items():
            setattr(data, key, value)
        _set_entry_record(data, _at(2026, 10, 13, 15, 0))

        with (
            patch.object(
                daily_totals_store,
                "snapshot_dw_entry_record",
                side_effect=RuntimeError("boom"),
            ),
            caplog.at_level(logging.WARNING),
        ):
            await totals_store.async_save(data, _tracker(_at(2026, 10, 13, 16, 0)))

        mock_store.async_save.assert_awaited_once_with({
            "date": "2026-10-13",
            "totals": _totals(),
            DW_ENTRY_SNAPSHOT_KEY: None,
        })
        warnings = [r.getMessage() for r in caplog.records]
        assert len(warnings) == 1
        assert "demand-window entry record" in warnings[0]
        assert "boom" in warnings[0]

    # A value away from the default for each annotation a record field may
    # have. A field of a new type fails the round trip below until it is
    # given a kind in DW_ENTRY_RECORD_FIELDS and a value here.
    _ROUND_TRIP_VALUES: dict[str, object] = {
        "float | None": 12.5,
        "datetime | None": _at(2026, 10, 13, 15, 0),
        "date | None": date(2026, 10, 13),
    }

    async def _round_trip(self, totals_store, mock_store, source, fresh) -> None:
        await totals_store.async_save(source, _tracker(_at(2026, 10, 13, 16, 39)))
        mock_store.async_load.return_value = mock_store.async_save.await_args.args[0]
        later = _at(2026, 10, 13, 16, 41)
        with patch(NOW_PATH, return_value=later):
            assert await totals_store.async_restore(fresh, _tracker(later)) is True

    async def test_every_entry_record_field_on_the_data_round_trips(
        self, totals_store, mock_store, data
    ):
        """A sixth dw_entry_actual_* field cannot silently miss the snapshot.

        Not a comparison of two lists: every such field on CoordinatorData is
        set, saved, restored into a fresh object and compared.
        """
        on_data = [
            field
            for field in dataclasses.fields(CoordinatorData)
            if field.name.startswith("dw_entry_actual_")
        ]
        assert len(on_data) >= 5
        for field in on_data:
            assert getattr(data, field.name) is None, field.name
            setattr(data, field.name, self._ROUND_TRIP_VALUES[str(field.type)])

        fresh = CoordinatorData()
        await self._round_trip(totals_store, mock_store, data, fresh)

        for field in on_data:
            assert getattr(fresh, field.name) == getattr(data, field.name), field.name
        assert {field.name for field in on_data} == set(DW_ENTRY_RECORD_FIELDS)
        assert not set(DW_ENTRY_RECORD_FIELDS) & set(DAILY_ACCUMULATOR_FIELDS)

    async def test_a_field_added_to_the_record_is_saved_and_restored(
        self, totals_store, mock_store
    ):
        """DW_ENTRY_RECORD_FIELDS is what save and restore work from, so
        adding a field there is all it takes to persist it."""

        @dataclasses.dataclass
        class _Extended(CoordinatorData):
            dw_entry_actual_extra_pct: float | None = None

        source = _Extended()
        _set_entry_record(source, _at(2026, 10, 13, 15, 0))
        source.dw_entry_actual_extra_pct = 12.5

        fresh = _Extended()
        with patch.dict(
            daily_totals_store.DW_ENTRY_RECORD_FIELDS,
            {"dw_entry_actual_extra_pct": "percent"},
        ):
            await self._round_trip(totals_store, mock_store, source, fresh)

        assert fresh.dw_entry_actual_extra_pct == 12.5
        assert fresh.dw_entry_actual_soc_pct == 64.0

    async def test_record_timestamp_just_inside_its_day_is_accepted(
        self, totals_store, mock_store, data
    ):
        """The same-day check on the timestamp is not off by one at the edges."""
        for captured_at in (
            _at(2026, 10, 13, 0, 0),
            _at(2026, 10, 13, 23, 59) + timedelta(seconds=59),
        ):
            source = CoordinatorData()
            _set_entry_record(source, captured_at)
            fresh = CoordinatorData()

            await self._round_trip(totals_store, mock_store, source, fresh)

            assert fresh.dw_entry_actual_at == captured_at

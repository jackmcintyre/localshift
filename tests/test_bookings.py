"""Tests for deferrable-load bookings (issue #1237)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError

from custom_components.localshift import bookings as bk
from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.sensors.bookings import BookingsSensor

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)


class FakeStore:
    """In-memory stand-in for homeassistant.helpers.storage.Store."""

    saved: dict = {}

    def __init__(self, hass, version, key):
        self.key = key

    async def async_load(self):
        return FakeStore.saved.get(self.key)

    async def async_save(self, data):
        FakeStore.saved[self.key] = data


@pytest.fixture(autouse=True)
def fake_store():
    FakeStore.saved = {}
    with patch.object(bk, "Store", FakeStore):
        yield


def make_store(on_change=None):
    return bk.BookingStore(MagicMock(), "e1", on_change or MagicMock())


def sunny(from_hour, to_hour, kw=3.0):
    """excess_kw_at: `kw` of spare solar between the given hours, else none."""

    def excess(slot_start):
        return kw if from_hour <= slot_start.hour < to_hour else -1.0

    return excess


BOOK = {
    "name": "washer",
    "duration_min": 60,
    "est_kwh": 1.0,
    "earliest_start": NOW,
    "latest_finish": NOW + timedelta(hours=8),
}


class TestPickStart:
    def test_first_start_that_fits_spare_solar(self):
        start, reason = bk.pick_start(
            NOW, NOW + timedelta(hours=8), timedelta(hours=1), 1.0, sunny(11, 14)
        )
        assert start == NOW.replace(hour=11)
        assert "solar" in reason

    def test_load_must_fit_for_every_slot(self):
        # Only one sunny hour of two needed: never fits, falls back.
        latest = NOW + timedelta(hours=8)
        start, _ = bk.pick_start(NOW, latest, timedelta(hours=2), 1.0, sunny(11, 12))
        assert start == latest - timedelta(hours=2)

    def test_deadline_fallback_when_no_solar(self):
        latest = NOW + timedelta(hours=8)
        start, reason = bk.pick_start(
            NOW, latest, timedelta(hours=1), 1.0, lambda _s: None
        )
        assert start == latest - timedelta(hours=1)
        assert "deadline" in reason

    def test_load_bigger_than_spare_solar_does_not_fit(self):
        latest = NOW + timedelta(hours=4)
        start, _ = bk.pick_start(
            NOW, latest, timedelta(hours=1), 5.0, sunny(9, 13, kw=2.0)
        )
        assert start == latest - timedelta(hours=1)


class TestExcessFromData:
    def test_spare_solar_is_forecast_minus_load(self):
        data = CoordinatorData()
        data.solcast_today = [
            {
                "period_start": NOW.isoformat(),
                "pv_estimate": 3.0,
            }
        ]
        data.load_forecast_slots = [1.0, 1.0]
        with patch.object(bk, "get_solar_for_15min_slot", return_value=0.5):
            excess = bk.excess_from_data(data, NOW)
            assert excess(NOW) == pytest.approx(1.0)  # 0.5 kWh/15min = 2 kW - 1
            assert excess(NOW + timedelta(minutes=15)) == pytest.approx(1.0)
            assert excess(NOW + timedelta(minutes=30)) is None
            assert excess(NOW - timedelta(minutes=15)) is None


class TestBookingStore:
    async def test_book_picks_and_persists(self):
        store = make_store()
        booking = await store.async_book(**BOOK, now=NOW, excess_kw_at=sunny(11, 14))
        assert booking["status"] == "booked"
        assert booking["start_at"] == NOW.replace(hour=11).isoformat()
        assert booking["finish_by"] == BOOK["latest_finish"].isoformat()
        assert booking["est_kwh"] == 1.0

        reloaded = make_store()
        await reloaded.async_load(NOW)
        assert reloaded.as_list(NOW) == [booking]

    async def test_est_kw_gives_kwh(self):
        store = make_store()
        args = {k: v for k, v in BOOK.items() if k != "est_kwh"}
        booking = await store.async_book(
            **args, est_kw=2.0, now=NOW, excess_kw_at=sunny(11, 14)
        )
        assert booking["est_kwh"] == 2.0

    @pytest.mark.parametrize(
        "change",
        [
            {"est_kwh": None},
            {"duration_min": 0},
            {"est_kwh": -1.0},
            {"latest_finish": NOW + timedelta(minutes=30)},
            {"latest_finish": NOW - timedelta(hours=1)},
        ],
    )
    async def test_invalid_booking_rejected(self, change):
        store = make_store()
        with pytest.raises(ServiceValidationError):
            await store.async_book(
                **{**BOOK, **change}, now=NOW, excess_kw_at=sunny(11, 14)
            )
        assert store.as_list(NOW) == []

    async def test_earliest_start_clamped_to_now(self):
        store = make_store()
        booking = await store.async_book(
            **{**BOOK, "earliest_start": NOW - timedelta(hours=3)},
            now=NOW,
            excess_kw_at=sunny(0, 24),
        )
        assert booking["start_at"] == NOW.isoformat()

    async def test_cancel_by_id_and_by_name(self):
        store = make_store()
        a = await store.async_book(**BOOK, now=NOW, excess_kw_at=sunny(11, 14))
        await store.async_book(
            **{**BOOK, "name": "dryer"}, now=NOW, excess_kw_at=sunny(11, 14)
        )
        assert await store.async_cancel(booking_id=a["id"], now=NOW) == 1
        by_status = {b["name"]: b["status"] for b in store.as_list(NOW)}
        assert by_status == {"washer": "cancelled", "dryer": "booked"}
        assert await store.async_cancel(name="dryer", now=NOW) == 1
        assert await store.async_cancel(name="dryer", now=NOW) == 0

    async def test_cancel_needs_id_or_name(self):
        with pytest.raises(ServiceValidationError):
            await make_store().async_cancel(now=NOW)

    async def test_status_transitions(self):
        store = make_store()
        b = await store.async_book(**BOOK, now=NOW, excess_kw_at=sunny(11, 14))
        await store.async_report(b["id"], "started", NOW)
        await store.async_report(b["id"], "done", NOW)
        assert store.as_list(NOW)[0]["status"] == "done"
        with pytest.raises(ServiceValidationError):
            await store.async_report(b["id"], "started", NOW)

    async def test_report_unknown_id_or_status_rejected(self):
        store = make_store()
        with pytest.raises(ServiceValidationError):
            await store.async_report("nope", "started", NOW)
        b = await store.async_book(**BOOK, now=NOW, excess_kw_at=sunny(11, 14))
        with pytest.raises(ServiceValidationError):
            await store.async_report(b["id"], "booked", NOW)

    async def test_pruned_after_latest_finish(self):
        store = make_store()
        await store.async_book(**BOOK, now=NOW, excess_kw_at=sunny(11, 14))
        later = BOOK["latest_finish"] + timedelta(minutes=1)
        assert store.as_list(later) == []
        reloaded = make_store()
        await reloaded.async_load(later)
        assert FakeStore.saved["localshift.bookings.e1"]["bookings"]
        assert reloaded.as_list(NOW) == []

    async def test_change_notifies_listeners(self):
        changed = MagicMock()
        store = make_store(changed)
        b = await store.async_book(**BOOK, now=NOW, excess_kw_at=sunny(11, 14))
        await store.async_report(b["id"], "started", NOW)
        await store.async_cancel(booking_id=b["id"], now=NOW)
        assert changed.call_count == 3


def hass_with_store(store):
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(bookings=store, data=CoordinatorData())
    )
    hass = MagicMock()
    hass.config_entries.async_loaded_entries.return_value = [entry]
    return hass


def call(**data):
    return SimpleNamespace(data=data)


class TestServices:
    async def test_book_load_returns_booking(self):
        store = make_store()
        hass = hass_with_store(store)
        with patch.object(bk.dt_util, "utcnow", return_value=NOW):
            result = await bk.async_book_load(hass, call(**BOOK))
        assert result["status"] == "booked"
        assert (
            result["start_at"]
            == (BOOK["latest_finish"] - timedelta(hours=1)).isoformat()
        )

    async def test_book_load_with_id_and_status_reports(self):
        store = make_store()
        hass = hass_with_store(store)
        with patch.object(bk.dt_util, "utcnow", return_value=NOW):
            booked = await bk.async_book_load(hass, call(**BOOK))
            result = await bk.async_book_load(
                hass, call(booking_id=booked["id"], status="started")
            )
        assert result["status"] == "started"

    async def test_cancel_load(self):
        store = make_store()
        hass = hass_with_store(store)
        with patch.object(bk.dt_util, "utcnow", return_value=NOW):
            await bk.async_book_load(hass, call(**BOOK))
            result = await bk.async_cancel_load(hass, call(name="washer"))
        assert result == {"cancelled": 1}

    async def test_not_loaded_is_rejected(self):
        hass = MagicMock()
        hass.config_entries.async_loaded_entries.return_value = []
        with pytest.raises(ServiceValidationError):
            await bk.async_cancel_load(hass, call(name="x"))

    def test_schema_requires_window_for_a_booking(self):
        with pytest.raises(vol.Invalid):
            bk.BOOK_SCHEMA({"status": "booked", "booking_id": "x"})
        parsed = bk.BOOK_SCHEMA({
            "name": "washer",
            "duration_min": 60,
            "est_kwh": 1.0,
            "earliest_start": "2026-11-05T09:00:00+00:00",
            "latest_finish": "2026-11-05T17:00:00+00:00",
        })
        assert parsed["earliest_start"].tzinfo is not None
        assert (
            bk.BOOK_SCHEMA({"booking_id": "ab", "status": "done"})["status"] == "done"
        )

    async def test_book_load_missing_fields_rejected(self):
        hass = hass_with_store(make_store())
        with pytest.raises(ServiceValidationError):
            await bk.async_book_load(hass, call(name="washer"))

    def test_register_and_unregister(self):
        hass = MagicMock()
        hass.services.has_service.return_value = False
        bk.async_register_services(hass)
        assert hass.services.async_register.call_count == 2
        hass.services.has_service.return_value = True
        bk.async_register_services(hass)
        assert hass.services.async_register.call_count == 2
        bk.async_unregister_services(hass)
        assert hass.services.async_remove.call_count == 2


class TestBookingsSensor:
    async def test_state_counts_live_bookings(self):
        store = make_store()
        a = await store.async_book(**BOOK, now=NOW, excess_kw_at=sunny(11, 14))
        await store.async_book(
            **{**BOOK, "name": "dryer"}, now=NOW, excess_kw_at=sunny(11, 14)
        )
        await store.async_cancel(booking_id=a["id"], now=NOW)
        coordinator = MagicMock(bookings=store)
        sensor = BookingsSensor(coordinator, MagicMock())
        with patch(
            "custom_components.localshift.sensors.bookings.dt_util.utcnow",
            return_value=NOW,
        ):
            sensor._update_from_coordinator()
            attrs = sensor.extra_state_attributes
        assert sensor.native_value == 1
        assert {b["name"] for b in attrs["bookings"]} == {"washer", "dryer"}
        assert set(attrs["bookings"][0]) >= {
            "id",
            "name",
            "start_at",
            "finish_by",
            "est_kwh",
            "status",
            "reason",
        }

    async def test_no_store_yet(self):
        sensor = BookingsSensor(MagicMock(bookings=None), MagicMock())
        sensor._update_from_coordinator()
        assert sensor.native_value == 0
        assert sensor.extra_state_attributes == {"bookings": []}

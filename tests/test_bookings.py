"""Tests for deferrable-load bookings (issue #1237)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.localshift import bookings as bk
from custom_components.localshift.const import DOMAIN
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


def decision(start, export_kwh, minutes=15):
    return {
        "timestamp_iso": start.isoformat(),
        "slot_interval_minutes": minutes,
        "grid_export_kwh": export_kwh,
    }


class TestExcessFromData:
    def test_spare_solar_is_the_plans_export_as_kw(self):
        data = CoordinatorData()
        data.optimizer_decisions = [
            decision(NOW, 0.5),
            decision(NOW + timedelta(minutes=15), 0.0),
        ]
        excess = bk.excess_from_data(data)
        assert excess(NOW) == pytest.approx(2.0)  # 0.5 kWh in 15 min
        assert excess(NOW + timedelta(minutes=14)) == pytest.approx(2.0)
        assert excess(NOW + timedelta(minutes=15)) == 0.0
        assert excess(NOW + timedelta(minutes=30)) is None
        assert excess(NOW - timedelta(minutes=1)) is None


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

    async def test_malformed_stored_payload_starts_empty(self, caplog):
        FakeStore.saved["localshift.bookings.e1"] = {"bookings": {"x": {"bad": 1}}}
        store = make_store()
        await store.async_load(NOW)
        assert store.as_list(NOW) == []
        assert "starting with none" in caplog.text

    async def test_window_arithmetic_survives_the_dst_start_night(self):
        # Sydney clocks go 02:00 -> 03:00 on 4 Oct 2026: 03:30 local is only
        # 90 real minutes after 01:00 local.
        with patch.object(
            bk.dt_util, "DEFAULT_TIME_ZONE", ZoneInfo("Australia/Sydney")
        ):
            parsed = bk.BOOK_SCHEMA({
                "latest_finish": "2026-10-04T03:30:00",
                "earliest_start": "2026-10-03T22:00:00",
            })
        store = make_store()
        booking = await store.async_book(
            name="washer",
            duration_min=120,
            est_kwh=1.0,
            earliest_start=parsed["earliest_start"],
            latest_finish=parsed["latest_finish"],
            now=parsed["earliest_start"],
            excess_kw_at=lambda _s: None,
        )
        start = datetime.fromisoformat(booking["start_at"])
        assert parsed["latest_finish"] - start == timedelta(minutes=120)

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

    @pytest.mark.parametrize("extra", [{"booking_id": "ab"}, {"status": "done"}])
    async def test_booking_id_and_status_go_together(self, extra):
        hass = hass_with_store(make_store())
        with pytest.raises(ServiceValidationError, match="go together"):
            await bk.async_book_load(hass, call(**BOOK, **extra))

    def test_energy_given_once(self):
        with pytest.raises(vol.Invalid):
            bk.BOOK_SCHEMA({"est_kwh": 1.0, "est_kw": 1.0})

    async def test_services_run_through_real_home_assistant(self, tmp_path):
        """Core must accept the handlers: a lambda here was run as an executor job."""
        hass = HomeAssistant(str(tmp_path))
        store = make_store()
        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(bookings=store, data=CoordinatorData())
        )
        data = {
            "name": "washer",
            "duration_min": 60,
            "est_kwh": 1.0,
            "earliest_start": NOW.isoformat(),
            "latest_finish": (NOW + timedelta(hours=8)).isoformat(),
        }
        bk.async_register_services(hass)
        hass.config_entries = MagicMock()
        hass.config_entries.async_loaded_entries.return_value = [entry]
        with patch.object(bk.dt_util, "utcnow", return_value=NOW):
            booked = await hass.services.async_call(
                DOMAIN, "book_load", data, blocking=True, return_response=True
            )
            cancelled = await hass.services.async_call(
                DOMAIN,
                "cancel_load",
                {"name": "washer"},
                blocking=True,
                return_response=True,
            )
        assert booked["status"] == "booked"
        assert cancelled == {"cancelled": 1}
        await hass.async_stop(force=True)

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

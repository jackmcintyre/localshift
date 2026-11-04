"""Deferrable-load bookings (issue #1237).

The house books a load (washer, dryer, dishwasher) with a window; LocalShift
picks the start and publishes it on ``sensor.localshift_bookings``. The pick
reads the existing forecasts only: the planner is not involved.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator.data import CoordinatorData
from .forecast.solar import get_solar_for_15min_slot

if TYPE_CHECKING:
    from .coordinator import LocalShiftCoordinator

STORAGE_VERSION = 1
SLOT = timedelta(minutes=15)
# A booking only moves forward: booked -> started -> done, or cancelled.
_NEXT_STATUS = {"booked": {"started", "done"}, "started": {"done"}}
SERVICE_BOOK = "book_load"
SERVICE_CANCEL = "cancel_load"

ExcessAt = Callable[[datetime], "float | None"]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)


BOOK_SCHEMA = vol.Schema({
    vol.Optional("name"): cv.string,
    vol.Optional("duration_min"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Optional("est_kwh"): vol.All(vol.Coerce(float), vol.Range(min=0.01)),
    vol.Optional("est_kw"): vol.All(vol.Coerce(float), vol.Range(min=0.01)),
    vol.Optional("earliest_start"): vol.All(cv.datetime, _aware),
    vol.Optional("latest_finish"): vol.All(cv.datetime, _aware),
    # The house reports a start or finish through the same service.
    vol.Optional("booking_id"): cv.string,
    vol.Optional("status"): vol.In(["started", "done"]),
})
CANCEL_SCHEMA = vol.Schema({
    vol.Optional("booking_id"): cv.string,
    vol.Optional("name"): cv.string,
})


def excess_from_data(data: CoordinatorData, now: datetime) -> ExcessAt:
    """Spare solar (kW) at a slot: Solcast minus the load forecast, as the plan sees it."""
    base = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
    solcast = [*data.solcast_today, *data.solcast_tomorrow]

    def excess_at(slot_start: datetime) -> float | None:
        index = (slot_start - base) // SLOT
        if not 0 <= index < len(data.load_forecast_slots):
            return None
        solar_kw = get_solar_for_15min_slot(solcast, slot_start) * 4
        return solar_kw - data.load_forecast_slots[index]

    return excess_at


def pick_start(
    earliest: datetime,
    latest_finish: datetime,
    duration: timedelta,
    load_kw: float,
    excess_at: ExcessAt,
) -> tuple[datetime, str]:
    """First start whose every slot has spare solar for the load, else the deadline start."""
    latest_start = latest_finish - duration
    slots = -(-duration // SLOT)
    start = earliest
    while start <= latest_start:
        spare = [excess_at(start + SLOT * i) for i in range(slots)]
        if all(kw is not None and kw >= load_kw for kw in spare):
            return start, "fits spare solar"
        start += SLOT
    return latest_start, "no spare-solar window; latest start to meet the deadline"


class BookingStore:
    """Bookings, persisted so they survive a restart and pruned after latest_finish."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, on_change: Callable[[], None]
    ) -> None:
        self._store = Store(hass, STORAGE_VERSION, f"localshift.bookings.{entry_id}")
        self._on_change = on_change
        self._bookings: dict[str, dict[str, Any]] = {}

    async def async_load(self, now: datetime) -> None:
        self._bookings = (await self._store.async_load() or {}).get("bookings", {})
        self._prune(now)

    def _prune(self, now: datetime) -> None:
        self._bookings = {
            key: b
            for key, b in self._bookings.items()
            if datetime.fromisoformat(b["finish_by"]) >= now
        }

    def as_list(self, now: datetime) -> list[dict[str, Any]]:
        return [
            b
            for b in self._bookings.values()
            if datetime.fromisoformat(b["finish_by"]) >= now
        ]

    async def _commit(self, now: datetime) -> None:
        self._prune(now)
        await self._store.async_save({"bookings": self._bookings})
        self._on_change()

    async def async_book(
        self,
        name: str,
        duration_min: int,
        earliest_start: datetime,
        latest_finish: datetime,
        now: datetime,
        excess_kw_at: ExcessAt,
        est_kwh: float | None = None,
        est_kw: float | None = None,
    ) -> dict[str, Any]:
        hours = duration_min / 60
        kwh = est_kwh if est_kwh is not None else (est_kw or 0) * hours
        if duration_min <= 0 or kwh <= 0:
            raise ServiceValidationError(
                "duration_min and est_kwh (or est_kw) are required"
            )
        earliest = max(earliest_start, now)
        duration = timedelta(minutes=duration_min)
        if latest_finish - earliest < duration:
            raise ServiceValidationError("The window is shorter than the duration")
        start, reason = pick_start(
            earliest, latest_finish, duration, kwh / hours, excess_kw_at
        )
        booking = {
            "id": secrets.token_hex(4),
            "name": name,
            "start_at": start.isoformat(),
            "finish_by": latest_finish.isoformat(),
            "est_kwh": round(kwh, 2),
            "status": "booked",
            "reason": reason,
        }
        self._bookings[booking["id"]] = booking
        await self._commit(now)
        return booking

    async def async_cancel(
        self,
        now: datetime,
        booking_id: str | None = None,
        name: str | None = None,
    ) -> int:
        if booking_id is None and name is None:
            raise ServiceValidationError("booking_id or name is required")
        hits = [
            b
            for b in self._bookings.values()
            if b["status"] in _NEXT_STATUS
            and (b["id"] == booking_id if booking_id else b["name"] == name)
        ]
        for booking in hits:
            booking["status"] = "cancelled"
        if hits:
            await self._commit(now)
        return len(hits)

    async def async_report(
        self, booking_id: str, status: str, now: datetime
    ) -> dict[str, Any]:
        booking = self._bookings.get(booking_id)
        if booking is None:
            raise ServiceValidationError(f"Unknown booking {booking_id}")
        if status not in _NEXT_STATUS.get(booking["status"], set()):
            raise ServiceValidationError(
                f"A {booking['status']} booking cannot become {status}"
            )
        booking["status"] = status
        await self._commit(now)
        return booking


def _store(hass: HomeAssistant) -> BookingStore:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    coordinator: LocalShiftCoordinator | None = (
        entries[0].runtime_data if entries else None
    )
    if coordinator is None or coordinator.bookings is None:
        raise ServiceValidationError("LocalShift is not loaded")
    return coordinator.bookings


async def async_book_load(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    """Book a load, or (with booking_id and status) report that one started or finished."""
    store, now, d = _store(hass), dt_util.utcnow(), call.data
    if "booking_id" in d and "status" in d:
        return await store.async_report(d["booking_id"], d["status"], now)
    missing = {"name", "duration_min", "earliest_start", "latest_finish"} - set(d)
    if missing:
        raise ServiceValidationError(f"Missing: {', '.join(sorted(missing))}")
    coordinator = hass.config_entries.async_loaded_entries(DOMAIN)[0].runtime_data
    return await store.async_book(
        name=d["name"],
        duration_min=d["duration_min"],
        earliest_start=d["earliest_start"],
        latest_finish=d["latest_finish"],
        est_kwh=d.get("est_kwh"),
        est_kw=d.get("est_kw"),
        now=now,
        excess_kw_at=excess_from_data(coordinator.data, now),
    )


async def async_cancel_load(hass: HomeAssistant, call: ServiceCall) -> dict[str, int]:
    """Cancel the active booking with this id, or all active ones with this name."""
    cancelled = await _store(hass).async_cancel(
        dt_util.utcnow(),
        booking_id=call.data.get("booking_id"),
        name=call.data.get("name"),
    )
    return {"cancelled": cancelled}


def async_register_services(hass: HomeAssistant) -> None:
    """Register the booking services once per Home Assistant."""
    if hass.services.has_service(DOMAIN, SERVICE_BOOK):
        return
    for name, handler, schema in (
        (SERVICE_BOOK, async_book_load, BOOK_SCHEMA),
        (SERVICE_CANCEL, async_cancel_load, CANCEL_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN,
            name,
            lambda call, handler=handler: handler(hass, call),
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )


def async_unregister_services(hass: HomeAssistant) -> None:
    for name in (SERVICE_BOOK, SERVICE_CANCEL):
        hass.services.async_remove(DOMAIN, name)

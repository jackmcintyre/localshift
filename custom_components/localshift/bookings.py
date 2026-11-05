"""Deferrable-load bookings (issue #1237).

The house books a load (washer, dryer, dishwasher) with a window; LocalShift
picks the start and publishes it on ``sensor.localshift_bookings``. The pick
reads the existing forecasts only: the planner is not involved.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator.data import CoordinatorData
from .engine.booked_load import choose_start

if TYPE_CHECKING:
    from .coordinator import LocalShiftCoordinator

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1
SLOT = timedelta(minutes=15)
# A booking only moves forward: booked -> started -> done, or cancelled.
_NEXT_STATUS = {"booked": {"started", "done"}, "started": {"done"}}
SERVICE_BOOK = "book_load"
SERVICE_CANCEL = "cancel_load"

# Spare kW a load can count on across a window; None if the plan does not cover it.
ExcessAt = Callable[[datetime, datetime], "float | None"]
# Picks a start from the plan: (start, why), start None when the plan cannot say.
ChooseStart = Callable[
    [datetime, datetime, timedelta, float], Awaitable["tuple[datetime | None, str]"]
]
_REQUIRED = ("id", "name", "status", "finish_by")


def _aware(value: datetime) -> datetime:
    """UTC, reading a naive time as local; all window arithmetic is in UTC (DST)."""
    return dt_util.as_utc(
        value if value.tzinfo else value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    )


BOOK_SCHEMA = vol.Schema({
    vol.Optional("name"): cv.string,
    vol.Optional("duration_min"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Exclusive("est_kwh", "energy"): vol.All(vol.Coerce(float), vol.Range(min=0.01)),
    vol.Exclusive("est_kw", "energy"): vol.All(vol.Coerce(float), vol.Range(min=0.01)),
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


def excess_from_data(data: CoordinatorData) -> ExcessAt:
    """Spare solar (kW) over a window, from the plan's own slots (read-only).

    Per slot it is the forecast export, capped at the solar left after the
    house load, so a planned battery sell-off is not counted as spare solar.
    The figure for a window is its lowest slot; a window the plan does not
    fully cover has none.
    """
    slots = []
    for d in data.optimizer_decisions:
        start = dt_util.as_utc(datetime.fromisoformat(d["timestamp_iso"]))
        minutes = d["slot_interval_minutes"]
        solar_left = max(0.0, d.get("solar_kwh", 0.0) - d.get("consumption_kwh", 0.0))
        kwh = min(d["grid_export_kwh"], solar_left)
        slots.append((start, start + timedelta(minutes=minutes), kwh * 60 / minutes))

    def excess_at(start: datetime, end: datetime) -> float | None:
        hits = [(s, e, kw) for s, e, kw in slots if s < end and e > start]
        covered = sum((min(e, end) - max(s, start) for s, e, _ in hits), timedelta())
        return min(kw for *_, kw in hits) if hits and covered == end - start else None

    return excess_at


def pick_start(
    earliest: datetime,
    latest_finish: datetime,
    duration: timedelta,
    load_kw: float,
    excess_at: ExcessAt,
) -> tuple[datetime, str]:
    """First start whose whole run fits in spare solar, else the deadline start."""
    latest_start = latest_finish - duration
    start = earliest
    while start <= latest_start:
        spare = excess_at(start, start + duration)
        if spare is not None and spare >= load_kw:
            return start, "fits spare solar"
        start += SLOT
    return latest_start, "no spare-solar window; latest start to meet the deadline"


def _is_booking(entry: Any) -> bool:
    """A stored entry with every key the sensor and pruning rely on."""
    try:
        datetime.fromisoformat(entry["finish_by"])
        return all(key in entry for key in _REQUIRED)
    except (TypeError, KeyError, ValueError):
        return False


class BookingStore:
    """Bookings, persisted so they survive a restart and pruned after latest_finish."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, on_change: Callable[[], None]
    ) -> None:
        self._store = Store(hass, STORAGE_VERSION, f"localshift.bookings.{entry_id}")
        self._on_change = on_change
        self._bookings: dict[str, dict[str, Any]] = {}
        # Bookings are placed one at a time, each seeing the earlier ones.
        self._placing = asyncio.Lock()

    async def async_load(self, now: datetime) -> None:
        saved = await self._store.async_load()
        raw = saved.get("bookings") if isinstance(saved, dict) else None
        raw = raw if isinstance(raw, dict) else {}
        self._bookings = {k: b for k, b in raw.items() if _is_booking(b)}
        if len(self._bookings) != len(raw):
            _LOGGER.warning(
                "Dropped %d unreadable stored load bookings",
                len(raw) - len(self._bookings),
            )
        self._prune(now)

    def _live(self, now: datetime) -> dict[str, dict[str, Any]]:
        return {
            key: b
            for key, b in self._bookings.items()
            if datetime.fromisoformat(b["finish_by"]) >= now
        }

    def _prune(self, now: datetime) -> None:
        self._bookings = self._live(now)

    def as_list(self, now: datetime) -> list[dict[str, Any]]:
        return list(self._live(now).values())

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
        choose: ChooseStart | None = None,
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
        async with self._placing:
            start, reason = pick_start(
                earliest, latest_finish, duration, kwh / hours, excess_kw_at
            )
            if choose is not None:
                try:
                    planned, why = await choose(earliest, latest_finish, duration, kwh)
                except Exception:  # noqa: BLE001 - a booking must not fail on the planner
                    _LOGGER.exception("Planning a start for %s failed", name)
                    planned, why = None, "plan evaluation failed"
                if planned is not None:
                    start, reason = planned, why
                else:
                    reason = f"{why}; {reason}"
            return await self._add(
                name, duration_min, start, latest_finish, kwh, reason, now
            )

    async def _add(
        self,
        name: str,
        duration_min: int,
        start: datetime,
        latest_finish: datetime,
        kwh: float,
        reason: str,
        now: datetime,
    ) -> dict[str, Any]:
        booking = {
            "id": secrets.token_hex(4),
            "name": name,
            "duration_min": duration_min,
            "start_at": start.isoformat(),
            "finish_by": latest_finish.isoformat(),
            "est_kwh": round(kwh, 2),
            "status": "booked",
            "reason": reason,
        }
        self._bookings[booking["id"]] = booking
        await self._commit(now)
        return dict(booking)

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
        return dict(booking)


def _loaded(hass: HomeAssistant) -> tuple[LocalShiftCoordinator, BookingStore]:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    coordinator = entries[0].runtime_data if entries else None
    if coordinator is None or coordinator.bookings is None:
        raise ServiceValidationError("LocalShift is not loaded")
    return coordinator, coordinator.bookings


async def async_book_load(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Book a load, or (with booking_id and status) report that one started or finished."""
    (coordinator, store), now, d = _loaded(hass), dt_util.utcnow(), call.data
    if ("booking_id" in d) != ("status" in d):
        raise ServiceValidationError("booking_id and status go together")
    if "status" in d:
        return await store.async_report(d["booking_id"], d["status"], now)
    missing = {"name", "duration_min", "earliest_start", "latest_finish"} - set(d)
    if missing:
        raise ServiceValidationError(f"Missing: {', '.join(sorted(missing))}")
    return await store.async_book(
        name=d["name"],
        duration_min=d["duration_min"],
        earliest_start=d["earliest_start"],
        latest_finish=d["latest_finish"],
        est_kwh=d.get("est_kwh"),
        est_kw=d.get("est_kw"),
        now=now,
        excess_kw_at=excess_from_data(coordinator.data),
        choose=_plan_chooser(hass, coordinator, store, now),
    )


# The plan is rebuilt every cycle; a base older than this was not refreshed.
MAX_PLAN_AGE = timedelta(minutes=10)


def _stale_reason(facade: Any, base: Any, now: datetime) -> str | None:
    """Why the latest planning inputs cannot be trusted for a booking, if so."""
    if base is None:
        return "no plan yet"
    made = facade.booking_base_at
    first = datetime.fromisoformat(base.slots[0].timestamp_iso) if base.slots else None
    if made is None or now - made > MAX_PLAN_AGE:
        return "latest plan is out of date"
    if first is None or now >= first + timedelta(
        minutes=base.slots[0].slot_interval_minutes
    ):
        return "latest plan no longer covers now"
    return None


def _plan_chooser(
    hass: HomeAssistant,
    coordinator: LocalShiftCoordinator,
    store: BookingStore,
    now: datetime,
) -> ChooseStart:
    """Evaluate starts against the live plan, off the event loop."""
    engine = getattr(coordinator, "computation_engine", None)
    facade = engine.optimizer_facade if engine is not None else None
    base = facade.booking_base if facade is not None else None
    stale = (
        "no plan yet"
        if facade is None or base is None
        else _stale_reason(facade, base, now)
    )
    if facade is None or base is None or stale:
        why = stale or "no plan yet"

        async def unusable(*_: Any) -> tuple[datetime | None, str]:
            return None, why

        return unusable

    async def choose(
        earliest: datetime, latest_finish: datetime, duration: timedelta, kwh: float
    ) -> tuple[datetime | None, str]:
        return await hass.async_add_executor_job(
            choose_start,
            facade.planner,
            base,
            store.as_list(now),
            earliest,
            latest_finish,
            duration,
            kwh,
        )

    return choose


async def async_cancel_load(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Cancel the active booking with this id, or all active ones with this name."""
    cancelled = await _loaded(hass)[1].async_cancel(
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
            functools.partial(handler, hass),
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )


def async_unregister_services(hass: HomeAssistant) -> None:
    for name in (SERVICE_BOOK, SERVICE_CANCEL):
        hass.services.async_remove(DOMAIN, name)

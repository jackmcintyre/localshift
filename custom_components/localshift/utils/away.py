"""Away (holiday mode) helpers for the LocalShift integration.

The away entity is an optional on/off entity (any domain) that is on while the
house is empty. It is stored only in ``entry.options`` under
``CONF_AWAY_ENTITY``; unset means today's behaviour, exactly.

This module also fetches the away entity's on/off history from the recorder
and turns it into the two shapes learning needs to mask away hours out of its
training data (docs/holiday-away/plan.md "What to build" item 3):

- ``away_utc_hour_starts``: aware UTC hour-start datetimes, matching how HA's
  long-term statistics rows are keyed. Used to mask 28-day consumption stats.
- ``away_local_hour_keys``: local ``(date_iso, hour)`` pairs. Used to mask the
  30-day weather-correlation snapshots, which are keyed by local date/hour.

Both are built by walking the away entity's on/off intervals in 15-minute
steps (``_iter_quarter_hours``): every real UTC offset is a multiple of 15
minutes, so each quarter sits inside exactly one UTC hour and one local hour,
which makes the walk safe across DST transitions and half-hour-offset zones.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from ..const import CONF_AWAY_ENTITY

_LOGGER = logging.getLogger(__name__)


def get_away_entity_id(entry: ConfigEntry) -> str | None:
    """Return the configured away entity id, or None when unset.

    Options are the single source: a value left over in ``entry.data`` is
    ignored, so clearing the option really clears the behaviour. A non-string
    value (e.g. a bare ``MagicMock`` from a test fixture that never set
    ``options``) is treated as unset rather than as a truthy entity id.

    Args:
        entry: LocalShift config entry

    Returns:
        The away entity id, or None if no away entity is configured.

    """
    value = entry.options.get(CONF_AWAY_ENTITY)
    return value if isinstance(value, str) and value else None


def is_away_active(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Report whether the house is currently away.

    Unset, missing, unavailable or unknown all report "not away", which keeps
    today's behaviour on any failure.

    Args:
        hass: Home Assistant instance
        entry: LocalShift config entry

    Returns:
        True only when an away entity is configured and its state is "on".

    """
    entity_id = get_away_entity_id(entry)
    if not entity_id:
        return False

    state = hass.states.get(entity_id)
    return state is not None and state.state == STATE_ON


def away_state_unknown(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Report whether a configured away entity currently can't be read.

    True when an away entity is configured but is missing, unavailable or
    unknown. Callers hold their last known away state through such a gap
    rather than reading it as "home" (Jack's call, 24 Sep 2026): a package
    reload or an HA hiccup mid-trip must not release the away reserve, and a
    flapping entity must not write the Powerwall reserve on every flip. Only
    an explicit off, or clearing the option, ends away.

    Args:
        hass: Home Assistant instance
        entry: LocalShift config entry

    Returns:
        True when the configured entity's state can't be read.

    """
    entity_id = get_away_entity_id(entry)
    if not entity_id:
        return False
    state = hass.states.get(entity_id)
    return state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)


def fetch_away_intervals_sync(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]] | None:
    """Fetch the away entity's on/off intervals from the recorder (sync).

    Runs in the recorder's executor. Walks the state-change history: an
    interval opens on "on" and closes on any other readable state.
    Unavailable and unknown hold whatever state came before them, the same
    hold-last-known rule the live away state uses (``away_state_unknown``), so
    a reload mid-trip doesn't split the trip into away and home spells. Each interval's start is
    clamped to ``max(last_changed, start)`` so a state that was already "on"
    before the window doesn't produce an out-of-range start. An interval
    still open at ``end`` closes there.

    Any failure (missing recorder, query error, ...) returns None and logs a
    warning rather than raising. None is distinct from ``[]`` (no away time),
    so callers can skip caching a failed fetch and retry on the next tick
    instead of treating the day as unmasked (#1086).

    Args:
        hass: Home Assistant instance
        entity_id: The away entity to fetch history for
        start: Window start (aware datetime)
        end: Window end (aware datetime)

    Returns:
        List of (start, end) aware-datetime tuples, each an "on" interval
        clamped to [start, end], or None if the fetch failed.

    """
    try:
        from homeassistant.components.recorder import history

        states_by_entity = history.state_changes_during_period(
            hass,
            start,
            end,
            entity_id,
            no_attributes=True,
            include_start_time_state=True,
        )
        entity_states: list[State] = (
            states_by_entity.get(entity_id, [])
            if isinstance(states_by_entity, dict)
            else []
        )

        intervals: list[tuple[datetime, datetime]] = []
        open_start: datetime | None = None
        for state in entity_states:
            changed = getattr(state, "last_changed", None)
            if changed is None:
                continue
            if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                continue
            if state.state == STATE_ON:
                if open_start is None:
                    open_start = max(changed, start)
            elif open_start is not None:
                intervals.append((open_start, changed))
                open_start = None

        if open_start is not None:
            intervals.append((open_start, end))

        return intervals
    except Exception:
        _LOGGER.warning(
            "Failed to fetch away history for %s; retrying on the next refresh",
            entity_id,
            exc_info=True,
        )
        return None


async def async_get_away_intervals(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]] | None:
    """Fetch the away entity's on/off intervals from the recorder (async).

    Args:
        hass: Home Assistant instance
        entity_id: The away entity to fetch history for
        start: Window start (aware datetime)
        end: Window end (aware datetime)

    Returns:
        List of (start, end) aware-datetime tuples, or None if the fetch
        failed; see ``fetch_away_intervals_sync``.

    """
    from homeassistant.components import recorder

    return await recorder.get_instance(hass).async_add_executor_job(
        fetch_away_intervals_sync, hass, entity_id, start, end
    )


def _floor_15(value: datetime) -> datetime:
    """Floor a datetime down to the nearest 15-minute mark."""
    minute = (value.minute // 15) * 15
    return value.replace(minute=minute, second=0, microsecond=0)


def _iter_quarter_hours(
    intervals: list[tuple[datetime, datetime]],
) -> Iterator[datetime]:
    """Yield each aware UTC quarter-hour touched by the given intervals.

    For each interval, starts at ``floor_15(start)`` and steps 15 minutes
    while ``t < end`` (end-exclusive), so an interval that starts and ends
    exactly on a quarter boundary doesn't yield its end quarter. Duplicate
    quarters across overlapping/adjacent intervals are yielded more than
    once; callers collect into a set.

    Args:
        intervals: (start, end) aware-datetime tuples, in any timezone.

    Yields:
        Aware UTC datetimes, each on a 15-minute boundary.

    """
    for start, end in intervals:
        start_utc = dt_util.as_utc(start)
        end_utc = dt_util.as_utc(end)
        if end_utc <= start_utc:
            continue
        t = _floor_15(start_utc)
        while t < end_utc:
            yield t
            t += timedelta(minutes=15)


def away_utc_hour_starts(
    intervals: list[tuple[datetime, datetime]],
) -> frozenset[datetime]:
    """Return the aware UTC hour-starts touched by the given away intervals.

    HA long-term statistics rows are keyed by UTC hour start, which for
    Sydney is the same as local hour start. Used to mask consumption stats
    rows in ``forecast/history.py``.

    Args:
        intervals: (start, end) aware-datetime tuples of away time.

    Returns:
        Frozenset of aware UTC datetimes, each an hour start.

    """
    return frozenset(q.replace(minute=0) for q in _iter_quarter_hours(intervals))


def away_full_utc_hour_starts(
    intervals: list[tuple[datetime, datetime]],
) -> frozenset[datetime]:
    """Return the UTC hours that fall *entirely* inside an away interval.

    Unlike ``away_utc_hour_starts`` (which masks an hour touched by any part
    of an away interval, deliberately generous for the at-home mask), this is
    strict: for each interval it starts at ``ceil_hour(start)`` and includes
    an hour only while ``hour_start + 1h <= end``. That keeps the departure
    and return hours out of the away profile, so packing, the dryer running
    before leaving, or arriving home don't contaminate a 3-sample mean
    (docs/holiday-away/plan.md "What to build" item 2).

    Args:
        intervals: (start, end) aware-datetime tuples of away time, in any
            timezone. A reversed or zero-length interval contributes nothing.

    Returns:
        Frozenset of aware UTC datetimes, each an hour start that is fully
        covered by at least one interval.

    """
    hours: set[datetime] = set()
    for start, end in intervals:
        start_utc = dt_util.as_utc(start)
        end_utc = dt_util.as_utc(end)
        if end_utc <= start_utc:
            continue
        floored = start_utc.replace(minute=0, second=0, microsecond=0)
        hour_start = floored if floored >= start_utc else floored + timedelta(hours=1)
        while hour_start + timedelta(hours=1) <= end_utc:
            hours.add(hour_start)
            hour_start += timedelta(hours=1)
    return frozenset(hours)


def away_local_hour_keys(
    intervals: list[tuple[datetime, datetime]], tz: Any
) -> frozenset[tuple[str, int]]:
    """Return local (date_iso, hour) keys touched by the given away intervals.

    Used to mask weather-correlation snapshots in ``learning/correlation.py``,
    which are keyed by local date and hour. A local hour that occurs twice
    (a DST fall-back) collapses to one key; a local hour skipped by a DST
    spring-forward never appears.

    Args:
        intervals: (start, end) aware-datetime tuples of away time.
        tz: Timezone to convert into (e.g. ``dt_util.DEFAULT_TIME_ZONE``).

    Returns:
        Frozenset of (ISO date string, hour) tuples.

    """
    keys: set[tuple[str, int]] = set()
    for quarter in _iter_quarter_hours(intervals):
        local = quarter.astimezone(tz)
        keys.add((local.date().isoformat(), local.hour))
    return frozenset(keys)

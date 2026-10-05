"""When battery export can physically happen (Issue #1097).

PROACTIVE_EXPORT hands the Powerwall to Tesla's time-based control and relies
on it choosing to export. Tesla decides from the tariff stored on its side, not
from Amber's prices: outside its top sell rate it holds the battery, and
grid-charges it whenever grid charging is permitted. These helpers turn that
tariff into periods the planner must not schedule export in.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# Teslemetry tariff calendar events are summarised as "<period>: <price>/kWh".
_PRICE_PATTERN = re.compile(r"(-?\d+(?:\.\d+)?)\s*/\s*kWh", re.IGNORECASE)

Period = tuple[datetime, datetime]


def _parse_event(event: Any) -> tuple[datetime, datetime, float] | None:
    """Return (start, end, sell_price) for a tariff event, or None if unreadable."""
    if not isinstance(event, dict):
        return None
    match = _PRICE_PATTERN.search(str(event.get("summary", "")))
    start = dt_util.parse_datetime(str(event.get("start", "")))
    end = dt_util.parse_datetime(str(event.get("end", "")))
    if match is None or start is None or end is None:
        return None
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        return None
    return start, end, float(match.group(1))


def export_blocked_periods(events: Iterable[Any]) -> list[Period]:
    """Return the periods in which Tesla's sell tariff is below its top rate.

    Fails open: an empty list means "no known restriction". That is returned
    when the tariff has a single rate, and when any event cannot be read — a
    partly-understood tariff could misplace the top rate and block the hours
    export actually works in.
    """
    parsed = []
    for event in events:
        entry = _parse_event(event)
        if entry is None:
            _LOGGER.debug("Unreadable Tesla tariff event, not restricting: %s", event)
            return []
        parsed.append(entry)

    if not parsed:
        return []

    top_rate = max(price for _, _, price in parsed)
    return sorted((start, end) for start, end, price in parsed if price < top_rate)


def export_available_at(
    when: datetime,
    blocked_periods: Iterable[Period] | None,
    suppressed_until: datetime | None,
) -> bool:
    """Return False when export selected at *when* would not physically export.

    Args:
        when: Timezone-aware instant (a slot start, or now).
        blocked_periods: Output of ``export_blocked_periods``.
        suppressed_until: Hold-off set by the state machine's export-inversion
            watchdog, or None.

    """
    if when.tzinfo is None:
        # Cannot be compared with the aware tariff periods; do not restrict.
        return True
    if suppressed_until is not None and when < suppressed_until:
        return False
    return not any(start <= when < end for start, end in blocked_periods or ())

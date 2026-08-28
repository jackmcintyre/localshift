"""Slot schedule computation for hybrid timescale forecasts."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .utils import get_slot_duration_minutes, parse_slot_time

_LOGGER = logging.getLogger(__name__)

# Forecast slot constants
# 15-min slots throughout for consistent alignment with Solcast 30-minute periods
TOTAL_SLOTS = 96  # 24 hours × 4 slots/hour

# Hybrid timescale constants (Issue #327)
# Maximum number of 5-minute slots to use from Amber near-term forecast
MAX_5MIN_FORECAST_HOURS = 1  # Amber typically provides ~45-60 min of 5-min data

# Issue #510 Slice 2: Amber's detailedForecast starts each interval one second
# after the boundary (the 12:30 interval starts at 12:30:01). Absorb that
# offset when deciding which interval covers "now" — but only if it really is
# boundary noise, so a genuinely mid-interval start keeps its own boundary.
# Why 60s: Amber's observed offset is +1s (the 12:30 interval starts at
# 12:30:01), so this is ~60x margin — wide enough to absorb provider clock
# skew without ever reaching the next 5-minute boundary. If the offset were
# ever to exceed this, the covering entry stops being recognised and slot 0
# falls back to the synthetic next-interval borrow: the exact defect this
# slice removes. That failure is loud, not silent — it trips the
# SYNTHETIC SLOT FALLBACK warning below — but it is the reason to keep this
# generous rather than tighten it toward the observed +1s.
_BOUNDARY_OFFSET_TOLERANCE_S = 60


def _interval_origin(slot_start: datetime, duration_minutes: int) -> datetime:
    """Return the true interval boundary for an entry's start time.

    Floors slot_start down to the nearest duration_minutes boundary, but only
    treats that floor as the real origin when slot_start is within
    _BOUNDARY_OFFSET_TOLERANCE_S of it — a few seconds of provider clock skew,
    not a genuinely different (misaligned) start time.

    Args:
        slot_start: Entry's parsed start time
        duration_minutes: Entry's duration (5 or 30)

    Returns:
        The interval's true boundary, or slot_start unchanged if it is not
        boundary noise.

    """
    floored = slot_start.replace(
        minute=(slot_start.minute // duration_minutes) * duration_minutes,
        second=0,
        microsecond=0,
    )
    if (slot_start - floored).total_seconds() < _BOUNDARY_OFFSET_TOLERANCE_S:
        return floored
    return slot_start


def _covers_now(
    slot_start: datetime, duration_minutes: int, now_local: datetime
) -> bool:
    """Return True if this entry's interval contains now_local.

    Only 5- and 30-minute entries are eligible — 60-minute entries always
    return False here, which keeps _split_60min_slot's split-then-drop
    behaviour for the current hour completely unchanged (see module docstring
    constraint: 60-min path is untouched by Issue #510 Slice 2).

    Args:
        slot_start: Entry's parsed start time
        duration_minutes: Entry's duration (5, 30, or 60)
        now_local: Current local time

    Returns:
        True if [interval origin, interval origin + duration) contains now.

    """
    if duration_minutes not in (5, 30):
        return False
    origin = _interval_origin(slot_start, duration_minutes)
    return origin <= now_local < origin + timedelta(minutes=duration_minutes)


def _floor_to_5min(moment: datetime) -> datetime:
    """Floor a datetime down to the nearest 5-minute wall-clock boundary.

    Shared by the stale-sensor synthetic fallback and by the current-slot
    re-anchoring in `_parse_single_entry`, so the two "what does a 5-minute
    'now' slot look like" formulas can never drift apart.

    Args:
        moment: The datetime to floor.

    Returns:
        moment with minute rounded down to a multiple of 5 and seconds/
        microseconds zeroed.

    """
    current_5min = (moment.minute // 5) * 5
    return moment.replace(minute=current_5min, second=0, microsecond=0)


def compute_hybrid_slot_schedule(
    now_local: datetime,
    general_forecast: list[dict],
    ha_timezone: str,
    max_forecast_hours: int = 24,
) -> tuple[list[dict], dict]:
    """Build hybrid slot schedule: ALL 5-min slots, then 30-min.

    Issue #327: Uses native data granularities without interpolation.
    - Amber provides 5-min near-term (~45-60 min), then 30-min extended forecast
    - This function identifies 5-min slots and switches to 30-min at boundary

    NO INTERPOLATION - use actual data only.
    NO GAPS - 5-min slots end at 30-min boundary, 30-min starts immediately.

    Args:
        now_local: Current datetime in HA local timezone
        general_forecast: List of Amber price forecast entries with start_time, end_time, duration
        ha_timezone: HA configured timezone (e.g., "Australia/Sydney")
        max_forecast_hours: Maximum hours to forecast (default 24)

    Returns:
        Tuple of (slots, metadata) where:
        - slots: List of slot dicts with:
            - start: datetime of slot start
            - interval_minutes: 5 or 30
            - price: price in $/kWh
            - price_source: "forecast_current" (the entry whose interval covers
              now), "5min", "30min", or "synthetic" (stale-sensor fallback used
              only when no entry covers now). A covering 30-min entry is
              re-anchored to a 5-minute "now" quantum rather than kept at its
              own 30-min width, so slot 0's width never exceeds 5 minutes
              regardless of the feed's native granularity — see
              `_parse_single_entry`.
            - estimate: bool | None, the covering entry's Amber `estimate` flag
              (True pre-settlement, False once settled; None on synthetic or
              60-min-derived slots)
        - metadata: Dict with:
            - timezone: HA timezone
            - slot_intervals: {"5min": count, "30min": count}
            - transition_boundary: Time when 5-min switches to 30-min (or None)
            - total_slots: Total number of slots
            - horizon_hours: Actual time span covered by slots in hours

    """
    metadata = _create_initial_metadata(ha_timezone)

    if not general_forecast:
        _LOGGER.warning("compute_hybrid_slot_schedule: Empty general_forecast")
        return [], metadata

    # TEMPORARY DIAGNOSTIC (#510): slice 2 is live but every evaluation still
    # falls back to synthetic, meaning the entry covering "now" never reaches
    # the parser. Log what actually arrives so the upstream drop can be located.
    # Remove once the read path is fixed.
    _LOGGER.info(
        "GENERAL_FORECAST_IN: n=%d now_local=%s first3=%s",
        len(general_forecast or []),
        now_local.isoformat(),
        [
            {
                "start_time": str(e.get("start_time")),
                "per_kwh": e.get("per_kwh"),
                "duration": e.get("duration"),
                "estimate": e.get("estimate"),
            }
            for e in (general_forecast or [])[:3]
        ],
    )

    all_slots_raw = _parse_forecast_entries(general_forecast, now_local, ha_timezone)
    if not all_slots_raw:
        _LOGGER.warning("compute_hybrid_slot_schedule: No valid slots after parsing")
        return [], metadata

    all_slots_raw.sort(key=lambda x: x["start"])

    five_min_slots, thirty_min_slots = _separate_slots_by_duration(all_slots_raw)

    _LOGGER.debug(
        "compute_hybrid_slot_schedule: Found %d 5-min slots, %d 30-min slots",
        len(five_min_slots),
        len(thirty_min_slots),
    )

    cutoff_time = now_local + timedelta(hours=max_forecast_hours)
    slots, transition_boundary = _build_hybrid_schedule(
        five_min_slots, thirty_min_slots, cutoff_time
    )

    _ensure_current_slot_coverage(slots, now_local)

    if slots:
        _LOGGER.info(
            "SLOT0_CURRENT: start=%s interval=%dmin price=%.4f source=%s estimate=%s",
            slots[0]["start"].isoformat(),
            slots[0]["interval_minutes"],
            slots[0]["price"],
            slots[0]["price_source"],
            slots[0].get("estimate"),
        )

    _compute_slot_metadata(slots, metadata, transition_boundary)

    return slots, metadata


def _create_initial_metadata(ha_timezone: str) -> dict:
    """Create initial metadata dict.

    Args:
        ha_timezone: HA timezone string

    Returns:
        Initial metadata dict

    """
    return {
        "timezone": ha_timezone,
        "slot_intervals": {"5min": 0, "30min": 0},
        "transition_boundary": None,
        "total_slots": 0,
        "horizon_hours": 0.0,
    }


def _parse_forecast_entries(
    general_forecast: list[dict], now_local: datetime, ha_timezone: str
) -> list[dict]:
    """Parse forecast entries into slot dicts.

    Args:
        general_forecast: Raw forecast entries
        now_local: Current local time
        ha_timezone: HA timezone

    Returns:
        List of parsed slot dicts

    """
    all_slots_raw: list[dict] = []

    for entry in general_forecast:
        slot = _parse_single_entry(entry, now_local, ha_timezone)
        if slot:
            all_slots_raw.extend(slot if isinstance(slot, list) else [slot])

    return all_slots_raw


def _parse_single_entry(
    entry: dict, now_local: datetime, ha_timezone: str
) -> dict | list[dict] | None:
    """Parse a single forecast entry.

    Args:
        entry: Forecast entry dict
        now_local: Current local time
        ha_timezone: HA timezone

    Returns:
        Slot dict, list of slot dicts, or None

    """
    if not hasattr(entry, "get"):
        return None

    start_time_str = entry.get("start_time")
    if not start_time_str:
        return None

    slot_start = parse_slot_time(start_time_str, ha_timezone)
    if slot_start is None:
        return None

    duration_minutes = _get_entry_duration(entry, slot_start, ha_timezone)
    if duration_minutes is None or duration_minutes not in (5, 30, 60):
        return None

    # Issue #510 Slice 2: an entry whose interval covers "now" is retained as
    # the current slot even though its raw (pre-floor) start may read as
    # slightly in the past — that's the +1s Amber offset, not staleness. An
    # entry is only dropped once its interval has genuinely elapsed.
    is_current = _covers_now(slot_start, duration_minutes, now_local)
    if not is_current and slot_start < now_local:
        return None

    price = float(entry.get("per_kwh", 0))

    if duration_minutes == 60:
        return _split_60min_slot(slot_start, price)

    if is_current and duration_minutes == 30:
        # SCOPE NOTE (#510): this branch is OUT OF SCOPE per the spec's
        # Non-goals — "5-min price sites only … 30-min support is out of
        # scope". On a 5-minute Amber feed the entry covering "now" is
        # always a 5-minute one, so this branch is unreachable on the site
        # this was built for and has never been validated against live
        # 30-minute data. Kept because the reasoning below is sound and it
        # costs nothing dormant; re-verify against a real 30-minute feed
        # before relying on it.
        # Review feedback on #510 Slice 2: retaining this entry at its own
        # 30-minute width would make slot 0 up to 30 minutes wide no matter
        # how far into the interval "now" actually is (e.g. still the full
        # 30-min slot with ten seconds of real time left in it). Every DP
        # energy term scales by slot width (slot_hours = interval_minutes /
        # 60), so a mostly-elapsed slot 0 lets the optimiser book
        # charge/discharge time that no longer exists, and it drags
        # core.py's DW-runway anchor (minutes_to_dw) and the published
        # precharge_runway_quantum_min back with it by the same amount.
        # Re-anchor slot 0 to the same 5-minute "now" quantum the
        # stale-sensor fallback below already uses, priced from this real
        # covering entry instead of a synthetic borrowed price — the brief
        # asked for the covering entry's PRICE, not for slot 0 to grow to
        # 30 minutes wide. A covering 5-min entry is already at (or under)
        # that quantum, so it keeps its own start/width untouched below.
        return {
            "start": _floor_to_5min(now_local),
            "interval_minutes": 5,
            "price": price,
            "price_source": "forecast_current",
            "estimate": entry.get("estimate"),
        }

    return {
        "start": slot_start,
        "interval_minutes": duration_minutes,
        "price": price,
        "price_source": "forecast_current"
        if is_current
        else ("5min" if duration_minutes == 5 else "30min"),
        "estimate": entry.get("estimate"),
    }


def _get_entry_duration(
    entry: dict, slot_start: datetime, ha_timezone: str
) -> int | None:
    """Get duration for a forecast entry.

    Args:
        entry: Forecast entry
        slot_start: Parsed start time
        ha_timezone: HA timezone

    Returns:
        Duration in minutes or None

    """
    duration_minutes = get_slot_duration_minutes(entry)
    if duration_minutes is not None:
        return duration_minutes

    end_time_str = entry.get("end_time")
    if not end_time_str:
        return None

    slot_end = parse_slot_time(end_time_str, ha_timezone)
    if slot_end:
        return int((slot_end - slot_start).total_seconds() / 60)

    return None


def _split_60min_slot(slot_start: datetime, price: float) -> list[dict]:
    """Split a 60-minute slot into two 30-minute slots.

    Args:
        slot_start: Slot start time
        price: Price per kWh

    Returns:
        List of two 30-min slot dicts

    """
    return [
        {
            "start": slot_start,
            "interval_minutes": 30,
            "price": price,
            "price_source": "30min",
        },
        {
            "start": slot_start + timedelta(minutes=30),
            "interval_minutes": 30,
            "price": price,
            "price_source": "30min",
        },
    ]


def _separate_slots_by_duration(slots: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separate slots into 5-min and 30-min lists.

    Args:
        slots: All slots

    Returns:
        Tuple of (5-min slots, 30-min slots)

    """
    five_min = [s for s in slots if s["interval_minutes"] == 5]
    thirty_min = [s for s in slots if s["interval_minutes"] == 30]
    return five_min, thirty_min


def _build_hybrid_schedule(
    five_min_slots: list[dict], thirty_min_slots: list[dict], cutoff_time: datetime
) -> tuple[list[dict], datetime | None]:
    """Build hybrid schedule combining 5-min and 30-min slots.

    Args:
        five_min_slots: 5-minute slots
        thirty_min_slots: 30-minute slots
        cutoff_time: Maximum forecast time

    Returns:
        Tuple of (combined slots, transition boundary)

    """
    slots: list[dict] = []
    transition_boundary = None

    slots.extend(five_min_slots)

    if five_min_slots:
        last_5min_end = five_min_slots[-1]["start"] + timedelta(minutes=5)
        transition_boundary = _add_30min_after_transition(
            slots, thirty_min_slots, last_5min_end, cutoff_time
        )
    else:
        _add_all_30min_slots(slots, thirty_min_slots, cutoff_time)
        if thirty_min_slots:
            transition_boundary = thirty_min_slots[0]["start"]

    slots.sort(key=lambda x: x["start"])
    return slots, transition_boundary


def _add_30min_after_transition(
    slots: list[dict],
    thirty_min_slots: list[dict],
    last_5min_end: datetime,
    cutoff_time: datetime,
) -> datetime | None:
    """Add 30-min slots starting after 5-min transition.

    Args:
        slots: Slot list to extend
        thirty_min_slots: 30-minute slots
        last_5min_end: End time of last 5-min slot
        cutoff_time: Maximum forecast time

    Returns:
        Transition boundary time or None

    """
    for slot in thirty_min_slots:
        if slot["start"] >= last_5min_end:
            idx = thirty_min_slots.index(slot)
            for s in thirty_min_slots[idx:]:
                if s["start"] < cutoff_time:
                    slots.append(s)
            return slot["start"]
    return None


def _add_all_30min_slots(
    slots: list[dict], thirty_min_slots: list[dict], cutoff_time: datetime
) -> None:
    """Add all 30-min slots within cutoff.

    Args:
        slots: Slot list to extend
        thirty_min_slots: 30-minute slots
        cutoff_time: Maximum forecast time

    """
    for slot in thirty_min_slots:
        if slot["start"] < cutoff_time:
            slots.append(slot)


def _ensure_current_slot_coverage(slots: list[dict], now_local: datetime) -> None:
    """Ensure there's a slot covering 'now' by adding a synthetic slot if needed.

    Issue #510 Slice 2: this is the stale-sensor FALLBACK, not the steady
    state. In normal operation _parse_single_entry already retains the real
    forecast entry covering "now" as slot 0 (price_source="forecast_current"),
    so this function is a no-op. It only fires when no entry covers now at
    all — e.g. the price sensor's forecast has gone stale.

    Args:
        slots: Slot list (modified in place)
        now_local: Current local time

    """
    if not slots:
        return

    covers_now = _covers_now(slots[0]["start"], slots[0]["interval_minutes"], now_local)

    _LOGGER.info(
        "HYBRID_SLOTS: slots=%d, first_slot=%s, now_local=%s, comparison=%s",
        len(slots),
        slots[0]["start"].strftime("%H:%M:%S"),
        now_local.strftime("%H:%M:%S"),
        "covers now" if covers_now else "does not cover now",
    )

    if covers_now:
        return

    synthetic_start = _floor_to_5min(now_local)
    estimated_price = slots[0]["price"] if slots else 0.0

    synthetic_slot = {
        "start": synthetic_start,
        "interval_minutes": 5,
        "price": estimated_price,
        "price_source": "synthetic",
    }
    slots.insert(0, synthetic_slot)

    _LOGGER.warning(
        "SYNTHETIC SLOT FALLBACK: no forecast entry covers %s (first entry %s, "
        "gap=%.0fs) — price borrowed from the next interval; check the price "
        "sensor for staleness",
        synthetic_start.strftime("%H:%M:%S"),
        slots[1]["start"].strftime("%H:%M:%S") if len(slots) > 1 else "N/A",
        (slots[1]["start"] - synthetic_start).total_seconds() if len(slots) > 1 else 0,
    )


def _compute_slot_metadata(
    slots: list[dict], metadata: dict, transition_boundary: datetime | None
) -> None:
    """Compute and store slot metadata.

    Args:
        slots: Final slot list
        metadata: Metadata dict (modified in place)
        transition_boundary: Transition boundary time

    """
    five_min_count = len([s for s in slots if s["interval_minutes"] == 5])
    thirty_min_count = len([s for s in slots if s["interval_minutes"] in (30, 60)])

    metadata["slot_intervals"] = {"5min": five_min_count, "30min": thirty_min_count}
    metadata["total_slots"] = len(slots)
    metadata["transition_boundary"] = (
        transition_boundary.strftime("%H:%M") if transition_boundary else None
    )

    if slots:
        horizon_delta = slots[-1]["start"] - slots[0]["start"]
        last_slot_duration = slots[-1]["interval_minutes"]
        horizon_hours = (horizon_delta.total_seconds() / 3600.0) + (
            last_slot_duration / 60.0
        )
        metadata["horizon_hours"] = round(horizon_hours, 2)

        _log_slot_details(slots)

    _LOGGER.info(
        "Hybrid slot schedule: %d 5-min slots, %d 30-min slots, horizon=%.2fh, transition at %s",
        five_min_count,
        thirty_min_count,
        metadata["horizon_hours"],
        metadata["transition_boundary"] or "N/A",
    )


def _log_slot_details(slots: list[dict]) -> None:
    """Log timezone information for slots.

    Args:
        slots: Slot list

    """
    _LOGGER.info(
        "HYBRID_SLOTS: First 5 slots (with TZ): %s",
        [s["start"].isoformat() for s in slots[:5]],
    )
    _LOGGER.info(
        "HYBRID_SLOTS: Slot 0 TZ info: %s (offset=%s)",
        slots[0]["start"].isoformat(),
        slots[0]["start"].utcoffset(),
    )
    if len(slots) > 1:
        _LOGGER.info(
            "HYBRID_SLOTS: Slot 1 TZ info: %s (offset=%s)",
            slots[1]["start"].isoformat(),
            slots[1]["start"].utcoffset(),
        )

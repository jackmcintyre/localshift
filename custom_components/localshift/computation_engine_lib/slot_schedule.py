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
            - price_source: "5min" or "30min"
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
    if not isinstance(entry, dict):
        return None

    start_time_str = entry.get("start_time")
    if not start_time_str:
        return None

    slot_start = parse_slot_time(start_time_str, ha_timezone)
    if slot_start is None or slot_start < now_local:
        return None

    duration_minutes = _get_entry_duration(entry, slot_start, ha_timezone)
    if duration_minutes is None or duration_minutes not in (5, 30, 60):
        return None

    price = float(entry.get("per_kwh", 0))

    if duration_minutes == 60:
        return _split_60min_slot(slot_start, price)

    return {
        "start": slot_start,
        "interval_minutes": duration_minutes,
        "price": price,
        "price_source": "5min" if duration_minutes == 5 else "30min",
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
    """Ensure there's a slot covering 'now' by adding synthetic slot if needed.

    Args:
        slots: Slot list (modified in place)
        now_local: Current local time
    """
    if not slots:
        return

    _LOGGER.info(
        "HYBRID_SLOTS: slots=%d, first_slot=%s, now_local=%s, comparison=%s",
        len(slots),
        slots[0]["start"].strftime("%H:%M:%S"),
        now_local.strftime("%H:%M:%S"),
        "first > now" if slots[0]["start"] > now_local else "first <= now",
    )

    if slots[0]["start"] <= now_local:
        return

    current_5min = (now_local.minute // 5) * 5
    synthetic_start = now_local.replace(minute=current_5min, second=0, microsecond=0)
    estimated_price = slots[0]["price"] if slots else 0.0

    synthetic_slot = {
        "start": synthetic_start,
        "interval_minutes": 5,
        "price": estimated_price,
        "price_source": "synthetic",
    }
    slots.insert(0, synthetic_slot)

    _LOGGER.info(
        "Created synthetic slot at %s (Amber first slot was at %s, gap=%.0fs)",
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

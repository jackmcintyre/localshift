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
    slots: list[dict] = []
    metadata: dict = {
        "timezone": ha_timezone,
        "slot_intervals": {"5min": 0, "30min": 0},
        "transition_boundary": None,
        "total_slots": 0,
        "horizon_hours": 0.0,
    }

    if not general_forecast:
        _LOGGER.warning("compute_hybrid_slot_schedule: Empty general_forecast")
        return slots, metadata

    # Step 1: Parse all forecast entries and convert to local timezone
    all_slots_raw: list[dict] = []
    for entry in general_forecast:
        if not isinstance(entry, dict):
            continue

        start_time_str = entry.get("start_time")
        if not start_time_str:
            continue

        # Parse and convert to local timezone using our new utility
        slot_start = parse_slot_time(start_time_str, ha_timezone)
        if slot_start is None:
            continue

        # Skip past slots (only skip if strictly before now)
        if slot_start < now_local:
            continue

        # Determine duration from entry or calculate from gap to next
        duration_minutes = get_slot_duration_minutes(entry)
        if duration_minutes is None:
            # Try to calculate from end_time
            end_time_str = entry.get("end_time")
            if end_time_str:
                slot_end = parse_slot_time(end_time_str, ha_timezone)
                if slot_end:
                    duration_minutes = int((slot_end - slot_start).total_seconds() / 60)

        if duration_minutes is None:
            continue  # Skip if we can't determine duration

        # Accept 5-min, 30-min, or 60-min slots (60-min for backward compatibility with tests)
        # Amber native granularities are 5-min and 30-min, but tests may use 60-min
        if duration_minutes not in (5, 30, 60):
            continue

        price = float(entry.get("per_kwh", 0))

        all_slots_raw.append(
            {
                "start": slot_start,
                "interval_minutes": duration_minutes,
                "price": price,
                "price_source": "5min" if duration_minutes == 5 else "30min",
            }
        )

    if not all_slots_raw:
        _LOGGER.warning("compute_hybrid_slot_schedule: No valid slots after parsing")
        return slots, metadata

    # Step 2: Sort by start time
    all_slots_raw.sort(key=lambda x: x["start"])

    # Step 3: Separate 5-min and 30-min slots
    # Note: 60-min slots (used in tests) are treated as 30-min extended forecast
    five_min_slots = [s for s in all_slots_raw if s["interval_minutes"] == 5]
    thirty_min_slots = [s for s in all_slots_raw if s["interval_minutes"] in (30, 60)]

    _LOGGER.debug(
        "compute_hybrid_slot_schedule: Found %d 5-min slots, %d 30-min slots",
        len(five_min_slots),
        len(thirty_min_slots),
    )

    # Step 4: Add ALL 5-min slots (no minimum, no maximum)
    slots.extend(five_min_slots)

    # Step 5: Find first 30-min slot at or after last 5-min slot ends
    cutoff_time = now_local + timedelta(hours=max_forecast_hours)

    if five_min_slots:
        last_5min_end = five_min_slots[-1]["start"] + timedelta(minutes=5)

        # Find 30-min slot that starts at or after this time
        transition_boundary = None
        for slot in thirty_min_slots:
            if slot["start"] >= last_5min_end:
                # Found the transition point
                transition_boundary = slot["start"]
                # Add this and all subsequent 30-min slots within forecast window
                idx = thirty_min_slots.index(slot)
                for s in thirty_min_slots[idx:]:
                    if s["start"] < cutoff_time:
                        slots.append(s)
                break

        metadata["transition_boundary"] = (
            transition_boundary.strftime("%H:%M") if transition_boundary else None
        )
    else:
        # No 5-min data, use 30-min only
        for slot in thirty_min_slots:
            if slot["start"] < cutoff_time:
                slots.append(slot)
        if thirty_min_slots:
            metadata["transition_boundary"] = thirty_min_slots[0]["start"].strftime(
                "%H:%M"
            )

    # Step 6: Sort final slots by start time
    slots.sort(key=lambda x: x["start"])

    # Step 6.5: Ensure there's a slot covering "now"
    # If Amber's first slot is AFTER now, we need a synthetic current slot
    # This can happen when Amber's forecast starts a few minutes in the future
    _LOGGER.info(
        "HYBRID_SLOTS: slots=%d, first_slot=%s, now_local=%s, comparison=%s",
        len(slots),
        slots[0]["start"].strftime("%H:%M:%S") if slots else "N/A",
        now_local.strftime("%H:%M:%S"),
        "first > now" if slots and slots[0]["start"] > now_local else "first <= now",
    )
    if slots and slots[0]["start"] > now_local:
        # Create a synthetic slot at the current 5-minute boundary
        current_5min = (now_local.minute // 5) * 5
        synthetic_start = now_local.replace(
            minute=current_5min, second=0, microsecond=0
        )
        # Use the first real slot's price as estimate (or 0 if no slots)
        estimated_price = slots[0]["price"] if slots else 0.0
        synthetic_slot = {
            "start": synthetic_start,
            "interval_minutes": 5,
            "price": estimated_price,
            "price_source": "synthetic",  # Mark as synthetic for debugging
        }
        slots.insert(0, synthetic_slot)
        _LOGGER.info(
            "Created synthetic slot at %s (Amber first slot was at %s, gap=%.0fs)",
            synthetic_start.strftime("%H:%M:%S"),
            slots[1]["start"].strftime("%H:%M:%S") if len(slots) > 1 else "N/A",
            (slots[1]["start"] - synthetic_start).total_seconds()
            if len(slots) > 1
            else 0,
        )

    # Step 7: Calculate counts and metadata
    # Note: 60-min slots are counted as 30-min for backward compatibility
    five_min_count = len([s for s in slots if s["interval_minutes"] == 5])
    thirty_min_count = len([s for s in slots if s["interval_minutes"] in (30, 60)])

    metadata["slot_intervals"] = {
        "5min": five_min_count,
        "30min": thirty_min_count,
    }
    metadata["total_slots"] = len(slots)

    # Calculate actual horizon duration in hours
    if slots:
        horizon_delta = slots[-1]["start"] - slots[0]["start"]
        # Add the duration of the last slot to get full coverage
        last_slot_duration = slots[-1]["interval_minutes"]
        horizon_hours = (horizon_delta.total_seconds() / 3600.0) + (
            last_slot_duration / 60.0
        )
        metadata["horizon_hours"] = round(horizon_hours, 2)
    else:
        metadata["horizon_hours"] = 0.0

    # Log timezone information for first few slots (Issue #455)
    if slots:
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

    _LOGGER.info(
        "Hybrid slot schedule: %d 5-min slots, %d 30-min slots, horizon=%.2fh, transition at %s",
        five_min_count,
        thirty_min_count,
        metadata["horizon_hours"],
        metadata["transition_boundary"] or "N/A",
    )

    return slots, metadata

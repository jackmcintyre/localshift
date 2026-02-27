"""Solar forecasting utilities.

This module contains methods for solar forecasting that are self-contained
and don't modify CoordinatorData directly.

Price-related utilities have been moved to price_calculator.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

_LOGGER = logging.getLogger(__name__)

from homeassistant.util import dt as dt_util

from .utils import parse_forecast_dt


def get_solar_for_15min_slot(
    solcast_forecasts: list[dict[str, Any]],
    slot_start: datetime,
    debug_log: bool = False,
) -> float:
    """Get solar forecast (kWh) for a 15-minute slot from Solcast 30-min periods.

    Uses overlap-weighted accumulation: sums contributions from all Solcast periods
    that overlap the slot, weighted by the overlap fraction.

    This correctly handles 15-min slots that straddle two Solcast 30-min periods
    (e.g. a slot starting at :20 ends at :35, overlapping both :00-:30 and :30-:60).

    IMPORTANT: Solcast's pv_estimate values represent average power (kWh per hour),
    NOT energy per period. So we divide overlap_seconds by 3600 (seconds per hour),
    not by the period duration.
    """
    if not solcast_forecasts:
        return 0.0

    # Ensure slot boundaries are timezone-aware local datetimes
    if slot_start.tzinfo is None:
        slot_start = dt_util.as_local(dt_util.as_utc(slot_start))
    else:
        slot_start = dt_util.as_local(slot_start)

    slot_end = slot_start + timedelta(minutes=15)
    period_duration = timedelta(minutes=30)
    total_solar = 0.0

    # Debug: log first few entries to trace data structure
    if debug_log and len(solcast_forecasts) > 0:
        _LOGGER.debug(
            "SOLAR_DEBUG: First entry keys: %s",
            list(solcast_forecasts[0].keys()) if solcast_forecasts else "empty",
        )
        for i, entry in enumerate(solcast_forecasts[:3]):
            if isinstance(entry, dict):
                _LOGGER.debug(
                    "SOLAR_DEBUG: Entry %d: period_start=%s, pv_estimate=%s, pv_estimate10=%s, estimate=%s, estimate10=%s",
                    i,
                    entry.get("period_start") or entry.get("start"),
                    entry.get("pv_estimate"),
                    entry.get("pv_estimate10"),
                    entry.get("estimate"),
                    entry.get("estimate10"),
                )

    # Track if we found any matching period for this slot
    found_match = False
    matched_entries = []

    for entry in solcast_forecasts:
        if not isinstance(entry, dict):
            continue

        period_start_raw = entry.get("period_start") or entry.get("start")
        if period_start_raw is None:
            continue

        start_dt = dt_util.parse_datetime(str(period_start_raw))
        if not start_dt:
            continue

        start_local = dt_util.as_local(start_dt)
        end_local = start_local + period_duration

        overlap_start = max(start_local, slot_start)
        overlap_end = min(end_local, slot_end)
        overlap_seconds = (overlap_end - overlap_start).total_seconds()

        if overlap_seconds > 0:
            found_match = True

            # Get all possible values for debugging
            pv_estimate = entry.get("pv_estimate")
            pv_estimate10 = entry.get("pv_estimate10")
            estimate = entry.get("estimate")
            estimate10 = entry.get("estimate10")

            # Use pv_estimate (expected) as primary, fallback to pv_estimate10 (pessimistic)
            period_kwh = float(
                pv_estimate or estimate or pv_estimate10 or estimate10 or 0.0
            )
            # pv_estimate is kWh per HOUR, so divide by 3600 seconds
            # NOT by period duration (would give 2x error for 30-min periods)
            overlap_fraction = overlap_seconds / 3600.0
            contribution = period_kwh * overlap_fraction
            total_solar += contribution

            matched_entries.append(
                {
                    "period_start": start_local.strftime("%Y-%m-%d %H:%M"),
                    "pv_estimate": pv_estimate,
                    "pv_estimate10": pv_estimate10,
                    "selected_value": period_kwh,
                    "overlap_pct": overlap_fraction * 100,
                    "contribution": contribution,
                }
            )

    # Debug: log detailed match info for afternoon slots (14:00-18:00)
    slot_hour = slot_start.hour
    if debug_log and found_match and 14 <= slot_hour <= 18:
        _LOGGER.debug(
            "SOLAR_DEBUG_SLOT: Slot %s found %d matching periods, total=%.4f kWh",
            slot_start.strftime("%Y-%m-%d %H:%M"),
            len(matched_entries),
            total_solar,
        )
        for me in matched_entries:
            _LOGGER.debug(
                "SOLAR_DEBUG_MATCH: period=%s, pv_estimate=%s, pv_estimate10=%s, selected=%.4f, overlap=%.1f%%, contribution=%.4f",
                me["period_start"],
                me["pv_estimate"],
                me["pv_estimate10"],
                me["selected_value"],
                me["overlap_pct"],
                me["contribution"],
            )

    return total_solar


def get_solar_for_15min_slot_or_none(
    solcast_forecasts: list[dict[str, Any]],
    slot_start: datetime,
) -> float | None:
    """Get solar forecast (kWh), returning None when forecast data is missing.

    Unlike get_solar_for_15min_slot(), this returns None (not 0.0) when:
    - The forecast list is empty
    - No period in the forecast overlaps the requested slot

    This allows callers to distinguish "missing forecast data" from
    "genuinely zero solar production" (e.g., nighttime).

    Returns:
        Solar energy in kWh for the 15-min slot, or None if no forecast data.
    """
    if not solcast_forecasts:
        return None

    # Ensure slot boundaries are timezone-aware local datetimes
    if slot_start.tzinfo is None:
        slot_start = dt_util.as_local(dt_util.as_utc(slot_start))
    else:
        slot_start = dt_util.as_local(slot_start)

    slot_end = slot_start + timedelta(minutes=15)
    period_duration = timedelta(minutes=30)
    total_solar = 0.0

    # Track if we found any matching period for this slot
    found_match = False

    for entry in solcast_forecasts:
        if not isinstance(entry, dict):
            continue

        period_start_raw = entry.get("period_start") or entry.get("start")
        if period_start_raw is None:
            continue

        start_dt = dt_util.parse_datetime(str(period_start_raw))
        if not start_dt:
            continue

        start_local = dt_util.as_local(start_dt)
        end_local = start_local + period_duration

        overlap_start = max(start_local, slot_start)
        overlap_end = min(end_local, slot_end)
        overlap_seconds = (overlap_end - overlap_start).total_seconds()

        if overlap_seconds > 0:
            found_match = True

            # Use pv_estimate (expected) as primary, fallback to pv_estimate10 (pessimistic)
            period_kwh = float(
                entry.get("pv_estimate")
                or entry.get("estimate")
                or entry.get("pv_estimate10")
                or entry.get("estimate10")
                or 0.0
            )
            # pv_estimate is kWh per HOUR, so divide by 3600 seconds
            overlap_fraction = overlap_seconds / 3600.0
            total_solar += period_kwh * overlap_fraction

    # Return None if no matching period was found (forecast doesn't cover this slot)
    if not found_match:
        return None

    return total_solar


def get_solar_for_5min_slot(
    solcast_forecasts: list[dict[str, Any]],
    slot_start: datetime,
) -> float:
    """Get solar forecast (kWh) for a 5-minute slot from Solcast 30-min periods.

    Uses containment check: returns 1/6 of the period kWh because a 5-minute
    slot is exactly 5/30 = 1/6 of the 30-minute Solcast period.

    Args:
        solcast_forecasts: List of Solcast forecast dicts (today + tomorrow).
        slot_start: Start of the 5-minute slot (timezone-aware or naive local).

    Returns:
        Solar energy in kWh for the 5-minute slot, or 0.0 if no data found.
    """
    if not solcast_forecasts:
        return 0.0

    # Ensure slot boundaries are timezone-aware local datetimes
    if slot_start.tzinfo is None:
        slot_start = dt_util.as_local(dt_util.as_utc(slot_start))
    else:
        slot_start = dt_util.as_local(slot_start)

    slot_end = slot_start + timedelta(minutes=5)
    period_duration = timedelta(minutes=30)

    for entry in solcast_forecasts:
        if not isinstance(entry, dict):
            continue

        period_start_raw = entry.get("period_start") or entry.get("start")
        if period_start_raw is None:
            continue

        start_dt = dt_util.parse_datetime(str(period_start_raw))
        if not start_dt:
            continue

        start_local = dt_util.as_local(start_dt)
        end_local = start_local + period_duration
        # Use pv_estimate (expected) as primary, fallback to pv_estimate10 (pessimistic)
        period_kwh = float(
            entry.get("pv_estimate")
            or entry.get("estimate")
            or entry.get("pv_estimate10")
            or entry.get("estimate10")
            or 0.0
        )

        # Containment check: the 5-min slot must be fully inside the 30-min period
        if slot_start >= start_local and slot_end <= end_local:
            # pv_estimate is kWh per HOUR, so 5 min = 5/60 = 1/12 of an hour
            return period_kwh * (5.0 / 60.0)

    return 0.0


def get_solar_for_30min_slot(
    solcast_forecasts: list[dict[str, Any]],
    slot_start: datetime,
) -> float:
    """Get solar forecast (kWh) for a 30-minute slot from Solcast 30-min periods.

    Uses overlap-weighted accumulation: sums contributions from all Solcast periods
    that overlap the slot, weighted by the overlap fraction.

    This handles cases where Amber 30-min slots don't exactly align with Solcast
    30-min periods (e.g., different start times due to data source differences).

    IMPORTANT: Solcast's pv_estimate values represent average power (kWh per hour),
    NOT energy per period. So we divide overlap_seconds by 3600 (seconds per hour).

    Args:
        solcast_forecasts: List of Solcast forecast dicts (today + tomorrow).
        slot_start: Start of the 30-minute slot (timezone-aware or naive local).

    Returns:
        Solar energy in kWh for the 30-minute slot, or 0.0 if no data found.
    """
    if not solcast_forecasts:
        return 0.0

    # Ensure slot boundaries are timezone-aware local datetimes
    if slot_start.tzinfo is None:
        slot_start = dt_util.as_local(dt_util.as_utc(slot_start))
    else:
        slot_start = dt_util.as_local(slot_start)

    slot_end = slot_start + timedelta(minutes=30)
    period_duration = timedelta(minutes=30)
    total_solar = 0.0

    for entry in solcast_forecasts:
        if not isinstance(entry, dict):
            continue

        period_start_raw = entry.get("period_start") or entry.get("start")
        if period_start_raw is None:
            continue

        start_dt = dt_util.parse_datetime(str(period_start_raw))
        if not start_dt:
            continue

        start_local = dt_util.as_local(start_dt)
        end_local = start_local + period_duration

        # Calculate overlap between slot and Solcast period
        overlap_start = max(start_local, slot_start)
        overlap_end = min(end_local, slot_end)
        overlap_seconds = (overlap_end - overlap_start).total_seconds()

        if overlap_seconds > 0:
            # Use pv_estimate (expected) as primary, fallback to pv_estimate10 (pessimistic)
            period_kwh = float(
                entry.get("pv_estimate")
                or entry.get("estimate")
                or entry.get("pv_estimate10")
                or entry.get("estimate10")
                or 0.0
            )
            # pv_estimate is kWh per HOUR, so divide by 3600 seconds
            overlap_fraction = overlap_seconds / 3600.0
            total_solar += period_kwh * overlap_fraction

    return total_solar


def get_solar_for_slot_by_interval(
    solcast_forecasts: list[dict[str, Any]],
    slot_start: datetime,
    interval_minutes: int,
) -> float:
    """Get solar forecast (kWh) for a slot with variable duration.

    Dispatches to the appropriate function based on interval_minutes.
    Issue #327: Supports hybrid 5-min/30-min timescale.

    Args:
        solcast_forecasts: List of Solcast forecast dicts (today + tomorrow).
        slot_start: Start of the slot (timezone-aware or naive local).
        interval_minutes: Slot duration in minutes (5, 15, or 30).

    Returns:
        Solar energy in kWh for the slot, or 0.0 if no data found.
    """
    if interval_minutes == 5:
        return get_solar_for_5min_slot(solcast_forecasts, slot_start)
    elif interval_minutes == 15:
        return get_solar_for_15min_slot(solcast_forecasts, slot_start)
    elif interval_minutes == 30:
        return get_solar_for_30min_slot(solcast_forecasts, slot_start)
    else:
        _LOGGER.warning(
            "Unsupported slot interval %d minutes, defaulting to 15-min",
            interval_minutes,
        )
        return get_solar_for_15min_slot(solcast_forecasts, slot_start)


def get_solar_for_slot(
    solcast_forecasts: list[dict[str, Any]],
    slot_start: datetime,
) -> float:
    """Get solar forecast (kWh) for one hourly slot from Solcast half-hour periods."""
    if not solcast_forecasts:
        return 0.0

    # Ensure slot boundaries are timezone-aware local datetimes
    if slot_start.tzinfo is None:
        slot_start = dt_util.as_local(dt_util.as_utc(slot_start))
    else:
        slot_start = dt_util.as_local(slot_start)

    slot_end = slot_start + timedelta(hours=1)
    period_duration = timedelta(minutes=30)

    total_solar = 0.0

    for entry in solcast_forecasts:
        try:
            if not isinstance(entry, dict):
                continue

            period_start_raw = entry.get("period_start") or entry.get("start")
            if period_start_raw is None:
                continue

            start_dt = dt_util.parse_datetime(str(period_start_raw))
            if not start_dt:
                continue

            start_local = dt_util.as_local(start_dt)
            end_local = start_local + period_duration

            # overlap between [start_local, end_local) and [slot_start, slot_end)
            overlap_start = max(start_local, slot_start)
            overlap_end = min(end_local, slot_end)
            overlap_seconds = (overlap_end - overlap_start).total_seconds()

            if overlap_seconds > 0:
                # Use pv_estimate (expected) as primary, fallback to pv_estimate10 (pessimistic)
                period_kwh = float(
                    entry.get("pv_estimate")
                    or entry.get("estimate")
                    or entry.get("pv_estimate10")
                    or entry.get("estimate10")
                    or 0.0
                )
                # pv_estimate is kWh per HOUR, so divide by 3600 seconds
                overlap_fraction = overlap_seconds / 3600.0
                total_solar += period_kwh * overlap_fraction
        except (ValueError, TypeError):
            continue

    return total_solar


def sum_solar_before_target(
    solcast: list[dict[str, Any]],
    now_dt: datetime,
    target_hour: int,
) -> float:
    """Sum pessimistic solar kWh (pv_estimate10) from now until target_hour.

    NOTE: pv_estimate10 values represent average power (kWh per hour),
    NOT energy per period. We need to multiply by the time fraction.
    """
    target_dt = now_dt.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    period_duration = timedelta(minutes=30)
    total = 0.0
    for period in solcast:
        period_start = parse_forecast_dt(period.get("period_start"))
        if period_start is None:
            continue
        ps_local = dt_util.as_local(period_start)
        period_end = ps_local + period_duration
        # pv_estimate10 is kWh per HOUR (average power)
        kwh_per_hour = float(period.get("pv_estimate10", 0))

        if ps_local >= target_dt:
            # Period starts at or after target — skip
            continue

        if ps_local >= now_dt:
            # Fully future period before target
            # 30 min = 0.5 hour, so multiply by 0.5
            total += kwh_per_hour * 0.5
        elif period_end > now_dt:
            # In-progress period — prorate remaining fraction
            remaining_seconds = (period_end - now_dt).total_seconds()
            # Divide by 3600 to convert to hours (pv_estimate is per hour)
            fraction_of_hour = remaining_seconds / 3600.0
            total += kwh_per_hour * fraction_of_hour

    return total

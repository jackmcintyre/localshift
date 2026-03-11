"""Solar forecasting utilities.

This module contains methods for solar forecasting that are self-contained
and don't modify CoordinatorData directly.

Price-related utilities have been moved to price_calculator.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


def _parse_forecast_dt(dt_str: str | None) -> datetime | None:
    """Parse an ISO format datetime string from forecast data."""
    if dt_str is None:
        return None
    try:
        return dt_util.parse_datetime(str(dt_str))
    except (ValueError, TypeError):
        return None


def _normalize_slot_time(slot_start: datetime) -> datetime:
    """Ensure slot_start is a timezone-aware local datetime."""
    if slot_start.tzinfo is None:
        return dt_util.as_local(dt_util.as_utc(slot_start))
    return dt_util.as_local(slot_start)


def _get_period_estimate(entry: dict[str, Any]) -> float:
    """Extract the best available estimate from a forecast entry.

    Priority: pv_estimate > estimate > pv_estimate10 > estimate10 > 0.0
    """
    return float(
        entry.get("pv_estimate")
        or entry.get("estimate")
        or entry.get("pv_estimate10")
        or entry.get("estimate10")
        or 0.0
    )


def _process_forecast_entry(
    entry: dict[str, Any] | Any,
    slot_start: datetime,
    slot_end: datetime,
    period_duration: timedelta,
) -> dict[str, Any] | None:
    """Process a single forecast entry and return contribution if overlapping.

    Returns None if:
    - Entry is not a dict
    - Missing period_start/start field
    - Invalid datetime in period_start
    - No overlap with the slot

    Returns dict with contribution details if period overlaps slot.
    """
    if not isinstance(entry, dict):
        return None

    period_start_raw = entry.get("period_start") or entry.get("start")
    if period_start_raw is None:
        return None

    start_dt = dt_util.parse_datetime(str(period_start_raw))
    if not start_dt:
        return None

    start_local = dt_util.as_local(start_dt)
    end_local = start_local + period_duration

    overlap_start = max(start_local, slot_start)
    overlap_end = min(end_local, slot_end)
    overlap_seconds = (overlap_end - overlap_start).total_seconds()

    if overlap_seconds <= 0:
        return None

    period_kwh = _get_period_estimate(entry)
    overlap_fraction = overlap_seconds / 3600.0
    contribution = period_kwh * overlap_fraction

    return {
        "period_start": start_local.strftime("%Y-%m-%d %H:%M"),
        "pv_estimate": entry.get("pv_estimate"),
        "pv_estimate10": entry.get("pv_estimate10"),
        "selected_value": period_kwh,
        "overlap_pct": overlap_fraction * 100,
        "contribution": contribution,
    }


def _log_debug_forecast_entries(
    solcast_forecasts: list[dict[str, Any]],
) -> None:
    """Log debug info for first few forecast entries."""
    if not solcast_forecasts:
        return

    _LOGGER.debug(
        "SOLAR_DEBUG: First entry keys: %s",
        list(solcast_forecasts[0].keys()),
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


def _log_debug_matched_entries(
    slot_start: datetime,
    matched_entries: list[dict[str, Any]],
    total_solar: float,
) -> None:
    """Log debug info for matched entries in afternoon slots."""
    slot_hour = slot_start.hour
    if not (14 <= slot_hour <= 18):
        return

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

    slot_start = _normalize_slot_time(slot_start)
    slot_end = slot_start + timedelta(minutes=15)
    period_duration = timedelta(minutes=30)

    if debug_log:
        _log_debug_forecast_entries(solcast_forecasts)

    total_solar = 0.0
    matched_entries: list[dict[str, Any]] = []

    for entry in solcast_forecasts:
        result = _process_forecast_entry(entry, slot_start, slot_end, period_duration)
        if result is not None:
            total_solar += result["contribution"]
            matched_entries.append(result)

    if debug_log and matched_entries:
        _log_debug_matched_entries(slot_start, matched_entries, total_solar)

    return total_solar


def get_solar_for_5min_slot(
    solcast_forecasts: list[dict[str, Any]],
    slot_start: datetime,
) -> float:
    """Get solar forecast (kWh) for a 5-minute slot from Solcast 30-min periods.

    Uses overlap-weighted accumulation: sums contributions from all Solcast periods
    that overlap the slot, weighted by the overlap fraction.

    This correctly handles 5-min slots that straddle two Solcast 30-min periods
    (e.g., a slot at 15:55-16:00 overlaps both 15:30-16:00 and 16:00-16:30).

    IMPORTANT: Solcast's pv_estimate values represent average power (kWh per hour),
    NOT energy per period. So we divide overlap_seconds by 3600 (seconds per hour).

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


def sum_solar_before_target(
    solcast: list[dict[str, Any]],
    now_dt: datetime,
    target_hour: int,
) -> float:
    """Sum expected solar kWh (pv_estimate) from now until target_hour.

    NOTE: pv_estimate values represent average power (kWh per hour),
    NOT energy per period. We need to multiply by the time fraction.
    """
    target_dt = now_dt.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    period_duration = timedelta(minutes=30)
    total = 0.0
    for period in solcast:
        period_start = _parse_forecast_dt(period.get("period_start"))
        if period_start is None:
            continue
        ps_local = dt_util.as_local(period_start)
        period_end = ps_local + period_duration
        # pv_estimate is kWh per HOUR (average power)
        kwh_per_hour = float(period.get("pv_estimate", 0))

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

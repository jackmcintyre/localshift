"""Solar and price calculation utilities.

This module contains methods for solar forecasting and price calculations
that are self-contained and don't modify CoordinatorData directly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

_LOGGER = logging.getLogger(__name__)

from homeassistant.util import dt as dt_util

from .utils import parse_forecast_dt


def get_price_for_slot(
    price_forecasts: list[dict[str, Any]],
    slot_start: datetime,
) -> float:
    """Get price for a 15-minute slot from forecast.

    Returns the average price for the slot from 5-minute forecast data.
    """
    if not price_forecasts:
        return 0.0

    # Ensure slot boundaries are timezone-aware local datetimes
    if slot_start.tzinfo is None:
        slot_start = dt_util.as_local(dt_util.as_utc(slot_start))
    else:
        slot_start = dt_util.as_local(slot_start)

    slot_end = slot_start + timedelta(minutes=15)

    prices_in_slot = []
    for entry in price_forecasts:
        if not isinstance(entry, dict):
            continue

        start_raw = entry.get("start_time")
        if start_raw is None:
            continue

        start_dt = parse_forecast_dt(start_raw)
        if start_dt is None:
            continue

        start_local = dt_util.as_local(start_dt)
        end_local = start_local + timedelta(minutes=5)  # Prices are 5-min

        # Check if this price period overlaps with our slot
        if start_local < slot_end and end_local > slot_start:
            price = float(entry.get("per_kwh", 0.0))
            prices_in_slot.append(price)

    if prices_in_slot:
        return sum(prices_in_slot) / len(prices_in_slot)
    return 0.0


def get_price_for_slot_or_none(
    price_forecasts: list[dict[str, Any]],
    slot_start: datetime,
) -> float | None:
    """Get price for a 15-minute slot, returning None when there is no match.

    This is important for logic that needs to distinguish "missing forecast"
    from an actual $0.00 price.
    """
    if not price_forecasts:
        return None

    # Ensure slot boundaries are timezone-aware local datetimes
    if slot_start.tzinfo is None:
        slot_start = dt_util.as_local(dt_util.as_utc(slot_start))
    else:
        slot_start = dt_util.as_local(slot_start)

    slot_end = slot_start + timedelta(minutes=15)

    prices_in_slot: list[float] = []
    for entry in price_forecasts:
        if not isinstance(entry, dict):
            continue

        start_raw = entry.get("start_time")
        if start_raw is None:
            continue

        start_dt = parse_forecast_dt(start_raw)
        if start_dt is None:
            continue

        start_local = dt_util.as_local(start_dt)
        end_local = start_local + timedelta(minutes=5)  # Prices are 5-min

        # Check if this price period overlaps with our slot
        if start_local < slot_end and end_local > slot_start:
            price = float(entry.get("per_kwh", 0.0))
            prices_in_slot.append(price)

    if not prices_in_slot:
        return None
    return sum(prices_in_slot) / len(prices_in_slot)


def get_solar_for_15min_slot(
    solcast_forecasts: list[dict[str, Any]],
    slot_start: datetime,
) -> float:
    """Get solar forecast (kWh) for a 15-minute slot from Solcast 30-min periods.

    Uses overlap-weighted accumulation: sums contributions from all Solcast periods
    that overlap the slot, weighted by the overlap fraction.

    This correctly handles 15-min slots that straddle two Solcast 30-min periods
    (e.g. a slot starting at :20 ends at :35, overlapping both :00-:30 and :30-:60).
    The previous full-containment check returned 0 for such slots.
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
            period_kwh = float(
                entry.get("pv_estimate10")
                or entry.get("estimate10")
                or entry.get("pv_estimate")
                or entry.get("estimate")
                or 0.0
            )
            overlap_fraction = overlap_seconds / period_duration.total_seconds()
            total_solar += period_kwh * overlap_fraction

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
        period_kwh = float(
            entry.get("pv_estimate10")
            or entry.get("estimate10")
            or entry.get("pv_estimate")
            or entry.get("estimate")
            or 0.0
        )

        # Containment check: the 5-min slot must be fully inside the 30-min period
        if slot_start >= start_local and slot_end <= end_local:
            # 5 min = 1/6 of 30 min
            return period_kwh / 6.0

    return 0.0


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
    parsed_periods = 0
    overlap_hits = 0

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
            parsed_periods += 1
            end_local = start_local + period_duration

            # overlap between [start_local, end_local) and [slot_start, slot_end)
            overlap_start = max(start_local, slot_start)
            overlap_end = min(end_local, slot_end)
            overlap_seconds = (overlap_end - overlap_start).total_seconds()

            if overlap_seconds > 0:
                # Support common Solcast key variants
                period_kwh = float(
                    entry.get("pv_estimate10")
                    or entry.get("estimate10")
                    or entry.get("pv_estimate")
                    or entry.get("estimate")
                    or 0.0
                )
                overlap_fraction = overlap_seconds / period_duration.total_seconds()
                total_solar += period_kwh * overlap_fraction
                overlap_hits += 1
        except (ValueError, TypeError):
            continue

    return total_solar


def sum_solar_before_target(
    solcast: list[dict[str, Any]],
    now_dt: datetime,
    target_hour: int,
) -> float:
    """Sum pessimistic solar kWh (pv_estimate10) from now until target_hour."""
    target_dt = now_dt.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    period_duration = timedelta(minutes=30)
    total = 0.0
    for period in solcast:
        period_start = parse_forecast_dt(period.get("period_start"))
        if period_start is None:
            continue
        ps_local = dt_util.as_local(period_start)
        period_end = ps_local + period_duration
        kwh = float(period.get("pv_estimate10", 0))

        if ps_local >= target_dt:
            # Period starts at or after target — skip
            continue

        if ps_local >= now_dt:
            # Fully future period before target — include all of it
            total += kwh
        elif period_end > now_dt:
            # In-progress period — prorate remaining fraction
            remaining = (period_end - now_dt).total_seconds()
            fraction = remaining / period_duration.total_seconds()
            total += kwh * fraction

    return total

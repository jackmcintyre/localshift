"""Solar and price calculation utilities.

This module contains methods for solar forecasting and price calculations
that are self-contained and don't modify CoordinatorData directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .utils import parse_forecast_dt


def get_price_for_slot(
    price_forecasts: list[dict[str, Any]],
    slot_start: datetime,
) -> float:
    """Get price for a 15-minute slot from Amber forecast.

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
        end_local = start_local + timedelta(minutes=5)  # Amber prices are 5-min

        # Check if this price period overlaps with our slot
        if start_local < slot_end and end_local > slot_start:
            price = float(entry.get("per_kwh", 0.0))
            prices_in_slot.append(price)

    if prices_in_slot:
        return sum(prices_in_slot) / len(prices_in_slot)
    return 0.0


def get_solar_for_15min_slot(
    solcast_forecasts: list[dict[str, Any]],
    slot_start: datetime,
) -> float:
    """Get solar forecast (kWh) for 15-minute slot from Solcast 30-min periods.

    Splits 30-minute Solcast periods into two 15-minute halves.
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

        # Check which 15-minute half of 30-min period we're in
        period_midpoint = start_local + timedelta(minutes=15)

        if slot_start >= start_local and slot_end <= period_midpoint:
            # First half of period (0-15 min)
            # Simple approach: split evenly (50% each half)
            return period_kwh * 0.5
        elif slot_start >= period_midpoint and slot_end <= end_local:
            # Second half of period (15-30 min)
            return period_kwh * 0.5

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

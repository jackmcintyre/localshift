"""Price forecast utilities.

This module contains methods for looking up Amber price forecasts.
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
    """Get price for a slot from Amber forecast data.

    Amber mixes 5-min dispatch intervals (near-term) with 30-min extended
    forecast periods.  The overlap scan below handles both correctly.

    When no period overlaps the slot (e.g. a 5-min slot that falls in the
    gap between two non-adjacent 30-min entries), falls back to the most
    recent entry whose start_time <= slot_start so that prices are never
    silently zeroed out due to resolution mismatches.
    """
    if not price_forecasts:
        return 0.0

    # Ensure slot boundaries are timezone-aware local datetimes
    if slot_start.tzinfo is None:
        slot_start = dt_util.as_local(dt_util.as_utc(slot_start))
    else:
        slot_start = dt_util.as_local(slot_start)

    slot_end = slot_start + timedelta(minutes=15)

    prices_in_slot: list[float] = []
    fallback_price: float = 0.0
    fallback_start: datetime | None = None

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

        # Overlap check: use per-entry duration from the data when available,
        # otherwise assume 5 minutes (standard Amber dispatch interval).
        duration_minutes: int = int(entry.get("duration", 5))
        end_local = start_local + timedelta(minutes=duration_minutes)

        if start_local < slot_end and end_local > slot_start:
            price = float(entry.get("per_kwh", 0.0))
            prices_in_slot.append(price)

        # Track most-recent entry at or before slot_start for fallback
        if start_local <= slot_start:
            if fallback_start is None or start_local > fallback_start:
                fallback_start = start_local
                fallback_price = float(entry.get("per_kwh", 0.0))

    if prices_in_slot:
        return sum(prices_in_slot) / len(prices_in_slot)

    # Fallback: use the most recent price entry that started before this slot.
    # This handles the case where the Amber forecast has 30-min extended periods
    # that don't fully overlap short near-term slots (gap at :10/:15/:40/:45).
    return fallback_price


def get_price_for_slot_or_none(
    price_forecasts: list[dict[str, Any]],
    slot_start: datetime,
) -> float | None:
    """Get price for a slot, returning None only when the forecast has no data.

    Unlike get_price_for_slot(), this returns None (not 0.0) when the forecast
    list is empty or contains no entry at or before slot_start — allowing callers
    to distinguish "missing forecast" from a genuine $0.00 price.

    Uses the same duration-aware overlap + most-recent fallback as
    get_price_for_slot() so that 30-min extended periods are handled correctly.
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
    fallback_price: float = 0.0
    fallback_start: datetime | None = None

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

        duration_minutes: int = int(entry.get("duration", 5))
        end_local = start_local + timedelta(minutes=duration_minutes)

        if start_local < slot_end and end_local > slot_start:
            price = float(entry.get("per_kwh", 0.0))
            prices_in_slot.append(price)

        if start_local <= slot_start:
            if fallback_start is None or start_local > fallback_start:
                fallback_start = start_local
                fallback_price = float(entry.get("per_kwh", 0.0))

    if prices_in_slot:
        return sum(prices_in_slot) / len(prices_in_slot)

    # Return fallback price when the forecast exists but doesn't directly overlap.
    # Return None only when there is no entry at or before slot_start at all
    # (i.e. the forecast doesn't cover this time yet).
    if fallback_start is None:
        return None
    return fallback_price

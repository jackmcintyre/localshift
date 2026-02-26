"""Utility functions for computation engine.

This module contains pure static helper functions that don't require
instance state or modification of CoordinatorData.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.util import dt as dt_util


def parse_forecast_dt(dt_str: str | None) -> datetime | None:
    """Parse an ISO format datetime string from forecast data."""
    if dt_str is None:
        return None
    try:
        return dt_util.parse_datetime(str(dt_str))
    except (ValueError, TypeError):
        return None


def parse_slot_time(time_str: str, ha_timezone: str) -> datetime | None:
    """Parse Amber timestamp and convert to HA local timezone.

    Handles both UTC (+00:00) and local timezone timestamps from Amber.
    - Amber (100H General Forecast) uses UTC timestamps.
    - Amber Express uses local Sydney time timestamps.

    This function ensures consistent timezone handling by always converting
    to the Home Assistant configured timezone.

    Args:
        time_str: ISO timestamp with timezone (e.g., "2026-02-26T14:25:01+00:00")
        ha_timezone: HA configured timezone (e.g., "Australia/Sydney")

    Returns:
        datetime in HA local timezone, or None if parsing fails.
    """
    if not time_str:
        return None

    try:
        # Parse the ISO timestamp (includes timezone info)
        dt = datetime.fromisoformat(str(time_str))

        # If the datetime is naive (no timezone), assume it's in HA's timezone
        if dt.tzinfo is None:
            local_tz = ZoneInfo(ha_timezone)
            return dt.replace(tzinfo=local_tz)

        # Convert to HA local timezone
        local_tz = ZoneInfo(ha_timezone)
        return dt.astimezone(local_tz)
    except (ValueError, TypeError):
        return None


def get_slot_duration_minutes(entry: dict[str, Any]) -> int | None:
    """Get the duration of a forecast slot in minutes.

    Calculates duration from the entry's duration field, or by computing
    the difference between end_time and start_time.

    Args:
        entry: Forecast entry dict with 'duration', 'start_time', and/or 'end_time'

    Returns:
        Duration in minutes, or None if it cannot be determined.
    """
    # Check for explicit duration field
    duration = entry.get("duration")
    if duration is not None:
        return int(duration)

    # Calculate from start_time and end_time
    start_str = entry.get("start_time")
    end_str = entry.get("end_time")

    if start_str and end_str:
        try:
            start_dt = datetime.fromisoformat(str(start_str))
            end_dt = datetime.fromisoformat(str(end_str))
            delta_seconds = (end_dt - start_dt).total_seconds()
            return int(delta_seconds / 60)
        except (ValueError, TypeError):
            pass

    return None


def percentile(prices: list[float], percentile_value: float) -> float:
    """Calculate Nth percentile of a list of prices."""
    if not prices:
        return 0.0
    sorted_prices = sorted(prices)
    n = len(sorted_prices)
    index = (percentile_value / 100) * (n - 1)
    lower = int(index)
    upper = lower + 1
    if upper >= n:
        return sorted_prices[-1]
    fraction = index - lower
    return sorted_prices[lower] * (1 - fraction) + sorted_prices[upper] * fraction


def scan_forecast_for_spike(
    forecasts: list[dict[str, Any]],
    now_dt: datetime,
    cutoff: datetime,
) -> bool:
    """Return True if any forecast has spike_status == 'spike' in window."""
    for f in forecasts:
        start = parse_forecast_dt(f.get("start_time"))
        if start is None:
            continue
        start_local = dt_util.as_local(start)
        if start_local >= now_dt and start_local <= cutoff:
            if f.get("spike_status") == "spike":
                return True
    return False


def max_forecast_price(
    forecasts: list[dict[str, Any]],
    now_dt: datetime,
    cutoff: datetime,
) -> float:
    """Return maximum per_kwh price from forecasts within window."""
    max_price = 0.0
    for f in forecasts:
        start = parse_forecast_dt(f.get("start_time"))
        if start is None:
            continue
        start_local = dt_util.as_local(start)
        if start_local >= now_dt and start_local <= cutoff:
            price = float(f.get("per_kwh", 0))
            if price > max_price:
                max_price = price
    return round(max_price, 2)


def build_hourly_forecast_summary(
    forecast_15min: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Summarise 112 slots (5-min + 15-min) into hourly records with date.

    Uses (date, hour) as the key so slots from different calendar days
    are not merged. This ensures the full rolling 24-hour forecast is
    preserved (e.g., today's 17:00-23:00 AND tomorrow's 00:00-14:00).
    """
    hourly: dict[str, dict[str, Any]] = {}

    for row in forecast_15min:
        if not isinstance(row, dict):
            continue

        # Get timestamp to derive both date and hour
        ts_raw = row.get("timestamp")
        if ts_raw is None:
            continue
        try:
            slot_dt = datetime.fromisoformat(str(ts_raw))
        except (ValueError, TypeError):
            continue

        # Use ISO date (YYYY-MM-DD) + hour as key to separate days
        slot_date = slot_dt.date().isoformat()
        hour = slot_dt.hour
        key = f"{slot_date}_{hour:02d}"

        bucket = hourly.get(key)
        if bucket is None:
            predicted_soc_raw = row.get("predicted_soc")
            predicted_soc = (
                float(predicted_soc_raw)
                if isinstance(predicted_soc_raw, int | float)
                else 0.0
            )
            bucket = {
                "date": slot_date,
                "hour": hour,
                "predicted_soc": predicted_soc,
                "solar_kwh": 0.0,
                "consumption_kwh": 0.0,
                "net_kwh": 0.0,
                "grid_import_kwh": 0.0,
                "grid_export_kwh": 0.0,
            }
            hourly[key] = bucket

        predicted_soc_raw = row.get("predicted_soc")
        if isinstance(predicted_soc_raw, int | float):
            bucket["predicted_soc"] = float(predicted_soc_raw)

        for key_name in (
            "solar_kwh",
            "consumption_kwh",
            "net_kwh",
            "grid_import_kwh",
            "grid_export_kwh",
        ):
            try:
                bucket[key_name] += float(row.get(key_name) or 0.0)
            except (TypeError, ValueError):
                continue

    # Return in chronological order (by date, then hour)
    result: list[dict[str, Any]] = []
    for key in sorted(hourly.keys()):
        bucket = hourly[key]
        result.append(
            {
                "date": bucket["date"],
                "hour": bucket["hour"],
                "predicted_soc": round(float(bucket["predicted_soc"]), 1),
                "solar_kwh": round(float(bucket["solar_kwh"]), 3),
                "consumption_kwh": round(float(bucket["consumption_kwh"]), 3),
                "net_kwh": round(float(bucket["net_kwh"]), 3),
                "grid_import_kwh": round(float(bucket.get("grid_import_kwh", 0)), 3),
                "grid_export_kwh": round(float(bucket.get("grid_export_kwh", 0)), 3),
            }
        )
    return result


def analyze_spike_window(
    forecasts: list[dict[str, Any]],
    now_dt: datetime,
    max_lookahead_hours: float = 8.0,
) -> tuple[datetime | None, float, list[float]]:
    """Analyze feed-in forecast for spike window details.

    Scans the forecast to find the current/ongoing spike window and extracts
    key information for conservative spike discharge decisions.

    Args:
        forecasts: Feed-in price forecast list
        now_dt: Current datetime
        max_lookahead_hours: Maximum hours to look ahead for spike analysis

    Returns:
        Tuple of (spike_end_time, max_price, all_spike_prices)
        - spike_end_time: When the spike is predicted to end (None if no spike)
        - max_price: Maximum price within the spike window
        - all_spike_prices: List of all prices during spike window
    """
    cutoff = now_dt + timedelta(hours=max_lookahead_hours)

    spike_start: datetime | None = None
    spike_end: datetime | None = None
    max_price = 0.0
    all_spike_prices: list[float] = []

    for f in forecasts:
        start = parse_forecast_dt(f.get("start_time"))
        if start is None:
            continue

        start_local = dt_util.as_local(start)

        # Only consider slots within our lookahead window
        if start_local < now_dt or start_local > cutoff:
            continue

        # Check if this is a spike slot
        if f.get("spike_status") == "spike":
            price = float(f.get("per_kwh", 0))

            if spike_start is None:
                spike_start = start_local

            spike_end = start_local
            max_price = max(max_price, price)
            all_spike_prices.append(price)

    if not all_spike_prices:
        return None, 0.0, []

    return spike_end, round(max_price, 2), all_spike_prices


def calculate_spike_price_threshold(
    spike_prices: list[float],
    percentile_value: float,
) -> float:
    """Calculate price threshold for top X% of spike prices.

    For example, with percentile_value=75, returns the price at the 75th percentile
    of spike prices, meaning only prices in the top 25% will trigger exports.

    Args:
        spike_prices: List of prices during spike window
        percentile_value: Percentile threshold (50-95). Higher = more conservative.

    Returns:
        Price threshold - only export when FIT >= this price
    """
    if not spike_prices:
        return 0.0

    return percentile(spike_prices, percentile_value)

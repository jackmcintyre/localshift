"""Utility functions for computation engine.

This module contains pure static helper functions that don't require
instance state or modification of CoordinatorData.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util


def parse_forecast_dt(dt_str: str | None) -> datetime | None:
    """Parse an ISO format datetime string from forecast data."""
    if dt_str is None:
        return None
    try:
        return dt_util.parse_datetime(str(dt_str))
    except (ValueError, TypeError):
        return None


def percentile(prices: list[float], percentile: float) -> float:
    """Calculate Nth percentile of a list of prices."""
    if not prices:
        return 0.0
    sorted_prices = sorted(prices)
    n = len(sorted_prices)
    index = (percentile / 100) * (n - 1)
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
    """Summarise 96x 15-min slots into 24 hourly records.

    Keeps attributes smaller while still providing an hour-by-hour view.
    """
    hourly: dict[int, dict[str, Any]] = {}

    for row in forecast_15min:
        if not isinstance(row, dict):
            continue

        hour_raw = row.get("hour")
        if hour_raw is None:
            continue
        try:
            hour = int(hour_raw)
        except (TypeError, ValueError):
            continue

        if hour < 0 or hour > 23:
            continue

        bucket = hourly.get(hour)
        if bucket is None:
            predicted_soc_raw = row.get("predicted_soc")
            predicted_soc = (
                float(predicted_soc_raw)
                if isinstance(predicted_soc_raw, int | float)
                else 0.0
            )
            bucket = {
                "hour": hour,
                "predicted_soc": predicted_soc,
                "solar_kwh": 0.0,
                "consumption_kwh": 0.0,
                "net_kwh": 0.0,
                "grid_import_kwh": 0.0,
                "grid_export_kwh": 0.0,
            }
            hourly[hour] = bucket

        predicted_soc_raw = row.get("predicted_soc")
        if isinstance(predicted_soc_raw, int | float):
            bucket["predicted_soc"] = float(predicted_soc_raw)

        for key in (
            "solar_kwh",
            "consumption_kwh",
            "net_kwh",
            "grid_import_kwh",
            "grid_export_kwh",
        ):
            try:
                bucket[key] += float(row.get(key) or 0.0)
            except (TypeError, ValueError):
                continue

    # Return in hour order
    result: list[dict[str, Any]] = []
    for hour in sorted(hourly.keys()):
        bucket = hourly[hour]
        result.append(
            {
                "hour": hour,
                "predicted_soc": round(float(bucket["predicted_soc"]), 1),
                "solar_kwh": round(float(bucket["solar_kwh"]), 3),
                "consumption_kwh": round(float(bucket["consumption_kwh"]), 3),
                "net_kwh": round(float(bucket["net_kwh"]), 3),
                "grid_import_kwh": round(float(bucket.get("grid_import_kwh", 0)), 3),
                "grid_export_kwh": round(float(bucket.get("grid_export_kwh", 0)), 3),
            }
        )
    return result

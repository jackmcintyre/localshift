"""Integration tests for localshift component.

Tests the full flow from state changes through mode transitions,
including forecast computation and battery control decisions.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.coordinator import CoordinatorData


# Helper to create timezone-aware datetimes
def dt_aware(year, month, day, hour, minute=0, second=0):
    """Create a timezone-aware datetime in Australia/Sydney timezone."""
    return datetime(
        year, month, day, hour, minute, second, tzinfo=timezone(timedelta(hours=11))
    )


@pytest.fixture
def integration_data():
    """Create CoordinatorData for integration tests."""
    data = CoordinatorData()
    data.soc = 50.0
    data.operation_mode = "autonomous"
    data.backup_reserve = 50
    data.grid_power_kw = 0.0
    data.load_power_kw = 0.5
    data.solar_power_kw = 0.0
    data.general_price = 0.25
    data.feed_in_price = 0.08
    data.price_spike = False
    data.manual_override = False
    data.decision_log = []
    data.daily_forecast = []
    data.daily_forecast_soc_15min = []
    data.target_reached_today = False
    # Issue #319: Mark forecast as ready for integration tests
    data.forecast_ready = True
    data.forecast_status = "ready"
    # Issue #351: Provide general_forecast for hybrid timescale
    # Create 24 hours of 30-min slots starting from a base time
    base_time = dt_aware(2026, 2, 16, 0, 0, 0)
    data.general_forecast = []
    for i in range(48):  # 48 x 30-min slots = 24 hours
        start = base_time + timedelta(minutes=i * 30)
        end = start + timedelta(minutes=30)
        data.general_forecast.append({
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "per_kwh": 0.25,  # Default price
        })
    data.feed_in_forecast = []
    data.solcast_today = []
    data.solcast_tomorrow = []
    return data

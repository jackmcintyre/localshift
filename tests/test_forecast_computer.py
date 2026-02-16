"""Unit tests for ForecastComputer."""
import pytest
from datetime import datetime, time, timedelta
from unittest.mock import MagicMock

from custom_components.amber_powerwall.computation_engine_lib.forecast_computer import (
    ForecastComputer,
)


def test_estimate_hourly_consumption_with_historical(mock_entry, mock_get_entity_id):
    """Test hourly consumption estimation with historical data."""
    entry = mock_entry
    entry.options = {
        "load_weight_recent": 0.7,
    }
    
    computer = ForecastComputer(entry, mock_get_entity_id, lambda x: {})
    
    # With historical data
    hourly_avg = {16: 0.5, 17: 0.6, 18: 0.7}
    kw, source = computer._estimate_hourly_consumption_kw(
        hourly_avg, 17, 0.4, 0.5
    )
    
    assert source == "weighted_load"
    assert kw > 0


def test_estimate_hourly_consumption_fallback(mock_entry, mock_get_entity_id):
    """Test hourly consumption estimation with fallback."""
    entry = mock_entry
    entry.options = {
        "load_weight_recent": 0.7,
    }
    
    computer = ForecastComputer(entry, mock_get_entity_id, lambda x: {})
    
    # No historical data, use current
    kw, source = computer._estimate_hourly_consumption_kw(
        {}, 17, 0.4, 0.5
    )
    
    assert source == "live_load_fallback"


def test_find_negative_fit_windows_no_negatives(mock_entry, mock_get_entity_id):
    """Test negative FIT window detection with no negative prices."""
    entry = mock_entry
    computer = ForecastComputer(entry, mock_get_entity_id, lambda x: {})
    
    # All positive prices
    feed_in_forecast = [
        {"start_time": "2026-02-16T18:00:00+11:00", "end_time": "2026-02-16T18:05:00+11:00", "per_kwh": 0.08},
        {"start_time": "2026-02-16T18:05:00+11:00", "end_time": "2026-02-16T18:10:00+11:00", "per_kwh": 0.09},
        {"start_time": "2026-02-16T18:10:00+11:00", "end_time": "2026-02-16T18:15:00+11:00", "per_kwh": 0.10},
    ]
    
    start_time = datetime(2026, 2, 16, 18, 0, 0)
    windows = computer._find_negative_fit_windows(feed_in_forecast, start_time, max_hours=2)
    
    assert len(windows) == 0


def test_find_negative_fit_windows_with_negatives(mock_entry, mock_get_entity_id):
    """Test negative FIT window detection with negative prices."""
    entry = mock_entry
    computer = ForecastComputer(entry, mock_get_entity_id, lambda x: {})
    
    # Some negative prices
    feed_in_forecast = [
        {"start_time": "2026-02-16T18:00:00+11:00", "end_time": "2026-02-16T18:05:00+11:00", "per_kwh": 0.08},
        {"start_time": "2026-02-16T18:05:00+11:00", "end_time": "2026-02-16T18:10:00+11:00", "per_kwh": 0.00},
        {"start_time": "2026-02-16T18:10:00+11:00", "end_time": "2026-02-16T18:15:00+11:00", "per_kwh": -0.02},
        {"start_time": "2026-02-16T18:15:00+11:00", "end_time": "2026-02-16T18:20:00+11:00", "per_kwh": -0.01},
        {"start_time": "2026-02-16T18:20:00+11:00", "end_time": "2026-02-16T18:25:00+11:00", "per_kwh": 0.05},
    ]
    
    start_time = datetime(2026, 2, 16, 18, 0, 0)
    windows = computer._find_negative_fit_windows(feed_in_forecast, start_time, max_hours=2)
    
    assert len(windows) == 1
    assert windows[0][2] == pytest.approx(-0.02)


def test_calculate_percentile_fit_price(mock_entry, mock_get_entity_id):
    """Test percentile FIT price calculation."""
    entry = mock_entry
    computer = ForecastComputer(entry, mock_get_entity_id, lambda x: {})
    
    feed_in_forecast = [
        {"start_time": f"2026-02-16T{18+i:02d}:00:00+11:00", "end_time": f"2026-02-16T{18+i:02d}:05:00+11:00", "per_kwh": p}
        for i, p in enumerate([0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13, 0.14])
    ]
    
    start_time = datetime(2026, 2, 16, 18, 0, 0)
    percentile_price = computer._calculate_percentile_fit_price(feed_in_forecast, start_time, percentile=60, hours=2)
    
    assert percentile_price == pytest.approx(0.08)


def test_calculate_max_fit_price(mock_entry, mock_get_entity_id):
    """Test maximum FIT price calculation."""
    entry = mock_entry
    computer = ForecastComputer(entry, mock_get_entity_id, lambda x: {})
    
    feed_in_forecast = [
        {"start_time": f"2026-02-16T{18+i:02d}:00:00+11:00", "end_time": f"2026-02-16T{18+i:02d}:05:00+11:00", "per_kwh": p}
        for i, p in enumerate([0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13, 0.14])
    ]
    
    start_time = datetime(2026, 2, 16, 18, 0, 0)
    max_price = computer._calculate_max_fit_price(feed_in_forecast, start_time, hours=2)
    
    assert max_price == pytest.approx(0.14)


def test_parse_time_option(mock_entry, mock_get_entity_id):
    """Test time option parsing."""
    entry = mock_entry
    computer = ForecastComputer(entry, mock_get_entity_id, lambda x: {})
    
    t = computer._parse_time_option("demand_window_start", "18:00:00")
    
    assert t.hour == 18
    assert t.minute == 0
    assert t.second == 0


def test_parse_time_option_invalid(mock_entry, mock_get_entity_id):
    """Test time option parsing with invalid value."""
    entry = mock_entry
    computer = ForecastComputer(entry, mock_get_entity_id, lambda x: {})
    
    # Should fallback to default
    t = computer._parse_time_option("invalid", "18:00:00")
    
    assert t.hour == 18

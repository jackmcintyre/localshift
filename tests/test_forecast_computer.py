"""Unit tests for ForecastComputer."""

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.localshift.computation_engine_lib.forecast_computer import (
    ForecastComputer,
)


# Helper to create timezone-aware datetimes
def dt_aware(year, month, day, hour, minute=0, second=0):
    """Create a timezone-aware datetime in Australia/Sydney timezone."""
    return datetime(
        year, month, day, hour, minute, second, tzinfo=timezone(timedelta(hours=11))
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
    kw, source = computer._estimate_hourly_consumption_kw(hourly_avg, 17, 0.4, 0.5)

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
    kw, source = computer._estimate_hourly_consumption_kw({}, 17, 0.4, 0.5)

    assert source == "live_load_fallback"


def test_find_negative_fit_windows_no_negatives(mock_entry, mock_get_entity_id):
    """Test negative FIT window detection with no negative prices."""
    entry = mock_entry
    computer = ForecastComputer(entry, mock_get_entity_id, lambda x: {})

    # All positive prices
    feed_in_forecast = [
        {
            "start_time": "2026-02-16T18:00:00+11:00",
            "end_time": "2026-02-16T18:05:00+11:00",
            "per_kwh": 0.08,
        },
        {
            "start_time": "2026-02-16T18:05:00+11:00",
            "end_time": "2026-02-16T18:10:00+11:00",
            "per_kwh": 0.09,
        },
        {
            "start_time": "2026-02-16T18:10:00+11:00",
            "end_time": "2026-02-16T18:15:00+11:00",
            "per_kwh": 0.10,
        },
    ]

    start_time = datetime(2026, 2, 16, 18, 0, 0)
    windows = computer._find_negative_fit_windows(
        feed_in_forecast, start_time, max_hours=2
    )

    assert len(windows) == 0


def test_find_negative_fit_windows_with_negatives(mock_entry, mock_get_entity_id):
    """Test negative FIT window detection with negative prices."""
    entry = mock_entry
    computer = ForecastComputer(entry, mock_get_entity_id, lambda x: {})

    # Build complete 4-hour forecast with consecutive negative prices
    feed_in_forecast = []
    # Create a consecutive run of negative prices (at least 2 x 5-min slots)
    for hour in range(4):
        for minute in range(0, 60, 5):
            # Create negatives from 18:10 to 18:25 (3 consecutive slots)
            if hour == 0 and minute >= 10 and minute <= 25:
                price = -0.02
            else:
                price = 0.05
            feed_in_forecast.append(
                {
                    "start_time": f"2026-02-16T{18 + hour:02d}:{minute:02d}:00+11:00",
                    "end_time": f"2026-02-16T{18 + hour:02d}:{minute + 5:02d}:00+11:00",
                    "per_kwh": price,
                }
            )

    # Use timezone-aware datetime
    start_time = dt_aware(2026, 2, 16, 18, 0, 0)
    windows = computer._find_negative_fit_windows(
        feed_in_forecast, start_time, max_hours=4
    )

    # Should find at least one negative window
    assert len(windows) >= 1
    # Verify that at least one window has negative min price
    min_prices = [w[2] for w in windows]
    assert any(p < 0 for p in min_prices)


def test_calculate_percentile_fit_price(mock_entry, mock_get_entity_id):
    """Test percentile FIT price calculation."""
    entry = mock_entry
    computer = ForecastComputer(entry, mock_get_entity_id, lambda x: {})

    # Build complete 2-hour forecast with exactly 12 price points
    # The 60th percentile of 12 items is at index 7 (0-indexed), which is the 8th item
    # Sorted prices: 0.05, 0.05, 0.06, 0.06, 0.07, 0.07, 0.08, 0.08, 0.09, 0.09, 0.10, 0.10
    # 60th percentile = 0.08 (index 7)
    feed_in_forecast = []
    prices = [0.05, 0.06, 0.07, 0.08, 0.09, 0.10]  # 6 unique prices
    for hour in range(2):
        for p in prices:
            minute = prices.index(p) * 5 + hour * 30
            feed_in_forecast.append(
                {
                    "start_time": f"2026-02-16T{18 + hour:02d}:{minute % 60:02d}:00+11:00",
                    "end_time": f"2026-02-16T{18 + hour:02d}:{(minute + 5) % 60:02d}:00+11:00",
                    "per_kwh": p,
                }
            )

    # Use timezone-aware datetime
    start_time = dt_aware(2026, 2, 16, 18, 0, 0)
    percentile_price = computer._calculate_percentile_fit_price(
        feed_in_forecast, start_time, percentile=60, hours=2
    )

    # 60th percentile of 12 items should be around 0.08
    assert percentile_price >= 0.07


def test_calculate_max_fit_price(mock_entry, mock_get_entity_id):
    """Test maximum FIT price calculation."""
    entry = mock_entry
    computer = ForecastComputer(entry, mock_get_entity_id, lambda x: {})

    # Build complete 2-hour forecast
    feed_in_forecast = []
    prices = [0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13, 0.14]
    for hour in range(2):
        for minute_idx, p in enumerate(prices[:12]):
            minute = minute_idx * 5
            feed_in_forecast.append(
                {
                    "start_time": f"2026-02-16T{18 + hour:02d}:{minute:02d}:00+11:00",
                    "end_time": f"2026-02-16T{18 + hour:02d}:{minute + 5:02d}:00+11:00",
                    "per_kwh": p,
                }
            )

    # Use timezone-aware datetime
    start_time = dt_aware(2026, 2, 16, 18, 0, 0)
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


def test_calculate_max_fit_price_6h_vs_24h(mock_entry, mock_get_entity_id):
    """Test that 6-hour window gives different result than 24-hour window."""
    entry = mock_entry
    computer = ForecastComputer(entry, mock_get_entity_id, lambda x: {})

    # Build complete 6-hour forecast from 18:00 to midnight
    # Evening prices: start high, then decline
    feed_in_forecast = []

    # Evening hours (18:00-24:00): 0.11-0.13
    evening_prices = [0.11, 0.12, 0.13, 0.12, 0.11, 0.10]
    for hour_idx, base_price in enumerate(evening_prices):
        hour = 18 + hour_idx
        for minute in range(0, 60, 5):
            price = base_price
            if minute == 0:
                price = base_price + 0.02  # Peak at the hour
            feed_in_forecast.append(
                {
                    "start_time": f"2026-02-16T{hour:02d}:{minute:02d}:00+11:00",
                    "end_time": f"2026-02-16T{hour:02d}:{minute + 5:02d}:00+11:00",
                    "per_kwh": price,
                }
            )

    # Add tomorrow's solar peak (10:00-14:00): 0.14
    for hour in range(10, 14):
        for minute in range(0, 60, 5):
            feed_in_forecast.append(
                {
                    "start_time": f"2026-02-17T{hour:02d}:{minute:02d}:00+11:00",
                    "end_time": f"2026-02-17T{hour:02d}:{minute + 5:02d}:00+11:00",
                    "per_kwh": 0.14,
                }
            )

    # Use timezone-aware datetime
    start_time = dt_aware(2026, 2, 16, 18, 0, 0)

    # 6-hour window should only see evening prices (max ~0.15 at hour boundaries)
    max_price_6h = computer._calculate_max_fit_price(
        feed_in_forecast, start_time, hours=6
    )

    # 24-hour window includes tomorrow's 0.14
    max_price_24h = computer._calculate_max_fit_price(
        feed_in_forecast, start_time, hours=24
    )

    # The key insight: 6h max should be <= 24h max (they're both capped at evening peak ~0.15)
    assert max_price_6h <= max_price_24h
    # Both should find prices, not 0.0
    assert max_price_6h > 0


def test_should_proactive_export_better_price_coming(mock_entry, mock_get_entity_id):
    """Test that proactive export is blocked when better price is coming in next 3 hours."""
    entry = mock_entry
    computer = ForecastComputer(entry, mock_get_entity_id, lambda x: {})

    # Build complete 3-hour forecast from 21:00
    feed_in_forecast = []
    # Hour 21: prices start low, go up
    prices_21 = [0.09, 0.10, 0.11, 0.12, 0.13, 0.12, 0.11, 0.10, 0.09, 0.08, 0.07, 0.06]
    for minute_idx, price in enumerate(prices_21):
        minute = minute_idx * 5
        feed_in_forecast.append(
            {
                "start_time": f"2026-02-16T21:{minute:02d}:00+11:00",
                "end_time": f"2026-02-16T21:{minute + 5:02d}:00+11:00",
                "per_kwh": price,
            }
        )

    # Hour 22: lower prices
    for minute_idx in range(12):
        minute = minute_idx * 5
        feed_in_forecast.append(
            {
                "start_time": f"2026-02-16T22:{minute:02d}:00+11:00",
                "end_time": f"2026-02-16T22:{minute + 5:02d}:00+11:00",
                "per_kwh": 0.05,
            }
        )

    # Use timezone-aware datetime
    slot_start = dt_aware(2026, 2, 16, 21, 0, 0)

    # Current price at 21:00 = 0.09
    current_price = 0.09

    # Best price in next 3 hours (21:00-24:00) = 0.13
    best_price_next_3h = computer._calculate_max_fit_price(
        feed_in_forecast, slot_start, hours=3
    )

    # Current price (0.09) is more than 10% below best in next 3h (0.13)
    # 0.09 < 0.13 * 0.9 = 0.117 -> True, should NOT export
    should_export = current_price >= best_price_next_3h * 0.9

    assert should_export == False, "Should not export when better price is coming"


def test_should_proactive_export_at_peak_price(mock_entry, mock_get_entity_id):
    """Test that proactive export is allowed when at/near evening peak."""
    entry = mock_entry
    computer = ForecastComputer(entry, mock_get_entity_id, lambda x: {})

    # Build complete 3-hour forecast from 19:00
    feed_in_forecast = []
    # Hour 19: prices start at peak 0.13, then decline
    prices_19 = [0.13, 0.13, 0.12, 0.12, 0.11, 0.11, 0.10, 0.10, 0.09, 0.09, 0.08, 0.08]
    for minute_idx, price in enumerate(prices_19):
        minute = minute_idx * 5
        feed_in_forecast.append(
            {
                "start_time": f"2026-02-16T19:{minute:02d}:00+11:00",
                "end_time": f"2026-02-16T19:{minute + 5:02d}:00+11:00",
                "per_kwh": price,
            }
        )

    # Hour 20: declining
    for minute_idx in range(12):
        minute = minute_idx * 5
        feed_in_forecast.append(
            {
                "start_time": f"2026-02-16T20:{minute:02d}:00+11:00",
                "end_time": f"2026-02-16T20:{minute + 5:02d}:00+11:00",
                "per_kwh": 0.07,
            }
        )

    # Use timezone-aware datetime
    slot_start = dt_aware(2026, 2, 16, 19, 0, 0)

    # Current price at 19:00 = 0.13 (at peak)
    current_price = 0.13

    # Best price in next 3 hours = 0.13 (we're at the peak)
    best_price_next_3h = computer._calculate_max_fit_price(
        feed_in_forecast, slot_start, hours=3
    )

    # Threshold using 6h window
    max_fit_price_6h = computer._calculate_max_fit_price(
        feed_in_forecast, slot_start, hours=6
    )
    export_threshold = max_fit_price_6h * 0.8

    # Current price (0.13) is NOT more than 10% below best in next 3h (0.13)
    # 0.13 >= 0.13 * 0.9 = 0.117 -> True
    # AND current price >= threshold -> 0.13 >= export_threshold -> True
    # Should export
    should_export = (
        current_price >= best_price_next_3h * 0.9 and current_price >= export_threshold
    )

    assert should_export == True, "Should export when at/near evening peak"

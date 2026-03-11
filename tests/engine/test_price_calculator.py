"""Tests for price_calculator.py helper functions."""

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.localshift.engine.price_calculator import (
    _compute_price_slot_data,
    _parse_price_entry,
    get_price_for_slot,
    get_price_for_slot_or_none,
    get_price_for_slot_with_source,
)


class TestParsePriceEntry:
    """Tests for _parse_price_entry helper."""

    def test_parse_valid_entry(self):
        """Test parsing a valid price entry."""
        now = datetime.now(timezone.utc)
        entry = {
            "start_time": now.isoformat(),
            "per_kwh": 0.15,
            "duration": 5,
        }
        result = _parse_price_entry(entry, now, now + timedelta(minutes=15))
        assert result is not None
        assert result["price"] == 0.15
        assert result["duration_minutes"] == 5

    def test_parse_invalid_entry_not_dict(self):
        """Test that non-dict entries return None."""
        result = _parse_price_entry(
            "not a dict", datetime.now(timezone.utc), datetime.now(timezone.utc)
        )
        assert result is None

    def test_parse_entry_missing_start_time(self):
        """Test that entries without start_time return None."""
        entry = {"per_kwh": 0.15}
        result = _parse_price_entry(
            entry, datetime.now(timezone.utc), datetime.now(timezone.utc)
        )
        assert result is None


class TestComputePriceSlotData:
    """Tests for _compute_price_slot_data helper."""

    def test_empty_forecast_returns_empty(self):
        """Test empty forecast returns empty lists."""
        prices, fallback, start = _compute_price_slot_data(
            [], datetime.now(timezone.utc)
        )
        assert prices == []
        assert fallback == 0.0
        assert start is None

    def test_single_overlapping_price(self):
        """Test single price overlapping slot."""
        now = datetime.now(timezone.utc)
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.20,
                "duration": 30,
            }
        ]
        prices, fallback, _ = _compute_price_slot_data(forecast, now)
        assert prices == [0.20]
        assert fallback == 0.20


class TestGetPriceForSlot:
    """Tests for get_price_for_slot function."""

    def test_empty_forecast_returns_zero(self):
        """Test empty forecast returns 0.0."""
        assert get_price_for_slot([], datetime.now(timezone.utc)) == 0.0

    def test_single_price_forecast(self):
        """Test single price forecast."""
        now = datetime.now(timezone.utc)
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.25,
                "duration": 30,
            }
        ]
        assert get_price_for_slot(forecast, now) == 0.25


class TestGetPriceForSlotWithSource:
    """Tests for get_price_for_slot_with_source function."""

    def test_empty_forecast_returns_unknown(self):
        """Test empty forecast returns 0.0, unknown."""
        price, source = get_price_for_slot_with_source([], datetime.now(timezone.utc))
        assert price == 0.0
        assert source == "unknown"

    def test_5min_data_preferred(self):
        """Test 5-min data is preferred over 30-min."""
        now = datetime.now(timezone.utc)
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.10,
                "duration": 5,
            }
        ]
        price, source = get_price_for_slot_with_source(forecast, now)
        assert price == 0.10
        assert source == "5min"

    def test_30min_data_fallback(self):
        """Test 30-min data is used when no 5-min available."""
        now = datetime.now(timezone.utc)
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.15,
                "duration": 30,
            }
        ]
        price, source = get_price_for_slot_with_source(forecast, now)
        assert price == 0.15
        assert source == "30min"


class TestGetPriceForSlotOrNull:
    """Tests for get_price_for_slot_or_none function."""

    def test_empty_forecast_returns_none(self):
        """Test empty forecast returns None."""
        assert get_price_for_slot_or_none([], datetime.now(timezone.utc)) is None

    def test_overlapping_price_returns_value(self):
        """Test overlapping price returns value."""
        now = datetime.now(timezone.utc)
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.30,
                "duration": 30,
            }
        ]
        assert get_price_for_slot_or_none(forecast, now) == 0.30

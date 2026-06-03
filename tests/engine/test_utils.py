"""Tests for engine/utils.py spike detection."""

from datetime import datetime, timezone, timedelta

import pytest

from custom_components.localshift.engine.utils import (
    scan_forecast_for_spike,
    max_forecast_price,
    analyze_spike_window,
)
from custom_components.localshift.pricing.types import ForecastSlot


class TestScanForecastForSpike:
    """Tests for scan_forecast_for_spike function."""

    def test_spike_detection_uses_is_spike_field(self):
        """Test spike detection works with is_spike field (normalized ForecastSlot format)."""
        now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)
        cutoff = now + timedelta(hours=4)

        # Forecast with is_spike=True (normalized format from provider)
        forecasts = [
            ForecastSlot(
                start_time=now + timedelta(hours=1),
                duration=60,
                per_kwh=2.50,
                is_spike=True,
                source_type="provider",
            ),
            ForecastSlot(
                start_time=now + timedelta(hours=2),
                duration=60,
                per_kwh=0.15,
                is_spike=False,
                source_type="provider",
            ),
        ]

        # Should detect spike when is_spike=True
        result = scan_forecast_for_spike(forecasts, now, cutoff)
        assert result is True

    def test_no_spike_when_is_spike_false(self):
        """Test no spike detected when is_spike is False."""
        now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)
        cutoff = now + timedelta(hours=4)

        forecasts = [
            ForecastSlot(
                start_time=now + timedelta(hours=1),
                duration=60,
                per_kwh=0.15,
                is_spike=False,
                source_type="provider",
            ),
            ForecastSlot(
                start_time=now + timedelta(hours=2),
                duration=60,
                per_kwh=0.20,
                is_spike=False,
                source_type="provider",
            ),
        ]

        result = scan_forecast_for_spike(forecasts, now, cutoff)
        assert result is False

    def test_spike_detection_without_pricing_source_param(self):
        """Test that pricing_source parameter is no longer needed."""
        now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)
        cutoff = now + timedelta(hours=4)

        forecasts = [
            ForecastSlot(
                start_time=now + timedelta(hours=1),
                duration=60,
                per_kwh=2.50,
                is_spike=True,
                source_type="provider",
            ),
        ]

        # Should work without pricing_source parameter
        result = scan_forecast_for_spike(forecasts, now, cutoff)
        assert result is True

    def test_scan_forecast_for_spike_with_forecastslot_objects(self):
        """Test scan_forecast_for_spike handles ForecastSlot objects directly."""
        now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)
        cutoff = now + timedelta(hours=4)

        # Create forecast with ForecastSlot objects (Task 4.1)
        forecasts = [
            ForecastSlot(
                start_time=now + timedelta(hours=1),
                duration=30,
                per_kwh=2.50,
                is_spike=True,
                source_type="test",
            ),
            ForecastSlot(
                start_time=now + timedelta(hours=2),
                duration=30,
                per_kwh=0.15,
                is_spike=False,
                source_type="test",
            ),
        ]

        # Should detect spike when ForecastSlot has is_spike=True
        result = scan_forecast_for_spike(forecasts, now, cutoff)
        assert result is True

    def test_scan_forecast_no_spike_with_forecastslot_objects(self):
        """Test scan_forecast_for_spike with ForecastSlot objects when no spike."""
        now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)
        cutoff = now + timedelta(hours=4)

        forecasts = [
            ForecastSlot(
                start_time=now + timedelta(hours=1),
                duration=30,
                per_kwh=0.15,
                is_spike=False,
                source_type="test",
            ),
            ForecastSlot(
                start_time=now + timedelta(hours=2),
                duration=30,
                per_kwh=0.20,
                is_spike=False,
                source_type="test",
            ),
        ]

        result = scan_forecast_for_spike(forecasts, now, cutoff)
        assert result is False


class TestMaxForecastPrice:
    """Tests for max_forecast_price function."""

    def test_max_forecast_price_with_forecastslot_objects(self):
        """Test max_forecast_price handles ForecastSlot objects."""
        now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)
        cutoff = now + timedelta(hours=4)

        # Create forecast with varying prices
        forecasts = [
            ForecastSlot(
                start_time=now + timedelta(minutes=i * 30),
                duration=30,
                per_kwh=0.10 + (i * 0.05),
                is_spike=False,
                source_type="test",
            )
            for i in range(10)
        ]

        # Max price in window: cutoff at 4 hours includes slots 0-8
        # Slot 8 starts at 4 hours, price = 0.10 + (8 * 0.05) = 0.50
        max_price = max_forecast_price(forecasts, now, cutoff)

        assert max_price == pytest.approx(0.50, abs=0.01)

    def test_max_forecast_price_empty_forecast(self):
        """Test max_forecast_price returns 0.0 for empty forecast."""
        now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)
        cutoff = now + timedelta(hours=4)

        assert max_forecast_price([], now, cutoff) == 0.0


class TestAnalyzeSpikeWindow:
    """Tests for analyze_spike_window function."""

    def test_analyze_spike_window_with_forecastslot_objects(self):
        """Test analyze_spike_window handles ForecastSlot objects."""
        now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)

        # Create forecast with spike window
        forecasts = [
            ForecastSlot(
                start_time=now + timedelta(minutes=i * 30),
                duration=30,
                per_kwh=0.50 if 4 <= i < 8 else 0.10,
                is_spike=4 <= i < 8,
                source_type="test",
            )
            for i in range(12)
        ]

        spike_end, max_price, all_prices = analyze_spike_window(
            forecasts=forecasts,
            now_dt=now,
            max_lookahead_hours=6.0,
        )

        # Spike slots: 4, 5, 6, 7 (2 hours to 4 hours)
        # Last spike slot starts at 3.5 hours
        assert spike_end == now + timedelta(hours=3, minutes=30)
        assert max_price == pytest.approx(0.50, abs=0.01)
        assert len(all_prices) == 4
        assert all(p == 0.50 for p in all_prices)

    def test_analyze_spike_window_no_spike(self):
        """Test analyze_spike_window returns empty when no spike."""
        now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)

        forecasts = [
            ForecastSlot(
                start_time=now + timedelta(minutes=i * 30),
                duration=30,
                per_kwh=0.10,
                is_spike=False,
                source_type="test",
            )
            for i in range(10)
        ]

        spike_end, max_price, all_prices = analyze_spike_window(
            forecasts=forecasts,
            now_dt=now,
            max_lookahead_hours=6.0,
        )

        assert spike_end is None
        assert max_price == 0.0
        assert all_prices == []

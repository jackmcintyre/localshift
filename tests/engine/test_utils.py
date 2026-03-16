"""Tests for engine/utils.py spike detection."""

from datetime import datetime, timezone, timedelta

import pytest

from custom_components.localshift.engine.utils import (
    scan_forecast_for_spike,
)


class TestScanForecastForSpike:
    """Tests for scan_forecast_for_spike function."""

    def test_spike_detection_uses_is_spike_field(self):
        """Test spike detection works with is_spike field (normalized ForecastSlot format)."""
        now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)
        cutoff = now + timedelta(hours=4)

        # Forecast with is_spike=True (normalized format from provider)
        forecasts = [
            {
                "start_time": now + timedelta(hours=1),
                "per_kwh": 2.50,
                "is_spike": True,
            },
            {
                "start_time": now + timedelta(hours=2),
                "per_kwh": 0.15,
                "is_spike": False,
            },
        ]

        # Should detect spike when is_spike=True
        result = scan_forecast_for_spike(forecasts, now, cutoff)
        assert result is True

    def test_no_spike_when_is_spike_false(self):
        """Test no spike detected when is_spike is False."""
        now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)
        cutoff = now + timedelta(hours=4)

        forecasts = [
            {
                "start_time": now + timedelta(hours=1),
                "per_kwh": 0.15,
                "is_spike": False,
            },
            {
                "start_time": now + timedelta(hours=2),
                "per_kwh": 0.20,
                "is_spike": False,
            },
        ]

        result = scan_forecast_for_spike(forecasts, now, cutoff)
        assert result is False

    def test_spike_detection_without_pricing_source_param(self):
        """Test that pricing_source parameter is no longer needed."""
        now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)
        cutoff = now + timedelta(hours=4)

        forecasts = [
            {
                "start_time": now + timedelta(hours=1),
                "per_kwh": 2.50,
                "is_spike": True,
            },
        ]

        # Should work without pricing_source parameter
        result = scan_forecast_for_spike(forecasts, now, cutoff)
        assert result is True

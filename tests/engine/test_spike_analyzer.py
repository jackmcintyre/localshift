"""Tests for SpikeAnalyzer with normalized forecast data."""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest


class TestSpikeAnalyzerWithNormalizedData:
    """Tests for SpikeAnalyzer using is_spike field from normalized ForecastSlot data."""

    def test_analyze_spike_uses_is_spike_field(self):
        """Test that analyze_spike works with normalized is_spike field."""
        # This test verifies the function signature works without pricing_source
        # The actual behavior is tested in test_price_signal_engine.py
        from custom_components.localshift.engine.utils import analyze_spike_window

        now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)

        # Forecast with is_spike=True (normalized format from provider)
        forecasts = [
            {
                "start_time": now + timedelta(hours=1),
                "per_kwh": 2.50,
                "is_spike": True,
            },
        ]

        # Should work without pricing_source parameter
        result = analyze_spike_window(forecasts, now, 4.0)
        spike_end, max_price, spike_prices = result

        assert spike_end is not None
        assert max_price == 2.50
        assert len(spike_prices) == 1

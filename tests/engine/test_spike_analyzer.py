"""Tests for SpikeAnalyzer with normalized forecast data."""

from datetime import datetime, time, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.const import (
    BATTERY_CAPACITY_KWH,
    CONF_DEMAND_WINDOW_START,
)
from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.engine.spike_analyzer import SpikeAnalyzer
from custom_components.localshift.pricing.types import ForecastSlot


class TestSpikeAnalyzerWithNormalizedData:
    """Tests for SpikeAnalyzer using is_spike field from normalized ForecastSlot data."""

    def test_analyze_spike_uses_is_spike_field(self):
        """Test that analyze_spike works with normalized is_spike field."""
        # This test verifies the function signature works without pricing_source
        # The actual behavior is tested in test_price_signal_engine.py
        from custom_components.localshift.engine.utils import analyze_spike_window

        now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)

        # Forecast with is_spike=True (ForecastSlot format)
        forecasts = [
            ForecastSlot(
                start_time=now + timedelta(hours=1),
                duration=60,
                per_kwh=2.50,
                is_spike=True,
                source_type="test",
            ),
        ]

        # Should work with ForecastSlot objects
        result = analyze_spike_window(forecasts, now, 4.0)
        spike_end, max_price, spike_prices = result

        assert spike_end is not None
        assert max_price == 2.50
        assert len(spike_prices) == 1


class TestSpikeReserveLoad:
    """#987: the spike reserve is sized from forecast load, not one reading."""

    NOW = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)

    def _reserve_kwh(self, data):
        analyzer = SpikeAnalyzer(
            entry=MagicMock(),
            get_switch_state=lambda _: True,
            parse_time_option=lambda key, _default: (
                time(15) if key == CONF_DEMAND_WINDOW_START else time(21)
            ),
            analyze_spike_window=MagicMock(),
            calculate_spike_price_threshold=MagicMock(),
        )
        # Two-hour spike ending before the 15:00 demand window.
        soc = analyzer.calculate_spike_reserve_soc(
            data, self.NOW, self.NOW + timedelta(hours=2), 0.9
        )
        return soc / 100 * BATTERY_CAPACITY_KWH

    def test_uses_the_load_forecast_over_the_spike(self):
        data = CoordinatorData()
        data.load_power_kw = 6.0  # a kettle-and-oven reading
        data.recent_load_short_kw = 1.5
        data.load_forecast_slots = [1.0] * 8 + [9.0] * 88

        assert self._reserve_kwh(data) == pytest.approx(2.0)

    def test_without_a_forecast_uses_the_recent_load_median(self):
        data = CoordinatorData()
        data.load_power_kw = 6.0
        data.recent_load_short_kw = 1.5

        assert self._reserve_kwh(data) == pytest.approx(3.0)

"""Unit tests for PriceSignalEngine."""

from datetime import datetime, time, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.engine.price_signal_engine import (
    PriceSignalEngine,
)
from custom_components.localshift.pricing.types import ForecastSlot


@pytest.fixture
def price_signal_engine(mock_entry, mock_get_switch_state):
    """Create a PriceSignalEngine with simple time parsing."""

    def parse_time_option(key, default):
        value = str(mock_entry.options.get(key, default))
        return time.fromisoformat(value)

    return PriceSignalEngine(
        entry=mock_entry,
        get_switch_state=mock_get_switch_state,
        parse_time_option=parse_time_option,
    )


class TestPriceSignalEngineSpikeAnalysis:
    """Tests for PriceSignalEngine spike logic."""

    def test_analyze_spike_disabled(self, price_signal_engine, coordinator_data):
        """Spike analysis should skip when conservative mode disabled."""
        price_signal_engine._spike_analyzer._get_switch_state = MagicMock(
            return_value=False
        )

        now_dt = datetime(2026, 2, 16, 18, 0, 0, tzinfo=timezone(timedelta(hours=11)))
        price_signal_engine.analyze_spike(coordinator_data, now_dt)

        assert coordinator_data.spike_in_conservative_mode is False
        assert coordinator_data.spike_end_time is None

    def test_analyze_spike_no_spike_in_forecast(
        self, price_signal_engine, coordinator_data
    ):
        """Spike analysis should handle no spike in forecast."""

        def mock_switch_state(key):
            return key == "spike_discharge_conservative"

        price_signal_engine._spike_analyzer._get_switch_state = MagicMock(
            side_effect=mock_switch_state
        )

        coordinator_data.feed_in_forecast = [
            ForecastSlot(
                start_time=datetime(
                    2026, 2, 16, 18, 0, 0, tzinfo=timezone(timedelta(hours=11))
                ),
                duration=5,
                per_kwh=0.08,
                is_spike=False,
                source_type="test",
            ),
            ForecastSlot(
                start_time=datetime(
                    2026, 2, 16, 18, 5, 0, tzinfo=timezone(timedelta(hours=11))
                ),
                duration=5,
                per_kwh=0.09,
                is_spike=False,
                source_type="test",
            ),
        ]

        now_dt = datetime(2026, 2, 16, 18, 0, 0, tzinfo=timezone(timedelta(hours=11)))
        price_signal_engine.analyze_spike(coordinator_data, now_dt)

        assert coordinator_data.spike_end_time is None
        assert coordinator_data.spike_max_price == 0.0

    def test_analyze_spike_with_spike_prices(
        self, price_signal_engine, coordinator_data
    ):
        """Spike analysis should detect and analyze spike prices."""

        def mock_switch_state(key):
            return key == "spike_discharge_conservative"

        price_signal_engine._spike_analyzer._get_switch_state = MagicMock(
            side_effect=mock_switch_state
        )

        coordinator_data.feed_in_forecast = [
            {
                "start_time": "2026-02-16T18:00:00+11:00",
                "end_time": "2026-02-16T18:05:00+11:00",
                "per_kwh": 1.50,
                "spike_status": "spike",
            },
            {
                "start_time": "2026-02-16T18:05:00+11:00",
                "end_time": "2026-02-16T18:10:00+11:00",
                "per_kwh": 2.00,
                "spike_status": "spike",
            },
            {
                "start_time": "2026-02-16T18:10:00+11:00",
                "end_time": "2026-02-16T18:15:00+11:00",
                "per_kwh": 0.10,
            },
        ]

        now_dt = datetime(2026, 2, 16, 18, 0, 0, tzinfo=timezone(timedelta(hours=11)))

        spike_end = datetime(
            2026, 2, 16, 18, 10, 0, tzinfo=timezone(timedelta(hours=11))
        )
        max_price = 2.0
        spike_prices = [1.50, 2.00]
        price_signal_engine._spike_analyzer._analyze_spike_window = MagicMock(
            return_value=(spike_end, max_price, spike_prices)
        )

        price_signal_engine.analyze_spike(coordinator_data, now_dt)

        assert coordinator_data.spike_in_conservative_mode is True
        assert coordinator_data.spike_max_price > 1.0
        assert coordinator_data.spike_price_threshold > 0

    def test_analyze_spike_calculates_reserve_soc(
        self, price_signal_engine, coordinator_data
    ):
        """Spike analysis should calculate reserve SOC needed."""

        def mock_switch_state(key):
            return key == "spike_discharge_conservative"

        price_signal_engine._spike_analyzer._get_switch_state = MagicMock(
            side_effect=mock_switch_state
        )

        coordinator_data.feed_in_forecast = [
            {
                "start_time": "2026-02-16T18:00:00+11:00",
                "end_time": "2026-02-16T18:30:00+11:00",
                "per_kwh": 2.00,
            },
        ]
        coordinator_data.load_power_kw = 1.0

        now_dt = datetime(2026, 2, 16, 18, 0, 0, tzinfo=timezone(timedelta(hours=11)))

        spike_end = datetime(
            2026, 2, 16, 19, 0, 0, tzinfo=timezone(timedelta(hours=11))
        )
        max_price = 2.0
        spike_prices = [2.0]
        price_signal_engine._spike_analyzer._analyze_spike_window = MagicMock(
            return_value=(spike_end, max_price, spike_prices)
        )

        price_signal_engine.analyze_spike(coordinator_data, now_dt)

        assert coordinator_data.spike_reserve_soc >= 0
        assert coordinator_data.spike_hours_remaining >= 0

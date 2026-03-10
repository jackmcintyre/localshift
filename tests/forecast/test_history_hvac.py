"""Tests for HVAC-aware history fetching."""

from unittest.mock import MagicMock

import pytest

from custom_components.localshift.forecast.history import (
    HistoryFetcher,
)


@pytest.fixture
def history_fetcher():
    """Create a HistoryFetcher instance for testing."""
    hass = MagicMock()
    entry = MagicMock()
    return HistoryFetcher(hass, entry)


def test_calculate_baseline_profile_25th_percentile(history_fetcher):
    """Test that baseline calculation uses 25th percentile to filter spikes."""
    # Setup: 10 samples for hour 12, ranging from 1.0 to 10.0
    # 25th percentile of [1..10] is roughly 3.25 (depending on interpolation)
    # Our implementation uses simple index: int(len * 0.25) -> index 2
    # sorted: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
    # index 2 is value 3.0

    samples = {12: [1.0, 10.0, 5.0, 2.0, 8.0, 3.0, 9.0, 4.0, 7.0, 6.0]}

    baseline = history_fetcher.calculate_baseline_profile(samples)

    assert 12 in baseline
    # sorted: [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    # len=10, idx=2 -> 3.0
    assert baseline[12] == 3.0


def test_calculate_baseline_profile_few_samples(history_fetcher):
    """Test baseline calculation with few samples uses min."""
    samples = {
        10: [5.0, 2.0]  # Only 2 samples
    }

    baseline = history_fetcher.calculate_baseline_profile(samples)

    assert baseline[10] == 2.0  # Min value


def test_separate_hvac_load_no_climate_data(history_fetcher):
    """Test separation when no climate data is available (all to non-HVAC)."""
    weekday = {12: [1.0, 2.0]}
    weekend = {12: [3.0]}

    non_hvac, hvac = history_fetcher._separate_hvac_load(weekday, weekend, None)

    assert 12 in non_hvac
    assert len(non_hvac[12]) == 3
    assert sorted(non_hvac[12]) == [1.0, 2.0, 3.0]
    assert not hvac  # Empty


def test_separate_hvac_load_with_hvac_active(history_fetcher):
    """Test separation when climate data indicates HVAC active (all to HVAC)."""
    weekday = {12: [1.0, 2.0]}
    weekend = {12: [3.0]}

    climate_states = {"climate.ac": {"hvac_action": "cooling"}}

    non_hvac, hvac = history_fetcher._separate_hvac_load(
        weekday, weekend, climate_states
    )

    assert not non_hvac  # Empty
    assert 12 in hvac
    assert len(hvac[12]) == 3
    assert sorted(hvac[12]) == [1.0, 2.0, 3.0]


def test_separate_hvac_load_with_hvac_idle(history_fetcher):
    """Test separation when climate data indicates HVAC idle (all to non-HVAC)."""
    weekday = {12: [1.0, 2.0]}
    weekend = {12: [3.0]}

    climate_states = {"climate.ac": {"hvac_action": "idle"}}

    non_hvac, hvac = history_fetcher._separate_hvac_load(
        weekday, weekend, climate_states
    )

    assert 12 in non_hvac
    assert len(non_hvac[12]) == 3
    assert not hvac

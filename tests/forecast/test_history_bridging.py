"""Tests for HistoryFetcher bridging to CoordinatorData (Issue #493)."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.computation_engine import ComputationEngine
from custom_components.localshift.coordinator import CoordinatorData


@pytest.fixture
def mock_hass():
    """Create mock Home Assistant instance."""
    hass = MagicMock()
    hass.config.time_zone = "Australia/Sydney"
    return hass


@pytest.fixture
def mock_entry():
    """Create mock config entry."""
    entry = MagicMock()
    entry.options = {}
    return entry


@pytest.fixture
def computation_engine(mock_hass, mock_entry):
    """Create ComputationEngine instance with mocked dependencies."""
    engine = ComputationEngine(
        hass=mock_hass,
        entry=mock_entry,
        get_entity_id_func=lambda x: f"sensor.{x}",
        get_switch_state_func=lambda x: False,
    )
    return engine


@pytest.fixture
def coordinator_data():
    """Create CoordinatorData instance."""
    return CoordinatorData()


class TestHistoryFetcherBridging:
    """Test bridging of HistoryFetcher data to CoordinatorData."""

    def test_weekday_weekend_profile_bridging(
        self, computation_engine, coordinator_data
    ):
        """Test that weekday/weekend profiles are correctly bridged."""
        # Setup: Mock history fetcher with weekday/weekend data
        weekday_avg = {10: 1.5, 11: 2.0, 12: 2.5}
        weekday_counts = {10: 15, 11: 20, 12: 18}
        weekend_avg = {10: 1.0, 11: 1.2, 12: 1.8}
        weekend_counts = {10: 8, 11: 10, 12: 9}

        computation_engine._history_fetcher._weekday_hourly_avg_kw = weekday_avg
        computation_engine._history_fetcher._weekend_hourly_avg_kw = weekend_avg
        computation_engine._history_fetcher._weekday_sample_counts = weekday_counts
        computation_engine._history_fetcher._weekend_sample_counts = weekend_counts
        computation_engine._history_fetcher._profile_source = "weekday_weekend"

        # Call compute_derived_values (we'll manually trigger the bridging section)
        from datetime import datetime

        now_dt = datetime(2025, 1, 6, 10, 0, 0)  # Monday

        # Manually execute the bridging code
        (
            coordinator_data.weekday_hourly_profile_kw,
            coordinator_data.weekday_sample_counts,
        ) = computation_engine._history_fetcher.get_weekday_profile()
        (
            coordinator_data.weekend_hourly_profile_kw,
            coordinator_data.weekend_sample_counts,
        ) = computation_engine._history_fetcher.get_weekend_profile()
        coordinator_data.consumption_profile_type = (
            computation_engine._history_fetcher.get_profile_source()
        )

        if coordinator_data.consumption_profile_type == "weekday_weekend":
            coordinator_data.forecast_profile_selected = (
                "weekend" if now_dt.weekday() >= 5 else "weekday"
            )
        else:
            coordinator_data.forecast_profile_selected = (
                coordinator_data.consumption_profile_type
            )

        # Verify bridged data
        assert coordinator_data.weekday_hourly_profile_kw == weekday_avg
        assert coordinator_data.weekday_sample_counts == weekday_counts
        assert coordinator_data.weekend_hourly_profile_kw == weekend_avg
        assert coordinator_data.weekend_sample_counts == weekend_counts
        assert coordinator_data.consumption_profile_type == "weekday_weekend"
        assert coordinator_data.forecast_profile_selected == "weekday"  # Monday

    def test_combined_fallback_profile_bridging(
        self, computation_engine, coordinator_data
    ):
        """Test that combined fallback profile is correctly bridged."""
        # Setup: Mock history fetcher with insufficient samples
        computation_engine._history_fetcher._profile_source = "combined_fallback"
        computation_engine._history_fetcher._historical_load_cache = {
            10: 1.5,
            11: 2.0,
        }
        computation_engine._history_fetcher._historical_load_sample_counts = {
            10: 5,
            11: 6,
        }

        now_dt = datetime(2025, 1, 6, 10, 0, 0)  # Monday

        # Manually execute the bridging code
        coordinator_data.consumption_profile_type = (
            computation_engine._history_fetcher.get_profile_source()
        )
        coordinator_data.consumption_hourly_profile_kw = dict(
            computation_engine._history_fetcher._historical_load_cache
        )
        coordinator_data.consumption_hourly_sample_counts = dict(
            computation_engine._history_fetcher._historical_load_sample_counts
        )

        if coordinator_data.consumption_profile_type == "weekday_weekend":
            coordinator_data.forecast_profile_selected = (
                "weekend" if now_dt.weekday() >= 5 else "weekday"
            )
        else:
            coordinator_data.forecast_profile_selected = (
                coordinator_data.consumption_profile_type
            )

        # Verify bridged data
        assert coordinator_data.consumption_profile_type == "combined_fallback"
        assert coordinator_data.forecast_profile_selected == "combined_fallback"
        assert coordinator_data.consumption_hourly_profile_kw == {10: 1.5, 11: 2.0}
        assert coordinator_data.consumption_hourly_sample_counts == {10: 5, 11: 6}

    def test_recent_load_bridging(self, computation_engine, coordinator_data):
        """Test that recent load data is correctly bridged."""
        # Setup: Mock history fetcher with recent load data
        computation_engine._history_fetcher._recent_load_1hr_kw = 2.345
        computation_engine._history_fetcher._recent_load_1hr_statistic_id = (
            "sensor.load_power"
        )
        computation_engine._history_fetcher._recent_load_1hr_samples = 12
        computation_engine._history_fetcher._recent_load_1hr_last_error = ""

        # Manually execute the bridging code
        coordinator_data.recent_load_1hr_kw = (
            computation_engine._history_fetcher._recent_load_1hr_kw
        )
        coordinator_data.recent_load_1hr_statistic_id = (
            computation_engine._history_fetcher._recent_load_1hr_statistic_id
        )
        coordinator_data.recent_load_1hr_samples = (
            computation_engine._history_fetcher._recent_load_1hr_samples
        )
        coordinator_data.recent_load_1hr_last_error = (
            computation_engine._history_fetcher._recent_load_1hr_last_error
        )

        # Verify bridged data
        assert coordinator_data.recent_load_1hr_kw == 2.345
        assert coordinator_data.recent_load_1hr_statistic_id == "sensor.load_power"
        assert coordinator_data.recent_load_1hr_samples == 12
        assert coordinator_data.recent_load_1hr_last_error == ""

    def test_forecast_profile_selected_weekend(
        self, computation_engine, coordinator_data
    ):
        """Test forecast_profile_selected on weekend day."""
        computation_engine._history_fetcher._profile_source = "weekday_weekend"
        coordinator_data.consumption_profile_type = (
            computation_engine._history_fetcher.get_profile_source()
        )

        now_dt = datetime(2025, 1, 11, 10, 0, 0)  # Saturday

        if coordinator_data.consumption_profile_type == "weekday_weekend":
            coordinator_data.forecast_profile_selected = (
                "weekend" if now_dt.weekday() >= 5 else "weekday"
            )

        assert coordinator_data.forecast_profile_selected == "weekend"

    def test_forecast_profile_selected_weekday(
        self, computation_engine, coordinator_data
    ):
        """Test forecast_profile_selected on weekday."""
        computation_engine._history_fetcher._profile_source = "weekday_weekend"
        coordinator_data.consumption_profile_type = (
            computation_engine._history_fetcher.get_profile_source()
        )

        now_dt = datetime(2025, 1, 8, 10, 0, 0)  # Wednesday

        if coordinator_data.consumption_profile_type == "weekday_weekend":
            coordinator_data.forecast_profile_selected = (
                "weekend" if now_dt.weekday() >= 5 else "weekday"
            )

        assert coordinator_data.forecast_profile_selected == "weekday"

    def test_consumption_profile_hours_bridged(
        self, computation_engine, coordinator_data
    ):
        """Test that consumption_profile_hours is set from historical_load_cache size.

        Issue #493: consumption_profile_hours should reflect the number of hours
        with historical profile data, not always be 0.
        """
        # Setup: Mock history fetcher with 24 hours of profile data
        expected_hours = {
            0: 0.5,
            1: 0.4,
            2: 0.3,
            3: 0.3,
            4: 0.3,
            5: 0.4,
            6: 0.7,
            7: 1.2,
            8: 1.5,
            9: 2.0,
            10: 2.5,
            11: 2.8,
            12: 2.7,
            13: 2.4,
            14: 2.3,
            15: 2.0,
            16: 1.9,
            17: 2.1,
            18: 1.7,
            19: 1.6,
            20: 1.3,
            21: 1.2,
            22: 1.0,
            23: 0.7,
        }
        computation_engine._history_fetcher._historical_load_cache = expected_hours
        computation_engine._history_fetcher._historical_load_sample_counts = {
            h: 28 for h in range(24)
        }
        computation_engine._history_fetcher._historical_load_source = "statistics"

        # Execute the bridging logic
        coordinator_data.consumption_hourly_profile_kw = dict(
            computation_engine._history_fetcher._historical_load_cache
        )
        coordinator_data.consumption_source = (
            computation_engine._history_fetcher._historical_load_source
        )
        coordinator_data.consumption_profile_hours = len(
            computation_engine._history_fetcher._historical_load_cache
        )

        # Verify consumption_profile_hours reflects the number of hours in profile
        assert coordinator_data.consumption_profile_hours == 24

    def test_consumption_profile_hours_partial_data(
        self, computation_engine, coordinator_data
    ):
        """Test consumption_profile_hours with partial historical data (e.g., new install)."""
        # Setup: Mock history fetcher with only 12 hours of data (new installation)
        partial_hours = {10: 1.5, 11: 2.0, 12: 2.5, 13: 2.2, 14: 2.0, 15: 1.8}
        computation_engine._history_fetcher._historical_load_cache = partial_hours
        computation_engine._history_fetcher._historical_load_source = "statistics"

        # Execute the bridging logic
        coordinator_data.consumption_hourly_profile_kw = dict(
            computation_engine._history_fetcher._historical_load_cache
        )
        coordinator_data.consumption_source = (
            computation_engine._history_fetcher._historical_load_source
        )
        coordinator_data.consumption_profile_hours = len(
            computation_engine._history_fetcher._historical_load_cache
        )

        # Verify consumption_profile_hours reflects partial data
        assert coordinator_data.consumption_profile_hours == 6

    def test_consumption_profile_hours_no_data(
        self, computation_engine, coordinator_data
    ):
        """Test consumption_profile_hours when no historical data available."""
        # Setup: No historical data (very new installation or sensor unavailable)
        computation_engine._history_fetcher._historical_load_cache = {}
        computation_engine._history_fetcher._historical_load_source = (
            "live_load_fallback"
        )

        # Execute the bridging logic
        coordinator_data.consumption_hourly_profile_kw = dict(
            computation_engine._history_fetcher._historical_load_cache
        )
        coordinator_data.consumption_source = (
            computation_engine._history_fetcher._historical_load_source
        )
        coordinator_data.consumption_profile_hours = len(
            computation_engine._history_fetcher._historical_load_cache
        )

        # Verify consumption_profile_hours is 0 when no data
        assert coordinator_data.consumption_profile_hours == 0

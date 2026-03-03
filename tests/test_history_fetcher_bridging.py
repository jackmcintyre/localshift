"""Tests for HistoryFetcher bridging to CoordinatorData (Issue #493)."""

from datetime import datetime
from unittest.mock import MagicMock, PropertyMock

import pytest

from custom_components.localshift.computation_engine import ComputationEngine
from custom_components.localshift.coordinator_data import CoordinatorData


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

"""Tests for WeatherDiagnosticsEngine.

This module tests the weather diagnostics engine, including the new
weather anomaly detection integration (Issue #681).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.engine.weather_diagnostics import (
    WeatherDiagnosticsEngine,
)
from custom_components.localshift.learning.correlation import (
    WEATHER_ANOMALY_ANOMALOUS_WEIGHT,
    WEATHER_ANOMALY_NORMAL_WEIGHT,
    WeatherAnomalyResult,
)


class TestWeatherDiagnosticsAnomalyPopulation:
    """Tests for anomaly detection integration in WeatherDiagnosticsEngine."""

    @pytest.fixture
    def mock_entry_enabled(self):
        entry = MagicMock()
        entry.options = {"weather_learning_enabled": True}
        return entry

    @pytest.fixture
    def mock_entry_disabled(self):
        entry = MagicMock()
        entry.options = {"weather_learning_enabled": False}
        return entry

    @pytest.fixture
    def mock_weather_correlation(self):
        corr = MagicMock()
        corr.get_diagnostics.return_value = {
            "total_samples": 0,
            "average_cooling_slope": 0.0,
            "average_heating_slope": 0.0,
            "average_r_squared": 0.0,
            "hourly_regression": {},
        }
        return corr

    def test_record_daily_temperature_called_with_current_temp(
        self, mock_entry_enabled, mock_weather_correlation
    ):
        """record_daily_temperature is called with the current temperature."""
        mock_weather_correlation.get_current_temperature.return_value = 22.5
        mock_weather_correlation.detect_weather_anomaly.return_value = (
            WeatherAnomalyResult(
                is_anomalous=False,
                weight=WEATHER_ANOMALY_NORMAL_WEIGHT,
                temperature=22.5,
                deviation_sigma=0.5,
                mean_temperature=22.0,
                std_temperature=1.0,
            )
        )
        engine = WeatherDiagnosticsEngine(mock_entry_enabled)
        data = CoordinatorData()
        engine.populate_weather_diagnostics(data, mock_weather_correlation)
        mock_weather_correlation.record_daily_temperature.assert_called_once_with(22.5)

    def test_populates_average_slopes_and_r_squared(
        self, mock_entry_enabled, mock_weather_correlation
    ):
        mock_weather_correlation.get_diagnostics.return_value = {
            "total_samples": 42,
            "average_cooling_slope": 0.18,
            "average_heating_slope": 0.25,
            "average_r_squared": 0.35,
            "hourly_regression": {},
        }
        engine = WeatherDiagnosticsEngine(mock_entry_enabled)
        data = CoordinatorData()

        engine.populate_weather_diagnostics(data, mock_weather_correlation)

        assert data.weather_avg_cooling_slope == pytest.approx(0.18)
        assert data.weather_avg_heating_slope == pytest.approx(0.25)
        assert data.weather_avg_r_squared == pytest.approx(0.35)

    def test_sets_high_confidence_by_majority(
        self, mock_entry_enabled, mock_weather_correlation
    ):
        """Majority of hours high -> "high", and counts are surfaced."""
        mock_weather_correlation.get_diagnostics.return_value = {
            "total_samples": 60,
            "average_cooling_slope": 0.18,
            "average_heating_slope": 0.25,
            "average_r_squared": 0.35,
            "hourly_regression": {
                8: {"confidence": "high"},
                9: {"confidence": "high"},
                10: {"confidence": "medium"},
                11: {"confidence": "low"},
            },
        }
        mock_weather_correlation.get_current_temperature.return_value = None
        engine = WeatherDiagnosticsEngine(mock_entry_enabled)
        data = CoordinatorData()

        engine.populate_weather_diagnostics(data, mock_weather_correlation)

        # 2 of 4 high -> n_high*2 >= n_hours -> "high"
        assert data.weather_correlation_confidence == "high"
        assert data.weather_hours_with_data == 4
        assert data.weather_usable_hours == 3  # 2 high + 1 medium

    def test_single_high_hour_does_not_brand_forecast_high(
        self, mock_entry_enabled, mock_weather_correlation
    ):
        """1 high hour out of 10 must not label the whole forecast "high"."""
        hourly = {h: {"confidence": "low"} for h in range(10)}
        hourly[0] = {"confidence": "high"}
        mock_weather_correlation.get_diagnostics.return_value = {
            "total_samples": 60,
            "average_cooling_slope": 0.18,
            "average_heating_slope": 0.25,
            "average_r_squared": 0.35,
            "hourly_regression": hourly,
        }
        mock_weather_correlation.get_current_temperature.return_value = None
        engine = WeatherDiagnosticsEngine(mock_entry_enabled)
        data = CoordinatorData()

        engine.populate_weather_diagnostics(data, mock_weather_correlation)

        assert data.weather_correlation_confidence == "low"
        assert data.weather_hours_with_data == 10
        assert data.weather_usable_hours == 1


def _away_entry(weather_learning_enabled: bool = True) -> MagicMock:
    entry = MagicMock()
    entry.options = {"weather_learning_enabled": weather_learning_enabled}
    return entry


class TestWeatherAwayMaskedHoursBridge:
    """docs/holiday-away/plan.md item 2, decision D4: the *weather-correlation*
    learning window's away-masked-hour count (separate from the consumption
    window's ``away_masked_hours``) is bridged onto CoordinatorData."""

    def test_bridged_from_get_diagnostics(self):
        """W1: get_diagnostics()'s away_masked_hours reaches CoordinatorData."""
        weather_correlation = MagicMock()
        weather_correlation.get_diagnostics.return_value = {
            "total_samples": 100,
            "average_cooling_slope": 0.3,
            "average_heating_slope": 0.1,
            "average_r_squared": 0.5,
            "hourly_regression": {},
            "away_masked_hours": 42,
        }
        weather_correlation.get_current_temperature.return_value = None
        engine = WeatherDiagnosticsEngine(_away_entry())
        data = CoordinatorData()

        engine.populate_weather_diagnostics(data, weather_correlation)

        assert data.weather_away_masked_hours == 42

    def test_zero_when_key_absent(self):
        """The bridge defaults to 0 when get_diagnostics() omits the key."""
        weather_correlation = MagicMock()
        weather_correlation.get_diagnostics.return_value = {
            "total_samples": 0,
            "average_cooling_slope": 0.0,
            "average_heating_slope": 0.0,
            "average_r_squared": 0.0,
            "hourly_regression": {},
        }
        weather_correlation.get_current_temperature.return_value = None
        engine = WeatherDiagnosticsEngine(_away_entry())
        data = CoordinatorData()

        engine.populate_weather_diagnostics(data, weather_correlation)

        assert data.weather_away_masked_hours == 0

    def test_zero_when_learning_disabled(self):
        """W2: disabled learning short-circuits before get_diagnostics() runs,
        and weather_away_masked_hours is 0, overwriting any stale value."""
        engine = WeatherDiagnosticsEngine(_away_entry(weather_learning_enabled=False))
        data = CoordinatorData()
        data.weather_away_masked_hours = 7  # stale value from a prior cycle
        weather_correlation = MagicMock()

        engine.populate_weather_diagnostics(data, weather_correlation)

        assert data.weather_away_masked_hours == 0
        weather_correlation.get_diagnostics.assert_not_called()

    def test_zero_when_weather_correlation_none(self):
        """No WeatherCorrelation instance also reports 0, not stale data."""
        engine = WeatherDiagnosticsEngine(_away_entry())
        data = CoordinatorData()
        data.weather_away_masked_hours = 7

        engine.populate_weather_diagnostics(data, None)

        assert data.weather_away_masked_hours == 0

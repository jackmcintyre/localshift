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

    def test_anomaly_weight_is_1_when_learning_disabled(self, mock_entry_disabled):
        """When weather learning is disabled, weather_anomaly_weight is set to 1.0."""
        engine = WeatherDiagnosticsEngine(mock_entry_disabled)
        data = CoordinatorData()
        engine.populate_weather_diagnostics(data, None)
        assert data.weather_anomaly_weight == 1.0

    def test_anomaly_weight_is_1_when_correlation_is_none(self, mock_entry_enabled):
        """When weather_correlation is None, weather_anomaly_weight is set to 1.0."""
        engine = WeatherDiagnosticsEngine(mock_entry_enabled)
        data = CoordinatorData()
        engine.populate_weather_diagnostics(data, None)
        assert data.weather_anomaly_weight == 1.0

    def test_anomaly_weight_set_from_detect_weather_anomaly(
        self, mock_entry_enabled, mock_weather_correlation
    ):
        """Weight comes from detect_weather_anomaly when temperature is available."""
        mock_weather_correlation.get_current_temperature.return_value = 40.0
        mock_weather_correlation.detect_weather_anomaly.return_value = (
            WeatherAnomalyResult(
                is_anomalous=True,
                weight=WEATHER_ANOMALY_ANOMALOUS_WEIGHT,
                temperature=40.0,
                deviation_sigma=3.0,
                mean_temperature=20.0,
                std_temperature=2.0,
            )
        )
        engine = WeatherDiagnosticsEngine(mock_entry_enabled)
        data = CoordinatorData()
        engine.populate_weather_diagnostics(data, mock_weather_correlation)
        assert data.weather_anomaly_weight == WEATHER_ANOMALY_ANOMALOUS_WEIGHT

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

    def test_anomaly_weight_is_1_when_temperature_is_none(
        self, mock_entry_enabled, mock_weather_correlation
    ):
        """When get_current_temperature returns None, weight defaults to 1.0."""
        mock_weather_correlation.get_current_temperature.return_value = None
        engine = WeatherDiagnosticsEngine(mock_entry_enabled)
        data = CoordinatorData()
        engine.populate_weather_diagnostics(data, mock_weather_correlation)
        assert data.weather_anomaly_weight == 1.0
        mock_weather_correlation.record_daily_temperature.assert_not_called()

    def test_detect_weather_anomaly_called_with_current_temp(
        self, mock_entry_enabled, mock_weather_correlation
    ):
        """detect_weather_anomaly is called with the current temperature."""
        mock_weather_correlation.get_current_temperature.return_value = 18.0
        mock_weather_correlation.detect_weather_anomaly.return_value = (
            WeatherAnomalyResult(
                is_anomalous=False,
                weight=WEATHER_ANOMALY_NORMAL_WEIGHT,
                temperature=18.0,
                deviation_sigma=0.2,
                mean_temperature=19.0,
                std_temperature=1.5,
            )
        )
        engine = WeatherDiagnosticsEngine(mock_entry_enabled)
        data = CoordinatorData()
        engine.populate_weather_diagnostics(data, mock_weather_correlation)
        mock_weather_correlation.detect_weather_anomaly.assert_called_once_with(18.0)

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

"""Tests for learning/anomaly.py - weather anomaly detector."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from custom_components.localshift.learning.anomaly import (
    WEATHER_ANOMALY_ANOMALOUS_WEIGHT,
    WEATHER_ANOMALY_NORMAL_WEIGHT,
    WEATHER_TEMPERATURE_HISTORY_DAYS,
    WeatherAnomalyDetector,
)


def _populate_anomaly_history(
    detector: WeatherAnomalyDetector,
    temps: list[float],
    base_date: date | None = None,
) -> None:
    """Populate temperature history for anomaly detection tests."""
    if base_date is None:
        base_date = date(2024, 1, 1)
    for i, temp in enumerate(temps):
        d = base_date + timedelta(days=i)
        detector.record_daily_temperature(temp, date_key=d.isoformat())


class TestWeatherAnomalyDetection:
    """Tests for weather anomaly detection features."""

    def test_record_daily_temperature_stores_value(self, anomaly_detector):
        """Test that record_daily_temperature stores temperature values."""
        anomaly_detector.record_daily_temperature(22.5, date_key="2024-01-01")
        assert anomaly_detector.temperature_history["2024-01-01"] == 22.5

    def test_record_daily_temperature_is_idempotent_per_day(self, anomaly_detector):
        """Test that recording same day overwrites previous value."""
        anomaly_detector.record_daily_temperature(20.0, date_key="2024-01-01")
        anomaly_detector.record_daily_temperature(25.0, date_key="2024-01-01")
        assert anomaly_detector.temperature_history["2024-01-01"] == 25.0

    def test_record_daily_temperature_prunes_to_14_days(self, anomaly_detector):
        """Test that history is pruned to WEATHER_TEMPERATURE_HISTORY_DAYS."""
        for i in range(20):
            d = date(2024, 1, 1) + timedelta(days=i)
            anomaly_detector.record_daily_temperature(20.0, date_key=d.isoformat())
        assert (
            len(anomaly_detector.temperature_history)
            == WEATHER_TEMPERATURE_HISTORY_DAYS
        )

    def test_record_daily_temperature_keeps_newest_dates(self, anomaly_detector):
        """Test that pruning keeps the most recent dates."""
        for i in range(20):
            d = date(2024, 1, 1) + timedelta(days=i)
            anomaly_detector.record_daily_temperature(float(i), date_key=d.isoformat())
        oldest_retained = min(anomaly_detector.temperature_history.keys())
        assert oldest_retained == "2024-01-07"

    def test_record_daily_temperature_uses_today_by_default(self, anomaly_detector):
        """Test that record_daily_temperature uses today's date when not specified."""
        with patch("custom_components.localshift.learning.anomaly.dt_util") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2024-06-15"
            anomaly_detector.record_daily_temperature(22.0)
        assert "2024-06-15" in anomaly_detector.temperature_history

    def test_insufficient_history_returns_normal_weight(self, anomaly_detector):
        """Test that insufficient history (<7 days) returns normal weight."""
        _populate_anomaly_history(anomaly_detector, [20.0, 21.0, 22.0, 20.0, 21.0])
        result = anomaly_detector.detect_weather_anomaly(35.0)
        assert result.is_anomalous is False
        assert result.weight == WEATHER_ANOMALY_NORMAL_WEIGHT
        assert result.deviation_sigma == 0.0
        assert result.mean_temperature == 0.0
        assert result.std_temperature == 0.0

    def test_exactly_7_days_history_is_enough(self, anomaly_detector):
        """Test that exactly 7 days of history is sufficient."""
        _populate_anomaly_history(anomaly_detector, [20.0] * 7)
        result = anomaly_detector.detect_weather_anomaly(20.0)
        assert result.mean_temperature is not None

    def test_normal_temperature_not_flagged(self, anomaly_detector):
        """Test that normal temperature is not flagged as anomalous."""
        temps = [20.0, 21.0, 19.0, 20.5, 21.5, 20.0, 21.0, 20.0, 19.5, 21.0]
        _populate_anomaly_history(anomaly_detector, temps)
        result = anomaly_detector.detect_weather_anomaly(21.0)
        assert result.is_anomalous is False
        assert result.weight == WEATHER_ANOMALY_NORMAL_WEIGHT

    def test_extreme_high_temperature_is_anomalous(self, anomaly_detector):
        """Test that extreme high temperature is flagged as anomalous."""
        temps = [20.0] * 8 + [21.0, 19.0]
        _populate_anomaly_history(anomaly_detector, temps)
        result = anomaly_detector.detect_weather_anomaly(40.0)
        assert result.is_anomalous is True
        assert result.weight == WEATHER_ANOMALY_ANOMALOUS_WEIGHT
        assert result.deviation_sigma is not None
        assert result.deviation_sigma > 2.0

    def test_extreme_low_temperature_is_anomalous(self, anomaly_detector):
        """Test that extreme low temperature is flagged as anomalous."""
        temps = [20.0] * 8 + [21.0, 19.0]
        _populate_anomaly_history(anomaly_detector, temps)
        result = anomaly_detector.detect_weather_anomaly(0.0)
        assert result.is_anomalous is True
        assert result.weight == WEATHER_ANOMALY_ANOMALOUS_WEIGHT

    def test_anomaly_result_temperature_field(self, anomaly_detector):
        """Test that WeatherAnomalyResult includes the input temperature."""
        _populate_anomaly_history(anomaly_detector, [20.0] * 10)
        result = anomaly_detector.detect_weather_anomaly(35.0)
        assert result.temperature == 35.0

    def test_anomaly_result_mean_and_std_fields(self, anomaly_detector):
        """Test that WeatherAnomalyResult includes mean and std."""
        temps = [20.0] * 10
        _populate_anomaly_history(anomaly_detector, temps)
        result = anomaly_detector.detect_weather_anomaly(20.0)
        assert result.mean_temperature == pytest.approx(20.0)
        assert result.std_temperature == pytest.approx(0.0)

    def test_zero_std_does_not_raise(self, anomaly_detector):
        """Test that zero standard deviation is handled gracefully."""
        temps = [20.0] * 10
        _populate_anomaly_history(anomaly_detector, temps)
        result = anomaly_detector.detect_weather_anomaly(20.0)
        assert result.is_anomalous is False
        assert result.weight == WEATHER_ANOMALY_NORMAL_WEIGHT

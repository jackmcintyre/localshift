"""Weather diagnostic helpers for computation engine."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry

from ..const import (
    CONF_WEATHER_LEARNING_ENABLED,
    DEFAULT_WEATHER_LEARNING_ENABLED,
)
from ..coordinator.data import CoordinatorData
from ..learning.correlation import WeatherCorrelation

_LOGGER = logging.getLogger(__name__)


class WeatherDiagnosticsEngine:
    """Populate weather-correlation diagnostics in coordinator data."""

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize diagnostics engine."""
        self.entry = entry

    def populate_weather_diagnostics(
        self,
        data: CoordinatorData,
        weather_correlation: WeatherCorrelation | None,
    ) -> None:
        """Populate weather diagnostic fields for dashboards and debugging."""
        weather_learning_enabled = self.entry.options.get(
            CONF_WEATHER_LEARNING_ENABLED, DEFAULT_WEATHER_LEARNING_ENABLED
        )
        data.weather_learning_enabled = weather_learning_enabled

        if not weather_learning_enabled or weather_correlation is None:
            data.weather_correlation_confidence = "low"
            data.weather_adjustment_applied = False
            data.weather_avg_cooling_slope = 0.0
            data.weather_avg_heating_slope = 0.0
            data.weather_avg_r_squared = 0.0
            data.weather_sample_count = 0
            data.weather_anomaly_weight = 1.0
            return

        diagnostics = weather_correlation.get_diagnostics()

        data.weather_correlation_confidence = "low"
        data.weather_sample_count = diagnostics.get("total_samples", 0)
        data.weather_avg_cooling_slope = diagnostics.get("average_cooling_slope", 0.0)
        data.weather_avg_heating_slope = diagnostics.get("average_heating_slope", 0.0)
        data.weather_avg_r_squared = diagnostics.get("average_r_squared", 0.0)

        # Majority rule: the old "high if ANY hour is high" let a single good
        # hour brand the whole forecast "high". Report the label the bulk of
        # hours actually support, and surface the usable/total counts so the
        # number behind the label is visible.
        hourly_results = diagnostics.get("hourly_regression", {})
        confidences = [
            coef.get("confidence", "low") for coef in hourly_results.values()
        ]
        n_hours = len(confidences)
        n_high = confidences.count("high")
        n_usable = n_high + confidences.count("medium")
        if n_hours and n_high * 2 >= n_hours:
            data.weather_correlation_confidence = "high"
        elif n_hours and n_usable * 2 >= n_hours:
            data.weather_correlation_confidence = "medium"
        else:
            data.weather_correlation_confidence = "low"
        data.weather_usable_hours = n_usable
        data.weather_hours_with_data = n_hours

        # Issue #681: Weather anomaly detection for rollback protection
        current_temp = weather_correlation.get_current_temperature()
        if current_temp is not None:
            weather_correlation.record_daily_temperature(current_temp)
            anomaly_result = weather_correlation.detect_weather_anomaly(current_temp)
            data.weather_anomaly_weight = anomaly_result.weight
        else:
            data.weather_anomaly_weight = 1.0

        _LOGGER.debug(
            "Weather diagnostics: samples=%d, cooling=%.4f, heating=%.4f, r2=%.3f, confidence=%s",
            data.weather_sample_count,
            data.weather_avg_cooling_slope,
            data.weather_avg_heating_slope,
            data.weather_avg_r_squared,
            data.weather_correlation_confidence,
        )

from __future__ import annotations

import math
from dataclasses import dataclass

from homeassistant.util import dt as dt_util

# Issue #681: Weather anomaly detection constants
WEATHER_TEMPERATURE_HISTORY_DAYS = 14  # Days of temperature history to keep
WEATHER_ANOMALY_MIN_HISTORY_DAYS = 7  # Minimum days needed for anomaly detection
WEATHER_ANOMALY_SIGMA_THRESHOLD = 2.0  # Standard deviations for anomaly
WEATHER_ANOMALY_NORMAL_WEIGHT = 1.0  # Weight for normal days
WEATHER_ANOMALY_ANOMALOUS_WEIGHT = 0.3  # Weight for anomalous days (30%)


@dataclass
class WeatherAnomalyResult:
    """Result of weather anomaly detection (Issue #681).

    Attributes:
        is_anomalous: Whether the temperature is anomalous (±2σ from mean)
        weight: Weight to apply in rollback evaluation (0.3 or 1.0)
        temperature: The temperature that was evaluated
        deviation_sigma: How many standard deviations from mean (negative = below)
        mean_temperature: Mean of historical temperatures
        std_temperature: Standard deviation of historical temperatures

    """

    is_anomalous: bool
    weight: float
    temperature: float
    deviation_sigma: float
    mean_temperature: float
    std_temperature: float


class WeatherAnomalyDetector:
    """Detects anomalous weather based on temperature history."""

    def __init__(self, temperature_history: dict[str, float]) -> None:
        self._temperature_history = temperature_history

    @property
    def temperature_history(self) -> dict[str, float]:
        return self._temperature_history

    def set_temperature_history(self, temperature_history: dict[str, float]) -> None:
        self._temperature_history = temperature_history

    def record_daily_temperature(
        self, temperature: float, date_key: str | None = None
    ) -> None:
        """Record a daily temperature for anomaly detection (Issue #681).

        Idempotent: recording the same date twice overwrites the previous value.
        Automatically prunes history to WEATHER_TEMPERATURE_HISTORY_DAYS.

        Args:
            temperature: Temperature in °C
            date_key: ISO date string (YYYY-MM-DD), defaults to today

        """
        resolved_date_key: str
        if date_key is None:
            now = dt_util.now()
            if now is None:
                return
            resolved_date_key = now.strftime("%Y-%m-%d")
        else:
            resolved_date_key = date_key

        self._temperature_history[resolved_date_key] = temperature

        # Prune to keep only recent days
        if len(self._temperature_history) > WEATHER_TEMPERATURE_HISTORY_DAYS:
            sorted_dates = sorted(self._temperature_history.keys())
            for old_date in sorted_dates[
                : len(self._temperature_history) - WEATHER_TEMPERATURE_HISTORY_DAYS
            ]:
                del self._temperature_history[old_date]

    def detect_weather_anomaly(self, current_temp: float) -> WeatherAnomalyResult:
        """Detect if current temperature is anomalous (Issue #681).

        Anomalous means ±WEATHER_ANOMALY_SIGMA_THRESHOLD standard deviations
        from the WEATHER_TEMPERATURE_HISTORY_DAYS moving average.

        Args:
            current_temp: Current temperature in °C

        Returns:
            WeatherAnomalyResult with weight for rollback evaluation

        """
        history = self._temperature_history
        history_count = len(history)

        # Not enough history for reliable anomaly detection
        if history_count < WEATHER_ANOMALY_MIN_HISTORY_DAYS:
            return WeatherAnomalyResult(
                is_anomalous=False,
                weight=WEATHER_ANOMALY_NORMAL_WEIGHT,
                temperature=current_temp,
                deviation_sigma=0.0,
                mean_temperature=0.0,
                std_temperature=0.0,
            )

        temps = list(history.values())
        mean_temp = sum(temps) / len(temps)

        # Calculate standard deviation
        variance = sum((t - mean_temp) ** 2 for t in temps) / len(temps)
        std_temp = math.sqrt(variance)

        # Handle zero variance (all temperatures identical)
        if std_temp < 0.01:
            return WeatherAnomalyResult(
                is_anomalous=False,
                weight=WEATHER_ANOMALY_NORMAL_WEIGHT,
                temperature=current_temp,
                deviation_sigma=0.0,
                mean_temperature=mean_temp,
                std_temperature=std_temp,
            )

        deviation = (current_temp - mean_temp) / std_temp
        is_anomalous = abs(deviation) >= WEATHER_ANOMALY_SIGMA_THRESHOLD

        return WeatherAnomalyResult(
            is_anomalous=is_anomalous,
            weight=(
                WEATHER_ANOMALY_ANOMALOUS_WEIGHT
                if is_anomalous
                else WEATHER_ANOMALY_NORMAL_WEIGHT
            ),
            temperature=current_temp,
            deviation_sigma=deviation,
            mean_temperature=mean_temp,
            std_temperature=std_temp,
        )

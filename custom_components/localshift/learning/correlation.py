"""Weather correlation for temperature-based consumption prediction.

This module implements a degree-day model that learns the correlation between
temperature and household load. It uses Home Assistant's storage module for
persistence of learned coefficients.

The model learns separate coefficients for:
- Base load: Minimum load at mild temperatures (18-24°C band)
- Cooling coefficient: Additional kW per degree above cooling threshold
- Heating coefficient: Additional kW per degree below heating threshold

Each hour of the day has its own coefficients to capture daily patterns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_COOLING_THRESHOLD,
    CONF_HEATING_THRESHOLD,
    CONF_WEATHER_ENTITY,
    DEFAULT_COOLING_THRESHOLD,
    DEFAULT_HEATING_THRESHOLD,
    DOMAIN,
)
from .anomaly import (
    WEATHER_ANOMALY_ANOMALOUS_WEIGHT,
    WEATHER_ANOMALY_MIN_HISTORY_DAYS,
    WEATHER_ANOMALY_NORMAL_WEIGHT,
    WEATHER_ANOMALY_SIGMA_THRESHOLD,
    WEATHER_TEMPERATURE_HISTORY_DAYS,
    WeatherAnomalyDetector,
    WeatherAnomalyResult,
)
from .temperature import TemperatureForecast, TemperatureForecastProvider

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "TemperatureForecast",
    "WeatherAnomalyResult",
    "WEATHER_ANOMALY_ANOMALOUS_WEIGHT",
    "WEATHER_ANOMALY_MIN_HISTORY_DAYS",
    "WEATHER_ANOMALY_NORMAL_WEIGHT",
    "WEATHER_ANOMALY_SIGMA_THRESHOLD",
    "WEATHER_TEMPERATURE_HISTORY_DAYS",
]

# Storage version for migrations
STORAGE_VERSION = 2
STORAGE_KEY = "weather_correlation"

# Confidence thresholds based on sample count (legacy, retained for compatibility)
CONFIDENCE_LOW_THRESHOLD = 7  # Less than 7 samples = low confidence
CONFIDENCE_MEDIUM_THRESHOLD = 30  # 7-30 samples = medium, 30+ = high

# Regression configuration
MIN_TEMP_DELTA = 1.0
MIN_SAMPLES_PER_ZONE = 20
MIN_R_SQUARED = 0.10
MAX_SLOPE_KW_PER_DEGREE = 2.0
MAX_LOAD_MULTIPLIER = 3.0
SLIDING_WINDOW_DAYS = 30


@dataclass(slots=True)
class ZoneStats:
    """Sufficient statistics for regression fitting."""

    n: int = 0
    sum_x: float = 0.0
    sum_y: float = 0.0
    sum_xx: float = 0.0
    sum_xy: float = 0.0
    sum_yy: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "n": self.n,
            "sum_x": self.sum_x,
            "sum_y": self.sum_y,
            "sum_xx": self.sum_xx,
            "sum_xy": self.sum_xy,
            "sum_yy": self.sum_yy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ZoneStats:
        """Create from dictionary."""
        return cls(
            n=data.get("n", 0),
            sum_x=data.get("sum_x", 0.0),
            sum_y=data.get("sum_y", 0.0),
            sum_xx=data.get("sum_xx", 0.0),
            sum_xy=data.get("sum_xy", 0.0),
            sum_yy=data.get("sum_yy", 0.0),
        )


@dataclass(slots=True)
class HourlyRegressionData:
    """Per-hour regression data for daily snapshots."""

    mild: ZoneStats = field(default_factory=ZoneStats)
    heating: ZoneStats = field(default_factory=ZoneStats)
    cooling: ZoneStats = field(default_factory=ZoneStats)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "mild": self.mild.to_dict(),
            "heating": self.heating.to_dict(),
            "cooling": self.cooling.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HourlyRegressionData:
        """Create from dictionary."""
        return cls(
            mild=ZoneStats.from_dict(data.get("mild", {})),
            heating=ZoneStats.from_dict(data.get("heating", {})),
            cooling=ZoneStats.from_dict(data.get("cooling", {})),
        )


@dataclass(slots=True)
class DailySnapshot:
    """Regression snapshot for a single day."""

    date_key: str
    data: HourlyRegressionData = field(default_factory=HourlyRegressionData)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {"date_key": self.date_key, "data": self.data.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DailySnapshot:
        """Create from dictionary."""
        return cls(
            date_key=data.get("date_key", ""),
            data=HourlyRegressionData.from_dict(data.get("data", {})),
        )


@dataclass(slots=True)
class HourlyRegressionResult:
    """Computed regression results for a single hour."""

    base_load_kw: float = 0.0
    heating_slope: float = 0.0
    cooling_slope: float = 0.0
    r_squared: float = 0.0
    sample_count: int = 0
    confidence: str = "low"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for diagnostics."""
        return {
            "base_load_kw": self.base_load_kw,
            "heating_slope": self.heating_slope,
            "cooling_slope": self.cooling_slope,
            "r_squared": self.r_squared,
            "sample_count": self.sample_count,
            "confidence": self.confidence,
        }


def _fit_zone_regression(
    stats: ZoneStats, base_load_kw: float = 0.0
) -> tuple[float, float]:
    """Fit a zero-intercept regression for a zone.

    Uses sufficient statistics to compute slope and R^2. The base load is
    treated as a fixed intercept and removed from the dependent variable.
    """
    if stats.n < 2 or stats.sum_xx <= 0:
        return 0.0, 0.0

    adjusted_sum_xy = stats.sum_xy - base_load_kw * stats.sum_x
    adjusted_sum_yy = (
        stats.sum_yy - 2 * base_load_kw * stats.sum_y + stats.n * base_load_kw**2
    )

    slope = adjusted_sum_xy / stats.sum_xx

    if adjusted_sum_yy <= 0:
        return slope, 0.0

    r_squared = (adjusted_sum_xy * adjusted_sum_xy) / (stats.sum_xx * adjusted_sum_yy)
    return slope, r_squared


@dataclass
class WeatherCorrelationData:
    """Complete weather correlation data structure for storage.

    Attributes:
        version: Schema version for migrations
        weather_entity_id: Configured weather entity
        cooling_threshold: Temperature above which cooling load increases
        heating_threshold: Temperature below which heating load increases
        learning_stats: Aggregated statistics for diagnostics

    """

    version: int = STORAGE_VERSION
    weather_entity_id: str = ""
    cooling_threshold: float = 24.0  # °C
    heating_threshold: float = 18.0  # °C
    daily_regression_stats: dict[int, list[DailySnapshot]] = field(default_factory=dict)
    learning_stats: dict[str, Any] = field(default_factory=dict)
    temperature_history: dict[str, float] = field(
        default_factory=dict
    )  # Issue #681: ISO date -> temp

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "version": self.version,
            "weather_entity_id": self.weather_entity_id,
            "cooling_threshold": self.cooling_threshold,
            "heating_threshold": self.heating_threshold,
            "daily_regression_stats": {
                str(hour): [snapshot.to_dict() for snapshot in snapshots]
                for hour, snapshots in self.daily_regression_stats.items()
            },
            "learning_stats": self.learning_stats,
            "temperature_history": self.temperature_history,  # Issue #681
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WeatherCorrelationData:
        """Create from dictionary."""
        version = data.get("version", 1)
        if version == 1:
            _LOGGER.info(
                "Migrating weather correlation storage from v1 to v2; "
                "discarding hourly coefficients"
            )
            return cls(
                version=STORAGE_VERSION,
                weather_entity_id=data.get("weather_entity_id", ""),
                cooling_threshold=data.get("cooling_threshold", 24.0),
                heating_threshold=data.get("heating_threshold", 18.0),
                daily_regression_stats={},
                learning_stats=data.get("learning_stats", {}),
                temperature_history=data.get("temperature_history", {}),
            )

        daily_regression_stats: dict[int, list[DailySnapshot]] = {}
        for hour_str, snapshots in data.get("daily_regression_stats", {}).items():
            hour = int(hour_str)
            daily_regression_stats[hour] = [
                DailySnapshot.from_dict(snapshot) for snapshot in snapshots
            ]

        return cls(
            version=data.get("version", STORAGE_VERSION),
            weather_entity_id=data.get("weather_entity_id", ""),
            cooling_threshold=data.get("cooling_threshold", 24.0),
            heating_threshold=data.get("heating_threshold", 18.0),
            daily_regression_stats=daily_regression_stats,
            learning_stats=data.get("learning_stats", {}),
            temperature_history=data.get("temperature_history", {}),  # Issue #681
        )


class WeatherCorrelation:
    """Main class for weather-based consumption prediction.

    This class manages:
    - Loading/saving learned coefficients to HA storage
    - Learning from temperature/load observations
    - Predicting load adjustments based on temperature forecasts
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        """Initialize weather correlation.

        Args:
            hass: Home Assistant instance
            entry: Config entry for the integration

        """
        self.hass = hass
        self.entry = entry
        self._store = Store[dict[str, Any]](
            hass, STORAGE_VERSION, f"{DOMAIN}_{STORAGE_KEY}"
        )
        self._data: WeatherCorrelationData = WeatherCorrelationData()
        self._initialized = False

        # Accumulated observations for batch learning
        self._pending_observations: list[
            tuple[int, float, float]
        ] = []  # (hour, temp, load)

        self._temperature_provider = TemperatureForecastProvider(
            hass, entry, self._data.weather_entity_id
        )
        self._anomaly_detector = WeatherAnomalyDetector(self._data.temperature_history)

    async def async_initialize(self) -> None:
        """Load persisted data from storage."""
        if self._initialized:
            return

        stored_data = await self._store.async_load()
        if stored_data is not None:
            self._data = WeatherCorrelationData.from_dict(stored_data)
            total_samples = 0
            for snapshots in self._data.daily_regression_stats.values():
                for snapshot in snapshots:
                    total_samples += (
                        snapshot.data.mild.n
                        + snapshot.data.heating.n
                        + snapshot.data.cooling.n
                    )
            _LOGGER.info(
                "Loaded weather correlation data: %d hours, %d total samples",
                len(self._data.daily_regression_stats),
                total_samples,
            )
        else:
            # Initialize with config values
            # Check options first (user can change via Configure UI),
            # then fall back to data (initial setup)
            self._data.weather_entity_id = self.entry.options.get(
                CONF_WEATHER_ENTITY, ""
            ) or self.entry.data.get(CONF_WEATHER_ENTITY, "")
            self._data.cooling_threshold = self.entry.options.get(
                CONF_COOLING_THRESHOLD, DEFAULT_COOLING_THRESHOLD
            )
            self._data.heating_threshold = self.entry.options.get(
                CONF_HEATING_THRESHOLD, DEFAULT_HEATING_THRESHOLD
            )
            _LOGGER.info("Initialized new weather correlation data")

        self._temperature_provider.set_weather_entity_id(self._data.weather_entity_id)
        self._anomaly_detector.set_temperature_history(self._data.temperature_history)

        self._initialized = True

    async def async_save(self) -> None:
        """Persist coefficients to storage."""
        await self._store.async_save(self._data.to_dict())
        _LOGGER.debug("Saved weather correlation data to storage")

    async def async_reset(self) -> None:
        """Clear regression stats without touching temperature history."""
        self._data.daily_regression_stats.clear()
        await self.async_save()

    def get_temperature_forecast(self) -> list[TemperatureForecast]:
        """Fetch forecasted temperatures from weather entity.

        Returns:
            List of TemperatureForecast objects for upcoming hours.

        """
        return self._temperature_provider.get_temperature_forecast()

    async def async_get_temperature_forecast(
        self, force_refresh: bool = False
    ) -> list[TemperatureForecast]:
        """Fetch forecasted temperatures using weather.get_forecasts service.

        Uses Home Assistant's modern weather.get_forecasts service (HA 2024.3+)
        with caching to avoid excessive service calls.

        Args:
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            List of TemperatureForecast objects for upcoming hours.

        """
        forecasts = await self._temperature_provider.async_get_temperature_forecast(
            force_refresh=force_refresh
        )
        self._data.weather_entity_id = self._temperature_provider.weather_entity_id
        return forecasts

    def get_current_temperature(self) -> float | None:
        """Get current temperature from weather entity.

        Returns:
            Current temperature in °C, or None if unavailable.

        """
        return self._temperature_provider.get_current_temperature()

    def learn_from_sample(
        self, hour: int, temperature: float, actual_load_kw: float
    ) -> None:
        """Update coefficients based on observed temperature/load pair.

        This implements a sliding-window regression based on sufficient statistics.

        Args:
            hour: Hour of day (0-23)
            temperature: Observed temperature in °C
            actual_load_kw: Observed load in kW

        """
        if not (0 <= hour <= 23):
            _LOGGER.warning("Invalid hour %d, must be 0-23", hour)
            return

        date_key = self._today_key()
        snapshot = self._get_or_create_daily_snapshot(hour, date_key)
        cooling_threshold = self._data.cooling_threshold
        heating_threshold = self._data.heating_threshold

        if temperature > cooling_threshold:
            temp_delta = temperature - cooling_threshold
            if temp_delta >= MIN_TEMP_DELTA:
                self._update_zone_stats(
                    snapshot.data.cooling, temp_delta, actual_load_kw
                )
        elif temperature < heating_threshold:
            temp_delta = heating_threshold - temperature
            if temp_delta >= MIN_TEMP_DELTA:
                self._update_zone_stats(
                    snapshot.data.heating, temp_delta, actual_load_kw
                )
        else:
            self._update_zone_stats(snapshot.data.mild, 0.0, actual_load_kw)

        self._prune_daily_snapshots()

        _LOGGER.debug(
            "Learned sample for hour %d: temp=%.1f°C, load=%.2fkW",
            hour,
            temperature,
            actual_load_kw,
        )

    def predict_load(
        self, hour: int, temperature: float, base_load_kw: float
    ) -> tuple[float, str]:
        """Apply learned coefficients to predict load given temperature.

        Args:
            hour: Hour of day (0-23)
            temperature: Forecasted temperature in °C
            base_load_kw: Base load estimate from historical profile

        Returns:
            Tuple of (predicted_load_kw, adjustment_source)

        """
        if not (0 <= hour <= 23):
            return base_load_kw, "invalid_hour"

        aggregated = self._aggregate_hourly_stats(hour)
        if aggregated is None:
            return base_load_kw, "no_coefficients"

        base_load = self._average_mild_load(aggregated.mild)
        if base_load <= 0:
            base_load = base_load_kw

        cooling_threshold = self._data.cooling_threshold
        heating_threshold = self._data.heating_threshold

        if temperature > cooling_threshold:
            temp_delta = temperature - cooling_threshold
            if temp_delta < MIN_TEMP_DELTA:
                return base_load_kw, "weather_none"
            slope, r_squared = _fit_zone_regression(aggregated.cooling, base_load)
            slope = min(max(slope, 0.0), MAX_SLOPE_KW_PER_DEGREE)
            if aggregated.cooling.n < MIN_SAMPLES_PER_ZONE or r_squared < MIN_R_SQUARED:
                return base_load_kw, "low_confidence"
            predicted_load = base_load_kw + slope * temp_delta
            cap = max(base_load_kw, 0.1) * MAX_LOAD_MULTIPLIER
            predicted_load = min(predicted_load, cap)
            return max(0.0, predicted_load), "weather_cooling"

        if temperature < heating_threshold:
            temp_delta = heating_threshold - temperature
            if temp_delta < MIN_TEMP_DELTA:
                return base_load_kw, "weather_none"
            slope, r_squared = _fit_zone_regression(aggregated.heating, base_load)
            slope = min(max(slope, 0.0), MAX_SLOPE_KW_PER_DEGREE)
            if aggregated.heating.n < MIN_SAMPLES_PER_ZONE or r_squared < MIN_R_SQUARED:
                return base_load_kw, "low_confidence"
            predicted_load = base_load_kw + slope * temp_delta
            cap = max(base_load_kw, 0.1) * MAX_LOAD_MULTIPLIER
            predicted_load = min(predicted_load, cap)
            return max(0.0, predicted_load), "weather_heating"

        return base_load_kw, "weather_none"

    def _calculate_confidence(
        self, sample_count: int, r_squared: float | None = None
    ) -> str:
        """Calculate confidence level based on sample count.

        Args:
            sample_count: Number of samples used for learning

        Returns:
            Confidence level: "low", "medium", or "high"

        """
        if sample_count < MIN_SAMPLES_PER_ZONE:
            return "low"
        if r_squared is not None and r_squared < MIN_R_SQUARED:
            return "low"
        if sample_count < CONFIDENCE_MEDIUM_THRESHOLD:
            return "medium"
        else:
            return "high"

    @staticmethod
    def _update_zone_stats(stats: ZoneStats, x_value: float, y_value: float) -> None:
        stats.n += 1
        stats.sum_x += x_value
        stats.sum_y += y_value
        stats.sum_xx += x_value * x_value
        stats.sum_xy += x_value * y_value
        stats.sum_yy += y_value * y_value

    @staticmethod
    def _merge_zone_stats(target: ZoneStats, source: ZoneStats) -> None:
        target.n += source.n
        target.sum_x += source.sum_x
        target.sum_y += source.sum_y
        target.sum_xx += source.sum_xx
        target.sum_xy += source.sum_xy
        target.sum_yy += source.sum_yy

    def _aggregate_hourly_stats(self, hour: int) -> HourlyRegressionData | None:
        snapshots = self._data.daily_regression_stats.get(hour)
        if not snapshots:
            return None
        aggregated = HourlyRegressionData()
        for snapshot in snapshots:
            self._merge_zone_stats(aggregated.mild, snapshot.data.mild)
            self._merge_zone_stats(aggregated.heating, snapshot.data.heating)
            self._merge_zone_stats(aggregated.cooling, snapshot.data.cooling)
        return aggregated

    @staticmethod
    def _average_mild_load(stats: ZoneStats) -> float:
        if stats.n <= 0:
            return 0.0
        return stats.sum_y / stats.n

    def _build_hourly_result(
        self, aggregated: HourlyRegressionData
    ) -> HourlyRegressionResult:
        base_load = self._average_mild_load(aggregated.mild)
        heating_slope, heating_r2 = _fit_zone_regression(aggregated.heating, base_load)
        cooling_slope, cooling_r2 = _fit_zone_regression(aggregated.cooling, base_load)

        heating_slope = min(max(heating_slope, 0.0), MAX_SLOPE_KW_PER_DEGREE)
        cooling_slope = min(max(cooling_slope, 0.0), MAX_SLOPE_KW_PER_DEGREE)

        heating_conf = self._calculate_confidence(aggregated.heating.n, heating_r2)
        cooling_conf = self._calculate_confidence(aggregated.cooling.n, cooling_r2)

        confidence = "low"
        if "high" in (heating_conf, cooling_conf):
            confidence = "high"
        elif "medium" in (heating_conf, cooling_conf):
            confidence = "medium"

        r_squared_values = [
            value
            for value, count in (
                (heating_r2, aggregated.heating.n),
                (cooling_r2, aggregated.cooling.n),
            )
            if count >= MIN_SAMPLES_PER_ZONE
        ]
        r_squared = 0.0
        if r_squared_values:
            r_squared = sum(r_squared_values) / len(r_squared_values)

        sample_count = aggregated.mild.n + aggregated.heating.n + aggregated.cooling.n

        return HourlyRegressionResult(
            base_load_kw=base_load,
            heating_slope=heating_slope,
            cooling_slope=cooling_slope,
            r_squared=r_squared,
            sample_count=sample_count,
            confidence=confidence,
        )

    def get_diagnostics(self) -> dict[str, Any]:
        """Return learning statistics for diagnostics.

        Returns:
            Dictionary with learning stats and coefficient summaries.

        """
        total_samples = 0
        avg_cooling = 0.0
        avg_heating = 0.0
        avg_r_squared = 0.0
        cooling_count = 0
        heating_count = 0
        r_squared_count = 0

        hourly_results: dict[int, HourlyRegressionResult] = {}
        for hour in sorted(self._data.daily_regression_stats.keys()):
            aggregated = self._aggregate_hourly_stats(hour)
            if aggregated is None:
                continue
            result = self._build_hourly_result(aggregated)
            hourly_results[hour] = result
            total_samples += result.sample_count

            if result.cooling_slope > 0:
                avg_cooling += result.cooling_slope
                cooling_count += 1
            if result.heating_slope > 0:
                avg_heating += result.heating_slope
                heating_count += 1
            if result.r_squared > 0:
                avg_r_squared += result.r_squared
                r_squared_count += 1

        if cooling_count > 0:
            avg_cooling /= cooling_count
        if heating_count > 0:
            avg_heating /= heating_count
        if r_squared_count > 0:
            avg_r_squared /= r_squared_count

        avg_base = 0.0
        if hourly_results:
            avg_base = sum(result.base_load_kw for result in hourly_results.values())
            avg_base /= len(hourly_results)

        return {
            "weather_entity_id": self._data.weather_entity_id,
            "cooling_threshold": self._data.cooling_threshold,
            "heating_threshold": self._data.heating_threshold,
            "total_samples": total_samples,
            "hours_with_data": len(hourly_results),
            "average_base_load_kw": round(avg_base, 3),
            "average_cooling_slope": round(avg_cooling, 4),
            "average_heating_slope": round(avg_heating, 4),
            "average_r_squared": round(avg_r_squared, 4),
            "cooling_hours": cooling_count,
            "heating_hours": heating_count,
            "hourly_regression": {
                hour: result.to_dict() for hour, result in hourly_results.items()
            },
        }

    def get_coefficients_for_hour(self, hour: int) -> HourlyRegressionResult | None:
        """Get coefficients for a specific hour.

        Args:
            hour: Hour of day (0-23)

        Returns:
            HourlyRegressionResult or None if not available

        """
        if not (0 <= hour <= 23):
            return None
        aggregated = self._aggregate_hourly_stats(hour)
        if aggregated is None:
            return None
        return self._build_hourly_result(aggregated)

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
        self._anomaly_detector.record_daily_temperature(temperature, date_key)

    def detect_weather_anomaly(self, current_temp: float) -> WeatherAnomalyResult:
        """Detect if current temperature is anomalous (Issue #681).

        Anomalous means ±WEATHER_ANOMALY_SIGMA_THRESHOLD standard deviations
        from the WEATHER_TEMPERATURE_HISTORY_DAYS moving average.

        Args:
            current_temp: Current temperature in °C

        Returns:
            WeatherAnomalyResult with weight for rollback evaluation

        """
        return self._anomaly_detector.detect_weather_anomaly(current_temp)

    @staticmethod
    def _today_key(now: datetime | None = None) -> str:
        """Return today's date key in ISO format."""
        if now is None:
            now = dt_util.now()
        return now.date().isoformat()

    def _get_or_create_daily_snapshot(self, hour: int, date_key: str) -> DailySnapshot:
        """Return a daily snapshot for the given hour and date."""
        snapshots = self._data.daily_regression_stats.setdefault(hour, [])
        for snapshot in snapshots:
            if snapshot.date_key == date_key:
                return snapshot
        snapshot = DailySnapshot(date_key=date_key)
        snapshots.append(snapshot)
        return snapshot

    def _prune_daily_snapshots(self, now: datetime | None = None) -> None:
        """Prune snapshots older than the 30-day sliding window."""
        if now is None:
            now = dt_util.now()
        cutoff_date = (now.date() - timedelta(days=SLIDING_WINDOW_DAYS - 1)).isoformat()
        for hour, snapshots in list(self._data.daily_regression_stats.items()):
            kept = [
                snapshot for snapshot in snapshots if snapshot.date_key >= cutoff_date
            ]
            if kept:
                self._data.daily_regression_stats[hour] = kept
            else:
                self._data.daily_regression_stats.pop(hour, None)

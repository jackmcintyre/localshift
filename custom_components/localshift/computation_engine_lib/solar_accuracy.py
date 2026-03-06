"""Solar forecast accuracy tracking and bias correction.

Issue #378: Tracks forecast vs actual solar production to detect and correct
systematic bias in Solcast forecasts. Learns context-specific bias patterns
based on time of day, weather condition, and season.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

MAX_PERIOD_RECORDS = 1440
BIAS_HALF_LIFE_DAYS = 7.0


@dataclass
class SolarPeriodRecord:
    """Record of forecast vs actual for a 30-min period.

    Attributes:
        period_start: Start time of the 30-min period
        forecast_kwh: Solar forecast from Solcast pv_estimate
        actual_kwh: Actual solar energy produced
        weather_condition: Weather during period (sunny, cloudy, rainy, etc.)
        time_of_day: Time bucket (morning, afternoon, evening)
        season: Season (summer, autumn, winter, spring)
        bias: Calculated bias (forecast - actual) / forecast, or 0 if no forecast
    """

    period_start: datetime
    forecast_kwh: float
    actual_kwh: float
    weather_condition: str
    time_of_day: str
    season: str
    bias: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize the record to a dictionary for storage.

        Returns:
            Dictionary with all record fields serialized.
        """
        return {
            "period_start": self.period_start.isoformat(),
            "forecast_kwh": self.forecast_kwh,
            "actual_kwh": self.actual_kwh,
            "weather_condition": self.weather_condition,
            "time_of_day": self.time_of_day,
            "season": self.season,
            "bias": self.bias,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SolarPeriodRecord:
        """Deserialize a record from a dictionary.

        Args:
            data: Dictionary with serialized record data.

        Returns:
            SolarPeriodRecord instance.
        """
        period_start = datetime.fromisoformat(data["period_start"])
        return cls(
            period_start=period_start,
            forecast_kwh=data.get("forecast_kwh", 0.0),
            actual_kwh=data.get("actual_kwh", 0.0),
            weather_condition=data.get("weather_condition", "unknown"),
            time_of_day=data.get("time_of_day", "unknown"),
            season=data.get("season", "unknown"),
            bias=data.get("bias", 0.0),
        )


@dataclass
class SolarBiasMetrics:
    """Aggregated solar forecast bias metrics.

    Attributes:
        overall_bias: Mean bias across all samples
        bias_by_time: Bias grouped by time of day
        bias_by_weather: Bias grouped by weather condition
        bias_by_season: Bias grouped by season
        sample_count: Total number of samples
        mape: Mean Absolute Percentage Error
        accuracy: Overall accuracy percentage (100 - MAPE)
    """

    overall_bias: float = 0.0
    bias_by_time: dict[str, float] = field(default_factory=dict)
    bias_by_weather: dict[str, float] = field(default_factory=dict)
    bias_by_season: dict[str, float] = field(default_factory=dict)
    sample_count: int = 0
    mape: float = 0.0
    accuracy: float = 100.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize the metrics to a dictionary for storage.

        Returns:
            Dictionary with all metric fields.
        """
        return {
            "overall_bias": self.overall_bias,
            "bias_by_time": self.bias_by_time,
            "bias_by_weather": self.bias_by_weather,
            "bias_by_season": self.bias_by_season,
            "sample_count": self.sample_count,
            "mape": self.mape,
            "accuracy": self.accuracy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SolarBiasMetrics:
        """Deserialize metrics from a dictionary.

        Args:
            data: Dictionary with serialized metrics.

        Returns:
            SolarBiasMetrics instance.
        """
        return cls(
            overall_bias=data.get("overall_bias", 0.0),
            bias_by_time=data.get("bias_by_time", {}),
            bias_by_weather=data.get("bias_by_weather", {}),
            bias_by_season=data.get("bias_by_season", {}),
            sample_count=data.get("sample_count", 0),
            mape=data.get("mape", 0.0),
            accuracy=data.get("accuracy", 100.0),
        )


class SolarAccuracyTracker:
    """Tracks solar forecast accuracy and provides bias metrics.

    Flow:
    1. record_forecast(): Store forecast when period starts (called from slot_builder)
    2. backfill_actual(): Compare when period ends, calculate bias (called from coordinator)

    Uses time-weighted average with exponential decay to favor recent data.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the solar accuracy tracker.

        Args:
            hass: Home Assistant instance.
            entry_id: Config entry ID for storage key.
        """
        self._hass = hass
        self._store = Store(
            hass, version=1, key=f"localshift.solar_accuracy.{entry_id}"
        )
        self._pending_forecasts: dict[str, SolarPeriodRecord] = {}
        self._period_records: deque[SolarPeriodRecord] = deque(
            maxlen=MAX_PERIOD_RECORDS
        )
        self._metrics = SolarBiasMetrics()
        self._save_pending = False

    @property
    def metrics(self) -> SolarBiasMetrics:
        return self._metrics

    async def async_load(self) -> None:
        """Load stored data from HA storage."""
        try:
            data = await self._store.async_load()
            if data is None:
                _LOGGER.debug("No stored solar accuracy data found")
                return

            records_data = data.get("period_records", [])
            for record_data in records_data:
                try:
                    record = SolarPeriodRecord.from_dict(record_data)
                    self._period_records.append(record)
                except Exception as err:
                    _LOGGER.warning("Failed to load solar period record: %s", err)

            if "metrics" in data:
                self._metrics = SolarBiasMetrics.from_dict(data["metrics"])

            _LOGGER.info(
                "Loaded solar accuracy data: %d records, bias=%.2f, samples=%d",
                len(self._period_records),
                self._metrics.overall_bias,
                self._metrics.sample_count,
            )
        except Exception as err:
            _LOGGER.warning("Failed to load solar accuracy data: %s", err)

    async def async_save(self) -> None:
        """Save data to HA storage."""
        if not self._save_pending:
            return

        try:
            data = {
                "period_records": [r.to_dict() for r in self._period_records],
                "metrics": self._metrics.to_dict(),
            }
            await self._store.async_save(data)
            self._save_pending = False
            _LOGGER.debug("Saved solar accuracy data")
        except Exception as err:
            _LOGGER.warning("Failed to save solar accuracy data: %s", err)

    def record_forecast(
        self,
        period_start: datetime,
        forecast_kwh: float,
        weather_condition: str,
    ) -> None:
        """Record a solar forecast for a 30-min period.

        Called when building slots - stores forecast for later comparison.
        Uses the key as ISO format string for pending lookup.
        """
        key = period_start.isoformat()
        time_of_day = self._get_time_of_day(period_start)
        season = self._get_season(period_start)

        record = SolarPeriodRecord(
            period_start=period_start,
            forecast_kwh=forecast_kwh,
            actual_kwh=0.0,
            weather_condition=self._normalize_weather(weather_condition),
            time_of_day=time_of_day,
            season=season,
        )
        self._pending_forecasts[key] = record
        _LOGGER.debug(
            "Recorded solar forecast for %s: %.3f kWh, weather=%s, time=%s, season=%s",
            period_start.strftime("%H:%M"),
            forecast_kwh,
            weather_condition,
            time_of_day,
            season,
        )

    def backfill_actual(
        self,
        period_start: datetime,
        actual_kwh: float,
    ) -> None:
        """Backfill actual solar energy for a completed period.

        Called by coordinator after a period ends. Calculates bias and stores
        the completed record.
        """
        key = period_start.isoformat()
        pending = self._pending_forecasts.pop(key, None)

        if pending is None:
            _LOGGER.debug(
                "No pending forecast for period %s, skipping backfill",
                period_start.strftime("%H:%M"),
            )
            return

        pending.actual_kwh = actual_kwh
        pending.__post_init__()

        self._period_records.append(pending)
        self._save_pending = True

        self._recompute_metrics()

        _LOGGER.info(
            "Solar period %s: forecast=%.3f kWh, actual=%.3f kWh, bias=%.1f%%",
            period_start.strftime("%H:%M"),
            pending.forecast_kwh,
            actual_kwh,
            pending.bias * 100,
        )

    def _recompute_metrics(self) -> None:
        """Recompute aggregated metrics from all period records."""
        if not self._period_records:
            return

        records = list(self._period_records)

        self._metrics.sample_count = len(records)
        self._metrics.overall_bias = sum(r.bias for r in records) / len(records)

        mape_sum = 0.0
        mape_count = 0
        for r in records:
            if r.actual_kwh > 0.01:
                mape_sum += abs(r.forecast_kwh - r.actual_kwh) / r.actual_kwh * 100
                mape_count += 1
        self._metrics.mape = mape_sum / mape_count if mape_count > 0 else 0.0
        self._metrics.accuracy = max(0.0, 100.0 - self._metrics.mape)

        by_time: dict[str, list[float]] = defaultdict(list)
        by_weather: dict[str, list[float]] = defaultdict(list)
        by_season: dict[str, list[float]] = defaultdict(list)
        self._count_by_time: dict[str, int] = defaultdict(int)

        for r in records:
            by_time[r.time_of_day].append(r.bias)
            by_weather[r.weather_condition].append(r.bias)
            by_season[r.season].append(r.bias)
            self._count_by_time[r.time_of_day] += 1

        self._metrics.bias_by_time = {k: sum(v) / len(v) for k, v in by_time.items()}
        self._metrics.bias_by_weather = {
            k: sum(v) / len(v) for k, v in by_weather.items()
        }
        self._metrics.bias_by_season = {
            k: sum(v) / len(v) for k, v in by_season.items()
        }

    def _compute_context_bias(
        self,
        time_of_day: str,
        weather: str,
        season: str | None,
    ) -> tuple[float, int] | None:
        """Compute weighted average bias for specific context."""
        now = dt_util.now()
        biases: list[tuple[float, float]] = []

        for record in self._period_records:
            if record.time_of_day != time_of_day:
                continue
            if record.weather_condition != weather:
                continue
            if season and record.season != season:
                continue

            age_days = (now - record.period_start).total_seconds() / 86400.0
            weight = math.exp(-age_days / BIAS_HALF_LIFE_DAYS)
            biases.append((record.bias, weight))

        if not biases:
            return None

        total_weight = sum(w for _, w in biases)
        weighted_bias = sum(b * w for b, w in biases) / total_weight
        return (weighted_bias, len(biases))

    @staticmethod
    def _get_time_of_day(dt: datetime) -> str:
        """Classify time of day for bias grouping."""
        hour = dt.hour
        if 6 <= hour < 12:
            return "morning"
        elif 12 <= hour < 18:
            return "afternoon"
        elif 18 <= hour < 21:
            return "evening"
        else:
            return "night"

    @staticmethod
    def _get_season(dt: datetime) -> str:
        """Get season from date (Southern hemisphere convention)."""
        month = dt.month
        if month in (12, 1, 2):
            return "summer"
        elif month in (3, 4, 5):
            return "autumn"
        elif month in (6, 7, 8):
            return "winter"
        else:
            return "spring"

    @staticmethod
    def _normalize_weather(condition: str | None) -> str:
        """Normalize weather condition to standard groups."""
        if not condition:
            return "unknown"

        condition_lower = condition.lower()

        if "sunny" in condition_lower or "clear" in condition_lower:
            return "sunny"
        elif "cloudy" in condition_lower or "overcast" in condition_lower:
            return "cloudy"
        elif "rain" in condition_lower or "shower" in condition_lower:
            return "rainy"
        elif "snow" in condition_lower or "hail" in condition_lower:
            return "snow"
        elif "fog" in condition_lower or "mist" in condition_lower:
            return "foggy"
        else:
            return "unknown"

    def get_bias_correction(
        self,
        time_of_day: str,
        weather: str,
        season: str | None = None,
    ) -> float:
        """Get bias correction factor for given context.

        Returns a multiplier (0.0 to 2.0) to apply to forecasts.
        A positive bias means forecasts overestimate, so we reduce solar_kwh.
        A negative bias means forecasts underestimate, so we increase solar_kwh.

        Args:
            time_of_day: Time bucket ('morning', 'afternoon', 'evening', 'night')
            weather: Weather condition ('sunny', 'cloudy', 'rainy', etc.)
            season: Season ('summer', 'autumn', 'winter', 'spring'), optional for coarser granularity

        Returns:
            Bias correction factor. Returns 1.0 if no historical data available.
            Values < 1.0 reduce forecast (overestimate), > 1.0 increase (underestimate).
        """
        result = self._compute_context_bias(time_of_day, weather, season)
        if result is None:
            return 1.0

        weighted_bias, sample_count = result
        _LOGGER.debug(
            "Bias correction for %s/%s/%s: bias=%.2f%%, samples=%d",
            time_of_day,
            weather,
            season or "any",
            weighted_bias * 100,
            sample_count,
        )
        return 1.0 - weighted_bias

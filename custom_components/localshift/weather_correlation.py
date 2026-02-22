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

from .const import (
    CONF_COOLING_THRESHOLD,
    CONF_HEATING_THRESHOLD,
    CONF_WEATHER_ENTITY,
    DEFAULT_COOLING_THRESHOLD,
    DEFAULT_HEATING_THRESHOLD,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Storage version for migrations
STORAGE_VERSION = 1
STORAGE_KEY = "weather_correlation"

# Confidence thresholds based on sample count
CONFIDENCE_LOW_THRESHOLD = 7  # Less than 7 samples = low confidence
CONFIDENCE_MEDIUM_THRESHOLD = 30  # 7-30 samples = medium, 30+ = high

# Forecast cache settings
FORECAST_CACHE_TTL = timedelta(minutes=30)


@dataclass
class HourlyTemperatureCoefficients:
    """Temperature sensitivity coefficients for a single hour.

    Attributes:
        base_load_kw: Minimum load at mild temperatures (18-24°C band)
        cooling_coefficient: Additional kW per degree above cooling threshold
        heating_coefficient: Additional kW per degree below heating threshold
        sample_count: Number of data points used for learning
        last_updated: When coefficients were last recalculated
        confidence: low/medium/high based on sample count
    """

    base_load_kw: float = 0.0
    cooling_coefficient: float = 0.0  # kW per °C above cooling threshold
    heating_coefficient: float = 0.0  # kW per °C below heating threshold
    sample_count: int = 0
    last_updated: str = ""  # ISO format datetime
    confidence: str = "low"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "base_load_kw": self.base_load_kw,
            "cooling_coefficient": self.cooling_coefficient,
            "heating_coefficient": self.heating_coefficient,
            "sample_count": self.sample_count,
            "last_updated": self.last_updated,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HourlyTemperatureCoefficients:
        """Create from dictionary."""
        return cls(
            base_load_kw=data.get("base_load_kw", 0.0),
            cooling_coefficient=data.get("cooling_coefficient", 0.0),
            heating_coefficient=data.get("heating_coefficient", 0.0),
            sample_count=data.get("sample_count", 0),
            last_updated=data.get("last_updated", ""),
            confidence=data.get("confidence", "low"),
        )


@dataclass
class WeatherCorrelationData:
    """Complete weather correlation data structure for storage.

    Attributes:
        version: Schema version for migrations
        weather_entity_id: Configured weather entity
        cooling_threshold: Temperature above which cooling load increases
        heating_threshold: Temperature below which heating load increases
        hourly_coefficients: Dict mapping hour (0-23) to coefficients
        learning_stats: Aggregated statistics for diagnostics
    """

    version: int = 1
    weather_entity_id: str = ""
    cooling_threshold: float = 24.0  # °C
    heating_threshold: float = 18.0  # °C
    hourly_coefficients: dict[int, HourlyTemperatureCoefficients] = field(
        default_factory=dict
    )
    learning_stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "version": self.version,
            "weather_entity_id": self.weather_entity_id,
            "cooling_threshold": self.cooling_threshold,
            "heating_threshold": self.heating_threshold,
            "hourly_coefficients": {
                str(hour): coef.to_dict()
                for hour, coef in self.hourly_coefficients.items()
            },
            "learning_stats": self.learning_stats,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WeatherCorrelationData:
        """Create from dictionary."""
        hourly_coefficients = {}
        for hour_str, coef_data in data.get("hourly_coefficients", {}).items():
            hour = int(hour_str)
            hourly_coefficients[hour] = HourlyTemperatureCoefficients.from_dict(
                coef_data
            )

        return cls(
            version=data.get("version", 1),
            weather_entity_id=data.get("weather_entity_id", ""),
            cooling_threshold=data.get("cooling_threshold", 24.0),
            heating_threshold=data.get("heating_threshold", 18.0),
            hourly_coefficients=hourly_coefficients,
            learning_stats=data.get("learning_stats", {}),
        )


@dataclass
class TemperatureForecast:
    """Temperature forecast for a time slot.

    Attributes:
        slot_time: The datetime this forecast applies to
        temperature: Forecasted temperature in °C
        condition: Weather condition (sunny, cloudy, etc.)
    """

    slot_time: datetime
    temperature: float | None = None
    condition: str = "unknown"


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

        # Cached temperature forecast (updated periodically)
        self._cached_forecasts: list[TemperatureForecast] = []
        self._forecast_cache_time: datetime | None = None

    async def async_initialize(self) -> None:
        """Load persisted data from storage."""
        if self._initialized:
            return

        stored_data = await self._store.async_load()
        if stored_data is not None:
            self._data = WeatherCorrelationData.from_dict(stored_data)
            _LOGGER.info(
                "Loaded weather correlation data: %d hourly coefficients, %d total samples",
                len(self._data.hourly_coefficients),
                sum(c.sample_count for c in self._data.hourly_coefficients.values()),
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

        self._initialized = True

    async def async_save(self) -> None:
        """Persist coefficients to storage."""
        await self._store.async_save(self._data.to_dict())
        _LOGGER.debug("Saved weather correlation data to storage")

    def get_temperature_forecast(self) -> list[TemperatureForecast]:
        """Fetch forecasted temperatures from weather entity.

        Returns:
            List of TemperatureForecast objects for upcoming hours.
        """
        weather_entity = self._data.weather_entity_id
        if not weather_entity:
            _LOGGER.debug("No weather entity configured")
            return []

        state = self.hass.states.get(weather_entity)
        if state is None:
            _LOGGER.warning("Weather entity %s not found", weather_entity)
            return []

        forecasts: list[TemperatureForecast] = []

        # Get forecast from weather entity attributes
        # Most weather integrations provide forecast in attributes
        forecast_data = state.attributes.get("forecast", [])
        now = dt_util.now()

        for forecast_entry in forecast_data:
            # Parse forecast datetime
            forecast_time_str = forecast_entry.get("datetime")
            if not forecast_time_str:
                continue

            try:
                forecast_time = dt_util.parse_datetime(forecast_time_str)
                if forecast_time is None:
                    continue
            except (ValueError, TypeError):
                continue

            # Only include forecasts for the next 24 hours
            hours_ahead = (forecast_time - now).total_seconds() / 3600
            if hours_ahead < 0 or hours_ahead > 24:
                continue

            temperature = forecast_entry.get("temperature")
            condition = forecast_entry.get("condition", "unknown")

            forecasts.append(
                TemperatureForecast(
                    slot_time=forecast_time,
                    temperature=temperature,
                    condition=condition,
                )
            )

        _LOGGER.debug(
            "Got %d temperature forecasts from %s (legacy attribute)",
            len(forecasts),
            weather_entity,
        )

        return forecasts

    def _refresh_weather_entity_from_config(self) -> str:
        """Get the current weather entity from config entry.

        Always reads fresh from config to pick up user changes without
        requiring a restart. Checks options first (Configure UI), then data.

        Returns:
            Current weather entity ID from config, or empty string.
        """
        return self.entry.options.get(CONF_WEATHER_ENTITY, "") or self.entry.data.get(
            CONF_WEATHER_ENTITY, ""
        )

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
        # Always get fresh entity ID from config to pick up user changes
        weather_entity = self._refresh_weather_entity_from_config()
        if not weather_entity:
            _LOGGER.debug("No weather entity configured")
            return []

        # Update cached entity ID if changed
        if weather_entity != self._data.weather_entity_id:
            _LOGGER.info(
                "Weather entity changed from %s to %s, clearing forecast cache",
                self._data.weather_entity_id,
                weather_entity,
            )
            self._data.weather_entity_id = weather_entity
            # Clear cache to force fresh fetch with new entity
            self._cached_forecasts = []
            self._forecast_cache_time = None

        now = dt_util.now()

        # Return cached forecasts if still valid
        if (
            not force_refresh
            and self._forecast_cache_time is not None
            and self._cached_forecasts
            and (now - self._forecast_cache_time) < FORECAST_CACHE_TTL
        ):
            _LOGGER.debug(
                "Returning %d cached temperature forecasts (age: %s)",
                len(self._cached_forecasts),
                now - self._forecast_cache_time,
            )
            return self._cached_forecasts

        forecasts: list[TemperatureForecast] = []

        # Try modern service call first (HA 2024.3+)
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": weather_entity, "type": "hourly"},
                blocking=True,
                return_response=True,
            )

            if response and weather_entity in response:
                forecast_data = response[weather_entity]

                # Handle different response formats
                if isinstance(forecast_data, list):
                    for forecast_entry in forecast_data:
                        # Skip if not a dict (handles 'str' object error)
                        if not isinstance(forecast_entry, dict):
                            continue

                        # Parse forecast datetime - HA uses "datetime" key
                        forecast_time_str = forecast_entry.get("datetime")
                        if not forecast_time_str:
                            continue

                        try:
                            forecast_time = dt_util.parse_datetime(forecast_time_str)
                            if forecast_time is None:
                                continue
                        except (ValueError, TypeError):
                            continue

                        # Only include forecasts for the next 24 hours
                        hours_ahead = (forecast_time - now).total_seconds() / 3600
                        if hours_ahead < 0 or hours_ahead > 24:
                            continue

                        temperature = forecast_entry.get("temperature")
                        condition = forecast_entry.get("condition", "unknown")

                        forecasts.append(
                            TemperatureForecast(
                                slot_time=forecast_time,
                                temperature=temperature,
                                condition=condition,
                            )
                        )

                    _LOGGER.info(
                        "Fetched %d temperature forecasts via weather.get_forecasts service",
                        len(forecasts),
                    )
                else:
                    _LOGGER.debug(
                        "Unexpected forecast_data format: %s",
                        type(forecast_data).__name__,
                    )
            else:
                _LOGGER.debug(
                    "No response from weather.get_forecasts for entity %s, "
                    "trying legacy attribute",
                    weather_entity,
                )

        except Exception as e:
            _LOGGER.debug(
                "Failed to fetch forecasts via weather.get_forecasts service: %s, "
                "falling back to legacy attribute",
                e,
            )

        # If no forecasts from modern service, try legacy attribute
        if not forecasts:
            forecasts = self.get_temperature_forecast()

        # Update cache
        self._cached_forecasts = forecasts
        self._forecast_cache_time = now

        return forecasts

    def get_current_temperature(self) -> float | None:
        """Get current temperature from weather entity.

        Returns:
            Current temperature in °C, or None if unavailable.
        """
        weather_entity = self._data.weather_entity_id
        if not weather_entity:
            return None

        state = self.hass.states.get(weather_entity)
        if state is None:
            return None

        try:
            return float(state.attributes.get("temperature", 0))
        except (ValueError, TypeError):
            return None

    def learn_from_sample(
        self, hour: int, temperature: float, actual_load_kw: float
    ) -> None:
        """Update coefficients based on observed temperature/load pair.

        This implements an incremental learning algorithm that updates
        coefficients using a simple moving average approach.

        Args:
            hour: Hour of day (0-23)
            temperature: Observed temperature in °C
            actual_load_kw: Observed load in kW
        """
        if not (0 <= hour <= 23):
            _LOGGER.warning("Invalid hour %d, must be 0-23", hour)
            return

        # Get or create coefficients for this hour
        if hour not in self._data.hourly_coefficients:
            self._data.hourly_coefficients[hour] = HourlyTemperatureCoefficients()

        coef = self._data.hourly_coefficients[hour]

        # Calculate the temperature delta based on thresholds
        cooling_threshold = self._data.cooling_threshold
        heating_threshold = self._data.heating_threshold

        # Determine which coefficient to update
        if temperature > cooling_threshold:
            # Cooling mode: temperature above cooling threshold
            temp_delta = temperature - cooling_threshold
            if temp_delta > 0:
                # Calculate implied cooling coefficient
                # actual_load = base_load + cooling_coef * temp_delta
                # We estimate base_load from previous samples
                if coef.base_load_kw > 0:
                    implied_cooling_coef = (
                        actual_load_kw - coef.base_load_kw
                    ) / temp_delta
                    # Update using moving average
                    if coef.cooling_coefficient == 0:
                        coef.cooling_coefficient = implied_cooling_coef
                    else:
                        # Weighted update: new = 0.1 * new + 0.9 * old
                        coef.cooling_coefficient = (
                            0.1 * implied_cooling_coef + 0.9 * coef.cooling_coefficient
                        )
                else:
                    # First sample - estimate base load
                    coef.base_load_kw = actual_load_kw * 0.8  # Rough estimate

        elif temperature < heating_threshold:
            # Heating mode: temperature below heating threshold
            temp_delta = heating_threshold - temperature
            if temp_delta > 0:
                if coef.base_load_kw > 0:
                    implied_heating_coef = (
                        actual_load_kw - coef.base_load_kw
                    ) / temp_delta
                    if coef.heating_coefficient == 0:
                        coef.heating_coefficient = implied_heating_coef
                    else:
                        coef.heating_coefficient = (
                            0.1 * implied_heating_coef + 0.9 * coef.heating_coefficient
                        )
                else:
                    coef.base_load_kw = actual_load_kw * 0.8

        else:
            # Mild temperature - update base load estimate
            if coef.base_load_kw == 0:
                coef.base_load_kw = actual_load_kw
            else:
                # Moving average for base load
                coef.base_load_kw = 0.1 * actual_load_kw + 0.9 * coef.base_load_kw

        # Update sample count and confidence
        coef.sample_count += 1
        coef.last_updated = dt_util.now().isoformat()
        coef.confidence = self._calculate_confidence(coef.sample_count)

        _LOGGER.debug(
            "Learned sample for hour %d: temp=%.1f°C, load=%.2fkW, "
            "base=%.2f, cooling=%.3f, heating=%.3f, samples=%d, confidence=%s",
            hour,
            temperature,
            actual_load_kw,
            coef.base_load_kw,
            coef.cooling_coefficient,
            coef.heating_coefficient,
            coef.sample_count,
            coef.confidence,
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

        if hour not in self._data.hourly_coefficients:
            # No learned data for this hour
            return base_load_kw, "no_coefficients"

        coef = self._data.hourly_coefficients[hour]

        # Only apply adjustment if we have sufficient confidence
        if coef.confidence == "low":
            return base_load_kw, "low_confidence"

        cooling_threshold = self._data.cooling_threshold
        heating_threshold = self._data.heating_threshold

        # Start with the learned base load or fall back to provided base
        predicted_load = coef.base_load_kw if coef.base_load_kw > 0 else base_load_kw

        adjustment = 0.0
        adjustment_type = "none"

        if temperature > cooling_threshold and coef.cooling_coefficient > 0:
            # Apply cooling adjustment
            temp_delta = temperature - cooling_threshold
            adjustment = coef.cooling_coefficient * temp_delta
            adjustment_type = "cooling"

        elif temperature < heating_threshold and coef.heating_coefficient > 0:
            # Apply heating adjustment
            temp_delta = heating_threshold - temperature
            adjustment = coef.heating_coefficient * temp_delta
            adjustment_type = "heating"

        predicted_load += adjustment

        _LOGGER.debug(
            "Predicted load for hour %d: temp=%.1f°C, base=%.2fkW, "
            "adjustment=%.2fkW (%s), total=%.2fkW",
            hour,
            temperature,
            base_load_kw,
            adjustment,
            adjustment_type,
            predicted_load,
        )

        return round(predicted_load, 3), f"weather_{adjustment_type}"

    def _calculate_confidence(self, sample_count: int) -> str:
        """Calculate confidence level based on sample count.

        Args:
            sample_count: Number of samples used for learning

        Returns:
            Confidence level: "low", "medium", or "high"
        """
        if sample_count < CONFIDENCE_LOW_THRESHOLD:
            return "low"
        elif sample_count < CONFIDENCE_MEDIUM_THRESHOLD:
            return "medium"
        else:
            return "high"

    def get_diagnostics(self) -> dict[str, Any]:
        """Return learning statistics for diagnostics.

        Returns:
            Dictionary with learning stats and coefficient summaries.
        """
        total_samples = sum(
            c.sample_count for c in self._data.hourly_coefficients.values()
        )

        # Calculate average coefficients
        avg_cooling = 0.0
        avg_heating = 0.0
        avg_base = 0.0
        cooling_count = 0
        heating_count = 0

        for coef in self._data.hourly_coefficients.values():
            if coef.cooling_coefficient > 0:
                avg_cooling += coef.cooling_coefficient
                cooling_count += 1
            if coef.heating_coefficient > 0:
                avg_heating += coef.heating_coefficient
                heating_count += 1
            avg_base += coef.base_load_kw

        num_hours = len(self._data.hourly_coefficients)
        if num_hours > 0:
            avg_base /= num_hours
        if cooling_count > 0:
            avg_cooling /= cooling_count
        if heating_count > 0:
            avg_heating /= heating_count

        return {
            "weather_entity_id": self._data.weather_entity_id,
            "cooling_threshold": self._data.cooling_threshold,
            "heating_threshold": self._data.heating_threshold,
            "total_samples": total_samples,
            "hours_with_data": num_hours,
            "average_base_load_kw": round(avg_base, 3),
            "average_cooling_coefficient": round(avg_cooling, 4),
            "average_heating_coefficient": round(avg_heating, 4),
            "cooling_hours": cooling_count,
            "heating_hours": heating_count,
            "hourly_coefficients": {
                hour: coef.to_dict()
                for hour, coef in sorted(self._data.hourly_coefficients.items())
            },
        }

    def get_coefficients_for_hour(
        self, hour: int
    ) -> HourlyTemperatureCoefficients | None:
        """Get coefficients for a specific hour.

        Args:
            hour: Hour of day (0-23)

        Returns:
            HourlyTemperatureCoefficients or None if not available
        """
        return self._data.hourly_coefficients.get(hour)

    def update_thresholds(
        self, cooling_threshold: float, heating_threshold: float
    ) -> None:
        """Update temperature thresholds.

        Args:
            cooling_threshold: New cooling threshold in °C
            heating_threshold: New heating threshold in °C
        """
        self._data.cooling_threshold = cooling_threshold
        self._data.heating_threshold = heating_threshold
        _LOGGER.info(
            "Updated temperature thresholds: cooling=%.1f°C, heating=%.1f°C",
            cooling_threshold,
            heating_threshold,
        )

    def update_weather_entity(self, entity_id: str) -> None:
        """Update the weather entity ID.

        Args:
            entity_id: New weather entity ID
        """
        self._data.weather_entity_id = entity_id
        _LOGGER.info("Updated weather entity to %s", entity_id)

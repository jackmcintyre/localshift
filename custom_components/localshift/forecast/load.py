"""Load forecaster for household consumption prediction."""

from __future__ import annotations

import logging
from datetime import time
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry

from ..const import (
    DEFAULT_CURRENT_HOUR_INSTANTANEOUS_WEIGHT,
    DEFAULT_CURRENT_HOUR_RECENT_WEIGHT,
    DEFAULT_LOAD_DECAY_FACTOR,
    DEFAULT_LOAD_INITIAL_WEIGHT,
    LOAD_FORECAST_CEILING_FACTOR,
)

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .corrections import ForecastCorrectionProvider


class LoadForecaster:
    """Forecasts household load consumption with weather integration."""

    def __init__(
        self,
        entry: ConfigEntry,
        weather_correlation: Any | None = None,
    ) -> None:
        """Initialize load forecaster.

        Args:
            entry: Config entry for accessing options
            weather_correlation: Optional WeatherCorrelation instance for temperature-based adjustments

        """
        self.entry = entry
        self._weather_correlation = weather_correlation
        self._adaptive_params = None  # Issue #170 Phase 2: Adaptive parameters
        self._forecast_corrections: ForecastCorrectionProvider | None = None
        self._weather_adjustment_applied = False  # Track if weather adjustment was used

    def set_weather_correlation(self, weather_correlation: Any | None) -> None:
        """Set or clear WeatherCorrelation dependency at runtime."""
        self._weather_correlation = weather_correlation

    def set_adaptive_params(self, adaptive_params: Any | None) -> None:
        """Set adaptive parameters from the learning system (Issue #170 Phase 2).

        Args:
            adaptive_params: AdaptiveParameters instance with tuned values,
                           or None to use defaults.

        """
        self._adaptive_params = adaptive_params

    def set_forecast_corrections(
        self, provider: ForecastCorrectionProvider | None
    ) -> None:
        self._forecast_corrections = provider

    def get_weather_adjustment_applied(self) -> bool:
        """Return whether weather adjustment was applied in last forecast."""
        return self._weather_adjustment_applied

    def reset_weather_adjustment_applied(self) -> None:
        """Reset weather adjustment flag before new forecast computation."""
        self._weather_adjustment_applied = False

    def parse_time_option(self, key: str, default: str) -> time:
        """Parse a time string option (HH:MM:SS) into a time object."""
        time_str = str(self.entry.options.get(key, default))
        parts = time_str.split(":")
        try:
            return time(
                int(parts[0]),
                int(parts[1]) if len(parts) > 1 else 0,
                int(parts[2]) if len(parts) > 2 else 0,
            )
        except (ValueError, IndexError):
            d_parts = default.split(":")
            return time(int(d_parts[0]), int(d_parts[1]), int(d_parts[2]))

    def estimate_hourly_consumption_kw(
        self,
        hourly_avg_kw: dict[int, float],
        slot_hour: int,
        current_hour: int | None,
        current_load_kw: float,
        recent_load_kw: float = 0.0,
        temperature: float | None = None,
        hours_ahead: float | None = None,
        day_of_week: int | None = None,
        season: str | None = None,
    ) -> tuple[float, str]:
        """Estimate hourly household consumption with exponential decay weighting.

        Issue #381: Uses exponential decay weighting instead of fixed blend.
        - Current hour (distance=0): uses live load directly
        - Each hour away: weight decays by 20% (factor=0.8)
        - Beyond 3 hours: use historical profile only

        This gives more weight to recent data for near-term predictions while
        using historical patterns for distant hours.

        When temperature is provided and weather correlation is available with
        sufficient confidence, applies temperature-based adjustments for heating/cooling.

        Args:
            hourly_avg_kw: Historical hourly averages (hour -> kW)
            slot_hour: The hour of the slot being estimated (for historical lookup)
            current_hour: Current hour of day (for legacy distance calc), or None to skip blending
            current_load_kw: Instantaneous live load in kW
            recent_load_kw: 1-hour rolling average load in kW
            temperature: Optional temperature for weather correlation
            hours_ahead: Actual hours ahead for decay weighting (fixes midnight wrap bug).
                        When provided, overrides clock-hour distance calculation.
                        Pass i/4.0 where i is the slot index (15-min slots).

        Returns tuple of (kW, source_tag).

        """
        historical_kw = self._get_historical(hourly_avg_kw, slot_hour)
        base_load_kw, base_source = self._calculate_base_load(
            historical_kw,
            slot_hour,
            current_hour,
            current_load_kw,
            recent_load_kw,
            hours_ahead,
            hourly_avg_kw,
        )
        adjusted_load_kw, adjusted_source = self._apply_weather_correlation(
            base_load_kw, base_source, slot_hour, temperature
        )
        final_load_kw = self._apply_consumption_bias(adjusted_load_kw, slot_hour)
        if day_of_week is not None and season is not None:
            final_load_kw = self._apply_context_correction(
                final_load_kw,
                day_of_week,
                slot_hour,
                season,
            )
        if hourly_avg_kw:
            ceiling = max(hourly_avg_kw.values()) * LOAD_FORECAST_CEILING_FACTOR
            if ceiling > 0 and final_load_kw > ceiling:
                _LOGGER.warning(
                    "LOAD_FORECAST_CEILING (Issue #826): forecast %.3f kW for hour %d "
                    "exceeds data-driven ceiling %.3f kW; clamping",
                    final_load_kw,
                    slot_hour,
                    ceiling,
                )
                final_load_kw = ceiling
        return round(final_load_kw, 3), adjusted_source

    def _get_historical(self, hourly_avg_kw: dict[int, float], slot_hour: int) -> float:
        """Get historical load for a specific hour.

        Args:
            hourly_avg_kw: Historical hourly averages
            slot_hour: Hour to lookup

        Returns:
            Historical load in kW (0.0 if not available)

        """
        historical_raw = hourly_avg_kw.get(slot_hour) if hourly_avg_kw else None
        return float(historical_raw) if isinstance(historical_raw, int | float) else 0.0

    def _calculate_base_load(
        self,
        historical_kw: float,
        slot_hour: int,
        current_hour: int | None,
        current_load_kw: float,
        recent_load_kw: float,
        hours_ahead: float | None,
        hourly_avg_kw: dict[int, float],
    ) -> tuple[float, str]:
        """Calculate base load with exponential decay weighting.

        Args:
            historical_kw: Historical load for this hour
            slot_hour: Slot hour
            current_hour: Current hour of day
            current_load_kw: Instantaneous live load
            recent_load_kw: 1-hour rolling average
            hours_ahead: Actual hours ahead
            hourly_avg_kw: Full historical profile

        Returns:
            Tuple of (base_load_kw, source_tag)

        """
        base_load_kw = 0.0
        base_source = ""
        has_historical = historical_kw > 0

        if current_hour is not None:
            hour_distance = self._calculate_hour_distance(
                slot_hour, current_hour, hours_ahead
            )
            base_load_kw, base_source = self._apply_exponential_decay(
                hour_distance,
                current_load_kw,
                recent_load_kw,
                historical_kw,
                has_historical,
                slot_hour,
            )

        if not base_source and has_historical:
            return historical_kw, "profile_hour"

        if not base_source:
            return self._fallback_to_available_data(
                hourly_avg_kw, current_load_kw, slot_hour
            )

        return base_load_kw, base_source

    def _calculate_hour_distance(
        self, slot_hour: int, current_hour: int, hours_ahead: float | None
    ) -> int:
        """Calculate hour distance for decay weighting.

        Args:
            slot_hour: Slot hour
            current_hour: Current hour
            hours_ahead: Actual hours ahead (if provided)

        Returns:
            Hour distance

        """
        if hours_ahead is not None:
            return int(hours_ahead)
        hour_distance = abs(slot_hour - current_hour)
        return min(hour_distance, 24 - hour_distance)

    def _apply_exponential_decay(
        self,
        hour_distance: int,
        current_load_kw: float,
        recent_load_kw: float,
        historical_kw: float,
        has_historical: bool,
        slot_hour: int,
    ) -> tuple[float, str]:
        """Apply exponential decay weighting for near-term slots.

        Args:
            hour_distance: Hours away from current time
            current_load_kw: Current live load
            recent_load_kw: Recent average load
            historical_kw: Historical load
            has_historical: Whether historical data exists
            slot_hour: Slot hour for logging

        Returns:
            Tuple of (load_kw, source_tag)

        """
        if hour_distance == 0:
            if current_load_kw > 0 and recent_load_kw > 0:
                blended = (
                    DEFAULT_CURRENT_HOUR_INSTANTANEOUS_WEIGHT * current_load_kw
                    + DEFAULT_CURRENT_HOUR_RECENT_WEIGHT * recent_load_kw
                )
                return blended, "blended_live"
            elif current_load_kw > 0:
                return current_load_kw, "live_load"
            elif recent_load_kw > 0:
                return recent_load_kw, "recent_load"

        if hour_distance <= 3 and recent_load_kw > 0 and has_historical:
            live_weight = DEFAULT_LOAD_INITIAL_WEIGHT * (
                DEFAULT_LOAD_DECAY_FACTOR**hour_distance
            )
            historical_weight = 1.0 - live_weight
            base_load_kw = (live_weight * recent_load_kw) + (
                historical_weight * historical_kw
            )
            _LOGGER.debug(
                "DECAY_WEIGHT: hour=%d, distance=%d, live_weight=%.2f, recent=%.2f, hist=%.2f, result=%.2f",
                slot_hour,
                hour_distance,
                live_weight,
                recent_load_kw,
                historical_kw,
                base_load_kw,
            )
            return base_load_kw, f"decay_load_d{hour_distance}"

        return 0.0, ""

    def _fallback_to_available_data(
        self, hourly_avg_kw: dict[int, float], current_load_kw: float, slot_hour: int
    ) -> tuple[float, str]:
        """Fallback to any available data when primary methods fail.

        Args:
            hourly_avg_kw: Historical hourly averages
            current_load_kw: Current live load
            slot_hour: Slot hour for logging

        Returns:
            Tuple of (load_kw, source_tag)

        """
        base_load_kw = 0.0
        base_source = "live_load_fallback"

        if hourly_avg_kw:
            values = [v for v in hourly_avg_kw.values() if v > 0]
            if values:
                base_load_kw = sum(values) / len(values)
                return base_load_kw, base_source

        if current_load_kw > 0:
            base_load_kw = current_load_kw
            return base_load_kw, base_source

        _LOGGER.warning(
            "NO_LOAD_DATA: No historical or live load data available for slot_hour=%d. "
            "Check load sensor availability and recorder history.",
            slot_hour,
        )

        return base_load_kw, base_source

    def _apply_weather_correlation(
        self,
        base_load_kw: float,
        base_source: str,
        slot_hour: int,
        temperature: float | None,
    ) -> tuple[float, str]:
        """Apply weather correlation adjustment.

        Args:
            base_load_kw: Base load before weather adjustment
            base_source: Source tag before adjustment
            slot_hour: Hour for coefficient lookup
            temperature: Temperature for adjustment

        Returns:
            Tuple of (adjusted_load_kw, adjusted_source)

        """
        adjusted_load_kw = base_load_kw
        adjusted_source = base_source

        if (
            self._weather_correlation is None
            or temperature is None
            or base_load_kw <= 0
        ):
            return adjusted_load_kw, adjusted_source

        coef = self._weather_correlation.get_coefficients_for_hour(slot_hour)
        if coef is None or coef.confidence not in ("medium", "high"):
            return adjusted_load_kw, adjusted_source

        weather_adjusted, adjustment_source = self._weather_correlation.predict_load(
            hour=slot_hour, temperature=temperature, base_load_kw=base_load_kw
        )

        if adjustment_source not in (
            "no_coefficients",
            "low_confidence",
            "invalid_hour",
            "weather_none",
        ):
            self._weather_adjustment_applied = True
            return weather_adjusted, adjustment_source

        return adjusted_load_kw, adjusted_source

    def _apply_consumption_bias(self, load_kw: float, slot_hour: int) -> float:
        """Apply consumption forecast bias adjustment.

        Args:
            load_kw: Load before bias adjustment
            slot_hour: Hour for logging

        Returns:
            Adjusted load

        """
        if self._adaptive_params is None:
            return load_kw

        consumption_bias = self._adaptive_params.get("consumption_forecast_bias", 0.0)
        if consumption_bias == 0.0:
            return load_kw

        adjusted = max(0.0, load_kw + consumption_bias)
        _LOGGER.debug(
            "CONSUMPTION_BIAS: hour=%d, base=%.2f kW, bias=%.2f kW, final=%.2f kW",
            slot_hour,
            load_kw,
            consumption_bias,
            adjusted,
        )
        return adjusted

    def _apply_context_correction(
        self,
        load_kw: float,
        day_of_week: int,
        hour_of_day: int,
        season: str,
    ) -> float:
        if self._forecast_corrections is None:
            return load_kw

        factor = self._forecast_corrections.get_correction_factor(
            day_of_week,
            hour_of_day,
            season,
        )
        if factor == 1.0:
            return load_kw

        corrected = load_kw * factor
        _LOGGER.debug(
            "Context correction: day=%d hour=%d season=%s factor=%.3f (%.2f -> %.2f kW)",
            day_of_week,
            hour_of_day,
            season,
            factor,
            load_kw,
            corrected,
        )
        return max(0.0, corrected)

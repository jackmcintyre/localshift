"""Load forecaster for household consumption prediction."""

from __future__ import annotations

import logging
from datetime import time
from typing import Any

from homeassistant.config_entries import ConfigEntry

from ..const import (
    DEFAULT_LOAD_DECAY_FACTOR,
    DEFAULT_LOAD_INITIAL_WEIGHT,
)

_LOGGER = logging.getLogger(__name__)


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

        Returns tuple of (kW, source_tag).
        """
        historical_raw = hourly_avg_kw.get(slot_hour) if hourly_avg_kw else None
        historical_kw = (
            float(historical_raw) if isinstance(historical_raw, int | float) else 0.0
        )

        # Check if we have valid historical data
        has_historical = historical_kw > 0

        # Calculate base load using exponential decay weighting
        base_load_kw = 0.0
        base_source = ""

        # EXPONENTIAL DECAY WEIGHTING (Issue #381)
        # When current_hour is None (simulations without time context), skip blending
        if current_hour is not None:
            # Calculate distance from current hour (handles midnight wrap)
            hour_distance = abs(slot_hour - current_hour)
            hour_distance = min(hour_distance, 24 - hour_distance)

            # CURRENT SLOT: Use live load directly
            # This ensures immediate accuracy for the current time slot
            if hour_distance == 0 and current_load_kw > 0:
                base_load_kw = current_load_kw
                base_source = "live_load"
            # NEAR-TERM SLOTS: Apply exponential decay weighting
            # Weight decays by DEFAULT_LOAD_DECAY_FACTOR per hour
            elif hour_distance <= 3 and recent_load_kw > 0 and has_historical:
                # Calculate decayed weight: initial_weight * (decay_factor ^ distance)
                # e.g., distance=1: 0.8 * 0.8 = 0.64, distance=2: 0.8 * 0.64 = 0.51
                live_weight = DEFAULT_LOAD_INITIAL_WEIGHT * (
                    DEFAULT_LOAD_DECAY_FACTOR**hour_distance
                )
                historical_weight = 1.0 - live_weight

                base_load_kw = (live_weight * recent_load_kw) + (
                    historical_weight * historical_kw
                )
                base_source = f"decay_load_d{hour_distance}"
                _LOGGER.debug(
                    "DECAY_WEIGHT: hour=%d, distance=%d, live_weight=%.2f, recent=%.2f, hist=%.2f, result=%.2f",
                    slot_hour,
                    hour_distance,
                    live_weight,
                    recent_load_kw,
                    historical_kw,
                    base_load_kw,
                )

        # Fallback to historical if available (primary path for distant hours)
        if not base_source and has_historical:
            base_load_kw = historical_kw
            base_source = "profile_hour"

        # Fallback to current load
        if not base_source:
            base_load_kw = current_load_kw if current_load_kw > 0 else 0.6
            base_source = "live_load_fallback"

        # WEATHER CORRELATION: Apply temperature-based adjustment if available
        # Only apply when:
        # 1. Weather correlation is initialized
        # 2. Temperature is provided
        # 3. We have base load to adjust
        # 4. Confidence is medium or high (not low)
        adjusted_load_kw = base_load_kw
        adjusted_source = base_source

        if (
            self._weather_correlation is not None
            and temperature is not None
            and base_load_kw > 0
        ):
            # Get coefficients for this hour
            coef = self._weather_correlation.get_coefficients_for_hour(slot_hour)
            if coef is not None and coef.confidence in ("medium", "high"):
                # Apply weather-based prediction
                weather_adjusted, adjustment_source = (
                    self._weather_correlation.predict_load(
                        hour=slot_hour,
                        temperature=temperature,
                        base_load_kw=base_load_kw,
                    )
                )
                # Only use adjustment if it's not a fallback
                if adjustment_source not in (
                    "no_coefficients",
                    "low_confidence",
                    "invalid_hour",
                ):
                    adjusted_load_kw = weather_adjusted
                    adjusted_source = adjustment_source

        # Issue #170 Phase 2: Apply consumption_forecast_bias adaptive parameter
        # Positive = assume higher consumption (more conservative for grid charging)
        # Negative = assume lower consumption (more optimistic for grid charging)
        if self._adaptive_params is not None:
            consumption_bias = self._adaptive_params.get(
                "consumption_forecast_bias", 0.0
            )
            if consumption_bias != 0.0:
                adjusted_load_kw = max(0.0, adjusted_load_kw + consumption_bias)
                _LOGGER.debug(
                    "CONSUMPTION_BIAS: hour=%d, base=%.2f kW, bias=%.2f kW, final=%.2f kW",
                    slot_hour,
                    base_load_kw,
                    consumption_bias,
                    adjusted_load_kw,
                )

        return round(adjusted_load_kw, 3), adjusted_source

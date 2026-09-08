"""Load forecaster for household consumption prediction."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import time
from typing import Any

from homeassistant.config_entries import ConfigEntry

from ..const import (
    DEFAULT_CURRENT_HOUR_INSTANTANEOUS_WEIGHT,
    DEFAULT_CURRENT_HOUR_RECENT_WEIGHT,
    DEFAULT_LOAD_DECAY_FACTOR,
    DEFAULT_LOAD_INITIAL_WEIGHT,
    LOAD_FORECAST_CEILING_FACTOR,
    MIN_SAMPLES_PER_AGGREGATE_HOUR,
    MIN_SAMPLES_PER_DAY_HOUR,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class LoadProfiles:
    """Per-day-of-week + weekday/weekend aggregate load profiles (Issue #679).

    Produced by ``HistoryFetcher`` and injected into ``LoadForecaster`` via
    ``set_daily_profiles()``. ``daily_avg`` / ``daily_counts`` are keyed by
    day-of-week (Monday=0 .. Sunday=6) then hour-of-day (0-23); ``weekday_*``
    / ``weekend_*`` are the existing 2-bucket aggregate, keyed by hour-of-day
    only. All fields default to empty so a partially-populated instance
    (e.g. no aggregate data yet) degrades gracefully rather than raising.
    """

    daily_avg: dict[int, dict[int, float]] = field(default_factory=dict)
    daily_counts: dict[int, dict[int, int]] = field(default_factory=dict)
    weekday_avg: dict[int, float] = field(default_factory=dict)
    weekday_counts: dict[int, int] = field(default_factory=dict)
    weekend_avg: dict[int, float] = field(default_factory=dict)
    weekend_counts: dict[int, int] = field(default_factory=dict)


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
        # True if ≥1 slot was weather-adjusted in the most recent forecast run;
        # reset every run at pipeline.py:53, published at pipeline.py:77-79.
        self._weather_adjustment_applied = False
        # Issue #679: per-day-of-week load profiles, injected via
        # set_daily_profiles(). None means "not wired up" — every call then
        # behaves exactly as it did before this feature existed.
        self._daily_profiles: LoadProfiles | None = None
        # Diagnostics: which resolution bucket won for each qualifying hour
        # in the profile resolved during the most recent estimate call.
        self._profile_bucket_counts: dict[str, int] = {}

    def set_weather_correlation(self, weather_correlation: Any | None) -> None:
        """Set or clear WeatherCorrelation dependency at runtime."""
        self._weather_correlation = weather_correlation

    def set_daily_profiles(self, profiles: LoadProfiles | None) -> None:
        """Inject per-day-of-week load profiles (Issue #679), or clear with None.

        Without this call (or with ``None``), ``estimate_hourly_consumption_kw``
        is byte-identical to its pre-#679 behaviour: it always uses the
        caller-supplied ``hourly_avg_kw`` combined profile.
        """
        self._daily_profiles = profiles

    def get_profile_bucket_counts(self) -> dict[str, int]:
        """Diagnostics: bucket tag -> qualifying-hour count for the most
        recently resolved day-of-week profile (Issue #679).

        Recomputed on every ``estimate_hourly_consumption_kw`` call that has
        both injected profiles and a ``day_of_week``; reflects the full
        resolution for that day (every qualifying hour), not just the single
        ``slot_hour`` that call happened to ask about.
        """
        return dict(self._profile_bucket_counts)

    def _resolve_daily_profile(
        self, day_of_week: int, hourly_avg_kw: dict[int, float]
    ) -> tuple[dict[int, float], dict[int, str]]:
        """Merge day-specific, aggregate, and global profiles for one day.

        Per hour, in priority order:
        1. The day-specific bucket wins with >= MIN_SAMPLES_PER_DAY_HOUR samples.
        2. Else the weekday/weekend aggregate wins with
           >= MIN_SAMPLES_PER_AGGREGATE_HOUR samples.
        3. Else the caller-supplied combined/global average (``hourly_avg_kw``)
           for that hour, tagged "global_avg".

        Rung 3 is the fix for a review finding on this method's first cut: an
        hour that cleared neither threshold used to be omitted from the
        returned dict entirely (not backfilled from anywhere), which meant
        ``estimate_hourly_consumption_kw`` read 0.0 for that hour, treated it
        as "no historical data", and fell all the way through to the flat-mean
        fallback — e.g. a single missing recorder sample thinning Saturday
        18:00 below both thresholds silently replaced a genuine 4.0 kW evening
        peak with a ~0.5 kW flat mean. Backfilling from the global average
        instead of omitting the hour keeps the resolved profile complete over
        every hour ``hourly_avg_kw`` covers. (It also kept the #826 ceiling
        honest in the first cut, which took ``max()`` over this dict alone;
        the ceiling now takes the max across the resolved *and* combined
        profiles, so completeness no longer affects it — see
        ``estimate_hourly_consumption_kw``.)

        A non-numeric entry (corrupt injected data) is treated as not
        qualifying for that rung rather than raising.

        Returns:
            Tuple of (resolved_hourly_avg_kw, bucket_tag_by_hour). Both are
            empty only when no hour anywhere qualifies at any rung — including
            the global average, i.e. ``hourly_avg_kw`` is itself empty — which
            the caller treats identically to "profiles not injected".

        """
        profiles = self._daily_profiles
        if profiles is None:
            return {}, {}

        day_avg = profiles.daily_avg.get(day_of_week, {})
        day_counts = profiles.daily_counts.get(day_of_week, {})
        if day_of_week >= 5:
            agg_avg = profiles.weekend_avg
            agg_counts = profiles.weekend_counts
            agg_tag = "aggregate_weekend"
        else:
            agg_avg = profiles.weekday_avg
            agg_counts = profiles.weekday_counts
            agg_tag = "aggregate_weekday"

        resolved: dict[int, float] = {}
        tags: dict[int, str] = {}
        for hour in set(day_avg) | set(agg_avg) | set(hourly_avg_kw):
            day_value = day_avg.get(hour)
            if day_counts.get(hour, 0) >= MIN_SAMPLES_PER_DAY_HOUR and isinstance(
                day_value, int | float
            ):
                resolved[hour] = float(day_value)
                tags[hour] = f"day_{day_of_week}"
                continue

            agg_value = agg_avg.get(hour)
            if agg_counts.get(hour, 0) >= MIN_SAMPLES_PER_AGGREGATE_HOUR and isinstance(
                agg_value, int | float
            ):
                resolved[hour] = float(agg_value)
                tags[hour] = agg_tag
                continue

            global_value = hourly_avg_kw.get(hour)
            if isinstance(global_value, int | float):
                resolved[hour] = float(global_value)
                tags[hour] = "global_avg"

        return resolved, tags

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

        Returns tuple of (kW, source_tag). When per-day-of-week profiles are
        injected (set_daily_profiles) and day_of_week is given, source_tag may
        carry a ":<bucket>" suffix (e.g. "profile_hour:day_2",
        "decay_load_d1:aggregate_weekday") naming which resolution rung fed
        the historical/ceiling/fallback computations for this hour (Issue #679).

        """
        # Issue #679: resolve the per-day-of-week profile (if any) before doing
        # anything else. `effective_hourly_avg_kw` replaces `hourly_avg_kw`
        # everywhere below; it stays the caller-supplied combined profile
        # whenever profiles are unset, day_of_week is None, or nothing
        # qualifies anywhere for this day — i.e. byte-identical to pre-#679
        # behaviour in all of those cases.
        effective_hourly_avg_kw = hourly_avg_kw
        bucket_tag: str | None = None
        self._profile_bucket_counts = {}
        if self._daily_profiles is not None and day_of_week is not None:
            resolved, tags = self._resolve_daily_profile(day_of_week, hourly_avg_kw)
            if resolved:
                effective_hourly_avg_kw = resolved
                # "global_avg" means no day-specific or aggregate bucket won
                # this hour — keep the bare source tag (no ":global_avg"
                # suffix) so it reads identically to the pre-#679 tag and the
                # diagnostic sensor's value set doesn't grow unbounded.
                resolved_tag = tags.get(slot_hour)
                bucket_tag = resolved_tag if resolved_tag != "global_avg" else None
                self._profile_bucket_counts = dict(Counter(tags.values()))

        historical_kw = self._get_historical(effective_hourly_avg_kw, slot_hour)
        base_load_kw, base_source = self._calculate_base_load(
            historical_kw,
            slot_hour,
            current_hour,
            current_load_kw,
            recent_load_kw,
            hours_ahead,
            effective_hourly_avg_kw,
        )
        adjusted_load_kw, adjusted_source = self._apply_weather_correlation(
            base_load_kw, base_source, slot_hour, temperature
        )
        final_load_kw = adjusted_load_kw
        # Issue #826 ceiling: derived from the broadest profile available, not
        # from the resolved day bucket alone. Taking the max across the
        # resolved profile AND the caller-supplied combined profile means a
        # qualifying-but-quieter day bucket can only ever LOOSEN the ceiling,
        # never tighten it. The ceiling's job is to bound runaway forecasts
        # against the home's historical peak, and a 4-sample day-of-week mean
        # is not evidence that the peak shrank (review finding on the first
        # cut: a Sunday day profile of a flat 1.0 kW at exactly
        # MIN_SAMPLES_PER_DAY_HOUR samples collapsed a 12.0 kW ceiling to
        # 3.0 kW and clamped a genuine, measured 8.0 kW afternoon draw — a
        # 62% under-forecast of present-tense load).
        peaks = [
            value
            for profile in (effective_hourly_avg_kw, hourly_avg_kw)
            for value in profile.values()
            if isinstance(value, int | float)
        ]
        if peaks:
            ceiling = max(peaks) * LOAD_FORECAST_CEILING_FACTOR
            if ceiling > 0 and final_load_kw > ceiling:
                _LOGGER.warning(
                    "LOAD_FORECAST_CEILING (Issue #826): forecast %.3f kW for hour %d "
                    "exceeds data-driven ceiling %.3f kW; clamping",
                    final_load_kw,
                    slot_hour,
                    ceiling,
                )
                final_load_kw = ceiling

        if bucket_tag:
            adjusted_source = f"{adjusted_source}:{bucket_tag}"

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
        # No confidence pre-gate here: predict_load (correlation.py:507/520) is
        # the authoritative per-zone gate, refusing the adjustment (returning
        # "low_confidence") when the relevant zone fails samples/r². The old
        # `confidence not in ("medium","high")` check was strictly redundant
        # with that — the hour label is the max of zone confidences, so a passing
        # zone always passed it — and reading the now-stricter hour label here
        # would wrongly suppress a usable zone in a mixed hour. Behavior-neutral.
        if coef is None:
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

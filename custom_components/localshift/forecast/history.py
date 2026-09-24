"""Historical load data fetching and caching.

This module handles fetching historical load data from Home Assistant's
recorder/statistics database for consumption forecasting.

Supports day-of-week aware consumption prediction with separate weekday
and weekend profiles for improved forecast accuracy.

New in this patch (Issue #151):
- HVAC-aware load separation: separate HVAC-active vs non-HVAC samples.
- Baseline calculation from non-HVAC samples using the 25th percentile.
- Tagging of HVAC state information in the separated profiles (baseline/hvac).
"""

from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime, timedelta
from typing import Any, cast

try:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.util import dt as dt_util
except Exception:  # pragma: no cover

    class ConfigEntry:  # pragma: no cover
        pass

    class _ConfigStub:  # pragma: no cover
        """Stub for HA config when homeassistant is not available."""

        time_zone: str = "UTC"

    class _StatesStub:  # pragma: no cover
        """Stub for HA states when homeassistant is not available."""

        def get(self, entity_id: str):  # pragma: no cover
            return None

    class HomeAssistant:  # pragma: no cover
        """Stub for HomeAssistant when homeassistant is not available."""

        config = _ConfigStub()
        states = _StatesStub()

    class _DTUtilStub:  # pragma: no cover
        @staticmethod
        def now():  # pragma: no cover
            from datetime import datetime

            return datetime.now()

        @staticmethod
        def get_time_zone(tz):  # pragma: no cover
            return tz

        @staticmethod
        def utc_from_timestamp(ts):  # pragma: no cover
            from datetime import datetime

            if isinstance(ts, (int, float)):
                return datetime.fromtimestamp(ts, tz=UTC)
            return None

        @staticmethod
        def parse_datetime(dt_str):  # pragma: no cover
            from datetime import datetime

            try:
                return datetime.fromisoformat(dt_str)
            except Exception:
                return None

        @staticmethod
        def as_local(dt):  # pragma: no cover
            return dt

    dt_util = _DTUtilStub()  # pragma: no cover

from ..const import (
    AWAY_FLOOR_HOURS,
    HISTORY_WINDOW_DAYS,
    MIN_SAMPLES_PER_HOUR,
    RECENT_LOAD_SHORT_WINDOW_SAMPLES,
)
from ..utils.away import (
    away_full_utc_hour_starts,
    away_utc_hour_starts,
    fetch_away_intervals_sync,
    get_away_entity_id,
)
from .load import AwayProfiles

_LOGGER = logging.getLogger(__name__)


class HistoryFetcher:
    """Fetches and caches historical load data from HA statistics.

    Supports separate weekday/weekend consumption profiles for better
    forecast accuracy in households with different daily patterns.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the history fetcher.

        Args:
            hass: Home Assistant instance
            entry: Config entry

        """
        self.hass = hass
        self.entry = entry

        # Historical load cache (combined hourly averages - backward compatibility)
        self._historical_load_cache: dict[int, float] = {}
        self._historical_load_sample_counts: dict[int, int] = {}
        self._historical_load_source: str = "unknown"
        self._historical_load_cache_date: str = ""
        # Away-entity masking (docs/holiday-away/plan.md item 3): the cache is
        # valid only while both the date AND the configured away entity match,
        # so changing or clearing the option refetches on the next tick.
        self._historical_load_cache_away_entity: str | None = None
        self._away_masked_hours: int = 0
        # Away-mode consumption profile (docs/holiday-away/plan.md item 2):
        # built from the away-masked rows above, not a new recorder fetch.
        self._away_avg: dict[int, float] = {}
        self._away_counts: dict[int, int] = {}
        self._away_floor_kw: float | None = None

        # Day-of-week aware consumption profiles (issue-60)
        self._weekday_hourly_avg_kw: dict[int, float] = {}
        self._weekend_hourly_avg_kw: dict[int, float] = {}
        self._weekday_sample_counts: dict[int, int] = {}
        self._weekend_sample_counts: dict[int, int] = {}
        self._profile_source: str = (
            "unknown"  # "weekday_weekend" or "combined_fallback"
        )

        # Per-day-of-week (Mon=0..Sun=6) consumption profiles (Issue #679).
        # {day_of_week: {hour: avg_kw}} / {day_of_week: {hour: sample_count}}.
        # Populated under the same _historical_load_cache_date guard as the
        # weekday/weekend aggregate above, so cache invalidation stays correct.
        self._daily_hourly_avg_kw: dict[int, dict[int, float]] = {}
        self._daily_sample_counts: dict[int, dict[int, int]] = {}

        # Recent load cache (1-hour average)
        self._recent_load_1hr_kw: float = 0.0
        self._recent_load_short_kw: float = 0.0
        self._recent_load_cache_time: datetime | None = None
        self._recent_load_1hr_statistic_id: str = ""
        self._recent_load_1hr_samples: int = 0
        self._recent_load_1hr_last_error: str = ""

    async def async_get_historical_hourly_averages(
        self, entity_id: str
    ) -> tuple[dict[int, float], dict[int, int], str]:
        """Get hourly averages via thread pool, cached until midnight.

        Returns combined profile for backward compatibility.
        Use get_profile_for_day() for day-aware profiles.

        Returns: (hourly_avg_kw, sample_counts, source)
        """
        now = dt_util.now()
        today_str = now.strftime("%Y-%m-%d")
        away = get_away_entity_id(self.entry)

        # Check if cache is valid for today. The away entity is part of the
        # key: changing or clearing the option must refetch, not serve a
        # profile computed under the old (or no) mask.
        if (
            self._historical_load_cache_date == today_str
            and self._historical_load_cache
            and self._historical_load_cache_away_entity == away
        ):
            return (
                self._historical_load_cache,
                self._historical_load_sample_counts,
                self._historical_load_source,
            )

        # Run blocking history fetch in thread pool using recorder's executor
        from homeassistant.components import recorder

        _LOGGER.info("Fetching historical load data for entity: %s", entity_id)

        recorder_instance = recorder.get_instance(self.hass)
        result = await recorder_instance.async_add_executor_job(
            self._fetch_historical_data_sync, entity_id, now, away
        )

        hourly_avg_kw = result.get("combined_avg", {})
        sample_counts = result.get("combined_counts", {})
        weekday_avg = result.get("weekday_avg", {})
        weekend_avg = result.get("weekend_avg", {})
        weekday_counts = result.get("weekday_counts", {})
        weekend_counts = result.get("weekend_counts", {})
        daily_avg = result.get("daily_avg", {})
        daily_counts = result.get("daily_counts", {})
        profile_source = result.get("profile_source", "unknown")

        _LOGGER.info(
            "Historical data result: %s hours found (weekday: %s, weekend: %s)",
            len(hourly_avg_kw) if hourly_avg_kw else 0,
            len(weekday_avg) if weekday_avg else 0,
            len(weekend_avg) if weekend_avg else 0,
        )

        if hourly_avg_kw and len(hourly_avg_kw) >= 6:
            # Store combined profile (backward compatibility)
            self._historical_load_cache = hourly_avg_kw
            self._historical_load_sample_counts = sample_counts
            self._historical_load_source = "statistics"

            # Store day-of-week profiles
            self._weekday_hourly_avg_kw = weekday_avg
            self._weekend_hourly_avg_kw = weekend_avg
            self._weekday_sample_counts = weekday_counts
            self._weekend_sample_counts = weekend_counts
            self._profile_source = profile_source

            # Store per-day-of-week profiles (Issue #679), same cache-date
            # guard as everything else above so invalidation stays correct.
            self._daily_hourly_avg_kw = daily_avg
            self._daily_sample_counts = daily_counts

            self._historical_load_cache_date = today_str
            self._historical_load_cache_away_entity = away
            self._away_masked_hours = result.get("away_masked_hours", 0)
            # Always in the success branch, so clearing the away option
            # resets these to empty (result carries empty away fields when
            # no away entity is configured).
            self._away_avg = result.get("away_avg", {})
            self._away_counts = result.get("away_counts", {})
            self._away_floor_kw = result.get("away_floor_kw")
            _LOGGER.debug(
                "Historical load profile fetched: %s hours (source: %s)",
                len(hourly_avg_kw),
                profile_source,
            )
        else:
            self._historical_load_source = "live_load_fallback"
            self._profile_source = "live_load_fallback"
            _LOGGER.debug(
                "Using live load fallback (insufficient history: %s hours)",
                len(hourly_avg_kw) if hourly_avg_kw else 0,
            )
            # A trip longer than the history window can leave fewer than 6
            # at-home hours while the away profile is at its richest — store
            # it anyway. A failed fetch returns an empty away_avg too, so
            # this never overwrites a stored profile with nothing.
            away_avg = result.get("away_avg", {})
            if away_avg:
                self._away_avg = away_avg
                self._away_counts = result.get("away_counts", {})
                self._away_floor_kw = result.get("away_floor_kw")

        # NEW: Return enhanced result including HVAC-separated profiles
        # If available, baseline and HVAC-separated profiles will be included.
        # This keeps backward compatibility with existing callers.
        result.setdefault("baseline_avg", {})
        result.setdefault("baseline_counts", {})
        result.setdefault("hvac_avg", {})
        result.setdefault("hvac_counts", {})

        return (
            self._historical_load_cache,
            self._historical_load_sample_counts,
            self._historical_load_source,
        )

    def _fetch_historical_data_sync(
        self,
        entity_id: str,
        now: datetime,
        away_entity_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch historical data using HA recorder/statistics (runs in thread pool).

        Returns dict with:
        - combined_avg: Combined hourly averages (backward compatibility)
        - combined_counts: Combined sample counts
        - weekday_avg: Weekday hourly averages
        - weekend_avg: Weekend hourly averages
        - weekday_counts: Weekday sample counts
        - weekend_counts: Weekend sample counts
        - profile_source: "weekday_weekend" or "combined_fallback"
        - away_masked_hours: count of rows dropped as away hours (0 when
          away_entity_id is None)

        Additionally, HVAC-separated profiles (baseline and hvac) are exposed:
        - baseline_avg: baseline load by hour (non-HVAC), calculated using 25th percentile
        - baseline_counts: sample counts for baseline per hour
        - hvac_avg: HVAC load by hour
        - hvac_counts: sample counts for HVAC per hour

        When ``away_entity_id`` is set, hours the house was away for any part
        of are dropped from every profile below (docs/holiday-away/plan.md
        item 3) before they're computed, so every downstream profile —
        combined, weekday/weekend, per-day, baseline and hvac — is masked
        from this one change point.
        """
        start_time = now - timedelta(days=HISTORY_WINDOW_DAYS)

        recorder_stats = self._import_recorder_statistics()
        if recorder_stats is None:
            return self._empty_result()

        stat_ids = self._list_statistic_ids(recorder_stats)
        resolved_entity_id = self._resolve_statistic_id(entity_id, stat_ids)

        fn = self._get_statistics_fn(recorder_stats)
        if fn is None:
            return self._empty_result()

        statistics_data = self._fetch_statistics_data(
            fn, resolved_entity_id, start_time, now, period="hour"
        )
        if "error" in statistics_data:
            return self._empty_result()

        rows = statistics_data.get("rows", [])

        away_masked_hours = 0
        masked_rows: list[dict[str, Any]] = []
        intervals: list[tuple[datetime, datetime]] = []
        if away_entity_id:
            intervals = fetch_away_intervals_sync(
                self.hass, away_entity_id, start_time, now
            )
            away_hours = away_utc_hour_starts(intervals)
            rows, masked_rows = self._split_away_rows(rows, away_hours)
            away_masked_hours = len(masked_rows)

        local_tz = dt_util.get_time_zone(self.hass.config.time_zone)
        result = self._compute_historical_profiles(rows, local_tz)
        result["away_masked_hours"] = away_masked_hours
        result["away_avg"], result["away_counts"], result["away_floor_kw"] = (
            self._compute_away_profile(masked_rows, intervals, local_tz, result)
            if away_entity_id
            else ({}, {}, None)
        )
        return result

    def _compute_away_profile(
        self,
        masked_rows: list[dict[str, Any]],
        intervals: list[tuple[datetime, datetime]],
        local_tz: Any,
        at_home_result: dict[str, Any],
    ) -> tuple[dict[int, float], dict[int, int], float | None]:
        """Build the away-mode profile and overnight floor (plan item 2).

        Reuses the rows already dropped from the at-home profile (no new
        recorder IO): only the strict subset that falls entirely inside an
        away interval (``away_full_utc_hour_starts``) feeds the away
        average, so a departure or return hour never contaminates it. The
        floor is the mean of the (already at-home-masked) combined profile
        over ``AWAY_FLOOR_HOURS``.

        Args:
            masked_rows: Rows dropped from the at-home profile as away hours.
            intervals: The away entity's on/off intervals for this window.
            local_tz: Local timezone for day-of-week determination.
            at_home_result: This fetch's at-home ``_compute_historical_profiles``
                result, for the floor's source (``combined_avg``).

        Returns:
            Tuple of (away_avg, away_counts, away_floor_kw).

        """
        full_hours = away_full_utc_hour_starts(intervals)
        _, full_rows = self._split_away_rows(masked_rows, full_hours)
        away_by_weekday = self._separate_samples_by_weekday(full_rows, local_tz)
        away_weekday_by_hour, away_weekend_by_hour = self._derive_day_type_buckets(
            away_by_weekday
        )
        away_avg, away_counts = self._compute_combined_profile(
            away_weekday_by_hour, away_weekend_by_hour
        )

        at_home_combined = at_home_result.get("combined_avg", {})
        floor_values = [
            at_home_combined[hour]
            for hour in AWAY_FLOOR_HOURS
            if hour in at_home_combined
        ]
        away_floor_kw = sum(floor_values) / len(floor_values) if floor_values else None

        return away_avg, away_counts, away_floor_kw

    def _parse_row_start(self, start_val: Any) -> datetime | None:
        """Parse a statistics row's 'start' field into an aware datetime.

        Accepts the three shapes a row's 'start' can take: a datetime, a
        Unix timestamp (int/float), or an ISO string. Anything else returns
        None, and callers skip the row.

        Args:
            start_val: The row's 'start' value.

        Returns:
            An aware datetime, or None if it couldn't be parsed.

        """
        if isinstance(start_val, datetime):
            return start_val
        if isinstance(start_val, int | float):
            # Unix timestamp (seconds since epoch)
            return dt_util.utc_from_timestamp(start_val)
        if isinstance(start_val, str):
            return dt_util.parse_datetime(start_val)
        return None

    def _split_away_rows(
        self,
        rows: list[dict[str, Any]],
        away_hours: frozenset[datetime],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split statistics rows into (kept, masked) by away hour.

        A row is masked when its 'start', floored to the UTC hour, is one of
        the away entity's away hours. Rows whose start can't be parsed are
        kept — the existing parser in _separate_samples_by_weekday skips
        them the same way it always has, so a kept-but-unparseable row still
        drops out of every profile downstream.

        Args:
            rows: Statistics rows with a 'start' field.
            away_hours: Away hour starts (aware UTC datetimes), from
                away_utc_hour_starts.

        Returns:
            Tuple of (kept, masked) row lists.

        """
        if not away_hours:
            return rows, []

        kept: list[dict[str, Any]] = []
        masked: list[dict[str, Any]] = []
        for row in rows:
            parsed = (
                self._parse_row_start(row.get("start"))
                if isinstance(row, dict)
                else None
            )
            if parsed is None:
                kept.append(row)
                continue
            hour_start = dt_util.as_utc(parsed).replace(
                minute=0, second=0, microsecond=0
            )
            if hour_start in away_hours:
                masked.append(row)
            else:
                kept.append(row)
        return kept, masked

    def _compute_historical_profiles(
        self, rows: list[dict[str, Any]], local_tz: Any
    ) -> dict[str, Any]:
        """Compute consumption profiles from statistics rows.

        Args:
            rows: List of statistics rows with 'start' and 'mean' fields
            local_tz: Local timezone for day-of-week determination

        Returns:
            Dict with weekday/weekend/combined/baseline/hvac profiles

        """
        by_weekday = self._separate_samples_by_weekday(rows, local_tz)
        weekday_by_hour, weekend_by_hour = self._derive_day_type_buckets(by_weekday)

        non_hvac_by_hour, hvac_by_hour = self._separate_hvac_load(
            weekday_by_hour, weekend_by_hour, climate_states=None
        )

        baseline_by_hour = self.calculate_baseline_profile(non_hvac_by_hour)

        hvac_avg, hvac_counts = self._compute_hourly_averages(hvac_by_hour)
        weekday_avg, weekday_counts = self._compute_hourly_averages(weekday_by_hour)
        weekend_avg, weekend_counts = self._compute_hourly_averages(weekend_by_hour)

        combined_avg, combined_counts = self._compute_combined_profile(
            weekday_by_hour, weekend_by_hour
        )

        daily_avg, daily_counts = self._compute_daily_profiles(by_weekday)

        profile_source = self._determine_profile_source(weekday_counts, weekend_counts)

        return {
            "combined_avg": combined_avg,
            "combined_counts": combined_counts,
            "weekday_avg": weekday_avg,
            "weekend_avg": weekend_avg,
            "weekday_counts": weekday_counts,
            "weekend_counts": weekend_counts,
            "daily_avg": daily_avg,
            "daily_counts": daily_counts,
            "profile_source": profile_source,
            "baseline_avg": baseline_by_hour,
            "baseline_counts": {
                hour: len(non_hvac_by_hour.get(hour, [])) for hour in non_hvac_by_hour
            },
            "hvac_avg": hvac_avg,
            "hvac_counts": hvac_counts,
        }

    def _compute_hourly_averages(
        self, samples_by_hour: dict[int, list[float]]
    ) -> tuple[dict[int, float], dict[int, int]]:
        """Compute average and count for each hour.

        Args:
            samples_by_hour: Dict mapping hour to list of power values

        Returns:
            Tuple of (averages, counts) dicts

        """
        averages: dict[int, float] = {}
        counts: dict[int, int] = {}
        for hour, vals in samples_by_hour.items():
            if vals:
                averages[hour] = sum(vals) / len(vals)
                counts[hour] = len(vals)
        return averages, counts

    def _compute_combined_profile(
        self,
        weekday_by_hour: dict[int, list[float]],
        weekend_by_hour: dict[int, list[float]],
    ) -> tuple[dict[int, float], dict[int, int]]:
        """Compute combined profile from weekday and weekend samples.

        Args:
            weekday_by_hour: Weekday samples by hour
            weekend_by_hour: Weekend samples by hour

        Returns:
            Tuple of (combined_avg, combined_counts)

        """
        combined_avg: dict[int, float] = {}
        combined_counts: dict[int, int] = {}
        for hour in range(24):
            all_vals = weekday_by_hour.get(hour, []) + weekend_by_hour.get(hour, [])
            if all_vals:
                combined_avg[hour] = sum(all_vals) / len(all_vals)
                combined_counts[hour] = len(all_vals)
        return combined_avg, combined_counts

    def _separate_samples_by_weekday(
        self, rows: list[dict[str, Any]], _local_tz: Any
    ) -> dict[int, dict[int, list[float]]]:
        """Separate statistics rows into per-day-of-week hourly buckets (Issue #679).

        This is the single parser of `rows`: `_separate_samples_by_day_type`
        derives its weekday/weekend 2-bucket split from this method's output
        rather than re-parsing `rows` itself.

        Args:
            rows: List of statistics rows with 'start' and 'mean' fields
            _local_tz: Local timezone for day-of-week determination (unused,
                kept for API compatibility with _separate_samples_by_day_type)

        Returns:
            Dict {0..6: {0..23: [values]}}, Monday=0 .. Sunday=6. Every
            day-of-week and every hour key is always present (possibly with
            an empty list) so callers never need a defensive .get().

        """
        by_weekday: dict[int, dict[int, list[float]]] = {
            dow: {h: [] for h in range(24)} for dow in range(7)
        }

        for row in rows:
            if not isinstance(row, dict):
                continue

            row_dt = self._parse_row_start(row.get("start"))
            if row_dt is None:
                continue

            mean_val = row.get("mean")
            if mean_val in (None, "unknown", "unavailable"):
                continue

            try:
                mean_kw = float(mean_val)
            except (TypeError, ValueError):
                continue

            # Convert to local time for day-of-week determination
            local_dt = dt_util.as_local(row_dt)
            hour = local_dt.hour
            day_of_week = local_dt.weekday()  # Monday=0, Sunday=6

            by_weekday[day_of_week][hour].append(mean_kw)

        return by_weekday

    def _derive_day_type_buckets(
        self, by_weekday: dict[int, dict[int, list[float]]]
    ) -> tuple[dict[int, list[float]], dict[int, list[float]]]:
        """Merge per-day-of-week buckets into weekday (Mon-Fri) / weekend (Sat-Sun).

        Args:
            by_weekday: Output of _separate_samples_by_weekday: {0..6: {0..23: [values]}}

        Returns:
            Tuple of (weekday_by_hour, weekend_by_hour) where each is
            {hour: [values]} for hours 0-23.

        """
        weekday_by_hour: dict[int, list[float]] = {h: [] for h in range(24)}
        weekend_by_hour: dict[int, list[float]] = {h: [] for h in range(24)}

        for day_of_week, samples_by_hour in by_weekday.items():
            target = weekend_by_hour if day_of_week >= 5 else weekday_by_hour
            for hour, values in samples_by_hour.items():
                if values:
                    target[hour].extend(values)

        return weekday_by_hour, weekend_by_hour

    def _separate_samples_by_day_type(
        self, rows: list[dict[str, Any]], _local_tz: Any
    ) -> tuple[dict[int, list[float]], dict[int, list[float]]]:
        """Separate statistics rows into weekday and weekend hourly buckets.

        Derived from _separate_samples_by_weekday (Issue #679): weekday is the
        Mon-Fri (0-4) buckets merged, weekend is Sat-Sun (5-6) merged. Kept as
        its own method (rather than inlined at every call site) because its
        2-tuple return shape is part of the tested public surface.

        Args:
            rows: List of statistics rows with 'start' and 'mean' fields
            _local_tz: Local timezone for day-of-week determination (unused, kept for API compatibility)

        Returns:
            Tuple of (weekday_by_hour, weekend_by_hour) where each is
            {hour: [values]} for hours 0-23.

        """
        by_weekday = self._separate_samples_by_weekday(rows, _local_tz)
        return self._derive_day_type_buckets(by_weekday)

    def _compute_daily_profiles(
        self, by_weekday: dict[int, dict[int, list[float]]]
    ) -> tuple[dict[int, dict[int, float]], dict[int, dict[int, int]]]:
        """Compute per-day-of-week hourly averages and sample counts (Issue #679).

        Args:
            by_weekday: Output of _separate_samples_by_weekday: {0..6: {0..23: [values]}}

        Returns:
            Tuple of (daily_avg, daily_counts), each {day_of_week: {hour: value}}.
            A day-of-week with no samples anywhere still gets an entry (from
            _compute_hourly_averages skipping empty hours), so lookups on an
            always-present key never need a defensive .get() at the day level
            either — only at the hour level, same as the 2-bucket profiles.

        """
        daily_avg: dict[int, dict[int, float]] = {}
        daily_counts: dict[int, dict[int, int]] = {}
        for day_of_week, samples_by_hour in by_weekday.items():
            avg, counts = self._compute_hourly_averages(samples_by_hour)
            daily_avg[day_of_week] = avg
            daily_counts[day_of_week] = counts
        return daily_avg, daily_counts

    def _separate_hvac_load(
        self,
        weekday_by_hour: dict[int, list[float]],
        weekend_by_hour: dict[int, list[float]],
        climate_states: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[dict[int, list[float]], dict[int, list[float]]]:
        """Separate historical load samples into HVAC and non-HVAC buckets.

        This is a lightweight heuristic used in absence of per-sample HVAC state
        data. If climate_states is provided and contains any HVAC-active state
        (cooling/heating/drying), then samples are treated as HVAC-active for all
        hours. Otherwise, all samples are treated as non-HVAC.

        Args:
            weekday_by_hour: Weekday samples by hour (hour -> [power_kw]).
            weekend_by_hour: Weekend samples by hour (hour -> [power_kw]).
            climate_states: Optional mapping of climate entity states with 'hvac_action'.

        Returns:
            Tuple (non_hvac_by_hour, hvac_by_hour) where each is a dict hour -> [power_kw].

        """
        # Determine if any HVAC activity is reported in climate_states
        hvac_active_global = False
        if isinstance(climate_states, dict) and climate_states:
            for _entity_id, state in climate_states.items():
                action = state.get("hvac_action", "off")
                if isinstance(action, str) and action in (
                    "cooling",
                    "heating",
                    "drying",
                ):
                    hvac_active_global = True
                    break

        non_hvac: dict[int, list[float]] = {}
        hvac: dict[int, list[float]] = {}

        # Build a combined hourly map from both weekday and weekend samples
        for hour in range(24):
            values: list[float] = []
            if weekday_by_hour and hour in weekday_by_hour:
                values.extend(weekday_by_hour[hour])
            if weekend_by_hour and hour in weekend_by_hour:
                values.extend(weekend_by_hour[hour])
            if not values:
                continue
            if hvac_active_global:
                hvac[hour] = hvac.get(hour, []) + values
            else:
                non_hvac[hour] = non_hvac.get(hour, []) + values

        return non_hvac, hvac

    def calculate_baseline_profile(
        self, non_hvac_samples: dict[int, list[float]]
    ) -> dict[int, float]:
        """Calculate baseline load profile using 25th percentile.

        The 25th percentile filters out discretionary load spikes
        (dishwasher, EV charging, etc.) while preserving the typical
        background consumption pattern.

        Args:
            non_hvac_samples: Hour -> list of power readings (non-HVAC only).

        Returns:
            Hour -> baseline power in kW.

        """

        baseline: dict[int, float] = {}

        for hour, powers in non_hvac_samples.items():
            if len(powers) >= 3:
                # Use 25th percentile
                sorted_powers = sorted(powers)
                idx = int(len(sorted_powers) * 0.25)
                baseline[hour] = sorted_powers[idx]
            elif powers:
                # Not enough samples for percentile - use min
                baseline[hour] = min(powers)
            else:
                baseline[hour] = 0.0

        return baseline

    def _determine_profile_source(
        self,
        weekday_counts: dict[int, int],
        weekend_counts: dict[int, int],
    ) -> str:
        """Determine if we have sufficient samples for day-specific profiles.

        Args:
            weekday_counts: Sample counts per hour for weekdays
            weekend_counts: Sample counts per hour for weekends

        Returns:
            "weekday_weekend" if sufficient samples, "combined_fallback" otherwise

        """
        # Check if we have minimum samples for most hours in both profiles
        weekday_hours_with_min = sum(
            1 for count in weekday_counts.values() if count >= MIN_SAMPLES_PER_HOUR
        )
        weekend_hours_with_min = sum(
            1 for count in weekend_counts.values() if count >= MIN_SAMPLES_PER_HOUR
        )

        # Need at least 12 hours with minimum samples in each profile
        # (allowing for some hours with low activity)
        if weekday_hours_with_min >= 12 and weekend_hours_with_min >= 12:
            return "weekday_weekend"
        else:
            _LOGGER.debug(
                "Insufficient samples for day-specific profiles: "
                "weekday_hours=%s, weekend_hours=%s (min required: 12)",
                weekday_hours_with_min,
                weekend_hours_with_min,
            )
            return "combined_fallback"

    def _empty_result(self) -> dict[str, Any]:
        """Return empty result structure."""
        return {
            "combined_avg": {},
            "combined_counts": {},
            "weekday_avg": {},
            "weekend_avg": {},
            "weekday_counts": {},
            "weekend_counts": {},
            "daily_avg": {},
            "daily_counts": {},
            "profile_source": "unknown",
            "away_avg": {},
            "away_counts": {},
            "away_floor_kw": None,
        }

    def _resolve_statistic_id(
        self, entity_id: str, stat_ids: list[dict[str, Any]]
    ) -> str:
        """Find matching statistic_id from list.

        Args:
            entity_id: The entity ID to resolve
            stat_ids: List of statistic metadata dicts from recorder

        Returns:
            The matching statistic_id, or entity_id if not found

        """
        for sid in stat_ids:
            if not isinstance(sid, dict):
                continue
            stat_id = sid.get("statistic_id", "")
            # Match exact or without sensor. prefix
            if stat_id == entity_id or stat_id.replace(
                "sensor.", ""
            ) == entity_id.replace("sensor.", ""):
                return stat_id
        return entity_id  # Fallback to original

    def get_profile_for_day(
        self, target_date: datetime
    ) -> tuple[dict[int, float], dict[int, int], str]:
        """Get appropriate hourly profile based on target day's day-of-week.

        Args:
            target_date: The date to get the profile for

        Returns:
            Tuple of (hourly_avg_kw, sample_counts, source) where source is
            "weekday", "weekend", or "combined" (fallback).

        """
        # If no profiles available, return empty
        if not self._weekday_hourly_avg_kw and not self._weekend_hourly_avg_kw:
            return {}, {}, "combined"

        # If using combined fallback, return combined profile
        if self._profile_source == "combined_fallback":
            return (
                self._historical_load_cache,
                self._historical_load_sample_counts,
                "combined",
            )

        # Determine day type
        day_of_week = target_date.weekday()  # Monday=0, Sunday=6

        if day_of_week >= 5:  # Saturday or Sunday
            # Check if weekend profile has sufficient data
            if self._weekend_hourly_avg_kw:
                return (
                    self._weekend_hourly_avg_kw,
                    self._weekend_sample_counts,
                    "weekend",
                )
            # Fallback to combined if weekend profile insufficient
            return (
                self._historical_load_cache,
                self._historical_load_sample_counts,
                "combined",
            )
        else:
            # Weekday
            if self._weekday_hourly_avg_kw:
                return (
                    self._weekday_hourly_avg_kw,
                    self._weekday_sample_counts,
                    "weekday",
                )
            # Fallback to combined if weekday profile insufficient
            return (
                self._historical_load_cache,
                self._historical_load_sample_counts,
                "combined",
            )

    def get_weekday_profile(self) -> tuple[dict[int, float], dict[int, int]]:
        """Get weekday profile for diagnostics.

        Returns:
            Tuple of (weekday_avg, weekday_counts)

        """
        return self._weekday_hourly_avg_kw, self._weekday_sample_counts

    def get_weekend_profile(self) -> tuple[dict[int, float], dict[int, int]]:
        """Get weekend profile for diagnostics.

        Returns:
            Tuple of (weekend_avg, weekend_counts)

        """
        return self._weekend_hourly_avg_kw, self._weekend_sample_counts

    def get_daily_profiles(
        self,
    ) -> tuple[dict[int, dict[int, float]], dict[int, dict[int, int]]]:
        """Get per-day-of-week (Mon=0..Sun=6) profiles for injection (Issue #679).

        Returns:
            Tuple of (daily_avg, daily_counts), each {day_of_week: {hour: value}}.

        """
        return self._daily_hourly_avg_kw, self._daily_sample_counts

    def get_profile_source(self) -> str:
        """Get the current profile source for diagnostics.

        Returns:
            "weekday_weekend", "combined_fallback", or "unknown"

        """
        return self._profile_source

    async def async_get_recent_load_1hr(self, entity_id: str) -> float:
        """Get average load over the last 1 hour from HA statistics."""
        from homeassistant.components import recorder

        now = dt_util.now()

        # Check if cache is valid (within last 5 minutes)
        if (
            self._recent_load_cache_time is not None
            and (now - self._recent_load_cache_time).total_seconds() < 300
        ):
            return self._recent_load_1hr_kw

        # Run blocking history fetch in thread pool
        recorder_instance = recorder.get_instance(self.hass)
        try:
            result = await recorder_instance.async_add_executor_job(
                self._fetch_recent_load_sync, entity_id, now
            )
            self._recent_load_1hr_kw = float(result.get("recent_avg_kw", 0.0) or 0.0)
            self._recent_load_short_kw = float(
                result.get("recent_load_short_kw", 0.0) or 0.0
            )
            self._recent_load_1hr_statistic_id = str(result.get("statistic_id", ""))
            self._recent_load_1hr_samples = int(result.get("samples", 0) or 0)
            self._recent_load_1hr_last_error = str(result.get("error", ""))
            self._recent_load_cache_time = now
            return self._recent_load_1hr_kw
        except Exception as e:
            _LOGGER.warning("Failed to fetch recent load: %s", e)
            self._recent_load_1hr_kw = 0.0
            self._recent_load_short_kw = 0.0
            self._recent_load_1hr_statistic_id = ""
            self._recent_load_1hr_samples = 0
            self._recent_load_1hr_last_error = str(e)
            self._recent_load_cache_time = now
            return 0.0

    def _fetch_recent_load_sync(self, entity_id: str, now: datetime) -> dict[str, Any]:
        """Fetch recent 1-hour average (runs in thread pool)."""
        recorder_stats = self._import_recorder_statistics()
        if recorder_stats is None:
            return self._error_result("recorder import failed")

        start_time = now - timedelta(hours=1)
        stat_ids = self._list_statistic_ids(recorder_stats)
        resolved_entity_id = self._resolve_statistic_id(entity_id, stat_ids)

        fn = self._get_statistics_fn(recorder_stats)
        if fn is None:
            return self._error_result(
                "statistics_during_period not callable", resolved_entity_id
            )

        statistics_data = self._fetch_statistics_data(
            fn, resolved_entity_id, start_time, now
        )
        if "error" in statistics_data:
            return statistics_data

        return self._compute_recent_average(statistics_data, resolved_entity_id)

    def _import_recorder_statistics(self) -> Any:
        """Import recorder statistics module.

        Returns:
            Module or None if import failed

        """
        try:
            from homeassistant.components.recorder import (
                statistics as recorder_statistics,
            )

            return recorder_statistics
        except Exception:
            return None

    def _list_statistic_ids(self, recorder_stats: Any) -> list[dict[str, Any]]:
        """List available statistic IDs.

        Args:
            recorder_stats: Recorder statistics module

        Returns:
            List of statistic ID dicts

        """
        try:
            stat_meta_fn = getattr(recorder_stats, "list_statistic_ids", None)
            if not callable(stat_meta_fn):
                return []
            stat_ids_raw = stat_meta_fn(self.hass, None) or []
            if not isinstance(stat_ids_raw, list):
                return []
            return [
                cast(dict[str, Any], s) for s in stat_ids_raw if isinstance(s, dict)
            ]
        except Exception:
            return []

    def _get_statistics_fn(self, recorder_stats: Any) -> Any:
        """Get statistics_during_period callable.

        Args:
            recorder_stats: Recorder statistics module

        Returns:
            Callable function or None

        """
        fn = getattr(recorder_stats, "statistics_during_period", None)
        return fn if callable(fn) else None

    def _fetch_statistics_data(
        self,
        fn: Any,
        resolved_entity_id: str,
        start_time: datetime,
        end_time: datetime,
        period: str = "5minute",
    ) -> dict[str, Any]:
        """Fetch statistics data for entity.

        Args:
            fn: statistics_during_period function
            resolved_entity_id: Resolved statistic ID
            start_time: Query start time
            end_time: Query end time
            period: Aggregation period ("5minute" or "hour")

        Returns:
            Statistics data dict or error result

        """
        try:
            statistics_data_raw = fn(
                self.hass,
                start_time,
                end_time,
                [resolved_entity_id],
                period=period,
                types={"mean"},
                units=None,
            )
        except Exception:
            return self._error_result(
                "statistics_during_period exception", resolved_entity_id
            )

        if not isinstance(statistics_data_raw, dict):
            return self._error_result(
                "statistics_during_period returned non-dict", resolved_entity_id
            )

        statistics_data = cast(dict[str, Any], statistics_data_raw)

        if not statistics_data or resolved_entity_id not in statistics_data:
            return self._error_result("no statistics data", resolved_entity_id)

        rows_raw = statistics_data.get(resolved_entity_id)
        if not isinstance(rows_raw, list) or not rows_raw:
            return self._error_result("no rows", resolved_entity_id)

        rows: list[dict[str, Any]] = [
            cast(dict[str, Any], r) for r in rows_raw if isinstance(r, dict)
        ]
        if not rows:
            return self._error_result("no dict rows", resolved_entity_id)

        return {"rows": rows, "statistic_id": resolved_entity_id}

    def _compute_recent_average(
        self, data: dict[str, Any], resolved_entity_id: str
    ) -> dict[str, Any]:
        """Compute recent average from statistics rows.

        Args:
            data: Dict with 'rows' key
            resolved_entity_id: Statistic ID

        Returns:
            Result dict with average or error

        """
        rows = data.get("rows", [])
        values: list[float] = []
        pairs: list[tuple[Any, float]] = []

        for row in rows:
            if not isinstance(row, dict):
                continue
            mean_val = row.get("mean")
            if mean_val in (None, "unknown", "unavailable"):
                continue
            try:
                fval = float(mean_val)
            except (TypeError, ValueError):
                continue
            values.append(fval)
            pairs.append((row.get("start"), fval))

        if not values:
            return self._error_result("no numeric mean values", resolved_entity_id)

        recent_avg = sum(values) / len(values)
        short_kw = self._compute_short_window_median(
            pairs, RECENT_LOAD_SHORT_WINDOW_SAMPLES
        )

        return {
            "recent_avg_kw": recent_avg,
            "recent_load_short_kw": short_kw,
            "samples": len(values),
            "statistic_id": resolved_entity_id,
            "error": "",
        }

    def _compute_short_window_median(
        self, pairs: list[tuple[Any, float]], k: int
    ) -> float:
        """Return the median of the k most-recent 5-min stat means (by start timestamp).

        Rows without a start key are treated as oldest so they sort to the end only
        among their own bucket; rows with real starts always rank ahead of them.
        """
        pairs_sorted = sorted(pairs, key=lambda p: (p[0] is None, p[0]))
        window = [m for _, m in pairs_sorted[-k:]]
        return statistics.median(window) if window else 0.0

    def _error_result(self, error: str, statistic_id: str = "") -> dict[str, Any]:
        """Create standardized error result.

        Args:
            error: Error message
            statistic_id: Statistic ID (optional)

        Returns:
            Error result dict

        """
        return {
            "recent_avg_kw": 0.0,
            "recent_load_short_kw": 0.0,
            "samples": 0,
            "statistic_id": statistic_id,
            "error": error,
        }

    def get_cached_hourly_averages(self) -> dict[int, float]:
        """Get cached hourly averages (sync version).

        Returns combined profile for backward compatibility.
        """
        return self._historical_load_cache

    def get_away_masked_hours(self) -> int:
        """Return how many hourly rows were dropped as away hours.

        Zero when no away entity is configured, or before the first fetch.
        """
        return self._away_masked_hours

    def get_away_profiles(self) -> AwayProfiles:
        """Return the cached away-mode profile and overnight floor (plan item 2).

        Empty/None fields when no away entity is configured, or before the
        first fetch that produced one.
        """
        return AwayProfiles(
            away_avg=self._away_avg,
            away_counts=self._away_counts,
            floor_kw=self._away_floor_kw,
        )

    def clear_historical_cache(self) -> None:
        """Clear historical load cache to force refresh on next update."""
        # Clear combined profile
        self._historical_load_cache = {}
        self._historical_load_sample_counts = {}
        self._historical_load_source = "unknown"
        self._historical_load_cache_date = ""
        self._historical_load_cache_away_entity = None
        self._away_masked_hours = 0
        self._away_avg = {}
        self._away_counts = {}
        self._away_floor_kw = None

        # Clear day-of-week profiles
        self._weekday_hourly_avg_kw = {}
        self._weekend_hourly_avg_kw = {}
        self._weekday_sample_counts = {}
        self._weekend_sample_counts = {}
        self._profile_source = "unknown"

        # Clear per-day-of-week profiles (Issue #679)
        self._daily_hourly_avg_kw = {}
        self._daily_sample_counts = {}

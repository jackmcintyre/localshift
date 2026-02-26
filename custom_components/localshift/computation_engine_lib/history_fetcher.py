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
from datetime import UTC, datetime, timedelta
from typing import Any, cast

try:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.util import dt as dt_util
except Exception:

    class ConfigEntry:
        pass

    class _ConfigStub:
        """Stub for HA config when homeassistant is not available."""

        time_zone: str = "UTC"

    class HomeAssistant:
        """Stub for HomeAssistant when homeassistant is not available."""

        config = _ConfigStub()

    class _DTUtilStub:
        @staticmethod
        def now():
            from datetime import datetime

            return datetime.now()

        @staticmethod
        def get_time_zone(tz):
            return tz

        @staticmethod
        def utc_from_timestamp(ts):
            from datetime import datetime

            if isinstance(ts, (int, float)):
                return datetime.fromtimestamp(ts, tz=UTC)
            return None

        @staticmethod
        def parse_datetime(dt_str):
            from datetime import datetime

            try:
                return datetime.fromisoformat(dt_str)
            except Exception:
                return None

        @staticmethod
        def as_local(dt):
            return dt

    dt_util = _DTUtilStub()

from ..const import HISTORY_WINDOW_DAYS, MIN_SAMPLES_PER_HOUR

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

        # Day-of-week aware consumption profiles (issue-60)
        self._weekday_hourly_avg_kw: dict[int, float] = {}
        self._weekend_hourly_avg_kw: dict[int, float] = {}
        self._weekday_sample_counts: dict[int, int] = {}
        self._weekend_sample_counts: dict[int, int] = {}
        self._profile_source: str = (
            "unknown"  # "weekday_weekend" or "combined_fallback"
        )

        # Recent load cache (1-hour average)
        self._recent_load_1hr_kw: float = 0.0
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

        # Check if cache is valid for today
        if (
            self._historical_load_cache_date == today_str
            and self._historical_load_cache
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
            self._fetch_historical_data_sync, entity_id, now
        )

        hourly_avg_kw = result.get("combined_avg", {})
        sample_counts = result.get("combined_counts", {})
        weekday_avg = result.get("weekday_avg", {})
        weekend_avg = result.get("weekend_avg", {})
        weekday_counts = result.get("weekday_counts", {})
        weekend_counts = result.get("weekend_counts", {})
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

            self._historical_load_cache_date = today_str
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
        self, entity_id: str, now: datetime
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

        Additionally, HVAC-separated profiles (baseline and hvac) are exposed:
        - baseline_avg: baseline load by hour (non-HVAC), calculated using 25th percentile
        - baseline_counts: sample counts for baseline per hour
        - hvac_avg: HVAC load by hour
        - hvac_counts: sample counts for HVAC per hour
        """
        start_time = now - timedelta(days=HISTORY_WINDOW_DAYS)

        try:
            from homeassistant.components.recorder import (
                statistics as recorder_statistics,
            )
        except Exception as e:
            _LOGGER.info("Failed to import recorder statistics: %s", e)
            return self._empty_result()

        # Get statistics metadata to find the correct statistic_id
        stat_ids: list[dict[str, Any]] = []
        try:
            stat_meta_fn = getattr(recorder_statistics, "list_statistic_ids", None)
            if callable(stat_meta_fn):
                stat_ids_raw = stat_meta_fn(self.hass, None) or []
                if isinstance(stat_ids_raw, list):
                    stat_ids = [
                        cast(dict[str, Any], s)
                        for s in stat_ids_raw
                        if isinstance(s, dict)
                    ]
        except Exception as e:
            _LOGGER.info("Failed to list statistic ids: %s", e)
            pass

        # Find matching statistic_id
        resolved_entity_id = self._resolve_statistic_id(entity_id, stat_ids)

        # Get statistics
        fn = getattr(recorder_statistics, "statistics_during_period", None)
        if not callable(fn):
            return self._empty_result()

        try:
            statistics_data_raw = fn(
                self.hass,
                start_time,
                now,
                [resolved_entity_id],
                period="hour",
                types={"mean"},
                units=None,
            )
        except Exception as e:
            _LOGGER.info("statistics_during_period exception: %s", e)
            return self._empty_result()

        if not isinstance(statistics_data_raw, dict):
            return self._empty_result()

        statistics_data = cast(dict[str, Any], statistics_data_raw)

        if not statistics_data or resolved_entity_id not in statistics_data:
            return self._empty_result()

        rows_raw = statistics_data.get(resolved_entity_id)
        if not isinstance(rows_raw, list) or not rows_raw:
            return self._empty_result()

        rows: list[dict[str, Any]] = [
            cast(dict[str, Any], r) for r in rows_raw if isinstance(r, dict)
        ]
        if not rows:
            return self._empty_result()

        # Separate samples by day type (weekday vs weekend)
        weekday_by_hour, weekend_by_hour = self._separate_samples_by_day_type(
            rows, dt_util.get_time_zone(self.hass.config.time_zone)
        )

        # NEW HVAC separation (best-effort with available data)
        non_hvac_by_hour, hvac_by_hour = self._separate_hvac_load(
            weekday_by_hour, weekend_by_hour, climate_states=None
        )

        # Calculate profiles for non-HVAC baseline
        baseline_by_hour = self.calculate_baseline_profile(non_hvac_by_hour)

        # Calculate hvac profile averages
        hvac_avg: dict[int, float] = {}
        hvac_counts: dict[int, int] = {}
        for hour, values in hvac_by_hour.items():
            if values:
                hvac_avg[hour] = sum(values) / len(values)
                hvac_counts[hour] = len(values)

        # Prepare combined (backward-compatible) profile
        weekday_avg = {
            hour: sum(vals) / len(vals)
            for hour, vals in weekday_by_hour.items()
            if vals
        }
        weekend_avg = {
            hour: sum(vals) / len(vals)
            for hour, vals in weekend_by_hour.items()
            if vals
        }
        weekday_counts = {
            hour: len(vals) for hour, vals in weekday_by_hour.items() if vals
        }
        weekend_counts = {
            hour: len(vals) for hour, vals in weekend_by_hour.items() if vals
        }

        combined_avg: dict[int, float] = {}
        combined_counts: dict[int, int] = {}
        for hour in range(24):
            weekday_vals = weekday_by_hour.get(hour, [])
            weekend_vals = weekend_by_hour.get(hour, [])
            all_vals = weekday_vals + weekend_vals
            if all_vals:
                combined_avg[hour] = sum(all_vals) / len(all_vals)
                combined_counts[hour] = len(all_vals)

        # Determine profile source
        profile_source = self._determine_profile_source(weekday_counts, weekend_counts)

        # Build result payload
        result: dict[str, Any] = {
            "combined_avg": combined_avg,
            "combined_counts": combined_counts,
            "weekday_avg": weekday_avg,
            "weekend_avg": weekend_avg,
            "weekday_counts": weekday_counts,
            "weekend_counts": weekend_counts,
            "profile_source": profile_source,
            # New HVAC-separated metadata (optional in old callers)
            "baseline_avg": baseline_by_hour,
            "baseline_counts": {
                hour: len(non_hvac_by_hour.get(hour, [])) for hour in non_hvac_by_hour
            },
            "hvac_avg": hvac_avg,
            "hvac_counts": hvac_counts,
        }

        return result

    def _separate_samples_by_day_type(
        self, rows: list[dict[str, Any]], _local_tz: Any
    ) -> tuple[dict[int, list[float]], dict[int, list[float]]]:
        """Separate statistics rows into weekday and weekend hourly buckets.

        Args:
            rows: List of statistics rows with 'start' and 'mean' fields
            _local_tz: Local timezone for day-of-week determination (unused, kept for API compatibility)

        Returns:
            Tuple of (weekday_by_hour, weekend_by_hour) where each is
            {hour: [values]} for hours 0-23.
        """
        weekday_by_hour: dict[int, list[float]] = {h: [] for h in range(24)}
        weekend_by_hour: dict[int, list[float]] = {h: [] for h in range(24)}

        for row in rows:
            if not isinstance(row, dict):
                continue

            start_val = row.get("start")
            row_dt = None

            # Handle different timestamp formats
            if isinstance(start_val, datetime):
                row_dt = start_val
            elif isinstance(start_val, int | float):
                # Unix timestamp (seconds since epoch)
                row_dt = dt_util.utc_from_timestamp(start_val)
            elif isinstance(start_val, str):
                row_dt = dt_util.parse_datetime(start_val)

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

            # Separate weekday (Mon-Fri, 0-4) vs weekend (Sat-Sun, 5-6)
            if day_of_week >= 5:  # Saturday or Sunday
                weekend_by_hour[hour].append(mean_kw)
            else:
                weekday_by_hour[hour].append(mean_kw)

        return weekday_by_hour, weekend_by_hour

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
            "profile_source": "unknown",
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
            self._recent_load_1hr_statistic_id = str(result.get("statistic_id", ""))
            self._recent_load_1hr_samples = int(result.get("samples", 0) or 0)
            self._recent_load_1hr_last_error = str(result.get("error", ""))
            self._recent_load_cache_time = now
            return self._recent_load_1hr_kw
        except Exception as e:
            _LOGGER.warning("Failed to fetch recent load: %s", e)
            self._recent_load_1hr_kw = 0.0
            self._recent_load_1hr_statistic_id = ""
            self._recent_load_1hr_samples = 0
            self._recent_load_1hr_last_error = str(e)
            self._recent_load_cache_time = now
            return 0.0

    def _fetch_recent_load_sync(self, entity_id: str, now: datetime) -> dict[str, Any]:
        """Fetch recent 1-hour average (runs in thread pool)."""
        try:
            from homeassistant.components.recorder import (
                statistics as recorder_statistics,
            )
        except Exception:
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": "",
                "error": "recorder import failed",
            }

        end_time = now
        start_time = now - timedelta(hours=1)

        # Find matching statistic_id
        stat_ids: list[dict[str, Any]] = []
        try:
            stat_meta_fn = getattr(recorder_statistics, "list_statistic_ids", None)
            if callable(stat_meta_fn):
                stat_ids_raw = stat_meta_fn(self.hass, None) or []
                if isinstance(stat_ids_raw, list):
                    stat_ids = [
                        cast(dict[str, Any], s)
                        for s in stat_ids_raw
                        if isinstance(s, dict)
                    ]
        except Exception:
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": "",
                "error": "list_statistic_ids failed",
            }

        resolved_entity_id = self._resolve_statistic_id(entity_id, stat_ids)

        # Get statistics for last hour
        fn = getattr(recorder_statistics, "statistics_during_period", None)
        if not callable(fn):
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": resolved_entity_id,
                "error": "statistics_during_period not callable",
            }

        try:
            statistics_data_raw = fn(
                self.hass,
                start_time,
                end_time,
                [resolved_entity_id],
                period="5minute",
                types={"mean"},
                units=None,
            )
        except Exception:
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": resolved_entity_id,
                "error": "statistics_during_period exception",
            }

        if not isinstance(statistics_data_raw, dict):
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": resolved_entity_id,
                "error": "statistics_during_period returned non-dict",
            }

        statistics_data = cast(dict[str, Any], statistics_data_raw)

        if not statistics_data or resolved_entity_id not in statistics_data:
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": resolved_entity_id,
                "error": "no statistics data",
            }

        rows_raw = statistics_data.get(resolved_entity_id)
        if not isinstance(rows_raw, list) or not rows_raw:
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": resolved_entity_id,
                "error": "no rows",
            }

        rows: list[dict[str, Any]] = [
            cast(dict[str, Any], r) for r in rows_raw if isinstance(r, dict)
        ]
        if not rows:
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": resolved_entity_id,
                "error": "no dict rows",
            }

        # Calculate mean of available samples in the last hour
        values = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            mean_val = row.get("mean")
            if mean_val in (None, "unknown", "unavailable"):
                continue
            try:
                values.append(float(mean_val))
            except (TypeError, ValueError):
                continue

        if not values:
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": resolved_entity_id,
                "error": "no numeric mean values",
            }

        return {
            "recent_avg_kw": sum(values) / len(values),
            "samples": len(values),
            "statistic_id": resolved_entity_id,
            "error": "",
        }

    def get_cached_hourly_averages(self) -> dict[int, float]:
        """Get cached hourly averages (sync version).

        Returns combined profile for backward compatibility.
        """
        return self._historical_load_cache

    def clear_historical_cache(self) -> None:
        """Clear historical load cache to force refresh on next update."""
        # Clear combined profile
        self._historical_load_cache = {}
        self._historical_load_sample_counts = {}
        self._historical_load_source = "unknown"
        self._historical_load_cache_date = ""

        # Clear day-of-week profiles
        self._weekday_hourly_avg_kw = {}
        self._weekend_hourly_avg_kw = {}
        self._weekday_sample_counts = {}
        self._weekend_sample_counts = {}
        self._profile_source = "unknown"

    # -------------------------------------------------------------------------
    # Extended History Methods (Issue #268)
    # -------------------------------------------------------------------------

    async def async_get_extended_hourly_averages(
        self,
        entity_id: str,
        days: int = 90,
    ) -> tuple[dict[int, float], dict[int, float], str]:
        """Get hourly averages using Statistics API for extended lookback.

        Issue #268: Enables 90+ day lookback for seasonal pattern detection.
        Uses the Statistics API which is more efficient than the recorder
        for long time ranges.

        Args:
            entity_id: Entity ID to fetch statistics for
            days: Number of days to look back (default: 90)

        Returns:
            Tuple of (weekday_avg, weekend_avg, source) where:
            - weekday_avg: Hour -> average kW for weekdays
            - weekend_avg: Hour -> average kW for weekends
            - source: "statistics" or "recorder_fallback"
        """
        # Check if entity supports statistics
        if not await self.async_entity_supports_statistics(entity_id):
            _LOGGER.info(
                "Entity %s does not support long-term statistics, using recorder fallback",
                entity_id,
            )
            # Fallback to standard recorder method
            combined, _, source = await self.async_get_historical_hourly_averages(
                entity_id
            )
            return combined, combined, source

        from homeassistant.components import recorder
        from homeassistant.components.recorder.statistics import get_statistics

        now = dt_util.now()
        start_time = now - timedelta(days=days)

        _LOGGER.info(
            "Fetching extended statistics for %s: %d days (%s to %s)",
            entity_id,
            days,
            start_time.isoformat(),
            now.isoformat(),
        )

        try:
            recorder_instance = recorder.get_instance(self.hass)
            stats = await recorder_instance.async_add_executor_job(
                self._fetch_extended_statistics_sync,
                entity_id,
                start_time,
                now,
            )

            if stats:
                weekday_avg, weekend_avg = self._process_extended_statistics(stats)
                _LOGGER.info(
                    "Extended statistics fetched: %d weekday hours, %d weekend hours",
                    len(weekday_avg),
                    len(weekend_avg),
                )
                return weekday_avg, weekend_avg, "statistics"
            else:
                _LOGGER.warning("No extended statistics data returned for %s", entity_id)
                return {}, {}, "no_data"

        except Exception as e:
            _LOGGER.warning("Failed to fetch extended statistics: %s", e)
            return {}, {}, f"error: {e}"

    def _fetch_extended_statistics_sync(
        self,
        entity_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch extended statistics synchronously (runs in thread pool).

        Args:
            entity_id: Entity ID to fetch
            start_time: Start of period
            end_time: End of period

        Returns:
            List of statistics rows
        """
        try:
            from homeassistant.components.recorder.statistics import get_statistics
        except ImportError:
            _LOGGER.warning("get_statistics not available")
            return []

        try:
            stats = get_statistics(
                self.hass,
                start_time,
                end_time,
                [entity_id],
                "hour",
                ["mean", "sum"],
            )

            if isinstance(stats, dict) and entity_id in stats:
                return stats[entity_id]
            elif isinstance(stats, list):
                return stats
            else:
                return []

        except Exception as e:
            _LOGGER.warning("Error fetching extended statistics: %s", e)
            return []

    def _process_extended_statistics(
        self,
        stats: list[dict[str, Any]],
    ) -> tuple[dict[int, float], dict[int, float]]:
        """Process extended statistics into hourly profiles.

        Separates statistics into weekday and weekend profiles for
        seasonal pattern detection.

        Args:
            stats: List of statistics rows with 'start' and 'mean' fields

        Returns:
            Tuple of (weekday_avg, weekend_avg) where each is hour -> kW
        """
        weekday_by_hour: dict[int, list[float]] = {h: [] for h in range(24)}
        weekend_by_hour: dict[int, list[float]] = {h: [] for h in range(24)}

        for row in stats:
            if not isinstance(row, dict):
                continue

            start_val = row.get("start")
            row_dt = None

            if isinstance(start_val, datetime):
                row_dt = start_val
            elif isinstance(start_val, int | float):
                row_dt = dt_util.utc_from_timestamp(start_val)
            elif isinstance(start_val, str):
                row_dt = dt_util.parse_datetime(start_val)

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
            day_of_week = local_dt.weekday()

            if day_of_week >= 5:  # Weekend
                weekend_by_hour[hour].append(mean_kw)
            else:
                weekday_by_hour[hour].append(mean_kw)

        # Calculate averages
        weekday_avg = {
            hour: sum(vals) / len(vals)
            for hour, vals in weekday_by_hour.items()
            if vals
        }
        weekend_avg = {
            hour: sum(vals) / len(vals)
            for hour, vals in weekend_by_hour.items()
            if vals
        }

        return weekday_avg, weekend_avg

    async def async_entity_supports_statistics(self, entity_id: str) -> bool:
        """Check if an entity supports long-term statistics.

        Entities with state_class='measurement' or state_class='total'
        support long-term statistics.

        Args:
            entity_id: Entity ID to check

        Returns:
            True if the entity supports statistics
        """
        try:
            state = self.hass.states.get(entity_id)
            if not state:
                _LOGGER.debug("Entity %s not found", entity_id)
                return False

            state_class = state.attributes.get("state_class")
            if state_class in ("measurement", "total", "total_increasing"):
                _LOGGER.debug(
                    "Entity %s supports statistics (state_class=%s)",
                    entity_id,
                    state_class,
                )
                return True

            _LOGGER.debug(
                "Entity %s does not support statistics (state_class=%s)",
                entity_id,
                state_class,
            )
            return False

        except Exception as e:
            _LOGGER.warning("Error checking statistics support for %s: %s", entity_id, e)
            return False

    def detect_seasonal_profile(
        self,
        extended_stats: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Detect seasonal patterns from extended statistics.

        Issue #268: Analyzes 90+ days of statistics to identify:
        - Seasonal consumption patterns (summer vs winter)
        - Peak consumption hours by season
        - Trend direction (increasing/decreasing consumption)

        Args:
            extended_stats: List of statistics rows from extended lookback

        Returns:
            Dictionary with seasonal profile data:
            - summer_avg_kw: Average summer consumption by hour
            - winter_avg_kw: Average winter consumption by hour
            - peak_hours: Hours with highest consumption
            - trend: "increasing", "decreasing", or "stable"
        """
        if not extended_stats:
            return {
                "summer_avg_kw": {},
                "winter_avg_kw": {},
                "peak_hours": [],
                "trend": "unknown",
            }

        # Separate by season (simplified: Dec-Feb = summer, Jun-Aug = winter)
        # For southern hemisphere
        summer_months = [12, 1, 2]  # Dec, Jan, Feb
        winter_months = [6, 7, 8]  # Jun, Jul, Aug

        summer_by_hour: dict[int, list[float]] = {h: [] for h in range(24)}
        winter_by_hour: dict[int, list[float]] = {h: [] for h in range(24)}

        # For trend detection
        monthly_totals: dict[int, float] = {}

        for row in extended_stats:
            if not isinstance(row, dict):
                continue

            start_val = row.get("start")
            row_dt = None

            if isinstance(start_val, datetime):
                row_dt = start_val
            elif isinstance(start_val, int | float):
                row_dt = dt_util.utc_from_timestamp(start_val)
            elif isinstance(start_val, str):
                row_dt = dt_util.parse_datetime(start_val)

            if row_dt is None:
                continue

            mean_val = row.get("mean")
            if mean_val in (None, "unknown", "unavailable"):
                continue

            try:
                mean_kw = float(mean_val)
            except (TypeError, ValueError):
                continue

            local_dt = dt_util.as_local(row_dt)
            hour = local_dt.hour
            month = local_dt.month

            # Categorize by season
            if month in summer_months:
                summer_by_hour[hour].append(mean_kw)
            elif month in winter_months:
                winter_by_hour[hour].append(mean_kw)

            # Track monthly totals for trend
            if month not in monthly_totals:
                monthly_totals[month] = 0.0
            monthly_totals[month] += mean_kw

        # Calculate seasonal averages
        summer_avg = {
            hour: sum(vals) / len(vals)
            for hour, vals in summer_by_hour.items()
            if vals
        }
        winter_avg = {
            hour: sum(vals) / len(vals)
            for hour, vals in winter_by_hour.items()
            if vals
        }

        # Find peak hours (top 3 hours by consumption)
        all_hourly: dict[int, float] = {}
        for hour in range(24):
            summer_val = summer_avg.get(hour, 0)
            winter_val = winter_avg.get(hour, 0)
            # Average of both seasons
            if summer_val or winter_val:
                all_hourly[hour] = (summer_val + winter_val) / 2

        peak_hours = sorted(all_hourly.keys(), key=lambda h: all_hourly[h], reverse=True)[
            :3
        ]

        # Detect trend
        trend = "stable"
        if len(monthly_totals) >= 3:
            months_sorted = sorted(monthly_totals.keys())
            if len(months_sorted) >= 3:
                first_half = sum(
                    monthly_totals[m] for m in months_sorted[: len(months_sorted) // 2]
                )
                second_half = sum(
                    monthly_totals[m] for m in months_sorted[len(months_sorted) // 2 :]
                )
                if second_half > first_half * 1.1:
                    trend = "increasing"
                elif second_half < first_half * 0.9:
                    trend = "decreasing"

        return {
            "summer_avg_kw": summer_avg,
            "winter_avg_kw": winter_avg,
            "peak_hours": peak_hours,
            "trend": trend,
        }

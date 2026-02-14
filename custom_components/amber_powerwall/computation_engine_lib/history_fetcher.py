"""Historical load data fetching and caching.

This module handles fetching historical load data from Home Assistant's
recorder/statistics database for consumption forecasting.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


class HistoryFetcher:
    """Fetches and caches historical load data from HA statistics."""

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

        # Historical load cache (hourly averages)
        self._historical_load_cache: dict[int, float] = {}
        self._historical_load_sample_counts: dict[int, int] = {}
        self._historical_load_source: str = "unknown"
        self._historical_load_cache_date: str = ""

        # Recent load cache (1-hour average)
        self._recent_load_1hr_kw: float = 0.0
        self._recent_load_cache_time: datetime | None = None
        self._recent_load_1hr_statistic_id: str = ""
        self._recent_load_1hr_samples: int = 0
        self._recent_load_1hr_last_error: str = ""

    async def async_get_historical_hourly_averages(
        self, entity_id: str
    ) -> tuple[dict[int, float], dict[int, int], str]:
        """Get 7-day hourly averages via thread pool, cached until midnight.

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
        hourly_avg_kw, sample_counts = await recorder_instance.async_add_executor_job(
            self._fetch_historical_data_sync, entity_id, now
        )

        _LOGGER.info(
            "Historical data result: %s hours found",
            len(hourly_avg_kw) if hourly_avg_kw else 0,
        )

        if hourly_avg_kw and len(hourly_avg_kw) >= 6:
            self._historical_load_cache = hourly_avg_kw
            self._historical_load_sample_counts = sample_counts
            self._historical_load_source = "statistics"
            self._historical_load_cache_date = today_str
            _LOGGER.debug(
                "Historical load profile fetched: %s hours", len(hourly_avg_kw)
            )
        else:
            self._historical_load_source = "live_load_fallback"
            _LOGGER.debug(
                "Using live load fallback (insufficient history: %s hours)",
                len(hourly_avg_kw) if hourly_avg_kw else 0,
            )

        return (
            self._historical_load_cache,
            self._historical_load_sample_counts,
            self._historical_load_source,
        )

    def _fetch_historical_data_sync(
        self, entity_id: str, now: datetime
    ) -> tuple[dict[int, float], dict[int, int]]:
        """Fetch historical data using HA recorder/statistics (runs in thread pool)."""
        start_time = now - timedelta(days=7)

        try:
            from homeassistant.components.recorder import (
                statistics as recorder_statistics,
            )
        except Exception as e:
            _LOGGER.info("Failed to import recorder statistics: %s", e)
            return {}, {}

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
        resolved_entity_id = entity_id
        for sid in stat_ids:
            if not isinstance(sid, dict):
                continue
            stat_id = sid.get("statistic_id", "")
            if stat_id == entity_id or stat_id.replace(
                "sensor.", ""
            ) == entity_id.replace("sensor.", ""):
                resolved_entity_id = stat_id
                break

        # Get statistics
        fn = getattr(recorder_statistics, "statistics_during_period", None)
        if not callable(fn):
            return {}, {}

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
            return {}, {}

        if not isinstance(statistics_data_raw, dict):
            return {}, {}

        statistics_data = cast(dict[str, Any], statistics_data_raw)

        if not statistics_data or resolved_entity_id not in statistics_data:
            return {}, {}

        rows_raw = statistics_data.get(resolved_entity_id)
        if not isinstance(rows_raw, list) or not rows_raw:
            return {}, {}

        rows: list[dict[str, Any]] = [
            cast(dict[str, Any], r) for r in rows_raw if isinstance(r, dict)
        ]
        if not rows:
            return {}, {}

        # Process statistics into hourly averages
        by_hour_values: dict[int, list[float]] = {h: [] for h in range(24)}
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

            hour = dt_util.as_local(row_dt).hour
            by_hour_values[hour].append(mean_kw)

        hourly_avg_kw: dict[int, float] = {}
        sample_counts: dict[int, int] = {}
        for hour in range(24):
            samples = by_hour_values[hour]
            if not samples:
                continue
            sample_counts[hour] = len(samples)
            hourly_avg_kw[hour] = sum(samples) / len(samples)

        return hourly_avg_kw, sample_counts

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
        from homeassistant.components.recorder import statistics as recorder_statistics

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

        resolved_entity_id = entity_id
        for sid in stat_ids:
            if not isinstance(sid, dict):
                continue
            stat_id = sid.get("statistic_id", "")
            if stat_id == entity_id or stat_id.replace(
                "sensor.", ""
            ) == entity_id.replace("sensor.", ""):
                resolved_entity_id = stat_id
                break

        if not resolved_entity_id:
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": "",
                "error": "empty statistic_id",
            }

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
                period="hour",
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
        """Get cached hourly averages (sync version)."""
        return self._historical_load_cache

    def get_consumption_source(self, hourly_avg_kw: dict[int, float]) -> str:
        """Get consumption source for diagnostics."""
        return self._historical_load_source if hourly_avg_kw else "live_load_fallback"

    def get_sample_counts(self) -> dict[int, int]:
        """Get sample counts for diagnostics."""
        return dict(self._historical_load_sample_counts)

    def get_recent_load_info(self) -> tuple[float, str, int, str]:
        """Get recent load info for diagnostics."""
        return (
            self._recent_load_1hr_kw,
            self._recent_load_1hr_statistic_id,
            self._recent_load_1hr_samples,
            self._recent_load_1hr_last_error,
        )

    def clear_historical_cache(self) -> None:
        """Clear historical load cache to force refresh on next update."""
        self._historical_load_cache = {}
        self._historical_load_sample_counts = {}
        self._historical_load_source = "unknown"
        self._historical_load_cache_date = ""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

_LOGGER = logging.getLogger(__name__)

MIN_CORRECTION_SAMPLES = 10
CORRECTION_CLAMP_MIN = 0.5
CORRECTION_CLAMP_MAX = 1.5


@dataclass
class ContextErrorStats:
    mean_ratio: float = 1.0
    sample_count: int = 0
    last_updated: str = ""

    def record(self, actual_kw: float, forecast_kw: float) -> None:
        if forecast_kw <= 0:
            return
        ratio = actual_kw / forecast_kw
        self.sample_count += 1
        self.mean_ratio += (ratio - self.mean_ratio) / self.sample_count
        self.last_updated = datetime.now().isoformat()

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "mean_ratio": self.mean_ratio,
            "sample_count": self.sample_count,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ContextErrorStats:
        return cls(
            mean_ratio=data.get("mean_ratio", 1.0),
            sample_count=data.get("sample_count", 0),
            last_updated=data.get("last_updated", ""),
        )


class ForecastCorrectionProvider:
    def __init__(self, min_samples: int = MIN_CORRECTION_SAMPLES) -> None:
        self._stats: dict[str, ContextErrorStats] = {}
        self._min_samples = min_samples

    @staticmethod
    def _make_key(day_of_week: int, hour_of_day: int, season: str) -> str:
        return f"{day_of_week}:{hour_of_day}:{season}"

    def record_error(
        self,
        actual_kw: float,
        forecast_kw: float,
        day_of_week: int,
        hour_of_day: int,
        season: str,
    ) -> None:
        key = self._make_key(day_of_week, hour_of_day, season)
        if key not in self._stats:
            self._stats[key] = ContextErrorStats()
        self._stats[key].record(actual_kw, forecast_kw)

    def get_correction_factor(
        self, day_of_week: int, hour_of_day: int, season: str
    ) -> float:
        key = self._make_key(day_of_week, hour_of_day, season)
        stats = self._stats.get(key)
        if stats is None or stats.sample_count < self._min_samples:
            return 1.0
        return max(CORRECTION_CLAMP_MIN, min(CORRECTION_CLAMP_MAX, stats.mean_ratio))

    def get_stats_summary(self) -> dict[str, dict[str, float | int | bool]]:
        return {
            key: {
                "mean_ratio": value.mean_ratio,
                "sample_count": value.sample_count,
                "active": value.sample_count >= self._min_samples,
            }
            for key, value in self._stats.items()
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "stats": {key: value.to_dict() for key, value in self._stats.items()},
            "min_samples": self._min_samples,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ForecastCorrectionProvider:
        provider = cls(min_samples=data.get("min_samples", MIN_CORRECTION_SAMPLES))
        for key, value in data.get("stats", {}).items():
            provider._stats[key] = ContextErrorStats.from_dict(value)
        return provider

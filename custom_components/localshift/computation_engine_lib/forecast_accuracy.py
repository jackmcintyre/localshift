"""Forecast accuracy tracking helpers for computation engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

# Avoid circular import - only import for type hints
if TYPE_CHECKING:
    from ..coordinator_data import CoordinatorData

_LOGGER = logging.getLogger(__name__)


@dataclass
class ExtendedAccuracyMetrics:
    """Extended forecast accuracy metrics for long-term tracking.

    Issue #270: Multi-horizon forecast validation with bias detection.
    """

    accuracy_24h: float = 100.0
    accuracy_7d: float = 100.0
    accuracy_30d: float = 100.0
    bias: float = 0.0  # Systematic over/under prediction (percentage points)
    mape: float = 0.0  # Mean Absolute Percentage Error
    sample_count: int = 0
    last_updated: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "accuracy_24h": self.accuracy_24h,
            "accuracy_7d": self.accuracy_7d,
            "accuracy_30d": self.accuracy_30d,
            "bias": self.bias,
            "mape": self.mape,
            "sample_count": self.sample_count,
            "last_updated": self.last_updated.isoformat()
            if self.last_updated
            else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtendedAccuracyMetrics:
        """Create from dictionary (deserialization)."""
        last_updated = None
        if data.get("last_updated"):
            try:
                last_updated = datetime.fromisoformat(data["last_updated"])
            except (ValueError, TypeError):
                pass
        return cls(
            accuracy_24h=data.get("accuracy_24h", 100.0),
            accuracy_7d=data.get("accuracy_7d", 100.0),
            accuracy_30d=data.get("accuracy_30d", 100.0),
            bias=data.get("bias", 0.0),
            mape=data.get("mape", 0.0),
            sample_count=data.get("sample_count", 0),
            last_updated=last_updated,
        )


class ForecastAccuracyEngine:
    """Compare stored forecast predictions with current actual values."""

    async def compute_forecast_accuracy(self, data: CoordinatorData) -> None:
        """Compare past forecast predictions with actual outcomes."""
        now_dt = dt_util.now()

        if not hasattr(data, "forecast_error_soc_15min"):
            data.forecast_error_soc_15min = 0.0
        if not hasattr(data, "forecast_error_soc_1h"):
            data.forecast_error_soc_1h = 0.0
        if not hasattr(data, "forecast_error_soc_4h"):
            data.forecast_error_soc_4h = 0.0
        if not hasattr(data, "forecast_accuracy_soc_15min"):
            data.forecast_accuracy_soc_15min = 100.0
        if not hasattr(data, "forecast_accuracy_soc_1h"):
            data.forecast_accuracy_soc_1h = 100.0
        if not hasattr(data, "forecast_accuracy_soc_4h"):
            data.forecast_accuracy_soc_4h = 100.0
        if not hasattr(data, "forecast_error_buy_price_1h"):
            data.forecast_error_buy_price_1h = 0.0
        if not hasattr(data, "forecast_error_sell_price_1h"):
            data.forecast_error_sell_price_1h = 0.0

        data.forecast_last_comparison_time = now_dt.isoformat()

        try:
            actual_soc = data.soc
            actual_buy_price = data.general_price
            actual_sell_price = data.feed_in_price
            history = data.forecast_history

            comparisons_found = {15: False, 60: False, 240: False}

            for entry in history:
                if "offset_minutes" not in entry:
                    continue

                target_time_str = entry.get("target_time")
                if not target_time_str:
                    continue

                try:
                    target_dt = datetime.fromisoformat(target_time_str)
                except ValueError:
                    continue

                if target_dt.tzinfo is None:
                    target_dt = dt_util.as_local(dt_util.as_utc(target_dt))
                else:
                    target_dt = dt_util.as_local(target_dt)

                time_diff = abs((target_dt - now_dt).total_seconds())
                if time_diff > 60:
                    continue

                offset = entry.get("offset_minutes")
                predicted_soc = entry.get("predicted_soc")
                predicted_buy = entry.get("predicted_buy_price", actual_buy_price)
                predicted_sell = entry.get("predicted_sell_price", actual_sell_price)

                if predicted_soc is None:
                    continue

                soc_error = predicted_soc - actual_soc

                if offset == 15:
                    data.forecast_error_soc_15min = round(soc_error, 1)
                    data.forecast_accuracy_soc_15min = max(
                        0.0, min(100.0, 100.0 - abs(soc_error))
                    )
                    comparisons_found[15] = True
                elif offset == 60:
                    data.forecast_error_soc_1h = round(soc_error, 1)
                    data.forecast_accuracy_soc_1h = max(
                        0.0, min(100.0, 100.0 - abs(soc_error))
                    )
                    data.forecast_error_buy_price_1h = round(
                        predicted_buy - actual_buy_price, 4
                    )
                    data.forecast_error_sell_price_1h = round(
                        predicted_sell - actual_sell_price, 4
                    )
                    comparisons_found[60] = True
                elif offset == 240:
                    data.forecast_error_soc_4h = round(soc_error, 1)
                    data.forecast_accuracy_soc_4h = max(
                        0.0, min(100.0, 100.0 - abs(soc_error))
                    )
                    comparisons_found[240] = True

            if any(comparisons_found.values()):
                data.forecast_comparisons_made += 1

                _LOGGER.info(
                    "Forecast accuracy: 15min=%.1f%% (err=%.1f), 1h=%.1f%% (err=%.1f), 4h=%.1f%% (err=%.1f), comparisons=%d, found=%s",
                    data.forecast_accuracy_soc_15min,
                    data.forecast_error_soc_15min,
                    data.forecast_accuracy_soc_1h,
                    data.forecast_error_soc_1h,
                    data.forecast_accuracy_soc_4h,
                    data.forecast_error_soc_4h,
                    data.forecast_comparisons_made,
                    comparisons_found,
                )
            else:
                _LOGGER.debug(
                    "No forecast predictions found for comparison at %s (history has %d entries)",
                    now_dt.strftime("%H:%M"),
                    len(history),
                )

        except Exception as err:
            _LOGGER.warning("Failed to compute forecast accuracy: %s", err)


class ExtendedForecastAccuracyEngine:
    """Extended forecast accuracy tracking with long-term metrics.

    Issue #270: Multi-horizon validation with bias detection.
    """

    def __init__(self, storage_path: str | None = None) -> None:
        """Initialize the extended accuracy engine.

        Args:
            storage_path: Path to store accuracy history (optional)
        """
        self.storage_path = storage_path
        self._accuracy_history: list[dict[str, Any]] = []
        self._metrics = ExtendedAccuracyMetrics()

    @property
    def metrics(self) -> ExtendedAccuracyMetrics:
        """Return current accuracy metrics."""
        return self._metrics

    async def compute_extended_accuracy(
        self,
        data: CoordinatorData,
        _history_fetcher: Any | None = None,
    ) -> ExtendedAccuracyMetrics:
        """Compute extended accuracy metrics from historical data.

        Args:
            data: Current coordinator data
            history_fetcher: Optional history fetcher for statistics (unused, kept for future use)

        Returns:
            ExtendedAccuracyMetrics with computed values
        """
        now = dt_util.now()

        # Collect recent errors from forecast history
        errors: list[float] = []
        history = data.forecast_history

        for entry in history:
            predicted = entry.get("predicted_soc")
            actual = entry.get("actual_soc")
            if predicted is not None and actual is not None:
                errors.append(predicted - actual)

        if not errors:
            self._metrics.sample_count = 0
            return self._metrics

        # Calculate metrics
        self._metrics.sample_count = len(errors)

        # Mean error (bias)
        mean_error = sum(errors) / len(errors)
        self._metrics.bias = round(mean_error, 2)

        # Mean Absolute Percentage Error
        total_mape = 0.0
        for entry in history:
            predicted = entry.get("predicted_soc")
            actual = entry.get("actual_soc")
            if predicted is not None and actual is not None and actual > 0:
                total_mape += abs(predicted - actual) / actual * 100
        self._metrics.mape = round(total_mape / len(errors), 2) if errors else 0.0

        # Accuracy estimates (simplified - would need actual history for real values)
        self._metrics.accuracy_24h = max(0.0, min(100.0, 100.0 - abs(mean_error)))
        self._metrics.accuracy_7d = max(0.0, min(100.0, 100.0 - abs(mean_error) * 1.1))
        self._metrics.accuracy_30d = max(0.0, min(100.0, 100.0 - abs(mean_error) * 1.2))

        self._metrics.last_updated = now

        _LOGGER.info(
            "Extended accuracy: 24h=%.1f%%, 7d=%.1f%%, 30d=%.1f%%, bias=%.2f, mape=%.2f%%, samples=%d",
            self._metrics.accuracy_24h,
            self._metrics.accuracy_7d,
            self._metrics.accuracy_30d,
            self._metrics.bias,
            self._metrics.mape,
            self._metrics.sample_count,
        )

        return self._metrics

    def to_dict(self) -> dict[str, Any]:
        """Serialize engine state."""
        return {
            "metrics": self._metrics.to_dict(),
            "history_count": len(self._accuracy_history),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtendedForecastAccuracyEngine:
        """Deserialize engine state."""
        engine = cls()
        if "metrics" in data:
            engine._metrics = ExtendedAccuracyMetrics.from_dict(data["metrics"])
        return engine

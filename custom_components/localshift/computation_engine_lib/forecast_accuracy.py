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
        self._ensure_accuracy_fields(data)
        now_dt = dt_util.now()
        data.forecast_last_comparison_time = now_dt.isoformat()

        try:
            comparisons = self._process_history_for_comparisons(data, now_dt)
            self._apply_comparison_results(data, comparisons)
        except Exception as err:
            _LOGGER.warning("Failed to compute forecast accuracy: %s", err)

    def _ensure_accuracy_fields(self, data: CoordinatorData) -> None:
        """Initialize accuracy tracking fields if not present.

        Args:
            data: CoordinatorData instance
        """
        fields_defaults = [
            ("forecast_error_soc_15min", 0.0),
            ("forecast_error_soc_1h", 0.0),
            ("forecast_error_soc_4h", 0.0),
            ("forecast_accuracy_soc_15min", None),
            ("forecast_accuracy_soc_1h", None),
            ("forecast_accuracy_soc_4h", None),
            ("forecast_error_buy_price_1h", 0.0),
            ("forecast_error_sell_price_1h", 0.0),
        ]
        for field, default in fields_defaults:
            if not hasattr(data, field):
                setattr(data, field, default)

    def _process_history_for_comparisons(
        self, data: CoordinatorData, now_dt: datetime
    ) -> dict[int, dict | None]:
        """Process history entries and return comparisons by offset.

        Args:
            data: CoordinatorData instance
            now_dt: Current datetime

        Returns:
            Dict mapping offset (15, 60, 240) to comparison result dict
        """
        actual_soc = data.soc
        actual_buy_price = data.general_price
        actual_sell_price = data.feed_in_price
        history = data.forecast_history

        comparisons: dict[int, dict | None] = {15: None, 60: None, 240: None}

        for entry in history:
            result = self._process_single_entry(
                entry, now_dt, actual_soc, actual_buy_price, actual_sell_price
            )
            if result is not None and result["offset"] in comparisons:
                comparisons[result["offset"]] = result

        return comparisons

    def _process_single_entry(
        self,
        entry: dict,
        now_dt: datetime,
        actual_soc: float,
        actual_buy_price: float,
        actual_sell_price: float,
    ) -> dict | None:
        """Process a single history entry for comparison.

        Args:
            entry: History entry dict
            now_dt: Current datetime
            actual_soc: Actual SOC
            actual_buy_price: Actual buy price
            actual_sell_price: Actual sell price

        Returns:
            Comparison result dict or None
        """
        if "offset_minutes" not in entry:
            return None

        target_time_str = entry.get("target_time")
        if not target_time_str:
            return None

        try:
            target_dt = datetime.fromisoformat(target_time_str)
        except ValueError:
            return None

        if target_dt.tzinfo is None:
            target_dt = dt_util.as_local(dt_util.as_utc(target_dt))
        else:
            target_dt = dt_util.as_local(target_dt)

        time_diff = abs((target_dt - now_dt).total_seconds())
        if time_diff > 300:  # 5 minute window to tolerate scheduling drift
            return None

        offset = entry.get("offset_minutes")
        predicted_soc = entry.get("predicted_soc")
        if predicted_soc is None:
            return None

        predicted_buy = entry.get("predicted_buy_price", actual_buy_price)
        predicted_sell = entry.get("predicted_sell_price", actual_sell_price)
        soc_error = predicted_soc - actual_soc

        return {
            "offset": offset,
            "soc_error": soc_error,
            "predicted_buy": predicted_buy,
            "predicted_sell": predicted_sell,
            "actual_buy": actual_buy_price,
            "actual_sell": actual_sell_price,
        }

    def _apply_comparison_results(
        self, data: CoordinatorData, comparisons: dict[int, dict | None]
    ) -> None:
        """Apply comparison results to data.

        Args:
            data: CoordinatorData instance
            comparisons: Dict of comparison results by offset
        """
        found_15 = self._apply_offset_result(data, comparisons.get(15), 15)
        found_60 = self._apply_offset_result(data, comparisons.get(60), 60)
        found_240 = self._apply_offset_result(data, comparisons.get(240), 240)

        if found_15 or found_60 or found_240:
            data.forecast_comparisons_made += 1
            _LOGGER.info(
                "Forecast accuracy: 15min=%.1f%% (err=%.1f), 1h=%.1f%% (err=%.1f), 4h=%.1f%% (err=%.1f), comparisons=%d",
                data.forecast_accuracy_soc_15min,
                data.forecast_error_soc_15min,
                data.forecast_accuracy_soc_1h,
                data.forecast_error_soc_1h,
                data.forecast_accuracy_soc_4h,
                data.forecast_error_soc_4h,
                data.forecast_comparisons_made,
            )
        else:
            _LOGGER.debug(
                "No forecast predictions found for comparison (history has %d entries)",
                len(data.forecast_history),
            )

    def _apply_offset_result(
        self, data: CoordinatorData, result: dict | None, offset: int
    ) -> bool:
        """Apply comparison result for a specific offset.

        Args:
            data: CoordinatorData instance
            result: Comparison result dict or None
            offset: Offset in minutes (15, 60, or 240)

        Returns:
            True if result was applied
        """
        if result is None:
            return False

        soc_error = result["soc_error"]
        if offset == 15:
            data.forecast_error_soc_15min = round(soc_error, 1)
            data.forecast_accuracy_soc_15min = max(
                0.0, min(100.0, 100.0 - abs(soc_error))
            )
            return True
        elif offset == 60:
            data.forecast_error_soc_1h = round(soc_error, 1)
            data.forecast_accuracy_soc_1h = max(0.0, min(100.0, 100.0 - abs(soc_error)))
            data.forecast_error_buy_price_1h = round(
                result["predicted_buy"] - result["actual_buy"], 4
            )
            data.forecast_error_sell_price_1h = round(
                result["predicted_sell"] - result["actual_sell"], 4
            )
            return True
        elif offset == 240:
            data.forecast_error_soc_4h = round(soc_error, 1)
            data.forecast_accuracy_soc_4h = max(0.0, min(100.0, 100.0 - abs(soc_error)))
            return True
        return False


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

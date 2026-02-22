"""Forecast accuracy tracking helpers for computation engine."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.util import dt as dt_util

from ..coordinator_data import CoordinatorData

_LOGGER = logging.getLogger(__name__)


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

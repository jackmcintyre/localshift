"""Forecast change tracking helpers for computation engine."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

_LOGGER = logging.getLogger(__name__)


class ForecastChangeTracker:
    """Tracks when forecast should regenerate based on significant changes.

    Uses price sensor update timestamps to ensure ONE decision per price block.
    This prevents decision flip-flopping within the same 5-minute price period.
    """

    def __init__(self) -> None:
        """Initialize change tracker."""
        self._last_soc: float = -1.0  # -1 = not initialized
        self._last_price: float = -1.0
        self._last_feed_in: float = -1.0
        self._last_forecast_time: datetime | None = None

        # Price sensor update timestamps (key for stable decisions)
        self._last_price_update: datetime | None = None

        # Change thresholds (hardcoded, no config needed)
        self._SOC_THRESHOLD = 1.0  # 1% SOC change
        self._MAX_FORECAST_AGE = timedelta(
            minutes=10
        )  # Backup timer (increased from 1 min)

    def should_recompute_forecast(
        self,
        soc: float,
        price: float,
        feed_in_price: float,
        now_dt: datetime,
        price_update_time: datetime | None = None,
        force: bool = False,
    ) -> tuple[bool, str]:
        """Check if forecast should recompute.

        Uses price sensor update timestamp as the primary trigger for recomputation.
        This ensures ONE decision per price block, preventing flip-flopping.

        Args:
            soc: Current battery SOC percentage.
            price: Current buy price ($/kWh).
            feed_in_price: Current feed-in price ($/kWh).
            now_dt: Current datetime.
            price_update_time: Timestamp when price sensor last updated.
                               If provided, this is the PRIMARY trigger for recompute.
            force: If True, skip checks and recompute.

        Returns:
            Tuple of ``(should_recompute, reason)``.
        """
        # Force recompute (e.g., mode change, startup)
        if force:
            self._update_cache(soc, price, feed_in_price, now_dt, price_update_time)
            return True, "forced"

        # First run: no cached values
        if self._last_soc < 0:
            self._update_cache(soc, price, feed_in_price, now_dt, price_update_time)
            return True, "first_run"

        # PRIMARY TRIGGER: Price sensor update timestamp changed
        # This is the key fix - recompute when the price BLOCK changes,
        # not when the price VALUE changes (which may stay the same across blocks)
        if price_update_time is not None and self._last_price_update is not None:
            if price_update_time > self._last_price_update:
                reason = f"price_block_update_{price_update_time.isoformat()}"
                self._update_cache(soc, price, feed_in_price, now_dt, price_update_time)
                return True, reason
        elif price_update_time is not None and self._last_price_update is None:
            # First time we have a price update time
            self._update_cache(soc, price, feed_in_price, now_dt, price_update_time)
            return True, "first_price_update_time"

        # FALLBACK: Price VALUE changes (for backwards compatibility)
        # This handles cases where price_update_time is not available
        if price != self._last_price:
            reason = f"price_change_{price:.2f}"
            self._update_cache(soc, price, feed_in_price, now_dt, price_update_time)
            return True, reason

        if feed_in_price != self._last_feed_in:
            reason = f"fit_change_{feed_in_price:.2f}"
            self._update_cache(soc, price, feed_in_price, now_dt, price_update_time)
            return True, reason

        # SOC change - ONLY recompute if price sensor update time is unavailable
        # When price_update_time IS available, SOC changes within a price block
        # should NOT trigger recompute (prevents flip-flopping)
        if price_update_time is None:
            soc_change = abs(soc - self._last_soc)
            if soc_change >= self._SOC_THRESHOLD:
                reason = f"soc_change_{soc_change:.1f}%"
                self._update_cache(soc, price, feed_in_price, now_dt, price_update_time)
                return True, reason

        # Age check (backup timer - increased to 10 minutes)
        # This ensures we eventually recompute even if price sensor stops updating
        if self._last_forecast_time is not None:
            age = now_dt - self._last_forecast_time
            if age > self._MAX_FORECAST_AGE:
                reason = f"age_{age.total_seconds():.0f}s"
                self._update_cache(soc, price, feed_in_price, now_dt, price_update_time)
                return True, reason

        # No significant changes
        return False, "no_change"

    def _update_cache(
        self,
        soc: float,
        price: float,
        feed_in_price: float,
        now_dt: datetime,
        price_update_time: datetime | None = None,
    ) -> None:
        """Update cached values after recompute."""
        self._last_soc = soc
        self._last_price = price
        self._last_feed_in = feed_in_price
        self._last_forecast_time = now_dt
        if price_update_time is not None:
            self._last_price_update = price_update_time

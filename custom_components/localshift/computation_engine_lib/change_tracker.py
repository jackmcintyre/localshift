"""Forecast change tracking helpers for computation engine."""

from __future__ import annotations

from datetime import datetime, timedelta


class ForecastChangeTracker:
    """Tracks when forecast should regenerate based on significant changes."""

    def __init__(self) -> None:
        """Initialize change tracker."""
        self._last_soc: float = -1.0  # -1 = not initialized
        self._last_price: float = -1.0
        self._last_feed_in: float = -1.0
        self._last_forecast_time: datetime | None = None

        # Change thresholds (hardcoded, no config needed)
        self._SOC_THRESHOLD = 1.0  # 1% SOC change
        self._MAX_FORECAST_AGE = timedelta(minutes=1)

    def should_recompute_forecast(
        self,
        soc: float,
        price: float,
        feed_in_price: float,
        now_dt: datetime,
        force: bool = False,
    ) -> tuple[bool, str]:
        """Check if forecast should recompute.

        Args:
            soc: Current battery SOC percentage.
            price: Current buy price ($/kWh).
            feed_in_price: Current feed-in price ($/kWh).
            now_dt: Current datetime.
            force: If True, skip checks and recompute.

        Returns:
            Tuple of ``(should_recompute, reason)``.
        """
        # Force recompute (e.g., mode change, startup)
        if force:
            self._update_cache(soc, price, feed_in_price, now_dt)
            return True, "forced"

        # First run: no cached values
        if self._last_soc < 0:
            self._update_cache(soc, price, feed_in_price, now_dt)
            return True, "first_run"

        # Price changes (ANY change = recalc)
        if price != self._last_price:
            reason = f"price_change_{price:.2f}"
            self._update_cache(soc, price, feed_in_price, now_dt)
            return True, reason

        if feed_in_price != self._last_feed_in:
            reason = f"fit_change_{feed_in_price:.2f}"
            self._update_cache(soc, price, feed_in_price, now_dt)
            return True, reason

        # SOC change (1% threshold)
        soc_change = abs(soc - self._last_soc)
        if soc_change >= self._SOC_THRESHOLD:
            reason = f"soc_change_{soc_change:.1f}%"
            self._update_cache(soc, price, feed_in_price, now_dt)
            return True, reason

        # Age check (1-minute backup timer)
        if self._last_forecast_time is not None:
            age = now_dt - self._last_forecast_time
            if age > self._MAX_FORECAST_AGE:
                reason = f"age_{age.total_seconds():.0f}s"
                self._update_cache(soc, price, feed_in_price, now_dt)
                return True, reason

        # No significant changes
        return False, "no_change"

    def _update_cache(
        self,
        soc: float,
        price: float,
        feed_in_price: float,
        now_dt: datetime,
    ) -> None:
        """Update cached values after recompute."""
        self._last_soc = soc
        self._last_price = price
        self._last_feed_in = feed_in_price
        self._last_forecast_time = now_dt

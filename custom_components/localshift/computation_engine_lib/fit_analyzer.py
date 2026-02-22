"""FIT price analysis helpers for forecast computation."""

from __future__ import annotations

from datetime import datetime, timedelta

from .solar_utils import get_price_for_slot, get_price_for_slot_or_none


class FitAnalyzer:
    """Analyze feed-in tariff forecasts for export strategy."""

    def _find_negative_fit_windows(
        self, feed_in_forecast: list[dict], start_time: datetime, max_hours: int = 24
    ) -> list[tuple[datetime, datetime, float]]:
        """Find windows where feed-in price ≤ 0.

        Args:
            feed_in_forecast: Feed-in price forecast
            start_time: Start time for search
            max_hours: How many hours to search ahead

        Returns:
            List of (window_start, window_end, min_price) tuples
        """
        negative_windows = []
        base_slot = start_time.replace(minute=0, second=0, microsecond=0)
        current_window_start = None
        min_price_in_window = 0.0

        for offset in range(max_hours * 12):  # 5-min intervals = 12 per hour
            slot_time = base_slot + timedelta(minutes=5 * offset)
            price = get_price_for_slot_or_none(feed_in_forecast, slot_time)

            if price is not None and price <= 0:
                if current_window_start is None:
                    current_window_start = slot_time
                    min_price_in_window = price
                else:
                    min_price_in_window = min(min_price_in_window, price)
            elif current_window_start is not None:
                # Window ended
                negative_windows.append(
                    (current_window_start, slot_time, min_price_in_window)
                )
                current_window_start = None
                min_price_in_window = 0.0

        # Close any open window
        if current_window_start is not None:
            negative_windows.append(
                (
                    current_window_start,
                    base_slot + timedelta(minutes=5 * max_hours * 12),
                    min_price_in_window,
                )
            )

        return negative_windows

    def _calculate_average_fit_price(
        self, feed_in_forecast: list[dict], start_time: datetime, hours: int = 24
    ) -> float:
        """Calculate average FIT price over forecast window.

        Args:
            feed_in_forecast: Feed-in price forecast
            start_time: Start time for calculation
            hours: How many hours to include

        Returns:
            Average FIT price, or 0.0 if no data
        """
        prices = []
        base_slot = start_time.replace(minute=0, second=0, microsecond=0)

        for offset in range(hours * 12):  # 5-min intervals
            slot_time = base_slot + timedelta(minutes=5 * offset)
            price = get_price_for_slot(feed_in_forecast, slot_time)
            if price is not None:
                prices.append(price)

        if not prices:
            return 0.0

        return sum(prices) / len(prices)

    def _calculate_percentile_fit_price(
        self,
        feed_in_forecast: list[dict],
        start_time: datetime,
        percentile: float = 60.0,
        hours: int = 24,
    ) -> float:
        """Calculate Nth percentile FIT price over forecast window.

        Excludes bottom percentile of prices to identify reasonable export windows.
        E.g., 60th percentile excludes bottom 40% (mostly zero/negative prices).

        Args:
            feed_in_forecast: Feed-in price forecast
            start_time: Start time for calculation
            percentile: Percentile threshold (0-100, default 60)
            hours: How many hours to include

        Returns:
            Percentile FIT price, or 0.0 if no data
        """
        prices = []
        base_slot = start_time.replace(minute=0, second=0, microsecond=0)

        for offset in range(hours * 12):  # 5-min intervals
            slot_time = base_slot + timedelta(minutes=5 * offset)
            price = get_price_for_slot(feed_in_forecast, slot_time)
            if price is not None:
                prices.append(price)

        if not prices:
            return 0.0

        # Sort and find percentile
        prices.sort()
        index = int(len(prices) * percentile / 100)
        index = min(index, len(prices) - 1)
        return prices[index]

    def _calculate_max_fit_price(
        self,
        feed_in_forecast: list[dict],
        start_time: datetime,
        hours: int = 24,
    ) -> float:
        """Calculate maximum FIT price over forecast window.

        Args:
            feed_in_forecast: Feed-in price forecast
            start_time: Start time for calculation
            hours: How many hours to include

        Returns:
            Maximum FIT price, or 0.0 if no data
        """
        prices = []
        base_slot = start_time.replace(minute=0, second=0, microsecond=0)

        for offset in range(hours * 12):  # 5-min intervals
            slot_time = base_slot + timedelta(minutes=5 * offset)
            price = get_price_for_slot(feed_in_forecast, slot_time)
            if price is not None:
                prices.append(price)

        if not prices:
            return 0.0

        return max(prices)

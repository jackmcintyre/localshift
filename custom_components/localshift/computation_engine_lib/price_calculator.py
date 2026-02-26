"""Price and FIT calculation helpers for computation engine."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from ..const import (
    BATTERY_CAPACITY_KWH,
    CONF_BATTERY_TARGET,
    CONF_CHEAP_PRICE_PERCENTILE,
    CONF_MAX_PRECHARGE_PRICE,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_PERCENTILE,
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    DEFAULT_MAX_PRECHARGE_PRICE,
)
from ..coordinator_data import CoordinatorData
from .utils import parse_forecast_dt


def _compute_price_slot_data(
    price_forecasts: list[dict[str, Any]],
    slot_start: datetime,
) -> tuple[list[float], float, datetime | None]:
    """Compute price data for a time slot.

    This is a shared helper for get_price_for_slot() and get_price_for_slot_or_none()
    that handles the common logic of finding prices that overlap with a time slot.

    Amber mixes 5-min dispatch intervals (near-term) with 30-min extended
    forecast periods. The overlap scan handles both correctly.

    Args:
        price_forecasts: List of price forecast entries from Amber.
        slot_start: Start time of the slot to check.

    Returns:
        Tuple of (prices_in_slot, fallback_price, fallback_start):
        - prices_in_slot: List of prices that overlap with the slot.
        - fallback_price: Price from the most recent entry at or before slot_start.
        - fallback_start: Start time of the fallback entry (None if no fallback).
    """
    # Ensure slot boundaries are timezone-aware local datetimes
    if slot_start.tzinfo is None:
        slot_start = dt_util.as_local(dt_util.as_utc(slot_start))
    else:
        slot_start = dt_util.as_local(slot_start)

    slot_end = slot_start + timedelta(minutes=15)

    prices_in_slot: list[float] = []
    fallback_price: float = 0.0
    fallback_start: datetime | None = None

    for entry in price_forecasts:
        if not isinstance(entry, dict):
            continue

        start_raw = entry.get("start_time")
        if start_raw is None:
            continue

        start_dt = parse_forecast_dt(start_raw)
        if start_dt is None:
            continue

        start_local = dt_util.as_local(start_dt)

        # Overlap check: use per-entry duration from the data when available,
        # otherwise assume 5 minutes (standard Amber dispatch interval).
        duration_minutes: int = int(entry.get("duration", 5))
        end_local = start_local + timedelta(minutes=duration_minutes)

        if start_local < slot_end and end_local > slot_start:
            price = float(entry.get("per_kwh", 0.0))
            prices_in_slot.append(price)

        # Track most-recent entry at or before slot_start for fallback
        if start_local <= slot_start:
            if fallback_start is None or start_local > fallback_start:
                fallback_start = start_local
                fallback_price = float(entry.get("per_kwh", 0.0))

    return prices_in_slot, fallback_price, fallback_start


def get_price_for_slot(
    price_forecasts: list[dict[str, Any]],
    slot_start: datetime,
) -> float:
    """Get price for a slot from Amber forecast data.

    Amber mixes 5-min dispatch intervals (near-term) with 30-min extended
    forecast periods. The overlap scan handles both correctly.

    When no period overlaps the slot (e.g. a 5-min slot that falls in the
    gap between two non-adjacent 30-min entries), falls back to the most
    recent entry whose start_time <= slot_start so that prices are never
    silently zeroed out due to resolution mismatches.
    """
    if not price_forecasts:
        return 0.0

    prices_in_slot, fallback_price, _ = _compute_price_slot_data(
        price_forecasts, slot_start
    )

    if prices_in_slot:
        return sum(prices_in_slot) / len(prices_in_slot)

    # Fallback: use the most recent price entry that started before this slot.
    # This handles the case where the Amber forecast has 30-min extended periods
    # that don't fully overlap short near-term slots (gap at :10/:15/:40/:45).
    return fallback_price


def get_price_for_slot_or_none(
    price_forecasts: list[dict[str, Any]],
    slot_start: datetime,
) -> float | None:
    """Get price for a slot, returning None only when the forecast has no data.

    Unlike get_price_for_slot(), this returns None (not 0.0) when the forecast
    list is empty or contains no entry at or before slot_start — allowing callers
    to distinguish "missing forecast" from a genuine $0.00 price.

    Uses the same duration-aware overlap + most-recent fallback as
    get_price_for_slot() so that 30-min extended periods are handled correctly.
    """
    if not price_forecasts:
        return None

    prices_in_slot, fallback_price, fallback_start = _compute_price_slot_data(
        price_forecasts, slot_start
    )

    if prices_in_slot:
        return sum(prices_in_slot) / len(prices_in_slot)

    # Return fallback price when the forecast exists but doesn't directly overlap.
    # Return None only when there is no entry at or before slot_start at all
    # (i.e. the forecast doesn't cover this time yet).
    if fallback_start is None:
        return None
    return fallback_price


class PriceCalculator:
    """Compute price thresholds and solar-weighted FIT metrics."""

    # Hysteresis parameters to prevent threshold oscillation (Issue #282)
    _THRESHOLD_HYSTERESIS: float = 0.02  # Minimum change (2 cents) to trigger update
    _SMOOTHING_ALPHA: float = (
        0.3  # EMA smoothing factor (0 = no smoothing, 1 = instant)
    )

    def __init__(
        self,
        entry: ConfigEntry,
        parse_forecast_dt: Callable[[str | None], datetime | None],
        percentile_func: Callable[[list[float], float], float],
        sum_solar_before_target: Callable[[list[dict[str, Any]], datetime, int], float],
        get_expected_load_kw: Callable[[CoordinatorData, float], float],
    ) -> None:
        """Initialize calculator dependencies."""
        self.entry = entry
        self._parse_forecast_dt = parse_forecast_dt
        self._percentile = percentile_func
        self._sum_solar_before_target = sum_solar_before_target
        self._get_expected_load_kw = get_expected_load_kw
        # Track smoothed threshold for hysteresis (Issue #282)
        self._smoothed_effective_cheap_price: float | None = None
        self._last_raw_effective_cheap_price: float | None = None

    def _collect_forecast_prices_and_base(
        self,
        general_forecast: list[dict[str, Any]],
        now_dt: datetime,
    ) -> tuple[list[float], float, float]:
        """Collect forecast prices and compute base/max price values.

        Extracts duplicate logic from compute_effective_cheap_price methods.

        Args:
            general_forecast: List of forecast dictionaries with start_time and per_kwh.
            now_dt: Current datetime for filtering forecasts.

        Returns:
            Tuple of (forecast_prices, base_price, max_price).
        """
        lookahead = DEFAULT_FORECAST_LOOKAHEAD_HOURS
        cutoff = now_dt + timedelta(hours=lookahead)

        forecast_prices = []
        for forecast in general_forecast:
            if not isinstance(forecast, dict):
                continue
            start = self._parse_forecast_dt(forecast.get("start_time"))
            if start is None:
                continue
            start_local = dt_util.as_local(start)
            if start_local >= now_dt and start_local <= cutoff:
                forecast_prices.append(float(forecast.get("per_kwh", 0)))

        percentile_value = float(
            self.entry.options.get(
                CONF_CHEAP_PRICE_PERCENTILE, DEFAULT_CHEAP_PRICE_PERCENTILE
            )
        )
        if forecast_prices:
            base = round(self._percentile(forecast_prices, percentile_value), 2)
        else:
            base = float(
                self.entry.options.get(
                    CONF_MAX_PRECHARGE_PRICE, DEFAULT_MAX_PRECHARGE_PRICE
                )
            )

        max_price = float(
            self.entry.options.get(
                CONF_MAX_PRECHARGE_PRICE, DEFAULT_MAX_PRECHARGE_PRICE
            )
        )

        return forecast_prices, base, max_price

    def _calculate_urgency_adjusted_price(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        target_hour: int,
        base: float,
        max_price: float,
    ) -> float:
        """Calculate urgency-adjusted cheap price threshold.

        When solar cannot reach target and we're before the target window,
        applies urgency scaling and forecast floor constraints.

        Args:
            data: Coordinator data containing general forecast.
            now_dt: Current datetime.
            target_hour: Target hour for the calculation window.
            base: Base price from percentile calculation.
            max_price: Maximum allowed price from config.

        Returns:
            Calculated urgency-adjusted price rounded to 2 decimal places.
        """
        target_dt = now_dt.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        hours_left = max((target_dt - now_dt).total_seconds() / 3600, 0)
        total_window = 8.0
        urgency = max(min(1 - (hours_left / total_window), 1.0), 0.0)
        urgency_price = base + (max_price - base) * urgency

        min_forecast = max_price
        for forecast in data.general_forecast:
            start = self._parse_forecast_dt(forecast.get("start_time"))
            if start is None:
                continue
            start_local = dt_util.as_local(start)
            if start_local >= now_dt and start_local.hour < target_hour:
                price = float(forecast.get("per_kwh", max_price))
                if price < min_forecast:
                    min_forecast = price

        forecast_floor = max(min_forecast + 0.02, base)
        final = min(urgency_price, max_price)
        final = max(final, forecast_floor)
        return round(final, 2)

    def compute_effective_cheap_price_preliminary(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        before_dw: bool,
        target_hour: int,
        target_pct: float,
    ) -> None:
        """Compute preliminary effective cheap price threshold.

        Uses a simple solar/load estimate to break circular dependencies before
        full forecast computation has run.
        """
        # Use shared helper for forecast price collection
        _, base, max_price = self._collect_forecast_prices_and_base(
            data.general_forecast, now_dt
        )

        try:
            solar_kwh = self._sum_solar_before_target(
                data.solcast_today, now_dt, target_hour
            )
        except (AttributeError, TypeError):
            solar_kwh = 0.0

        deficit_kwh = max((target_pct - data.soc) / 100 * BATTERY_CAPACITY_KWH, 0)
        target_dt = now_dt.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        hours_to_target = max((target_dt - now_dt).total_seconds() / 3600, 0)
        expected_load_kw = self._get_expected_load_kw(data, hours_to_target)
        consumption_kwh = expected_load_kw * hours_to_target

        net_solar = solar_kwh - consumption_kwh
        preliminary_solar_can_reach = data.soc >= target_pct or net_solar >= deficit_kwh
        solar_gap = not preliminary_solar_can_reach

        if not solar_gap or not before_dw or data.target_reached_today:
            data.effective_cheap_price = base
        else:
            data.effective_cheap_price = self._calculate_urgency_adjusted_price(
                data, now_dt, target_hour, base, max_price
            )

    def _apply_threshold_hysteresis(self, raw_threshold: float) -> float:
        """Apply hysteresis and smoothing to threshold to prevent oscillation.

        Issue #282: The raw threshold can oscillate rapidly due to:
        - FIT adjustments changing with feed-in tariff
        - Urgency calculations changing with time
        - Forecast updates

        This method applies:
        1. Hysteresis: Only update if change exceeds minimum threshold
        2. EMA smoothing: Dampen rapid changes

        Args:
            raw_threshold: The newly calculated threshold value.

        Returns:
            The smoothed threshold value.
        """
        # First call - initialize with raw value
        if self._smoothed_effective_cheap_price is None:
            self._smoothed_effective_cheap_price = raw_threshold
            self._last_raw_effective_cheap_price = raw_threshold
            return raw_threshold

        # Check if change exceeds hysteresis threshold
        change = abs(raw_threshold - self._last_raw_effective_cheap_price)
        if change < self._THRESHOLD_HYSTERESIS:
            # Change too small - keep smoothed value
            return self._smoothed_effective_cheap_price

        # Apply EMA smoothing to the new value
        smoothed = (
            self._SMOOTHING_ALPHA * raw_threshold
            + (1 - self._SMOOTHING_ALPHA) * self._smoothed_effective_cheap_price
        )

        # Update tracking
        self._smoothed_effective_cheap_price = round(smoothed, 2)
        self._last_raw_effective_cheap_price = raw_threshold

        return self._smoothed_effective_cheap_price

    def compute_effective_cheap_price(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        before_dw: bool,
        target_hour: int,
    ) -> None:
        """Compute final effective cheap price threshold.

        Applies hysteresis and smoothing to prevent rapid oscillation (Issue #282).
        """
        # Use shared helper for forecast price collection
        _, base, max_price = self._collect_forecast_prices_and_base(
            data.general_forecast, now_dt
        )

        solar_gap = not data.solar_can_reach_target

        if not solar_gap or not before_dw or data.target_reached_today:
            raw_threshold = base
        else:
            raw_threshold = self._calculate_urgency_adjusted_price(
                data, now_dt, target_hour, base, max_price
            )

        # Apply hysteresis and smoothing (Issue #282)
        data.effective_cheap_price = self._apply_threshold_hysteresis(raw_threshold)

    def compute_solar_weighted_avg_fit(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        target_hour: int,
        after_dw: bool,
    ) -> None:
        """Compute solar-weighted average feed-in tariff."""
        if after_dw:
            data.solar_weighted_avg_fit = 0.0
            data.solar_remaining_kwh = 0.0
            return

        weighted_sum = 0.0
        total_solar = 0.0

        for period in data.solcast_today:
            period_start = self._parse_forecast_dt(period.get("period_start"))
            if period_start is None:
                continue
            period_start_local = dt_util.as_local(period_start)
            if period_start_local >= now_dt and period_start_local.hour <= target_hour:
                solar_kwh_val = float(period.get("pv_estimate10", 0))
                if solar_kwh_val <= 0:
                    continue

                mid_local = period_start_local + timedelta(minutes=15)
                fit_price = 0.0
                for forecast in data.feed_in_forecast:
                    forecast_start = self._parse_forecast_dt(forecast.get("start_time"))
                    forecast_end = self._parse_forecast_dt(forecast.get("end_time"))
                    if forecast_start is None or forecast_end is None:
                        continue
                    forecast_start_local = dt_util.as_local(forecast_start)
                    forecast_end_local = dt_util.as_local(forecast_end)
                    if forecast_start_local <= mid_local < forecast_end_local:
                        fit_price = float(forecast.get("per_kwh", 0))
                        break

                weighted_sum += solar_kwh_val * fit_price
                total_solar += solar_kwh_val

        if total_solar > 0:
            data.solar_weighted_avg_fit = round(weighted_sum / total_solar, 4)
        else:
            data.solar_weighted_avg_fit = 0.0
        data.solar_remaining_kwh = round(total_solar, 2)

    def get_target_soc(self) -> float:
        """Return configured battery target SOC percentage."""
        return float(
            self.entry.options.get(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET)
        )

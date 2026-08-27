"""Price and FIT calculation helpers for computation engine."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from ..const import (
    BATTERY_CAPACITY_KWH,
    CHARGE_RATE_GRID_KW,
    CONF_CHEAP_PRICE_PERCENTILE,
    CONF_MAX_PRECHARGE_PRICE,
    DEFAULT_CHEAP_PRICE_PERCENTILE,
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    DEFAULT_MAX_PRECHARGE_PRICE,
)
from ..coordinator.data import CoordinatorData
from ..pricing.types import ForecastSlot
from .dp_math import urgency_ramp_price, urgency_window_hours
from .utils import parse_forecast_dt

# A stale ``target_reached_today`` latch (e.g. from a missed midnight reset) must not
# suppress demand-window pre-charge when the battery is actually well below target.
# Honor the latch only within this SOC deadband of the target — wide enough to avoid
# a re-charging sawtooth after legitimately reaching target, narrow enough to re-engage
# urgency once the battery has clearly drained far below target before the DW.
_STALE_TARGET_REACHED_SOC_DEADBAND_PCT = 20.0


def _target_reached_blocks_urgency(data: CoordinatorData, target_pct: float) -> bool:
    """Whether the daily target-reached latch should suppress urgency pre-charge.

    Guards against a stale ``target_reached_today`` starving a battery that sits far
    below target before the demand window. The latch's only live reset is a single
    midnight event, so a missed reset would otherwise disable all pre-charge for ~24h.
    """
    if not data.target_reached_today:
        return False
    if target_pct <= 0.0:
        return True  # No target context (legacy callers) — preserve prior behaviour.
    return data.soc >= target_pct - _STALE_TARGET_REACHED_SOC_DEADBAND_PCT


def _parse_price_entry(
    entry: Any, slot_start: datetime, slot_end: datetime
) -> dict[str, Any] | None:
    """Parse a price forecast entry, returning None if invalid.

    Returns dict with: start_local, end_local, price, duration_minutes, is_overlap.
    """
    if not hasattr(entry, "get"):
        return None

    start_raw = entry.get("start_time")
    if start_raw is None:
        return None

    start_dt = parse_forecast_dt(start_raw)
    if start_dt is None:
        return None

    start_local = dt_util.as_local(start_dt)
    duration_minutes = int(entry.get("duration", 5))
    end_local = start_local + timedelta(minutes=duration_minutes)
    price = float(entry.get("per_kwh", 0.0))

    return {
        "start_local": start_local,
        "end_local": end_local,
        "price": price,
        "duration_minutes": duration_minutes,
        "is_overlap": start_local < slot_end and end_local > slot_start,
        "is_fallback_candidate": start_local <= slot_start,
    }


def _compute_price_slot_data(
    price_forecasts: list[dict[str, Any]],
    slot_start: datetime,
) -> tuple[list[float], float, datetime | None]:
    """Compute price data for a time slot.

    This is a shared helper for get_price_for_slot() and get_price_for_slot_or_none()
    that handles the common logic of finding prices that overlap with a time slot.

    Amber mixes 5-min dispatch intervals (near-term) with 30-min extended
    forecast periods. The overlap scan handles both correctly.
    """
    if slot_start.tzinfo is None:
        slot_start = dt_util.as_local(dt_util.as_utc(slot_start))
    else:
        slot_start = dt_util.as_local(slot_start)

    slot_end = slot_start + timedelta(minutes=15)

    prices_in_slot: list[float] = []
    fallback_price: float = 0.0
    fallback_start: datetime | None = None

    for entry in price_forecasts:
        parsed = _parse_price_entry(entry, slot_start, slot_end)
        if parsed is None:
            continue

        if parsed["is_overlap"]:
            prices_in_slot.append(parsed["price"])

        if parsed["is_fallback_candidate"]:
            if fallback_start is None or parsed["start_local"] > fallback_start:
                fallback_start = parsed["start_local"]
                fallback_price = parsed["price"]

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


def _collect_prices_by_source(
    price_forecasts: list[dict[str, Any]],
    slot_start: datetime,
    interval_minutes: int,
) -> dict[str, Any]:
    """Collect prices grouped by source (5min/30min) with fallback info."""
    if slot_start.tzinfo is None:
        slot_start = dt_util.as_local(dt_util.as_utc(slot_start))
    else:
        slot_start = dt_util.as_local(slot_start)

    slot_end = slot_start + timedelta(minutes=interval_minutes)

    prices_5min: list[float] = []
    prices_30min: list[float] = []
    fallback_price: float = 0.0
    fallback_source: str = "unknown"
    fallback_start: datetime | None = None

    for entry in price_forecasts:
        parsed = _parse_price_entry(entry, slot_start, slot_end)
        if parsed is None:
            continue

        source = "5min" if parsed["duration_minutes"] <= 5 else "30min"

        if parsed["is_overlap"]:
            if source == "5min":
                prices_5min.append(parsed["price"])
            else:
                prices_30min.append(parsed["price"])

        if parsed["is_fallback_candidate"]:
            if fallback_start is None or parsed["start_local"] > fallback_start:
                fallback_start = parsed["start_local"]
                fallback_price = parsed["price"]
                fallback_source = source

    return {
        "prices_5min": prices_5min,
        "prices_30min": prices_30min,
        "fallback_price": fallback_price,
        "fallback_source": fallback_source,
    }


def get_price_for_slot_with_source(
    price_forecasts: list[dict[str, Any]],
    slot_start: datetime,
    interval_minutes: int = 15,
) -> tuple[float, str]:
    """Get price for a slot along with price source metadata."""
    if not price_forecasts:
        return 0.0, "unknown"

    data = _collect_prices_by_source(price_forecasts, slot_start, interval_minutes)

    if data["prices_5min"]:
        return sum(data["prices_5min"]) / len(data["prices_5min"]), "5min"

    if data["prices_30min"]:
        return sum(data["prices_30min"]) / len(data["prices_30min"]), "30min"

    return data["fallback_price"], data["fallback_source"]


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
        general_forecast: list[ForecastSlot],
        now_dt: datetime,
        horizon_hours: float = 24.0,
    ) -> tuple[list[float], float, float]:
        """Collect forecast prices and compute base/max price values.

        Extracts duplicate logic from compute_effective_cheap_price methods.

        Args:
            general_forecast: List of forecast dictionaries with start_time and per_kwh.
            now_dt: Current datetime for filtering forecasts.
            horizon_hours: Actual hours of forecast available.

        Returns:
            Tuple of (forecast_prices, base_price, max_price).

        """
        lookahead = DEFAULT_FORECAST_LOOKAHEAD_HOURS
        cutoff = now_dt + timedelta(hours=lookahead)

        forecast_prices = []
        for forecast in general_forecast:
            if not hasattr(forecast, "get"):
                continue
            start = self._parse_forecast_dt(forecast.get("start_time"))
            if start is None:
                continue
            start_local = dt_util.as_local(start)
            if start_local >= now_dt and start_local <= cutoff:
                forecast_prices.append(float(forecast.get("per_kwh", 0)))

        configured_percentile = float(
            self.entry.options.get(
                CONF_CHEAP_PRICE_PERCENTILE, DEFAULT_CHEAP_PRICE_PERCENTILE
            )
        )

        # Issue #431: Scale percentile by horizon completeness.
        # If horizon < 24h, reduce percentile to be more selective about "cheap".
        horizon_factor = max(0.1, min(horizon_hours / 24.0, 1.0))
        percentile_value = configured_percentile * horizon_factor

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
        target_pct: float,
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
        if target_dt <= now_dt:
            target_dt += timedelta(days=1)
        hours_left = max((target_dt - now_dt).total_seconds() / 3600, 0)
        # Issue #559 Root Cause 2: 4h is now the FLOOR (not a fixed value) — narrow enough to
        # avoid urgency creep raising prices well before any real need (charging at $0.19 when
        # the threshold is $0.18). 2026-06-11 incident: a deep SOC deficit needs more than 4h
        # of charge runway, so the window widens (deficit-derived, capped 8h) only when the
        # battery genuinely cannot reach target in 4h. Shared with the optimizer's urgency
        # window via dp_math.urgency_window_hours.
        total_window = urgency_window_hours(
            data.soc,
            target_pct,
            BATTERY_CAPACITY_KWH,
            CHARGE_RATE_GRID_KW,
            0.92,
        )
        # Shared with the optimizer's per-slot pre-DW thresholds
        # (constraints.compute_pre_dw_charge_thresholds) so the live "now" threshold and
        # the value the DP assumes each future slot will be gated at cannot drift apart.
        urgency_price = urgency_ramp_price(base, max_price, hours_left, total_window)

        min_forecast = max_price
        now_dt_local = dt_util.as_local(now_dt) if now_dt.tzinfo else now_dt
        target_dt_local = dt_util.as_local(target_dt) if target_dt.tzinfo else target_dt
        for forecast in data.general_forecast:
            start = self._parse_forecast_dt(forecast.get("start_time"))
            if start is None:
                continue
            start_local = dt_util.as_local(start)
            if start_local >= now_dt_local and start_local < target_dt_local:
                price = float(forecast.get("per_kwh", max_price))
                if price < min_forecast:
                    min_forecast = price

        # Issue #559 Root Cause 2: removed the hard-coded +0.02 buffer.
        # The buffer created a "ghost" threshold ($0.19 when strict limit was $0.18),
        # causing the optimizer to charge at prices the user intended to exclude.
        forecast_floor = max(min_forecast, base)
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
            data.general_forecast, now_dt, data.forecast_horizon_hours
        )

        # Issue #800: record un-inflated percentile base (see compute_effective_cheap_price).
        data.base_cheap_price = base

        try:
            # Include tomorrow's forecast when target is tomorrow morning
            all_solcast = [*data.solcast_today, *data.solcast_tomorrow]
            solar_kwh = self._sum_solar_before_target(all_solcast, now_dt, target_hour)
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

        if (
            not solar_gap
            or not before_dw
            or _target_reached_blocks_urgency(data, target_pct)
        ):
            data.effective_cheap_price = base
        else:
            data.effective_cheap_price = self._calculate_urgency_adjusted_price(
                data, now_dt, target_hour, base, max_price, target_pct
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
        last_raw = self._last_raw_effective_cheap_price
        if last_raw is None:
            return self._smoothed_effective_cheap_price or raw_threshold

        change = abs(raw_threshold - last_raw)
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
        target_pct: float = 0.0,
    ) -> None:
        """Compute final effective cheap price threshold.

        Applies hysteresis and smoothing to prevent rapid oscillation (Issue #282).
        """
        # Use shared helper for forecast price collection
        _, base, max_price = self._collect_forecast_prices_and_base(
            data.general_forecast, now_dt, data.forecast_horizon_hours
        )

        # Issue #800: record the un-inflated "genuinely cheap" percentile base so the
        # optimizer can gate post-demand-window (tomorrow) slots on it instead of the
        # urgency-inflated effective price (which would cause overnight sawtooth charging).
        data.base_cheap_price = base

        solar_gap = not data.solar_can_reach_target

        if (
            not solar_gap
            or not before_dw
            or _target_reached_blocks_urgency(data, target_pct)
        ):
            raw_threshold = base
        else:
            raw_threshold = self._calculate_urgency_adjusted_price(
                data, now_dt, target_hour, base, max_price, target_pct
            )

        # Apply hysteresis and smoothing (Issue #282)
        data.effective_cheap_price = self._apply_threshold_hysteresis(raw_threshold)

    def _get_fit_price_for_period(
        self, feed_in_forecast: list[ForecastSlot], mid_local: datetime
    ) -> float:
        """Get FIT price for a specific time from forecast."""
        for forecast in feed_in_forecast:
            forecast_start = self._parse_forecast_dt(forecast.get("start_time"))
            forecast_end = self._parse_forecast_dt(forecast.get("end_time"))
            if forecast_start is None or forecast_end is None:
                continue
            forecast_start_local = dt_util.as_local(forecast_start)
            forecast_end_local = dt_util.as_local(forecast_end)
            if forecast_start_local <= mid_local < forecast_end_local:
                return float(forecast.get("per_kwh", 0))
        return 0.0

    def _compute_weighted_fit(
        self,
        all_solcast: list[dict[str, Any]],
        feed_in_forecast: list[ForecastSlot],
        now_dt: datetime,
        target_dt: datetime,
    ) -> tuple[float, float]:
        """Compute solar-weighted average FIT. Returns (weighted_avg, total_solar)."""
        weighted_sum = 0.0
        total_solar = 0.0

        now_dt_local = dt_util.as_local(now_dt) if now_dt.tzinfo else now_dt
        target_dt_local = dt_util.as_local(target_dt) if target_dt.tzinfo else target_dt

        for period in all_solcast:
            period_start = self._parse_forecast_dt(period.get("period_start"))
            if period_start is None:
                continue
            period_start_local = dt_util.as_local(period_start)
            if not (
                period_start_local >= now_dt_local
                and period_start_local < target_dt_local
            ):
                continue

            solar_kwh_val = float(period.get("pv_estimate", 0))
            if solar_kwh_val <= 0:
                continue

            mid_local = period_start_local + timedelta(minutes=15)
            fit_price = self._get_fit_price_for_period(feed_in_forecast, mid_local)

            weighted_sum += solar_kwh_val * fit_price
            total_solar += solar_kwh_val

        return weighted_sum, total_solar

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

        all_solcast = [*data.solcast_today, *data.solcast_tomorrow]
        target_dt = now_dt.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        if target_dt <= now_dt:
            target_dt += timedelta(days=1)

        weighted_sum, total_solar = self._compute_weighted_fit(
            all_solcast, data.feed_in_forecast, now_dt, target_dt
        )

        if total_solar > 0:
            data.solar_weighted_avg_fit = round(weighted_sum / total_solar, 4)
        else:
            data.solar_weighted_avg_fit = 0.0
        data.solar_remaining_kwh = round(total_solar, 2)

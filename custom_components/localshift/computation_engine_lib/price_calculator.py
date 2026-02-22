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
    CONF_FORECAST_LOOKAHEAD_HOURS,
    CONF_MAX_PRECHARGE_PRICE,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_PERCENTILE,
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    DEFAULT_MAX_PRECHARGE_PRICE,
)
from ..coordinator_data import CoordinatorData


class PriceCalculator:
    """Compute price thresholds and solar-weighted FIT metrics."""

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
        lookahead = float(
            self.entry.options.get(
                CONF_FORECAST_LOOKAHEAD_HOURS, DEFAULT_FORECAST_LOOKAHEAD_HOURS
            )
        )
        cutoff = now_dt + timedelta(hours=lookahead)

        forecast_prices = []
        for forecast in data.general_forecast:
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
            target_dt = now_dt.replace(
                hour=target_hour, minute=0, second=0, microsecond=0
            )
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
            data.effective_cheap_price = round(final, 2)

    def compute_effective_cheap_price(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        before_dw: bool,
        target_hour: int,
    ) -> None:
        """Compute final effective cheap price threshold."""
        lookahead = float(
            self.entry.options.get(
                CONF_FORECAST_LOOKAHEAD_HOURS, DEFAULT_FORECAST_LOOKAHEAD_HOURS
            )
        )
        cutoff = now_dt + timedelta(hours=lookahead)

        forecast_prices = []
        for forecast in data.general_forecast:
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
        solar_gap = not data.solar_can_reach_target

        if not solar_gap or not before_dw or data.target_reached_today:
            data.effective_cheap_price = base
        else:
            target_dt = now_dt.replace(
                hour=target_hour, minute=0, second=0, microsecond=0
            )
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
            data.effective_cheap_price = round(final, 2)

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

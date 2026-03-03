"""Excess solar signal orchestration for computation engine."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, time

from homeassistant.config_entries import ConfigEntry

from ..const import (
    CONF_BATTERY_TARGET,
    CONF_DEMAND_WINDOW_START,
    CONF_MINIMUM_TARGET_SOC,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_MINIMUM_TARGET_SOC,
)
from ..coordinator_data import CoordinatorData

_LOGGER = logging.getLogger(__name__)


class ExcessSolarSignalsEngine:
    """Compute high-level excess-solar load-shifting signals."""

    def __init__(
        self,
        entry: ConfigEntry,
        calculate_excess_by_windows: Callable[..., dict[str, float]],
        find_nearest_negative_fit_window: Callable[..., tuple[datetime | None, int]],
        calculate_excess_until_negative_fit: Callable[..., float],
        find_battery_fill_point: Callable[..., int | None],
        calculate_safe_additional_load: Callable[..., tuple[float, bool]],
        compute_load_shift_signal: Callable[..., tuple[str, float, int, str, str]],
        get_entity_id: Callable[[str], str],
        get_historical_hourly_averages: Callable[[str], dict[int, float]],
        recent_load_1hr_getter: Callable[[], float],
        parse_time_option: Callable[[str, str], time],
    ) -> None:
        """Initialize engine dependencies (Phase 4, #441)."""
        self.entry = entry
        self._calculate_excess_by_windows = calculate_excess_by_windows
        self._find_nearest_negative_fit_window = find_nearest_negative_fit_window
        self._calculate_excess_until_negative_fit = calculate_excess_until_negative_fit
        self._find_battery_fill_point = find_battery_fill_point
        self._calculate_safe_additional_load = calculate_safe_additional_load
        self._compute_load_shift_signal = compute_load_shift_signal
        self._get_entity_id = get_entity_id
        self._get_historical_hourly_averages = get_historical_hourly_averages
        self._recent_load_1hr_getter = recent_load_1hr_getter
        self._parse_time_option = parse_time_option

    def compute_signals(
        self,
        data: CoordinatorData,
        now_dt: datetime,
    ) -> None:
        """Compute excess-solar values and load-shift recommendations."""
        target_pct = float(
            self.entry.options.get(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET)
        )
        min_soc_pct = float(
            self.entry.options.get(CONF_MINIMUM_TARGET_SOC, DEFAULT_MINIMUM_TARGET_SOC)
        )
        demand_window_start = self._parse_time_option(
            CONF_DEMAND_WINDOW_START, DEFAULT_DEMAND_WINDOW_START
        )

        all_solcast = [*data.solcast_today, *data.solcast_tomorrow]

        load_entity_id = self._get_entity_id("teslemetry_load_power")
        hourly_avg_kw = self._get_historical_hourly_averages(load_entity_id)
        recent_load_kw = self._recent_load_1hr_getter()

        current_excess_kw = max(0.0, data.solar_power_kw - data.load_power_kw)
        data.current_excess_rate_kw = round(current_excess_kw, 2)

        current_5min = (now_dt.minute // 5) * 5
        base_slot = now_dt.replace(minute=current_5min, second=0, microsecond=0)
        current_hour = base_slot.hour

        excess_by_windows = self._calculate_excess_by_windows(
            base_slot=base_slot,
            all_solcast=all_solcast,
            historical_avg_kw=hourly_avg_kw,
            current_load_kw=data.load_power_kw,
            recent_load_kw=recent_load_kw,
            current_soc=data.soc,
            target_pct=target_pct,
            current_hour=current_hour,
        )

        data.excess_solar_current_hour_kwh = excess_by_windows.get(
            "excess_current_hour_kwh", 0.0
        )
        data.excess_solar_next_2h_kwh = excess_by_windows.get("excess_next_2h_kwh", 0.0)
        data.excess_solar_next_4h_kwh = excess_by_windows.get("excess_next_4h_kwh", 0.0)
        data.excess_until_battery_full_kwh = excess_by_windows.get(
            "excess_until_battery_full_kwh", 0.0
        )

        negative_fit_start, negative_fit_duration = (
            self._find_nearest_negative_fit_window(data.feed_in_forecast, now_dt)
        )
        data.negative_fit_window_start = negative_fit_start
        data.negative_fit_window_duration_minutes = negative_fit_duration

        data.excess_until_negative_fit_kwh = self._calculate_excess_until_negative_fit(
            base_slot=base_slot,
            negative_fit_start=negative_fit_start,
            all_solcast=all_solcast,
            historical_avg_kw=hourly_avg_kw,
            current_load_kw=data.load_power_kw,
            recent_load_kw=recent_load_kw,
            current_soc=data.soc,
            target_pct=target_pct,
            current_hour=current_hour,
        )

        fill_point_minutes = self._find_battery_fill_point(
            start_soc=data.soc,
            start_slot=base_slot,
            all_solcast=all_solcast,
            historical_avg_kw=hourly_avg_kw,
            current_load_kw=data.load_power_kw,
            recent_load_kw=recent_load_kw,
            current_hour=current_hour,
        )
        data.time_until_battery_full_minutes = fill_point_minutes or 0

        safe_additional_load, grid_charge_risk = self._calculate_safe_additional_load(
            base_slot=base_slot,
            all_solcast=all_solcast,
            historical_avg_kw=hourly_avg_kw,
            current_load_kw=data.load_power_kw,
            recent_load_kw=recent_load_kw,
            current_soc=data.soc,
            target_pct=target_pct,
            dw_start_time=demand_window_start,
            effective_cheap_price=data.effective_cheap_price,
            general_forecast=data.general_forecast,
            min_soc_pct=min_soc_pct,
            current_hour=current_hour,
        )
        data.safe_additional_load_kw = round(safe_additional_load, 1)
        data.grid_charge_risk = grid_charge_risk

        signal, recommended_kw, duration, reason, confidence = (
            self._compute_load_shift_signal(
                data=data,
                excess_by_windows=excess_by_windows,
                negative_fit_start=negative_fit_start,
                safe_additional_load=safe_additional_load,
                grid_charge_risk=grid_charge_risk,
                fill_point_minutes=fill_point_minutes,
            )
        )
        data.load_shift_signal = signal
        data.load_shift_recommended_kw = round(recommended_kw, 1)
        data.load_shift_recommended_duration_minutes = duration
        data.load_shift_reason = reason
        data.load_shift_confidence = confidence

        data.can_add_load_now = (
            data.excess_solar_next_2h_kwh > 0.5
            and safe_additional_load > 0.5
            and not grid_charge_risk
            and not data.demand_window_active
            and not data.manual_override
        )

        battery_charging = data.battery_power_kw < -0.1
        battery_near_full = data.soc > 80
        solar_exceeds_load = data.solar_power_kw > data.load_power_kw + 0.5

        data.excess_solar_available = (
            solar_exceeds_load
            and (battery_near_full or battery_charging)
            and not data.demand_window_active
            and data.can_add_load_now
        )

        _LOGGER.info(
            "Excess solar: available=%s, can_add_load=%s, signal=%s, safe_kw=%.1f, next_2h=%.1fkWh",
            data.excess_solar_available,
            data.can_add_load_now,
            data.load_shift_signal,
            data.safe_additional_load_kw,
            data.excess_solar_next_2h_kwh,
        )

"""Forecast pipeline for load/solar/excess-solar computations."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from ..const import (
    BATTERY_CAPACITY_KWH,
    CHARGE_RATE_GRID_KW,
    SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET,
)
from ..coordinator.data import CoordinatorData
from .solar import sum_solar_before_target
from .solar_accuracy import SolarAccuracyTracker

_LOGGER = logging.getLogger(__name__)


class ForecastPipeline:
    """Compute forecast-related fields used by the coordinator."""

    def __init__(
        self,
        load_forecaster: Any,
        price_signals: Any,
        forecast_history_store: Any,
        get_switch_state: Any,
        excess_solar_signals: Any,
    ) -> None:
        self._load_forecaster = load_forecaster
        self._price_signals = price_signals
        self._forecast_history_store = forecast_history_store
        self._get_switch_state = get_switch_state
        self._excess_solar_signals = excess_solar_signals

    def compute_load_forecast_slots(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        historical_avg_kw: dict[int, float],
        recent_load_kw: float,
        total_slots: int,
    ) -> None:
        """Populate data.load_forecast_slots with per-slot kW estimates."""
        current_5min = (now_dt.minute // 5) * 5
        base_slot = now_dt.replace(minute=current_5min, second=0, microsecond=0)
        current_hour = base_slot.hour

        self._load_forecaster.reset_weather_adjustment_applied()

        robust_current_kw = (
            data.recent_load_short_kw
            if data.recent_load_short_kw > 0
            else data.load_power_kw
        )

        slots: list[float] = []
        source_counts: dict[str, int] = {}
        for i in range(total_slots):
            slot_start = base_slot + timedelta(minutes=15 * i)
            slot_hour = slot_start.hour
            day_of_week = slot_start.weekday()
            season = SolarAccuracyTracker._get_season(slot_start)
            hours_ahead = i / 4.0
            temperature = data.weather_temperature_forecast.get(slot_hour)
            load_kw, source = self._load_forecaster.estimate_hourly_consumption_kw(
                hourly_avg_kw=historical_avg_kw,
                slot_hour=slot_hour,
                current_hour=current_hour,
                current_load_kw=robust_current_kw,
                recent_load_kw=recent_load_kw,
                temperature=temperature,
                hours_ahead=hours_ahead,
                day_of_week=day_of_week,
                season=season,
            )
            slots.append(load_kw)
            source_counts[source] = source_counts.get(source, 0) + 1

        data.load_forecast_slots = slots
        data.forecast_consumption_source_counts = source_counts
        data.weather_adjustment_applied = (
            self._load_forecaster.get_weather_adjustment_applied()
        )
        _LOGGER.info(
            "ISSUE_500 load_forecast_slots: %d slots, indices 4-8 = %s, recent_load=%.3f, hourly_avg_12=%.3f, robust_current=%.3f",
            len(slots),
            [round(slots[i], 3) for i in range(4, min(9, len(slots)))],
            recent_load_kw,
            historical_avg_kw.get(12, -1),
            robust_current_kw,
        )

    def compute_solar_battery_forecast(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        target_hour: int,
        before_dw: bool,
        after_dw: bool,
        target_pct: float,
    ) -> None:
        """Compute solar battery SOC forecast."""
        _ = before_dw
        if after_dw:
            dw_entry = self._get_dp_decision_at_demand_window(data, target_hour)
            if dw_entry:
                predicted_soc = dw_entry["predicted_soc_pct"]
                can_reach = predicted_soc >= target_pct
            else:
                predicted_soc = data.soc
                can_reach = data.soc >= target_pct

            boost_needed = False
            target_reached = data.soc >= target_pct
            if target_reached:
                data.target_reached_today = True

            next_dw_dt = now_dt.replace(
                hour=target_hour, minute=0, second=0, microsecond=0
            )
            if next_dw_dt <= now_dt:
                next_dw_dt += timedelta(days=1)
            hours_to_next_dw = (next_dw_dt - now_dt).total_seconds() / 3600

            data.solar_battery_forecast = {
                "predicted_soc": round(predicted_soc, 1),
                "solar_before_dw_kwh": 0.0,
                "consumption_estimate_kwh": 0.0,
                "net_solar_kwh": 0.0,
                "deficit_kwh": 0.0,
                "can_reach_target": can_reach,
                "boost_needed": boost_needed,
                "hours_to_target_time": round(hours_to_next_dw, 1),
                "target_reached_today": target_reached,
            }
            return

        dw_entry = self._get_dp_decision_at_demand_window(data, target_hour)

        if dw_entry:
            predicted_soc = dw_entry["predicted_soc_pct"]
            can_reach = predicted_soc >= target_pct

            deficit_kwh = max((target_pct - data.soc) / 100 * BATTERY_CAPACITY_KWH, 0)

            all_solcast = [*data.solcast_today, *data.solcast_tomorrow]
            from custom_components.localshift.forecast.analysis_resolver import (
                ConfidenceResolver,
            )

            resolver = ConfidenceResolver(
                getattr(data, "solcast_analysis_today", None),
                getattr(data, "solcast_analysis_tomorrow", None),
                absent_confidence=getattr(data, "solar_absent_confidence", 1.0),
            )
            solar_kwh = sum_solar_before_target(
                all_solcast, now_dt, target_hour, resolver=resolver
            )

            target_dt = now_dt.replace(
                hour=target_hour, minute=0, second=0, microsecond=0
            )
            hours_to_target = max((target_dt - now_dt).total_seconds() / 3600, 0)

            expected_load_kw = self._price_signals.get_expected_load_kw_from_slots(
                data, hours_to_target
            )
            consumption_kwh = expected_load_kw * hours_to_target

            net_solar = solar_kwh - consumption_kwh

            allow_dw_under_target = self._get_switch_state(
                SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET
            )
            if allow_dw_under_target and hasattr(data, "solar_can_reach_target_in_dw"):
                boost_needed = (
                    data.soc < target_pct and not data.solar_can_reach_target_in_dw
                )
            else:
                boost_needed = data.soc < target_pct and net_solar < deficit_kwh
        else:
            target_dt = now_dt.replace(
                hour=target_hour, minute=0, second=0, microsecond=0
            )
            hours_to_target = max((target_dt - now_dt).total_seconds() / 3600, 0)

            deficit_kwh = max((target_pct - data.soc) / 100 * BATTERY_CAPACITY_KWH, 0)

            all_solcast = [*data.solcast_today, *data.solcast_tomorrow]
            from custom_components.localshift.forecast.analysis_resolver import (
                ConfidenceResolver,
            )

            resolver = ConfidenceResolver(
                getattr(data, "solcast_analysis_today", None),
                getattr(data, "solcast_analysis_tomorrow", None),
                absent_confidence=getattr(data, "solar_absent_confidence", 1.0),
            )
            solar_kwh = sum_solar_before_target(
                all_solcast, now_dt, target_hour, resolver=resolver
            )

            expected_load_kw = self._price_signals.get_expected_load_kw_from_slots(
                data, hours_to_target
            )
            consumption_kwh = expected_load_kw * hours_to_target

            net_solar = solar_kwh - consumption_kwh

            net_solar_pct = net_solar / BATTERY_CAPACITY_KWH * 100
            predicted_soc = max(0.0, min(100.0, data.soc + net_solar_pct))

            can_reach = data.soc >= target_pct or net_solar >= deficit_kwh

            if data.soc >= target_pct:
                boost_needed = False
            else:
                remaining_deficit = max(deficit_kwh - max(net_solar, 0), 0)
                time_needed_hours = (
                    remaining_deficit / (CHARGE_RATE_GRID_KW * 0.9)
                    if remaining_deficit > 0
                    else 0
                )
                boost_needed = (
                    time_needed_hours > (hours_to_target - 0.5)
                    and remaining_deficit > 0
                )

        target_reached = data.soc >= target_pct
        if target_reached:
            data.target_reached_today = True

        data.solar_battery_forecast = {
            "predicted_soc": round(predicted_soc, 1),
            "solar_before_dw_kwh": round(solar_kwh, 2),
            "consumption_estimate_kwh": round(consumption_kwh, 2),
            "net_solar_kwh": round(net_solar, 2),
            "deficit_kwh": round(deficit_kwh, 2),
            "can_reach_target": can_reach,
            "boost_needed": boost_needed,
            "hours_to_target_time": round(hours_to_target, 1),
            "target_reached_today": target_reached,
        }

        self._forecast_history_store.store_forecast_history(data, now_dt)

    def compute_excess_solar_signals(
        self, data: CoordinatorData, now_dt: datetime
    ) -> None:
        """Compute excess solar load shifting signals."""
        self._excess_solar_signals.compute_signals(data, now_dt)

    def compute_solar_weighted_avg_fit(
        self, data: CoordinatorData, now_dt: datetime, target_hour: int, after_dw: bool
    ) -> None:
        """Compute solar-weighted average feed-in tariff."""
        self._price_signals.compute_solar_weighted_avg_fit(
            data=data, now_dt=now_dt, target_hour=target_hour, after_dw=after_dw
        )

    def _get_dp_decision_at_demand_window(
        self, data: CoordinatorData, target_hour: int
    ) -> dict | None:
        """Get the DP decision at or just after the demand window start time."""
        decisions = data.optimizer_decisions or []
        if not decisions:
            return None

        now_raw = dt_util.now()
        now_local = (
            dt_util.as_local(dt_util.as_utc(now_raw))
            if now_raw.tzinfo is None
            else dt_util.as_local(now_raw)
        )

        dw_start_dt = now_local.replace(
            hour=target_hour, minute=0, second=0, microsecond=0
        )
        if dw_start_dt <= now_local:
            dw_start_dt += timedelta(days=1)

        for decision in decisions:
            ts = decision.get("timestamp_iso", "")
            if not ts:
                continue
            try:
                slot_dt = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if slot_dt.tzinfo is None:
                slot_local = dt_util.as_local(dt_util.as_utc(slot_dt))
            else:
                slot_local = dt_util.as_local(slot_dt)

            if slot_local >= dw_start_dt:
                return decision

        return None

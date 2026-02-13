"""Computation engine for derived values and forecasts."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    BATTERY_CAPACITY_KWH,
    CHARGE_RATE_BACKUP_KW,
    CONF_BATTERY_TARGET,
    CONF_CHEAP_PRICE_DEADBAND,
    CONF_CHEAP_PRICE_PERCENTILE,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    CONF_FORECAST_LOOKAHEAD_HOURS,
    CONF_HOLD_ABSOLUTE_CHEAP_THRESHOLD,
    CONF_HOLD_MIN_SAVINGS_PERCENT,
    CONF_MAX_PRECHARGE_PRICE,
    CONF_PRECHARGE_BATTERY_THRESHOLD,
    CONF_SUN_ENTITY,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_DEADBAND,
    DEFAULT_CHEAP_PRICE_PERCENTILE,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    DEFAULT_HOLD_ABSOLUTE_CHEAP_THRESHOLD,
    DEFAULT_HOLD_MIN_SAVINGS_PERCENT,
    DEFAULT_MAX_PRECHARGE_PRICE,
    DEFAULT_PRECHARGE_BATTERY_THRESHOLD,
    DISCHARGE_EARLIEST_HOUR,
    SOLAR_EXPORT_SURPLUS_ENTRY,
    SOLAR_EXPORT_SURPLUS_STAY,
    BatteryMode,
)
from .coordinator_data import CoordinatorData

_LOGGER = logging.getLogger(__name__)


class ComputationEngine:
    """Computes all derived sensor values from raw state."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        get_entity_id_func: callable,
        get_switch_state_func: callable,
    ) -> None:
        """Initialize computation engine.

        Args:
            hass: Home Assistant instance
            entry: Config entry
            get_entity_id_func: Function to get entity IDs by config key
            get_switch_state_func: Function to get switch states
        """
        self.hass = hass
        self.entry = entry
        self._get_entity_id = get_entity_id_func
        self._get_switch_state = get_switch_state_func
        self._historical_load_cache: dict[int, float] = {}
        self._historical_load_cache_date: str = ""
        self._previous_active_mode = None
        self._last_forecast_hour: int | None = None

    def compute_derived_values(self, data: CoordinatorData) -> None:
        """Compute all derived sensor/binary_sensor values from raw state.

        Ported from Jinja templates in the YAML package. Steps are ordered
        by dependency — later steps can reference earlier results.
        """
        now_dt = dt_util.now()

        # Common time values used by multiple steps
        dw_start_time = self._parse_time_option(
            CONF_DEMAND_WINDOW_START, DEFAULT_DEMAND_WINDOW_START
        )
        dw_end_time = self._parse_time_option(
            CONF_DEMAND_WINDOW_END, DEFAULT_DEMAND_WINDOW_END
        )
        target_hour = dw_start_time.hour
        now_t = now_dt.time()
        before_dw = now_t < dw_start_time
        after_dw = now_t >= dw_start_time

        # ---- Step 1: Directional power (always positive) ----
        data.grid_import_power_kw = max(data.grid_power_kw, 0.0)
        data.grid_export_power_kw = max(-data.grid_power_kw, 0.0)

        # ---- Step 2: Mode detection from Teslemetry state ----
        data.force_discharge_active = (
            data.operation_mode == "autonomous" and data.backup_reserve < 11
        )
        # force_charge_active = ANY charging state (backup OR boost)
        data.force_charge_active = data.operation_mode == "backup" or (
            data.operation_mode == "autonomous" and data.backup_reserve > 99
        )
        data.boost_charge_active = (
            data.operation_mode == "autonomous" and data.backup_reserve > 99
        )
        # hold_active uses internal flag (matches YAML: input_boolean.battery_hold_mode)
        data.hold_active = data.hold_mode

        # ---- Step 3: demand_window_active ----
        dw_block_enabled = self._get_switch_state("demand_window_block")
        data.demand_window_active = (
            dw_block_enabled and now_t >= dw_start_time and now_t < dw_end_time
        )

        # ---- Step 4: solar_battery_forecast ----
        self._compute_solar_battery_forecast(
            data, now_dt, target_hour, before_dw, after_dw
        )

        # ---- Step 5: solar_can_reach_target (from forecast) ----
        data.solar_can_reach_target = data.solar_battery_forecast.get(
            "can_reach_target", True
        )

        # ---- Step 6: boost_charge_needed (from forecast) ----
        data.boost_charge_needed = data.solar_battery_forecast.get(
            "boost_needed", False
        )

        # ---- Step 7: effective_cheap_price ----
        self._compute_effective_cheap_price(data, now_dt, before_dw, target_hour)

        # ---- Step 8: cheap_charge_stop_price ----
        deadband = float(
            self.entry.options.get(
                CONF_CHEAP_PRICE_DEADBAND, DEFAULT_CHEAP_PRICE_DEADBAND
            )
        )
        data.cheap_charge_stop_price = round(data.effective_cheap_price + deadband, 2)

        # ---- Step 9: forecast_spike_within_window ----
        lookahead = float(
            self.entry.options.get(
                CONF_FORECAST_LOOKAHEAD_HOURS, DEFAULT_FORECAST_LOOKAHEAD_HOURS
            )
        )
        cutoff = now_dt + timedelta(hours=lookahead)
        data.forecast_spike_within_window = self._scan_forecast_for_spike(
            data.feed_in_forecast, now_dt, cutoff
        )
        data.max_forecast_price = self._max_forecast_price(
            data.feed_in_forecast, now_dt, cutoff
        )

        # ---- Step 10: forecast_expensive_period_coming ----
        data.forecast_expensive_period_coming = self._scan_forecast_for_spike(
            data.general_forecast, now_dt, cutoff
        )

        # ---- Step 11: hold_justified ----
        self._compute_hold_justified(data, now_dt, cutoff)

        # ---- Step 12: solar_weighted_avg_fit ----
        self._compute_solar_weighted_avg_fit(data, now_dt, target_hour, after_dw)

        # ---- Step 13: solar_export_hold_justified ----
        self._compute_solar_export_hold_justified(data, before_dw)

        # ---- Step 14: active_mode ----
        self._compute_active_mode(data, now_dt)

        # ---- Step 15: decision_log ----
        # Only add to decision log after startup grace period (when values are populated)
        if (
            data.active_mode != self._previous_active_mode
            and self._previous_active_mode is not None
        ):
            self._add_to_decision_log(data, now_dt)

    def _compute_solar_battery_forecast(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        target_hour: int,
        before_dw: bool,
        after_dw: bool,
    ) -> None:
        """Compute solar battery SOC forecast."""
        target_pct = float(
            self.entry.options.get(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET)
        )

        if after_dw:
            # After DW start: report current SOC, safe defaults
            # Check if sun is down for accurate overnight assessment
            sun_entity_id = self._get_entity_id(CONF_SUN_ENTITY)
            sun_state = self.hass.states.get(sun_entity_id)
            sun_up = sun_state is not None and sun_state.state == "above_horizon"

            # If sun is down and SOC not at target, can't reach target via solar
            can_reach = data.soc >= target_pct or sun_up

            data.solar_battery_forecast = {
                "predicted_soc": round(data.soc, 1),
                "solar_before_dw_kwh": 0.0,
                "consumption_estimate_kwh": 0.0,
                "net_solar_kwh": 0.0,
                "deficit_kwh": 0.0,
                "can_reach_target": can_reach,
                "boost_needed": False,
                "hours_to_target_time": 0.0,
            }
            # Mark target reached if SOC is there
            if data.soc >= target_pct:
                data.target_reached_today = True
        else:
            # Hours remaining until DW start
            target_dt = now_dt.replace(
                hour=target_hour, minute=0, second=0, microsecond=0
            )
            hours_to_target = max((target_dt - now_dt).total_seconds() / 3600, 0)

            # Deficit: kWh needed to reach target
            deficit_kwh = max((target_pct - data.soc) / 100 * BATTERY_CAPACITY_KWH, 0)

            # Solar forecast: pessimistic estimate between now and DW
            solar_kwh = self._sum_solar_before_target(
                data.solcast_today, now_dt, target_hour
            )

            # Consumption estimate: current load extrapolated
            # Use historical average load if available, otherwise use current load
            expected_load_kw = self._get_expected_load_kw(data, hours_to_target)
            consumption_kwh = expected_load_kw * hours_to_target

            # Net solar (after consumption)
            net_solar = solar_kwh - consumption_kwh

            # Predicted SOC at DW
            net_solar_pct = net_solar / BATTERY_CAPACITY_KWH * 100
            predicted_soc = data.soc + net_solar_pct

            # Can solar alone reach target?
            can_reach = data.soc >= target_pct or net_solar >= deficit_kwh

            # Boost needed? Only if gentle charging can't reach target before DW
            if data.soc >= target_pct:
                boost_needed = False
            else:
                remaining_deficit = max(deficit_kwh - max(net_solar, 0), 0)
                time_needed_hours = (
                    remaining_deficit / (CHARGE_RATE_BACKUP_KW * 0.9)
                    if remaining_deficit > 0
                    else 0
                )
                boost_needed = (
                    time_needed_hours > (hours_to_target - 0.5)
                    and remaining_deficit > 0
                )

            data.solar_battery_forecast = {
                "predicted_soc": round(predicted_soc, 1),
                "solar_before_dw_kwh": round(solar_kwh, 2),
                "consumption_estimate_kwh": round(consumption_kwh, 2),
                "net_solar_kwh": round(net_solar, 2),
                "deficit_kwh": round(deficit_kwh, 2),
                "can_reach_target": can_reach,
                "boost_needed": boost_needed,
                "hours_to_target_time": round(hours_to_target, 1),
            }

            # Mark target reached if SOC is there
            if data.soc >= target_pct:
                data.target_reached_today = True

            # Store forecast history when hour changes (for planned vs actual chart)
            current_hour = now_dt.hour
            if (
                self._last_forecast_hour is None
                or current_hour != self._last_forecast_hour
            ):
                self._store_forecast_history(
                    data, now_dt, predicted_soc, solar_kwh, consumption_kwh
                )
                self._last_forecast_hour = current_hour

    def _store_forecast_history(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        predicted_soc: float,
        solar_kwh: float,
        consumption_kwh: float,
    ) -> None:
        """Store forecast prediction to history for planned vs actual comparison."""
        entry = {
            "timestamp": now_dt.isoformat(),
            "predicted_soc": round(predicted_soc, 1),
            "solar_before_dw_kwh": round(solar_kwh, 2),
            "consumption_estimate_kwh": round(consumption_kwh, 2),
        }
        data.forecast_history.append(entry)

        # Keep only last 48 entries (2 days of hourly data)
        if len(data.forecast_history) > 48:
            data.forecast_history = data.forecast_history[-48:]

    def _compute_effective_cheap_price(
        self, data: CoordinatorData, now_dt: datetime, before_dw: bool, target_hour: int
    ) -> None:
        """Compute the effective cheap price threshold."""
        # Calculate base from percentile of forecast prices
        lookahead = float(
            self.entry.options.get(
                CONF_FORECAST_LOOKAHEAD_HOURS, DEFAULT_FORECAST_LOOKAHEAD_HOURS
            )
        )
        cutoff = now_dt + timedelta(hours=lookahead)

        # Collect forecast prices within lookahead window
        forecast_prices = []
        for f in data.general_forecast:
            start = self._parse_forecast_dt(f.get("start_time"))
            if start is None:
                continue
            start_local = dt_util.as_local(start)
            if start_local >= now_dt and start_local <= cutoff:
                forecast_prices.append(float(f.get("per_kwh", 0)))

        # Calculate percentile-based cheap price
        percentile = float(
            self.entry.options.get(
                CONF_CHEAP_PRICE_PERCENTILE, DEFAULT_CHEAP_PRICE_PERCENTILE
            )
        )
        if forecast_prices:
            base = round(self._percentile(forecast_prices, percentile), 2)
        else:
            # Fallback to max_precharge_price if no forecast data
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

            # Find minimum forecast price before DW
            min_forecast = max_price
            for f in data.general_forecast:
                start = self._parse_forecast_dt(f.get("start_time"))
                if start is None:
                    continue
                start_local = dt_util.as_local(start)
                if start_local >= now_dt and start_local.hour < target_hour:
                    price = float(f.get("per_kwh", max_price))
                    if price < min_forecast:
                        min_forecast = price

            forecast_floor = max(min_forecast + 0.02, base)
            final = min(urgency_price, max_price)
            final = max(final, forecast_floor)
            data.effective_cheap_price = round(final, 2)

    def _compute_hold_justified(
        self, data: CoordinatorData, now_dt: datetime, cutoff: datetime
    ) -> None:
        """Compute whether holding battery is justified."""
        # Check 1: meaningful solar (>= 0.5 kWh) within lookahead
        solar_kwh_lookahead = 0.0
        for forecast_list in [data.solcast_today, data.solcast_tomorrow]:
            for period in forecast_list:
                period_start = self._parse_forecast_dt(period.get("period_start"))
                if period_start is None:
                    continue
                ps_local = dt_util.as_local(period_start)
                if ps_local >= now_dt and ps_local <= cutoff:
                    solar_kwh_lookahead += float(period.get("pv_estimate10", 0))

        # Check 2: financially justified price savings within lookahead
        min_future_price = 0.99
        for f in data.general_forecast:
            start = self._parse_forecast_dt(f.get("start_time"))
            if start is None:
                continue
            start_local = dt_util.as_local(start)
            if start_local >= now_dt and start_local <= cutoff:
                price = float(f.get("per_kwh", 0.99))
                if price < min_future_price:
                    min_future_price = price

        # Calculate savings as percentage
        price_drop_pct = 0.0
        if data.general_price > 0:
            price_drop_pct = (
                (data.general_price - min_future_price) / data.general_price
            ) * 100

        # Read configurable thresholds
        min_savings_percent = float(
            self.entry.options.get(
                CONF_HOLD_MIN_SAVINGS_PERCENT, DEFAULT_HOLD_MIN_SAVINGS_PERCENT
            )
        )
        absolute_cheap_threshold = float(
            self.entry.options.get(
                CONF_HOLD_ABSOLUTE_CHEAP_THRESHOLD,
                DEFAULT_HOLD_ABSOLUTE_CHEAP_THRESHOLD,
            )
        )

        cheap_coming = (
            price_drop_pct > min_savings_percent
            or min_future_price < absolute_cheap_threshold
        )

        # Don't justify holding overnight (10pm-6am) when there's no sun to charge
        # This prevents unnecessary hold mode triggered by:
        # 1. Price drops when solar charging isn't possible anyway
        # 2. Solar forecasts when there's no sun to benefit
        overnight_hours = now_dt.hour >= 22 or now_dt.hour < 6
        if overnight_hours and not data.solar_can_reach_target:
            cheap_coming = False
            # Also disable solar-based hold justification overnight
            solar_kwh_lookahead = 0.0

        data.hold_justified = solar_kwh_lookahead >= 0.5 or cheap_coming

    def _compute_solar_weighted_avg_fit(
        self, data: CoordinatorData, now_dt: datetime, target_hour: int, after_dw: bool
    ) -> None:
        """Compute solar-weighted average feed-in tariff."""
        if after_dw:
            data.solar_weighted_avg_fit = 0.0
            data.solar_remaining_kwh = 0.0
        else:
            weighted_sum = 0.0
            total_solar = 0.0

            for period in data.solcast_today:
                period_start = self._parse_forecast_dt(period.get("period_start"))
                if period_start is None:
                    continue
                ps_local = dt_util.as_local(period_start)
                if ps_local >= now_dt and ps_local.hour < target_hour:
                    solar_kwh_val = float(period.get("pv_estimate10", 0))
                    if solar_kwh_val > 0:
                        # Find FIT price at midpoint of 30-min period
                        mid = period_start + timedelta(minutes=15)
                        fit_price = 0.0
                        for f in data.feed_in_forecast:
                            f_start = self._parse_forecast_dt(f.get("start_time"))
                            f_end = self._parse_forecast_dt(f.get("end_time"))
                            if (
                                f_start is not None
                                and f_end is not None
                                and f_start <= mid < f_end
                            ):
                                fit_price = float(f.get("per_kwh", 0))
                                break

                        weighted_sum += solar_kwh_val * fit_price
                        total_solar += solar_kwh_val

            if total_solar > 0:
                data.solar_weighted_avg_fit = round(weighted_sum / total_solar, 4)
            else:
                data.solar_weighted_avg_fit = 0.0
            data.solar_remaining_kwh = round(total_solar, 2)

    def _compute_solar_export_hold_justified(
        self, data: CoordinatorData, before_dw: bool
    ) -> None:
        """Compute whether solar export hold is justified."""
        # Safe default: assume sun is down if entity unavailable
        sun_entity_id = self._get_entity_id(CONF_SUN_ENTITY)
        sun_state = self.hass.states.get(sun_entity_id)
        sun_up = sun_state is not None and sun_state.state == "above_horizon"
        if sun_state is None:
            _LOGGER.debug(
                "Sun entity %s not found, assuming sun is down", sun_entity_id
            )

        deficit_kwh = data.solar_battery_forecast.get("deficit_kwh", 0)
        net_solar_kwh = data.solar_battery_forecast.get("net_solar_kwh", 0)
        current_fit = data.feed_in_price
        avg_fit = data.solar_weighted_avg_fit
        in_solar_export_hold = data.solar_export_hold
        charging = data.force_charge_active

        if (
            not sun_up
            or not before_dw
            or data.demand_window_active
            or deficit_kwh <= 0
            or charging
        ):
            data.solar_export_hold_justified = False
            data.surplus_ratio = 0.0
        else:
            surplus_ratio = net_solar_kwh / deficit_kwh if deficit_kwh > 0 else 0
            data.surplus_ratio = round(surplus_ratio, 2)
            threshold = (
                SOLAR_EXPORT_SURPLUS_STAY
                if in_solar_export_hold
                else SOLAR_EXPORT_SURPLUS_ENTRY
            )
            data.solar_export_hold_justified = (
                surplus_ratio >= threshold and current_fit > avg_fit and avg_fit > 0
            )

    def _compute_active_mode(self, data: CoordinatorData, now_dt: datetime) -> None:
        """Compute the active battery mode."""
        automation_enabled = self._get_switch_state("automation_enabled")
        spike_discharge_enabled = self._get_switch_state("spike_discharge_enabled")

        # Check if we're in the valid discharge window (6am-midnight)
        current_hour = now_dt.hour
        in_discharge_window = current_hour >= DISCHARGE_EARLIEST_HOUR

        # Check sun status
        sun_entity_id = self._get_entity_id(CONF_SUN_ENTITY)
        sun_state = self.hass.states.get(sun_entity_id)
        sun_up = sun_state is not None and sun_state.state == "above_horizon"

        # Debug: Log state when considering HOLD mode
        if data.hold_justified or data.forecast_spike_within_window:
            _LOGGER.debug(
                "Hold mode consideration at %s: hold_justified=%s, "
                "hold_mode=%s, solar_export_hold=%s, "
                "forecast_spike=%s, solar_can_reach=%s, sun_up=%s, "
                "price=%.2f, stop_price=%.2f",
                now_dt.strftime("%H:%M"),
                data.hold_justified,
                data.hold_mode,
                data.solar_export_hold,
                data.forecast_spike_within_window,
                data.solar_can_reach_target,
                sun_up,
                data.general_price,
                data.cheap_charge_stop_price,
            )

        if not automation_enabled:
            data.active_mode = BatteryMode.MANUAL
        elif data.demand_window_active:
            data.active_mode = BatteryMode.DEMAND_BLOCK
        elif data.price_spike and spike_discharge_enabled and in_discharge_window:
            data.active_mode = BatteryMode.SPIKE_DISCHARGE
        elif data.manual_override:
            data.active_mode = BatteryMode.MANUAL
        elif data.solar_export_hold and data.hold_mode:
            data.active_mode = BatteryMode.SOLAR_EXPORT_HOLD
        elif data.general_price < data.effective_cheap_price:
            # Price below threshold — consider charging
            precharge_threshold = float(
                self.entry.options.get(
                    CONF_PRECHARGE_BATTERY_THRESHOLD,
                    DEFAULT_PRECHARGE_BATTERY_THRESHOLD,
                )
            )
            battery_low = data.soc < precharge_threshold
            expensive_coming = data.forecast_expensive_period_coming
            solar_gap_flag = not data.solar_can_reach_target

            if data.target_reached_today:
                data.active_mode = BatteryMode.SELF_CONSUMPTION
            elif sun_up and (solar_gap_flag or expensive_coming):
                if data.boost_charge_needed:
                    data.active_mode = BatteryMode.BOOST_CHARGING
                else:
                    data.active_mode = BatteryMode.GRID_CHARGING
            elif not sun_up and battery_low and expensive_coming:
                data.active_mode = BatteryMode.GRID_CHARGING
            else:
                data.active_mode = BatteryMode.SELF_CONSUMPTION
        elif data.general_price < data.cheap_charge_stop_price:
            # Price in deadband — maintain charge or hold
            if data.force_charge_active:
                if data.boost_charge_active:
                    data.active_mode = BatteryMode.BOOST_CHARGING
                else:
                    data.active_mode = BatteryMode.GRID_CHARGING
            else:
                if data.hold_justified:
                    data.active_mode = BatteryMode.HOLD
                else:
                    data.active_mode = BatteryMode.SELF_CONSUMPTION
        elif data.forecast_spike_within_window:
            # Don't hold for spikes overnight (10pm-6am) when there's no sun to benefit
            overnight_hours = now_dt.hour >= 22 or now_dt.hour < 6
            if not (overnight_hours and not data.solar_can_reach_target):
                data.active_mode = BatteryMode.HOLDING_FOR_SPIKE
            else:
                data.active_mode = BatteryMode.SELF_CONSUMPTION
        else:
            data.active_mode = BatteryMode.SELF_CONSUMPTION

    def _add_to_decision_log(self, data: CoordinatorData, now_dt: datetime) -> None:
        """Add entry to decision log when mode changes."""
        old_mode = self._previous_active_mode
        new_mode = data.active_mode
        entry = {
            "timestamp": now_dt.isoformat(),
            "old_mode": old_mode if old_mode else "unknown",
            "new_mode": new_mode,
            "buy_price": round(data.general_price, 2),
            "sell_price": round(data.feed_in_price, 2),
            "soc": round(data.soc),
            "effective_threshold": data.effective_cheap_price,
            "reason": f"Mode changed: {old_mode} -> {new_mode}",
        }
        data.decision_log.append(entry)
        # Cap log at 50 entries
        if len(data.decision_log) > 50:
            data.decision_log = data.decision_log[-50:]

        self._previous_active_mode = new_mode

    def _get_expected_load_kw(
        self, data: CoordinatorData, hours_to_target: float
    ) -> float:
        """Calculate expected load based on 7-day historical averages."""
        load_entity_id = self._get_entity_id("teslemetry_load_power")

        # Get cached historical hourly averages
        hourly_avg_kw = self._get_historical_hourly_averages(load_entity_id)

        if hourly_avg_kw:
            # Sum hourly averages from current hour until demand window
            now_dt = dt_util.now()
            dw_start_time = self._parse_time_option(
                CONF_DEMAND_WINDOW_START, DEFAULT_DEMAND_WINDOW_START
            )
            target_hour = dw_start_time.hour
            current_hour = now_dt.hour

            total_expected_kwh = 0.0
            hour = current_hour

            # Sum hours from now until target hour
            while hour != target_hour:
                if hour in hourly_avg_kw:
                    total_expected_kwh += hourly_avg_kw[hour]
                hour = (hour + 1) % 24
                # Safety: don't loop forever
                if hour == current_hour:
                    break

            # Add 10% buffer to be conservative
            total_expected_kwh *= 1.1

            if total_expected_kwh > 0:
                return total_expected_kwh

        # Fallback to current load or default
        current_load = data.load_power_kw if hasattr(data, "load_power_kw") else 0
        return (
            (current_load * hours_to_target)
            if current_load > 0
            else (0.5 * hours_to_target)
        )

    def _get_historical_hourly_averages(self, entity_id: str) -> dict[int, float]:
        """Get 7-day hourly averages, cached until midnight."""
        now = dt_util.now()
        today_str = now.strftime("%Y-%m-%d")

        # Check if cache is valid for today
        if (
            self._historical_load_cache_date == today_str
            and self._historical_load_cache
        ):
            return self._historical_load_cache

        # Cache expired or empty - fetch new data
        start_time = now - timedelta(days=7)

        try:
            import requests

            ha_url = self.hass.config.api.base_url
            url = f"{ha_url}/api/history/period/{start_time.isoformat()}"
            headers = {
                "Authorization": f"Bearer {self.hass.config.api.token}",
                "Content-Type": "application/json",
            }
            params = {"filter_entity_id": entity_id, "end_time": now.isoformat()}

            response = requests.get(url, headers=headers, params=params, timeout=10)

            if response.status_code != 200:
                return {}

            data = response.json()
            if not data or len(data) == 0:
                return {}

            # Calculate average from historical data points
            values = []
            for state_list in data:
                for state in state_list:
                    if state.get("state") not in (None, "unavailable", "unknown"):
                        try:
                            val = float(state["state"])
                            values.append(val)
                        except (ValueError, TypeError):
                            pass

            if not values:
                return {}

            # Calculate average power in kW
            state = self.hass.states.get(entity_id)
            unit = state.attributes.get("unit_of_measurement", "") if state else ""

            avg_value = sum(values) / len(values)

            # Convert to kW if necessary
            if unit == "W":
                avg_kw = avg_value / 1000.0
            elif unit == "kW":
                avg_kw = avg_value
            else:
                _LOGGER.debug("Unknown unit %s for %s, assuming kW", unit, entity_id)
                avg_kw = avg_value

            # Store in cache
            self._historical_load_cache = {h: avg_kw for h in range(24)}
            self._historical_load_cache_date = today_str

            return self._historical_load_cache

        except Exception as e:
            _LOGGER.debug("Failed to get historical hourly averages: %s", e)
            return {}

    def _parse_time_option(self, key: str, default: str) -> time:
        """Parse a time string option (HH:MM:SS) into a time object."""
        time_str = str(self.entry.options.get(key, default))
        parts = time_str.split(":")
        try:
            return time(
                int(parts[0]),
                int(parts[1]) if len(parts) > 1 else 0,
                int(parts[2]) if len(parts) > 2 else 0,
            )
        except (ValueError, IndexError):
            d_parts = default.split(":")
            return time(int(d_parts[0]), int(d_parts[1]), int(d_parts[2]))

    @staticmethod
    def _parse_forecast_dt(dt_str: str | None) -> datetime | None:
        """Parse an ISO format datetime string from forecast data."""
        if dt_str is None:
            return None
        try:
            return dt_util.parse_datetime(str(dt_str))
        except (ValueError, TypeError):
            return None

    def _sum_solar_before_target(
        self,
        solcast: list[dict[str, Any]],
        now_dt: datetime,
        target_hour: int,
    ) -> float:
        """Sum pessimistic solar kWh (pv_estimate10) from now until target_hour."""
        target_dt = now_dt.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        period_duration = timedelta(minutes=30)
        total = 0.0
        for period in solcast:
            period_start = self._parse_forecast_dt(period.get("period_start"))
            if period_start is None:
                continue
            ps_local = dt_util.as_local(period_start)
            period_end = ps_local + period_duration
            kwh = float(period.get("pv_estimate10", 0))

            if ps_local >= target_dt:
                # Period starts at or after the target — skip
                continue

            if ps_local >= now_dt:
                # Fully future period before target — include all of it
                total += kwh
            elif period_end > now_dt:
                # In-progress period — prorate remaining fraction
                remaining = (period_end - now_dt).total_seconds()
                fraction = remaining / period_duration.total_seconds()
                total += kwh * fraction

        return total

    @staticmethod
    def _scan_forecast_for_spike(
        forecasts: list[dict[str, Any]],
        now_dt: datetime,
        cutoff: datetime,
    ) -> bool:
        """Return True if any forecast has spike_status == 'spike' in window."""
        for f in forecasts:
            start = ComputationEngine._parse_forecast_dt(f.get("start_time"))
            if start is None:
                continue
            start_local = dt_util.as_local(start)
            if start_local >= now_dt and start_local <= cutoff:
                if f.get("spike_status") == "spike":
                    return True
        return False

    @staticmethod
    def _max_forecast_price(
        forecasts: list[dict[str, Any]],
        now_dt: datetime,
        cutoff: datetime,
    ) -> float:
        """Return the maximum per_kwh price from forecasts within the window."""
        max_price = 0.0
        for f in forecasts:
            start = ComputationEngine._parse_forecast_dt(f.get("start_time"))
            if start is None:
                continue
            start_local = dt_util.as_local(start)
            if start_local >= now_dt and start_local <= cutoff:
                price = float(f.get("per_kwh", 0))
                if price > max_price:
                    max_price = price
        return round(max_price, 2)

    @staticmethod
    def _percentile(
        prices: list[float],
        percentile: float,
    ) -> float:
        """Calculate the Nth percentile of a list of prices."""
        if not prices:
            return 0.0
        sorted_prices = sorted(prices)
        n = len(sorted_prices)
        index = (percentile / 100) * (n - 1)
        lower = int(index)
        upper = lower + 1
        if upper >= n:
            return sorted_prices[-1]
        fraction = index - lower
        return sorted_prices[lower] * (1 - fraction) + sorted_prices[upper] * fraction

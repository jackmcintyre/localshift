"""Computation engine for derived values and forecasts."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .computation_engine_lib import (
    ForecastComputer,
    HistoryFetcher,
    build_hourly_forecast_summary,
    max_forecast_price,
    parse_forecast_dt,
    percentile,
    scan_forecast_for_spike,
    sum_solar_before_target,
)
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
    CONF_SUN_ENTITY,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_DEADBAND,
    DEFAULT_CHEAP_PRICE_PERCENTILE,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    DEFAULT_HOLD_ABSOLUTE_CHEAP_THRESHOLD,
    DEFAULT_HOLD_MIN_SAVINGS_PERCENT,
    DEFAULT_LOAD_WEIGHT_RECENT,
    DEFAULT_MAX_PRECHARGE_PRICE,
    DISCHARGE_EARLIEST_HOUR,
    SOLAR_EXPORT_SURPLUS_ENTRY,
    SOLAR_EXPORT_SURPLUS_STAY,
    BatteryMode,
)
from .coordinator_data import CoordinatorData

_LOGGER = logging.getLogger(__name__)


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
            soc: Current battery SOC percentage
            price: Current buy price ($/kWh)
            feed_in_price: Current feed-in price ($/kWh)
            now_dt: Current datetime
            force: If True, skip checks and recompute

        Returns:
            (should_recompute, reason)
            reason is a string for logging (e.g., "price_change_0.15")
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

        # History fetcher for historical load data (delegated to separate module)
        self._history_fetcher = HistoryFetcher(hass, entry)

        # Forecast computer for 15-minute battery SOC forecasting
        self._forecast_computer = ForecastComputer(
            entry, get_entity_id_func, self._get_historical_hourly_averages
        )

        # Change tracker for forecast regeneration
        self._forecast_change_tracker = ForecastChangeTracker()

        # Local cache properties (delegated to history_fetcher for storage)
        self._last_weighting: float = DEFAULT_LOAD_WEIGHT_RECENT
        self._previous_active_mode = None
        self._last_forecast_hour: int | None = None
        self._last_decision_log_time: datetime | None = None

    # ========================================================================
    # MAIN ENTRY POINT
    # ========================================================================

    def compute_derived_values(self, data: CoordinatorData) -> None:
        """Compute all derived sensor/binary_sensor values from raw state.

        Ported from Jinja templates in YAML package. Steps are ordered
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
        # Add entry when mode changes OR periodically for status updates
        mode_changed = (
            data.active_mode != self._previous_active_mode
            and self._previous_active_mode is not None
        )

        # Only skip logging during initial startup when all data is zero
        # Once we have valid data, always log mode changes and periodic updates
        if self._last_decision_log_time is None and (
            data.general_price == 0 or data.feed_in_price == 0 or data.soc == 0
        ):
            _LOGGER.debug("Skipping decision log - sensor data not yet populated")
        elif mode_changed:
            self._add_to_decision_log(data, now_dt, mode_change=True)
        elif self._last_decision_log_time is None:
            # First evaluation after startup - log initial state
            self._add_to_decision_log(data, now_dt, mode_change=False)
        elif (now_dt - self._last_decision_log_time) >= timedelta(minutes=5):
            # Periodic status update every 5 minutes
            self._add_to_decision_log(data, now_dt, mode_change=False)

        # ---- Step 16: daily_forecast ----
        self._compute_daily_15min_forecast(data, now_dt)

    # ========================================================================
    # SOLAR & BATTERY FORECASTING
    # ========================================================================

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

            # Mark target reached if SOC is there
            target_reached = data.soc >= target_pct
            if target_reached:
                data.target_reached_today = True

            data.solar_battery_forecast = {
                "predicted_soc": round(data.soc, 1),
                "solar_before_dw_kwh": 0.0,
                "consumption_estimate_kwh": 0.0,
                "net_solar_kwh": 0.0,
                "deficit_kwh": 0.0,
                "can_reach_target": can_reach,
                "boost_needed": False,
                "hours_to_target_time": 0.0,
                "target_reached_today": target_reached,
            }
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

            # Predicted SOC at DW (clamped to 0-100%)
            net_solar_pct = net_solar / BATTERY_CAPACITY_KWH * 100
            predicted_soc = max(0.0, min(100.0, data.soc + net_solar_pct))

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

            # Mark target reached if SOC is there
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

    def _compute_daily_15min_forecast(
        self,
        data: CoordinatorData,
        now_dt: datetime,
    ) -> None:
        """Compute full 24-hour forecast with 15-minute breakdown (delegates to ForecastComputer).

        Provides 4x granularity over hourly forecast, capturing meaningful
        price variations from Amber's 5-minute pricing data.

        Uses change detection to skip unnecessary recomputations.
        """
        # Check if recompute is needed
        should_recompute, reason = (
            self._forecast_change_tracker.should_recompute_forecast(
                soc=data.soc,
                price=data.general_price,
                feed_in_price=data.feed_in_price,
                now_dt=now_dt,
            )
        )

        if should_recompute:
            _LOGGER.info("Recomputing forecast: %s", reason)

            # Get historical hourly averages
            load_entity_id = self._get_entity_id("teslemetry_load_power")
            hourly_avg_kw = self._get_historical_hourly_averages(load_entity_id)

            # Get recent 1-hour load for weighted forecasting
            recent_load_kw = self._recent_load_1hr_kw

            # Delegate to ForecastComputer
            (
                data.daily_forecast,
                data.daily_forecast_soc_15min,
                data.forecast_consumption_source_counts,
            ) = self._forecast_computer.compute_forecast(
                data=data,
                now_dt=now_dt,
                historical_avg_kw=hourly_avg_kw,
                recent_load_kw=recent_load_kw,
                historical_load_source=self._historical_load_source,
                historical_load_sample_counts=self._historical_load_sample_counts,
            )

            # Also keep a compact 24-entry hourly view for markdown table
            data.daily_forecast_hourly = build_hourly_forecast_summary(
                data.daily_forecast
            )
        else:
            _LOGGER.debug("Forecast unchanged, skipping recompute")

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
            if not isinstance(f, dict):
                continue
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
                if ps_local >= now_dt and ps_local.hour <= target_hour:
                    solar_kwh_val = float(period.get("pv_estimate10", 0))
                    if solar_kwh_val > 0:
                        # Find FIT price at midpoint of 30-min period
                        # Use local time for both solar midpoint and FIT periods
                        mid_local = ps_local + timedelta(minutes=15)
                        fit_price = 0.0
                        for f in data.feed_in_forecast:
                            f_start = self._parse_forecast_dt(f.get("start_time"))
                            f_end = self._parse_forecast_dt(f.get("end_time"))
                            if f_start is not None and f_end is not None:
                                # Convert FIT period to local time for comparison
                                f_start_local = dt_util.as_local(f_start)
                                f_end_local = dt_util.as_local(f_end)

                                # Check if midpoint falls within FIT period
                                if f_start_local <= mid_local < f_end_local:
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

    def _get_forecast_entry_for_now(
        self, data: CoordinatorData, now_dt: datetime
    ) -> dict | None:
        """Get forecast entry for current time slot.

        Returns None if forecast unavailable or current time
        not in forecast range.
        """
        if not data.daily_forecast:
            return None

        current_hour = now_dt.hour
        current_minute = now_dt.minute

        # Find matching entry
        for entry in data.daily_forecast:
            if entry["hour"] == current_hour and entry["minute"] == current_minute:
                return entry

        return None

    def _compute_active_mode(self, data: CoordinatorData, now_dt: datetime) -> None:
        """Compute active battery mode."""
        automation_enabled = self._get_switch_state("automation_enabled")
        spike_discharge_enabled = self._get_switch_state("spike_discharge_enabled")

        # Check if we're in valid discharge window (6am-midnight)
        current_hour = now_dt.hour
        in_discharge_window = current_hour >= DISCHARGE_EARLIEST_HOUR

        # ========================================================================
        # FORECAST-DRIVED CONTROL (Single Source of Truth)
        # ========================================================================

        # Get forecast entry for current time
        forecast_entry = self._get_forecast_entry_for_now(data, now_dt)

        # Fallback: Forecast unavailable (startup, data corruption, etc.)
        if not forecast_entry:
            _LOGGER.warning("Forecast unavailable, using price-based fallback")
            target_pct = float(
                self.entry.options.get(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET)
            )
            # Simple logic: charge if cheap and below target
            if (
                data.general_price <= data.effective_cheap_price
                and data.soc < target_pct
            ):
                data.active_mode = BatteryMode.GRID_CHARGING
            else:
                data.active_mode = BatteryMode.SELF_CONSUMPTION
            return

        # FORECAST-DRIVED: Grid charging (follow forecast plan)
        if forecast_entry.get("grid_charge_boost"):
            data.active_mode = BatteryMode.BOOST_CHARGING
            _LOGGER.info(
                "Forecast-driven: BOOST_CHARGING at %s", now_dt.strftime("%H:%M")
            )
            return
        elif forecast_entry.get("grid_charge"):
            data.active_mode = BatteryMode.GRID_CHARGING
            _LOGGER.info(
                "Forecast-driven: GRID_CHARGING at %s", now_dt.strftime("%H:%M")
            )
            return

        # ========================================================================
        # OTHER MODES (Non-Charging)
        # ========================================================================

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
        elif data.hold_justified:
            data.active_mode = BatteryMode.HOLD
        elif data.forecast_spike_within_window:
            # Don't hold for spikes overnight (10pm-6am) when there's no sun to benefit
            overnight_hours = now_dt.hour >= 22 or now_dt.hour < 6
            if not (overnight_hours and not data.solar_can_reach_target):
                data.active_mode = BatteryMode.HOLDING_FOR_SPIKE
            else:
                data.active_mode = BatteryMode.SELF_CONSUMPTION
        else:
            data.active_mode = BatteryMode.SELF_CONSUMPTION

    def _add_to_decision_log(
        self, data: CoordinatorData, now_dt: datetime, mode_change: bool
    ) -> None:
        """Add entry to decision log when mode changes or periodically."""
        # Startup check is now handled in calling code (Step 15)
        # This method assumes data is already validated

        old_mode = self._previous_active_mode
        new_mode = data.active_mode

        if mode_change:
            reason = f"Mode changed: {old_mode} -> {new_mode}"
            # Only update previous mode when it actually changed
            self._previous_active_mode = new_mode
        else:
            reason = f"Status update: {new_mode} (no change)"

        entry = {
            "timestamp": now_dt.isoformat(),
            "old_mode": old_mode if old_mode else "unknown",
            "new_mode": new_mode,
            "buy_price": round(data.general_price, 2),
            "sell_price": round(data.feed_in_price, 2),
            "soc": round(data.soc),
            "effective_threshold": data.effective_cheap_price,
            "reason": reason,
        }
        data.decision_log.append(entry)
        # Cap log at 50 entries
        if len(data.decision_log) > 50:
            data.decision_log = data.decision_log[-50:]

        self._last_decision_log_time = now_dt

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
                return total_expected_kwh / max(
                    hours_to_target, 1
                )  # Return average kW, not total kWh

        # Fallback to current load or default
        current_load = data.load_power_kw if hasattr(data, "load_power_kw") else 0
        return current_load if current_load > 0 else 0.5

    async def async_get_historical_hourly_averages(
        self, entity_id: str
    ) -> tuple[dict[int, float], dict[int, int], str]:
        """Get 7-day hourly averages via thread pool, cached until midnight.

        Returns: (hourly_avg_kw, sample_counts, source)
        """
        return await self._history_fetcher.async_get_historical_hourly_averages(
            entity_id
        )

    async def async_get_recent_load_1hr(self, entity_id: str) -> float:
        """Get average load over the last 1 hour from HA statistics.

        Returns: Average power in kW over last hour, or 0.0 if unavailable.
        """
        return await self._history_fetcher.async_get_recent_load_1hr(entity_id)

    @property
    def _historical_load_cache(self) -> dict[int, float]:
        """Get cached hourly averages from history fetcher."""
        return self._history_fetcher._historical_load_cache

    @property
    def _historical_load_sample_counts(self) -> dict[int, int]:
        """Get sample counts from history fetcher."""
        return self._history_fetcher._historical_load_sample_counts

    @property
    def _historical_load_source(self) -> str:
        """Get load source from history fetcher."""
        return self._history_fetcher._historical_load_source

    @property
    def _recent_load_1hr_kw(self) -> float:
        """Get recent 1hr load from history fetcher."""
        return self._history_fetcher._recent_load_1hr_kw

    @property
    def _recent_load_1hr_statistic_id(self) -> str:
        """Get recent load statistic ID from history fetcher."""
        return self._history_fetcher._recent_load_1hr_statistic_id

    @property
    def _recent_load_1hr_samples(self) -> int:
        """Get recent load samples from history fetcher."""
        return self._history_fetcher._recent_load_1hr_samples

    @property
    def _recent_load_1hr_last_error(self) -> str:
        """Get recent load last error from history fetcher."""
        return self._history_fetcher._recent_load_1hr_last_error

    def _get_historical_hourly_averages(self, entity_id: str) -> dict[int, float]:
        """Get cached hourly averages (sync version for compute_derived_values).

        Returns cached data - actual fetching happens in async_get_historical_hourly_averages.
        """
        return self._history_fetcher.get_cached_hourly_averages()

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
        """Parse an ISO format datetime string from forecast data (delegates to utils)."""
        return parse_forecast_dt(dt_str)

    def _sum_solar_before_target(
        self,
        solcast: list[dict[str, Any]],
        now_dt: datetime,
        target_hour: int,
    ) -> float:
        """Sum pessimistic solar kWh (pv_estimate10) from now until target_hour (delegates to utils)."""
        return sum_solar_before_target(solcast, now_dt, target_hour)

    @staticmethod
    def _scan_forecast_for_spike(
        forecasts: list[dict[str, Any]],
        now_dt: datetime,
        cutoff: datetime,
    ) -> bool:
        """Return True if any forecast has spike_status == 'spike' in window (delegates to utils)."""
        return scan_forecast_for_spike(forecasts, now_dt, cutoff)

    @staticmethod
    def _max_forecast_price(
        forecasts: list[dict[str, Any]],
        now_dt: datetime,
        cutoff: datetime,
    ) -> float:
        """Return maximum per_kwh price from forecasts within window (delegates to utils)."""
        return max_forecast_price(forecasts, now_dt, cutoff)

    @staticmethod
    def _percentile(
        prices: list[float],
        percentile_value: float,
    ) -> float:
        """Calculate Nth percentile of a list of prices (delegates to utils)."""
        return percentile(prices, percentile_value)

    def clear_historical_cache(self) -> None:
        """Clear historical load cache to force refresh on next update."""
        self._history_fetcher.clear_historical_cache()

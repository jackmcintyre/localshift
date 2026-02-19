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
    analyze_spike_window,
    build_hourly_forecast_summary,
    calculate_spike_price_threshold,
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
    CONF_MAX_PRECHARGE_PRICE,
    CONF_SPIKE_PRICE_PERCENTILE,
    CONF_SUN_ENTITY,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_DEADBAND,
    DEFAULT_CHEAP_PRICE_PERCENTILE,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    DEFAULT_LOAD_WEIGHT_RECENT,
    DEFAULT_MAX_PRECHARGE_PRICE,
    DEFAULT_SPIKE_PRICE_PERCENTILE,
    DISCHARGE_EARLIEST_HOUR,
    SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET,
    SWITCH_SPIKE_DISCHARGE_CONSERVATIVE,
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
        now_t = now_dt.replace(microsecond=0).time()
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

        # ---- Step 3: demand_window_active ----
        dw_block_enabled = self._get_switch_state("demand_window_block")
        data.demand_window_active = (
            dw_block_enabled and now_t >= dw_start_time and now_t < dw_end_time
        )

        # Get target percentage for later use
        target_pct = float(
            self.entry.options.get(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET)
        )

        # ---- Step 7a: effective_cheap_price (BEFORE forecast to break circular dependency) ----
        # Compute effective_cheap_price BEFORE forecast using preliminary solar estimate
        # This breaks the circular dependency where forecast depends on effective_cheap_price
        # which depends on solar_can_reach_target which depends on forecast
        self._compute_effective_cheap_price_preliminary(
            data, now_dt, before_dw, target_hour, target_pct
        )

        # Set allow_dw_entry_under_target flag on data for forecast_computer
        # This allows grid charging decision to simulate to DW END instead of DW START
        # when solar can reach target within the DW period
        allow_dw_under_target = self._get_switch_state(
            SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET
        )
        data.allow_dw_entry_under_target = allow_dw_under_target and before_dw

        # ---- Step 4/16: daily_forecast (detailed 15-min forecast) ----
        # Compute detailed forecast AFTER effective_cheap_price is set
        # This is the single source of truth
        self._compute_daily_15min_forecast(data, now_dt)

        # ---- Step 5: solar_can_reach_target (derived from detailed forecast) ----
        # Derive from detailed forecast - single source of truth
        dw_entry = self._get_forecast_at_demand_window(data, target_hour)
        if dw_entry:
            data.solar_can_reach_target = dw_entry["predicted_soc"] >= target_pct
            data.boost_charge_needed = dw_entry.get("grid_charge_boost", False)
        else:
            # Fallback if forecast doesn't span to DW (e.g., late in day)
            # Use current SOC as a conservative estimate
            data.solar_can_reach_target = data.soc >= target_pct
            data.boost_charge_needed = False

        # ---- Step 6: boost_charge_needed ----
        # (already set in Step 5 above)

        # ---- Step 6b: solar_can_reach_target_in_dw ----
        # Read from switch state (device-level toggle)
        allow_dw_under_target = self._get_switch_state(
            SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET
        )

        if allow_dw_under_target and before_dw:
            # Simulate solar-only charging through entire DW period
            dw_end_time = self._parse_time_option(
                CONF_DEMAND_WINDOW_END, DEFAULT_DEMAND_WINDOW_END
            )
            sim_end = now_dt.replace(
                hour=dw_end_time.hour,
                minute=dw_end_time.minute,
                second=0,
                microsecond=0,
            )

            # Get historical averages and recent load for simulation
            load_entity_id = self._get_entity_id("teslemetry_load_power")
            hourly_avg_kw = self._get_historical_hourly_averages(load_entity_id)
            recent_load_kw = self._recent_load_1hr_kw

            # Get all Solcast forecasts
            all_solcast = [*data.solcast_today, *data.solcast_tomorrow]

            # Simulate solar-only charging through DW period
            soc_at_end, max_soc, can_reach = (
                self._forecast_computer._simulate_future_soc_with_solar_only(
                    actual_current_soc=data.soc,
                    start_slot=now_dt,
                    target_pct=target_pct,
                    all_solcast=all_solcast,
                    historical_avg_kw=hourly_avg_kw,
                    current_load_kw=data.load_power_kw,
                    recent_load_kw=recent_load_kw,
                    dw_start_time=dw_start_time,
                    end_time=sim_end,
                )
            )
            data.solar_can_reach_target_in_dw = can_reach

            _LOGGER.info(
                "DW entry check: current SOC=%.1f%%, target=%d%%, "
                "DW end=%s, solar can reach=%s",
                data.soc,
                target_pct,
                dw_end_time.strftime("%H:%M"),
                can_reach,
            )
        else:
            data.solar_can_reach_target_in_dw = False

        # ---- Step 7: effective_cheap_price (final update) ----
        # Update effective_cheap_price with actual solar_can_reach_target from forecast
        self._compute_effective_cheap_price(data, now_dt, before_dw, target_hour)

        # ---- Step 8: cheap_charge_stop_price ----
        deadband = float(
            self.entry.options.get(
                CONF_CHEAP_PRICE_DEADBAND, DEFAULT_CHEAP_PRICE_DEADBAND
            )
        )
        data.cheap_charge_stop_price = round(data.effective_cheap_price + deadband, 2)

        # ---- Step 4: solar_battery_forecast (legacy - for backwards compatibility) ----
        # Kept for API compatibility, but values derived from detailed forecast
        self._compute_solar_battery_forecast(
            data, now_dt, target_hour, before_dw, after_dw
        )

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
        # max_forecast_price tracks the max SELL price (feed-in) for spike detection.
        data.max_forecast_price = self._max_forecast_price(
            data.feed_in_forecast, now_dt, cutoff
        )
        # max_buy_forecast_price tracks the max BUY price for pre-charge decisions. (Fix #3)
        data.max_buy_forecast_price = self._max_forecast_price(
            data.general_forecast, now_dt, cutoff
        )

        # ---- Step 10: forecast_expensive_period_coming ----
        data.forecast_expensive_period_coming = self._scan_forecast_for_spike(
            data.general_forecast, now_dt, cutoff
        )

        # ---- Step 10b: spike analysis (conservative mode) ----
        self._analyze_spike(data, now_dt)

        # ---- Step 11: solar_weighted_avg_fit ----
        self._compute_solar_weighted_avg_fit(data, now_dt, target_hour, after_dw)

        # ---- Step 12: active_mode ----

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
        # (computed earlier; left intentionally blank)

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

            # Use detailed forecast if available (includes grid charging)
            # This aligns with the binary sensor solar_can_reach_target
            dw_entry = self._get_forecast_at_demand_window(data, target_hour)
            if dw_entry:
                predicted_soc = dw_entry["predicted_soc"]
                can_reach = predicted_soc >= target_pct
            else:
                # Fallback to current SOC
                predicted_soc = data.soc
                can_reach = data.soc >= target_pct or sun_up

            # Boost_needed indicates if solar alone can reach target (for dashboard display)
            # After DW, boost is not applicable
            boost_needed = False

            # Mark target reached if SOC is there
            target_reached = data.soc >= target_pct
            if target_reached:
                data.target_reached_today = True

            # Calculate hours until next demand window (with day rollover for after_dw case)
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
        else:
            # Before DW: use detailed 15-min forecast for consistency
            # This ensures can_reach_target matches the binary sensor
            # (both now include grid charging effects)
            dw_entry = self._get_forecast_at_demand_window(data, target_hour)

            if dw_entry:
                # Use detailed forecast - includes grid charging effects
                predicted_soc = dw_entry["predicted_soc"]
                can_reach = predicted_soc >= target_pct

                # For boost_needed: calculate if solar ALONE can reach target
                # (without grid charging) - this is the "solar gap" indicator
                deficit_kwh = max(
                    (target_pct - data.soc) / 100 * BATTERY_CAPACITY_KWH, 0
                )

                # Solar forecast: pessimistic estimate between now and DW
                solar_kwh = self._sum_solar_before_target(
                    data.solcast_today, now_dt, target_hour
                )

                # Hours remaining until DW start
                target_dt = now_dt.replace(
                    hour=target_hour, minute=0, second=0, microsecond=0
                )
                hours_to_target = max((target_dt - now_dt).total_seconds() / 3600, 0)

                # Consumption estimate
                expected_load_kw = self._get_expected_load_kw(data, hours_to_target)
                consumption_kwh = expected_load_kw * hours_to_target

                # Net solar (after consumption) - solar only, no grid charging
                net_solar = solar_kwh - consumption_kwh

                # Boost needed if solar alone can't reach target
                boost_needed = data.soc < target_pct and net_solar < deficit_kwh
            else:
                # Fallback if detailed forecast unavailable (shouldn't normally happen)
                # Hours remaining until DW start
                target_dt = now_dt.replace(
                    hour=target_hour, minute=0, second=0, microsecond=0
                )
                hours_to_target = max((target_dt - now_dt).total_seconds() / 3600, 0)

                # Deficit: kWh needed to reach target
                deficit_kwh = max(
                    (target_pct - data.soc) / 100 * BATTERY_CAPACITY_KWH, 0
                )

                # Solar forecast: pessimistic estimate between now and DW
                solar_kwh = self._sum_solar_before_target(
                    data.solcast_today, now_dt, target_hour
                )

                # Consumption estimate: current load extrapolated
                expected_load_kw = self._get_expected_load_kw(data, hours_to_target)
                consumption_kwh = expected_load_kw * hours_to_target

                # Net solar (after consumption)
                net_solar = solar_kwh - consumption_kwh

                # Predicted SOC at DW (clamped to 0-100%)
                net_solar_pct = net_solar / BATTERY_CAPACITY_KWH * 100
                predicted_soc = max(0.0, min(100.0, data.soc + net_solar_pct))

                # Can solar alone reach target? (fallback calculation)
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
        price variations from 5-minute pricing data.

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

            try:
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

                # Propagate recent load diagnostic fields for dashboard debugging
                data.recent_load_1hr_statistic_id = self._recent_load_1hr_statistic_id
                data.recent_load_1hr_samples = self._recent_load_1hr_samples
                data.recent_load_1hr_last_error = self._recent_load_1hr_last_error

            except Exception as e:
                _LOGGER.error("Forecast computation failed: %s", e, exc_info=True)
                # Keep existing forecast if it exists, otherwise set empty
                if not data.daily_forecast:
                    data.daily_forecast = []
                if not data.daily_forecast_soc_15min:
                    data.daily_forecast_soc_15min = []
        else:
            _LOGGER.debug("Forecast unchanged, skipping recompute")

    def _compute_effective_cheap_price_preliminary(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        before_dw: bool,
        target_hour: int,
        target_pct: float,
    ) -> None:
        """Compute preliminary effective cheap price threshold using solar estimate.

        This breaks the circular dependency by using a quick solar estimate instead
        of the actual solar_can_reach_target from the forecast.
        """
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

        # PRELIMINARY SOLAR ESTIMATE: Use simple solar forecast + load estimate
        # This avoids the circular dependency with the detailed forecast
        try:
            solar_kwh = self._sum_solar_before_target(
                data.solcast_today, now_dt, target_hour
            )
        except (AttributeError, TypeError):
            # Handle missing or malformed Solcast data gracefully
            solar_kwh = 0.0
        deficit_kwh = max((target_pct - data.soc) / 100 * BATTERY_CAPACITY_KWH, 0)

        # Estimate consumption using historical averages
        target_dt = now_dt.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        hours_to_target = max((target_dt - now_dt).total_seconds() / 3600, 0)
        expected_load_kw = self._get_expected_load_kw(data, hours_to_target)
        consumption_kwh = expected_load_kw * hours_to_target

        # Preliminary solar gap assessment
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

    def _compute_effective_cheap_price(
        self, data: CoordinatorData, now_dt: datetime, before_dw: bool, target_hour: int
    ) -> None:
        """Compute the final effective cheap price threshold using actual forecast results.

        This is called after the forecast is computed, allowing it to use the actual
        solar_can_reach_target from the detailed forecast simulation.
        """
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

    def _get_forecast_entry_for_now(
        self, data: CoordinatorData, now_dt: datetime
    ) -> dict | None:
        """Get the forecast entry whose slot covers the current moment.

        Strategy: find the most-recent entry whose timestamp ≤ now.  Because
        ``compute_forecast`` now starts from the rounded-down 5-minute boundary
        there is always an entry whose start time ≤ now, so no fallback gap
        logic is required.

        This is granularity-agnostic: it works correctly whether the forecast
        contains 5-minute near-term slots, 15-minute long-term slots, or any
        future mix thereof.

        Also populates debug fields on ``data`` for dashboard troubleshooting.
        """
        # Initialise debug fields
        data.debug_forecast_slot_found = False
        data.debug_forecast_slot_time = ""
        data.debug_first_forecast_slot_time = ""
        data.debug_time_gap_seconds = 0.0

        if not data.daily_forecast:
            return None

        # Record first forecast slot time for debugging
        first_entry = data.daily_forecast[0]
        first_slot_dt = datetime.fromisoformat(first_entry.get("timestamp", ""))
        first_slot_local = dt_util.as_local(first_slot_dt)
        data.debug_first_forecast_slot_time = first_slot_local.strftime("%H:%M:%S")

        # Ensure now_dt is timezone-aware for comparison with tz-aware slot timestamps.
        if now_dt.tzinfo is None:
            now_local = dt_util.as_local(dt_util.as_utc(now_dt))
        else:
            now_local = dt_util.as_local(now_dt)

        # Walk the forecast list and keep track of the most-recent entry whose
        # start time is at or before now.  The list is chronological so we can
        # stop as soon as we pass now.
        best_entry: dict | None = None
        best_slot_local: datetime | None = None

        for entry in data.daily_forecast:
            ts = entry.get("timestamp", "")
            if not ts:
                continue
            slot_dt = datetime.fromisoformat(ts)
            slot_local = dt_util.as_local(slot_dt)

            if slot_local <= now_local:
                best_entry = entry
                best_slot_local = slot_local
            else:
                # List is sorted chronologically; once we're past now we're done.
                break

        if best_entry is not None and best_slot_local is not None:
            data.debug_forecast_slot_found = True
            data.debug_forecast_slot_time = best_slot_local.strftime("%H:%M:%S")
            data.debug_time_gap_seconds = (now_local - best_slot_local).total_seconds()
            _LOGGER.debug(
                "Forecast lookup: now=%s → slot=%s (age=%.0fs, interval=%dmin)",
                now_local.strftime("%H:%M:%S"),
                best_slot_local.strftime("%H:%M:%S"),
                data.debug_time_gap_seconds,
                best_entry.get("slot_interval_minutes", 15),
            )
            return best_entry

        # Forecast hasn't started yet (now_dt is before all slots) — this is
        # theoretically impossible with round-down base_slot but guard anyway.
        time_diff = (first_slot_local - now_local).total_seconds()
        data.debug_time_gap_seconds = time_diff
        _LOGGER.warning(
            "Forecast lookup: now=%s is before first slot %s (gap=%.0fs) — returning None",
            now_local.strftime("%H:%M:%S"),
            first_slot_local.strftime("%H:%M:%S"),
            time_diff,
        )
        return None

    def _get_forecast_at_demand_window(
        self, data: CoordinatorData, target_hour: int
    ) -> dict | None:
        """Get the forecast entry at or just after the demand window start time.

        Finds the first forecast slot whose timestamp is at or after the DW start
        (target_hour:00:00). This handles the case where 15-minute forecast slots
        don't align exactly with the hour boundary (e.g., slots at 14:55, 15:10
        when forecast starts at 09:55).

        This correctly handles the post-DW period: if it is currently 17:00
        and the DW started at 15:00, today's 15:xx entry is in the past and
        is skipped. The next qualifying entry is tomorrow's 15:xx slot.
        If the forecast doesn't span far enough to include a future DW entry,
        ``None`` is returned and callers fall back to the current SOC.
        """
        if not data.daily_forecast:
            return None

        # Normalise now to tz-aware local time (test mocks may return naive datetimes).
        now_raw = dt_util.now()
        if now_raw.tzinfo is None:
            now_local = dt_util.as_local(dt_util.as_utc(now_raw))
        else:
            now_local = dt_util.as_local(now_raw)

        # Calculate the DW start datetime for comparison
        # DW start is at target_hour:00:00
        dw_start_dt = now_local.replace(
            hour=target_hour, minute=0, second=0, microsecond=0
        )
        # If DW start is in the past, look for tomorrow's DW
        if dw_start_dt <= now_local:
            dw_start_dt += timedelta(days=1)

        # Find the first slot at or after the DW start time
        # This handles non-aligned forecast slots (e.g., 15:10 instead of 15:00)
        for entry in data.daily_forecast:
            ts = entry.get("timestamp", "")
            if not ts:
                continue
            try:
                slot_dt = datetime.fromisoformat(ts)
            except ValueError:
                continue  # Malformed timestamp — skip
            # Normalise slot to tz-aware local time.
            if slot_dt.tzinfo is None:
                slot_local = dt_util.as_local(dt_util.as_utc(slot_dt))
            else:
                slot_local = dt_util.as_local(slot_dt)

            # Find first slot at or after DW start
            if slot_local >= dw_start_dt:
                return entry

        # No future DW slot found in the current forecast window
        return None

    def _compute_active_mode(self, data: CoordinatorData, now_dt: datetime) -> None:
        """Compute active battery mode."""
        automation_enabled = self._get_switch_state("automation_enabled")
        spike_discharge_enabled = self._get_switch_state("spike_discharge_enabled")

        # ========================================================================
        # AUTOMATION DISABLED (Highest Priority - Bypass all logic)
        # ========================================================================

        # When automation is disabled, set to self-consumption and bypass all state changes
        if not automation_enabled:
            data.active_mode = BatteryMode.SELF_CONSUMPTION
            return

        # Reset flags at the start of each computation
        data.proactive_export_active = False

        # Check if we're in valid discharge window (6am-midnight)
        current_hour = now_dt.hour
        in_discharge_window = current_hour >= DISCHARGE_EARLIEST_HOUR

        # ========================================================================
        # FORECAST-DRIVED CONTROL (Single Source of Truth)
        # ========================================================================

        # Get forecast entry for current time
        forecast_entry = self._get_forecast_entry_for_now(data, now_dt)

        # Fallback: Forecast unavailable - default to self-consumption
        # If we don't have forecast data, we can't make intelligent decisions,
        # so stay in safe mode (self-consumption)
        if not forecast_entry:
            data.debug_mode_source = "no_forecast"
            _LOGGER.warning(
                "Forecast unavailable, defaulting to self-consumption (no fallback logic)"
            )
            data.active_mode = BatteryMode.SELF_CONSUMPTION
            return

        # Forecast-driven path
        data.debug_mode_source = "forecast"

        # Log forecast entry for debugging
        _LOGGER.info(
            "Mode decision at %s: slot_time=%s, grid_charge=%s, grid_charge_boost=%s, grid_import_kwh=%.3f, proactive_export=%s, soc=%.1f%%",
            now_dt.strftime("%H:%M"),
            forecast_entry.get("timestamp", "unknown")[
                -14:-9
            ],  # Extract HH:MM from ISO
            forecast_entry.get("grid_charge", False),
            forecast_entry.get("grid_charge_boost", False),
            forecast_entry.get("grid_import_kwh", 0),
            forecast_entry.get("proactive_export", False),
            data.soc,
        )

        # FORECAST-DRIVED: Grid charging (follow forecast plan)
        # Safety: Only activate if there's actual grid import planned
        # Use small threshold (0.01 kWh) to handle floating point edge cases
        grid_import_kwh = forecast_entry.get("grid_import_kwh", 0)
        GRID_IMPORT_THRESHOLD = 0.01  # Minimum kWh to consider grid charging active

        if forecast_entry.get("grid_charge_boost"):
            if grid_import_kwh > GRID_IMPORT_THRESHOLD:
                data.active_mode = BatteryMode.BOOST_CHARGING
                _LOGGER.info(
                    "Forecast-driven: BOOST_CHARGING at %s, import=%.3f kWh",
                    now_dt.strftime("%H:%M"),
                    grid_import_kwh,
                )
                return
            else:
                # Boost flag set but no import - fall through to check grid_charge
                _LOGGER.debug(
                    "grid_charge_boost=True but grid_import_kwh=0, checking grid_charge"
                )

        if forecast_entry.get("grid_charge"):
            if grid_import_kwh > GRID_IMPORT_THRESHOLD:
                data.active_mode = BatteryMode.GRID_CHARGING
                _LOGGER.info(
                    "Forecast-driven: GRID_CHARGING at %s, import=%.3f kWh",
                    now_dt.strftime("%H:%M"),
                    grid_import_kwh,
                )
                return
            else:
                _LOGGER.debug(
                    "grid_charge=True but grid_import_kwh=%.3f, staying in self-consumption",
                    grid_import_kwh,
                )

        # FORECAST-DRIVED: Proactive export (before negative feed-in prices)
        # No discharge-window guard here — unlike SPIKE_DISCHARGE (which is reactive),
        # proactive export is forecast-driven and the forecast computer already tracks
        # predicted SOC across all slots.  It will only mark a slot for export if the
        # battery SOC remaining after export is sufficient to cover overnight load until
        # solar returns the next morning.  A time-of-day guard would incorrectly block
        # legitimate overnight export at high feed-in prices.
        if forecast_entry.get("proactive_export"):
            export_amount = forecast_entry.get("export_amount_kwh", 0.0)
            EXPORT_THRESHOLD = 0.01  # Minimum kWh to consider export active

            if export_amount > EXPORT_THRESHOLD:
                data.active_mode = BatteryMode.PROACTIVE_EXPORT
                data.proactive_export_active = True
                _LOGGER.info(
                    "Forecast-driven: PROACTIVE_EXPORT at %s, amount=%.2f kWh",
                    now_dt.strftime("%H:%M"),
                    export_amount,
                )
                return
            else:
                _LOGGER.debug(
                    "proactive_export=True but export_amount_kwh=%.3f, staying in self-consumption",
                    export_amount,
                )

        # ========================================================================
        # OTHER MODES (Non-Charging)
        # ========================================================================

        if data.price_spike and spike_discharge_enabled and in_discharge_window:
            data.active_mode = BatteryMode.SPIKE_DISCHARGE
        elif data.demand_window_active:
            # Once in demand window, STAY in DEMAND_BLOCK regardless of SOC.
            # The solar_can_reach_target_in_dw check is only for ENTRY decision (before DW starts).
            # This prevents premature exit from demand block when SOC drops below target during DW.
            data.active_mode = BatteryMode.DEMAND_BLOCK
        elif data.manual_override:
            data.active_mode = BatteryMode.MANUAL
        else:
            _LOGGER.debug(
                "Mode fallthrough to SELF_CONSUMPTION at %s: "
                "grid_charge=%s grid_boost=%s proactive=%s "
                "spike=%s dw=%s manual=%s",
                now_dt.strftime("%H:%M"),
                forecast_entry.get("grid_charge"),
                forecast_entry.get("grid_charge_boost"),
                forecast_entry.get("proactive_export"),
                data.price_spike,
                data.demand_window_active,
                data.manual_override,
            )
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

    # ========================================================================
    # SPIKE ANALYSIS (Conservative Spike Discharge)
    # ========================================================================

    def _analyze_spike(
        self,
        data: CoordinatorData,
        now_dt: datetime,
    ) -> None:
        """Analyze feed-in forecast for spike window details (conservative mode).

        Called during compute_derived_values to populate spike analysis fields.
        These fields are used by _compute_active_mode for conservative decisions.
        """
        # Get configuration
        conservative_enabled = self._get_switch_state(
            SWITCH_SPIKE_DISCHARGE_CONSERVATIVE
        )
        spike_percentile = float(
            self.entry.options.get(
                CONF_SPIKE_PRICE_PERCENTILE, DEFAULT_SPIKE_PRICE_PERCENTILE
            )
        )

        # Default values
        data.spike_end_time = None
        data.spike_max_price = 0.0
        data.spike_price_threshold = 0.0
        data.spike_reserve_soc = 0.0
        data.spike_hours_remaining = 0.0
        data.spike_in_conservative_mode = False

        # Skip analysis if conservative mode not enabled
        if not conservative_enabled:
            return

        # Analyze spike window
        lookahead = float(
            self.entry.options.get(
                CONF_FORECAST_LOOKAHEAD_HOURS, DEFAULT_FORECAST_LOOKAHEAD_HOURS
            )
        )

        spike_end, max_price, spike_prices = analyze_spike_window(
            data.feed_in_forecast, now_dt, lookahead
        )

        if spike_end is None or not spike_prices:
            # No spike detected
            return

        # Populate spike analysis fields
        data.spike_end_time = spike_end
        data.spike_max_price = max_price
        data.spike_hours_remaining = (spike_end - now_dt).total_seconds() / 3600

        # Calculate price threshold for top X% of spike prices
        data.spike_price_threshold = calculate_spike_price_threshold(
            spike_prices, spike_percentile
        )

        # Calculate reserve SOC needed to survive spike + demand window if overlapping
        data.spike_reserve_soc = self._calculate_spike_reserve_soc(
            data, now_dt, spike_end, spike_percentile
        )

        data.spike_in_conservative_mode = True

        _LOGGER.info(
            "Spike analysis: max_price=%.2f, threshold=%.2f, reserve=%.1f%%, hours_remaining=%.1f",
            data.spike_max_price,
            data.spike_price_threshold,
            data.spike_reserve_soc,
            data.spike_hours_remaining,
        )

    def _calculate_spike_reserve_soc(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        spike_end: datetime,
        spike_percentile: float,
    ) -> float:
        """Calculate reserve SOC needed to survive spike period.

        Reserve = max(spike_duration_hours, demand_window_hours) * avg_load_kWh
        divided by battery_capacity_kWh * 100%

        If demand window overlaps with or starts during spike, include full DW duration.
        """
        # Get demand window times
        dw_start_time = self._parse_time_option(
            CONF_DEMAND_WINDOW_START, DEFAULT_DEMAND_WINDOW_START
        )
        dw_end_time = self._parse_time_option(
            CONF_DEMAND_WINDOW_END, DEFAULT_DEMAND_WINDOW_END
        )

        # Calculate spike duration
        spike_duration_hours = max((spike_end - now_dt).total_seconds() / 3600, 0)

        # Calculate demand window duration
        dw_duration_hours = (
            datetime.combine(now_dt.date(), dw_end_time)
            - datetime.combine(now_dt.date(), dw_start_time)
        ).total_seconds() / 3600
        if dw_duration_hours < 0:
            dw_duration_hours += 24  # Handle overnight DW

        # Determine if DW overlaps with or starts during spike
        dw_start_dt = now_dt.replace(
            hour=dw_start_time.hour,
            minute=dw_start_time.minute,
            second=0,
            microsecond=0,
        )
        # If DW starts after now but before spike ends, include it
        dw_overlaps = dw_start_dt > now_dt and dw_start_dt < spike_end

        # Use the longer of spike duration or DW duration (if overlapping)
        if dw_overlaps:
            # Include full demand window - battery must survive spike + DW
            required_hours = max(spike_duration_hours, dw_duration_hours)
            _LOGGER.debug(
                "DW overlaps spike: spike=%.1fh, dw=%.1fh, using=%.1fh",
                spike_duration_hours,
                dw_duration_hours,
                required_hours,
            )
        else:
            required_hours = spike_duration_hours

        # Get expected load (use current load as estimate)
        expected_load_kw = data.load_power_kw if data.load_power_kw > 0 else 0.5

        # Calculate reserve kWh needed
        reserve_kwh = expected_load_kw * required_hours

        # Convert to SOC percentage
        reserve_soc = (reserve_kwh / BATTERY_CAPACITY_KWH) * 100

        # Cap at reasonable maximum (can't reserve more than 100%)
        return min(reserve_soc, 100.0)

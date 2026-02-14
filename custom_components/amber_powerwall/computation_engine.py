"""Computation engine for derived values and forecasts."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    BATTERY_CAPACITY_KWH,
    CHARGE_RATE_BACKUP_KW,
    CHARGE_RATE_BOOST_KW,
    CONF_BATTERY_TARGET,
    CONF_CHEAP_PRICE_DEADBAND,
    CONF_CHEAP_PRICE_PERCENTILE,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    CONF_FORECAST_LOOKAHEAD_HOURS,
    CONF_HOLD_ABSOLUTE_CHEAP_THRESHOLD,
    CONF_HOLD_MIN_SAVINGS_PERCENT,
    CONF_LOAD_WEIGHT_RECENT,
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
    DEFAULT_LOAD_WEIGHT_RECENT,
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
        self._historical_load_sample_counts: dict[int, int] = {}
        self._historical_load_source: str = "unknown"
        self._historical_load_cache_date: str = ""
        self._recent_load_1hr_kw: float = 0.0
        self._recent_load_cache_time: datetime | None = None
        self._recent_load_1hr_statistic_id: str = ""
        self._recent_load_1hr_samples: int = 0
        self._recent_load_1hr_last_error: str = ""
        self._last_weighting: float = DEFAULT_LOAD_WEIGHT_RECENT
        self._previous_active_mode = None
        self._last_forecast_hour: int | None = None
        self._last_decision_log_time: datetime | None = None

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
        """Compute full 24-hour forecast with 15-minute breakdown.

        Provides 4x granularity over hourly forecast, capturing meaningful
        price variations from Amber's 5-minute pricing data.
        """
        data.daily_forecast = []
        data.daily_forecast_soc_15min = []
        data.forecast_consumption_source_counts = {}

        # Get historical hourly averages
        load_entity_id = self._get_entity_id("teslemetry_load_power")
        hourly_avg_kw = self._get_historical_hourly_averages(load_entity_id)
        all_solcast = [*data.solcast_today, *data.solcast_tomorrow]

        # Publish consumption profile diagnostics for transparency
        data.consumption_source = (
            self._historical_load_source if hourly_avg_kw else "live_load_fallback"
        )
        data.consumption_statistic_id = load_entity_id
        data.consumption_profile_hours = len(hourly_avg_kw)
        data.consumption_fallback_hours = 0
        data.consumption_hourly_sample_counts = dict(
            self._historical_load_sample_counts
        )
        data.consumption_hourly_profile_kw = {
            hour: round(val, 3) for hour, val in sorted(hourly_avg_kw.items())
        }

        # Get recent 1-hour load for weighted forecasting
        # Use cached value if available, otherwise will use 0 (falls back to historical)
        recent_load_kw = self._recent_load_1hr_kw
        data.recent_load_1hr_kw = recent_load_kw
        data.recent_load_1hr_statistic_id = self._recent_load_1hr_statistic_id
        data.recent_load_1hr_samples = self._recent_load_1hr_samples
        data.recent_load_1hr_last_error = self._recent_load_1hr_last_error
        data.consumption_weighting = self._last_weighting

        if not all_solcast:
            _LOGGER.debug("15-min forecast: no Solcast entries available")
        if not hourly_avg_kw:
            _LOGGER.debug(
                "15-min forecast: no historical hourly load profile available; using live load fallback"
            )

        current_soc = data.soc
        predicted_soc = current_soc
        base_slot = now_dt.replace(minute=0, second=0, microsecond=0)

        # Get target SOC for grid charging decisions
        target_pct = float(
            self.entry.options.get(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET)
        )
        target_kwh = target_pct / 100 * BATTERY_CAPACITY_KWH

        # Get demand window times for zero-grid-import constraint
        dw_start_time = self._parse_time_option(
            CONF_DEMAND_WINDOW_START, DEFAULT_DEMAND_WINDOW_START
        )
        dw_end_time = self._parse_time_option(
            CONF_DEMAND_WINDOW_END, DEFAULT_DEMAND_WINDOW_END
        )
        target_hour = dw_start_time.hour

        # Build rolling 24-hour forecast with 15-minute slots (96 total)
        for offset in range(96):
            slot_start = base_slot + timedelta(minutes=15 * offset)
            slot_hour = slot_start.hour
            slot_minute = slot_start.minute
            slot_time = slot_start.time()

            # Check if we're in demand window (zero grid import constraint)
            in_demand_window = dw_start_time <= slot_time < dw_end_time

            # Get solar forecast for this 15-minute slot
            solar_kwh = self._get_solar_for_15min_slot(all_solcast, slot_start)

            # Get expected consumption (hourly_avg / 4 for 15-min)
            # Pass recent load for weighted forecasting
            load_kw, load_source = self._estimate_hourly_consumption_kw(
                hourly_avg_kw,
                slot_hour,
                data.load_power_kw,
                recent_load_kw,
            )
            if load_source != "profile_hour":
                data.consumption_fallback_hours += 1
            data.forecast_consumption_source_counts[load_source] = (
                data.forecast_consumption_source_counts.get(load_source, 0) + 1
            )
            consumption_kwh = load_kw / 4  # 15-min is 1/4 of hour

            # Calculate raw net energy for 15 minutes
            net_kwh = solar_kwh - consumption_kwh

            # Get price for this slot (for price-aware grid charging)
            slot_price = self._get_price_for_slot(data.general_forecast, slot_start)

            # Calculate effective cheap price for this slot (with urgency)
            # Only before demand window and if we haven't reached target
            slot_effective_cheap = data.effective_cheap_price
            if (
                slot_start
                < now_dt.replace(hour=target_hour, minute=0, second=0, microsecond=0)
                and predicted_soc < target_pct
            ):
                # Apply urgency: as we get closer to DW, willing to pay more
                hours_to_target = max(
                    (
                        slot_start.replace(hour=target_hour, minute=0) - slot_start
                    ).total_seconds()
                    / 3600,
                    0,
                )
                if hours_to_target > 0:
                    urgency = max(min(1 - (hours_to_target / 8.0), 1.0), 0.0)
                    max_price = float(
                        self.entry.options.get(
                            CONF_MAX_PRECHARGE_PRICE, DEFAULT_MAX_PRECHARGE_PRICE
                        )
                    )
                    base_cheap = (
                        float(
                            self.entry.options.get(
                                CONF_CHEAP_PRICE_PERCENTILE,
                                DEFAULT_CHEAP_PRICE_PERCENTILE,
                            )
                        )
                        / 100
                        * max_price
                    )  # Rough estimate
                    slot_effective_cheap = (
                        base_cheap + (max_price - base_cheap) * urgency
                    )

            # Determine if we should grid charge
            # Grid charge if:
            # 1. Not in demand window (zero import constraint)
            # 2. Before demand window
            # 3. SOC is below target
            # 4. Price is at or below effective cheap price
            # 5. Either: there's a deficit OR we need to charge to reach target
            has_deficit = net_kwh < 0
            needs_charge = predicted_soc < target_pct

            # Only grid charge when price is cheap AND we need to charge
            should_grid_charge = (
                not in_demand_window
                and slot_hour < target_hour
                and predicted_soc < target_pct
                and slot_price <= slot_effective_cheap
                and (
                    has_deficit or needs_charge
                )  # Charge if deficit OR need to reach target
            )

            # Apply realistic battery transfer limits and efficiency
            # Max transfer: 3.3 kW = 0.825 kWh per 15 minutes (backup mode)
            max_slot_transfer_kwh = CHARGE_RATE_BACKUP_KW / 4

            # Determine if we should use boost charging (5kW) based on urgency
            # If very close to DW and far from target, use boost
            hours_to_dw = max(
                (
                    slot_start.replace(hour=target_hour, minute=0) - slot_start
                ).total_seconds()
                / 3600,
                0,
            )
            if should_grid_charge and hours_to_dw < 2:
                max_slot_transfer_kwh = CHARGE_RATE_BOOST_KW / 4  # 5kW boost

            if net_kwh >= 0:
                # Excess solar: first charge battery, then export excess
                battery_delta_kwh = min(net_kwh, max_slot_transfer_kwh) * 0.92

                # If we need grid charging and there's room in battery
                if should_grid_charge:
                    # Calculate how much we can still add to reach target
                    current_battery_kwh = predicted_soc / 100 * BATTERY_CAPACITY_KWH
                    space_remaining_kwh = max(target_kwh - current_battery_kwh, 0)
                    grid_charge_amount = min(
                        max_slot_transfer_kwh * 0.92, space_remaining_kwh
                    )
                    battery_delta_kwh += grid_charge_amount
                    grid_import_kwh = (
                        grid_charge_amount / 0.92
                    )  # Account for efficiency
                else:
                    grid_import_kwh = 0.0

                excess_after_battery = net_kwh - battery_delta_kwh
                grid_export_kwh = max(excess_after_battery, 0)
            else:
                # Deficit: battery discharges to cover what it can, then import rest from grid
                battery_kwh = predicted_soc / 100 * BATTERY_CAPACITY_KWH
                battery_is_empty = (
                    battery_kwh <= 0.5
                )  # Consider empty if < 0.5 kWh (~2% SOC)

                _LOGGER.debug(
                    "DEFICIT slot %02d:%02d: net=%.3f, soc=%.1f, battery_kwh=%.3f, empty=%s, in_dw=%s",
                    slot_hour,
                    slot_minute,
                    net_kwh,
                    predicted_soc,
                    battery_kwh,
                    battery_is_empty,
                    in_demand_window,
                )

                if battery_is_empty:
                    # Battery is empty - but only import if NOT in demand window
                    # During demand window, we don't import even if empty (for demand charge savings)
                    battery_delta_kwh = 0.0  # Battery can't discharge
                    if in_demand_window:
                        grid_import_kwh = 0.0  # Block imports during DW even if empty
                        _LOGGER.debug("  -> BATTERY EMPTY+IN_DW: no import (DW blocks)")
                    else:
                        grid_import_kwh = -net_kwh  # Import the full deficit
                        _LOGGER.debug(
                            "  -> BATTERY EMPTY: grid_import=%.3f (full deficit)",
                            grid_import_kwh,
                        )
                else:
                    # Battery has charge - can discharge up to max rate
                    battery_delta_kwh = max(net_kwh, -max_slot_transfer_kwh) / 0.95

                    # What's left after battery discharge
                    deficit_after_battery = net_kwh - battery_delta_kwh

                    # Only import if there's still a deficit after battery
                    if deficit_after_battery < 0 and not in_demand_window:
                        # Outside demand window - import the remaining deficit
                        grid_import_kwh = -deficit_after_battery
                        _LOGGER.debug(
                            "  -> HAS_CHARGE+OUTSIDE_DW: deficit_after=%.3f, grid_import=%.3f",
                            deficit_after_battery,
                            grid_import_kwh,
                        )
                    else:
                        grid_import_kwh = 0.0
                        _LOGGER.debug(
                            "  -> HAS_CHARGE+IN_DW: no import allowed (deficit_after=%.3f)",
                            deficit_after_battery,
                        )

                grid_export_kwh = 0.0

            # Iterative SOC simulation with clamp each 15 minutes
            predicted_soc = predicted_soc + (
                battery_delta_kwh / BATTERY_CAPACITY_KWH * 100
            )
            predicted_soc = max(0.0, min(100.0, predicted_soc))

            # Store a light-weight SOC timeseries to avoid huge attributes
            # Used by dashboard chart (timestamp, soc)
            data.daily_forecast_soc_15min.append(
                [slot_start.isoformat(), round(predicted_soc, 1)]
            )

            data.daily_forecast.append(
                {
                    "hour": slot_hour,
                    "minute": slot_minute,
                    "timestamp": slot_start.isoformat(),
                    "predicted_soc": round(predicted_soc, 1),
                    "solar_kwh": round(solar_kwh, 3),  # More precision for 15-min
                    "consumption_kwh": round(consumption_kwh, 3),
                    "consumption_source": load_source,
                    "net_kwh": round(net_kwh, 3),
                    "grid_import_kwh": round(grid_import_kwh, 3),
                    "grid_export_kwh": round(grid_export_kwh, 3),
                }
            )

        # Also keep a compact 24-entry hourly view for the markdown table.
        data.daily_forecast_hourly = self._build_hourly_forecast_summary(
            data.daily_forecast
        )

    @staticmethod
    def _build_hourly_forecast_summary(
        forecast_15min: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Summarise 96x 15-min slots into 24 hourly records.

        Keeps attributes smaller while still providing an hour-by-hour view.
        """
        hourly: dict[int, dict[str, Any]] = {}

        for row in forecast_15min:
            if not isinstance(row, dict):
                continue

            hour_raw = row.get("hour")
            if hour_raw is None:
                continue
            try:
                hour = int(hour_raw)
            except (TypeError, ValueError):
                continue

            if hour < 0 or hour > 23:
                continue

            bucket = hourly.get(hour)
            if bucket is None:
                predicted_soc_raw = row.get("predicted_soc")
                predicted_soc = (
                    float(predicted_soc_raw)
                    if isinstance(predicted_soc_raw, int | float)
                    else 0.0
                )
                bucket = {
                    "hour": hour,
                    "predicted_soc": predicted_soc,
                    "solar_kwh": 0.0,
                    "consumption_kwh": 0.0,
                    "net_kwh": 0.0,
                    "grid_import_kwh": 0.0,
                    "grid_export_kwh": 0.0,
                }
                hourly[hour] = bucket

            predicted_soc_raw = row.get("predicted_soc")
            if isinstance(predicted_soc_raw, int | float):
                bucket["predicted_soc"] = float(predicted_soc_raw)

            for key in (
                "solar_kwh",
                "consumption_kwh",
                "net_kwh",
                "grid_import_kwh",
                "grid_export_kwh",
            ):
                try:
                    bucket[key] += float(row.get(key) or 0.0)
                except (TypeError, ValueError):
                    continue

        # Return in hour order
        result: list[dict[str, Any]] = []
        for hour in sorted(hourly.keys()):
            bucket = hourly[hour]
            result.append(
                {
                    "hour": hour,
                    "predicted_soc": round(float(bucket["predicted_soc"]), 1),
                    "solar_kwh": round(float(bucket["solar_kwh"]), 3),
                    "consumption_kwh": round(float(bucket["consumption_kwh"]), 3),
                    "net_kwh": round(float(bucket["net_kwh"]), 3),
                    "grid_import_kwh": round(
                        float(bucket.get("grid_import_kwh", 0)), 3
                    ),
                    "grid_export_kwh": round(
                        float(bucket.get("grid_export_kwh", 0)), 3
                    ),
                }
            )
        return result

    def _get_price_for_slot(
        self,
        price_forecasts: list[dict[str, Any]],
        slot_start: datetime,
    ) -> float:
        """Get price for a 15-minute slot from Amber forecast.

        Returns the average price for the slot from 5-minute forecast data.
        """
        if not price_forecasts:
            return 0.0

        # Ensure slot boundaries are timezone-aware local datetimes
        if slot_start.tzinfo is None:
            slot_start = dt_util.as_local(dt_util.as_utc(slot_start))
        else:
            slot_start = dt_util.as_local(slot_start)

        slot_end = slot_start + timedelta(minutes=15)

        prices_in_slot = []
        for entry in price_forecasts:
            if not isinstance(entry, dict):
                continue

            start_raw = entry.get("start_time")
            if start_raw is None:
                continue

            start_dt = self._parse_forecast_dt(start_raw)
            if start_dt is None:
                continue

            start_local = dt_util.as_local(start_dt)
            end_local = start_local + timedelta(minutes=5)  # Amber prices are 5-min

            # Check if this price period overlaps with our slot
            if start_local < slot_end and end_local > slot_start:
                price = float(entry.get("per_kwh", 0.0))
                prices_in_slot.append(price)

        if prices_in_slot:
            return sum(prices_in_slot) / len(prices_in_slot)
        return 0.0

    def _get_solar_for_15min_slot(
        self,
        solcast_forecasts: list[dict[str, Any]],
        slot_start: datetime,
    ) -> float:
        """Get solar forecast (kWh) for 15-minute slot from Solcast 30-min periods.

        Splits 30-minute Solcast periods into two 15-minute halves.
        """
        if not solcast_forecasts:
            return 0.0

        # Ensure slot boundaries are timezone-aware local datetimes
        if slot_start.tzinfo is None:
            slot_start = dt_util.as_local(dt_util.as_utc(slot_start))
        else:
            slot_start = dt_util.as_local(slot_start)

        slot_end = slot_start + timedelta(minutes=15)
        period_duration = timedelta(minutes=30)

        for entry in solcast_forecasts:
            if not isinstance(entry, dict):
                continue

            period_start_raw = entry.get("period_start") or entry.get("start")
            if period_start_raw is None:
                continue

            start_dt = dt_util.parse_datetime(str(period_start_raw))
            if not start_dt:
                continue

            start_local = dt_util.as_local(start_dt)
            end_local = start_local + period_duration
            period_kwh = float(
                entry.get("pv_estimate10")
                or entry.get("estimate10")
                or entry.get("pv_estimate")
                or entry.get("estimate")
                or 0.0
            )

            # Check which 15-minute half of 30-min period we're in
            period_midpoint = start_local + timedelta(minutes=15)

            if slot_start >= start_local and slot_end <= period_midpoint:
                # First half of period (0-15 min)
                # Simple approach: split evenly (50% each half)
                return period_kwh * 0.5
            elif slot_start >= period_midpoint and slot_end <= end_local:
                # Second half of period (15-30 min)
                return period_kwh * 0.5

        return 0.0

    def _compute_daily_hourly_forecast(
        self,
        data: CoordinatorData,
        now_dt: datetime,
    ) -> None:
        """Compute full 24-hour forecast with hourly breakdown."""
        data.daily_forecast = []

        # Get historical hourly averages
        load_entity_id = self._get_entity_id("teslemetry_load_power")
        hourly_avg_kw = self._get_historical_hourly_averages(load_entity_id)
        all_solcast = [*data.solcast_today, *data.solcast_tomorrow]

        # Publish consumption profile diagnostics for transparency
        data.consumption_source = (
            self._historical_load_source if hourly_avg_kw else "live_load_fallback"
        )
        data.consumption_statistic_id = load_entity_id
        data.consumption_profile_hours = len(hourly_avg_kw)
        data.consumption_fallback_hours = 0
        data.consumption_hourly_sample_counts = dict(
            self._historical_load_sample_counts
        )
        data.consumption_hourly_profile_kw = {
            hour: round(val, 3) for hour, val in sorted(hourly_avg_kw.items())
        }

        if not all_solcast:
            _LOGGER.debug("Daily forecast: no Solcast entries available")
        if not hourly_avg_kw:
            _LOGGER.debug(
                "Daily forecast: no historical hourly load profile available; using live load fallback"
            )

        current_soc = data.soc
        predicted_soc = current_soc
        base_slot = now_dt.replace(minute=0, second=0, microsecond=0)

        # Build rolling 24-hour forecast from the current hour
        for offset in range(24):
            slot_start = base_slot + timedelta(hours=offset)
            slot_hour = slot_start.hour

            # Get solar forecast for this slot (aggregated from Solcast half-hour entries)
            solar_kwh = self._get_solar_for_slot(all_solcast, slot_start)

            # Get expected consumption for this slot's hour
            load_kw, load_source = self._estimate_hourly_consumption_kw(
                hourly_avg_kw,
                slot_hour,
                data.load_power_kw,
            )
            if load_source != "profile_hour":
                data.consumption_fallback_hours += 1
            consumption_kwh = load_kw

            # Calculate raw net energy for the hour
            net_kwh = solar_kwh - consumption_kwh

            # Apply realistic battery transfer limits and efficiency
            max_hourly_transfer_kwh = CHARGE_RATE_BACKUP_KW  # 1-hour slot
            if net_kwh >= 0:
                battery_delta_kwh = min(net_kwh, max_hourly_transfer_kwh) * 0.92
            else:
                battery_delta_kwh = max(net_kwh, -max_hourly_transfer_kwh) / 0.95

            # Iterative SOC simulation with clamp each hour
            predicted_soc = predicted_soc + (
                battery_delta_kwh / BATTERY_CAPACITY_KWH * 100
            )
            predicted_soc = max(0.0, min(100.0, predicted_soc))

            data.daily_forecast.append(
                {
                    "hour": slot_hour,
                    "timestamp": slot_start.isoformat(),
                    "predicted_soc": round(predicted_soc, 1),
                    "solar_kwh": round(solar_kwh, 2),
                    "consumption_kwh": round(consumption_kwh, 2),
                    "consumption_source": load_source,
                    "net_kwh": round(net_kwh, 2),
                }
            )

    def _estimate_hourly_consumption_kw(
        self,
        hourly_avg_kw: dict[int, float],
        slot_hour: int,
        current_load_kw: float,
        recent_load_kw: float = 0.0,
    ) -> tuple[float, str]:
        """Estimate hourly household consumption with weighted blend.

        Blends recent 1-hour average with historical hourly average for
        more responsive forecasting.

        Returns tuple of (kW, source_tag).
        """
        # Get the weighting configuration
        recent_weight = float(
            self.entry.options.get(CONF_LOAD_WEIGHT_RECENT, DEFAULT_LOAD_WEIGHT_RECENT)
        )
        historical_weight = 1.0 - recent_weight

        # Store the weighting for diagnostics
        self._last_weighting = recent_weight

        historical_raw = hourly_avg_kw.get(slot_hour) if hourly_avg_kw else None
        historical_kw = (
            float(historical_raw) if isinstance(historical_raw, int | float) else 0.0
        )
        sample_count = self._historical_load_sample_counts.get(slot_hour, 0)

        # Check if we have valid historical data
        has_historical = historical_kw > 0 and sample_count >= 1

        # If we have recent load data and weighting > 0, apply weighted blend
        if recent_load_kw > 0 and recent_weight > 0 and has_historical:
            weighted = (recent_weight * recent_load_kw) + (
                historical_weight * historical_kw
            )
            return round(weighted, 3), "weighted_load"

        # Fallback to historical if available
        if has_historical:
            return round(historical_kw, 3), "profile_hour"

        # Fallback to current load
        base_kw = current_load_kw if current_load_kw > 0 else 0.6
        return round(base_kw, 3), "live_load_fallback"

    def _get_solar_for_slot(
        self,
        solcast_forecasts: list[dict[str, Any]],
        slot_start: datetime,
    ) -> float:
        """Get solar forecast (kWh) for one hourly slot from Solcast half-hour periods."""
        if not solcast_forecasts:
            return 0.0

        # Ensure slot boundaries are timezone-aware local datetimes
        if slot_start.tzinfo is None:
            slot_start = dt_util.as_local(dt_util.as_utc(slot_start))
        else:
            slot_start = dt_util.as_local(slot_start)

        slot_end = slot_start + timedelta(hours=1)
        period_duration = timedelta(minutes=30)

        total_solar = 0.0
        parsed_periods = 0
        overlap_hits = 0

        for entry in solcast_forecasts:
            try:
                if not isinstance(entry, dict):
                    continue

                period_start_raw = entry.get("period_start") or entry.get("start")
                if period_start_raw is None:
                    continue

                start_dt = dt_util.parse_datetime(str(period_start_raw))
                if not start_dt:
                    continue

                start_local = dt_util.as_local(start_dt)
                parsed_periods += 1
                end_local = start_local + period_duration

                # overlap between [start_local, end_local) and [slot_start, slot_end)
                overlap_start = max(start_local, slot_start)
                overlap_end = min(end_local, slot_end)
                overlap_seconds = (overlap_end - overlap_start).total_seconds()

                if overlap_seconds > 0:
                    # Support common Solcast key variants
                    period_kwh = float(
                        entry.get("pv_estimate10")
                        or entry.get("estimate10")
                        or entry.get("pv_estimate")
                        or entry.get("estimate")
                        or 0.0
                    )
                    overlap_fraction = overlap_seconds / period_duration.total_seconds()
                    total_solar += period_kwh * overlap_fraction
                    overlap_hits += 1
            except (ValueError, TypeError):
                continue

        if (
            total_solar == 0.0
            and solcast_forecasts
            and slot_start.hour in (8, 9, 10, 11, 12, 13, 14, 15, 16)
        ):
            sample = (
                solcast_forecasts[0] if isinstance(solcast_forecasts[0], dict) else {}
            )
            _LOGGER.debug(
                "Solar slot resolved to 0. slot=%s parsed_periods=%s overlap_hits=%s sample_keys=%s sample_period_start=%s",
                slot_start.isoformat(),
                parsed_periods,
                overlap_hits,
                sorted(sample.keys()) if isinstance(sample, dict) else [],
                sample.get("period_start") if isinstance(sample, dict) else None,
            )

        return total_solar

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

    def _compute_active_mode(self, data: CoordinatorData, now_dt: datetime) -> None:
        """Compute active battery mode."""
        automation_enabled = self._get_switch_state("automation_enabled")
        spike_discharge_enabled = self._get_switch_state("spike_discharge_enabled")

        # Check if we're in valid discharge window (6am-midnight)
        current_hour = now_dt.hour
        in_discharge_window = current_hour >= DISCHARGE_EARLIEST_HOUR

        # Check sun status
        sun_entity_id = self._get_entity_id(CONF_SUN_ENTITY)
        sun_state = self.hass.states.get(sun_entity_id)
        sun_up = sun_state is not None and sun_state.state == "above_horizon"

        # Debug: Log state when considering HOLD mode
        if data.hold_justified or data.forecast_spike_within_window:
            _LOGGER.info(
                "Hold mode consideration at %s: hold_justified=%s, "
                "hold_mode=%s, solar_export_hold=%s, "
                "forecast_spike=%s, solar_can_reach=%s, sun_up=%s, "
                "soc=%.1f%%, backup_reserve=%.1f%%, "
                "price=%.2f, stop_price=%.2f",
                now_dt.strftime("%H:%M"),
                data.hold_justified,
                data.hold_mode,
                data.solar_export_hold,
                data.forecast_spike_within_window,
                data.solar_can_reach_target,
                sun_up,
                data.soc,
                data.backup_reserve,
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
        elif data.general_price <= data.cheap_charge_stop_price:
            # Price in deadband or at stop price — maintain charge or hold
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
        now = dt_util.now()
        today_str = now.strftime("%Y-%m-%d")

        # Check if cache is valid for today
        if (
            self._historical_load_cache_date == today_str
            and self._historical_load_cache
        ):
            return (
                self._historical_load_cache,
                self._historical_load_sample_counts,
                self._historical_load_source,
            )

        # Run blocking history fetch in thread pool using recorder's executor
        # This is the proper way to access the database from a custom integration
        from homeassistant.components import recorder

        _LOGGER.info("Fetching historical load data for entity: %s", entity_id)

        recorder_instance = recorder.get_instance(self.hass)
        hourly_avg_kw, sample_counts = await recorder_instance.async_add_executor_job(
            self._fetch_historical_data_sync, entity_id, now
        )

        _LOGGER.info(
            "Historical data result: %s hours found",
            len(hourly_avg_kw) if hourly_avg_kw else 0,
        )

        if hourly_avg_kw and len(hourly_avg_kw) >= 6:
            self._historical_load_cache = hourly_avg_kw
            self._historical_load_sample_counts = sample_counts
            self._historical_load_source = "statistics"
            self._historical_load_cache_date = today_str
            _LOGGER.debug(
                "Historical load profile fetched: %s hours", len(hourly_avg_kw)
            )
        else:
            self._historical_load_source = "live_load_fallback"
            _LOGGER.debug(
                "Using live load fallback (insufficient history: %s hours)",
                len(hourly_avg_kw) if hourly_avg_kw else 0,
            )

        return (
            self._historical_load_cache,
            self._historical_load_sample_counts,
            self._historical_load_source,
        )

    def _fetch_historical_data_sync(
        self, entity_id: str, now: datetime
    ) -> tuple[dict[int, float], dict[int, int]]:
        """Fetch historical data using HA recorder/statistics (runs in thread pool).

        This runs in a thread pool so it won't block the HA event loop.
        """
        _LOGGER.info("DEBUG: Starting _fetch_historical_data_sync for %s", entity_id)

        start_time = now - timedelta(days=7)

        try:
            from homeassistant.components.recorder import (
                statistics as recorder_statistics,
            )

            _LOGGER.info("DEBUG: Imported recorder_statistics OK")
        except Exception as e:
            _LOGGER.info("DEBUG: Failed to import recorder statistics: %s", e)
            return {}, {}

        # Get statistics metadata to find the correct statistic_id
        stat_ids: list[dict[str, Any]] = []
        try:
            stat_meta_fn = getattr(recorder_statistics, "list_statistic_ids", None)
            _LOGGER.info("DEBUG: list_statistic_ids function: %s", stat_meta_fn)
            if callable(stat_meta_fn):
                stat_ids_raw = stat_meta_fn(self.hass, None) or []
                if isinstance(stat_ids_raw, list):
                    stat_ids = [
                        cast(dict[str, Any], s)
                        for s in stat_ids_raw
                        if isinstance(s, dict)
                    ]
                _LOGGER.info(
                    "DEBUG: Found %d statistic IDs for entity %s",
                    len(stat_ids),
                    entity_id,
                )
            else:
                _LOGGER.info("DEBUG: list_statistic_ids is not callable")
        except Exception as e:
            _LOGGER.info("DEBUG: Failed to list statistic ids: %s", e)
            pass

        # Find matching statistic_id
        resolved_entity_id = entity_id
        matched = False
        for sid in stat_ids:
            if not isinstance(sid, dict):
                continue
            stat_id = sid.get("statistic_id", "")
            if stat_id == entity_id or stat_id.replace(
                "sensor.", ""
            ) == entity_id.replace("sensor.", ""):
                resolved_entity_id = stat_id
                matched = True
                _LOGGER.debug(
                    "Matched statistic_id: %s for entity %s", stat_id, entity_id
                )
                break

        if not matched:
            _LOGGER.debug(
                "No matching statistic_id found for %s. Available: %s",
                entity_id,
                [
                    s.get("statistic_id") if isinstance(s, dict) else str(s)
                    for s in stat_ids[:10]
                ],
            )

        # Get statistics
        fn = getattr(recorder_statistics, "statistics_during_period", None)
        _LOGGER.info("DEBUG: statistics_during_period function: %s", fn)
        if not callable(fn):
            return {}, {}

        try:
            _LOGGER.info(
                "DEBUG: Calling statistics_during_period with entity=%s, start=%s, end=%s",
                resolved_entity_id,
                start_time,
                now,
            )

            statistics_data_raw = fn(
                self.hass,
                start_time,
                now,
                [resolved_entity_id],
                period="hour",
                types={"mean"},
                units=None,
            )
            _LOGGER.info(
                "DEBUG: statistics_during_period returned: %s", statistics_data_raw
            )
        except Exception as e:
            _LOGGER.info("DEBUG: statistics_during_period exception: %s", e)
            return {}, {}

        if not isinstance(statistics_data_raw, dict):
            return {}, {}

        statistics_data = cast(dict[str, Any], statistics_data_raw)

        if not statistics_data or resolved_entity_id not in statistics_data:
            return {}, {}

        rows_raw = statistics_data.get(resolved_entity_id)
        if not isinstance(rows_raw, list) or not rows_raw:
            return {}, {}

        rows: list[dict[str, Any]] = [
            cast(dict[str, Any], r) for r in rows_raw if isinstance(r, dict)
        ]
        if not rows:
            return {}, {}

        # Process statistics into hourly averages
        by_hour_values: dict[int, list[float]] = {h: [] for h in range(24)}
        for row in rows:
            if not isinstance(row, dict):
                continue

            start_val = row.get("start")
            row_dt = None

            # Handle different timestamp formats
            if isinstance(start_val, datetime):
                row_dt = start_val
            elif isinstance(start_val, int | float):
                # Unix timestamp (seconds since epoch)
                row_dt = dt_util.utc_from_timestamp(start_val)
            elif isinstance(start_val, str):
                # Try parsing as string
                row_dt = dt_util.parse_datetime(start_val)

            if row_dt is None:
                continue

            mean_val = row.get("mean")
            if mean_val in (None, "unknown", "unavailable"):
                continue

            try:
                mean_kw = float(mean_val)
            except (TypeError, ValueError):
                continue

            hour = dt_util.as_local(row_dt).hour
            by_hour_values[hour].append(mean_kw)

        hourly_avg_kw: dict[int, float] = {}
        sample_counts: dict[int, int] = {}
        for hour in range(24):
            samples = by_hour_values[hour]
            if not samples:
                continue
            sample_counts[hour] = len(samples)
            hourly_avg_kw[hour] = sum(samples) / len(samples)

        return hourly_avg_kw, sample_counts

    async def async_get_recent_load_1hr(self, entity_id: str) -> float:
        """Get average load over the last 1 hour from HA statistics.

        Returns: Average power in kW over last hour, or 0.0 if unavailable.
        """
        from homeassistant.components import recorder

        now = dt_util.now()

        # Check if cache is valid (within last 5 minutes)
        if (
            self._recent_load_cache_time is not None
            and (now - self._recent_load_cache_time).total_seconds() < 300
        ):
            return self._recent_load_1hr_kw

        # Run blocking history fetch in thread pool
        recorder_instance = recorder.get_instance(self.hass)
        try:
            result = await recorder_instance.async_add_executor_job(
                self._fetch_recent_load_sync, entity_id, now
            )
            self._recent_load_1hr_kw = float(result.get("recent_avg_kw", 0.0) or 0.0)
            self._recent_load_1hr_statistic_id = str(result.get("statistic_id", ""))
            self._recent_load_1hr_samples = int(result.get("samples", 0) or 0)
            self._recent_load_1hr_last_error = str(result.get("error", ""))
            self._recent_load_cache_time = now
            _LOGGER.debug(
                "Recent 1hr load: %.3f kW (statistic_id=%s samples=%s error=%s)",
                self._recent_load_1hr_kw,
                self._recent_load_1hr_statistic_id,
                self._recent_load_1hr_samples,
                self._recent_load_1hr_last_error,
            )
            return self._recent_load_1hr_kw
        except Exception as e:
            # Cache failures too, so we don't repeatedly hit the recorder DB.
            _LOGGER.warning("Failed to fetch recent load: %s", e)
            self._recent_load_1hr_kw = 0.0
            self._recent_load_1hr_statistic_id = ""
            self._recent_load_1hr_samples = 0
            self._recent_load_1hr_last_error = str(e)
            self._recent_load_cache_time = now
            return 0.0

    def _fetch_recent_load_sync(self, entity_id: str, now: datetime) -> dict[str, Any]:
        """Fetch recent 1-hour average (runs in thread pool).

        Returns a dict for diagnostics:
          - recent_avg_kw: float
          - samples: int
          - statistic_id: str
          - error: str
        """
        from homeassistant.components.recorder import statistics as recorder_statistics

        end_time = now
        start_time = now - timedelta(hours=1)

        # Find matching statistic_id (same logic as historical fetch)
        stat_ids: list[dict[str, Any]] = []
        try:
            stat_meta_fn = getattr(recorder_statistics, "list_statistic_ids", None)
            if callable(stat_meta_fn):
                stat_ids_raw = stat_meta_fn(self.hass, None) or []
                if isinstance(stat_ids_raw, list):
                    stat_ids = [
                        cast(dict[str, Any], s)
                        for s in stat_ids_raw
                        if isinstance(s, dict)
                    ]
        except Exception:
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": "",
                "error": "list_statistic_ids failed",
            }

        resolved_entity_id = entity_id
        for sid in stat_ids:
            if not isinstance(sid, dict):
                continue
            stat_id = sid.get("statistic_id", "")
            if stat_id == entity_id or stat_id.replace(
                "sensor.", ""
            ) == entity_id.replace("sensor.", ""):
                resolved_entity_id = stat_id
                break

        if not resolved_entity_id:
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": "",
                "error": "empty statistic_id",
            }

        # Get statistics for last hour
        fn = getattr(recorder_statistics, "statistics_during_period", None)
        if not callable(fn):
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": resolved_entity_id,
                "error": "statistics_during_period not callable",
            }

        try:
            statistics_data_raw = fn(
                self.hass,
                start_time,
                end_time,
                [resolved_entity_id],
                period="hour",
                types={"mean"},
                units=None,
            )
        except Exception:
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": resolved_entity_id,
                "error": "statistics_during_period exception",
            }

        if not isinstance(statistics_data_raw, dict):
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": resolved_entity_id,
                "error": "statistics_during_period returned non-dict",
            }

        statistics_data = cast(dict[str, Any], statistics_data_raw)

        if not statistics_data or resolved_entity_id not in statistics_data:
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": resolved_entity_id,
                "error": "no statistics data",
            }

        rows_raw = statistics_data.get(resolved_entity_id)
        if not isinstance(rows_raw, list) or not rows_raw:
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": resolved_entity_id,
                "error": "no rows",
            }

        rows: list[dict[str, Any]] = [
            cast(dict[str, Any], r) for r in rows_raw if isinstance(r, dict)
        ]
        if not rows:
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": resolved_entity_id,
                "error": "no dict rows",
            }

        # Calculate mean of available samples in the last hour
        values = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            mean_val = row.get("mean")
            if mean_val in (None, "unknown", "unavailable"):
                continue
            try:
                values.append(float(mean_val))
            except (TypeError, ValueError):
                continue

        if not values:
            return {
                "recent_avg_kw": 0.0,
                "samples": 0,
                "statistic_id": resolved_entity_id,
                "error": "no numeric mean values",
            }

        return {
            "recent_avg_kw": sum(values) / len(values),
            "samples": len(values),
            "statistic_id": resolved_entity_id,
            "error": "",
        }

    def _get_historical_hourly_averages(self, entity_id: str) -> dict[int, float]:
        """Get cached hourly averages (sync version for compute_derived_values).

        Returns cached data - actual fetching happens in async_get_historical_hourly_averages.
        """
        return self._historical_load_cache

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
                # Period starts at or after target — skip
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
        """Return maximum per_kwh price from forecasts within window."""
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
        """Calculate Nth percentile of a list of prices."""
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

    def clear_historical_cache(self) -> None:
        """Clear historical load cache to force refresh on next update."""
        self._historical_load_cache = {}
        self._historical_load_sample_counts = {}
        self._historical_load_source = "unknown"
        self._historical_load_cache_date = ""

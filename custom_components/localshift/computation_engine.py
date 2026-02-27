"""Computation engine for derived values and forecasts."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .computation_engine_lib import (
    ExcessSolarSignalsEngine,
    ForecastAccuracyEngine,
    ForecastChangeTracker,
    ForecastComputer,
    HistoryFetcher,
    ModeDecisionEngine,
    PriceCalculator,
    SpikeAnalyzer,
    WeatherDiagnosticsEngine,
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
    CHARGE_RATE_GRID_KW,
    CONF_BATTERY_TARGET,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    CONF_SUN_ENTITY,
    CONF_WEATHER_LEARNING_ENABLED,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_DEADBAND,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    DEFAULT_LOAD_WEIGHT_RECENT,
    DEFAULT_WEATHER_LEARNING_ENABLED,
    SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET,
)
from .const import (
    BatteryMode as _BatteryMode,
)
from .coordinator_data import CoordinatorData
from .weather_correlation import WeatherCorrelation

# Backward-compatible re-export for tests/importers that import BatteryMode
# from computation_engine.
BatteryMode = _BatteryMode

_LOGGER = logging.getLogger(__name__)


class ComputationEngine:
    """Computes all derived sensor values from raw state."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        get_entity_id_func: Callable[[str], str],
        get_switch_state_func: Callable[[str], bool],
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

        # Forecast history storage (HA Storage) - persists predictions across restarts
        self._forecast_history_store: Any = None  # Initialized in async_start
        self._forecast_history_loaded: bool = False

        # History fetcher for historical load data (delegated to separate module)
        self._history_fetcher = HistoryFetcher(hass, entry)

        # Weather correlation for temperature-based consumption prediction
        self._weather_correlation: WeatherCorrelation | None = None

        # Forecast computer for 15-minute battery SOC forecasting
        # Pass day-aware profile function for issue-60
        self._forecast_computer = ForecastComputer(
            entry,
            get_entity_id_func,
            self._get_historical_hourly_averages,
            self._get_profile_for_day,
            weather_correlation=self._weather_correlation,
        )

        self._price_calculator = PriceCalculator(
            entry=entry,
            parse_forecast_dt=self._parse_forecast_dt,
            percentile_func=self._percentile,
            sum_solar_before_target=self._sum_solar_before_target,
            get_expected_load_kw=self._get_expected_load_kw,
        )
        self._mode_decision = ModeDecisionEngine(
            get_switch_state=self._get_switch_state,
            get_forecast_entry_for_now=self._get_forecast_entry_for_now,
        )
        self._spike_analyzer = SpikeAnalyzer(
            entry=entry,
            get_switch_state=self._get_switch_state,
            parse_time_option=self._parse_time_option,
            analyze_spike_window=analyze_spike_window,
            calculate_spike_price_threshold=calculate_spike_price_threshold,
        )
        self._excess_solar_signals = ExcessSolarSignalsEngine(
            entry=entry,
            forecast_computer=self._forecast_computer,
            get_entity_id=self._get_entity_id,
            get_historical_hourly_averages=self._get_historical_hourly_averages,
            recent_load_1hr_getter=lambda: self._recent_load_1hr_kw,
            parse_time_option=self._parse_time_option,
        )
        self._forecast_accuracy = ForecastAccuracyEngine()
        self._weather_diagnostics = WeatherDiagnosticsEngine(entry)

        # Change tracker for forecast regeneration
        self._forecast_change_tracker = ForecastChangeTracker()

        # Local cache properties (delegated to history_fetcher for storage)
        self._last_weighting: float = DEFAULT_LOAD_WEIGHT_RECENT
        self._previous_active_mode = None
        self._last_forecast_hour: int | None = None
        self._last_decision_log_time: datetime | None = None

        # Baseline load profile for Issue #137 (set by coordinator)
        self._baseline_avg_kw: dict[int, float] = {}

        # Thermal manager for HVAC-aware load forecasting (Issue #152)
        self._thermal_manager: Any = None

    # ========================================================================
    # MAIN ENTRY POINT
    # ========================================================================

    def compute_derived_values(self, data: CoordinatorData) -> None:
        """Compute all derived sensor/binary_sensor values from raw state.

        Ported from Jinja templates in YAML package. Steps are ordered
        by dependency — later steps can reference earlier results.
        """
        now_dt = dt_util.now()

        # Pass adaptive parameters to forecast computer (Issue #170 Phase 2)
        self._forecast_computer.set_adaptive_params(data.adaptive_params)

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
        # Pass current charging state for hysteresis (Issue #34)
        self._compute_daily_15min_forecast(data, now_dt)

        # ---- Step 5: solar_can_reach_target (derived from detailed forecast) ----
        # Derive from detailed forecast - single source of truth
        dw_entry = self._get_forecast_at_demand_window(data, target_hour)
        if dw_entry:
            data.solar_can_reach_target = dw_entry["predicted_soc"] >= target_pct
        else:
            # Fallback if forecast doesn't span to DW (e.g., late in day)
            # Use current SOC as a conservative estimate
            data.solar_can_reach_target = data.soc >= target_pct

        # ---- Step 6: boost_charge_needed ----
        # Read from CURRENT forecast slot (not DW entry) - fixes #44
        # The boost_charge_needed flag indicates if boost charging is needed NOW,
        # which is determined by the current slot's grid_charge_boost flag.
        current_entry = self._get_forecast_entry_for_now(data, now_dt)
        data.boost_charge_needed = (
            current_entry.get("grid_charge_boost", False) if current_entry else False
        )

        # ---- Step 6b: solar_can_reach_target_in_dw ----
        # MOVED BEFORE solar_battery_forecast so boost_needed can use this flag.
        # This ensures the diagnostic boost_needed matches the actual decision logic.
        # Read from switch state (device-level toggle)
        allow_dw_under_target = self._get_switch_state(
            SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET
        )

        if allow_dw_under_target and before_dw:
            # Simulate solar-only charging through entire DW period
            dw_end_time_obj = self._parse_time_option(
                CONF_DEMAND_WINDOW_END, DEFAULT_DEMAND_WINDOW_END
            )
            sim_end = now_dt.replace(
                hour=dw_end_time_obj.hour,
                minute=dw_end_time_obj.minute,
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
            soc_at_end, max_soc, can_reach, _ = (
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
                dw_end_time_obj.strftime("%H:%M"),
                can_reach,
            )
        else:
            data.solar_can_reach_target_in_dw = False

        # ---- Step 7: effective_cheap_price (final update) ----
        # Update effective_cheap_price with actual solar_can_reach_target from forecast
        self._compute_effective_cheap_price(data, now_dt, before_dw, target_hour)

        # ---- Step 8: cheap_charge_stop_price ----
        # Hardcoded deadband (Issue #214)
        deadband = DEFAULT_CHEAP_PRICE_DEADBAND
        data.cheap_charge_stop_price = round(data.effective_cheap_price + deadband, 2)

        # ---- Step 4: solar_battery_forecast (legacy - for backwards compatibility) ----
        # Kept for API compatibility, but values derived from detailed forecast
        self._compute_solar_battery_forecast(
            data, now_dt, target_hour, before_dw, after_dw
        )

        # ---- Step 9: forecast_spike_within_window ----
        # Hardcoded lookahead (Issue #214)
        lookahead = DEFAULT_FORECAST_LOOKAHEAD_HOURS
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

        # ---- Step 17: excess_solar_signals (backlog-high-017) ----
        self._compute_excess_solar_signals(data, now_dt)

        # ---- Step 18: weather correlation diagnostics (Issue #61) ----
        self._populate_weather_diagnostics(data)

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
                # Include tomorrow's forecast when target is tomorrow morning
                all_solcast = [*data.solcast_today, *data.solcast_tomorrow]
                solar_kwh = self._sum_solar_before_target(
                    all_solcast, now_dt, target_hour
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

                # Boost needed if solar alone can't reach target.
                # When allow_dw_entry_under_target is enabled, use solar_can_reach_target_in_dw
                # which simulates solar charging through the DW period (not just to DW START).
                # This ensures the diagnostic flag matches the actual decision logic.
                allow_dw_under_target = self._get_switch_state(
                    SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET
                )
                if allow_dw_under_target and hasattr(
                    data, "solar_can_reach_target_in_dw"
                ):
                    # Use the DW-extended simulation result for consistency
                    boost_needed = (
                        data.soc < target_pct and not data.solar_can_reach_target_in_dw
                    )
                else:
                    # Standard calculation: boost needed if solar alone can't reach target before DW
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
                # Include tomorrow's forecast when target is tomorrow morning
                all_solcast = [*data.solcast_today, *data.solcast_tomorrow]
                solar_kwh = self._sum_solar_before_target(
                    all_solcast, now_dt, target_hour
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
                        remaining_deficit / (CHARGE_RATE_GRID_KW * 0.9)
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
        """Store forecast prediction to history for planned vs actual comparison.

        Stores predictions at specific future times for accuracy tracking:
        - What SOC we predict for 15 minutes from now
        - What SOC we predict for 1 hour from now
        - What SOC we predict for 4 hours from now

        Each prediction has a target_time so we can later compare:
        "What did we predict for time T?" vs "What was actual at time T?"
        """
        # Find forecast entries for specific future times
        slots = data.daily_forecast
        if not slots:
            return

        # Store predictions for 15min, 1h, and 4h into the future
        # These will be compared when that time arrives
        for offset_minutes in [15, 60, 240]:
            target_dt = now_dt + timedelta(minutes=offset_minutes)

            # Normalize target_dt timezone for comparison
            if target_dt.tzinfo is None:
                target_dt = dt_util.as_local(dt_util.as_utc(target_dt))
            else:
                target_dt = dt_util.as_local(target_dt)

            # Find the forecast slot that covers target_dt
            for slot in slots:
                ts = slot.get("timestamp", "")
                if not ts:
                    continue
                try:
                    slot_dt = datetime.fromisoformat(ts)
                except ValueError:
                    continue

                # Normalize timezone
                if slot_dt.tzinfo is None:
                    slot_dt = dt_util.as_local(dt_util.as_utc(slot_dt))
                else:
                    slot_dt = dt_util.as_local(slot_dt)

                # Check if this slot covers target_dt (within slot interval)
                slot_interval = slot.get("slot_interval_minutes", 15)
                slot_end = slot_dt + timedelta(minutes=slot_interval)

                if slot_dt <= target_dt < slot_end:
                    entry = {
                        "prediction_time": now_dt.isoformat(),
                        "target_time": target_dt.isoformat(),
                        "offset_minutes": offset_minutes,
                        "predicted_soc": slot.get("predicted_soc", 0),
                        "predicted_buy_price": slot.get("buy_price", 0),
                        "predicted_sell_price": slot.get("sell_price", 0),
                    }
                    data.forecast_history.append(entry)
                    break

        # Also store the legacy DW prediction for compatibility
        entry = {
            "timestamp": now_dt.isoformat(),
            "predicted_soc": round(predicted_soc, 1),
            "solar_before_dw_kwh": round(solar_kwh, 2),
            "consumption_estimate_kwh": round(consumption_kwh, 2),
        }
        data.forecast_history.append(entry)

        # Keep last 200 entries (allows 4+ hours of predictions across multiple days)
        if len(data.forecast_history) > 200:
            data.forecast_history = data.forecast_history[-200:]

    def set_baseline_load(self, baseline_avg_kw: dict[int, float]) -> None:
        """Set the baseline load profile for Issue #137 feedback loop fix.

        Called by coordinator to provide the estimated non-HVAC baseline
        consumption. This is used for grid charging decisions to prevent
        the feedback loop where HVAC load triggers unnecessary charging.

        Args:
            baseline_avg_kw: Dict of hour -> baseline load in kW.
        """
        self._baseline_avg_kw = baseline_avg_kw
        if baseline_avg_kw:
            _LOGGER.debug(
                "Baseline load set: %d hours, avg=%.2f kW",
                len(baseline_avg_kw),
                sum(baseline_avg_kw.values()) / len(baseline_avg_kw),
            )

    def set_thermal_manager(self, thermal_manager: Any | None) -> None:
        """Set the thermal manager for HVAC-aware load forecasting (Issue #152).

        Called by coordinator to provide the thermal manager instance.
        This enables HVAC load prediction in the forecast.

        Args:
            thermal_manager: ThermalManager instance, or None to disable.
        """
        self._thermal_manager = thermal_manager
        # Propagate to forecast computer
        self._forecast_computer.set_thermal_manager(thermal_manager)
        if thermal_manager:
            _LOGGER.info("Thermal manager set for HVAC-aware load forecasting")

    def _compute_daily_15min_forecast(
        self,
        data: CoordinatorData,
        now_dt: datetime,
    ) -> None:
        """Compute full 24-hour forecast with 15-minute breakdown (delegates to ForecastComputer).

        Provides 4x granularity over hourly forecast, capturing meaningful
        price variations from 5-minute pricing data.

        Uses change detection to skip unnecessary recomputations.

        Issue #137: Uses baseline load (non-HVAC) for grid charging decisions
        when available, preventing the feedback loop where HVAC spikes trigger
        unnecessary grid charging.

        Price Block Stability: Uses price sensor update timestamps to ensure
        ONE decision per price block, preventing flip-flopping within the same
        5-minute price period.
        """
        # Get price sensor update timestamps for stable decisions
        price_update_time = self._get_price_sensor_update_time()

        # Check if recompute is needed
        should_recompute, reason = (
            self._forecast_change_tracker.should_recompute_forecast(
                soc=data.soc,
                price=data.general_price,
                feed_in_price=data.feed_in_price,
                now_dt=now_dt,
                price_update_time=price_update_time,
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

                # Issue #137: Use baseline load if available for grid charging decisions
                # This prevents HVAC spikes from triggering unnecessary grid charging
                baseline_for_forecast = (
                    self._baseline_avg_kw if self._baseline_avg_kw else None
                )

                if baseline_for_forecast:
                    _LOGGER.info(
                        "Using baseline load for forecast (Issue #137): avg=%.2f kW vs historical %.2f kW",
                        sum(baseline_for_forecast.values())
                        / len(baseline_for_forecast),
                        sum(hourly_avg_kw.values()) / len(hourly_avg_kw)
                        if hourly_avg_kw
                        else 0,
                    )

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
                    baseline_avg_kw=baseline_for_forecast,  # Issue #137
                )

                # Also keep a compact 24-entry hourly view for markdown table
                data.daily_forecast_hourly = build_hourly_forecast_summary(
                    data.daily_forecast
                )

                # Propagate recent load diagnostic fields for dashboard debugging
                data.recent_load_1hr_statistic_id = self._recent_load_1hr_statistic_id
                data.recent_load_1hr_samples = self._recent_load_1hr_samples
                data.recent_load_1hr_last_error = self._recent_load_1hr_last_error

                # Propagate day-of-week profile diagnostics (issue-60)
                weekday_avg, weekday_counts = (
                    self._history_fetcher.get_weekday_profile()
                )
                weekend_avg, weekend_counts = (
                    self._history_fetcher.get_weekend_profile()
                )
                data.consumption_profile_type = (
                    self._history_fetcher.get_profile_source()
                )
                # Determine which profile is selected for TODAY's forecast
                now_local = dt_util.now()
                day_of_week = now_local.weekday()  # Monday=0, Sunday=6
                if day_of_week >= 5:  # Saturday or Sunday
                    data.forecast_profile_selected = "weekend"
                else:
                    data.forecast_profile_selected = "weekday"
                data.weekday_sample_counts = weekday_counts
                data.weekend_sample_counts = weekend_counts
                data.weekday_hourly_profile_kw = weekday_avg
                data.weekend_hourly_profile_kw = weekend_avg

                # Store forecast history on every recompute (Issue #131)
                # This ensures predictions are available for accuracy tracking
                self._store_forecast_history_every_update(data, now_dt)

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
        """Compute preliminary effective cheap price threshold."""
        self._price_calculator.compute_effective_cheap_price_preliminary(
            data=data,
            now_dt=now_dt,
            before_dw=before_dw,
            target_hour=target_hour,
            target_pct=target_pct,
        )

    def _compute_effective_cheap_price(
        self, data: CoordinatorData, now_dt: datetime, before_dw: bool, target_hour: int
    ) -> None:
        """Compute final effective cheap price threshold."""
        self._price_calculator.compute_effective_cheap_price(
            data=data,
            now_dt=now_dt,
            before_dw=before_dw,
            target_hour=target_hour,
        )

    def _compute_solar_weighted_avg_fit(
        self, data: CoordinatorData, now_dt: datetime, target_hour: int, after_dw: bool
    ) -> None:
        """Compute solar-weighted average feed-in tariff."""
        self._price_calculator.compute_solar_weighted_avg_fit(
            data=data,
            now_dt=now_dt,
            target_hour=target_hour,
            after_dw=after_dw,
        )

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
        self._mode_decision.compute_active_mode(data, now_dt)

    def _add_to_decision_log(
        self, data: CoordinatorData, now_dt: datetime, mode_change: bool
    ) -> None:
        """Add entry to decision log when mode changes or periodically."""
        self._previous_active_mode = self._mode_decision.add_to_decision_log(
            data=data,
            now_dt=now_dt,
            previous_active_mode=self._previous_active_mode,
            mode_change=mode_change,
        )
        self._last_decision_log_time = now_dt

    def _get_expected_load_kw(
        self, data: CoordinatorData, hours_to_target: float
    ) -> float:
        """Calculate expected load based on 7-day historical averages with time-distance weighting.

        Uses time-distance weighting consistent with forecast slots:
        - For hours close to current time: blend recent load with historical
        - For distant hours: use historical profile only

        This prevents overestimation when recent load is much lower than historical averages.
        """
        load_entity_id = self._get_entity_id("teslemetry_load_power")

        # Get cached historical hourly averages
        hourly_avg_kw = self._get_historical_hourly_averages(load_entity_id)

        # Get recent 1-hour load for weighted blending
        recent_load_kw = self._recent_load_1hr_kw

        # Get weighting configuration (hardcoded default - Issue #214)
        recent_weight = DEFAULT_LOAD_WEIGHT_RECENT
        historical_weight = 1.0 - recent_weight

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
            hours_counted = 0

            # Sum hours from now until target hour with time-distance weighting
            while hour != target_hour:
                historical_kw = hourly_avg_kw.get(hour, 0.0)

                # TIME-DISTANCE WEIGHTING: Only blend recent load for hours close to now
                # Calculate distance from current hour (handles midnight wrap)
                hour_distance = abs(hour - current_hour)
                hour_distance = min(hour_distance, 24 - hour_distance)

                # Only apply weighted blend for hours within 3 hours of current time
                # Beyond that, recent load is NOT predictive
                max_blend_distance = 3

                if (
                    hour_distance <= max_blend_distance
                    and recent_load_kw > 0
                    and recent_weight > 0
                    and historical_kw > 0
                ):
                    # Blend recent load with historical for nearby hours
                    load_kw = (recent_weight * recent_load_kw) + (
                        historical_weight * historical_kw
                    )
                    _LOGGER.debug(
                        "Load estimate for hour %d: %.2f kW (blended: recent=%.2f, hist=%.2f, distance=%d)",
                        hour,
                        load_kw,
                        recent_load_kw,
                        historical_kw,
                        hour_distance,
                    )
                else:
                    # Use historical only for distant hours
                    load_kw = historical_kw

                total_expected_kwh += load_kw
                hours_counted += 1
                hour = (hour + 1) % 24
                # Safety: don't loop forever
                if hour == current_hour:
                    break

            # Add 10% buffer to be conservative
            total_expected_kwh *= 1.1

            if total_expected_kwh > 0 and hours_counted > 0:
                # Return average kW
                return total_expected_kwh / max(hours_to_target, 1)

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

    def _get_profile_for_day(
        self, target_date: datetime
    ) -> tuple[dict[int, float], dict[int, int], str]:
        """Get day-aware consumption profile based on target day's day-of-week.

        Args:
            target_date: The date to get the profile for

        Returns:
            Tuple of (hourly_avg_kw, sample_counts, source) where source is
            "weekday", "weekend", or "combined" (fallback).
        """
        return self._history_fetcher.get_profile_for_day(target_date)

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
    # FORECAST HISTORY PERSISTENCE (Issue #131)
    # ========================================================================

    async def async_initialize_forecast_history_storage(self) -> None:
        """Initialize forecast history storage and load persisted data."""
        try:
            from homeassistant.helpers.storage import Store

            self._forecast_history_store = Store(
                self.hass, 1, "localshift_forecast_history"
            )
            _LOGGER.info("Forecast history storage initialized")
        except Exception as e:
            _LOGGER.warning("Failed to initialize forecast history storage: %s", e)
            self._forecast_history_store = None

    async def async_load_forecast_history(self, data: CoordinatorData) -> None:
        """Load persisted forecast history from storage.

        Called during coordinator startup to restore predictions across restarts.
        """
        if self._forecast_history_store is None:
            _LOGGER.debug("No forecast history store available")
            return

        try:
            stored_data = await self._forecast_history_store.async_load()
            if stored_data and isinstance(stored_data, dict):
                history = stored_data.get("forecast_history", [])
                first_prediction = stored_data.get("first_prediction_time", "")

                if history:
                    # Filter out entries with target_time in the past (older than 4 hours)
                    # These are no longer useful for accuracy tracking
                    now_dt = dt_util.now()
                    cutoff = now_dt - timedelta(hours=4)

                    valid_entries = []
                    for entry in history:
                        if "target_time" not in entry:
                            continue
                        try:
                            target_dt = datetime.fromisoformat(entry["target_time"])
                            if target_dt.tzinfo is None:
                                target_dt = dt_util.as_local(dt_util.as_utc(target_dt))
                            else:
                                target_dt = dt_util.as_local(target_dt)

                            if target_dt >= cutoff:
                                valid_entries.append(entry)
                        except (ValueError, TypeError):
                            continue

                    data.forecast_history = valid_entries
                    data.forecast_first_prediction_time = first_prediction
                    data.forecast_history_count = len(valid_entries)

                    _LOGGER.info(
                        "Loaded %d forecast history entries from storage (filtered from %d)",
                        len(valid_entries),
                        len(history),
                    )

                    # Find first prediction time from loaded entries if not stored
                    if not first_prediction and valid_entries:
                        for entry in valid_entries:
                            if entry.get("prediction_time"):
                                data.forecast_first_prediction_time = entry[
                                    "prediction_time"
                                ]
                                break

                self._forecast_history_loaded = True
        except Exception as e:
            _LOGGER.warning("Failed to load forecast history: %s", e)

    async def async_save_forecast_history(self, data: CoordinatorData) -> None:
        """Persist forecast history to storage.

        Called after new predictions are stored.
        """
        if self._forecast_history_store is None:
            return

        try:
            # Store only entries with target_time (not legacy entries)
            entries_to_save = [
                entry
                for entry in data.forecast_history
                if "target_time" in entry and "offset_minutes" in entry
            ]

            # Limit to recent entries (keep storage size manageable)
            if len(entries_to_save) > 100:
                entries_to_save = entries_to_save[-100:]

            stored_data = {
                "forecast_history": entries_to_save,
                "first_prediction_time": data.forecast_first_prediction_time,
            }

            await self._forecast_history_store.async_save(stored_data)
            _LOGGER.debug("Saved %d forecast history entries", len(entries_to_save))
        except Exception as e:
            _LOGGER.warning("Failed to save forecast history: %s", e)

    def _store_forecast_history_every_update(
        self,
        data: CoordinatorData,
        now_dt: datetime,
    ) -> None:
        """Store forecast predictions on every forecast recompute.

        This ensures we always have predictions available for accuracy tracking,
        not just on hour boundaries.

        Args:
            data: CoordinatorData to update
            now_dt: Current datetime
        """
        slots = data.daily_forecast
        if not slots:
            return

        # Track if this is the first prediction
        is_first_prediction = len(data.forecast_history) == 0

        # Store predictions for 15min, 1h, and 4h into the future
        for offset_minutes in [15, 60, 240]:
            target_dt = now_dt + timedelta(minutes=offset_minutes)

            # Normalize target_dt timezone
            if target_dt.tzinfo is None:
                target_dt = dt_util.as_local(dt_util.as_utc(target_dt))
            else:
                target_dt = dt_util.as_local(target_dt)

            # Find the forecast slot that covers target_dt
            for slot in slots:
                ts = slot.get("timestamp", "")
                if not ts:
                    continue
                try:
                    slot_dt = datetime.fromisoformat(ts)
                except ValueError:
                    continue

                # Normalize timezone
                if slot_dt.tzinfo is None:
                    slot_dt = dt_util.as_local(dt_util.as_utc(slot_dt))
                else:
                    slot_dt = dt_util.as_local(slot_dt)

                # Check if this slot covers target_dt
                slot_interval = slot.get("slot_interval_minutes", 15)
                slot_end = slot_dt + timedelta(minutes=slot_interval)

                if slot_dt <= target_dt < slot_end:
                    # Check if we already have a prediction for this target_time
                    # Avoid duplicates by checking target_time + offset combination
                    target_key = f"{target_dt.isoformat()}_{offset_minutes}"
                    existing_keys = {
                        f"{e.get('target_time')}_{e.get('offset_minutes')}"
                        for e in data.forecast_history
                        if "target_time" in e and "offset_minutes" in e
                    }

                    if target_key not in existing_keys:
                        entry = {
                            "prediction_time": now_dt.isoformat(),
                            "target_time": target_dt.isoformat(),
                            "offset_minutes": offset_minutes,
                            "predicted_soc": slot.get("predicted_soc", 0),
                            "predicted_buy_price": slot.get("buy_price", 0),
                            "predicted_sell_price": slot.get("sell_price", 0),
                        }
                        data.forecast_history.append(entry)

                        # Track first prediction time
                        if is_first_prediction:
                            data.forecast_first_prediction_time = now_dt.isoformat()
                            is_first_prediction = False
                    break

        # Update count
        data.forecast_history_count = len(data.forecast_history)

        # Keep last 200 entries
        if len(data.forecast_history) > 200:
            data.forecast_history = data.forecast_history[-200:]

    # ========================================================================
    # WEATHER CORRELATION (Issue #61)
    # ========================================================================

    async def async_initialize_weather_correlation(self) -> None:
        """Initialize weather correlation system.

        Should be called during coordinator startup.
        """
        weather_learning_enabled = self.entry.options.get(
            CONF_WEATHER_LEARNING_ENABLED, DEFAULT_WEATHER_LEARNING_ENABLED
        )

        if not weather_learning_enabled:
            _LOGGER.debug("Weather learning disabled, skipping initialization")
            return

        try:
            self._weather_correlation = WeatherCorrelation(self.hass, self.entry)
            await self._weather_correlation.async_initialize()
            self._forecast_computer.set_weather_correlation(self._weather_correlation)
            _LOGGER.info("Weather correlation initialized successfully")
        except Exception as e:
            _LOGGER.error("Failed to initialize weather correlation: %s", e)
            self._weather_correlation = None
            self._forecast_computer.set_weather_correlation(None)

    async def async_learn_weather_sample(self, data: CoordinatorData) -> None:
        """Learn from current temperature/load observation.

        Called periodically to update the learning model with actual
        temperature and load data.

        Args:
            data: CoordinatorData with current weather and load values
        """
        if self._weather_correlation is None:
            return

        weather_learning_enabled = self.entry.options.get(
            CONF_WEATHER_LEARNING_ENABLED, DEFAULT_WEATHER_LEARNING_ENABLED
        )

        if not weather_learning_enabled:
            return

        # Only learn if we have valid temperature and load data
        current_temp = data.weather_temperature_current
        if current_temp <= 0:  # Invalid temperature
            return

        current_load = data.load_power_kw
        if current_load <= 0:
            return

        now_dt = dt_util.now()
        current_hour = now_dt.hour

        # Learn from this sample
        self._weather_correlation.learn_from_sample(
            hour=current_hour,
            temperature=current_temp,
            actual_load_kw=current_load,
        )

        # Save periodically (every hour)
        if current_hour != getattr(self, "_last_weather_save_hour", -1):
            await self._weather_correlation.async_save()
            self._last_weather_save_hour = current_hour

    async def async_refresh_weather_forecast(self) -> list | None:
        """Refresh temperature forecast from weather entity.

        Uses the modern weather.get_forecasts service (HA 2024.3+) with caching.
        Should be called periodically (e.g., every 30 minutes) by the coordinator.

        Returns:
            List of TemperatureForecast objects, or None if unavailable.
        """
        if self._weather_correlation is None:
            _LOGGER.info(
                "Weather correlation not initialized, skipping forecast refresh"
            )
            return None

        weather_learning_enabled = self.entry.options.get(
            CONF_WEATHER_LEARNING_ENABLED, DEFAULT_WEATHER_LEARNING_ENABLED
        )

        if not weather_learning_enabled:
            _LOGGER.debug("Weather learning disabled, skipping forecast refresh")
            return None

        try:
            forecasts = await self._weather_correlation.async_get_temperature_forecast()
            _LOGGER.info(
                "Refreshed %d temperature forecasts from weather entity",
                len(forecasts),
            )
            return forecasts
        except Exception as e:
            _LOGGER.warning("Failed to refresh weather forecast: %s", e)
            return None

    @property
    def weather_correlation(self) -> WeatherCorrelation | None:
        """Get the weather correlation instance for external access."""
        return self._weather_correlation

    # ========================================================================
    # SPIKE ANALYSIS (Conservative Spike Discharge)
    # ========================================================================

    def _analyze_spike(
        self,
        data: CoordinatorData,
        now_dt: datetime,
    ) -> None:
        """Analyze feed-in forecast for spike window details."""
        self._spike_analyzer.analyze_spike(data, now_dt)

    # ========================================================================
    # EXCESS SOLAR LOAD SHIFTING (backlog-high-017)
    # ========================================================================

    def _compute_excess_solar_signals(
        self,
        data: CoordinatorData,
        now_dt: datetime,
    ) -> None:
        """Compute excess solar load shifting signals."""
        self._excess_solar_signals.compute_signals(data, now_dt)

    # ========================================================================
    # FORECAST ACCURACY TRACKING (Issue #37 Phase 2)
    # ========================================================================

    async def async_compute_forecast_accuracy(self, data: CoordinatorData) -> None:
        """Compare past forecast predictions with actual outcomes."""
        await self._forecast_accuracy.compute_forecast_accuracy(data)

    # ========================================================================
    # WEATHER DIAGNOSTICS (Issue #61)
    # ========================================================================

    def _populate_weather_diagnostics(self, data: CoordinatorData) -> None:
        """Populate weather correlation diagnostic fields."""
        self._weather_diagnostics.populate_weather_diagnostics(
            data=data,
            weather_correlation=self._weather_correlation,
        )

    # ========================================================================
    # PRICE SENSOR UPDATE TIME (Price Block Stability)
    # ========================================================================

    def _get_price_sensor_update_time(self) -> datetime | None:
        """Get the most recent update timestamp from price sensors.

        Watches both general_price and feed_in_price sensors.
        Returns the later of the two timestamps, which indicates when
        the price BLOCK changed (both update simultaneously from Amber).

        Returns:
            datetime of the most recent price sensor update, or None if unavailable.
        """
        try:
            # Get both price sensors (using correct config keys)
            general_price_entity_id = self._get_entity_id("pricing_general_price")
            feed_in_price_entity_id = self._get_entity_id("pricing_feed_in_price")

            _LOGGER.debug(
                "Price sensor entity IDs: general=%s, feed_in=%s",
                general_price_entity_id,
                feed_in_price_entity_id,
            )

            general_price_state = self.hass.states.get(general_price_entity_id)
            feed_in_price_state = self.hass.states.get(feed_in_price_entity_id)

            # Get the later of the two update timestamps
            # Both should update simultaneously from Amber, but use max to be safe
            update_times = []

            if general_price_state is not None:
                update_times.append(general_price_state.last_updated)
                _LOGGER.debug(
                    "General price sensor last_updated: %s",
                    general_price_state.last_updated,
                )

            if feed_in_price_state is not None:
                update_times.append(feed_in_price_state.last_updated)
                _LOGGER.debug(
                    "Feed-in price sensor last_updated: %s",
                    feed_in_price_state.last_updated,
                )

            if not update_times:
                _LOGGER.warning(
                    "No price sensor states found: general=%s, feed_in=%s",
                    general_price_state,
                    feed_in_price_state,
                )
                return None

            # Return the most recent update time
            result = max(update_times)
            _LOGGER.debug("Price sensor update time: %s", result)
            return result

        except Exception as e:
            _LOGGER.warning("Could not get price sensor update time: %s", e)
            return None

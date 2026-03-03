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
    HistoryFetcher,
    PriceCalculator,
    SpikeAnalyzer,
    WeatherDiagnosticsEngine,
    analyze_spike_window,
    calculate_spike_price_threshold,
    max_forecast_price,
    parse_forecast_dt,
    percentile,
    scan_forecast_for_spike,
    sum_solar_before_target,
)
from .computation_engine_lib.excess_solar import ExcessSolarEngine
from .computation_engine_lib.load_forecaster import LoadForecaster
from .computation_engine_lib.optimizer_dp import DPPlanner
from .computation_engine_lib.optimizer_shadow_runner import (
    OptimizerSafetyGate,
    _build_optimizer_config,
    _build_summary,
    _derive_runtime_apply_plan,
    _find_current_slot_index,
    _normalize_initial_soc,
    _serialize_decision,
    _serialize_result,
)
from .computation_engine_lib.slot_builder import SlotBuilder
from .computation_engine_lib.slot_schedule import TOTAL_SLOTS
from .computation_engine_lib.soc_simulator import SocSimulator
from .const import (
    BATTERY_CAPACITY_KWH,
    CHARGE_RATE_GRID_KW,
    CONF_ALLOW_DW_ENTRY_UNDER_TARGET,
    CONF_BATTERY_TARGET,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    CONF_EXPORT_PRICE_MARGIN,
    CONF_MINIMUM_TARGET_SOC,
    CONF_OPTIMIZATION_MODE,
    CONF_OPTIMIZER_CONTROL_MODE,
    CONF_OPTIMIZER_ENABLED,
    CONF_SUN_ENTITY,
    CONF_WEATHER_LEARNING_ENABLED,
    DEFAULT_ALLOW_DW_ENTRY_UNDER_TARGET,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_DEADBAND,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_EXPORT_PRICE_MARGIN,
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    DEFAULT_LOAD_WEIGHT_RECENT,
    DEFAULT_MINIMUM_TARGET_SOC,
    DEFAULT_OPTIMIZATION_MODE,
    DEFAULT_OPTIMIZER_CONTROL_MODE,
    DEFAULT_OPTIMIZER_ENABLED,
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

        # Phase 4 (#441): Create engines directly (no ForecastComputer wrapper)
        self._load_forecaster = LoadForecaster(
            entry=entry, weather_correlation=self._weather_correlation
        )
        self._soc_simulator = SocSimulator(
            estimate_hourly_consumption_kw=self._load_forecaster.estimate_hourly_consumption_kw
        )
        self._excess_solar_engine = ExcessSolarEngine(
            entry=entry,
            estimate_hourly_consumption_kw=self._load_forecaster.estimate_hourly_consumption_kw,
            simulate_with_additional_load=self._soc_simulator._simulate_with_additional_load,
        )

        self._price_calculator = PriceCalculator(
            entry=entry,
            parse_forecast_dt=self._parse_forecast_dt,
            percentile_func=self._percentile,
            sum_solar_before_target=self._sum_solar_before_target,
            get_expected_load_kw=self._get_expected_load_kw_from_slots,
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
            calculate_excess_by_windows=self._excess_solar_engine._calculate_excess_by_windows,
            find_nearest_negative_fit_window=self._excess_solar_engine._find_nearest_negative_fit_window,
            calculate_excess_until_negative_fit=self._excess_solar_engine._calculate_excess_until_negative_fit,
            find_battery_fill_point=self._soc_simulator._find_battery_fill_point,
            calculate_safe_additional_load=self._excess_solar_engine._calculate_safe_additional_load,
            compute_load_shift_signal=self._excess_solar_engine._compute_load_shift_signal,
            get_entity_id=self._get_entity_id,
            get_historical_hourly_averages=self._get_historical_hourly_averages,
            recent_load_1hr_getter=lambda: self._recent_load_1hr_kw,
            parse_time_option=self._parse_time_option,
        )
        self._forecast_accuracy = ForecastAccuracyEngine()
        self._weather_diagnostics = WeatherDiagnosticsEngine(entry)

        # DP Planner for Phase 3 (#441) - eliminates one-cycle lag
        self._dp_planner = DPPlanner()

        # Local cache properties (delegated to history_fetcher for storage)
        self._last_weighting: float = DEFAULT_LOAD_WEIGHT_RECENT
        self._previous_active_mode = None
        self._last_forecast_hour: int | None = None
        self._last_decision_log_time: datetime | None = None

        # Baseline load profile for Issue #137 (set by coordinator)
        self._baseline_avg_kw: dict[int, float] = {}

    # ========================================================================
    # MAIN ENTRY POINT
    # ========================================================================

    def compute_derived_values(self, data: CoordinatorData) -> None:
        """Compute all derived sensor/binary_sensor values from raw state.

        Ported from Jinja templates in YAML package. Steps are ordered
        by dependency — later steps can reference earlier results.
        """
        now_dt = dt_util.now()

        # Pass adaptive parameters to load forecaster (Issue #170 Phase 2)
        self._load_forecaster.set_adaptive_params(data.adaptive_params)

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

        # ---- Manual override check (Phase 4, #441: moved from deleted ModeDecisionEngine) ----
        # Always respect manual override first — user is in control
        if data.manual_override:
            data.active_mode = _BatteryMode.MANUAL
            data.debug_mode_source = "manual_override"
            # Skip DP optimizer and other mode decisions when in manual mode
            return

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

        # ---- Phase 1 (#441): Shared load forecast slots ----
        # Builds data.load_forecast_slots for use by DP optimizer and other helpers.
        load_entity_id = self._get_entity_id("teslemetry_load_power")
        hourly_avg_kw = self._get_historical_hourly_averages(load_entity_id)
        recent_load_kw = self._recent_load_1hr_kw
        self._compute_load_forecast_slots(data, now_dt, hourly_avg_kw, recent_load_kw)

        # ---- Step DP: Inline DP optimizer (Phase 4, #441) ----
        # Runs before effective_cheap_price final update so solar_can_reach_target
        # is populated from DP result (not legacy solar-only simulation).
        self._run_dp_optimizer_inline(data, now_dt)

        # ---- Step 6: boost_charge_needed (Phase 4: derive from DP decision) ----
        # Read from current-slot DP decision (not forecast_computer).
        current_slot_idx = _find_current_slot_index(data)
        decisions = data.optimizer_shadow_decisions or []
        if decisions and 0 <= current_slot_idx < len(decisions):
            data.boost_charge_needed = decisions[current_slot_idx].get(
                "grid_charge_boost", False
            )
        else:
            data.boost_charge_needed = False

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

        # ---- Step 12: decision_log ----
        # Phase 4 (#441): active_mode is set by _run_dp_optimizer_inline (Phase 3).
        # _compute_active_mode is removed in Phase 4.

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

            # Use DP decision if available (Phase 4, #441)
            dw_entry = self._get_dp_decision_at_demand_window(data, target_hour)
            if dw_entry:
                predicted_soc = dw_entry["predicted_soc_pct"]
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
            # Before DW: use DP decision (Phase 4, #441)
            dw_entry = self._get_dp_decision_at_demand_window(data, target_hour)

            if dw_entry:
                # Use DP decision - includes grid charging effects
                predicted_soc = dw_entry["predicted_soc_pct"]
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

                # Consumption estimate from load_forecast_slots (Phase 4)
                expected_load_kw = self._get_expected_load_kw_from_slots(
                    data, hours_to_target
                )
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
                # Fallback if DP decision unavailable
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
                expected_load_kw = self._get_expected_load_kw_from_slots(
                    data, hours_to_target
                )
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
                self._store_forecast_history(data, now_dt)
                self._last_forecast_hour = current_hour

    def _store_forecast_history(
        self,
        data: CoordinatorData,
        now_dt: datetime,
    ) -> None:
        """Store DP optimizer predictions to history for planned vs actual comparison (Phase 4, #441).

        Stores predictions at specific future times for accuracy tracking:
        - What SOC we predict for 15 minutes from now
        - What SOC we predict for 1 hour from now
        - What SOC we predict for 4 hours from now

        Each prediction has a target_time so we can later compare:
        "What did we predict for time T?" vs "What was actual at time T?"
        """
        # Find DP decision entries for specific future times
        slots = data.optimizer_shadow_decisions or []
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

            # Find the DP decision slot that covers target_dt
            for slot in slots:
                ts = slot.get("timestamp_iso", "")
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
                        "predicted_soc": slot.get("predicted_soc_pct", 0),
                        "predicted_buy_price": slot.get("buy_price", 0),
                        "predicted_sell_price": slot.get("sell_price", 0),
                    }
                    data.forecast_history.append(entry)
                    break

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

    def _compute_load_forecast_slots(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        historical_avg_kw: dict[int, float],
        recent_load_kw: float,
    ) -> None:
        """Populate data.load_forecast_slots with per-slot kW estimates.

        Runs before ForecastComputer.compute_forecast() so both the legacy planner
        and the DP optimizer can read from a shared intermediate (Issue #441 Phase 1).

        Uses LoadForecaster (exponential decay, Issue #381) to produce the same
        values that ForecastComputer._estimate_hourly_consumption_kw() would produce.
        Slots are fixed 15-min (TOTAL_SLOTS = 96) aligned to the current 5-min boundary.
        """
        current_5min = (now_dt.minute // 5) * 5
        base_slot = now_dt.replace(minute=current_5min, second=0, microsecond=0)
        current_hour = base_slot.hour

        slots: list[float] = []
        for i in range(TOTAL_SLOTS):
            slot_start = base_slot + timedelta(minutes=15 * i)
            slot_hour = slot_start.hour
            load_kw, _ = self._load_forecaster.estimate_hourly_consumption_kw(
                hourly_avg_kw=historical_avg_kw,
                slot_hour=slot_hour,
                current_hour=current_hour,
                current_load_kw=data.load_power_kw,
                recent_load_kw=recent_load_kw,
            )
            slots.append(load_kw)

        data.load_forecast_slots = slots
        _LOGGER.debug(
            "load_forecast_slots: %d slots computed, first=%.3f kW, last=%.3f kW",
            len(slots),
            slots[0] if slots else 0.0,
            slots[-1] if slots else 0.0,
        )

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

    def _get_dp_decision_at_demand_window(
        self, data: CoordinatorData, target_hour: int
    ) -> dict | None:
        """Get the DP decision at or just after the demand window start time (Phase 4, #441).

        Finds the first decision whose timestamp_iso is at or after the DW start
        (target_hour:00:00). Handles post-DW period by looking for tomorrow's DW
        if today's has passed.
        """
        from datetime import datetime, timedelta  # noqa: PLC0415

        decisions = data.optimizer_shadow_decisions or []
        if not decisions:
            return None

        # Normalise now to tz-aware local time
        now_raw = dt_util.now()
        now_local = (
            dt_util.as_local(dt_util.as_utc(now_raw))
            if now_raw.tzinfo is None
            else dt_util.as_local(now_raw)
        )

        # Calculate DW start datetime
        dw_start_dt = now_local.replace(
            hour=target_hour, minute=0, second=0, microsecond=0
        )
        if dw_start_dt <= now_local:
            dw_start_dt += timedelta(days=1)

        # Find first decision at or after DW start
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

    def _get_expected_load_kw_from_slots(
        self, data: CoordinatorData, hours_to_target: float
    ) -> float:
        """Estimate average load kW until DW using data.load_forecast_slots (Phase 4, #441).

        Averages slots from current slot to DW entry slot. Falls back to current
        load if no forecast slots available.
        """
        if not data.load_forecast_slots:
            return data.load_power_kw if data.load_power_kw > 0 else 0.5

        # Number of 15-min slots until DW (4 slots per hour)
        slots_until_dw = max(1, int(hours_to_target * 4))
        relevant = data.load_forecast_slots[:slots_until_dw]
        return sum(relevant) / len(relevant) if relevant else 0.5

    # ========================================================================
    # PHASE 3 (#441): INLINE DP OPTIMIZER - ELIMINATE ONE-CYCLE LAG
    # ========================================================================

    def _build_optimizer_config_options(self) -> dict[str, Any]:
        """Build config_options dict for optimizer and safety gate.

        Phase 3 (#441): Consolidates config options construction.
        """
        return {
            "optimizer_enabled": self.entry.options.get(
                CONF_OPTIMIZER_ENABLED, DEFAULT_OPTIMIZER_ENABLED
            ),
            CONF_OPTIMIZER_CONTROL_MODE: self.entry.options.get(
                CONF_OPTIMIZER_CONTROL_MODE, DEFAULT_OPTIMIZER_CONTROL_MODE
            ),
            CONF_MINIMUM_TARGET_SOC: self.entry.options.get(
                CONF_MINIMUM_TARGET_SOC, DEFAULT_MINIMUM_TARGET_SOC
            ),
            CONF_BATTERY_TARGET: self.entry.options.get(
                CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET
            ),
            CONF_ALLOW_DW_ENTRY_UNDER_TARGET: self.entry.options.get(
                SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET, DEFAULT_ALLOW_DW_ENTRY_UNDER_TARGET
            ),
            CONF_OPTIMIZATION_MODE: self.entry.options.get(
                CONF_OPTIMIZATION_MODE, DEFAULT_OPTIMIZATION_MODE
            ),
            CONF_EXPORT_PRICE_MARGIN: self.entry.options.get(
                CONF_EXPORT_PRICE_MARGIN, DEFAULT_EXPORT_PRICE_MARGIN
            ),
        }

    def _run_dp_optimizer_inline(self, data: CoordinatorData, now_dt: datetime) -> None:
        """Run DP optimizer inline so active_mode has no cycle lag (Phase 3, #441).

        Steps A–E from the architecture doc:
          A: build slot contexts from raw data
          B: run DPPlanner.plan()
          C: write optimizer fields to data
          D: derive apply plan from current-slot decision
          E: assign data.active_mode (or default to SELF_CONSUMPTION on gate failure)
        """
        import uuid  # noqa: PLC0415

        from homeassistant.util import dt as dt_util  # noqa: PLC0415

        # Read config once for this cycle
        config_options = self._build_optimizer_config_options()

        # Gate: only proceed if optimizer is enabled
        optimizer_enabled = config_options.get("optimizer_enabled", False)
        if not optimizer_enabled:
            # Optimizer disabled — active_mode will be set by legacy path (_compute_active_mode)
            return

        control_mode = config_options.get(
            CONF_OPTIMIZER_CONTROL_MODE, DEFAULT_OPTIMIZER_CONTROL_MODE
        )

        # Set runtime mode for sensor visibility
        data.optimizer_runtime_mode = control_mode

        try:
            # Step A: Build slots from raw data (Phase 2 SlotBuilder)
            ha_timezone = (
                str(dt_util.DEFAULT_TIME_ZONE)
                if dt_util.DEFAULT_TIME_ZONE
                else "Australia/Sydney"
            )
            slot_builder = SlotBuilder(
                config_options=config_options, ha_timezone=ha_timezone
            )
            slots, slot_metadata = slot_builder.build_slots(data, data.adaptive_params)

            if not slots:
                _LOGGER.warning("DP optimizer: no slots available, skipping")
                return

            # Step B: Build OptimizerConfig
            optimizer_config = _build_optimizer_config(data, config_options)

            # Normalize and validate SOC
            initial_soc, soc_info = _normalize_initial_soc(data.soc, optimizer_config)
            if initial_soc is None:
                _LOGGER.warning(
                    "DP optimizer: invalid SOC %s, skipping", soc_info.get("error")
                )
                return

            # Step B: Run DPPlanner
            cycle_id = uuid.uuid4().hex[:12]
            from .computation_engine_lib.optimizer_dp import (
                OptimizerInputs,  # noqa: PLC0415
            )

            inputs = OptimizerInputs(
                cycle_id=cycle_id,
                initial_soc_pct=initial_soc,
                slots=slots,
                config=optimizer_config,
            )
            result = self._dp_planner.plan(inputs)

            # Step C: Write optimizer fields (always, even on solve failure, for diagnostics)
            self._write_optimizer_fields(
                data, result, slot_metadata, config_options, cycle_id
            )

            # Steps D+E: Derive apply plan and assign active_mode (only if control is active)
            if control_mode == "active":
                self._assign_active_mode(data, result, optimizer_config)

        except Exception as e:
            _LOGGER.warning(
                "Inline DP optimizer failed (non-blocking): %s", e, exc_info=True
            )
            # active_mode falls through to legacy _compute_active_mode

    def _write_optimizer_fields(
        self,
        data: CoordinatorData,
        result: Any,  # OptimizerResult
        slot_metadata: Any,  # SlotBuildMetadata
        config_options: dict[str, Any],
        cycle_id: str,
    ) -> None:
        """Write DP result to CoordinatorData shadow fields (Step C)."""
        from homeassistant.util import dt as dt_util  # noqa: PLC0415

        data.optimizer_shadow_result = _serialize_result(result)
        data.optimizer_shadow_decisions = [
            _serialize_decision(d) for d in result.decisions
        ]
        data.optimizer_shadow_summary = _build_summary(
            result=result,
            cycle_id=cycle_id,
            cycle_timestamp_iso=dt_util.utcnow().isoformat(),
            parity_info=slot_metadata.to_parity_dict(),
            config_options=config_options,
        )

        # Derive solar_can_reach_target from DP result (Phase 4, #441)
        # Replaces the legacy _simulate_future_soc_with_solar_only() calls.
        data.solar_can_reach_target = result.can_solar_reach_target
        # solar_can_reach_target_in_dw: when allow_dw_entry_under_target is True,
        # terminal_penalty_idx is DW end — so can_solar_reach_target already reflects
        # the extended horizon. Mirror the value and set False when the switch is off
        # (same as the legacy path which set it to False when allow_dw_under_target=False).
        allow_dw_under_target = config_options.get("allow_dw_entry_under_target", False)
        data.solar_can_reach_target_in_dw = (
            result.can_solar_reach_target if allow_dw_under_target else False
        )

    def _assign_active_mode(
        self,
        data: CoordinatorData,
        result: Any,  # OptimizerResult
        optimizer_config: Any,  # OptimizerConfig
    ) -> None:
        """Assign data.active_mode from DP result within the same cycle (Steps D+E).

        On safety gate failure, defaults to SELF_CONSUMPTION — the safe hardware state.
        """
        # Build alignment info from the summary we just wrote
        alignment = {
            "valid": True,  # SlotBuilder produces aligned slots by construction (Phase 2)
            "issues": [],
            "warnings": [],
        }

        config_options = self._build_optimizer_config_options()
        safety_gate = OptimizerSafetyGate(config_options)
        gate_result = safety_gate.check_admission(data, result, alignment)

        if not gate_result.allowed:
            _LOGGER.info(
                "DP optimizer safety gate blocked: %s — defaulting to SELF_CONSUMPTION",
                gate_result.block_reason,
            )

            data.active_mode = _BatteryMode.SELF_CONSUMPTION
            data.optimizer_last_apply_status = "blocked"
            data.optimizer_safety_block_reason = gate_result.block_reason or ""
            data.optimizer_fallback_count = data.optimizer_fallback_count + 1
            return

        # Gate passed — derive apply plan from current slot
        current_slot_idx = _find_current_slot_index(data)
        apply_plan = _derive_runtime_apply_plan(
            data.optimizer_shadow_decisions, current_slot_idx, optimizer_config
        )
        data.optimizer_apply_plan = apply_plan

        if apply_plan.get("fallback_to_legacy", True):
            _LOGGER.info(
                "DP optimizer: apply plan requests fallback — defaulting to SELF_CONSUMPTION"
            )

            data.active_mode = _BatteryMode.SELF_CONSUMPTION
            data.optimizer_last_apply_status = "fallback"
            data.optimizer_fallback_count = data.optimizer_fallback_count + 1
            return

        # Map battery_mode string → BatteryMode enum
        battery_mode_str = apply_plan.get("battery_mode", "")
        try:
            data.active_mode = _BatteryMode(battery_mode_str)
            data.optimizer_last_apply_status = "ready_to_apply"
            data.optimizer_safety_block_reason = ""
            data.optimizer_fallback_count = 0
            _LOGGER.info(
                "DP optimizer (same-cycle): selected %s (action=%s, slot=%d)",
                battery_mode_str,
                apply_plan.get("action"),
                current_slot_idx,
            )
        except ValueError:
            _LOGGER.warning(
                "DP optimizer: invalid battery_mode '%s' — SELF_CONSUMPTION",
                battery_mode_str,
            )

            data.active_mode = _BatteryMode.SELF_CONSUMPTION
            data.optimizer_last_apply_status = "fallback"

    def _add_to_decision_log(
        self, data: CoordinatorData, now_dt: datetime, mode_change: bool
    ) -> None:
        """Add entry to decision log when mode changes or periodically (inlined from ModeDecisionEngine, Phase 4 #441)."""
        old_mode = self._previous_active_mode
        new_mode = data.active_mode
        old_display = old_mode.display_name if old_mode else "Unknown"
        new_display = new_mode.display_name if new_mode else "Unknown"
        reason = (
            f"Mode changed: {old_display} -> {new_display}"
            if mode_change
            else f"Status update: {new_display}"
        )
        entry = {
            "timestamp": now_dt.isoformat(),
            "old_mode": old_mode.value if old_mode else "unknown",
            "new_mode": new_mode.value if new_mode else "unknown",
            "old_mode_display": old_display,
            "new_mode_display": new_display,
            "buy_price": round(data.general_price, 2),
            "sell_price": round(data.feed_in_price, 2),
            "soc": round(data.soc),
            "effective_threshold": data.effective_cheap_price,
            "reason": reason,
        }
        data.decision_log.append(entry)
        if len(data.decision_log) > 50:
            data.decision_log = data.decision_log[-50:]
        self._previous_active_mode = new_mode
        self._last_decision_log_time = now_dt

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
            self._load_forecaster.set_weather_correlation(self._weather_correlation)
            _LOGGER.info("Weather correlation initialized successfully")
        except Exception as e:
            _LOGGER.error("Failed to initialize weather correlation: %s", e)
            self._weather_correlation = None
            self._load_forecaster.set_weather_correlation(None)

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

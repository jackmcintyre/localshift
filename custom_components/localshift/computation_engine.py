"""Computation engine for derived values and forecasts."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ALLOW_DW_ENTRY_UNDER_TARGET,
    CONF_BATTERY_TARGET,
    CONF_COMPARISON_MODE,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    CONF_EXPORT_PRICE_MARGIN,
    CONF_MINIMUM_TARGET_SOC,
    CONF_OPTIMIZATION_MODE,
    CONF_PRICING_DATA_SOURCE,
    CONF_SWITCHING_PENALTY,
    CONF_TARGET_PENALTY,
    CONF_WEATHER_LEARNING_ENABLED,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_DEADBAND,
    DEFAULT_COMPARISON_MODE,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_EXPORT_PRICE_MARGIN,
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    DEFAULT_MINIMUM_TARGET_SOC,
    DEFAULT_OPTIMIZATION_MODE,
    DEFAULT_PRICING_DATA_SOURCE,
    DEFAULT_SWITCHING_PENALTY,
    DEFAULT_TARGET_PENALTY,
    DEFAULT_WEATHER_LEARNING_ENABLED,
    SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET,
)
from .const import (
    BatteryMode as _BatteryMode,
)
from .coordinator import CoordinatorData
from .engine import (
    ExcessSolarSignalsEngine,
    WeatherDiagnosticsEngine,
    max_forecast_price,
    parse_forecast_dt,
    percentile,
    scan_forecast_for_spike,
)
from .engine.excess_solar import ExcessSolarEngine
from .engine.optimizer_dp import DPPlanner
from .engine.optimizer_facade import OptimizerFacade
from .engine.optimizer_runner import _find_current_slot_index
from .engine.price_signal_engine import PriceSignalEngine
from .engine.slot_schedule import TOTAL_SLOTS
from .engine.soc_simulator import SocSimulator
from .forecast import (
    AccuracyMetricsStore,
    ForecastAccuracyEngine,
    ForecastHistoryStore,
    ForecastPipeline,
    HistoryFetcher,
    LoadForecaster,
    sum_solar_before_target,
)
from .learning.correlation import WeatherCorrelation
from .pricing.types import ForecastSlot

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
        self._forecast_history_store = ForecastHistoryStore(hass)

        # Accuracy metrics storage (HA Storage) - persists accuracy across restarts (Issue #706)
        self._accuracy_metrics_store = AccuracyMetricsStore(hass)

        # History fetcher for historical load data (delegated to separate module)
        self._history_fetcher = HistoryFetcher(hass, entry)

        # Weather correlation for temperature-based consumption prediction
        self._weather_correlation: WeatherCorrelation | None = None

        # Create core engines for DP optimizer pipeline
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

        self._price_signals = PriceSignalEngine(
            entry=entry,
            get_switch_state=self._get_switch_state,
            parse_time_option=self._parse_time_option,
        )
        self._excess_solar_signals = ExcessSolarSignalsEngine(
            entry=entry,
            calculate_excess_by_windows=self._excess_solar_engine.calculate_excess_by_windows,
            find_nearest_negative_fit_window=self._excess_solar_engine.find_nearest_negative_fit_window,
            calculate_excess_until_negative_fit=self._excess_solar_engine.calculate_excess_until_negative_fit,
            find_battery_fill_point=self._soc_simulator.find_battery_fill_point,
            calculate_safe_additional_load=self._excess_solar_engine.calculate_safe_additional_load,
            compute_load_shift_signal=self._excess_solar_engine.compute_load_shift_signal,
            get_entity_id=self._get_entity_id,
            get_historical_hourly_averages=self._get_historical_hourly_averages,
            recent_load_1hr_getter=lambda: self._recent_load_1hr_kw,
            parse_time_option=self._parse_time_option,
        )
        self._forecast_accuracy = ForecastAccuracyEngine()
        self._weather_diagnostics = WeatherDiagnosticsEngine(entry)

        self._forecast_pipeline = ForecastPipeline(
            load_forecaster=self._load_forecaster,
            price_signals=self._price_signals,
            forecast_history_store=self._forecast_history_store,
            get_switch_state=self._get_switch_state,
            excess_solar_signals=self._excess_solar_signals,
        )

        # DP Planner for Phase 3 (#441) - eliminates one-cycle lag
        self._dp_planner = DPPlanner()

        self._optimizer_facade = OptimizerFacade(self._dp_planner)

        # Local cache properties (delegated to history_fetcher for storage)
        self._previous_active_mode = None
        self._last_decision_log_time: datetime | None = None

        # Baseline load profile for Issue #137 (set by coordinator)
        self._baseline_avg_kw: dict[int, float] = {}

    def set_solar_accuracy_tracker(self, tracker: Any) -> None:
        """Set the solar accuracy tracker for bias correction.

        Args:
            tracker: SolarAccuracyTracker instance

        """
        self._optimizer_facade.set_solar_accuracy_tracker(tracker)
        _LOGGER.info("Solar accuracy tracker connected to computation engine")

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

        # ---- Manual override check ----
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
        self._price_signals.compute_effective_cheap_price_preliminary(
            data=data,
            now_dt=now_dt,
            before_dw=before_dw,
            target_hour=target_hour,
            target_pct=target_pct,
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
        self._forecast_pipeline.compute_load_forecast_slots(
            data=data,
            now_dt=now_dt,
            historical_avg_kw=hourly_avg_kw,
            recent_load_kw=recent_load_kw,
            total_slots=TOTAL_SLOTS,
        )

        # ---- Bridge HistoryFetcher results to CoordinatorData (Issue #493) ----
        # These fields were never populated, causing diagnostic data to be stuck at defaults
        (
            data.weekday_hourly_profile_kw,
            data.weekday_sample_counts,
        ) = self._history_fetcher.get_weekday_profile()
        (
            data.weekend_hourly_profile_kw,
            data.weekend_sample_counts,
        ) = self._history_fetcher.get_weekend_profile()
        data.consumption_profile_type = self._history_fetcher.get_profile_source()

        # Bridge combined profile for backward compatibility
        data.consumption_hourly_profile_kw = dict(
            self._history_fetcher._historical_load_cache
        )
        data.consumption_hourly_sample_counts = dict(
            self._history_fetcher._historical_load_sample_counts
        )
        data.consumption_source = self._history_fetcher._historical_load_source
        data.consumption_profile_hours = len(
            self._history_fetcher._historical_load_cache
        )

        # Bridge recent load data
        data.recent_load_1hr_kw = self._history_fetcher._recent_load_1hr_kw
        data.recent_load_1hr_statistic_id = (
            self._history_fetcher._recent_load_1hr_statistic_id
        )
        data.recent_load_1hr_samples = self._history_fetcher._recent_load_1hr_samples
        data.recent_load_1hr_last_error = (
            self._history_fetcher._recent_load_1hr_last_error
        )

        # Determine forecast_profile_selected based on current day
        if data.consumption_profile_type == "weekday_weekend":
            data.forecast_profile_selected = (
                "weekend" if now_dt.weekday() >= 5 else "weekday"
            )
        else:
            data.forecast_profile_selected = data.consumption_profile_type

        # ---- Step DP: Inline DP optimizer (Phase 4, #441) ----
        # Runs before effective_cheap_price final update so solar_can_reach_target
        # is populated from DP result (not legacy solar-only simulation).
        config_options = self._build_optimizer_config_options()
        self._optimizer_facade.run_inline(
            data=data, now_dt=now_dt, config_options=config_options
        )

        # ---- Step 6: boost_charge_needed (Phase 4: derive from DP decision) ----
        # Read from current-slot DP decision (not forecast_computer).
        current_slot_idx = _find_current_slot_index(data)
        decisions = data.optimizer_decisions or []
        if decisions and 0 <= current_slot_idx < len(decisions):
            data.boost_charge_needed = decisions[current_slot_idx].get(
                "grid_charge_boost", False
            )
        else:
            data.boost_charge_needed = False

        # ---- Step 7: effective_cheap_price (final update) ----
        # Update effective_cheap_price with actual solar_can_reach_target from forecast
        # IMPORTANT: Save the optimizer's threshold BEFORE Step 7 overwrites it.
        # The optimizer ran with the preliminary threshold from Step 7a. Step 7 recomputes
        # effective_cheap_price using the optimizer's solar_can_reach_target result.
        # If the recomputed value differs from the preliminary, we track which was used
        # so the plan and UI are consistent (Planner Threshold Reconciliation, Fix #xxx).
        data.planner_threshold_used = data.effective_cheap_price
        self._price_signals.compute_effective_cheap_price(
            data=data,
            now_dt=now_dt,
            before_dw=before_dw,
            target_hour=target_hour,
        )

        # ---- Step 8: cheap_charge_stop_price ----
        # Hardcoded deadband (Issue #214)
        deadband = DEFAULT_CHEAP_PRICE_DEADBAND
        data.cheap_charge_stop_price = round(data.effective_cheap_price + deadband, 2)

        # ---- Step 4: solar_battery_forecast (legacy - for backwards compatibility) ----
        # Kept for API compatibility, but values derived from detailed forecast
        self._forecast_pipeline.compute_solar_battery_forecast(
            data=data,
            now_dt=now_dt,
            target_hour=target_hour,
            before_dw=before_dw,
            after_dw=after_dw,
            target_pct=target_pct,
        )

        # ---- Step 9: forecast_spike_within_window ----
        # Hardcoded lookahead (Issue #214)
        lookahead = DEFAULT_FORECAST_LOOKAHEAD_HOURS
        cutoff = now_dt + timedelta(hours=lookahead)

        # Issue #300: scan_forecast_for_spike no longer needs pricing_source
        data.forecast_spike_within_window = self._price_signals.scan_forecast_for_spike(
            data.feed_in_forecast, now_dt, cutoff
        )
        # max_forecast_price tracks the max SELL price (feed-in) for spike detection.
        data.max_forecast_price = self._price_signals.max_forecast_price(
            data.feed_in_forecast, now_dt, cutoff
        )
        # max_buy_forecast_price tracks the max BUY price for pre-charge decisions. (Fix #3)
        data.max_buy_forecast_price = self._price_signals.max_forecast_price(
            data.general_forecast, now_dt, cutoff
        )

        # ---- Step 10: forecast_expensive_period_coming ----
        data.forecast_expensive_period_coming = (
            self._price_signals.scan_forecast_for_spike(
                data.general_forecast, now_dt, cutoff
            )
        )

        # ---- Step 10b: spike analysis (conservative mode) ----
        self._price_signals.analyze_spike(data, now_dt)

        # ---- Step 11: solar_weighted_avg_fit ----
        self._forecast_pipeline.compute_solar_weighted_avg_fit(
            data=data, now_dt=now_dt, target_hour=target_hour, after_dw=after_dw
        )

        # ---- Step 12: decision_log ----
        # Phase 4 (#441): active_mode is set by the optimizer facade (Phase 3).
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
        self._forecast_pipeline.compute_excess_solar_signals(data, now_dt)

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
        self._forecast_pipeline.compute_solar_battery_forecast(
            data=data,
            now_dt=now_dt,
            target_hour=target_hour,
            before_dw=before_dw,
            after_dw=after_dw,
            target_pct=target_pct,
        )

    def _compute_load_forecast_slots(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        historical_avg_kw: dict[int, float],
        recent_load_kw: float,
    ) -> None:
        """Populate data.load_forecast_slots with per-slot kW estimates."""
        self._forecast_pipeline.compute_load_forecast_slots(
            data=data,
            now_dt=now_dt,
            historical_avg_kw=historical_avg_kw,
            recent_load_kw=recent_load_kw,
            total_slots=TOTAL_SLOTS,
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
        self._price_signals.compute_effective_cheap_price_preliminary(
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
        self._price_signals.compute_effective_cheap_price(
            data=data,
            now_dt=now_dt,
            before_dw=before_dw,
            target_hour=target_hour,
        )

    def _compute_solar_weighted_avg_fit(
        self, data: CoordinatorData, now_dt: datetime, target_hour: int, after_dw: bool
    ) -> None:
        """Compute solar-weighted average feed-in tariff."""
        self._forecast_pipeline.compute_solar_weighted_avg_fit(
            data=data,
            now_dt=now_dt,
            target_hour=target_hour,
            after_dw=after_dw,
        )

    def _get_dp_decision_at_demand_window(
        self, data: CoordinatorData, target_hour: int
    ) -> dict | None:
        """Get the DP decision at or just after the demand window start time."""
        return self._forecast_pipeline._get_dp_decision_at_demand_window(
            data, target_hour
        )

    def _get_expected_load_kw_from_slots(
        self, data: CoordinatorData, hours_to_target: float
    ) -> float:
        """Estimate average load kW until DW using data.load_forecast_slots (Phase 4, #441).

        Averages slots from current slot to DW entry slot. Falls back to current
        load if no forecast slots available.
        """
        return self._price_signals.get_expected_load_kw_from_slots(
            data, hours_to_target
        )

    # ========================================================================
    # PHASE 3 (#441): INLINE DP OPTIMIZER - ELIMINATE ONE-CYCLE LAG
    # ========================================================================

    def _build_optimizer_config_options(self) -> dict[str, Any]:
        """Build config_options dict for optimizer and safety gate.

        Phase 3 (#441): Consolidates config options construction.
        Phase 6 (#448): Removed optimizer_enabled and control_mode options.
        """
        return {
            CONF_MINIMUM_TARGET_SOC: self.entry.options.get(
                CONF_MINIMUM_TARGET_SOC, DEFAULT_MINIMUM_TARGET_SOC
            ),
            CONF_BATTERY_TARGET: self.entry.options.get(
                CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET
            ),
            CONF_ALLOW_DW_ENTRY_UNDER_TARGET: self._get_switch_state(
                SWITCH_ALLOW_DW_ENTRY_UNDER_TARGET
            ),
            CONF_OPTIMIZATION_MODE: self.entry.options.get(
                CONF_OPTIMIZATION_MODE, DEFAULT_OPTIMIZATION_MODE
            ),
            CONF_EXPORT_PRICE_MARGIN: self.entry.options.get(
                CONF_EXPORT_PRICE_MARGIN, DEFAULT_EXPORT_PRICE_MARGIN
            ),
            CONF_SWITCHING_PENALTY: self.entry.options.get(
                CONF_SWITCHING_PENALTY, DEFAULT_SWITCHING_PENALTY
            ),
            CONF_TARGET_PENALTY: self.entry.options.get(
                CONF_TARGET_PENALTY, DEFAULT_TARGET_PENALTY
            ),
            "pricing_source": self.entry.options.get(
                CONF_PRICING_DATA_SOURCE, DEFAULT_PRICING_DATA_SOURCE
            ),
            "comparison_mode": self.entry.options.get(
                CONF_COMPARISON_MODE, DEFAULT_COMPARISON_MODE
            ),
            # ha_timezone override: if present in entry.options (e.g. injected by tests
            # via config_overrides), use it to avoid relying on dt_util.DEFAULT_TIME_ZONE.
            "ha_timezone": self.entry.options.get("ha_timezone", None),
        }

    def _add_to_decision_log(
        self, data: CoordinatorData, now_dt: datetime, mode_change: bool
    ) -> None:
        """Add entry to decision log when mode changes or periodically."""
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
        forecasts: list[ForecastSlot],
        now_dt: datetime,
        cutoff: datetime,
    ) -> bool:
        """Return True if any forecast has spike_status == 'spike' in window (delegates to utils)."""
        return scan_forecast_for_spike(forecasts, now_dt, cutoff)

    @staticmethod
    def _max_forecast_price(
        forecasts: list[ForecastSlot],
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
        await self._forecast_history_store.async_initialize()

    async def async_load_forecast_history(self, data: CoordinatorData) -> None:
        """Load persisted forecast history from storage.

        Called during coordinator startup to restore predictions across restarts.
        """
        await self._forecast_history_store.async_load(data)

    async def async_save_forecast_history(self, data: CoordinatorData) -> None:
        """Persist forecast history to storage.

        Called after new predictions are stored.
        """
        await self._forecast_history_store.async_save(data)

    # ========================================================================
    # ACCURACY METRICS PERSISTENCE (Issue #706)
    # ========================================================================

    async def async_initialize_accuracy_metrics_storage(self) -> None:
        """Initialize accuracy metrics storage."""
        await self._accuracy_metrics_store.async_initialize()

    async def async_load_accuracy_metrics(self, data: CoordinatorData) -> None:
        """Load persisted accuracy metrics from storage.

        Called during coordinator startup to restore metrics across restarts.
        """
        await self._accuracy_metrics_store.async_load(data)

    async def async_save_accuracy_metrics(self, data: CoordinatorData) -> None:
        """Persist accuracy metrics to storage.

        Called after each slow-tick cycle.
        """
        await self._accuracy_metrics_store.async_save(data)

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
        self._price_signals.analyze_spike(data, now_dt)

    # ========================================================================
    # EXCESS SOLAR LOAD SHIFTING (backlog-high-017)
    # ========================================================================

    def _compute_excess_solar_signals(
        self,
        data: CoordinatorData,
        now_dt: datetime,
    ) -> None:
        """Compute excess solar load shifting signals."""
        self._forecast_pipeline.compute_excess_solar_signals(data, now_dt)

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

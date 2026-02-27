"""Forecast computer for battery SOC and grid interaction forecasting."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from ..const import (
    BATTERY_CAPACITY_KWH,
    BOOST_CHARGE_MAX_SOC,
    CHARGE_RATE_BOOST_KW,
    CHARGE_RATE_GRID_KW,
    CHARGE_RATE_SOLAR_KW,
    CONF_BATTERY_TARGET,
    CONF_CHEAP_PRICE_PERCENTILE,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    CONF_EXPORT_PRICE_MARGIN,
    CONF_MAX_PRECHARGE_PRICE,
    CONF_MINIMUM_TARGET_SOC,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_PERCENTILE,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_EXPORT_PRICE_MARGIN,
    DEFAULT_LOAD_WEIGHT_RECENT,
    DEFAULT_MAX_PRECHARGE_PRICE,
    DEFAULT_MINIMUM_TARGET_SOC,
)
from ..coordinator_data import AdaptiveParameters, CoordinatorData
from .excess_solar import ExcessSolarEngine
from .fit_analyzer import FitAnalyzer
from .grid_charge_decision import GridChargeDecisionEngine
from .price_calculator import get_price_for_slot
from .proactive_export import ProactiveExportEngine
from .soc_simulator import SocSimulator
from .solar_utils import (
    get_solar_for_15min_slot,
    get_solar_for_15min_slot_or_none,
    get_solar_for_slot_by_interval,
)
from .utils import get_slot_duration_minutes, parse_slot_time

_LOGGER = logging.getLogger(__name__)

# Forecast slot constants
# 15-min slots throughout for consistent alignment with Solcast 30-minute periods
TOTAL_SLOTS = 96  # 24 hours × 4 slots/hour

# Hybrid timescale constants (Issue #327)
# Maximum number of 5-minute slots to use from Amber near-term forecast
MAX_5MIN_FORECAST_HOURS = 1  # Amber typically provides ~45-60 min of 5-min data


def compute_hybrid_slot_schedule(
    now_local: datetime,
    general_forecast: list[dict],
    ha_timezone: str,
    max_forecast_hours: int = 24,
) -> tuple[list[dict], dict]:
    """Build hybrid slot schedule: ALL 5-min slots, then 30-min.

    Issue #327: Uses native data granularities without interpolation.
    - Amber provides 5-min near-term (~45-60 min), then 30-min extended forecast
    - This function identifies 5-min slots and switches to 30-min at boundary

    NO INTERPOLATION - use actual data only.
    NO GAPS - 5-min slots end at 30-min boundary, 30-min starts immediately.

    Args:
        now_local: Current datetime in HA local timezone
        general_forecast: List of Amber price forecast entries with start_time, end_time, duration
        ha_timezone: HA configured timezone (e.g., "Australia/Sydney")
        max_forecast_hours: Maximum hours to forecast (default 24)

    Returns:
        Tuple of (slots, metadata) where:
        - slots: List of slot dicts with:
            - start: datetime of slot start
            - interval_minutes: 5 or 30
            - price: price in $/kWh
            - price_source: "5min" or "30min"
        - metadata: Dict with:
            - timezone: HA timezone
            - slot_intervals: {"5min": count, "30min": count}
            - transition_boundary: Time when 5-min switches to 30-min (or None)
            - total_slots: Total number of slots
    """
    slots: list[dict] = []
    metadata: dict = {
        "timezone": ha_timezone,
        "slot_intervals": {"5min": 0, "30min": 0},
        "transition_boundary": None,
        "total_slots": 0,
    }

    if not general_forecast:
        _LOGGER.warning("compute_hybrid_slot_schedule: Empty general_forecast")
        return slots, metadata

    # Step 1: Parse all forecast entries and convert to local timezone
    all_slots_raw: list[dict] = []
    for entry in general_forecast:
        if not isinstance(entry, dict):
            continue

        start_time_str = entry.get("start_time")
        if not start_time_str:
            continue

        # Parse and convert to local timezone using our new utility
        slot_start = parse_slot_time(start_time_str, ha_timezone)
        if slot_start is None:
            continue

        # Skip past slots (only skip if strictly before now)
        if slot_start < now_local:
            continue

        # Determine duration from entry or calculate from gap to next
        duration_minutes = get_slot_duration_minutes(entry)
        if duration_minutes is None:
            # Try to calculate from end_time
            end_time_str = entry.get("end_time")
            if end_time_str:
                slot_end = parse_slot_time(end_time_str, ha_timezone)
                if slot_end:
                    duration_minutes = int((slot_end - slot_start).total_seconds() / 60)

        if duration_minutes is None:
            continue  # Skip if we can't determine duration

        # Accept 5-min, 30-min, or 60-min slots (60-min for backward compatibility with tests)
        # Amber native granularities are 5-min and 30-min, but tests may use 60-min
        if duration_minutes not in (5, 30, 60):
            continue

        price = float(entry.get("per_kwh", 0))

        all_slots_raw.append(
            {
                "start": slot_start,
                "interval_minutes": duration_minutes,
                "price": price,
                "price_source": "5min" if duration_minutes == 5 else "30min",
            }
        )

    if not all_slots_raw:
        _LOGGER.warning("compute_hybrid_slot_schedule: No valid slots after parsing")
        return slots, metadata

    # Step 2: Sort by start time
    all_slots_raw.sort(key=lambda x: x["start"])

    # Step 3: Separate 5-min and 30-min slots
    # Note: 60-min slots (used in tests) are treated as 30-min extended forecast
    five_min_slots = [s for s in all_slots_raw if s["interval_minutes"] == 5]
    thirty_min_slots = [s for s in all_slots_raw if s["interval_minutes"] in (30, 60)]

    _LOGGER.debug(
        "compute_hybrid_slot_schedule: Found %d 5-min slots, %d 30-min slots",
        len(five_min_slots),
        len(thirty_min_slots),
    )

    # Step 4: Add ALL 5-min slots (no minimum, no maximum)
    slots.extend(five_min_slots)

    # Step 5: Find first 30-min slot at or after last 5-min slot ends
    cutoff_time = now_local + timedelta(hours=max_forecast_hours)

    if five_min_slots:
        last_5min_end = five_min_slots[-1]["start"] + timedelta(minutes=5)

        # Find 30-min slot that starts at or after this time
        transition_boundary = None
        for slot in thirty_min_slots:
            if slot["start"] >= last_5min_end:
                # Found the transition point
                transition_boundary = slot["start"]
                # Add this and all subsequent 30-min slots within forecast window
                idx = thirty_min_slots.index(slot)
                for s in thirty_min_slots[idx:]:
                    if s["start"] < cutoff_time:
                        slots.append(s)
                break

        metadata["transition_boundary"] = (
            transition_boundary.strftime("%H:%M") if transition_boundary else None
        )
    else:
        # No 5-min data, use 30-min only
        for slot in thirty_min_slots:
            if slot["start"] < cutoff_time:
                slots.append(slot)
        if thirty_min_slots:
            metadata["transition_boundary"] = thirty_min_slots[0]["start"].strftime(
                "%H:%M"
            )

    # Step 6: Sort final slots by start time
    slots.sort(key=lambda x: x["start"])

    # Step 7: Calculate counts and metadata
    # Note: 60-min slots are counted as 30-min for backward compatibility
    five_min_count = len([s for s in slots if s["interval_minutes"] == 5])
    thirty_min_count = len([s for s in slots if s["interval_minutes"] in (30, 60)])

    metadata["slot_intervals"] = {
        "5min": five_min_count,
        "30min": thirty_min_count,
    }
    metadata["total_slots"] = len(slots)

    _LOGGER.info(
        "Hybrid slot schedule: %d 5-min slots, %d 30-min slots, transition at %s",
        five_min_count,
        thirty_min_count,
        metadata["transition_boundary"] or "N/A",
    )

    return slots, metadata


class ForecastComputer:
    """Computes 24-hour battery forecast with 15-minute breakdown."""

    def __init__(
        self,
        entry: ConfigEntry,
        get_entity_id_func: Callable[[str], str],
        get_historical_func: Callable[[str], dict[int, float]],
        get_profile_for_day_func: Callable[
            [datetime], tuple[dict[int, float], dict[int, int], str]
        ]
        | None = None,
        weather_correlation: Any | None = None,
        thermal_manager: Any | None = None,
    ) -> None:
        """Initialize forecast computer.

        Args:
            entry: Config entry
            get_entity_id_func: Function to get entity IDs by config key
            get_historical_func: Function to get historical hourly averages (combined profile)
            get_profile_for_day_func: Optional function to get day-aware profile (issue-60)
            weather_correlation: Optional WeatherCorrelation instance for temperature-based adjustments
            thermal_manager: Optional ThermalManager instance for HVAC-aware load forecasting (Issue #152)
        """
        self.entry = entry
        self._get_entity_id = get_entity_id_func
        self._get_historical_hourly_averages = get_historical_func
        self._get_profile_for_day = get_profile_for_day_func
        self._weather_correlation = weather_correlation
        self._thermal_manager = thermal_manager
        self._adaptive_params = (
            None  # Issue #170 Phase 2: Adaptive parameters from learning
        )

        # Extracted helper modules (issue #145)
        self._fit_analyzer = FitAnalyzer()
        self._soc_simulator = SocSimulator(self._estimate_hourly_consumption_kw)
        self._grid_charge_decision = GridChargeDecisionEngine(
            next_demand_window_start_dt=self._next_demand_window_start_dt,
            find_solar_start_time=self._find_solar_start_time,
            simulate_overnight_drain_to_solar=self._simulate_overnight_drain_to_solar,
            simulate_future_soc_with_solar_only=self._simulate_future_soc_with_solar_only,
        )
        self._excess_solar = ExcessSolarEngine(
            entry=self.entry,
            estimate_hourly_consumption_kw=self._estimate_hourly_consumption_kw,
            simulate_with_additional_load=self._simulate_with_additional_load,
        )
        self._proactive_export = ProactiveExportEngine(
            calculate_solar_energy_between_slots=self._calculate_solar_energy_between_slots,
            calculate_solar_energy_until_solar_start=self._calculate_solar_energy_until_solar_start,
            calculate_max_fit_price=self._calculate_max_fit_price,
            simulate_overnight_drain_after_export=self._simulate_overnight_drain_after_export,
        )

    def set_weather_correlation(self, weather_correlation: Any | None) -> None:
        """Set or clear WeatherCorrelation dependency at runtime."""
        self._weather_correlation = weather_correlation

    def set_thermal_manager(self, thermal_manager: Any | None) -> None:
        """Set or clear ThermalManager dependency at runtime.

        Args:
            thermal_manager: ThermalManager instance for HVAC-aware load forecasting,
                           or None to disable HVAC prediction.
        """
        self._thermal_manager = thermal_manager

    def set_adaptive_params(self, adaptive_params: AdaptiveParameters | None) -> None:
        """Set adaptive parameters from the learning system (Issue #170 Phase 2).

        These parameters are tuned by the ParameterOptimizer based on measured
        outcomes from decision tracking. They adjust key thresholds and biases
        to improve system performance over time.

        Args:
            adaptive_params: AdaptiveParameters instance with tuned values,
                           or None to use defaults.
        """
        self._adaptive_params = adaptive_params
        # Propagate to sub-engines
        self._grid_charge_decision.set_adaptive_params(adaptive_params)
        self._proactive_export.set_adaptive_params(adaptive_params)

    def _predict_hvac_load_for_slot(
        self,
        slot_hour: int,
        temperature: float | None,
        daily_thermal_mode: str | None,
    ) -> float:
        """Predict HVAC load for a given slot.

        Uses the thermal manager's learned HVAC power data and the daily
        thermal mode to predict how much HVAC load to expect.

        Args:
            slot_hour: Hour of day (0-23)
            temperature: Forecasted temperature in °C, or None
            daily_thermal_mode: Current daily thermal mode ("off", "cool", "heat", "dry")

        Returns:
            Predicted HVAC load in kW, or 0.0 if unavailable.
        """
        if self._thermal_manager is None:
            return 0.0

        if daily_thermal_mode is None or daily_thermal_mode == "off":
            return 0.0

        # Import ThermalMode for comparison
        try:
            from ..const import ThermalMode

            mode = ThermalMode(daily_thermal_mode)
        except (ValueError, ImportError):
            return 0.0

        # Use temperature if available, otherwise use a default estimate
        temp = temperature if temperature is not None else 25.0

        try:
            hvac_kw = self._thermal_manager.predict_hvac_load(
                hour=slot_hour,
                temperature=temp,
                daily_mode=mode,
            )
            return max(0.0, hvac_kw)
        except Exception as err:
            _LOGGER.debug(
                "HVAC prediction failed for hour %d: %s",
                slot_hour,
                err,
            )
            return 0.0

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

    def _estimate_hourly_consumption_kw(
        self,
        hourly_avg_kw: dict[int, float],
        slot_hour: int,
        current_hour: int | None,
        current_load_kw: float,
        recent_load_kw: float = 0.0,
        temperature: float | None = None,
    ) -> tuple[float, str]:
        """Estimate hourly household consumption with time-distance-weighted blend.

        Blends recent 1-hour average with historical hourly average ONLY for
        hours close to current time, when recent load is predictive.

        For distant hours (e.g., overnight when forecasting from midday),
        uses historical profile only to avoid overestimating load.

        When temperature is provided and weather correlation is available with
        sufficient confidence, applies temperature-based adjustments for heating/cooling.

        Returns tuple of (kW, source_tag).
        """
        # Get the weighting configuration (hardcoded default - Issue #214)
        recent_weight = DEFAULT_LOAD_WEIGHT_RECENT
        historical_weight = 1.0 - recent_weight

        historical_raw = hourly_avg_kw.get(slot_hour) if hourly_avg_kw else None
        historical_kw = (
            float(historical_raw) if isinstance(historical_raw, int | float) else 0.0
        )

        # Check if we have valid historical data
        has_historical = historical_kw > 0

        # Calculate base load using existing logic
        base_load_kw = 0.0
        base_source = ""

        # TIME-DISTANCE WEIGHTING: Only blend recent load for hours close to now
        # When current_hour is None (simulations without time context), skip blending
        if current_hour is not None:
            # Calculate distance from current hour (handles midnight wrap)
            hour_distance = abs(slot_hour - current_hour)
            hour_distance = min(hour_distance, 24 - hour_distance)

            # Only apply weighted blend for hours within 3 hours of current time
            # Beyond that, recent load is NOT predictive (e.g., daytime load ≠ overnight load)
            max_blend_distance = 3

            if (
                hour_distance <= max_blend_distance
                and recent_load_kw > 0
                and recent_weight > 0
                and has_historical
            ):
                base_load_kw = (recent_weight * recent_load_kw) + (
                    historical_weight * historical_kw
                )
                base_source = "weighted_load"

        # Fallback to historical if available (primary path for distant hours)
        if not base_source and has_historical:
            base_load_kw = historical_kw
            base_source = "profile_hour"

        # Fallback to current load
        if not base_source:
            base_load_kw = current_load_kw if current_load_kw > 0 else 0.6
            base_source = "live_load_fallback"

        # WEATHER CORRELATION: Apply temperature-based adjustment if available
        # Only apply when:
        # 1. Weather correlation is initialized
        # 2. Temperature is provided
        # 3. We have base load to adjust
        # 4. Confidence is medium or high (not low)
        adjusted_load_kw = base_load_kw
        adjusted_source = base_source

        if (
            self._weather_correlation is not None
            and temperature is not None
            and base_load_kw > 0
        ):
            # Get coefficients for this hour
            coef = self._weather_correlation.get_coefficients_for_hour(slot_hour)
            if coef is not None and coef.confidence in ("medium", "high"):
                # Apply weather-based prediction
                weather_adjusted, adjustment_source = (
                    self._weather_correlation.predict_load(
                        hour=slot_hour,
                        temperature=temperature,
                        base_load_kw=base_load_kw,
                    )
                )
                # Only use adjustment if it's not a fallback
                if adjustment_source not in (
                    "no_coefficients",
                    "low_confidence",
                    "invalid_hour",
                ):
                    adjusted_load_kw = weather_adjusted
                    adjusted_source = adjustment_source

        # Issue #170 Phase 2: Apply consumption_forecast_bias adaptive parameter
        # Positive = assume higher consumption (more conservative for grid charging)
        # Negative = assume lower consumption (more optimistic for grid charging)
        if self._adaptive_params is not None:
            consumption_bias = self._adaptive_params.get(
                "consumption_forecast_bias", 0.0
            )
            if consumption_bias != 0.0:
                adjusted_load_kw = max(0.0, adjusted_load_kw + consumption_bias)
                _LOGGER.debug(
                    "CONSUMPTION_BIAS: hour=%d, base=%.2f kW, bias=%.2f kW, final=%.2f kW",
                    slot_hour,
                    base_load_kw,
                    consumption_bias,
                    adjusted_load_kw,
                )

        return round(adjusted_load_kw, 3), adjusted_source

    def _simulate_future_soc_with_solar_only(
        self,
        actual_current_soc: float,
        start_slot: datetime,
        target_pct: float,
        all_solcast: list[dict],
        historical_avg_kw: dict[int, float],
        current_load_kw: float,
        recent_load_kw: float,
        dw_start_time: time,
        end_time: datetime,
        min_soc_pct: float = 0.0,
        current_hour: int | None = None,
        baseline_avg_kw: dict[int, float] | None = None,
        dw_end_time: time | None = None,
    ) -> tuple[float, float, bool, bool]:
        """Delegate _simulate_future_soc_with_solar_only to SocSimulator.

        Single source of truth is now in SocSimulator._simulate_future_soc_with_solar_only.
        This wrapper maintains backward compatibility with all callers.

        Args:
            actual_current_soc: ACTUAL current battery SOC (from real-time data)
            start_slot: Starting slot time
            target_pct: Target SOC percentage
            all_solcast: Full Solcast forecast (today + tomorrow)
            historical_avg_kw: Historical hourly load profile (fallback)
            current_load_kw: Current load power
            recent_load_kw: Recent 1-hour average load
            dw_start_time: Demand window start time
            end_time: End time (exclusive) to simulate until
            min_soc_pct: Minimum SOC floor
            current_hour: Current hour for load estimation
            baseline_avg_kw: Optional baseline (non-HVAC) load profile for #137
            dw_end_time: Demand window end time (for DW-period max_soc tracking)

        Returns:
            (soc_at_end_pct, max_soc_pct, can_reach_target, was_truncated)
        """
        return self._soc_simulator._simulate_future_soc_with_solar_only(
            actual_current_soc=actual_current_soc,
            start_slot=start_slot,
            target_pct=target_pct,
            all_solcast=all_solcast,
            historical_avg_kw=historical_avg_kw,
            current_load_kw=current_load_kw,
            recent_load_kw=recent_load_kw,
            dw_start_time=dw_start_time,
            end_time=end_time,
            min_soc_pct=min_soc_pct,
            current_hour=current_hour,
            baseline_avg_kw=baseline_avg_kw,
            dw_end_time=dw_end_time,
        )

    def _next_demand_window_start_dt(
        self,
        slot_start: datetime,
        dw_start_time: time,
    ) -> datetime:
        """Get the next demand-window start datetime relative to slot_start."""
        candidate = slot_start.replace(
            hour=dw_start_time.hour,
            minute=dw_start_time.minute,
            second=dw_start_time.second,
            microsecond=0,
        )
        if candidate <= slot_start:
            candidate += timedelta(days=1)
        return candidate

    def _find_solar_start_time(
        self,
        start_slot: datetime,
        all_solcast: list[dict],
        max_hours: int = 12,
    ) -> datetime | None:
        """Find when solar production starts (first slot with >0.1 kWh).

        Args:
            start_slot: Starting slot time
            all_solcast: Full Solcast forecast
            max_hours: Maximum hours to search ahead

        Returns:
            Datetime of first slot with meaningful solar, or None if not found
        """
        base_slot = start_slot.replace(second=0, microsecond=0)

        for offset in range(max_hours * 4):  # 4 slots per hour
            check_slot = base_slot + timedelta(minutes=15 * offset)
            solar_kwh = get_solar_for_15min_slot(all_solcast, check_slot)
            if solar_kwh > 0.1:  # Meaningful solar production
                return check_slot

        return None

    def _simulate_overnight_drain_to_solar(
        self,
        start_soc: float,
        start_slot: datetime,
        solar_start: datetime,
        all_solcast: list[dict],
        historical_avg_kw: dict[int, float],
        current_load_kw: float,
        recent_load_kw: float,
        min_soc_pct: float = 0.0,
    ) -> float:
        """Delegate _simulate_overnight_drain_to_solar to extracted helper module."""
        return self._soc_simulator._simulate_overnight_drain_to_solar(
            start_soc=start_soc,
            start_slot=start_slot,
            solar_start=solar_start,
            all_solcast=all_solcast,
            historical_avg_kw=historical_avg_kw,
            current_load_kw=current_load_kw,
            recent_load_kw=recent_load_kw,
            min_soc_pct=min_soc_pct,
        )

    def _find_negative_fit_windows(
        self, feed_in_forecast: list[dict], start_time: datetime, max_hours: int = 24
    ) -> list[tuple[datetime, datetime, float]]:
        """Delegate _find_negative_fit_windows to extracted helper module."""
        return self._fit_analyzer._find_negative_fit_windows(
            feed_in_forecast=feed_in_forecast,
            start_time=start_time,
            max_hours=max_hours,
        )

    def _calculate_local_effective_cheap_price(
        self,
        slot_start: datetime,
        general_forecast: list[dict],
        target_pct: float,
        current_soc: float,
        dw_start_time: time,
        base_cheap_price: float,
        max_price: float,
    ) -> float:
        """Delegate _calculate_local_effective_cheap_price to extracted helper module."""
        return self._grid_charge_decision._calculate_local_effective_cheap_price(
            slot_start=slot_start,
            general_forecast=general_forecast,
            target_pct=target_pct,
            current_soc=current_soc,
            dw_start_time=dw_start_time,
            base_cheap_price=base_cheap_price,
            max_price=max_price,
        )

    def _should_grid_charge_at_slot(
        self,
        slot_start: datetime,
        solar_kwh: float,
        slot_price: float,
        predicted_soc: float,
        target_pct: float,
        effective_cheap_price: float,
        is_before_dw: bool,
        in_demand_window: bool,
        gap_to_target: float,
        is_daylight: bool,
        all_solcast: list[dict],
        historical_avg_kw: dict[int, float],
        current_load_kw: float,
        recent_load_kw: float,
        dw_start_time: time,
        dw_end_time: time,
        allow_dw_entry_under_target: bool,
        general_price_current: float,
        min_soc_pct: float = 0.0,
        current_hour: int | None = None,
        is_current_slot: bool = False,
        is_currently_grid_charging: bool = False,
        baseline_avg_kw: dict[int, float] | None = None,
    ) -> tuple[bool, bool]:
        """Determine if grid charging should happen at this slot.

        Smart grid charging with very cheap price as safety net.
        Uses forecast simulation to avoid unnecessary grid charging.

        Strategy:
        1. PREFER SPOT: Use current spot price ONLY for current slot (real-time decision)
        2. For future slots, use forecast price
        3. Only charge when price is cheap (<= effective_cheap_price)
        4. Fall back to forecast-based logic when spot is unavailable
        5. When allow_dw_entry_under_target is True, simulate to DW END instead of DW START
           (allows solar to charge during DW period)
        6. HYSTERESIS: Once grid charging starts, require stronger evidence to stop
           (solar must reach target + margin, not just target)

        Issue #137: When baseline_avg_kw is provided, use it instead of historical_avg_kw
        for grid charging decisions. This prevents the feedback loop where HVAC spikes
        trigger unnecessary grid charging.

        Args:
            slot_start: Start time of the 15-minute slot
            solar_kwh: Solar forecast for this slot
            slot_price: Buy price for this slot (from forecast)
            predicted_soc: Predicted SOC at start of slot
            target_pct: Target SOC percentage
            effective_cheap_price: Cheap price threshold
            is_before_dw: True if before demand window
            in_demand_window: True if in demand window
            gap_to_target: How many percent to target
            is_daylight: True if solar_kwh > 0.05
            all_solcast: Full Solcast forecast
            historical_avg_kw: Historical hourly load profile
            current_load_kw: Current load power
            recent_load_kw: Recent 1-hour average load
            dw_start_time: Demand window start time
            dw_end_time: Demand window end time
            allow_dw_entry_under_target: If True, solar can charge during DW
            general_price_current: Current spot buy price (only for current slot)
            is_current_slot: True if this is the current time slot (use spot price)
            is_currently_grid_charging: True if currently in grid charging mode (hysteresis)
            baseline_avg_kw: Optional baseline (non-HVAC) load profile for Issue #137

        Returns:
            (should_charge, should_boost)
        """
        # Basic constraints
        if in_demand_window:
            return False, False

        if not is_before_dw:
            return False, False

        # Grid charging decisions are independent of daylight/solar.
        # The daylight check has been removed to allow overnight grid charging
        # when prices are cheap and solar alone cannot reach the target. (Fix #1)

        if gap_to_target <= 0:
            return False, False

        # SPOT PRICE: Only use for current slot (real-time decision)
        # For future slots, always use forecast price
        if is_current_slot and general_price_current > 0:
            use_price = general_price_current
            _LOGGER.debug(
                "GRID_CHARGE: Using spot price $%.2f for current slot (forecast: $%.2f)",
                general_price_current,
                slot_price,
            )
        else:
            # Future slot or spot unavailable - use forecast price
            use_price = slot_price

        # Price-based thresholds
        price_is_cheap = use_price <= effective_cheap_price
        price_is_very_cheap = use_price <= (effective_cheap_price * 0.8)

        # SMART FORECAST: Simulate forward with solar only
        # Model: can we reach target using solar only?
        # If yes, do NOT grid charge.
        #
        # KEY: When allow_dw_entry_under_target is True, simulate to DW END
        # instead of DW START. This allows solar to continue charging during
        # the DW period and reach target within the DW window.
        #
        # ISSUE #292: Solar simulation MUST run BEFORE hysteresis check.
        # Previously, hysteresis would bypass solar simulation entirely,
        # causing unnecessary grid charging when solar could reach target.
        sim_start = slot_start

        if allow_dw_entry_under_target:
            # Simulate through entire DW period to DW end
            # This allows solar to charge during DW hours
            sim_end = slot_start.replace(
                hour=dw_end_time.hour,
                minute=dw_end_time.minute,
                second=0,
                microsecond=0,
            )
            # If DW end is earlier than now, it's tomorrow
            if sim_end <= slot_start:
                sim_end += timedelta(days=1)
            _LOGGER.debug(
                "GRID_CHARGE: Simulating to DW END %s (allow_dw_entry_under_target=True)",
                sim_end.strftime("%H:%M"),
            )
        else:
            # Standard behavior: simulate to next DW start
            sim_end = self._next_demand_window_start_dt(slot_start, dw_start_time)

        # OVERNIGHT EFFICIENCY CHECK:
        # For overnight slots (no solar), we need to check differently.
        # Grid charging overnight at $0.15/kWh when tomorrow's solar is "free" is
        # economically wrong. Only grid charge overnight if solar truly can't reach target.
        is_overnight_slot = solar_kwh < 0.01  # No meaningful solar

        if is_overnight_slot:
            # Find when solar production starts
            solar_start = self._find_solar_start_time(slot_start, all_solcast)

            if solar_start is not None:
                # Simulate overnight drain to get SOC at solar start
                soc_at_solar_start = self._simulate_overnight_drain_to_solar(
                    start_soc=predicted_soc,
                    start_slot=slot_start,
                    solar_start=solar_start,
                    all_solcast=all_solcast,
                    historical_avg_kw=historical_avg_kw,
                    current_load_kw=current_load_kw,
                    recent_load_kw=recent_load_kw,
                    min_soc_pct=min_soc_pct,
                )

                # DEBUG: Log simulation parameters for troubleshooting
                _LOGGER.info(
                    "OVERNIGHT_SIM[%02d:%02d]: predicted_soc=%.1f%%, solar_start=%s, soc_at_solar_start=%.1f%%, sim_end=%s",
                    slot_start.hour,
                    slot_start.minute,
                    predicted_soc,
                    solar_start.strftime("%H:%M"),
                    soc_at_solar_start,
                    sim_end.strftime("%H:%M"),
                )

                # Now simulate from solar start to next DW
                # ISSUE #137: Pass baseline for grid charging decisions
                # FIX: Pass dw_end_time for proper DW-period max_soc tracking
                soc_at_end, max_soc, can_reach_with_solar_only, was_truncated = (
                    self._simulate_future_soc_with_solar_only(
                        actual_current_soc=soc_at_solar_start,
                        start_slot=solar_start,  # Start from solar, not from now
                        target_pct=target_pct,
                        all_solcast=all_solcast,
                        historical_avg_kw=historical_avg_kw,
                        current_load_kw=current_load_kw,
                        recent_load_kw=recent_load_kw,
                        dw_start_time=dw_start_time,
                        end_time=sim_end,
                        min_soc_pct=min_soc_pct,
                        baseline_avg_kw=baseline_avg_kw,
                        dw_end_time=dw_end_time,
                    )
                )

                # FIX: If simulation was truncated by Solcast horizon, we can't
                # reliably determine if solar can reach target. Skip grid charging
                # unless price is very cheap (safety net).
                if was_truncated:
                    _LOGGER.info(
                        "OVERNIGHT_SKIP_TRUNCATED[%02d:%02d]: simulation truncated by Solcast horizon, skipping grid charge decision",
                        slot_start.hour,
                        slot_start.minute,
                    )
                    if price_is_very_cheap:
                        if predicted_soc >= BOOST_CHARGE_MAX_SOC:
                            return True, False
                        return True, True
                    return False, False

                # Solar from dawn can reach target: NO overnight grid charging
                if can_reach_with_solar_only:
                    _LOGGER.debug(
                        "OVERNIGHT_SKIP[%02d:%02d]: solar from %s reaches target (SOC %.1f%% -> max %.1f%%)",
                        slot_start.hour,
                        slot_start.minute,
                        solar_start.strftime("%H:%M"),
                        soc_at_solar_start,
                        max_soc,
                    )
                    return False, False
                else:
                    # Log when overnight grid charging is being considered
                    _LOGGER.info(
                        "OVERNIGHT_GRID_CHARGE[%02d:%02d]: solar cannot reach target (max_soc=%.1f%% < %d%%), price=$%.3f",
                        slot_start.hour,
                        slot_start.minute,
                        max_soc,
                        target_pct,
                        use_price,
                    )
            else:
                # No solar found in forecast lookahead window.
                # This happens for slots near the end of the Solcast forecast horizon
                # (e.g., evening slots on "tomorrow" when Solcast only has today + tomorrow).
                # These slots are far enough away that we shouldn't make grid charging decisions.
                # Skip grid charging - don't simulate when we lack the data.
                _LOGGER.debug(
                    "OVERNIGHT_CHECK: %02d:%02d - no solar forecast found within lookahead window, skipping grid charge decision (slot beyond reliable simulation horizon)",
                    slot_start.hour,
                    slot_start.minute,
                )
                # Only grid charge if price is very cheap (safety net for extreme cases)
                # Issue #309: Skip boost if SOC >= 80%
                if price_is_very_cheap:
                    if predicted_soc >= BOOST_CHARGE_MAX_SOC:
                        return True, False
                    return True, True
                return False, False
        else:
            # Daylight slot - use original simulation logic
            # ISSUE #137: Pass baseline for grid charging decisions
            # FIX: Pass dw_end_time for proper DW-period max_soc tracking
            soc_at_end, max_soc, can_reach_with_solar_only, was_truncated = (
                self._simulate_future_soc_with_solar_only(
                    actual_current_soc=predicted_soc,
                    start_slot=sim_start,
                    target_pct=target_pct,
                    all_solcast=all_solcast,
                    historical_avg_kw=historical_avg_kw,
                    current_load_kw=current_load_kw,
                    recent_load_kw=recent_load_kw,
                    dw_start_time=dw_start_time,
                    end_time=sim_end,
                    min_soc_pct=min_soc_pct,
                    baseline_avg_kw=baseline_avg_kw,
                    dw_end_time=dw_end_time,
                )
            )

            # FIX: If simulation was truncated by Solcast horizon, we can't
            # reliably determine if solar can reach target. Skip grid charging
            # unless price is very cheap (safety net).
            if was_truncated:
                _LOGGER.info(
                    "DAYLIGHT_SKIP_TRUNCATED[%02d:%02d]: simulation truncated by Solcast horizon, skipping grid charge decision",
                    slot_start.hour,
                    slot_start.minute,
                )
                if price_is_very_cheap:
                    if predicted_soc >= BOOST_CHARGE_MAX_SOC:
                        return True, False
                    return True, True
                return False, False

            # Solar forecast says we'll reach target: NO grid charging
            if can_reach_with_solar_only:
                _LOGGER.debug(
                    "SOLAR_SKIP[%02d:%02d]: max_soc=%.1f%% >= %d%%",
                    slot_start.hour,
                    slot_start.minute,
                    max_soc,
                    target_pct,
                )
                return False, False

        # HYSTERESIS: If currently grid charging and solar can't reach target,
        # apply stickiness to prevent flip-flopping. Only applies to current slot.
        # This is now AFTER solar simulation, so we know solar truly can't reach target.
        if (
            is_current_slot
            and is_currently_grid_charging
            and price_is_cheap
            and gap_to_target > 0
        ):
            # Continue charging - solar simulation confirmed we need it
            _LOGGER.info(
                "GRID_CHARGE HYSTERESIS: Continuing at %s (price=$%.2f, SOC=%.1f%%, target=%d%%) - solar cannot reach target",
                slot_start.strftime("%H:%M"),
                use_price,
                predicted_soc,
                target_pct,
            )
            # Check if we should boost (very cheap price)
            # Issue #309: Skip boost if SOC >= 80% (Powerwall throttles charge rate)
            if price_is_very_cheap:
                if predicted_soc >= BOOST_CHARGE_MAX_SOC:
                    return True, False  # Continue at normal rate
                return True, True
            return True, False

        # SAFETY NET: Charge if very cheap (forecast could be wrong)
        # IMPORTANT: only applies when solar *cannot* meet the target before DW.
        # Issue #309: Skip boost charging if SOC >= 80% (Powerwall throttles charge rate)
        if price_is_very_cheap:
            if predicted_soc >= BOOST_CHARGE_MAX_SOC:
                _LOGGER.info(
                    "Grid charge: VERY CHEAP price $%.2f at %s (safety net) - SOC %.1f%% >= 80%%, using normal rate instead of boost",
                    slot_price,
                    slot_start.strftime("%H:%M"),
                    predicted_soc,
                )
                return True, False  # Grid charge at normal rate, not boost
            _LOGGER.info(
                "Grid charge: VERY CHEAP price $%.2f at %s (safety net)",
                slot_price,
                slot_start.strftime("%H:%M"),
            )
            return True, True

        # Solar not enough: Charge at cheap prices
        if price_is_cheap:
            _LOGGER.info(
                "Grid charge: CHEAP price $%.2f at %s (gap to target: %.1f%%)",
                slot_price,
                slot_start.strftime("%H:%M"),
                gap_to_target,
            )
            return True, False

        # Not cheap, no urgent need: Wait
        return False, False

    def _calculate_average_fit_price(
        self, feed_in_forecast: list[dict], start_time: datetime, hours: int = 24
    ) -> float:
        """Delegate _calculate_average_fit_price to extracted helper module."""
        return self._fit_analyzer._calculate_average_fit_price(
            feed_in_forecast=feed_in_forecast,
            start_time=start_time,
            hours=hours,
        )

    def _calculate_percentile_fit_price(
        self,
        feed_in_forecast: list[dict],
        start_time: datetime,
        percentile: float = 60.0,
        hours: int = 24,
    ) -> float:
        """Delegate _calculate_percentile_fit_price to extracted helper module."""
        return self._fit_analyzer._calculate_percentile_fit_price(
            feed_in_forecast=feed_in_forecast,
            start_time=start_time,
            percentile=percentile,
            hours=hours,
        )

    def _calculate_max_fit_price(
        self, feed_in_forecast: list[dict], start_time: datetime, hours: int = 24
    ) -> float:
        """Delegate _calculate_max_fit_price to extracted helper module."""
        return self._fit_analyzer._calculate_max_fit_price(
            feed_in_forecast=feed_in_forecast,
            start_time=start_time,
            hours=hours,
        )

    def _simulate_minimum_soc_without_exports(
        self,
        start_soc: float,
        start_slot: datetime,
        all_solcast: list[dict],
        historical_avg_kw: dict[int, float],
        current_load_kw: float,
        recent_load_kw: float,
        dw_start_time: time,
        dw_end_time: time,
        max_hours: int = 24,
    ) -> tuple[float, float]:
        """Delegate _simulate_minimum_soc_without_exports to extracted helper module."""
        return self._soc_simulator._simulate_minimum_soc_without_exports(
            start_soc=start_soc,
            start_slot=start_slot,
            all_solcast=all_solcast,
            historical_avg_kw=historical_avg_kw,
            current_load_kw=current_load_kw,
            recent_load_kw=recent_load_kw,
            dw_start_time=dw_start_time,
            dw_end_time=dw_end_time,
            max_hours=max_hours,
        )

    def _simulate_overnight_drain_after_export(
        self,
        start_soc: float,
        start_slot: datetime,
        all_solcast: list[dict],
        historical_avg_kw: dict[int, float],
        current_load_kw: float,
        recent_load_kw: float,
        export_min_soc_pct: float,
    ) -> tuple[float, float, bool]:
        """Delegate _simulate_overnight_drain_after_export to extracted helper module."""
        return self._soc_simulator._simulate_overnight_drain_after_export(
            start_soc=start_soc,
            start_slot=start_slot,
            all_solcast=all_solcast,
            historical_avg_kw=historical_avg_kw,
            current_load_kw=current_load_kw,
            recent_load_kw=recent_load_kw,
            export_min_soc_pct=export_min_soc_pct,
        )

    def _find_battery_fill_point(
        self,
        start_soc: float,
        start_slot: datetime,
        all_solcast: list[dict],
        historical_avg_kw: dict[int, float],
        current_load_kw: float,
        recent_load_kw: float,
        current_hour: int | None = None,
        hybrid_slots: list[dict] | None = None,
    ) -> int | None:
        """Find elapsed minutes when battery first reaches 100% from solar charging.

        Issue #329: Supports hybrid timescale with variable slot durations.
        Falls back to 15-min slots when hybrid_slots is not provided.

        Args:
            start_soc: Starting SOC percentage
            start_slot: Starting slot time
            all_solcast: Full Solcast forecast
            historical_avg_kw: Historical hourly load profile
            current_load_kw: Current load power
            recent_load_kw: Recent 1-hour average load
            current_hour: Current hour for load estimation
            hybrid_slots: Optional list of hybrid slots with variable durations.
                         Each slot has 'start' (datetime) and 'interval_minutes' (int).

        Returns:
            Elapsed minutes until 100% SOC, or None if it never fills
        """
        soc = start_soc
        base_slot = start_slot.replace(second=0, microsecond=0)
        elapsed_minutes = 0

        if hybrid_slots:
            # Hybrid mode: use variable slot durations
            for slot in hybrid_slots:
                slot_start = slot["start"]
                interval_minutes = slot["interval_minutes"]
                slot_fraction = interval_minutes / 60.0
                slot_hour = slot_start.hour

                # Use variable-duration solar function
                solar_kwh = get_solar_for_slot_by_interval(
                    all_solcast, slot_start, interval_minutes
                )
                load_kw, _ = self._estimate_hourly_consumption_kw(
                    historical_avg_kw,
                    slot_hour,
                    current_hour,
                    current_load_kw,
                    recent_load_kw,
                )
                # Scale consumption to slot duration
                consumption_kwh = load_kw * slot_fraction
                net_kwh = solar_kwh - consumption_kwh

                # Apply battery charging (no grid charging, no exports)
                # Use solar charge rate (5kW) as max
                max_slot_transfer_kwh = CHARGE_RATE_SOLAR_KW * slot_fraction
                if net_kwh >= 0:
                    delta = min(net_kwh, max_slot_transfer_kwh) * 0.92
                else:
                    delta = max(net_kwh, -max_slot_transfer_kwh) / 0.95

                soc += delta / BATTERY_CAPACITY_KWH * 100
                soc = min(100.0, soc)  # Cap at 100%

                if soc >= 100.0:
                    return elapsed_minutes

                elapsed_minutes += interval_minutes

            return None  # Never fills within hybrid slot horizon
        else:
            # Legacy mode: fixed 15-min slots
            slot_fraction = 15 / 60.0  # 0.25 hours

            for i in range(TOTAL_SLOTS):
                slot_start = base_slot + timedelta(minutes=15 * i)
                slot_hour = slot_start.hour

                solar_kwh = get_solar_for_15min_slot(all_solcast, slot_start)
                load_kw, _ = self._estimate_hourly_consumption_kw(
                    historical_avg_kw,
                    slot_hour,
                    current_hour,
                    current_load_kw,
                    recent_load_kw,
                )
                consumption_kwh = load_kw * slot_fraction
                net_kwh = solar_kwh - consumption_kwh

                max_slot_transfer_kwh = CHARGE_RATE_SOLAR_KW * slot_fraction
                if net_kwh >= 0:
                    delta = min(net_kwh, max_slot_transfer_kwh) * 0.92
                else:
                    delta = max(net_kwh, -max_slot_transfer_kwh) / 0.95

                soc += delta / BATTERY_CAPACITY_KWH * 100
                soc = min(100.0, soc)

                if soc >= 100.0:
                    return elapsed_minutes

                elapsed_minutes += 15

            return None  # Never fills

    def _calculate_solar_energy_between_slots(
        self,
        start_elapsed_minutes: float,
        end_elapsed_minutes: float,
        base_slot: datetime,
        all_solcast: list[dict],
        historical_avg_kw: dict[int, float],
        current_load_kw: float,
        recent_load_kw: float,
        current_hour: int | None = None,
        hybrid_slots: list[dict] | None = None,
    ) -> float:
        """Calculate net solar energy (solar - load) between two time points.

        Issue #329: Supports hybrid timescale with variable slot durations.
        Falls back to 15-min slots when hybrid_slots is not provided.

        Args:
            start_elapsed_minutes: Starting time in minutes from base_slot
            end_elapsed_minutes: Ending time in minutes from base_slot
            base_slot: Base datetime for offset calculation
            all_solcast: Full Solcast forecast
            historical_avg_kw: Historical hourly load profile
            current_load_kw: Current load power
            recent_load_kw: Recent 1-hour average load
            current_hour: Current hour for load estimation
            hybrid_slots: Optional list of hybrid slots with variable durations.
                         Each slot has 'start' (datetime) and 'interval_minutes' (int).

        Returns:
            Net solar energy in kWh (positive = excess)
        """
        net_energy = 0.0

        if hybrid_slots:
            # Hybrid mode: use variable slot durations
            elapsed_minutes = 0

            for slot in hybrid_slots:
                slot_start = slot["start"]
                interval_minutes = slot["interval_minutes"]
                slot_fraction = interval_minutes / 60.0

                # Check if this slot is within the time range
                if elapsed_minutes >= end_elapsed_minutes:
                    break

                if elapsed_minutes + interval_minutes <= start_elapsed_minutes:
                    # Skip slots before start time
                    elapsed_minutes += interval_minutes
                    continue

                slot_hour = slot_start.hour

                # Use variable-duration solar function
                solar_kwh = get_solar_for_slot_by_interval(
                    all_solcast, slot_start, interval_minutes
                )
                load_kw, _ = self._estimate_hourly_consumption_kw(
                    historical_avg_kw,
                    slot_hour,
                    current_hour,
                    current_load_kw,
                    recent_load_kw,
                )
                consumption_kwh = load_kw * slot_fraction
                net_kwh = solar_kwh - consumption_kwh

                if net_kwh > 0:
                    # Apply charging efficiency for excess
                    net_energy += net_kwh * 0.92

                elapsed_minutes += interval_minutes
        else:
            # Legacy mode: fixed 15-min slots
            slot_fraction = 15 / 60.0  # 0.25 hours

            # Calculate start and end slot indices
            start_slot_idx = max(0, int(start_elapsed_minutes // 15))
            end_slot_idx = int(end_elapsed_minutes // 15) + 1

            # Iterate through 15-min slots
            for i in range(start_slot_idx, end_slot_idx):
                slot_start = base_slot + timedelta(minutes=15 * i)
                slot_hour = slot_start.hour

                solar_kwh = get_solar_for_15min_slot(all_solcast, slot_start)
                load_kw, _ = self._estimate_hourly_consumption_kw(
                    historical_avg_kw,
                    slot_hour,
                    current_hour,
                    current_load_kw,
                    recent_load_kw,
                )
                consumption_kwh = load_kw * slot_fraction
                net_kwh = solar_kwh - consumption_kwh

                if net_kwh > 0:
                    # Apply charging efficiency for excess
                    net_energy += net_kwh * 0.92

        return net_energy

    def _calculate_excess_by_windows(
        self,
        base_slot: datetime,
        all_solcast: list[dict],
        historical_avg_kw: dict[int, float],
        current_load_kw: float,
        recent_load_kw: float,
        current_soc: float,
        target_pct: float,
        current_hour: int | None = None,
    ) -> dict[str, float]:
        """Delegate _calculate_excess_by_windows to extracted helper module."""
        return self._excess_solar._calculate_excess_by_windows(
            base_slot=base_slot,
            all_solcast=all_solcast,
            historical_avg_kw=historical_avg_kw,
            current_load_kw=current_load_kw,
            recent_load_kw=recent_load_kw,
            current_soc=current_soc,
            target_pct=target_pct,
            current_hour=current_hour,
        )

    def _find_nearest_negative_fit_window(
        self,
        feed_in_forecast: list[dict],
        start_time: datetime,
        max_hours: int = 24,
    ) -> tuple[datetime | None, int]:
        """Delegate _find_nearest_negative_fit_window to extracted helper module."""
        return self._excess_solar._find_nearest_negative_fit_window(
            feed_in_forecast=feed_in_forecast,
            start_time=start_time,
            max_hours=max_hours,
        )

    def _calculate_excess_until_negative_fit(
        self,
        base_slot: datetime,
        negative_fit_start: datetime | None,
        all_solcast: list[dict],
        historical_avg_kw: dict[int, float],
        current_load_kw: float,
        recent_load_kw: float,
        current_soc: float,
        target_pct: float,
        current_hour: int | None = None,
    ) -> float:
        """Delegate _calculate_excess_until_negative_fit to extracted helper module."""
        return self._excess_solar._calculate_excess_until_negative_fit(
            base_slot=base_slot,
            negative_fit_start=negative_fit_start,
            all_solcast=all_solcast,
            historical_avg_kw=historical_avg_kw,
            current_load_kw=current_load_kw,
            recent_load_kw=recent_load_kw,
            current_soc=current_soc,
            target_pct=target_pct,
            current_hour=current_hour,
        )

    def _calculate_safe_additional_load(
        self,
        base_slot: datetime,
        all_solcast: list[dict],
        historical_avg_kw: dict[int, float],
        current_load_kw: float,
        recent_load_kw: float,
        current_soc: float,
        target_pct: float,
        dw_start_time: time,
        effective_cheap_price: float,
        general_forecast: list[dict],
        min_soc_pct: float = 0.0,
        current_hour: int | None = None,
    ) -> tuple[float, bool]:
        """Delegate _calculate_safe_additional_load to extracted helper module."""
        return self._excess_solar._calculate_safe_additional_load(
            base_slot=base_slot,
            all_solcast=all_solcast,
            historical_avg_kw=historical_avg_kw,
            current_load_kw=current_load_kw,
            recent_load_kw=recent_load_kw,
            current_soc=current_soc,
            target_pct=target_pct,
            dw_start_time=dw_start_time,
            effective_cheap_price=effective_cheap_price,
            general_forecast=general_forecast,
            min_soc_pct=min_soc_pct,
            current_hour=current_hour,
        )

    def _simulate_with_additional_load(
        self,
        base_slot: datetime,
        all_solcast: list[dict],
        historical_avg_kw: dict[int, float],
        current_load_kw: float,
        recent_load_kw: float,
        current_soc: float,
        target_pct: float,
        dw_start_time: time,
        effective_cheap_price: float,
        general_forecast: list[dict],
        additional_load_kw: float,
        min_soc_pct: float = 0.0,
        current_hour: int | None = None,
    ) -> bool:
        """Delegate _simulate_with_additional_load to extracted helper module."""
        return self._soc_simulator._simulate_with_additional_load(
            base_slot=base_slot,
            all_solcast=all_solcast,
            historical_avg_kw=historical_avg_kw,
            current_load_kw=current_load_kw,
            recent_load_kw=recent_load_kw,
            current_soc=current_soc,
            target_pct=target_pct,
            dw_start_time=dw_start_time,
            effective_cheap_price=effective_cheap_price,
            general_forecast=general_forecast,
            additional_load_kw=additional_load_kw,
            min_soc_pct=min_soc_pct,
            current_hour=current_hour,
        )

    def _compute_load_shift_signal(
        self,
        data: CoordinatorData,
        excess_by_windows: dict[str, float],
        negative_fit_start: datetime | None,
        safe_additional_load: float,
        grid_charge_risk: bool,
        fill_point_minutes: int | None,
    ) -> tuple[str, float, int, str, str]:
        """Delegate _compute_load_shift_signal to extracted helper module."""
        return self._excess_solar._compute_load_shift_signal(
            data=data,
            excess_by_windows=excess_by_windows,
            negative_fit_start=negative_fit_start,
            safe_additional_load=safe_additional_load,
            grid_charge_risk=grid_charge_risk,
            fill_point_minutes=fill_point_minutes,
        )

    def _calculate_solar_energy_until_solar_start(
        self,
        start_slot: datetime,
        all_solcast: list[dict],
        historical_avg_kw: dict[int, float],
        current_load_kw: float,
        recent_load_kw: float,
        max_hours: int = 12,
    ) -> float:
        """Calculate net solar energy (solar - load) until solar production starts.

        This determines how much "free" energy will be available to replace
        exported battery energy before grid charging would be needed.

        Args:
            start_slot: Starting slot time
            all_solcast: Full Solcast forecast
            historical_avg_kw: Historical hourly load profile
            current_load_kw: Current load power
            recent_load_kw: Recent 1-hour average load
            max_hours: Maximum hours to search ahead

        Returns:
            Net solar energy in kWh (positive = excess available)
        """
        # Find when solar production starts
        solar_start = self._find_solar_start_time(start_slot, all_solcast, max_hours)
        if solar_start is None:
            return 0.0

        net_energy = 0.0
        base_slot = start_slot.replace(second=0, microsecond=0)
        slot_fraction = 15 / 60.0  # 0.25 hours

        slot_time = base_slot
        while slot_time < solar_start:
            slot_hour = slot_time.hour

            # Get solar (should be ~0 overnight) and load
            solar_kwh = get_solar_for_15min_slot(all_solcast, slot_time)

            load_kw, _ = self._estimate_hourly_consumption_kw(
                historical_avg_kw,
                slot_hour,
                None,  # current_hour - not available in this simulation
                current_load_kw,
                recent_load_kw,
            )
            consumption_kwh = load_kw * slot_fraction
            net_kwh = solar_kwh - consumption_kwh

            # Only accumulate if positive (excess solar)
            if net_kwh > 0:
                net_energy += net_kwh * 0.92  # Charging efficiency

            slot_time += timedelta(minutes=15)

        return net_energy

    def _calculate_expected_replacement_price(
        self,
        slot_start: datetime,
        solar_energy_available: float,
        export_amount_kwh: float,
        general_forecast: list[dict],
        effective_cheap_price: float,
    ) -> float:
        """Delegate _calculate_expected_replacement_price to extracted helper module."""
        return self._proactive_export._calculate_expected_replacement_price(
            slot_start=slot_start,
            solar_energy_available=solar_energy_available,
            export_amount_kwh=export_amount_kwh,
            general_forecast=general_forecast,
            effective_cheap_price=effective_cheap_price,
        )

    def _should_proactive_export_at_slot(
        self,
        slot_start: datetime,
        slot_hour: int,
        solar_kwh: float,
        slot_fit_price: float,
        predicted_soc: float,
        target_pct: float,
        in_demand_window: bool,
        forecasted_excess_kwh: float,
        remaining_export_budget_kwh: float,
        feed_in_forecast: list[dict],
        min_soc_no_exports: float,
        export_min_soc_pct: float,
        effective_cheap_price: float,
        feed_in_price_current: float,
        export_price_margin: float = DEFAULT_EXPORT_PRICE_MARGIN,
        all_solcast: list[dict] | None = None,
        historical_avg_kw: dict[int, float] | None = None,
        current_load_kw: float = 0.0,
        recent_load_kw: float = 0.0,
        general_forecast: list[dict] | None = None,
        is_current_slot: bool = False,
        current_elapsed_minutes: float = 0,
        fill_point_elapsed_minutes: int | None = None,
    ) -> tuple[bool, float]:
        """Delegate _should_proactive_export_at_slot to extracted helper module."""
        return self._proactive_export._should_proactive_export_at_slot(
            slot_start=slot_start,
            slot_hour=slot_hour,
            solar_kwh=solar_kwh,
            slot_fit_price=slot_fit_price,
            predicted_soc=predicted_soc,
            target_pct=target_pct,
            in_demand_window=in_demand_window,
            forecasted_excess_kwh=forecasted_excess_kwh,
            remaining_export_budget_kwh=remaining_export_budget_kwh,
            feed_in_forecast=feed_in_forecast,
            min_soc_no_exports=min_soc_no_exports,
            export_min_soc_pct=export_min_soc_pct,
            effective_cheap_price=effective_cheap_price,
            feed_in_price_current=feed_in_price_current,
            export_price_margin=export_price_margin,
            all_solcast=all_solcast,
            historical_avg_kw=historical_avg_kw,
            current_load_kw=current_load_kw,
            recent_load_kw=recent_load_kw,
            general_forecast=general_forecast,
            is_current_slot=is_current_slot,
            current_elapsed_minutes=current_elapsed_minutes,
            fill_point_elapsed_minutes=fill_point_elapsed_minutes,
        )

    def compute_forecast(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        historical_avg_kw: dict[int, float],
        recent_load_kw: float,
        historical_load_source: str,
        historical_load_sample_counts: dict[int, int],
        baseline_avg_kw: dict[int, float] | None = None,
    ) -> tuple[list[dict], list[list], dict[str, int]]:
        """Compute full 24-hour forecast with 15-minute breakdown.

        Provides 4x granularity over hourly forecast, capturing meaningful
        price variations from 5-minute pricing data.

        Issue #137: When baseline_avg_kw is provided, use it for grid charging
        decisions instead of historical_avg_kw. This prevents the feedback loop
        where HVAC spikes trigger unnecessary grid charging.

        Issue #324: Price-optimized grid charging - schedules cheapest slots first.

        Args:
            data: CoordinatorData with current state
            now_dt: Current datetime
            historical_avg_kw: Historical hourly load profile (includes HVAC spikes)
            recent_load_kw: Recent 1-hour average load
            historical_load_source: Source of historical data
            historical_load_sample_counts: Sample counts per hour
            baseline_avg_kw: Optional baseline (non-HVAC) load profile for Issue #137

        Returns:
            tuple of (daily_forecast, daily_forecast_soc_15min, consumption_source_counts)
        """
        daily_forecast = []
        daily_forecast_soc_15min = []
        consumption_source_counts = {}

        # Get all Solcast forecasts
        all_solcast = [*data.solcast_today, *data.solcast_tomorrow]

        # Weather temperature forecasts (hourly), keyed by local date/hour for fast lookup.
        temperature_by_hour: dict[tuple[int, int, int, int], float] = {}
        if self._weather_correlation is not None:
            try:
                for forecast in self._weather_correlation.get_temperature_forecast():
                    if forecast.temperature is None:
                        continue
                    temp = float(forecast.temperature)
                    slot_local = dt_util.as_local(forecast.slot_time)
                    key = (
                        slot_local.year,
                        slot_local.month,
                        slot_local.day,
                        slot_local.hour,
                    )
                    # Keep first forecast for each hour.
                    if key not in temperature_by_hour:
                        temperature_by_hour[key] = temp
            except Exception as err:
                _LOGGER.debug(
                    "Failed to read temperature forecast for load adjustment: %s", err
                )

        # Reset and re-populate during this forecast cycle.
        data.weather_adjustment_applied = False

        # Publish consumption profile diagnostics for transparency
        data.consumption_source = (
            historical_load_source if historical_avg_kw else "live_load_fallback"
        )
        data.consumption_statistic_id = self._get_entity_id("teslemetry_load_power")
        data.consumption_profile_hours = len(historical_avg_kw)
        data.consumption_fallback_hours = 0
        data.consumption_hourly_sample_counts = dict(historical_load_sample_counts)
        data.consumption_hourly_profile_kw = {
            hour: round(val, 3) for hour, val in sorted(historical_avg_kw.items())
        }

        # Get recent 1-hour load for weighted forecasting
        data.recent_load_1hr_kw = recent_load_kw
        # Hardcoded weighting (Issue #214)
        data.consumption_weighting = DEFAULT_LOAD_WEIGHT_RECENT

        if not all_solcast:
            _LOGGER.warning(
                "15-min forecast: no Solcast entries available - solar forecast data is missing. "
                "Check Solcast integration status."
            )
        if not historical_avg_kw:
            _LOGGER.debug(
                "15-min forecast: no historical hourly load profile available; using live load fallback"
            )

        # Track missing solar data for diagnostics
        missing_solar_slots: list[str] = []

        current_soc = data.soc
        predicted_soc = current_soc

        # Round DOWN to the current 5-minute boundary so there is always a slot that
        # covers "right now" in the forecast.  This eliminates the rounding mismatch
        # between forecast generation and the lookup in _get_forecast_entry_for_now()
        # (Issue 3 in MODE_SWITCHING_DELAY_ANALYSIS.md).
        current_5min = (now_dt.minute // 5) * 5
        base_slot = now_dt.replace(minute=current_5min, second=0, microsecond=0)

        # Current hour for time-distance weighted load estimation
        # (only blend recent load for hours close to current hour)
        current_hour = base_slot.hour

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

        # ========================================================================
        # ISSUE #329: HYBRID TIMESCALE FORECAST
        #
        # Uses compute_hybrid_slot_schedule() to get dynamic slots from Amber:
        # - 5-min slots for near-term (where Amber has actual 5-min data)
        # - 30-min slots for extended forecast
        #
        # NO INTERPOLATION - uses actual data only.
        # NO GAPS - 5-min slots end at 30-min boundary, 30-min starts immediately.
        # ========================================================================

        # Get HA timezone for hybrid slot schedule
        # Use dt_util.DEFAULT_TIME_ZONE which returns the configured HA timezone
        ha_timezone = (
            str(dt_util.DEFAULT_TIME_ZONE)
            if dt_util.DEFAULT_TIME_ZONE
            else "Australia/Sydney"
        )

        # Compute hybrid slot schedule from Amber general forecast
        hybrid_slots, hybrid_metadata = compute_hybrid_slot_schedule(
            now_local=dt_util.as_local(now_dt),
            general_forecast=data.general_forecast,
            ha_timezone=str(ha_timezone),
            max_forecast_hours=24,
        )

        # Store hybrid metadata in CoordinatorData for diagnostics
        data.hybrid_slot_metadata = hybrid_metadata

        _LOGGER.info(
            "Hybrid forecast: %d slots (%d 5-min, %d 30-min), transition at %s",
            hybrid_metadata.get("total_slots", 0),
            hybrid_metadata.get("slot_intervals", {}).get("5min", 0),
            hybrid_metadata.get("slot_intervals", {}).get("30min", 0),
            hybrid_metadata.get("transition_boundary", "N/A"),
        )

        # ========================================================================
        # CALCULATE FORECASTED EXCESS AND MINIMUM SOC FOR PROACTIVE EXPORT
        # ========================================================================
        # Sum all solar - consumption for full 24-hour forecast
        # This tells us if we'll have excess energy that should be exported
        # before feed-in prices go negative
        forecasted_excess_kwh = 0.0
        current_kwh = current_soc / 100 * BATTERY_CAPACITY_KWH
        space_to_target_kwh = max(target_kwh - current_kwh, 0)

        # Calculate excess for full 24-hour window using hybrid slots
        # This includes both today's remaining hours and tomorrow's solar production
        for slot in hybrid_slots:
            slot_start = slot["start"]
            interval_minutes = slot["interval_minutes"]
            slot_fraction = interval_minutes / 60.0
            slot_hour = slot_start.hour
            slot_temp = temperature_by_hour.get(
                (slot_start.year, slot_start.month, slot_start.day, slot_start.hour)
            )

            solar_kwh = get_solar_for_slot_by_interval(
                all_solcast, slot_start, interval_minutes
            )
            load_kw, _ = self._estimate_hourly_consumption_kw(
                historical_avg_kw,
                slot_hour,
                current_hour,
                data.load_power_kw,
                recent_load_kw,
                slot_temp,
            )
            consumption_kwh = load_kw * slot_fraction
            net_kwh = solar_kwh - consumption_kwh

            # Accumulate excess (positive net) beyond what we need for target
            if net_kwh > 0:
                if space_to_target_kwh > 0:
                    # First fill target gap
                    used_for_target = min(net_kwh, space_to_target_kwh)
                    space_to_target_kwh -= used_for_target
                    forecasted_excess_kwh += max(0, net_kwh - used_for_target)
                else:
                    # Target met, all excess is exportable
                    forecasted_excess_kwh += net_kwh

        # Export budget: total excess minus 10% buffer
        export_budget_kwh = max(0, forecasted_excess_kwh * 0.90)

        # Calculate minimum SOC without any proactive exports
        # This helps us determine safe export limits
        min_soc_no_exports, final_soc_no_exports = (
            self._simulate_minimum_soc_without_exports(
                start_soc=current_soc,
                start_slot=base_slot,
                all_solcast=all_solcast,
                historical_avg_kw=historical_avg_kw,
                current_load_kw=data.load_power_kw,
                recent_load_kw=recent_load_kw,
                dw_start_time=dw_start_time,
                dw_end_time=dw_end_time,
                max_hours=24,
            )
        )

        _LOGGER.info(
            "Forecasted excess: %.2f kWh, export budget: %.2f kWh (full 24h forecast)",
            forecasted_excess_kwh,
            export_budget_kwh,
        )
        _LOGGER.info(
            "Minimum SOC without exports: %.1f%%, final SOC: %.1f%%",
            min_soc_no_exports,
            final_soc_no_exports,
        )

        # FILL-POINT: Find when battery will first reach 100% from solar
        fill_point_elapsed_minutes = self._find_battery_fill_point(
            start_soc=current_soc,
            start_slot=base_slot,
            all_solcast=all_solcast,
            historical_avg_kw=historical_avg_kw,
            current_load_kw=data.load_power_kw,
            recent_load_kw=recent_load_kw,
        )
        if fill_point_elapsed_minutes is not None:
            fill_time = base_slot + timedelta(minutes=fill_point_elapsed_minutes)
            _LOGGER.info(
                "Battery will fill in %d minutes (%s) from solar charging",
                fill_point_elapsed_minutes,
                fill_time.strftime("%H:%M"),
            )
        else:
            _LOGGER.info("Battery will not reach 100% from solar in next 24 hours")

        # Read minimum SOC once before the loop (used for SOC floor and grid charging simulation)
        export_min_soc_pct = float(
            self.entry.options.get(CONF_MINIMUM_TARGET_SOC, DEFAULT_MINIMUM_TARGET_SOC)
        )
        remaining_export_budget = export_budget_kwh
        slot_fraction = 15 / 60.0  # 0.25 hours

        # Issue #283: Maximum grid import budget to prevent overcharging
        max_grid_import_kwh = max(
            0, (target_pct - current_soc) / 100 * BATTERY_CAPACITY_KWH * 1.05
        )  # 5% buffer

        # ========================================================================
        # ISSUE #324: Price-optimized grid charging
        # Two-pass algorithm to schedule cheapest slots first:
        # Pass 1: Collect all candidate slots that need grid charging
        # Pass 2: Sort by price and mark which slots should charge
        # ========================================================================

        # Pass 1: Collect candidates for grid charging
        # We process chronologically to get correct SOC simulation,
        # but defer the actual scheduling decision.
        grid_charge_candidates: list[dict] = []

        # Track SOC for candidate evaluation (solar-only simulation)
        candidate_soc = current_soc

        for slot_idx in range(TOTAL_SLOTS):
            slot_start = base_slot + timedelta(minutes=15 * slot_idx)
            is_first_slot = slot_idx == 0

            slot_hour = slot_start.hour
            slot_minute = slot_start.minute
            slot_time = slot_start.time()

            # SOC at the start of this slot
            soc_at_slot_start = current_soc if is_first_slot else candidate_soc

            # Check if we're in demand window
            in_demand_window = dw_start_time <= slot_time < dw_end_time

            # Get solar forecast
            solar_kwh_or_none = get_solar_for_15min_slot_or_none(
                all_solcast, slot_start
            )
            if solar_kwh_or_none is None:
                missing_solar_slots.append(slot_start.strftime("%H:%M"))
                solar_kwh = 0.0
            else:
                solar_kwh = solar_kwh_or_none

            # Get expected consumption
            slot_temp = temperature_by_hour.get(
                (slot_start.year, slot_start.month, slot_start.day, slot_start.hour)
            )
            baseline_kw, load_source = self._estimate_hourly_consumption_kw(
                historical_avg_kw,
                slot_hour,
                current_hour,
                data.load_power_kw,
                recent_load_kw,
                slot_temp,
            )
            if load_source.startswith("weather_"):
                data.weather_adjustment_applied = True
            if load_source != "profile_hour":
                data.consumption_fallback_hours += 1
            consumption_source_counts[load_source] = (
                consumption_source_counts.get(load_source, 0) + 1
            )

            # Issue #152: Add HVAC prediction
            daily_thermal_mode = getattr(data, "daily_thermal_mode", None)
            if isinstance(daily_thermal_mode, str):
                pass
            elif daily_thermal_mode is not None:
                daily_thermal_mode = str(daily_thermal_mode)

            hvac_kw = self._predict_hvac_load_for_slot(
                slot_hour=slot_hour,
                temperature=slot_temp,
                daily_thermal_mode=daily_thermal_mode,
            )

            total_load_kw = baseline_kw + hvac_kw

            if hvac_kw > 0.1:
                load_source = f"{load_source}+hvac"

            consumption_kwh = total_load_kw * slot_fraction
            net_kwh = solar_kwh - consumption_kwh

            # Get slot price
            _slot_price = get_price_for_slot(data.general_forecast, slot_start)

            # Determine if we should grid charge
            gap_to_target = max(target_pct - soc_at_slot_start, 0)

            next_dw_start = self._next_demand_window_start_dt(slot_start, dw_start_time)
            is_before_dw = slot_start < next_dw_start
            is_daylight = solar_kwh > 0.05

            max_solar_charge_kwh = CHARGE_RATE_SOLAR_KW * slot_fraction
            max_grid_charge_kwh = CHARGE_RATE_GRID_KW * slot_fraction

            allow_dw_entry_under_target = getattr(
                data, "allow_dw_entry_under_target", False
            )

            is_currently_grid_charging = getattr(data, "force_charge_active", False)

            # Calculate local effective cheap price
            max_price = float(
                self.entry.options.get(
                    CONF_MAX_PRECHARGE_PRICE, DEFAULT_MAX_PRECHARGE_PRICE
                )
            )
            cheap_price_percentile = float(
                self.entry.options.get(
                    CONF_CHEAP_PRICE_PERCENTILE, DEFAULT_CHEAP_PRICE_PERCENTILE
                )
            )

            forecast_prices = []
            for f in data.general_forecast:
                if not isinstance(f, dict):
                    continue
                start_str = f.get("start_time")
                if not start_str:
                    continue
                try:
                    f_start = datetime.fromisoformat(start_str)
                    f_local = dt_util.as_local(f_start)
                except ValueError:
                    continue
                if f_local >= slot_start:
                    forecast_prices.append(float(f.get("per_kwh", 0)))
            if forecast_prices:
                forecast_prices.sort()
                idx = int(len(forecast_prices) * cheap_price_percentile / 100)
                idx = min(idx, len(forecast_prices) - 1)
                base_cheap_price = round(forecast_prices[idx], 2)
            else:
                base_cheap_price = data.effective_cheap_price

            local_effective_cheap_price = self._calculate_local_effective_cheap_price(
                slot_start=slot_start,
                general_forecast=data.general_forecast,
                target_pct=target_pct,
                current_soc=soc_at_slot_start,
                dw_start_time=dw_start_time,
                base_cheap_price=base_cheap_price,
                max_price=max_price,
            )

            should_grid_charge, should_boost = self._should_grid_charge_at_slot(
                slot_start=slot_start,
                solar_kwh=solar_kwh,
                slot_price=_slot_price,
                predicted_soc=soc_at_slot_start,
                target_pct=target_pct,
                effective_cheap_price=local_effective_cheap_price,
                is_before_dw=is_before_dw,
                in_demand_window=in_demand_window,
                gap_to_target=gap_to_target,
                is_daylight=is_daylight,
                all_solcast=all_solcast,
                historical_avg_kw=historical_avg_kw,
                current_load_kw=data.load_power_kw,
                recent_load_kw=recent_load_kw,
                dw_start_time=dw_start_time,
                dw_end_time=dw_end_time,
                allow_dw_entry_under_target=allow_dw_entry_under_target,
                general_price_current=data.general_price,
                min_soc_pct=export_min_soc_pct,
                is_current_slot=is_first_slot,
                is_currently_grid_charging=is_currently_grid_charging,
                baseline_avg_kw=baseline_avg_kw,
            )

            # If this slot should grid charge, add to candidates
            if should_grid_charge:
                # Calculate charge amount
                if should_boost:
                    charge_rate = CHARGE_RATE_BOOST_KW
                else:
                    charge_rate = CHARGE_RATE_GRID_KW

                max_charge_kwh = charge_rate * slot_fraction
                current_battery_kwh = soc_at_slot_start / 100 * BATTERY_CAPACITY_KWH
                space_remaining_kwh = max(target_kwh - current_battery_kwh, 0)
                grid_charge_amount = min(max_charge_kwh * 0.92, space_remaining_kwh)

                grid_charge_candidates.append(
                    {
                        "slot_idx": slot_idx,
                        "slot_start": slot_start,
                        "price": _slot_price,
                        "should_boost": should_boost,
                        "charge_amount_kwh": grid_charge_amount,
                        "soc_at_slot_start": soc_at_slot_start,
                    }
                )

            # Update candidate SOC (solar-only simulation for next slot)
            if net_kwh >= 0:
                battery_delta_kwh = min(net_kwh, max_solar_charge_kwh) * 0.92
            else:
                battery_delta_kwh = max(net_kwh, -max_solar_charge_kwh) / 0.95

            if is_first_slot:
                new_candidate_soc = current_soc
            else:
                new_candidate_soc = candidate_soc + (
                    battery_delta_kwh / BATTERY_CAPACITY_KWH * 100
                )

            # Apply minimum SOC floor
            if not is_first_slot and new_candidate_soc < export_min_soc_pct:
                new_candidate_soc = export_min_soc_pct

            candidate_soc = max(0.0, min(100.0, new_candidate_soc))

        # ========================================================================
        # Pass 2: Sort candidates by price and schedule cheapest first
        # Issue #344: When prices are equal, prefer LATER slots to maximize
        # solar contribution during earlier hours (just-in-time charging).
        # ========================================================================

        # Sort by (price ASC, slot_idx DESC) - cheapest first, but later slots
        # preferred when prices are equal. This allows solar to contribute during
        # morning hours before grid charging kicks in.
        grid_charge_candidates.sort(key=lambda x: (x["price"], -x["slot_idx"]))

        # Track which slots are scheduled for grid charging
        scheduled_grid_charges: dict[int, dict] = {}
        scheduled_cumulative_kwh = 0.0

        for candidate in grid_charge_candidates:
            if scheduled_cumulative_kwh >= max_grid_import_kwh:
                # Budget exhausted
                break

            slot_idx = candidate["slot_idx"]
            charge_amount = candidate["charge_amount_kwh"]

            # Schedule this slot
            scheduled_grid_charges[slot_idx] = {
                "should_boost": candidate["should_boost"],
                "charge_amount_kwh": charge_amount,
                "price": candidate["price"],
            }
            scheduled_cumulative_kwh += charge_amount

        _LOGGER.info(
            "Price-optimized grid charging: %d candidates, %d scheduled (%.2f kWh of %.2f kWh max)",
            len(grid_charge_candidates),
            len(scheduled_grid_charges),
            scheduled_cumulative_kwh,
            max_grid_import_kwh,
        )

        # ========================================================================
        # Pass 4 (Issue #332): Verify target SOC at DW start, fill gap if needed
        #
        # The fixed budget in Pass 2 doesn't account for discharge between
        # charging slots and the demand window. This pass verifies that the
        # predicted SOC at DW start meets the target, and schedules additional
        # cheap slots if needed.
        # ========================================================================

        # Find the slot index for demand window start
        # The slot that CONTAINS the DW start time, not necessarily aligned to it
        dw_start_slot_idx = None
        dw_start_datetime = base_slot.replace(
            hour=dw_start_time.hour,
            minute=dw_start_time.minute,
            second=0,
            microsecond=0,
        )
        # If DW start is earlier than base_slot, it's tomorrow's DW
        if dw_start_datetime <= base_slot:
            dw_start_datetime += timedelta(days=1)

        for slot_idx in range(TOTAL_SLOTS):
            slot_start = base_slot + timedelta(minutes=15 * slot_idx)
            slot_end = slot_start + timedelta(minutes=15)
            # Check if DW start falls within this slot
            if slot_start <= dw_start_datetime < slot_end:
                dw_start_slot_idx = slot_idx
                break

        _LOGGER.info(
            "Pass 4: DW start time=%s, dw_start_datetime=%s, dw_start_slot_idx=%s",
            dw_start_time.strftime("%H:%M"),
            dw_start_datetime.strftime("%Y-%m-%d %H:%M"),
            dw_start_slot_idx if dw_start_slot_idx is not None else "None",
        )

        # If DW is today, verify target will be met
        if dw_start_slot_idx is not None:
            # Simulate SOC trajectory with scheduled grid charges to find SOC at DW start
            sim_soc = current_soc
            for slot_idx in range(dw_start_slot_idx):
                slot_start = base_slot + timedelta(minutes=15 * slot_idx)
                slot_hour = slot_start.hour

                # Get solar and load for this slot
                solar_kwh = get_solar_for_15min_slot(all_solcast, slot_start)
                slot_temp = temperature_by_hour.get(
                    (slot_start.year, slot_start.month, slot_start.day, slot_start.hour)
                )
                load_kw, _ = self._estimate_hourly_consumption_kw(
                    historical_avg_kw,
                    slot_hour,
                    current_hour,
                    data.load_power_kw,
                    recent_load_kw,
                    slot_temp,
                )

                # Add HVAC prediction
                daily_thermal_mode = getattr(data, "daily_thermal_mode", None)
                if isinstance(daily_thermal_mode, str):
                    pass
                elif daily_thermal_mode is not None:
                    daily_thermal_mode = str(daily_thermal_mode)

                hvac_kw = self._predict_hvac_load_for_slot(
                    slot_hour=slot_hour,
                    temperature=slot_temp,
                    daily_thermal_mode=daily_thermal_mode,
                )
                total_load_kw = load_kw + hvac_kw

                consumption_kwh = total_load_kw * slot_fraction
                net_kwh = solar_kwh - consumption_kwh

                # Calculate battery delta from solar
                max_solar_charge_kwh = CHARGE_RATE_SOLAR_KW * slot_fraction
                if net_kwh >= 0:
                    battery_delta_kwh = min(net_kwh, max_solar_charge_kwh) * 0.92
                else:
                    battery_delta_kwh = max(net_kwh, -max_solar_charge_kwh) / 0.95

                # Add grid charging if scheduled
                if slot_idx in scheduled_grid_charges:
                    scheduled = scheduled_grid_charges[slot_idx]
                    if scheduled["should_boost"]:
                        charge_rate = CHARGE_RATE_BOOST_KW
                    else:
                        charge_rate = CHARGE_RATE_GRID_KW

                    max_charge_kwh = charge_rate * slot_fraction
                    current_battery_kwh = sim_soc / 100 * BATTERY_CAPACITY_KWH
                    space_remaining_kwh = max(target_kwh - current_battery_kwh, 0)
                    grid_charge_amount = min(max_charge_kwh * 0.92, space_remaining_kwh)
                    battery_delta_kwh += grid_charge_amount

                # Update SOC
                sim_soc += battery_delta_kwh / BATTERY_CAPACITY_KWH * 100
                sim_soc = max(export_min_soc_pct, min(100.0, sim_soc))

            predicted_soc_at_dw = sim_soc

            # Check if we're below target
            if predicted_soc_at_dw < target_pct:
                gap_pct = target_pct - predicted_soc_at_dw
                gap_kwh = gap_pct / 100 * BATTERY_CAPACITY_KWH

                _LOGGER.info(
                    "Pass 4: Predicted SOC at DW start (%.1f%%) below target (%.1f%%). Gap: %.2f kWh. Scheduling additional slots.",
                    predicted_soc_at_dw,
                    target_pct,
                    gap_kwh,
                )

                # Find remaining candidates not already scheduled
                remaining_candidates = [
                    c
                    for c in grid_charge_candidates
                    if c["slot_idx"] not in scheduled_grid_charges
                    and c["slot_idx"] < dw_start_slot_idx  # Only slots before DW
                ]

                # Sort by price (cheapest first)
                remaining_candidates.sort(key=lambda x: x["price"])

                # Schedule until gap is filled
                additional_scheduled = 0
                for candidate in remaining_candidates:
                    if gap_kwh <= 0:
                        break

                    slot_idx = candidate["slot_idx"]
                    charge_amount = candidate["charge_amount_kwh"]

                    # Schedule this slot
                    scheduled_grid_charges[slot_idx] = {
                        "should_boost": candidate["should_boost"],
                        "charge_amount_kwh": charge_amount,
                        "price": candidate["price"],
                    }
                    gap_kwh -= charge_amount
                    additional_scheduled += 1

                if additional_scheduled > 0:
                    _LOGGER.info(
                        "Pass 4: Scheduled %d additional grid charge slot(s) to fill gap",
                        additional_scheduled,
                    )
            else:
                _LOGGER.info(
                    "Pass 4: Predicted SOC at DW start (%.1f%%) meets target (%.1f%%). No additional slots needed.",
                    predicted_soc_at_dw,
                    target_pct,
                )

        # ========================================================================
        # Pass 3: Build the forecast with scheduled grid charges
        # ========================================================================

        predicted_soc = current_soc

        for slot_idx in range(TOTAL_SLOTS):
            slot_start = base_slot + timedelta(minutes=15 * slot_idx)
            is_first_slot = slot_idx == 0

            slot_hour = slot_start.hour
            slot_minute = slot_start.minute
            slot_time = slot_start.time()

            # SOC at the start of this slot
            soc_at_slot_start = current_soc if is_first_slot else predicted_soc

            # Check if we're in demand window
            in_demand_window = dw_start_time <= slot_time < dw_end_time

            # Get solar forecast
            solar_kwh_or_none = get_solar_for_15min_slot_or_none(
                all_solcast, slot_start
            )
            if solar_kwh_or_none is None:
                solar_kwh = 0.0
            else:
                solar_kwh = solar_kwh_or_none

            # Get expected consumption
            slot_temp = temperature_by_hour.get(
                (slot_start.year, slot_start.month, slot_start.day, slot_start.hour)
            )
            baseline_kw, load_source = self._estimate_hourly_consumption_kw(
                historical_avg_kw,
                slot_hour,
                current_hour,
                data.load_power_kw,
                recent_load_kw,
                slot_temp,
            )

            daily_thermal_mode = getattr(data, "daily_thermal_mode", None)
            if isinstance(daily_thermal_mode, str):
                pass
            elif daily_thermal_mode is not None:
                daily_thermal_mode = str(daily_thermal_mode)

            hvac_kw = self._predict_hvac_load_for_slot(
                slot_hour=slot_hour,
                temperature=slot_temp,
                daily_thermal_mode=daily_thermal_mode,
            )

            total_load_kw = baseline_kw + hvac_kw

            if hvac_kw > 0.1:
                load_source = f"{load_source}+hvac"

            consumption_kwh = total_load_kw * slot_fraction
            net_kwh = solar_kwh - consumption_kwh

            _slot_price = get_price_for_slot(data.general_forecast, slot_start)

            gap_to_target = max(target_pct - soc_at_slot_start, 0)

            max_solar_charge_kwh = CHARGE_RATE_SOLAR_KW * slot_fraction
            max_grid_charge_kwh = CHARGE_RATE_GRID_KW * slot_fraction

            # Check if this slot is scheduled for grid charging
            if slot_idx in scheduled_grid_charges:
                scheduled = scheduled_grid_charges[slot_idx]
                should_grid_charge = True
                should_boost = scheduled["should_boost"]

                # Issue #309: Disable boost if SOC >= 80% (Powerwall throttles charge rate)
                # The scheduled boost was determined in Pass 1 using solar-only SOC simulation,
                # but here we have the actual predicted SOC including grid charging from
                # previous slots. Re-evaluate boost decision against actual SOC.
                if should_boost and predicted_soc >= BOOST_CHARGE_MAX_SOC:
                    should_boost = False
                    _LOGGER.info(
                        "Forecast: Disabling boost at %s - SOC %.1f%% >= 80%% (was scheduled from Pass 1)",
                        slot_start.strftime("%H:%M"),
                        predicted_soc,
                    )

                # Determine charge rate
                if should_boost:
                    max_grid_charge_kwh = CHARGE_RATE_BOOST_KW * slot_fraction
                else:
                    max_grid_charge_kwh = CHARGE_RATE_GRID_KW * slot_fraction
            else:
                should_grid_charge = False
                should_boost = False

            # Check if battery will be at or above 100% after solar charging
            battery_at_or_above_cap = (
                predicted_soc + (net_kwh / BATTERY_CAPACITY_KWH * 100)
            ) >= 100.0

            # Step 1: Calculate base battery delta from solar/load
            if net_kwh >= 0:
                battery_delta_kwh = min(net_kwh, max_solar_charge_kwh) * 0.92
            else:
                battery_delta_kwh = max(net_kwh, -max_solar_charge_kwh) / 0.95

            # Step 2: Add grid charging if scheduled
            if should_grid_charge:
                current_battery_kwh = predicted_soc / 100 * BATTERY_CAPACITY_KWH
                space_remaining_kwh = max(target_kwh - current_battery_kwh, 0)
                grid_charge_amount = min(
                    max_grid_charge_kwh * 0.92, space_remaining_kwh
                )
                battery_delta_kwh += grid_charge_amount
                grid_import_kwh = grid_charge_amount / 0.92
            else:
                grid_import_kwh = 0.0

            # Step 3: Check for proactive export
            elapsed_minutes = (slot_start - base_slot).total_seconds() / 60

            export_price_margin = float(
                self.entry.options.get(
                    CONF_EXPORT_PRICE_MARGIN, DEFAULT_EXPORT_PRICE_MARGIN
                )
            )

            _slot_fit_price = get_price_for_slot(data.feed_in_forecast, slot_start)
            should_proactive_export, proactive_export_amount = (
                self._should_proactive_export_at_slot(
                    slot_start=slot_start,
                    slot_hour=slot_hour,
                    solar_kwh=solar_kwh,
                    slot_fit_price=_slot_fit_price,
                    predicted_soc=predicted_soc,
                    target_pct=target_pct,
                    in_demand_window=in_demand_window,
                    forecasted_excess_kwh=forecasted_excess_kwh,
                    remaining_export_budget_kwh=remaining_export_budget,
                    feed_in_forecast=data.feed_in_forecast,
                    min_soc_no_exports=min_soc_no_exports,
                    export_min_soc_pct=export_min_soc_pct,
                    effective_cheap_price=data.effective_cheap_price,
                    feed_in_price_current=data.feed_in_price,
                    export_price_margin=export_price_margin,
                    all_solcast=all_solcast,
                    historical_avg_kw=historical_avg_kw,
                    current_load_kw=data.load_power_kw,
                    recent_load_kw=recent_load_kw,
                    general_forecast=data.general_forecast,
                    is_current_slot=is_first_slot,
                    current_elapsed_minutes=elapsed_minutes,
                    fill_point_elapsed_minutes=fill_point_elapsed_minutes,
                )
            )

            # Apply proactive export if needed
            if should_proactive_export:
                export_discharge_kwh = proactive_export_amount / 0.95
                battery_delta_kwh -= export_discharge_kwh
                grid_export_kwh = proactive_export_amount
                remaining_export_budget -= proactive_export_amount
            else:
                # Calculate normal grid export
                if net_kwh >= 0:
                    if battery_at_or_above_cap:
                        grid_export_kwh = net_kwh
                    else:
                        grid_export_kwh = max(0.0, net_kwh - max_solar_charge_kwh)
                else:
                    grid_export_kwh = 0.0

            # Update rolling SOC prediction
            if is_first_slot:
                new_predicted_soc = current_soc
            else:
                new_predicted_soc = predicted_soc + (
                    battery_delta_kwh / BATTERY_CAPACITY_KWH * 100
                )

            # Minimum SOC floor
            if (
                not is_first_slot
                and not should_grid_charge
                and new_predicted_soc < export_min_soc_pct
            ):
                shortfall_pct = export_min_soc_pct - new_predicted_soc
                passive_grid_import_kwh = shortfall_pct / 100 * BATTERY_CAPACITY_KWH
                grid_import_kwh += passive_grid_import_kwh
                new_predicted_soc = export_min_soc_pct

                if net_kwh > 0 and not in_demand_window:
                    excess_kwh = net_kwh
                    charge_delta = min(excess_kwh, max_solar_charge_kwh) * 0.92
                    new_predicted_soc += charge_delta / BATTERY_CAPACITY_KWH * 100
                    grid_import_kwh = max(0, grid_import_kwh - consumption_kwh / 0.92)

            predicted_soc = max(0.0, min(100.0, new_predicted_soc))

            daily_forecast_soc_15min.append(
                [slot_start.isoformat(), round(predicted_soc, 1)]
            )

            daily_forecast.append(
                {
                    "hour": slot_hour,
                    "minute": slot_minute,
                    "timestamp": slot_start.isoformat(),
                    "slot_interval_minutes": 15,
                    "predicted_soc": round(predicted_soc, 1),
                    "solar_kwh": round(solar_kwh, 4),
                    "consumption_kwh": round(consumption_kwh, 4),
                    "consumption_source": load_source,
                    "net_kwh": round(net_kwh, 4),
                    "grid_import_kwh": round(grid_import_kwh, 4),
                    "grid_export_kwh": round(grid_export_kwh, 4),
                    "grid_charge": should_grid_charge,
                    "grid_charge_boost": should_boost,
                    "proactive_export": should_proactive_export,
                    "export_amount_kwh": (
                        round(proactive_export_amount, 4)
                        if should_proactive_export
                        else 0.0
                    ),
                    "buy_price": round(_slot_price, 4),
                    "sell_price": round(_slot_fit_price, 4),
                }
            )

        # Log warning if any solar forecast data was missing
        if missing_solar_slots:
            _LOGGER.warning(
                "Solar forecast data missing for %d slot(s): %s. "
                "These slots will use 0.0 kWh solar. Check Solcast integration.",
                len(missing_solar_slots),
                ", ".join(missing_solar_slots[:10])
                + ("..." if len(missing_solar_slots) > 10 else ""),
            )

        # ========================================================================
        # SUMMARY LOGGING: Grid charging decisions across all 96 slots
        # ========================================================================
        grid_charge_slots = sum(
            1 for slot in daily_forecast if slot.get("grid_charge", False)
        )
        skipped_slots = TOTAL_SLOTS - grid_charge_slots
        _LOGGER.info(
            "Grid charging forecast: %d slot(s) with grid charging, %d slot(s) skipped (solar sufficient or price not cheap)",
            grid_charge_slots,
            skipped_slots,
        )

        # ========================================================================
        # CALCULATE FORECAST COSTS (rest of today)
        # ========================================================================
        end_of_today = now_dt.replace(hour=23, minute=59, second=59, microsecond=0)

        forecast_import_cost = 0.0
        forecast_export_revenue = 0.0
        forecast_grid_charge_cost = 0.0
        forecast_proactive_export_revenue = 0.0

        for slot in daily_forecast:
            slot_ts = slot.get("timestamp", "")
            if not slot_ts:
                continue

            try:
                slot_dt = datetime.fromisoformat(slot_ts)
            except ValueError:
                continue

            if slot_dt > end_of_today:
                break

            grid_import_kwh = slot.get("grid_import_kwh", 0) or 0
            grid_export_kwh = slot.get("grid_export_kwh", 0) or 0
            buy_price = slot.get("buy_price", 0) or 0
            sell_price = slot.get("sell_price", 0) or 0
            is_grid_charge = slot.get("grid_charge", False)
            is_proactive_export = slot.get("proactive_export", False)

            forecast_import_cost += grid_import_kwh * buy_price
            forecast_export_revenue += grid_export_kwh * sell_price

            if is_grid_charge:
                forecast_grid_charge_cost += grid_import_kwh * buy_price

            if is_proactive_export:
                forecast_proactive_export_revenue += grid_export_kwh * sell_price

        data.forecast_import_cost = round(forecast_import_cost, 2)
        data.forecast_export_revenue = round(forecast_export_revenue, 2)
        data.forecast_net_cost = round(
            forecast_import_cost - forecast_export_revenue, 2
        )
        data.forecast_grid_charge_cost = round(forecast_grid_charge_cost, 2)
        data.forecast_proactive_export_revenue = round(
            forecast_proactive_export_revenue, 2
        )

        _LOGGER.info(
            "Forecast costs (rest of today): import=%.2f, export=%.2f, net=%.2f",
            data.forecast_import_cost,
            data.forecast_export_revenue,
            data.forecast_net_cost,
        )

        return daily_forecast, daily_forecast_soc_15min, consumption_source_counts

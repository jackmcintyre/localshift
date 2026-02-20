"""Forecast computer for battery SOC and grid interaction forecasting."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from ..const import (
    BATTERY_CAPACITY_KWH,
    CHARGE_RATE_BACKUP_KW,
    CHARGE_RATE_BOOST_KW,
    CHARGE_RATE_SOLAR_KW,
    CONF_BATTERY_TARGET,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    CONF_LOAD_WEIGHT_RECENT,
    CONF_MINIMUM_TARGET_SOC,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_LOAD_WEIGHT_RECENT,
    DEFAULT_MINIMUM_TARGET_SOC,
)
from ..coordinator_data import CoordinatorData
from .solar_utils import (
    get_price_for_slot,
    get_price_for_slot_or_none,
    get_solar_for_15min_slot,
    get_solar_for_15min_slot_or_none,
)

_LOGGER = logging.getLogger(__name__)

# Forecast slot constants
# 15-min slots throughout for consistent alignment with Solcast 30-minute periods
TOTAL_SLOTS = 96  # 24 hours × 4 slots/hour


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
    ) -> None:
        """Initialize forecast computer.

        Args:
            entry: Config entry
            get_entity_id_func: Function to get entity IDs by config key
            get_historical_func: Function to get historical hourly averages (combined profile)
            get_profile_for_day_func: Optional function to get day-aware profile (issue-60)
        """
        self.entry = entry
        self._get_entity_id = get_entity_id_func
        self._get_historical_hourly_averages = get_historical_func
        self._get_profile_for_day = get_profile_for_day_func

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
    ) -> tuple[float, str]:
        """Estimate hourly household consumption with time-distance-weighted blend.

        Blends recent 1-hour average with historical hourly average ONLY for
        hours close to current time, when recent load is predictive.

        For distant hours (e.g., overnight when forecasting from midday),
        uses historical profile only to avoid overestimating load.

        Returns tuple of (kW, source_tag).
        """
        # Get the weighting configuration
        recent_weight = float(
            self.entry.options.get(CONF_LOAD_WEIGHT_RECENT, DEFAULT_LOAD_WEIGHT_RECENT)
        )
        historical_weight = 1.0 - recent_weight

        historical_raw = hourly_avg_kw.get(slot_hour) if hourly_avg_kw else None
        historical_kw = (
            float(historical_raw) if isinstance(historical_raw, int | float) else 0.0
        )

        # Check if we have valid historical data
        has_historical = historical_kw > 0

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
                weighted = (recent_weight * recent_load_kw) + (
                    historical_weight * historical_kw
                )
                return round(weighted, 3), "weighted_load"

        # Fallback to historical if available (primary path for distant hours)
        if has_historical:
            return round(historical_kw, 3), "profile_hour"

        # Fallback to current load
        base_kw = current_load_kw if current_load_kw > 0 else 0.6
        return round(base_kw, 3), "live_load_fallback"

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
    ) -> tuple[float, float, bool]:
        """Simulate future SOC trajectory with solar only (no grid charging).

        Uses 15-min slots throughout for consistency with main forecast loop.

        When end_time == dw_start_time, simulation stops at DW start (existing behavior).
        When end_time > dw_start_time, simulation continues through DW period.

        This helps determine if grid charging is necessary.

        Args:
            actual_current_soc: ACTUAL current battery SOC (from real-time data)
            start_slot: Starting slot time
            target_pct: Target SOC percentage
            all_solcast: Full Solcast forecast (today + tomorrow)
            historical_avg_kw: Historical hourly load profile
            current_load_kw: Current load power
            recent_load_kw: Recent 1-hour average load
            dw_start_time: Demand window start time
            end_time: End time (exclusive) to simulate until

        Returns:
            (soc_at_end_pct, max_soc_pct, can_reach_target)
        """
        soc = actual_current_soc
        base_slot = start_slot.replace(second=0, microsecond=0)

        # Cap end_time to Solcast horizon to avoid repeated solar lookups outside forecast range.
        solcast_end: datetime | None = None
        period_duration = timedelta(minutes=30)
        for entry in all_solcast:
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
            if solcast_end is None or end_local > solcast_end:
                solcast_end = end_local

        sim_end = end_time
        if solcast_end is not None and sim_end > solcast_end:
            sim_end = solcast_end

        if sim_end <= base_slot:
            return soc, soc, soc >= target_pct

        # Use 15-min slots throughout for consistency
        max_soc = soc
        slot_fraction = 15 / 60.0  # 0.25 hours

        slot_time = base_slot
        while slot_time < sim_end:
            slot_time += timedelta(minutes=15)
            slot_hour = slot_time.hour

            # Get solar and load for this 15-min slot
            solar_kwh = get_solar_for_15min_slot(all_solcast, slot_time)

            load_kw, _ = self._estimate_hourly_consumption_kw(
                historical_avg_kw,
                slot_hour,
                current_hour,
                current_load_kw,
                recent_load_kw,
            )
            consumption_kwh = load_kw * slot_fraction
            net_kwh = solar_kwh - consumption_kwh

            # Apply battery delta (no grid charging)
            # Use solar charge rate (5kW) as max
            max_slot_transfer_kwh = CHARGE_RATE_SOLAR_KW * slot_fraction
            if net_kwh >= 0:
                delta = min(net_kwh, max_slot_transfer_kwh) * 0.92
            else:
                delta = max(net_kwh, -max_slot_transfer_kwh) / 0.95

            soc += delta / BATTERY_CAPACITY_KWH * 100
            soc = max(min_soc_pct, min(100.0, soc))

            max_soc = max(max_soc, soc)

            # Fast-path: if we've already reached target, we can stop.
            if max_soc >= target_pct:
                return soc, max_soc, True

        return soc, max_soc, max_soc >= target_pct

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
        """Simulate overnight drain from current slot until solar starts.

        Uses 15-min slots throughout for consistency with main forecast loop.

        Args:
            start_soc: Starting SOC percentage
            start_slot: Starting slot time
            solar_start: When solar production starts
            all_solcast: Full Solcast forecast
            historical_avg_kw: Historical hourly load profile
            current_load_kw: Current load power
            recent_load_kw: Recent 1-hour average load
            min_soc_pct: Minimum SOC floor

        Returns:
            SOC percentage at solar start time
        """
        soc = start_soc
        base_slot = start_slot.replace(second=0, microsecond=0)

        # Use 15-min slots throughout for consistency
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

            # Apply battery discharge
            max_slot_transfer_kwh = CHARGE_RATE_BACKUP_KW * slot_fraction
            if net_kwh >= 0:
                delta = min(net_kwh, max_slot_transfer_kwh) * 0.92
            else:
                delta = max(net_kwh, -max_slot_transfer_kwh) / 0.95

            soc += delta / BATTERY_CAPACITY_KWH * 100
            soc = max(min_soc_pct, min(100.0, soc))

            slot_time += timedelta(minutes=15)

        return soc

    def _find_negative_fit_windows(
        self, feed_in_forecast: list[dict], start_time: datetime, max_hours: int = 24
    ) -> list[tuple[datetime, datetime, float]]:
        """Find windows where feed-in price ≤ 0.

        Args:
            feed_in_forecast: Feed-in price forecast
            start_time: Start time for search
            max_hours: How many hours to search ahead

        Returns:
            List of (window_start, window_end, min_price) tuples
        """
        negative_windows = []
        base_slot = start_time.replace(minute=0, second=0, microsecond=0)
        current_window_start = None
        min_price_in_window = 0.0

        for offset in range(max_hours * 12):  # 5-min intervals = 12 per hour
            slot_time = base_slot + timedelta(minutes=5 * offset)
            price = get_price_for_slot_or_none(feed_in_forecast, slot_time)

            if price is not None and price <= 0:
                if current_window_start is None:
                    current_window_start = slot_time
                    min_price_in_window = price
                else:
                    min_price_in_window = min(min_price_in_window, price)
            elif current_window_start is not None:
                # Window ended
                negative_windows.append(
                    (current_window_start, slot_time, min_price_in_window)
                )
                current_window_start = None
                min_price_in_window = 0.0

        # Close any open window
        if current_window_start is not None:
            negative_windows.append(
                (
                    current_window_start,
                    base_slot + timedelta(minutes=5 * max_hours * 12),
                    min_price_in_window,
                )
            )

        return negative_windows

    def _calculate_solar_during_negative_fit(
        self,
        base_slot: datetime,
        negative_fit_windows: list[tuple[datetime, datetime, float]],
        all_solcast: list[dict],
        historical_avg_kw: dict[int, float],
        current_load_kw: float,
        recent_load_kw: float,
        current_hour: int | None = None,
        fill_point_datetime: datetime | None = None,
    ) -> float:
        """Calculate solar energy that would be exported at negative FIT prices.

        This only counts solar AFTER the battery fills, because:
        - Before fill: solar charges the battery (not exported)
        - After fill: solar is exported at the current FIT price

        If the battery fills DURING a negative FIT window, we only count
        solar from the fill point onwards.

        Args:
            base_slot: Starting slot time
            negative_fit_windows: List of (start, end, min_price) tuples for negative FIT
            all_solcast: Full Solcast forecast
            historical_avg_kw: Historical hourly load profile
            current_load_kw: Current load power
            recent_load_kw: Recent 1-hour average load
            current_hour: Current hour for load estimation
            fill_point_datetime: When battery reaches 100% (or None if never fills)

        Returns:
            Total kWh of solar that would be exported at negative FIT prices
        """
        if not negative_fit_windows:
            return 0.0

        total_solar_kwh = 0.0
        slot_fraction = 15 / 60.0  # 0.25 hours

        for window_start, window_end, _ in negative_fit_windows:
            # Skip windows that have already passed
            if window_end <= base_slot:
                continue

            # Adjust start to be at or after base_slot
            effective_start = max(window_start, base_slot)

            # KEY FIX: If battery fills during or before this window,
            # only count solar AFTER the fill point
            if fill_point_datetime is not None:
                if fill_point_datetime >= window_end:
                    # Battery fills AFTER this window - no export at negative FIT
                    continue
                elif fill_point_datetime > effective_start:
                    # Battery fills DURING this window - start counting from fill point
                    effective_start = fill_point_datetime

            # Calculate slots in this window
            window_duration_minutes = (
                window_end - effective_start
            ).total_seconds() / 60
            slots_in_window = max(0, int(window_duration_minutes / 15))

            for offset in range(slots_in_window):
                slot_start = effective_start + timedelta(minutes=15 * offset)
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

                # Only count excess solar (would be exported)
                if net_kwh > 0:
                    total_solar_kwh += net_kwh

        return total_solar_kwh

    # Hysteresis margin for grid charging decisions (Issue #34)
    # Once grid charging starts, require this much margin above target before stopping
    GRID_CHARGE_HYSTERESIS_MARGIN_PCT = 5.0

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

        # HYSTERESIS: If currently grid charging, apply stickiness to prevent flip-flopping
        # Only stop charging if:
        # 1. Price is no longer cheap, OR
        # 2. Solar simulation shows STRONG margin above target (target + hysteresis)
        if is_currently_grid_charging and price_is_cheap and gap_to_target > 0:
            # Continue charging - don't stop just because solar *might* reach target
            # The forecast could be optimistic; real-time conditions may differ
            _LOGGER.info(
                "GRID_CHARGE HYSTERESIS: Continuing at %s (price=$%.2f, SOC=%.1f%%, gap=%.1f%%) - avoiding flip-flop",
                slot_start.strftime("%H:%M"),
                use_price,
                predicted_soc,
                target_pct,
                gap_to_target,
            )
            # Check if we should boost (very cheap price)
            if price_is_very_cheap:
                return True, True
            return True, False

        # SMART FORECAST: Simulate forward with solar only
        # Model: can we reach target using solar only?
        # If yes, do NOT grid charge.
        #
        # KEY: When allow_dw_entry_under_target is True, simulate to DW END
        # instead of DW START. This allows solar to continue charging during
        # the DW period and reach target within the DW window.
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

                # Now simulate from solar start to next DW
                soc_at_end, max_soc, can_reach_with_solar_only = (
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
                    )
                )

                _LOGGER.debug(
                    "OVERNIGHT_CHECK: %02d:%02d SOC %.1f%% -> %.1f%% at solar start %s, max_soc=%.1f%%",
                    slot_start.hour,
                    slot_start.minute,
                    predicted_soc,
                    soc_at_solar_start,
                    solar_start.strftime("%H:%M"),
                    max_soc,
                )

                # Solar from dawn can reach target: NO overnight grid charging
                if can_reach_with_solar_only:
                    _LOGGER.debug(
                        "Grid charge SKIPPED overnight: solar from %s reaches target (SOC at dawn: %.1f%% -> max %.1f%%)",
                        solar_start.strftime("%H:%M"),
                        soc_at_solar_start,
                        max_soc,
                    )
                    return False, False
            else:
                # No solar found in forecast - proceed with normal simulation
                soc_at_end, max_soc, can_reach_with_solar_only = (
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
                    )
                )

                if can_reach_with_solar_only:
                    _LOGGER.debug(
                        "Grid charge SKIPPED: solar forecast reaches target before DW (max_soc=%.1f%% >= %d%%)",
                        max_soc,
                        target_pct,
                    )
                    return False, False
        else:
            # Daylight slot - use original simulation logic
            soc_at_end, max_soc, can_reach_with_solar_only = (
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
                )
            )

            # Solar forecast says we'll reach target: NO grid charging
            if can_reach_with_solar_only:
                _LOGGER.debug(
                    "Grid charge SKIPPED: solar forecast reaches target before DW (max_soc=%.1f%% >= %d%%)",
                    max_soc,
                    target_pct,
                )
                return False, False

        # SAFETY NET: Charge if very cheap (forecast could be wrong)
        # IMPORTANT: only applies when solar *cannot* meet the target before DW.
        if price_is_very_cheap:
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
        """Calculate average FIT price over forecast window.

        Args:
            feed_in_forecast: Feed-in price forecast
            start_time: Start time for calculation
            hours: How many hours to include

        Returns:
            Average FIT price, or 0.0 if no data
        """
        prices = []
        base_slot = start_time.replace(minute=0, second=0, microsecond=0)

        for offset in range(hours * 12):  # 5-min intervals
            slot_time = base_slot + timedelta(minutes=5 * offset)
            price = get_price_for_slot(feed_in_forecast, slot_time)
            if price is not None:
                prices.append(price)

        if not prices:
            return 0.0

        return sum(prices) / len(prices)

    def _calculate_percentile_fit_price(
        self,
        feed_in_forecast: list[dict],
        start_time: datetime,
        percentile: float = 60.0,
        hours: int = 24,
    ) -> float:
        """Calculate Nth percentile FIT price over forecast window.

        Excludes bottom percentile of prices to identify reasonable export windows.
        E.g., 60th percentile excludes bottom 40% (mostly zero/negative prices).

        Args:
            feed_in_forecast: Feed-in price forecast
            start_time: Start time for calculation
            percentile: Percentile threshold (0-100, default 60)
            hours: How many hours to include

        Returns:
            Percentile FIT price, or 0.0 if no data
        """
        prices = []
        base_slot = start_time.replace(minute=0, second=0, microsecond=0)

        for offset in range(hours * 12):  # 5-min intervals
            slot_time = base_slot + timedelta(minutes=5 * offset)
            price = get_price_for_slot(feed_in_forecast, slot_time)
            if price is not None:
                prices.append(price)

        if not prices:
            return 0.0

        # Sort and find percentile
        prices.sort()
        index = int(len(prices) * percentile / 100)
        index = min(index, len(prices) - 1)
        return prices[index]

    def _calculate_max_fit_price(
        self,
        feed_in_forecast: list[dict],
        start_time: datetime,
        hours: int = 24,
    ) -> float:
        """Calculate maximum FIT price over forecast window.

        Args:
            feed_in_forecast: Feed-in price forecast
            start_time: Start time for calculation
            hours: How many hours to include

        Returns:
            Maximum FIT price, or 0.0 if no data
        """
        prices = []
        base_slot = start_time.replace(minute=0, second=0, microsecond=0)

        for offset in range(hours * 12):  # 5-min intervals
            slot_time = base_slot + timedelta(minutes=5 * offset)
            price = get_price_for_slot(feed_in_forecast, slot_time)
            if price is not None:
                prices.append(price)

        if not prices:
            return 0.0

        return max(prices)

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
        """Simulate 24-hour forecast WITHOUT proactive exports to find minimum SOC.

        Uses 15-min slots throughout for consistency with main forecast loop.

        This helps determine how much we can safely export without dropping
        below minimum SOC threshold.

        Args:
            start_soc: Starting SOC percentage
            start_slot: Starting slot time
            all_solcast: Full Solcast forecast
            historical_avg_kw: Historical hourly load profile
            current_load_kw: Current load power
            recent_load_kw: Recent 1-hour average load
            dw_start_time: Demand window start time
            dw_end_time: Demand window end time
            max_hours: How many hours to simulate

        Returns:
            (minimum_soc_pct, final_soc_pct)
        """
        soc = start_soc
        min_soc = soc
        base_slot = start_slot.replace(second=0, microsecond=0)

        # Use 15-min slots throughout for consistency
        slot_fraction = 15 / 60.0  # 0.25 hours

        # Calculate total slots to simulate
        total_slots = max_hours * 4  # 4 slots per hour

        for i in range(total_slots):
            slot_time = base_slot + timedelta(minutes=15 * i)
            slot_hour = slot_time.hour

            # Get solar and load for this 15-min slot
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

            # Apply realistic battery limits (no grid charging in this simulation)
            max_slot_transfer_kwh = CHARGE_RATE_BACKUP_KW * slot_fraction

            if net_kwh >= 0:
                delta = min(net_kwh, max_slot_transfer_kwh) * 0.92
            else:
                delta = max(net_kwh, -max_slot_transfer_kwh) / 0.95

            # Update SOC
            soc += delta / BATTERY_CAPACITY_KWH * 100
            soc = max(0.0, min(100.0, soc))

            # Track minimum SOC
            min_soc = min(min_soc, soc)

        return min_soc, soc

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
        """Simulate overnight drain after export to find minimum SOC.

        Uses 15-min slots throughout for consistency with main forecast loop.

        This simulates from the export slot until solar production starts
        (typically 06:00-07:00) to ensure the battery won't drop below
        minimum SOC during the night.

        Args:
            start_soc: Starting SOC percentage (after export)
            start_slot: Starting slot time (after export slot)
            all_solcast: Full Solcast forecast
            historical_avg_kw: Historical hourly load profile
            current_load_kw: Current load power
            recent_load_kw: Recent 1-hour average load
            export_min_soc_pct: Minimum SOC threshold

        Returns:
            (minimum_soc_pct, soc_at_solar_start, solar_found_in_forecast)
        """
        soc = start_soc
        min_soc = soc
        base_slot = start_slot.replace(second=0, microsecond=0)

        # Find when solar production starts (first slot with >0.1 kWh solar)
        solar_start_slot = None
        for offset in range(24 * 4):  # Check up to 24 hours (96 slots)
            check_slot = base_slot + timedelta(minutes=15 * offset)
            solar_kwh = get_solar_for_15min_slot(all_solcast, check_slot)
            if solar_kwh > 0.1:  # Meaningful solar production
                solar_start_slot = check_slot
                break

        # Track whether we found solar in the forecast
        solar_found = solar_start_slot is not None

        # If no solar found, simulate 8 hours (typical overnight period)
        if solar_start_slot is None:
            solar_start_slot = base_slot + timedelta(hours=8)

        # Use 15-min slots throughout for consistency
        slot_fraction = 15 / 60.0  # 0.25 hours

        slot_time = base_slot
        while slot_time < solar_start_slot:
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

            # Apply battery discharge (negative net = discharge)
            max_slot_transfer_kwh = CHARGE_RATE_BACKUP_KW * slot_fraction
            if net_kwh >= 0:
                delta = min(net_kwh, max_slot_transfer_kwh) * 0.92
            else:
                delta = max(net_kwh, -max_slot_transfer_kwh) / 0.95

            # Update SOC
            soc += delta / BATTERY_CAPACITY_KWH * 100
            soc = max(0.0, min(100.0, soc))

            # Track minimum SOC
            min_soc = min(min_soc, soc)

            # Early exit if we've already dropped below minimum
            if min_soc < export_min_soc_pct:
                break

            slot_time += timedelta(minutes=15)

        return min_soc, soc, solar_found

    def _find_battery_fill_point(
        self,
        start_soc: float,
        start_slot: datetime,
        all_solcast: list[dict],
        historical_avg_kw: dict[int, float],
        current_load_kw: float,
        recent_load_kw: float,
        current_hour: int | None = None,
    ) -> int | None:
        """Find elapsed minutes when battery first reaches 100% from solar charging.

        Uses 15-min slots throughout for consistency with main forecast loop.

        Args:
            start_soc: Starting SOC percentage
            start_slot: Starting slot time
            all_solcast: Full Solcast forecast
            historical_avg_kw: Historical hourly load profile
            current_load_kw: Current load power
            recent_load_kw: Recent 1-hour average load

        Returns:
            Elapsed minutes until 100% SOC, or None if it never fills
        """
        soc = start_soc
        base_slot = start_slot.replace(second=0, microsecond=0)
        elapsed_minutes = 0
        slot_fraction = 15 / 60.0  # 0.25 hours

        # Use 15-min slots throughout for consistency
        for i in range(TOTAL_SLOTS):
            slot_start = base_slot + timedelta(minutes=15 * i)
            slot_hour = slot_start.hour

            # Use 15-min solar function
            solar_kwh = get_solar_for_15min_slot(all_solcast, slot_start)
            load_kw, _ = self._estimate_hourly_consumption_kw(
                historical_avg_kw,
                slot_hour,
                current_hour,
                current_load_kw,
                recent_load_kw,
            )
            # Scale consumption to 15-min slot
            consumption_kwh = load_kw * slot_fraction
            net_kwh = solar_kwh - consumption_kwh

            # Apply battery charging (no grid charging, no exports)
            # Use solar charge rate (5kW) as max, scale to 15-min slot
            max_slot_transfer_kwh = CHARGE_RATE_SOLAR_KW * slot_fraction
            if net_kwh >= 0:
                delta = min(net_kwh, max_slot_transfer_kwh) * 0.92
            else:
                delta = max(net_kwh, -max_slot_transfer_kwh) / 0.95

            soc += delta / BATTERY_CAPACITY_KWH * 100
            soc = min(100.0, soc)  # Cap at 100%

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
    ) -> float:
        """Calculate net solar energy (solar - load) between two time points.

        Uses 15-min slots throughout for consistency with main forecast loop.

        Args:
            start_elapsed_minutes: Starting time in minutes from base_slot
            end_elapsed_minutes: Ending time in minutes from base_slot
            base_slot: Base datetime for offset calculation
            all_solcast: Full Solcast forecast
            historical_avg_kw: Historical hourly load profile
            current_load_kw: Current load power
            recent_load_kw: Recent 1-hour average load

        Returns:
            Net solar energy in kWh (positive = excess)
        """
        net_energy = 0.0
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
        """Calculate excess solar energy for different time windows.

        Args:
            base_slot: Starting slot time
            all_solcast: Full Solcast forecast
            historical_avg_kw: Historical hourly load profile
            current_load_kw: Current load power
            recent_load_kw: Recent 1-hour average load
            current_soc: Current battery SOC percentage
            target_pct: Target SOC percentage
            current_hour: Current hour for load estimation

        Returns:
            Dict with excess amounts for different windows:
            - excess_current_hour_kwh
            - excess_next_2h_kwh
            - excess_next_4h_kwh
            - excess_until_battery_full_kwh
        """
        slot_fraction = 15 / 60.0  # 0.25 hours
        current_kwh = current_soc / 100 * BATTERY_CAPACITY_KWH
        target_kwh = target_pct / 100 * BATTERY_CAPACITY_KWH
        space_to_target_kwh = max(target_kwh - current_kwh, 0)

        excess_current_hour = 0.0
        excess_next_2h = 0.0
        excess_next_4h = 0.0
        excess_until_full = 0.0
        accumulated_space = space_to_target_kwh

        # Calculate for each time window
        for offset in range(16):  # 4 hours = 16 slots
            slot_start = base_slot + timedelta(minutes=15 * offset)
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
                # Apply charging efficiency
                excess_kwh = net_kwh * 0.92

                # Accumulate for time windows
                if offset < 4:  # First hour (4 slots)
                    excess_current_hour += excess_kwh
                if offset < 8:  # First 2 hours (8 slots)
                    excess_next_2h += excess_kwh
                excess_next_4h += excess_kwh  # Full 4 hours

                # Track excess until battery would be full
                if accumulated_space > 0:
                    used_for_target = min(excess_kwh, accumulated_space)
                    accumulated_space -= used_for_target
                else:
                    # Battery would be full, this is true excess
                    excess_until_full += excess_kwh

        return {
            "excess_current_hour_kwh": round(excess_current_hour, 2),
            "excess_next_2h_kwh": round(excess_next_2h, 2),
            "excess_next_4h_kwh": round(excess_next_4h, 2),
            "excess_until_battery_full_kwh": round(excess_until_full, 2),
        }

    def _find_nearest_negative_fit_window(
        self,
        feed_in_forecast: list[dict],
        start_time: datetime,
        max_hours: int = 24,
    ) -> tuple[datetime | None, int]:
        """Find the next negative FIT window.

        Args:
            feed_in_forecast: Feed-in price forecast
            start_time: Start time for search
            max_hours: How many hours to search ahead

        Returns:
            (window_start, duration_minutes) or (None, 0) if no window found
        """
        base_slot = start_time.replace(minute=0, second=0, microsecond=0)
        current_window_start = None
        window_duration_minutes = 0

        for offset in range(max_hours * 12):  # 5-min intervals = 12 per hour
            slot_time = base_slot + timedelta(minutes=5 * offset)
            price = get_price_for_slot_or_none(feed_in_forecast, slot_time)

            if price is not None and price <= 0:
                if current_window_start is None:
                    current_window_start = slot_time
                    window_duration_minutes = 5
                else:
                    window_duration_minutes += 5
            elif current_window_start is not None:
                # Window ended - return the first one found
                return current_window_start, window_duration_minutes

        # If window extends to end of search period
        if current_window_start is not None:
            return current_window_start, window_duration_minutes

        return None, 0

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
        """Calculate excess solar available before negative FIT window.

        Args:
            base_slot: Starting slot time
            negative_fit_start: When negative FIT window starts (or None)
            all_solcast: Full Solcast forecast
            historical_avg_kw: Historical hourly load profile
            current_load_kw: Current load power
            recent_load_kw: Recent 1-hour average load
            current_soc: Current battery SOC percentage
            target_pct: Target SOC percentage
            current_hour: Current hour for load estimation

        Returns:
            Excess kWh available before negative FIT window
        """
        if negative_fit_start is None:
            return 0.0

        slot_fraction = 15 / 60.0  # 0.25 hours
        current_kwh = current_soc / 100 * BATTERY_CAPACITY_KWH
        target_kwh = target_pct / 100 * BATTERY_CAPACITY_KWH
        space_to_target_kwh = max(target_kwh - current_kwh, 0)

        excess_until_fit = 0.0

        # Calculate slots until negative FIT window
        slots_until_fit = int((negative_fit_start - base_slot).total_seconds() / 900)
        slots_until_fit = max(0, min(slots_until_fit, 96))  # Cap at 24 hours

        for offset in range(slots_until_fit):
            slot_start = base_slot + timedelta(minutes=15 * offset)
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
                excess_kwh = net_kwh * 0.92

                if space_to_target_kwh > 0:
                    used_for_target = min(excess_kwh, space_to_target_kwh)
                    space_to_target_kwh -= used_for_target
                else:
                    excess_until_fit += excess_kwh

        return round(excess_until_fit, 2)

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
        """Calculate max additional load that won't trigger grid charging.

        Uses simulation to find the safe threshold. Tests progressively
        higher additional loads until grid charging would be required.

        Args:
            base_slot: Starting slot time
            all_solcast: Full Solcast forecast
            historical_avg_kw: Historical hourly load profile
            current_load_kw: Current load power
            recent_load_kw: Recent 1-hour average load
            current_soc: Current battery SOC percentage
            target_pct: Target SOC percentage
            dw_start_time: Demand window start time
            effective_cheap_price: Cheap price threshold
            general_forecast: Buy price forecast
            min_soc_pct: Minimum SOC floor
            current_hour: Current hour for load estimation

        Returns:
            (safe_additional_load_kw, grid_charge_risk)
        """
        # Quick check: if SOC is below target, adding load is risky
        if current_soc < target_pct - 5:
            return 0.0, True

        # Simulate with progressively higher loads
        # Test loads: 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0 kW
        test_loads = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]
        safe_load = 0.0

        for additional_load in test_loads:
            would_grid_charge = self._simulate_with_additional_load(
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
                additional_load_kw=additional_load,
                min_soc_pct=min_soc_pct,
                current_hour=current_hour,
            )

            if would_grid_charge:
                # This load would trigger grid charging
                # Return the previous safe value
                return safe_load, safe_load < additional_load

            safe_load = additional_load

        # All tested loads are safe
        return safe_load, False

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
        """Simulate forecast with additional load to check for grid charging.

        Args:
            base_slot: Starting slot time
            all_solcast: Full Solcast forecast
            historical_avg_kw: Historical hourly load profile
            current_load_kw: Current load power
            recent_load_kw: Recent 1-hour average load
            current_soc: Current battery SOC percentage
            target_pct: Target SOC percentage
            dw_start_time: Demand window start time
            effective_cheap_price: Cheap price threshold
            general_forecast: Buy price forecast
            additional_load_kw: Additional load to simulate
            min_soc_pct: Minimum SOC floor
            current_hour: Current hour for load estimation

        Returns:
            True if grid charging would be needed, False otherwise
        """
        soc = current_soc
        slot_fraction = 15 / 60.0  # 0.25 hours

        # Find next DW start
        next_dw_start = base_slot.replace(
            hour=dw_start_time.hour,
            minute=dw_start_time.minute,
            second=0,
            microsecond=0,
        )
        if next_dw_start <= base_slot:
            next_dw_start += timedelta(days=1)

        # Simulate until DW start
        slots_to_simulate = int((next_dw_start - base_slot).total_seconds() / 900)
        slots_to_simulate = max(0, min(slots_to_simulate, 96))

        for offset in range(slots_to_simulate):
            slot_start = base_slot + timedelta(minutes=15 * offset)
            slot_hour = slot_start.hour

            solar_kwh = get_solar_for_15min_slot(all_solcast, slot_start)
            load_kw, _ = self._estimate_hourly_consumption_kw(
                historical_avg_kw,
                slot_hour,
                current_hour,
                current_load_kw,
                recent_load_kw,
            )

            # Add the additional load we're testing
            total_consumption_kwh = (load_kw + additional_load_kw) * slot_fraction
            net_kwh = solar_kwh - total_consumption_kwh

            # Apply battery delta
            max_slot_transfer_kwh = CHARGE_RATE_SOLAR_KW * slot_fraction
            if net_kwh >= 0:
                delta = min(net_kwh, max_slot_transfer_kwh) * 0.92
            else:
                delta = max(net_kwh, -max_slot_transfer_kwh) / 0.95

            soc += delta / BATTERY_CAPACITY_KWH * 100
            soc = max(min_soc_pct, min(100.0, soc))

        # Check if we'd need grid charging to reach target
        if soc < target_pct:
            # Would need grid charging - check if there are cheap slots available
            for offset in range(slots_to_simulate):
                slot_start = base_slot + timedelta(minutes=15 * offset)
                price = get_price_for_slot(general_forecast, slot_start)
                if price is not None and price <= effective_cheap_price:
                    return True  # Would grid charge at cheap price

        return False

    def _compute_load_shift_signal(
        self,
        data: CoordinatorData,
        excess_by_windows: dict[str, float],
        negative_fit_start: datetime | None,
        safe_additional_load: float,
        grid_charge_risk: bool,
        fill_point_minutes: int | None,
    ) -> tuple[str, float, int, str, str]:
        """Determine the load shift signal based on current state and forecast.

        Args:
            data: CoordinatorData with current state
            excess_by_windows: Excess amounts for different time windows
            negative_fit_start: When negative FIT window starts
            safe_additional_load: Max safe additional load in kW
            grid_charge_risk: Whether adding load might trigger grid charging
            fill_point_minutes: Minutes until battery fills (or None)

        Returns:
            (signal, recommended_kw, duration_minutes, reason, confidence)
        """
        # HOLD conditions (highest priority)
        if data.demand_window_active:
            return (
                "HOLD",
                0.0,
                0,
                "Demand window active - maintain current loads",
                "high",
            )

        if data.manual_override:
            return "HOLD", 0.0, 0, "Manual override active", "high"

        if not data.solcast_today:
            return "HOLD", 0.0, 0, "No solar forecast available", "low"

        # REDUCE_LOAD conditions
        if grid_charge_risk:
            return (
                "REDUCE_LOAD",
                -1.0,  # Suggest reducing by 1kW
                60,
                "Current load may trigger grid charging",
                "high",
            )

        target_pct = float(
            self.entry.options.get(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET)
        )
        if data.soc < target_pct - 10:
            return (
                "REDUCE_LOAD",
                -0.5,
                60,
                f"Battery below target ({data.soc:.0f}% < {target_pct:.0f}%) - reduce discretionary load",
                "medium",
            )

        # INCREASE_LOAD conditions
        excess_until_full = excess_by_windows.get("excess_until_battery_full_kwh", 0)
        excess_next_2h = excess_by_windows.get("excess_next_2h_kwh", 0)

        if safe_additional_load > 0.5 and not grid_charge_risk:
            # Determine reason and duration
            if negative_fit_start is not None and excess_until_full > 2.0:
                duration = min(
                    int((negative_fit_start - dt_util.now()).total_seconds() / 60), 120
                )
                return (
                    "INCREASE_LOAD",
                    safe_additional_load,
                    max(duration, 30),
                    f"Excess solar: {excess_until_full:.1f}kWh before negative FIT at {negative_fit_start.strftime('%H:%M')}",
                    "high" if excess_next_2h > 3 else "medium",
                )

            if excess_until_full > 1.0:
                # Calculate duration based on fill point
                duration = min(fill_point_minutes or 60, 120)
                return (
                    "INCREASE_LOAD",
                    safe_additional_load,
                    duration,
                    f"Excess solar: {excess_until_full:.1f}kWh before battery full",
                    "medium",
                )

            if excess_next_2h > 2.0:
                return (
                    "INCREASE_LOAD",
                    min(safe_additional_load, excess_next_2h / 2),
                    60,
                    f"Excess solar: {excess_next_2h:.1f}kWh available in next 2 hours",
                    "medium",
                )

        # Default: MAINTAIN_LOAD
        return (
            "MAINTAIN_LOAD",
            0.0,
            0,
            "Current balance is optimal",
            "medium",
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
        all_solcast: list[dict] | None = None,
        historical_avg_kw: dict[int, float] | None = None,
        current_load_kw: float = 0.0,
        recent_load_kw: float = 0.0,
        is_current_slot: bool = False,
        current_elapsed_minutes: float = 0,
        fill_point_elapsed_minutes: int | None = None,
        negative_fit_headroom_kwh: float = 0.0,
        in_negative_fit_window: bool = False,
    ) -> tuple[bool, float]:
        """Determine if proactive export should happen at this slot.

        Proactive export has TWO modes:

        MODE 1 - NEGATIVE FIT AVOIDANCE (Priority):
        When there's a negative FIT window coming, export NOW at positive FIT
        to create headroom for solar that would otherwise export at negative prices.
        This maximizes revenue by avoiding negative FIT losses.

        MODE 2 - PRICE ARBITRAGE (Fallback):
        When no negative FIT window, export at above-average FIT prices before
        the battery fills (price comparison with fill-time price).

        Strategy:
        1. NEGATIVE FIT AVOIDANCE: If negative FIT window coming and FIT > 0 now,
           export to create headroom = solar_during_negative_fit - battery_space_at_fill
        2. PRICE ARBITRAGE: If current FIT > fill-time FIT, export for arbitrage
        3. Always respect minimum SOC (never dip below configured reserve)
        4. Only export when FIT is strictly positive (> $0.00)

        Args:
            slot_start: Start time of 15-minute slot
            slot_hour: Hour of slot
            solar_kwh: Solar forecast for this slot
            slot_fit_price: Feed-in price for this slot (from forecast)
            predicted_soc: Predicted SOC at start of slot
            target_pct: Target SOC percentage (battery target)
            in_demand_window: True if in demand window
            forecasted_excess_kwh: Total excess solar forecasted
            remaining_export_budget_kwh: Exportable energy remaining in budget
            feed_in_forecast: Full FIT price forecast
            min_soc_no_exports: Minimum SOC over 24h without proactive exports
            export_min_soc_pct: Minimum SOC threshold for exports (from config)
            effective_cheap_price: The effective cheap price threshold (not used for
                negative FIT avoidance, only for price arbitrage mode)
            feed_in_price_current: Current spot feed-in price (only for current slot)
            all_solcast: Full Solcast forecast (for overnight simulation)
            historical_avg_kw: Historical hourly load profile (for overnight simulation)
            current_load_kw: Current load power (for overnight simulation)
            recent_load_kw: Recent 1-hour average load (for overnight simulation)
            is_current_slot: True if this is the current time slot (use spot price)
            current_elapsed_minutes: Minutes elapsed since forecast start
            fill_point_elapsed_minutes: Minutes until battery fills (or None)
            negative_fit_headroom_kwh: Headroom needed to avoid negative FIT exports
            in_negative_fit_window: True if currently in a negative FIT period

        Returns:
            (should_export, export_amount_kwh)
        """
        # SPOT PRICE: Only use for current slot (real-time decision)
        # For future slots, always use forecast price
        if is_current_slot and feed_in_price_current > 0:
            # Current slot with positive spot price - use it for real-time decision
            use_price = feed_in_price_current
            _LOGGER.debug(
                "PROACTIVE_EXPORT: Using spot price $%.2f for current slot (forecast: $%.2f)",
                feed_in_price_current,
                slot_fit_price,
            )
        else:
            # Future slot or spot unavailable - use forecast-based logic
            use_price = slot_fit_price

        # ========================================================================
        # MODE 1: NEGATIVE FIT AVOIDANCE (Priority)
        # ========================================================================
        # If there's a negative FIT window coming and we need headroom,
        # export NOW at positive FIT to avoid exporting later at negative FIT.
        # This is the primary strategy for high-solar days.

        if negative_fit_headroom_kwh > 0 and not in_negative_fit_window:
            # FIT must be strictly positive to export (preserve battery cycles)
            if use_price <= 0:
                _LOGGER.debug(
                    "PROACTIVE_EXPORT: %02d:%02d BLOCKED - FIT $%.3f <= 0 (negative FIT avoidance mode)",
                    slot_hour,
                    slot_start.minute,
                    use_price,
                )
                return False, 0.0

            # Must have SOC above minimum to export
            if predicted_soc <= export_min_soc_pct:
                _LOGGER.debug(
                    "PROACTIVE_EXPORT: %02d:%02d BLOCKED - SOC %.1f%% <= min %.1f%% (negative FIT avoidance)",
                    slot_hour,
                    slot_start.minute,
                    predicted_soc,
                    export_min_soc_pct,
                )
                return False, 0.0

            # Calculate how much we can export (above minimum SOC)
            available_above_min_kwh = (
                (predicted_soc - export_min_soc_pct) / 100 * BATTERY_CAPACITY_KWH
            )

            # Export amount = min(headroom needed, available above min, max rate)
            max_export_rate_kwh = 8.7 / 4  # 2.175 kWh per 15 min slot
            export_amount = min(
                negative_fit_headroom_kwh,
                available_above_min_kwh,
                max_export_rate_kwh,
            )

            if export_amount > 0:
                _LOGGER.info(
                    "PROACTIVE_EXPORT: %02d:%02d NEGATIVE_FIT_AVOIDANCE - FIT=$%.3f, headroom=%.2f kWh, exporting=%.3f kWh, SOC=%.1f%%",
                    slot_hour,
                    slot_start.minute,
                    use_price,
                    negative_fit_headroom_kwh,
                    export_amount,
                    predicted_soc,
                )
                return True, round(export_amount, 3)

            return False, 0.0

        # ========================================================================
        # MODE 2: PRICE ARBITRAGE (Fallback - when no negative FIT window)
        # ========================================================================
        # Original logic: export if current price > fill-time price
        # This is for price arbitrage opportunities when there's no negative FIT to avoid.

        # If we're already in a negative FIT window, don't do price arbitrage
        if in_negative_fit_window:
            return False, 0.0

        # ABOVE-TARGET GATE: Only export when battery is at or above the target SOC.
        # Exporting from a battery below target worsens the deficit and forces solar
        # to spend time refilling exported energy instead of reaching the target.
        # Allow a small 2% hysteresis to avoid blocking exports right at the boundary.
        if predicted_soc < (target_pct - 2.0):
            _LOGGER.debug(
                "PROACTIVE_EXPORT: %02d:%02d BLOCKED - SOC %.1f%% < target %.1f%% (below target, not exporting)",
                slot_hour,
                slot_start.minute,
                predicted_soc,
                target_pct,
            )
            return False, 0.0

        # During demand window: allow export but use dynamic floor protection.
        # The existing checks below (min_soc, buffer, ending SOC) provide adequate
        # protection by ensuring we keep enough SOC to cover remaining DW hours.
        # This allows profitable exports during price spikes while protecting coverage.

        # Need buffer in battery (minimum reserve from config)
        if predicted_soc <= export_min_soc_pct:
            return False, 0.0

        # Critical: Don't export if battery will already drop below threshold without exports
        # This prevents draining of battery too low
        if min_soc_no_exports < export_min_soc_pct:
            _LOGGER.debug(
                "PROACTIVE_EXPORT: BLOCKED - min SOC without exports (%.1f%%) < %.1f%%",
                min_soc_no_exports,
                export_min_soc_pct,
            )
            return False, 0.0

        # Additional safety: Need some buffer above minimum
        # This provides a small safety margin for forecast uncertainty
        # Note: Overnight drain is separately simulated, so this is just a quick check
        required_buffer_pct = 5.0  # 5% extra buffer above minimum
        if predicted_soc < (export_min_soc_pct + required_buffer_pct):
            _LOGGER.debug(
                "PROACTIVE_EXPORT: BLOCKED - SOC %.1f%% < buffer (%.1f%% + %.1f%%)",
                predicted_soc,
                export_min_soc_pct,
                required_buffer_pct,
            )
            return False, 0.0

        # Only export if we have forecasted excess (not just current SOC)
        # This prevents exporting when we might need to charge later
        if forecasted_excess_kwh <= 0:
            return False, 0.0

        # FILL-POINT BASED EXPORT STRATEGY:
        # Only export BEFORE the battery would naturally fill from solar
        # This ensures we have room to capture incoming solar after export

        # CONSTRAINT 1: Only export if battery will fill at some point
        if fill_point_elapsed_minutes is None:
            _LOGGER.debug(
                "PROACTIVE_EXPORT: %02d:%02d BLOCKED - battery never fills from solar",
                slot_hour,
                slot_start.minute,
            )
            return False, 0.0

        # CONSTRAINT 2: Only export BEFORE the fill point
        # After the battery fills, there's no room for more solar
        if current_elapsed_minutes >= fill_point_elapsed_minutes:
            _LOGGER.debug(
                "PROACTIVE_EXPORT: %02d:%02d BLOCKED - elapsed %.1f min >= fill point %d min",
                slot_hour,
                slot_start.minute,
                current_elapsed_minutes,
                fill_point_elapsed_minutes,
            )
            return False, 0.0

        # CONSTRAINT 3: Verify enough solar AFTER export to reach fill point
        # Calculate solar energy available between now and fill point
        if all_solcast is not None and historical_avg_kw is not None:
            solar_until_fill = self._calculate_solar_energy_between_slots(
                start_elapsed_minutes=current_elapsed_minutes,
                end_elapsed_minutes=fill_point_elapsed_minutes,
                base_slot=slot_start - timedelta(minutes=current_elapsed_minutes),
                all_solcast=all_solcast,
                historical_avg_kw=historical_avg_kw,
                current_load_kw=current_load_kw,
                recent_load_kw=recent_load_kw,
            )

            # Need enough solar to recharge what we export
            # If we export X kWh, we need X kWh of solar to get back to fill point
            # Note: solar_until_fill already accounts for charging efficiency
            max_export_allowed = solar_until_fill * 0.9  # 10% safety margin

            if max_export_allowed <= 0:
                _LOGGER.debug(
                    "PROACTIVE_EXPORT: %02d:%02d BLOCKED - no solar to recharge before fill",
                    slot_hour,
                    slot_start.minute,
                )
                return False, 0.0

        # Calculate hours until fill point (for price window calculation)
        hours_until_fill = (
            (fill_point_elapsed_minutes - current_elapsed_minutes) / 60
            if fill_point_elapsed_minutes is not None
            else 6
        )

        # Never proactive-export into a non-positive FIT.
        if use_price <= 0:
            return False, 0.0

        # KEY INSIGHT: Once the battery fills, surplus solar is exported automatically.
        # So the question is: "Should I export NOW at current price, or wait and export
        # the surplus at fill-time price?"
        #
        # Get the FIT price AT the fill point - this is what we'd get if we wait.
        # If current price < fill-time price: DON'T export (you'll get more at fill time)
        # If current price > fill-time price: Export now (better than waiting)
        fill_time = slot_start + timedelta(
            minutes=fill_point_elapsed_minutes - current_elapsed_minutes
        )
        fill_time_price = get_price_for_slot(feed_in_forecast, fill_time)

        # If we can't get fill-time price, fall back to max in window
        if fill_time_price is None or fill_time_price <= 0:
            hours_for_price_lookup = min(max(int(hours_until_fill), 1), 24)
            max_fit_price_before_fill = self._calculate_max_fit_price(
                feed_in_forecast, slot_start, hours=hours_for_price_lookup
            )
            fill_time_price = max_fit_price_before_fill
            _LOGGER.debug(
                "PROACTIVE_EXPORT: %02d:%02d fill-time price unavailable, using max=$%.2f",
                slot_hour,
                slot_start.minute,
                fill_time_price,
            )

        # Only export if current price >= fill-time price
        # (because surplus will export at fill time anyway, so we should only
        # export now if we get a better price than waiting)
        if use_price < fill_time_price:
            _LOGGER.debug(
                "PROACTIVE_EXPORT: %02d:%02d BLOCKED - current $%.2f < fill-time $%.2f (fill at %s, hours_until_fill=%.1f)",
                slot_hour,
                slot_start.minute,
                use_price,
                fill_time_price,
                fill_time.strftime("%H:%M"),
                hours_until_fill,
            )
            return False, 0.0

        # CRITICAL: Calculate total discharge (export + load) before deciding
        # If solar_kwh < consumption, battery is already discharging for load
        # Adding export discharge on top could drain battery too fast
        # Calculate consumption from slot data (need to estimate it here)
        # Since we don't have consumption_kwh passed in, estimate from solar and net
        # For now, assume no load discharge during solar hours, only overnight
        net_discharge_kwh = 0.0
        if solar_kwh < 0.001:  # No solar - overnight hours
            # Estimate consumption: typical overnight load ~0.2-0.4 kWh per 15 min
            # This is conservative - actual value will vary
            net_discharge_kwh = 0.3 / 0.95  # 95% discharge efficiency

        # Calculate exportable amount from battery (capped by SOC and max rate)
        battery_exportable_kwh = (
            (predicted_soc - export_min_soc_pct) / 100 * BATTERY_CAPACITY_KWH
        )
        max_export_rate_kwh = 8.7 / 4  # 2.175 kWh per 15 min slot

        # Export amount = min(battery exportable, remaining budget, max rate)
        export_amount = min(
            battery_exportable_kwh,
            remaining_export_budget_kwh,
            max_export_rate_kwh,
        )

        # CRITICAL FIX: Check ending SOC after TOTAL discharge (export + load)
        # This prevents aggressive exports when battery is already discharging for load
        export_discharge_kwh = export_amount / 0.95  # 95% efficiency
        total_discharge_kwh = net_discharge_kwh + export_discharge_kwh
        soc_after_discharge = predicted_soc - (
            total_discharge_kwh / BATTERY_CAPACITY_KWH * 100
        )

        # Need buffer above minimum to account for continued overnight drain
        required_buffer_pct = 15.0  # 15% extra buffer
        min_safe_soc = export_min_soc_pct + required_buffer_pct

        if soc_after_discharge < min_safe_soc:
            # Don't export - would drop below safe level
            _LOGGER.debug(
                "PROACTIVE_EXPORT: %02d:%02d BLOCKED - ending SOC=%.1f%% < %.1f%% (load=%.3f + export=%.3f)",
                slot_hour,
                slot_start.minute,
                soc_after_discharge,
                min_safe_soc,
                net_discharge_kwh,
                export_discharge_kwh,
            )
            return False, 0.0

        # CRITICAL: Simulate overnight drain after this export
        # This ensures battery won't drop below minimum before solar starts
        if (
            all_solcast is not None
            and historical_avg_kw is not None
            and solar_kwh < 0.001
        ):  # Only for overnight slots (no solar)
            # Simulate from next slot until solar starts
            next_slot = slot_start + timedelta(minutes=15)
            min_overnight_soc, _, solar_found = (
                self._simulate_overnight_drain_after_export(
                    start_soc=soc_after_discharge,
                    start_slot=next_slot,
                    all_solcast=all_solcast,
                    historical_avg_kw=historical_avg_kw,
                    current_load_kw=current_load_kw,
                    recent_load_kw=recent_load_kw,
                    export_min_soc_pct=export_min_soc_pct,
                )
            )

            # Block exports if we can't see solar in the forecast
            # This happens in late forecast slots (e.g., last 6-8 hours of 24h window)
            if not solar_found:
                _LOGGER.debug(
                    "PROACTIVE_EXPORT: %02d:%02d BLOCKED - no solar visibility in forecast for overnight simulation",
                    slot_hour,
                    slot_start.minute,
                )
                return False, 0.0

            if min_overnight_soc < export_min_soc_pct:
                _LOGGER.debug(
                    "PROACTIVE_EXPORT: %02d:%02d BLOCKED - overnight min SOC %.1f%% < %.1f%% after export",
                    slot_hour,
                    slot_start.minute,
                    min_overnight_soc,
                    export_min_soc_pct,
                )
                return False, 0.0

        # THROTTLING: Apply dynamic reserve (SOC - 5%) to limit export amount
        # This matches the actual throttling that will happen in battery_controller.py
        # The system will set reserve = max(4, SOC - 5), limiting each export session
        # to ~5% of battery capacity (~0.675 kWh per session)
        #
        # Per-session limit: 5% of battery capacity
        per_session_limit_kwh = 5.0 / 100 * BATTERY_CAPACITY_KWH  # ~0.675 kWh

        # Also respect minimum SOC floor
        available_above_min_kwh = max(
            0,
            (predicted_soc - export_min_soc_pct) / 100 * BATTERY_CAPACITY_KWH,
        )

        # Throttled export = min(session limit, available above min)
        throttled_exportable_kwh = min(per_session_limit_kwh, available_above_min_kwh)

        # Re-apply the export limit with throttling
        export_amount = min(
            export_amount,
            throttled_exportable_kwh,
        )

        if export_amount > 0:
            _LOGGER.debug(
                "PROACTIVE_EXPORT: %02d:%02d price=$%.2f >= fill-time $%.2f, amount=%.3f kWh, ending_soc=%.1f%%",
                slot_hour,
                slot_start.minute,
                use_price,
                fill_time_price,
                export_amount,
                soc_after_discharge,
            )
            return True, round(export_amount, 3)

        return False, 0.0

    def compute_forecast(
        self,
        data: CoordinatorData,
        now_dt: datetime,
        historical_avg_kw: dict[int, float],
        recent_load_kw: float,
        historical_load_source: str,
        historical_load_sample_counts: dict[int, int],
    ) -> tuple[list[dict], list[list], dict[str, int]]:
        """Compute full 24-hour forecast with 15-minute breakdown.

        Provides 4x granularity over hourly forecast, capturing meaningful
        price variations from 5-minute pricing data.

        Returns:
            tuple of (daily_forecast, daily_forecast_soc_15min, consumption_source_counts)
        """
        daily_forecast = []
        daily_forecast_soc_15min = []
        consumption_source_counts = {}

        # Get all Solcast forecasts
        all_solcast = [*data.solcast_today, *data.solcast_tomorrow]

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
        data.consumption_weighting = float(
            self.entry.options.get(CONF_LOAD_WEIGHT_RECENT, DEFAULT_LOAD_WEIGHT_RECENT)
        )

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
        # CALCULATE FORECASTED EXCESS AND MINIMUM SOC FOR PROACTIVE EXPORT
        # ========================================================================
        # Sum all solar - consumption for full 24-hour forecast
        # This tells us if we'll have excess energy that should be exported
        # before feed-in prices go negative
        forecasted_excess_kwh = 0.0
        current_kwh = current_soc / 100 * BATTERY_CAPACITY_KWH
        space_to_target_kwh = max(target_kwh - current_kwh, 0)

        # Calculate excess for full 24-hour window (all 96 slots)
        # This includes both today's remaining hours and tomorrow's solar production
        for offset in range(96):
            slot_start = base_slot + timedelta(minutes=15 * offset)
            slot_hour = slot_start.hour

            solar_kwh = get_solar_for_15min_slot(all_solcast, slot_start)
            load_kw, _ = self._estimate_hourly_consumption_kw(
                historical_avg_kw,
                slot_hour,
                current_hour,
                data.load_power_kw,
                recent_load_kw,
            )
            consumption_kwh = load_kw / 4
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

        # ========================================================================
        # NEGATIVE FIT AVOIDANCE: Calculate headroom needed
        # ========================================================================
        # Find negative FIT windows and calculate how much solar will be produced
        # during those windows. This tells us how much headroom we need to create
        # by exporting proactively at positive FIT prices.
        negative_fit_windows = self._find_negative_fit_windows(
            data.feed_in_forecast, base_slot, max_hours=24
        )

        # Calculate fill point datetime for solar calculation
        fill_point_datetime = None
        if fill_point_elapsed_minutes is not None:
            fill_point_datetime = base_slot + timedelta(
                minutes=fill_point_elapsed_minutes
            )

        # Calculate solar energy during negative FIT windows (only after fill point)
        solar_during_negative_fit_kwh = self._calculate_solar_during_negative_fit(
            base_slot=base_slot,
            negative_fit_windows=negative_fit_windows,
            all_solcast=all_solcast,
            historical_avg_kw=historical_avg_kw,
            current_load_kw=data.load_power_kw,
            recent_load_kw=recent_load_kw,
            current_hour=current_hour,
            fill_point_datetime=fill_point_datetime,
        )

        # Calculate battery space at fill point (0% space when full)
        # If battery fills at 100%, there's no space for solar during negative FIT
        battery_space_at_fill_kwh = 0.0  # At 100% SOC, no space

        # Headroom needed = solar during negative FIT - battery space at fill
        # This is the amount we need to export BEFORE negative FIT arrives
        negative_fit_headroom_kwh = max(
            0, solar_during_negative_fit_kwh - battery_space_at_fill_kwh
        )

        if negative_fit_windows:
            _LOGGER.info(
                "NEGATIVE_FIT_AVOIDANCE: %d window(s) found, solar during negative FIT=%.2f kWh, headroom needed=%.2f kWh",
                len(negative_fit_windows),
                solar_during_negative_fit_kwh,
                negative_fit_headroom_kwh,
            )
        else:
            _LOGGER.info("No negative FIT windows found in next 24 hours")

        # ========================================================================
        # 15-MIN FORECAST: 96 × 15-min slots for full 24-hour coverage
        #
        # Uses uniform 15-min slots throughout for consistent alignment with
        # Solcast 30-minute periods. This eliminates the complexity of hybrid
        # timescales and ensures all SOC predictions are consistent across
        # the main loop and simulation functions.
        # ========================================================================

        # Read minimum SOC once before the loop (used for SOC floor and grid charging simulation)
        export_min_soc_pct = float(
            self.entry.options.get(CONF_MINIMUM_TARGET_SOC, DEFAULT_MINIMUM_TARGET_SOC)
        )
        remaining_export_budget = export_budget_kwh
        slot_fraction = 15 / 60.0  # 0.25 hours

        for slot_idx in range(TOTAL_SLOTS):
            slot_start = base_slot + timedelta(minutes=15 * slot_idx)
            is_first_slot = slot_idx == 0

            slot_hour = slot_start.hour
            slot_minute = slot_start.minute
            slot_time = slot_start.time()

            # SOC at the start of this slot for any decision-making.
            # For the first slot, use actual SOC. For later slots, use the rolling forecast SOC.
            soc_at_slot_start = current_soc if is_first_slot else predicted_soc

            # Check if we're in demand window (zero grid import constraint)
            in_demand_window = dw_start_time <= slot_time < dw_end_time

            # Get solar forecast scaled to this 15-min slot's duration.
            # Enable debug logging for: first slot, 6-hour marks, and afternoon slots (14-18)
            debug_this_slot = (
                is_first_slot
                or (slot_minute == 0 and slot_hour % 6 == 0)
                or (14 <= slot_hour <= 18)
            )

            # Use _or_none variant to detect missing forecast data vs genuine zero
            solar_kwh_or_none = get_solar_for_15min_slot_or_none(
                all_solcast, slot_start
            )
            if solar_kwh_or_none is None:
                # No forecast data for this slot - track for diagnostics
                missing_solar_slots.append(slot_start.strftime("%H:%M"))
                solar_kwh = 0.0  # Graceful degradation
            else:
                solar_kwh = solar_kwh_or_none

            # Debug logging for solar data
            if debug_this_slot:
                get_solar_for_15min_slot(all_solcast, slot_start, debug_log=True)

            # Get expected consumption scaled to this slot's duration.
            load_kw, load_source = self._estimate_hourly_consumption_kw(
                historical_avg_kw,
                slot_hour,
                current_hour,
                data.load_power_kw,
                recent_load_kw,
            )
            if load_source != "profile_hour":
                data.consumption_fallback_hours += 1
            consumption_source_counts[load_source] = (
                consumption_source_counts.get(load_source, 0) + 1
            )
            consumption_kwh = load_kw * slot_fraction

            # Calculate raw net energy for this slot
            net_kwh = solar_kwh - consumption_kwh

            # Get slot price for logging/analysis
            _slot_price = get_price_for_slot(data.general_forecast, slot_start)

            # Determine if we should grid charge using single source of truth
            gap_to_target = max(target_pct - soc_at_slot_start, 0)

            # A slot is eligible for pre-DW grid charging only if it falls before
            # the daily demand-window start hour. Use proper datetime comparison
            # via _next_demand_window_start_dt() which correctly handles day boundaries.
            # (Fixes is_before_dw wrap-around bug — Issue 4 in MODE_SWITCHING_DELAY_ANALYSIS.md)
            # (backlog-high-019: Previous `slot_hour < target_hour` failed for evening slots)
            next_dw_start = self._next_demand_window_start_dt(slot_start, dw_start_time)
            is_before_dw = slot_start < next_dw_start

            # is_daylight is kept for the method signature but no longer used as a gate.
            # Grid charging decisions are independent of solar availability.
            is_daylight = solar_kwh > 0.05

            # Scale charge-rate caps to this slot's duration
            max_solar_charge_kwh = CHARGE_RATE_SOLAR_KW * slot_fraction
            max_grid_charge_kwh = CHARGE_RATE_BACKUP_KW * slot_fraction

            # Use single source of truth for grid charging decision
            # Get allow_dw_entry_under_target from data (set by computation_engine)
            allow_dw_entry_under_target = getattr(
                data, "allow_dw_entry_under_target", False
            )

            # HYSTERESIS: Use actual charging state from Teslemetry for stickiness
            # This prevents flip-flopping when forecast is optimistic but real conditions differ
            is_currently_grid_charging = getattr(data, "force_charge_active", False)

            should_grid_charge, should_boost = self._should_grid_charge_at_slot(
                slot_start=slot_start,
                solar_kwh=solar_kwh,
                slot_price=_slot_price,
                predicted_soc=soc_at_slot_start,
                target_pct=target_pct,
                effective_cheap_price=data.effective_cheap_price,
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
            )

            # Debug logging for charging decision
            _LOGGER.debug(
                "GRID_CHARGE[15min]: %02d:%02d in_dw=%s before_dw=%s soc=%.1f<%d gap=%d -> charge=%s boost=%s",
                slot_hour,
                slot_minute,
                in_demand_window,
                is_before_dw,
                soc_at_slot_start,
                target_pct,
                gap_to_target,
                should_grid_charge,
                should_boost,
            )

            # Determine boost / urgency upgrade for grid charging rate
            next_dw_start = self._next_demand_window_start_dt(slot_start, dw_start_time)
            hours_to_dw = (next_dw_start - slot_start).total_seconds() / 3600
            if should_boost:
                max_grid_charge_kwh = CHARGE_RATE_BOOST_KW * slot_fraction
            elif should_grid_charge and hours_to_dw < 2:
                max_grid_charge_kwh = CHARGE_RATE_BOOST_KW * slot_fraction

            # Check if battery will be at or above 100% after solar charging
            battery_at_or_above_cap = (
                predicted_soc + (net_kwh / BATTERY_CAPACITY_KWH * 100)
            ) >= 100.0

            # Step 1: Calculate base battery delta from solar/load
            if net_kwh >= 0:
                battery_delta_kwh = min(net_kwh, max_solar_charge_kwh) * 0.92
            else:
                battery_delta_kwh = max(net_kwh, -max_solar_charge_kwh) / 0.95

            # Step 2: Add grid charging if needed (INDEPENDENT of solar!)
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

            # Step 3: Check for proactive export (before updating SOC).
            # Calculate elapsed minutes from base_slot for hybrid timescale comparisons
            elapsed_minutes = (slot_start - base_slot).total_seconds() / 60

            _slot_fit_price = get_price_for_slot(data.feed_in_forecast, slot_start)

            # Check if this slot is in a negative FIT window
            in_negative_fit_window = False
            for win_start, win_end, _ in negative_fit_windows:
                if win_start <= slot_start < win_end:
                    in_negative_fit_window = True
                    break

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
                    all_solcast=all_solcast,
                    historical_avg_kw=historical_avg_kw,
                    current_load_kw=data.load_power_kw,
                    recent_load_kw=recent_load_kw,
                    is_current_slot=is_first_slot,
                    current_elapsed_minutes=elapsed_minutes,
                    fill_point_elapsed_minutes=fill_point_elapsed_minutes,
                    negative_fit_headroom_kwh=negative_fit_headroom_kwh,
                    in_negative_fit_window=in_negative_fit_window,
                )
            )

            # Apply proactive export if needed (discharge battery)
            if should_proactive_export:
                export_discharge_kwh = proactive_export_amount / 0.95
                battery_delta_kwh -= export_discharge_kwh
                grid_export_kwh = proactive_export_amount
                remaining_export_budget -= proactive_export_amount

                _LOGGER.debug(
                    "PROACTIVE_EXPORT: %02d:%02d amount=%.3f kWh, remaining budget=%.3f kWh",
                    slot_hour,
                    slot_minute,
                    proactive_export_amount,
                    remaining_export_budget,
                )
            else:
                # Step 3a: Calculate normal grid export
                if net_kwh >= 0:
                    if battery_at_or_above_cap:
                        # Battery full: all solar net goes to grid
                        grid_export_kwh = net_kwh
                    else:
                        # Battery has space.  Only the rate-limited portion (net
                        # solar above the battery's max charge rate) goes to grid.
                        # The * 0.92 charging efficiency loss is heat, NOT grid export.
                        grid_export_kwh = max(0.0, net_kwh - max_solar_charge_kwh)
                else:
                    # Negative net: battery discharges to cover load deficit.
                    # The discharge_kwh = load_deficit / 0.95 (efficiency), but the
                    # 5% difference is thermal loss, not grid export.  In self-
                    # consumption mode there is no grid export during discharge.
                    grid_export_kwh = 0.0

            # Update rolling SOC prediction.
            # First slot: preserve current SOC (no time-based delta applied).
            if is_first_slot:
                new_predicted_soc = current_soc
            else:
                new_predicted_soc = predicted_soc + (
                    battery_delta_kwh / BATTERY_CAPACITY_KWH * 100
                )

            # Minimum SOC floor: if battery would discharge below configured minimum,
            # the inverter stops discharging and load must come from the grid instead.
            if (
                not is_first_slot
                and not should_grid_charge
                and new_predicted_soc < export_min_soc_pct
            ):
                shortfall_pct = export_min_soc_pct - new_predicted_soc
                passive_grid_import_kwh = shortfall_pct / 100 * BATTERY_CAPACITY_KWH
                grid_import_kwh += passive_grid_import_kwh
                new_predicted_soc = export_min_soc_pct

                # (backlog-high-022) Apply solar charging at minimum SOC:
                # If there's solar excess after load, charge the battery above minimum.
                # This prevents SOC from staying flat at minimum when solar > load.
                if net_kwh > 0 and not in_demand_window:
                    # Solar exceeds load - charge battery with excess
                    excess_kwh = net_kwh
                    charge_delta = min(excess_kwh, max_solar_charge_kwh) * 0.92
                    new_predicted_soc += charge_delta / BATTERY_CAPACITY_KWH * 100
                    # Reduce grid import since solar is covering load
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
        # Calculate expected costs for the remainder of today (until midnight)
        # This includes: grid import cost, grid export revenue, and breakdown by type

        # Find end of today (midnight)
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

            # Only include slots from now until end of today
            if slot_dt > end_of_today:
                break

            grid_import_kwh = slot.get("grid_import_kwh", 0) or 0
            grid_export_kwh = slot.get("grid_export_kwh", 0) or 0
            buy_price = slot.get("buy_price", 0) or 0
            sell_price = slot.get("sell_price", 0) or 0
            is_grid_charge = slot.get("grid_charge", False)
            is_proactive_export = slot.get("proactive_export", False)

            # Accumulate costs
            forecast_import_cost += grid_import_kwh * buy_price
            forecast_export_revenue += grid_export_kwh * sell_price

            # Track grid charge cost separately (energy used to charge battery from grid)
            if is_grid_charge:
                forecast_grid_charge_cost += grid_import_kwh * buy_price

            # Track proactive export revenue separately
            if is_proactive_export:
                forecast_proactive_export_revenue += grid_export_kwh * sell_price

        # Store forecast cost totals in data object
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

"""SOC simulation helpers for forecast computation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta

from homeassistant.util import dt as dt_util

from ..const import (
    BATTERY_CAPACITY_KWH,
    CHARGE_RATE_GRID_KW,
    CHARGE_RATE_SOLAR_KW,
)
from .price_calculator import get_price_for_slot
from .solar_utils import get_solar_for_15min_slot, get_solar_for_slot_by_interval


class SocSimulator:
    """Simulate SOC trajectories under different scenarios."""

    def __init__(
        self,
        estimate_hourly_consumption_kw: Callable[..., tuple[float, str]],
    ) -> None:
        """Initialize simulator with load estimation callback."""
        self._estimate_hourly_consumption_kw = estimate_hourly_consumption_kw

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
        hybrid_slots: list[dict] | None = None,
    ) -> tuple[float, float, bool, bool]:
        """Simulate future SOC trajectory with solar only (no grid charging).

        Issue #329: Supports hybrid timescale with variable slot durations.
        Falls back to 15-min slots when hybrid_slots is not provided.

        When end_time == dw_start_time, simulation stops at DW start (existing behavior).
        When end_time > dw_start_time, simulation continues through DW period.

        FIX FOR DW SIMULATION: When simulating through DW period, we need to check
        if SOC reaches target DURING the relevant period (DW), not just at any point
        during the simulation. Previously, max_soc could peak at midday and then
        decline before DW, but the simulation would say "target reached" incorrectly.

        This helps determine if grid charging is necessary.

        CRITICAL for Issue #137: When baseline_avg_kw is provided, use it instead
        of historical_avg_kw for load estimation. This prevents the chicken-and-egg
        feedback loop where:
        1. HVAC turns on → load increases
        2. System forecasts higher consumption using historical (with HVAC spikes)
        3. System triggers grid charging unnecessarily
        4. Energy wasted instead of using solar surplus

        By using baseline (non-HVAC) load for grid charging decisions, we ensure
        that discretionary HVAC load doesn't trigger unnecessary grid charging.

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
            hybrid_slots: Optional list of hybrid slots with variable durations.
                         Each slot has 'start' (datetime) and 'interval_minutes' (int).

        Returns:
            (soc_at_end_pct, max_soc_pct, can_reach_target, was_truncated)
        """
        soc = actual_current_soc
        base_slot = start_slot.replace(second=0, microsecond=0)

        # ISSUE #137: Use baseline load for grid charging decisions
        # When baseline_avg_kw is provided, use it instead of historical_avg_kw
        # This prevents the feedback loop where HVAC spikes trigger grid charging
        load_profile = baseline_avg_kw if baseline_avg_kw else historical_avg_kw

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
        truncated = False
        if solcast_end is not None and sim_end > solcast_end:
            sim_end = solcast_end
            truncated = True

        if sim_end <= base_slot:
            return soc, soc, soc >= target_pct, truncated

        max_soc = soc
        max_soc_in_dw = soc  # Track max SOC specifically during DW period

        # Determine DW period boundaries for max_soc_in_dw tracking
        dw_start_dt = base_slot.replace(
            hour=dw_start_time.hour,
            minute=dw_start_time.minute,
            second=0,
            microsecond=0,
        )
        if dw_start_dt <= base_slot:
            dw_start_dt += timedelta(days=1)

        # Calculate DW end time
        if dw_end_time is not None:
            dw_end_dt = base_slot.replace(
                hour=dw_end_time.hour,
                minute=dw_end_time.minute,
                second=0,
                microsecond=0,
            )
            if dw_end_dt <= dw_start_dt:
                dw_end_dt += timedelta(days=1)
        else:
            dw_end_dt = dw_start_dt + timedelta(hours=6)  # Default 6-hour DW

        if hybrid_slots:
            # Hybrid mode: use variable slot durations
            for slot in hybrid_slots:
                slot_start = slot["start"]
                interval_minutes = slot["interval_minutes"]
                slot_fraction = interval_minutes / 60.0

                # Stop if we've reached the end time
                if slot_start >= sim_end:
                    break

                slot_hour = slot_start.hour
                hours_ahead = (slot_start - base_slot).total_seconds() / 3600

                # Use variable-duration solar function
                solar_kwh = get_solar_for_slot_by_interval(
                    all_solcast, slot_start, interval_minutes
                )

                # ISSUE #137: Use baseline load profile when provided
                load_kw, _ = self._estimate_hourly_consumption_kw(
                    load_profile,
                    slot_hour,
                    current_hour,
                    current_load_kw,
                    recent_load_kw,
                    hours_ahead=hours_ahead,
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

                # Track max_soc specifically during DW period
                if dw_start_dt <= slot_start < dw_end_dt:
                    max_soc_in_dw = max(max_soc_in_dw, soc)
        else:
            # Legacy mode: fixed 15-min slots
            slot_fraction = 15 / 60.0  # 0.25 hours

            slot_time = base_slot
            while slot_time < sim_end:
                slot_time += timedelta(minutes=15)
                slot_hour = slot_time.hour
                hours_ahead = (slot_time - base_slot).total_seconds() / 3600

                # Get solar and load for this 15-min slot
                solar_kwh = get_solar_for_15min_slot(all_solcast, slot_time)

                # ISSUE #137: Use baseline load profile when provided
                load_kw, _ = self._estimate_hourly_consumption_kw(
                    load_profile,
                    slot_hour,
                    current_hour,
                    current_load_kw,
                    recent_load_kw,
                    hours_ahead=hours_ahead,
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

                # Track max_soc specifically during DW period
                # This is critical for allow_dw_entry_under_target logic
                if dw_start_dt <= slot_time < dw_end_dt:
                    max_soc_in_dw = max(max_soc_in_dw, soc)

        # FIX: When simulating through DW period (allow_dw_entry_under_target=True),
        # check if max_soc DURING DW reaches target, not just max_soc during entire simulation.
        # This prevents false positives where SOC peaks at midday but declines before DW.
        if end_time > dw_start_dt:
            # Simulation went through DW period - check DW-specific max_soc
            can_reach = max_soc_in_dw >= target_pct
            return soc, max_soc, can_reach, truncated

        return soc, max_soc, max_soc >= target_pct, truncated

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
            hours_ahead = (slot_time - base_slot).total_seconds() / 3600

            # Get solar (should be ~0 overnight) and load
            solar_kwh = get_solar_for_15min_slot(all_solcast, slot_time)

            load_kw, _ = self._estimate_hourly_consumption_kw(
                historical_avg_kw,
                slot_hour,
                None,
                current_load_kw,
                recent_load_kw,
                hours_ahead=hours_ahead,
            )
            consumption_kwh = load_kw * slot_fraction
            net_kwh = solar_kwh - consumption_kwh

            # Apply battery discharge
            max_slot_transfer_kwh = CHARGE_RATE_GRID_KW * slot_fraction
            if net_kwh >= 0:
                delta = min(net_kwh, max_slot_transfer_kwh) * 0.92
            else:
                delta = max(net_kwh, -max_slot_transfer_kwh) / 0.95

            soc += delta / BATTERY_CAPACITY_KWH * 100
            soc = max(min_soc_pct, min(100.0, soc))

            slot_time += timedelta(minutes=15)

        return soc

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
            hours_ahead = i / 4.0

            # Get solar and load for this 15-min slot
            solar_kwh = get_solar_for_15min_slot(all_solcast, slot_time)

            load_kw, _ = self._estimate_hourly_consumption_kw(
                historical_avg_kw,
                slot_hour,
                None,
                current_load_kw,
                recent_load_kw,
                hours_ahead=hours_ahead,
            )
            consumption_kwh = load_kw * slot_fraction
            net_kwh = solar_kwh - consumption_kwh

            # Apply realistic battery limits (no grid charging in this simulation)
            max_slot_transfer_kwh = CHARGE_RATE_GRID_KW * slot_fraction

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

        # If no solar found, simulate 12 hours (typical overnight period)
        if solar_start_slot is None:
            solar_start_slot = base_slot + timedelta(hours=12)

        # Use 15-min slots throughout for consistency
        slot_fraction = 15 / 60.0  # 0.25 hours

        slot_time = base_slot
        while slot_time < solar_start_slot:
            slot_hour = slot_time.hour
            hours_ahead = (slot_time - base_slot).total_seconds() / 3600

            # Get solar (should be ~0 overnight) and load
            solar_kwh = get_solar_for_15min_slot(all_solcast, slot_time)

            load_kw, _ = self._estimate_hourly_consumption_kw(
                historical_avg_kw,
                slot_hour,
                None,
                current_load_kw,
                recent_load_kw,
                hours_ahead=hours_ahead,
            )
            consumption_kwh = load_kw * slot_fraction
            net_kwh = solar_kwh - consumption_kwh

            # Apply battery discharge (negative net = discharge)
            max_slot_transfer_kwh = CHARGE_RATE_GRID_KW * slot_fraction
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
            hours_ahead = offset / 4.0

            solar_kwh = get_solar_for_15min_slot(all_solcast, slot_start)
            load_kw, _ = self._estimate_hourly_consumption_kw(
                historical_avg_kw,
                slot_hour,
                current_hour,
                current_load_kw,
                recent_load_kw,
                hours_ahead=hours_ahead,
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
        from .slot_schedule import TOTAL_SLOTS  # noqa: PLC0415

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
                hours_ahead = (slot_start - base_slot).total_seconds() / 3600

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
                    hours_ahead=hours_ahead,
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
                hours_ahead = i / 4.0

                solar_kwh = get_solar_for_15min_slot(all_solcast, slot_start)
                load_kw, _ = self._estimate_hourly_consumption_kw(
                    historical_avg_kw,
                    slot_hour,
                    current_hour,
                    current_load_kw,
                    recent_load_kw,
                    hours_ahead=hours_ahead,
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

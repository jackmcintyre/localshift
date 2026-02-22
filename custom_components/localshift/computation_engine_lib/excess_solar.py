"""Excess-solar and load-shifting helpers for forecast computation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from ..const import (
    BATTERY_CAPACITY_KWH,
    CONF_BATTERY_TARGET,
    DEFAULT_BATTERY_TARGET,
)
from ..coordinator_data import CoordinatorData
from .solar_utils import get_price_for_slot_or_none, get_solar_for_15min_slot


class ExcessSolarEngine:
    """Compute excess-solar metrics and load-shift recommendations."""

    def __init__(
        self,
        entry: ConfigEntry,
        estimate_hourly_consumption_kw: Callable[..., tuple[float, str]],
        simulate_with_additional_load: Callable[..., bool],
    ) -> None:
        """Initialize engine dependencies."""
        self.entry = entry
        self._estimate_hourly_consumption_kw = estimate_hourly_consumption_kw
        self._simulate_with_additional_load = simulate_with_additional_load

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

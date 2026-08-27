"""SOC simulation helpers for forecast computation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta

from ..const import (
    BATTERY_CAPACITY_KWH,
    CHARGE_RATE_SOLAR_KW,
)
from ..forecast.solar import get_solar_for_15min_slot, get_solar_for_slot_by_interval
from .price_calculator import get_price_for_slot


class SocSimulator:
    """Simulate SOC trajectories under different scenarios."""

    def __init__(
        self,
        estimate_hourly_consumption_kw: Callable[..., tuple[float, str]],
    ) -> None:
        """Initialize simulator with load estimation callback."""
        self._estimate_hourly_consumption_kw = estimate_hourly_consumption_kw

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
        from custom_components.localshift.engine.slot_schedule import TOTAL_SLOTS

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

    def find_battery_fill_point(self, *args, **kwargs) -> int | None:
        """Public wrapper for battery fill point calculation."""
        return self._find_battery_fill_point(*args, **kwargs)

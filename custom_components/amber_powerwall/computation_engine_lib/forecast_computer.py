"""Forecast computer for battery SOC and grid interaction forecasting."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry

from ..const import (
    BATTERY_CAPACITY_KWH,
    CHARGE_RATE_BACKUP_KW,
    CHARGE_RATE_BOOST_KW,
    CONF_BATTERY_TARGET,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    CONF_LOAD_WEIGHT_RECENT,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_LOAD_WEIGHT_RECENT,
)
from ..coordinator_data import CoordinatorData
from .solar_utils import get_price_for_slot, get_solar_for_15min_slot

_LOGGER = logging.getLogger(__name__)


class ForecastComputer:
    """Computes 24-hour battery forecast with 15-minute breakdown."""

    def __init__(
        self,
        entry: ConfigEntry,
        get_entity_id_func: callable,
        get_historical_func: callable,
    ) -> None:
        """Initialize forecast computer.

        Args:
            entry: Config entry
            get_entity_id_func: Function to get entity IDs by config key
            get_historical_func: Function to get historical hourly averages
        """
        self.entry = entry
        self._get_entity_id = get_entity_id_func
        self._get_historical_hourly_averages = get_historical_func

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

        historical_raw = hourly_avg_kw.get(slot_hour) if hourly_avg_kw else None
        historical_kw = (
            float(historical_raw) if isinstance(historical_raw, int | float) else 0.0
        )

        # Check if we have valid historical data
        has_historical = historical_kw > 0

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
    ) -> tuple[bool, bool]:
        """Determine if grid charging should happen at this slot.

        Single source of truth for grid charging decisions.
        Used by both forecast simulation and mode control.

        Args:
            slot_start: Start time of the 15-minute slot
            solar_kwh: Solar forecast for this slot
            slot_price: Buy price for this slot
            predicted_soc: Predicted SOC at start of slot
            target_pct: Target SOC percentage
            effective_cheap_price: Cheap price threshold
            is_before_dw: True if before demand window
            in_demand_window: True if in demand window
            gap_to_target: How many percent to target
            is_daylight: True if solar_kwh > 0.05

        Returns:
            (should_charge, should_boost)
        """
        # Never charge during demand window
        if in_demand_window:
            return False, False

        # Never charge after demand window
        if not is_before_dw:
            return False, False

        # Must have solar (daylight) to charge
        if not is_daylight:
            return False, False

        # Already at target - no charging needed
        if gap_to_target <= 0:
            return False, False

        # Price-based decisions
        price_is_cheap = slot_price <= effective_cheap_price
        price_is_very_cheap = slot_price <= (effective_cheap_price * 0.8)

        # Very cheap: boost charge (stock up!)
        if price_is_very_cheap:
            return True, True

        # Cheap: normal charge
        if price_is_cheap:
            return True, False

        # Far from target: charge anyway (urgency)
        if gap_to_target > 10:
            return True, False

        # Wait for cheaper price
        return False, False

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
        price variations from Amber's 5-minute pricing data.

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
            _LOGGER.debug("15-min forecast: no Solcast entries available")
        if not historical_avg_kw:
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
            solar_kwh = get_solar_for_15min_slot(all_solcast, slot_start)

            # Get expected consumption (hourly_avg / 4 for 15-min)
            # Pass recent load for weighted forecasting
            load_kw, load_source = self._estimate_hourly_consumption_kw(
                historical_avg_kw,
                slot_hour,
                data.load_power_kw,
                recent_load_kw,
            )
            if load_source != "profile_hour":
                data.consumption_fallback_hours += 1
            consumption_source_counts[load_source] = (
                consumption_source_counts.get(load_source, 0) + 1
            )
            consumption_kwh = load_kw / 4  # 15-min is 1/4 of hour

            # Calculate raw net energy for 15 minutes
            net_kwh = solar_kwh - consumption_kwh

            # Get slot price for logging/analysis
            _slot_price = get_price_for_slot(data.general_forecast, slot_start)

            # Determine if we should grid charge using single source of truth
            gap_to_target = max(target_pct - predicted_soc, 0)

            # Handle wrap-around: if current time is past target hour,
            # next DW is tomorrow
            if now_dt.hour >= target_hour:
                is_before_dw = slot_hour >= now_dt.hour or slot_hour < target_hour
            else:
                is_before_dw = slot_hour < target_hour

            # Explicit daylight check - must have solar to grid charge
            is_daylight = solar_kwh > 0.05

            # Use single source of truth for grid charging decision
            should_grid_charge, should_boost = self._should_grid_charge_at_slot(
                slot_start=slot_start,
                solar_kwh=solar_kwh,
                slot_price=_slot_price,
                predicted_soc=predicted_soc,
                target_pct=target_pct,
                effective_cheap_price=data.effective_cheap_price,
                is_before_dw=is_before_dw,
                in_demand_window=in_demand_window,
                gap_to_target=gap_to_target,
                is_daylight=is_daylight,
            )

            # Debug logging for charging decision
            _LOGGER.debug(
                "GRID_CHARGE: %02d:%02d in_dw=%s before_dw=%s soc=%.1f<%d gap=%d -> charge=%s boost=%s",
                slot_hour,
                slot_minute,
                in_demand_window,
                is_before_dw,
                predicted_soc,
                target_pct,
                gap_to_target,
                should_grid_charge,
                should_boost,
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
            if should_boost:
                max_slot_transfer_kwh = CHARGE_RATE_BOOST_KW / 4  # 5kW boost
            elif should_grid_charge and hours_to_dw < 2:
                max_slot_transfer_kwh = CHARGE_RATE_BOOST_KW / 4  # 5kW boost

            # Step 1: Calculate base battery delta from solar/load
            if net_kwh >= 0:
                # Solar excess: charge battery with what we can
                battery_delta_kwh = min(net_kwh, max_slot_transfer_kwh) * 0.92
            else:
                # Deficit: battery discharges to cover what it can
                battery_delta_kwh = max(net_kwh, -max_slot_transfer_kwh) / 0.95

            # Step 2: Add grid charging if needed (INDEPENDENT of solar!)
            # If we need to reach target → charge
            # When → price is cheap (but don't block if we NEED target)
            if should_grid_charge:
                current_battery_kwh = predicted_soc / 100 * BATTERY_CAPACITY_KWH
                space_remaining_kwh = max(target_kwh - current_battery_kwh, 0)
                grid_charge_amount = min(
                    max_slot_transfer_kwh * 0.92, space_remaining_kwh
                )
                battery_delta_kwh += grid_charge_amount
                grid_import_kwh = grid_charge_amount / 0.92
            else:
                grid_import_kwh = 0.0

            # Step 3: Calculate grid export (only if solar > battery capacity)
            if net_kwh >= 0:
                excess_after_battery = net_kwh - battery_delta_kwh
                grid_export_kwh = max(excess_after_battery, 0)
            else:
                # Deficit: battery already handled in Step 1 (discharge)
                # Grid import already handled in Step 2 (grid charging)
                # Just calculate export (which is 0 in deficit)
                grid_export_kwh = 0.0

            # Iterative SOC simulation with clamp each 15 minutes
            predicted_soc = predicted_soc + (
                battery_delta_kwh / BATTERY_CAPACITY_KWH * 100
            )
            predicted_soc = max(0.0, min(100.0, predicted_soc))

            # Store a light-weight SOC timeseries to avoid huge attributes
            # Used by dashboard chart (timestamp, soc)
            daily_forecast_soc_15min.append(
                [slot_start.isoformat(), round(predicted_soc, 1)]
            )

            daily_forecast.append(
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
                    "grid_charge": should_grid_charge,
                    "grid_charge_boost": should_boost,
                }
            )

        return daily_forecast, daily_forecast_soc_15min, consumption_source_counts

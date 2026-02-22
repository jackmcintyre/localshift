"""Proactive export decision helpers for forecast computation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from ..const import BATTERY_CAPACITY_KWH, DEFAULT_EXPORT_PRICE_MARGIN
from .solar_utils import get_price_for_slot

_LOGGER = logging.getLogger(__name__)


class ProactiveExportEngine:
    """Evaluate proactive export opportunities safely."""

    def __init__(
        self,
        calculate_solar_energy_between_slots: Callable[..., float],
        calculate_solar_energy_until_solar_start: Callable[..., float],
        calculate_max_fit_price: Callable[..., float],
        simulate_overnight_drain_after_export: Callable[..., tuple[float, float, bool]],
    ) -> None:
        """Initialize engine dependencies."""
        self._calculate_solar_energy_between_slots = (
            calculate_solar_energy_between_slots
        )
        self._calculate_solar_energy_until_solar_start = (
            calculate_solar_energy_until_solar_start
        )
        self._calculate_max_fit_price = calculate_max_fit_price
        self._simulate_overnight_drain_after_export = (
            simulate_overnight_drain_after_export
        )

    def _calculate_expected_replacement_price(
        self,
        slot_start: datetime,
        solar_energy_available: float,
        export_amount_kwh: float,
        general_forecast: list[dict],
        effective_cheap_price: float,
    ) -> float:
        """Calculate the expected cost to replace exported energy.

        If solar will cover the export, return 0 (free replacement).
        Otherwise, return the expected grid import price.

        Args:
            slot_start: Starting slot time
            solar_energy_available: Net solar energy before grid import needed
            export_amount_kwh: Amount to be exported
            general_forecast: Buy price forecast
            effective_cheap_price: Cheap price threshold for grid charging

        Returns:
            Expected replacement cost in $/kWh (0 if solar covers it)
        """
        # If solar energy covers the export, replacement is free
        if solar_energy_available >= export_amount_kwh:
            return 0.0

        # Need to import from grid - find expected price
        # Use the effective cheap price as the expected grid import price
        # This is the price we would pay to recharge the battery
        return effective_cheap_price

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
        """Determine if proactive export should happen at this slot.

        Proactive export exports excess battery energy during above-percentile
        FIT price windows to maximize revenue.

        Strategy:
        1. PREFER SPOT: Use current spot price ONLY for current slot (real-time decision)
        2. For future slots, use forecast price and check if better price is coming
        3. Only export when FIT > effective_cheap_price (profitability floor)
        4. Only export when battery is AT OR ABOVE target SOC (no deficit exporting)
        5. Check ending SOC after export (not just starting SOC)
        6. Only export if minimum SOC without exports >= export_min_soc_pct
        7. Only export if we have forecasted excess (won't run short)
        8. CRITICAL: Simulate overnight drain to ensure battery won't drop
           below minimum before solar production starts

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
            effective_cheap_price: The effective cheap price threshold used for grid
                charging decisions. Exports below this price are unprofitable when
                the battery holds grid-charged energy.
            feed_in_price_current: Current spot feed-in price (only for current slot)
            all_solcast: Full Solcast forecast (for overnight simulation)
            historical_avg_kw: Historical hourly load profile (for overnight simulation)
            current_load_kw: Current load power (for overnight simulation)
            recent_load_kw: Recent 1-hour average load (for overnight simulation)
            is_current_slot: True if this is the current time slot (use spot price)

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

        # PROFITABILITY FLOOR: Never export below the effective cheap price.
        # This prevents selling grid-charged energy at a loss. If the sell price is
        # below what we would pay to charge (effective_cheap_price), export is
        # unprofitable regardless of other conditions.
        if use_price <= effective_cheap_price:
            _LOGGER.debug(
                "PROACTIVE_EXPORT: %02d:%02d BLOCKED - sell $%.3f <= cheap_price floor $%.3f (unprofitable)",
                slot_hour,
                slot_start.minute,
                use_price,
                effective_cheap_price,
            )
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

        # REPLACEMENT COST CHECK (Issue #70):
        # Before allowing export, check whether the exported energy will be replaced
        # by solar (free) or by grid import (costly).
        #
        # If solar will recharge the battery for FREE -> Allow export
        # If grid import needed to replace -> Only allow if FIT >= replacement_price + margin
        #
        # NOTE: This check only applies to OVERNIGHT exports (no solar in current slot).
        # During the day, the fill-point-based solar check (CONSTRAINT 3 above) already
        # ensures there's enough solar to recharge the exported amount.
        if (
            solar_kwh < 0.01  # Only for overnight slots (no solar in current slot)
            and all_solcast is not None
            and historical_avg_kw is not None
            and general_forecast is not None
        ):
            # Calculate tentative export amount for replacement cost analysis
            battery_exportable_kwh = (
                (predicted_soc - export_min_soc_pct) / 100 * BATTERY_CAPACITY_KWH
            )
            max_export_rate_kwh = 8.7 / 4  # 2.175 kWh per 15 min slot
            tentative_export = min(
                battery_exportable_kwh,
                remaining_export_budget_kwh,
                max_export_rate_kwh,
            )

            # Calculate solar energy available until solar production starts
            solar_energy_available = self._calculate_solar_energy_until_solar_start(
                start_slot=slot_start,
                all_solcast=all_solcast,
                historical_avg_kw=historical_avg_kw,
                current_load_kw=current_load_kw,
                recent_load_kw=recent_load_kw,
            )

            # Calculate expected replacement price
            expected_replacement_price = self._calculate_expected_replacement_price(
                slot_start=slot_start,
                solar_energy_available=solar_energy_available,
                export_amount_kwh=tentative_export,
                general_forecast=general_forecast,
                effective_cheap_price=effective_cheap_price,
            )

            # If grid import needed to replace (expected_replacement_price > 0),
            # check if export is profitable
            if expected_replacement_price > 0:
                min_required_fit = expected_replacement_price + export_price_margin
                if use_price < min_required_fit:
                    _LOGGER.debug(
                        "PROACTIVE_EXPORT: %02d:%02d BLOCKED - FIT $%.3f < replacement $%.3f + margin $%.3f (solar=%.2f kWh, export=%.2f kWh)",
                        slot_hour,
                        slot_start.minute,
                        use_price,
                        expected_replacement_price,
                        export_price_margin,
                        solar_energy_available,
                        tentative_export,
                    )
                    return False, 0.0
                else:
                    _LOGGER.debug(
                        "PROACTIVE_EXPORT: %02d:%02d ALLOWED - FIT $%.3f >= replacement $%.3f + margin $%.3f (profitable arbitrage)",
                        slot_hour,
                        slot_start.minute,
                        use_price,
                        expected_replacement_price,
                        export_price_margin,
                    )

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

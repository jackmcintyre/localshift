"""Grid charging decision helpers for forecast computation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


class GridChargeDecisionEngine:
    """Evaluate whether a slot should trigger grid charging."""

    def __init__(
        self,
        next_demand_window_start_dt: Callable[[datetime, time], datetime],
        find_solar_start_time: Callable[[datetime, list[dict]], datetime | None],
        simulate_overnight_drain_to_solar: Callable[..., float],
        simulate_future_soc_with_solar_only: Callable[..., tuple[float, float, bool, bool]],
    ) -> None:
        """Initialize decision engine with required callbacks."""
        self._next_demand_window_start_dt = next_demand_window_start_dt
        self._find_solar_start_time = find_solar_start_time
        self._simulate_overnight_drain_to_solar = simulate_overnight_drain_to_solar
        self._simulate_future_soc_with_solar_only = simulate_future_soc_with_solar_only
        self._adaptive_params = None

    def set_adaptive_params(self, adaptive_params: Any | None) -> None:
        """Set adaptive parameters from the learning system (Issue #170 Phase 2).

        These parameters adjust grid charging decisions based on learned
        outcomes from historical performance.

        Args:
            adaptive_params: AdaptiveParameters instance with tuned values,
                           or None to use defaults.
        """
        self._adaptive_params = adaptive_params

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
        """Calculate local effective cheap price for a specific slot.

        Key insight: Urgency pricing should only apply to slots before TODAY's DW.
        For slots after today's DW (targeting tomorrow's DW), use base price only.

        This prevents evening slots from being gated by urgency pricing calculated
        in the morning for today's target.

        Issue #170 Phase 2: Applies cheap_price_bias adaptive parameter.

        Args:
            slot_start: Start time of the slot
            general_forecast: Buy price forecast
            target_pct: Target SOC percentage
            current_soc: Current SOC percentage
            dw_start_time: Demand window start time
            base_cheap_price: Base percentile cheap price
            max_price: Maximum allowed price

        Returns:
            Local effective cheap price for this slot
        """
        # Issue #170 Phase 2: Apply cheap_price_bias adaptive parameter
        # Positive bias = more willing to grid charge (higher threshold)
        # Negative bias = more conservative (lower threshold)
        if self._adaptive_params is not None:
            bias_cents = self._adaptive_params.get("cheap_price_bias", 0.0)
            # Convert c/kWh to $/kWh and apply to base price
            base_cheap_price = base_cheap_price + (bias_cents / 100.0)
        # Get today's DW start datetime
        now_local = dt_util.now()
        today_dw_start = now_local.replace(
            hour=dw_start_time.hour,
            minute=dw_start_time.minute,
            second=0,
            microsecond=0,
        )

        # Check if slot is before or after TODAY's DW
        if slot_start < today_dw_start:
            # Slot is before today's DW - urgency pricing may apply
            # Calculate hours left until today's DW
            hours_left = max((today_dw_start - slot_start).total_seconds() / 3600, 0)

            # Check if there's a solar gap for today's target
            # Simplified: if SOC is well below target, apply urgency
            gap_to_target = target_pct - current_soc
            solar_gap = gap_to_target > 5  # More than 5% gap = urgency

            if solar_gap and hours_left < 8:
                # Apply urgency pricing
                urgency = max(min(1 - (hours_left / 8.0), 1.0), 0.0)
                urgency_price = (
                    base_cheap_price + (max_price - base_cheap_price) * urgency
                )

                # Find minimum forecast price before today's DW
                min_forecast = max_price
                for f in general_forecast:
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

                    if f_local >= slot_start and f_local < today_dw_start:
                        price = float(f.get("per_kwh", max_price))
                        if price < min_forecast:
                            min_forecast = price

                forecast_floor = max(min_forecast + 0.02, base_cheap_price)
                final = min(urgency_price, max_price)
                final = max(final, forecast_floor)
                return round(final, 2)

        # Slot is after today's DW, or no urgency needed
        # Use base price only - tomorrow has plenty of time for solar
        return base_cheap_price

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

        Issue #170 Phase 2: Applies adaptive parameters:
        - solar_confidence_factor: Scales solar forecasts (pessimistic/optimistic)
        - overnight_drain_safety_margin: Extra buffer for overnight simulations
        - grid_charge_soc_headroom: Adjusts target SOC

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

        # Issue #170 Phase 2: Apply adaptive parameters
        # solar_confidence_factor: Scale solar forecast (0.5-1.5)
        # <1.0 = pessimistic (charge more), >1.0 = optimistic (trust solar, charge less)
        solar_confidence_factor = 1.0
        if self._adaptive_params is not None:
            solar_confidence_factor = self._adaptive_params.get(
                "solar_confidence_factor", 1.0
            )

        # grid_charge_soc_headroom: Add headroom to target SOC
        # Positive = charge slightly above target to account for forecast errors
        soc_headroom = 0.0
        if self._adaptive_params is not None:
            soc_headroom = self._adaptive_params.get("grid_charge_soc_headroom", 0.0)

        # Apply headroom to effective target
        effective_target_pct = target_pct + soc_headroom

        # Apply solar confidence factor to the solar forecast
        adjusted_solar_kwh = solar_kwh * solar_confidence_factor

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
        # NOTE: Hysteresis only applies to the current real-time slot to prevent hardware
        # flip-flopping. Future forecast slots should always run through solar simulation
        # for optimal planning.
        if (
            is_current_slot
            and is_currently_grid_charging
            and price_is_cheap
            and gap_to_target > 0
        ):
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

        # Issue #170 Phase 2: Get overnight_drain_safety_margin
        # Positive = keep more buffer (be more conservative about overnight drain)
        overnight_drain_margin = 0.0
        if self._adaptive_params is not None:
            overnight_drain_margin = self._adaptive_params.get(
                "overnight_drain_safety_margin", 0.0
            )

        # OVERNIGHT EFFICIENCY CHECK:
        # For overnight slots (no solar), we need to check differently.
        # Grid charging overnight at $0.15/kWh when tomorrow's solar is "free" is
        # economically wrong. Only grid charge overnight if solar truly can't reach target.
        # Use adjusted solar to determine if this is an overnight slot
        is_overnight_slot = adjusted_solar_kwh < 0.01  # No meaningful solar

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

                # Issue #170 Phase 2: Apply overnight drain safety margin
                # Reduce the simulated SOC by the margin to be more conservative
                adjusted_soc_at_solar = soc_at_solar_start - overnight_drain_margin

                # Now simulate from solar start to next DW
                # Use effective_target_pct for simulation (includes headroom)
                soc_at_end, max_soc, can_reach_with_solar_only, _ = (
                    self._simulate_future_soc_with_solar_only(
                        actual_current_soc=max(min_soc_pct, adjusted_soc_at_solar),
                        start_slot=solar_start,  # Start from solar, not from now
                        target_pct=effective_target_pct,
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
                    "OVERNIGHT_CHECK: %02d:%02d SOC %.1f%% -> %.1f%% at solar start %s (margin -%.1f%%), max_soc=%.1f%%",
                    slot_start.hour,
                    slot_start.minute,
                    predicted_soc,
                    adjusted_soc_at_solar,
                    solar_start.strftime("%H:%M"),
                    overnight_drain_margin,
                    max_soc,
                )

                # Solar from dawn can reach target: NO overnight grid charging
                if can_reach_with_solar_only:
                    _LOGGER.debug(
                        "Grid charge SKIPPED overnight: solar from %s reaches target (SOC at dawn: %.1f%% -> max %.1f%%)",
                        solar_start.strftime("%H:%M"),
                        adjusted_soc_at_solar,
                        max_soc,
                    )
                    return False, False
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
                if price_is_very_cheap:
                    return True, True
                return False, False
        else:
            # Daylight slot - use simulation with adaptive parameters
            # Use effective_target_pct for simulation (includes headroom)
            soc_at_end, max_soc, can_reach_with_solar_only, _ = (
                self._simulate_future_soc_with_solar_only(
                    actual_current_soc=predicted_soc,
                    start_slot=sim_start,
                    target_pct=effective_target_pct,
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
                    "Grid charge SKIPPED: solar forecast reaches target before DW (max_soc=%.1f%% >= %d%%, headroom=%.1f%%)",
                    max_soc,
                    effective_target_pct,
                    soc_headroom,
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

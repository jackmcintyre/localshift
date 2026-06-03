"""Penalty factor calculations for the DP optimizer."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from custom_components.localshift.engine.types import (
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)


def get_solar_opportunity_penalty_factor(
    action: PlannerAction,
    grid_import_kwh: float,
    slot: SlotContext,
    slot_idx: int,
    slots: list[SlotContext],
    config: OptimizerConfig,
    terminal_penalty_idx: int | None,
    all_solcast: list[dict[str, Any]] | None = None,
) -> float:
    """Calculate the solar opportunity penalty factor for a slot (#610).

    Uses a coverage-ratio formula: the factor scales by how much
    projected solar surplus is available relative to battery capacity.
    The DP already handles time through backward induction, so no
    separate time discount is applied.

    Returns a value in [0.0, 1.0] where:
    - 0.0 = no significant solar forecast (no penalty)
    - 1.0 = solar surplus >= battery capacity (full penalty)
    """
    if action not in (
        PlannerAction.CHARGE_GRID_NORMAL,
        PlannerAction.CHARGE_GRID_BOOST,
    ):
        return 0.0

    if grid_import_kwh <= 0 or slot.solar_kwh > 0:
        return 0.0

    # Only skip penalty if we're AT or PAST the demand window entry point.
    # Slots BEFORE the demand window should still get the penalty to avoid
    # premature grid charging when solar is coming (Issue #610).
    if terminal_penalty_idx is not None and slot_idx >= terminal_penalty_idx:
        return 0.0

    # Sum projected solar surplus from current slot to end of DP horizon
    total_surplus: float = sum(
        max(0.0, s.solar_kwh - s.consumption_kwh) for s in slots[slot_idx:]
    )

    # Check solcast for solar BEYOND the DP slots horizon (horizon-aware)
    if all_solcast:
        try:
            last_slot_time = datetime.fromisoformat(slots[-1].timestamp_iso)
            for period in all_solcast:
                period_start_str = period.get("period_start")
                if not period_start_str:
                    continue
                period_start = datetime.fromisoformat(str(period_start_str))
                if period_start >= last_slot_time:
                    # Assumes 30-min periods (standard for solcast integrations)
                    solar_kwh = float(period.get("pv_estimate", 0)) * 0.5
                    total_surplus += solar_kwh
        except (ValueError, TypeError):
            pass

    threshold_kwh = config.battery_capacity_kwh * 0.30
    if total_surplus < threshold_kwh:
        return 0.0

    # Coverage ratio: how much of battery capacity solar can fill
    return min(1.0, total_surplus / config.battery_capacity_kwh)


def get_futile_cycling_penalty_factor(
    action: PlannerAction,
    slot_idx: int,
    slots: list[SlotContext],
    config: OptimizerConfig,
    soc_after_charge_pct: float,
    charge_kwh: float,
    terminal_penalty_idx: int | None = None,
) -> float:
    """Compute penalty factor for grid charging that will be drained before a useful period.

    Issue #638: Overnight grid charging at $0.14/kWh is wasteful if the charged energy
    drains through house load before reaching a solar-surplus period or demand window.
    This factor estimates the fraction of charged energy that will be consumed by house
    load before reaching a useful period.

    Returns:
        0.0 = all charged energy is retained for a useful period (no penalty)
        1.0 = all charged energy will drain through house load before useful period
        0.3-0.7 = partial drain (proportional penalty)

    """
    # Only apply to grid charging actions
    if action not in (
        PlannerAction.CHARGE_GRID_NORMAL,
        PlannerAction.CHARGE_GRID_BOOST,
    ):
        return 0.0

    # No charge means no futile cycling
    if charge_kwh <= 0.0:
        return 0.0

    # Forward-simulate HOLD drain from post-charge SOC through future slots.
    # Stop when we reach a useful period: solar surplus or demand window entry.
    soc = soc_after_charge_pct
    total_drained = 0.0

    capacity_kwh = config.battery_capacity_kwh
    min_soc = config.min_soc_pct
    discharge_eff = config.discharge_efficiency

    from custom_components.localshift.engine.constraints import (
        cheap_threshold_for_slot,
    )

    charge_slot_price = slots[slot_idx].buy_price
    for future_idx in range(slot_idx + 1, len(slots)):
        future_slot = slots[future_idx]
        # A "useful period" is where solar surplus, demand window, or a cheaper
        # feasible charge window makes the charged energy valuable — stop draining here.
        if future_slot.solar_kwh > future_slot.consumption_kwh:
            break
        if future_slot.is_demand_window_slot:
            break
        # A cheaper feasible charge window: charging is allowed (price <= the gate's
        # per-slot cheap threshold — Issue #800, so post-demand-window slots use the
        # un-inflated base) and meaningfully cheaper than now — energy can be stored
        # for the later cheap charge.
        future_threshold = cheap_threshold_for_slot(
            config, future_idx, terminal_penalty_idx
        )
        if (
            future_slot.buy_price <= future_threshold
            and future_slot.buy_price < charge_slot_price - 0.02
        ):
            break

        # Simulate HOLD deficit: battery covers house load up to available capacity
        net_load = future_slot.consumption_kwh - future_slot.solar_kwh
        if net_load <= 0.0:
            # Solar covers load — no drain this slot
            continue

        available_kwh = max(0.0, (soc - min_soc) / 100.0 * capacity_kwh)
        max_deliverable = available_kwh * discharge_eff
        battery_used = min(net_load, max_deliverable)

        if battery_used <= 0.0:
            # SOC is at floor, no further drain possible
            break

        # Update simulated SOC
        soc -= (battery_used / discharge_eff / capacity_kwh) * 100.0
        total_drained += battery_used

        if soc <= min_soc:
            break

    return min(1.0, total_drained / charge_kwh)

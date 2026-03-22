"""Constraint functions for DP optimizer (feasible_actions + solar gates)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from custom_components.localshift.engine.types import (
        NegativeFitAvoidanceContext,
        OptimizerConfig,
        PlannerAction,
        SlotContext,
    )

_LOGGER = logging.getLogger(__name__)


def _determine_export_actions(
    soc_pct: float,
    slot: SlotContext,
    config: OptimizerConfig,
    slot_idx: int,
    negative_fit_avoidance_context: NegativeFitAvoidanceContext | None,
) -> list[PlannerAction]:
    """Determine export actions based on mode and negative-FIT avoidance context.

    Issue #719: Recoverability-based negative-FIT avoidance.

    Allows proactive export at positive FIT before the risk window when:
    - SOC is above the recoverability floor + buffer
    - The slot is before the risk window starts
    - The slot has positive sell price

    The recoverability floor ensures we only discharge energy that can be
    recovered via future solar before the deadline, avoiding later grid import.
    """
    from custom_components.localshift.engine.types import PlannerAction

    actions = []
    can_discharge = soc_pct > config.min_soc_pct

    if not can_discharge:
        return actions

    use_avoidance = (
        negative_fit_avoidance_context is not None
        and slot_idx < negative_fit_avoidance_context.risk_window_start_idx
    )

    if use_avoidance and negative_fit_avoidance_context is not None:
        if slot.sell_price > 0:
            if slot.is_demand_window_slot:
                from custom_components.localshift.const import (
                    NEGATIVE_FIT_DW_EXPORT_MIN_BENEFIT_PER_KWH,
                )

                net_benefit = slot.sell_price - max(0.0, slot.buy_price)
                if net_benefit < NEGATIVE_FIT_DW_EXPORT_MIN_BENEFIT_PER_KWH:
                    return actions

            floor_pct = negative_fit_avoidance_context.recoverability_floor_pct_by_slot[
                slot_idx
            ]
            if soc_pct > floor_pct + 2.0:
                actions.append(PlannerAction.EXPORT_PROACTIVE)
    else:
        if config.optimization_mode == "self_consumption":
            min_profitable_sell = max(0.0, slot.buy_price) + config.export_price_margin
            if slot.sell_price >= min_profitable_sell:
                actions.append(PlannerAction.EXPORT_PROACTIVE)
        else:
            if slot.sell_price > 0:
                actions.append(PlannerAction.EXPORT_PROACTIVE)

    return actions


def feasible_actions(
    soc_pct: float,
    slot: SlotContext,
    config: OptimizerConfig,
    slot_idx: int = 0,
    slots: list[SlotContext] | None = None,
    terminal_penalty_idx: int | None = None,
    negative_fit_avoidance_context: NegativeFitAvoidanceContext | None = None,
) -> list[PlannerAction]:
    """Return list of actions feasible from given SOC and slot context.

    Constraints checked:
    - SOC floor/ceiling
    - Demand window: no grid import during DW slots
    - Optimization mode (self_consumption vs arbitrage)
    - Price thresholds for self-consumption mode
    - Solar surplus gate: suppresses grid charging when solar will cover
      the full SOC deficit before the demand window (self_consumption mode only)
    - Slot duration vs transfer limits
    - Negative FIT avoidance (Issue #719)

    PHILOSOPHY NOTE (self_consumption):
    - Low overnight SOC is acceptable. Do not add reserve-holding behavior
      that penalizes the battery hitting minimum SOC overnight.
    - Charging from grid is gated by price AND solar sufficiency —
      not by a desire to avoid low SOC.
    - If battery runs out overnight, grid import is the correct outcome.
      See docs/PLANNING_MODEL.md "Control Philosophy".

    Args:
        soc_pct: Current battery SOC percentage.
        slot: Per-slot context (price, solar, consumption, flags).
        config: Optimizer configuration and constraints.
        slot_idx: Index of the current slot in the planning horizon (default 0).
        slots: Full list of planning slots (None disables solar gate).
        terminal_penalty_idx: Index at which the shortfall penalty is applied
            (None disables solar gate).
        negative_fit_avoidance_context: Context for bounded pre-discharge (Issue #719).

    """
    from custom_components.localshift.engine.types import PlannerAction

    actions = []

    can_charge = soc_pct < config.max_soc_pct

    actions.append(PlannerAction.HOLD)

    _solar_covers_deficit = False
    _global_solar_covers = False
    if config.optimization_mode == "self_consumption" and slots is not None:
        _global_solar_covers = check_global_solar_sufficiency(
            soc_pct, slot_idx, slots, config
        )

    if (
        can_charge
        and not slot.is_demand_window_slot
        and not (_solar_covers_deficit or _global_solar_covers)
    ):
        if config.optimization_mode == "self_consumption":
            price_is_cheap = slot.buy_price <= config.effective_cheap_price
            price_is_very_cheap = slot.buy_price <= config.effective_cheap_price * 0.8

            if price_is_cheap:
                actions.append(PlannerAction.CHARGE_GRID_NORMAL)
                if price_is_very_cheap:
                    actions.append(PlannerAction.CHARGE_GRID_BOOST)
        else:
            actions.append(PlannerAction.CHARGE_GRID_NORMAL)
            actions.append(PlannerAction.CHARGE_GRID_BOOST)

    actions.extend(
        _determine_export_actions(
            soc_pct, slot, config, slot_idx, negative_fit_avoidance_context
        )
    )

    return actions


def check_global_solar_sufficiency(
    soc_pct: float,
    slot_idx: int,
    slots: list[SlotContext],
    config: OptimizerConfig,
) -> bool:
    """Check if remaining solar in the full horizon covers the SOC deficit to target.

    Uses realistic simulation that accounts for charge rate limits and efficiency,
    ensuring consistency with solar_can_reach_target sensor.

    Fixes Issue #701: Previous implementation used raw surplus without rate limits,
    incorrectly blocking cheap grid charging when solar was insufficient in reality.

    Args:
        soc_pct: Current battery SOC percentage.
        slot_idx: Index of the current slot in the planning horizon.
        slots: Full list of planning slots.
        config: Optimizer configuration.

    Returns:
        True if realistic solar simulation can raise SOC from soc_pct to demand_window_target_soc_pct.

    """
    if not slots:
        return False

    soc_deficit_pct = config.demand_window_target_soc_pct - soc_pct
    if soc_deficit_pct <= 0:
        return False

    from custom_components.localshift.engine.dp_math import (
        _simulate_solar_only_terminal_soc,
    )

    remaining_slots = slots[slot_idx:]
    simulated_terminal_soc = _simulate_solar_only_terminal_soc(
        soc_pct, remaining_slots, None, config
    )
    return simulated_terminal_soc >= config.demand_window_target_soc_pct


def is_cheap_import_window(
    slot: SlotContext,
    config: OptimizerConfig,
) -> bool:
    """Check if this is a cheap import window opportunity.

    Args:
        slot: Slot context
        config: Optimizer config

    Returns:
        True if cheap import window (price below effective_cheap_price)

    """
    return slot.buy_price <= config.effective_cheap_price


def is_blind_to_future_solar(
    slots: list[SlotContext],
    slot_idx: int,
    terminal_penalty_idx: int | None,
) -> bool:
    """Check if optimizer is blind to future solar (Issue #431 Horizon Guard).

    Args:
        slots: All slots
        slot_idx: Current slot index
        terminal_penalty_idx: Terminal penalty index

    Returns:
        True if blind to future solar

    """
    if terminal_penalty_idx is None:
        return True
    slots_beyond = len(slots) - terminal_penalty_idx - 1
    return slots_beyond < 8

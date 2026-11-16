"""Reason code classification for DP optimizer decisions."""

from __future__ import annotations

from custom_components.localshift.engine.constraints import (
    VERY_CHEAP_PRICE_FACTOR,
    cheap_threshold_for_slot,
)
from custom_components.localshift.engine.penalties import (
    get_solar_opportunity_penalty_factor,
)
from custom_components.localshift.engine.solar import (
    get_forecast_accuracy,
    projected_solar_soc_gain_pct,
)
from custom_components.localshift.engine.types import (
    NegativeFitAvoidanceContext,
    ObjectiveTerms,
    OptimizerConfig,
    OptimizerInputs,
    PlannedSlotDecision,
    PlannerAction,
    PlannerReasonCode,
    SlotContext,
)


def classify_reason(
    action: PlannerAction,
    slot: SlotContext,
    slot_idx: int,
    slots: list[SlotContext],
    soc: float,
    next_soc: float,
    config: OptimizerConfig,
    terminal_penalty_idx: int | None,
    objective_terms: ObjectiveTerms | None = None,
    inputs: OptimizerInputs | None = None,
    negative_fit_avoidance_context: NegativeFitAvoidanceContext | None = None,
) -> PlannerReasonCode:
    """Classify the reason for a decision based on action and context.

    Uses deterministic rules to assign a primary reason code.
    """
    if action == PlannerAction.HOLD:
        return classify_hold_reason(
            soc,
            slot,
            next_soc,
            config,
            objective_terms,
            slot_idx=slot_idx,
            slots=slots,
            terminal_penalty_idx=terminal_penalty_idx,
            inputs=inputs,
        )
    if action == PlannerAction.EXPORT_PROACTIVE:
        return classify_export_reason(
            slot,
            slot_idx=slot_idx,
            negative_fit_avoidance_context=negative_fit_avoidance_context,
        )
    if action in (
        PlannerAction.CHARGE_GRID_NORMAL,
        PlannerAction.CHARGE_GRID_BOOST,
    ):
        return classify_charge_reason(
            slot,
            slot_idx,
            slots,
            soc,
            config,
            terminal_penalty_idx,
            objective_terms=objective_terms,
            inputs=inputs,
        )
    return PlannerReasonCode.IDLE


def classify_hold_reason(
    soc: float,
    slot: SlotContext,
    next_soc: float,
    config: OptimizerConfig,
    objective_terms: ObjectiveTerms | None = None,
    slot_idx: int = 0,
    slots: list[SlotContext] | None = None,
    terminal_penalty_idx: int | None = None,
    inputs: OptimizerInputs | None = None,
) -> PlannerReasonCode:
    """Classify HOLD action reason.

    In self-consumption mode, identifies when grid charging was suppressed
    due to upcoming solar (Issue #610, #619).
    """
    if soc >= config.max_soc_pct - 0.5:
        return PlannerReasonCode.SOC_CEILING_CONSTRAINT
    if soc <= config.discharge_floor_pct + 0.5:
        return PlannerReasonCode.SOC_FLOOR_CONSTRAINT

    net_kwh = slot.solar_kwh - slot.consumption_kwh
    if net_kwh > 0 and next_soc > soc:
        return PlannerReasonCode.SOLAR_SURPLUS_CAPTURE

    # Check if we are waiting for solar (Issue #619)
    # If price is cheap but we aren't charging, and solar is coming, label it.
    # Issue #800: use the per-slot cheap threshold so a post-demand-window HOLD priced
    # above the un-inflated base is not mislabeled as a suppressed cheap-charge window.
    if (
        config.optimization_mode == "self_consumption"
        and slot.buy_price
        <= cheap_threshold_for_slot(config, slot_idx, terminal_penalty_idx)
        and slots is not None
        and inputs is not None
        and inputs.all_solcast
    ):
        factor = get_solar_opportunity_penalty_factor(
            action=PlannerAction.CHARGE_GRID_NORMAL,
            grid_import_kwh=1.0,  # hypothetical
            slot=slot,
            slot_idx=slot_idx,
            slots=slots,
            config=config,
            terminal_penalty_idx=terminal_penalty_idx,
            all_solcast=inputs.all_solcast,
        )
        if factor > 0:
            return PlannerReasonCode.SOLAR_OPPORTUNITY_WAIT

    return PlannerReasonCode.IDLE


def classify_export_reason(
    slot: SlotContext,
    *,
    slot_idx: int | None = None,
    negative_fit_avoidance_context: NegativeFitAvoidanceContext | None = None,
) -> PlannerReasonCode:
    """Classify EXPORT action reason."""
    # Mirrors the ``use_avoidance`` window in ``_determine_export_actions`` — the
    # two must agree or an export chosen for avoidance reasons gets reported under
    # the wrong reason code.
    if (
        negative_fit_avoidance_context is not None
        and slot_idx is not None
        and slot_idx <= negative_fit_avoidance_context.risk_window_end_idx
    ):
        return PlannerReasonCode.NEGATIVE_FIT_AVOIDANCE

    if slot.sell_price > 0:
        return PlannerReasonCode.HIGH_SELL_PRICE_EXPORT
    return PlannerReasonCode.NEGATIVE_FIT_AVOIDANCE


def classify_charge_reason(
    slot: SlotContext,
    slot_idx: int,
    slots: list[SlotContext],
    soc: float,
    config: OptimizerConfig,
    terminal_penalty_idx: int | None,
    *,
    objective_terms: ObjectiveTerms | None = None,
    inputs: OptimizerInputs | None = None,
    skip_target_shortfall: bool = False,
) -> PlannerReasonCode:
    """Classify CHARGE action reason.

    ``skip_target_shortfall`` is set once the plan itself is known to reach the
    target without grid charging (see ``relabel_unneeded_shortfall``).
    """

    if not skip_target_shortfall and _is_target_shortfall_risk(
        slot_idx,
        slots,
        soc,
        config,
        terminal_penalty_idx,
        inputs=inputs,
    ):
        return PlannerReasonCode.TARGET_SHORTFALL_RISK
    if _is_cheap_import_window(
        slot,
        slot_idx,
        config,
        terminal_penalty_idx,
        slots,
        inputs=inputs,
    ):
        return PlannerReasonCode.CHEAP_IMPORT_WINDOW
    if objective_terms and objective_terms.solar_opportunity_penalty > 0:
        return PlannerReasonCode.SOLAR_OPPORTUNITY_WAIT
    return PlannerReasonCode.UNCLASSIFIED


def relabel_unneeded_shortfall(
    decisions: list[PlannedSlotDecision],
    slots: list[SlotContext],
    initial_soc_pct: float,
    config: OptimizerConfig,
    terminal_penalty_idx: int | None,
    inputs: OptimizerInputs | None = None,
) -> None:
    """Issue #1265: drop TARGET_SHORTFALL_RISK from a plan that never falls short.

    The per-slot classifier discounts solar by forecast accuracy, so it can call a
    charge a shortfall risk while the plan's own SOC path reaches the target on
    solar alone. When the planned SOC entering the demand window, less everything
    grid-charged before it, still meets the target, there was no shortfall to
    guard against: re-classify those charges without the shortfall rule.
    """
    if terminal_penalty_idx is None or not 0 < terminal_penalty_idx <= len(decisions):
        return
    charge_actions = (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
    flagged = [
        i
        for i, d in enumerate(decisions[:terminal_penalty_idx])
        if d.action in charge_actions
        and d.reason_code == PlannerReasonCode.TARGET_SHORTFALL_RISK
    ]
    if not flagged:
        return
    grid_soc_pct = (
        sum(
            d.grid_import_kwh
            for d in decisions[:terminal_penalty_idx]
            if d.action in charge_actions
        )
        * config.charge_efficiency
        / config.battery_capacity_kwh
        * 100.0
    )
    entry_soc = decisions[terminal_penalty_idx - 1].predicted_soc_pct
    if entry_soc - grid_soc_pct < config.demand_window_target_soc_pct:
        return
    for i in flagged:
        soc = decisions[i - 1].predicted_soc_pct if i > 0 else initial_soc_pct
        decisions[i].reason_code = classify_charge_reason(
            slots[i],
            i,
            slots,
            soc,
            config,
            terminal_penalty_idx,
            objective_terms=decisions[i].objective_terms,
            inputs=inputs,
            skip_target_shortfall=True,
        )


def _is_target_shortfall_risk(
    slot_idx: int,
    slots: list[SlotContext],
    soc: float,
    config: OptimizerConfig,
    terminal_penalty_idx: int | None,
    inputs: OptimizerInputs | None = None,
) -> bool:
    """Check if grid charge is needed for demand window target."""
    if terminal_penalty_idx is None or slot_idx >= terminal_penalty_idx:
        return False
    soc_deficit = config.demand_window_target_soc_pct - soc
    if soc_deficit <= 0:
        return False

    potential_soc_gain_pct = projected_solar_soc_gain_pct(
        slot_idx=slot_idx,
        slots=slots,
        terminal_penalty_idx=terminal_penalty_idx,
        battery_capacity_kwh=config.battery_capacity_kwh,
        initial_soc_pct=soc,
        config=config,
    )

    forecast_accuracy = (
        get_forecast_accuracy(inputs.solar_accuracy_tracker) if inputs else 1.0
    )
    accuracy_discount = max(0.0, min(1.0, forecast_accuracy))
    potential_soc_gain_pct *= accuracy_discount

    return potential_soc_gain_pct < soc_deficit


def _is_cheap_import_window(
    slot: SlotContext,
    slot_idx: int,
    config: OptimizerConfig,
    terminal_penalty_idx: int | None,
    slots: list[SlotContext],
    *,
    inputs: OptimizerInputs | None = None,
) -> bool:
    """Check if this is a cheap import window opportunity.

    Issue #800: uses the per-slot cheap threshold (post-demand-window slots gate on the
    un-inflated base) so the label matches the feasibility gate.
    """
    threshold = cheap_threshold_for_slot(config, slot_idx, terminal_penalty_idx)
    if slot.buy_price > threshold:
        return False
    is_blind = _is_blind_to_future_solar(terminal_penalty_idx, slots, inputs=inputs)
    return not is_blind or slot.buy_price <= (threshold * VERY_CHEAP_PRICE_FACTOR)


def _is_blind_to_future_solar(
    terminal_penalty_idx: int | None,
    slots: list[SlotContext],
    inputs: OptimizerInputs | None = None,
) -> bool:
    """Check if optimizer is blind to future solar (Issue #431 Horizon Guard)."""
    # If we have horizon-aware solar forecast, we aren't blind (Issue #610)
    if inputs and inputs.all_solcast:
        return False

    if terminal_penalty_idx is None:
        return True
    slots_beyond = len(slots) - terminal_penalty_idx - 1
    return slots_beyond < 8

"""Reason code classification for DP optimizer decisions."""

from __future__ import annotations

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
    if soc <= config.min_soc_pct + 0.5:
        return PlannerReasonCode.SOC_FLOOR_CONSTRAINT

    net_kwh = slot.solar_kwh - slot.consumption_kwh
    if net_kwh > 0 and next_soc > soc:
        return PlannerReasonCode.SOLAR_SURPLUS_CAPTURE

    # Check if we are waiting for solar (Issue #619)
    # If price is cheap but we aren't charging, and solar is coming, label it.
    if (
        config.optimization_mode == "self_consumption"
        and slot.buy_price <= config.effective_cheap_price
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
    if (
        negative_fit_avoidance_context is not None
        and slot_idx is not None
        and slot_idx < negative_fit_avoidance_context.risk_window_start_idx
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
) -> PlannerReasonCode:
    """Classify CHARGE action reason."""

    if _is_target_shortfall_risk(
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
        config,
        terminal_penalty_idx,
        slots,
        inputs=inputs,
    ):
        return PlannerReasonCode.CHEAP_IMPORT_WINDOW
    if objective_terms and objective_terms.solar_opportunity_penalty > 0:
        return PlannerReasonCode.SOLAR_OPPORTUNITY_WAIT
    return PlannerReasonCode.UNCLASSIFIED


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
    config: OptimizerConfig,
    terminal_penalty_idx: int | None,
    slots: list[SlotContext],
    *,
    inputs: OptimizerInputs | None = None,
) -> bool:
    """Check if this is a cheap import window opportunity."""
    if slot.buy_price > config.effective_cheap_price:
        return False
    is_blind = _is_blind_to_future_solar(terminal_penalty_idx, slots, inputs=inputs)
    return not is_blind or slot.buy_price <= (config.effective_cheap_price * 0.8)


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

"""Negative-FIT avoidance context derivation (Issue #719)."""

from __future__ import annotations

from custom_components.localshift.engine.types import (
    NegativeFitAvoidanceContext,
    OptimizerConfig,
    OptimizerInputs,
)


def find_risk_window(slots: list) -> tuple[int | None, int | None]:
    """Find the spill-risk window (first bad-FIT through end of bad window)."""
    risk_start_idx = None
    n_slots = len(slots)

    for idx, slot in enumerate(slots):
        if slot.sell_price <= 0:
            risk_start_idx = idx
            break

    if risk_start_idx is None:
        return None, None

    risk_end_idx = risk_start_idx
    for idx in range(risk_start_idx, n_slots):
        if slots[idx].sell_price <= 0:
            risk_end_idx = idx
        else:
            break

    return risk_start_idx, risk_end_idx


def compute_required_headroom(
    slots: list,
    risk_start_idx: int,
    risk_end_idx: int,
    charge_efficiency: float,
    max_headroom_kwh: float,
) -> float:
    """Compute storage needed to absorb spill during risk window.

    Capped at max_headroom_kwh (battery capacity minus minimum floor).
    """
    from custom_components.localshift.const import (
        NEGATIVE_FIT_OVERFLOW_BUFFER_FACTOR,
    )

    required_headroom_kwh = 0.0
    for idx in range(risk_start_idx, risk_end_idx + 1):
        slot = slots[idx]
        net_kwh = slot.solar_kwh - slot.consumption_kwh
        if net_kwh > 0:
            required_headroom_kwh += net_kwh * charge_efficiency

    required_headroom_kwh *= NEGATIVE_FIT_OVERFLOW_BUFFER_FACTOR
    return min(required_headroom_kwh, max_headroom_kwh)


def compute_recovery_by_slot(
    slots: list, recovery_deadline_idx: int, charge_efficiency: float
) -> list[float]:
    """Precompute conservative recovery potential from each slot to deadline."""
    recovery_by_slot = []
    for slot_idx in range(len(slots)):
        recoverable_kwh = 0.0
        for future_idx in range(slot_idx + 1, recovery_deadline_idx + 1):
            future_slot = slots[future_idx]
            net_kwh = future_slot.solar_kwh - future_slot.consumption_kwh
            if net_kwh > 0:
                recoverable_kwh += net_kwh * charge_efficiency * 0.8
        recovery_by_slot.append(recoverable_kwh)
    return recovery_by_slot


def compute_floor_by_slot(
    n_slots: int,
    current_kwh: float,
    target_kwh: float,
    min_floor_kwh: float,
    battery_capacity_kwh: float,
    recovery_by_slot: list[float],
) -> list[float]:
    """Precompute recoverability floor for each slot."""
    floor_by_slot = []
    for slot_idx in range(n_slots):
        recoverable_kwh = recovery_by_slot[slot_idx]

        max_discharge_kwh = min(
            current_kwh - min_floor_kwh,
            recoverable_kwh,
        )
        max_discharge_kwh = max(max_discharge_kwh, 0.0)

        floor_kwh = current_kwh - max_discharge_kwh
        floor_kwh = max(floor_kwh, min_floor_kwh)
        floor_kwh = min(floor_kwh, target_kwh)

        floor_pct = floor_kwh / battery_capacity_kwh * 100.0
        floor_by_slot.append(floor_pct)
    return floor_by_slot


def derive_negative_fit_avoidance_context(
    inputs: OptimizerInputs,
) -> NegativeFitAvoidanceContext | None:
    """Derive context for recoverability-based negative-FIT avoidance.

    The planner may proactively discharge at positive FIT before a bad-price
    spill window when conservative future solar can still recover the battery
    to target by the relevant deadline.

    Returns None if any of:
    - No negative-FIT window within horizon
    - No earlier positive-FIT slots
    - No recovery path to target (cannot safely pre-discharge)
    """
    slots = inputs.slots
    config = inputs.config
    battery_capacity_kwh = config.battery_capacity_kwh
    n_slots = len(slots)

    if n_slots == 0:
        return None

    risk_start_idx, risk_end_idx = find_risk_window(slots)
    if risk_start_idx is None or risk_end_idx is None:
        return None

    has_positive_before = any(s.sell_price > 0 for s in slots[:risk_start_idx])
    if not has_positive_before:
        return None

    min_floor_kwh = config.min_soc_pct / 100.0 * battery_capacity_kwh
    max_headroom_kwh = battery_capacity_kwh - min_floor_kwh

    required_headroom_kwh = compute_required_headroom(
        slots,
        risk_start_idx,
        risk_end_idx,
        config.charge_efficiency,
        max_headroom_kwh,
    )
    if required_headroom_kwh <= 0:
        return None

    target_kwh = config.demand_window_target_soc_pct / 100.0 * battery_capacity_kwh
    current_kwh = inputs.initial_soc_pct / 100.0 * battery_capacity_kwh
    existing_headroom_kwh = max(target_kwh - current_kwh, 0.0)

    if existing_headroom_kwh >= required_headroom_kwh:
        return None

    recovery_deadline_idx = None
    for idx, slot in enumerate(slots):
        if slot.is_demand_window_slot:
            recovery_deadline_idx = idx
            break
    if recovery_deadline_idx is None:
        recovery_deadline_idx = n_slots - 1

    recovery_by_slot = compute_recovery_by_slot(
        slots, recovery_deadline_idx, config.charge_efficiency
    )

    floor_by_slot = compute_floor_by_slot(
        n_slots,
        current_kwh,
        target_kwh,
        min_floor_kwh,
        battery_capacity_kwh,
        recovery_by_slot,
    )

    return NegativeFitAvoidanceContext(
        risk_window_start_idx=risk_start_idx,
        risk_window_end_idx=risk_end_idx,
        required_headroom_kwh=required_headroom_kwh,
        recovery_deadline_idx=recovery_deadline_idx,
        conservative_recovery_kwh_by_slot=tuple(recovery_by_slot),
        recoverability_floor_pct_by_slot=tuple(floor_by_slot),
    )


def compute_recoverability_floor_pct(
    *,
    current_soc_pct: float,
    slot_idx: int,
    context: NegativeFitAvoidanceContext,
    config: OptimizerConfig,
    inputs: OptimizerInputs,
) -> float:
    """Compute the minimum SOC that still allows recovery to target.

    The recoverability floor is how low SOC can go now while still being
    able to recover to demand_window_target_soc_pct by the deadline using
    conservative future solar estimates.

    This is the planner-side guardrail. The Tesla-side PROACTIVE_EXPORT
    throttling (SOC - 5%, min 4%) remains the actuator guardrail.
    """
    battery_capacity_kwh = config.battery_capacity_kwh
    target_kwh = config.demand_window_target_soc_pct / 100.0 * battery_capacity_kwh
    min_floor_kwh = config.min_soc_pct / 100.0 * battery_capacity_kwh

    current_kwh = current_soc_pct / 100.0 * battery_capacity_kwh

    if slot_idx >= len(context.conservative_recovery_kwh_by_slot):
        return config.demand_window_target_soc_pct

    recoverable_kwh = context.conservative_recovery_kwh_by_slot[slot_idx]

    max_discharge_kwh = min(
        current_kwh - min_floor_kwh,
        recoverable_kwh,
    )
    max_discharge_kwh = max(max_discharge_kwh, 0.0)

    floor_kwh = current_kwh - max_discharge_kwh
    floor_kwh = max(floor_kwh, min_floor_kwh)
    floor_kwh = min(floor_kwh, target_kwh)

    floor_pct = floor_kwh / battery_capacity_kwh * 100.0
    return floor_pct

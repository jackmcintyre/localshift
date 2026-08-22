"""Negative-FIT avoidance context derivation (Issue #719)."""

from __future__ import annotations

from custom_components.localshift.engine.types import (
    NegativeFitAvoidanceContext,
    OptimizerConfig,
    OptimizerInputs,
)


def find_risk_window(
    slots: list, limit_idx: int | None = None
) -> tuple[int | None, int | None]:
    """Find the spill-risk window (first through last bad-FIT slot in the horizon).

    The window spans the whole bad-price period rather than only its first
    contiguous run. Real horizons interleave short positive blips through a
    negative middle — a single afternoon can flip sign a dozen times — and a
    first-run-only scan sizes the window off one slot, under-reading the day's
    spill by an order of magnitude and leaving ``required_headroom_kwh`` far too
    small to justify any pre-discharge.

    Positive slots inside the span are export *opportunities*, not window
    terminators; ``_determine_export_actions`` is what decides which of them are
    usable, gated on the recoverability floor.

    ``limit_idx`` bounds the scan (inclusive), so the window ends on the last bad
    slot at or before the recovery deadline rather than on the deadline itself —
    which would otherwise pull trailing positive slots into the headroom sum.
    """
    risk_start_idx = None
    risk_end_idx = None
    last_idx = len(slots) - 1 if limit_idx is None else min(limit_idx, len(slots) - 1)

    for idx in range(last_idx + 1):
        if slots[idx].sell_price <= 0:
            if risk_start_idx is None:
                risk_start_idx = idx
            risk_end_idx = idx

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
    target_kwh: float,
    min_floor_kwh: float,
    battery_capacity_kwh: float,
    recovery_by_slot: list[float],
) -> list[float]:
    """Precompute the recoverability floor for each slot.

    The floor is the lowest level from which conservative future solar still
    reaches *target* by the deadline: ``target - recoverable``, never below the
    configured minimum SOC.

    It is deliberately anchored on the target rather than on present SOC. Asking
    "how far can I discharge and get back to where I am now" is the wrong
    question whenever current SOC is below target — which is most of a pre-demand
    -window day — and on a weak-solar day with a low starting SOC it collapses
    the floor onto ``min_soc_pct`` and lets the planner sell down to it, arriving
    at the demand window tens of points short.

    Because the answer depends only on the recovery available from each slot, it
    is the same for every SOC the DP explores and can safely be precomputed.
    """
    floor_by_slot = []
    for slot_idx in range(n_slots):
        recoverable_kwh = recovery_by_slot[slot_idx]

        floor_kwh = target_kwh - recoverable_kwh
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
    - No bad-FIT slot at or before the recovery deadline
    - No positive-FIT slot to sell into at or before the window ends
    - No recovery path to target (cannot safely pre-discharge)
    """
    slots = inputs.slots
    config = inputs.config
    battery_capacity_kwh = config.battery_capacity_kwh
    n_slots = len(slots)

    if n_slots == 0:
        return None

    recovery_deadline_idx = None
    for idx, slot in enumerate(slots):
        if slot.is_demand_window_slot:
            recovery_deadline_idx = idx
            break
    if recovery_deadline_idx is None:
        recovery_deadline_idx = n_slots - 1

    # Bound the scan at the recovery deadline. A 24h+ horizon usually carries
    # tomorrow's negative middle as well, and sizing today's pre-discharge off a
    # spill that lands after the battery has to be back at target overstates the
    # headroom needed and stretches the window past anything this mechanism can
    # act on.
    risk_start_idx, risk_end_idx = find_risk_window(slots, recovery_deadline_idx)
    if risk_start_idx is None or risk_end_idx is None:
        return None

    # An export opportunity is any positive-FIT slot at or before the end of the
    # risk window. Requiring one strictly *before* ``risk_start_idx`` made the
    # feature disable itself in exactly the situation it exists for: once slot 0
    # is already negative, ``risk_start_idx`` is 0, the "before" slice is empty,
    # and the planner loses its export action for the whole horizon. On a
    # scattered-negative afternoon the usable slots are the positive blips
    # *inside* the window, so scan through ``risk_end_idx`` instead.
    has_export_opportunity = any(s.sell_price > 0 for s in slots[: risk_end_idx + 1])
    if not has_export_opportunity:
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

    recovery_by_slot = compute_recovery_by_slot(
        slots, recovery_deadline_idx, config.charge_efficiency
    )

    floor_by_slot = compute_floor_by_slot(
        n_slots,
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
    slot_idx: int,
    context: NegativeFitAvoidanceContext,
    config: OptimizerConfig,
) -> float:
    """Compute the minimum SOC that still allows recovery to target.

    The recoverability floor is how low SOC can go now while still being
    able to recover to demand_window_target_soc_pct by the deadline using
    conservative future solar estimates.

    Kept in step with ``compute_floor_by_slot`` — same ``target - recoverable``
    anchor — so the scalar and precomputed forms cannot disagree.

    This is the planner-side guardrail. The Tesla-side PROACTIVE_EXPORT
    throttling (SOC - 5%, min 4%) remains the actuator guardrail.
    """
    battery_capacity_kwh = config.battery_capacity_kwh
    target_kwh = config.demand_window_target_soc_pct / 100.0 * battery_capacity_kwh
    min_floor_kwh = config.min_soc_pct / 100.0 * battery_capacity_kwh

    if slot_idx >= len(context.conservative_recovery_kwh_by_slot):
        return config.demand_window_target_soc_pct

    recoverable_kwh = context.conservative_recovery_kwh_by_slot[slot_idx]

    floor_kwh = target_kwh - recoverable_kwh
    floor_kwh = max(floor_kwh, min_floor_kwh)
    floor_kwh = min(floor_kwh, target_kwh)

    floor_pct = floor_kwh / battery_capacity_kwh * 100.0
    return floor_pct

"""Negative-FIT avoidance context derivation (Issue #719)."""

from __future__ import annotations

from dataclasses import dataclass

from custom_components.localshift.engine.types import (
    NegativeFitAvoidanceContext,
    OptimizerConfig,
    OptimizerInputs,
)


@dataclass(frozen=True)
class NegativeFitAssessment:
    """What the avoidance derivation decided this cycle, and why (Issue #1103).

    ``context`` is what the DP consumes. The rest exists so a window that was
    found and then dropped as immaterial can be explained from the optimizer
    summary instead of looking identical to "no negative prices today".
    """

    context: NegativeFitAvoidanceContext | None = None
    """The avoidance context, or None when avoidance does not apply."""

    spill_cost: float | None = None
    """Dollars the risk window would cost on the do-nothing trajectory: surplus
    that cannot be stored, times the magnitude of the non-positive sell price.
    None when the horizon has no risk window to cost."""

    spill_exposure: float | None = None
    """Dollars of window surplus at its non-positive price with the battery
    left out of it altogether. Depends on the forecast alone, so it is what an
    already-open window is held against. None when there is no risk window."""

    spill_threshold: float = 0.0
    """Dollar threshold the spill cost must reach
    (``OptimizerConfig.negative_fit_min_spill_cost``). 0.0 means the gate is
    disabled."""

    suppressed: bool = False
    """True when a context would have been created and the materiality gate
    alone removed it."""

    held_open: bool = False
    """True when the spill cost is below the threshold and the context exists
    only because the window was already open and the forecast is still
    material."""


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


def compute_spill_cost(
    slots: list,
    risk_start_idx: int,
    risk_end_idx: int,
    initial_soc_pct: float,
    config: OptimizerConfig,
) -> float:
    """Dollar cost of the spill the risk window forces if nothing is done.

    Walks the do-nothing trajectory (solar minus load, clamped to the SOC
    bounds) from slot 0 through the end of the window. In each non-positive
    FIT slot, whatever surplus the battery has no room for goes to the grid at
    that slot's price, and its magnitude is the cost. Surplus the battery
    absorbs costs nothing, and positive-FIT slots inside the window earn rather
    than cost, so neither contributes.

    This is the quantity the avoidance mechanism exists to reduce. Comparing it
    with a dollar threshold is what stops a window worth a fraction of a cent
    from unlocking a morning of battery export (Issue #1103).
    """
    battery_capacity_kwh = config.battery_capacity_kwh
    charge_efficiency = config.charge_efficiency
    soc_pct = min(max(initial_soc_pct, config.discharge_floor_pct), config.max_soc_pct)

    spill_cost = 0.0
    for idx in range(min(risk_end_idx, len(slots) - 1) + 1):
        slot = slots[idx]
        net_kwh = slot.solar_kwh - slot.consumption_kwh
        if net_kwh <= 0.0:
            soc_pct += net_kwh / battery_capacity_kwh * 100.0
            soc_pct = max(soc_pct, config.discharge_floor_pct)
            continue

        room_kwh = (config.max_soc_pct - soc_pct) / 100.0 * battery_capacity_kwh
        storable_kwh = room_kwh / charge_efficiency if charge_efficiency > 0.0 else 0.0
        absorbed_kwh = min(net_kwh, max(storable_kwh, 0.0))
        soc_pct += absorbed_kwh * charge_efficiency / battery_capacity_kwh * 100.0
        soc_pct = min(soc_pct, config.max_soc_pct)

        if idx >= risk_start_idx and slot.sell_price <= 0:
            spill_cost += (net_kwh - absorbed_kwh) * -slot.sell_price

    return spill_cost


def compute_spill_exposure(
    slots: list, risk_start_idx: int, risk_end_idx: int
) -> float:
    """Dollars of surplus the risk window carries at non-positive prices.

    ``compute_spill_cost`` with the battery taken out: every kWh of net surplus
    in a non-positive slot, at that slot's price, whether or not there would be
    room to store it. It is therefore an upper bound on the spill cost, and it
    is a property of the forecast alone — exporting the battery does not change
    it. That is what makes it the right measure for an open window, where the
    spill cost has already been driven down by the exports the gate approved.
    """
    exposure = 0.0
    for idx in range(risk_start_idx, min(risk_end_idx, len(slots) - 1) + 1):
        slot = slots[idx]
        net_kwh = slot.solar_kwh - slot.consumption_kwh
        if net_kwh > 0.0 and slot.sell_price <= 0:
            exposure += net_kwh * -slot.sell_price
    return exposure


def _materiality(
    spill_cost: float,
    spill_exposure: float,
    threshold: float,
    window_open: bool,
) -> tuple[bool, bool]:
    """Decide the materiality gate. Returns ``(suppressed, held_open)``.

    ``threshold`` is dollars for the whole window
    (``OptimizerConfig.negative_fit_min_spill_cost``); 0 disables the gate.

    A window opens when the spill it would cost reaches the threshold. That
    figure cannot also decide when it closes: it is costed from the present SOC,
    and the first thing an open window does is export the battery, so the cost
    falls under the threshold because of the plan the gate just approved and
    the rest of that plan is dropped. An open window is therefore held against
    the forecast's exposure instead, which the battery cannot move, and against
    a lower level, so a revision of a fraction of a cent across the threshold
    does not flip the plan either. It still closes when the negative prices are
    revised away, and near the end of a window once what is left is small.
    """
    from custom_components.localshift.const import (
        NEGATIVE_FIT_SPILL_RELEASE_FRACTION,
    )

    if threshold <= 0.0 or spill_cost >= threshold:
        return False, False
    release = threshold * NEGATIVE_FIT_SPILL_RELEASE_FRACTION
    if window_open and spill_exposure >= release:
        return False, True
    return True, False


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


def _recovery_deadline_idx(slots: list) -> int:
    """First demand-window slot, or the end of the horizon when there is none."""
    for idx, slot in enumerate(slots):
        if slot.is_demand_window_slot:
            return idx
    return len(slots) - 1


def _existing_headroom_kwh(inputs: OptimizerInputs, risk_start_idx: int) -> float:
    """Room to full at the start of the risk window if nothing is done.

    Projects SOC to the start of the risk window along the do-nothing
    trajectory: solar minus load, slot by slot, clamped to the SOC bounds.
    The static ``target - initial SOC`` headroom this replaces is blind to
    overnight load drain — on a heavy-load evening it counts headroom the
    house consumes before the spill window opens, admitting pre-discharge
    exports that only displace load-serving energy. Headroom is room to
    *full*, matching the ``capacity - floor`` cap on the required headroom:
    spill charges the battery toward max SOC regardless of target.
    """
    config = inputs.config
    battery_capacity_kwh = config.battery_capacity_kwh
    soc_at_risk_pct = inputs.initial_soc_pct
    for slot in inputs.slots[:risk_start_idx]:
        net_kwh = slot.solar_kwh - slot.consumption_kwh
        stored_kwh = net_kwh * config.charge_efficiency if net_kwh > 0.0 else net_kwh
        soc_at_risk_pct += stored_kwh / battery_capacity_kwh * 100.0
    soc_at_risk_pct = min(
        max(soc_at_risk_pct, config.discharge_floor_pct), config.max_soc_pct
    )
    return (config.max_soc_pct - soc_at_risk_pct) / 100.0 * battery_capacity_kwh


def derive_negative_fit_avoidance_context(
    inputs: OptimizerInputs,
) -> NegativeFitAvoidanceContext | None:
    """Derive context for recoverability-based negative-FIT avoidance.

    Thin wrapper over ``assess_negative_fit_avoidance`` for callers that only
    need the context; the planner uses the assessment so it can publish why a
    window was dropped.
    """
    return assess_negative_fit_avoidance(inputs).context


def assess_negative_fit_avoidance(inputs: OptimizerInputs) -> NegativeFitAssessment:
    """Derive the avoidance context, with the figures that explain the outcome.

    The planner may proactively discharge at positive FIT before a bad-price
    spill window when conservative future solar can still recover the battery
    to target by the relevant deadline.

    ``context`` is None if any of:
    - No negative-FIT window within horizon
    - No bad-FIT slot at or before the recovery deadline
    - No positive-FIT slot to sell into at or before the window ends
    - No recovery path to target (cannot safely pre-discharge)
    - Projected load drain already leaves room to absorb the spill
    - The spill the window would cost is below
      ``config.negative_fit_min_spill_cost`` dollars and the window is not
      already open on a still-material forecast (Issue #1103) — the one case
      reported as ``suppressed``
    """
    slots = inputs.slots
    config = inputs.config
    battery_capacity_kwh = config.battery_capacity_kwh
    n_slots = len(slots)
    spill_threshold = max(0.0, config.negative_fit_min_spill_cost)
    none = NegativeFitAssessment(spill_threshold=spill_threshold)

    if n_slots == 0:
        return none

    recovery_deadline_idx = _recovery_deadline_idx(slots)

    # Bound the scan at the recovery deadline. A 24h+ horizon usually carries
    # tomorrow's negative middle as well, and sizing today's pre-discharge off a
    # spill that lands after the battery has to be back at target overstates the
    # headroom needed and stretches the window past anything this mechanism can
    # act on.
    risk_start_idx, risk_end_idx = find_risk_window(slots, recovery_deadline_idx)
    if risk_start_idx is None or risk_end_idx is None:
        return none

    # Costed as soon as a window exists, so the summary carries the figure
    # whichever of the checks below ends up deciding the outcome.
    spill_cost = compute_spill_cost(
        slots, risk_start_idx, risk_end_idx, inputs.initial_soc_pct, config
    )
    spill_exposure = compute_spill_exposure(slots, risk_start_idx, risk_end_idx)
    none = NegativeFitAssessment(
        spill_cost=spill_cost,
        spill_exposure=spill_exposure,
        spill_threshold=spill_threshold,
    )

    # An export opportunity is any positive-FIT slot at or before the end of the
    # risk window. Requiring one strictly *before* ``risk_start_idx`` made the
    # feature disable itself in exactly the situation it exists for: once slot 0
    # is already negative, ``risk_start_idx`` is 0, the "before" slice is empty,
    # and the planner loses its export action for the whole horizon. On a
    # scattered-negative afternoon the usable slots are the positive blips
    # *inside* the window, so scan through ``risk_end_idx`` instead.
    # Issue #1097: a slot the hardware will not export in is not an opportunity.
    has_export_opportunity = any(
        s.sell_price > 0 and s.export_available for s in slots[: risk_end_idx + 1]
    )
    if not has_export_opportunity:
        return none

    min_floor_kwh = config.discharge_floor_pct / 100.0 * battery_capacity_kwh
    max_headroom_kwh = battery_capacity_kwh - min_floor_kwh

    required_headroom_kwh = compute_required_headroom(
        slots,
        risk_start_idx,
        risk_end_idx,
        config.charge_efficiency,
        max_headroom_kwh,
    )
    if required_headroom_kwh <= 0:
        return none

    target_kwh = config.demand_window_target_soc_pct / 100.0 * battery_capacity_kwh

    if _existing_headroom_kwh(inputs, risk_start_idx) >= required_headroom_kwh:
        return none

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

    # Issue #1103: materiality. Everything above asks whether pre-discharge is
    # possible and safe; none of it asks whether the spill is worth avoiding.
    # Any ``sell_price <= 0`` opened the window, and an open window drops the
    # ordinary export value test in ``_determine_export_actions``, so a slot at
    # -0.04c was enough to license exporting the battery all morning at 1-7c.
    # Checked last so ``suppressed`` means exactly "this gate, and only this
    # gate, removed the context".
    suppressed, held_open = _materiality(
        spill_cost, spill_exposure, spill_threshold, inputs.negative_fit_window_open
    )
    if suppressed:
        return NegativeFitAssessment(
            spill_cost=spill_cost,
            spill_exposure=spill_exposure,
            spill_threshold=spill_threshold,
            suppressed=True,
        )

    return NegativeFitAssessment(
        context=NegativeFitAvoidanceContext(
            risk_window_start_idx=risk_start_idx,
            risk_window_end_idx=risk_end_idx,
            required_headroom_kwh=required_headroom_kwh,
            recovery_deadline_idx=recovery_deadline_idx,
            conservative_recovery_kwh_by_slot=tuple(recovery_by_slot),
            recoverability_floor_pct_by_slot=tuple(floor_by_slot),
        ),
        spill_cost=spill_cost,
        spill_exposure=spill_exposure,
        spill_threshold=spill_threshold,
        held_open=held_open,
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
    min_floor_kwh = config.discharge_floor_pct / 100.0 * battery_capacity_kwh

    if slot_idx >= len(context.conservative_recovery_kwh_by_slot):
        return config.demand_window_target_soc_pct

    recoverable_kwh = context.conservative_recovery_kwh_by_slot[slot_idx]

    floor_kwh = target_kwh - recoverable_kwh
    floor_kwh = max(floor_kwh, min_floor_kwh)
    floor_kwh = min(floor_kwh, target_kwh)

    floor_pct = floor_kwh / battery_capacity_kwh * 100.0
    return floor_pct

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

VERY_CHEAP_PRICE_FACTOR = 0.8
"""Fraction of the cheap threshold below which a price is "very cheap".

A price at/below ``cheap_threshold × VERY_CHEAP_PRICE_FACTOR`` unconditionally unlocks
boost charging (a genuine bargain worth filling fast). Shared with the reason-code
classifier so the "very cheap" boundary agrees everywhere it is applied."""


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

    # Grid charging is capped at the demand-window target in self-consumption
    # mode, not at the physical 100% ceiling. The target (battery_target plus any
    # learned drain/headroom margin) is the SOC the planner is trying to reach for
    # demand-window readiness; grid should fill the battery up to it and no further.
    # Without this cap, any cheap slot below 100% invites a marginal above-target
    # top-up (e.g. grid-charging at 98% just before the demand window) that the
    # full-retail self-consumption credit makes look profitable but which only
    # cycles the battery for negligible gain. Solar can still fill above the target
    # for free (export actions are handled separately), and arbitrage mode keeps the
    # physical ceiling so it can still chase price spreads.
    charge_ceiling = (
        config.demand_window_target_soc_pct
        if config.optimization_mode == "self_consumption"
        else config.max_soc_pct
    )
    can_charge = soc_pct < charge_ceiling

    actions.append(PlannerAction.HOLD)

    _solar_covers_deficit = False
    _global_solar_covers = False
    if config.optimization_mode == "self_consumption" and slots is not None:
        _global_solar_covers = check_global_solar_sufficiency(
            soc_pct, slot_idx, slots, config, terminal_penalty_idx
        )

    if (
        can_charge
        and not slot.is_demand_window_slot
        and not (_solar_covers_deficit or _global_solar_covers)
    ):
        if config.optimization_mode == "self_consumption":
            cheap_threshold = cheap_threshold_for_slot(
                config, slot_idx, terminal_penalty_idx
            )
            price_is_cheap = slot.buy_price <= cheap_threshold
            price_is_very_cheap = (
                slot.buy_price <= cheap_threshold * VERY_CHEAP_PRICE_FACTOR
            )

            if price_is_cheap:
                actions.append(PlannerAction.CHARGE_GRID_NORMAL)
                # Shortfall-aware boost (2026-06-11 incident): boost is normally reserved
                # for genuinely very-cheap prices, but if normal-rate charging from here
                # cannot reach the demand-window target in the slots remaining, unlock
                # boost so the DP has a feasible path to target instead of eating the
                # terminal shortfall penalty. At an equal price, boost stores more per
                # slot; the very-cheap gate alone left the target unreachable while prices
                # sat just above 0.8× threshold. The ``slot_idx < terminal_penalty_idx``
                # guard is load-bearing: it confines this to pre-DW slots so a post-DW low
                # SOC + cheap price can never unlock overnight boost (#800 in a new costume).
                normal_cannot_reach_target = (
                    terminal_penalty_idx is not None
                    and slot_idx < terminal_penalty_idx
                    and config.max_normal_gain_pct_to_terminal is not None
                    and slot_idx < len(config.max_normal_gain_pct_to_terminal)
                    and soc_pct + config.max_normal_gain_pct_to_terminal[slot_idx]
                    < config.demand_window_target_soc_pct
                )
                # Hard DW-target feasibility gate (issue #885): while strictly below the
                # hard floor in a pre-DW slot, unlock boost so the DP actually HAS a
                # fast-enough path to clear the floor. Without this, normal-rate charging
                # can arrive at the DW under target (the live "boost downshifted to grid"
                # failure) and the terminal pruning would have no feasible action to route
                # through. The ``slot_idx < terminal_penalty_idx`` guard (shared with the
                # floor's own scope) confines this to pre-DW slots — a post-DW low SOC can
                # never unlock overnight boost (#800 protection).
                hard_floor_needs_boost = (
                    config.hard_target_floor is not None
                    and terminal_penalty_idx is not None
                    and slot_idx < terminal_penalty_idx
                    and soc_pct < config.hard_target_floor
                )
                if (
                    price_is_very_cheap
                    or normal_cannot_reach_target
                    or hard_floor_needs_boost
                ):
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


def cheap_threshold_for_slot(
    config: OptimizerConfig,
    slot_idx: int,
    terminal_penalty_idx: int | None,
) -> float:
    """Return the cheap-price threshold to apply to a slot's grid-charge gate.

    Shared by the feasibility gate (``feasible_actions``), the futile-cycling penalty
    (``penalties``), and the reason-code labels (``reason_codes``) so all three agree on
    what counts as "cheap" for a given slot.

    Issue #800 (overnight SOC floor bounce / sawtooth).
    ``effective_cheap_price`` is a "now" value that today's low-solar urgency may have
    inflated above the genuinely-cheap base (to fund pre-charge before the demand window).
    That urgency only legitimately applies to slots close to the upcoming demand window, so
    the inflated value is used only inside the urgency window
    ``[urgency_window_start_idx, terminal_penalty_idx)``; every other slot (post-DW, and any
    slot earlier than the deficit-derived urgency window — e.g. tonight's overnight when the
    next horizon DW is tomorrow evening) is gated on the un-inflated ``base_cheap_price``.
    The window width is deficit-derived (floor 4h, cap 8h) via
    ``dp_math.urgency_window_hours``, not a fixed 4h. Using the inflated
    value outside that window classifies overnight slots as "cheap" and drives net-negative
    sawtooth charging.

    Backward compatible: when ``urgency_window_start_idx`` is None (e.g. direct unit calls),
    the inflated price applies to all pre-DW slots (legacy: base only at/after the DW entry).

    Target-first eligibility (2026-06-12): when ``config.pre_dw_charge_thresholds`` is
    present (computed by ``compute_pre_dw_charge_thresholds``), pre-DW slots return their
    precomputed per-slot threshold instead — sized so the demand-window target is fundable
    from the cheapest sufficient slots up to ``max_precharge_price``, and time-consistent
    (each slot gated at the urgency the live controller will have when it arrives). Slots
    at/after the DW entry never use the list, so post-DW gating is byte-for-byte the
    legacy base-price behaviour (#800 protection).
    """
    pre_dw = config.pre_dw_charge_thresholds
    if (
        pre_dw is not None
        and terminal_penalty_idx is not None
        and slot_idx < terminal_penalty_idx
        and slot_idx < len(pre_dw)
    ):
        return pre_dw[slot_idx]

    threshold = config.effective_cheap_price
    if config.base_cheap_price is None or terminal_penalty_idx is None:
        return threshold

    uw_start = config.urgency_window_start_idx
    if uw_start is None:
        in_urgency_window = slot_idx < terminal_penalty_idx  # legacy
    else:
        in_urgency_window = uw_start <= slot_idx < terminal_penalty_idx

    if not in_urgency_window:
        threshold = min(threshold, config.base_cheap_price)
    return threshold


def compute_pre_dw_charge_thresholds(
    slots: list[SlotContext],
    config: OptimizerConfig,
    terminal_penalty_idx: int | None,
    initial_soc_pct: float,
) -> list[float] | None:
    """Per-slot charge-price thresholds that make the DW target fundable (2026-06-12).

    The hard cheap-price gate in ``feasible_actions`` is the load-bearing anti-sawtooth
    lever (soft penalties get paid through — #800/#804/#816), but gating *pre-DW* slots at
    the cheap percentile made the demand-window target structurally unreachable on days
    with few cheap slots: 2026-06-12 live, a 12.7 kWh deficit met ~1.2 h of eligible slot
    time, the plan entered the DW at 48.5% vs a 95% target, and held at 11–13 ¢ midday
    while scheduling 13–15.7 ¢ imports all evening. The #624 hard target constraint and
    the shortfall penalty could not fix it: no penalty can buy an action that is not in
    the feasible set.

    For each slot before the DW entry this returns the max of:

    - the legacy ``cheap_threshold_for_slot`` value (never tightens the gate),
    - the urgency ramp (``dp_math.urgency_ramp_price``) evaluated at that slot's OWN time,
      so a morning plan sees the same near-DW unlocks the live controller's re-plans will
      apply when those slots arrive (fixes the now-scalar time-inconsistency that made
      plans procrastinate and lie), and
    - the "water level": the marginal buy price of the cheapest set of pre-DW slots whose
      combined boost-rate charge capacity closes the SOC deficit to target (net of
      accuracy-discounted solar, mirroring ``check_global_solar_sufficiency``). Eligibility
      by price ≤ water level IS the cheapest-sufficient-set: the DP funds the target from
      the cheapest slots first and pricier slots unlock only when genuinely needed.

    Everything is clamped to ``config.max_precharge_price`` — the operator's existing
    pre-charge authorization ceiling. Slots at/after the DW entry keep their legacy
    thresholds untouched (post-DW/overnight gating on ``base_cheap_price`` is the #800
    protection and this function never widens it).

    Water-level capacity sizing uses the boost rate and ignores the >80% charge taper
    (slightly optimistic, i.e. the water level errs low); the ramp term guarantees
    eligibility keeps widening toward the ceiling as the DW approaches, so taper optimism
    cannot strand the target.

    Side effect: records the raw funding water level (None when there is no deficit) on
    ``config.pre_dw_funding_water_level`` so the min-cycle-saving exemption can scope
    itself to genuinely target-funding charges (see ``core._is_urgency_precharge``).

    Returns None (feature inert, legacy behaviour) when there is no demand window, the
    mode is not self-consumption, ``max_precharge_price`` is unset, or the ceiling does
    not exceed the ramp base. Callers must reset ``config.pre_dw_charge_thresholds`` AND
    ``config.pre_dw_funding_water_level`` to None before invoking (this function reads
    legacy thresholds via ``cheap_threshold_for_slot``, which consults the former, and
    the inert early-returns never touch the latter).
    """
    if (
        terminal_penalty_idx is None
        or terminal_penalty_idx <= 0
        or config.optimization_mode != "self_consumption"
        or config.max_precharge_price is None
        or not slots
        or terminal_penalty_idx >= len(slots)
    ):
        return None

    from datetime import datetime

    from custom_components.localshift.engine.dp_math import (
        urgency_ramp_price,
        urgency_window_hours,
    )

    ramp_base = (
        config.base_cheap_price
        if config.base_cheap_price is not None
        else config.effective_cheap_price
    )
    max_price = config.max_precharge_price
    if max_price <= ramp_base:
        return None

    legacy = [
        cheap_threshold_for_slot(config, j, terminal_penalty_idx)
        for j in range(len(slots))
    ]

    try:
        dw_time = datetime.fromisoformat(slots[terminal_penalty_idx].timestamp_iso)
    except (ValueError, TypeError):
        return None

    window_hours = urgency_window_hours(
        initial_soc_pct,
        config.demand_window_target_soc_pct,
        config.battery_capacity_kwh,
        config.charge_rate_kw,
        config.charge_efficiency,
    )

    required_stored_kwh = _required_stored_kwh_to_target(
        slots, config, terminal_penalty_idx, initial_soc_pct
    )
    water = _target_funding_water_level(
        slots, config, terminal_penalty_idx, required_stored_kwh, max_price
    )
    # Expose the raw funding water level (None when there is no deficit) so the
    # min-cycle-saving exemption can distinguish target-funding charges (price ≤ water)
    # from merely legacy-cheap ones (see ``_is_urgency_precharge``). Deliberately NOT
    # floored at ramp_base: the floor would re-admit every base-percentile-cheap slot,
    # which is exactly the non-funding population the exemption must exclude.
    config.pre_dw_funding_water_level = water

    thresholds: list[float] = []
    for j, slot in enumerate(slots):
        if j >= terminal_penalty_idx:
            thresholds.append(legacy[j])
            continue
        try:
            slot_time = datetime.fromisoformat(slot.timestamp_iso)
            hours_to_dw = max(0.0, (dw_time - slot_time).total_seconds() / 3600.0)
            ramp_j = urgency_ramp_price(ramp_base, max_price, hours_to_dw, window_hours)
        except (ValueError, TypeError):
            ramp_j = ramp_base
        # ramp_j and water are ≤ max_price by construction; legacy is deliberately NOT
        # clamped to the ceiling — on a spike day whose percentile base exceeds
        # max_precharge_price this function must never tighten the existing gate.
        # ramp_j ≥ ramp_base always, so a None water (no deficit) needs no substitute.
        thresholds.append(max(legacy[j], ramp_j, water if water is not None else 0.0))
    return thresholds


def _required_stored_kwh_to_target(
    slots: list[SlotContext],
    config: OptimizerConfig,
    terminal_penalty_idx: int,
    initial_soc_pct: float,
) -> float:
    """Grid charge (stored kWh) needed to reach the DW target, net of expected solar.

    Expected SOC at DW entry on solar alone, discounting only the positive gain by
    forecast accuracy — the same shape as ``check_global_solar_sufficiency``, so the
    required grid charge is no more optimistic than the solar feasibility gate
    (the raw-vs-discounted asymmetry behind the 2026-06-09 undercharge / #816).
    """
    from custom_components.localshift.engine.dp_math import (
        _simulate_solar_only_terminal_soc,
    )

    sim_soc = _simulate_solar_only_terminal_soc(
        initial_soc_pct, slots, terminal_penalty_idx, config
    )
    accuracy = max(0.0, min(1.0, config.solar_forecast_accuracy))
    if sim_soc >= initial_soc_pct:
        expected_soc_at_dw = initial_soc_pct + (sim_soc - initial_soc_pct) * accuracy
    else:
        expected_soc_at_dw = sim_soc
    return max(
        0.0,
        (config.demand_window_target_soc_pct - expected_soc_at_dw)
        / 100.0
        * config.battery_capacity_kwh,
    )


def _target_funding_water_level(
    slots: list[SlotContext],
    config: OptimizerConfig,
    terminal_penalty_idx: int,
    required_stored_kwh: float,
    max_price: float,
) -> float | None:
    """Marginal buy price of the cheapest pre-DW slot set that closes the deficit.

    Eligibility by ``price <= water level`` IS cheapest-sufficient-set funding: the DP
    funds the target from the cheapest slots first and pricier slots unlock only when
    genuinely needed. When even every pre-DW slot together cannot close the deficit,
    the operator's full ``max_precharge_price`` ceiling is authorized.

    Returns the raw water level (un-floored by the ramp base — callers that fold it into
    the per-slot threshold max already include the ramp term), or None when there is no
    deficit and hence nothing to fund.
    """
    if required_stored_kwh <= 0.0:
        return None

    candidates: list[tuple[float, float]] = []
    for j in range(terminal_penalty_idx):
        slot = slots[j]
        if slot.is_demand_window_slot:
            continue
        stored_kwh = (
            config.boost_charge_rate_kw
            * (slot.slot_interval_minutes / 60.0)
            * config.charge_efficiency
        )
        if stored_kwh > 0.0:
            candidates.append((slot.buy_price, stored_kwh))
    candidates.sort(key=lambda c: c[0])

    water = max_price
    cumulative = 0.0
    for price, stored_kwh in candidates:
        cumulative += stored_kwh
        if cumulative >= required_stored_kwh:
            water = price
            break
    return min(water, max_price)


def compute_max_normal_gain_pct_to_terminal(
    slots: list[SlotContext],
    config: OptimizerConfig,
    terminal_penalty_idx: int | None,
) -> list[float] | None:
    """Max SOC %-points normal-rate grid charging can add from each slot to the DW entry.

    For each slot index ``i < terminal_penalty_idx`` this returns the most SOC the battery
    could gain by charging at the *normal* grid rate in every chargeable slot from ``i`` up
    to (but not including) the demand-window entry — i.e. an SOC-independent upper bound on
    reachable gain. The shortfall-aware boost gate in ``feasible_actions`` compares
    ``soc_pct + gain[i]`` against the target: when even this optimistic normal-rate ceiling
    falls short, boost is unlocked so the DP keeps a feasible path to target.

    A slot contributes ``charge_rate_kw × slot_hours × charge_efficiency /
    battery_capacity_kwh × 100`` %-points when it is non-DW and its price is at/below the
    per-slot cheap threshold (``cheap_threshold_for_slot``); otherwise it carries the
    running suffix sum unchanged. Entries at/after ``terminal_penalty_idx`` are ``0.0``.

    The charge taper (``charge_taper_start_pct``) is deliberately ignored: it depends on the
    SOC *trajectory*, which is incompatible with this SOC-independent precompute. That makes
    the gain slightly optimistic only near the 80%+ taper boundary, so boost can be
    under-granted there — a safe error direction (we never over-boost from this bound).

    Pre-DW solar gain is likewise ignored, which biases the other way: boost can unlock even
    when normal-rate charging *plus* solar would have reached target. This is benign — the DP
    still cost-selects, and at an equal price boost costs the same per kWh as normal, so an
    over-grant of *feasibility* does not force an over-charge.

    Returns ``None`` when there is no demand window (``terminal_penalty_idx is None``) or the
    mode is not self-consumption, which keeps the gate dormant (boost only at very-cheap) for
    existing tests and direct callers.
    """
    if terminal_penalty_idx is None or config.optimization_mode != "self_consumption":
        return None

    gains = [0.0] * len(slots)
    cumulative = 0.0
    for j in range(min(terminal_penalty_idx, len(slots)) - 1, -1, -1):
        slot = slots[j]
        if not slot.is_demand_window_slot:
            threshold = cheap_threshold_for_slot(config, j, terminal_penalty_idx)
            if slot.buy_price <= threshold:
                slot_hours = slot.slot_interval_minutes / 60.0
                cumulative += (
                    config.charge_rate_kw
                    * slot_hours
                    * config.charge_efficiency
                    / config.battery_capacity_kwh
                    * 100.0
                )
        gains[j] = cumulative
    return gains


def compute_max_feasible_terminal_soc(
    slots: list[SlotContext],
    config: OptimizerConfig,
    terminal_penalty_idx: int | None,
    initial_soc_pct: float,
) -> float | None:
    """Highest SOC the DW-entry slot can physically reach via eligible pre-DW charging.

    Hard DW-target feasibility gate (issue #885). The soft shortfall penalty is
    structurally capped below grid-charge prices, so the DP pays through it and the
    battery enters the demand window under target with no backstop. To make target a
    HARD constraint without forcing charging through ineligible (expensive) or
    post-DW slots, the solver needs to know what SOC is actually *reachable* at the
    DW-entry slot — the floor it can prune below.

    This forward-simulates the most optimistic eligible trajectory to the DW entry:
    every pre-DW, non-DW slot whose price is at/below its per-slot eligibility
    threshold (``cheap_threshold_for_slot`` — the same #870 water-level / urgency-ramp
    gate ``feasible_actions`` uses, so this NEVER admits a slot the DP could not itself
    charge in) contributes a boost-rate charge; every other slot evolves on solar/load
    only. The result is clamped to the charge ceiling (target in self-consumption mode).

    The simulation is measured at the SOC ENTERING the DW-entry slot (before that slot's
    own consumption drift) — matching how the DP applies the terminal penalty in
    ``_backward_induction`` (to ``dp[terminal_penalty_idx][bin]``, keyed by the SOC at the
    START of the entry slot). Measuring post-entry-decay instead would understate the
    reachable floor by one slot's load and make a physically-reachable target look unmet.

    Because charging is the most this trajectory ever does, the returned SOC is an upper
    bound on what any feasible plan can reach at the DW entry. The solver uses
    ``min(target, this)`` as the hard floor: when target is reachable the floor IS the
    target; when it is physically unreachable (too little time/rate/eligible-cheap
    energy) the floor degrades gracefully to the max feasible SOC, so the DP still
    produces a non-empty plan that charges as far as it can rather than an infeasible one.

    Returns ``None`` (gate inert) when there is no demand window, the mode is not
    self-consumption, or the entry is slot 0 (nothing earlier to charge in).
    """
    if (
        terminal_penalty_idx is None
        or terminal_penalty_idx <= 0
        or config.optimization_mode != "self_consumption"
        or not slots
        or terminal_penalty_idx >= len(slots)
    ):
        return None

    charge_ceiling = config.demand_window_target_soc_pct
    soc = initial_soc_pct
    # Iterate only the pre-DW slots: the floor is the SOC ENTERING the DW-entry slot.
    for i in range(terminal_penalty_idx):
        slot = slots[i]
        slot_hours = slot.slot_interval_minutes / 60.0
        # Solar/load drift first (mirrors the solar-only simulation shape).
        net_kwh = slot.solar_kwh - slot.consumption_kwh
        max_solar_transfer = config.solar_charge_rate_kw * slot_hours
        if net_kwh >= 0:
            soc += (
                min(net_kwh, max_solar_transfer)
                * config.charge_efficiency
                / config.battery_capacity_kwh
                * 100.0
            )
        else:
            soc += (
                max(net_kwh, -max_solar_transfer)
                / config.discharge_efficiency
                / config.battery_capacity_kwh
                * 100.0
            )
        # Then the most optimistic eligible grid charge (boost) in this pre-DW slot.
        if not slot.is_demand_window_slot and soc < charge_ceiling:
            threshold = cheap_threshold_for_slot(config, i, terminal_penalty_idx)
            if slot.buy_price <= threshold:
                soc += (
                    config.boost_charge_rate_kw
                    * slot_hours
                    * config.charge_efficiency
                    / config.battery_capacity_kwh
                    * 100.0
                )
        soc = max(config.min_soc_pct, min(charge_ceiling, soc))
    return soc


def check_global_solar_sufficiency(
    soc_pct: float,
    slot_idx: int,
    slots: list[SlotContext],
    config: OptimizerConfig,
    terminal_penalty_idx: int | None = None,
) -> bool:
    """Check if remaining solar before the deadline covers the SOC deficit to target.

    Uses realistic simulation that accounts for charge rate limits and efficiency,
    ensuring consistency with solar_can_reach_target sensor.

    Fixes Issue #701: Previous implementation used raw surplus without rate limits,
    incorrectly blocking cheap grid charging when solar was insufficient in reality.

    Args:
        soc_pct: Current battery SOC percentage.
        slot_idx: Index of the current slot in the planning horizon.
        slots: Full list of planning slots.
        config: Optimizer configuration.
        terminal_penalty_idx: Index at which the shortfall penalty is applied.

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
    relative_terminal_idx: int | None = None
    if terminal_penalty_idx is not None:
        if terminal_penalty_idx < slot_idx:
            return False
        relative_terminal_idx = terminal_penalty_idx - slot_idx

    simulated_terminal_soc = _simulate_solar_only_terminal_soc(
        soc_pct, remaining_slots, relative_terminal_idx, config
    )

    # Discount the projected solar gain by forecast accuracy so this hard
    # feasibility gate is no more optimistic than the shortfall cost model
    # (reason_codes._is_target_shortfall_risk, which applies the same discount).
    # Without it, a low-accuracy forecast that over-projects pre-DW solar makes the
    # gate conclude "solar reaches target" and strips grid pre-charge from the
    # feasible set, causing demand-window undercharge (2026-06-09 incident; #816).
    # Mirrors the classifier: discount only the positive gain over current SOC.
    accuracy = max(0.0, min(1.0, config.solar_forecast_accuracy))
    discounted_gain = max(0.0, simulated_terminal_soc - soc_pct) * accuracy
    return (soc_pct + discounted_gain) >= config.demand_window_target_soc_pct


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

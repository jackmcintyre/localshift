"""Core DPPlanner implementation for battery optimization."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

from custom_components.localshift.engine.constraints import (
    check_global_solar_sufficiency,
    compute_max_feasible_terminal_soc,
    compute_max_normal_gain_pct_to_terminal,
    compute_pre_dw_charge_thresholds,
)
from custom_components.localshift.engine.constraints import (
    feasible_actions as _constraints_feasible_actions,
)
from custom_components.localshift.engine.cost import (
    stage_cost as _cost_stage_cost,
)
from custom_components.localshift.engine.cost import (
    terminal_cost as _cost_terminal_cost,
)
from custom_components.localshift.engine.cost import (
    terminal_salvage_value as _cost_terminal_salvage_value,
)
from custom_components.localshift.engine.dp_math import (
    _build_soc_grid,
    _interpolate_cost_to_soc,
    _map_soc_to_bin,
    _simulate_max_soc_in_demand_window,
    urgency_window_hours,
)
from custom_components.localshift.engine.negative_fit import (
    derive_negative_fit_avoidance_context,
)
from custom_components.localshift.engine.penalties import (
    get_futile_cycling_penalty_factor,
    get_solar_opportunity_penalty_factor,
)
from custom_components.localshift.engine.reason_codes import classify_reason
from custom_components.localshift.engine.solar import (
    can_solar_reach_target,
    can_solar_reach_target_feasible,
    get_forecast_accuracy,
    projected_solcast_gain_pct,
)
from custom_components.localshift.engine.spike_event import find_funding_slots
from custom_components.localshift.engine.transitions import _tapered_stored_kwh
from custom_components.localshift.engine.transitions import transition as _transition
from custom_components.localshift.engine.types import (
    NegativeFitAvoidanceContext,
    OptimizerConfig,
    OptimizerInputs,
    OptimizerResult,
    PlannedSlotDecision,
    PlannerAction,
    SlotContext,
)

_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Action priority for deterministic tie-breaking (lower index = higher priority)
# -----------------------------------------------------------------------------

_ACTION_PRIORITY: dict[PlannerAction, int] = {
    PlannerAction.HOLD: 0,
    PlannerAction.CHARGE_GRID_NORMAL: 1,
    PlannerAction.CHARGE_GRID_BOOST: 2,
    PlannerAction.EXPORT_PROACTIVE: 3,
}

_GRID_CHARGE_ACTIONS = (
    PlannerAction.CHARGE_GRID_NORMAL,
    PlannerAction.CHARGE_GRID_BOOST,
)


def _evaluate_action_cost(
    action: PlannerAction,
    soc: float,
    slot: SlotContext,
    slot_idx: int,
    slots: list[SlotContext],
    soc_grid: list[float],
    dp_next: dict,
    config: OptimizerConfig,
    inputs: OptimizerInputs,
    terminal_penalty_idx: int | None,
) -> tuple[float, int, float, float, float, float]:
    """Compute per-action cost for a single (slot, soc-bin, action) triple.

    Returns a plain tuple of (next_soc, next_bin, grid_import, grid_export,
    charge_kwh, total_cost) with all intermediate quantities already applied:
    SOC clamping, bin lookup with inf-fallback interpolation, switching cost,
    Issue #610 solar-opportunity factor, Issue #638 futile-cycling factor, and
    stage + future cost summation.

    Args:

        action: Action to evaluate.
        soc: Current SOC percentage.
        slot: Current slot context.
        slot_idx: Index of the current slot.
        slots: All slot contexts.
        soc_grid: SOC discretisation grid.
        dp_next: dp[slot_idx + 1] — the next-slot value table (NOT the whole dp list).
        config: Optimizer config.
        inputs: Optimizer inputs (for current_action and all_solcast).
        terminal_penalty_idx: Terminal penalty index or None.

    Returns:

        Tuple of (next_soc, next_bin, grid_import, grid_export, charge_kwh, total_cost).

    """

    next_soc, grid_import, grid_export = _transition(soc, action, slot, config)
    next_soc = max(config.min_soc_pct, min(config.max_soc_pct, next_soc))
    next_bin = _map_soc_to_bin(next_soc, soc_grid)
    future_cost = dp_next.get(next_bin, (float("inf"),))[0]

    if future_cost == float("inf") and dp_next:
        future_cost = _interpolate_cost_to_soc(
            next_soc, soc_grid, {k: v[0] for k, v in dp_next.items()}
        )

    is_switch = (
        slot_idx == 0
        and inputs.current_action is not None
        and action != inputs.current_action
    )

    # Issue #610: horizon-aware solar opportunity cost

    solar_opp_factor = get_solar_opportunity_penalty_factor(
        action=action,
        grid_import_kwh=grid_import,
        slot=slot,
        slot_idx=slot_idx,
        slots=slots,
        config=config,
        terminal_penalty_idx=terminal_penalty_idx,
        all_solcast=inputs.all_solcast,
    )

    # Issue #638: futile cycling penalty

    charge_kwh = max(0.0, next_soc - soc) / 100.0 * config.battery_capacity_kwh

    futile_factor = get_futile_cycling_penalty_factor(
        action=action,
        slot_idx=slot_idx,
        slots=slots,
        config=config,
        soc_after_charge_pct=next_soc,
        charge_kwh=charge_kwh,
        terminal_penalty_idx=terminal_penalty_idx,
    )

    stage = _cost_stage_cost(
        action,
        grid_import,
        grid_export,
        slot,
        config,
        soc_pct=soc,
        is_switch=is_switch,
        solar_opportunity_penalty_factor=solar_opp_factor,
        futile_cycling_penalty_factor=futile_factor,
    )
    total_cost = stage.net_cost + future_cost

    return next_soc, next_bin, grid_import, grid_export, charge_kwh, total_cost


def _is_urgency_precharge(
    slot_idx: int,
    soc: float,
    buy_price: float,
    terminal_penalty_idx: int | None,
    config: OptimizerConfig,
) -> bool:
    """Return True when this slot qualifies as a demand-window pre-charge that must be exempted from the min-cycle-saving gate.

    Demand-window pre-charge exemption (2026-06-11 sub-target incident): a charge
    inside the urgency window that is still below the DW target is needed to reach
    that target, not speculative cycling. Without exempting it, the min-cycle-saving
    gate drops each early pre-charge slot — its per-slot margin over simply deferring
    the charge is below the threshold because the charge "could" happen later — and
    those deferrals compound: by the time charging is forced it is deep in the
    taper region with too few slots left, so the plan enters the DW under target
    (live: 91.8% vs a 95% target, holding the first slots instead of charging).
    Mirrors the SOC-floor anti-sawtooth guard's urgency-window exemption below.
    Safe vs the #800 overnight sawtooth: those slots are post-DW / far pre-DW and
    never inside an urgency window, so the gate stays fully active there.

    Target-first eligibility (2026-06-12): a pre-DW charge at/below the funding
    water level (config.pre_dw_funding_water_level — the marginal price of the
    cheapest sufficient slot set) is target-driven even outside the 4-8h urgency
    window, so it is exempted too. Without this, min-cycle-saving re-introduces
    the procrastination the per-slot thresholds exist to fix (each early slot's
    margin over deferring is thin, the deferrals compound, and the plan
    undershoots — the #860 incident shape).

    The water-level test is deliberately NOT "pre_dw_charge_thresholds is not
    None": those thresholds are max(legacy, ramp, water), and the LEGACY
    component keeps slots eligible for reasons unrelated to funding the target.
    A blanket exemption re-enabled the #800 overnight sawtooth (2026-06-13
    regression: a 12.5¢ floor-bounce charge at 03:00, fully drained before the
    8.3¢ midday slots that actually fund the DW target). Energy charged in a
    far-out, above-water slot drains to the SOC floor before the DW, funds
    nothing, and must face the gate. Post-DW slots are never exempted by either
    branch.

    Args:

        slot_idx: Index of the current slot.
        soc: Current SOC percentage.
        buy_price: The slot's grid buy price ($/kWh).
        terminal_penalty_idx: Terminal penalty index or None.
        config: Optimizer config.

    Returns:

        True when the slot should be exempt from the min-cycle-saving gate.

    """

    # NOTE: spike-event funding slots are deliberately NOT exempted here.
    #
    # #908 exempted them, reasoning that qualification already uses min_cycle_saving as
    # its bar so the gate is redundant. It is not: the two compare different quantities.
    # ``spike_event.find_funding_slots`` compares SLOT PRICES
    # (``slots[i].buy_price - slots[j].buy_price >= spread``); this gate compares the DP's
    # REAL COSTS (``hold_total_cost - charge_total_cost >= min_cycle_saving * charge_kwh``),
    # which nets off round-trip losses, the switching penalty, solar that would have filled
    # the battery for free anyway, and — decisively — whether the stored energy actually
    # survives to the dear slot instead of bleeding into overnight load first. A slot can
    # pass the price-spread test and still fail the real economics, and exempting it left
    # the codebase's ONLY hard anti-cycling gate switched off across up to half the horizon
    # (measured: qualification fires on 55% of ordinary days). That is the #800 sawtooth's
    # entry point, and ``_solve_guarded`` cannot catch it — it selects on the DP objective,
    # which is exactly the metric this gate exists to correct.
    #
    # The anti-procrastination rationale borrowed from the demand-window branches below
    # does not transfer either. Those exist because deferring a pre-charge can miss a
    # DEADLINE (the DW target, with its terminal penalty). A price spike has no deadline:
    # charging one slot later still captures it. Measured on the live 2026-08-05 fixture,
    # keeping the gate on still serves the $1.65 slot entirely from the battery
    # (spike-slot import $0.00) and still halves the morning block; it costs $0.28 of
    # capture by charging at 06:00 rather than 05:30. That is the gate doing its job.
    #
    # Non-monotonicity — the other reason #908 gave — is already dominated by
    # ``_solve_guarded``, which keeps whichever plan is cheaper.

    return (
        terminal_penalty_idx is not None
        and slot_idx < terminal_penalty_idx
        and soc < config.demand_window_target_soc_pct
        and (
            (
                config.pre_dw_funding_water_level is not None
                and buy_price <= config.pre_dw_funding_water_level
            )
            or (
                config.urgency_window_start_idx is not None
                and config.urgency_window_start_idx <= slot_idx
            )
        )
    )


def _min_cycle_saving_blocks(
    action: PlannerAction,
    charge_kwh: float,
    total_cost: float,
    hold_total_cost: float | None,
    is_urgency_precharge: bool,
    config: OptimizerConfig,
) -> bool:
    """Return True when the min-cycle-saving gate blocks the given charge action.

    Min-cycle-saving gate (anti-micro-cycling): a grid charge is only worth
    a battery cycle if it beats simply holding by at least
    config.min_cycle_saving dollars per kWh charged. The margin
    (hold_total_cost - total_cost) is the DP's real cost difference, so it
    already credits every value source the optimizer sees — evening-peak
    avoidance, the demand-window target, backup readiness — via future_cost.
    That preserves genuine pre-charge and spike capture while dropping thin
    speculative arbitrage. A HARD skip; unlike the soft penalties (#606/#804)
    it cannot be paid through. HOLD is appended first by feasible_actions, so
    hold_total_cost is set before any charge action is evaluated.

    Args:

        action: Action being evaluated.
        charge_kwh: kWh of charge gained by the action (from clamped next_soc).
        total_cost: Total DP cost of the action (stage + future).
        hold_total_cost: Total DP cost of the HOLD action for this state (or None
            when HOLD has not yet been evaluated — should not happen in practice).
        is_urgency_precharge: Whether this slot is exempt from the gate.
        config: Optimizer config.

    Returns:

        True when the action should be skipped (gate fires).

    """

    if not (
        config.min_cycle_saving > 0.0
        and charge_kwh > 0.0
        and hold_total_cost is not None
        and not is_urgency_precharge
        and action in _GRID_CHARGE_ACTIONS
    ):
        return False
    saving = hold_total_cost - total_cost
    return 0.0 < saving < config.min_cycle_saving * charge_kwh


def _floor_guard_blocks(
    action: PlannerAction,
    soc: float,
    next_soc: float,
    slot_idx: int,
    terminal_penalty_idx: int | None,
    config: OptimizerConfig,
) -> bool:
    """Return True when the SOC-floor anti-sawtooth guard blocks the given charge action.

    SOC-floor anti-sawtooth guard: at SOC floor, only allow charging if:
    1. Charge amount is meaningful (> min_floor_charge_gain_pct SOC), OR
    2. We are within the urgency window before the demand window.

    Tiny charges at the SOC floor without an urgent need produce sawtooth
    oscillation — the optimizer charges by a hair, decays back to floor,
    and repeats — so they are skipped. The urgency-window exemption mirrors
    the min-cycle-saving gate's exemption: slots inside the urgency window
    are target-driven and must not be blocked.

    Args:

        action: Action being evaluated.
        soc: Current SOC percentage.
        next_soc: Next SOC percentage (after clamping).
        slot_idx: Index of the current slot.
        terminal_penalty_idx: Terminal penalty index or None.
        config: Optimizer config.

    Returns:

        True when the action should be skipped (guard fires).

    """

    if not (
        soc <= config.min_soc_pct + config.min_soc_floor_buffer_pct
        and action in _GRID_CHARGE_ACTIONS
    ):
        return False
    charge_soc_gain = next_soc - soc
    in_urgency_window = (
        terminal_penalty_idx is not None
        and config.urgency_window_start_idx is not None
        and slot_idx >= config.urgency_window_start_idx
    )
    return charge_soc_gain < config.min_floor_charge_gain_pct and not in_urgency_window


class DPPlanner:
    """Deterministic dynamic-programming battery optimizer.

    State space: (slot_index, soc_bin)
    Actions: PlannerAction enum
    Objective: minimize total net cost including shortfall penalty

    Phase C: Full DP implementation with deterministic tie-breaking.
    """

    VERSION = "dp_v1"

    def __init__(self, config: OptimizerConfig | None = None) -> None:

        self._config = config or OptimizerConfig()

    # ------------------------------------------------------------------

    # Public API

    # ------------------------------------------------------------------

    def plan(self, inputs: OptimizerInputs) -> OptimizerResult:
        """

        Run the DP optimizer over the provided inputs.
        Returns an OptimizerResult. On success, decisions contains one
        PlannedSlotDecision per slot in inputs.slots.

        """

        start = time.monotonic()

        try:
            result = self._solve_guarded(inputs)

        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "DPPlanner.plan() failed for cycle %s: %s", inputs.cycle_id, exc
            )

            return OptimizerResult(
                success=False,
                planner_version=self.VERSION,
                solve_time_seconds=time.monotonic() - start,
                error_message=str(exc),
            )

        result.solve_time_seconds = time.monotonic() - start

        return result

    def _solve_guarded(self, inputs: OptimizerInputs) -> OptimizerResult:
        """Solve with and without spike funding, keeping the cheaper plan.

        WHY A GUARD RATHER THAN A BETTER GATE
        -------------------------------------
        This DP is approximate: the min-cycle-saving gate prunes actions using a
        comparison that is not part of the (slot, soc_bin) state, so enlarging the
        feasible set is NOT guaranteed to improve the optimum. A 200-scenario sweep
        of the qualification rule on its own found ~4% of firing scenarios came out
        WORSE than the unmodified planner (worst case $0.49) — the same shape as the
        long tail of anti-cycling regressions (#800/#804/#816).

        Rather than chase monotonicity in an approximate solver, dominate it: solve
        both ways and keep the winner on projected net cost. Harm becomes impossible
        by construction, and the sweep's no-harm and monotonicity properties both go
        from FAIL to PASS. Cost is one extra solve (~0.04 s live) and it is only paid
        on days where something actually qualifies.

        Falls back to the baseline plan on any failure in the spike path, so a bug
        here can degrade the feature but never the planner.
        """
        baseline = self._solve(inputs)

        config = inputs.config
        if not getattr(config, "spike_precharge_enabled", True):
            return baseline

        try:
            funding = find_funding_slots(inputs.slots, config)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Spike funding detection failed, using baseline: %s", exc)
            return baseline

        if not funding:
            return baseline

        try:
            config.spike_funding_slots = funding
            candidate = self._solve(inputs)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Spike-funded solve failed, using baseline: %s", exc)
            return baseline
        finally:
            config.spike_funding_slots = None

        if not candidate.success:
            return baseline

        # Positive => the widening would save money. Recorded either way: the common
        # outcome is a dead-on tie (qualification fires, the DP declines to use the
        # extra option, plans are identical), and lumping ties in with genuine
        # rejections makes a benign 40%-of-firing-cycles look like a 40% failure rate.
        delta = baseline.projected_net_cost - candidate.projected_net_cost

        # Strictly cheaper only — ties keep the baseline so the plan never churns
        # for nothing.
        #
        # A strict comparison is a knife edge, and this planner re-solves every few
        # minutes against a revised forecast, so a near-tie flips sign under ordinary
        # jitter and takes the committed action with it. That is survivable only because
        # qualification is now scoped to genuine spikes: an ordinary day produces no
        # funding slots at all, so there is no near-tie to flip (measured: 0 of 400
        # ordinary-day scenarios qualify, and the committed action no longer flaps in any
        # of them). Adding a margin HERE was tried and rejected — the per-slot
        # min-cycle-saving gate already charges the operator's cycle bar against this
        # same energy, so charging it a second time at plan level left just $0.06 of
        # headroom on the live 2026-08-05 spike, i.e. it made the incident fix fragile
        # while protecting against a case qualification no longer admits.
        if delta > 0:
            _LOGGER.info(
                "SPIKE PRECHARGE: %d funding slot(s) accepted, "
                "projected net cost $%.4f -> $%.4f (saves $%.4f)",
                len(funding),
                baseline.projected_net_cost,
                candidate.projected_net_cost,
                delta,
            )
            candidate.spike_funding_slot_count = len(funding)
            candidate.spike_funding_accepted = True
            candidate.spike_funding_net_cost_delta = delta
            return candidate

        if delta < 0:
            _LOGGER.debug(
                "SPIKE PRECHARGE: %d funding slot(s) REJECTED by guard — "
                "$%.4f would have been worse than $%.4f",
                len(funding),
                candidate.projected_net_cost,
                baseline.projected_net_cost,
            )
        else:
            _LOGGER.debug(
                "SPIKE PRECHARGE: %d funding slot(s) qualified but changed nothing",
                len(funding),
            )
        baseline.spike_funding_slot_count = len(funding)
        baseline.spike_funding_accepted = False
        baseline.spike_funding_net_cost_delta = delta
        return baseline

    # ------------------------------------------------------------------

    # Internal solve — Full DP Implementation (Phase C)

    # ------------------------------------------------------------------

    def _solve(self, inputs: OptimizerInputs) -> OptimizerResult:
        """

        Full DP solver implementation.
        Algorithm:

          1. Build SOC grid from config

          2. Forward pass: compute cost-to-go for all (slot, soc_bin) states

          3. Backward pass: reconstruct optimal action sequence

          4. Build PlannedSlotDecision list with reason codes

        """

        config = inputs.config
        slots = inputs.slots
        n_slots = len(slots)

        if n_slots == 0:
            return self._empty_result()

        soc_grid = _build_soc_grid(config)
        demand_bounds = self._find_demand_window_bounds(slots)
        solar_capable = can_solar_reach_target(inputs, slots, config, demand_bounds)

        terminal_penalty_idx = self._determine_terminal_penalty_idx(
            config, demand_bounds
        )

        # Publish the DW-entry slot index on the config alongside its siblings below.
        # urgency_window_start_idx is only interpretable against this bound, and
        # out-of-solver consumers (the pre-charge execution backstop) need "before the
        # demand window" without re-deriving the window. Must be assigned, not merely
        # local: the backstop reads it off the config after the plan returns.
        config.terminal_penalty_idx = terminal_penalty_idx

        # Issue #800 follow-up: the urgency-inflated effective_cheap_price is only valid
        # near the demand window. Record where the urgency window begins so the cheap-price
        # gate uses the inflated value only there and the un-inflated base elsewhere
        # (otherwise tonight's overnight — far before tomorrow's DW — sawtooths).
        config.urgency_window_start_idx = self._determine_urgency_window_start_idx(
            slots, terminal_penalty_idx, inputs.initial_soc_pct, config
        )

        # Feed live forecast accuracy into the pre-charge feasibility gate so it
        # discounts projected solar the same way the shortfall cost model does
        # (check_global_solar_sufficiency / _is_target_shortfall_risk). Prevents the
        # gate over-trusting an inaccurate forecast and blocking grid pre-charge
        # (2026-06-09 demand-window undercharge; recurrence of #816). Must come before
        # compute_pre_dw_charge_thresholds, whose required-charge estimate applies the
        # same discount.
        config.solar_forecast_accuracy = get_forecast_accuracy(
            inputs.solar_accuracy_tracker
        )

        # Target-first eligibility (2026-06-12): per-slot pre-DW charge thresholds sized
        # so the demand-window target is fundable from the cheapest sufficient slots up
        # to max_precharge_price, and time-consistent (each slot gated at the urgency it
        # will have when it arrives, not today's now-scalar). Reset first: the precompute
        # reads legacy thresholds via cheap_threshold_for_slot, which consults this field,
        # and its inert early-returns never write the water level.
        config.pre_dw_charge_thresholds = None
        config.pre_dw_funding_water_level = None
        config.hard_target_floor = None
        # Same reset discipline as the fields above: the config object is reused across
        # planning cycles, so stale runway telemetry from a previous cycle must never
        # survive into one whose early-return path never recomputes it.
        config.hard_floor_suppressed_by_solar = False
        config.precharge_runway_slack_min = None
        config.precharge_runway_quantum_min = 0.0
        config.pre_dw_charge_thresholds = compute_pre_dw_charge_thresholds(
            slots, config, terminal_penalty_idx, inputs.initial_soc_pct
        )

        # Shortfall-aware boost (2026-06-11 incident): precompute, per slot, the most SOC
        # normal-rate grid charging could add from that slot to the demand-window entry, so
        # the boost gate can unlock boost when normal alone cannot reach target. Must come
        # after urgency_window_start_idx and pre_dw_charge_thresholds (the precompute calls
        # cheap_threshold_for_slot, which reads both) and before _initialize_dp_tables.
        # Follows the existing config-attach precedent (urgency_window_start_idx /
        # solar_forecast_accuracy); per-call cost in the DP inner loop is one index + compare.
        config.max_normal_gain_pct_to_terminal = (
            compute_max_normal_gain_pct_to_terminal(slots, config, terminal_penalty_idx)
        )

        # Hard DW-target feasibility gate (issue #885). In strict mode
        # (allow_dw_entry_under_target=False, self-consumption) the soft shortfall penalty
        # is structurally capped below grid-charge prices, so the DP holds and enters the
        # demand window under target. Compute the HARD floor the terminal-penalty slot
        # must clear: min(target, max physically/eligibly reachable SOC). Below-floor
        # terminal states are pruned (effectively infinite penalty) in
        # _initialize_dp_tables, so backward induction routes through the CHEAPEST eligible
        # pre-DW slots. The floor degrades to the max feasible SOC when target is
        # unreachable (no infeasible/empty plan), is None when solar alone reaches target
        # (don't fight #816/#849), and is None outside strict self-consumption (legacy).
        config.hard_target_floor = self._compute_hard_target_floor(
            slots, config, terminal_penalty_idx, inputs, solar_capable
        )

        # Pre-charge runway telemetry (fast-follow to #901). Purely additive: publishes
        # WHY the hard floor is dormant and how much physical runway is left, without
        # touching the floor itself or anything the DP reads. Deliberately a separate
        # call rather than an extra return value from _compute_hard_target_floor — that
        # function feeds _initialize_dp_tables and feasible_actions, and this codebase's
        # DP-core regression history (#800/#804/#816) says not to reshape it for telemetry.
        self._publish_precharge_runway_telemetry(
            slots, config, terminal_penalty_idx, inputs, solar_capable
        )

        dp, terminal_penalty_by_bin = self._initialize_dp_tables(
            n_slots,
            soc_grid,
            config,
            terminal_penalty_idx,
            solar_capable,
            inputs,
            config.hard_target_floor,
        )

        # Issue #719: Derive negative-FIT avoidance context before backward induction

        negative_fit_avoidance_context = derive_negative_fit_avoidance_context(inputs)

        states_explored = self._backward_induction(
            dp,
            slots,
            soc_grid,
            config,
            terminal_penalty_idx,
            inputs,
            negative_fit_avoidance_context,
            terminal_penalty_by_bin,
        )

        decisions, totals, reason_histogram = self._forward_reconstruct(
            dp,
            inputs,
            slots,
            soc_grid,
            config,
            terminal_penalty_idx,
            negative_fit_avoidance_context,
        )

        terminal_shortfall = self._compute_terminal_shortfall(
            inputs, decisions, config, terminal_penalty_idx, demand_bounds
        )

        # Compute terminal diagnostics (PR #789 wiring fix)
        terminal_diags: dict[str, Any] = {}
        forecast_accuracy_val: float | None = None

        if terminal_penalty_idx is not None and not solar_capable:
            # Recompute terminal context values for diagnostics
            future_solar_gain_pct = 0.0
            if inputs.all_solcast and inputs.slots:
                from custom_components.localshift.forecast.analysis_resolver import (
                    ConfidenceResolver,
                )

                last_slot = inputs.slots[-1]
                last_slot_start = datetime.fromisoformat(last_slot.timestamp_iso)
                last_slot_end = last_slot_start + timedelta(
                    minutes=last_slot.slot_interval_minutes
                )
                target_slot = inputs.slots[terminal_penalty_idx]
                target_time = datetime.fromisoformat(target_slot.timestamp_iso)
                confidence_resolver = ConfidenceResolver(
                    inputs.solcast_analysis_today,
                    inputs.solcast_analysis_tomorrow,
                    absent_confidence=getattr(inputs, "solar_absent_confidence", 1.0),
                )
                future_solar_gain_pct = projected_solcast_gain_pct(
                    inputs.all_solcast,
                    start_time=last_slot_end,
                    end_time=target_time,
                    battery_capacity_kwh=config.battery_capacity_kwh,
                    confidence_resolver=confidence_resolver,
                )

            forecast_accuracy_val = get_forecast_accuracy(inputs.solar_accuracy_tracker)
            accuracy_discount = max(0.5, min(1.0, forecast_accuracy_val))

            terminal_diags = self._get_terminal_diagnostics(
                soc_pct=inputs.initial_soc_pct,
                target=config.demand_window_target_soc_pct,
                accuracy_discount=accuracy_discount,
                future_solar_gain_pct=future_solar_gain_pct,
                decisions=decisions,
                terminal_penalty_idx=terminal_penalty_idx,
            )

        can_solar = can_solar_reach_target_feasible(
            inputs, slots, config, terminal_penalty_idx
        )

        return OptimizerResult(
            success=True,
            planner_version=self.VERSION,
            total_slots=n_slots,
            states_explored=states_explored,
            decisions=decisions,
            projected_import_kwh=totals["import"],
            projected_export_kwh=totals["export"],
            projected_net_cost=totals["net_cost"],
            terminal_shortfall_pct=terminal_shortfall,
            can_solar_reach_target=can_solar,
            can_solar_reach_target_in_dw=solar_capable,
            reason_code_histogram=reason_histogram,
            # Terminal diagnostics (Issue #816: removed adjusted_solar_gain_pct, effective_soc_at_terminal)
            forecast_accuracy=forecast_accuracy_val,
            accuracy_discount_factor=terminal_diags.get("accuracy_discount_factor"),
            peak_soc_pct=terminal_diags.get("peak_soc_pct"),
            dw_entry_soc_pct=terminal_diags.get("dw_entry_soc_pct"),
        )

    def _empty_result(self) -> OptimizerResult:
        """Return empty optimizer result."""

        return OptimizerResult(
            success=True,
            planner_version=self.VERSION,
            total_slots=0,
            states_explored=0,
            decisions=[],
            reason_code_histogram={},
        )

    def _find_demand_window_bounds(
        self, slots: list[SlotContext]
    ) -> dict[str, int | None]:
        """Find demand window entry and end indices for the FIRST DW block.

        When cross-day scenarios have multiple DW blocks, only the first block

        is considered (Issue #633).

        Args:

            slots: List of slot contexts

        Returns:

            Dict with 'entry_idx' and 'end_idx' keys

        """

        entry_idx = None
        end_idx = None
        in_demand_window = False

        # When the plan is computed mid-demand-window, slot 0 is already inside the DW and
        # slots.py flags it as an entry (prev_in_demand_window is seeded False). That
        # in-progress DW is not a future deadline the optimizer can pre-charge for — taking
        # it as terminal_penalty_idx pins the target penalty at the present instant and
        # leaves the next REAL DW entry (e.g. tomorrow's) with no pre-charge incentive. So
        # ignore entries until we have exited that initial in-progress block.
        started_in_dw = bool(slots) and slots[0].is_demand_window_slot
        exited_initial_dw = not started_in_dw

        for i, slot in enumerate(slots):
            if not exited_initial_dw:
                if not slot.is_demand_window_slot:
                    exited_initial_dw = True
                else:
                    # Still inside the initial in-progress DW block: skip its (false) entry.
                    continue

            if slot.is_demand_window_entry:
                if entry_idx is None:
                    entry_idx = i

                elif in_demand_window:
                    end_idx = i - 1
                    break

            if slot.is_demand_window_slot:
                in_demand_window = True

            if in_demand_window and not slot.is_demand_window_slot:
                end_idx = i - 1
                break

        if in_demand_window and end_idx is None:
            end_idx = len(slots) - 1

        return {"entry_idx": entry_idx, "end_idx": end_idx}

    def _determine_terminal_penalty_idx(
        self, config: OptimizerConfig, demand_bounds: dict[str, int | None]
    ) -> int | None:
        """Determine where to apply terminal penalty.

        Args:

            config: Optimizer config
            demand_bounds: Demand window bounds

        Returns:

            Terminal penalty index or None

        """

        # Always apply penalty at DW entry to incentivize charging before DW

        return demand_bounds["entry_idx"]

    def _determine_urgency_window_start_idx(
        self,
        slots: list[SlotContext],
        terminal_penalty_idx: int | None,
        initial_soc_pct: float,
        config: OptimizerConfig,
    ) -> int | None:
        """Index of the first slot within the urgency window before the DW entry.

        The urgency-inflated ``effective_cheap_price`` only legitimately applies to slots
        within the urgency window of the demand-window entry (matching the urgency ramp in
        ``price_calculator``). The window width is deficit-derived (floor 4h, cap 8h) via
        ``dp_math.urgency_window_hours`` — a deep SOC deficit needs more pre-charge runway
        than a fixed 4h allows (2026-06-11 incident: 11.6% -> 95% needs ~4.2h). Slots earlier
        than that — notably tonight's overnight when the next horizon DW is tomorrow
        evening — must be gated on the un-inflated base instead (Issue #800 follow-up).
        Returns None when there is no demand window.
        """
        if terminal_penalty_idx is None:
            return None
        try:
            dw_time = datetime.fromisoformat(slots[terminal_penalty_idx].timestamp_iso)
        except (ValueError, IndexError, TypeError):
            return None
        window_hours = urgency_window_hours(
            initial_soc_pct,
            config.demand_window_target_soc_pct,
            config.battery_capacity_kwh,
            config.charge_rate_kw,
            config.charge_efficiency,
        )
        cutoff = dw_time - timedelta(hours=window_hours)
        for i in range(terminal_penalty_idx + 1):
            try:
                slot_time = datetime.fromisoformat(slots[i].timestamp_iso)
            except (ValueError, TypeError):
                continue
            if slot_time >= cutoff:
                return i
        return terminal_penalty_idx

    def _compute_hard_target_floor(
        self,
        slots: list[SlotContext],
        config: OptimizerConfig,
        terminal_penalty_idx: int | None,
        inputs: OptimizerInputs,
        solar_capable: bool,
    ) -> float | None:
        """Hard SOC floor the DW-entry slot must clear (issue #885), or None when inert.

        Returns ``min(target, max_feasible_terminal_soc)`` so the gate forces charging up
        to — but never beyond — the target, routing the DP through the cheapest eligible
        pre-DW slots. Returns ``None`` (gate dormant, legacy soft-penalty behaviour) when:

        - ``allow_dw_entry_under_target`` is True (target may be met mid-DW via solar — the
          penalty stays at the horizon boundary and pre-charge must not be forced), or
        - the mode is not self-consumption / there is no demand window / the entry is slot
          0 (``compute_max_feasible_terminal_soc`` returns None), or
        - solar alone is projected to reach the target by the DW entry — either at the DW
          entry slot (``check_global_solar_sufficiency`` from the current SOC) or anywhere
          during the DW (``solar_capable``). Forcing grid charge then would fight the
          stale-solar / over-optimistic-solar protections (#816/#849).

        The floor is the SOC the battery must reach ENTERING the demand window (the SOC at
        the START of ``terminal_penalty_idx``), matching where the DP applies the terminal
        penalty (``dp[terminal_penalty_idx][bin]``) and how the shortfall is measured.
        """
        if config.allow_dw_entry_under_target:
            return None
        if solar_capable:
            return None
        # Solar alone reaches target by the DW entry: don't force grid charge (#816/#849).
        if check_global_solar_sufficiency(
            inputs.initial_soc_pct, 0, slots, config, terminal_penalty_idx
        ):
            return None
        max_feasible = compute_max_feasible_terminal_soc(
            slots, config, terminal_penalty_idx, inputs.initial_soc_pct
        )
        if max_feasible is None:
            return None
        return min(config.demand_window_target_soc_pct, max_feasible)

    def _publish_precharge_runway_telemetry(
        self,
        slots: list[SlotContext],
        config: OptimizerConfig,
        terminal_penalty_idx: int | None,
        inputs: OptimizerInputs,
        solar_capable: bool,
    ) -> None:
        """Publish ``hard_floor_suppressed_by_solar`` / ``precharge_runway_slack_min``.

        Fast-follow to #901. The pre-charge execution backstop is gated on
        ``hard_target_floor``, which ``_compute_hard_target_floor`` deliberately returns
        None from on any day solar looks sufficient (don't fight #816/#849) — so the
        backstop is fully dormant exactly on the days a late cloud event can strand the
        battery. These two fields give an out-of-solver consumer what it needs to
        distinguish "dormant because pre-charge genuinely is not required" from "dormant
        only because a solar forecast said so", and to measure the physical runway left.

        Pure telemetry: writes only the two config fields, reads only functions the solve
        already called, and MUST NOT be allowed to change any plan. Call it AFTER
        ``config.hard_target_floor`` has been assigned — the suppression flag is defined
        relative to that value.
        """
        config.hard_floor_suppressed_by_solar = self._hard_floor_suppressed_by_solar(
            slots, config, terminal_penalty_idx, inputs, solar_capable
        )
        config.precharge_runway_slack_min = self._precharge_runway_slack_min(
            slots, config, terminal_penalty_idx, inputs
        )
        # The slack reading is quantized to slot 0's width (the DP has no clock, so the
        # time term can only step at slot boundaries while the SOC gap closes
        # continuously). Publishing the quantum lets the consumer size its hysteresis
        # band against the sawtooth instead of against a hardcoded constant that happens
        # to clear a 5-minute slot and not a 30-minute one.
        config.precharge_runway_quantum_min = (
            float(slots[0].slot_interval_minutes) if slots else 0.0
        )

    def _hard_floor_suppressed_by_solar(
        self,
        slots: list[SlotContext],
        config: OptimizerConfig,
        terminal_penalty_idx: int | None,
        inputs: OptimizerInputs,
        solar_capable: bool,
    ) -> bool:
        """True when the hard floor is dormant ONLY because solar looked sufficient.

        Mirrors ``_compute_hard_target_floor``'s early-return ladder in the same order, so
        the two can never disagree about which branch fired:

        1. floor is set        -> not suppressed (the gate is live)
        2. allow_dw_entry_under_target -> policy dormancy, NOT solar
        3. neither solar check fired   -> dormant for some other reason
        4. max feasible is None        -> structural dormancy (no DW / slot-0 entry /
           non-self-consumption), NOT solar

        Only a case that survives all four is a genuine solar suppression.
        """
        if config.hard_target_floor is not None:
            return False
        if config.allow_dw_entry_under_target:
            return False
        if not solar_capable and not check_global_solar_sufficiency(
            inputs.initial_soc_pct, 0, slots, config, terminal_penalty_idx
        ):
            return False
        return (
            compute_max_feasible_terminal_soc(
                slots, config, terminal_penalty_idx, inputs.initial_soc_pct
            )
            is not None
        )

    def _precharge_runway_slack_min(
        self,
        slots: list[SlotContext],
        config: OptimizerConfig,
        terminal_penalty_idx: int | None,
        inputs: OptimizerInputs,
    ) -> float | None:
        """Spare minutes between now and the last moment boost could still reach target.

        ``minutes_to_dw_entry - minutes_of_boost_needed_to_close_the_gap``.

        Both terms are deliberately biased to under-report slack, because the only safe
        error direction for a guardrail is "fires slightly early":

        * **Time.** The DP is clock-free by construction (deterministic and replayable),
          so the only anchor available is a slot boundary — and
          ``_ensure_current_slot_coverage`` guarantees ``slots[0].start <= now``, which
          means measuring from slot 0's *start* over-reports the real runway by however
          much of that slot has already elapsed (up to a full 30 minutes when the horizon
          opens on a 30-minute slot). The anchor is therefore the *end* of slot 0, the
          only bound that is never later than ``now``.
        * **Rate.** Minutes-needed is integrated through ``transitions._tapered_stored_kwh``
          — the same CV-taper charge model the DP's own transitions use — rather than a
          flat ``rate x efficiency``. Above ``charge_taper_start_pct`` (80% by default) the
          Powerwall derates toward ``charge_taper_min_factor``, and *every* pre-charge to a
          95% target crosses that knee. The flat form understated the 2026-07-28 live case
          (59.1% -> 95%) by 15 minutes — larger than the whole default margin, and enough
          to keep the arm shut on the very incident it was written for.

        Returns None when the quantity is not interpretable rather than guessing.
        """
        if (
            terminal_penalty_idx is None
            or terminal_penalty_idx <= 0
            or not slots
            or terminal_penalty_idx >= len(slots)
        ):
            return None
        try:
            dw_time = datetime.fromisoformat(slots[terminal_penalty_idx].timestamp_iso)
            start_time = datetime.fromisoformat(slots[0].timestamp_iso)
        except (ValueError, TypeError):
            return None
        # Anchor at the END of slot 0 — see the docstring: slot 0 already contains ``now``,
        # so its start would credit runway that has already been spent.
        minutes_to_dw = (dw_time - start_time).total_seconds() / 60.0 - float(
            slots[0].slot_interval_minutes
        )

        gap_pct = config.demand_window_target_soc_pct - inputs.initial_soc_pct
        if gap_pct <= 0.0:
            # Already at/above target: the whole remaining runway is slack.
            return minutes_to_dw
        minutes_needed = self._boost_minutes_to_close_gap(
            config, inputs.initial_soc_pct
        )
        if minutes_needed is None:
            return None
        return minutes_to_dw - minutes_needed

    @staticmethod
    def _boost_minutes_to_close_gap(
        config: OptimizerConfig, initial_soc_pct: float
    ) -> float | None:
        """Minutes of boost charging needed to lift SOC to target, taper included.

        Steps the engine's own charge model (``transitions._tapered_stored_kwh``) at
        one-minute resolution so the answer cannot disagree with the transition function
        that actually moves SOC inside the DP. A closed-form ``gap / (rate * eff)``
        cannot express the CV taper, and the taper is not a rounding error: it is worth
        ~15 minutes on a realistic pre-charge to a 95% target.

        Returns None on degenerate constants (zero capacity/rate/efficiency) or when the
        target is unreachable at any duration, which is never the same thing as zero.
        """
        target = config.demand_window_target_soc_pct
        if config.battery_capacity_kwh <= 0.0:
            return None
        if config.boost_charge_rate_kw <= 0.0 or config.charge_efficiency <= 0.0:
            return None

        soc = initial_soc_pct
        minutes = 0.0
        # Ceiling: the untapered traversal of the whole SOC range can never take longer
        # than this many minutes divided by the taper's own worst-case factor, so it
        # bounds the loop without capping a legitimate answer.
        untapered_full_range_min = 100.0 / (
            config.boost_charge_rate_kw
            * config.charge_efficiency
            / config.battery_capacity_kwh
            * 100.0
            / 60.0
        )
        limit = untapered_full_range_min / max(1e-3, config.charge_taper_min_factor)
        while soc < target and minutes < limit:
            stored_kwh = _tapered_stored_kwh(
                soc, config.boost_charge_rate_kw, 1.0 / 60.0, config
            )
            delta = stored_kwh / config.battery_capacity_kwh * 100.0
            if delta <= 1e-9:
                return None
            soc += delta
            minutes += 1.0
        if soc < target:
            return None
        return minutes

    def _initialize_dp_tables(
        self,
        n_slots: int,
        soc_grid: list[float],
        config: OptimizerConfig,
        terminal_penalty_idx: int | None,
        solar_can_reach_target: bool,
        inputs: OptimizerInputs,
        hard_target_floor: float | None = None,
    ) -> tuple[
        list[dict[int, tuple[float, PlannerAction, int, float, float, float]]],
        dict[int, float],
    ]:
        """Initialize DP tables and compute the per-bin demand-window-entry penalty.

        In self-consumption mode, credits future solar gain (Issue #619) to
        prevent grid charging when solar will cover the shortfall.

        Issue #624: In self_consumption mode, treat target as a hard constraint by
        using a very high cost for states below target.

        Issue #811/#816 (horizon-end myopia): in the strict target mode
        (``allow_dw_entry_under_target=False``) the target/shortfall penalty is applied
        at the DEMAND-WINDOW ENTRY (``terminal_penalty_idx``) during backward induction,
        NOT at the end of the planning horizon. Applying it at the horizon boundary made
        the optimizer grid-charge overnight to hit a target at an arbitrary cutoff (which
        moves every cycle as the rolling horizon slides), contradicting the Control
        Philosophy and producing horizon-dependent overnight charging.

        When ``allow_dw_entry_under_target=True`` (Issue #505), the target may instead be
        met at any point DURING the demand window via solar, so the penalty stays at the
        horizon boundary (``dp[n_slots]``) as before — relocating it to the entry would
        wrongly force pre-charge that mid-DW solar was meant to cover.

        Returns ``(dp, terminal_penalty_by_bin)`` where ``terminal_penalty_by_bin`` maps
        soc-bin index -> shortfall penalty to add at ``terminal_penalty_idx`` (empty when
        there is no demand window, or when the penalty stays at the horizon boundary).
        """

        dp: list[dict[int, tuple[float, PlannerAction, int, float, float, float]]] = [
            {} for _ in range(n_slots + 1)
        ]

        # Horizon-end boundary carries no target (Issue #811/#816) — but it does
        # carry a bounded residual-energy salvage credit (also Issue #811): residual
        # SOC above the floor displaces a post-horizon grid import once the rolling
        # horizon advances, so pricing it at exactly zero made the planner too
        # willing to dump value near the end of the modeled horizon. The credit is
        # bounded (at most half the cheapest observed buy price, capped absolutely)
        # so charging to harvest it can never pay, and it never touches the
        # strict-mode DW-entry penalty rows.
        salvage_buy_price = (
            min(slot.buy_price for slot in inputs.slots) if inputs.slots else 0.0
        )
        for bin_idx, soc in enumerate(soc_grid):
            salvage_credit = (
                _cost_terminal_salvage_value(soc, config, salvage_buy_price)
                if config.terminal_salvage_enabled
                else 0.0
            )
            dp[n_slots][bin_idx] = (
                -salvage_credit,
                PlannerAction.HOLD,
                bin_idx,
                0.0,
                0.0,
                0.0,
            )

        terminal_penalty_by_bin: dict[int, float] = {}

        if terminal_penalty_idx is not None:
            target = config.demand_window_target_soc_pct

            # Issue #619: Horizon-aware shortfall credit

            # Account for solar surplus beyond the plan horizon that will help

            # reach the target by the demand window entry.

            future_solar_gain_pct = 0.0

            if inputs.all_solcast and inputs.slots:
                from custom_components.localshift.forecast.analysis_resolver import (
                    ConfidenceResolver,
                )

                last_slot = inputs.slots[-1]
                last_slot_start = datetime.fromisoformat(last_slot.timestamp_iso)

                last_slot_end = last_slot_start + timedelta(
                    minutes=last_slot.slot_interval_minutes
                )
                target_slot = inputs.slots[terminal_penalty_idx]

                target_time = datetime.fromisoformat(target_slot.timestamp_iso)
                confidence_resolver = ConfidenceResolver(
                    inputs.solcast_analysis_today,
                    inputs.solcast_analysis_tomorrow,
                    absent_confidence=getattr(inputs, "solar_absent_confidence", 1.0),
                )

                # Helper computes gain between end of plan and target time

                future_solar_gain_pct = projected_solcast_gain_pct(
                    inputs.all_solcast,
                    start_time=last_slot_end,
                    end_time=target_time,
                    battery_capacity_kwh=config.battery_capacity_kwh,
                    confidence_resolver=confidence_resolver,
                )

            # Issue #624: Hard constraint in self_consumption mode

            # Use a very high penalty (effectively infinite) for states below target

            # to force the optimizer to find a path that reaches the target.

            # We use a finite value instead of float('inf') to handle infeasible cases gracefully.

            use_hard_constraint = config.optimization_mode == "self_consumption"

            # Calculate max possible grid cost to set penalty above it

            # Max SOC gain needed = 100%, battery capacity in kWh

            # Max cost = capacity_kwh * max_price * 2 (safety factor)

            max_grid_cost = config.battery_capacity_kwh * 0.30 * 2  # ~$8 for 13.5kWh

            hard_constraint_penalty = max_grid_cost * 10  # 10x the max cost

            # Apply accuracy-based discount to beyond-horizon solar (Issue #785)
            forecast_accuracy = get_forecast_accuracy(inputs.solar_accuracy_tracker)
            accuracy_discount = max(0.5, min(1.0, forecast_accuracy))

            _LOGGER.debug(
                "Terminal cost: forecast_accuracy=%.1f%%, discount=%.2f",
                forecast_accuracy * 100,
                accuracy_discount,
            )

            for bin_idx, soc in enumerate(soc_grid):
                effective_soc = soc + future_solar_gain_pct

                # Hard DW-target feasibility gate (issue #885). When a hard floor is set
                # (strict mode, solar insufficient), DW-entry states below the floor are
                # pruned with the effectively-infinite penalty so backward induction MUST
                # route a charging path that clears it. The floor is min(target, max
                # feasible), so an unreachable target degrades gracefully — every bin up to
                # the max feasible SOC is admitted and the DP charges as far as it can.
                # The penalty is monotonic in the gap, so among below-floor states the DP
                # still prefers the highest reachable SOC (no infeasible/empty plan).
                if hard_target_floor is not None and effective_soc < hard_target_floor:
                    # Issue #903: measure the gap to TARGET, then ADD the floor breach —
                    # never the gap to the floor alone. The floor is min(target, max
                    # feasible), so when it degrades below target a gap-to-floor penalty
                    # is SMALLER than the gap-to-target penalty applied just above the
                    # floor: the schedule stepped UP at the boundary (measured $3.24 just
                    # below vs $615.60 just above) and made the degraded floor an
                    # attractor the DP deliberately parked under, forgoing reachable cheap
                    # SOC. Summing keeps the penalty non-increasing in SOC across the
                    # whole below-target range and continuous at the floor (the breach
                    # term vanishes there), while still making a breach strictly worse.
                    shortfall = target - effective_soc
                    breach = hard_target_floor - effective_soc
                    shortfall_penalty = (shortfall + breach) * hard_constraint_penalty
                elif use_hard_constraint and effective_soc < target:
                    shortfall = target - effective_soc
                    shortfall_penalty = shortfall * hard_constraint_penalty
                else:
                    shortfall_penalty = _cost_terminal_cost(
                        effective_soc, target, config
                    )

                if config.allow_dw_entry_under_target:
                    # Issue #505: target may be met mid-DW via solar — keep the penalty
                    # at the horizon boundary (legacy behaviour) so it does not force
                    # pre-charge before the demand window. Issue #811: net the bounded
                    # residual salvage credit against it (raw bin soc — post-horizon
                    # solar gain is not bankable energy at the boundary).
                    salvage_credit = (
                        _cost_terminal_salvage_value(soc, config, salvage_buy_price)
                        if config.terminal_salvage_enabled
                        else 0.0
                    )
                    dp[n_slots][bin_idx] = (
                        shortfall_penalty - salvage_credit,
                        PlannerAction.HOLD,
                        bin_idx,
                        0.0,
                        0.0,
                        0.0,
                    )
                else:
                    # Issue #811/#816: strict mode — apply at the DW entry during backward
                    # induction, not at the arbitrary horizon boundary.
                    terminal_penalty_by_bin[bin_idx] = shortfall_penalty

        return dp, terminal_penalty_by_bin

    def _get_terminal_diagnostics(
        self,
        soc_pct: float,
        target: float,
        accuracy_discount: float,
        future_solar_gain_pct: float,
        decisions: list[PlannedSlotDecision],
        terminal_penalty_idx: int | None,
    ) -> dict[str, Any]:
        """Extract diagnostic metrics for terminal cost calculation.

        Args:
            soc_pct: Current state of charge percentage
            target: Target SOC percentage
            accuracy_discount: Applied discount factor
            future_solar_gain_pct: Beyond-horizon solar gain
            decisions: All optimizer decisions with predicted SOC
            terminal_penalty_idx: Index of terminal penalty slot

        Returns:

            Dictionary of diagnostic metrics

        """
        peak_soc = max(d.predicted_soc_pct for d in decisions) if decisions else soc_pct

        dw_entry_soc = None

        if (
            terminal_penalty_idx is not None
            and decisions
            and terminal_penalty_idx < len(decisions)
        ):
            # SOC entering the window (start of the entry slot) — consistent with the
            # terminal penalty / #885 hard floor and with _compute_terminal_shortfall.
            dw_entry_soc = self._dw_entry_soc(decisions, terminal_penalty_idx)

        return {
            "accuracy_discount_factor": round(accuracy_discount, 2),
            "peak_soc_pct": round(peak_soc, 2),
            "dw_entry_soc_pct": round(dw_entry_soc, 2) if dw_entry_soc else None,
        }

    def _backward_induction(
        self,
        dp: list[dict],
        slots: list[SlotContext],
        soc_grid: list[float],
        config: OptimizerConfig,
        terminal_penalty_idx: int | None,
        inputs: OptimizerInputs,
        negative_fit_avoidance_context: NegativeFitAvoidanceContext | None = None,
        terminal_penalty_by_bin: dict[int, float] | None = None,
    ) -> int:
        """Perform backward induction to fill DP tables.

        Args:

            dp: DP tables
            slots: Slot contexts
            soc_grid: SOC grid
            config: Optimizer config
            terminal_penalty_idx: Terminal penalty index
            inputs: Optimizer inputs
            terminal_penalty_by_bin: Per-bin shortfall penalty applied at the DW-entry
                slot (Issue #811/#816); the cost of entering the demand window at that
                bin's SOC. Constant across actions at that slot, so it is added after
                action selection.

        Returns:

            Number of states explored

        """

        n_slots = len(slots)
        states_explored = 0
        penalty_by_bin = terminal_penalty_by_bin or {}

        for slot_idx in range(n_slots - 1, -1, -1):
            slot = slots[slot_idx]
            apply_terminal_penalty = slot_idx == terminal_penalty_idx

            for bin_idx, soc in enumerate(soc_grid):
                best, action_count = self._compute_best_action(
                    dp,
                    slot_idx,
                    slot,
                    soc,
                    soc_grid,
                    config,
                    terminal_penalty_idx,
                    slots,
                    inputs,
                    negative_fit_avoidance_context,
                )
                if apply_terminal_penalty:
                    # Cost of entering the demand window at this SOC (Issue #811/#816).
                    penalty = penalty_by_bin.get(bin_idx, 0.0)
                    if penalty:
                        best = (best[0] + penalty, *best[1:])
                dp[slot_idx][bin_idx] = best
                states_explored += action_count

        return states_explored

    def _compute_best_action(
        self,
        dp: list[dict],
        slot_idx: int,
        slot: SlotContext,
        soc: float,
        soc_grid: list[float],
        config: OptimizerConfig,
        terminal_penalty_idx: int | None,
        slots: list[SlotContext],
        inputs: OptimizerInputs,
        negative_fit_avoidance_context: NegativeFitAvoidanceContext | None = None,
    ) -> tuple[tuple[float, PlannerAction, int, float, float, float], int]:
        """Compute best action for a state.

        Args:

            dp: DP tables
            slot_idx: Slot index
            slot: Slot context
            soc: Current SOC
            soc_grid: SOC grid
            config: Optimizer config
            terminal_penalty_idx: Terminal penalty index
            slots: All slots
            inputs: Optimizer inputs

        Returns:

            Tuple of (best result tuple, actions explored count)

        """

        actions = _constraints_feasible_actions(
            soc,
            slot,
            config,
            slot_idx=slot_idx,
            slots=slots,
            terminal_penalty_idx=terminal_penalty_idx,
            negative_fit_avoidance_context=negative_fit_avoidance_context,
        )
        best_cost = float("inf")
        best_action = PlannerAction.HOLD
        best_next_bin = 0
        best_import = 0.0
        best_export = 0.0
        best_next_soc = soc
        states_explored = 0
        hold_total_cost: float | None = None

        # is_urgency_precharge depends only on loop-invariant quantities, so hoist it.
        urgency_precharge = _is_urgency_precharge(
            slot_idx, soc, slot.buy_price, terminal_penalty_idx, config
        )

        dp_next = dp[slot_idx + 1]

        for action in actions:
            next_soc, next_bin, grid_import, grid_export, charge_kwh, total_cost = (
                _evaluate_action_cost(
                    action,
                    soc,
                    slot,
                    slot_idx,
                    slots,
                    soc_grid,
                    dp_next,
                    config,
                    inputs,
                    terminal_penalty_idx,
                )
            )

            if action == PlannerAction.HOLD:
                hold_total_cost = total_cost
            elif _min_cycle_saving_blocks(
                action,
                charge_kwh,
                total_cost,
                hold_total_cost,
                urgency_precharge,
                config,
            ):
                states_explored += 1
                continue

            if _floor_guard_blocks(
                action, soc, next_soc, slot_idx, terminal_penalty_idx, config
            ):
                states_explored += 1
                continue

            if total_cost < best_cost or (
                total_cost == best_cost
                and _ACTION_PRIORITY.get(action, 99)
                < _ACTION_PRIORITY.get(best_action, 99)
            ):
                best_cost = total_cost
                best_action = action
                best_next_bin = next_bin
                best_import = grid_import
                best_export = grid_export
                best_next_soc = next_soc

            states_explored += 1

        return (
            (
                best_cost,
                best_action,
                best_next_bin,
                best_import,
                best_export,
                best_next_soc,
            ),
            states_explored,
        )

    def _forward_reconstruct(
        self,
        dp: list[dict],
        inputs: OptimizerInputs,
        slots: list[SlotContext],
        soc_grid: list[float],
        config: OptimizerConfig,
        terminal_penalty_idx: int | None,
        negative_fit_avoidance_context: NegativeFitAvoidanceContext | None = None,
    ) -> tuple[list[PlannedSlotDecision], dict[str, float], dict[str, int]]:
        """Reconstruct optimal path forward.

        Args:

            dp: DP tables
            inputs: Optimizer inputs
            slots: Slot contexts
            soc_grid: SOC grid
            config: Optimizer config
            terminal_penalty_idx: Terminal penalty index

        Returns:

            Tuple of (decisions, totals, reason_histogram)

        """

        decisions: list[PlannedSlotDecision] = []
        current_soc = inputs.initial_soc_pct
        current_bin = _map_soc_to_bin(current_soc, soc_grid)
        totals = {"import": 0.0, "export": 0.0, "net_cost": 0.0}
        reason_histogram: dict[str, int] = {}

        for slot_idx, slot in enumerate(slots):
            if current_bin not in dp[slot_idx]:
                action = PlannerAction.HOLD

            else:
                _, action, _, _, _, _ = dp[slot_idx][current_bin]

            next_soc, grid_import, grid_export = _transition(
                current_soc, action, slot, config
            )
            next_soc = max(config.min_soc_pct, min(config.max_soc_pct, next_soc))

            is_switch = (
                slot_idx == 0
                and inputs.current_action is not None
                and action != inputs.current_action
            )

            # Issue #610: horizon-aware solar opportunity cost

            solar_opp_factor = get_solar_opportunity_penalty_factor(
                action=action,
                grid_import_kwh=grid_import,
                slot=slot,
                slot_idx=slot_idx,
                slots=slots,
                config=config,
                terminal_penalty_idx=terminal_penalty_idx,
                all_solcast=inputs.all_solcast,
            )

            # Issue #638: futile cycling penalty

            recon_charge_kwh = (
                max(0.0, next_soc - current_soc) / 100.0 * config.battery_capacity_kwh
            )

            recon_futile_factor = get_futile_cycling_penalty_factor(
                action=action,
                slot_idx=slot_idx,
                slots=slots,
                config=config,
                soc_after_charge_pct=next_soc,
                charge_kwh=recon_charge_kwh,
                terminal_penalty_idx=terminal_penalty_idx,
            )

            stage = _cost_stage_cost(
                action,
                grid_import,
                grid_export,
                slot,
                config,
                soc_pct=current_soc,
                is_switch=is_switch,
                solar_opportunity_penalty_factor=solar_opp_factor,
                futile_cycling_penalty_factor=recon_futile_factor,
            )

            reason = classify_reason(
                action,
                slot,
                slot_idx,
                slots,
                current_soc,
                next_soc,
                config,
                terminal_penalty_idx,
                stage,
                inputs=inputs,
                negative_fit_avoidance_context=negative_fit_avoidance_context,
            )

            decision = PlannedSlotDecision(
                slot_index=slot.slot_index,
                timestamp_iso=slot.timestamp_iso,
                slot_interval_minutes=slot.slot_interval_minutes,
                action=action,
                reason_code=reason,
                objective_terms=stage,
                predicted_soc_pct=next_soc,
                grid_import_kwh=grid_import,
                grid_export_kwh=grid_export,
                solar_kwh=slot.solar_kwh,
                consumption_kwh=slot.consumption_kwh,
                buy_price=slot.buy_price,
                sell_price=slot.sell_price,
                is_solar_opportunity=stage.solar_opportunity_penalty > 0,
            )
            decisions.append(decision)

            totals["import"] += grid_import
            totals["export"] += grid_export
            totals["net_cost"] += stage.net_cost

            reason_key = reason.value
            reason_histogram[reason_key] = reason_histogram.get(reason_key, 0) + 1

            current_soc = next_soc
            current_bin = _map_soc_to_bin(current_soc, soc_grid)

        return decisions, totals, reason_histogram

    def _compute_terminal_shortfall(
        self,
        inputs: OptimizerInputs,
        decisions: list[PlannedSlotDecision],
        config: OptimizerConfig,
        terminal_penalty_idx: int | None,
        demand_bounds: dict[str, int | None] | None = None,
    ) -> float:
        """Compute terminal shortfall.

        Args:

            inputs: Optimizer inputs
            decisions: Planned decisions
            config: Optimizer config
            terminal_penalty_idx: Terminal penalty index

            demand_bounds: Demand window bounds (entry_idx, end_idx) for first DW block.

                Used to scope the solar simulation to the first DW block only (Issue #633).

        Returns:

            Terminal shortfall percentage

        """

        if terminal_penalty_idx is None:
            return 0.0

        target = config.demand_window_target_soc_pct

        if config.allow_dw_entry_under_target:
            max_soc_in_dw = _simulate_max_soc_in_demand_window(
                inputs.initial_soc_pct, inputs.slots, config, demand_bounds
            )

            return max(0.0, target - max_soc_in_dw)

        if terminal_penalty_idx < len(decisions):
            terminal_soc = self._dw_entry_soc(decisions, terminal_penalty_idx)

            return max(0.0, target - terminal_soc)

        return 0.0

    @staticmethod
    def _dw_entry_soc(
        decisions: list[PlannedSlotDecision], terminal_penalty_idx: int
    ) -> float:
        """SOC entering the demand window — the SOC at the START of the DW-entry slot.

        The DP applies the target/shortfall penalty and the #885 hard floor to
        ``dp[terminal_penalty_idx][bin]``, which is keyed by the SOC at the START of the
        entry slot (= the end-of-slot SOC of ``terminal_penalty_idx - 1``), NOT the SOC
        after the entry slot's own consumption has drained it. Measuring the shortfall at
        that same point keeps the reported ``terminal_shortfall_pct`` / ``dw_entry_soc_pct``
        consistent with what the optimizer actually controls; measuring post-decay instead
        booked a phantom ~1 slot of load as an unavoidable shortfall even when the battery
        entered the window exactly at target (issue #885).

        Falls back to the entry slot's own predicted SOC when the entry is slot 0 (nothing
        precedes it — the in-progress-DW edge handled by ``_find_demand_window_bounds``).
        """
        if terminal_penalty_idx <= 0:
            return decisions[terminal_penalty_idx].predicted_soc_pct
        return decisions[terminal_penalty_idx - 1].predicted_soc_pct

    # ------------------------------------------------------------------

    # Pure primitive functions (to be expanded in Phase C of #403)

    # ------------------------------------------------------------------

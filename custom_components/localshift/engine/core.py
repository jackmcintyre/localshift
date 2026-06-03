"""Core DPPlanner implementation for battery optimization."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

from custom_components.localshift.engine.constraints import (
    feasible_actions as _constraints_feasible_actions,
)
from custom_components.localshift.engine.cost import (
    stage_cost as _cost_stage_cost,
)
from custom_components.localshift.engine.cost import (
    terminal_cost as _cost_terminal_cost,
)
from custom_components.localshift.engine.dp_math import (
    _build_soc_grid,
    _interpolate_cost_to_soc,
    _map_soc_to_bin,
    _simulate_max_soc_in_demand_window,
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

# Issue #800 follow-up: hours before the demand-window entry over which the urgency-inflated
# effective_cheap_price is considered valid. Matches the urgency ramp window in
# price_calculator._calculate_urgency_adjusted_price (total_window = 4.0h). Slots earlier
# than this use the un-inflated base price for the cheap-charge gate.
_URGENCY_WINDOW_HOURS = 4.0


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
            result = self._solve(inputs)

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

        # Issue #800 follow-up: the urgency-inflated effective_cheap_price is only valid
        # near the demand window. Record where the urgency window begins so the cheap-price
        # gate uses the inflated value only there and the un-inflated base elsewhere
        # (otherwise tonight's overnight — far before tomorrow's DW — sawtooths).
        config.urgency_window_start_idx = self._determine_urgency_window_start_idx(
            slots, terminal_penalty_idx
        )

        dp, terminal_penalty_by_bin = self._initialize_dp_tables(
            n_slots, soc_grid, config, terminal_penalty_idx, solar_capable, inputs
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

        for i, slot in enumerate(slots):
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
    ) -> int | None:
        """Index of the first slot within the urgency window before the DW entry.

        The urgency-inflated ``effective_cheap_price`` only legitimately applies to slots
        within ~``_URGENCY_WINDOW_HOURS`` of the demand-window entry (matching the urgency
        ramp in ``price_calculator``). Slots earlier than that — notably tonight's overnight
        when the next horizon DW is tomorrow evening — must be gated on the un-inflated base
        instead (Issue #800 follow-up). Returns None when there is no demand window.
        """
        if terminal_penalty_idx is None:
            return None
        try:
            dw_time = datetime.fromisoformat(slots[terminal_penalty_idx].timestamp_iso)
        except (ValueError, IndexError, TypeError):
            return None
        cutoff = dw_time - timedelta(hours=_URGENCY_WINDOW_HOURS)
        for i in range(terminal_penalty_idx + 1):
            try:
                slot_time = datetime.fromisoformat(slots[i].timestamp_iso)
            except (ValueError, TypeError):
                continue
            if slot_time >= cutoff:
                return i
        return terminal_penalty_idx

    def _initialize_dp_tables(
        self,
        n_slots: int,
        soc_grid: list[float],
        config: OptimizerConfig,
        terminal_penalty_idx: int | None,
        solar_can_reach_target: bool,
        inputs: OptimizerInputs,
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

        # Horizon-end boundary carries no target (Issue #811): always zero.
        for bin_idx in range(len(soc_grid)):
            dp[n_slots][bin_idx] = (0.0, PlannerAction.HOLD, bin_idx, 0.0, 0.0, 0.0)

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

                if use_hard_constraint and effective_soc < target:
                    shortfall = target - effective_soc
                    shortfall_penalty = shortfall * hard_constraint_penalty
                else:
                    shortfall_penalty = _cost_terminal_cost(
                        effective_soc, target, config
                    )

                if config.allow_dw_entry_under_target:
                    # Issue #505: target may be met mid-DW via solar — keep the penalty
                    # at the horizon boundary (legacy behaviour) so it does not force
                    # pre-charge before the demand window.
                    dp[n_slots][bin_idx] = (
                        shortfall_penalty,
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

        if terminal_penalty_idx is not None and decisions:
            dw_entry_soc = decisions[terminal_penalty_idx].predicted_soc_pct

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

        for action in actions:
            next_soc, grid_import, grid_export = _transition(soc, action, slot, config)
            next_soc = max(config.min_soc_pct, min(config.max_soc_pct, next_soc))
            next_bin = _map_soc_to_bin(next_soc, soc_grid)
            future_cost = dp[slot_idx + 1].get(next_bin, (float("inf"),))[0]

            if future_cost == float("inf") and dp[slot_idx + 1]:
                future_cost = _interpolate_cost_to_soc(
                    next_soc, soc_grid, {k: v[0] for k, v in dp[slot_idx + 1].items()}
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
            terminal_soc = decisions[terminal_penalty_idx].predicted_soc_pct

            return max(0.0, target - terminal_soc)

        return 0.0

    # ------------------------------------------------------------------

    # Pure primitive functions (to be expanded in Phase C of #403)

    # ------------------------------------------------------------------

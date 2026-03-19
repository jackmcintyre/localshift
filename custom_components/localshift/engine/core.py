"""Core DPPlanner implementation for battery optimization."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

from custom_components.localshift.engine.constraints import (
    _determine_export_actions,
    check_global_solar_sufficiency,
    feasible_actions as _constraints_feasible_actions,
)
from custom_components.localshift.engine.cost import (
    stage_cost as _cost_stage_cost,
    terminal_cost as _cost_terminal_cost,
)
from custom_components.localshift.engine.dp_math import (
    _build_soc_grid,
    _interpolate_cost_to_soc,
    _map_soc_to_bin,
    _simulate_max_soc_in_demand_window,
    _simulate_solar_only_terminal_soc,
)
from custom_components.localshift.engine.types import (
    NegativeFitAvoidanceContext,
    ObjectiveTerms,
    OptimizerConfig,
    OptimizerInputs,
    OptimizerResult,
    PlannedSlotDecision,
    PlannerAction,
    PlannerReasonCode,
    SlotContext,
)
from custom_components.localshift.engine.negative_fit import (
    compute_recoverability_floor_pct,
    derive_negative_fit_avoidance_context,
)
from custom_components.localshift.engine.transitions import transition as _transition
from custom_components.localshift.forecast.solar_accuracy import SolarAccuracyTracker

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


class DPPlanner:
    """
    Deterministic dynamic-programming battery optimizer.

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
        solar_capable = self._check_solar_can_reach_target(inputs, demand_bounds)
        terminal_penalty_idx = self._determine_terminal_penalty_idx(
            config, demand_bounds
        )

        dp = self._initialize_dp_tables(
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
        can_solar = self._can_solar_reach_target(
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

    def _check_solar_can_reach_target(
        self, inputs: OptimizerInputs, demand_bounds: dict[str, int | None]
    ) -> bool:
        """Check if solar can reach target during demand window.

        Args:
            inputs: Optimizer inputs
            demand_bounds: Demand window bounds

        Returns:
            True if solar can reach target

        """
        config = inputs.config
        demand_window_entry_idx = demand_bounds["entry_idx"]

        if not config.allow_dw_entry_under_target or demand_window_entry_idx is None:
            return False

        max_soc_in_dw = _simulate_max_soc_in_demand_window(
            inputs.initial_soc_pct, inputs.slots, config, demand_bounds
        )
        return max_soc_in_dw >= config.demand_window_target_soc_pct

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

    def _initialize_dp_tables(
        self,
        n_slots: int,
        soc_grid: list[float],
        config: OptimizerConfig,
        terminal_penalty_idx: int | None,
        solar_can_reach_target: bool,
        inputs: OptimizerInputs,
    ) -> list[dict[int, tuple[float, PlannerAction, int, float, float, float]]]:
        """Initialize DP tables with terminal costs.

        In self-consumption mode, credits future solar gain (Issue #619) to
        prevent grid charging when solar will cover the shortfall.

        Issue #624: In self_consumption mode, treat target as hard constraint by
        using infinite cost for states below target at terminal penalty index.
        """
        dp: list[dict[int, tuple[float, PlannerAction, int, float, float, float]]] = [
            {} for _ in range(n_slots + 1)
        ]

        if terminal_penalty_idx is not None and not solar_can_reach_target:
            target = config.demand_window_target_soc_pct

            # Issue #619: Horizon-aware shortfall credit
            # Account for solar surplus beyond the plan horizon that will help
            # reach the target by the demand window entry.
            future_solar_gain_pct = 0.0
            if inputs.all_solcast and inputs.slots:
                last_slot = inputs.slots[-1]
                last_slot_start = datetime.fromisoformat(last_slot.timestamp_iso)
                last_slot_end = last_slot_start + timedelta(
                    minutes=last_slot.slot_interval_minutes
                )
                target_slot = inputs.slots[terminal_penalty_idx]
                target_time = datetime.fromisoformat(target_slot.timestamp_iso)

                # Helper computes gain between end of plan and target time
                future_solar_gain_pct = DPPlanner._projected_solcast_gain_pct(
                    inputs.all_solcast,
                    start_time=last_slot_end,
                    end_time=target_time,
                    battery_capacity_kwh=config.battery_capacity_kwh,
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

            # Check if solar within the horizon can cover the deficit
            # This prevents unnecessary grid charging when solar is sufficient
            projected_solar_gain_pct = DPPlanner._projected_solar_soc_gain_pct(
                slot_idx=0,
                slots=inputs.slots,
                terminal_penalty_idx=terminal_penalty_idx,
                battery_capacity_kwh=config.battery_capacity_kwh,
            )

            # Apply accuracy-based discount to projected solar (Issue #785)
            forecast_accuracy = self._get_forecast_accuracy(
                inputs.solar_accuracy_tracker
            )
            accuracy_discount = max(0.5, min(1.0, forecast_accuracy))
            adjusted_solar_gain_pct = projected_solar_gain_pct * accuracy_discount

            # Add debug logging
            _LOGGER.debug(
                "Terminal cost discount: accuracy=%.1f%%, discount=%.2f, "
                "raw_solar_gain=%.1f%%, adjusted=%.1f%%",
                forecast_accuracy * 100,
                accuracy_discount,
                projected_solar_gain_pct,
                adjusted_solar_gain_pct,
            )

            for bin_idx, soc in enumerate(soc_grid):
                # Subtract future solar gain from shortfall (Issue #619)
                effective_soc = soc + future_solar_gain_pct + adjusted_solar_gain_pct

                if use_hard_constraint and effective_soc < target:
                    # Hard constraint: very high penalty for states below target
                    # This strongly incentivizes the optimizer to reach target
                    shortfall = target - effective_soc
                    shortfall_penalty = shortfall * hard_constraint_penalty
                else:
                    # Soft penalty for states at or above target, or in arbitrage mode
                    shortfall_penalty = _cost_terminal_cost(
                        effective_soc, target, config
                    )
                dp[n_slots][bin_idx] = (
                    shortfall_penalty,
                    PlannerAction.HOLD,
                    bin_idx,
                    0.0,
                    0.0,
                    0.0,
                )
        else:
            n_bins = len(soc_grid)
            for bin_idx in range(n_bins):
                dp[n_slots][bin_idx] = (0.0, PlannerAction.HOLD, bin_idx, 0.0, 0.0, 0.0)

        return dp

    def _get_terminal_diagnostics(
        self,
        soc_pct: float,
        target: float,
        projected_solar_gain_pct: float,
        accuracy_discount: float,
        future_solar_gain_pct: float,
        decisions: list[PlannedSlotDecision],
        terminal_penalty_idx: int | None,
    ) -> dict[str, Any]:
        """Extract diagnostic metrics for terminal cost calculation.

        Args:
            soc_pct: Current state of charge percentage
            target: Target SOC percentage
            projected_solar_gain_pct: Raw solar projection
            accuracy_discount: Applied discount factor
            future_solar_gain_pct: Beyond-horizon solar gain
            decisions: All optimizer decisions with predicted SOC
            terminal_penalty_idx: Index of terminal penalty slot

        Returns:
            Dictionary of diagnostic metrics
        """
        adjusted_solar_gain = projected_solar_gain_pct * accuracy_discount
        effective_soc = soc_pct + future_solar_gain_pct + adjusted_solar_gain

        peak_soc = max(d.predicted_soc_pct for d in decisions) if decisions else soc_pct

        dw_entry_soc = None
        if terminal_penalty_idx is not None and decisions:
            dw_entry_soc = decisions[terminal_penalty_idx].predicted_soc_pct

        return {
            "projected_solar_gain_pct": round(projected_solar_gain_pct, 2),
            "accuracy_discount_factor": round(accuracy_discount, 2),
            "adjusted_solar_gain_pct": round(adjusted_solar_gain, 2),
            "effective_soc_at_terminal": round(effective_soc, 2),
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
    ) -> int:
        """Perform backward induction to fill DP tables.

        Args:
            dp: DP tables
            slots: Slot contexts
            soc_grid: SOC grid
            config: Optimizer config
            terminal_penalty_idx: Terminal penalty index
            inputs: Optimizer inputs

        Returns:
            Number of states explored

        """
        n_slots = len(slots)
        states_explored = 0

        for slot_idx in range(n_slots - 1, -1, -1):
            slot = slots[slot_idx]
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
            solar_opp_factor = self._get_solar_opportunity_penalty_factor(
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
            futile_factor = self._get_futile_cycling_penalty_factor(
                action=action,
                slot_idx=slot_idx,
                slots=slots,
                config=config,
                soc_after_charge_pct=next_soc,
                charge_kwh=charge_kwh,
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
            solar_opp_factor = self._get_solar_opportunity_penalty_factor(
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
            recon_futile_factor = self._get_futile_cycling_penalty_factor(
                action=action,
                slot_idx=slot_idx,
                slots=slots,
                config=config,
                soc_after_charge_pct=next_soc,
                charge_kwh=recon_charge_kwh,
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

            reason = self._classify_reason(
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

    def _get_solar_opportunity_penalty_factor(
        self,
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

    def _get_futile_cycling_penalty_factor(
        self,
        action: PlannerAction,
        slot_idx: int,
        slots: list[SlotContext],
        config: OptimizerConfig,
        soc_after_charge_pct: float,
        charge_kwh: float,
    ) -> float:
        """Compute penalty factor for grid charging that will be drained before a useful period.

        Issue #638: Overnight grid charging at $0.14/kWh is wasteful if the charged energy
        drains through house load before reaching a solar-surplus period or demand window.
        This factor estimates the fraction of charged energy that will be consumed by house
        load before reaching a useful period.

        Args:
            action: The action being considered (only applies to CHARGE_GRID_*)
            slot_idx: Index of the current slot
            slots: All slot contexts
            config: Optimizer config
            soc_after_charge_pct: SOC immediately after charging (post-transition)
            charge_kwh: kWh added to battery in this charge action

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

        for future_slot in slots[slot_idx + 1 :]:
            # A "useful period" is where solar surplus or demand window makes the
            # charged energy valuable — stop draining here.
            if future_slot.solar_kwh > future_slot.consumption_kwh:
                break
            if future_slot.is_demand_window_slot:
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

    def _can_solar_reach_target(
        self,
        inputs: OptimizerInputs,
        slots: list[SlotContext],
        config: OptimizerConfig,
        terminal_penalty_idx: int | None,
    ) -> bool:
        """Check if solar alone can reach target.

        Args:
            inputs: Optimizer inputs
            slots: Slot contexts
            config: Optimizer config
            terminal_penalty_idx: Terminal penalty index

        Returns:
            True if solar can reach target

        """
        return (
            _simulate_solar_only_terminal_soc(
                initial_soc_pct=inputs.initial_soc_pct,
                slots=slots,
                terminal_penalty_idx=terminal_penalty_idx,
                config=config,
            )
            >= config.demand_window_target_soc_pct
        )

    def _classify_reason(
        self,
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
        """
        Classify the reason for a decision based on action and context.

        Uses deterministic rules to assign a primary reason code.
        """
        if action == PlannerAction.HOLD:
            return self._classify_hold_reason(
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
            return self._classify_export_reason(
                slot,
                slot_idx=slot_idx,
                negative_fit_avoidance_context=negative_fit_avoidance_context,
            )
        if action in (
            PlannerAction.CHARGE_GRID_NORMAL,
            PlannerAction.CHARGE_GRID_BOOST,
        ):
            return self._classify_charge_reason(
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

    def _classify_hold_reason(
        self,
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
            factor = self._get_solar_opportunity_penalty_factor(
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

    def _classify_export_reason(
        self,
        slot: SlotContext,
        *,
        slot_idx: int | None = None,
        negative_fit_avoidance_context: NegativeFitAvoidanceContext | None = None,
    ) -> PlannerReasonCode:
        """Classify EXPORT action reason.

        Args:
            slot: Slot context

        Returns:
            Reason code for EXPORT action

        """
        if (
            negative_fit_avoidance_context is not None
            and slot_idx is not None
            and slot_idx < negative_fit_avoidance_context.risk_window_start_idx
        ):
            return PlannerReasonCode.NEGATIVE_FIT_AVOIDANCE

        if slot.sell_price > 0:
            return PlannerReasonCode.HIGH_SELL_PRICE_EXPORT
        return PlannerReasonCode.NEGATIVE_FIT_AVOIDANCE

    def _classify_charge_reason(
        self,
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
        """Classify CHARGE action reason.

        Args:
            slot: Slot context
            slot_idx: Slot index
            slots: All slots
            soc: Current SOC
            config: Optimizer config
            terminal_penalty_idx: Terminal penalty index
            objective_terms: Cost breakdown for this slot/action
            inputs: Full optimizer inputs (optional)

        Returns:
            Reason code for CHARGE action

        """
        if self._is_target_shortfall_risk(
            slot_idx, slots, soc, config, terminal_penalty_idx, inputs=inputs
        ):
            return PlannerReasonCode.TARGET_SHORTFALL_RISK
        if self._is_cheap_import_window(
            slot, config, terminal_penalty_idx, slots, inputs=inputs
        ):
            return PlannerReasonCode.CHEAP_IMPORT_WINDOW
        if objective_terms and objective_terms.solar_opportunity_penalty > 0:
            return PlannerReasonCode.SOLAR_OPPORTUNITY_WAIT
        return PlannerReasonCode.UNCLASSIFIED

    def _is_target_shortfall_risk(
        self,
        slot_idx: int,
        slots: list[SlotContext],
        soc: float,
        config: OptimizerConfig,
        terminal_penalty_idx: int | None,
        inputs: OptimizerInputs | None = None,
    ) -> bool:
        """Check if grid charge is needed for demand window target.

        Incorporates future solar gain from Solcast beyond the horizon (Issue #619).
        """
        if terminal_penalty_idx is None or slot_idx >= terminal_penalty_idx:
            return False
        soc_deficit = config.demand_window_target_soc_pct - soc
        if soc_deficit <= 0:
            return False

        # 1. Gain from solar within slots
        potential_soc_gain_pct = DPPlanner._projected_solar_soc_gain_pct(
            slot_idx=slot_idx,
            slots=slots,
            terminal_penalty_idx=terminal_penalty_idx,
            battery_capacity_kwh=config.battery_capacity_kwh,
        )

        # 2. Gain from solar beyond horizon (Issue #619)
        if inputs and inputs.all_solcast:
            last_slot = slots[-1]
            last_slot_start = datetime.fromisoformat(last_slot.timestamp_iso)
            last_slot_end = last_slot_start + timedelta(
                minutes=last_slot.slot_interval_minutes
            )
            target_slot = slots[terminal_penalty_idx]
            target_time = datetime.fromisoformat(target_slot.timestamp_iso)

            future_gain = DPPlanner._projected_solcast_gain_pct(
                inputs.all_solcast,
                start_time=last_slot_end,
                end_time=target_time,
                battery_capacity_kwh=config.battery_capacity_kwh,
            )
            potential_soc_gain_pct += future_gain

        return potential_soc_gain_pct < soc_deficit

    def _is_cheap_import_window(
        self,
        slot: SlotContext,
        config: OptimizerConfig,
        terminal_penalty_idx: int | None,
        slots: list[SlotContext],
        *,
        inputs: OptimizerInputs | None = None,
    ) -> bool:
        """Check if this is a cheap import window opportunity.

        Args:
            slot: Slot context
            config: Optimizer config
            terminal_penalty_idx: Terminal penalty index
            slots: All slots
            inputs: Full optimizer inputs (optional)

        Returns:
            True if cheap import window

        """
        if slot.buy_price > config.effective_cheap_price:
            return False
        is_blind = self._is_blind_to_future_solar(
            terminal_penalty_idx, slots, inputs=inputs
        )
        return not is_blind or slot.buy_price <= (config.effective_cheap_price * 0.8)

    def _is_blind_to_future_solar(
        self,
        terminal_penalty_idx: int | None,
        slots: list[SlotContext],
        inputs: OptimizerInputs | None = None,
    ) -> bool:
        """Check if optimizer is blind to future solar (Issue #431 Horizon Guard).

        Args:
            terminal_penalty_idx: Terminal penalty index
            slots: All slots
            inputs: Full optimizer inputs (to check all_solcast)

        Returns:
            True if blind to future solar

        """
        # If we have horizon-aware solar forecast, we aren't blind (Issue #610)
        if inputs and inputs.all_solcast:
            return False

        if terminal_penalty_idx is None:
            return True
        slots_beyond = len(slots) - terminal_penalty_idx - 1
        return slots_beyond < 8

    # ------------------------------------------------------------------
    # Pure primitive functions (to be expanded in Phase C of #403)
    # ------------------------------------------------------------------

    @staticmethod
    def _projected_solar_soc_gain_pct(
        slot_idx: int,
        slots: list[SlotContext],
        terminal_penalty_idx: int,
        battery_capacity_kwh: float,
    ) -> float:
        """
        Estimate the net SOC gain (%) achievable from solar between slot_idx
        (inclusive) and terminal_penalty_idx (exclusive), after subtracting
        household consumption.

        A positive return value means solar surplus exceeds consumption over the
        window; negative means consumption exceeds solar (net grid draw expected).

        Used by feasible_actions() to decide whether to suppress grid charging.
        """
        projected_net_kwh = sum(
            s.solar_kwh - s.consumption_kwh
            for s in slots[slot_idx:terminal_penalty_idx]
        )
        return (projected_net_kwh / battery_capacity_kwh) * 100.0

    @staticmethod
    def _projected_solcast_gain_pct(
        all_solcast: list[dict[str, Any]],
        start_time: datetime,
        end_time: datetime,
        battery_capacity_kwh: float,
        avg_load_kw: float = 0.5,
    ) -> float:
        """Estimate net SOC gain (%) from solar in Solcast beyond the DP horizon.

        Calculates sum(solar - consumption) for the window [start_time, end_time).
        Solar comes from pv_estimate in all_solcast; consumption is estimated
        using avg_load_kw.
        """
        if end_time <= start_time:
            return 0.0

        solar_kwh = 0.0
        for period in all_solcast:
            p_start_str = period.get("period_start")
            if not p_start_str:
                continue
            try:
                p_start = datetime.fromisoformat(str(p_start_str))
                # Solcast periods are typically 30 mins
                if start_time <= p_start < end_time:
                    solar_kwh += float(period.get("pv_estimate", 0)) * 0.5
            except (ValueError, TypeError):
                continue

        hours = (end_time - start_time).total_seconds() / 3600.0
        consumption_kwh = avg_load_kw * hours

        net_kwh = max(0.0, solar_kwh - consumption_kwh)
        return (net_kwh / battery_capacity_kwh) * 100.0

    def _get_forecast_accuracy(
        self,
        solar_accuracy_tracker: SolarAccuracyTracker | None,
    ) -> float:
        """Get overall forecast accuracy from tracker.

        Returns:
            float: Accuracy as decimal (0.0 to 1.0), or 1.0 if unavailable/invalid
        """
        if solar_accuracy_tracker is None:
            return 1.0

        try:
            accuracy_pct = solar_accuracy_tracker.metrics.accuracy
        except AttributeError:
            return 1.0

        if accuracy_pct is None or accuracy_pct <= 0:
            return 1.0

        return accuracy_pct / 100.0

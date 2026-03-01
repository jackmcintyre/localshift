"""
optimizer_dp.py — DP-based battery optimizer.

Phase: Phase C — DP Solver Implementation (#403).
Status: SHADOW ONLY — does not control runtime behavior.

This module provides:
- PlannerAction     — enum of discrete battery control actions
- SlotContext       — normalized per-slot input (price, solar, consumption, constraints)
- OptimizerInputs   — full horizon inputs for a single planning cycle
- OptimizerConfig   — tunable constraint and objective parameters
- PlannedSlotDecision — per-slot optimizer output including action, reason code, objective terms
- OptimizerResult   — full plan result from DPPlanner.plan()
- DPPlanner         — deterministic DP solver over (slot_index, soc_bin) state space

Design principles:
- Pure, side-effect free functions (feasibility, transition, objective).
- Deterministic: identical inputs produce identical output.
- Debuggable: every slot decision carries reason code + objective term breakdown.
- No HA/coordinator dependencies at this level (data is injected via OptimizerInputs).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action vocabulary
# ---------------------------------------------------------------------------


class PlannerAction(StrEnum):
    """Discrete control actions the optimizer may assign to a forecast slot."""

    HOLD = "hold"
    """Battery operates in self-consumption mode; no active grid import/export."""

    CHARGE_GRID_NORMAL = "charge_grid_normal"
    """Charge battery from grid at normal rate (buy_price < threshold)."""

    CHARGE_GRID_BOOST = "charge_grid_boost"
    """Charge battery from grid at maximum rate (emergency / strong opportunity)."""

    EXPORT_PROACTIVE = "export_proactive"
    """Discharge battery to grid during high sell-price window."""


# ---------------------------------------------------------------------------
# Reason codes
# ---------------------------------------------------------------------------


class PlannerReasonCode(StrEnum):
    """Classification codes for optimizer slot decisions (used in diagnostics/debugging)."""

    TARGET_SHORTFALL_RISK = "TARGET_SHORTFALL_RISK"
    """Grid charge needed because solar cannot meet demand-window SOC target."""

    CHEAP_IMPORT_WINDOW = "CHEAP_IMPORT_WINDOW"
    """Grid charge justified by low import price relative to shadow cost of storage."""

    SOLAR_SURPLUS_CAPTURE = "SOLAR_SURPLUS_CAPTURE"
    """Holding to absorb forecast solar surplus; grid charge unnecessary."""

    NEGATIVE_FIT_AVOIDANCE = "NEGATIVE_FIT_AVOIDANCE"
    """Export avoided because sell price is negative or below profitability floor."""

    HIGH_SELL_PRICE_EXPORT = "HIGH_SELL_PRICE_EXPORT"
    """Proactive export justified by high sell price; headroom created for solar."""

    SOC_FLOOR_CONSTRAINT = "SOC_FLOOR_CONSTRAINT"
    """Action constrained by minimum SOC safety floor."""

    SOC_CEILING_CONSTRAINT = "SOC_CEILING_CONSTRAINT"
    """Action constrained by maximum SOC (battery full)."""

    DEMAND_WINDOW_CONSTRAINT = "DEMAND_WINDOW_CONSTRAINT"
    """Action constrained by demand window entry requirements."""

    IDLE = "IDLE"
    """No economic or constraint reason to act; holding in self-consumption."""

    UNCLASSIFIED = "UNCLASSIFIED"
    """Reason not yet classified (should not appear in stable production)."""


# ---------------------------------------------------------------------------
# Per-slot context (normalized inputs)
# ---------------------------------------------------------------------------


@dataclass
class SlotContext:
    """
    Normalized representation of a single forecast slot.

    All energy quantities are in kWh; power in kW; prices in $/kWh.
    Slot interval is in minutes and must be explicit (no assumption of 30-min slots).
    """

    slot_index: int
    """0-based index within the planning horizon."""

    timestamp_iso: str
    """ISO 8601 UTC timestamp for this slot (used for alignment / audit)."""

    slot_interval_minutes: int
    """Duration of this slot in minutes (typically 5 or 30)."""

    buy_price: float
    """Import price in $/kWh for this slot."""

    sell_price: float
    """Export (FIT) price in $/kWh. May be negative."""

    solar_kwh: float
    """Forecast solar generation for this slot in kWh."""

    consumption_kwh: float
    """Forecast household consumption for this slot in kWh."""

    is_demand_window_entry: bool = False
    """True if this slot is the demand-window entry boundary."""

    is_demand_window_slot: bool = False
    """True if this slot falls within the demand window."""

    price_source: str = "unknown"
    """Source of price data (e.g. '5min', '30min', 'synthetic')."""


# ---------------------------------------------------------------------------
# Optimizer configuration
# ---------------------------------------------------------------------------


@dataclass
class OptimizerConfig:
    """
    Tunable parameters controlling optimizer constraints and objective weights.

    All default values are conservative starting points; tune via comparison analytics.
    """

    # --- Battery hardware constraints ---
    battery_capacity_kwh: float = 13.5
    """Usable battery capacity in kWh."""

    charge_rate_kw: float = 3.3
    """Maximum battery charge rate from grid in kW (matches CHARGE_RATE_GRID_KW)."""

    boost_charge_rate_kw: float = 5.0
    """Maximum battery charge rate in boost mode in kW."""

    discharge_rate_kw: float = 5.0
    """Maximum battery discharge rate in kW."""

    charge_efficiency: float = 0.95
    """Round-trip charging efficiency (0–1)."""

    discharge_efficiency: float = 0.95
    """Round-trip discharging efficiency (0–1)."""

    min_soc_pct: float = 10.0
    """Minimum allowed SOC (%)."""

    max_soc_pct: float = 100.0
    """Maximum allowed SOC (%)."""

    # --- Demand window target ---
    demand_window_target_soc_pct: float = 80.0
    """Required SOC (%) at demand window entry."""

    # --- Objective weights ---
    target_shortfall_penalty_per_pct: float = 1.0
    """Penalty applied per % SOC below target at demand-window entry."""

    cycle_penalty_per_kwh: float = 0.005
    """Mild penalty per kWh cycled to discourage unnecessary grid arbitrage."""

    # --- SOC discretization ---
    soc_bins: int = 50
    """Number of SOC bins for DP state space (higher = more precise, slower)."""


# ---------------------------------------------------------------------------
# Per-slot decision output
# ---------------------------------------------------------------------------


@dataclass
class ObjectiveTerms:
    """Breakdown of objective cost for a single slot/action combination."""

    import_cost: float = 0.0
    """Cost of grid import in this slot (positive = cost)."""

    export_revenue: float = 0.0
    """Revenue from grid export in this slot (positive = revenue)."""

    cycle_penalty: float = 0.0
    """Penalty for battery cycling."""

    shortfall_penalty: float = 0.0
    """Terminal penalty applied at demand window boundary (only for terminal slots)."""

    @property
    def net_cost(self) -> float:
        """Net slot cost = import - revenue + penalties."""
        return self.import_cost - self.export_revenue + self.cycle_penalty + self.shortfall_penalty

    def to_dict(self) -> dict:
        """Serialize to dict for sensor attributes and shadow output."""
        return {
            "import_cost": self.import_cost,
            "export_revenue": self.export_revenue,
            "cycle_penalty": self.cycle_penalty,
            "shortfall_penalty": self.shortfall_penalty,
            "net_cost": self.net_cost,
        }


@dataclass
class PlannedSlotDecision:
    """
    Optimizer output for a single forecast slot.

    Compatible fields are provided so the existing forecast pipeline
    can derive legacy boolean flags from action.
    """

    slot_index: int
    timestamp_iso: str
    slot_interval_minutes: int

    action: PlannerAction
    """The optimizer's chosen action for this slot."""

    reason_code: PlannerReasonCode
    """Primary reason/classification for this decision."""

    objective_terms: ObjectiveTerms
    """Per-slot objective term breakdown for debugging."""

    predicted_soc_pct: float
    """Predicted battery SOC (%) at the end of this slot."""

    grid_import_kwh: float
    """Grid import energy for this slot (kWh)."""

    grid_export_kwh: float
    """Grid export energy for this slot (kWh)."""

    # --- Derived compatibility flags (set from action) ---
    @property
    def grid_charge(self) -> bool:
        return self.action in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)

    @property
    def grid_charge_boost(self) -> bool:
        return self.action == PlannerAction.CHARGE_GRID_BOOST

    @property
    def proactive_export(self) -> bool:
        return self.action == PlannerAction.EXPORT_PROACTIVE


# ---------------------------------------------------------------------------
# Full optimizer result
# ---------------------------------------------------------------------------


@dataclass
class OptimizerResult:
    """Full output from a DPPlanner.plan() call."""

    success: bool
    """True if optimizer produced a complete valid plan."""

    planner_version: str = "dp_v1"
    """Version identifier for this planner (used in comparison records)."""

    solve_time_seconds: float = 0.0
    """Wall-clock time taken to solve (for performance diagnostics)."""

    total_slots: int = 0
    """Total number of slots in the planning horizon."""

    states_explored: int = 0
    """Number of DP states evaluated."""

    decisions: list[PlannedSlotDecision] = field(default_factory=list)
    """Ordered list of per-slot decisions from first slot to end of horizon."""

    projected_import_kwh: float = 0.0
    """Total projected grid import over horizon (kWh)."""

    projected_export_kwh: float = 0.0
    """Total projected grid export over horizon (kWh)."""

    projected_net_cost: float = 0.0
    """Total projected net cost over horizon ($)."""

    terminal_shortfall_pct: float = 0.0
    """Residual SOC shortfall (%) at demand window entry, if any."""

    error_message: str | None = None
    """Error description if success=False."""

    reason_code_histogram: dict[str, int] = field(default_factory=dict)
    """Count of each reason code across all slots (for diagnostics)."""


# ---------------------------------------------------------------------------
# Optimizer inputs
# ---------------------------------------------------------------------------


@dataclass
class OptimizerInputs:
    """
    Full inputs for a single planning cycle.

    The coordinator is responsible for populating this from
    coordinator data, forecast series, and config.
    """

    cycle_id: str
    """Unique identifier for this planning cycle (for audit/comparison)."""

    initial_soc_pct: float
    """Battery SOC at the start of the planning horizon (%)."""

    slots: list[SlotContext]
    """Ordered list of forecast slots from now to end of horizon."""

    config: OptimizerConfig = field(default_factory=OptimizerConfig)
    """Optimizer configuration and constraints."""


# ---------------------------------------------------------------------------
# DP Planner
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Helper functions for DP solver
# ---------------------------------------------------------------------------


def _build_soc_grid(config: OptimizerConfig) -> list[float]:
    """
    Build SOC discretization grid from min_soc_pct to max_soc_pct.

    Returns a list of SOC values evenly spaced across the valid range.
    The grid always includes both boundaries.
    """
    if config.soc_bins <= 1:
        return [config.min_soc_pct]

    step = (config.max_soc_pct - config.min_soc_pct) / (config.soc_bins - 1)
    return [config.min_soc_pct + i * step for i in range(config.soc_bins)]


def _map_soc_to_bin(soc_pct: float, grid: list[float]) -> int:
    """
    Map a SOC percentage to the nearest bin index in the grid.

    Uses nearest-neighbor mapping for determinism.
    Returns 0 if grid is empty.
    """
    if not grid:
        return 0

    # Find nearest bin
    best_idx = 0
    best_dist = abs(soc_pct - grid[0])
    for i, soc in enumerate(grid[1:], start=1):
        dist = abs(soc_pct - soc)
        if dist < best_dist:
            best_dist = dist
            best_idx = i
    return best_idx


def _interpolate_cost_to_soc(
    soc_pct: float,
    soc_grid: list[float],
    cost_table: dict[int, float],
) -> float:
    """
    Interpolate cost from cost_table at nearest grid points to target SOC.

    Uses linear interpolation between adjacent bins for smoother cost landscape.
    Falls back to nearest-neighbor if at boundaries.
    """
    if not soc_grid or not cost_table:
        return float("inf")

    # Find the two bins bracketing soc_pct
    lower_idx = 0
    for i in range(len(soc_grid) - 1, -1, -1):
        if soc_grid[i] <= soc_pct and i in cost_table:
            lower_idx = i
            break
    else:
        # soc_pct is below all grid points - use lowest
        lower_idx = min(cost_table.keys())

    upper_idx = len(soc_grid) - 1
    for i in range(len(soc_grid)):
        if soc_grid[i] >= soc_pct and i in cost_table:
            upper_idx = i
            break
    else:
        # soc_pct is above all grid points - use highest
        upper_idx = max(cost_table.keys())

    if lower_idx == upper_idx:
        return cost_table.get(lower_idx, float("inf"))

    # Linear interpolation
    lower_soc = soc_grid[lower_idx]
    upper_soc = soc_grid[upper_idx]
    lower_cost = cost_table.get(lower_idx, float("inf"))
    upper_cost = cost_table.get(upper_idx, float("inf"))

    if upper_soc == lower_soc:
        return lower_cost

    ratio = (soc_pct - lower_soc) / (upper_soc - lower_soc)
    return lower_cost + ratio * (upper_cost - lower_cost)


# Action priority for deterministic tie-breaking (lower index = higher priority)
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
            _LOGGER.error("DPPlanner.plan() failed for cycle %s: %s", inputs.cycle_id, exc)
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

        # Handle empty input
        if n_slots == 0:
            return OptimizerResult(
                success=True,
                planner_version=self.VERSION,
                total_slots=0,
                states_explored=0,
                decisions=[],
                reason_code_histogram={},
            )

        # Build SOC discretization grid
        soc_grid = _build_soc_grid(config)
        n_bins = len(soc_grid)

        # Find demand window entry slot (if any)
        demand_window_entry_idx = None
        for i, slot in enumerate(slots):
            if slot.is_demand_window_entry:
                demand_window_entry_idx = i
                break

        # ------------------------------------------------------------------
        # Forward pass: compute cost-to-go tables
        # dp[slot_idx][soc_bin] = (min_cost, best_action, next_soc_bin)
        # ------------------------------------------------------------------
        dp: list[dict[int, tuple[float, PlannerAction, int, float, float, float]]] = [
            {} for _ in range(n_slots + 1)
        ]

        # Initialize terminal costs (after last slot)
        if demand_window_entry_idx is not None:
            # Apply shortfall penalty at demand window entry
            target = config.demand_window_target_soc_pct
            for bin_idx, soc in enumerate(soc_grid):
                shortfall_penalty = DPPlanner.terminal_cost(soc, target, config)
                dp[n_slots][bin_idx] = (shortfall_penalty, PlannerAction.HOLD, bin_idx, 0.0, 0.0, 0.0)
        else:
            # No demand window - no terminal penalty
            for bin_idx in range(n_bins):
                dp[n_slots][bin_idx] = (0.0, PlannerAction.HOLD, bin_idx, 0.0, 0.0, 0.0)

        states_explored = 0

        # Backward induction: fill DP tables from last slot to first
        for slot_idx in range(n_slots - 1, -1, -1):
            slot = slots[slot_idx]

            for bin_idx, soc in enumerate(soc_grid):
                # Get feasible actions for this state
                actions = DPPlanner.feasible_actions(soc, slot, config)

                best_cost = float("inf")
                best_action = PlannerAction.HOLD
                best_next_bin = bin_idx
                best_import = 0.0
                best_export = 0.0
                best_next_soc = soc

                for action in actions:
                    # Compute transition
                    next_soc, grid_import, grid_export = DPPlanner.transition(
                        soc, action, slot, config
                    )

                    # Clamp next_soc to valid range
                    next_soc = max(config.min_soc_pct, min(config.max_soc_pct, next_soc))

                    # Map next_soc to nearest bin
                    next_bin = _map_soc_to_bin(next_soc, soc_grid)

                    # Get future cost from next slot
                    future_cost = dp[slot_idx + 1].get(next_bin, (float("inf"),))[0]

                    # If exact bin not found, interpolate
                    if future_cost == float("inf") and dp[slot_idx + 1]:
                        future_cost = _interpolate_cost_to_soc(
                            next_soc, soc_grid, {k: v[0] for k, v in dp[slot_idx + 1].items()}
                        )

                    # Compute stage cost
                    stage = DPPlanner.stage_cost(
                        action, grid_import, grid_export, slot, config
                    )
                    total_cost = stage.net_cost + future_cost

                    # Deterministic tie-breaking: prefer lower priority index
                    if (
                        total_cost < best_cost
                        or (
                            total_cost == best_cost
                            and _ACTION_PRIORITY.get(action, 99) < _ACTION_PRIORITY.get(best_action, 99)
                        )
                    ):
                        best_cost = total_cost
                        best_action = action
                        best_next_bin = next_bin
                        best_import = grid_import
                        best_export = grid_export
                        best_next_soc = next_soc

                    states_explored += 1

                dp[slot_idx][bin_idx] = (
                    best_cost,
                    best_action,
                    best_next_bin,
                    best_import,
                    best_export,
                    best_next_soc,
                )

        # ------------------------------------------------------------------
        # Forward pass: reconstruct optimal path
        # ------------------------------------------------------------------
        decisions: list[PlannedSlotDecision] = []
        current_soc = inputs.initial_soc_pct
        current_bin = _map_soc_to_bin(current_soc, soc_grid)

        total_import = 0.0
        total_export = 0.0
        total_net_cost = 0.0
        reason_histogram: dict[str, int] = {}

        for slot_idx, slot in enumerate(slots):
            if current_bin not in dp[slot_idx]:
                # Fallback: should not happen with proper initialization
                _LOGGER.warning(
                    "DP state missing at slot %d, bin %d - using HOLD fallback",
                    slot_idx,
                    current_bin,
                )
                action = PlannerAction.HOLD
                next_soc = current_soc
                grid_import = 0.0
                grid_export = 0.0
            else:
                _, action, next_bin, grid_import, grid_export, next_soc = dp[slot_idx][current_bin]

            # Compute stage cost for this decision
            stage = DPPlanner.stage_cost(action, grid_import, grid_export, slot, config)

            # Determine reason code
            reason = self._classify_reason(
                action=action,
                slot=slot,
                soc=current_soc,
                next_soc=next_soc,
                config=config,
                demand_window_entry_idx=demand_window_entry_idx,
            )

            # Record decision
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
            )
            decisions.append(decision)

            # Update accumulators
            total_import += grid_import
            total_export += grid_export
            total_net_cost += stage.net_cost
            reason_key = reason.value
            reason_histogram[reason_key] = reason_histogram.get(reason_key, 0) + 1

            # Advance state
            current_soc = next_soc
            current_bin = _map_soc_to_bin(current_soc, soc_grid)

        # Calculate terminal shortfall (at demand window entry if applicable)
        terminal_shortfall = 0.0
        if demand_window_entry_idx is not None and demand_window_entry_idx < len(decisions):
            terminal_soc = decisions[demand_window_entry_idx].predicted_soc_pct
            target = config.demand_window_target_soc_pct
            terminal_shortfall = max(0.0, target - terminal_soc)

        return OptimizerResult(
            success=True,
            planner_version=self.VERSION,
            total_slots=n_slots,
            states_explored=states_explored,
            decisions=decisions,
            projected_import_kwh=total_import,
            projected_export_kwh=total_export,
            projected_net_cost=total_net_cost,
            terminal_shortfall_pct=terminal_shortfall,
            reason_code_histogram=reason_histogram,
        )

    def _classify_reason(
        self,
        action: PlannerAction,
        slot: SlotContext,
        soc: float,
        next_soc: float,
        config: OptimizerConfig,
        demand_window_entry_idx: int | None,
    ) -> PlannerReasonCode:
        """
        Classify the reason for a decision based on action and context.

        Uses deterministic rules to assign a primary reason code.
        """
        # SOC constraint checks
        if action == PlannerAction.HOLD:
            if soc >= config.max_soc_pct - 0.5:
                return PlannerReasonCode.SOC_CEILING_CONSTRAINT
            if soc <= config.min_soc_pct + 0.5:
                return PlannerReasonCode.SOC_FLOOR_CONSTRAINT

        # Export reasoning
        if action == PlannerAction.EXPORT_PROACTIVE:
            if slot.sell_price > 0:
                return PlannerReasonCode.HIGH_SELL_PRICE_EXPORT
            # Should not reach here if feasible_actions blocks negative FIT
            return PlannerReasonCode.NEGATIVE_FIT_AVOIDANCE

        # Grid charge reasoning
        if action in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST):
            # Check if needed for demand window target
            if demand_window_entry_idx is not None and slot.slot_index < demand_window_entry_idx:
                # Check if current trajectory would miss target
                slots_remaining = demand_window_entry_idx - slot.slot_index
                soc_deficit = config.demand_window_target_soc_pct - soc
                if soc_deficit > 0:
                    # Rough estimate: can solar alone meet the target?
                    future_solar = sum(
                        s.solar_kwh - s.consumption_kwh
                        for s in (
                            slot for i, slot in enumerate(
                                [slot] * slots_remaining  # Approximation
                            )
                        )
                    )
                    potential_soc_gain = (future_solar / config.battery_capacity_kwh) * 100.0
                    if potential_soc_gain < soc_deficit * 0.8:
                        return PlannerReasonCode.TARGET_SHORTFALL_RISK

            # Check for cheap import opportunity
            if slot.buy_price < 0.15:  # Threshold for "cheap"
                return PlannerReasonCode.CHEAP_IMPORT_WINDOW

            return PlannerReasonCode.TARGET_SHORTFALL_RISK

        # HOLD with solar surplus
        if action == PlannerAction.HOLD:
            net_kwh = slot.solar_kwh - slot.consumption_kwh
            if net_kwh > 0 and next_soc > soc:
                return PlannerReasonCode.SOLAR_SURPLUS_CAPTURE

        # Default
        return PlannerReasonCode.IDLE

    # ------------------------------------------------------------------
    # Pure primitive functions (to be expanded in Phase C of #403)
    # ------------------------------------------------------------------

    @staticmethod
    def feasible_actions(
        soc_pct: float,
        slot: SlotContext,
        config: OptimizerConfig,
    ) -> list[PlannerAction]:
        """
        Return list of actions feasible from given SOC and slot context.

        Constraints checked:
        - SOC floor/ceiling
        - Demand window entry requirements
        - Slot duration vs transfer limits (TODO: implement fully in Phase C)
        """
        actions = []

        can_charge = soc_pct < config.max_soc_pct
        can_discharge = soc_pct > config.min_soc_pct

        actions.append(PlannerAction.HOLD)

        if can_charge:
            actions.append(PlannerAction.CHARGE_GRID_NORMAL)
            actions.append(PlannerAction.CHARGE_GRID_BOOST)

        if can_discharge and slot.sell_price > 0:
            actions.append(PlannerAction.EXPORT_PROACTIVE)

        return actions

    @staticmethod
    def transition(
        soc_pct: float,
        action: PlannerAction,
        slot: SlotContext,
        config: OptimizerConfig,
    ) -> tuple[float, float, float]:
        """
        Compute next SOC, grid_import_kwh, grid_export_kwh for a given action.

        All actions account for solar generation and household consumption.

        Returns:
            (next_soc_pct, grid_import_kwh, grid_export_kwh)

        Phase C: Full implementation with efficiency losses and SOC clipping.
        """
        slot_hours = slot.slot_interval_minutes / 60.0
        net_kwh = slot.solar_kwh - slot.consumption_kwh  # positive = surplus
        capacity_kwh = config.battery_capacity_kwh

        if action == PlannerAction.HOLD:
            # Battery absorbs surplus or supplies deficit from solar/consumption balance
            # Net positive = charge from solar surplus
            # Net negative = discharge to meet consumption
            delta_soc = (net_kwh / capacity_kwh) * 100.0
            next_soc = soc_pct + delta_soc
            # Clip to valid range
            next_soc = max(config.min_soc_pct, min(config.max_soc_pct, next_soc))
            return next_soc, 0.0, 0.0

        if action == PlannerAction.CHARGE_GRID_NORMAL:
            # Grid charge at normal rate, plus solar/consumption net effect
            max_charge_kwh = config.charge_rate_kw * slot_hours
            effective_charge_kwh = max_charge_kwh * config.charge_efficiency

            # Account for solar surplus (reduces grid need) or deficit (increases effective charge)
            # Solar surplus goes to battery first, then grid tops up
            if net_kwh > 0:
                # Solar surplus charges battery directly
                solar_to_battery = net_kwh * config.charge_efficiency
                # Grid charge only what's needed to fill remaining capacity
                soc_from_solar = (solar_to_battery / capacity_kwh) * 100.0
                remaining_headroom = config.max_soc_pct - soc_pct - soc_from_solar
                if remaining_headroom > 0:
                    grid_charge_kwh = min(effective_charge_kwh, (remaining_headroom / 100.0) * capacity_kwh)
                else:
                    grid_charge_kwh = 0.0
            else:
                # Net consumption - grid must charge battery AND cover deficit
                # Battery still charges from grid, but household draws from grid too
                grid_charge_kwh = effective_charge_kwh

            delta_soc = (grid_charge_kwh / capacity_kwh) * 100.0
            if net_kwh > 0:
                delta_soc += (net_kwh * config.charge_efficiency / capacity_kwh) * 100.0

            next_soc = soc_pct + delta_soc
            # Clip to max SOC
            if next_soc > config.max_soc_pct:
                # Reduce grid import to exactly hit max
                actual_grid_kwh = max(0.0, (config.max_soc_pct - soc_pct) / 100.0 * capacity_kwh)
                next_soc = config.max_soc_pct
                return next_soc, actual_grid_kwh, 0.0

            return next_soc, grid_charge_kwh, 0.0

        if action == PlannerAction.CHARGE_GRID_BOOST:
            # Grid charge at boost rate, plus solar/consumption net effect
            max_charge_kwh = config.boost_charge_rate_kw * slot_hours
            effective_charge_kwh = max_charge_kwh * config.charge_efficiency

            if net_kwh > 0:
                solar_to_battery = net_kwh * config.charge_efficiency
                soc_from_solar = (solar_to_battery / capacity_kwh) * 100.0
                remaining_headroom = config.max_soc_pct - soc_pct - soc_from_solar
                if remaining_headroom > 0:
                    grid_charge_kwh = min(effective_charge_kwh, (remaining_headroom / 100.0) * capacity_kwh)
                else:
                    grid_charge_kwh = 0.0
            else:
                grid_charge_kwh = effective_charge_kwh

            delta_soc = (grid_charge_kwh / capacity_kwh) * 100.0
            if net_kwh > 0:
                delta_soc += (net_kwh * config.charge_efficiency / capacity_kwh) * 100.0

            next_soc = soc_pct + delta_soc
            # Clip to max SOC
            if next_soc > config.max_soc_pct:
                actual_grid_kwh = max(0.0, (config.max_soc_pct - soc_pct) / 100.0 * capacity_kwh)
                next_soc = config.max_soc_pct
                return next_soc, actual_grid_kwh, 0.0

            return next_soc, grid_charge_kwh, 0.0

        if action == PlannerAction.EXPORT_PROACTIVE:
            # Discharge to grid at max rate
            max_discharge_kwh = config.discharge_rate_kw * slot_hours
            # Effective export accounts for discharge efficiency loss
            effective_export_kwh = max_discharge_kwh / config.discharge_efficiency

            # Account for solar/consumption net
            # Solar goes directly to export (not through battery)
            # Consumption reduces effective export
            net_export = effective_export_kwh
            if net_kwh > 0:
                # Solar surplus adds to export directly
                net_export += net_kwh
            else:
                # Consumption deficit reduces export
                net_export = max(0.0, net_export + net_kwh)

            delta_soc = -(effective_export_kwh / capacity_kwh) * 100.0
            next_soc = soc_pct + delta_soc

            # Clip to min SOC
            if next_soc < config.min_soc_pct:
                # Reduce export to exactly hit min
                available_kwh = max(0.0, (soc_pct - config.min_soc_pct) / 100.0 * capacity_kwh)
                actual_export = available_kwh * config.discharge_efficiency
                next_soc = config.min_soc_pct
                return next_soc, 0.0, actual_export

            return next_soc, 0.0, net_export

        return soc_pct, 0.0, 0.0

    @staticmethod
    def stage_cost(
        action: PlannerAction,
        grid_import_kwh: float,
        grid_export_kwh: float,
        slot: SlotContext,
        config: OptimizerConfig,
    ) -> ObjectiveTerms:
        """
        Compute per-slot stage cost terms for an action.

        Returns ObjectiveTerms with all applicable cost components.
        """
        import_cost = grid_import_kwh * slot.buy_price
        export_revenue = grid_export_kwh * max(0.0, slot.sell_price)
        cycle_kwh = grid_import_kwh + grid_export_kwh
        cycle_penalty = cycle_kwh * config.cycle_penalty_per_kwh

        return ObjectiveTerms(
            import_cost=import_cost,
            export_revenue=export_revenue,
            cycle_penalty=cycle_penalty,
        )

    @staticmethod
    def terminal_cost(
        final_soc_pct: float,
        target_soc_pct: float,
        config: OptimizerConfig,
    ) -> float:
        """
        Compute penalty for missing the demand-window SOC target.

        Returns 0 if target is met; positive penalty per % shortfall otherwise.
        """
        shortfall = max(0.0, target_soc_pct - final_soc_pct)
        return shortfall * config.target_shortfall_penalty_per_pct

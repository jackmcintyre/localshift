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

    solar_charge_rate_kw: float = 5.0
    """Maximum solar-to-battery charge rate in kW (Powerwall 3 inverter limit)."""

    discharge_rate_kw: float = 5.0
    """Maximum battery discharge rate in kW."""

    charge_efficiency: float = 0.92
    """Charging efficiency (energy lost going into battery)."""

    discharge_efficiency: float = 0.95
    """Round-trip discharging efficiency (0–1)."""

    min_soc_pct: float = 10.0
    """Minimum allowed SOC (%)."""

    max_soc_pct: float = 100.0
    """Maximum allowed SOC (%)."""

    # --- Demand window target ---
    demand_window_target_soc_pct: float = 80.0
    """Required SOC (%) at demand window entry."""

    allow_dw_entry_under_target: bool = False
    """If True, allow reaching target during DW via solar (instead of by DW start)."""

    # --- Objective weights ---
    target_shortfall_penalty_per_pct: float = 0.030
    """Penalty applied per % SOC below target at demand-window entry ($/%-point).

    This should be calibrated to the actual cost of importing 1% SOC from the grid
    at the cheapest available price, with a small safety multiplier:

        penalty = effective_cheap_price ($/kWh) * battery_capacity_kwh / 100 * safety_factor

    Example: 0.15 $/kWh * 13.5 kWh / 100 * 1.5 = $0.030 per %-point

    Do NOT use the original default of 1.0 — it is ~53x the actual remediation cost
    and causes the optimizer to grid-charge compulsively. See issue #438.

    In production, this value is computed in optimizer_shadow_runner._build_optimizer_config()
    from the live tariff data; the dataclass default here is a reasonable fallback
    for unit tests and standalone use.
    """

    cycle_penalty_per_kwh: float = 0.005
    """Mild penalty per kWh cycled to discourage unnecessary grid arbitrage."""

    # --- SOC discretization ---
    soc_bins: int = 50
    """Number of SOC bins for DP state space (higher = more precise, slower)."""

    # --- Optimization mode (Issue #406) ---
    optimization_mode: str = "self_consumption"
    """Optimization strategy: 'self_consumption' (default) or 'arbitrage'."""

    self_consumption_value_per_kwh: float = 0.15
    """Value of using battery energy for household load ($/kWh). Auto-derived from average buy price."""

    effective_cheap_price: float = 0.10
    """Price threshold for grid charging in self-consumption mode ($/kWh)."""

    export_price_margin: float = 0.02
    """Minimum profit margin for proactive export above self-consumption value ($/kWh)."""

    forecast_horizon_hours: float = 24.0
    """Actual hours of forecast available (Issue #431)."""


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

    self_consumption_value: float = 0.0
    """Value of battery energy used for household load (benefit, subtracted from cost)."""

    uncertainty_penalty: float = 0.0
    """Penalty for grid actions when forecast horizon is restricted (Issue #431)."""

    @property
    def net_cost(self) -> float:
        """Net slot cost = import - revenue - self_consumption_value + penalties."""
        return (
            self.import_cost
            - self.export_revenue
            - self.self_consumption_value
            + self.cycle_penalty
            + self.shortfall_penalty
            + self.uncertainty_penalty
        )

    def to_dict(self) -> dict:
        """Serialize to dict for sensor attributes and shadow output."""
        return {
            "import_cost": self.import_cost,
            "export_revenue": self.export_revenue,
            "cycle_penalty": self.cycle_penalty,
            "shortfall_penalty": self.shortfall_penalty,
            "self_consumption_value": self.self_consumption_value,
            "uncertainty_penalty": self.uncertainty_penalty,
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

    # --- Slot context passthroughs (for dashboard debug display) ---
    solar_kwh: float = 0.0
    """Forecast solar generation for this slot (kWh), copied from SlotContext."""

    consumption_kwh: float = 0.0
    """Forecast household consumption for this slot (kWh), copied from SlotContext."""

    buy_price: float = 0.0
    """Import price ($/kWh), copied from SlotContext."""

    sell_price: float = 0.0
    """Export (FIT) price ($/kWh), copied from SlotContext."""

    # --- Derived compatibility flags (set from action) ---
    @property
    def grid_charge(self) -> bool:
        return self.action in (
            PlannerAction.CHARGE_GRID_NORMAL,
            PlannerAction.CHARGE_GRID_BOOST,
        )

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

    can_solar_reach_target: bool = False
    """True if solar alone can reach DW target (no grid charge, no export). Phase 4, #441."""

    can_solar_reach_target_in_dw: bool = False
    """True if solar alone reaches target at any point during DW (allow_dw_entry_under_target mode). Issue #505."""

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

        # Find demand window entry and end slots (if any)
        demand_window_entry_idx = None
        demand_window_end_idx = None
        in_demand_window = False
        for i, slot in enumerate(slots):
            if slot.is_demand_window_entry:
                demand_window_entry_idx = i
            if slot.is_demand_window_slot:
                in_demand_window = True
            # DW ends when we see the first slot that's not in DW after being in DW
            if in_demand_window and not slot.is_demand_window_slot:
                demand_window_end_idx = i - 1
                break

        # If DW extends to end of horizon, set end to last slot
        if in_demand_window and demand_window_end_idx is None:
            demand_window_end_idx = n_slots - 1

        # Pre-compute whether solar can reach target during DW (Issue #505)
        # Used to conditionally zero out terminal penalty when allow_dw_entry_under_target=True
        solar_can_reach_target_in_dw = False
        if config.allow_dw_entry_under_target and demand_window_entry_idx is not None:
            max_soc_in_dw = _simulate_max_soc_in_demand_window(
                inputs.initial_soc_pct, slots, config
            )
            solar_can_reach_target_in_dw = (
                max_soc_in_dw >= config.demand_window_target_soc_pct
            )

        # Determine where to apply terminal penalty
        # If allow_dw_entry_under_target is True, apply at DW end (allows solar during DW)
        # Otherwise, apply at DW entry (must meet target by DW start)
        if config.allow_dw_entry_under_target and demand_window_end_idx is not None:
            terminal_penalty_idx = demand_window_end_idx
        else:
            terminal_penalty_idx = demand_window_entry_idx

        # ------------------------------------------------------------------
        # Forward pass: compute cost-to-go tables
        # dp[slot_idx][soc_bin] = (min_cost, best_action, next_soc_bin)
        # ------------------------------------------------------------------
        dp: list[dict[int, tuple[float, PlannerAction, int, float, float, float]]] = [
            {} for _ in range(n_slots + 1)
        ]

        # Initialize terminal costs (after last slot)
        if terminal_penalty_idx is not None:
            # Apply shortfall penalty at terminal penalty index
            target = config.demand_window_target_soc_pct
            for bin_idx, soc in enumerate(soc_grid):
                # Issue #505: When allow_dw_entry_under_target=True and solar can reach
                # target during DW, zero out the terminal penalty (trust solar)
                if config.allow_dw_entry_under_target and solar_can_reach_target_in_dw:
                    shortfall_penalty = 0.0
                else:
                    shortfall_penalty = DPPlanner.terminal_cost(soc, target, config)
                dp[n_slots][bin_idx] = (
                    shortfall_penalty,
                    PlannerAction.HOLD,
                    bin_idx,
                    0.0,
                    0.0,
                    0.0,
                )
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
                actions = DPPlanner.feasible_actions(
                    soc,
                    slot,
                    config,
                    slot_idx=slot_idx,
                    slots=slots,
                    terminal_penalty_idx=terminal_penalty_idx,
                )

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
                    next_soc = max(
                        config.min_soc_pct, min(config.max_soc_pct, next_soc)
                    )

                    # Map next_soc to nearest bin
                    next_bin = _map_soc_to_bin(next_soc, soc_grid)

                    # Get future cost from next slot
                    future_cost = dp[slot_idx + 1].get(next_bin, (float("inf"),))[0]

                    # If exact bin not found, interpolate
                    if future_cost == float("inf") and dp[slot_idx + 1]:
                        future_cost = _interpolate_cost_to_soc(
                            next_soc,
                            soc_grid,
                            {k: v[0] for k, v in dp[slot_idx + 1].items()},
                        )

                    # Compute stage cost
                    stage = DPPlanner.stage_cost(
                        action,
                        grid_import,
                        grid_export,
                        slot,
                        config,
                        soc_pct=soc,
                    )
                    total_cost = stage.net_cost + future_cost

                    # Deterministic tie-breaking: prefer lower priority index
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
            else:
                # Read optimal action from DP table (determined by backward
                # induction at the bin-center SOC).
                _, action, _, _, _, _ = dp[slot_idx][current_bin]

            # Re-compute the transition from the actual current_soc rather
            # than using the stored bin-center values.  The DP table stores
            # grid_import/export/next_soc computed at the bin-center SOC,
            # which can differ from the true SOC tracked through the forward
            # pass.  This mismatch causes physically incorrect energy
            # quantities — most visibly, zero grid_import at the SOC floor
            # when the bin center had just enough headroom to cover load but
            # the actual SOC does not.  (Fixes #414)
            next_soc, grid_import, grid_export = DPPlanner.transition(
                current_soc, action, slot, config
            )
            next_soc = max(config.min_soc_pct, min(config.max_soc_pct, next_soc))

            # Compute stage cost for this decision
            stage = DPPlanner.stage_cost(
                action,
                grid_import,
                grid_export,
                slot,
                config,
                soc_pct=current_soc,
            )

            # Determine reason code
            reason = self._classify_reason(
                action=action,
                slot=slot,
                slot_idx=slot_idx,
                slots=slots,
                soc=current_soc,
                next_soc=next_soc,
                config=config,
                terminal_penalty_idx=terminal_penalty_idx,
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
                solar_kwh=slot.solar_kwh,
                consumption_kwh=slot.consumption_kwh,
                buy_price=slot.buy_price,
                sell_price=slot.sell_price,
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

        # Calculate terminal shortfall at the terminal penalty index
        terminal_shortfall = 0.0
        if terminal_penalty_idx is not None:
            target = config.demand_window_target_soc_pct
            if config.allow_dw_entry_under_target:
                # Issue #505: Shortfall based on max SOC during DW
                max_soc_in_dw = _simulate_max_soc_in_demand_window(
                    inputs.initial_soc_pct, slots, config
                )
                terminal_shortfall = max(0.0, target - max_soc_in_dw)
            elif terminal_penalty_idx < len(decisions):
                # Original: shortfall at fixed checkpoint
                terminal_soc = decisions[terminal_penalty_idx].predicted_soc_pct
                terminal_shortfall = max(0.0, target - terminal_soc)

        # Determine if solar alone can reach the DW target (Phase 4, #441)
        can_solar = (
            _simulate_solar_only_terminal_soc(
                initial_soc_pct=inputs.initial_soc_pct,
                slots=slots,
                terminal_penalty_idx=terminal_penalty_idx,
                config=config,
            )
            >= config.demand_window_target_soc_pct
        )

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
            can_solar_reach_target=can_solar,
            can_solar_reach_target_in_dw=solar_can_reach_target_in_dw,
            reason_code_histogram=reason_histogram,
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
        if action in (
            PlannerAction.CHARGE_GRID_NORMAL,
            PlannerAction.CHARGE_GRID_BOOST,
        ):
            # Check if needed for demand window target
            # Use terminal_penalty_idx which is DW entry (default) or DW end (if allow_dw_entry_under_target)
            if terminal_penalty_idx is not None and slot_idx < terminal_penalty_idx:
                soc_deficit = config.demand_window_target_soc_pct - soc
                if soc_deficit > 0:
                    # Use shared helper (same calculation as feasible_actions solar gate).
                    potential_soc_gain_pct = DPPlanner._projected_solar_soc_gain_pct(
                        slot_idx=slot_idx,
                        slots=slots,
                        terminal_penalty_idx=terminal_penalty_idx,
                        battery_capacity_kwh=config.battery_capacity_kwh,
                    )
                    if potential_soc_gain_pct < soc_deficit:
                        return PlannerReasonCode.TARGET_SHORTFALL_RISK

            # Check for cheap import opportunity
            if slot.buy_price <= config.effective_cheap_price:
                # Issue #431: Horizon Guard
                # If blind to next day's solar, don't use CHEAP_IMPORT_WINDOW reason
                # unless price is exceptionally cheap (safety net).
                is_blind = False
                if terminal_penalty_idx is None:
                    is_blind = True
                else:
                    # Estimate visibility beyond terminal penalty
                    slots_beyond = len(slots) - terminal_penalty_idx - 1
                    if slots_beyond < 8:  # < 4 hours visibility into DW
                        is_blind = True

                if not is_blind or slot.buy_price <= (
                    config.effective_cheap_price * 0.8
                ):
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
    def feasible_actions(
        soc_pct: float,
        slot: SlotContext,
        config: OptimizerConfig,
        slot_idx: int = 0,
        slots: list[SlotContext] | None = None,
        terminal_penalty_idx: int | None = None,
    ) -> list[PlannerAction]:
        """
        Return list of actions feasible from given SOC and slot context.

        Constraints checked:
        - SOC floor/ceiling
        - Demand window: no grid import during DW slots
        - Optimization mode (self_consumption vs arbitrage)
        - Price thresholds for self-consumption mode
        - Solar surplus gate: suppresses grid charging when solar will cover
          the full SOC deficit before the demand window (self_consumption mode only)
        - Slot duration vs transfer limits

        Args:
            soc_pct: Current battery SOC percentage.
            slot: Per-slot context (price, solar, consumption, flags).
            config: Optimizer configuration and constraints.
            slot_idx: Index of the current slot in the planning horizon (default 0).
            slots: Full list of planning slots (None disables solar gate).
            terminal_penalty_idx: Index at which the shortfall penalty is applied
                (None disables solar gate).
        """
        actions = []

        can_charge = soc_pct < config.max_soc_pct
        can_discharge = soc_pct > config.min_soc_pct

        actions.append(PlannerAction.HOLD)

        # Solar surplus gate: if projected solar can cover the full SOC deficit
        # before the demand window, suppress grid charging entirely.
        # This prevents the solver from grid-charging during solar-peak hours
        # when solar will naturally fill the battery at zero cost.
        _solar_covers_deficit = False
        if (
            config.optimization_mode == "self_consumption"
            and slots is not None
            and terminal_penalty_idx is not None
            and slot_idx < terminal_penalty_idx
        ):
            soc_deficit_pct = config.demand_window_target_soc_pct - soc_pct
            if soc_deficit_pct > 0:
                if config.allow_dw_entry_under_target:
                    # Issue #505: Check if solar reaches target at any point during DW
                    max_soc_in_dw = _simulate_max_soc_in_demand_window(
                        soc_pct, slots, config
                    )
                    if max_soc_in_dw >= config.demand_window_target_soc_pct:
                        _solar_covers_deficit = True
                else:
                    # Original: solar must cover deficit to DW entry
                    solar_gain_pct = DPPlanner._projected_solar_soc_gain_pct(
                        slot_idx=slot_idx,
                        slots=slots,
                        terminal_penalty_idx=terminal_penalty_idx,
                        battery_capacity_kwh=config.battery_capacity_kwh,
                    )
                    if solar_gain_pct >= soc_deficit_pct:
                        _solar_covers_deficit = True

        # Grid charging constraints (Issue #406)
        if can_charge and not slot.is_demand_window_slot and not _solar_covers_deficit:
            # In self-consumption mode, only charge if price is cheap
            if config.optimization_mode == "self_consumption":
                price_is_cheap = slot.buy_price <= config.effective_cheap_price
                price_is_very_cheap = (
                    slot.buy_price <= config.effective_cheap_price * 0.8
                )

                if price_is_cheap:
                    actions.append(PlannerAction.CHARGE_GRID_NORMAL)
                    if price_is_very_cheap:
                        actions.append(PlannerAction.CHARGE_GRID_BOOST)
            else:
                # Arbitrage mode: charge whenever below max (no solar gate)
                actions.append(PlannerAction.CHARGE_GRID_NORMAL)
                actions.append(PlannerAction.CHARGE_GRID_BOOST)

        # Export constraints (Issue #406)
        if can_discharge:
            # In self-consumption mode, only export if profitable vs keeping energy for load
            if config.optimization_mode == "self_consumption":
                min_profitable_sell = (
                    config.self_consumption_value_per_kwh + config.export_price_margin
                )
                if slot.sell_price >= min_profitable_sell:
                    actions.append(PlannerAction.EXPORT_PROACTIVE)
            else:
                # Arbitrage mode: export if positive price
                if slot.sell_price > 0:
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
            # Battery passively follows site net flow under HOLD:
            # - Surplus solar charges battery first, then remaining surplus exports.
            # - Net load discharges battery first, then remaining deficit imports.
            # Rate limits, efficiency, and SOC bounds are applied in both directions.

            if net_kwh >= 0:
                # Surplus solar can be sent to battery subject to rate + headroom.
                limit_kwh = config.solar_charge_rate_kw * slot_hours
                solar_surplus_kwh = net_kwh
                solar_by_rate_kwh = min(solar_surplus_kwh, limit_kwh)
                headroom_kwh = max(
                    0.0, (config.max_soc_pct - soc_pct) / 100.0 * capacity_kwh
                )

                if config.charge_efficiency <= 0:
                    solar_to_battery_kwh = 0.0
                else:
                    solar_by_soc_kwh = headroom_kwh / config.charge_efficiency
                    solar_to_battery_kwh = min(solar_by_rate_kwh, solar_by_soc_kwh)

                stored_kwh = solar_to_battery_kwh * config.charge_efficiency
                delta_soc = (stored_kwh / capacity_kwh) * 100.0
                next_soc = soc_pct + delta_soc
                grid_export_kwh = max(0.0, solar_surplus_kwh - solar_to_battery_kwh)
                return next_soc, 0.0, grid_export_kwh
            else:
                # Net load can be supplied by battery subject to rate + floor.
                limit_kwh = config.discharge_rate_kw * slot_hours
                load_deficit_kwh = -net_kwh
                discharge_by_rate_kwh = min(load_deficit_kwh, limit_kwh)
                available_battery_kwh = max(
                    0.0, (soc_pct - config.min_soc_pct) / 100.0 * capacity_kwh
                )
                max_load_from_battery_kwh = (
                    available_battery_kwh * config.discharge_efficiency
                )
                battery_to_load_kwh = min(
                    discharge_by_rate_kwh, max_load_from_battery_kwh
                )

                if config.discharge_efficiency <= 0:
                    battery_delta_kwh = 0.0
                else:
                    battery_delta_kwh = -(
                        battery_to_load_kwh / config.discharge_efficiency
                    )

                delta_soc = (battery_delta_kwh / capacity_kwh) * 100.0
                next_soc = soc_pct + delta_soc
                grid_import_kwh = max(0.0, load_deficit_kwh - battery_to_load_kwh)
                return next_soc, grid_import_kwh, 0.0

        if action == PlannerAction.CHARGE_GRID_NORMAL:
            # Grid charge at normal rate, plus solar/consumption net effect
            max_charge_kwh = config.charge_rate_kw * slot_hours
            effective_charge_kwh = max_charge_kwh * config.charge_efficiency

            if net_kwh > 0:
                # Solar surplus: solar charges battery directly
                solar_to_battery = net_kwh * config.charge_efficiency
                soc_from_solar = (solar_to_battery / capacity_kwh) * 100.0
                remaining_headroom = config.max_soc_pct - soc_pct - soc_from_solar
                if remaining_headroom > 0:
                    grid_charge_stored_kwh = min(
                        effective_charge_kwh,
                        (remaining_headroom / 100.0) * capacity_kwh,
                    )
                else:
                    grid_charge_stored_kwh = 0.0
                # Grid import: pre-efficiency energy to charge battery
                grid_import_kwh = grid_charge_stored_kwh / config.charge_efficiency
                delta_soc_from_grid = grid_charge_stored_kwh / capacity_kwh * 100.0
                delta_soc_from_solar = solar_to_battery / capacity_kwh * 100.0
                next_soc = soc_pct + delta_soc_from_grid + delta_soc_from_solar
            else:
                # Net consumption deficit: grid supplies battery charging AND household deficit
                grid_charge_stored_kwh = effective_charge_kwh
                # Grid import for battery (pre-efficiency) + household deficit
                grid_import_kwh = max_charge_kwh + (-net_kwh)
                delta_soc = (grid_charge_stored_kwh / capacity_kwh) * 100.0
                next_soc = soc_pct + delta_soc

            # Clip to max SOC if necessary
            if next_soc > config.max_soc_pct:
                # Reduce grid charging to hit max_soc exactly
                total_soc_needed = config.max_soc_pct - soc_pct
                solar_soc_contrib = 0.0
                if net_kwh > 0:
                    solar_soc_contrib = (
                        net_kwh * config.charge_efficiency / capacity_kwh
                    ) * 100.0
                grid_soc_needed = max(0.0, total_soc_needed - solar_soc_contrib)
                # Pre-efficiency grid energy needed for that SOC increase
                grid_import_for_charging = (
                    grid_soc_needed / 100.0 * capacity_kwh
                ) / config.charge_efficiency
                grid_import_total = grid_import_for_charging
                if net_kwh < 0:
                    grid_import_total += -net_kwh
                next_soc = config.max_soc_pct
                return next_soc, grid_import_total, 0.0

            return next_soc, grid_import_kwh, 0.0

        if action == PlannerAction.CHARGE_GRID_BOOST:
            # Grid charge at boost rate, plus solar/consumption net effect
            max_charge_kwh = config.boost_charge_rate_kw * slot_hours
            effective_charge_kwh = max_charge_kwh * config.charge_efficiency

            if net_kwh > 0:
                # Solar surplus: solar charges battery directly
                solar_to_battery = net_kwh * config.charge_efficiency
                soc_from_solar = (solar_to_battery / capacity_kwh) * 100.0
                remaining_headroom = config.max_soc_pct - soc_pct - soc_from_solar
                if remaining_headroom > 0:
                    grid_charge_stored_kwh = min(
                        effective_charge_kwh,
                        (remaining_headroom / 100.0) * capacity_kwh,
                    )
                else:
                    grid_charge_stored_kwh = 0.0
                # Grid import: pre-efficiency energy to charge battery
                grid_import_kwh = grid_charge_stored_kwh / config.charge_efficiency
                delta_soc_from_grid = grid_charge_stored_kwh / capacity_kwh * 100.0
                delta_soc_from_solar = solar_to_battery / capacity_kwh * 100.0
                next_soc = soc_pct + delta_soc_from_grid + delta_soc_from_solar
            else:
                # Net consumption deficit: grid supplies battery charging AND household deficit
                grid_charge_stored_kwh = effective_charge_kwh
                # Grid import for battery (pre-efficiency) + household deficit
                grid_import_kwh = max_charge_kwh + (-net_kwh)
                delta_soc = (grid_charge_stored_kwh / capacity_kwh) * 100.0
                next_soc = soc_pct + delta_soc

            # Clip to max SOC if necessary
            if next_soc > config.max_soc_pct:
                # Reduce grid charging to hit max_soc exactly
                total_soc_needed = config.max_soc_pct - soc_pct
                solar_soc_contrib = 0.0
                if net_kwh > 0:
                    solar_soc_contrib = (
                        net_kwh * config.charge_efficiency / capacity_kwh
                    ) * 100.0
                grid_soc_needed = max(0.0, total_soc_needed - solar_soc_contrib)
                # Pre-efficiency grid energy needed for that SOC increase
                grid_import_for_charging = (
                    grid_soc_needed / 100.0 * capacity_kwh
                ) / config.charge_efficiency
                grid_import_total = grid_import_for_charging
                if net_kwh < 0:
                    grid_import_total += -net_kwh
                next_soc = config.max_soc_pct
                return next_soc, grid_import_total, 0.0

            return next_soc, grid_import_kwh, 0.0

        if action == PlannerAction.EXPORT_PROACTIVE:
            # Discharge to grid at max rate
            max_discharge_kwh = config.discharge_rate_kw * slot_hours
            # Effective export accounts for discharge efficiency loss
            effective_export_kwh = max_discharge_kwh * config.discharge_efficiency

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

            delta_soc = -(max_discharge_kwh / capacity_kwh) * 100.0
            next_soc = soc_pct + delta_soc

            # Clip to min SOC
            if next_soc < config.min_soc_pct:
                # Reduce export to exactly hit min
                available_kwh = max(
                    0.0, (soc_pct - config.min_soc_pct) / 100.0 * capacity_kwh
                )
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
        *,
        soc_pct: float | None = None,
    ) -> ObjectiveTerms:
        """
        Compute per-slot stage cost terms for an action.

        In self-consumption mode, adds value for battery energy used to cover load.
        This makes the optimizer prefer keeping energy for household use over exporting
        unless the export price exceeds the self-consumption value + margin.

        Args:
            soc_pct: Current SOC percentage *before* this slot's transition.
                     When provided, caps self-consumption credit by the
                     battery's physical discharge capacity (Fixes #417).
        """
        import_cost = grid_import_kwh * slot.buy_price
        export_revenue = grid_export_kwh * max(0.0, slot.sell_price)
        cycle_kwh = grid_import_kwh + grid_export_kwh
        cycle_penalty = cycle_kwh * config.cycle_penalty_per_kwh

        # Issue #431: uncertainty penalty for grid charging when horizon is short.
        # This biases the optimizer toward HOLD (waiting for more data) when blind.
        uncertainty_penalty = 0.0
        if action in (
            PlannerAction.CHARGE_GRID_NORMAL,
            PlannerAction.CHARGE_GRID_BOOST,
        ):
            if config.forecast_horizon_hours < 20.0:
                # Add a meaningful penalty proportional to "blindness"
                horizon_penalty_factor = (20.0 - config.forecast_horizon_hours) / 20.0
                # Multiply by import amount. 0.05 $/kWh is a strong deterrent.
                uncertainty_penalty = 0.05 * horizon_penalty_factor * grid_import_kwh

        # Calculate self-consumption value (Issue #406)
        # Battery energy used to cover household load has value because it avoids
        # buying from grid at retail price
        self_consumption_value = 0.0
        if config.optimization_mode == "self_consumption":
            # Net load that battery covers = consumption - solar - grid_import
            # (If positive, battery discharges for load; if negative, battery is being charged)
            net_load = slot.consumption_kwh - slot.solar_kwh
            if net_load > 0:
                # Household needs energy beyond what solar provides
                # Battery covers some of this (the rest would be grid import)
                # Grid export takes energy away from load coverage
                battery_for_load = max(
                    0.0, net_load - grid_import_kwh - grid_export_kwh
                )

                # Cap by physical discharge capacity when SOC is known.
                # Without this cap, the formula can credit load coverage
                # that the battery cannot physically deliver — e.g. at SOC
                # floor or during EXPORT_PROACTIVE where all discharge goes
                # to grid, not to load.  (Fixes #417)
                if soc_pct is not None:
                    slot_hours = slot.slot_interval_minutes / 60.0
                    max_discharge_kwh = config.discharge_rate_kw * slot_hours
                    available_kwh = max(
                        0.0,
                        (soc_pct - config.min_soc_pct)
                        / 100.0
                        * config.battery_capacity_kwh,
                    )
                    max_load_kwh = min(
                        max_discharge_kwh,
                        available_kwh * config.discharge_efficiency,
                    )
                    battery_for_load = min(battery_for_load, max_load_kwh)

                self_consumption_value = (
                    battery_for_load * config.self_consumption_value_per_kwh
                )

        return ObjectiveTerms(
            import_cost=import_cost,
            export_revenue=export_revenue,
            cycle_penalty=cycle_penalty,
            self_consumption_value=self_consumption_value,
            uncertainty_penalty=uncertainty_penalty,
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


def _simulate_solar_only_terminal_soc(
    initial_soc_pct: float,
    slots: list[SlotContext],
    terminal_penalty_idx: int | None,
    config: OptimizerConfig,
) -> float:
    """Fast solar-only SOC simulation — no grid actions, no exports.

    Walks each slot accumulating net solar/consumption, clamps SOC to
    [config.min_soc_pct, 100]. Returns SOC at the terminal_penalty_idx
    slot, or at the last slot if terminal_penalty_idx is None.

    Used to populate OptimizerResult.can_solar_reach_target (Phase 4, #441).
    """
    soc = initial_soc_pct
    for i, slot in enumerate(slots):
        net_kwh = slot.solar_kwh - slot.consumption_kwh
        slot_hours = slot.slot_interval_minutes / 60.0
        max_slot_transfer_kwh = config.charge_rate_kw * slot_hours
        if net_kwh >= 0:
            delta = min(net_kwh, max_slot_transfer_kwh) * config.charge_efficiency
        else:
            delta = max(net_kwh, -max_slot_transfer_kwh) / config.discharge_efficiency
        soc += delta / config.battery_capacity_kwh * 100
        soc = max(config.min_soc_pct, min(100.0, soc))
        if terminal_penalty_idx is not None and i == terminal_penalty_idx:
            return soc
    return soc


def _simulate_max_soc_in_demand_window(
    initial_soc_pct: float,
    slots: list[SlotContext],
    config: OptimizerConfig,
) -> float:
    """Simulate solar-only SOC and return max SOC within demand window slots.

    Used when allow_dw_entry_under_target=True to determine if solar
    will reach target at any point during DW (Issue #505).

    Args:
        initial_soc_pct: Starting SOC percentage.
        slots: List of slot contexts to simulate.
        config: Optimizer configuration.

    Returns:
        Maximum SOC percentage reached within any demand window slot.
        Returns initial_soc_pct if no DW slots exist.
    """
    soc = initial_soc_pct
    max_soc_in_dw = soc

    for slot in slots:
        net_kwh = slot.solar_kwh - slot.consumption_kwh
        slot_hours = slot.slot_interval_minutes / 60.0
        max_transfer_kwh = config.charge_rate_kw * slot_hours

        if net_kwh >= 0:
            delta = min(net_kwh, max_transfer_kwh) * config.charge_efficiency
        else:
            delta = max(net_kwh, -max_transfer_kwh) / config.discharge_efficiency

        soc += delta / config.battery_capacity_kwh * 100
        soc = max(config.min_soc_pct, min(100.0, soc))

        if slot.is_demand_window_slot:
            max_soc_in_dw = max(max_soc_in_dw, soc)

    return max_soc_in_dw

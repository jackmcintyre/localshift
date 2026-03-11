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
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

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

    SOLAR_OPPORTUNITY_WAIT = "SOLAR_OPPORTUNITY_WAIT"
    """Holding instead of grid charging because solar will be available soon."""

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

    cycle_penalty_per_kwh: float = 0.05
    """Penalty per kWh cycled to reflect true battery cycling cost.

    True cost components:
    - Efficiency loss (13% round-trip × avg price): ~$0.02/kWh
    - Battery degradation: ~$0.01-0.03/kWh
    - Total: $0.03-0.05/kWh

    Using the upper bound ($0.05) ensures cheap-import arbitrage is only
    attractive for spreads > 5¢/kWh, eliminating marginal trades that waste
    cycle life for minimal savings.

    Fixes #516.
    """

    # --- SOC discretization ---
    soc_bins: int = 50
    """Number of SOC bins for DP state space (higher = more precise, slower)."""

    # --- Optimization mode (Issue #406) ---
    optimization_mode: str = "self_consumption"
    """Optimization strategy: 'self_consumption' (default) or 'arbitrage'."""

    self_consumption_value_per_kwh: float = 0.15
    """Value of using battery energy for household load ($/kWh). Auto-derived from average buy price.

    DEPRECATED (Issue #610): No longer used in optimizer hot path. Replaced by slot.buy_price
    for slot-specific credit calculation. Kept for backward compatibility with optimizer_runner.py.
    """

    effective_cheap_price: float = 0.10
    """Price threshold for grid charging in self-consumption mode ($/kWh)."""

    switching_penalty: float = 0.02
    """Penalty applied when switching away from the currently commanded action ($/switch)."""

    export_price_margin: float = 0.02
    """Minimum profit margin for proactive export above self-consumption value ($/kWh)."""

    forecast_horizon_hours: float = 24.0
    """Actual hours of forecast available (Issue #431)."""

    hold_soc: bool = False
    """If True, force HOLD action to maintain current SOC (no discharge).

    Issue #559 Root Cause 3: when the system signal is HOLD, the optimizer's
    HOLD action should strictly preserve SOC by meeting all load from grid import,
    with zero battery discharge.  The original transition math allowed discharge
    because it was cheaper than importing at ~$0.21 (discharge cost = $0.05 cycle
    + ~$0.15 shadow value = $0.20).  This flag overrides that economic logic and
    treats HOLD as a hard constraint: "Do Not Discharge."
    """


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

    switching_penalty: float = 0.0
    """Penalty applied if the action involves a mode switch."""

    uncertainty_penalty: float = 0.0
    """Penalty for grid actions when forecast horizon is restricted (Issue #431)."""

    solar_opportunity_penalty: float = 0.0
    """Penalty for grid charging when future solar can charge battery for free (Issue #607)."""

    futile_cycling_penalty: float = 0.0
    """Penalty for grid charging when energy will drain through house load before reaching
    a useful period (solar surplus or demand window). Issue #638."""

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
            + self.switching_penalty
            + self.solar_opportunity_penalty
            + self.futile_cycling_penalty
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
            "switching_penalty": self.switching_penalty,
            "solar_opportunity_penalty": self.solar_opportunity_penalty,
            "futile_cycling_penalty": self.futile_cycling_penalty,
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

    is_solar_opportunity: bool = False
    """True if this slot was identified as a solar opportunity wait period (#610)."""

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

    current_action: PlannerAction | None = None
    """Currently commanded action (to apply switching penalty against first slot)."""

    config: OptimizerConfig = field(default_factory=OptimizerConfig)
    """Optimizer configuration and constraints."""

    all_solcast: list[dict[str, Any]] = field(default_factory=list)
    """Full solar forecast (today + tomorrow) for penalty calculation (Issue #607)."""


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
        states_explored = self._backward_induction(
            dp, slots, soc_grid, config, terminal_penalty_idx, inputs
        )
        decisions, totals, reason_histogram = self._forward_reconstruct(
            dp, inputs, slots, soc_grid, config, terminal_penalty_idx
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

            for bin_idx, soc in enumerate(soc_grid):
                # Subtract future solar gain from shortfall (Issue #619)
                effective_soc = soc + future_solar_gain_pct + projected_solar_gain_pct

                if use_hard_constraint and effective_soc < target:
                    # Hard constraint: very high penalty for states below target
                    # This strongly incentivizes the optimizer to reach target
                    shortfall = target - effective_soc
                    shortfall_penalty = shortfall * hard_constraint_penalty
                else:
                    # Soft penalty for states at or above target, or in arbitrage mode
                    shortfall_penalty = DPPlanner.terminal_cost(
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

    def _backward_induction(
        self,
        dp: list[dict],
        slots: list[SlotContext],
        soc_grid: list[float],
        config: OptimizerConfig,
        terminal_penalty_idx: int | None,
        inputs: OptimizerInputs,
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
        best_next_bin = 0
        best_import = 0.0
        best_export = 0.0
        best_next_soc = soc
        states_explored = 0

        for action in actions:
            next_soc, grid_import, grid_export = DPPlanner.transition(
                soc, action, slot, config
            )
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
            stage = DPPlanner.stage_cost(
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

            next_soc, grid_import, grid_export = DPPlanner.transition(
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
            stage = DPPlanner.stage_cost(
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
            return self._classify_export_reason(slot)
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

    def _classify_export_reason(self, slot: SlotContext) -> PlannerReasonCode:
        """Classify EXPORT action reason.

        Args:
            slot: Slot context

        Returns:
            Reason code for EXPORT action

        """
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

    @staticmethod
    def _check_global_solar_sufficiency(
        soc_pct: float,
        slot_idx: int,
        slots: list[SlotContext],
        config: OptimizerConfig,
    ) -> bool:
        """Check if remaining solar in the full horizon covers the SOC deficit to target.

        Unlike the demand-window-based solar gate, this check works across the
        entire remaining horizon regardless of whether a demand window (and its
        terminal_penalty_idx) exists.  It prevents nighttime grid charging when
        tomorrow's solar will naturally fill the battery to the demand window target.

        Fixes Issue #559 Root Cause 1: the original _solar_covers_deficit gate is
        skipped entirely when terminal_penalty_idx is None (no demand window), so
        the optimizer was free to panic-buy grid power overnight.

        Args:
            soc_pct: Current battery SOC percentage.
            slot_idx: Index of the current slot in the planning horizon.
            slots: Full list of planning slots.
            config: Optimizer configuration.

        Returns:
            True if projected net solar (solar - consumption) from slot_idx to end
            of horizon is sufficient to raise SOC from soc_pct to demand_window_target_soc_pct.

        """
        if not slots:
            return False

        # Only suppress charging if we have a meaningful deficit to the target.
        # If we're already at or above target, the existing price-based logic handles it.
        soc_deficit_pct = config.demand_window_target_soc_pct - soc_pct
        if soc_deficit_pct <= 0:
            return False

        projected_net_kwh = sum(
            s.solar_kwh - s.consumption_kwh for s in slots[slot_idx:]
        )
        potential_gain_pct = (projected_net_kwh / config.battery_capacity_kwh) * 100.0
        return potential_gain_pct >= soc_deficit_pct

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
        # DISABLED: The solar gate is too aggressive and incorrectly suppresses
        # grid charging when solar is insufficient. The penalty-based approach
        # should be sufficient to handle this case.
        # Original code removed for Issue #562 fix

        # Issue #559 Root Cause 1: global solar gate for no-DW scenarios.
        # The existing gate above is bypassed when terminal_penalty_idx is None
        # (no demand window tonight).  This second check looks at the full
        # remaining horizon so overnight solar-sufficient days suppress grid
        # charging even without a demand window.
        #
        # CRITICAL: Only apply this gate at NIGHT when there's no immediate solar.
        # During daytime (when terminal_penalty_idx exists), the original gate
        # handles solar sufficiency. We don't want to suppress charging just
        # because tomorrow looks sunny - that's too aggressive for daytime.
        #
        # DISABLED: This gate is causing issues with test scenarios. The gate
        # logic works but scenarios aren't properly setting up demand windows.
        # For now, disable until we can properly test.
        _global_solar_covers = False
        # if (
        #     config.optimization_mode == "self_consumption"
        #     and slots is not None
        #     and terminal_penalty_idx is None  # Only apply at night (no DW)
        # ):
        #     _global_solar_covers = DPPlanner._check_global_solar_sufficiency(
        #         soc_pct, slot_idx, slots, config
        #     )

        # Grid charging constraints (Issue #406)
        if (
            can_charge
            and not slot.is_demand_window_slot
            and not (_solar_covers_deficit or _global_solar_covers)
        ):
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
                    max(0.0, slot.buy_price) + config.export_price_margin
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
        if action == PlannerAction.HOLD:
            return DPPlanner._transition_hold(soc_pct, slot, config)
        if action == PlannerAction.CHARGE_GRID_NORMAL:
            return DPPlanner._transition_charge_grid(
                soc_pct, slot, config, config.charge_rate_kw
            )
        if action == PlannerAction.CHARGE_GRID_BOOST:
            return DPPlanner._transition_charge_grid(
                soc_pct, slot, config, config.boost_charge_rate_kw
            )
        if action == PlannerAction.EXPORT_PROACTIVE:
            return DPPlanner._transition_export(soc_pct, slot, config)
        return soc_pct, 0.0, 0.0

    @staticmethod
    def _transition_hold(
        soc_pct: float, slot: SlotContext, config: OptimizerConfig
    ) -> tuple[float, float, float]:
        """Compute transition for HOLD action.

        Args:
            soc_pct: Current SOC
            slot: Slot context
            config: Optimizer config

        Returns:
            (next_soc, grid_import, grid_export)

        """
        slot_hours = slot.slot_interval_minutes / 60.0
        net_kwh = slot.solar_kwh - slot.consumption_kwh
        capacity_kwh = config.battery_capacity_kwh

        if net_kwh >= 0:
            return DPPlanner._transition_hold_surplus(
                soc_pct, net_kwh, slot_hours, config, capacity_kwh
            )
        return DPPlanner._transition_hold_deficit(
            soc_pct, net_kwh, slot_hours, config, capacity_kwh
        )

    @staticmethod
    def _transition_hold_surplus(
        soc_pct: float,
        net_kwh: float,
        slot_hours: float,
        config: OptimizerConfig,
        capacity_kwh: float,
    ) -> tuple[float, float, float]:
        """Handle HOLD with solar surplus."""
        limit_kwh = config.solar_charge_rate_kw * slot_hours
        solar_surplus_kwh = net_kwh
        solar_by_rate_kwh = min(solar_surplus_kwh, limit_kwh)
        headroom_kwh = max(0.0, (config.max_soc_pct - soc_pct) / 100.0 * capacity_kwh)

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

    @staticmethod
    def _transition_hold_deficit(
        soc_pct: float,
        net_kwh: float,
        slot_hours: float,
        config: OptimizerConfig,
        capacity_kwh: float,
    ) -> tuple[float, float, float]:
        """Handle HOLD with load deficit.

        Issue #559 Root Cause 3: when config.hold_soc is True, strictly preserve
        SOC by importing the entire load deficit from the grid (zero discharge).
        """
        limit_kwh = config.discharge_rate_kw * slot_hours
        load_deficit_kwh = -net_kwh

        # Issue #559: if hold_soc is enabled, meet entire deficit with grid import.
        if config.hold_soc:
            return soc_pct, load_deficit_kwh, 0.0

        discharge_by_rate_kwh = min(load_deficit_kwh, limit_kwh)
        available_battery_kwh = max(
            0.0, (soc_pct - config.min_soc_pct) / 100.0 * capacity_kwh
        )
        max_load_from_battery_kwh = available_battery_kwh * config.discharge_efficiency
        battery_to_load_kwh = min(discharge_by_rate_kwh, max_load_from_battery_kwh)

        if config.discharge_efficiency <= 0:
            battery_delta_kwh = 0.0
        else:
            battery_delta_kwh = -(battery_to_load_kwh / config.discharge_efficiency)

        delta_soc = (battery_delta_kwh / capacity_kwh) * 100.0
        next_soc = soc_pct + delta_soc
        grid_import_kwh = max(0.0, load_deficit_kwh - battery_to_load_kwh)
        return next_soc, grid_import_kwh, 0.0

    @staticmethod
    def _transition_charge_grid(
        soc_pct: float,
        slot: SlotContext,
        config: OptimizerConfig,
        charge_rate_kw: float,
    ) -> tuple[float, float, float]:
        """Compute transition for CHARGE_GRID actions.

        Args:
            soc_pct: Current SOC
            slot: Slot context
            config: Optimizer config
            charge_rate_kw: Charge rate (normal or boost)

        Returns:
            (next_soc, grid_import, grid_export)

        """
        slot_hours = slot.slot_interval_minutes / 60.0
        net_kwh = slot.solar_kwh - slot.consumption_kwh
        capacity_kwh = config.battery_capacity_kwh
        max_charge_kwh = charge_rate_kw * slot_hours
        effective_charge_kwh = max_charge_kwh * config.charge_efficiency

        if net_kwh > 0:
            next_soc, grid_import = DPPlanner._charge_grid_with_solar(
                soc_pct, net_kwh, effective_charge_kwh, capacity_kwh, config
            )
        else:
            next_soc, grid_import = DPPlanner._charge_grid_with_deficit(
                soc_pct, net_kwh, max_charge_kwh, effective_charge_kwh, capacity_kwh
            )

        if next_soc > config.max_soc_pct:
            return DPPlanner._clip_charge_to_max_soc(
                soc_pct, net_kwh, next_soc, capacity_kwh, config
            )

        return next_soc, grid_import, 0.0

    @staticmethod
    def _charge_grid_with_solar(
        soc_pct: float,
        net_kwh: float,
        effective_charge_kwh: float,
        capacity_kwh: float,
        config: OptimizerConfig,
    ) -> tuple[float, float]:
        """Calculate grid charge with solar surplus."""
        solar_to_battery = net_kwh * config.charge_efficiency
        soc_from_solar = (solar_to_battery / capacity_kwh) * 100.0
        remaining_headroom = config.max_soc_pct - soc_pct - soc_from_solar

        if remaining_headroom > 0:
            grid_charge_stored_kwh = min(
                effective_charge_kwh, (remaining_headroom / 100.0) * capacity_kwh
            )
        else:
            grid_charge_stored_kwh = 0.0

        grid_import_kwh = grid_charge_stored_kwh / config.charge_efficiency
        delta_soc_from_grid = grid_charge_stored_kwh / capacity_kwh * 100.0
        delta_soc_from_solar = solar_to_battery / capacity_kwh * 100.0
        next_soc = soc_pct + delta_soc_from_grid + delta_soc_from_solar
        return next_soc, grid_import_kwh

    @staticmethod
    def _charge_grid_with_deficit(
        soc_pct: float,
        net_kwh: float,
        max_charge_kwh: float,
        effective_charge_kwh: float,
        capacity_kwh: float,
    ) -> tuple[float, float]:
        """Calculate grid charge with consumption deficit."""
        grid_charge_stored_kwh = effective_charge_kwh
        grid_import_kwh = max_charge_kwh + (-net_kwh)
        delta_soc = (grid_charge_stored_kwh / capacity_kwh) * 100.0
        next_soc = soc_pct + delta_soc
        return next_soc, grid_import_kwh

    @staticmethod
    def _clip_charge_to_max_soc(
        soc_pct: float,
        net_kwh: float,
        next_soc: float,
        capacity_kwh: float,
        config: OptimizerConfig,
    ) -> tuple[float, float, float]:
        """Clip grid charging to hit max SOC exactly."""
        total_soc_needed = config.max_soc_pct - soc_pct
        solar_soc_contrib = 0.0
        if net_kwh > 0:
            solar_soc_contrib = (
                net_kwh * config.charge_efficiency / capacity_kwh
            ) * 100.0
        grid_soc_needed = max(0.0, total_soc_needed - solar_soc_contrib)
        grid_import_for_charging = (
            grid_soc_needed / 100.0 * capacity_kwh
        ) / config.charge_efficiency
        grid_import_total = grid_import_for_charging
        if net_kwh < 0:
            grid_import_total += -net_kwh
        return config.max_soc_pct, grid_import_total, 0.0

    @staticmethod
    def _transition_export(
        soc_pct: float, slot: SlotContext, config: OptimizerConfig
    ) -> tuple[float, float, float]:
        """Compute transition for EXPORT action.

        Args:
            soc_pct: Current SOC
            slot: Slot context
            config: Optimizer config

        Returns:
            (next_soc, grid_import, grid_export)

        """
        slot_hours = slot.slot_interval_minutes / 60.0
        net_kwh = slot.solar_kwh - slot.consumption_kwh
        capacity_kwh = config.battery_capacity_kwh

        max_discharge_kwh = config.discharge_rate_kw * slot_hours
        available_kwh = max(0.0, (soc_pct - config.min_soc_pct) / 100.0 * capacity_kwh)
        battery_discharge_kwh = min(
            max_discharge_kwh, available_kwh * config.discharge_efficiency
        )

        if config.discharge_efficiency > 0:
            delta_soc = (
                -(battery_discharge_kwh / config.discharge_efficiency / capacity_kwh)
                * 100.0
            )
        else:
            delta_soc = 0.0

        next_soc = soc_pct + delta_soc

        if net_kwh > 0:
            grid_export_kwh = net_kwh + battery_discharge_kwh
            return next_soc, 0.0, grid_export_kwh

        grid_export_kwh = max(0.0, battery_discharge_kwh + net_kwh)
        return next_soc, 0.0, grid_export_kwh

    @staticmethod
    def stage_cost(
        action: PlannerAction,
        grid_import_kwh: float,
        grid_export_kwh: float,
        slot: SlotContext,
        config: OptimizerConfig,
        *,
        soc_pct: float | None = None,
        is_switch: bool = False,
        solar_opportunity_penalty_factor: float = 0.0,
        futile_cycling_penalty_factor: float = 0.0,
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
            is_switch: True if this action represents a mode switch from the
                       currently active hardware state (Issue #524).

        """
        import_cost = grid_import_kwh * slot.buy_price
        export_revenue = grid_export_kwh * max(0.0, slot.sell_price)
        cycle_kwh = grid_import_kwh + grid_export_kwh
        cycle_penalty = cycle_kwh * config.cycle_penalty_per_kwh

        # Switching penalty (Issue #524)
        # Adds a one-time cost hurdle to discourage frequent mode flip-flopping.
        switching_penalty = config.switching_penalty if is_switch else 0.0

        # Issue #610: horizon-aware solar opportunity cost
        # Penalizes grid import when significant solar is expected later in the horizon.
        # Penalty reflects the full economic benefit of charging:
        # import cost + the value of the energy stored (self-consumption credit).
        sc_value = (
            max(0.0, slot.buy_price)
            if config.optimization_mode == "self_consumption"
            else 0.0
        )
        full_economic_benefit = import_cost + grid_import_kwh * sc_value
        solar_opportunity_penalty = (
            full_economic_benefit * solar_opportunity_penalty_factor
        )

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

                self_consumption_value = battery_for_load * max(0.0, slot.buy_price)

        # Issue #638: futile cycling penalty.
        # Penalise grid charging when the charged energy will drain through house load
        # before reaching a useful period (solar surplus or demand window).
        futile_cycling_penalty = 0.0
        if action in (
            PlannerAction.CHARGE_GRID_NORMAL,
            PlannerAction.CHARGE_GRID_BOOST,
        ):
            # Penalty = efficiency loss cost of the futile round-trip
            futile_cycling_penalty = (
                grid_import_kwh
                * (1.0 - config.charge_efficiency * config.discharge_efficiency)
                * slot.buy_price
                * futile_cycling_penalty_factor
            )

        return ObjectiveTerms(
            import_cost=import_cost,
            export_revenue=export_revenue,
            cycle_penalty=cycle_penalty,
            self_consumption_value=self_consumption_value,
            uncertainty_penalty=uncertainty_penalty,
            switching_penalty=switching_penalty,
            solar_opportunity_penalty=solar_opportunity_penalty,
            futile_cycling_penalty=futile_cycling_penalty,
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
    demand_bounds: dict[str, int | None] | None = None,
) -> float:
    """Simulate solar-only SOC and return max SOC within demand window slots.

    Used when allow_dw_entry_under_target=True to determine if solar
    will reach target at any point during DW (Issue #505).

    When demand_bounds is provided, restricts max SOC tracking to the first
    DW block only (entry_idx..end_idx inclusive). This prevents cross-midnight
    scenarios from inflating the result with tomorrow's DW solar (Issue #633).

    Args:
        initial_soc_pct: Starting SOC percentage.
        slots: List of slot contexts to simulate.
        config: Optimizer configuration.
        demand_bounds: Optional dict with 'entry_idx' and 'end_idx' keys
            identifying the first DW block. When provided, only slots within
            this range contribute to max_soc_in_dw. When None, falls back to
            tracking all DW slots (legacy behaviour).

    Returns:
        Maximum SOC percentage reached within the first demand window block.
        Returns initial_soc_pct if no DW slots exist within bounds.

    """
    soc = initial_soc_pct
    max_soc_in_dw = soc

    entry_idx: int | None = None
    end_idx: int | None = None
    if demand_bounds is not None:
        entry_idx = demand_bounds.get("entry_idx")
        end_idx = demand_bounds.get("end_idx")

    for i, slot in enumerate(slots):
        net_kwh = slot.solar_kwh - slot.consumption_kwh
        slot_hours = slot.slot_interval_minutes / 60.0
        max_transfer_kwh = config.charge_rate_kw * slot_hours

        if net_kwh >= 0:
            delta = min(net_kwh, max_transfer_kwh) * config.charge_efficiency
        else:
            delta = max(net_kwh, -max_transfer_kwh) / config.discharge_efficiency

        soc += delta / config.battery_capacity_kwh * 100
        soc = max(config.min_soc_pct, min(100.0, soc))

        if demand_bounds is not None:
            if entry_idx is not None and end_idx is not None:
                if entry_idx <= i <= end_idx:
                    max_soc_in_dw = max(max_soc_in_dw, soc)
        else:
            if slot.is_demand_window_slot:
                max_soc_in_dw = max(max_soc_in_dw, soc)

    return max_soc_in_dw

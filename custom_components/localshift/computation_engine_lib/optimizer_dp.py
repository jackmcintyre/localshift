"""
optimizer_dp.py — DP-based battery optimizer (shadow-mode scaffold).

Phase: MVP scaffolding (Phase 1 of #403 / target architecture #401).
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


class DPPlanner:
    """
    Deterministic dynamic-programming battery optimizer.

    State space: (slot_index, soc_bin)
    Actions: PlannerAction enum
    Objective: minimize total net cost including shortfall penalty

    This is an MVP scaffold. The solve() method returns a stub result
    until the DP algorithm is fully implemented.
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

        In the MVP scaffold, this returns a HOLD decision for every slot
        as a safe no-op placeholder until the full algorithm is implemented.
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
    # Internal solve (MVP stub — replace with full DP)
    # ------------------------------------------------------------------

    def _solve(self, inputs: OptimizerInputs) -> OptimizerResult:
        """
        MVP STUB: returns HOLD for every slot.

        TODO (#403 Phase C): Replace with full DP over (slot_index, soc_bin).
        Algorithm outline:
          1. Build soc_bins from min_soc_pct to max_soc_pct in config.soc_bins steps.
          2. For each slot (forward pass): evaluate feasible_actions() per state.
          3. For each (state, action): compute transition() and stage_cost().
          4. Store optimal cost-to-go table.
          5. Backward pass: reconstruct optimal action sequence.
          6. Map action sequence to PlannedSlotDecision list.
        """
        decisions: list[PlannedSlotDecision] = []
        soc_pct = inputs.initial_soc_pct

        for slot in inputs.slots:
            decision = PlannedSlotDecision(
                slot_index=slot.slot_index,
                timestamp_iso=slot.timestamp_iso,
                slot_interval_minutes=slot.slot_interval_minutes,
                action=PlannerAction.HOLD,
                reason_code=PlannerReasonCode.IDLE,
                objective_terms=ObjectiveTerms(),
                predicted_soc_pct=soc_pct,
                grid_import_kwh=0.0,
                grid_export_kwh=0.0,
            )
            decisions.append(decision)

        return OptimizerResult(
            success=True,
            planner_version=self.VERSION,
            total_slots=len(inputs.slots),
            states_explored=0,  # stub
            decisions=decisions,
            projected_import_kwh=0.0,
            projected_export_kwh=0.0,
            projected_net_cost=0.0,
            reason_code_histogram={"IDLE": len(decisions)},
        )

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

        Returns:
            (next_soc_pct, grid_import_kwh, grid_export_kwh)

        TODO (#403 Phase C): Implement fully with efficiency losses,
        partial slot charge clipping at SOC boundaries.
        """
        slot_hours = slot.slot_interval_minutes / 60.0
        net_kwh = slot.solar_kwh - slot.consumption_kwh  # positive = surplus

        if action == PlannerAction.HOLD:
            # Battery absorbs/supplies net deficit/surplus up to rate limits
            # Simplified: net effect on battery from solar surplus
            delta_soc = (net_kwh / config.battery_capacity_kwh) * 100.0
            next_soc = max(config.min_soc_pct, min(config.max_soc_pct, soc_pct + delta_soc))
            return next_soc, 0.0, 0.0

        if action == PlannerAction.CHARGE_GRID_NORMAL:
            charge_kwh = config.charge_rate_kw * slot_hours * config.charge_efficiency
            delta_soc = (charge_kwh / config.battery_capacity_kwh) * 100.0
            next_soc = min(config.max_soc_pct, soc_pct + delta_soc)
            return next_soc, charge_kwh, 0.0

        if action == PlannerAction.CHARGE_GRID_BOOST:
            charge_kwh = config.boost_charge_rate_kw * slot_hours * config.charge_efficiency
            delta_soc = (charge_kwh / config.battery_capacity_kwh) * 100.0
            next_soc = min(config.max_soc_pct, soc_pct + delta_soc)
            return next_soc, charge_kwh, 0.0

        if action == PlannerAction.EXPORT_PROACTIVE:
            discharge_kwh = config.discharge_rate_kw * slot_hours / config.discharge_efficiency
            delta_soc = -(discharge_kwh / config.battery_capacity_kwh) * 100.0
            next_soc = max(config.min_soc_pct, soc_pct + delta_soc)
            return next_soc, 0.0, discharge_kwh

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

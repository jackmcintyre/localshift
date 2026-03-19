"""engine/types.py — Optimizer type definitions.

Extracted from optimizer_dp.py (issue #641 refactor).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from custom_components.localshift.forecast.solar_accuracy import (
        SolarAccuracyTracker,
    )

# -----------------------------------------------------------------------------
# Action vocabulary
# -----------------------------------------------------------------------------


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


# -----------------------------------------------------------------------------
# Reason codes
# -----------------------------------------------------------------------------


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


# -----------------------------------------------------------------------------
# Per-slot context (normalized inputs)
# -----------------------------------------------------------------------------


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


# -----------------------------------------------------------------------------
# Optimizer configuration
# -----------------------------------------------------------------------------


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

    cycle_penalty_per_kwh: float = 0.08
    """Penalty per kWh cycled to reflect true battery cycling cost.

    True cost components:
    - Efficiency loss (13% round-trip × avg price): ~$0.02/kWh
    - Battery degradation: ~$0.03-0.05/kWh
    - Total: $0.05-0.07/kWh

    Using upper-mid range ($0.08) ensures cheap-import arbitrage is only
    attractive for spreads > 8¢/kWh, eliminating marginal trades that waste
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


# -----------------------------------------------------------------------------
# Per-slot decision output
# -----------------------------------------------------------------------------


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


# -----------------------------------------------------------------------------
# Full optimizer result
# -----------------------------------------------------------------------------


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


# -----------------------------------------------------------------------------
# Negative FIT avoidance context
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class NegativeFitAvoidanceContext:
    """Immutable context for recoverability-based negative-FIT avoidance.

    The planner may proactively discharge at positive FIT before a bad-price
    spill window when conservative future solar can still recover the battery
    to target by the relevant deadline.
    """

    risk_window_start_idx: int
    """Index of the first slot in the spill-risk window (first sell_price <= 0)."""

    risk_window_end_idx: int
    """Index of the last slot in the spill-risk window (inclusive)."""

    required_headroom_kwh: float
    """Estimated storage space (kWh) needed to absorb spill during risk window."""

    recovery_deadline_idx: int | None
    """Slot index by which target must be recoverable (demand window or horizon end)."""

    conservative_recovery_kwh_by_slot: tuple[float, ...]
    """Conservative recoverable solar (kWh) from each slot to recovery deadline."""

    recoverability_floor_pct_by_slot: tuple[float, ...]
    """Precomputed recoverability floor (%) for each slot based on future recovery potential."""


# -----------------------------------------------------------------------------
# Optimizer inputs
# -----------------------------------------------------------------------------


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

    solar_accuracy_tracker: SolarAccuracyTracker | None = None
    """Tracker for forecast accuracy to apply discount to terminal cost (Issue #785)."""

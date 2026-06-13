"""Coordinator data structures for the LocalShift integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from ..const import BatteryMode

if TYPE_CHECKING:
    from ..forecast.solcast_analysis import SolcastAnalysis
    from ..pricing.types import ForecastSlot


@dataclass
class PerformanceMetrics:
    """Aggregated performance metrics for the learning system.

    Issue #170 Phase 1: Tracks decision outcomes and efficiency metrics.
    Issue #683: Added counterfactual TOU baseline metrics for optimizer value measurement.
    """

    # Daily metrics
    total_decisions_today: int = 0
    avg_decision_score_today: float = 0.0
    grid_charge_efficiency: float = 0.0
    export_loss_ratio: float = 0.0
    unnecessary_grid_charge_kwh: float = 0.0

    # Rolling metrics (7-day)
    avg_decision_score_7d: float = 0.0
    avg_daily_cost_7d: float = 0.0
    cost_trend: str = "stable"

    # Per-mode metrics
    mode_durations_today: dict[str, float] = field(default_factory=dict)
    mode_cost_attribution: dict[str, float] = field(default_factory=dict)

    # Counterfactual metrics (Issue #683)
    counterfactual_tou_cost: float = 0.0
    counterfactual_actual_cost: float = 0.0
    optimizer_advantage_daily: float = 0.0
    optimizer_advantage_7d: float = 0.0
    optimizer_advantage_daily_avg: float = 0.0
    optimizer_advantage_percent: float = 0.0
    counterfactual_degrading: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "total_decisions_today": self.total_decisions_today,
            "avg_decision_score_today": self.avg_decision_score_today,
            "grid_charge_efficiency": self.grid_charge_efficiency,
            "export_loss_ratio": self.export_loss_ratio,
            "unnecessary_grid_charge_kwh": self.unnecessary_grid_charge_kwh,
            "avg_decision_score_7d": self.avg_decision_score_7d,
            "avg_daily_cost_7d": self.avg_daily_cost_7d,
            "cost_trend": self.cost_trend,
            "mode_durations_today": self.mode_durations_today,
            "mode_cost_attribution": self.mode_cost_attribution,
            "counterfactual_tou_cost": self.counterfactual_tou_cost,
            "counterfactual_actual_cost": self.counterfactual_actual_cost,
            "optimizer_advantage_daily": self.optimizer_advantage_daily,
            "optimizer_advantage_7d": self.optimizer_advantage_7d,
            "optimizer_advantage_daily_avg": self.optimizer_advantage_daily_avg,
            "optimizer_advantage_percent": self.optimizer_advantage_percent,
            "counterfactual_degrading": self.counterfactual_degrading,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerformanceMetrics:
        """Create from dictionary (deserialization)."""
        return cls(
            total_decisions_today=data.get("total_decisions_today", 0),
            avg_decision_score_today=data.get("avg_decision_score_today", 0.0),
            grid_charge_efficiency=data.get("grid_charge_efficiency", 0.0),
            export_loss_ratio=data.get("export_loss_ratio", 0.0),
            unnecessary_grid_charge_kwh=data.get("unnecessary_grid_charge_kwh", 0.0),
            avg_decision_score_7d=data.get("avg_decision_score_7d", 0.0),
            avg_daily_cost_7d=data.get("avg_daily_cost_7d", 0.0),
            cost_trend=data.get("cost_trend", "stable"),
            mode_durations_today=data.get("mode_durations_today", {}),
            mode_cost_attribution=data.get("mode_cost_attribution", {}),
            counterfactual_tou_cost=data.get("counterfactual_tou_cost", 0.0),
            counterfactual_actual_cost=data.get("counterfactual_actual_cost", 0.0),
            optimizer_advantage_daily=data.get("optimizer_advantage_daily", 0.0),
            optimizer_advantage_7d=data.get("optimizer_advantage_7d", 0.0),
            optimizer_advantage_daily_avg=data.get(
                "optimizer_advantage_daily_avg", 0.0
            ),
            optimizer_advantage_percent=data.get("optimizer_advantage_percent", 0.0),
            counterfactual_degrading=data.get("counterfactual_degrading", False),
        )


@dataclass
class AdaptiveParameters:
    """Current state of learning system parameters.

    Issue #170 Phase 2: Holds optimized parameter values from the learning system.
    """

    values: dict[str, float] = field(
        default_factory=dict
    )  # param_name -> current_value
    confidence: dict[str, float] = field(default_factory=dict)  # param_name -> 0.0-1.0
    last_updated: datetime | None = None
    update_count: int = 0

    def get(self, param_name: str, default: float = 0.0) -> float:
        """Get current parameter value, falling back to default."""
        return self.values.get(param_name, default)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "values": self.values,
            "confidence": self.confidence,
            "last_updated": self.last_updated.isoformat()
            if self.last_updated
            else None,
            "update_count": self.update_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdaptiveParameters:
        """Create from dictionary (deserialization)."""
        last_updated = None
        if data.get("last_updated"):
            try:
                last_updated = datetime.fromisoformat(data["last_updated"])
            except (ValueError, TypeError):
                pass
        return cls(
            values=data.get("values", {}),
            confidence=data.get("confidence", {}),
            last_updated=last_updated,
            update_count=data.get("update_count", 0),
        )


@dataclass
class ChargingDecision:
    """Represents a charging decision for a specific time slot.

    This is the shared decision structure used by both the forecast
    simulation and the active mode logic to ensure consistency.
    """

    slot_start: datetime
    should_grid_charge: bool = False
    should_boost: bool = False
    charge_amount_kwh: float = 0.0
    price_per_kwh: float = 0.0
    reason: str = ""


@dataclass
class CoordinatorData:
    """Snapshot of all computed data, consumed by sensor entities."""

    # External state (raw reads)
    grid_power_kw: float = 0.0
    battery_power_kw: float = 0.0
    solar_power_kw: float = 0.0
    load_power_kw: float = 0.0
    soc: float = 0.0
    operation_mode: str = ""
    backup_reserve: float = 0.0
    general_price: float = 0.0
    feed_in_price: float = 0.0
    price_spike: bool = False

    # Shadow prices for A/B comparison (Issue #300)
    general_price_shadow: float = 0.0
    feed_in_price_shadow: float = 0.0
    general_forecast_shadow: list[ForecastSlot] = field(default_factory=list)
    feed_in_forecast_shadow: list[ForecastSlot] = field(default_factory=list)

    # Decision comparison results
    primary_decision: str = ""
    shadow_decision: str = ""
    comparison_match: bool = True
    price_delta: float = 0.0  # Difference between sources

    # Demand window from Amber Express (Issue #300)
    demand_window_amber: bool = False

    prices_available: bool = (
        True  # False when price entities are unavailable (Issue #330)
    )
    general_forecast: list[ForecastSlot] = field(default_factory=list)
    feed_in_forecast: list[ForecastSlot] = field(default_factory=list)
    solcast_today: list[dict[str, Any]] = field(default_factory=list)
    solcast_tomorrow: list[dict[str, Any]] = field(default_factory=list)
    allow_export: str = "unknown"

    # Solcast v4.5.1 analysis attribute data (Issue #778)
    solcast_analysis_today: SolcastAnalysis | None = None
    solcast_analysis_tomorrow: SolcastAnalysis | None = None
    solcast_mape: float | None = None  # From sensor.solcast_pv_forecast_accuracy
    avg_confidence_today: float = 1.0
    avg_confidence_tomorrow: float = 1.0
    low_confidence_periods: list[tuple[datetime, float]] = field(default_factory=list)

    # Computed binary sensors
    forecast_spike_within_window: bool = False
    force_discharge_active: bool = False
    force_charge_active: bool = False
    boost_charge_active: bool = False
    proactive_export_active: bool = False
    forecast_expensive_period_coming: bool = False
    solar_can_reach_target: bool = False
    solar_can_reach_target_in_dw: bool = False
    boost_charge_needed: bool = False
    demand_window_active: bool = False

    # Battery preservation (Issue #350)
    preserve_soc: float | None = None  # SOC to preserve when charging needed

    # Extra attributes for binary sensors
    max_forecast_price: float = 0.0
    max_buy_forecast_price: float = 0.0  # Max buy price (general_forecast) for display
    surplus_ratio: float = 0.0

    # Computed sensors
    effective_cheap_price: float = 0.0
    # Un-inflated percentile cheap threshold (Issue #800). None = not yet computed.
    # May legitimately be <= 0 in negative-wholesale markets, so a None sentinel (not 0.0)
    # is used to distinguish "absent" from "genuinely cheap/negative".
    base_cheap_price: float | None = None
    cheap_charge_stop_price: float = 0.0
    planner_threshold_used: float | None = None  # Threshold the optimizer actually used
    solar_weighted_avg_fit: float = 0.0
    solar_remaining_kwh: float = 0.0
    active_mode: BatteryMode = BatteryMode.SELF_CONSUMPTION
    solar_battery_forecast: dict[str, Any] = field(default_factory=dict)
    decision_log: list[dict[str, Any]] = field(default_factory=list)
    forecast_history: list[dict[str, Any]] = field(default_factory=list)
    # Legacy forecast fields — retained for compatibility, always empty after #441 migration.
    daily_forecast: list[dict[str, Any]] = field(default_factory=list)
    daily_forecast_hourly: list[dict[str, Any]] = field(default_factory=list)
    daily_forecast_soc_15min: list[list[Any]] = field(default_factory=list)
    consumption_source: str = "unknown"
    consumption_profile_hours: int = 0
    consumption_fallback_hours: int = 0
    consumption_statistic_id: str = ""
    consumption_hourly_sample_counts: dict[int, int] = field(default_factory=dict)
    consumption_hourly_profile_kw: dict[int, float] = field(default_factory=dict)
    # Day-of-week aware consumption profiles (issue-60)
    consumption_profile_type: str = (
        "combined"  # "weekday_weekend" or "combined" (system capability)
    )
    forecast_profile_selected: str = (
        "unknown"  # "weekday", "weekend", or "combined" for current forecast
    )
    weekday_sample_counts: dict[int, int] = field(default_factory=dict)
    weekend_sample_counts: dict[int, int] = field(default_factory=dict)
    weekday_hourly_profile_kw: dict[int, float] = field(default_factory=dict)
    weekend_hourly_profile_kw: dict[int, float] = field(default_factory=dict)
    forecast_consumption_source_counts: dict[str, int] = field(default_factory=dict)
    recent_load_1hr_kw: float = 0.0
    recent_load_short_kw: float = 0.0
    recent_load_1hr_statistic_id: str = ""
    recent_load_1hr_samples: int = 0
    recent_load_1hr_last_error: str = ""

    # Shared load forecast slots for DP optimizer
    # 96 entries, one per 15-min slot starting from the current 5-min boundary.
    load_forecast_slots: list[float] = field(default_factory=list)

    # Cost accumators
    grid_import_cost: float = 0.0
    grid_export_revenue: float = 0.0
    battery_savings: float = 0.0
    battery_charge_cost: float = 0.0

    # Daily energy accumulators (Issue #868) — real per-minute kWh accounting used
    # to compute grid_charge_efficiency / export_loss_ratio / unnecessary_grid_charge_kwh.
    # In-memory only (no persistence), matching the daily cost accumulators above.
    grid_import_kwh_today: float = 0.0  # Total grid energy imported today
    grid_export_kwh_today: float = 0.0  # Total grid energy exported today
    grid_to_battery_kwh_today: float = 0.0  # Grid energy charged into the battery
    soc_gain_during_grid_charge_kwh_today: float = (
        0.0  # Battery energy gained while charging from grid
    )
    export_while_battery_not_full_kwh_today: float = (
        0.0  # Exported energy that could have charged a non-full battery
    )

    # Forecast cost accumulators (rest of today)
    forecast_import_cost: float = 0.0  # Expected grid import cost
    forecast_export_revenue: float = 0.0  # Expected grid export revenue
    forecast_net_cost: float = 0.0  # Expected net cost (import - export)
    forecast_grid_charge_cost: float = 0.0  # Expected cost for grid charging
    forecast_proactive_export_revenue: float = 0.0  # Revenue from proactive exports

    # Internal state flags (managed by state machine / buttons)
    manual_override: bool = False
    target_reached_today: bool = False
    # Local date the target latch was last reset. Drives a date-change reset in the
    # compute cycle that is immune to a missed midnight event (the latch's only other
    # live reset), preventing a stuck latch from silently disabling pre-charge.
    last_target_reset_date: date | None = None
    allow_dw_entry_under_target: bool = (
        False  # Allow DW entry when solar can reach target
    )
    solar_absent_confidence: float = 1.0
    """Confidence to use when Solcast analysis is absent; 0.3 when stale_solar_conservative=True."""

    # Shared charging decisions (computed once, used by both forecast and active_mode)
    forecast_charging_decisions: list[ChargingDecision] = field(default_factory=list)
    charging_needed_before_dw: float = 0.0  # Total kWh needed before DW
    optimal_charge_start: datetime | None = None  # Earliest optimal charging slot
    optimal_charge_end: datetime | None = None  # Latest optimal charging slot

    # Debug/diagnostic fields for dashboard troubleshooting
    forecast_ready: bool = (
        False  # True when Solcast data is available and valid (Issue #319)
    )
    forecast_status: str = (
        "initializing"  # "ready", "partial", "stale", "initializing" (Issue #319)
    )
    debug_forecast_slot_found: bool = (
        False  # True if current time slot found in forecast
    )
    debug_forecast_slot_time: str = ""  # Time of matched forecast slot (HH:MM)
    debug_first_forecast_slot_time: str = ""  # Time of first forecast slot (HH:MM)
    debug_time_gap_seconds: float = 0.0  # Seconds between now and first forecast slot
    debug_mode_source: str = (
        "unknown"  # "manual_override" | "optimizer" | "fallback" | "unknown"
    )
    debug_dry_run: bool = False  # Dry run mode active
    debug_commanded_mode: str = ""  # State machine's commanded mode
    debug_pending_transition: str = ""  # Pending mode transition (if any)
    debug_debounce_wait_seconds: float = 0.0  # Seconds remaining in debounce

    # Spike analysis fields for conservative spike discharge
    spike_end_time: datetime | None = None  # Estimated end of current spike
    spike_max_price: float = 0.0  # Maximum price within spike window
    spike_price_threshold: float = 0.0  # Price threshold for top X% percentile
    spike_reserve_soc: float = 0.0  # Calculated reserve SOC for spike survival
    spike_hours_remaining: float = 0.0  # Hours until spike ends
    spike_in_conservative_mode: bool = False  # Whether conservative mode is active

    # Excess solar load shifting sensors (backlog-high-017)
    excess_solar_available: bool = False  # Simple ON/OFF for basic automations
    excess_solar_current_kw: float = 0.0  # Current excess generation rate
    excess_solar_current_hour_kwh: float = 0.0  # Excess available in current hour
    excess_solar_next_2h_kwh: float = 0.0  # Excess available in next 2 hours
    excess_solar_next_4h_kwh: float = 0.0  # Excess available in next 4 hours
    excess_until_battery_full_kwh: float = 0.0  # Excess until battery reaches 100%
    excess_until_negative_fit_kwh: float = 0.0  # Excess before negative FIT window
    time_until_battery_full_minutes: int = 0  # Minutes until battery full from solar
    negative_fit_window_start: datetime | None = None  # When negative FIT begins
    negative_fit_window_duration_minutes: int = 0  # Duration of negative FIT period
    can_add_load_now: bool = False  # Critical: safe to add discretionary load
    safe_additional_load_kw: float = 0.0  # Max kW that can be added safely
    load_shift_signal: str = "HOLD"  # INCREASE_LOAD, MAINTAIN_LOAD, REDUCE_LOAD, HOLD
    load_shift_recommended_kw: float = 0.0  # Suggested load change (+ or -)
    load_shift_recommended_duration_minutes: int = 0  # How long to maintain change
    load_shift_reason: str = ""  # Human-readable explanation
    load_shift_confidence: str = "low"  # low/medium/high
    grid_charge_risk: bool = False  # Would adding load trigger grid charging?
    current_excess_rate_kw: float = 0.0  # Current excess generation rate (Real-time)

    # Weather correlation fields (Issue #61)
    weather_entity_id: str = ""  # Configured weather entity
    weather_temperature_current: float = 0.0  # Current temperature in °C
    weather_temperature_forecast: dict[int, float] = field(
        default_factory=dict
    )  # hour -> temperature
    weather_condition: str = "unknown"  # Current weather condition
    weather_correlation_confidence: str = "low"  # low/medium/high
    weather_adjustment_applied: bool = False  # Whether weather adjustment was used
    weather_learning_enabled: bool = True  # Whether learning is enabled
    weather_avg_cooling_slope: float = 0.0  # Average kW per °C above cooling threshold
    weather_avg_heating_slope: float = 0.0  # Average kW per °C below heating threshold
    weather_avg_r_squared: float = 0.0  # Average regression fit quality
    weather_sample_count: int = 0  # Number of samples used for learning
    weather_usable_hours: int = 0  # Hours whose label is medium or high
    weather_hours_with_data: int = 0  # Hours with any regression result
    weather_anomaly_weight: float = 1.0  # Issue #681: Weight for rollback evaluation

    # Forecast accuracy tracking fields (Issue #37 Phase 2)
    # SOC prediction errors (predicted - actual, in percentage points)
    forecast_error_soc_15min: float = 0.0  # Error for 15-minute predictions
    forecast_error_soc_1h: float = 0.0  # Error for 1-hour predictions
    forecast_error_soc_4h: float = 0.0  # Error for 4-hour predictions
    # SOC accuracy percentages (100 - abs error, clamped to 0-100)
    forecast_accuracy_soc_15min: float | None = None  # None = no data yet
    forecast_accuracy_soc_1h: float | None = None  # None = no data yet
    forecast_accuracy_soc_4h: float | None = None  # None = no data yet
    # Price prediction errors
    forecast_error_buy_price_1h: float = 0.0  # Buy price error ($/kWh)
    forecast_error_sell_price_1h: float = 0.0  # Sell price error ($/kWh)
    # Comparison metadata
    forecast_comparisons_made: int = 0  # Total comparisons since restart
    forecast_last_comparison_time: str = ""  # ISO timestamp of last comparison
    forecast_first_prediction_time: str = ""  # ISO timestamp of first stored prediction
    forecast_history_count: int = 0  # Number of predictions in history

    # Entity health and error tracking (Issue #94)
    integration_status: str = "ok"  # "ok", "degraded", "error"
    integration_status_message: str = "All systems operational"
    entity_errors: list[str] = field(default_factory=list)  # Current error messages
    entity_warnings: list[str] = field(default_factory=list)  # Current warning messages
    entity_health: dict[str, Any] = field(
        default_factory=dict
    )  # Per-entity health status
    last_entity_check: str = ""  # ISO timestamp of last health check
    required_entities_healthy: bool = True  # Are all required entities available?
    localshift_entity_health: dict[str, Any] = field(
        default_factory=dict
    )  # Health status for LocalShift internal entities
    orphaned_localshift_entities: dict[str, Any] = field(
        default_factory=dict
    )  # Owned registry entries absent from LOCALSHIFT_ENTITY_CONFIG (Issue #880)

    # --- Learning system (Issue #170 Phase 1) ---
    performance_metrics: PerformanceMetrics = field(default_factory=PerformanceMetrics)
    recent_decision_log: list[dict[str, Any]] = field(
        default_factory=list
    )  # last 24h of decisions for sensor
    learning_status: str = "observing"  # "observing", "tuning", "optimizing"
    battery_target_soc: float = 80.0  # Configured battery target for decision scoring

    # --- Adaptive parameters (Issue #170 Phase 2) ---
    adaptive_params: AdaptiveParameters = field(default_factory=AdaptiveParameters)

    # --- Pattern analysis (Issue #170 Phase 3) ---
    pattern_report_summary: dict[str, Any] = field(
        default_factory=dict
    )  # Latest pattern analysis summary
    active_bias_corrections: list[dict[str, Any]] = field(
        default_factory=list
    )  # Currently active corrections
    last_pattern_analysis: str | None = (
        None  # ISO timestamp of the last pattern-analysis run
    )
    solar_bias_metrics: dict[str, Any] = field(
        default_factory=dict
    )  # Solar forecast bias metrics and correction factors
    solar_forecast_accuracy: float | None = (
        None  # Overall solar forecast accuracy %; None until enough samples (#881)
    )
    hybrid_solar_accuracy: float | None = (
        None  # Combined LocalShift + Solcast MAPE accuracy
    )

    # --- Optimization controller (Issue #170 Phase 4) ---
    optimization_weights: dict[str, float] = field(
        default_factory=dict
    )  # Objective weights for multi-objective scoring
    contextual_adjustments_active: list[dict[str, Any]] = field(
        default_factory=list
    )  # Active contextual adjustments

    # --- Hybrid timescale metadata (Issue #329) ---
    hybrid_slot_metadata: dict[str, Any] = field(
        default_factory=dict
    )  # slot_intervals, transition_boundary, total_slots, horizon_hours
    forecast_horizon_hours: float = (
        0.0  # Actual time span covered by forecast (Issue #431)
    )

    # --- Startup ready state (Issue #349) ---
    # Tracks whether all required inputs are valid before making automation decisions
    automation_ready: bool = False  # True when all required inputs are valid
    automation_ready_status: dict[str, bool] = field(
        default_factory=dict
    )  # input_name -> is_valid
    automation_ready_missing: list[str] = field(
        default_factory=list
    )  # List of missing/invalid inputs

    # ---------------------------------------------------------------------------
    # --- DP Optimizer outputs (#403 Phase 1, #447 Phase 5)
    # ---------------------------------------------------------------------------
    # The DP optimizer is now the primary planner (Phase 5).
    # None when optimizer is disabled or not yet run this cycle.

    optimizer_result: dict[str, Any] | None = None
    """Serialized OptimizerResult from DPPlanner.plan() for this cycle.
    Keys: success, planner_version, solve_time_seconds, total_slots,
    states_explored, projected_import_kwh, projected_export_kwh,
    projected_net_cost, terminal_shortfall_pct, error_message, reason_code_histogram.
    Decisions are stored separately in optimizer_decisions.
    """

    optimizer_decisions: list[dict[str, Any]] = field(default_factory=list)
    """Per-slot decisions from the optimizer.
    Each dict mirrors PlannedSlotDecision fields including:
    slot_index, timestamp_iso, slot_interval_minutes, action, reason_code,
    objective_terms (dict), predicted_soc_pct, grid_import_kwh, grid_export_kwh,
    solar_kwh, consumption_kwh, buy_price, sell_price.
    """

    optimizer_summary: dict[str, Any] = field(default_factory=dict)
    """Compact summary of optimizer run for diagnostics sensor.
    Keys: enabled, planner_version, success, solve_time_seconds,
    projected_net_cost, projected_import_kwh, projected_export_kwh,
    total_slots, reason_code_histogram, error_message.
    """

    # ---------------------------------------------------------------------------
    # --- Optimizer runtime status (Phase 6 #448) ---
    # ---------------------------------------------------------------------------
    # Tracks apply status for the optimizer-driven control path.

    optimizer_last_apply_status: str = "none"
    """Last apply attempt status: "none", "success", "fallback", "blocked"."""

    optimizer_safety_block_reason: str = ""
    """Reason for safety gate block (empty if not blocked)."""

    optimizer_active_applied_at: str | None = None
    """ISO timestamp of last successful apply."""

    optimizer_apply_plan: dict[str, Any] | None = None
    """Apply plan derived from optimizer decisions.
    Contains: action, battery_mode, target_soc, reason.
    """

    load_deviation_diagnostics: dict[str, Any] = field(default_factory=dict)

    cloud_event_diagnostics: dict[str, Any] = field(default_factory=dict)

    cloud_event_solar_scale_factor: float | None = None

    # ---------------------------------------------------------------------------
    # --- Decision-to-Implementation Lag Tracking (Issue #501) ---
    # ---------------------------------------------------------------------------
    # Measures time from decision made to hardware validation passed.

    decision_timestamp: datetime | None = None
    """When active_mode was set to a new value (decision made)."""

    decision_mode: BatteryMode | None = None
    """The mode that was decided (for lag tracking)."""

    implementation_timestamp: datetime | None = None
    """When validation passed for the decision."""

    decision_lag_seconds: float | None = None
    """Calculated lag from decision to implementation (seconds)."""

    decision_lag_history: list[dict[str, Any]] = field(default_factory=list)
    """History of recent decision-to-implementation lag measurements.
    Each entry: {from_mode, to_mode, lag_seconds, decision_time, implementation_time}.
    Max 50 entries.
    """

    # ---------------------------------------------------------------------------
    # --- Decision-token gating (#622 gate replacement) ---
    # ---------------------------------------------------------------------------
    # Transient per-evaluation flag set by the state machine BEFORE
    # compute_derived_values runs. When False the optimizer facade must NOT
    # re-decide active_mode (it pins the previously-decided mode); the would-be
    # plan mode is surfaced via debug_plan_mode_pending instead. Default False so
    # any code path that forgets to set it freezes rather than thrashes.

    mode_decision_allowed: bool = False
    """True only on evaluations where the decision context changed (one decision
    per price/spike/DW-boundary/SOC-floor change). Transient; not persisted."""

    debug_plan_mode_pending: str | None = None
    """When the decision is frozen, the mode the plan would have selected
    ("plan wants X, decision held at Y"). None when no decision is pending."""

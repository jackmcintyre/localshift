"""Coordinator data structures for the LocalShift integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .const import BatteryMode

if TYPE_CHECKING:
    from .computation_engine_lib.forecast_accuracy import ExtendedAccuracyMetrics


def _default_extended_accuracy_metrics() -> Any:
    """Factory for ExtendedAccuracyMetrics that avoids circular import at module load."""
    from .computation_engine_lib.forecast_accuracy import ExtendedAccuracyMetrics

    return ExtendedAccuracyMetrics()


@dataclass
class ReconciliationReport:
    """Cost reconciliation report comparing estimated vs actual costs.

    Issue #269: Validates cost estimates against metered statistics.
    """

    timestamp: datetime
    period_start: datetime
    period_end: datetime

    # Import cost comparison
    estimated_import_cost: float = 0.0
    actual_import_cost: float = 0.0
    import_variance_pct: float = 0.0

    # Export revenue comparison
    estimated_export_revenue: float = 0.0
    actual_export_revenue: float = 0.0
    export_variance_pct: float = 0.0

    # Overall metrics
    total_variance_pct: float = 0.0
    is_significant: bool = False  # True if variance > 10%
    significance_threshold: float = 10.0  # Percentage threshold

    # Error tracking
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "estimated_import_cost": self.estimated_import_cost,
            "actual_import_cost": self.actual_import_cost,
            "import_variance_pct": self.import_variance_pct,
            "estimated_export_revenue": self.estimated_export_revenue,
            "actual_export_revenue": self.actual_export_revenue,
            "export_variance_pct": self.export_variance_pct,
            "total_variance_pct": self.total_variance_pct,
            "is_significant": self.is_significant,
            "significance_threshold": self.significance_threshold,
            "errors": self.errors,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReconciliationReport:
        """Create from dictionary (deserialization)."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            period_start=datetime.fromisoformat(data["period_start"]),
            period_end=datetime.fromisoformat(data["period_end"]),
            estimated_import_cost=data.get("estimated_import_cost", 0.0),
            actual_import_cost=data.get("actual_import_cost", 0.0),
            import_variance_pct=data.get("import_variance_pct", 0.0),
            estimated_export_revenue=data.get("estimated_export_revenue", 0.0),
            actual_export_revenue=data.get("actual_export_revenue", 0.0),
            export_variance_pct=data.get("export_variance_pct", 0.0),
            total_variance_pct=data.get("total_variance_pct", 0.0),
            is_significant=data.get("is_significant", False),
            significance_threshold=data.get("significance_threshold", 10.0),
            errors=data.get("errors", []),
        )


@dataclass
class PerformanceMetrics:
    """Aggregated performance metrics for the learning system.

    Issue #170 Phase 1: Tracks decision outcomes and efficiency metrics.
    """

    # Daily metrics
    total_decisions_today: int = 0
    avg_decision_score_today: float = 0.0
    grid_charge_efficiency: float = 0.0  # kWh used vs kWh needed
    export_loss_ratio: float = (
        0.0  # exported kWh that was grid-purchased / total grid purchased
    )
    unnecessary_grid_charge_kwh: float = 0.0

    # Rolling metrics (7-day)
    avg_decision_score_7d: float = 0.0
    avg_daily_cost_7d: float = 0.0
    cost_trend: str = "stable"  # "improving", "stable", "degrading"

    # Per-mode metrics
    mode_durations_today: dict[str, float] = field(
        default_factory=dict
    )  # mode -> minutes
    mode_cost_attribution: dict[str, float] = field(
        default_factory=dict
    )  # mode -> $ cost/saving

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
    prices_available: bool = (
        True  # False when price entities are unavailable (Issue #330)
    )
    general_forecast: list[dict[str, Any]] = field(default_factory=list)
    feed_in_forecast: list[dict[str, Any]] = field(default_factory=list)
    solcast_today: list[dict[str, Any]] = field(default_factory=list)
    solcast_tomorrow: list[dict[str, Any]] = field(default_factory=list)
    allow_export: str = "unknown"

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
    cheap_charge_stop_price: float = 0.0
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

    # Forecast cost accumulators (rest of today)
    forecast_import_cost: float = 0.0  # Expected grid import cost
    forecast_export_revenue: float = 0.0  # Expected grid export revenue
    forecast_net_cost: float = 0.0  # Expected net cost (import - export)
    forecast_grid_charge_cost: float = 0.0  # Expected cost for grid charging
    forecast_proactive_export_revenue: float = 0.0  # Revenue from proactive exports

    # Internal state flags (managed by state machine / buttons)
    manual_override: bool = False
    target_reached_today: bool = False
    allow_dw_entry_under_target: bool = (
        False  # Allow DW entry when solar can reach target
    )

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
    debug_mode_source: str = "unknown"  # "forecast" or "fallback"
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
    weather_cooling_coefficient: float = (
        0.0  # Learned kW per °C above cooling threshold
    )
    weather_heating_coefficient: float = (
        0.0  # Learned kW per °C below heating threshold
    )
    weather_sample_count: int = 0  # Number of samples used for learning

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
    solar_bias_metrics: dict[str, Any] = field(
        default_factory=dict
    )  # Solar forecast bias metrics and correction factors
    solar_forecast_accuracy: float = 100.0  # Overall solar forecast accuracy percentage

    # --- Optimization controller (Issue #170 Phase 4) ---
    optimization_weights: dict[str, float] = field(
        default_factory=dict
    )  # Objective weights for multi-objective scoring
    contextual_adjustments_active: list[dict[str, Any]] = field(
        default_factory=list
    )  # Active contextual adjustments

    # --- Cost reconciliation (Issue #269) ---
    reconciliation_report: ReconciliationReport | None = (
        None  # Last cost reconciliation report
    )

    # --- Extended forecast accuracy (Issue #270) ---
    extended_accuracy_metrics: ExtendedAccuracyMetrics = field(
        default_factory=_default_extended_accuracy_metrics
    )

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

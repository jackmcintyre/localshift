"""
optimizer_shadow_runner.py — Dual-run entrypoint for shadow-mode optimizer.

Phase F (#403): Active-control pilot with safety gates.
Status: Supports shadow, assist, and active modes.

This module is the single entry point called by the coordinator each compute
cycle AFTER the legacy planner has finished. It:
  1. Converts legacy forecast slots + coordinator data into OptimizerInputs.
  2. Runs DPPlanner.plan() (shadow — no side effects).
  3. Runs PlannerComparator.compare() to produce a side-by-side diff.
  4. Writes all results back into CoordinatorData shadow fields.
  5. (Phase F) In active mode: applies optimizer decision via safety gate.

The coordinator calls run_shadow_optimizer(data, config_options) and that
is the ONLY coupling point. All optimizer internals stay isolated here.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .optimizer_dp import (
    DPPlanner,
    OptimizerConfig,
    OptimizerInputs,
    OptimizerResult,
    SlotContext,
)
from .planner_comparator import PlannerComparator

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_shadow_optimizer(
    data: Any,  # CoordinatorData — avoid circular import at module load
    config_options: dict[str, Any],
    planner: DPPlanner | None = None,
    comparator: PlannerComparator | None = None,
) -> None:
    """
    Run the DP optimizer in shadow mode and write results into ``data``.

    This function is SAFE to call every compute cycle. If the optimizer is
    disabled or encounters any error it exits cleanly without touching runtime
    control fields.

    Args:
        data:           CoordinatorData instance (mutated for shadow fields only).
        config_options: Integration options dict (from config_entry.options).
        planner:        Optional pre-constructed DPPlanner (for testing / DI).
        comparator:     Optional pre-constructed PlannerComparator (for testing / DI).
    """
    # --- Feature gate ---
    from ..const import (  # noqa: PLC0415
        CONF_OPTIMIZER_ENABLED,
        DEFAULT_OPTIMIZER_ENABLED,
    )

    enabled = config_options.get(CONF_OPTIMIZER_ENABLED, DEFAULT_OPTIMIZER_ENABLED)
    if not enabled:
        # Optimizer disabled — write a minimal summary and exit cleanly
        data.optimizer_shadow_summary = {
            "enabled": False,
            "shadow_mode": True,
            "planner_version": DPPlanner.VERSION,
            "success": False,
            "error_message": "optimizer_enabled=False",
        }
        return

    cycle_id = _make_cycle_id()
    cycle_timestamp_iso = datetime.now(UTC).isoformat()

    _LOGGER.debug(
        "Shadow optimizer starting cycle %s (%d legacy slots)",
        cycle_id,
        len(data.daily_forecast),
    )

    try:
        _run(
            data=data,
            config_options=config_options,
            cycle_id=cycle_id,
            cycle_timestamp_iso=cycle_timestamp_iso,
            planner=planner or DPPlanner(),
            comparator=comparator or PlannerComparator(),
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error(
            "Shadow optimizer failed for cycle %s: %s", cycle_id, exc, exc_info=True
        )
        data.optimizer_shadow_summary = {
            "enabled": True,
            "shadow_mode": True,
            "planner_version": DPPlanner.VERSION,
            "success": False,
            "error_message": str(exc),
            "cycle_id": cycle_id,
        }


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------


def _run(
    data: Any,
    config_options: dict[str, Any],
    cycle_id: str,
    cycle_timestamp_iso: str,
    planner: DPPlanner,
    comparator: PlannerComparator,
) -> None:
    """Core shadow run logic (separated for testability)."""

    # 1. Build OptimizerInputs from coordinator data
    optimizer_config = _build_optimizer_config(data, config_options)
    slots, parity_info = _build_slot_contexts(data)

    if not slots:
        _LOGGER.debug(
            "Shadow optimizer: no slots available, skipping cycle %s", cycle_id
        )
        data.optimizer_shadow_summary = {
            "enabled": True,
            "shadow_mode": True,
            "planner_version": DPPlanner.VERSION,
            "success": False,
            "error_message": "no_slots_available",
            "cycle_id": cycle_id,
            "parity_completeness_pct": 0.0,
        }
        return

    # 1b. Validate slot alignment (Phase B #403)
    alignment = _validate_slot_alignment(data.daily_forecast, slots)
    if not alignment["valid"]:
        _LOGGER.warning(
            "Shadow optimizer slot alignment issues: %s",
            alignment["issues"],
        )

    initial_soc_pct, soc_info = _normalize_initial_soc(data.soc, optimizer_config)
    if initial_soc_pct is None:
        data.optimizer_shadow_summary = {
            "enabled": True,
            "shadow_mode": True,
            "planner_version": DPPlanner.VERSION,
            "success": False,
            "error_message": "invalid_initial_soc",
            "cycle_id": cycle_id,
            "cycle_timestamp_iso": cycle_timestamp_iso,
            "computed_at": cycle_timestamp_iso,
            "initial_soc_info": soc_info,
            "parity_completeness_pct": parity_info.get("completeness_pct", 0.0),
            "alignment_valid": alignment.get("valid", False),
        }
        _LOGGER.warning(
            "Shadow optimizer skipped cycle %s due to invalid initial SOC: %s",
            cycle_id,
            soc_info,
        )
        return

    inputs = OptimizerInputs(
        cycle_id=cycle_id,
        initial_soc_pct=initial_soc_pct,
        slots=slots,
        config=optimizer_config,
    )

    # 2. Run DP optimizer (shadow — pure computation, no side effects)
    result: OptimizerResult = planner.plan(inputs)

    # 3. Write shadow result fields
    data.optimizer_shadow_result = _serialize_result(result)
    data.optimizer_shadow_decisions = [_serialize_decision(d) for d in result.decisions]
    data.optimizer_shadow_summary = _build_summary(
        result,
        cycle_id,
        cycle_timestamp_iso,
        parity_info,
        alignment,
        config_options,
        soc_info,
    )

    _LOGGER.debug(
        "Shadow optimizer cycle %s complete: success=%s slots=%d solve=%.3fs net_cost=%.4f",
        cycle_id,
        result.success,
        result.total_slots,
        result.solve_time_seconds,
        result.projected_net_cost,
    )

    # 4. Run comparison against legacy planner
    legacy_import_kwh, legacy_export_kwh = _compute_legacy_energy_totals(
        data.daily_forecast
    )

    comparison_record = comparator.compare(
        cycle_id=cycle_id,
        cycle_timestamp_iso=cycle_timestamp_iso,
        legacy_slots=data.daily_forecast,
        optimizer_decisions=result.decisions,
        legacy_projected_net_cost=getattr(data, "forecast_net_cost", 0.0),
        legacy_projected_import_kwh=legacy_import_kwh,
        legacy_projected_export_kwh=legacy_export_kwh,
        optimizer_projected_net_cost=result.projected_net_cost,
        optimizer_projected_import_kwh=result.projected_import_kwh,
        optimizer_projected_export_kwh=result.projected_export_kwh,
        demand_window_target_soc_pct=optimizer_config.demand_window_target_soc_pct,
    )
    data.optimizer_comparison = comparison_record.to_dict()

    _LOGGER.debug(
        "Shadow comparison cycle %s: %d mismatches, net_cost_delta=%.4f",
        cycle_id,
        comparison_record.mismatch_count,
        comparison_record.net_cost_delta,
    )


# ---------------------------------------------------------------------------
# Input builders
# ---------------------------------------------------------------------------


def _build_optimizer_config(
    data: Any, config_options: dict[str, Any]
) -> OptimizerConfig:
    """
    Build OptimizerConfig from coordinator data and config options.

    Phase B (#403): Complete mapping of all config fields from user settings.
    Uses safe defaults for tunable parameters that will be exposed in Phase C.
    """
    from ..const import (  # noqa: PLC0415
        BATTERY_CAPACITY_KWH,
        CHARGE_RATE_BOOST_KW,
        CHARGE_RATE_GRID_KW,
        CHARGE_RATE_SOLAR_KW,
        CONF_ALLOW_DW_ENTRY_UNDER_TARGET,
        CONF_BATTERY_TARGET,
        CONF_EXPORT_PRICE_MARGIN,
        CONF_MINIMUM_TARGET_SOC,
        CONF_OPTIMIZATION_MODE,
        DEFAULT_ALLOW_DW_ENTRY_UNDER_TARGET,
        DEFAULT_BATTERY_TARGET,
        DEFAULT_EXPORT_PRICE_MARGIN,
        DEFAULT_MINIMUM_TARGET_SOC,
        DEFAULT_OPTIMIZATION_MODE,
    )

    # User-configurable target SOC for demand window
    target_soc = float(config_options.get(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET))

    # User-configurable minimum SOC (floor for discharge modes)
    min_soc = float(
        config_options.get(CONF_MINIMUM_TARGET_SOC, DEFAULT_MINIMUM_TARGET_SOC)
    )

    # Allow reaching target during DW via solar (instead of by DW start)
    allow_dw_entry_under_target = config_options.get(
        CONF_ALLOW_DW_ENTRY_UNDER_TARGET, DEFAULT_ALLOW_DW_ENTRY_UNDER_TARGET
    )

    optimization_mode = str(
        config_options.get(CONF_OPTIMIZATION_MODE, DEFAULT_OPTIMIZATION_MODE)
    )

    effective_cheap_price = float(getattr(data, "effective_cheap_price", 0.10))
    self_consumption_value_per_kwh = float(
        getattr(data, "general_price", effective_cheap_price)
    )
    if self_consumption_value_per_kwh <= 0.0:
        self_consumption_value_per_kwh = max(0.10, effective_cheap_price)

    export_price_margin = float(
        config_options.get(CONF_EXPORT_PRICE_MARGIN, DEFAULT_EXPORT_PRICE_MARGIN)
    )

    return OptimizerConfig(
        # --- Battery hardware constraints ---
        battery_capacity_kwh=BATTERY_CAPACITY_KWH,
        charge_rate_kw=CHARGE_RATE_GRID_KW,  # 3.3 kW normal grid charge
        boost_charge_rate_kw=CHARGE_RATE_BOOST_KW,  # 5.0 kW boost charge
        solar_charge_rate_kw=CHARGE_RATE_SOLAR_KW,  # 5.0 kW solar->battery cap
        discharge_rate_kw=CHARGE_RATE_BOOST_KW,  # 5.0 kW (Powerwall symmetric)
        # --- Efficiency defaults (Powerwall typical) ---
        # TODO (#403 Phase C): Consider exposing via config if needed
        charge_efficiency=0.92,
        discharge_efficiency=0.95,
        # --- SOC constraints ---
        min_soc_pct=min_soc,  # User-configured minimum
        max_soc_pct=100.0,  # Hard ceiling
        # --- Demand window target ---
        demand_window_target_soc_pct=target_soc,  # User-configured target
        allow_dw_entry_under_target=allow_dw_entry_under_target,
        # --- Objective weights (conservative defaults) ---
        # TODO (#403 Phase C): Expose for tuning if comparison analytics suggest
        target_shortfall_penalty_per_pct=1.0,
        cycle_penalty_per_kwh=0.005,
        # --- SOC discretization ---
        soc_bins=50,
        # --- Optimization mode ---
        optimization_mode=optimization_mode,
        self_consumption_value_per_kwh=self_consumption_value_per_kwh,
        effective_cheap_price=effective_cheap_price,
        export_price_margin=export_price_margin,
        forecast_horizon_hours=float(getattr(data, "forecast_horizon_hours", 24.0)),
    )


# Track parity completeness for diagnostics
_PARITY_FIELDS = [
    "buy_price",
    "sell_price",
    "solar_kwh",
    "consumption_kwh",
    "slot_interval_minutes",
    "is_demand_window_entry",
    "is_demand_window_slot",
]


def _build_slot_contexts(data: Any) -> tuple[list[SlotContext], dict[str, Any]]:
    """
    Convert legacy daily_forecast slots to SlotContext list.

    Each legacy slot dict must provide at minimum:
      - timestamp or start_time (ISO string)
      - buy_price
      - sell_price (or feed_in_price)
      - solar_kwh
      - consumption_kwh
      - slot_interval_minutes (added by #403 deterministic identity work)

    Missing fields are defaulted safely.

    Returns:
        (contexts, parity_info) tuple where:
        - contexts: list of SlotContext objects
        - parity_info: dict with completeness diagnostics
    """
    contexts: list[SlotContext] = []
    total_fields = 0
    populated_fields = 0
    defaulted_fields: dict[str, int] = {}

    for idx, slot in enumerate(data.daily_forecast):
        # Timestamp — use slot_index-derived ISO if present, else from slot
        timestamp_iso = (
            slot.get("timestamp_iso")
            or slot.get("start_time", "")
            or slot.get("period_start", "")
        )

        slot_minutes = int(slot.get("slot_interval_minutes", 30))

        # Track buy_price completeness
        buy_price, defaulted = _get_slot_field(slot, "buy_price", "general_price", 0.0)
        if defaulted:
            defaulted_fields["buy_price"] = defaulted_fields.get("buy_price", 0) + 1
        total_fields += 1
        if not defaulted:
            populated_fields += 1

        # Track sell_price completeness
        sell_price, defaulted = _get_slot_field(
            slot, "sell_price", "feed_in_price", 0.0
        )
        if defaulted:
            defaulted_fields["sell_price"] = defaulted_fields.get("sell_price", 0) + 1
        total_fields += 1
        if not defaulted:
            populated_fields += 1

        # Track solar_kwh completeness
        solar_kwh, defaulted = _get_slot_field(slot, "solar_kwh", "pv_estimate", 0.0)
        if defaulted:
            defaulted_fields["solar_kwh"] = defaulted_fields.get("solar_kwh", 0) + 1
        total_fields += 1
        if not defaulted:
            populated_fields += 1

        # Track consumption_kwh completeness
        consumption_kwh, defaulted = _get_slot_field(
            slot, "consumption_kwh", "estimated_consumption_kwh", 0.0
        )
        if defaulted:
            defaulted_fields["consumption_kwh"] = (
                defaulted_fields.get("consumption_kwh", 0) + 1
            )
        total_fields += 1
        if not defaulted:
            populated_fields += 1

        ctx = SlotContext(
            slot_index=idx,
            timestamp_iso=str(timestamp_iso),
            slot_interval_minutes=slot_minutes,
            buy_price=buy_price,
            sell_price=sell_price,
            solar_kwh=solar_kwh,
            consumption_kwh=consumption_kwh,
            is_demand_window_entry=bool(slot.get("is_demand_window_entry", False)),
            is_demand_window_slot=bool(slot.get("is_demand_window", False)),
            price_source=str(slot.get("price_source", slot.get("slot_type", "legacy"))),
        )
        contexts.append(ctx)

    # Calculate completeness percentage
    completeness_pct = (
        (populated_fields / total_fields * 100) if total_fields > 0 else 0.0
    )

    parity_info = {
        "total_slots": len(contexts),
        "total_fields_checked": total_fields,
        "populated_fields": populated_fields,
        "defaulted_fields": defaulted_fields,
        "completeness_pct": round(completeness_pct, 1),
    }

    return contexts, parity_info


def _get_slot_field(
    slot: dict[str, Any],
    primary_key: str,
    fallback_key: str,
    default: float,
) -> tuple[float, bool]:
    """
    Get a field value from slot dict with fallback and default.

    Returns:
        (value, was_defaulted) tuple
    """
    if primary_key in slot:
        return float(slot[primary_key]), False
    if fallback_key in slot:
        return float(slot[fallback_key]), False
    return default, True


def _normalize_initial_soc(
    raw_soc: Any,
    config: OptimizerConfig,
) -> tuple[float | None, dict[str, Any]]:
    """Normalize and validate initial SOC before optimizer input construction.

    Behavior:
    - Reject non-numeric / non-finite values.
    - Treat 0 < SOC <= 1 as fractional input and convert to percentage.
    - Reject SOC <= 0 as invalid (typically unavailable entity fallback).
    - Clamp out-of-range values to configured bounds.
    """
    info: dict[str, Any] = {
        "raw_soc": raw_soc,
        "normalization": "none",
    }

    try:
        soc = float(raw_soc)
    except (TypeError, ValueError):
        info["error"] = "non_numeric"
        return None, info

    if not math.isfinite(soc):
        info["error"] = "non_finite"
        return None, info

    if 0.0 < soc <= 1.0:
        soc *= 100.0
        info["normalization"] = "fraction_to_percent"

    if soc <= 0.0:
        info["error"] = "non_positive"
        return None, info

    clamped_soc = max(config.min_soc_pct, min(config.max_soc_pct, soc))
    if clamped_soc != soc:
        info["normalization"] = "clamped_to_bounds"
        info["pre_clamp_soc"] = soc
    soc = clamped_soc

    info["normalized_soc_pct"] = round(soc, 3)
    return soc, info


def _compute_legacy_energy_totals(
    legacy_slots: list[dict[str, Any]],
) -> tuple[float, float]:
    """Compute total legacy import/export kWh from forecast slot payload."""
    total_import = 0.0
    total_export = 0.0

    for slot in legacy_slots:
        try:
            total_import += float(slot.get("grid_import_kwh", 0.0) or 0.0)
        except (TypeError, ValueError):
            pass
        try:
            total_export += float(slot.get("grid_export_kwh", 0.0) or 0.0)
        except (TypeError, ValueError):
            pass

    return total_import, total_export


def _validate_slot_alignment(
    legacy_slots: list[dict[str, Any]],
    contexts: list[SlotContext],
) -> dict[str, Any]:
    """
    Validate alignment between legacy slots and SlotContexts.

    Phase B (#403): Ensures 1:1 mapping and flag mismatches for debugging.

    Returns:
        Dict with validation results including any alignment issues.
    """
    issues: list[str] = []
    warnings: list[str] = []

    # Check count alignment
    if len(legacy_slots) != len(contexts):
        issues.append(
            f"slot_count_mismatch: legacy={len(legacy_slots)} contexts={len(contexts)}"
        )
        return {
            "valid": False,
            "issues": issues,
            "warnings": warnings,
        }

    # Check per-slot alignment
    for idx, (legacy, ctx) in enumerate(zip(legacy_slots, contexts, strict=True)):
        # Check slot_index
        if ctx.slot_index != idx:
            issues.append(f"slot_{idx}: index_mismatch ctx.slot_index={ctx.slot_index}")

        # Check timestamp presence (warning only if missing)
        if not ctx.timestamp_iso:
            warnings.append(f"slot_{idx}: missing_timestamp")

        # Check for negative prices (may indicate data issues)
        if ctx.buy_price < 0:
            warnings.append(f"slot_{idx}: negative_buy_price={ctx.buy_price}")

        # Check for slot_interval_minutes consistency
        legacy_minutes = legacy.get("slot_interval_minutes", 30)
        if ctx.slot_interval_minutes != legacy_minutes:
            issues.append(
                f"slot_{idx}: interval_mismatch legacy={legacy_minutes} ctx={ctx.slot_interval_minutes}"
            )

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "slots_checked": len(contexts),
    }


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def _serialize_result(result: OptimizerResult) -> dict[str, Any]:
    """Serialize OptimizerResult metadata (without decisions) to dict."""
    return {
        "success": result.success,
        "planner_version": result.planner_version,
        "solve_time_seconds": round(result.solve_time_seconds, 4),
        "total_slots": result.total_slots,
        "states_explored": result.states_explored,
        "projected_import_kwh": round(result.projected_import_kwh, 3),
        "projected_export_kwh": round(result.projected_export_kwh, 3),
        "projected_net_cost": round(result.projected_net_cost, 4),
        "terminal_shortfall_pct": round(result.terminal_shortfall_pct, 2),
        "error_message": result.error_message,
        "reason_code_histogram": result.reason_code_histogram,
    }


def _serialize_decision(decision: Any) -> dict[str, Any]:
    """Serialize a single PlannedSlotDecision to dict."""
    return {
        "slot_index": decision.slot_index,
        "timestamp_iso": decision.timestamp_iso,
        "slot_interval_minutes": decision.slot_interval_minutes,
        "action": decision.action.value
        if hasattr(decision.action, "value")
        else str(decision.action),
        "reason_code": decision.reason_code.value
        if hasattr(decision.reason_code, "value")
        else str(decision.reason_code),
        "objective_terms": {
            "import_cost": round(decision.objective_terms.import_cost, 4),
            "export_revenue": round(decision.objective_terms.export_revenue, 4),
            "cycle_penalty": round(decision.objective_terms.cycle_penalty, 4),
            "shortfall_penalty": round(decision.objective_terms.shortfall_penalty, 4),
            "net_cost": round(decision.objective_terms.net_cost, 4),
        },
        "predicted_soc_pct": round(decision.predicted_soc_pct, 2),
        "grid_import_kwh": round(decision.grid_import_kwh, 4),
        "grid_export_kwh": round(decision.grid_export_kwh, 4),
        # Derived compatibility flags
        "grid_charge": decision.grid_charge,
        "grid_charge_boost": decision.grid_charge_boost,
        "proactive_export": decision.proactive_export,
    }


def _build_summary(
    result: OptimizerResult,
    cycle_id: str,
    cycle_timestamp_iso: str,
    parity_info: dict[str, Any] | None = None,
    alignment: dict[str, Any] | None = None,
    config_options: dict[str, Any] | None = None,
    initial_soc_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the compact optimizer_shadow_summary dict for the diagnostics sensor."""
    summary = {
        "enabled": True,
        "shadow_mode": True,
        "planner_version": result.planner_version,
        "success": result.success,
        "cycle_id": cycle_id,
        "cycle_timestamp_iso": cycle_timestamp_iso,
        "computed_at": cycle_timestamp_iso,
        "solve_time_seconds": round(result.solve_time_seconds, 4),
        "total_slots": result.total_slots,
        "projected_net_cost": round(result.projected_net_cost, 4),
        "projected_import_kwh": round(result.projected_import_kwh, 3),
        "projected_export_kwh": round(result.projected_export_kwh, 3),
        "terminal_shortfall_pct": round(result.terminal_shortfall_pct, 2),
        "reason_code_histogram": result.reason_code_histogram,
        "error_message": result.error_message,
        "config_options": config_options or {},
    }

    if initial_soc_info:
        summary["initial_soc_info"] = initial_soc_info
        summary["initial_soc_pct"] = initial_soc_info.get("normalized_soc_pct")

    # Add parity completeness diagnostics (Phase B #403)
    if parity_info:
        summary["parity_completeness_pct"] = parity_info.get("completeness_pct", 0.0)
        summary["parity_defaulted_fields"] = parity_info.get("defaulted_fields", {})

    # Add alignment validation results (Phase B #403)
    if alignment:
        summary["alignment_valid"] = alignment.get("valid", False)
        if alignment.get("issues"):
            summary["alignment_issues"] = alignment["issues"]
        if alignment.get("warnings"):
            summary["alignment_warnings"] = alignment["warnings"]

    return summary


def _make_cycle_id() -> str:
    """Generate a short unique cycle identifier."""
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Phase F: Active-mode safety gate and apply path
# ---------------------------------------------------------------------------


@dataclass
class SafetyGateResult:
    """Result of safety gate admission check."""

    allowed: bool
    """True if active-mode execution is permitted."""

    block_reason: str | None = None
    """Reason for block (None if allowed)."""

    details: dict[str, Any] = field(default_factory=dict)
    """Diagnostic details for logging."""


class OptimizerSafetyGate:
    """
    Safety gate for active-mode optimizer execution.

    Validates prerequisites before allowing optimizer decisions to drive runtime behavior.
    All checks must pass for active-mode to proceed; any failure triggers fallback to legacy control.
    """

    def __init__(self, config_options: dict[str, Any]) -> None:
        """Initialize safety gate with config options.

        Args:
            config_options: Integration options from config_entry.options
        """
        from ..const import (  # noqa: PLC0415
            CONF_OPTIMIZER_CONTROL_MODE,
            CONF_OPTIMIZER_ENABLED,
            DEFAULT_OPTIMIZER_CONTROL_MODE,
            DEFAULT_OPTIMIZER_ENABLED,
            OPTIMIZER_COOLDOWN_CYCLES,
            OPTIMIZER_FORECAST_FRESHNESS_MINUTES,
        )

        self._config_options = config_options
        self._optimizer_enabled = config_options.get(
            CONF_OPTIMIZER_ENABLED, DEFAULT_OPTIMIZER_ENABLED
        )
        self._control_mode = config_options.get(
            CONF_OPTIMIZER_CONTROL_MODE, DEFAULT_OPTIMIZER_CONTROL_MODE
        )
        self._cooldown_cycles = OPTIMIZER_COOLDOWN_CYCLES
        self._forecast_freshness_minutes = OPTIMIZER_FORECAST_FRESHNESS_MINUTES

    def check_admission(
        self,
        data: Any,
        optimizer_result: OptimizerResult | None,
        alignment: dict[str, Any] | None,
    ) -> SafetyGateResult:
        """
        Check all admission criteria for active-mode execution.

        Args:
            data: CoordinatorData instance
            optimizer_result: Result from DPPlanner.plan()
            alignment: Slot alignment validation result

        Returns:
            SafetyGateResult with allowed status and block reason
        """
        checks: list[dict[str, Any]] = []
        details: dict[str, Any] = {}

        # Check 1: Optimizer enabled
        if not self._optimizer_enabled:
            return SafetyGateResult(
                allowed=False,
                block_reason="optimizer_disabled",
                details={"enabled": False},
            )
        checks.append({"name": "optimizer_enabled", "passed": True})
        details["optimizer_enabled"] = True

        # Check 2: Control mode is active
        if self._control_mode != "active":
            return SafetyGateResult(
                allowed=False,
                block_reason=f"control_mode_not_active(mode={self._control_mode})",
                details={"control_mode": self._control_mode},
            )
        checks.append({"name": "control_mode_active", "passed": True})
        details["control_mode"] = "active"

        # Check 3: Optimizer result exists
        if optimizer_result is None:
            return SafetyGateResult(
                allowed=False,
                block_reason="optimizer_result_none",
                details={"optimizer_result": None},
            )
        checks.append({"name": "optimizer_result_exists", "passed": True})

        # Check 4: Optimizer solve succeeded
        if not optimizer_result.success:
            return SafetyGateResult(
                allowed=False,
                block_reason="optimizer_solve_failed",
                details={
                    "success": False,
                    "error": optimizer_result.error_message,
                },
            )
        checks.append({"name": "solve_success", "passed": True})
        details["solve_success"] = True

        # Check 5: Slot alignment valid
        if alignment and not alignment.get("valid", True):
            return SafetyGateResult(
                allowed=False,
                block_reason="slot_alignment_invalid",
                details={"alignment_issues": alignment.get("issues", [])},
            )
        checks.append({"name": "slot_alignment_valid", "passed": True})
        details["alignment_valid"] = alignment.get("valid", True) if alignment else None

        # Check 5: Forecast freshness
        forecast_age_minutes = self._get_forecast_age_minutes(data)
        if (
            forecast_age_minutes is not None
            and forecast_age_minutes > self._forecast_freshness_minutes
        ):
            return SafetyGateResult(
                allowed=False,
                block_reason="forecast_stale",
                details={
                    "age_minutes": forecast_age_minutes,
                    "max_allowed_minutes": self._forecast_freshness_minutes,
                },
            )
        checks.append({"name": "forecast_freshness", "passed": True})
        details["forecast_age_minutes"] = forecast_age_minutes

        details["forecast_freshness_max"] = self._forecast_freshness_minutes

        # Check 6: Not in fallback cooldown
        fallback_count = getattr(data, "optimizer_fallback_count", 0)
        if fallback_count >= self._cooldown_cycles:
            return SafetyGateResult(
                allowed=False,
                block_reason="fallback_cooldown_active",
                details={
                    "fallback_count": fallback_count,
                    "cooldown_threshold": self._cooldown_cycles,
                },
            )
        checks.append({"name": "not_in_cooldown", "passed": True})
        details["fallback_count"] = fallback_count
        details["cooldown_cycles"] = self._cooldown_cycles

        # Check 7: Has decisions for current slot
        if not optimizer_result.decisions:
            return SafetyGateResult(
                allowed=False,
                block_reason="no_decisions_available",
                details={"decision_count": 0},
            )
        checks.append({"name": "has_decisions", "passed": True})
        details["decision_count"] = len(optimizer_result.decisions)

        # All checks passed
        details["checks_passed"] = len(checks)
        details["total_checks"] = len(checks)

        return SafetyGateResult(
            allowed=True,
            block_reason=None,
            details=details,
        )

    def _get_forecast_age_minutes(self, data: Any) -> float | None:
        """Get the age of the forecast in minutes.

        Args:
            data: CoordinatorData instance

        Returns:
            Age in minutes, or None if not determinable
        """
        cycle_timestamp_str = getattr(data, "optimizer_shadow_summary", {}).get(
            "cycle_timestamp_iso"
        )
        if not cycle_timestamp_str:
            return None

        try:
            cycle_time = datetime.fromisoformat(
                cycle_timestamp_str.replace("Z", "+00:00")
            )
            now = datetime.now(UTC)
            age = now - cycle_time
            return age.total_seconds() / 60.0
        except (ValueError, TypeError):
            return None


# ---------------------------------------------------------------------------
# Apply-path logic
# ---------------------------------------------------------------------------


def _derive_runtime_apply_plan(
    decisions: list[Any],
    current_slot_idx: int,
    config: OptimizerConfig,
) -> dict[str, Any]:
    """
    Derive the runtime apply plan from optimizer decisions for the current slot.

    Maps PlannerAction to BatteryMode and extracts SOC targets.

    Args:
        decisions: List of PlannedSlotDecision objects
        current_slot_idx: Index of the current slot (0 if unknown)
        config: OptimizerConfig for target SOC values

    Returns:
        Dict with:
            - action: PlannerAction value
            - battery_mode: BatteryMode string for runtime
            - target_soc: Target SOC percentage (or None)
            - fallback_to_legacy: True if action cannot be applied
    """
    from ..const import BatteryMode  # noqa: PLC0415
    from .optimizer_dp import PlannerAction  # noqa: PLC0415

    if not decisions or current_slot_idx < 0 or current_slot_idx >= len(decisions):
        return {
            "action": "hold",
            "battery_mode": BatteryMode.SELF_CONSUMPTION.value,
            "target_soc": None,
            "fallback_to_legacy": True,
            "reason": "no_valid_decision_for_current_slot",
        }

    decision = decisions[current_slot_idx]
    action = decision.action

    # Map PlannerAction to BatteryMode
    if action == PlannerAction.HOLD:
        return {
            "action": action.value,
            "battery_mode": BatteryMode.SELF_CONSUMPTION.value,
            "target_soc": None,
            "fallback_to_legacy": False,
            "reason": "optimizer_hold",
        }

    if action == PlannerAction.CHARGE_GRID_NORMAL:
        return {
            "action": action.value,
            "battery_mode": BatteryMode.GRID_CHARGING.value,
            "target_soc": config.demand_window_target_soc_pct,
            "fallback_to_legacy": False,
            "reason": "optimizer_charge_grid_normal",
        }

    if action == PlannerAction.CHARGE_GRID_BOOST:
        return {
            "action": action.value,
            "battery_mode": BatteryMode.BOOST_CHARGING.value,
            "target_soc": config.demand_window_target_soc_pct,
            "fallback_to_legacy": False,
            "reason": "optimizer_charge_grid_boost",
        }

    if action == PlannerAction.EXPORT_PROACTIVE:
        return {
            "action": action.value,
            "battery_mode": BatteryMode.PROACTIVE_EXPORT.value,
            "target_soc": None,
            "fallback_to_legacy": False,
            "reason": "optimizer_export_proactive",
        }

    # Unknown action - fall back to legacy
    return {
        "action": str(action),
        "battery_mode": BatteryMode.SELF_CONSUMPTION.value,
        "target_soc": None,
        "fallback_to_legacy": True,
        "reason": f"unknown_action_{action}",
    }


def _find_current_slot_index(data: Any) -> int:
    """
    Find the index of the current slot in the optimizer decisions.

    Args:
        data: CoordinatorData instance

    Returns:
        Index of current slot, or 0 as fallback
    """
    now = datetime.now(UTC)

    decisions = data.optimizer_shadow_decisions
    if not decisions:
        return 0

    # Find the first slot where timestamp_iso is >= now
    for idx, decision in enumerate(decisions):
        timestamp_str = decision.get("timestamp_iso", "")
        if not timestamp_str:
            continue

        try:
            slot_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            slot_end = slot_time + timedelta(
                minutes=decision.get("slot_interval_minutes", 30)
            )

            if slot_time <= now < slot_end:
                return idx
        except (ValueError, TypeError):
            continue

    # Default: use first slot
    return 0

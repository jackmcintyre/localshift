"""
optimizer_runner.py — DP optimizer entrypoint.

Phase 6 (#448): Renamed from optimizer_shadow_runner.py, removed shadow terminology.

This module is the single entry point called by the coordinator each compute
cycle. It:
  1. Converts raw coordinator data into OptimizerInputs via SlotBuilder.
  2. Runs DPPlanner.plan() to compute optimal schedule.
  3. Writes all results into CoordinatorData optimizer_* fields.

The coordinator calls run_optimizer(data, config_options) and that
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
    PlannerAction,
    SlotContext,
)
from .slot_builder import SlotBuilder

_LOGGER = logging.getLogger(__name__)

# Safety multiplier for shortfall penalty calibration.
# The penalty is set to (cheapest_grid_price * battery_kwh / 100) * this factor.
# A value of 1.5 means the optimizer mildly prefers pre-charging over risking
# a shortfall, but won't pay more than 1.5x the remediation cost to do so.
# Do not set above 3.0 without careful testing — higher values cause compulsive
# pre-charging even when solar will cover the deficit.
_SHORTFALL_PENALTY_SAFETY_FACTOR = 3.0


def _get_ha_timezone() -> str:
    """Get Home Assistant timezone string.

    Returns:
        Timezone string (e.g., "Australia/Sydney") or "UTC" as fallback.

    """
    from homeassistant.util import dt as dt_util

    try:
        tz = dt_util.get_time_zone()
        return str(tz) if tz else "UTC"
    except Exception:
        return "UTC"


def _map_mode_to_action(mode: Any) -> PlannerAction | None:
    """Map BatteryMode to PlannerAction for switching penalty calculation.

    Args:
        mode: BatteryMode enum value (or string).

    Returns:
        PlannerAction or None if mode is not actionable by optimizer.

    """
    from ..const import BatteryMode  # noqa: PLC0415

    if mode == BatteryMode.SELF_CONSUMPTION:
        return PlannerAction.HOLD
    if mode == BatteryMode.GRID_CHARGING:
        return PlannerAction.CHARGE_GRID_NORMAL
    if mode == BatteryMode.BOOST_CHARGING:
        return PlannerAction.CHARGE_GRID_BOOST
    if mode == BatteryMode.PROACTIVE_EXPORT:
        return PlannerAction.EXPORT_PROACTIVE

    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_optimizer(
    data: Any,  # CoordinatorData — avoid circular import at module load
    config_options: dict[str, Any],
    planner: DPPlanner | None = None,
) -> None:
    """
    Run the DP optimizer and write results into ``data``.

    This function is SAFE to call every compute cycle. If the optimizer
    encounters any error it exits cleanly without touching runtime
    control fields.

    Args:
        data:           CoordinatorData instance (mutated for optimizer fields).
        config_options: Integration options dict (from config_entry.options).
        planner:        Optional pre-constructed DPPlanner (for testing / DI).

    """
    cycle_id = _make_cycle_id()
    cycle_timestamp_iso = datetime.now(UTC).isoformat()

    _LOGGER.debug(
        "Optimizer starting cycle %s (%d legacy slots)",
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
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Optimizer failed for cycle %s: %s", cycle_id, exc, exc_info=True)
        data.optimizer_summary = {
            "enabled": True,
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
) -> None:
    """Core optimizer run logic (separated for testability)."""

    # 1. Build OptimizerInputs from coordinator data
    optimizer_config = _build_optimizer_config(data, config_options)
    slot_builder = SlotBuilder(
        config_options=config_options, ha_timezone=_get_ha_timezone()
    )
    slots, slot_metadata = slot_builder.build_slots(
        data, getattr(data, "adaptive_params", None)
    )
    parity_info = slot_metadata.to_parity_dict()  # backward-compat shim

    if not slots:
        _LOGGER.debug("Optimizer: no slots available, skipping cycle %s", cycle_id)
        data.optimizer_summary = {
            "enabled": True,
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
            "Optimizer slot alignment issues: %s",
            alignment["issues"],
        )

    initial_soc_pct, soc_info = _normalize_initial_soc(data.soc, optimizer_config)
    if initial_soc_pct is None:
        data.optimizer_summary = {
            "enabled": True,
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
            "Optimizer skipped cycle %s due to invalid initial SOC: %s",
            cycle_id,
            soc_info,
        )
        return

    inputs = OptimizerInputs(
        cycle_id=cycle_id,
        initial_soc_pct=initial_soc_pct,
        current_action=_map_mode_to_action(data.active_mode),
        slots=slots,
        config=optimizer_config,
    )

    # 2. Run DP optimizer (shadow — pure computation, no side effects)
    result: OptimizerResult = planner.plan(inputs)

    # 3. Write result fields
    data.optimizer_result = _serialize_result(result)
    data.optimizer_decisions = [_serialize_decision(d) for d in result.decisions]
    data.optimizer_summary = _build_summary(
        result,
        cycle_id,
        cycle_timestamp_iso,
        parity_info,
        alignment,
        config_options,
        soc_info,
    )

    _LOGGER.debug(
        "Optimizer cycle %s complete: success=%s slots=%d solve=%.3fs net_cost=%.4f",
        cycle_id,
        result.success,
        result.total_slots,
        result.solve_time_seconds,
        result.projected_net_cost,
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
        CONF_SWITCHING_PENALTY,
        DEFAULT_ALLOW_DW_ENTRY_UNDER_TARGET,
        DEFAULT_BATTERY_TARGET,
        DEFAULT_EXPORT_PRICE_MARGIN,
        DEFAULT_MINIMUM_TARGET_SOC,
        DEFAULT_OPTIMIZATION_MODE,
        DEFAULT_SWITCHING_PENALTY,
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

    switching_penalty = float(
        config_options.get(CONF_SWITCHING_PENALTY, DEFAULT_SWITCHING_PENALTY)
    )

    # Apply adaptive parameter transforms (Issue #444 Phase 2)
    adaptive = getattr(data, "adaptive_params", None)

    # cheap_price_bias -> effective_cheap_price
    cheap_price_bias = adaptive.get("cheap_price_bias", 0.0) if adaptive else 0.0
    effective_cheap_price = max(0.0, effective_cheap_price + cheap_price_bias / 100)

    # grid_charge_soc_headroom + overnight_drain_safety_margin -> demand_window_target_soc_pct
    grid_charge_soc_headroom = (
        adaptive.get("grid_charge_soc_headroom", 0.0) if adaptive else 0.0
    )
    overnight_drain_safety_margin = (
        adaptive.get("overnight_drain_safety_margin", 0.0) if adaptive else 0.0
    )
    target_soc = min(
        100.0, target_soc + grid_charge_soc_headroom + overnight_drain_safety_margin
    )

    # export_threshold_adjustment -> export_price_margin
    export_threshold_adj = (
        adaptive.get("export_threshold_adjustment", 0.0) if adaptive else 0.0
    )
    export_price_margin = max(0.0, export_price_margin + export_threshold_adj / 100)

    return OptimizerConfig(
        # --- Battery hardware constraints ---
        battery_capacity_kwh=BATTERY_CAPACITY_KWH,
        charge_rate_kw=CHARGE_RATE_GRID_KW,  # 3.3 kW normal grid charge
        boost_charge_rate_kw=CHARGE_RATE_BOOST_KW,  # 5.0 kW boost charge
        solar_charge_rate_kw=CHARGE_RATE_SOLAR_KW,  # 5.0 kW solar->battery cap
        discharge_rate_kw=CHARGE_RATE_BOOST_KW,  # 5.0 kW (Powerwall symmetric)
        # --- Efficiency defaults (Powerwall typical) ---
        charge_efficiency=0.92,
        discharge_efficiency=0.95,
        # --- SOC constraints ---
        min_soc_pct=min_soc,  # User-configured minimum
        max_soc_pct=100.0,  # Hard ceiling
        # --- Demand window target ---
        demand_window_target_soc_pct=target_soc,  # User-configured target
        allow_dw_entry_under_target=allow_dw_entry_under_target,
        # --- Objective weights ---
        # Calibrated to the actual cost of buying 1% SOC at the cheapest grid price,
        # with a safety margin so the optimizer mildly prefers pre-charging over shortfall.
        # Formula: effective_cheap_price ($/kWh) * battery_kwh / 100 * safety_factor
        # Example at typical Amber overnight rates: 0.15 * 13.5 / 100 * 1.5 = $0.030/%-pt
        # (Fixes #438 — original hardcoded 1.0 was ~53x the actual remediation cost)
        target_shortfall_penalty_per_pct=(
            effective_cheap_price
            * BATTERY_CAPACITY_KWH
            / 100.0
            * _SHORTFALL_PENALTY_SAFETY_FACTOR
        ),
        cycle_penalty_per_kwh=0.05,  # True cycle cost (Fixes #516)
        # --- SOC discretization ---
        soc_bins=50,
        # --- Optimization mode ---
        optimization_mode=optimization_mode,
        self_consumption_value_per_kwh=self_consumption_value_per_kwh,
        effective_cheap_price=effective_cheap_price,
        switching_penalty=switching_penalty,
        export_price_margin=export_price_margin,
        forecast_horizon_hours=float(getattr(data, "forecast_horizon_hours", 24.0)),
        # Issue #559 Root Cause 3: map HOLD signal to strict no-discharge constraint.
        hold_soc=(getattr(data, "load_shift_signal", "HOLD") == "HOLD"),
    )


def _normalize_initial_soc(
    raw_soc: Any,
    config: OptimizerConfig,
) -> tuple[float | None, dict[str, Any]]:
    """Normalize and validate initial SOC before optimizer input construction.

    Behavior:
    - Reject non-numeric / non-finite values.
    - Warn if 0 < SOC <= 1.0 (potential misconfiguration, entity should be 0-100 scale).
    - Reject SOC <= 0 as invalid (typically unavailable entity fallback).
    - Clamp out-of-range values to configured bounds.

    Note: Teslemetry always provides SOC in percentage scale (0-100).
    The fractional-to-percent auto-detection was removed (Issue #424).
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
        _LOGGER.warning(
            "SOC value %.3f is unusually low. Teslemetry provides percentage scale (0-100). "
            "Check that the SOC sensor entity is configured correctly.",
            soc,
        )

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
        "can_solar_reach_target": result.can_solar_reach_target,  # Phase 8 (#450): enables contradiction regression test
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
        "objective_terms": decision.objective_terms.to_dict(),
        "predicted_soc_pct": round(decision.predicted_soc_pct, 2),
        "grid_import_kwh": round(decision.grid_import_kwh, 4),
        "grid_export_kwh": round(decision.grid_export_kwh, 4),
        # Slot context passthroughs (for dashboard debug display) — see #434
        "solar_kwh": round(decision.solar_kwh, 3),
        "consumption_kwh": round(decision.consumption_kwh, 3),
        "buy_price": round(decision.buy_price, 4),
        "sell_price": round(decision.sell_price, 4),
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
    """Build the compact optimizer_summary dict for the diagnostics sensor."""
    summary = {
        "enabled": True,
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
    Safety gate for optimizer execution.

    Validates prerequisites before allowing optimizer decisions to drive runtime behavior.
    All checks must pass for execution; any failure triggers fallback to SELF_CONSUMPTION.
    """

    def __init__(self, config_options: dict[str, Any]) -> None:
        """Initialize safety gate with config options.

        Args:
            config_options: Integration options from config_entry.options

        """
        from ..const import (  # noqa: PLC0415
            OPTIMIZER_FORECAST_FRESHNESS_MINUTES,
        )

        self._config_options = config_options
        self._forecast_freshness_minutes = OPTIMIZER_FORECAST_FRESHNESS_MINUTES

    def check_admission(
        self,
        data: Any,
        optimizer_result: OptimizerResult | None,
        alignment: dict[str, Any] | None,
    ) -> SafetyGateResult:
        """
        Check all admission criteria for optimizer execution.

        Args:
            data: CoordinatorData instance
            optimizer_result: Result from DPPlanner.plan()
            alignment: Slot alignment validation result

        Returns:
            SafetyGateResult with allowed status and block reason

        """
        checks: list[dict[str, Any]] = []
        details: dict[str, Any] = {}

        # Check 1: Optimizer result exists
        if optimizer_result is None:
            return SafetyGateResult(
                allowed=False,
                block_reason="optimizer_result_none",
                details={"optimizer_result": None},
            )
        checks.append({"name": "optimizer_result_exists", "passed": True})

        # Check 2: Optimizer solve succeeded
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

        # Check 3: Slot alignment valid
        if alignment and not alignment.get("valid", True):
            return SafetyGateResult(
                allowed=False,
                block_reason="slot_alignment_invalid",
                details={"alignment_issues": alignment.get("issues", [])},
            )
        checks.append({"name": "slot_alignment_valid", "passed": True})
        details["alignment_valid"] = alignment.get("valid", True) if alignment else None

        # Check 4: Forecast freshness
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

        # Check 5: Has decisions for current slot
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
        cycle_timestamp_str = getattr(data, "optimizer_summary", {}).get(
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
            - reason: Explanation for the decision

    """
    from ..const import BatteryMode  # noqa: PLC0415

    if not decisions or current_slot_idx < 0 or current_slot_idx >= len(decisions):
        return {
            "action": "hold",
            "battery_mode": BatteryMode.SELF_CONSUMPTION.value,
            "target_soc": None,
            "reason": "no_valid_decision_for_current_slot",
        }

    decision = decisions[current_slot_idx]
    # Phase 4 (#446): decisions are now dicts (serialized), not PlannedSlotDecision objects
    action_str = decision.get("action", "")

    # Map PlannerAction string to BatteryMode
    if action_str == "hold":
        return {
            "action": action_str,
            "battery_mode": BatteryMode.HOLD.value,
            "target_soc": None,
            "reason": "optimizer_hold",
        }

    if action_str == "charge_grid_normal":
        return {
            "action": action_str,
            "battery_mode": BatteryMode.GRID_CHARGING.value,
            "target_soc": config.demand_window_target_soc_pct,
            "reason": "optimizer_charge_grid_normal",
        }

    if action_str == "charge_grid_boost":
        return {
            "action": action_str,
            "battery_mode": BatteryMode.BOOST_CHARGING.value,
            "target_soc": config.demand_window_target_soc_pct,
            "reason": "optimizer_charge_grid_boost",
        }

    if action_str == "export_proactive":
        return {
            "action": action_str,
            "battery_mode": BatteryMode.PROACTIVE_EXPORT.value,
            "target_soc": None,
            "reason": "optimizer_export_proactive",
        }

    # Unknown action - default to SELF_CONSUMPTION
    return {
        "action": action_str or "unknown",
        "battery_mode": BatteryMode.SELF_CONSUMPTION.value,
        "target_soc": None,
        "reason": f"unknown_action_{action_str}",
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

    decisions = data.optimizer_decisions
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

"""
optimizer_shadow_runner.py — Dual-run entrypoint for shadow-mode optimizer.

Phase: MVP scaffolding (#403 Phase 1).
Status: SHADOW ONLY — does not affect runtime control behavior.

This module is the single entry point called by the coordinator each compute
cycle AFTER the legacy planner has finished. It:
  1. Converts legacy forecast slots + coordinator data into OptimizerInputs.
  2. Runs DPPlanner.plan() (shadow — no side effects).
  3. Runs PlannerComparator.compare() to produce a side-by-side diff.
  4. Writes all results back into CoordinatorData shadow fields.

The coordinator calls run_shadow_optimizer(data, config_options) and that
is the ONLY coupling point. All optimizer internals stay isolated here.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
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
    slots = _build_slot_contexts(data)

    if not slots:
        _LOGGER.debug("Shadow optimizer: no slots available, skipping cycle %s", cycle_id)
        data.optimizer_shadow_summary = {
            "enabled": True,
            "shadow_mode": True,
            "planner_version": DPPlanner.VERSION,
            "success": False,
            "error_message": "no_slots_available",
            "cycle_id": cycle_id,
        }
        return

    inputs = OptimizerInputs(
        cycle_id=cycle_id,
        initial_soc_pct=float(data.soc),
        slots=slots,
        config=optimizer_config,
    )

    # 2. Run DP optimizer (shadow — pure computation, no side effects)
    result: OptimizerResult = planner.plan(inputs)

    # 3. Write shadow result fields
    data.optimizer_shadow_result = _serialize_result(result)
    data.optimizer_shadow_decisions = [
        _serialize_decision(d) for d in result.decisions
    ]
    data.optimizer_shadow_summary = _build_summary(result, cycle_id, cycle_timestamp_iso)

    _LOGGER.debug(
        "Shadow optimizer cycle %s complete: success=%s slots=%d solve=%.3fs net_cost=%.4f",
        cycle_id,
        result.success,
        result.total_slots,
        result.solve_time_seconds,
        result.projected_net_cost,
    )

    # 4. Run comparison against legacy planner
    comparison_record = comparator.compare(
        cycle_id=cycle_id,
        cycle_timestamp_iso=cycle_timestamp_iso,
        legacy_slots=data.daily_forecast,
        optimizer_decisions=result.decisions,
        legacy_projected_net_cost=getattr(data, "forecast_net_cost", 0.0),
        legacy_projected_import_kwh=getattr(data, "forecast_import_cost", 0.0),
        legacy_projected_export_kwh=getattr(data, "forecast_export_revenue", 0.0),
        optimizer_projected_net_cost=result.projected_net_cost,
        optimizer_projected_import_kwh=result.projected_import_kwh,
        optimizer_projected_export_kwh=result.projected_export_kwh,
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

    MVP: uses defaults + battery target from config.
    TODO (#403 Phase B): Wire in all user-configurable thresholds.
    """
    from ..const import (  # noqa: PLC0415
        BATTERY_CAPACITY_KWH,
        CHARGE_RATE_BOOST_KW,
        CHARGE_RATE_GRID_KW,
        CONF_BATTERY_TARGET,
        DEFAULT_BATTERY_TARGET,
    )

    target_soc = float(
        config_options.get(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET)
    )
    return OptimizerConfig(
        battery_capacity_kwh=BATTERY_CAPACITY_KWH,
        charge_rate_kw=CHARGE_RATE_GRID_KW,
        boost_charge_rate_kw=CHARGE_RATE_BOOST_KW,
        demand_window_target_soc_pct=target_soc,
    )


def _build_slot_contexts(data: Any) -> list[SlotContext]:
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
    """
    contexts: list[SlotContext] = []

    for idx, slot in enumerate(data.daily_forecast):
        # Timestamp — use slot_index-derived ISO if present, else from slot
        timestamp_iso = (
            slot.get("timestamp_iso")
            or slot.get("start_time", "")
            or slot.get("period_start", "")
        )

        slot_minutes = int(slot.get("slot_interval_minutes", 30))

        ctx = SlotContext(
            slot_index=idx,
            timestamp_iso=str(timestamp_iso),
            slot_interval_minutes=slot_minutes,
            buy_price=float(slot.get("buy_price", slot.get("general_price", 0.0))),
            sell_price=float(slot.get("sell_price", slot.get("feed_in_price", 0.0))),
            solar_kwh=float(slot.get("solar_kwh", slot.get("pv_estimate", 0.0))),
            consumption_kwh=float(slot.get("consumption_kwh", slot.get("estimated_consumption_kwh", 0.0))),
            is_demand_window_entry=bool(slot.get("is_demand_window_entry", False)),
            is_demand_window_slot=bool(slot.get("is_demand_window", False)),
            price_source=str(slot.get("price_source", slot.get("slot_type", "legacy"))),
        )
        contexts.append(ctx)

    return contexts


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
        "action": decision.action.value if hasattr(decision.action, "value") else str(decision.action),
        "reason_code": decision.reason_code.value if hasattr(decision.reason_code, "value") else str(decision.reason_code),
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
) -> dict[str, Any]:
    """Build the compact optimizer_shadow_summary dict for the diagnostics sensor."""
    return {
        "enabled": True,
        "shadow_mode": True,
        "planner_version": result.planner_version,
        "success": result.success,
        "cycle_id": cycle_id,
        "cycle_timestamp_iso": cycle_timestamp_iso,
        "solve_time_seconds": round(result.solve_time_seconds, 4),
        "total_slots": result.total_slots,
        "projected_net_cost": round(result.projected_net_cost, 4),
        "projected_import_kwh": round(result.projected_import_kwh, 3),
        "projected_export_kwh": round(result.projected_export_kwh, 3),
        "terminal_shortfall_pct": round(result.terminal_shortfall_pct, 2),
        "reason_code_histogram": result.reason_code_histogram,
        "error_message": result.error_message,
    }


def _make_cycle_id() -> str:
    """Generate a short unique cycle identifier."""
    return uuid.uuid4().hex[:12]

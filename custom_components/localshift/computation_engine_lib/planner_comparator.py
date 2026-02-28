"""
planner_comparator.py — Side-by-side comparison of legacy vs optimizer plans.

Phase: MVP scaffolding (#403 Phase 1).
Status: SHADOW ONLY — does not control runtime behavior.

Provides:
- SlotMismatch      — classification of a single slot-level disagreement
- PlannerComparisonRecord — full cycle comparison output
- PlannerComparator  — computes comparison between legacy and shadow plans each cycle
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mismatch taxonomy
# ---------------------------------------------------------------------------


class MismatchType(StrEnum):
    """Classification of a slot-level disagreement between legacy and optimizer."""

    ACTION_MISMATCH = "ACTION_MISMATCH"
    """Planners chose different actions (charge vs hold vs export)."""

    IMPORT_QUANTITY_MISMATCH = "IMPORT_QUANTITY_MISMATCH"
    """Both plan grid import but differ in quantity (>threshold)."""

    EXPORT_QUANTITY_MISMATCH = "EXPORT_QUANTITY_MISMATCH"
    """Both plan grid export but differ in quantity (>threshold)."""

    TARGET_ATTAINMENT_MISMATCH = "TARGET_ATTAINMENT_MISMATCH"
    """Planners differ in whether DW SOC target will be met."""

    PROFITABILITY_MISMATCH = "PROFITABILITY_MISMATCH"
    """Optimizer avoids an action legacy takes on economics grounds (or vice versa)."""

    UNCLASSIFIED = "UNCLASSIFIED"
    """Disagreement exists but has not yet been classified."""


# ---------------------------------------------------------------------------
# Per-slot mismatch record
# ---------------------------------------------------------------------------


@dataclass
class SlotMismatch:
    """A single slot where legacy and optimizer plans disagree."""

    slot_index: int
    timestamp_iso: str
    slot_interval_minutes: int

    mismatch_type: MismatchType

    legacy_action: str
    """Action chosen by legacy planner (as string for serializability)."""

    optimizer_action: str
    """Action chosen by optimizer (as string for serializability)."""

    legacy_import_kwh: float = 0.0
    optimizer_import_kwh: float = 0.0

    legacy_export_kwh: float = 0.0
    optimizer_export_kwh: float = 0.0

    legacy_net_cost: float = 0.0
    optimizer_net_cost: float = 0.0
    """Projected net cost for this slot from each planner."""

    reason_detail: str = ""
    """Human-readable reason for classification."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for storage/sensor attributes."""
        return {
            "slot_index": self.slot_index,
            "timestamp_iso": self.timestamp_iso,
            "slot_interval_minutes": self.slot_interval_minutes,
            "mismatch_type": self.mismatch_type.value,
            "legacy_action": self.legacy_action,
            "optimizer_action": self.optimizer_action,
            "legacy_import_kwh": round(self.legacy_import_kwh, 4),
            "optimizer_import_kwh": round(self.optimizer_import_kwh, 4),
            "legacy_export_kwh": round(self.legacy_export_kwh, 4),
            "optimizer_export_kwh": round(self.optimizer_export_kwh, 4),
            "legacy_net_cost": round(self.legacy_net_cost, 4),
            "optimizer_net_cost": round(self.optimizer_net_cost, 4),
            "reason_detail": self.reason_detail,
        }


# ---------------------------------------------------------------------------
# Full cycle comparison record
# ---------------------------------------------------------------------------


@dataclass
class PlannerComparisonRecord:
    """
    Full side-by-side comparison of legacy and optimizer plans for one cycle.

    Serialized into CoordinatorData.optimizer_comparison each cycle.
    """

    cycle_id: str
    """Unique cycle identifier tying this record to the compute cycle."""

    cycle_timestamp_iso: str
    """ISO 8601 timestamp when this comparison was computed."""

    legacy_planner_version: str = "legacy"
    optimizer_planner_version: str = "dp_v1"

    # --- Slot counts ---
    total_slots: int = 0
    aligned_slots: int = 0
    """Number of slots successfully aligned between the two plans."""

    mismatch_count: int = 0
    mismatch_by_type: dict[str, int] = field(default_factory=dict)
    """Count of mismatches by MismatchType value."""

    # --- Cost comparison ---
    legacy_projected_import_kwh: float = 0.0
    optimizer_projected_import_kwh: float = 0.0
    import_kwh_delta: float = 0.0
    """optimizer - legacy (negative = optimizer imports less = better)."""

    legacy_projected_export_kwh: float = 0.0
    optimizer_projected_export_kwh: float = 0.0
    export_kwh_delta: float = 0.0
    """optimizer - legacy (positive = optimizer exports more = better)."""

    legacy_projected_net_cost: float = 0.0
    optimizer_projected_net_cost: float = 0.0
    net_cost_delta: float = 0.0
    """optimizer - legacy (negative = optimizer cheaper = better)."""

    # --- Target attainment ---
    legacy_meets_dw_target: bool | None = None
    optimizer_meets_dw_target: bool | None = None

    # --- Mismatch detail ---
    top_mismatches: list[dict[str, Any]] = field(default_factory=list)
    """Top-N slot mismatches (by absolute cost delta), serialized."""

    # --- Status ---
    comparison_succeeded: bool = True
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for CoordinatorData.optimizer_comparison."""
        return {
            "cycle_id": self.cycle_id,
            "cycle_timestamp_iso": self.cycle_timestamp_iso,
            "legacy_planner_version": self.legacy_planner_version,
            "optimizer_planner_version": self.optimizer_planner_version,
            "total_slots": self.total_slots,
            "aligned_slots": self.aligned_slots,
            "mismatch_count": self.mismatch_count,
            "mismatch_by_type": self.mismatch_by_type,
            "legacy_projected_import_kwh": round(self.legacy_projected_import_kwh, 3),
            "optimizer_projected_import_kwh": round(self.optimizer_projected_import_kwh, 3),
            "import_kwh_delta": round(self.import_kwh_delta, 3),
            "legacy_projected_export_kwh": round(self.legacy_projected_export_kwh, 3),
            "optimizer_projected_export_kwh": round(self.optimizer_projected_export_kwh, 3),
            "export_kwh_delta": round(self.export_kwh_delta, 3),
            "legacy_projected_net_cost": round(self.legacy_projected_net_cost, 4),
            "optimizer_projected_net_cost": round(self.optimizer_projected_net_cost, 4),
            "net_cost_delta": round(self.net_cost_delta, 4),
            "legacy_meets_dw_target": self.legacy_meets_dw_target,
            "optimizer_meets_dw_target": self.optimizer_meets_dw_target,
            "top_mismatches": self.top_mismatches,
            "comparison_succeeded": self.comparison_succeeded,
            "error_message": self.error_message,
        }


# ---------------------------------------------------------------------------
# Comparator
# ---------------------------------------------------------------------------


class PlannerComparator:
    """
    Computes a PlannerComparisonRecord from two sets of forecast slots.

    Usage:
        comparator = PlannerComparator()
        record = comparator.compare(
            cycle_id=...,
            cycle_timestamp_iso=...,
            legacy_slots=data.daily_forecast,         # list[dict]
            optimizer_decisions=result.decisions,      # list[PlannedSlotDecision]
            legacy_projected_net_cost=data.forecast_net_cost,
            legacy_projected_import_kwh=total_import,
            legacy_projected_export_kwh=total_export,
        )
        data.optimizer_comparison = record.to_dict()
    """

    TOP_N_MISMATCHES = 5
    QUANTITY_DIFF_THRESHOLD_KWH = 0.05

    def compare(
        self,
        cycle_id: str,
        cycle_timestamp_iso: str,
        legacy_slots: list[dict[str, Any]],
        optimizer_decisions: list[Any],  # list[PlannedSlotDecision]
        legacy_projected_net_cost: float = 0.0,
        legacy_projected_import_kwh: float = 0.0,
        legacy_projected_export_kwh: float = 0.0,
        optimizer_projected_net_cost: float = 0.0,
        optimizer_projected_import_kwh: float = 0.0,
        optimizer_projected_export_kwh: float = 0.0,
    ) -> PlannerComparisonRecord:
        """
        Produce a PlannerComparisonRecord for this cycle.

        legacy_slots: list of slot dicts from legacy planner (daily_forecast).
        optimizer_decisions: list of PlannedSlotDecision from DPPlanner.

        Alignment is done by slot_index (0-based position).
        """
        record = PlannerComparisonRecord(
            cycle_id=cycle_id,
            cycle_timestamp_iso=cycle_timestamp_iso,
        )

        try:
            record = self._compute(
                record=record,
                legacy_slots=legacy_slots,
                optimizer_decisions=optimizer_decisions,
                legacy_projected_net_cost=legacy_projected_net_cost,
                legacy_projected_import_kwh=legacy_projected_import_kwh,
                legacy_projected_export_kwh=legacy_projected_export_kwh,
                optimizer_projected_net_cost=optimizer_projected_net_cost,
                optimizer_projected_import_kwh=optimizer_projected_import_kwh,
                optimizer_projected_export_kwh=optimizer_projected_export_kwh,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("PlannerComparator.compare() failed for cycle %s: %s", cycle_id, exc)
            record.comparison_succeeded = False
            record.error_message = str(exc)

        return record

    def _compute(
        self,
        record: PlannerComparisonRecord,
        legacy_slots: list[dict[str, Any]],
        optimizer_decisions: list[Any],
        legacy_projected_net_cost: float,
        legacy_projected_import_kwh: float,
        legacy_projected_export_kwh: float,
        optimizer_projected_net_cost: float,
        optimizer_projected_import_kwh: float,
        optimizer_projected_export_kwh: float,
    ) -> PlannerComparisonRecord:
        """Internal computation logic."""

        record.total_slots = max(len(legacy_slots), len(optimizer_decisions))
        record.aligned_slots = min(len(legacy_slots), len(optimizer_decisions))

        # --- Cost summary ---
        record.legacy_projected_net_cost = legacy_projected_net_cost
        record.optimizer_projected_net_cost = optimizer_projected_net_cost
        record.net_cost_delta = optimizer_projected_net_cost - legacy_projected_net_cost

        record.legacy_projected_import_kwh = legacy_projected_import_kwh
        record.optimizer_projected_import_kwh = optimizer_projected_import_kwh
        record.import_kwh_delta = optimizer_projected_import_kwh - legacy_projected_import_kwh

        record.legacy_projected_export_kwh = legacy_projected_export_kwh
        record.optimizer_projected_export_kwh = optimizer_projected_export_kwh
        record.export_kwh_delta = optimizer_projected_export_kwh - legacy_projected_export_kwh

        # --- Slot-level comparison ---
        mismatches: list[SlotMismatch] = []
        mismatch_by_type: dict[str, int] = {}

        for idx, (legacy_slot, opt_decision) in enumerate(
            zip(legacy_slots, optimizer_decisions, strict=False)
        ):
            mismatch = self._compare_slot(idx, legacy_slot, opt_decision)
            if mismatch is not None:
                mismatches.append(mismatch)
                key = mismatch.mismatch_type.value
                mismatch_by_type[key] = mismatch_by_type.get(key, 0) + 1

        record.mismatch_count = len(mismatches)
        record.mismatch_by_type = mismatch_by_type

        # Sort by abs cost delta and take top N
        mismatches.sort(
            key=lambda m: abs(m.optimizer_net_cost - m.legacy_net_cost), reverse=True
        )
        record.top_mismatches = [
            m.to_dict() for m in mismatches[: self.TOP_N_MISMATCHES]
        ]

        record.comparison_succeeded = True
        return record

    def _compare_slot(
        self,
        slot_index: int,
        legacy_slot: dict[str, Any],
        opt_decision: Any,  # PlannedSlotDecision
    ) -> SlotMismatch | None:
        """
        Compare a single slot pair. Returns SlotMismatch or None if equivalent.

        MVP implementation: detects action-level differences only.
        TODO (#403 Phase 3): Add quantity and profitability classification.
        """
        # Derive legacy "action" from existing boolean flags
        legacy_action = "hold"
        if legacy_slot.get("grid_charge_boost"):
            legacy_action = "charge_grid_boost"
        elif legacy_slot.get("grid_charge"):
            legacy_action = "charge_grid_normal"
        elif legacy_slot.get("proactive_export"):
            legacy_action = "export_proactive"

        optimizer_action = getattr(opt_decision, "action", None)
        optimizer_action_str = (
            optimizer_action.value if hasattr(optimizer_action, "value") else str(optimizer_action)
        )

        if legacy_action == optimizer_action_str:
            return None  # No mismatch

        timestamp_iso = (
            legacy_slot.get("timestamp_iso", "")
            or getattr(opt_decision, "timestamp_iso", "")
        )
        slot_minutes = legacy_slot.get("slot_interval_minutes", 30)

        return SlotMismatch(
            slot_index=slot_index,
            timestamp_iso=timestamp_iso,
            slot_interval_minutes=slot_minutes,
            mismatch_type=MismatchType.ACTION_MISMATCH,
            legacy_action=legacy_action,
            optimizer_action=optimizer_action_str,
            legacy_import_kwh=legacy_slot.get("grid_import_kwh", 0.0),
            optimizer_import_kwh=getattr(opt_decision, "grid_import_kwh", 0.0),
            legacy_export_kwh=legacy_slot.get("grid_export_kwh", 0.0),
            optimizer_export_kwh=getattr(opt_decision, "grid_export_kwh", 0.0),
            reason_detail="Action type differs between planners",
        )

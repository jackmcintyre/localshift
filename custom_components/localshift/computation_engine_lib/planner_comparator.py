"""
planner_comparator.py — Side-by-side comparison of legacy vs optimizer plans.

Phase: Phase D — Comparator & Analytics Hardening (#403).
Status: SHADOW ONLY — does not control runtime behavior.

Provides:
- SlotMismatch      — classification of a single slot-level disagreement
- PlannerComparisonRecord — full cycle comparison output
- PlannerComparator  — computes comparison between legacy and shadow plans each cycle

Phase D enhancements:
- Full mismatch taxonomy (ACTION, IMPORT_QUANTITY, EXPORT_QUANTITY, TARGET_ATTAINMENT, PROFITABILITY)
- Significance-based ranking for triage
- Per-slot delta computation with configurable thresholds
- Summary rollups for diagnostics
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

    Phase D enhancements:
    - Summary rollup with aggregated statistics
    - Significance scoring for triage
    - Payload size control via MAX_TOP_MISMATCHES
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
    """Top-N slot mismatches (by significance score), serialized."""

    # --- Summary rollup (Phase D) ---
    summary: dict[str, Any] = field(default_factory=dict)
    """Aggregated statistics for diagnostics."""

    # --- Status ---
    comparison_succeeded: bool = True
    error_message: str = ""

    # --- Performance (Phase D) ---
    comparison_time_ms: float = 0.0
    """Time taken to compute this comparison in milliseconds."""

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
            "optimizer_projected_import_kwh": round(
                self.optimizer_projected_import_kwh, 3
            ),
            "import_kwh_delta": round(self.import_kwh_delta, 3),
            "legacy_projected_export_kwh": round(self.legacy_projected_export_kwh, 3),
            "optimizer_projected_export_kwh": round(
                self.optimizer_projected_export_kwh, 3
            ),
            "export_kwh_delta": round(self.export_kwh_delta, 3),
            "legacy_projected_net_cost": round(self.legacy_projected_net_cost, 4),
            "optimizer_projected_net_cost": round(self.optimizer_projected_net_cost, 4),
            "net_cost_delta": round(self.net_cost_delta, 4),
            "legacy_meets_dw_target": self.legacy_meets_dw_target,
            "optimizer_meets_dw_target": self.optimizer_meets_dw_target,
            "top_mismatches": self.top_mismatches,
            "summary": self.summary,
            "comparison_succeeded": self.comparison_succeeded,
            "error_message": self.error_message,
            "comparison_time_ms": round(self.comparison_time_ms, 2),
        }


# ---------------------------------------------------------------------------
# Comparator
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Threshold constants (Phase D)
# ---------------------------------------------------------------------------

# Quantity difference threshold for IMPORT/EXPORT_QUANTITY_MISMATCH classification
# If |legacy_qty - optimizer_qty| > threshold, classify as quantity mismatch
QUANTITY_DIFF_THRESHOLD_KWH = 0.05

# Cost difference threshold for PROFITABILITY_MISMATCH classification
# If |legacy_cost - optimizer_cost| > threshold AND actions differ, classify as profitability
COST_DIFF_THRESHOLD_DOLLARS = 0.01

# Significance score weights for ranking mismatches
SIGNIFICANCE_WEIGHT_COST_IMPACT = 1.0
SIGNIFICANCE_WEIGHT_ACTION_SEVERITY = {
    # Higher weight = more significant mismatch
    "charge_grid_boost": 2.0,  # Expensive action, high impact
    "export_proactive": 1.5,  # Medium impact
    "charge_grid_normal": 1.2,  # Normal impact
    "hold": 0.5,  # Low impact
}


class PlannerComparator:
    """
    Computes a PlannerComparisonRecord from two sets of forecast slots.

    Phase D enhancements:
    - Full mismatch taxonomy (ACTION, IMPORT_QUANTITY, EXPORT_QUANTITY, TARGET_ATTAINMENT, PROFITABILITY)
    - Significance-based ranking for triage
    - Per-slot delta computation with configurable thresholds
    - Summary rollups for diagnostics

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
        demand_window_target_soc_pct: float | None = None,
    ) -> PlannerComparisonRecord:
        """
        Produce a PlannerComparisonRecord for this cycle.

        legacy_slots: list of slot dicts from legacy planner (daily_forecast).
        optimizer_decisions: list of PlannedSlotDecision from DPPlanner.

        Alignment is done by slot_index (0-based position).

        Phase D: Includes timing and summary rollup computation.
        """
        import time

        record = PlannerComparisonRecord(
            cycle_id=cycle_id,
            cycle_timestamp_iso=cycle_timestamp_iso,
        )

        start_time = time.monotonic()
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
                demand_window_target_soc_pct=demand_window_target_soc_pct,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "PlannerComparator.compare() failed for cycle %s: %s", cycle_id, exc
            )
            record.comparison_succeeded = False
            record.error_message = str(exc)

        # Record timing
        record.comparison_time_ms = (time.monotonic() - start_time) * 1000.0
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
        demand_window_target_soc_pct: float | None,
    ) -> PlannerComparisonRecord:
        """
        Internal computation logic.

        Phase D: Includes significance-based ranking and summary rollup.
        """

        record.total_slots = max(len(legacy_slots), len(optimizer_decisions))
        record.aligned_slots = min(len(legacy_slots), len(optimizer_decisions))

        # --- Cost summary ---
        record.legacy_projected_net_cost = legacy_projected_net_cost
        record.optimizer_projected_net_cost = optimizer_projected_net_cost
        record.net_cost_delta = optimizer_projected_net_cost - legacy_projected_net_cost

        record.legacy_projected_import_kwh = legacy_projected_import_kwh
        record.optimizer_projected_import_kwh = optimizer_projected_import_kwh
        record.import_kwh_delta = (
            optimizer_projected_import_kwh - legacy_projected_import_kwh
        )

        record.legacy_projected_export_kwh = legacy_projected_export_kwh
        record.optimizer_projected_export_kwh = optimizer_projected_export_kwh
        record.export_kwh_delta = (
            optimizer_projected_export_kwh - legacy_projected_export_kwh
        )

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

        # --- Demand-window target attainment comparison ---
        legacy_meets_target, optimizer_meets_target = self._compute_target_attainment(
            legacy_slots=legacy_slots,
            optimizer_decisions=optimizer_decisions,
            demand_window_target_soc_pct=demand_window_target_soc_pct,
        )
        record.legacy_meets_dw_target = legacy_meets_target
        record.optimizer_meets_dw_target = optimizer_meets_target

        if (
            legacy_meets_target is not None
            and optimizer_meets_target is not None
            and legacy_meets_target != optimizer_meets_target
        ):
            target_mismatch = self._build_target_attainment_mismatch(
                legacy_slots=legacy_slots,
                optimizer_decisions=optimizer_decisions,
                legacy_meets_target=legacy_meets_target,
                optimizer_meets_target=optimizer_meets_target,
            )
            mismatches.append(target_mismatch)
            key = target_mismatch.mismatch_type.value
            mismatch_by_type[key] = mismatch_by_type.get(key, 0) + 1

        record.mismatch_count = len(mismatches)
        record.mismatch_by_type = mismatch_by_type

        # Phase D: Sort by significance score and take top N
        ranked_mismatches = self.rank_mismatches(mismatches)
        record.top_mismatches = [
            m.to_dict() for m in ranked_mismatches[: self.TOP_N_MISMATCHES]
        ]

        # Phase D: Compute summary rollup
        record.summary = self.compute_summary_rollup(mismatches)

        record.comparison_succeeded = True
        return record

    def _compute_target_attainment(
        self,
        legacy_slots: list[dict[str, Any]],
        optimizer_decisions: list[Any],
        demand_window_target_soc_pct: float | None,
    ) -> tuple[bool | None, bool | None]:
        """Compute DW target attainment booleans for legacy and optimizer plans."""
        if demand_window_target_soc_pct is None:
            return None, None

        for idx, legacy_slot in enumerate(legacy_slots):
            if not legacy_slot.get("is_demand_window_entry", False):
                continue

            legacy_soc = legacy_slot.get("predicted_soc")
            optimizer_soc = None
            if idx < len(optimizer_decisions):
                optimizer_soc = getattr(
                    optimizer_decisions[idx], "predicted_soc_pct", None
                )

            try:
                legacy_soc_f = float(legacy_soc) if legacy_soc is not None else None
            except (TypeError, ValueError):
                legacy_soc_f = None

            try:
                optimizer_soc_f = (
                    float(optimizer_soc) if optimizer_soc is not None else None
                )
            except (TypeError, ValueError):
                optimizer_soc_f = None

            legacy_meets = (
                legacy_soc_f >= demand_window_target_soc_pct
                if legacy_soc_f is not None
                else None
            )
            optimizer_meets = (
                optimizer_soc_f >= demand_window_target_soc_pct
                if optimizer_soc_f is not None
                else None
            )
            return legacy_meets, optimizer_meets

        return None, None

    def _build_target_attainment_mismatch(
        self,
        legacy_slots: list[dict[str, Any]],
        optimizer_decisions: list[Any],
        legacy_meets_target: bool,
        optimizer_meets_target: bool,
    ) -> SlotMismatch:
        """Create a synthetic mismatch record for DW target attainment divergence."""
        entry_idx = 0
        for idx, legacy_slot in enumerate(legacy_slots):
            if legacy_slot.get("is_demand_window_entry", False):
                entry_idx = idx
                break

        legacy_slot = legacy_slots[entry_idx] if legacy_slots else {}
        opt_decision = (
            optimizer_decisions[entry_idx]
            if entry_idx < len(optimizer_decisions)
            else None
        )

        legacy_action = "hold"
        if legacy_slot.get("grid_charge_boost"):
            legacy_action = "charge_grid_boost"
        elif legacy_slot.get("grid_charge"):
            legacy_action = "charge_grid_normal"
        elif legacy_slot.get("proactive_export"):
            legacy_action = "export_proactive"

        optimizer_action = getattr(opt_decision, "action", None)
        optimizer_action_str = (
            optimizer_action.value
            if hasattr(optimizer_action, "value")
            else str(optimizer_action)
        )

        timestamp_iso = legacy_slot.get("timestamp_iso", "") or getattr(
            opt_decision, "timestamp_iso", ""
        )
        slot_minutes = legacy_slot.get("slot_interval_minutes", 30)

        return SlotMismatch(
            slot_index=entry_idx,
            timestamp_iso=timestamp_iso,
            slot_interval_minutes=slot_minutes,
            mismatch_type=MismatchType.TARGET_ATTAINMENT_MISMATCH,
            legacy_action=legacy_action,
            optimizer_action=optimizer_action_str,
            legacy_import_kwh=float(legacy_slot.get("grid_import_kwh", 0.0) or 0.0),
            optimizer_import_kwh=float(
                getattr(opt_decision, "grid_import_kwh", 0.0) or 0.0
            ),
            legacy_export_kwh=float(legacy_slot.get("grid_export_kwh", 0.0) or 0.0),
            optimizer_export_kwh=float(
                getattr(opt_decision, "grid_export_kwh", 0.0) or 0.0
            ),
            reason_detail=(
                f"Demand-window target attainment differs: "
                f"legacy_meets={legacy_meets_target}, optimizer_meets={optimizer_meets_target}"
            ),
        )

    def _compare_slot(
        self,
        slot_index: int,
        legacy_slot: dict[str, Any],
        opt_decision: Any,  # PlannedSlotDecision
    ) -> SlotMismatch | None:
        """
        Compare a single slot pair. Returns SlotMismatch or None if equivalent.

        Phase D implementation: Full mismatch taxonomy with significance scoring.
        Classification priority:
        1. ACTION_MISMATCH - Different action types
        2. IMPORT_QUANTITY_MISMATCH - Same action, different import qty
        3. EXPORT_QUANTITY_MISMATCH - Same action, different export qty
        4. PROFITABILITY_MISMATCH - Same action but different cost implications
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
            optimizer_action.value
            if hasattr(optimizer_action, "value")
            else str(optimizer_action)
        )

        # Extract quantities
        legacy_import = legacy_slot.get("grid_import_kwh", 0.0)
        legacy_export = legacy_slot.get("grid_export_kwh", 0.0)
        optimizer_import = getattr(opt_decision, "grid_import_kwh", 0.0)
        optimizer_export = getattr(opt_decision, "grid_export_kwh", 0.0)

        # Compute per-slot net costs (approximate)
        buy_price = legacy_slot.get("buy_price", 0.0)
        sell_price = legacy_slot.get("sell_price", 0.0)
        legacy_net_cost = (legacy_import * buy_price) - (
            legacy_export * max(0, sell_price)
        )
        optimizer_net_cost = (optimizer_import * buy_price) - (
            optimizer_export * max(0, sell_price)
        )

        timestamp_iso = legacy_slot.get("timestamp_iso", "") or getattr(
            opt_decision, "timestamp_iso", ""
        )
        slot_minutes = legacy_slot.get("slot_interval_minutes", 30)

        # Check for any mismatch
        action_differs = legacy_action != optimizer_action_str
        import_diff = abs(legacy_import - optimizer_import)
        export_diff = abs(legacy_export - optimizer_export)
        cost_diff = abs(legacy_net_cost - optimizer_net_cost)

        # No mismatch if all quantities match and actions are the same
        if (
            not action_differs
            and import_diff <= QUANTITY_DIFF_THRESHOLD_KWH
            and export_diff <= QUANTITY_DIFF_THRESHOLD_KWH
        ):
            return None

        # Classify mismatch type (priority order)
        mismatch_type = MismatchType.UNCLASSIFIED
        reason_detail = ""

        if action_differs:
            # Check if this is a profitability-driven action change
            if cost_diff > COST_DIFF_THRESHOLD_DOLLARS:
                # One planner chose a more expensive action
                if legacy_net_cost > optimizer_net_cost:
                    mismatch_type = MismatchType.PROFITABILITY_MISMATCH
                    reason_detail = f"Optimizer avoids costly legacy action ({legacy_action} -> {optimizer_action_str}), saves ${legacy_net_cost - optimizer_net_cost:.4f}"
                else:
                    mismatch_type = MismatchType.PROFITABILITY_MISMATCH
                    reason_detail = f"Legacy avoids costly optimizer action ({legacy_action} -> {optimizer_action_str}), saves ${optimizer_net_cost - legacy_net_cost:.4f}"
            else:
                mismatch_type = MismatchType.ACTION_MISMATCH
                reason_detail = f"Action type differs: legacy={legacy_action}, optimizer={optimizer_action_str}"

        elif import_diff > QUANTITY_DIFF_THRESHOLD_KWH:
            mismatch_type = MismatchType.IMPORT_QUANTITY_MISMATCH
            reason_detail = f"Import qty differs: legacy={legacy_import:.3f}kWh, optimizer={optimizer_import:.3f}kWh (Δ={import_diff:.3f}kWh)"

        elif export_diff > QUANTITY_DIFF_THRESHOLD_KWH:
            mismatch_type = MismatchType.EXPORT_QUANTITY_MISMATCH
            reason_detail = f"Export qty differs: legacy={legacy_export:.3f}kWh, optimizer={optimizer_export:.3f}kWh (Δ={export_diff:.3f}kWh)"

        elif cost_diff > COST_DIFF_THRESHOLD_DOLLARS:
            mismatch_type = MismatchType.PROFITABILITY_MISMATCH
            reason_detail = f"Same action but cost differs: legacy=${legacy_net_cost:.4f}, optimizer=${optimizer_net_cost:.4f}"

        return SlotMismatch(
            slot_index=slot_index,
            timestamp_iso=timestamp_iso,
            slot_interval_minutes=slot_minutes,
            mismatch_type=mismatch_type,
            legacy_action=legacy_action,
            optimizer_action=optimizer_action_str,
            legacy_import_kwh=legacy_import,
            optimizer_import_kwh=optimizer_import,
            legacy_export_kwh=legacy_export,
            optimizer_export_kwh=optimizer_export,
            legacy_net_cost=legacy_net_cost,
            optimizer_net_cost=optimizer_net_cost,
            reason_detail=reason_detail,
        )

    def compute_significance_score(self, mismatch: SlotMismatch) -> float:
        """
        Compute a significance score for ranking mismatches in triage.

        Higher score = more significant mismatch requiring attention.

        Factors:
        - Cost impact (absolute dollar difference)
        - Action severity (boost > export > normal > hold)
        - Quantity delta (larger = more significant)
        """
        cost_impact = abs(mismatch.optimizer_net_cost - mismatch.legacy_net_cost)

        # Get action severity weights (use the more severe of the two actions)
        legacy_weight = SIGNIFICANCE_WEIGHT_ACTION_SEVERITY.get(
            mismatch.legacy_action, 1.0
        )
        optimizer_weight = SIGNIFICANCE_WEIGHT_ACTION_SEVERITY.get(
            mismatch.optimizer_action, 1.0
        )
        action_severity = max(legacy_weight, optimizer_weight)

        # Quantity delta
        qty_delta = abs(
            mismatch.legacy_import_kwh - mismatch.optimizer_import_kwh
        ) + abs(mismatch.legacy_export_kwh - mismatch.optimizer_export_kwh)

        # Combined score
        score = (
            (cost_impact * SIGNIFICANCE_WEIGHT_COST_IMPACT)
            + (action_severity * 0.1)
            + (qty_delta * 0.05)
        )
        return score

    def rank_mismatches(self, mismatches: list[SlotMismatch]) -> list[SlotMismatch]:
        """
        Rank mismatches by significance score for triage.

        Returns mismatches sorted by descending significance.
        """
        scored = [(self.compute_significance_score(m), m) for m in mismatches]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored]

    def compute_summary_rollup(self, mismatches: list[SlotMismatch]) -> dict[str, Any]:
        """
        Compute summary statistics for a set of mismatches.

        Used for diagnostics and sensor attributes.
        """
        if not mismatches:
            return {
                "total_mismatches": 0,
                "total_cost_impact": 0.0,
                "by_type": {},
                "most_significant_type": None,
                "avg_significance_score": 0.0,
            }

        by_type: dict[str, list[SlotMismatch]] = {}
        total_cost_impact = 0.0
        total_significance = 0.0

        for m in mismatches:
            key = m.mismatch_type.value
            if key not in by_type:
                by_type[key] = []
            by_type[key].append(m)
            total_cost_impact += abs(m.optimizer_net_cost - m.legacy_net_cost)
            total_significance += self.compute_significance_score(m)

        # Find most significant type by count
        most_significant_type = (
            max(by_type.keys(), key=lambda k: len(by_type[k])) if by_type else None
        )

        return {
            "total_mismatches": len(mismatches),
            "total_cost_impact": round(total_cost_impact, 4),
            "by_type": {k: len(v) for k, v in by_type.items()},
            "most_significant_type": most_significant_type,
            "avg_significance_score": round(total_significance / len(mismatches), 4)
            if mismatches
            else 0.0,
        }

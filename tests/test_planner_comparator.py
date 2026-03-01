"""
Tests for planner_comparator.py — Phase D enhancements.

Tests cover:
- Full mismatch taxonomy (ACTION, IMPORT_QUANTITY, EXPORT_QUANTITY, PROFITABILITY)
- Significance-based ranking
- Summary rollups
- Performance timing
"""

import pytest

from custom_components.localshift.computation_engine_lib.planner_comparator import (
    MismatchType,
    PlannerComparator,
    PlannerComparisonRecord,
    SlotMismatch,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def comparator() -> PlannerComparator:
    """Create a PlannerComparator instance."""
    return PlannerComparator()


@pytest.fixture
def legacy_slot_hold() -> dict:
    """Legacy slot with hold action."""
    return {
        "timestamp_iso": "2025-01-01T00:00:00Z",
        "slot_interval_minutes": 30,
        "grid_charge": False,
        "grid_charge_boost": False,
        "proactive_export": False,
        "grid_import_kwh": 0.0,
        "grid_export_kwh": 0.0,
        "buy_price": 0.25,
        "sell_price": 0.08,
    }


@pytest.fixture
def legacy_slot_charge() -> dict:
    """Legacy slot with grid charge action."""
    return {
        "timestamp_iso": "2025-01-01T00:30:00Z",
        "slot_interval_minutes": 30,
        "grid_charge": True,
        "grid_charge_boost": False,
        "proactive_export": False,
        "grid_import_kwh": 1.65,
        "grid_export_kwh": 0.0,
        "buy_price": 0.15,
        "sell_price": 0.08,
    }


@pytest.fixture
def legacy_slot_boost() -> dict:
    """Legacy slot with boost charge action."""
    return {
        "timestamp_iso": "2025-01-01T01:00:00Z",
        "slot_interval_minutes": 30,
        "grid_charge": True,
        "grid_charge_boost": True,
        "proactive_export": False,
        "grid_import_kwh": 2.5,
        "grid_export_kwh": 0.0,
        "buy_price": 0.10,
        "sell_price": 0.08,
    }


@pytest.fixture
def legacy_slot_export() -> dict:
    """Legacy slot with export action."""
    return {
        "timestamp_iso": "2025-01-01T01:30:00Z",
        "slot_interval_minutes": 30,
        "grid_charge": False,
        "grid_charge_boost": False,
        "proactive_export": True,
        "grid_import_kwh": 0.0,
        "grid_export_kwh": 2.5,
        "buy_price": 0.30,
        "sell_price": 0.25,
    }


class MockOptimizerDecision:
    """Mock PlannedSlotDecision for testing."""

    def __init__(
        self,
        action: str = "hold",
        timestamp_iso: str = "2025-01-01T00:00:00Z",
        slot_interval_minutes: int = 30,
        grid_import_kwh: float = 0.0,
        grid_export_kwh: float = 0.0,
        predicted_soc_pct: float = 50.0,
    ):
        self.action = type("Action", (), {"value": action})()
        self.timestamp_iso = timestamp_iso
        self.slot_interval_minutes = slot_interval_minutes
        self.grid_import_kwh = grid_import_kwh
        self.grid_export_kwh = grid_export_kwh
        self.predicted_soc_pct = predicted_soc_pct


# ---------------------------------------------------------------------------
# Test Mismatch Taxonomy
# ---------------------------------------------------------------------------


class TestMismatchTaxonomy:
    """Tests for full mismatch taxonomy classification."""

    def test_profitability_mismatch_hold_vs_charge(self, comparator, legacy_slot_hold):
        """Test PROFITABILITY_MISMATCH when legacy holds and optimizer charges (cost differs)."""
        opt_decision = MockOptimizerDecision(action="charge_grid_normal", grid_import_kwh=1.65)
        
        mismatch = comparator._compare_slot(0, legacy_slot_hold, opt_decision)
        
        # When actions differ AND cost differs, it's PROFITABILITY_MISMATCH
        assert mismatch is not None
        assert mismatch.mismatch_type == MismatchType.PROFITABILITY_MISMATCH

    def test_profitability_mismatch_charge_vs_export(self, comparator, legacy_slot_charge):
        """Test PROFITABILITY_MISMATCH when legacy charges and optimizer exports."""
        opt_decision = MockOptimizerDecision(action="export_proactive", grid_export_kwh=2.5)
        
        mismatch = comparator._compare_slot(0, legacy_slot_charge, opt_decision)
        
        # When actions differ AND cost differs, it's PROFITABILITY_MISMATCH
        assert mismatch is not None
        assert mismatch.mismatch_type == MismatchType.PROFITABILITY_MISMATCH

    def test_action_mismatch_low_cost_diff(self, comparator):
        """Test ACTION_MISMATCH when cost diff is below threshold."""
        # Create a slot where the cost difference is below COST_DIFF_THRESHOLD_DOLLARS
        legacy_slot = {
            "timestamp_iso": "2025-01-01T00:00:00Z",
            "slot_interval_minutes": 30,
            "grid_charge": False,
            "grid_charge_boost": False,
            "proactive_export": False,
            "grid_import_kwh": 0.0,
            "grid_export_kwh": 0.0,
            "buy_price": 0.001,  # Very low price so cost diff is small
            "sell_price": 0.001,
        }
        # Optimizer charges but cost diff is tiny
        opt_decision = MockOptimizerDecision(action="charge_grid_normal", grid_import_kwh=0.01)
        
        mismatch = comparator._compare_slot(0, legacy_slot, opt_decision)
        
        # With very low cost diff, it should be ACTION_MISMATCH not PROFITABILITY_MISMATCH
        assert mismatch is not None
        assert mismatch.mismatch_type == MismatchType.ACTION_MISMATCH
        assert "Action type differs" in mismatch.reason_detail

    def test_profitability_mismatch_optimizer_cheaper(self, comparator, legacy_slot_boost):
        """Test PROFITABILITY_MISMATCH when optimizer avoids costly action."""
        # Legacy boost charges at $0.10/kWh, optimizer holds (costs nothing)
        opt_decision = MockOptimizerDecision(action="hold")
        
        mismatch = comparator._compare_slot(0, legacy_slot_boost, opt_decision)
        
        assert mismatch is not None
        assert mismatch.mismatch_type == MismatchType.PROFITABILITY_MISMATCH
        assert "Optimizer avoids costly legacy action" in mismatch.reason_detail

    def test_import_quantity_mismatch(self, comparator, legacy_slot_charge):
        """Test IMPORT_QUANTITY_MISMATCH when same action but different qty."""
        # Same action (charge) but different import quantity
        opt_decision = MockOptimizerDecision(
            action="charge_grid_normal",
            grid_import_kwh=1.0,  # Less than legacy's 1.65
        )
        
        mismatch = comparator._compare_slot(0, legacy_slot_charge, opt_decision)
        
        assert mismatch is not None
        assert mismatch.mismatch_type == MismatchType.IMPORT_QUANTITY_MISMATCH
        assert "Import qty differs" in mismatch.reason_detail

    def test_export_quantity_mismatch(self, comparator, legacy_slot_export):
        """Test EXPORT_QUANTITY_MISMATCH when same action but different qty."""
        # Same action (export) but different export quantity
        opt_decision = MockOptimizerDecision(
            action="export_proactive",
            grid_export_kwh=1.5,  # Less than legacy's 2.5
        )
        
        mismatch = comparator._compare_slot(0, legacy_slot_export, opt_decision)
        
        assert mismatch is not None
        assert mismatch.mismatch_type == MismatchType.EXPORT_QUANTITY_MISMATCH
        assert "Export qty differs" in mismatch.reason_detail

    def test_no_mismatch_same_action_same_qty(self, comparator, legacy_slot_hold):
        """Test no mismatch when actions and quantities match."""
        opt_decision = MockOptimizerDecision(action="hold")
        
        mismatch = comparator._compare_slot(0, legacy_slot_hold, opt_decision)
        
        assert mismatch is None

    def test_no_mismatch_small_quantity_diff(self, comparator, legacy_slot_charge):
        """Test no mismatch when quantity diff is below threshold."""
        # Difference of 0.01 kWh is below QUANTITY_DIFF_THRESHOLD_KWH (0.05)
        opt_decision = MockOptimizerDecision(
            action="charge_grid_normal",
            grid_import_kwh=1.64,  # 0.01 less than legacy's 1.65
        )
        
        mismatch = comparator._compare_slot(0, legacy_slot_charge, opt_decision)
        
        assert mismatch is None


# ---------------------------------------------------------------------------
# Test Significance Scoring
# ---------------------------------------------------------------------------


class TestSignificanceScoring:
    """Tests for significance-based ranking."""

    def test_significance_score_higher_for_boost(self, comparator):
        """Boost action should have higher significance than hold."""
        boost_mismatch = SlotMismatch(
            slot_index=0,
            timestamp_iso="2025-01-01T00:00:00Z",
            slot_interval_minutes=30,
            mismatch_type=MismatchType.ACTION_MISMATCH,
            legacy_action="charge_grid_boost",
            optimizer_action="hold",
            legacy_import_kwh=2.5,
            optimizer_import_kwh=0.0,
            legacy_net_cost=0.25,
            optimizer_net_cost=0.0,
        )
        
        hold_mismatch = SlotMismatch(
            slot_index=1,
            timestamp_iso="2025-01-01T00:30:00Z",
            slot_interval_minutes=30,
            mismatch_type=MismatchType.ACTION_MISMATCH,
            legacy_action="hold",
            optimizer_action="hold",
            legacy_import_kwh=0.0,
            optimizer_import_kwh=0.0,
            legacy_net_cost=0.0,
            optimizer_net_cost=0.0,
        )
        
        boost_score = comparator.compute_significance_score(boost_mismatch)
        hold_score = comparator.compute_significance_score(hold_mismatch)
        
        assert boost_score > hold_score

    def test_rank_mismatches_by_significance(self, comparator):
        """Test that mismatches are ranked by descending significance."""
        mismatches = [
            SlotMismatch(
                slot_index=0,
                timestamp_iso="2025-01-01T00:00:00Z",
                slot_interval_minutes=30,
                mismatch_type=MismatchType.ACTION_MISMATCH,
                legacy_action="hold",
                optimizer_action="hold",
                legacy_net_cost=0.0,
                optimizer_net_cost=0.0,
            ),
            SlotMismatch(
                slot_index=1,
                timestamp_iso="2025-01-01T00:30:00Z",
                slot_interval_minutes=30,
                mismatch_type=MismatchType.ACTION_MISMATCH,
                legacy_action="charge_grid_boost",
                optimizer_action="hold",
                legacy_net_cost=1.0,  # High cost impact
                optimizer_net_cost=0.0,
            ),
            SlotMismatch(
                slot_index=2,
                timestamp_iso="2025-01-01T01:00:00Z",
                slot_interval_minutes=30,
                mismatch_type=MismatchType.ACTION_MISMATCH,
                legacy_action="export_proactive",
                optimizer_action="hold",
                legacy_net_cost=0.5,
                optimizer_net_cost=0.0,
            ),
        ]
        
        ranked = comparator.rank_mismatches(mismatches)
        
        # Should be sorted by descending significance
        assert ranked[0].slot_index == 1  # Boost has highest significance
        assert ranked[1].slot_index == 2  # Export is next
        assert ranked[2].slot_index == 0  # Hold is lowest


# ---------------------------------------------------------------------------
# Test Summary Rollup
# ---------------------------------------------------------------------------


class TestSummaryRollup:
    """Tests for summary rollup computation."""

    def test_empty_mismatches(self, comparator):
        """Test summary with no mismatches."""
        summary = comparator.compute_summary_rollup([])
        
        assert summary["total_mismatches"] == 0
        assert summary["total_cost_impact"] == 0.0
        assert summary["by_type"] == {}
        assert summary["most_significant_type"] is None
        assert summary["avg_significance_score"] == 0.0

    def test_summary_with_mismatches(self, comparator):
        """Test summary with multiple mismatches."""
        mismatches = [
            SlotMismatch(
                slot_index=0,
                timestamp_iso="2025-01-01T00:00:00Z",
                slot_interval_minutes=30,
                mismatch_type=MismatchType.ACTION_MISMATCH,
                legacy_action="charge_grid_normal",
                optimizer_action="hold",
                legacy_net_cost=0.5,
                optimizer_net_cost=0.0,
            ),
            SlotMismatch(
                slot_index=1,
                timestamp_iso="2025-01-01T00:30:00Z",
                slot_interval_minutes=30,
                mismatch_type=MismatchType.IMPORT_QUANTITY_MISMATCH,
                legacy_action="charge_grid_normal",
                optimizer_action="charge_grid_normal",
                legacy_net_cost=0.3,
                optimizer_net_cost=0.2,
            ),
            SlotMismatch(
                slot_index=2,
                timestamp_iso="2025-01-01T01:00:00Z",
                slot_interval_minutes=30,
                mismatch_type=MismatchType.ACTION_MISMATCH,
                legacy_action="export_proactive",
                optimizer_action="hold",
                legacy_net_cost=0.4,
                optimizer_net_cost=0.0,
            ),
        ]
        
        summary = comparator.compute_summary_rollup(mismatches)
        
        assert summary["total_mismatches"] == 3
        assert summary["by_type"]["ACTION_MISMATCH"] == 2
        assert summary["by_type"]["IMPORT_QUANTITY_MISMATCH"] == 1
        assert summary["most_significant_type"] == "ACTION_MISMATCH"
        assert summary["avg_significance_score"] > 0


# ---------------------------------------------------------------------------
# Test Full Comparison
# ---------------------------------------------------------------------------


class TestFullComparison:
    """Tests for full comparison cycle."""

    def test_compare_returns_record(self, comparator, legacy_slot_hold, legacy_slot_charge):
        """Test that compare returns a PlannerComparisonRecord."""
        legacy_slots = [legacy_slot_hold, legacy_slot_charge]
        optimizer_decisions = [
            MockOptimizerDecision(action="hold"),
            MockOptimizerDecision(action="charge_grid_normal", grid_import_kwh=1.65),
        ]
        
        record = comparator.compare(
            cycle_id="test-cycle-001",
            cycle_timestamp_iso="2025-01-01T00:00:00Z",
            legacy_slots=legacy_slots,
            optimizer_decisions=optimizer_decisions,
        )
        
        assert isinstance(record, PlannerComparisonRecord)
        assert record.cycle_id == "test-cycle-001"
        assert record.total_slots == 2
        assert record.aligned_slots == 2
        assert record.comparison_succeeded is True

    def test_compare_includes_timing(self, comparator, legacy_slot_hold):
        """Test that compare includes timing information."""
        record = comparator.compare(
            cycle_id="test-cycle-002",
            cycle_timestamp_iso="2025-01-01T00:00:00Z",
            legacy_slots=[legacy_slot_hold],
            optimizer_decisions=[MockOptimizerDecision(action="hold")],
        )
        
        assert record.comparison_time_ms >= 0

    def test_compare_includes_summary(self, comparator, legacy_slot_hold, legacy_slot_charge):
        """Test that compare includes summary rollup."""
        legacy_slots = [legacy_slot_hold, legacy_slot_charge]
        optimizer_decisions = [
            MockOptimizerDecision(action="charge_grid_normal", grid_import_kwh=1.0),  # Mismatch
            MockOptimizerDecision(action="hold"),  # Mismatch
        ]
        
        record = comparator.compare(
            cycle_id="test-cycle-003",
            cycle_timestamp_iso="2025-01-01T00:00:00Z",
            legacy_slots=legacy_slots,
            optimizer_decisions=optimizer_decisions,
        )
        
        assert "total_mismatches" in record.summary
        assert record.summary["total_mismatches"] == 2

    def test_compare_limits_top_mismatches(self, comparator):
        """Test that compare limits top_mismatches to TOP_N_MISMATCHES."""
        # Create more mismatches than TOP_N_MISMATCHES
        legacy_slots = []
        optimizer_decisions = []
        for i in range(10):
            legacy_slots.append({
                "timestamp_iso": f"2025-01-01T{i:02d}:00:00Z",
                "slot_interval_minutes": 30,
                "grid_charge": True,
                "grid_charge_boost": False,
                "proactive_export": False,
                "grid_import_kwh": 1.65,
                "buy_price": 0.15,
                "sell_price": 0.08,
            })
            optimizer_decisions.append(MockOptimizerDecision(action="hold"))
        
        record = comparator.compare(
            cycle_id="test-cycle-004",
            cycle_timestamp_iso="2025-01-01T00:00:00Z",
            legacy_slots=legacy_slots,
            optimizer_decisions=optimizer_decisions,
        )
        
        assert len(record.top_mismatches) <= PlannerComparator.TOP_N_MISMATCHES

    def test_to_dict_serialization(self, comparator, legacy_slot_hold):
        """Test that to_dict properly serializes the record."""
        record = comparator.compare(
            cycle_id="test-cycle-005",
            cycle_timestamp_iso="2025-01-01T00:00:00Z",
            legacy_slots=[legacy_slot_hold],
            optimizer_decisions=[MockOptimizerDecision(action="hold")],
        )
        
        data = record.to_dict()
        
        assert "cycle_id" in data
        assert "summary" in data
        assert "comparison_time_ms" in data
        assert data["cycle_id"] == "test-cycle-005"

    def test_target_attainment_mismatch_is_recorded(self, comparator):
        """Target-attainment divergence should create TARGET_ATTAINMENT_MISMATCH."""
        legacy_slots = [
            {
                "timestamp_iso": "2025-01-01T18:00:00Z",
                "slot_interval_minutes": 30,
                "is_demand_window_entry": True,
                "grid_charge": False,
                "grid_charge_boost": False,
                "proactive_export": False,
                "grid_import_kwh": 0.0,
                "grid_export_kwh": 0.0,
                "buy_price": 0.25,
                "sell_price": 0.08,
                "predicted_soc": 78.0,
            }
        ]
        optimizer_decisions = [
            MockOptimizerDecision(
                action="hold",
                predicted_soc_pct=82.0,
            )
        ]

        record = comparator.compare(
            cycle_id="test-target-mismatch",
            cycle_timestamp_iso="2025-01-01T18:00:00Z",
            legacy_slots=legacy_slots,
            optimizer_decisions=optimizer_decisions,
            demand_window_target_soc_pct=80.0,
        )

        assert record.legacy_meets_dw_target is False
        assert record.optimizer_meets_dw_target is True
        assert (
            record.mismatch_by_type.get(MismatchType.TARGET_ATTAINMENT_MISMATCH.value, 0)
            == 1
        )

    def test_target_attainment_agreement_has_no_extra_mismatch(self, comparator):
        """When both planners meet target, no synthetic target mismatch is added."""
        legacy_slots = [
            {
                "timestamp_iso": "2025-01-01T18:00:00Z",
                "slot_interval_minutes": 30,
                "is_demand_window_entry": True,
                "grid_charge": False,
                "grid_charge_boost": False,
                "proactive_export": False,
                "grid_import_kwh": 0.0,
                "grid_export_kwh": 0.0,
                "buy_price": 0.25,
                "sell_price": 0.08,
                "predicted_soc": 81.0,
            }
        ]
        optimizer_decisions = [
            MockOptimizerDecision(
                action="hold",
                predicted_soc_pct=82.0,
            )
        ]

        record = comparator.compare(
            cycle_id="test-target-agreement",
            cycle_timestamp_iso="2025-01-01T18:00:00Z",
            legacy_slots=legacy_slots,
            optimizer_decisions=optimizer_decisions,
            demand_window_target_soc_pct=80.0,
        )

        assert record.legacy_meets_dw_target is True
        assert record.optimizer_meets_dw_target is True
        assert (
            record.mismatch_by_type.get(MismatchType.TARGET_ATTAINMENT_MISMATCH.value, 0)
            == 0
        )


# ---------------------------------------------------------------------------
# Test Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_inputs(self, comparator):
        """Test comparison with empty inputs."""
        record = comparator.compare(
            cycle_id="test-empty",
            cycle_timestamp_iso="2025-01-01T00:00:00Z",
            legacy_slots=[],
            optimizer_decisions=[],
        )
        
        assert record.total_slots == 0
        assert record.mismatch_count == 0
        assert record.comparison_succeeded is True

    def test_mismatched_lengths(self, comparator, legacy_slot_hold):
        """Test comparison with different numbers of slots."""
        record = comparator.compare(
            cycle_id="test-length-mismatch",
            cycle_timestamp_iso="2025-01-01T00:00:00Z",
            legacy_slots=[legacy_slot_hold, legacy_slot_hold, legacy_slot_hold],
            optimizer_decisions=[MockOptimizerDecision(action="hold")],
        )
        
        assert record.total_slots == 3
        assert record.aligned_slots == 1

    def test_missing_fields_in_legacy_slot(self, comparator):
        """Test comparison with missing fields in legacy slot."""
        legacy_slot = {"timestamp_iso": "2025-01-01T00:00:00Z"}  # Missing most fields
        
        record = comparator.compare(
            cycle_id="test-missing-fields",
            cycle_timestamp_iso="2025-01-01T00:00:00Z",
            legacy_slots=[legacy_slot],
            optimizer_decisions=[MockOptimizerDecision(action="hold")],
        )
        
        assert record.comparison_succeeded is True
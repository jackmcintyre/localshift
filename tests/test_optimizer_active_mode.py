"""
Tests for Phase 6 (#448) — Optimizer safety gate and apply-path mapping.

Tests cover:
- OptimizerSafetyGate admission checks (simplified: no mode/enabled/cooldown checks)
- Action → BatteryMode mapping (no fallback_to_legacy field)
- Safety gate failure defaults to SELF_CONSUMPTION
"""

from datetime import datetime

import pytest

from custom_components.localshift.computation_engine_lib.optimizer_dp import (
    ObjectiveTerms,
    OptimizerConfig,
    OptimizerResult,
    PlannedSlotDecision,
    PlannerAction,
    PlannerReasonCode,
)
from custom_components.localshift.computation_engine_lib.optimizer_runner import (
    OptimizerSafetyGate,
    _derive_runtime_apply_plan,
)


class MockData:
    """Mock CoordinatorData for testing."""

    def __init__(
        self,
        optimizer_summary: dict | None = None,
    ):
        self.optimizer_summary = optimizer_summary or {}


# ---------------------------------------------------------------------------
# Test OptimizerSafetyGate
# ---------------------------------------------------------------------------


class TestOptimizerSafetyGate:
    """Tests for OptimizerSafetyGate admission checks."""

    def test_allows_when_all_checks_pass(self):
        """Gate should allow when all checks pass."""
        config_options = {}
        data = MockData(
            optimizer_summary={
                "enabled": True,
                "success": True,
                "cycle_timestamp_iso": datetime.now().isoformat(),
                "alignment_valid": True,
            },
        )
        decision = PlannedSlotDecision(
            slot_index=0,
            timestamp_iso="2025-01-01T00:00:00",
            slot_interval_minutes=30,
            action=PlannerAction.HOLD,
            reason_code=PlannerReasonCode.IDLE,
            objective_terms=ObjectiveTerms(),
            predicted_soc_pct=50.0,
            grid_import_kwh=0.0,
            grid_export_kwh=0.0,
        )
        optimizer_result = OptimizerResult(
            success=True,
            total_slots=48,
            decisions=[decision],
        )
        alignment = {"valid": True, "issues": []}

        gate = OptimizerSafetyGate(config_options)
        result = gate.check_admission(data, optimizer_result, alignment)

        assert result.allowed is True
        assert result.block_reason is None

    def test_blocks_on_none_result(self):
        """Gate should block when optimizer result is None."""
        gate = OptimizerSafetyGate({})
        result = gate.check_admission(MockData(), None, {"valid": True})

        assert result.allowed is False
        assert result.block_reason == "optimizer_result_none"

    def test_blocks_on_failed_solve(self):
        """Gate should block when optimizer solve failed."""
        optimizer_result = OptimizerResult(
            success=False,
            error_message="test error",
        )

        gate = OptimizerSafetyGate({})
        result = gate.check_admission(MockData(), optimizer_result, None)

        assert result.allowed is False
        assert result.block_reason == "optimizer_solve_failed"

    def test_blocks_on_slot_alignment_failure(self):
        """Gate should block when slot alignment is invalid."""
        optimizer_result = OptimizerResult(success=True, decisions=[])
        alignment = {"valid": False, "issues": ["slot_count_mismatch"]}

        gate = OptimizerSafetyGate({})
        result = gate.check_admission(MockData(), optimizer_result, alignment)

        assert result.allowed is False
        assert result.block_reason == "slot_alignment_invalid"

    def test_blocks_on_no_decisions(self):
        """Gate should block when there are no decisions."""
        optimizer_result = OptimizerResult(success=True, decisions=[])

        gate = OptimizerSafetyGate({})
        result = gate.check_admission(
            MockData(),
            optimizer_result,
            {"valid": True},
        )

        assert result.allowed is False
        assert result.block_reason == "no_decisions_available"

    def test_blocks_on_stale_forecast(self):
        """Gate should block when forecast is stale."""
        from custom_components.localshift.const import (
            OPTIMIZER_FORECAST_FRESHNESS_MINUTES,
        )

        # Set timestamp way in the past
        old_ts = "2020-01-01T00:00:00+00:00"
        data = MockData(optimizer_summary={"cycle_timestamp_iso": old_ts})
        decision = PlannedSlotDecision(
            slot_index=0,
            timestamp_iso="2025-01-01T00:00:00",
            slot_interval_minutes=30,
            action=PlannerAction.HOLD,
            reason_code=PlannerReasonCode.IDLE,
            objective_terms=ObjectiveTerms(),
            predicted_soc_pct=50.0,
            grid_import_kwh=0.0,
            grid_export_kwh=0.0,
        )
        optimizer_result = OptimizerResult(success=True, decisions=[decision])

        gate = OptimizerSafetyGate({})
        result = gate.check_admission(data, optimizer_result, {"valid": True})

        assert result.allowed is False
        assert result.block_reason == "forecast_stale"
        assert result.details["age_minutes"] > OPTIMIZER_FORECAST_FRESHNESS_MINUTES


# ---------------------------------------------------------------------------
# Test Apply-Path Mapping (no fallback_to_legacy field)
# ---------------------------------------------------------------------------


class TestApplyPathMapping:
    """Tests for _derive_runtime_apply_plan mapping."""

    def test_hold_maps_to_hold_mode(self):
        """HOLD action should map to SELF_CONSUMPTION when hold_soc is False.

        The HOLD action is now differentiated based on hold_soc flag.
        See issue #559 / #591.
        """
        config = OptimizerConfig(demand_window_target_soc_pct=80.0)
        decisions = [
            {
                "action": "hold",
                "slot_index": 0,
                "timestamp_iso": "2025-01-01T00:00:00",
                "slot_interval_minutes": 30,
            }
        ]

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["battery_mode"] == "self_consumption"
        assert "fallback_to_legacy" not in result

    def test_charge_grid_normal_maps_to_grid_charging(self):
        """CHARGE_GRID_NORMAL action should map to GRID_CHARGING mode."""
        config = OptimizerConfig(demand_window_target_soc_pct=80.0)
        decisions = [{"action": "charge_grid_normal", "slot_index": 0}]

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["battery_mode"] == "grid_charging"
        assert result["target_soc"] == 80.0
        assert "fallback_to_legacy" not in result

    def test_charge_grid_boost_maps_to_boost_charging(self):
        """CHARGE_GRID_BOOST action should map to BOOST_CHARGING mode."""
        config = OptimizerConfig(demand_window_target_soc_pct=100.0)
        decisions = [{"action": "charge_grid_boost", "slot_index": 0}]

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["battery_mode"] == "boost_charging"
        assert result["target_soc"] == 100.0
        assert "fallback_to_legacy" not in result

    def test_export_proactive_maps_to_proactive_export(self):
        """EXPORT_PROACTIVE action should map to PROACTIVE_EXPORT mode."""
        config = OptimizerConfig()
        decisions = [{"action": "export_proactive", "slot_index": 0}]

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["battery_mode"] == "proactive_export"
        assert "fallback_to_legacy" not in result

    def test_out_of_bounds_index_defaults_to_self_consumption(self):
        """Out of bounds index should default to SELF_CONSUMPTION, not fallback."""
        config = OptimizerConfig()
        decisions = [{"action": "hold", "slot_index": 0}]

        result = _derive_runtime_apply_plan(decisions, 100, config)

        assert result["battery_mode"] == "self_consumption"
        assert result["reason"] == "no_valid_decision_for_current_slot"
        assert "fallback_to_legacy" not in result

    def test_empty_decisions_defaults_to_self_consumption(self):
        """Empty decisions list should default to SELF_CONSUMPTION."""
        config = OptimizerConfig()

        result = _derive_runtime_apply_plan([], 0, config)

        assert result["battery_mode"] == "self_consumption"
        assert result["reason"] == "no_valid_decision_for_current_slot"
        assert "fallback_to_legacy" not in result

    def test_unknown_action_defaults_to_self_consumption(self):
        """Unknown action should default to SELF_CONSUMPTION (not raise)."""
        config = OptimizerConfig()
        decisions = [{"action": "completely_unknown_action", "slot_index": 0}]

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["battery_mode"] == "self_consumption"
        assert "unknown_action_completely_unknown_action" in result["reason"]
        assert "fallback_to_legacy" not in result


# ---------------------------------------------------------------------------
# Test safety gate failure defaults to SELF_CONSUMPTION (issue #448 AC)
# ---------------------------------------------------------------------------


class TestSafetyGateFailureDefaultsToSelfConsumption:
    """Acceptance criterion: gate failure → SELF_CONSUMPTION, no fallback logic."""

    def test_gate_failure_returns_blocked_not_allowed(self):
        """Gate result is not allowed when optimizer solve failed."""
        optimizer_result = OptimizerResult(
            success=False,
            error_message="solve_failed",
        )
        gate = OptimizerSafetyGate({})
        result = gate.check_admission(MockData(), optimizer_result, None)

        assert result.allowed is False
        assert result.block_reason is not None

    def test_no_fallback_count_field_on_coordinator_data(self):
        """CoordinatorData must not have optimizer_fallback_count field (Phase 6)."""
        from custom_components.localshift.coordinator_data import CoordinatorData

        data = CoordinatorData()
        assert not hasattr(data, "optimizer_fallback_count"), (
            "optimizer_fallback_count should have been removed in Phase 6 (#448)"
        )

    def test_no_optimizer_runtime_mode_field_on_coordinator_data(self):
        """CoordinatorData must not have optimizer_runtime_mode field (Phase 6)."""
        from custom_components.localshift.coordinator_data import CoordinatorData

        data = CoordinatorData()
        assert not hasattr(data, "optimizer_runtime_mode"), (
            "optimizer_runtime_mode should have been removed in Phase 6 (#448)"
        )

    def test_optimizer_runner_class_exists(self):
        """OptimizerRunner (renamed) should be importable from optimizer_runner."""
        from custom_components.localshift.computation_engine_lib.optimizer_runner import (
            OptimizerSafetyGate,
            _derive_runtime_apply_plan,
            run_optimizer,
        )

        assert OptimizerSafetyGate is not None
        assert _derive_runtime_apply_plan is not None
        assert run_optimizer is not None

    def test_optimizer_shadow_runner_module_does_not_exist(self):
        """optimizer_shadow_runner module must not exist after Phase 6."""
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(
                "custom_components.localshift.computation_engine_lib.optimizer_shadow_runner"
            )

    def test_planner_comparator_module_does_not_exist(self):
        """planner_comparator module must not exist after Phase 6."""
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(
                "custom_components.localshift.computation_engine_lib.planner_comparator"
            )

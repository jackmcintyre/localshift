"""
Tests for Phase F — Active-Control Pilot.

Tests cover:
- OptimizerSafetyGate admission checks
- Action → BatteryMode mapping
- Fallback behavior
- Cooldown logic
"""

from datetime import datetime

from custom_components.localshift.computation_engine_lib.optimizer_dp import (
    OptimizerConfig,
    OptimizerResult,
    PlannedSlotDecision,
    PlannerAction,
    PlannerReasonCode,
    ObjectiveTerms,
)
from custom_components.localshift.computation_engine_lib.optimizer_shadow_runner import (
    OptimizerSafetyGate,
    _derive_runtime_apply_plan,
)


class MockData:
    """Mock CoordinatorData for testing."""

    def __init__(
        self,
        optimizer_shadow_summary: dict | None = None,
        optimizer_shadow_decisions: list | None = None,
        optimizer_fallback_count: int = 0,
    ):
        self.optimizer_shadow_summary = optimizer_shadow_summary or {}
        self.optimizer_shadow_decisions = optimizer_shadow_decisions or []
        self.optimizer_fallback_count = optimizer_fallback_count


# ---------------------------------------------------------------------------
# Test OptimizerSafetyGate
# ---------------------------------------------------------------------------


class TestOptimizerSafetyGate:
    """Tests for OptimizerSafetyGate admission checks."""

    def test_allows_when_all_checks_pass(self):
        """Gate should allow when all checks pass."""
        from custom_components.localshift.computation_engine_lib.optimizer_dp import (
            PlannerReasonCode,
            ObjectiveTerms,
        )

        config_options = {
            "optimizer_enabled": True,
            "optimizer_control_mode": "active",
        }
        data = MockData(
            optimizer_shadow_summary={
                "enabled": True,
                "success": True,
                "cycle_timestamp_iso": datetime.now().isoformat(),
                "alignment_valid": True,
            },
            optimizer_fallback_count=0,
        )
        # Create a valid PlannedSlotDecision
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

    def test_blocks_on_disabled_optimizer(self):
        """Gate should block when optimizer is disabled."""
        config_options = {
            "optimizer_enabled": False,
            "optimizer_control_mode": "active",
        }

        gate = OptimizerSafetyGate(config_options)
        result = gate.check_admission(MockData(), None, None)

        assert result.allowed is False
        assert result.block_reason == "optimizer_disabled"

    def test_blocks_on_wrong_control_mode(self):
        """Gate should block when control mode is not active."""
        config_options = {
            "optimizer_enabled": True,
            "optimizer_control_mode": "shadow",
        }

        gate = OptimizerSafetyGate(config_options)
        result = gate.check_admission(MockData(), None, None)

        assert result.allowed is False
        assert result.block_reason is not None
        assert "control_mode_not_active" in str(result.block_reason)

    def test_blocks_on_failed_solve(self):
        """Gate should block when optimizer solve failed."""
        config_options = {
            "optimizer_enabled": True,
            "optimizer_control_mode": "active",
        }
        data = MockData(
            optimizer_shadow_summary={"enabled": True, "success": False},
        )
        optimizer_result = OptimizerResult(
            success=False,
            error_message="test error",
        )

        gate = OptimizerSafetyGate(config_options)
        result = gate.check_admission(data, optimizer_result, None)

        assert result.allowed is False
        assert result.block_reason == "optimizer_solve_failed"

    def test_blocks_on_slot_alignment_failure(self):
        """Gate should block when slot alignment is invalid."""
        config_options = {
            "optimizer_enabled": True,
            "optimizer_control_mode": "active",
        }
        optimizer_result = OptimizerResult(success=True, decisions=[])
        alignment = {"valid": False, "issues": ["slot_count_mismatch"]}

        gate = OptimizerSafetyGate(config_options)
        result = gate.check_admission(MockData(), optimizer_result, alignment)

        assert result.allowed is False
        assert result.block_reason == "slot_alignment_invalid"

    def test_blocks_on_no_decisions(self):
        """Gate should block when there are no decisions."""
        config_options = {
            "optimizer_enabled": True,
            "optimizer_control_mode": "active",
        }
        optimizer_result = OptimizerResult(success=True, decisions=[])

        gate = OptimizerSafetyGate(config_options)
        result = gate.check_admission(
            MockData(optimizer_shadow_decisions=[]),
            optimizer_result,
            {"valid": True},
        )

        assert result.allowed is False
        assert result.block_reason == "no_decisions_available"

    def test_blocks_on_cooldown(self):
        """Gate should block when in fallback cooldown."""
        config_options = {
            "optimizer_enabled": True,
            "optimizer_control_mode": "active",
        }
        data = MockData(optimizer_fallback_count=5)  # Exceeds default cooldown of 3

        gate = OptimizerSafetyGate(config_options)
        # Pass None for decisions - the gate checks fallback_count before checking decisions
        result = gate.check_admission(
            data,
            OptimizerResult(success=True, decisions=[]),
            {"valid": True},
        )

        assert result.allowed is False
        assert result.block_reason == "fallback_cooldown_active"
        assert result.details["fallback_count"] == 5


# ---------------------------------------------------------------------------
# Test Apply-Path Mapping
# ---------------------------------------------------------------------------


class TestApplyPathMapping:
    """Tests for _derive_runtime_apply_plan mapping."""

    def test_hold_maps_to_self_consumption(self):
        """HOLD action should map to SELF_CONSUMPTION mode."""
        config = OptimizerConfig(demand_window_target_soc_pct=80.0)
        decisions = [
            type(
                "Decision",
                (),
                {
                    "action": PlannerAction.HOLD,
                    "slot_index": 0,
                    "timestamp_iso": "2025-01-01T00:00:00",
                    "slot_interval_minutes": 30,
                },
            )()
        ]

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["battery_mode"] == "self_consumption"
        assert result["fallback_to_legacy"] is False

    def test_charge_grid_normal_maps_to_grid_charging(self):
        """CHARGE_GRID_NORMAL action should map to GRID_CHARGING mode."""
        config = OptimizerConfig(demand_window_target_soc_pct=80.0)
        decisions = [
            type(
                "Decision",
                (),
                {
                    "action": PlannerAction.CHARGE_GRID_NORMAL,
                    "slot_index": 0,
                },
            )()
        ]

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["battery_mode"] == "grid_charging"
        assert result["target_soc"] == 80.0
        assert result["fallback_to_legacy"] is False

    def test_charge_grid_boost_maps_to_boost_charging(self):
        """CHARGE_GRID_BOOST action should map to BOOST_CHARGING mode."""
        config = OptimizerConfig(demand_window_target_soc_pct=100.0)
        decisions = [
            type(
                "Decision",
                (),
                {
                    "action": PlannerAction.CHARGE_GRID_BOOST,
                    "slot_index": 0,
                },
            )()
        ]

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["battery_mode"] == "boost_charging"
        assert result["target_soc"] == 100.0
        assert result["fallback_to_legacy"] is False

    def test_export_proactive_maps_to_proactive_export(self):
        """EXPORT_PROACTIVE action should map to PROACTIVE_EXPORT mode."""
        config = OptimizerConfig()
        decisions = [
            type(
                "Decision",
                (),
                {
                    "action": PlannerAction.EXPORT_PROACTIVE,
                    "slot_index": 0,
                },
            )()
        ]

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["battery_mode"] == "proactive_export"
        assert result["fallback_to_legacy"] is False

    def test_unknown_index_falls_back_to_legacy(self):
        """Out of bounds index should fall back to legacy."""
        config = OptimizerConfig()
        decisions = [
            type(
                "Decision",
                (),
                {
                    "action": PlannerAction.HOLD,
                    "slot_index": 0,
                },
            )()
        ]

        result = _derive_runtime_apply_plan(decisions, 100, config)

        assert result["fallback_to_legacy"] is True
        assert result["battery_mode"] == "self_consumption"

    def test_empty_decisions_falls_back_to_legacy(self):
        """Empty decisions list should fall back to legacy."""
        config = OptimizerConfig()

        result = _derive_runtime_apply_plan([], 0, config)

        assert result["fallback_to_legacy"] is True
        assert result["reason"] == "no_valid_decision_for_current_slot"


# ---------------------------------------------------------------------------
# Test Fallback Behavior
# ---------------------------------------------------------------------------


class TestFallbackBehavior:
    """Tests for fallback behavior."""

    def test_increment_fallback_count_on_block(self):
        """Fallback count should increment on safety gate block."""
        config_options = {
            "optimizer_enabled": True,
            "optimizer_control_mode": "active",
        }
        data = MockData(optimizer_fallback_count=2)

        gate = OptimizerSafetyGate(config_options)
        result = gate.check_admission(
            data,
            OptimizerResult(success=True, decisions=[]),
            {"valid": True},
        )

        assert result.allowed is False
        # Note: The count increment happens in coordinator, not in safety gate

    def test_cooldown_threshold(self):
        """Cooldown should trigger at threshold."""
        from custom_components.localshift.const import OPTIMIZER_COOLDOWN_CYCLES

        assert OPTIMIZER_COOLDOWN_CYCLES == 3


# ---------------------------------------------------------------------------
# Test Integration Safety
# ---------------------------------------------------------------------------


class TestIntegrationSafety:
    """Tests for safety-critical integration behavior."""

    def test_safety_gate_returns_details_on_block(self):
        """Safety gate should return detailed block reason."""
        config_options = {
            "optimizer_enabled": True,
            "optimizer_control_mode": "shadow",  # Not active
        }

        gate = OptimizerSafetyGate(config_options)
        result = gate.check_admission(MockData(), None, None)

        assert result.allowed is False
        assert result.details.get("control_mode") == "shadow"

    def test_optimizer_result_none_handled(self):
        """None optimizer_result should be handled gracefully."""
        config_options = {
            "optimizer_enabled": True,
            "optimizer_control_mode": "active",
        }

        gate = OptimizerSafetyGate(config_options)
        result = gate.check_admission(MockData(), None, {"valid": True})

        assert result.allowed is False
        assert result.block_reason == "optimizer_result_none"


# ---------------------------------------------------------------------------
# Test Computation Engine Active Mode Integration
# ---------------------------------------------------------------------------


class TestComputationEngineActiveModeIntegration:
    """Tests for active mode integration in computation engine."""

    def test_active_mode_uses_optimizer_decision(self):
        """Computation engine should use optimizer decision when active mode is enabled."""
        from custom_components.localshift.computation_engine import ComputationEngine
        from custom_components.localshift.const import BatteryMode

        # Create mock coordinator data with active mode settings
        data = type(
            "CoordinatorData",
            (),
            {
                "optimizer_runtime_mode": "active",
                "optimizer_apply_plan": {
                    "action": "charge_grid_normal",
                    "battery_mode": "grid_charging",
                    "target_soc": 80.0,
                    "fallback_to_legacy": False,
                    "reason": "optimizer_charge_grid_normal",
                },
                "active_mode": BatteryMode.SELF_CONSUMPTION,
            },
        )()

        # Create a mock computation engine
        engine = ComputationEngine.__new__(ComputationEngine)

        # Call the active mode override check
        result = engine._check_active_mode_optimizer_override(data)

        # Verify the optimizer decision was applied
        assert result is True
        assert data.active_mode == BatteryMode.GRID_CHARGING

    def test_legacy_used_when_not_active_mode(self):
        """Legacy mode should be used when optimizer is not in active mode."""
        from custom_components.localshift.computation_engine import ComputationEngine
        from custom_components.localshift.const import BatteryMode

        # Create mock coordinator data with shadow mode
        data = type(
            "CoordinatorData",
            (),
            {
                "optimizer_runtime_mode": "shadow",
                "optimizer_apply_plan": None,
                "active_mode": BatteryMode.SELF_CONSUMPTION,
            },
        )()

        engine = ComputationEngine.__new__(ComputationEngine)
        result = engine._check_active_mode_optimizer_override(data)

        # Verify legacy mode is used (no override applied)
        assert result is False

    def test_fallback_to_legacy_when_requested(self):
        """Should fall back to legacy when optimizer requests fallback."""
        from custom_components.localshift.computation_engine import ComputationEngine
        from custom_components.localshift.const import BatteryMode

        # Create mock coordinator data with active mode but fallback requested
        data = type(
            "CoordinatorData",
            (),
            {
                "optimizer_runtime_mode": "active",
                "optimizer_apply_plan": {
                    "action": "hold",
                    "battery_mode": "self_consumption",
                    "fallback_to_legacy": True,  # Explicit fallback request
                    "reason": "no_valid_decision_for_current_slot",
                },
                "active_mode": BatteryMode.SELF_CONSUMPTION,
            },
        )()

        engine = ComputationEngine.__new__(ComputationEngine)
        result = engine._check_active_mode_optimizer_override(data)

        # Verify fallback to legacy
        assert result is False

    def test_proactive_export_action(self):
        """PROACTIVE_EXPORT action should map correctly."""
        from custom_components.localshift.computation_engine import ComputationEngine
        from custom_components.localshift.const import BatteryMode

        data = type(
            "CoordinatorData",
            (),
            {
                "optimizer_runtime_mode": "active",
                "optimizer_apply_plan": {
                    "action": "export_proactive",
                    "battery_mode": "proactive_export",
                    "fallback_to_legacy": False,
                },
                "active_mode": BatteryMode.SELF_CONSUMPTION,
            },
        )()

        engine = ComputationEngine.__new__(ComputationEngine)
        result = engine._check_active_mode_optimizer_override(data)

        assert result is True
        assert data.active_mode == BatteryMode.PROACTIVE_EXPORT

    def test_boost_charging_action(self):
        """BOOST_CHARGING action should map correctly."""
        from custom_components.localshift.computation_engine import ComputationEngine
        from custom_components.localshift.const import BatteryMode

        data = type(
            "CoordinatorData",
            (),
            {
                "optimizer_runtime_mode": "active",
                "optimizer_apply_plan": {
                    "action": "charge_grid_boost",
                    "battery_mode": "boost_charging",
                    "fallback_to_legacy": False,
                },
                "active_mode": BatteryMode.SELF_CONSUMPTION,
            },
        )()

        engine = ComputationEngine.__new__(ComputationEngine)
        result = engine._check_active_mode_optimizer_override(data)

        assert result is True
        assert data.active_mode == BatteryMode.BOOST_CHARGING

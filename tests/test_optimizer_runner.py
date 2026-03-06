"""Tests for _derive_runtime_apply_plan mapping PlannerAction to BatteryMode.

This tests the fix for the HOLD overloading issue where HOLD was always mapped
to BatteryMode.HOLD, causing overnight grid imports instead of battery discharge.
"""

from __future__ import annotations

import pytest

from custom_components.localshift.computation_engine_lib.optimizer_dp import (
    OptimizerConfig,
)
from custom_components.localshift.computation_engine_lib.optimizer_runner import (
    _derive_runtime_apply_plan,
)
from custom_components.localshift.const import BatteryMode


class TestDeriveRuntimeApplyPlan:
    """Test cases for _derive_runtime_apply_plan action-to-mode mapping."""

    def test_hold_maps_to_self_consumption_when_hold_soc_false(self):
        """HOLD action should map to SELF_CONSUMPTION when hold_soc is False."""
        config = OptimizerConfig(hold_soc=False)
        decisions = [{"action": "hold"}]

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["action"] == "hold"
        assert result["battery_mode"] == BatteryMode.SELF_CONSUMPTION.value
        assert result["reason"] == "optimizer_self_consumption"

    def test_hold_maps_to_hold_when_hold_soc_true(self):
        """HOLD action should map to HOLD when hold_soc is True (strict preserve)."""
        config = OptimizerConfig(hold_soc=True)
        decisions = [{"action": "hold"}]

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["action"] == "hold"
        assert result["battery_mode"] == BatteryMode.HOLD.value
        assert result["reason"] == "optimizer_hold_strict"

    def test_charge_grid_normal_maps_correctly(self):
        """CHARGE_GRID_NORMAL should map to GRID_CHARGING with target_soc."""
        config = OptimizerConfig(demand_window_target_soc_pct=80.0)
        decisions = [{"action": "charge_grid_normal"}]

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["action"] == "charge_grid_normal"
        assert result["battery_mode"] == BatteryMode.GRID_CHARGING.value
        assert result["target_soc"] == 80.0
        assert result["reason"] == "optimizer_charge_grid_normal"

    def test_charge_grid_boost_maps_correctly(self):
        """CHARGE_GRID_BOOST should map to BOOST_CHARGING with target_soc."""
        config = OptimizerConfig(demand_window_target_soc_pct=95.0)
        decisions = [{"action": "charge_grid_boost"}]

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["action"] == "charge_grid_boost"
        assert result["battery_mode"] == BatteryMode.BOOST_CHARGING.value
        assert result["target_soc"] == 95.0
        assert result["reason"] == "optimizer_charge_grid_boost"

    def test_export_proactive_maps_correctly(self):
        """EXPORT_PROACTIVE should map to PROACTIVE_EXPORT."""
        config = OptimizerConfig()
        decisions = [{"action": "export_proactive"}]

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["action"] == "export_proactive"
        assert result["battery_mode"] == BatteryMode.PROACTIVE_EXPORT.value
        assert result["target_soc"] is None
        assert result["reason"] == "optimizer_export_proactive"

    def test_invalid_slot_index_returns_self_consumption(self):
        """Invalid slot index should default to SELF_CONSUMPTION."""
        config = OptimizerConfig(hold_soc=True)  # Even with hold_soc=True
        decisions = [{"action": "hold"}]

        result = _derive_runtime_apply_plan(decisions, -1, config)

        assert result["action"] == "hold"
        assert result["battery_mode"] == BatteryMode.SELF_CONSUMPTION.value
        assert result["reason"] == "no_valid_decision_for_current_slot"

    def test_empty_decisions_returns_self_consumption(self):
        """Empty decisions list should default to SELF_CONSUMPTION."""
        config = OptimizerConfig(hold_soc=True)
        decisions = []

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["action"] == "hold"
        assert result["battery_mode"] == BatteryMode.SELF_CONSUMPTION.value
        assert result["reason"] == "no_valid_decision_for_current_slot"

    def test_unknown_action_returns_self_consumption(self):
        """Unknown action should default to SELF_CONSUMPTION."""
        config = OptimizerConfig()
        decisions = [{"action": "unknown_action"}]

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["action"] == "unknown_action"
        assert result["battery_mode"] == BatteryMode.SELF_CONSUMPTION.value
        assert result["target_soc"] is None
        assert result["reason"] == "unknown_action_unknown_action"

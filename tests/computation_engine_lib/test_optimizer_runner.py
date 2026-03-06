from custom_components.localshift.computation_engine_lib.optimizer_dp import (
    OptimizerConfig,
)
from custom_components.localshift.computation_engine_lib.optimizer_runner import (
    _derive_runtime_apply_plan,
)
from custom_components.localshift.const import BatteryMode


class TestOptimizerRunner:
    def test_derive_runtime_apply_plan_hold(self):
        """Test how PlannerAction.HOLD is mapped to BatteryMode based on config.hold_soc."""
        # Setup decisions list
        decisions = [{"action": "hold"}]

        # Test Case 1: config.hold_soc is True
        config_with_hold = OptimizerConfig(hold_soc=True)
        result_with_hold = _derive_runtime_apply_plan(decisions, 0, config_with_hold)

        assert result_with_hold["action"] == "hold"
        assert result_with_hold["battery_mode"] == BatteryMode.HOLD.value
        assert result_with_hold["reason"] == "optimizer_hold_strict"

        # Test Case 2: config.hold_soc is False
        config_without_hold = OptimizerConfig(hold_soc=False)
        result_without_hold = _derive_runtime_apply_plan(
            decisions, 0, config_without_hold
        )

        assert result_without_hold["action"] == "hold"
        assert result_without_hold["battery_mode"] == BatteryMode.SELF_CONSUMPTION.value
        assert result_without_hold["reason"] == "optimizer_self_consumption"

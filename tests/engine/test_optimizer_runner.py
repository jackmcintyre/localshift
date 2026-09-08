from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.engine.optimizer_dp import (
    ObjectiveTerms,
    OptimizerConfig,
    OptimizerResult,
    PlannedSlotDecision,
    PlannerAction,
    PlannerReasonCode,
)
from custom_components.localshift.engine.optimizer_runner import (
    OptimizerSafetyGate,
    _build_optimizer_config,
    _build_summary,
    _derive_runtime_apply_plan,
    _find_current_slot_index,
    _normalize_initial_soc,
    _serialize_decision,
)


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

    def test_derive_runtime_apply_plan_unknown_action(self):
        """Unknown actions should default to self-consumption."""
        decisions = [{"action": "mystery"}]
        config = OptimizerConfig(hold_soc=False)

        result = _derive_runtime_apply_plan(decisions, 0, config)

        assert result["battery_mode"] == BatteryMode.SELF_CONSUMPTION.value
        assert result["reason"] == "unknown_action_mystery"

    def test_derive_runtime_apply_plan_charge_and_export(self):
        """Charge and export actions should map to expected modes."""
        config = OptimizerConfig(demand_window_target_soc_pct=85.0)

        result_charge = _derive_runtime_apply_plan(
            [{"action": "charge_grid_normal"}], 0, config
        )
        assert result_charge["battery_mode"] == BatteryMode.GRID_CHARGING.value
        assert result_charge["target_soc"] == 85.0

        result_boost = _derive_runtime_apply_plan(
            [{"action": "charge_grid_boost"}], 0, config
        )
        assert result_boost["battery_mode"] == BatteryMode.BOOST_CHARGING.value

        result_export = _derive_runtime_apply_plan(
            [{"action": "export_proactive"}], 0, config
        )
        assert result_export["battery_mode"] == BatteryMode.PROACTIVE_EXPORT.value

    def test_derive_runtime_apply_plan_invalid_index(self):
        """Invalid slot index should return default hold plan."""
        decisions = [{"action": "hold"}]
        config = OptimizerConfig(hold_soc=False)

        result = _derive_runtime_apply_plan(decisions, 5, config)

        assert result["action"] == "hold"
        assert result["battery_mode"] == BatteryMode.SELF_CONSUMPTION.value
        assert result["reason"] == "no_valid_decision_for_current_slot"


class TestOptimizerRunnerHelpers:
    def test_normalize_initial_soc_rejects_invalid(self):
        """Invalid SOC inputs should be rejected with error info."""
        config = OptimizerConfig(min_soc_pct=10.0, max_soc_pct=90.0)

        soc, info = _normalize_initial_soc("bad", config)
        assert soc is None
        assert info["error"] == "non_numeric"

        soc, info = _normalize_initial_soc(float("inf"), config)
        assert soc is None
        assert info["error"] == "non_finite"

        soc, info = _normalize_initial_soc(0.0, config)
        assert soc is None
        assert info["error"] == "non_positive"

    def test_normalize_initial_soc_clamps_and_warns(self, caplog):
        """SOC should be clamped to bounds and warn on fractional input."""
        config = OptimizerConfig(min_soc_pct=10.0, max_soc_pct=90.0)

        soc, info = _normalize_initial_soc(0.5, config)

        assert soc == 10.0
        assert info["normalization"] == "clamped_to_bounds"
        assert info["pre_clamp_soc"] == 0.5
        assert info["normalized_soc_pct"] == 10.0
        assert "unusually low" in caplog.text

    def test_build_optimizer_config_base_cheap_price_passthrough(self):
        """Issue #800: base_cheap_price flows through as the objective percentile floor,
        independent of effective_cheap_price (the post-DW gate uses min() of the two,
        so the floor must be readable on its own), and must tolerate <= 0 values
        (negative-wholesale markets) without being floored to 0.0 (which would block
        ALL post-DW charging).
        """

        class MockData:
            effective_cheap_price = 0.10
            general_price = 0.20
            base_cheap_price = 0.08

        updated = _build_optimizer_config(MockData(), {})
        assert updated.effective_cheap_price == pytest.approx(0.10)
        assert updated.base_cheap_price == pytest.approx(0.08)

    def test_build_optimizer_config_base_cheap_price_negative_market(self):
        """A genuinely-negative percentile base passes through (not coerced to None/0)."""

        class MockData:
            effective_cheap_price = 0.05
            general_price = 0.20
            base_cheap_price = -0.02

        updated = _build_optimizer_config(MockData(), {})
        assert updated.base_cheap_price == pytest.approx(-0.02)

    def test_build_optimizer_config_base_cheap_price_absent_is_none(self):
        """When the coordinator has not computed a base yet, gating is disabled (None)."""

        class MockData:
            effective_cheap_price = 0.10
            general_price = 0.20

        updated = _build_optimizer_config(MockData(), {})
        assert updated.base_cheap_price is None

    def test_build_optimizer_config_wires_switching_penalty_per_kwh(self):
        """Issue #919: runner should read the new per-kWh floor and pass it
        through so the DP can scale the switching hurdle to slot energy."""

        class MockData:
            effective_cheap_price = 0.10
            general_price = 0.20

        updated = _build_optimizer_config(
            MockData(),
            {"switching_penalty_per_kwh": 0.50},
        )
        assert updated.switching_penalty_per_kwh == pytest.approx(0.50)

        # Default (option absent) should fall back to production default 0.40,
        # not the dataclass 0.0 default (which is overridden by the runner).
        updated_default = _build_optimizer_config(MockData(), {})
        assert updated_default.switching_penalty_per_kwh == pytest.approx(0.40)

    def test_serialize_decision(self):
        """Serialize helper should format core decision fields for sensors."""
        decision = PlannedSlotDecision(
            slot_index=0,
            timestamp_iso="2026-01-01T10:00:00Z",
            slot_interval_minutes=30,
            action=PlannerAction.HOLD,
            reason_code=PlannerReasonCode.IDLE,
            objective_terms=ObjectiveTerms(),
            predicted_soc_pct=55.1234,
            grid_import_kwh=1.23456,
            grid_export_kwh=0.0,
            solar_kwh=0.1,
            consumption_kwh=0.2,
            buy_price=0.12345,
            sell_price=0.05678,
        )

        serialized_decision = _serialize_decision(decision)

        assert serialized_decision["predicted_soc_pct"] == 55.12
        assert serialized_decision["grid_import_kwh"] == 1.2346
        assert serialized_decision["buy_price"] == 0.1235

    def test_build_summary_includes_optional_fields(self):
        """Summary should include parity and SOC info when provided."""
        result = OptimizerResult(
            success=True,
            projected_net_cost=1.0,
            projected_import_kwh=2.0,
            projected_export_kwh=0.5,
            terminal_shortfall_pct=0.0,
        )
        summary = _build_summary(
            result=result,
            cycle_id="cycle",
            cycle_timestamp_iso="2026-01-01T10:00:00Z",
            parity_info={"completeness_pct": 90.0, "defaulted_fields": {"x": 1}},
            config_options={"a": 1},
            initial_soc_info={"normalized_soc_pct": 55.0},
        )

        assert summary["initial_soc_pct"] == 55.0
        assert summary["parity_completeness_pct"] == 90.0

    def test_find_current_slot_index(self):
        """Slot index should resolve to current slot or default to 0."""

        class MockData:
            optimizer_decisions = []

        assert _find_current_slot_index(MockData()) == 0

        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        data = MockData()
        data.optimizer_decisions = [
            {
                "timestamp_iso": "2026-01-01T10:00:00Z",
                "slot_interval_minutes": 30,
            }
        ]

        with patch(
            "custom_components.localshift.engine.optimizer_runner.datetime"
        ) as mock_datetime:
            mock_datetime.now.return_value = now
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            mock_datetime.UTC = UTC
            mock_datetime.timedelta = timedelta
            assert _find_current_slot_index(data) == 0

        data.optimizer_decisions = [
            {"timestamp_iso": "", "slot_interval_minutes": 30},
            {"timestamp_iso": "bad-timestamp", "slot_interval_minutes": 30},
        ]
        with patch(
            "custom_components.localshift.engine.optimizer_runner.datetime"
        ) as mock_datetime:
            mock_datetime.now.return_value = now
            mock_datetime.fromisoformat.side_effect = ValueError("bad")
            mock_datetime.UTC = UTC
            mock_datetime.timedelta = timedelta
            assert _find_current_slot_index(data) == 0


class TestOptimizerSafetyGate:
    def test_gate_blocks_when_result_missing(self):
        """Missing optimizer result should block admission."""
        gate = OptimizerSafetyGate({})

        result = gate.check_admission(
            data=object(), optimizer_result=None, alignment=None
        )

        assert result.allowed is False
        assert result.block_reason == "optimizer_result_none"

    def test_gate_blocks_when_solve_failed(self):
        """Failed solve should block admission."""
        gate = OptimizerSafetyGate({})
        optimizer_result = OptimizerResult(success=False, error_message="boom")

        result = gate.check_admission(
            data=object(), optimizer_result=optimizer_result, alignment=None
        )

        assert result.allowed is False
        assert result.block_reason == "optimizer_solve_failed"

    def test_gate_blocks_on_alignment_failure(self):
        """Alignment issues should block admission."""
        gate = OptimizerSafetyGate({})
        optimizer_result = OptimizerResult(success=True, decisions=[object()])
        alignment = {"valid": False, "issues": ["bad"]}

        result = gate.check_admission(
            data=object(), optimizer_result=optimizer_result, alignment=alignment
        )

        assert result.allowed is False
        assert result.block_reason == "slot_alignment_invalid"

    def test_gate_blocks_on_stale_forecast(self):
        """Stale forecast should block admission."""
        gate = OptimizerSafetyGate({})
        gate._forecast_freshness_minutes = 5

        class MockData:
            optimizer_summary = {"cycle_timestamp_iso": "2026-01-01T09:00:00Z"}

        optimizer_result = OptimizerResult(success=True, decisions=[object()])

        with patch(
            "custom_components.localshift.engine.optimizer_runner.datetime"
        ) as mock_datetime:
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            mock_datetime.now.return_value = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
            mock_datetime.UTC = UTC
            result = gate.check_admission(
                data=MockData(), optimizer_result=optimizer_result, alignment=None
            )

        assert result.allowed is False
        assert result.block_reason == "forecast_stale"

    def test_gate_blocks_when_no_decisions(self):
        """Empty decisions should block admission."""
        gate = OptimizerSafetyGate({})
        optimizer_result = OptimizerResult(success=True, decisions=[])

        result = gate.check_admission(
            data=object(), optimizer_result=optimizer_result, alignment=None
        )

        assert result.allowed is False
        assert result.block_reason == "no_decisions_available"

    def test_gate_allows_valid(self):
        """Passing checks should allow optimizer execution."""
        gate = OptimizerSafetyGate({})
        gate._forecast_freshness_minutes = 60

        class MockData:
            optimizer_summary = {"cycle_timestamp_iso": "2026-01-01T09:59:00Z"}

        optimizer_result = OptimizerResult(success=True, decisions=[object()])

        with patch(
            "custom_components.localshift.engine.optimizer_runner.datetime"
        ) as mock_datetime:
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            mock_datetime.now.return_value = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
            mock_datetime.UTC = UTC
            result = gate.check_admission(
                data=MockData(), optimizer_result=optimizer_result, alignment=None
            )

        assert result.allowed is True

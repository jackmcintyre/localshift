from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from custom_components.localshift.engine.optimizer_dp import (
    ObjectiveTerms,
    OptimizerConfig,
    OptimizerResult,
    PlannedSlotDecision,
    PlannerAction,
    PlannerReasonCode,
    SlotContext,
)
from custom_components.localshift.engine.optimizer_runner import (
    OptimizerSafetyGate,
    _build_optimizer_config,
    _build_summary,
    _compute_legacy_energy_totals,
    _derive_runtime_apply_plan,
    _find_current_slot_index,
    _get_ha_timezone,
    _make_cycle_id,
    _map_mode_to_action,
    _normalize_initial_soc,
    _run,
    _serialize_decision,
    _serialize_result,
    _validate_slot_alignment,
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
    def test_get_ha_timezone_success(self):
        """Timezone helper should return string when HA provides tzinfo."""
        with patch("homeassistant.util.dt.get_time_zone", return_value=UTC):
            assert _get_ha_timezone() == "UTC"

    def test_get_ha_timezone_fallback(self):
        """Timezone helper should fall back to UTC on errors."""
        with patch(
            "homeassistant.util.dt.get_time_zone", side_effect=Exception("boom")
        ):
            assert _get_ha_timezone() == "UTC"

    def test_map_mode_to_action(self):
        """BatteryMode values should map to PlannerAction."""
        assert _map_mode_to_action(BatteryMode.SELF_CONSUMPTION) == PlannerAction.HOLD
        assert _map_mode_to_action(BatteryMode.GRID_CHARGING) == (
            PlannerAction.CHARGE_GRID_NORMAL
        )
        assert _map_mode_to_action(BatteryMode.BOOST_CHARGING) == (
            PlannerAction.CHARGE_GRID_BOOST
        )
        assert _map_mode_to_action(BatteryMode.PROACTIVE_EXPORT) == (
            PlannerAction.EXPORT_PROACTIVE
        )
        assert _map_mode_to_action("unknown") is None

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

    def test_build_optimizer_config_adaptive_adjustments(self):
        """Adaptive params should adjust target, cheap price, and export margin."""

        class MockData:
            effective_cheap_price = 0.10
            general_price = -0.01
            adaptive_params = {
                "cheap_price_bias": 2.0,
                "grid_charge_soc_headroom": 5.0,
                "overnight_drain_safety_margin": 3.0,
                "export_threshold_adjustment": 1.0,
            }

        config_options = {
            "battery_target": 80.0,
            "export_price_margin": 0.10,
        }

        updated = _build_optimizer_config(MockData(), config_options)

        assert updated.effective_cheap_price == pytest.approx(0.12)
        assert updated.demand_window_target_soc_pct == 88.0
        assert updated.export_price_margin == pytest.approx(0.11)
        assert updated.self_consumption_value_per_kwh == pytest.approx(0.10)

    def test_compute_legacy_energy_totals(self):
        """Legacy totals should ignore invalid numeric inputs."""
        totals = _compute_legacy_energy_totals([
            {"grid_import_kwh": "bad", "grid_export_kwh": 1.0},
            {"grid_import_kwh": 2.5, "grid_export_kwh": None},
        ])

        assert totals == (2.5, 1.0)

    def test_validate_slot_alignment_mismatch(self):
        """Slot alignment should flag count mismatch."""
        legacy_slots = [{}]
        contexts: list[SlotContext] = []

        result = _validate_slot_alignment(legacy_slots, contexts)

        assert result["valid"] is False
        assert "slot_count_mismatch" in result["issues"][0]

    def test_validate_slot_alignment_warnings_and_issues(self):
        """Slot alignment should surface warnings and issues for bad slots."""
        legacy_slots = [{"slot_interval_minutes": 30}]
        contexts = [
            SlotContext(
                slot_index=1,
                timestamp_iso="",
                slot_interval_minutes=15,
                buy_price=-0.1,
                sell_price=0.0,
                solar_kwh=0.0,
                consumption_kwh=0.0,
            )
        ]

        result = _validate_slot_alignment(legacy_slots, contexts)

        assert result["valid"] is False
        assert any("index_mismatch" in issue for issue in result["issues"])
        assert any("interval_mismatch" in issue for issue in result["issues"])
        assert any("missing_timestamp" in warning for warning in result["warnings"])
        assert any("negative_buy_price" in warning for warning in result["warnings"])

    def test_serialize_result_and_decision(self):
        """Serialize helpers should format core fields for sensors."""
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
        result = OptimizerResult(
            success=True,
            solve_time_seconds=1.23456,
            total_slots=1,
            states_explored=10,
            projected_import_kwh=1.23456,
            projected_export_kwh=0.0,
            projected_net_cost=0.12345,
            terminal_shortfall_pct=1.234,
            decisions=[decision],
        )

        serialized_decision = _serialize_decision(decision)
        serialized_result = _serialize_result(result)

        assert serialized_decision["predicted_soc_pct"] == 55.12
        assert serialized_decision["grid_import_kwh"] == 1.2346
        assert serialized_decision["buy_price"] == 0.1235
        assert serialized_result["solve_time_seconds"] == 1.2346
        assert serialized_result["projected_net_cost"] == 0.1235

    def test_build_summary_includes_optional_fields(self):
        """Summary should include parity, alignment, and SOC info when provided."""
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
            alignment={"valid": False, "issues": ["bad"], "warnings": ["warn"]},
            config_options={"a": 1},
            initial_soc_info={"normalized_soc_pct": 55.0},
        )

        assert summary["initial_soc_pct"] == 55.0
        assert summary["parity_completeness_pct"] == 90.0
        assert summary["alignment_valid"] is False
        assert summary["alignment_issues"] == ["bad"]
        assert summary["alignment_warnings"] == ["warn"]

    def test_make_cycle_id_length(self):
        """Cycle IDs should be short and deterministic length."""
        cycle_id = _make_cycle_id()
        assert len(cycle_id) == 12

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


class TestOptimizerRun:
    def test_run_no_slots_sets_summary(self):
        """Empty slot list should set summary with no_slots_available."""

        class MockData:
            daily_forecast = []
            soc = 50.0
            active_mode = BatteryMode.SELF_CONSUMPTION

        class MockMeta:
            @staticmethod
            def to_parity_dict():
                return {"completeness_pct": 0.0}

        with patch(
            "custom_components.localshift.engine.optimizer_runner.SlotBuilder"
        ) as mock_builder:
            mock_builder.return_value.build_slots.return_value = ([], MockMeta())

            data = MockData()
            _run(
                data=data,
                config_options={},
                cycle_id="cycle",
                cycle_timestamp_iso="2026-01-01T10:00:00Z",
                planner=object(),
            )

        assert data.optimizer_summary["error_message"] == "no_slots_available"

    def test_run_invalid_soc_sets_summary(self):
        """Invalid initial SOC should set error summary and exit."""

        class MockData:
            daily_forecast = [{}]
            soc = "bad"
            active_mode = BatteryMode.SELF_CONSUMPTION

        class MockMeta:
            @staticmethod
            def to_parity_dict():
                return {"completeness_pct": 100.0}

        with (
            patch(
                "custom_components.localshift.engine.optimizer_runner.SlotBuilder"
            ) as mock_builder,
            patch(
                "custom_components.localshift.engine.optimizer_runner._normalize_initial_soc"
            ) as mock_norm,
        ):
            mock_builder.return_value.build_slots.return_value = (
                [SlotContext(0, "2026-01-01T10:00:00Z", 30, 0.2, 0.1, 0, 0)],
                MockMeta(),
            )
            mock_norm.return_value = (None, {"error": "non_numeric"})

            data = MockData()
            _run(
                data=data,
                config_options={},
                cycle_id="cycle",
                cycle_timestamp_iso="2026-01-01T10:00:00Z",
                planner=object(),
            )

        assert data.optimizer_summary["error_message"] == "invalid_initial_soc"

    def test_run_success_writes_outputs(self):
        """Successful optimizer run should populate result fields."""

        class MockData:
            daily_forecast = [{}]
            soc = 50.0
            active_mode = BatteryMode.SELF_CONSUMPTION
            optimizer_summary = None
            optimizer_result = None
            optimizer_decisions = None

        class MockMeta:
            @staticmethod
            def to_parity_dict():
                return {"completeness_pct": 100.0}

        decision = PlannedSlotDecision(
            slot_index=0,
            timestamp_iso="2026-01-01T10:00:00Z",
            slot_interval_minutes=30,
            action=PlannerAction.HOLD,
            reason_code=PlannerReasonCode.IDLE,
            objective_terms=ObjectiveTerms(),
            predicted_soc_pct=55.0,
            grid_import_kwh=1.0,
            grid_export_kwh=0.0,
            solar_kwh=0.0,
            consumption_kwh=0.0,
            buy_price=0.2,
            sell_price=0.1,
        )
        result = OptimizerResult(success=True, total_slots=1, decisions=[decision])

        class MockPlanner:
            def plan(self, inputs):
                return result

        with (
            patch(
                "custom_components.localshift.engine.optimizer_runner.SlotBuilder"
            ) as mock_builder,
            patch(
                "custom_components.localshift.engine.optimizer_runner._validate_slot_alignment"
            ) as mock_align,
            patch(
                "custom_components.localshift.engine.optimizer_runner._normalize_initial_soc"
            ) as mock_norm,
        ):
            mock_builder.return_value.build_slots.return_value = (
                [SlotContext(0, "2026-01-01T10:00:00Z", 30, 0.2, 0.1, 0, 0)],
                MockMeta(),
            )
            mock_align.return_value = {"valid": True}
            mock_norm.return_value = (50.0, {"normalized_soc_pct": 50.0})

            data = MockData()
            _run(
                data=data,
                config_options={},
                cycle_id="cycle",
                cycle_timestamp_iso="2026-01-01T10:00:00Z",
                planner=MockPlanner(),
            )

        assert data.optimizer_result is not None
        assert data.optimizer_decisions is not None
        assert data.optimizer_summary["success"] is True

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.localshift.const import BatteryMode, CONF_BATTERY_TARGET
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
    run_optimizer,
)
from custom_components.localshift.learning.charge_rate import ChargeRateCurve


class TestOptimizerRunner:
    def _slot(self, slot_index: int = 0, interval_minutes: int = 30) -> SlotContext:
        return SlotContext(
            slot_index=slot_index,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            slot_interval_minutes=interval_minutes,
            buy_price=0.0,
            sell_price=0.0,
            solar_kwh=0.0,
            consumption_kwh=0.0,
        )

    def _decision(
        self,
        action: PlannerAction | str = PlannerAction.HOLD,
        reason: PlannerReasonCode | str = PlannerReasonCode.IDLE,
    ) -> PlannedSlotDecision:
        return PlannedSlotDecision(
            slot_index=0,
            timestamp_iso="2024-01-01T00:00:00+00:00",
            slot_interval_minutes=30,
            action=action,
            reason_code=reason,
            objective_terms=ObjectiveTerms(import_cost=1.0, export_revenue=0.5),
            predicted_soc_pct=50.0,
            grid_import_kwh=1.0,
            grid_export_kwh=0.0,
        )

    def _result(self, decisions: list[PlannedSlotDecision]) -> OptimizerResult:
        return OptimizerResult(
            success=True,
            total_slots=len(decisions),
            decisions=decisions,
            projected_import_kwh=1.0,
            projected_export_kwh=0.5,
            projected_net_cost=0.2,
            terminal_shortfall_pct=1.5,
            reason_code_histogram={"IDLE": 1},
        )

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

    def test_derive_runtime_apply_plan_charge_modes(self) -> None:
        config = OptimizerConfig(demand_window_target_soc_pct=80.0)

        normal = _derive_runtime_apply_plan(
            [{"action": "charge_grid_normal"}], 0, config
        )
        assert normal["battery_mode"] == BatteryMode.GRID_CHARGING.value
        assert normal["target_soc"] == pytest.approx(80.0)

        boost = _derive_runtime_apply_plan([{"action": "charge_grid_boost"}], 0, config)
        assert boost["battery_mode"] == BatteryMode.BOOST_CHARGING.value
        assert boost["target_soc"] == pytest.approx(80.0)

        export = _derive_runtime_apply_plan([{"action": "export_proactive"}], 0, config)
        assert export["battery_mode"] == BatteryMode.PROACTIVE_EXPORT.value

        unknown = _derive_runtime_apply_plan([{"action": "mystery"}], 0, config)
        assert unknown["battery_mode"] == BatteryMode.SELF_CONSUMPTION.value

    def test_get_ha_timezone_returns_string(self) -> None:
        tz = _get_ha_timezone()
        assert isinstance(tz, str)
        assert tz

    def test_map_mode_to_action_mappings(self) -> None:
        assert _map_mode_to_action(BatteryMode.SELF_CONSUMPTION) == PlannerAction.HOLD
        assert (
            _map_mode_to_action(BatteryMode.GRID_CHARGING)
            == PlannerAction.CHARGE_GRID_NORMAL
        )
        assert (
            _map_mode_to_action(BatteryMode.BOOST_CHARGING)
            == PlannerAction.CHARGE_GRID_BOOST
        )
        assert (
            _map_mode_to_action(BatteryMode.PROACTIVE_EXPORT)
            == PlannerAction.EXPORT_PROACTIVE
        )
        assert _map_mode_to_action("unknown") is None

    def test_build_optimizer_config_applies_curves_and_adaptive(self) -> None:
        curve_normal = ChargeRateCurve.from_bins({0: 4.0})
        curve_boost = ChargeRateCurve.from_bins({0: 6.0})

        data = SimpleNamespace(
            effective_cheap_price=0.1,
            general_price=0.0,
            adaptive_params={
                "cheap_price_bias": 5.0,
                "grid_charge_soc_headroom": 2.0,
                "overnight_drain_safety_margin": 1.0,
                "export_threshold_adjustment": 3.0,
            },
            forecast_horizon_hours=24.0,
            charge_rate_curves={"normal": curve_normal, "boost": curve_boost},
            learning_enabled=True,
        )

        config = _build_optimizer_config(data, {CONF_BATTERY_TARGET: 70.0})

        assert config.charge_rate_curve is curve_normal
        assert config.boost_charge_rate_curve is curve_boost
        assert config.effective_cheap_price == pytest.approx(0.15)
        assert config.self_consumption_value_per_kwh == pytest.approx(0.1)
        assert config.demand_window_target_soc_pct == pytest.approx(73.0)

    def test_normalize_initial_soc_invalid_values(self) -> None:
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

    def test_normalize_initial_soc_clamps_bounds(self) -> None:
        config = OptimizerConfig(min_soc_pct=10.0, max_soc_pct=90.0)

        soc, info = _normalize_initial_soc(0.5, config)
        assert soc == pytest.approx(10.0)
        assert info["normalization"] == "clamped_to_bounds"

        soc, info = _normalize_initial_soc(95.0, config)
        assert soc == pytest.approx(90.0)
        assert info["normalization"] == "clamped_to_bounds"

    def test_compute_legacy_energy_totals_handles_invalid(self) -> None:
        total_import, total_export = _compute_legacy_energy_totals([
            {"grid_import_kwh": "1.2", "grid_export_kwh": 0.5},
            {"grid_import_kwh": None},
            {"grid_export_kwh": "bad"},
        ])

        assert total_import == pytest.approx(1.2)
        assert total_export == pytest.approx(0.5)

    def test_validate_slot_alignment_mismatch_counts(self) -> None:
        result = _validate_slot_alignment(
            legacy_slots=[{"slot_interval_minutes": 30}, {"slot_interval_minutes": 30}],
            contexts=[self._slot()],
        )

        assert result["valid"] is False
        assert "slot_count_mismatch" in result["issues"][0]

    def test_validate_slot_alignment_flags_details(self) -> None:
        contexts = [
            SlotContext(
                slot_index=1,
                timestamp_iso="",
                slot_interval_minutes=30,
                buy_price=-0.01,
                sell_price=0.0,
                solar_kwh=0.0,
                consumption_kwh=0.0,
            )
        ]
        legacy_slots = [{"slot_interval_minutes": 15}]

        result = _validate_slot_alignment(legacy_slots, contexts)

        assert result["valid"] is False
        assert any("index_mismatch" in issue for issue in result["issues"])
        assert any("interval_mismatch" in issue for issue in result["issues"])
        assert "missing_timestamp" in result["warnings"][0]
        assert "negative_buy_price" in result["warnings"][1]

    def test_serialize_result_and_decision(self) -> None:
        decision = self._decision()
        result = self._result([decision])

        serialized = _serialize_result(result)
        assert serialized["success"] is True
        assert serialized["total_slots"] == 1

        decision_dict = _serialize_decision(decision)
        assert decision_dict["action"] == PlannerAction.HOLD.value
        assert decision_dict["reason_code"] == PlannerReasonCode.IDLE.value
        assert decision_dict["grid_import_kwh"] == pytest.approx(1.0)

    def test_serialize_decision_string_values(self) -> None:
        decision = self._decision(action="hold", reason="IDLE")

        decision_dict = _serialize_decision(decision)
        assert decision_dict["action"] == "hold"
        assert decision_dict["reason_code"] == "IDLE"

    def test_build_summary_includes_parity_alignment(self) -> None:
        result = self._result([self._decision()])
        summary = _build_summary(
            result,
            cycle_id="cycle",
            cycle_timestamp_iso="2024-01-01T00:00:00+00:00",
            parity_info={"completeness_pct": 99.0, "defaulted_fields": {"a": 1}},
            alignment={
                "valid": False,
                "issues": ["slot_issue"],
                "warnings": ["slot_warning"],
            },
            config_options={"foo": "bar"},
            initial_soc_info={"normalized_soc_pct": 55.0},
        )

        assert summary["parity_completeness_pct"] == pytest.approx(99.0)
        assert summary["alignment_valid"] is False
        assert summary["alignment_issues"] == ["slot_issue"]
        assert summary["alignment_warnings"] == ["slot_warning"]
        assert summary["initial_soc_pct"] == pytest.approx(55.0)

    def test_make_cycle_id_length(self) -> None:
        cycle_id = _make_cycle_id()
        assert len(cycle_id) == 12

    def test_safety_gate_blocks_when_missing_result(self) -> None:
        gate = OptimizerSafetyGate({})
        result = gate.check_admission(SimpleNamespace(optimizer_summary={}), None, None)
        assert result.allowed is False
        assert result.block_reason == "optimizer_result_none"

    def test_safety_gate_blocks_when_failed(self) -> None:
        gate = OptimizerSafetyGate({})
        failed = OptimizerResult(success=False, error_message="fail")
        result = gate.check_admission(
            SimpleNamespace(optimizer_summary={}), failed, None
        )
        assert result.allowed is False
        assert result.block_reason == "optimizer_solve_failed"

    def test_safety_gate_blocks_alignment(self) -> None:
        gate = OptimizerSafetyGate({})
        ok_result = self._result([self._decision()])
        result = gate.check_admission(
            SimpleNamespace(optimizer_summary={}),
            ok_result,
            {"valid": False, "issues": ["slot"]},
        )
        assert result.allowed is False
        assert result.block_reason == "slot_alignment_invalid"

    def test_safety_gate_blocks_forecast_stale(self) -> None:
        gate = OptimizerSafetyGate({})
        ok_result = self._result([self._decision()])
        stale_timestamp = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        data = SimpleNamespace(
            optimizer_summary={"cycle_timestamp_iso": stale_timestamp}
        )

        result = gate.check_admission(data, ok_result, {"valid": True})
        assert result.allowed is False
        assert result.block_reason == "forecast_stale"

    def test_safety_gate_handles_invalid_timestamp(self) -> None:
        gate = OptimizerSafetyGate({})
        ok_result = self._result([self._decision()])
        data = SimpleNamespace(optimizer_summary={"cycle_timestamp_iso": "bad"})

        result = gate.check_admission(data, ok_result, {"valid": True})
        assert result.allowed is True

    def test_safety_gate_blocks_no_decisions(self) -> None:
        gate = OptimizerSafetyGate({})
        ok_result = self._result([])
        ok_result.decisions = []
        data = SimpleNamespace(
            optimizer_summary={"cycle_timestamp_iso": datetime.now(UTC).isoformat()}
        )

        result = gate.check_admission(data, ok_result, {"valid": True})
        assert result.allowed is False
        assert result.block_reason == "no_decisions_available"

    def test_safety_gate_allows_success(self) -> None:
        gate = OptimizerSafetyGate({})
        ok_result = self._result([self._decision()])
        data = SimpleNamespace(
            optimizer_summary={"cycle_timestamp_iso": datetime.now(UTC).isoformat()}
        )

        result = gate.check_admission(data, ok_result, {"valid": True})
        assert result.allowed is True

    def test_find_current_slot_index_variants(self) -> None:
        now = datetime.now(UTC)
        decisions = [
            {
                "timestamp_iso": (now - timedelta(minutes=10)).isoformat(),
                "slot_interval_minutes": 30,
            },
            {"timestamp_iso": "bad", "slot_interval_minutes": 30},
        ]

        assert (
            _find_current_slot_index(SimpleNamespace(optimizer_decisions=decisions))
            == 0
        )
        assert _find_current_slot_index(SimpleNamespace(optimizer_decisions=[])) == 0
        assert (
            _find_current_slot_index(
                SimpleNamespace(
                    optimizer_decisions=[
                        {"timestamp_iso": "", "slot_interval_minutes": 30}
                    ]
                )
            )
            == 0
        )

    def test_run_optimizer_handles_exception(self, monkeypatch) -> None:
        def _raise(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "custom_components.localshift.engine.optimizer_runner._run", _raise
        )
        data = SimpleNamespace(daily_forecast=[])
        run_optimizer(data, {})

        assert data.optimizer_summary["success"] is False
        assert "boom" in data.optimizer_summary["error_message"]

    def test_run_no_slots_summary(self, monkeypatch) -> None:
        class DummyMeta:
            def to_parity_dict(self) -> dict:
                return {"completeness_pct": 0.0, "defaulted_fields": {}}

        def _build_slots(_self, _data, _adaptive):
            return [], DummyMeta()

        monkeypatch.setattr(
            "custom_components.localshift.engine.optimizer_runner.SlotBuilder.build_slots",
            _build_slots,
        )

        data = SimpleNamespace(
            daily_forecast=[],
            soc=50.0,
            active_mode=None,
            effective_cheap_price=0.1,
            general_price=0.2,
            adaptive_params=None,
            forecast_horizon_hours=24.0,
        )

        _run(data, {}, "cycle", "2024-01-01T00:00:00+00:00", planner=SimpleNamespace())

        assert data.optimizer_summary["error_message"] == "no_slots_available"

    def test_run_invalid_initial_soc(self, monkeypatch) -> None:
        class DummyMeta:
            def to_parity_dict(self) -> dict:
                return {"completeness_pct": 100.0, "defaulted_fields": {}}

        slot = self._slot()

        def _build_slots(_self, _data, _adaptive):
            return [slot], DummyMeta()

        monkeypatch.setattr(
            "custom_components.localshift.engine.optimizer_runner.SlotBuilder.build_slots",
            _build_slots,
        )

        data = SimpleNamespace(
            daily_forecast=[{"slot_interval_minutes": 30}],
            soc=0.0,
            active_mode=None,
            effective_cheap_price=0.1,
            general_price=0.2,
            adaptive_params=None,
            forecast_horizon_hours=24.0,
        )

        _run(data, {}, "cycle", "2024-01-01T00:00:00+00:00", planner=SimpleNamespace())

        assert data.optimizer_summary["error_message"] == "invalid_initial_soc"

    def test_run_success_path(self, monkeypatch) -> None:
        class DummyMeta:
            def to_parity_dict(self) -> dict:
                return {"completeness_pct": 100.0, "defaulted_fields": {}}

        slot = self._slot()

        def _build_slots(_self, _data, _adaptive):
            return [slot], DummyMeta()

        monkeypatch.setattr(
            "custom_components.localshift.engine.optimizer_runner.SlotBuilder.build_slots",
            _build_slots,
        )

        class DummyPlanner:
            def plan(self, _inputs):
                return self._result([self._decision()])

            def _result(self, decisions):
                return OptimizerResult(
                    success=True,
                    total_slots=len(decisions),
                    decisions=decisions,
                    projected_import_kwh=1.0,
                    projected_export_kwh=0.5,
                    projected_net_cost=0.2,
                    terminal_shortfall_pct=0.0,
                    reason_code_histogram={"IDLE": 1},
                )

            def _decision(self):
                return PlannedSlotDecision(
                    slot_index=0,
                    timestamp_iso="2024-01-01T00:00:00+00:00",
                    slot_interval_minutes=30,
                    action=PlannerAction.HOLD,
                    reason_code=PlannerReasonCode.IDLE,
                    objective_terms=ObjectiveTerms(),
                    predicted_soc_pct=50.0,
                    grid_import_kwh=0.0,
                    grid_export_kwh=0.0,
                )

        data = SimpleNamespace(
            daily_forecast=[{"slot_interval_minutes": 30}],
            soc=50.0,
            active_mode=None,
            effective_cheap_price=0.1,
            general_price=0.2,
            adaptive_params=None,
            forecast_horizon_hours=24.0,
        )

        _run(data, {}, "cycle", "2024-01-01T00:00:00+00:00", planner=DummyPlanner())

        assert data.optimizer_summary["success"] is True
        assert data.optimizer_result["success"] is True

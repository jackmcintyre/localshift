from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.engine.optimizer_dp import (
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)
from custom_components.localshift.engine.optimizer_runner import (
    OptimizerSafetyGate,
    _build_mode_action_rate_lookup,
    _build_optimizer_config,
    _build_summary,
    _compute_legacy_energy_totals,
    _derive_runtime_apply_plan,
    _extract_mode_rates_from_rows,
    _find_current_slot_index,
    _get_ha_timezone,
    _make_cycle_id,
    _map_mode_to_action,
    _normalize_initial_soc,
    _parse_mode_rate_row,
    _run,
    _serialize_decision,
    _serialize_result,
    _validate_slot_alignment,
    run_optimizer,
)


def _slot() -> SlotContext:
    return SlotContext(
        slot_index=0,
        timestamp_iso="2024-01-01T00:00:00+00:00",
        slot_interval_minutes=30,
        buy_price=0.2,
        sell_price=0.05,
        solar_kwh=1.0,
        consumption_kwh=1.5,
    )


class DummyData:
    def __init__(self) -> None:
        self.daily_forecast = [{"slot_interval_minutes": 30}]
        self.soc = 55.0
        self.active_mode = BatteryMode.SELF_CONSUMPTION
        self.effective_cheap_price = 0.1
        self.general_price = 0.2
        self.adaptive_params = None
        self.forecast_horizon_hours = 24.0
        self.charge_rate_curves = {}
        self.charge_rate_mode_analysis = {}
        self.learning_enabled = False
        self.optimizer_summary = {}
        self.optimizer_decisions = []
        self.optimizer_result = {}


def test_get_ha_timezone_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyDt:
        @staticmethod
        def get_time_zone() -> object:
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "homeassistant.util.dt.get_time_zone",
        DummyDt.get_time_zone,
    )
    assert _get_ha_timezone() == "UTC"


def test_get_ha_timezone_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "homeassistant.util.dt.get_time_zone",
        lambda: "Australia/Sydney",
    )
    assert _get_ha_timezone() == "Australia/Sydney"


def test_map_mode_to_action() -> None:
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


def test_parse_mode_rate_row_valid_and_invalid() -> None:
    assert _parse_mode_rate_row(
        {"soc": 42, "charge_kw": 3.4, "n": 7},
        "charge_kw",
    ) == (42, 3.4, 7)
    assert (
        _parse_mode_rate_row({"soc": -1, "charge_kw": 3.4, "n": 7}, "charge_kw") is None
    )
    assert (
        _parse_mode_rate_row({"soc": 42, "charge_kw": -1.0, "n": 7}, "charge_kw")
        is None
    )
    assert (
        _parse_mode_rate_row(
            {"soc": 42, "charge_kw": float("nan"), "n": 7}, "charge_kw"
        )
        is None
    )
    assert _parse_mode_rate_row({"soc": 42, "charge_kw": 3.4, "n": 0}, "charge_kw") == (
        42,
        3.4,
        1,
    )
    assert _parse_mode_rate_row("not_a_dict", "charge_kw") is None
    assert _parse_mode_rate_row({"soc": 42, "charge_kw": "bad"}, "charge_kw") is None
    assert (
        _parse_mode_rate_row({"soc": True, "charge_kw": 3.4, "n": 7}, "charge_kw")
        is None
    )
    assert (
        _parse_mode_rate_row({"soc": 42, "charge_kw": True, "n": 7}, "charge_kw")
        is None
    )
    assert _parse_mode_rate_row(
        {"soc": 42, "charge_kw": 3.4, "n": True}, "charge_kw"
    ) == (
        42,
        3.4,
        1,
    )


def test_extract_mode_rates_from_rows_weighted_average() -> None:
    bins, avg = _extract_mode_rates_from_rows(
        [
            {"soc": 20, "charge_kw": 3.0, "n": 2},
            {"soc": 30, "charge_kw": 4.0, "n": 1},
            {"soc": "bad", "charge_kw": 9.0, "n": 99},
        ],
        "charge_kw",
    )
    assert bins == {20: 3.0, 30: 4.0}
    assert avg == pytest.approx((3.0 * 2 + 4.0 * 1) / 3)


def test_build_mode_action_rate_lookup() -> None:
    payload = {
        "soc_bins_1pct_by_mode": {
            "grid_charging": [{"soc": 50, "charge_kw": 3.2, "n": 2}],
            "boost_charging": [{"soc": 60, "charge_kw": 4.8, "n": 3}],
            "proactive_export": [{"soc": 70, "discharge_kw": 2.7, "n": 4}],
        }
    }
    bins, avgs = _build_mode_action_rate_lookup(payload)
    assert bins[PlannerAction.CHARGE_GRID_NORMAL][50] == pytest.approx(3.2)
    assert bins[PlannerAction.CHARGE_GRID_BOOST][60] == pytest.approx(4.8)
    assert bins[PlannerAction.EXPORT_PROACTIVE][70] == pytest.approx(2.7)
    assert avgs[PlannerAction.EXPORT_PROACTIVE] == pytest.approx(2.7)


def test_build_mode_action_rate_lookup_invalid_payload() -> None:
    assert _build_mode_action_rate_lookup(None) == ({}, {})
    assert _build_mode_action_rate_lookup({"soc_bins_1pct_by_mode": "bad"}) == ({}, {})
    bins, avgs = _build_mode_action_rate_lookup({
        "soc_bins_1pct_by_mode": {
            "grid_charging": {"soc": 50, "charge_kw": 3.1},
            "boost_charging": [{"soc": 60, "charge_kw": 4.0, "n": 1}],
        }
    })
    assert PlannerAction.CHARGE_GRID_NORMAL not in bins
    assert avgs[PlannerAction.CHARGE_GRID_BOOST] == pytest.approx(4.0)


@pytest.mark.parametrize(
    ("raw_soc", "expected_error"),
    [
        ("bad", "non_numeric"),
        (True, "non_numeric"),
        (float("inf"), "non_finite"),
        (0.0, "non_positive"),
        (-2.0, "non_positive"),
    ],
)
def test_normalize_initial_soc_invalid(raw_soc: object, expected_error: str) -> None:
    soc, info = _normalize_initial_soc(raw_soc, OptimizerConfig())
    assert soc is None
    assert info["error"] == expected_error


def test_normalize_initial_soc_clamps_and_passes() -> None:
    config = OptimizerConfig(min_soc_pct=20.0, max_soc_pct=80.0)
    soc, info = _normalize_initial_soc(10.0, config)
    assert soc == 20.0
    assert info["normalization"] == "clamped_to_bounds"
    soc2, info2 = _normalize_initial_soc(60.0, config)
    assert soc2 == 60.0
    assert info2["normalization"] == "none"


def test_normalize_initial_soc_warns_on_tiny_positive(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")
    soc, info = _normalize_initial_soc(0.5, OptimizerConfig(min_soc_pct=5.0))
    assert soc == 5.0
    assert info["normalization"] == "clamped_to_bounds"
    assert "Teslemetry provides percentage scale" in caplog.text


def test_compute_legacy_energy_totals() -> None:
    imp, exp = _compute_legacy_energy_totals([
        {"grid_import_kwh": 1.2, "grid_export_kwh": 0.3},
        {"grid_import_kwh": "bad", "grid_export_kwh": "bad"},
        {},
    ])
    assert imp == pytest.approx(1.2)
    assert exp == pytest.approx(0.3)


def test_validate_slot_alignment() -> None:
    slot = _slot()
    valid = _validate_slot_alignment(
        [{"slot_interval_minutes": 30}],
        [slot],
    )
    assert valid["valid"] is True

    mismatch = _validate_slot_alignment([], [slot])
    assert mismatch["valid"] is False
    assert "slot_count_mismatch" in mismatch["issues"][0]


def test_validate_slot_alignment_reports_index_timestamp_price_interval() -> None:
    bad_slot = SlotContext(
        slot_index=99,
        timestamp_iso="",
        slot_interval_minutes=15,
        buy_price=-0.1,
        sell_price=0.0,
        solar_kwh=0.0,
        consumption_kwh=0.0,
    )
    result = _validate_slot_alignment(
        [{"slot_interval_minutes": 30}],
        [bad_slot],
    )
    assert result["valid"] is False
    assert any("index_mismatch" in issue for issue in result["issues"])
    assert any("interval_mismatch" in issue for issue in result["issues"])
    assert any("missing_timestamp" in warning for warning in result["warnings"])
    assert any("negative_buy_price" in warning for warning in result["warnings"])


def test_serialize_result_and_decision() -> None:
    result = SimpleNamespace(
        success=True,
        planner_version="1",
        solve_time_seconds=0.12345,
        total_slots=4,
        states_explored=100,
        projected_import_kwh=4.1234,
        projected_export_kwh=1.9999,
        projected_net_cost=0.87654,
        terminal_shortfall_pct=2.345,
        error_message=None,
        reason_code_histogram={"x": 1},
        can_solar_reach_target=True,
    )
    serialized = _serialize_result(result)
    assert serialized["solve_time_seconds"] == 0.1235
    assert serialized["terminal_shortfall_pct"] == 2.35

    decision = SimpleNamespace(
        slot_index=1,
        timestamp_iso="2024-01-01T00:00:00+00:00",
        slot_interval_minutes=30,
        action=PlannerAction.HOLD,
        reason_code=SimpleNamespace(value="reason"),
        objective_terms=SimpleNamespace(to_dict=lambda: {"a": 1}),
        predicted_soc_pct=50.123,
        grid_import_kwh=1.23456,
        grid_export_kwh=0.98765,
        solar_kwh=0.1234,
        consumption_kwh=0.9876,
        buy_price=0.23456,
        sell_price=0.11119,
        grid_charge=False,
        grid_charge_boost=False,
        proactive_export=False,
    )
    d = _serialize_decision(decision)
    assert d["action"] == "hold"
    assert d["grid_import_kwh"] == 1.2346


def test_build_summary_contains_optional_sections() -> None:
    result = SimpleNamespace(
        planner_version="x",
        success=True,
        solve_time_seconds=0.1,
        total_slots=3,
        projected_net_cost=1.0,
        projected_import_kwh=2.0,
        projected_export_kwh=0.5,
        terminal_shortfall_pct=3.3,
        reason_code_histogram={},
        error_message=None,
        forecast_accuracy=0.9,
        accuracy_discount_factor=0.8,
        peak_soc_pct=92.0,
        dw_entry_soc_pct=71.0,
    )
    summary = _build_summary(
        result,
        "cid",
        "ts",
        parity_info={"completeness_pct": 95.0, "defaulted_fields": {"x": 1}},
        alignment={"valid": False, "issues": ["a"], "warnings": ["b"]},
        config_options={"foo": "bar"},
        initial_soc_info={"normalized_soc_pct": 55.0},
    )
    assert summary["cycle_id"] == "cid"
    assert summary["parity_completeness_pct"] == 95.0
    assert summary["alignment_issues"] == ["a"]
    assert summary["initial_soc_pct"] == 55.0


def test_make_cycle_id_shape() -> None:
    cid = _make_cycle_id()
    assert len(cid) == 12
    assert cid != _make_cycle_id()


def test_derive_runtime_apply_plan_all_actions() -> None:
    config = OptimizerConfig(hold_soc=True, demand_window_target_soc_pct=88.0)
    assert (
        _derive_runtime_apply_plan([{"action": "hold"}], 0, config)["battery_mode"]
        == BatteryMode.HOLD.value
    )
    assert (
        _derive_runtime_apply_plan([{"action": "charge_grid_normal"}], 0, config)[
            "battery_mode"
        ]
        == BatteryMode.GRID_CHARGING.value
    )
    assert (
        _derive_runtime_apply_plan([{"action": "charge_grid_boost"}], 0, config)[
            "battery_mode"
        ]
        == BatteryMode.BOOST_CHARGING.value
    )
    assert (
        _derive_runtime_apply_plan([{"action": "export_proactive"}], 0, config)[
            "battery_mode"
        ]
        == BatteryMode.PROACTIVE_EXPORT.value
    )
    assert (
        _derive_runtime_apply_plan([{"action": "mystery"}], 0, config)["battery_mode"]
        == BatteryMode.SELF_CONSUMPTION.value
    )
    assert (
        _derive_runtime_apply_plan([], 0, config)["reason"]
        == "no_valid_decision_for_current_slot"
    )


def test_find_current_slot_index() -> None:
    now = datetime.now(UTC)
    data = SimpleNamespace(
        optimizer_decisions=[
            {
                "timestamp_iso": (now - timedelta(minutes=45)).isoformat(),
                "slot_interval_minutes": 30,
            },
            {
                "timestamp_iso": (now - timedelta(minutes=15)).isoformat(),
                "slot_interval_minutes": 30,
            },
            {"timestamp_iso": "bad", "slot_interval_minutes": 30},
        ]
    )
    idx = _find_current_slot_index(data)
    assert idx == 1
    assert _find_current_slot_index(SimpleNamespace(optimizer_decisions=[])) == 0


def test_find_current_slot_index_fallback_with_missing_and_invalid_timestamps() -> None:
    now = datetime.now(UTC)
    data = SimpleNamespace(
        optimizer_decisions=[
            {"timestamp_iso": "", "slot_interval_minutes": 30},
            {"timestamp_iso": "not-a-time", "slot_interval_minutes": 30},
            {
                "timestamp_iso": (now - timedelta(hours=2)).isoformat(),
                "slot_interval_minutes": 30,
            },
        ]
    )
    assert _find_current_slot_index(data) == 0


def test_safety_gate_paths() -> None:
    gate = OptimizerSafetyGate({})
    data = SimpleNamespace(optimizer_summary={})
    blocked_none = gate.check_admission(data, None, None)
    assert blocked_none.allowed is False

    result_failed = SimpleNamespace(success=False, error_message="x", decisions=[])
    blocked_failed = gate.check_admission(data, result_failed, None)
    assert blocked_failed.block_reason == "optimizer_solve_failed"

    result_ok = SimpleNamespace(success=True, error_message=None, decisions=[1])
    blocked_alignment = gate.check_admission(
        data, result_ok, {"valid": False, "issues": ["x"]}
    )
    assert blocked_alignment.block_reason == "slot_alignment_invalid"

    stale_data = SimpleNamespace(
        optimizer_summary={
            "cycle_timestamp_iso": (datetime.now(UTC) - timedelta(hours=5)).isoformat()
        }
    )
    blocked_stale = gate.check_admission(stale_data, result_ok, {"valid": True})
    assert blocked_stale.block_reason == "forecast_stale"

    no_decisions = SimpleNamespace(success=True, error_message=None, decisions=[])
    blocked_decisions = gate.check_admission(data, no_decisions, {"valid": True})
    assert blocked_decisions.block_reason == "no_decisions_available"

    fresh_data = SimpleNamespace(
        optimizer_summary={
            "cycle_timestamp_iso": (
                datetime.now(UTC) - timedelta(minutes=5)
            ).isoformat()
        }
    )
    allowed = gate.check_admission(fresh_data, result_ok, {"valid": True})
    assert allowed.allowed is True


def test_safety_gate_handles_invalid_forecast_timestamp() -> None:
    gate = OptimizerSafetyGate({})
    data = SimpleNamespace(optimizer_summary={"cycle_timestamp_iso": "bad-format"})
    result_ok = SimpleNamespace(success=True, error_message=None, decisions=[1])
    allowed = gate.check_admission(data, result_ok, {"valid": True})
    assert allowed.allowed is True


def test_run_optimizer_exception_sets_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    data = DummyData()

    def _raise(**_: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "custom_components.localshift.engine.optimizer_runner._run",
        _raise,
    )

    run_optimizer(data, {})
    assert data.optimizer_summary["success"] is False
    assert data.optimizer_summary["error_message"] == "boom"


def test_run_no_slots_sets_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    data = DummyData()

    class DummyMeta:
        @staticmethod
        def to_parity_dict() -> dict[str, object]:
            return {"completeness_pct": 50.0}

    class DummyBuilder:
        def __init__(self, **_: object) -> None:
            pass

        def build_slots(self, *_: object) -> tuple[list[SlotContext], DummyMeta]:
            return [], DummyMeta()

    monkeypatch.setattr(
        "custom_components.localshift.engine.optimizer_runner.SlotBuilder",
        DummyBuilder,
    )

    _run(data, {}, "cid", "ts", planner=MagicMock())
    assert data.optimizer_summary["error_message"] == "no_slots_available"


def test_run_logs_alignment_warning_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    data = DummyData()

    class DummyMeta:
        @staticmethod
        def to_parity_dict() -> dict[str, object]:
            return {"completeness_pct": 100.0, "defaulted_fields": {}}

    bad_slot = SlotContext(
        slot_index=7,
        timestamp_iso="",
        slot_interval_minutes=15,
        buy_price=-0.1,
        sell_price=0.0,
        solar_kwh=0.0,
        consumption_kwh=0.0,
    )

    class DummyBuilder:
        def __init__(self, **_: object) -> None:
            pass

        def build_slots(self, *_: object) -> tuple[list[SlotContext], DummyMeta]:
            return [bad_slot], DummyMeta()

    monkeypatch.setattr(
        "custom_components.localshift.engine.optimizer_runner.SlotBuilder",
        DummyBuilder,
    )

    decision = SimpleNamespace(
        slot_index=0,
        timestamp_iso="2024-01-01T00:00:00+00:00",
        slot_interval_minutes=30,
        action=PlannerAction.HOLD,
        reason_code=SimpleNamespace(value="reason"),
        objective_terms=SimpleNamespace(to_dict=lambda: {}),
        predicted_soc_pct=55.0,
        grid_import_kwh=0.0,
        grid_export_kwh=0.0,
        solar_kwh=0.0,
        consumption_kwh=0.0,
        buy_price=0.0,
        sell_price=0.0,
        grid_charge=False,
        grid_charge_boost=False,
        proactive_export=False,
    )
    planner = MagicMock()
    planner.plan.return_value = SimpleNamespace(
        success=True,
        planner_version="x",
        solve_time_seconds=0.01,
        total_slots=1,
        states_explored=1,
        projected_import_kwh=0.0,
        projected_export_kwh=0.0,
        projected_net_cost=0.0,
        terminal_shortfall_pct=0.0,
        error_message=None,
        reason_code_histogram={},
        can_solar_reach_target=True,
        decisions=[decision],
        forecast_accuracy=1.0,
        accuracy_discount_factor=1.0,
        peak_soc_pct=55.0,
        dw_entry_soc_pct=55.0,
    )

    caplog.set_level("WARNING")
    _run(data, {}, "cid", "ts", planner=planner)
    assert "slot alignment issues" in caplog.text
    assert data.optimizer_summary["success"] is True


def test_run_invalid_initial_soc_sets_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    data = DummyData()
    data.soc = 0.0

    class DummyMeta:
        @staticmethod
        def to_parity_dict() -> dict[str, object]:
            return {"completeness_pct": 100.0, "defaulted_fields": {}}

    class DummyBuilder:
        def __init__(self, **_: object) -> None:
            pass

        def build_slots(self, *_: object) -> tuple[list[SlotContext], DummyMeta]:
            return [_slot()], DummyMeta()

    monkeypatch.setattr(
        "custom_components.localshift.engine.optimizer_runner.SlotBuilder",
        DummyBuilder,
    )

    _run(data, {}, "cid", "ts", planner=MagicMock())
    assert data.optimizer_summary["error_message"] == "invalid_initial_soc"
    assert data.optimizer_summary["alignment_valid"] is True


def test_run_success_writes_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    data = DummyData()

    class DummyMeta:
        @staticmethod
        def to_parity_dict() -> dict[str, object]:
            return {"completeness_pct": 100.0, "defaulted_fields": {}}

    class DummyBuilder:
        def __init__(self, **_: object) -> None:
            pass

        def build_slots(self, *_: object) -> tuple[list[SlotContext], DummyMeta]:
            return [_slot()], DummyMeta()

    monkeypatch.setattr(
        "custom_components.localshift.engine.optimizer_runner.SlotBuilder",
        DummyBuilder,
    )

    decision = SimpleNamespace(
        slot_index=0,
        timestamp_iso="2024-01-01T00:00:00+00:00",
        slot_interval_minutes=30,
        action=PlannerAction.HOLD,
        reason_code=SimpleNamespace(value="reason"),
        objective_terms=SimpleNamespace(to_dict=lambda: {}),
        predicted_soc_pct=55.0,
        grid_import_kwh=0.0,
        grid_export_kwh=0.0,
        solar_kwh=0.0,
        consumption_kwh=0.0,
        buy_price=0.0,
        sell_price=0.0,
        grid_charge=False,
        grid_charge_boost=False,
        proactive_export=False,
    )
    result = SimpleNamespace(
        success=True,
        planner_version="x",
        solve_time_seconds=0.01,
        total_slots=1,
        states_explored=1,
        projected_import_kwh=0.0,
        projected_export_kwh=0.0,
        projected_net_cost=0.0,
        terminal_shortfall_pct=0.0,
        error_message=None,
        reason_code_histogram={},
        can_solar_reach_target=True,
        decisions=[decision],
        forecast_accuracy=1.0,
        accuracy_discount_factor=1.0,
        peak_soc_pct=55.0,
        dw_entry_soc_pct=55.0,
    )
    planner = MagicMock()
    planner.plan.return_value = result

    _run(data, {}, "cid", "ts", planner=planner)

    assert data.optimizer_result["success"] is True
    assert len(data.optimizer_decisions) == 1
    assert data.optimizer_summary["success"] is True


def test_build_optimizer_config_mode_lookup_and_curves() -> None:
    curve = SimpleNamespace()

    data = DummyData()
    data.learning_enabled = True
    data.charge_rate_curves = {"normal": curve, "boost": curve}
    data.charge_rate_mode_analysis = {
        "soc_bins_1pct_by_mode": {
            "grid_charging": [{"soc": 50, "charge_kw": 3.0, "n": 2}],
            "boost_charging": [{"soc": 60, "charge_kw": 4.0, "n": 2}],
            "proactive_export": [{"soc": 70, "discharge_kw": 2.5, "n": 2}],
        }
    }
    cfg = _build_optimizer_config(data, {})
    assert cfg.charge_rate_curve is curve
    assert cfg.boost_charge_rate_curve is curve
    assert cfg.mode_action_soc_bin_rates[PlannerAction.CHARGE_GRID_NORMAL][
        50
    ] == pytest.approx(3.0)
    assert cfg.mode_action_average_rates[
        PlannerAction.EXPORT_PROACTIVE
    ] == pytest.approx(2.5)


def test_build_optimizer_config_self_consumption_floor_and_invalid_mode_rows() -> None:
    data = DummyData()
    data.general_price = 0.0
    data.effective_cheap_price = 0.0
    data.charge_rate_mode_analysis = {
        "soc_bins_1pct_by_mode": {
            "grid_charging": {"soc": 50, "charge_kw": 3.0, "n": 2},
        }
    }
    cfg = _build_optimizer_config(data, {})
    assert cfg.self_consumption_value_per_kwh == pytest.approx(0.10)
    assert cfg.mode_action_soc_bin_rates == {}

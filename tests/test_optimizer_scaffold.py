"""
tests/test_optimizer_scaffold.py — Phase 1 scaffolding tests for #403.

Tests that the DP optimizer skeleton, planner comparator, and shadow runner
can all be imported and produce valid output structures without errors.

These tests run ENTIRELY OFFLINE — no Home Assistant or Solcast data required.
"""

from __future__ import annotations

import pytest

from custom_components.localshift.computation_engine_lib.optimizer_dp import (
    DPPlanner,
    ObjectiveTerms,
    OptimizerConfig,
    OptimizerInputs,
    OptimizerResult,
    PlannedSlotDecision,
    PlannerAction,
    PlannerReasonCode,
    SlotContext,
)
from custom_components.localshift.computation_engine_lib.planner_comparator import (
    MismatchType,
    PlannerComparator,
    SlotMismatch,
)
from custom_components.localshift.const import (
    CONF_OPTIMIZER_CONTROL_MODE,
    CONF_OPTIMIZER_ENABLED,
    DEFAULT_OPTIMIZER_CONTROL_MODE,
    DEFAULT_OPTIMIZER_ENABLED,
    OPTIMIZER_CONTROL_MODES,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_config() -> OptimizerConfig:
    return OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
    )


@pytest.fixture
def single_slot() -> SlotContext:
    return SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.12,
        sell_price=0.08,
        solar_kwh=1.2,
        consumption_kwh=0.4,
    )


@pytest.fixture
def multi_slots() -> list[SlotContext]:
    """48 x 30-min slots — one full day."""
    return [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{(i // 2):02d}:{(i % 2) * 30:02d}:00",
            slot_interval_minutes=30,
            buy_price=0.10 + 0.01 * (i % 10),
            sell_price=0.06,
            solar_kwh=max(0.0, 2.5 - abs(i - 24) * 0.1),  # peaks around noon
            consumption_kwh=0.35,
        )
        for i in range(48)
    ]


# ---------------------------------------------------------------------------
# OptimizerConfig tests
# ---------------------------------------------------------------------------


def test_optimizer_config_defaults():
    config = OptimizerConfig(battery_capacity_kwh=13.5)
    assert config.battery_capacity_kwh == 13.5
    assert config.charge_rate_kw == 3.3  # default
    assert config.demand_window_target_soc_pct == 80.0  # default


def test_optimizer_config_custom():
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        charge_rate_kw=5.0,
        demand_window_target_soc_pct=90.0,
    )
    assert config.charge_rate_kw == 5.0
    assert config.demand_window_target_soc_pct == 90.0


# ---------------------------------------------------------------------------
# SlotContext tests
# ---------------------------------------------------------------------------


def test_slot_context_fields(single_slot):
    assert single_slot.slot_index == 0
    assert single_slot.buy_price == pytest.approx(0.12)
    assert single_slot.solar_kwh == pytest.approx(1.2)


def test_slot_context_derived_flags(single_slot):
    """Demand window and daylight flags should derive from defaults."""
    assert single_slot.is_demand_window_slot is False
    assert single_slot.is_demand_window_entry is False


# ---------------------------------------------------------------------------
# DPPlanner — single slot
# ---------------------------------------------------------------------------


def test_dp_planner_single_slot_returns_result(default_config, single_slot):
    inputs = OptimizerInputs(
        cycle_id="test-single",
        initial_soc_pct=55.0,
        slots=[single_slot],
        config=default_config,
    )
    result = DPPlanner().plan(inputs)
    assert isinstance(result, OptimizerResult)
    assert result.success
    assert result.total_slots == 1
    assert len(result.decisions) == 1


def test_dp_planner_single_slot_decision_structure(default_config, single_slot):
    inputs = OptimizerInputs(
        cycle_id="test-struct",
        initial_soc_pct=55.0,
        slots=[single_slot],
        config=default_config,
    )
    result = DPPlanner().plan(inputs)
    d = result.decisions[0]
    assert isinstance(d, PlannedSlotDecision)
    assert d.slot_index == 0
    assert isinstance(d.action, PlannerAction)
    assert isinstance(d.reason_code, PlannerReasonCode)
    assert isinstance(d.objective_terms, ObjectiveTerms)
    assert 0.0 <= d.predicted_soc_pct <= 100.0
    assert d.grid_import_kwh >= 0.0
    assert d.grid_export_kwh >= 0.0


def test_dp_planner_hold_action_for_sunny_cheap_slot(default_config):
    """When solar > consumption and price is not cheap, hold is expected."""
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T12:00:00",
        slot_interval_minutes=30,
        buy_price=0.35,  # expensive
        sell_price=0.10,
        solar_kwh=2.0,
        consumption_kwh=0.5,
    )
    inputs = OptimizerInputs(
        cycle_id="sunny-expensive",
        initial_soc_pct=60.0,
        slots=[slot],
        config=default_config,
    )
    result = DPPlanner().plan(inputs)
    assert result.success
    assert result.decisions[0].action == PlannerAction.HOLD


def test_dp_planner_no_negative_soc(default_config, multi_slots):
    """Predicted SOC must never go below 0 across a full day of slots."""
    inputs = OptimizerInputs(
        cycle_id="no-neg-soc",
        initial_soc_pct=20.0,
        slots=multi_slots,
        config=default_config,
    )
    result = DPPlanner().plan(inputs)
    assert result.success
    for d in result.decisions:
        assert d.predicted_soc_pct >= 0.0, f"Negative SOC at slot {d.slot_index}"


def test_dp_planner_reason_code_histogram(default_config, multi_slots):
    """Reason code histogram must be populated."""
    inputs = OptimizerInputs(
        cycle_id="histogram",
        initial_soc_pct=50.0,
        slots=multi_slots,
        config=default_config,
    )
    result = DPPlanner().plan(inputs)
    assert isinstance(result.reason_code_histogram, dict)
    # Total decisions in histogram == total slots
    assert sum(result.reason_code_histogram.values()) == len(multi_slots)


def test_dp_planner_solve_time_populated(default_config, multi_slots):
    inputs = OptimizerInputs(
        cycle_id="timing",
        initial_soc_pct=50.0,
        slots=multi_slots,
        config=default_config,
    )
    result = DPPlanner().plan(inputs)
    assert result.solve_time_seconds >= 0.0


def test_dp_planner_empty_slots(default_config):
    """Empty slot list should return a valid but empty result."""
    inputs = OptimizerInputs(
        cycle_id="empty",
        initial_soc_pct=50.0,
        slots=[],
        config=default_config,
    )
    result = DPPlanner().plan(inputs)
    # Scaffold returns success=True with zero decisions for empty input
    assert result.success is True
    assert result.total_slots == 0
    assert result.decisions == []


def test_dp_planner_version_string():
    assert isinstance(DPPlanner.VERSION, str)
    assert len(DPPlanner.VERSION) > 0


# ---------------------------------------------------------------------------
# ObjectiveTerms
# ---------------------------------------------------------------------------


def test_objective_terms_net_cost():
    terms = ObjectiveTerms(
        import_cost=1.50,
        export_revenue=0.80,
        cycle_penalty=0.05,
        shortfall_penalty=0.0,
    )
    assert terms.net_cost == pytest.approx(1.50 - 0.80 + 0.05)


def test_objective_terms_to_dict():
    terms = ObjectiveTerms(import_cost=1.0, export_revenue=0.5)
    d = terms.to_dict()
    assert "import_cost" in d
    assert "net_cost" in d
    assert d["net_cost"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# PlannerComparator tests
# ---------------------------------------------------------------------------


@pytest.fixture
def hold_result(default_config, single_slot) -> OptimizerResult:
    inputs = OptimizerInputs(
        cycle_id="cmp-single",
        initial_soc_pct=55.0,
        slots=[single_slot],
        config=default_config,
    )
    return DPPlanner().plan(inputs)


def test_comparator_no_mismatch_when_both_hold(hold_result):
    legacy_slots = [
        {
            "slot_index": 0,
            "timestamp_iso": "2026-01-03T10:00:00",
            "slot_interval_minutes": 30,
            "grid_charge": False,
            "grid_charge_boost": False,
            "proactive_export": False,
        }
    ]
    comp = PlannerComparator()
    record = comp.compare(
        cycle_id="no-mismatch",
        cycle_timestamp_iso="2026-01-03T10:00:00",
        legacy_slots=legacy_slots,
        optimizer_decisions=hold_result.decisions,
    )
    assert record.comparison_succeeded
    assert record.mismatch_count == 0
    assert record.aligned_slots == 1


def test_comparator_detects_action_mismatch(default_config, single_slot, hold_result):
    """Legacy says charge, optimizer says hold → ACTION_MISMATCH."""
    legacy_slots = [
        {
            "slot_index": 0,
            "timestamp_iso": "2026-01-03T10:00:00",
            "slot_interval_minutes": 30,
            "grid_charge": True,  # legacy charges
            "grid_charge_boost": False,
            "proactive_export": False,
        }
    ]
    comp = PlannerComparator()
    record = comp.compare(
        cycle_id="mismatch-test",
        cycle_timestamp_iso="2026-01-03T10:00:00",
        legacy_slots=legacy_slots,
        optimizer_decisions=hold_result.decisions,
    )
    assert record.comparison_succeeded
    assert record.mismatch_count == 1
    assert record.mismatch_by_type.get(MismatchType.ACTION_MISMATCH.value, 0) == 1


def test_comparator_to_dict_complete(hold_result):
    legacy_slots = [{"grid_charge": False, "proactive_export": False}]
    comp = PlannerComparator()
    record = comp.compare(
        cycle_id="dict-test",
        cycle_timestamp_iso="2026-01-03T10:00:00",
        legacy_slots=legacy_slots,
        optimizer_decisions=hold_result.decisions,
    )
    d = record.to_dict()
    required_keys = [
        "cycle_id",
        "total_slots",
        "aligned_slots",
        "mismatch_count",
        "net_cost_delta",
        "comparison_succeeded",
    ]
    for key in required_keys:
        assert key in d, f"Missing key in to_dict(): {key}"


def test_comparator_cost_deltas(hold_result):
    comp = PlannerComparator()
    record = comp.compare(
        cycle_id="delta-test",
        cycle_timestamp_iso="2026-01-03T10:00:00",
        legacy_slots=[{"grid_charge": False, "proactive_export": False}],
        optimizer_decisions=hold_result.decisions,
        legacy_projected_net_cost=1.50,
        optimizer_projected_net_cost=1.20,
    )
    assert record.net_cost_delta == pytest.approx(1.20 - 1.50)
    assert record.import_kwh_delta == pytest.approx(0.0)  # defaults


def test_comparator_handles_empty_slots():
    comp = PlannerComparator()
    record = comp.compare(
        cycle_id="empty-both",
        cycle_timestamp_iso="2026-01-03T10:00:00",
        legacy_slots=[],
        optimizer_decisions=[],
    )
    assert record.comparison_succeeded
    assert record.total_slots == 0
    assert record.mismatch_count == 0


# ---------------------------------------------------------------------------
# SlotMismatch
# ---------------------------------------------------------------------------


def test_slot_mismatch_to_dict():
    m = SlotMismatch(
        slot_index=3,
        timestamp_iso="2026-01-03T11:30:00",
        slot_interval_minutes=30,
        mismatch_type=MismatchType.ACTION_MISMATCH,
        legacy_action="charge_grid_normal",
        optimizer_action="hold",
    )
    d = m.to_dict()
    assert d["slot_index"] == 3
    assert d["mismatch_type"] == "ACTION_MISMATCH"
    assert d["legacy_action"] == "charge_grid_normal"
    assert d["optimizer_action"] == "hold"


# ---------------------------------------------------------------------------
# Rollout config constants
# ---------------------------------------------------------------------------


def test_rollout_constants_defaults():
    assert DEFAULT_OPTIMIZER_ENABLED is False
    assert DEFAULT_OPTIMIZER_CONTROL_MODE == "shadow"
    assert "shadow" in OPTIMIZER_CONTROL_MODES
    assert "active" not in OPTIMIZER_CONTROL_MODES  # not yet released


def test_rollout_constants_keys():
    assert CONF_OPTIMIZER_ENABLED == "optimizer_enabled"
    assert CONF_OPTIMIZER_CONTROL_MODE == "optimizer_control_mode"

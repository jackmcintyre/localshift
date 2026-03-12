"""Tests for engine/types.py — verify all type symbols are importable and correct."""

from __future__ import annotations

import pytest
from custom_components.localshift.engine.types import (
    PlannerAction,
    PlannerReasonCode,
    SlotContext,
    OptimizerConfig,
    ObjectiveTerms,
    PlannedSlotDecision,
    OptimizerResult,
    OptimizerInputs,
)


def test_planner_action_values():
    assert PlannerAction.HOLD == "hold"
    assert PlannerAction.CHARGE_GRID_NORMAL == "charge_grid_normal"
    assert PlannerAction.CHARGE_GRID_BOOST == "charge_grid_boost"
    assert PlannerAction.EXPORT_PROACTIVE == "export_proactive"


def test_planner_reason_code_count():
    assert len(PlannerReasonCode) == 11


def test_objective_terms_net_cost():
    t = ObjectiveTerms(import_cost=1.0, export_revenue=0.5)
    assert t.net_cost == pytest.approx(0.5)


def test_objective_terms_to_dict_keys():
    t = ObjectiveTerms()
    d = t.to_dict()
    assert "import_cost" in d
    assert "net_cost" in d


def test_planned_slot_decision_properties():
    psd = PlannedSlotDecision(
        slot_index=0,
        timestamp_iso="2026-01-01T00:00:00",
        slot_interval_minutes=30,
        action=PlannerAction.CHARGE_GRID_NORMAL,
        reason_code=PlannerReasonCode.CHEAP_IMPORT_WINDOW,
        objective_terms=ObjectiveTerms(),
        predicted_soc_pct=50.0,
        grid_import_kwh=0.5,
        grid_export_kwh=0.0,
        solar_kwh=0.0,
        consumption_kwh=0.3,
        buy_price=0.25,
        sell_price=0.10,
    )
    assert psd.grid_charge is True
    assert psd.grid_charge_boost is False
    assert psd.proactive_export is False


def test_optimizer_config_defaults():
    cfg = OptimizerConfig()
    assert cfg.soc_bins > 0
    assert 0 < cfg.min_soc_pct < cfg.max_soc_pct


def test_slot_context_fields():
    sc = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-01T00:00:00",
        slot_interval_minutes=30,
        buy_price=0.25,
        sell_price=0.10,
        solar_kwh=0.5,
        consumption_kwh=0.3,
    )
    assert sc.slot_index == 0


def test_optimizer_inputs_fields():
    cfg = OptimizerConfig()
    oi = OptimizerInputs(cycle_id="test", initial_soc_pct=50.0, slots=[], config=cfg)
    assert oi.cycle_id == "test"


def test_optimizer_result_defaults():
    r = OptimizerResult(success=True, planner_version="dp_v1", decisions=[])
    assert r.success is True

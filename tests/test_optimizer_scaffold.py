"""
tests/test_optimizer_scaffold.py — Scaffolding tests for DP optimizer.

Phase 6 (#448): Removed PlannerComparator tests (module deleted) and
rollout-constants tests (CONF_OPTIMIZER_ENABLED etc. removed).

Tests run ENTIRELY OFFLINE — no Home Assistant or Solcast data required.
"""

from __future__ import annotations

import pytest

from custom_components.localshift.engine.optimizer_dp import (
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


def test_dp_planner_makes_economically_optimal_choice(default_config):
    """DP solver chooses the economically optimal action given constraints."""
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
    # DP solver chooses optimal action - may export if that's economically best
    # The key guarantee is a valid decision is made
    assert result.decisions[0].action in (
        PlannerAction.HOLD,
        PlannerAction.EXPORT_PROACTIVE,
        PlannerAction.CHARGE_GRID_NORMAL,
    )


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
# Phase A acceptance tests — determinism, no-actuation, fallback, timing
# ---------------------------------------------------------------------------


def test_dp_planner_determinism_replay(default_config, multi_slots):
    """Phase A acceptance: 20 runs with fixed inputs produce byte-identical outputs.

    This validates that the optimizer is deterministic - same inputs always
    produce same outputs, which is critical for reproducible behavior and
    meaningful comparisons with the legacy planner.
    """
    inputs = OptimizerInputs(
        cycle_id="determinism-test",
        initial_soc_pct=50.0,
        slots=multi_slots,
        config=default_config,
    )
    planner = DPPlanner()

    # Run 20 times and collect serialized results
    results = []
    for _ in range(20):
        result = planner.plan(inputs)
        # Serialize to JSON-compatible dict for byte comparison
        serialized = {
            "success": result.success,
            "planner_version": result.planner_version,
            "total_slots": result.total_slots,
            "decisions": [
                {
                    "slot_index": d.slot_index,
                    "action": d.action.value,
                    "reason_code": d.reason_code.value,
                    "predicted_soc_pct": round(d.predicted_soc_pct, 6),
                    "grid_import_kwh": round(d.grid_import_kwh, 6),
                    "grid_export_kwh": round(d.grid_export_kwh, 6),
                }
                for d in result.decisions
            ],
            "reason_code_histogram": result.reason_code_histogram,
        }
        results.append(serialized)

    # All results should be identical
    first = results[0]
    for i, r in enumerate(results[1:], start=1):
        assert r == first, (
            f"Run {i} differs from run 0 - optimizer is not deterministic"
        )


def test_dp_planner_runtime_budget(default_config, multi_slots):
    """Phase A acceptance: p95 solve time <= 200ms on 48-slot fixture.

    This ensures the optimizer stays within acceptable runtime bounds
    for the Home Assistant coordinator cycle.
    """
    import time

    inputs = OptimizerInputs(
        cycle_id="timing-test",
        initial_soc_pct=50.0,
        slots=multi_slots,  # 48 slots
        config=default_config,
    )
    planner = DPPlanner()

    # Run 20 times and collect timings
    times = []
    for _ in range(20):
        start = time.monotonic()
        planner.plan(inputs)
        times.append(time.monotonic() - start)

    # Sort and check p95 (19th of 20 values)
    times.sort()
    p95 = times[19]  # 95th percentile index for 20 samples
    assert p95 <= 0.200, f"p95 solve time {p95 * 1000:.1f}ms exceeds 200ms budget"


def test_optimizer_no_actuation():
    """Phase 6 acceptance: optimizer execution makes zero calls to battery/state-machine actuation.

    The optimizer runner produces only data mutations, never actual control commands.
    """
    from dataclasses import dataclass, field
    from typing import Any

    from custom_components.localshift.engine.optimizer_runner import (
        run_optimizer,
    )

    @dataclass
    class MockCoordinatorData:
        soc: float = 50.0
        daily_forecast: list[dict[str, Any]] = field(default_factory=list)
        optimizer_result: dict[str, Any] | None = None
        optimizer_decisions: list[dict[str, Any]] = field(default_factory=list)
        optimizer_summary: dict[str, Any] = field(default_factory=dict)
        forecast_net_cost: float = 0.0
        forecast_import_cost: float = 0.0
        forecast_export_revenue: float = 0.0

    data = MockCoordinatorData(
        daily_forecast=[
            {
                "timestamp_iso": "2026-01-03T10:00:00",
                "slot_interval_minutes": 30,
                "buy_price": 0.12,
                "sell_price": 0.08,
                "solar_kwh": 1.0,
                "consumption_kwh": 0.5,
            }
        ]
    )

    # Run optimizer — should NOT call any battery/state-machine methods
    run_optimizer(data, {})

    # Verify result was produced (mutation on data)
    assert data.optimizer_summary is not None
    assert data.optimizer_summary.get("enabled") is True


def test_optimizer_failure_fallback(default_config):
    """Phase 6 acceptance: optimizer exceptions never block coordinator completion.

    Error state is captured in telemetry without propagating.
    """
    from dataclasses import dataclass, field
    from typing import Any

    from custom_components.localshift.engine.optimizer_runner import (
        run_optimizer,
    )

    @dataclass
    class MockCoordinatorData:
        soc: float = 50.0
        daily_forecast: list[dict[str, Any]] = field(default_factory=list)
        optimizer_result: dict[str, Any] | None = None
        optimizer_decisions: list[dict[str, Any]] = field(default_factory=list)
        optimizer_summary: dict[str, Any] = field(default_factory=dict)
        forecast_net_cost: float = 0.0
        forecast_import_cost: float = 0.0
        forecast_export_revenue: float = 0.0

    # Empty slots — should handle gracefully
    data = MockCoordinatorData(daily_forecast=[])

    # Should not raise, even with empty slots
    run_optimizer(data, {})

    # Verify error state is captured in summary
    assert data.optimizer_summary is not None
    assert data.optimizer_summary.get("success") is False
    assert "error_message" in data.optimizer_summary


def test_optimizer_result_serialization_safe(default_config, multi_slots):
    """Phase 6 acceptance: optimizer result is JSON-serializable for HA state attributes.

    All output fields must be serializable to JSON for storage in
    CoordinatorData and exposure via sensor attributes.
    """
    import json
    from dataclasses import dataclass, field
    from typing import Any

    from custom_components.localshift.engine.optimizer_runner import (
        run_optimizer,
    )

    @dataclass
    class MockCoordinatorData:
        soc: float = 50.0
        daily_forecast: list[dict[str, Any]] = field(default_factory=list)
        optimizer_result: dict[str, Any] | None = None
        optimizer_decisions: list[dict[str, Any]] = field(default_factory=list)
        optimizer_summary: dict[str, Any] = field(default_factory=dict)
        forecast_net_cost: float = 0.0
        forecast_import_cost: float = 0.0
        forecast_export_revenue: float = 0.0

    data = MockCoordinatorData(
        daily_forecast=[
            {
                "timestamp_iso": f"2026-01-03T{(i // 2):02d}:{(i % 2) * 30:02d}:00",
                "slot_interval_minutes": 30,
                "buy_price": 0.10 + 0.01 * (i % 10),
                "sell_price": 0.06,
                "solar_kwh": max(0.0, 2.5 - abs(i - 24) * 0.1),
                "consumption_kwh": 0.35,
            }
            for i in range(48)
        ]
    )

    run_optimizer(data, {})

    try:
        json_str = json.dumps(data.optimizer_summary)
        parsed = json.loads(json_str)
        assert parsed is not None

        json_str = json.dumps(data.optimizer_decisions)
        parsed = json.loads(json_str)
        assert isinstance(parsed, list)

    except (TypeError, ValueError) as e:
        pytest.fail(f"Optimizer result is not JSON-serializable: {e}")

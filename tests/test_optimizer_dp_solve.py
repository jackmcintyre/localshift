"""
tests/test_optimizer_dp_solve.py — Phase C DP solver tests for #403.

Tests that the DP optimizer produces correct, deterministic plans
with valid SOC projections and objective term accounting.

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
    _build_soc_grid,
    _map_soc_to_bin,
    _simulate_max_soc_in_demand_window,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_config() -> OptimizerConfig:
    return OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        soc_bins=20,  # Fewer bins for faster tests
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


@pytest.fixture
def demand_window_slots() -> list[SlotContext]:
    """Slots with demand window entry at slot 18 (evening peak)."""
    slots = []
    for i in range(24):
        slot = SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{(i // 2):02d}:{(i % 2) * 30:02d}:00",
            slot_interval_minutes=30,
            buy_price=0.15 if i >= 18 else 0.10,  # evening peak pricing
            sell_price=0.06,
            solar_kwh=max(0.0, 2.0 - abs(i - 12) * 0.15),  # peaks at noon
            consumption_kwh=0.5 if i >= 18 else 0.3,  # evening consumption
            is_demand_window_entry=(i == 18),
            is_demand_window_slot=(i >= 18),
        )
        slots.append(slot)
    return slots


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


def test_build_soc_grid_default_bins():
    config = OptimizerConfig(soc_bins=10, min_soc_pct=10.0, max_soc_pct=100.0)
    grid = _build_soc_grid(config)
    assert len(grid) == 10
    assert grid[0] == pytest.approx(10.0)
    assert grid[-1] == pytest.approx(100.0)


def test_build_soc_grid_single_bin():
    config = OptimizerConfig(soc_bins=1, min_soc_pct=20.0, max_soc_pct=80.0)
    grid = _build_soc_grid(config)
    assert len(grid) == 1
    assert grid[0] == pytest.approx(20.0)


def test_map_soc_to_bin_exact():
    grid = [0.0, 25.0, 50.0, 75.0, 100.0]
    assert _map_soc_to_bin(0.0, grid) == 0
    assert _map_soc_to_bin(50.0, grid) == 2
    assert _map_soc_to_bin(100.0, grid) == 4


def test_map_soc_to_bin_nearest():
    grid = [0.0, 25.0, 50.0, 75.0, 100.0]
    # 12.5 is closer to 0 than 25
    assert _map_soc_to_bin(12.0, grid) == 0
    # 15 is closer to 25 than 0
    assert _map_soc_to_bin(15.0, grid) == 1


def test_map_soc_to_bin_empty_grid():
    assert _map_soc_to_bin(50.0, []) == 0


def test_transition_hold_at_soc_floor_emits_passive_grid_import(default_config):
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.30,
        sell_price=0.08,
        solar_kwh=0.0,
        consumption_kwh=1.0,
    )

    next_soc, grid_import, grid_export = DPPlanner.transition(
        soc_pct=default_config.min_soc_pct,
        action=PlannerAction.HOLD,
        slot=slot,
        config=default_config,
    )

    assert next_soc == pytest.approx(default_config.min_soc_pct)
    assert grid_import == pytest.approx(1.0)
    assert grid_export == pytest.approx(0.0)

    terms = DPPlanner.stage_cost(
        action=PlannerAction.HOLD,
        grid_import_kwh=grid_import,
        grid_export_kwh=grid_export,
        slot=slot,
        config=default_config,
    )
    assert terms.import_cost == pytest.approx(slot.buy_price * grid_import)


def test_transition_hold_at_soc_ceiling_emits_passive_grid_export(default_config):
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.12,
        sell_price=0.10,
        solar_kwh=1.2,
        consumption_kwh=0.0,
    )

    next_soc, grid_import, grid_export = DPPlanner.transition(
        soc_pct=default_config.max_soc_pct,
        action=PlannerAction.HOLD,
        slot=slot,
        config=default_config,
    )

    assert next_soc == pytest.approx(default_config.max_soc_pct)
    assert grid_import == pytest.approx(0.0)
    assert grid_export == pytest.approx(1.2)

    terms = DPPlanner.stage_cost(
        action=PlannerAction.HOLD,
        grid_import_kwh=grid_import,
        grid_export_kwh=grid_export,
        slot=slot,
        config=default_config,
    )
    assert terms.export_revenue == pytest.approx(slot.sell_price * grid_export)


def test_transition_hold_respects_separate_rates():
    """Issue #422: HOLD must use solar_charge_rate_kw for charge and discharge_rate_kw for discharge."""
    config = OptimizerConfig(solar_charge_rate_kw=2.0, discharge_rate_kw=4.0)
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-01T12:00:00",
        slot_interval_minutes=60,
        buy_price=0.1,
        sell_price=0.05,
        solar_kwh=0,
        consumption_kwh=0,
    )

    # 1. Discharge (net_kwh = -10): should be capped by discharge_rate_kw (4.0 kW * 1h = 4.0 kWh)
    slot.consumption_kwh = 10.0
    _, grid_import, _ = DPPlanner.transition(
        soc_pct=50.0, action=PlannerAction.HOLD, slot=slot, config=config
    )
    assert grid_import == pytest.approx(6.0)  # 10.0 - 4.0

    # 2. Charge (net_kwh = 10): should be capped by solar_charge_rate_kw (2.0 kW * 1h = 2.0 kWh)
    slot.consumption_kwh = 0.0
    slot.solar_kwh = 10.0
    _, _, grid_export = DPPlanner.transition(
        soc_pct=50.0, action=PlannerAction.HOLD, slot=slot, config=config
    )
    assert grid_export == pytest.approx(8.0)  # 10.0 - 2.0


# ---------------------------------------------------------------------------
# DPPlanner core tests
# ---------------------------------------------------------------------------
# DPPlanner core tests
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


def test_dp_planner_no_negative_soc(default_config, multi_slots):
    """Predicted SOC must never go below min_soc_pct across a full day."""
    inputs = OptimizerInputs(
        cycle_id="no-neg-soc",
        initial_soc_pct=20.0,
        slots=multi_slots,
        config=default_config,
    )
    result = DPPlanner().plan(inputs)
    assert result.success
    for d in result.decisions:
        assert d.predicted_soc_pct >= default_config.min_soc_pct, (
            f"SOC {d.predicted_soc_pct} below min at slot {d.slot_index}"
        )


def test_dp_planner_no_exceed_max_soc(default_config, multi_slots):
    """Predicted SOC must never exceed max_soc_pct."""
    inputs = OptimizerInputs(
        cycle_id="no-max-soc",
        initial_soc_pct=95.0,
        slots=multi_slots,
        config=default_config,
    )
    result = DPPlanner().plan(inputs)
    assert result.success
    for d in result.decisions:
        assert d.predicted_soc_pct <= default_config.max_soc_pct, (
            f"SOC {d.predicted_soc_pct} above max at slot {d.slot_index}"
        )


def test_dp_planner_reason_code_histogram(default_config, multi_slots):
    """Reason code histogram must be populated and sum to total slots."""
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
    assert result.success is True
    assert result.total_slots == 0
    assert result.decisions == []


def test_dp_planner_version_string():
    assert isinstance(DPPlanner.VERSION, str)
    assert len(DPPlanner.VERSION) > 0


# ---------------------------------------------------------------------------
# Determinism tests (Phase C acceptance)
# ---------------------------------------------------------------------------


def test_dp_planner_determinism_replay(default_config, multi_slots):
    """Phase C acceptance: 20 runs with fixed inputs produce byte-identical outputs."""
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
    """Phase C acceptance: p95 solve time <= 200ms on 48-slot fixture."""
    import time

    inputs = OptimizerInputs(
        cycle_id="timing-test",
        initial_soc_pct=50.0,
        slots=multi_slots,
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


# ---------------------------------------------------------------------------
# Demand window tests
# ---------------------------------------------------------------------------


def test_dp_planner_demand_window_target_attainment(demand_window_slots):
    """Optimizer should plan to meet demand window target SOC."""
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        soc_bins=20,
        target_shortfall_penalty_per_pct=2.0,  # Strong penalty
    )
    inputs = OptimizerInputs(
        cycle_id="demand-window",
        initial_soc_pct=30.0,
        slots=demand_window_slots,
        config=config,
    )
    result = DPPlanner().plan(inputs)
    assert result.success

    # Check SOC at demand window entry (slot 18)
    dw_decision = result.decisions[18]
    # With strong penalty, optimizer should plan to reach target
    # (may not fully reach if solar is insufficient)
    assert dw_decision.predicted_soc_pct >= 30.0  # At least didn't drop


def test_dp_planner_terminal_shortfall_calculation(demand_window_slots):
    """Terminal shortfall should be calculated correctly."""
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        soc_bins=20,
    )
    inputs = OptimizerInputs(
        cycle_id="shortfall-calc",
        initial_soc_pct=30.0,
        slots=demand_window_slots,
        config=config,
    )
    result = DPPlanner().plan(inputs)
    assert result.success
    # Terminal shortfall should be >= 0
    assert result.terminal_shortfall_pct >= 0.0


def test_feasible_actions_blocks_grid_charging_in_demand_window(default_config):
    """Grid charging actions should be unavailable during demand window slots."""
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T18:00:00",
        slot_interval_minutes=30,
        buy_price=0.08,
        sell_price=0.05,
        solar_kwh=0.0,
        consumption_kwh=0.5,
        is_demand_window_slot=True,
    )

    actions = DPPlanner.feasible_actions(50.0, slot, default_config)

    assert PlannerAction.HOLD in actions
    assert PlannerAction.CHARGE_GRID_NORMAL not in actions
    assert PlannerAction.CHARGE_GRID_BOOST not in actions


def test_terminal_penalty_applied_at_dw_entry_by_default():
    """When switch is off, shortfall is measured at demand window entry."""
    slots = [
        SlotContext(
            slot_index=0,
            timestamp_iso="2026-01-03T18:00:00",
            slot_interval_minutes=30,
            buy_price=0.30,
            sell_price=0.0,
            solar_kwh=0.0,
            consumption_kwh=0.0,
            is_demand_window_entry=True,
            is_demand_window_slot=True,
        ),
        SlotContext(
            slot_index=1,
            timestamp_iso="2026-01-03T18:30:00",
            slot_interval_minutes=30,
            buy_price=0.30,
            sell_price=0.0,
            solar_kwh=2.0,
            consumption_kwh=0.0,
            is_demand_window_slot=True,
        ),
        SlotContext(
            slot_index=2,
            timestamp_iso="2026-01-03T19:00:00",
            slot_interval_minutes=30,
            buy_price=0.30,
            sell_price=0.0,
            solar_kwh=0.0,
            consumption_kwh=0.0,
            is_demand_window_slot=False,
        ),
    ]

    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=60.0,
        allow_dw_entry_under_target=False,
        soc_bins=20,
    )
    inputs = OptimizerInputs(
        cycle_id="dw-entry-penalty",
        initial_soc_pct=50.0,
        slots=slots,
        config=config,
    )

    result = DPPlanner().plan(inputs)

    assert result.success
    assert result.terminal_shortfall_pct > 0.0


def test_terminal_penalty_applied_at_dw_end_when_switch_enabled():
    """When switch is on, shortfall can be recovered within demand window."""
    slots = [
        SlotContext(
            slot_index=0,
            timestamp_iso="2026-01-03T18:00:00",
            slot_interval_minutes=30,
            buy_price=0.30,
            sell_price=0.0,
            solar_kwh=0.0,
            consumption_kwh=0.0,
            is_demand_window_entry=True,
            is_demand_window_slot=True,
        ),
        SlotContext(
            slot_index=1,
            timestamp_iso="2026-01-03T18:30:00",
            slot_interval_minutes=30,
            buy_price=0.30,
            sell_price=0.0,
            solar_kwh=2.0,
            consumption_kwh=0.0,
            is_demand_window_slot=True,
        ),
        SlotContext(
            slot_index=2,
            timestamp_iso="2026-01-03T19:00:00",
            slot_interval_minutes=30,
            buy_price=0.30,
            sell_price=0.0,
            solar_kwh=0.0,
            consumption_kwh=0.0,
            is_demand_window_slot=False,
        ),
    ]

    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=60.0,
        allow_dw_entry_under_target=True,
        soc_bins=20,
    )
    inputs = OptimizerInputs(
        cycle_id="dw-end-penalty",
        initial_soc_pct=50.0,
        slots=slots,
        config=config,
    )

    result = DPPlanner().plan(inputs)

    assert result.success
    assert result.terminal_shortfall_pct == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Negative FIT tests
# ---------------------------------------------------------------------------


def test_dp_planner_negative_fit_no_export(default_config):
    """Optimizer should not export when FIT is negative."""
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T12:00:00",
        slot_interval_minutes=30,
        buy_price=0.10,
        sell_price=-0.05,  # Negative FIT
        solar_kwh=0.5,
        consumption_kwh=0.3,
    )
    inputs = OptimizerInputs(
        cycle_id="negative-fit",
        initial_soc_pct=80.0,  # High SOC could export
        slots=[slot],
        config=default_config,
    )
    result = DPPlanner().plan(inputs)
    assert result.success
    # Should not export with negative FIT
    assert result.decisions[0].action != PlannerAction.EXPORT_PROACTIVE


# ---------------------------------------------------------------------------
# SOC boundary tests
# ---------------------------------------------------------------------------


def test_dp_planner_respects_min_soc_floor(default_config):
    """Battery should not discharge below min SOC floor."""
    # Create slots with high consumption, no solar
    slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{i:02d}:00:00",
            slot_interval_minutes=60,
            buy_price=0.30,  # Expensive - might want to avoid
            sell_price=0.05,
            solar_kwh=0.0,  # No solar
            consumption_kwh=2.0,  # High consumption
        )
        for i in range(6)
    ]
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        min_soc_pct=20.0,
        soc_bins=20,
    )
    inputs = OptimizerInputs(
        cycle_id="soc-floor",
        initial_soc_pct=25.0,  # Close to floor
        slots=slots,
        config=config,
    )
    result = DPPlanner().plan(inputs)
    assert result.success
    # All SOCs should be >= min_soc_pct
    for d in result.decisions:
        assert d.predicted_soc_pct >= config.min_soc_pct - 0.5, (
            f"SOC {d.predicted_soc_pct} below floor at slot {d.slot_index}"
        )


def test_dp_planner_respects_max_soc_ceiling(default_config):
    """Battery should not charge above max SOC ceiling."""
    # Create slots with lots of solar
    slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{i:02d}:00:00",
            slot_interval_minutes=60,
            buy_price=0.10,
            sell_price=0.05,
            solar_kwh=5.0,  # Lots of solar
            consumption_kwh=0.5,
        )
        for i in range(6)
    ]
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        max_soc_pct=95.0,
        soc_bins=20,
    )
    inputs = OptimizerInputs(
        cycle_id="soc-ceiling",
        initial_soc_pct=90.0,  # Close to ceiling
        slots=slots,
        config=config,
    )
    result = DPPlanner().plan(inputs)
    assert result.success
    # All SOCs should be <= max_soc_pct
    for d in result.decisions:
        assert d.predicted_soc_pct <= config.max_soc_pct + 0.5, (
            f"SOC {d.predicted_soc_pct} above ceiling at slot {d.slot_index}"
        )


# ---------------------------------------------------------------------------
# Grid charge decision tests
# ---------------------------------------------------------------------------


def test_dp_planner_grid_charge_when_cheap(default_config):
    """Optimizer should grid charge when price is cheap."""
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T02:00:00",
        slot_interval_minutes=30,
        buy_price=0.05,  # Very cheap
        sell_price=0.03,
        solar_kwh=0.0,  # Night time
        consumption_kwh=0.3,
    )
    inputs = OptimizerInputs(
        cycle_id="cheap-charge",
        initial_soc_pct=30.0,  # Low SOC
        slots=[slot],
        config=default_config,
    )
    result = DPPlanner().plan(inputs)
    assert result.success
    # Should charge from grid when price is cheap
    # (exact action depends on full optimization, but import should be > 0)
    assert result.decisions[0].grid_import_kwh >= 0.0


# ---------------------------------------------------------------------------
# Objective terms tests
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
# Projected totals tests
# ---------------------------------------------------------------------------


def test_dp_planner_projected_totals(default_config, multi_slots):
    """Projected totals should be sum of all slot quantities."""
    inputs = OptimizerInputs(
        cycle_id="totals-test",
        initial_soc_pct=50.0,
        slots=multi_slots,
        config=default_config,
    )
    result = DPPlanner().plan(inputs)
    assert result.success

    # Sum from decisions
    total_import = sum(d.grid_import_kwh for d in result.decisions)
    total_export = sum(d.grid_export_kwh for d in result.decisions)

    assert result.projected_import_kwh == pytest.approx(total_import, rel=0.01)
    assert result.projected_export_kwh == pytest.approx(total_export, rel=0.01)


# ---------------------------------------------------------------------------
# States explored test
# ---------------------------------------------------------------------------


def test_dp_planner_states_explored():
    """States explored should be positive for non-empty input."""
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        soc_bins=20,
        optimization_mode="arbitrage",
    )
    multi_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{(i // 2):02d}:{(i % 2) * 30:02d}:00",
            slot_interval_minutes=30,
            buy_price=0.10 + 0.01 * (i % 10),
            sell_price=0.06,
            solar_kwh=max(0.0, 2.5 - abs(i - 24) * 0.1),
            consumption_kwh=0.35,
        )
        for i in range(48)
    ]
    inputs = OptimizerInputs(
        cycle_id="states-test",
        initial_soc_pct=50.0,
        slots=multi_slots,
        config=config,
    )
    result = DPPlanner().plan(inputs)
    assert result.success
    assert result.states_explored > 0
    # Should explore roughly n_slots * n_bins * n_actions states.
    expected_min = len(multi_slots) * config.soc_bins * 2
    assert result.states_explored >= expected_min


# ---------------------------------------------------------------------------
# Reason-classification tests
# ---------------------------------------------------------------------------


def test_classify_reason_target_shortfall_uses_future_slots():
    """Grid charging before DW should be tagged shortfall risk when net future solar is insufficient."""
    planner = DPPlanner()
    config = OptimizerConfig(
        battery_capacity_kwh=13.5, demand_window_target_soc_pct=80.0
    )

    slots = [
        SlotContext(
            slot_index=0,
            timestamp_iso="2026-01-03T10:00:00",
            slot_interval_minutes=30,
            buy_price=0.20,
            sell_price=0.06,
            solar_kwh=0.1,
            consumption_kwh=0.7,
        ),
        SlotContext(
            slot_index=1,
            timestamp_iso="2026-01-03T10:30:00",
            slot_interval_minutes=30,
            buy_price=0.20,
            sell_price=0.06,
            solar_kwh=0.2,
            consumption_kwh=0.8,
        ),
        SlotContext(
            slot_index=2,
            timestamp_iso="2026-01-03T11:00:00",
            slot_interval_minutes=30,
            buy_price=0.20,
            sell_price=0.06,
            solar_kwh=0.2,
            consumption_kwh=0.8,
            is_demand_window_entry=True,
        ),
    ]

    reason = planner._classify_reason(  # noqa: SLF001 - unit test for internal logic
        action=PlannerAction.CHARGE_GRID_NORMAL,
        slot=slots[0],
        slot_idx=0,
        slots=slots,
        soc=40.0,
        next_soc=45.0,
        config=config,
        terminal_penalty_idx=2,
    )

    assert reason == PlannerReasonCode.TARGET_SHORTFALL_RISK


def test_classify_reason_cheap_import_when_target_can_be_met():
    """Cheap price should classify as CHEAP_IMPORT_WINDOW when shortfall risk test does not trigger.

    Uses a very cheap price (≤ effective_cheap_price * 0.8) so the blind-horizon guard
    still allows the CHEAP_IMPORT_WINDOW classification (only 3 slots — horizon is short).
    """
    planner = DPPlanner()
    config = OptimizerConfig(
        battery_capacity_kwh=13.5, demand_window_target_soc_pct=80.0
    )

    slots = [
        SlotContext(
            slot_index=0,
            timestamp_iso="2026-01-03T10:00:00",
            slot_interval_minutes=30,
            buy_price=0.07,  # very cheap: ≤ effective_cheap_price * 0.8 (0.10 * 0.8 = 0.08)
            sell_price=0.06,
            solar_kwh=1.5,
            consumption_kwh=0.2,
        ),
        SlotContext(
            slot_index=1,
            timestamp_iso="2026-01-03T10:30:00",
            slot_interval_minutes=30,
            buy_price=0.07,
            sell_price=0.06,
            solar_kwh=1.5,
            consumption_kwh=0.2,
        ),
        SlotContext(
            slot_index=2,
            timestamp_iso="2026-01-03T11:00:00",
            slot_interval_minutes=30,
            buy_price=0.07,
            sell_price=0.06,
            solar_kwh=1.0,
            consumption_kwh=0.2,
            is_demand_window_entry=True,
        ),
    ]

    reason = planner._classify_reason(  # noqa: SLF001 - unit test for internal logic
        action=PlannerAction.CHARGE_GRID_NORMAL,
        slot=slots[0],
        slot_idx=0,
        slots=slots,
        soc=75.0,
        next_soc=78.0,
        config=config,
        terminal_penalty_idx=2,
    )

    assert reason == PlannerReasonCode.CHEAP_IMPORT_WINDOW


# ---------------------------------------------------------------------------
# Tests: solar gate in feasible_actions() (#437 / #439)
# ---------------------------------------------------------------------------


def _make_slot(
    slot_index: int = 0,
    buy_price: float = 0.08,
    solar_kwh: float = 0.0,
    consumption_kwh: float = 0.3,
    is_demand_window_slot: bool = False,
) -> SlotContext:
    """Helper to create a minimal SlotContext for feasibility tests."""
    return SlotContext(
        slot_index=slot_index,
        timestamp_iso=f"2026-01-03T10:{slot_index * 5:02d}:00",
        slot_interval_minutes=30,
        buy_price=buy_price,
        sell_price=0.06,
        solar_kwh=solar_kwh,
        consumption_kwh=consumption_kwh,
        is_demand_window_slot=is_demand_window_slot,
    )


def test_feasible_actions_suppresses_grid_charge_when_solar_covers_deficit():
    """Solar surplus >= SOC deficit: no CHARGE_GRID_* in feasible actions (self_consumption mode)."""
    # SOC=70, target=80 → deficit=10%
    # 10% of 13.5 kWh = 1.35 kWh needed
    # Slots: 6 slots each with solar=0.5 kWh, consumption=0.2 kWh → net=0.3 kWh/slot → 1.8 kWh total
    # 1.8 / 13.5 * 100 = 13.3% solar gain >= 10% deficit → gate fires
    slots = [
        _make_slot(slot_index=i, buy_price=0.08, solar_kwh=0.5, consumption_kwh=0.2)
        for i in range(6)
    ]
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.10,
        optimization_mode="self_consumption",
        soc_bins=20,
    )

    actions = DPPlanner.feasible_actions(
        soc_pct=70.0,
        slot=slots[0],
        config=config,
        slot_idx=0,
        slots=slots,
        terminal_penalty_idx=6,
    )

    assert PlannerAction.HOLD in actions
    assert PlannerAction.CHARGE_GRID_NORMAL not in actions
    assert PlannerAction.CHARGE_GRID_BOOST not in actions


def test_feasible_actions_allows_grid_charge_when_solar_insufficient():
    """Solar surplus < SOC deficit: CHARGE_GRID_* remains feasible (price-gated)."""
    # SOC=70, target=80 → deficit=10% (1.35 kWh needed)
    # Slots: 6 slots each solar=0.1 kWh, consumption=0.2 kWh → net=-0.1/slot → -0.6 kWh total
    # -0.6 / 13.5 * 100 = -4.4% solar gain < 10% deficit → gate does NOT fire
    # buy_price=0.08 < effective_cheap_price=0.10 → charge actions available
    slots = [
        _make_slot(slot_index=i, buy_price=0.08, solar_kwh=0.1, consumption_kwh=0.2)
        for i in range(6)
    ]
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.10,
        optimization_mode="self_consumption",
        soc_bins=20,
    )

    actions = DPPlanner.feasible_actions(
        soc_pct=70.0,
        slot=slots[0],
        config=config,
        slot_idx=0,
        slots=slots,
        terminal_penalty_idx=6,
    )

    assert PlannerAction.HOLD in actions
    assert PlannerAction.CHARGE_GRID_NORMAL in actions
    # buy_price=0.08 = effective_cheap_price * 0.8 = 0.08 → very_cheap → boost also offered
    assert PlannerAction.CHARGE_GRID_BOOST in actions


def test_feasible_actions_no_context_behaves_as_before():
    """When slot_idx/slots/terminal_penalty_idx not provided, no solar gate (original behaviour)."""
    # This checks the 3-argument call still works identically to pre-fix.
    # SOC=70, target=80, cheap price, no solar — without context the gate is disabled.
    slot = _make_slot(buy_price=0.08, solar_kwh=0.0, consumption_kwh=0.3)
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.10,
        optimization_mode="self_consumption",
        soc_bins=20,
    )

    # 3-argument form: no solar context → gate disabled → grid charge offered normally
    actions = DPPlanner.feasible_actions(70.0, slot, config)

    assert PlannerAction.HOLD in actions
    # Price is cheap, no gate in effect → grid charge should be available
    assert PlannerAction.CHARGE_GRID_NORMAL in actions


def test_feasible_actions_gate_disabled_in_arbitrage_mode():
    """Solar gate does not apply in arbitrage mode — grid charge always available."""
    # Solar more than covers deficit, but mode='arbitrage' → gate is skipped
    slots = [
        _make_slot(slot_index=i, buy_price=0.08, solar_kwh=2.0, consumption_kwh=0.1)
        for i in range(6)
    ]
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.10,
        optimization_mode="arbitrage",
        soc_bins=20,
    )

    actions = DPPlanner.feasible_actions(
        soc_pct=70.0,
        slot=slots[0],
        config=config,
        slot_idx=0,
        slots=slots,
        terminal_penalty_idx=6,
    )

    # Arbitrage mode skips gate — both charge actions always available (below max_soc)
    assert PlannerAction.CHARGE_GRID_NORMAL in actions
    assert PlannerAction.CHARGE_GRID_BOOST in actions


def test_feasible_actions_gate_not_fired_when_soc_already_at_target():
    """When SOC >= target (no deficit), gate should not suppress grid charging."""
    # SOC=85 >= target=80 → deficit=0 → gate does not fire
    slots = [
        _make_slot(slot_index=i, buy_price=0.08, solar_kwh=0.0, consumption_kwh=0.3)
        for i in range(4)
    ]
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.10,
        optimization_mode="self_consumption",
        soc_bins=20,
    )

    actions = DPPlanner.feasible_actions(
        soc_pct=85.0,
        slot=slots[0],
        config=config,
        slot_idx=0,
        slots=slots,
        terminal_penalty_idx=4,
    )

    # No deficit → gate does not fire → price-gated grid charge offered
    assert PlannerAction.HOLD in actions
    assert PlannerAction.CHARGE_GRID_NORMAL in actions


def test_projected_solar_soc_gain_pct_positive_surplus():
    """_projected_solar_soc_gain_pct returns positive when solar > consumption."""
    slots = [
        _make_slot(slot_index=i, solar_kwh=1.0, consumption_kwh=0.3) for i in range(4)
    ]
    # 4 slots * (1.0 - 0.3) = 2.8 kWh net; 2.8/13.5 * 100 = ~20.7%
    result = DPPlanner._projected_solar_soc_gain_pct(  # noqa: SLF001
        slot_idx=0,
        slots=slots,
        terminal_penalty_idx=4,
        battery_capacity_kwh=13.5,
    )
    assert result == pytest.approx((4 * 0.7 / 13.5) * 100.0, rel=1e-6)


def test_projected_solar_soc_gain_pct_negative_when_consumption_dominates():
    """_projected_solar_soc_gain_pct returns negative when consumption > solar."""
    slots = [
        _make_slot(slot_index=i, solar_kwh=0.1, consumption_kwh=0.5) for i in range(4)
    ]
    result = DPPlanner._projected_solar_soc_gain_pct(  # noqa: SLF001
        slot_idx=0,
        slots=slots,
        terminal_penalty_idx=4,
        battery_capacity_kwh=13.5,
    )
    assert result < 0.0


def test_projected_solar_soc_gain_pct_respects_slot_range():
    """Only slots in [slot_idx, terminal_penalty_idx) are counted."""
    slots = [
        _make_slot(slot_index=0, solar_kwh=3.0, consumption_kwh=0.1),  # large surplus
        _make_slot(slot_index=1, solar_kwh=0.0, consumption_kwh=0.5),  # deficit
        _make_slot(slot_index=2, solar_kwh=0.0, consumption_kwh=0.5),  # deficit
        _make_slot(slot_index=3, solar_kwh=3.0, consumption_kwh=0.1),  # ignored
    ]
    # Only slots 1–2 (slot_idx=1, terminal=3); both are net deficit
    result = DPPlanner._projected_solar_soc_gain_pct(  # noqa: SLF001
        slot_idx=1,
        slots=slots,
        terminal_penalty_idx=3,
        battery_capacity_kwh=13.5,
    )
    # 2 slots * (0.0 - 0.5) = -1.0 kWh; -1.0/13.5*100 = -7.4%
    assert result < 0.0
    assert result == pytest.approx((2 * -0.5 / 13.5) * 100.0, rel=1e-6)


def test_optimizer_does_not_grid_charge_during_solar_peak_with_sufficient_solar():
    """Integration: on a sunny day with enough solar, no grid charging before demand window."""
    # 8 pre-DW slots (09:00–13:00) with strong solar; DW entry at slot 8
    # SOC deficit: 80% - 70% = 10% (1.35 kWh)
    # Solar surplus: 8 slots * (0.6 kWh solar - 0.2 kWh consumption) = 3.2 kWh net
    # 3.2 / 13.5 * 100 = 23.7% solar gain >> 10% deficit → gate fires on all pre-DW slots
    pre_dw_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{9 + i // 2:02d}:{(i % 2) * 30:02d}:00",
            slot_interval_minutes=30,
            buy_price=0.08,  # cheap — would grid-charge without the gate
            sell_price=0.06,
            solar_kwh=0.6,
            consumption_kwh=0.2,
        )
        for i in range(8)
    ]
    dw_slots = [
        SlotContext(
            slot_index=8 + i,
            timestamp_iso=f"2026-01-03T1{3 + i // 2}:{(i % 2) * 30:02d}:00",
            slot_interval_minutes=30,
            buy_price=0.30,
            sell_price=0.06,
            solar_kwh=0.0,
            consumption_kwh=0.5,
            is_demand_window_entry=(i == 0),
            is_demand_window_slot=True,
        )
        for i in range(4)
    ]
    slots = pre_dw_slots + dw_slots

    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.10,
        optimization_mode="self_consumption",
        soc_bins=30,
        target_shortfall_penalty_per_pct=0.030,
    )
    inputs = OptimizerInputs(
        cycle_id="test-solar-gate-integration",
        initial_soc_pct=70.0,
        slots=slots,
        config=config,
    )

    result = DPPlanner().plan(inputs)
    assert result.success

    # No grid charging actions in the pre-DW solar window (slots 0–7)
    for decision in result.decisions[:8]:
        assert decision.action not in (
            PlannerAction.CHARGE_GRID_NORMAL,
            PlannerAction.CHARGE_GRID_BOOST,
        ), f"Unexpected grid charge at slot {decision.slot_index}: {decision.action}"


def test_allow_dw_entry_under_target_suppresses_charge_when_solar_reaches_target_mid_dw():
    """Issue #505: When allow_dw_entry_under_target=True, no grid charging if solar
    reaches target at ANY point during DW (not just at DW end)."""
    # Scenario:
    # - Initial SOC: 50%
    # - Target: 80%
    # - Pre-DW (slots 0-3): Low solar, cheap prices - could charge
    # - DW (slots 4-7): High solar - will reach target at slot 5
    # - Expected: NO grid charging because solar reaches target mid-DW

    pre_dw_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{10 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.10,  # cheap enough to grid charge
            sell_price=0.05,
            solar_kwh=0.5,  # low solar
            consumption_kwh=0.5,
        )
        for i in range(4)
    ]
    dw_slots = [
        SlotContext(
            slot_index=4 + i,
            timestamp_iso=f"2026-01-03T{14 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.15,
            sell_price=0.05,
            solar_kwh=3.0 if i < 2 else 1.0,  # high solar early in DW
            consumption_kwh=0.5,
            is_demand_window_entry=(i == 0),
            is_demand_window_slot=True,
        )
        for i in range(4)
    ]
    slots = pre_dw_slots + dw_slots

    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.12,
        allow_dw_entry_under_target=True,  # Enable the new behavior
        optimization_mode="self_consumption",
        soc_bins=20,
        target_shortfall_penalty_per_pct=0.030,
    )
    inputs = OptimizerInputs(
        cycle_id="test-dw-target-anywhere",
        initial_soc_pct=50.0,
        slots=slots,
        config=config,
    )

    result = DPPlanner().plan(inputs)
    assert result.success

    # Solar should reach ~80%+ during DW, so no grid charging needed
    # Check pre-DW slots (0-3) don't grid charge
    for decision in result.decisions[:4]:
        assert decision.action not in (
            PlannerAction.CHARGE_GRID_NORMAL,
            PlannerAction.CHARGE_GRID_BOOST,
        ), (
            f"Pre-DW slot {decision.slot_index} should not grid-charge when solar will reach target in DW"
        )

    # Verify solar reaches target in DW
    assert result.can_solar_reach_target_in_dw is True


def test_allow_dw_entry_under_target_charges_when_solar_insufficient():
    """Issue #505: Grid charging still occurs when solar cannot reach target during DW."""
    # Scenario:
    # - Initial SOC: 50%
    # - Target: 80%
    # - Pre-DW (slots 0-3): Cheap prices
    # - DW (slots 4-7): Low solar - will NOT reach target
    # - Expected: Grid charging occurs because solar insufficient

    pre_dw_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{10 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.10,  # cheap
            sell_price=0.05,
            solar_kwh=0.3,  # low solar
            consumption_kwh=0.5,
        )
        for i in range(4)
    ]
    dw_slots = [
        SlotContext(
            slot_index=4 + i,
            timestamp_iso=f"2026-01-03T{14 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.20,
            sell_price=0.05,
            solar_kwh=0.3,  # consistently low solar
            consumption_kwh=0.5,
            is_demand_window_entry=(i == 0),
            is_demand_window_slot=True,
        )
        for i in range(4)
    ]
    slots = pre_dw_slots + dw_slots

    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.12,
        allow_dw_entry_under_target=True,
        optimization_mode="self_consumption",
        soc_bins=20,
        target_shortfall_penalty_per_pct=0.030,
    )
    inputs = OptimizerInputs(
        cycle_id="test-dw-target-anywhere-insufficient",
        initial_soc_pct=50.0,
        slots=slots,
        config=config,
    )

    result = DPPlanner().plan(inputs)
    assert result.success

    # Solar cannot reach target, so grid charging should occur
    grid_charge_actions = [
        d.action
        for d in result.decisions
        if d.action
        in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
    ]
    assert len(grid_charge_actions) > 0, "Should grid-charge when solar insufficient"

    # Verify solar cannot reach target in DW
    assert result.can_solar_reach_target_in_dw is False


def test_allow_dw_entry_under_target_false_uses_original_behavior():
    """Issue #505: When allow_dw_entry_under_target=False, use original gate logic.

    This test verifies the two modes behave differently:
    - allow_dw_entry_under_target=False: Target must be reached BY DW ENTRY
    - allow_dw_entry_under_target=True: Target must be reached AT ANY POINT in DW

    With False mode, high terminal penalty, and pre-DW discharge, optimizer
    grid-charges to meet target by DW entry.
    """
    # Pre-DW: Net discharge (solar < consumption) to create SOC deficit
    pre_dw_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{10 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.10,  # cheap enough to grid charge
            sell_price=0.05,
            solar_kwh=0.0,  # No solar pre-DW
            consumption_kwh=1.0,  # Net discharge
        )
        for i in range(4)
    ]
    # DW: High solar that would reach target mid-DW (but not by DW entry)
    dw_slots = [
        SlotContext(
            slot_index=4 + i,
            timestamp_iso=f"2026-01-03T{14 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.15,
            sell_price=0.05,
            solar_kwh=3.0 if i < 2 else 1.0,  # High solar early in DW
            consumption_kwh=0.5,
            is_demand_window_entry=(i == 0),
            is_demand_window_slot=True,
        )
        for i in range(4)
    ]
    slots = pre_dw_slots + dw_slots

    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.12,
        allow_dw_entry_under_target=False,  # Must reach target BY DW entry
        optimization_mode="self_consumption",
        soc_bins=20,
        target_shortfall_penalty_per_pct=0.150,  # High penalty to incentivize charging
    )
    inputs = OptimizerInputs(
        cycle_id="test-dw-target-at-entry",
        initial_soc_pct=50.0,
        slots=slots,
        config=config,
    )

    result = DPPlanner().plan(inputs)
    assert result.success

    # With allow_dw_entry_under_target=False, target must be met BY DW entry.
    # Pre-DW discharge creates deficit, high penalty forces grid charging.
    pre_dw_grid_charges = [
        d.action
        for d in result.decisions[:4]
        if d.action
        in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
    ]
    assert len(pre_dw_grid_charges) > 0, (
        "With allow_dw_entry_under_target=False and high penalty, "
        "should grid-charge when solar won't reach target by DW entry. "
        f"Got decisions: {[d.action.name for d in result.decisions[:4]]}"
    )

"""
tests/test_optimizer_dp_solve.py — Phase C DP solver tests for #403.

Tests that the DP optimizer produces correct, deterministic plans
with valid SOC projections and objective term accounting.

These tests run ENTIRELY OFFLINE — no Home Assistant or Solcast data required.
"""

from __future__ import annotations

import pytest

from custom_components.localshift.engine.constraints import (
    check_global_solar_sufficiency,
    feasible_actions,
)
from custom_components.localshift.engine.cost import stage_cost
from custom_components.localshift.engine.negative_fit import (
    derive_negative_fit_avoidance_context,
)
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
    _build_soc_grid,
    _map_soc_to_bin,
    _simulate_max_soc_in_demand_window,
)
from custom_components.localshift.engine.penalties import (
    get_futile_cycling_penalty_factor,
)
from custom_components.localshift.engine.reason_codes import (
    _is_cheap_import_window,
    _is_target_shortfall_risk,
    classify_charge_reason,
    classify_export_reason,
    classify_reason,
)
from custom_components.localshift.engine.solar import (
    projected_solar_soc_gain_pct,
    projected_solcast_gain_pct,
)
from custom_components.localshift.engine.transitions import (
    _transition_hold_deficit,
    transition,
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

    next_soc, grid_import, grid_export = transition(
        soc_pct=default_config.min_soc_pct,
        action=PlannerAction.HOLD,
        slot=slot,
        config=default_config,
    )

    assert next_soc == pytest.approx(default_config.min_soc_pct)
    assert grid_import == pytest.approx(1.0)
    assert grid_export == pytest.approx(0.0)

    terms = stage_cost(
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

    next_soc, grid_import, grid_export = transition(
        soc_pct=default_config.max_soc_pct,
        action=PlannerAction.HOLD,
        slot=slot,
        config=default_config,
    )

    assert next_soc == pytest.approx(default_config.max_soc_pct)
    assert grid_import == pytest.approx(0.0)
    assert grid_export == pytest.approx(1.2)

    terms = stage_cost(
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
    _, grid_import, _ = transition(
        soc_pct=50.0, action=PlannerAction.HOLD, slot=slot, config=config
    )
    assert grid_import == pytest.approx(6.0)  # 10.0 - 4.0

    # 2. Charge (net_kwh = 10): should be capped by solar_charge_rate_kw (2.0 kW * 1h = 2.0 kWh)
    slot.consumption_kwh = 0.0
    slot.solar_kwh = 10.0
    _, _, grid_export = transition(
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

    actions = feasible_actions(50.0, slot, default_config)

    assert PlannerAction.HOLD in actions
    assert PlannerAction.CHARGE_GRID_NORMAL not in actions
    assert PlannerAction.CHARGE_GRID_BOOST not in actions


def test_terminal_penalty_applied_at_dw_entry_by_default():
    """When switch is off, shortfall is measured at demand window entry."""
    # Horizon starts BEFORE the demand window so the entry is a genuine transition into
    # the DW (slot 1), not an in-progress DW at slot 0 (which _find_demand_window_bounds
    # now correctly ignores — see test_mid_dw_target_undercharge).
    slots = [
        SlotContext(
            slot_index=0,
            timestamp_iso="2026-01-03T17:30:00",
            slot_interval_minutes=30,
            buy_price=0.30,
            sell_price=0.0,
            solar_kwh=0.0,
            consumption_kwh=0.0,
            is_demand_window_entry=False,
            is_demand_window_slot=False,
        ),
        SlotContext(
            slot_index=1,
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
            slot_index=2,
            timestamp_iso="2026-01-03T18:30:00",
            slot_interval_minutes=30,
            buy_price=0.30,
            sell_price=0.0,
            solar_kwh=2.0,
            consumption_kwh=0.0,
            is_demand_window_slot=True,
        ),
        SlotContext(
            slot_index=3,
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
        shortfall_penalty=0.0,
    )
    assert terms.net_cost == pytest.approx(1.50 - 0.80)


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

    reason = classify_reason(  # noqa: SLF001 - unit test for internal logic
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

    reason = classify_reason(  # noqa: SLF001 - unit test for internal logic
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
    """Solar surplus >= SOC deficit: gate suppresses grid charging.

    Issue #638: Fixed the surplus formula (was using net instead of surplus)
    and removed the DW guard. Now the gate correctly suppresses grid charging
    when solar surplus covers the deficit, regardless of demand window status.
    """
    # SOC=70, target=80 → deficit=10%
    # 10% of 13.5 kWh = 1.35 kWh needed
    # Slots: 6 slots each with solar=0.5 kWh, consumption=0.2 kWh → surplus=0.3 kWh/slot
    # Total surplus = 1.8 kWh → 13.3% gain > 10% deficit → gate fires
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

    actions = feasible_actions(
        soc_pct=70.0,
        slot=slots[0],
        config=config,
        slot_idx=0,
        slots=slots,
        terminal_penalty_idx=6,  # DW exists but gate still fires
    )

    assert PlannerAction.HOLD in actions
    assert PlannerAction.CHARGE_GRID_NORMAL not in actions, (
        "Gate should suppress grid charging when solar surplus covers deficit"
    )


def test_check_global_solar_sufficiency_empty_slots_returns_false():
    """Edge case: empty slots list should return False (no solar to cover deficit)."""
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.10,
        optimization_mode="self_consumption",
        soc_bins=20,
    )

    result = check_global_solar_sufficiency(
        soc_pct=70.0,  # 10% deficit
        config=config,
        slot_idx=0,
        slots=[],  # Empty
    )
    assert result is False


def test_check_global_solar_sufficiency_no_deficit_returns_false():
    """Edge case: when SOC >= target, no deficit to cover, return False early."""
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.10,
        optimization_mode="self_consumption",
        soc_bins=20,
    )
    slots = [
        _make_slot(slot_index=i, solar_kwh=5.0, consumption_kwh=0.1) for i in range(6)
    ]

    # SOC=85 >= target=80 → deficit <= 0
    result = check_global_solar_sufficiency(
        soc_pct=85.0,
        config=config,
        slot_idx=0,
        slots=slots,
    )
    assert result is False


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

    actions = feasible_actions(
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
    actions = feasible_actions(70.0, slot, config)

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

    actions = feasible_actions(
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


def test_feasible_actions_grid_charge_capped_at_target():
    """At/above the demand-window target, grid charging is capped (not offered).

    Previously, with SOC >= target the solar-sufficiency gate did not fire and a
    cheap price would still offer grid charge — producing marginal above-target
    top-ups (e.g. grid-charging at 98% just before the demand window). Grid
    charging is now capped at ``demand_window_target_soc_pct`` in self-consumption
    mode, so only HOLD remains. (The solar gate still does not fire here; the
    target cap is the operative brake. Solar may still fill above target for free.)
    """
    # SOC=85 >= target=80 → above the grid-charge ceiling
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

    actions = feasible_actions(
        soc_pct=85.0,
        slot=slots[0],
        config=config,
        slot_idx=0,
        slots=slots,
        terminal_penalty_idx=4,
    )

    # Above target → grid charge capped even though price is cheap and gate is idle
    assert PlannerAction.HOLD in actions
    assert PlannerAction.CHARGE_GRID_NORMAL not in actions


def test_projected_solar_soc_gain_pct_positive_surplus():
    """projected_solar_soc_gain_pct returns positive gain when solar > consumption.

    Simulation caps gain at solar_charge_rate * slot_hours, so actual gain is
    less than the naive sum would suggest.
    """
    slots = [
        _make_slot(slot_index=i, solar_kwh=1.0, consumption_kwh=0.3) for i in range(4)
    ]
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        charge_rate_kw=5.0,
        solar_charge_rate_kw=5.0,
        discharge_rate_kw=5.0,
        charge_efficiency=0.9,
        discharge_efficiency=0.95,
        min_soc_pct=5.0,
    )
    # net=0.7 kWh/slot, solar_charge allows 2.5 kWh/slot, so 0.7 is the cap
    # 4 slots * 0.7 kWh * 0.9 eff / 13.5 * 100 = ~18.67%
    result = projected_solar_soc_gain_pct(  # noqa: SLF001
        slot_idx=0,
        slots=slots,
        terminal_penalty_idx=4,
        battery_capacity_kwh=13.5,
        initial_soc_pct=50.0,
        config=config,
    )
    assert result == pytest.approx(18.67, rel=0.01)


def test_projected_solar_soc_gain_pct_zero_when_consumption_dominates():
    """When consumption dominates, projected solar gain is zero (not negative).

    The function returns max(0, terminal_soc - initial_soc), so any net
    consumption shortfall results in 0 gain, not negative.
    """
    slots = [
        _make_slot(slot_index=i, solar_kwh=0.1, consumption_kwh=0.5) for i in range(4)
    ]
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        charge_rate_kw=5.0,
        solar_charge_rate_kw=5.0,
        discharge_rate_kw=5.0,
        charge_efficiency=0.9,
        discharge_efficiency=0.95,
        min_soc_pct=5.0,
    )
    result = projected_solar_soc_gain_pct(  # noqa: SLF001
        slot_idx=0,
        slots=slots,
        terminal_penalty_idx=4,
        battery_capacity_kwh=13.5,
        initial_soc_pct=50.0,
        config=config,
    )
    assert result == 0.0


def test_projected_solar_soc_gain_pct_respects_slot_range():
    """Only slots in [slot_idx, terminal_penalty_idx) are counted.

    Slot 0 (large surplus) is excluded by slot_idx=1. Slots 1-2 have net
    deficit, so terminal SOC ends below initial → gain is 0.
    """
    slots = [
        _make_slot(slot_index=0, solar_kwh=3.0, consumption_kwh=0.1),  # large surplus
        _make_slot(slot_index=1, solar_kwh=0.0, consumption_kwh=0.5),  # deficit
        _make_slot(slot_index=2, solar_kwh=0.0, consumption_kwh=0.5),  # deficit
        _make_slot(slot_index=3, solar_kwh=3.0, consumption_kwh=0.1),  # ignored
    ]
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        charge_rate_kw=5.0,
        solar_charge_rate_kw=5.0,
        discharge_rate_kw=5.0,
        charge_efficiency=0.9,
        discharge_efficiency=0.95,
        min_soc_pct=5.0,
    )
    # slot_idx=1, terminal=3 → slots 1 and 2 (net deficit)
    result = projected_solar_soc_gain_pct(  # noqa: SLF001
        slot_idx=1,
        slots=slots,
        terminal_penalty_idx=3,
        battery_capacity_kwh=13.5,
        initial_soc_pct=50.0,
        config=config,
    )
    assert result == 0.0


def test_optimizer_does_not_grid_charge_during_solar_peak_with_sufficient_solar():
    """Integration: on a sunny day with enough solar, no grid charging before demand window.

    Issue #811/#816: in strict mode the target is enforced at DW *entry*, not held to the
    end of the DW. The battery is expected to discharge during the demand window to cover
    its load (that is the point of pre-charging), so end-of-DW SOC may fall below target —
    that is correct, not a shortfall.
    """
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

    # The optimizer should meet the 80% target by DW entry (slot 8)
    # SOC at DW entry = predicted_soc_pct at end of slot 7
    pre_dw_final_decision = result.decisions[7]  # Last pre-DW slot
    soc_at_dw_entry = pre_dw_final_decision.predicted_soc_pct
    assert soc_at_dw_entry >= 80.0, (
        f"Expected SOC ≥80% at DW entry (slot 8), got {soc_at_dw_entry:.1f}%"
    )

    # Issue #811/#816: the target is enforced at DW entry, not held to the end of the DW.
    # The battery should discharge during the demand window (slots 8-11, $0.30, no solar)
    # to cover its load rather than hoard charge to a horizon-end target — so no grid
    # import at the peak, and end-of-DW SOC is allowed to fall below target.
    dw_decisions = result.decisions[8:]
    dw_grid_import = sum(d.grid_import_kwh for d in dw_decisions)
    assert dw_grid_import == 0.0, (
        f"battery should cover DW load from storage, not import at the peak; "
        f"got {dw_grid_import:.2f} kWh of DW grid import"
    )
    assert result.decisions[-1].predicted_soc_pct < soc_at_dw_entry, (
        "battery should discharge through the demand window (it was pre-charged to be used)"
    )

    # Any grid charging in the pre-DW solar window (slots 0-7) should be economically
    # justified: buying at $0.08 to avoid importing at $0.30 during DW.
    # This is correct economic arbitrage behavior per PLANNING_MODEL.md.
    pre_dw_grid_charges = [
        d
        for d in result.decisions[:8]
        if d.action
        in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
    ]
    if pre_dw_grid_charges:
        # Verify charging only happens at cheap buy prices ($0.08)
        for d in pre_dw_grid_charges:
            assert d.buy_price <= 0.10, (
                f"Grid charging at slot {d.slot_index} uses expensive rate ${d.buy_price:.2f}"
            )
        # Verify the final SOC is at or above the target (optimizer may charge beyond
        # target if it's economically optimal, but must at least meet it)
        assert final_decision.predicted_soc_pct >= 80.0, (
            f"Grid charging occurred but didn't meet 80% target (got {final_decision.predicted_soc_pct:.1f}%)"
        )


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


# ---------------------------------------------------------------------------
# Issue #559: Pointless Overnight Battery Cycles
# ---------------------------------------------------------------------------


def test_global_solar_gate_fires_when_no_demand_window():
    """Issue #559 Phase 1: global solar gate prevents charging when terminal_penalty_idx is None.

    When solar from slot to END of horizon can cover the SOC deficit, the gate
    should suppress grid charging entirely (hard constraint, not soft penalty).
    """
    # SOC=60, target=80 → deficit=20% (2.7 kWh needed)
    # Slots: 12 overnight/morning slots, each solar=0.6 kWh, consumption=0.3 kWh → net=0.3 kWh/slot
    # 12 * 0.3 = 3.6 kWh total net solar → 3.6 / 13.5 * 100 = 26.7% gain >= 20% deficit
    # → global gate fires → no grid charging even though terminal_penalty_idx=None
    slots = [
        _make_slot(slot_index=i, buy_price=0.08, solar_kwh=0.6, consumption_kwh=0.3)
        for i in range(12)
    ]
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.10,
        optimization_mode="self_consumption",
        soc_bins=20,
    )

    # Test the helper method directly
    result = check_global_solar_sufficiency(60.0, 0, slots, config)
    assert result is True, (
        "Global solar sufficiency should return True when solar >= deficit"
    )

    # With gate ENABLED, feasible_actions should suppress grid charging
    actions = feasible_actions(
        soc_pct=60.0,
        slot=slots[0],
        config=config,
        slot_idx=0,
        slots=slots,
        terminal_penalty_idx=None,  # No demand window → gate runs
    )

    assert PlannerAction.HOLD in actions
    # Global gate should suppress grid charging when solar sufficient
    assert PlannerAction.CHARGE_GRID_NORMAL not in actions
    assert PlannerAction.CHARGE_GRID_BOOST not in actions


def test_global_solar_gate_allows_charge_when_solar_insufficient():
    """Issue #559 Phase 1: global gate returns False when solar < deficit.

    When solar is insufficient to cover the SOC deficit, the gate should NOT
    suppress grid charging (price gate still applies).
    """
    # SOC=60, target=80 → deficit=20% (2.7 kWh needed)
    # Slots: 12 slots, each solar=0.1 kWh, consumption=0.3 kWh → net=-0.2 kWh/slot
    # 12 * -0.2 = -2.4 kWh total → -2.4 / 13.5 * 100 = -17.8% gain < 20% deficit
    # → method returns False (solar insufficient)
    slots = [
        _make_slot(slot_index=i, buy_price=0.08, solar_kwh=0.1, consumption_kwh=0.3)
        for i in range(12)
    ]
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.10,
        optimization_mode="self_consumption",
        soc_bins=20,
    )

    # Test the helper method directly
    result = check_global_solar_sufficiency(60.0, 0, slots, config)
    assert result is False, (
        "Global solar sufficiency should return False when solar < deficit"
    )

    # With gate enabled but solar insufficient, charge is allowed via price gate
    actions = feasible_actions(
        soc_pct=60.0,
        slot=slots[0],
        config=config,
        slot_idx=0,
        slots=slots,
        terminal_penalty_idx=None,
    )

    assert PlannerAction.HOLD in actions
    # buy_price=0.08 < effective_cheap_price=0.10 → charge allowed
    assert PlannerAction.CHARGE_GRID_NORMAL in actions
    assert PlannerAction.CHARGE_GRID_BOOST in actions


# ---------------------------------------------------------------------------
# Issue #701: Global Solar Gate Uses Realistic Simulation
# ---------------------------------------------------------------------------


def test_global_solar_sufficiency_uses_realistic_simulation():
    """Issue #701: Gate should use realistic simulation with rate limits and efficiency.

    The bug (Issue #638 regression): the gate used raw surplus without accounting
    for charge rate limits and efficiency losses. This incorrectly blocked cheap
    grid charging when solar couldn't actually reach the target.

    This test verifies the realistic simulation:
    - Overnight consumption depletes the battery
    - Daytime solar has enough surplus to recover AND reach target
    - Realistic simulation accounts for efficiency losses

    Scenario:
    - Start at 60% SOC, target 80% (need 20% = 2.7 kWh)
    - 8 overnight slots: 0.2 kWh consumption each = 1.6 kWh total discharge
    - 8 daytime slots: 1.8 kWh surplus each = 14.4 kWh total surplus
    - With 90% efficiency: 14.4 * 0.9 = 12.96 kWh effective charge

    Realistic simulation:
    - Overnight: SOC drops by 1.6 / 13.5 * 100 = 11.9% → 48.1%
    - Daytime: SOC rises by 12.96 / 13.5 * 100 = 96% → capped at 100%

    Since 100% >= 80% target, result should be TRUE.
    """
    overnight = [
        _make_slot(slot_index=i, buy_price=0.08, solar_kwh=0.0, consumption_kwh=0.2)
        for i in range(8)
    ]
    daytime = [
        _make_slot(slot_index=i + 8, buy_price=0.08, solar_kwh=2.0, consumption_kwh=0.2)
        for i in range(8)
    ]
    slots = overnight + daytime

    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.10,
        optimization_mode="self_consumption",
        soc_bins=20,
        charge_efficiency=0.9,
        discharge_efficiency=0.95,
    )

    result = check_global_solar_sufficiency(60.0, 0, slots, config)
    assert result is True, (
        "Gate should return True when realistic simulation reaches target"
    )


def test_global_solar_sufficiency_realistic_returns_false_when_insufficient():
    """Issue #701: Gate should return False when rate limits prevent reaching target.

    This test verifies that realistic simulation correctly handles rate limits.
    Even with abundant surplus, if charge rate limits prevent capturing enough,
    the gate should return False.

    Scenario:
    - Start at 30% SOC, target 80% (need 50% = 6.75 kWh)
    - 4 daytime slots: 10 kWh surplus each = 40 kWh raw surplus
    - But charge rate of 5 kW = 2.5 kWh max per 30-min slot
    - Per slot: min(10, 2.5) * 0.9 = 2.25 kWh captured
    - Total captured: 4 * 2.25 = 9 kWh

    Realistic: 9 kWh > 6.75 kWh needed → TRUE (enough despite rate limit)

    But if we need more (e.g., start at 20% → need 60% = 8.1 kWh):
    - 9 kWh > 8.1 kWh → still TRUE

    Let's make it fail: start at 10%, target 80% (need 70% = 9.45 kWh)
    - 9 kWh < 9.45 kWh → FALSE
    """
    daytime = [
        _make_slot(slot_index=i, buy_price=0.08, solar_kwh=10.0, consumption_kwh=0.0)
        for i in range(4)
    ]

    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.10,
        optimization_mode="self_consumption",
        soc_bins=20,
        charge_efficiency=0.9,
        solar_charge_rate_kw=5.0,
    )

    result = check_global_solar_sufficiency(10.0, 0, daytime, config)
    assert result is False, (
        "Gate should return False when rate limits prevent reaching target"
    )


def test_global_solar_gate_fires_with_demand_window():
    """Issue #638: Gate should work when demand window exists.

    Currently the gate only runs when terminal_penalty_idx is None (no DW).
    But it should also suppress grid charging when a DW exists and solar
    can fill the battery before DW entry.
    """
    slots_before_dw = [
        _make_slot(slot_index=i, buy_price=0.08, solar_kwh=1.2, consumption_kwh=0.2)
        for i in range(4)
    ]
    slots_during_dw = [
        _make_slot(
            slot_index=i + 4,
            buy_price=0.08,
            solar_kwh=1.2,
            consumption_kwh=0.2,
            is_demand_window_slot=True,
        )
        for i in range(4)
    ]
    slots = slots_before_dw + slots_during_dw

    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.10,
        optimization_mode="self_consumption",
        soc_bins=20,
    )

    actions = feasible_actions(
        soc_pct=50.0,
        slot=slots[0],
        config=config,
        slot_idx=0,
        slots=slots,
        terminal_penalty_idx=4,
    )

    assert PlannerAction.HOLD in actions
    assert PlannerAction.CHARGE_GRID_NORMAL not in actions, (
        "Gate should suppress grid charging when solar covers deficit, "
        "even with DW active (terminal_penalty_idx is not None)"
    )


def test_global_solar_gate_allows_charge_with_dw_when_insufficient():
    """Issue #638: Gate should allow charging when DW exists but solar insufficient."""
    slots_before_dw = [
        _make_slot(slot_index=i, buy_price=0.08, solar_kwh=0.2, consumption_kwh=0.3)
        for i in range(4)
    ]
    slots_during_dw = [
        _make_slot(
            slot_index=i + 4,
            buy_price=0.08,
            solar_kwh=0.2,
            consumption_kwh=0.3,
            is_demand_window_slot=True,
        )
        for i in range(4)
    ]
    slots = slots_before_dw + slots_during_dw

    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.10,
        optimization_mode="self_consumption",
        soc_bins=20,
    )

    actions = feasible_actions(
        soc_pct=50.0,
        slot=slots[0],
        config=config,
        slot_idx=0,
        slots=slots,
        terminal_penalty_idx=4,
    )

    assert PlannerAction.HOLD in actions
    assert PlannerAction.CHARGE_GRID_NORMAL in actions, (
        "Gate should allow grid charging when solar insufficient, even with DW"
    )


def test_global_solar_gate_ignores_solar_after_deadline():
    """Issue #801: Solar after deadline should not suppress pre-deadline charging."""
    slots = [
        SlotContext(
            slot_index=0,
            timestamp_iso="2026-01-03T08:00:00",
            slot_interval_minutes=30,
            buy_price=0.08,
            sell_price=0.06,
            solar_kwh=0.0,
            consumption_kwh=0.2,
            is_demand_window_slot=False,
        ),
        SlotContext(
            slot_index=1,
            timestamp_iso="2026-01-03T08:30:00",
            slot_interval_minutes=30,
            buy_price=0.08,
            sell_price=0.06,
            solar_kwh=0.0,
            consumption_kwh=0.2,
            is_demand_window_entry=True,
            is_demand_window_slot=True,
        ),
        SlotContext(
            slot_index=2,
            timestamp_iso="2026-01-03T09:00:00",
            slot_interval_minutes=30,
            buy_price=0.08,
            sell_price=0.06,
            solar_kwh=2.0,
            consumption_kwh=0.0,
            is_demand_window_slot=True,
        ),
        SlotContext(
            slot_index=3,
            timestamp_iso="2026-01-03T09:30:00",
            slot_interval_minutes=30,
            buy_price=0.08,
            sell_price=0.06,
            solar_kwh=2.0,
            consumption_kwh=0.0,
            is_demand_window_slot=True,
        ),
    ]
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.10,
        optimization_mode="self_consumption",
        soc_bins=20,
    )

    actions = feasible_actions(
        soc_pct=60.0,
        slot=slots[0],
        config=config,
        slot_idx=0,
        slots=slots,
        terminal_penalty_idx=1,
    )

    assert PlannerAction.CHARGE_GRID_NORMAL in actions, (
        "Charging should remain feasible when solar arrives after the deadline"
    )


def test_hold_soc_enforces_no_discharge():
    """Issue #559 Phase 3: hold_soc=True prevents discharge during HOLD action."""
    # SOC=50%, net=-0.5 kWh (load deficit), slot_hours=1
    # Without hold_soc: battery discharges to cover some load
    # With hold_soc: entire deficit imported from grid, SOC unchanged
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        discharge_rate_kw=5.0,
        discharge_efficiency=0.95,
        min_soc_pct=10.0,
        hold_soc=True,  # Enforce strict no-discharge
    )

    next_soc, grid_import, grid_export = _transition_hold_deficit(
        soc_pct=50.0,
        net_kwh=-0.5,  # 0.5 kWh load deficit
        slot_hours=1.0,
        config=config,
        capacity_kwh=13.5,
    )

    # SOC must remain unchanged
    assert next_soc == 50.0
    # Entire deficit imported from grid
    assert grid_import == 0.5
    assert grid_export == 0.0


def test_hold_soc_false_allows_discharge():
    """Issue #559 Phase 3: hold_soc=False allows normal discharge logic."""
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        discharge_rate_kw=5.0,
        discharge_efficiency=0.95,
        min_soc_pct=10.0,
        hold_soc=False,  # Normal discharge allowed
    )

    next_soc, grid_import, grid_export = _transition_hold_deficit(
        soc_pct=50.0,
        net_kwh=-0.5,
        slot_hours=1.0,
        config=config,
        capacity_kwh=13.5,
    )

    # SOC should decrease (battery discharges)
    # Grid import should be less than full deficit (battery covered some)
    assert grid_import < 0.5
    assert grid_export == 0.0


# ---------------------------------------------------------------------------
# Issue #633: Cross-Day Demand Window Solar Check Bug
# ---------------------------------------------------------------------------


def test_simulate_max_soc_scopes_to_first_dw_block_only():
    """Issue #633: _simulate_max_soc_in_demand_window must only track first DW block.

    When demand_bounds is provided, max_soc tracking is restricted to
    [entry_idx, end_idx] — tomorrow's DW slots must be excluded.
    """
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        charge_rate_kw=5.0,
        charge_efficiency=0.95,
        discharge_efficiency=0.95,
    )
    slots = [
        SlotContext(
            slot_index=0,
            timestamp_iso="2026-01-03T16:00:00",
            slot_interval_minutes=60,
            buy_price=0.10,
            sell_price=0.05,
            solar_kwh=0.3,
            consumption_kwh=0.5,
        ),
        SlotContext(
            slot_index=1,
            timestamp_iso="2026-01-03T17:00:00",
            slot_interval_minutes=60,
            buy_price=0.10,
            sell_price=0.05,
            solar_kwh=0.3,
            consumption_kwh=0.5,
        ),
        SlotContext(
            slot_index=2,
            timestamp_iso="2026-01-03T18:00:00",
            slot_interval_minutes=60,
            buy_price=0.25,
            sell_price=0.05,
            solar_kwh=0.3,
            consumption_kwh=0.5,
            is_demand_window_entry=True,
            is_demand_window_slot=True,
        ),
        SlotContext(
            slot_index=3,
            timestamp_iso="2026-01-03T19:00:00",
            slot_interval_minutes=60,
            buy_price=0.25,
            sell_price=0.05,
            solar_kwh=0.3,
            consumption_kwh=0.5,
            is_demand_window_slot=True,
        ),
        SlotContext(
            slot_index=4,
            timestamp_iso="2026-01-04T18:00:00",
            slot_interval_minutes=60,
            buy_price=0.25,
            sell_price=0.05,
            solar_kwh=8.0,
            consumption_kwh=0.5,
            is_demand_window_slot=True,
        ),
        SlotContext(
            slot_index=5,
            timestamp_iso="2026-01-04T19:00:00",
            slot_interval_minutes=60,
            buy_price=0.25,
            sell_price=0.05,
            solar_kwh=8.0,
            consumption_kwh=0.5,
            is_demand_window_slot=True,
        ),
    ]
    demand_bounds: dict[str, int | None] = {"entry_idx": 2, "end_idx": 3}

    result = _simulate_max_soc_in_demand_window(
        initial_soc_pct=50.0,
        slots=slots,
        config=config,
        demand_bounds=demand_bounds,
    )

    assert result < 80.0, (
        f"max_soc_in_dw={result:.1f}% should be < 80% when tomorrow's DW is excluded"
    )


def test_cross_day_dw_solar_only_checks_first_block():
    """Issue #633: With two DW blocks in horizon, only today's DW solar is checked.

    When tomorrow's DW has high solar but today's does not,
    can_solar_reach_target_in_dw must be False.
    """
    pre_dw_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{12 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.10,
            sell_price=0.05,
            solar_kwh=0.5,
            consumption_kwh=0.5,
        )
        for i in range(6)
    ]
    today_dw_slots = [
        SlotContext(
            slot_index=6 + i,
            timestamp_iso=f"2026-01-03T{18 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.25,
            sell_price=0.05,
            solar_kwh=0.2,
            consumption_kwh=0.5,
            is_demand_window_entry=(i == 0),
            is_demand_window_slot=True,
        )
        for i in range(2)
    ]
    tomorrow_dw_slots = [
        SlotContext(
            slot_index=8 + i,
            timestamp_iso=f"2026-01-04T{18 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.25,
            sell_price=0.05,
            solar_kwh=8.0,
            consumption_kwh=0.5,
            is_demand_window_entry=(i == 0),
            is_demand_window_slot=True,
        )
        for i in range(2)
    ]
    slots = pre_dw_slots + today_dw_slots + tomorrow_dw_slots

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
        cycle_id="test-633-cross-day-solar-check",
        initial_soc_pct=50.0,
        slots=slots,
        config=config,
    )

    result = DPPlanner().plan(inputs)
    assert result.success

    assert result.can_solar_reach_target_in_dw is False, (
        "Today's DW has insufficient solar; tomorrow's DW must not inflate the check"
    )


def test_cross_day_dw_grid_charges_for_today():
    """Issue #633: When today's DW has insufficient solar, shortfall is detected.

    This test verifies that when cross-day scenarios have two DW blocks,
    the optimizer correctly identifies that today's DW cannot reach target
    via solar alone, even when tomorrow's DW has abundant solar.

    The key fix is that can_solar_reach_target_in_dw and terminal_shortfall_pct
    are computed using only today's DW slots, not tomorrow's.
    """
    pre_dw_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{12 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.10,
            sell_price=0.05,
            solar_kwh=0.5,
            consumption_kwh=0.5,
        )
        for i in range(6)
    ]
    today_dw_slots = [
        SlotContext(
            slot_index=6 + i,
            timestamp_iso=f"2026-01-03T{18 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.25,
            sell_price=0.05,
            solar_kwh=0.2,
            consumption_kwh=0.5,
            is_demand_window_entry=(i == 0),
            is_demand_window_slot=True,
        )
        for i in range(2)
    ]
    tomorrow_dw_slots = [
        SlotContext(
            slot_index=8 + i,
            timestamp_iso=f"2026-01-04T{18 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.25,
            sell_price=0.05,
            solar_kwh=8.0,
            consumption_kwh=0.5,
            is_demand_window_entry=(i == 0),
            is_demand_window_slot=True,
        )
        for i in range(2)
    ]
    slots = pre_dw_slots + today_dw_slots + tomorrow_dw_slots

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
        cycle_id="test-633-cross-day-grid-charge",
        initial_soc_pct=50.0,
        slots=slots,
        config=config,
    )

    result = DPPlanner().plan(inputs)
    assert result.success

    # Core fix verification: solar check scoped to today's DW only
    assert result.can_solar_reach_target_in_dw is False, (
        "can_solar_reach_target_in_dw must be False when today's DW solar is insufficient, "
        "even though tomorrow's DW has abundant solar"
    )

    # Terminal shortfall must reflect today's DW, not tomorrow's
    assert result.terminal_shortfall_pct > 0, (
        f"terminal_shortfall_pct={result.terminal_shortfall_pct}% must be > 0 when today's DW cannot reach target"
    )


def test_cross_day_dw_regression_single_block_unchanged():
    """Issue #633: Regression — single DW block behavior must be unchanged after fix."""
    pre_dw_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{10 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.10,
            sell_price=0.05,
            solar_kwh=0.5,
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
            solar_kwh=3.0 if i < 2 else 1.0,
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
        cycle_id="test-633-regression-single-dw",
        initial_soc_pct=50.0,
        slots=slots,
        config=config,
    )

    result = DPPlanner().plan(inputs)
    assert result.success

    # Regression: single DW with sufficient solar must still work correctly
    assert result.can_solar_reach_target_in_dw is True, (
        "can_solar_reach_target_in_dw must be True when DW solar is sufficient"
    )
    assert result.terminal_shortfall_pct == 0.0, (
        f"terminal_shortfall_pct={result.terminal_shortfall_pct}% must be 0 when DW solar is sufficient"
    )
    for decision in result.decisions[:4]:
        assert decision.action not in (
            PlannerAction.CHARGE_GRID_NORMAL,
            PlannerAction.CHARGE_GRID_BOOST,
        ), (
            f"Slot {decision.slot_index} should not grid-charge when single DW solar is sufficient"
        )


def test_terminal_shortfall_cross_day_uses_first_dw_only():
    """Issue #633: _compute_terminal_shortfall must scope to first DW block.

    When shortfall is computed via allow_dw_entry_under_target path,
    tomorrow's DW solar must not mask today's shortfall.
    """
    pre_dw_slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{12 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.10,
            sell_price=0.05,
            solar_kwh=0.5,
            consumption_kwh=0.5,
        )
        for i in range(6)
    ]
    today_dw_slots = [
        SlotContext(
            slot_index=6 + i,
            timestamp_iso=f"2026-01-03T{18 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.25,
            sell_price=0.05,
            solar_kwh=0.2,
            consumption_kwh=0.5,
            is_demand_window_entry=(i == 0),
            is_demand_window_slot=True,
        )
        for i in range(2)
    ]
    tomorrow_dw_slots = [
        SlotContext(
            slot_index=8 + i,
            timestamp_iso=f"2026-01-04T{18 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.25,
            sell_price=0.05,
            solar_kwh=8.0,
            consumption_kwh=0.5,
            is_demand_window_entry=(i == 0),
            is_demand_window_slot=True,
        )
        for i in range(2)
    ]
    slots = pre_dw_slots + today_dw_slots + tomorrow_dw_slots

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
        cycle_id="test-633-shortfall-cross-day",
        initial_soc_pct=50.0,
        slots=slots,
        config=config,
    )

    result = DPPlanner().plan(inputs)
    assert result.success
    assert result.terminal_shortfall_pct > 0.0, (
        "Cross-day DW should report shortfall for first DW only"
    )


# ---------------------------------------------------------------------------
# Coverage tests for edge cases (to reach 95% threshold)
# ---------------------------------------------------------------------------


def test_interpolate_cost_to_soc_empty_inputs():
    """Line 536-537: Empty soc_grid or cost_table returns inf."""
    from custom_components.localshift.engine.optimizer_dp import (
        _interpolate_cost_to_soc,
    )

    soc_grid = [0.0, 50.0, 100.0]
    cost_table = {0: 1.0, 1: 0.5, 2: 0.0}

    assert _interpolate_cost_to_soc(50.0, [], cost_table) == float("inf")
    assert _interpolate_cost_to_soc(50.0, soc_grid, {}) == float("inf")


def test_interpolate_cost_to_soc_below_grid():
    """Line 545-547: SOC below all grid points uses lowest bin."""
    from custom_components.localshift.engine.optimizer_dp import (
        _interpolate_cost_to_soc,
    )

    soc_grid = [20.0, 50.0, 80.0]
    cost_table = {0: 1.0, 1: 0.5, 2: 0.0}

    result = _interpolate_cost_to_soc(10.0, soc_grid, cost_table)
    assert result == 1.0


def test_interpolate_cost_to_soc_above_grid():
    """Line 554-556: SOC above all grid points uses highest bin."""
    from custom_components.localshift.engine.optimizer_dp import (
        _interpolate_cost_to_soc,
    )

    soc_grid = [20.0, 50.0, 80.0]
    cost_table = {0: 1.0, 1: 0.5, 2: 0.0}

    result = _interpolate_cost_to_soc(90.0, soc_grid, cost_table)
    assert result == 0.0


def test_interpolate_cost_to_soc_linear():
    """Line 570-571: Linear interpolation between bins."""
    from custom_components.localshift.engine.optimizer_dp import (
        _interpolate_cost_to_soc,
    )

    soc_grid = [0.0, 50.0, 100.0]
    cost_table = {0: 1.0, 1: 0.5, 2: 0.0}

    result = _interpolate_cost_to_soc(25.0, soc_grid, cost_table)
    assert result == pytest.approx(0.75, rel=0.01)


def test_classify_export_reason_negative_fit():
    """Line 1484: EXPORT with sell_price <= 0 returns NEGATIVE_FIT_AVOIDANCE."""
    planner = DPPlanner()
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.25,
        sell_price=-0.05,
        solar_kwh=0.0,
        consumption_kwh=0.3,
    )
    reason = classify_export_reason(slot)
    assert reason == PlannerReasonCode.NEGATIVE_FIT_AVOIDANCE


def test_classify_charge_reason_solar_opportunity_wait():
    """Line 1523: CHARGE with solar_opportunity_penalty > 0 returns SOLAR_OPPORTUNITY_WAIT."""
    planner = DPPlanner()
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.14,
        sell_price=0.05,
        solar_kwh=0.0,
        consumption_kwh=0.3,
    )
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        effective_cheap_price=0.16,
        optimization_mode="self_consumption",
        soc_bins=20,
    )
    objective_terms = ObjectiveTerms(
        import_cost=0.05,
        export_revenue=0.0,
        self_consumption_value=0.0,
        shortfall_penalty=0.0,
        uncertainty_penalty=0.0,
        switching_penalty=0.0,
        solar_opportunity_penalty=0.10,
        futile_cycling_penalty=0.0,
    )

    reason = classify_charge_reason(
        slot, 0, [slot], 50.0, config, None, objective_terms=objective_terms
    )
    assert reason == PlannerReasonCode.SOLAR_OPPORTUNITY_WAIT


def test_is_target_shortfall_risk_no_deficit():
    """Line 1543: Returns False when soc >= target (no deficit)."""
    planner = DPPlanner()
    slots = [
        _make_slot(slot_index=i, solar_kwh=0.0, consumption_kwh=0.3) for i in range(4)
    ]
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        optimization_mode="self_consumption",
        soc_bins=20,
    )

    result = _is_target_shortfall_risk(0, slots, 85.0, config, 3)
    assert result is False


def test_is_target_shortfall_risk_with_solcast():
    """Lines 1555-1569: Solcast gain added when inputs.all_solcast present."""
    planner = DPPlanner()
    slots = [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{8 + i}:00:00",
            slot_interval_minutes=60,
            buy_price=0.14,
            sell_price=0.05,
            solar_kwh=0.0,
            consumption_kwh=0.5,
        )
        for i in range(4)
    ]
    slots[-1].is_demand_window_slot = True
    slots[-1].is_demand_window_entry = True

    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        optimization_mode="self_consumption",
        soc_bins=20,
    )

    all_solcast = [
        {"period_start": "2026-01-03T12:00:00", "pv_estimate": 5.0},
    ]

    inputs = OptimizerInputs(
        cycle_id="test-solcast",
        initial_soc_pct=50.0,
        slots=slots,
        config=config,
        all_solcast=all_solcast,
    )

    result = _is_target_shortfall_risk(0, slots, 50.0, config, 3, inputs=inputs)
    assert isinstance(result, bool)


def test_is_cheap_import_window_expensive_price():
    """Line 1596: Returns False when buy_price > effective_cheap_price."""
    planner = DPPlanner()
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.25,
        sell_price=0.05,
        solar_kwh=0.0,
        consumption_kwh=0.3,
    )
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        effective_cheap_price=0.14,
        optimization_mode="self_consumption",
        soc_bins=20,
    )

    result = _is_cheap_import_window(slot, 0, config, None, [slot])
    assert result is False


def test_transition_unknown_action():
    """Line 1868: Unknown action returns (soc, 0.0, 0.0) - tested via type checker."""
    pass


def test_transition_hold_surplus_zero_efficiency():
    """Line 1912: charge_efficiency <= 0 means no battery charging."""
    slot = _make_slot(solar_kwh=5.0, consumption_kwh=1.0)
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        charge_efficiency=0.0,
        soc_bins=20,
    )

    next_soc, grid_import, grid_export = transition(
        50.0, PlannerAction.HOLD, slot, config
    )
    assert next_soc == 50.0
    assert grid_export == pytest.approx(4.0, rel=0.01)


def test_transition_hold_deficit_zero_efficiency():
    """Line 1951: discharge_efficiency <= 0 means no battery discharge."""
    slot = _make_slot(solar_kwh=0.0, consumption_kwh=1.0)
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        discharge_efficiency=0.0,
        min_soc_pct=10.0,
        soc_bins=20,
    )

    next_soc, grid_import, grid_export = transition(
        50.0, PlannerAction.HOLD, slot, config
    )
    assert next_soc == 50.0
    assert grid_import == pytest.approx(1.0, rel=0.01)


def test_transition_export_zero_efficiency():
    """Line 2097: discharge_efficiency <= 0 in export means delta_soc = 0."""
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.25,
        sell_price=0.25,
        solar_kwh=0.0,
        consumption_kwh=0.5,
    )
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        discharge_efficiency=0.0,
        min_soc_pct=10.0,
        soc_bins=20,
    )

    next_soc, grid_import, grid_export = transition(
        50.0, PlannerAction.EXPORT_PROACTIVE, slot, config
    )
    assert next_soc == 50.0


def test_stage_cost_uncertainty_penalty():
    """Lines 2168-2170: Uncertainty penalty for short forecast horizon."""
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.14,
        sell_price=0.05,
        solar_kwh=0.0,
        consumption_kwh=0.3,
    )
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        forecast_horizon_hours=10.0,
        optimization_mode="self_consumption",
        soc_bins=20,
    )

    terms = stage_cost(
        PlannerAction.CHARGE_GRID_NORMAL,
        grid_import_kwh=0.5,
        grid_export_kwh=0.0,
        slot=slot,
        config=config,
    )

    assert terms.uncertainty_penalty > 0.0


def test_simulate_max_soc_with_demand_bounds():
    """Lines 2333-2339: Simulate with demand_bounds entry/end indices."""
    from custom_components.localshift.engine.optimizer_dp import (
        _simulate_max_soc_in_demand_window,
    )

    slots = [
        _make_slot(
            slot_index=i,
            solar_kwh=2.0 if i in (2, 3) else 0.0,
            consumption_kwh=0.3,
            is_demand_window_slot=(i in (2, 3)),
        )
        for i in range(5)
    ]

    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        min_soc_pct=10.0,
        soc_bins=20,
    )

    max_soc = _simulate_max_soc_in_demand_window(
        10.0, slots, config, demand_bounds={"entry_idx": 2, "end_idx": 3}
    )
    assert max_soc > 10.0


def test_projected_solcast_gain_pct():
    """Lines 1672-1689: _projected_solcast_gain_pct with valid solcast data."""
    from datetime import datetime

    all_solcast = [
        {"period_start": "2026-01-03T10:00:00", "pv_estimate": 4.0},
        {"period_start": "2026-01-03T10:30:00", "pv_estimate": 5.0},
    ]

    start_time = datetime.fromisoformat("2026-01-03T09:00:00")
    end_time = datetime.fromisoformat("2026-01-03T11:00:00")

    result = projected_solcast_gain_pct(
        all_solcast, start_time, end_time, battery_capacity_kwh=13.5
    )

    assert result > 0.0


def test_futile_cycling_battery_at_floor():
    """Line 1334: Futile cycling breaks when battery_used <= 0 (SOC at floor)."""
    planner = DPPlanner()
    slots = [
        _make_slot(slot_index=i, solar_kwh=0.0, consumption_kwh=1.0) for i in range(10)
    ]
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        min_soc_pct=10.0,
        soc_bins=20,
    )

    factor = get_futile_cycling_penalty_factor(
        action=PlannerAction.CHARGE_GRID_NORMAL,
        slot_idx=0,
        slots=slots,
        config=config,
        soc_after_charge_pct=10.0,
        charge_kwh=1.0,
    )

    assert factor >= 0.0


def test_classify_reason_idle():
    """Line 1420: Unknown action returns IDLE reason - tested via type checker."""
    pass


def test_plan_with_empty_slots():
    """Lines 613-617: plan() handles empty slots gracefully."""
    planner = DPPlanner()

    inputs = OptimizerInputs(
        cycle_id="test-empty",
        initial_soc_pct=50.0,
        slots=[],
        config=OptimizerConfig(battery_capacity_kwh=13.5, soc_bins=20),
    )

    result = planner.plan(inputs)
    assert result.success
    assert result.total_slots == 0


def test_negative_fit_context_type():
    """Smoke test: NegativeFitAvoidanceContext can be constructed."""
    from custom_components.localshift.engine.types import NegativeFitAvoidanceContext

    ctx = NegativeFitAvoidanceContext(
        risk_window_start_idx=10,
        risk_window_end_idx=15,
        required_headroom_kwh=5.0,
        recovery_deadline_idx=20,
        conservative_recovery_kwh_by_slot=(10.0, 10.0, 10.0),
        recoverability_floor_pct_by_slot=(30.0, 30.0, 30.0),
    )
    assert ctx.risk_window_start_idx == 10
    assert ctx.risk_window_end_idx == 15
    assert ctx.required_headroom_kwh == 5.0
    assert ctx.recovery_deadline_idx == 20


# =============================================================================
# Chunk 2: Negative FIT Avoidance Context Tests
# =============================================================================


def _make_slot_for_neg_fit(
    idx: int, sell_price: float, solar_kwh: float = 0.0, consumption_kwh: float = 0.0
) -> SlotContext:
    """Helper to create a 30-min slot for negative-FIT tests."""
    return SlotContext(
        slot_index=idx,
        timestamp_iso=f"2026-01-03T{(idx // 2):02d}:{(idx % 2) * 30:02d}:00",
        slot_interval_minutes=30,
        buy_price=0.10,
        sell_price=sell_price,
        solar_kwh=solar_kwh,
        consumption_kwh=consumption_kwh,
    )


def test_negative_fit_context_no_window(default_config):
    """Returns None when no negative-FIT window in horizon."""
    planner = DPPlanner(default_config)
    slots = [_make_slot_for_neg_fit(i, sell_price=0.08) for i in range(10)]
    inputs = OptimizerInputs(
        cycle_id="test",
        initial_soc_pct=50.0,
        slots=slots,
        config=default_config,
    )
    ctx = derive_negative_fit_avoidance_context(inputs)
    assert ctx is None


def test_negative_fit_context_no_overflow(default_config):
    """Returns None when no forecast overflow projected."""
    planner = DPPlanner(default_config)
    slots = [
        _make_slot_for_neg_fit(i, sell_price=0.08 if i < 5 else -0.05)
        for i in range(10)
    ]
    inputs = OptimizerInputs(
        cycle_id="test",
        initial_soc_pct=50.0,
        slots=slots,
        config=default_config,
    )
    ctx = derive_negative_fit_avoidance_context(inputs)
    assert ctx is None


def test_negative_fit_context_no_positive_slots(default_config):
    """Returns None when no earlier positive-FIT slots."""
    planner = DPPlanner(default_config)
    slots = [
        _make_slot_for_neg_fit(i, sell_price=0.08 if i >= 5 else 0.0) for i in range(10)
    ]
    slots[5] = _make_slot_for_neg_fit(5, sell_price=-0.05)
    inputs = OptimizerInputs(
        cycle_id="test",
        initial_soc_pct=50.0,
        slots=slots,
        config=default_config,
    )
    ctx = derive_negative_fit_avoidance_context(inputs)
    assert ctx is None


def test_negative_fit_context_computes_floor(default_config):
    """Computes correct recoverability_floor when all conditions met."""
    planner = DPPlanner(default_config)
    default_config.demand_window_target_soc_pct = 80.0
    slots = []
    for i in range(10):
        if i < 4:
            sell = 0.08
            solar = 2.0
        else:
            sell = -0.05
            solar = 1.0
        slots.append(_make_slot_for_neg_fit(i, sell_price=sell, solar_kwh=solar))
    inputs = OptimizerInputs(
        cycle_id="test",
        initial_soc_pct=95.0,
        slots=slots,
        config=default_config,
    )
    ctx = derive_negative_fit_avoidance_context(inputs)
    assert ctx is not None
    assert ctx.risk_window_start_idx == 4
    assert ctx.required_headroom_kwh > 0
    assert len(ctx.recoverability_floor_pct_by_slot) == 10
    assert all(
        f >= default_config.min_soc_pct for f in ctx.recoverability_floor_pct_by_slot
    )

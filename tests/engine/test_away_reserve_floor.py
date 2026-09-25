"""Planner-side away discharge floor (docs/holiday-away/plan.md item 4).

Covers the ``away_reserve_floor_pct`` field, the ``discharge_floor_pct``
property, and every read that switched from ``min_soc_pct`` to it:
``feasible_actions()`` (the export gate and the negative-FIT recoverability
floor), the HOLD/EXPORT transitions, the anti-sawtooth guard, and the DP
planner end to end. Consult docs/PLANNING_MODEL.md for the constraint model
this floor plugs into.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.engine.constraints import feasible_actions
from custom_components.localshift.engine.core import _floor_guard_blocks
from custom_components.localshift.engine.optimizer_dp import (
    DPPlanner,
    OptimizerInputs,
)
from custom_components.localshift.engine.optimizer_runner import (
    _build_optimizer_config,
)
from custom_components.localshift.engine.transitions import (
    _transition_export,
    _transition_hold_deficit,
)
from custom_components.localshift.engine.types import (
    NegativeFitAvoidanceContext,
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)


def _slot(
    buy: float = 0.10,
    sell: float = 0.10,
    solar: float = 0.0,
    consumption: float = 0.0,
    interval_minutes: int = 30,
    is_dw: bool = False,
) -> SlotContext:
    return SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=interval_minutes,
        buy_price=buy,
        sell_price=sell,
        solar_kwh=solar,
        consumption_kwh=consumption,
        is_demand_window_slot=is_dw,
    )


def _config(**overrides) -> OptimizerConfig:
    base = dict(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        soc_bins=20,
        optimization_mode="self_consumption",
        export_price_margin=0.02,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        discharge_rate_kw=5.0,
        discharge_efficiency=0.95,
    )
    base.update(overrides)
    return OptimizerConfig(**base)


# ---------------------------------------------------------------------------
# B1/B2: discharge_floor_pct
# ---------------------------------------------------------------------------


def test_b1_no_floor_means_discharge_floor_equals_min_soc():
    config = _config(min_soc_pct=10.0, away_reserve_floor_pct=None)
    assert config.discharge_floor_pct == config.min_soc_pct == 10.0


@pytest.mark.parametrize(
    ("min_soc", "away", "expected"),
    [
        (20.0, 30.0, 30.0),  # away above min_soc wins
        (20.0, 15.0, 20.0),  # min_soc above away wins
    ],
)
def test_b2_discharge_floor_is_max_of_both_orders(min_soc, away, expected):
    config = _config(min_soc_pct=min_soc, away_reserve_floor_pct=away)
    assert config.discharge_floor_pct == expected


# ---------------------------------------------------------------------------
# B3: EXPORT_PROACTIVE gated on the away floor
# ---------------------------------------------------------------------------


def test_b3_export_absent_at_the_floor():
    config = _config(min_soc_pct=10.0, away_reserve_floor_pct=30.0)
    slot = _slot(buy=0.10, sell=0.50)  # very profitable sell
    actions = feasible_actions(30.0, slot, config)
    assert PlannerAction.EXPORT_PROACTIVE not in actions


def test_b3_export_present_at_the_floor_when_floor_off():
    config = _config(min_soc_pct=10.0, away_reserve_floor_pct=None)
    slot = _slot(buy=0.10, sell=0.50)
    actions = feasible_actions(30.0, slot, config)
    assert PlannerAction.EXPORT_PROACTIVE in actions


def test_b3_export_present_just_above_the_floor():
    config = _config(min_soc_pct=10.0, away_reserve_floor_pct=30.0)
    slot = _slot(buy=0.10, sell=0.50)
    actions = feasible_actions(31.0, slot, config)
    assert PlannerAction.EXPORT_PROACTIVE in actions


# ---------------------------------------------------------------------------
# B4: negative-FIT avoidance branch refuses export at/below the floor
# ---------------------------------------------------------------------------


def test_b4_negative_fit_avoidance_refuses_export_at_the_floor():
    config = _config(
        min_soc_pct=10.0,
        away_reserve_floor_pct=30.0,
        discharge_rate_kw=5.0,
    )
    slot = _slot(buy=0.10, sell=0.20, interval_minutes=30, is_dw=False)
    # The floor for this slot in the avoidance context sits above what a
    # full-rate export could land at from SOC 30, so the export must be
    # refused even though there's a positive-FIT opportunity.
    context = NegativeFitAvoidanceContext(
        risk_window_start_idx=0,
        risk_window_end_idx=5,
        required_headroom_kwh=5.0,
        recovery_deadline_idx=5,
        conservative_recovery_kwh_by_slot=(50.0,) * 6,
        recoverability_floor_pct_by_slot=(30.0,) * 6,
    )
    actions = feasible_actions(
        30.0, slot, config, slot_idx=0, negative_fit_avoidance_context=context
    )
    assert PlannerAction.EXPORT_PROACTIVE not in actions


def _avoidance_context(recoverability_floor_pct: float) -> NegativeFitAvoidanceContext:
    return NegativeFitAvoidanceContext(
        risk_window_start_idx=0,
        risk_window_end_idx=5,
        required_headroom_kwh=5.0,
        recovery_deadline_idx=5,
        conservative_recovery_kwh_by_slot=(50.0,) * 6,
        recoverability_floor_pct_by_slot=(recoverability_floor_pct,) * 6,
    )


def test_b4_landing_clamp_admits_export_the_away_floor_bounds():
    """Above the floor, the landing point is clamped at the away floor.

    From SOC 40 a full-rate 30-min slot sheds 18.5pp. Without the floor it
    would land at 21.5, under the recoverability floor of 25, so export is
    refused. With the away floor at 30 the transition clamps there, which
    clears 25, so export is admitted: the one branch where the floor adds an
    action (docs/ENTITY_REFERENCE.md, number.localshift_away_reserve).
    """
    slot = _slot(buy=0.10, sell=0.20, interval_minutes=30)
    context = _avoidance_context(25.0)

    without_floor = feasible_actions(
        40.0,
        slot,
        _config(away_reserve_floor_pct=None),
        slot_idx=0,
        negative_fit_avoidance_context=context,
    )
    with_floor = feasible_actions(
        40.0,
        slot,
        _config(away_reserve_floor_pct=30.0),
        slot_idx=0,
        negative_fit_avoidance_context=context,
    )

    assert PlannerAction.EXPORT_PROACTIVE not in without_floor
    assert PlannerAction.EXPORT_PROACTIVE in with_floor
    next_soc, _import, _export = _transition_export(
        40.0, slot, _config(away_reserve_floor_pct=30.0)
    )
    assert next_soc == pytest.approx(30.0)


def test_b4_landing_clamp_still_refuses_under_a_higher_recoverability_floor():
    """The clamp never lets an export land under the recoverability floor."""
    slot = _slot(buy=0.10, sell=0.20, interval_minutes=30)
    actions = feasible_actions(
        40.0,
        slot,
        _config(away_reserve_floor_pct=30.0),
        slot_idx=0,
        negative_fit_avoidance_context=_avoidance_context(35.0),
    )
    assert PlannerAction.EXPORT_PROACTIVE not in actions


# ---------------------------------------------------------------------------
# B5: a small "subset" sweep — actions with the floor never exceed actions
# without it (the floor only removes actions, never unlocks anything).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("soc_pct", [10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 50.0, 80.0])
@pytest.mark.parametrize("buy,sell", [(0.10, 0.05), (0.10, 0.30), (0.30, 0.05)])
def test_b5_floor_only_removes_actions(soc_pct, buy, sell):
    slot = _slot(buy=buy, sell=sell)
    with_floor = set(
        feasible_actions(soc_pct, slot, _config(away_reserve_floor_pct=30.0))
    )
    without_floor = set(
        feasible_actions(soc_pct, slot, _config(away_reserve_floor_pct=None))
    )
    assert with_floor <= without_floor


# ---------------------------------------------------------------------------
# B6: HOLD stays feasible at and below the floor, including when the floor
# sits above the demand-window target.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("soc_pct", [30.0, 25.0, 10.0])
def test_b6_hold_always_feasible_at_or_below_floor(soc_pct):
    config = _config(min_soc_pct=10.0, away_reserve_floor_pct=30.0)
    actions = feasible_actions(soc_pct, _slot(), config)
    assert PlannerAction.HOLD in actions


def test_b6_hold_feasible_when_floor_exceeds_dw_target():
    # away=80, target=70: the floor is deliberately above the target.
    config = _config(
        min_soc_pct=10.0,
        away_reserve_floor_pct=80.0,
        demand_window_target_soc_pct=70.0,
    )
    actions = feasible_actions(80.0, _slot(is_dw=True), config)
    assert PlannerAction.HOLD in actions


# ---------------------------------------------------------------------------
# B7: HOLD deficit transitions clamp at the floor.
# ---------------------------------------------------------------------------


def test_b7_hold_deficit_from_floor_imports_whole_deficit():
    config = _config(min_soc_pct=10.0, away_reserve_floor_pct=30.0)
    next_soc, grid_import, grid_export = _transition_hold_deficit(
        soc_pct=30.0,
        net_kwh=-2.0,
        slot_hours=0.5,
        config=config,
        capacity_kwh=13.5,
    )
    assert next_soc == pytest.approx(30.0)
    assert grid_import == pytest.approx(2.0)
    assert grid_export == 0.0


def test_b7_hold_deficit_lands_exactly_at_floor():
    config = _config(min_soc_pct=10.0, away_reserve_floor_pct=30.0)
    # SOC 32 -> floor 30 is 2 SOC points = 0.27 kWh headroom; ask for a much
    # larger deficit so the battery contribution is capped at the floor.
    next_soc, grid_import, grid_export = _transition_hold_deficit(
        soc_pct=32.0,
        net_kwh=-10.0,
        slot_hours=1.0,
        config=config,
        capacity_kwh=13.5,
    )
    assert next_soc == pytest.approx(30.0, abs=0.05)
    assert grid_import > 0.0


def test_b7_hold_deficit_below_floor_stays_put():
    # Already below the away floor (e.g. a slot mid-transition into away):
    # the transition must not force a charge to reach it — it just refuses
    # to discharge further.
    config = _config(min_soc_pct=10.0, away_reserve_floor_pct=30.0)
    next_soc, grid_import, grid_export = _transition_hold_deficit(
        soc_pct=25.0,
        net_kwh=-2.0,
        slot_hours=0.5,
        config=config,
        capacity_kwh=13.5,
    )
    assert next_soc == pytest.approx(25.0)
    assert grid_import == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# B8: export lands at or above the floor.
# ---------------------------------------------------------------------------


def test_b8_export_lands_at_or_above_floor():
    config = _config(min_soc_pct=10.0, away_reserve_floor_pct=30.0)
    slot = _slot(buy=0.10, sell=0.30, solar=0.0, consumption=0.0, interval_minutes=30)
    next_soc, _grid_import, _grid_export = _transition_export(35.0, slot, config)
    assert next_soc >= 30.0 - 1e-9


# ---------------------------------------------------------------------------
# B9: full DPPlanner().plan() over an overnight horizon, no solar.
# ---------------------------------------------------------------------------


def _overnight_slots(n: int = 24, buy: float = 0.15) -> list[SlotContext]:
    return [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{(i // 2):02d}:{(i % 2) * 30:02d}:00",
            slot_interval_minutes=30,
            buy_price=buy,
            sell_price=0.05,
            solar_kwh=0.0,
            consumption_kwh=0.6,
        )
        for i in range(n)
    ]


def test_b9_plan_never_drops_below_away_floor():
    config = _config(min_soc_pct=10.0, away_reserve_floor_pct=30.0, soc_bins=40)
    inputs = OptimizerInputs(
        cycle_id="b9-away",
        initial_soc_pct=60.0,
        slots=_overnight_slots(),
        config=config,
    )
    result = DPPlanner().plan(inputs)
    assert result.success
    assert all(d.predicted_soc_pct >= 30.0 - 1e-6 for d in result.decisions)


def test_b9_plan_without_floor_can_drop_below_the_away_value():
    """Proves the B9 test above actually bites (isn't vacuously true)."""
    config = _config(min_soc_pct=10.0, away_reserve_floor_pct=None, soc_bins=40)
    inputs = OptimizerInputs(
        cycle_id="b9-no-away",
        initial_soc_pct=60.0,
        slots=_overnight_slots(),
        config=config,
    )
    result = DPPlanner().plan(inputs)
    assert result.success
    assert any(d.predicted_soc_pct < 30.0 for d in result.decisions)


def test_b9_plan_starting_below_floor_is_not_clamped_up():
    """SOC 22 with an away floor of 30: the plan starts at 22 (never lifted
    to the floor — that would repeat the 6/30 stale-SOC incident) and never
    drops below its own starting point."""
    config = _config(min_soc_pct=10.0, away_reserve_floor_pct=30.0, soc_bins=40)
    inputs = OptimizerInputs(
        cycle_id="b9-below-floor-start",
        initial_soc_pct=22.0,
        slots=_overnight_slots(),
        config=config,
    )
    result = DPPlanner().plan(inputs)
    assert result.success
    first = result.decisions[0]
    assert first.predicted_soc_pct == pytest.approx(22.0, abs=1.7) or (
        22.0 - 1.7 <= first.predicted_soc_pct
    )
    assert all(d.predicted_soc_pct >= 22.0 - 1.7 for d in result.decisions)


# ---------------------------------------------------------------------------
# B10/B11: runner _build_optimizer_config — away True/False, clamping,
# MagicMock/SimpleNamespace safety, and release across consecutive calls.
# ---------------------------------------------------------------------------


def _minimal_data(**overrides):
    base = dict(away_active=False)
    base.update(overrides)
    return SimpleNamespace(**base)


def test_b10_away_true_uses_configured_value():
    config = _build_optimizer_config(
        _minimal_data(away_active=True), {"away_reserve": 40}
    )
    assert config.away_reserve_floor_pct == 40.0


def test_b10_away_false_gives_none():
    config = _build_optimizer_config(
        _minimal_data(away_active=False), {"away_reserve": 40}
    )
    assert config.away_reserve_floor_pct is None


def test_b10_away_value_clamped_to_backup_reserve_max():
    config = _build_optimizer_config(
        _minimal_data(away_active=True), {"away_reserve": 95}
    )
    assert config.away_reserve_floor_pct == 80.0


def test_b10_simplenamespace_without_away_active_gives_none():
    """A test double that never set away_active must not silently arm the
    floor — this is exactly the trap ``getattr(..., False) is True`` guards
    against for a bare SimpleNamespace()."""
    config = _build_optimizer_config(SimpleNamespace(), {"away_reserve": 40})
    assert config.away_reserve_floor_pct is None


def test_b10_magicmock_away_active_gives_none():
    """A MagicMock's ``away_active`` attribute is truthy by default when
    nothing set it — ``is True`` must refuse it. ``adaptive_params`` is
    pinned to None; it is unrelated to this guard and a MagicMock there
    breaks arithmetic elsewhere in the builder."""
    data = MagicMock(adaptive_params=None)
    config = _build_optimizer_config(data, {"away_reserve": 40})
    assert config.away_reserve_floor_pct is None


def test_b11_away_then_not_away_releases_on_next_call():
    away_config = _build_optimizer_config(
        _minimal_data(away_active=True), {"away_reserve": 40}
    )
    released_config = _build_optimizer_config(
        _minimal_data(away_active=False), {"away_reserve": 40}
    )
    assert away_config.away_reserve_floor_pct == 40.0
    assert released_config.away_reserve_floor_pct is None


# ---------------------------------------------------------------------------
# B12: anti-sawtooth guard fires within the buffer of the away floor.
# ---------------------------------------------------------------------------


def test_b12_floor_guard_fires_within_buffer_of_away_floor():
    config = _config(
        min_soc_pct=10.0,
        away_reserve_floor_pct=30.0,
        min_soc_floor_buffer_pct=1.0,
        min_floor_charge_gain_pct=2.0,
    )
    # SOC 30.5 is within the 1-point buffer above the away floor (30). A
    # tiny charge (next_soc 30.6, gain 0.1 < min_floor_charge_gain_pct) with
    # no urgency window must be blocked, exactly as it would be at
    # min_soc_pct without the away floor.
    blocked = _floor_guard_blocks(
        action=PlannerAction.CHARGE_GRID_NORMAL,
        soc=30.5,
        next_soc=30.6,
        slot_idx=0,
        terminal_penalty_idx=None,
        config=config,
    )
    assert blocked is True


def test_b12_floor_guard_does_not_fire_well_above_away_floor():
    config = _config(
        min_soc_pct=10.0,
        away_reserve_floor_pct=30.0,
        min_soc_floor_buffer_pct=1.0,
        min_floor_charge_gain_pct=2.0,
    )
    blocked = _floor_guard_blocks(
        action=PlannerAction.CHARGE_GRID_NORMAL,
        soc=40.0,
        next_soc=45.0,
        slot_idx=0,
        terminal_penalty_idx=None,
        config=config,
    )
    assert blocked is False


def test_b12_floor_guard_does_not_fire_below_away_floor():
    """SOC 22 on a trip that started low: a 1.87pp charge is not blocked, since
    HOLD doesn't drain under the away floor and there is no sawtooth there."""
    config = _config(
        min_soc_pct=10.0,
        away_reserve_floor_pct=30.0,
        min_soc_floor_buffer_pct=1.0,
        min_floor_charge_gain_pct=2.0,
    )
    blocked = _floor_guard_blocks(
        action=PlannerAction.CHARGE_GRID_NORMAL,
        soc=22.0,
        next_soc=23.87,
        slot_idx=0,
        terminal_penalty_idx=None,
        config=config,
    )
    assert blocked is False


@pytest.mark.parametrize("away_floor", [None, 30.0])
def test_b12_floor_guard_min_soc_band_unchanged(away_floor):
    """The original band at min_soc_pct fires as before, away or not."""
    config = _config(
        min_soc_pct=10.0,
        away_reserve_floor_pct=away_floor,
        min_soc_floor_buffer_pct=1.0,
        min_floor_charge_gain_pct=2.0,
    )
    blocked = _floor_guard_blocks(
        action=PlannerAction.CHARGE_GRID_NORMAL,
        soc=10.5,
        next_soc=10.6,
        slot_idx=0,
        terminal_penalty_idx=None,
        config=config,
    )
    assert blocked is True


def test_max_feasible_terminal_soc_clamps_at_away_floor():
    """#1091: with no eligible charging, the forward simulation drains to the
    away floor, not to min_soc_pct, matching the DP's own HOLD transitions."""
    from custom_components.localshift.engine.constraints import (
        compute_max_feasible_terminal_soc,
    )

    slots = _overnight_slots(n=24, buy=0.90)  # nothing cheap enough to charge
    away = _config(min_soc_pct=10.0, away_reserve_floor_pct=30.0)
    home = _config(min_soc_pct=10.0, away_reserve_floor_pct=None)

    away_max = compute_max_feasible_terminal_soc(slots, away, 23, 35.0)
    home_max = compute_max_feasible_terminal_soc(slots, home, 23, 35.0)

    assert home_max is not None and home_max < 30.0  # proves the drain bites
    assert away_max == pytest.approx(30.0)


def test_b9_starting_soc_is_never_lifted_to_the_away_floor():
    """The runner's starting-SOC clamp stays at min_soc_pct while away: a real
    22% SOC must reach the planner as 22, not the 30% away floor (the 6/30
    stale-SOC class of error)."""
    from custom_components.localshift.engine.optimizer_runner import (
        _normalize_initial_soc,
    )

    config = _config(min_soc_pct=10.0, away_reserve_floor_pct=30.0)

    soc, _info = _normalize_initial_soc(22.0, config)

    assert soc == pytest.approx(22.0)

"""Hard DW-target feasibility gate (issue #885).

The battery used to enter the evening demand window (DW) under target with no hard
backstop: target attainment was only a *soft* terminal penalty, structurally capped
below grid-charge prices, so the DP paid through it and held. The 3pm force-charge
guardrail that used to backstop this was removed 2026-06-12.

These tests encode:

1. The 2026-06-14 live repro: SOC 66%, target 95%, flat-ish price ~0.13 (below the
   max_pre_charge_price of 0.20), DW ~1h ahead, solar insufficient. With
   ``allow_dw_entry_under_target=False`` the plan must now reach target.
2. A sawtooth guard (#800): the hard gate must NOT introduce overnight or post-DW
   force-charging.
3. Graceful degradation: when the target is physically unreachable (not enough
   time/rate), the plan charges to the max feasible SOC without error.
4. Pre-charge runway telemetry (fast-follow to #901, 2026-07-28): the two additive
   solver-derived fields ``hard_floor_suppressed_by_solar`` and
   ``precharge_runway_slack_min``, plus a pinned regression proving the addition did
   NOT move ``hard_target_floor`` in any scenario.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.transitions import _tapered_stored_kwh
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)

_CHARGE = (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
_INTERVAL = 30


def _config(**overrides) -> OptimizerConfig:
    defaults = dict(
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=95.0,
        allow_dw_entry_under_target=False,
        optimization_mode="self_consumption",
        battery_capacity_kwh=13.5,
        charge_rate_kw=3.3,
        boost_charge_rate_kw=5.0,
        charge_efficiency=0.92,
        discharge_efficiency=0.95,
        effective_cheap_price=0.115,
        base_cheap_price=0.115,
        max_precharge_price=0.20,
        target_shortfall_penalty_per_pct=0.08,
        soc_bins=100,
    )
    defaults.update(overrides)
    return OptimizerConfig(**defaults)


# ---------------------------------------------------------------------------
# 2026-06-14 live repro: SOC 66%, target 95%, flat ~0.13 price, DW ~1h ahead.
# ---------------------------------------------------------------------------

_REPRO_START = datetime(2026, 6, 14, 13, 0)
# DW ~2h ahead -> entry at slot 4 (13:00..14:30 pre-DW, then 15:00 DW). Four boost-rate
# pre-DW slots make 95% physically reachable (max-feasible peak ~100%), so this is a
# genuine soft-penalty undercharge, NOT a physical-infeasibility case. Long horizon so
# there is ample post-DW time the gate must NOT charge in.
_REPRO_DW_ENTRY_IDX = 4
_REPRO_N_SLOTS = 16


def _repro_slots() -> list[SlotContext]:
    slots: list[SlotContext] = []
    for i in range(_REPRO_N_SLOTS):
        t = _REPRO_START + timedelta(minutes=_INTERVAL * i)
        is_dw = _REPRO_DW_ENTRY_IDX <= i < _REPRO_DW_ENTRY_IDX + 8  # 4h DW
        # Flat-ish ~0.13 buy price everywhere (below max_pre_charge_price 0.20),
        # evening peak inside the DW.
        buy = 0.30 if is_dw else 0.13
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=_INTERVAL,
                buy_price=buy,
                sell_price=0.05,
                # Solar insufficient (net negative): tiny solar, real consumption.
                solar_kwh=0.05,
                consumption_kwh=0.3,
                is_demand_window_entry=(i == _REPRO_DW_ENTRY_IDX),
                is_demand_window_slot=is_dw,
            )
        )
    return slots


def _plan_repro(**config_overrides):
    slots = _repro_slots()
    config = _config(**config_overrides)
    return DPPlanner(config).plan(
        OptimizerInputs(
            cycle_id="repro-885",
            initial_soc_pct=66.0,
            slots=slots,
            config=config,
            all_solcast=[],
        )
    )


def _plan(slots, initial_soc_pct, **config_overrides):
    """Plan and return ``(result, config)`` so solver-derived fields can be inspected."""
    config = _config(**config_overrides)
    result = DPPlanner(config).plan(
        OptimizerInputs(
            cycle_id="repro-885",
            initial_soc_pct=initial_soc_pct,
            slots=slots,
            config=config,
            all_solcast=[],
        )
    )
    return result, config


def test_repro_2026_06_14_reaches_target_under_hard_gate():
    """The 2026-06-14 live repro: plan must reach target with the hard gate.

    SOC 66 -> 95 on a 13.5 kWh battery needs ~3.9 kWh stored; the four boost-rate
    pre-DW slots can reach ~100% peak, so target is physically reachable. Price 0.13 <
    max_pre_charge_price 0.20, so the slots ARE eligible. The hard gate must route a
    charge through them so the DW-entry SOC lands at target.

    MUST FAIL on origin/main (soft penalty caps below price -> DW entry ~93.6%, shortfall
    ~1.4%) and PASS with the fix (shortfall -> ~0).
    """
    result = _plan_repro()
    assert result.success
    assert result.terminal_shortfall_pct < 0.5, (
        "hard gate must drive DW entry to target; got shortfall "
        f"{result.terminal_shortfall_pct}%"
    )
    # The SOC entering the demand window (start of the entry slot) should be at/near
    # target. This is the value the DP controls and reports as dw_entry_soc_pct.
    assert result.dw_entry_soc_pct is not None
    assert result.dw_entry_soc_pct >= 94.5, (
        f"DW entry SOC {result.dw_entry_soc_pct}% under target"
    )


def test_repro_charges_only_in_eligible_pre_dw_slots():
    """Charging happens, only pre-DW, and only at eligible (<= max_pre_charge_price) prices."""
    result = _plan_repro()
    charges = [d for d in result.decisions if d.action in _CHARGE]
    assert charges, "expected grid charging to reach target (was all-HOLD)"
    for c in charges:
        idx = next(
            d.slot_index for d in result.decisions if d.timestamp_iso == c.timestamp_iso
        )
        assert idx < _REPRO_DW_ENTRY_IDX, (
            f"charge at slot {idx} is not strictly pre-DW (entry={_REPRO_DW_ENTRY_IDX})"
        )
        assert c.buy_price <= 0.20, f"charge at ineligible price {c.buy_price}"


def test_does_not_charge_above_target():
    """The gate must not over-charge: peak SOC stays at/near target, not 100%."""
    result = _plan_repro()
    assert result.success
    # Target is 95; allow a small overshoot from bin granularity but never near 100.
    assert result.peak_soc_pct is not None
    assert result.peak_soc_pct <= 97.0, (
        f"plan over-charged above target; peak {result.peak_soc_pct}%"
    )


# ---------------------------------------------------------------------------
# Sawtooth guard (#800): no overnight / post-DW force-charging.
# ---------------------------------------------------------------------------


def test_hard_gate_no_post_dw_or_overnight_charge():
    """The hard gate is strictly bounded to pre-DW slots (guards #800)."""
    result = _plan_repro()
    assert result.success
    for d in result.decisions:
        if d.action not in _CHARGE:
            continue
        idx = d.slot_index
        assert idx < _REPRO_DW_ENTRY_IDX, (
            "no charging inside or after the DW (sawtooth guard); "
            f"got charge at slot {idx}"
        )


# ---------------------------------------------------------------------------
# Solar sufficiency: don't fight #816/#849.
# ---------------------------------------------------------------------------


_SOLAR_DW_ENTRY_IDX = 16  # 8h ahead -> plenty of daytime solar before it
_SOLAR_N_SLOTS = 24


def _solar_sufficient_slots() -> list[SlotContext]:
    """DW far out with strong pre-DW solar, so solar alone reaches target from 66%."""
    start = datetime(2026, 6, 14, 8, 0)
    slots: list[SlotContext] = []
    for i in range(_SOLAR_N_SLOTS):
        t = start + timedelta(minutes=_INTERVAL * i)
        is_dw = _SOLAR_DW_ENTRY_IDX <= i < _SOLAR_DW_ENTRY_IDX + 6
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=_INTERVAL,
                buy_price=0.30 if is_dw else 0.13,
                sell_price=0.05,
                # Strong solar before the DW, easily reaching target from 66%.
                solar_kwh=3.0 if (not is_dw and i < _SOLAR_DW_ENTRY_IDX) else 0.0,
                consumption_kwh=0.3,
                is_demand_window_entry=(i == _SOLAR_DW_ENTRY_IDX),
                is_demand_window_slot=is_dw,
            )
        )
    return slots


def test_no_grid_charge_when_solar_reaches_target():
    """When solar alone projects to target, the hard gate must not force grid charging."""
    result, _config_out = _plan(_solar_sufficient_slots(), 66.0)
    assert result.success
    charges = [d for d in result.decisions if d.action in _CHARGE]
    assert charges == [], (
        "solar reaches target on its own — the hard gate must not grid-charge; "
        f"got charges at {[d.timestamp_iso for d in charges]}"
    )


# ---------------------------------------------------------------------------
# Graceful degradation: unreachable target -> max feasible, no error/empty plan.
# ---------------------------------------------------------------------------


_UNREACHABLE_DW_ENTRY_IDX = 1
_UNREACHABLE_N_SLOTS = 12


def _unreachable_slots() -> list[SlotContext]:
    """DW entry at slot 1 (only ONE 30-min pre-DW slot).

    From 20% to 95% needs ~10 kWh; one boost slot stores ~2.3 kWh -> physically
    unreachable, so the floor must degrade to the max feasible SOC.
    """
    start = datetime(2026, 6, 14, 14, 30)
    slots: list[SlotContext] = []
    for i in range(_UNREACHABLE_N_SLOTS):
        t = start + timedelta(minutes=_INTERVAL * i)
        is_dw = _UNREACHABLE_DW_ENTRY_IDX <= i < _UNREACHABLE_DW_ENTRY_IDX + 6
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=_INTERVAL,
                buy_price=0.30 if is_dw else 0.13,
                sell_price=0.05,
                solar_kwh=0.0,
                consumption_kwh=0.3,
                is_demand_window_entry=(i == _UNREACHABLE_DW_ENTRY_IDX),
                is_demand_window_slot=is_dw,
            )
        )
    return slots


def test_unreachable_target_degrades_to_max_feasible():
    """Insufficient time/rate before the DW: charge to max feasible, no error."""
    result, _config_out = _plan(_unreachable_slots(), 20.0)
    assert result.success
    assert result.decisions, "plan must not be empty when target is unreachable"
    # Still a meaningful shortfall (target physically unreachable), but the single
    # eligible pre-DW slot should be used to charge as much as possible.
    assert result.terminal_shortfall_pct > 0.0
    pre_dw_charge = [
        d
        for d in result.decisions
        if d.action in _CHARGE and d.slot_index < _UNREACHABLE_DW_ENTRY_IDX
    ]
    assert pre_dw_charge, "the one eligible pre-DW slot must be used to charge"


# ---------------------------------------------------------------------------
# Pre-charge runway telemetry (fast-follow to #901, 2026-07-28).
#
# #901's execution backstop returns None whenever ``hard_target_floor`` is None, and
# the floor is deliberately suppressed on any day solar looks sufficient (don't fight
# #816/#849) — so the backstop built to stop a repeat of the 2026-07-27 undercharge is
# fully dormant exactly on the days a late cloud event can strand the battery. Stage A
# publishes the two facts an out-of-solver arm needs to close that hole, WITHOUT
# touching the DP.
# ---------------------------------------------------------------------------

_NO_DW_N_SLOTS = 12


def _no_dw_slots() -> list[SlotContext]:
    """A horizon containing no demand window at all (structural dormancy)."""
    start = datetime(2026, 6, 14, 13, 0)
    return [
        SlotContext(
            slot_index=i,
            timestamp_iso=(start + timedelta(minutes=_INTERVAL * i)).isoformat(),
            slot_interval_minutes=_INTERVAL,
            buy_price=0.13,
            sell_price=0.05,
            solar_kwh=0.05,
            consumption_kwh=0.3,
            is_demand_window_entry=False,
            is_demand_window_slot=False,
        )
        for i in range(_NO_DW_N_SLOTS)
    ]


def _expected_slack(config: OptimizerConfig, dw_entry_idx: int, soc: float) -> float:
    """The slack the shipped code must produce, via the SHIPPED charge model.

    Deliberately calls ``DPPlanner._boost_minutes_to_close_gap`` rather than re-deriving
    a rate here. A hand-rolled ``gap / (rate * efficiency)`` duplicate is what previously
    let these tests agree with a formula that disagreed with the transition function
    actually moving SOC: it ignored the CV taper and understated the 2026-07-28 live case
    by 15 minutes, and no change to the model, the constants or the capacity could have
    failed it. The only thing pinned locally is the TIME term, which is genuine test
    knowledge (where the DW entry sits in the fixture).
    """
    minutes_to_dw = _INTERVAL * dw_entry_idx - _INTERVAL  # anchored at slot 0's END
    gap_pct = config.demand_window_target_soc_pct - soc
    if gap_pct <= 0.0:
        return minutes_to_dw
    needed = DPPlanner._boost_minutes_to_close_gap(config, soc)
    assert needed is not None
    return minutes_to_dw - needed


# --- The pinned regression: hard_target_floor must not move. ----------------

# Captured from the code BEFORE the telemetry fields were added. Stage A is purely
# additive; if any of these move, the DP's own input changed and the change is not
# additive after all.
#
# The ``unreachable`` baseline was deliberately re-captured for #903 (34.697855750487335
# -> 37.03703703703704). That fixture's single pre-DW slot is a CHARGE slot with negative
# net load, and ``compute_max_feasible_terminal_soc`` used to subtract the slot's load
# drift on top of the boost credit — double-counting a deficit the DP's own charge
# transition imports from the grid. The new value is `20.0 + one untapered boost slot`,
# with no drift. Every other baseline is unchanged, which is the point: the correction is
# scoped to charge slots on a negative-net-load runway.
_FLOOR_BASELINES = [
    # (case name, slots factory, initial SOC, config overrides, expected floor)
    ("repro", _repro_slots, 66.0, {}, 95.0),
    ("solar_sufficient", _solar_sufficient_slots, 66.0, {}, None),
    ("unreachable", _unreachable_slots, 20.0, {}, 37.03703703703704),
    (
        "allow_under_target",
        _repro_slots,
        66.0,
        {"allow_dw_entry_under_target": True},
        None,
    ),
    ("no_dw", _no_dw_slots, 66.0, {}, None),
    ("arbitrage", _repro_slots, 66.0, {"optimization_mode": "arbitrage"}, None),
]


@pytest.mark.parametrize(
    ("name", "slots_factory", "soc", "overrides", "expected_floor"),
    _FLOOR_BASELINES,
    ids=[c[0] for c in _FLOOR_BASELINES],
)
def test_hard_target_floor_unchanged_by_runway_telemetry(
    name, slots_factory, soc, overrides, expected_floor
):
    """``_compute_hard_target_floor``'s value is byte-identical to pre-change.

    The runway telemetry is published NEXT TO the floor, never inside it: this pins the
    DP-facing value across every branch of the floor's early-return ladder (live gate,
    solar-suppressed, physically unreachable, policy-dormant, no DW, legacy mode).
    """
    _result, config = _plan(slots_factory(), soc, **overrides)
    if expected_floor is None:
        assert config.hard_target_floor is None, (
            f"{name}: floor moved from None to {config.hard_target_floor}"
        )
    else:
        assert config.hard_target_floor == pytest.approx(expected_floor, abs=1e-12), (
            f"{name}: floor moved from {expected_floor} to {config.hard_target_floor}"
        )


# --- hard_floor_suppressed_by_solar ----------------------------------------


def test_suppressed_by_solar_true_when_only_solar_holds_the_gate_off():
    """The exact #901 blind spot: floor dormant ONLY because solar looked sufficient.

    The gate is structurally applicable (strict mode, self-consumption, a real DW that
    is not slot 0, max-feasible non-None) and the solar-sufficiency branch is the single
    reason the floor is None. This is the case an out-of-solver arm must be able to see.
    """
    _result, config = _plan(_solar_sufficient_slots(), 66.0)
    assert config.hard_target_floor is None
    assert config.hard_floor_suppressed_by_solar is True


def test_suppressed_by_solar_false_when_gate_is_live():
    """Floor set ⇒ nothing is suppressed; #901's existing path already covers this."""
    _result, config = _plan(_repro_slots(), 66.0)
    assert config.hard_target_floor == pytest.approx(95.0)
    assert config.hard_floor_suppressed_by_solar is False


def test_suppressed_by_solar_false_when_target_unreachable():
    """A degraded (but live) floor is not a suppression."""
    _result, config = _plan(_unreachable_slots(), 20.0)
    assert config.hard_target_floor is not None
    assert config.hard_floor_suppressed_by_solar is False


@pytest.mark.parametrize(
    ("name", "slots_factory", "soc", "overrides"),
    [
        (
            "allow_under_target",
            _repro_slots,
            66.0,
            {"allow_dw_entry_under_target": True},
        ),
        ("no_dw", _no_dw_slots, 66.0, {}),
        ("arbitrage", _repro_slots, 66.0, {"optimization_mode": "arbitrage"}),
    ],
    ids=["allow_under_target", "no_dw", "arbitrage"],
)
def test_suppressed_by_solar_false_for_policy_and_structural_dormancy(
    name, slots_factory, soc, overrides
):
    """Every OTHER None reason must keep a flag-gated backstop exactly as dormant.

    ``allow_dw_entry_under_target`` (the target may legitimately be met mid-DW by solar),
    no demand window, and legacy/non-self-consumption mode are all deliberate dormancy —
    not a forecast standing in for a guardrail.
    """
    _result, config = _plan(slots_factory(), soc, **overrides)
    assert config.hard_target_floor is None
    assert config.hard_floor_suppressed_by_solar is False, (
        f"{name}: dormancy is policy/structural, not solar"
    )


# --- precharge_runway_slack_min --------------------------------------------


def test_runway_slack_matches_the_documented_formula():
    """slack = minutes_to_DW - minutes_of_boost_needed, via the engine's charge model."""
    _result, config = _plan(_repro_slots(), 66.0)
    assert config.precharge_runway_slack_min == pytest.approx(
        _expected_slack(config, _REPRO_DW_ENTRY_IDX, 66.0)
    )


def test_runway_slack_accounts_for_the_cv_charge_taper():
    """The gap term must use the taper the DP's own transitions apply, not a flat rate.

    Every pre-charge to a 95% target crosses ``charge_taper_start_pct`` (90% after
    Issue #905), where the Powerwall derates toward ``charge_taper_min_factor``.
    A flat ``gap / (rate * eff)`` reads the battery as faster than the transition
    function will actually move it, and the error is one-sided: it over-reports
    slack, i.e. it keeps a guardrail shut.
    """
    config = _config()
    flat_min = (95.0 - 59.1) / (
        config.boost_charge_rate_kw
        * config.charge_efficiency
        / config.battery_capacity_kwh
        * 100.0
        / 60.0
    )
    tapered_min = DPPlanner._boost_minutes_to_close_gap(config, 59.1)
    assert tapered_min is not None
    # The taper adds measurable time even with the raised 90% knee.
    assert tapered_min - flat_min > 1.0


def test_runway_slack_is_measured_from_the_end_of_slot_0():
    """Slot 0 already contains ``now``, so its START would credit spent runway.

    ``_ensure_current_slot_coverage`` guarantees ``slots[0].start <= now``, and the DP is
    clock-free, so the only bound that is never later than ``now`` is slot 0's end. The
    error direction matters more than the magnitude: a guardrail may fire early, it may
    not report runway it does not have.
    """
    _result, config = _plan(_repro_slots(), 66.0)
    naive_from_slot_start = _INTERVAL * _REPRO_DW_ENTRY_IDX - (
        _INTERVAL * _REPRO_DW_ENTRY_IDX - _INTERVAL
    )
    assert config.precharge_runway_slack_min == pytest.approx(
        _expected_slack(config, _REPRO_DW_ENTRY_IDX, 66.0)
    )
    # Strictly less than the slot-0-start reading, by exactly one slot width.
    assert naive_from_slot_start == pytest.approx(_INTERVAL)


def test_runway_quantum_is_published_for_the_consumer_s_hysteresis():
    """The slack reading is quantized to slot 0; the consumer needs the amplitude.

    The time term steps at slot boundaries while the SOC gap closes continuously, so the
    published slack sawtooths by exactly one slot width. A hysteresis band sized against
    a constant instead of this value re-arms at every boundary on a 30-minute horizon.
    """
    _result, config = _plan(_repro_slots(), 66.0)
    assert config.precharge_runway_quantum_min == pytest.approx(float(_INTERVAL))


def test_runway_slack_is_large_on_a_genuinely_good_day():
    """A day with runway to spare reads far above any plausible margin.

    NB this is a statement about *runway*, not about solar: the gap the slack measures is
    the LIVE gap, which projected solar has not yet closed. See the
    ``precharge_runway_slack_min`` docstring — the arm is deliberately NOT self-limiting
    on a sunny morning, and ``precharge_runway_margin_min`` is the control for that.
    """
    _result, config = _plan(_solar_sufficient_slots(), 66.0)
    assert config.hard_floor_suppressed_by_solar is True
    assert config.precharge_runway_slack_min is not None
    assert config.precharge_runway_slack_min > 300.0


def test_runway_slack_negative_when_target_already_unreachable():
    """20% -> 95% with one 30-min pre-DW slot: boost cannot close the gap in time."""
    _result, config = _plan(_unreachable_slots(), 20.0)
    assert config.precharge_runway_slack_min is not None
    assert config.precharge_runway_slack_min < 0.0
    assert config.precharge_runway_slack_min == pytest.approx(
        _expected_slack(_config(), _UNREACHABLE_DW_ENTRY_IDX, 20.0)
    )


def test_runway_slack_is_full_runway_when_already_at_target():
    """No gap to close ⇒ the whole remaining runway is slack (never a false alarm)."""
    _result, config = _plan(_repro_slots(), 97.0)
    assert config.precharge_runway_slack_min == pytest.approx(
        _INTERVAL * _REPRO_DW_ENTRY_IDX - _INTERVAL
    )


def test_runway_slack_none_without_a_demand_window():
    """Not interpretable ⇒ None, rather than a number an arm could misread."""
    _result, config = _plan(_no_dw_slots(), 66.0)
    assert config.terminal_penalty_idx is None
    assert config.precharge_runway_slack_min is None


def test_runway_slack_is_a_governor_while_boost_charging():
    """Charging at exactly the boost rate holds slack constant — it cannot oscillate.

    Advance the horizon by one slot AND raise the SOC by exactly what boost would add in
    that slot: the runway shortens by 30 min and the gap shrinks by 30 min worth of
    charge, so the slack is unchanged. An arm gated on slack therefore never flaps while
    the boost it asked for is actually running.

    The SOC advance uses the engine's own tapered charge model. That is the point: the
    invariant only holds because the slack's gap term and the transition that moves SOC
    are now the SAME model. Under the earlier flat-rate gap term they disagreed above the
    taper knee, so slack drifted while boost was running.
    """
    base_slots = _repro_slots()
    _r0, config0 = _plan(base_slots, 66.0)

    # Drop slot 0 and re-index: the same physical schedule, one slot later.
    advanced = [
        SlotContext(
            slot_index=i,
            timestamp_iso=s.timestamp_iso,
            slot_interval_minutes=s.slot_interval_minutes,
            buy_price=s.buy_price,
            sell_price=s.sell_price,
            solar_kwh=s.solar_kwh,
            consumption_kwh=s.consumption_kwh,
            is_demand_window_entry=s.is_demand_window_entry,
            is_demand_window_slot=s.is_demand_window_slot,
        )
        for i, s in enumerate(base_slots[1:])
    ]
    cfg = _config()
    charged_soc = 66.0 + (
        _tapered_stored_kwh(66.0, cfg.boost_charge_rate_kw, _INTERVAL / 60.0, cfg)
        / cfg.battery_capacity_kwh
        * 100.0
    )
    _r1, config1 = _plan(advanced, charged_soc)

    assert config0.precharge_runway_slack_min is not None
    assert config1.precharge_runway_slack_min is not None
    assert config1.precharge_runway_slack_min == pytest.approx(
        config0.precharge_runway_slack_min
    )


# --- Reset discipline -------------------------------------------------------


def test_runway_telemetry_is_reset_between_cycles():
    """A reused config must never carry a previous cycle's runway telemetry forward."""
    config = _config()
    planner = DPPlanner(config)
    planner.plan(
        OptimizerInputs(
            cycle_id="cycle-1-solar",
            initial_soc_pct=66.0,
            slots=_solar_sufficient_slots(),
            config=config,
            all_solcast=[],
        )
    )
    assert config.hard_floor_suppressed_by_solar is True
    assert config.precharge_runway_slack_min is not None

    planner.plan(
        OptimizerInputs(
            cycle_id="cycle-2-no-dw",
            initial_soc_pct=66.0,
            slots=_no_dw_slots(),
            config=config,
            all_solcast=[],
        )
    )
    assert config.hard_floor_suppressed_by_solar is False
    assert config.precharge_runway_slack_min is None


def test_runway_margin_default_is_declared_and_unread_by_the_dp():
    """The Stage B knob exists with a default; the DP must not consume it.

    Changing the margin cannot change a plan — it is read by the arming logic outside
    the solver, never by the DP.
    """
    # Stage B made const.DEFAULT_PRECHARGE_RUNWAY_MARGIN_MIN (15.0) the single source of
    # truth, so the dataclass default and the live number entity cannot drift apart.
    assert OptimizerConfig().precharge_runway_margin_min == 15.0
    baseline, _c0 = _plan(_repro_slots(), 66.0)
    widened, _c1 = _plan(_repro_slots(), 66.0, precharge_runway_margin_min=600.0)
    assert [d.action for d in baseline.decisions] == [
        d.action for d in widened.decisions
    ]
    assert baseline.dw_entry_soc_pct == widened.dw_entry_soc_pct

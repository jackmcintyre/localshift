"""DW-entry floor on a negative-net-load pre-DW runway (issue #903).

Two coupled defects, both observed live on 2026-07-29 (masked at the hardware by the
#901 execution backstop, so the battery reached target while the *plan* said ``hold``):

1. ``compute_max_feasible_terminal_soc`` subtracted the forecast net-load drift in every
   pre-DW slot, INCLUDING the slots where it also credited a boost grid charge. The DP's
   own charge transition imports the load deficit from the grid instead of draining the
   battery (``_charge_grid_with_deficit``), so the function's documented upper-bound
   property was false whenever net load was negative. Live: 88.67 reported against a
   physically reachable 95.0, which degraded ``hard_target_floor`` below a reachable
   target.

2. The terminal penalty was non-monotone at the floor boundary — below the floor it
   measured the gap to the FLOOR, at/above it the gap to the TARGET. Since the floor is
   ``min(target, max_feasible)``, a degraded floor made below-floor states strictly
   CHEAPER than states just above it ($3.24 vs $615.60 across the boundary in replay), so
   the DP deliberately parked under the degraded floor rather than charging past it.

The test gap that let this survive: no test covered a pre-DW runway with negative net
load. These fixtures are that shape.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.localshift.engine.constraints import (
    compute_max_feasible_terminal_soc,
)
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)

_CHARGE = (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)

# --- The 2026-07-29 live shape ---------------------------------------------
#
# 12 remaining pre-DW 5-minute slots, mid-morning cloud collapse (solar 0.6 kW), a load
# spike leaving ~1.73 kW of net load, live buy price 0.06 against a 0.12 cheap-effective
# threshold — i.e. every remaining slot comfortably eligible.
_LIVE_INTERVAL = 5
_LIVE_DW_ENTRY_IDX = 12
_LIVE_N_SLOTS = 24
_LIVE_INITIAL_SOC = 67.15
_LIVE_SOLAR_KW = 0.6
_LIVE_NET_LOAD_KW = 1.73


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
        solar_charge_rate_kw=5.0,
        charge_efficiency=0.92,
        discharge_efficiency=0.95,
        effective_cheap_price=0.12,
        base_cheap_price=0.12,
        max_precharge_price=0.20,
        target_shortfall_penalty_per_pct=0.10,
        soc_bins=100,
    )
    defaults.update(overrides)
    return OptimizerConfig(**defaults)


def _live_slots(
    n_slots: int = _LIVE_N_SLOTS,
    dw_entry_idx: int = _LIVE_DW_ENTRY_IDX,
    solar_kw: float = _LIVE_SOLAR_KW,
    net_load_kw: float = _LIVE_NET_LOAD_KW,
) -> list[SlotContext]:
    """Pre-DW runway with NEGATIVE net load — the shape no prior test covered."""
    start = datetime(2026, 7, 29, 14, 4)
    hours = _LIVE_INTERVAL / 60.0
    solar_kwh = solar_kw * hours
    consumption_kwh = (solar_kw + net_load_kw) * hours
    slots: list[SlotContext] = []
    for i in range(n_slots):
        t = start + timedelta(minutes=_LIVE_INTERVAL * i)
        is_dw = i >= dw_entry_idx
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=_LIVE_INTERVAL,
                buy_price=0.30 if is_dw else 0.06,
                sell_price=0.03,
                solar_kwh=0.0 if is_dw else solar_kwh,
                consumption_kwh=consumption_kwh,
                is_demand_window_entry=(i == dw_entry_idx),
                is_demand_window_slot=is_dw,
            )
        )
    return slots


def _plan(slots, initial_soc_pct, **overrides):
    config = _config(**overrides)
    result = DPPlanner(config).plan(
        OptimizerInputs(
            cycle_id="repro-903",
            initial_soc_pct=initial_soc_pct,
            slots=slots,
            config=config,
            all_solcast=[],
        )
    )
    return result, config


def _boost_gain_pct(config: OptimizerConfig, interval_min: int) -> float:
    """Untapered boost SOC gain for one slot — the credit max-feasible applies."""
    return (
        config.boost_charge_rate_kw
        * (interval_min / 60.0)
        * config.charge_efficiency
        / config.battery_capacity_kwh
        * 100.0
    )


# ---------------------------------------------------------------------------
# Defect 1 — drift must not be double-counted in a credited charge slot.
# ---------------------------------------------------------------------------


def test_max_feasible_does_not_subtract_drift_in_charge_slots():
    """12 cheap boost slots reach the 95 ceiling; the old code reported ~88.

    Each 5-minute boost slot stores ~2.84 SOC points. Twelve of them is ~34 points, so
    from 67.15 the ceiling clamp at target is hit well before the DW entry. The pre-fix
    code subtracted ~1.12 points of load drift in each of those same slots, cutting the
    per-slot gain to ~1.72 and landing ~21.5 points above initial — the fixed "+21.5
    regardless of SOC" signature seen live.
    """
    config = _config()
    slots = _live_slots()

    max_feasible = compute_max_feasible_terminal_soc(
        slots, config, _LIVE_DW_ENTRY_IDX, _LIVE_INITIAL_SOC
    )

    assert max_feasible == pytest.approx(config.demand_window_target_soc_pct)
    # Guard the specific regression: the old value sat ~6.3 points under target.
    assert max_feasible > 94.0

    # And the per-slot credit really is the full untapered boost gain, undrifted.
    short_runway = _live_slots(n_slots=4, dw_entry_idx=2)
    two_slot = compute_max_feasible_terminal_soc(short_runway, config, 2, 40.0)
    assert two_slot == pytest.approx(40.0 + 2 * _boost_gain_pct(config, _LIVE_INTERVAL))


def test_max_feasible_is_a_true_upper_bound_on_the_dp():
    """The documented contract: no feasible plan may exceed the reported max.

    This is the property the drift double-count broke — the DP's own transitions reached
    94.37 while max-feasible claimed 88.67. Charging is the most this trajectory ever
    does, and it ignores the CV taper the DP pays, so it must bound the DP from above.
    """
    slots = _live_slots()
    result, config = _plan(slots, _LIVE_INITIAL_SOC)
    max_feasible = compute_max_feasible_terminal_soc(
        slots, config, _LIVE_DW_ENTRY_IDX, _LIVE_INITIAL_SOC
    )

    assert result.success
    assert result.dw_entry_soc_pct is not None
    assert max_feasible is not None
    assert result.dw_entry_soc_pct <= max_feasible + 1e-9, (
        f"DP reached {result.dw_entry_soc_pct} above the claimed max {max_feasible}"
    )


def test_max_feasible_still_drifts_in_non_charging_slots():
    """The undrifted path is scoped to credited charge slots only.

    With every pre-DW price above ``max_precharge_price`` nothing is eligible, so the
    trajectory must fall on pure solar/load drift and LOSE SOC. A blanket "never drift"
    would have made this rise.
    """
    config = _config()
    slots = _live_slots()
    for slot in slots:
        if not slot.is_demand_window_slot:
            slot.buy_price = 0.50  # above max_precharge_price -> ineligible

    max_feasible = compute_max_feasible_terminal_soc(
        slots, config, _LIVE_DW_ENTRY_IDX, _LIVE_INITIAL_SOC
    )
    assert max_feasible is not None
    assert max_feasible < _LIVE_INITIAL_SOC, (
        "with no eligible slot the runway must drain on net load, not hold flat"
    )


def test_max_feasible_stacks_solar_surplus_on_a_charge_slot():
    """Positive net load in a charge slot mirrors ``_charge_grid_with_solar``.

    Grid charge and solar surplus both land in the battery; the solar side is not capped
    by ``solar_charge_rate_kw`` here because that cap governs the solar-ONLY path.
    """
    config = _config()
    slots = _live_slots(n_slots=4, dw_entry_idx=2, solar_kw=3.0, net_load_kw=-2.0)
    surplus_kwh = slots[0].solar_kwh - slots[0].consumption_kwh
    assert surplus_kwh > 0

    max_feasible = compute_max_feasible_terminal_soc(slots, config, 2, 40.0)
    per_slot = (
        _boost_gain_pct(config, _LIVE_INTERVAL)
        + surplus_kwh * config.charge_efficiency / config.battery_capacity_kwh * 100.0
    )
    assert max_feasible == pytest.approx(40.0 + 2 * per_slot)


# ---------------------------------------------------------------------------
# Defect 2 — the terminal penalty must be monotone through the floor.
# ---------------------------------------------------------------------------


def _terminal_penalties(slots, initial_soc_pct, **overrides):
    """``(soc_grid, penalty_by_bin, config)`` from a real solve's terminal costs."""
    config = _config(**overrides)
    planner = DPPlanner(config)
    inputs = OptimizerInputs(
        cycle_id="penalty-903",
        initial_soc_pct=initial_soc_pct,
        slots=slots,
        config=config,
        all_solcast=[],
    )
    # Run a full solve first so the solver-derived floor is on the config.
    planner.plan(inputs)
    dw_entry_idx = next(
        i for i, slot in enumerate(slots) if slot.is_demand_window_entry
    )
    soc_grid = [
        config.min_soc_pct
        + (config.max_soc_pct - config.min_soc_pct) * i / (config.soc_bins - 1)
        for i in range(config.soc_bins)
    ]
    _dp, penalty_by_bin = planner._initialize_dp_tables(
        len(slots),
        soc_grid,
        config,
        dw_entry_idx,
        False,
        inputs,
        config.hard_target_floor,
    )
    return soc_grid, penalty_by_bin, config


def _degraded_floor_slots() -> list[SlotContext]:
    """A runway too short to reach target, so the floor degrades below it.

    Three 5-minute boost slots store ~8.5 points; from 40% that is nowhere near 95%, so
    ``hard_target_floor`` lands well under target — the regime where the old gap-to-floor
    penalty inverted against gap-to-target.
    """
    return _live_slots(n_slots=12, dw_entry_idx=3)


def test_floor_degrades_below_target_in_this_fixture():
    """Guard the fixture itself: the boundary only exists when floor < target."""
    _result, config = _plan(_degraded_floor_slots(), 40.0)
    assert config.hard_target_floor is not None
    assert config.hard_target_floor < config.demand_window_target_soc_pct


def test_terminal_penalty_is_monotone_non_increasing_in_soc():
    """No SOC may be penalized MORE than a lower SOC — the attractor's root cause."""
    soc_grid, penalties, config = _terminal_penalties(_degraded_floor_slots(), 40.0)
    assert penalties, "strict mode must populate the DW-entry penalty"
    assert config.hard_target_floor is not None

    for bin_idx in range(1, len(soc_grid)):
        lower, higher = penalties[bin_idx - 1], penalties[bin_idx]
        assert higher <= lower + 1e-9, (
            f"penalty rose with SOC: {soc_grid[bin_idx - 1]:.2f}% -> ${lower:.2f} but "
            f"{soc_grid[bin_idx]:.2f}% -> ${higher:.2f}"
        )


def test_terminal_penalty_is_continuous_across_the_floor():
    """The breach term must vanish at the floor, not step by (target - floor).

    Measured pre-fix across the boundary: $3.24 just below, $615.60 just above.
    """
    soc_grid, penalties, config = _terminal_penalties(_degraded_floor_slots(), 40.0)
    floor = config.hard_target_floor
    assert floor is not None

    below = max(i for i, soc in enumerate(soc_grid) if soc < floor)
    above = below + 1
    step = penalties[above] - penalties[below]
    bin_width = soc_grid[above] - soc_grid[below]
    # Slope is at most 2x the per-point penalty (gap-to-target + breach), so the step
    # across one bin can never approach the (target - floor) cliff the old code had.
    max_step = 2.0 * bin_width * (config.battery_capacity_kwh * 0.30 * 2) * 10
    assert abs(step) <= max_step + 1e-9, (
        f"discontinuity at the floor: ${penalties[below]:.2f} -> ${penalties[above]:.2f}"
    )


def test_below_floor_is_strictly_worse_than_the_floor():
    """The floor still prunes: breaching it must cost more than clearing it."""
    soc_grid, penalties, config = _terminal_penalties(_degraded_floor_slots(), 40.0)
    floor = config.hard_target_floor
    assert floor is not None

    below = max(i for i, soc in enumerate(soc_grid) if soc < floor)
    above = below + 1
    assert penalties[below] > penalties[above]


# ---------------------------------------------------------------------------
# End-to-end: the coupled fix on the live 2026-07-29 regime.
# ---------------------------------------------------------------------------


def test_live_20260729_plan_charges_the_cheap_runway_to_target():
    """The planner must want the charge the hardware was already doing.

    Live, the plan said ``hold`` at a 0.06 buy price with 12 eligible pre-DW slots while
    the #901 execution backstop drove the battery at -5.0 kW. Both defects had to go: the
    drift fix restores a 95 floor, and the monotone penalty stops the DP parking under it.
    """
    result, config = _plan(_live_slots(), _LIVE_INITIAL_SOC)

    assert result.success
    assert config.hard_target_floor == pytest.approx(95.0)
    assert result.dw_entry_soc_pct is not None
    assert result.dw_entry_soc_pct >= 93.0, (
        f"DW entry {result.dw_entry_soc_pct}% — planner still parking under the floor"
    )
    pre_dw_charges = [
        d
        for d in result.decisions
        if d.action in _CHARGE and d.slot_index < _LIVE_DW_ENTRY_IDX
    ]
    assert pre_dw_charges, (
        "every pre-DW slot was cheap and eligible; plan charged in none"
    )


def test_dp_does_not_park_under_a_degraded_floor():
    """The attractor itself, end to end: DW entry must CLEAR the floor, not sit under it.

    With the old gap-to-floor penalty the DP settled one bin BELOW the floor (44.55
    against a 45.15 floor) because every state above it was priced against the
    DP-unreachable target and therefore cost more. Stated against the solver's own floor
    rather than a pinned SOC, so it survives any legitimate move in the floor's value.
    """
    result, config = _plan(_degraded_floor_slots(), 40.0)
    assert result.success
    assert config.hard_target_floor is not None
    assert config.hard_target_floor < config.demand_window_target_soc_pct, (
        "fixture guard: this only tests anything while the floor is degraded"
    )
    assert result.dw_entry_soc_pct is not None
    assert result.dw_entry_soc_pct >= config.hard_target_floor - 1e-9, (
        f"DP parked at {result.dw_entry_soc_pct}% under its own floor "
        f"{config.hard_target_floor}%"
    )


def test_max_feasible_is_monotone_in_initial_soc():
    """More SOC in hand can never mean less SOC reachable.

    A ``soc < charge_ceiling`` guard on the charge credit made a slot that arrived AT the
    ceiling fall back through the drift branch, so 69.77 reported a LOWER max feasible
    (93.88) than 67.15 did (95.0) — and the floor with it.
    """
    config = _config()
    slots = _live_slots()
    previous = None
    for soc in (60.0, 65.0, 67.15, 69.77, 75.0, 90.0, 95.0):
        value = compute_max_feasible_terminal_soc(
            slots, config, _LIVE_DW_ENTRY_IDX, soc
        )
        assert value is not None
        if previous is not None:
            assert value >= previous - 1e-9, (
                f"max feasible fell from {previous} to {value} as initial SOC rose"
            )
        previous = value

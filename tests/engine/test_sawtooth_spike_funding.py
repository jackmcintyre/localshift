"""Sawtooth regression for spike-event pre-charge funding (#908 follow-up).

#908 gave the DP a charge action in slots where a forecast price spike would otherwise
be met at the SOC floor. Two things about how it did that reopened the #800 sawtooth.

**Qualification was not actually scoped to spikes.** ``find_funding_slots`` admitted any
slot sitting a cycle bar below enough future load — which on an ordinary day is just the
overnight trough sitting below the morning peak. Measured over 400 randomised
ordinary-day horizons carrying no spike at all, it fired on 219 of them and the guard
accepted a *changed* plan on 109, against a commit claiming "no spike -> byte-identical
plan". That claim was verified on a single fixture at ``min_cycle_saving=0.25``, the top
of the operator's range; qualification widens as that knob falls.

**And the exemption disarmed the anti-cycling gate.** Every funding slot was exempted
from the min-cycle-saving gate — the codebase's only HARD anti-cycling screen — on the
grounds that qualification already uses ``min_cycle_saving`` as its bar. The two are not
the same comparison: qualification compares slot PRICES, the gate compares the DP's REAL
COSTS (round-trip losses, switching penalty, free solar, and whether the energy survives
to the dear slot instead of bleeding into overnight load first).

The two together put the feature on a knife edge on ordinary days, and this planner
re-solves every few minutes against a revised forecast. Ordinary jitter then flipped the
decision from cycle to cycle, so the committed action — the one the state machine
executes — alternated charge / hold / charge / hold. That is the sawtooth as the battery
experiences it, and no single-plan fixture can see it, which is why these tests re-plan.
"""

from __future__ import annotations

import random

import pytest

from custom_components.localshift.engine.core import _is_urgency_precharge
from custom_components.localshift.engine.optimizer_dp import (
    DPPlanner,
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)
from custom_components.localshift.engine.spike_event import (
    find_funding_slots,
    spike_price_floor,
)

CHARGE_ACTIONS = {PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST}

INTERVAL = 30

# An ordinary winter day, 15:00 -> 15:00, taken verbatim from the randomised scenario
# that reproduced the live flapping (overnight trough ~$0.18, evening demand window
# ~$0.43, morning peak ~$0.55 OUTSIDE the demand window, solar building through midday).
# Nothing here is a spike: the morning peak is 2.2x the day's median, which is simply
# what a winter morning looks like. Under #908 this horizon flapped the committed action
# five times across twelve re-plans.
#            buy,    sell,   solar_kwh, load_kwh, is_demand_window
_ORDINARY_DAY = [
    (0.2427, 0.1427, 0.3845, 0.431, False),  # 15:00
    (0.2516, 0.1516, 0.1960, 0.431, False),
    (0.4512, 0.3512, 0.0000, 0.431, True),  # 16:00 demand window opens
    (0.4331, 0.3331, 0.0000, 0.431, True),
    (0.4142, 0.3142, 0.0000, 0.6895, True),
    (0.4272, 0.3272, 0.0000, 0.6895, True),
    (0.4248, 0.3248, 0.0000, 0.6895, True),
    (0.4311, 0.3311, 0.0000, 0.6895, True),
    (0.4379, 0.3379, 0.0000, 0.6895, True),
    (0.4145, 0.3145, 0.0000, 0.6895, True),  # 19:30 demand window closes
    (0.2561, 0.1561, 0.0000, 0.6895, False),
    (0.2652, 0.1652, 0.0000, 0.6895, False),
    (0.2663, 0.1663, 0.0000, 0.6895, False),
    (0.2543, 0.1543, 0.0000, 0.6895, False),
    (0.1732, 0.0732, 0.0000, 0.431, False),  # 22:00 overnight trough begins
    (0.1873, 0.0873, 0.0000, 0.431, False),
    (0.1804, 0.0804, 0.0000, 0.431, False),
    (0.1724, 0.0724, 0.0000, 0.431, False),
    (0.1884, 0.0884, 0.0000, 0.431, False),
    (0.1761, 0.0761, 0.0000, 0.431, False),
    (0.1924, 0.0924, 0.0000, 0.431, False),
    (0.1918, 0.0918, 0.0000, 0.431, False),
    (0.1807, 0.0807, 0.0000, 0.431, False),
    (0.1932, 0.0932, 0.0000, 0.431, False),
    (0.1730, 0.0730, 0.0000, 0.431, False),
    (0.1770, 0.0770, 0.0000, 0.431, False),
    (0.1808, 0.0808, 0.0000, 0.431, False),
    (0.1763, 0.0763, 0.0000, 0.431, False),  # 04:30 trough ends
    (0.2499, 0.1499, 0.0000, 0.431, False),
    (0.2440, 0.1440, 0.0000, 0.431, False),
    (0.2546, 0.1546, 0.0000, 0.6895, False),
    (0.5303, 0.4303, 0.0000, 0.6895, False),  # 06:30 morning peak begins
    (0.5247, 0.4247, 0.0000, 0.6895, False),
    (0.5407, 0.4407, 0.0000, 0.6895, False),  # 07:30
    (0.5503, 0.4503, 0.0000, 0.6895, False),
    (0.5161, 0.4161, 0.1960, 0.6895, False),
    (0.5141, 0.4141, 0.3845, 0.431, False),  # 09:00 morning peak ends
    (0.2649, 0.1649, 0.5581, 0.431, False),
    (0.2533, 0.1533, 0.7104, 0.431, False),
    (0.2623, 0.1623, 0.8353, 0.431, False),
    (0.2509, 0.1509, 0.9282, 0.431, False),
    (0.2661, 0.1661, 0.9853, 0.431, False),
    (0.2505, 0.1505, 1.0046, 0.431, False),
    (0.2431, 0.1431, 0.9853, 0.431, False),
    (0.2512, 0.1512, 0.9282, 0.431, False),
    (0.2453, 0.1453, 0.8353, 0.431, False),
    (0.2635, 0.1635, 0.7104, 0.431, False),
    (0.2564, 0.1564, 0.5581, 0.431, False),  # 14:30
]

_MORNING_PEAK_IDX = 33  # 07:30, the dearest ordinary slot ($0.5407)
_INITIAL_SOC = 43.714

# This horizon's live settings, from the same scenario.
_CHEAP_PRICE = 0.1834
_CYCLE_BAR = 0.10


def _slots(
    spike: dict[int, float] | None = None,
    jitter: random.Random | None = None,
) -> list[SlotContext]:
    """The ordinary day, optionally with a spike injected and/or re-forecast.

    ``jitter`` models what a live re-plan actually sees: Amber revises its price
    forecast and Solcast its solar forecast every cycle, by fractions of a percent.
    """
    overrides = spike or {}
    out = []
    for idx, (buy, sell, solar, load, is_dw) in enumerate(_ORDINARY_DAY):
        buy = overrides.get(idx, buy)
        if jitter is not None:
            buy *= jitter.uniform(0.985, 1.015)
            solar *= jitter.uniform(0.97, 1.03)
        hour = (15.0 + idx * 0.5) % 24.0
        out.append(
            SlotContext(
                slot_index=idx,
                timestamp_iso=f"2026-08-05T{int(hour):02d}:{int((hour % 1) * 60):02d}:00",
                slot_interval_minutes=INTERVAL,
                buy_price=round(buy, 4),
                sell_price=sell,
                solar_kwh=round(solar, 4),
                consumption_kwh=load,
                is_demand_window_entry=(idx == 2),
                is_demand_window_slot=is_dw,
            )
        )
    return out


def _config(**overrides) -> OptimizerConfig:
    base = dict(
        battery_capacity_kwh=13.5,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=100.0,
        optimization_mode="self_consumption",
        switching_penalty=0.02,
        target_shortfall_penalty_per_pct=0.03,
        min_cycle_saving=_CYCLE_BAR,
        max_precharge_price=0.20,
        effective_cheap_price=_CHEAP_PRICE,
        base_cheap_price=_CHEAP_PRICE,
        soc_bins=100,
    )
    base.update(overrides)
    return OptimizerConfig(**base)


def _plan(slots=None, initial_soc=_INITIAL_SOC, **config_overrides):
    return DPPlanner().plan(
        OptimizerInputs(
            cycle_id="sawtooth-spike",
            initial_soc_pct=initial_soc,
            slots=slots if slots is not None else _slots(),
            config=_config(**config_overrides),
        )
    )


# --- qualification must be scoped to genuine spikes ------------------------------------


def test_ordinary_morning_peak_is_not_a_spike():
    """The core scoping rule, on the horizon that reproduced the live flapping.

    A $0.55 morning peak against a $0.25 day is 2.2x the median — an ordinary winter
    morning, not an event. #908 qualified it anyway, because a price SPREAD alone cannot
    tell the two apart: the overnight trough sits a full cycle bar below the morning peak
    on any ordinary day.
    """
    assert find_funding_slots(_slots(), _config()) == frozenset()


def test_a_genuine_outlier_still_qualifies():
    """Scoping must not blind the feature to the thing it exists for.

    Same horizon, but 07:30 now carries a real spike rather than a peak.
    """
    assert find_funding_slots(_slots(spike={_MORNING_PEAK_IDX: 3.20}), _config())


def test_spike_floor_tracks_the_days_own_price_level():
    """What counts as a spike is relative to the horizon, not an absolute dollar figure.

    Demand-window slots are excluded from the median so an expensive DW cannot mask a
    genuine spike outside it.
    """
    floor = spike_price_floor(_slots())

    assert floor > max(s.buy_price for s in _slots() if not s.is_demand_window_slot), (
        "an ordinary day must have nothing at or above its own spike floor"
    )
    assert spike_price_floor(_slots(spike={_MORNING_PEAK_IDX: 3.20})) == pytest.approx(
        floor
    ), "injecting one spike must not move the floor materially"


def test_spike_floor_fails_closed_on_a_non_positive_median():
    """A negative-wholesale horizon has no ordinary-price level to measure against."""
    slots = _slots()
    for slot in slots:
        slot.buy_price = -0.05

    assert spike_price_floor(slots) == float("inf")
    assert find_funding_slots(slots, _config()) == frozenset()


def test_spike_floor_fails_closed_when_every_slot_is_a_demand_window_slot():
    """No non-DW slot means no ordinary-price level, so nothing may qualify."""
    slots = _slots()
    for slot in slots:
        slot.is_demand_window_slot = True

    assert spike_price_floor(slots) == float("inf")
    assert find_funding_slots(slots, _config()) == frozenset()


def test_inert_when_round_trip_efficiency_is_degenerate():
    """No spread can ever be cleared if a stored kWh returns nothing."""
    assert (
        find_funding_slots(
            _slots(spike={_MORNING_PEAK_IDX: 3.20}), _config(charge_efficiency=0.0)
        )
        == frozenset()
    )


def test_inert_when_a_slot_can_store_nothing():
    """A slot that cannot charge cannot fund anything, whatever the price gap."""
    assert (
        find_funding_slots(
            _slots(spike={_MORNING_PEAK_IDX: 3.20}), _config(charge_rate_kw=0.0)
        )
        == frozenset()
    )


# --- the min-cycle-saving gate must stay armed ------------------------------------------


def test_funding_slots_are_not_exempt_from_the_cycle_gate():
    """A spike funding slot must still face the anti-cycling gate.

    #908 returned True here for every funding slot. Qualification proves a PRICE spread,
    not that the cycle pays once round-trip losses, the switching penalty and the drain
    between charge and use are netted off — so the gate is not redundant, and exempting
    these slots disarmed it wherever qualification fired.
    """
    config = _config(spike_funding_slots=frozenset({30}))

    # Slot 30 has no demand-window justification, so nothing else can exempt it.
    assert not _is_urgency_precharge(
        30, soc=50.0, buy_price=0.19, terminal_penalty_idx=4, config=config
    )


def test_demand_window_precharge_exemption_is_untouched():
    """Removing the spike exemption must not disturb the DW pre-charge exemption.

    That one is load-bearing for a different failure (#860 procrastination into a missed
    demand-window target) and is scoped to pre-DW slots below the target.
    """
    config = _config(pre_dw_funding_water_level=0.20)

    assert _is_urgency_precharge(
        1, soc=50.0, buy_price=0.19, terminal_penalty_idx=2, config=config
    )


# --- end-to-end: the ordinary day must be left alone ------------------------------------


@pytest.mark.parametrize("cycle_bar", (0.05, 0.10, 0.15, 0.20, 0.25, 0.30))
def test_ordinary_day_plan_is_untouched_at_every_cycle_bar(cycle_bar):
    """No spike in the horizon => the feature changes nothing.

    Parametrised because qualification widens as ``min_cycle_saving`` falls, and the
    operator can set it anywhere from $0.00 to $1.00. #908's inertness fixture pinned
    only $0.25.
    """
    enabled = _plan(min_cycle_saving=cycle_bar)
    baseline = _plan(min_cycle_saving=cycle_bar, spike_precharge_enabled=False)

    assert [d.action for d in enabled.decisions] == [
        d.action for d in baseline.decisions
    ], f"ordinary-day plan changed at min_cycle_saving=${cycle_bar}"
    assert enabled.projected_net_cost == pytest.approx(baseline.projected_net_cost)
    assert enabled.spike_funding_slot_count == 0


@pytest.mark.parametrize("cycle_bar", (0.05, 0.10, 0.15, 0.20, 0.25, 0.30))
def test_ordinary_day_never_grid_charges_above_the_cheap_base(cycle_bar):
    """The #800 signature itself: a charge above the genuinely-cheap base."""
    enabled = _plan(min_cycle_saving=cycle_bar)

    sawtooth_charges = [
        d.slot_index
        for d in enabled.decisions
        if d.action in CHARGE_ACTIONS and d.buy_price > _CHEAP_PRICE
    ]
    assert sawtooth_charges == [], (
        f"grid charging above the cheap base at slots {sawtooth_charges} "
        f"(min_cycle_saving=${cycle_bar})"
    )


# --- cross-cycle stability: the sawtooth as the battery experiences it -------------------


@pytest.mark.parametrize("cycle_bar", (0.05, 0.10, 0.15))
def test_committed_action_does_not_flap_across_replans(cycle_bar):
    """The live sawtooth is plan CHURN, which no single-plan fixture can see.

    LocalShift re-plans every few minutes and the state machine executes decisions[0].
    Re-solving this horizon against ordinary forecast revisions must not flip the
    committed action between charging and holding. Under #908 this exact scenario
    produced charge / hold / hold / charge / hold / hold / charge across twelve cycles.
    """
    jitter = random.Random(20260805)
    committed = [
        _plan(slots=_slots(jitter=jitter), min_cycle_saving=cycle_bar)
        .decisions[0]
        .action
        for _ in range(12)
    ]

    charging = [action in CHARGE_ACTIONS for action in committed]
    flaps = sum(1 for a, b in zip(charging, charging[1:], strict=False) if a != b)
    assert flaps == 0, (
        f"committed action flapped {flaps} time(s) across re-plans "
        f"(min_cycle_saving=${cycle_bar}): {[a.value for a in committed]}"
    )


def test_spike_day_decision_is_stable_across_replans():
    """Stability must come from being far from the boundary, not from never firing.

    A genuine spike must be picked up on every cycle, not on some of them — otherwise
    the fix has merely moved the flapping onto the days that matter.
    """
    jitter = random.Random(20260805)
    results = [
        _plan(slots=_slots(spike={_MORNING_PEAK_IDX: 3.20}, jitter=jitter))
        for _ in range(8)
    ]

    assert all(r.spike_funding_slot_count > 0 for r in results), (
        "a real spike must qualify on every cycle: "
        f"{[r.spike_funding_slot_count for r in results]}"
    )

"""Price-block target: import ban from Amber, deadline and target from the price (#1104).

The demand window used to ban import AND set the deadline. With
``switch.localshift_demand_window_from_amber`` on, the ban follows Amber's flag
(``is_charge_window_slot``) and the deadline follows the flagged window when one is
ahead, else the first expensive run of slots in the forecast. See
docs/PRICE_BLOCK_TARGET.md.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

import pytest

from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.price_block import (
    apply_price_block,
    block_target_soc_pct,
    detect_price_block,
)
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)

_CHARGE = (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
# Tue 3 Nov 2026 is a working weekday; Sat 7 Nov a weekend; Tue 3 Nov holiday case
# uses the same prices (the planner sees only flags and prices, not the calendar).
_START = datetime(2026, 11, 3, 13, 0)


def _price(h: float) -> float:
    """Flat 30c, cheapest 15:30-16:30 at 29c, dear evening 17:30-21:30 at 70c."""
    if 15.5 <= h < 16.5:
        return 0.29
    if 17.5 <= h < 21.5:
        return 0.70
    return 0.30


def _price_13oct(h: float) -> float:
    """The 13 Oct 2026 shape from the study: 41c shoulders, so everything after the
    29c trough is dear and the block opens at 16:30."""
    if 15.5 <= h < 16.5:
        return 0.29
    if 17.5 <= h < 21.5:
        return 0.70
    return 0.41


def _slots(
    price: Callable[[float], float] = _price,
    n: int = 20,
    flagged: tuple[float, float] | None = None,
    consumption: float = 0.5,
) -> list[SlotContext]:
    """Half-hour slots from 13:00. ``flagged`` is an Amber charge window (hours)."""
    out = []
    previous = False
    for i in range(n):
        t = _START + timedelta(minutes=30 * i)
        h = t.hour + t.minute / 60.0
        in_window = flagged is not None and flagged[0] <= h < flagged[1]
        out.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=30,
                buy_price=price(h),
                sell_price=0.05,
                solar_kwh=0.0,
                consumption_kwh=consumption,
                is_demand_window_entry=in_window and not previous,
                is_demand_window_slot=in_window,
            )
        )
        previous = in_window
    return out


def _config(switch: bool, **kw) -> OptimizerConfig:
    return OptimizerConfig(
        min_soc_pct=10.0,
        demand_window_target_soc_pct=95.0,
        optimization_mode="self_consumption",
        effective_cheap_price=0.20,
        base_cheap_price=0.20,
        min_cycle_saving=0.25,
        soc_bins=100,
        demand_window_from_amber=switch,
        max_precharge_price=1.0,
        **kw,
    )


def _plan(slots: list[SlotContext], switch: bool, soc0: float = 10.0):
    config = _config(switch)
    result = DPPlanner().plan(
        OptimizerInputs(
            cycle_id="price-block", initial_soc_pct=soc0, slots=slots, config=config
        )
    )
    return result, config


def _charge_hours(result) -> list[float]:
    return [
        datetime.fromisoformat(d.timestamp_iso).hour
        + datetime.fromisoformat(d.timestamp_iso).minute / 60.0
        for d in result.decisions
        if d.action in _CHARGE
    ]


# --- the four days the issue names -------------------------------------------------


def test_off_season_day_pre_charges_ahead_of_a_dear_evening():
    """No flag anywhere: the four study gates no longer starve the charge."""
    slots = _slots()
    result, config = _plan(slots, switch=True)
    hours = _charge_hours(result)
    assert hours, "expected a grid charge ahead of the 70c evening"
    assert all(h < 17.5 for h in hours), hours
    assert not any(s.is_charge_window_slot for s in slots)
    assert config.terminal_penalty_idx == 9  # 17:30


@pytest.mark.parametrize("day", ["weekend", "holiday"])
def test_unflagged_weekend_or_holiday_pre_charges_the_same(day):
    """Amber flags neither; the planner sees only prices and flags. The 13 Oct
    shape, where the block opens right after the trough, plans the same way."""
    result, config = _plan(_slots(_price_13oct), switch=True)
    assert config.terminal_penalty_idx == 7, day
    hours = _charge_hours(result)
    assert hours
    assert all(h < 16.5 for h in hours), hours


def test_weekday_inside_the_flagged_window_keeps_the_flagged_deadline():
    """A flagged window ahead is the deadline and the target is battery_target."""
    slots = _slots(flagged=(17.5, 21.0))
    result, config = _plan(slots, switch=True)
    assert config.demand_window_target_soc_pct == 95.0
    assert config.terminal_penalty_idx == 9
    assert [s.is_demand_window_slot for s in slots] == [
        s.is_charge_window_slot for s in slots
    ]
    assert _charge_hours(result)
    assert not any(h >= 17.5 for h in _charge_hours(result))


def test_weekday_mid_window_gets_no_price_block():
    """Slot 0 already inside the flagged window: the window is no deadline, and the
    next-window tail, not a price block, prepares for the one after it."""
    slots = _slots(flagged=(13.0, 15.0))
    config = _config(True)
    assert apply_price_block(slots, config) is None
    assert config.demand_window_target_soc_pct == 95.0
    assert [s.is_demand_window_slot for s in slots] == [
        s.is_charge_window_slot for s in slots
    ]
    assert not any(s.is_demand_window_entry for s in slots[1:])


# --- switch off is the old behaviour -----------------------------------------------


def test_switch_off_changes_nothing():
    slots = _slots()
    config = _config(False)
    assert apply_price_block(slots, config) is None
    assert config.demand_window_target_soc_pct == 95.0
    assert not any(s.is_demand_window_slot for s in slots)
    result, _ = _plan(_slots(_price_13oct), switch=False)
    assert _charge_hours(result) == []


# --- the ban is the Amber flag, not the price block --------------------------------


def test_price_block_does_not_ban_import_but_a_charge_window_does():
    block_slots = _slots()
    apply_price_block(block_slots, _config(True))
    assert block_slots[10].is_demand_window_slot
    assert not block_slots[10].is_charge_window_slot

    window_slots = _slots(flagged=(17.5, 21.0))
    assert window_slots[10].is_charge_window_slot


def test_slot_without_the_split_bans_as_before():
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-11-03T17:30:00",
        slot_interval_minutes=30,
        buy_price=0.5,
        sell_price=0.1,
        solar_kwh=0.0,
        consumption_kwh=0.5,
        is_demand_window_slot=True,
    )
    assert slot.is_charge_window_slot is True


# --- detector ----------------------------------------------------------------------


def test_detect_finds_the_evening_run():
    assert detect_price_block(_slots()) == (9, 16)


def test_detect_needs_two_hours():
    slots = _slots(lambda h: 0.70 if 17.5 <= h < 18.5 else 0.30)
    assert detect_price_block(slots) is None


def test_detect_ignores_a_spread_below_the_knob():
    assert detect_price_block(_slots(lambda h: 0.30 if h < 17.5 else 0.36)) is None


def test_detect_needs_net_load():
    slots = _slots()
    for s in slots:
        s.solar_kwh = 1.0
    assert detect_price_block(slots) is None


def test_detect_tolerates_one_slot_gap_but_not_two():
    def one(h: float) -> float:
        return 0.30 if (h < 17.5 or h >= 21.5 or h == 19.0) else 0.70

    def two(h: float) -> float:
        return 0.30 if (h < 17.5 or h >= 21.5 or h in (19.0, 19.5)) else 0.70

    assert detect_price_block(_slots(one)) == (9, 16)
    # Two slots out ends the run: 1.5 h before the gap and 1.5 h after, neither enough.
    assert detect_price_block(_slots(two)) is None


def test_detect_drops_a_short_first_run_and_takes_the_next():
    def price(h: float) -> float:
        return 0.70 if (14.0 <= h < 15.0 or 18.0 <= h < 21.0) else 0.30

    assert detect_price_block(_slots(price)) == (10, 15)


def test_detect_run_reaching_the_end_of_the_horizon():
    assert detect_price_block(_slots(lambda h: 0.30 if h < 18 else 0.70, n=16)) == (
        10,
        15,
    )


def test_detect_leaves_charge_window_slots_out():
    slots = _slots(flagged=(17.5, 21.0))
    assert detect_price_block(slots) is None


def test_detect_empty_and_flat():
    assert detect_price_block([]) is None
    assert detect_price_block(_slots(lambda h: 0.30)) is None


# --- target ------------------------------------------------------------------------


def test_target_is_sized_to_the_block_load():
    slots = _slots()
    config = _config(True)
    apply_price_block(slots, config)
    # 8 slots x 0.5 kWh = 4 kWh out of the battery, plus the headroom.
    expected = 10.0 + 4.0 / config.discharge_efficiency / 13.5 * 100.0 + 5.0
    assert config.demand_window_target_soc_pct == pytest.approx(expected)
    assert 10.0 < config.demand_window_target_soc_pct < 95.0


def test_target_is_clamped_to_the_configured_target():
    slots = _slots(consumption=5.0)
    config = _config(True)
    apply_price_block(slots, config)
    assert config.demand_window_target_soc_pct == 95.0


def test_target_discounts_solar_by_forecast_accuracy():
    block = _slots()[9:17]
    for s in block:
        s.solar_kwh = 0.4
    config = _config(True)
    full = block_target_soc_pct(block, config, 1.0, 95.0)
    half = block_target_soc_pct(block, config, 0.5, 95.0)
    assert half > full


def test_target_never_below_the_floor():
    block = _slots(consumption=0.0)[9:17]
    assert block_target_soc_pct(block, _config(True), 1.0, 95.0) == 15.0


def test_replanning_the_same_inputs_is_stable():
    """booking_base replans the very slots and config the first plan used."""
    slots = _slots()
    config = _config(True)
    apply_price_block(slots, config)
    first = (
        config.demand_window_target_soc_pct,
        [s.is_demand_window_slot for s in slots],
    )
    apply_price_block(slots, config)
    assert (
        config.demand_window_target_soc_pct,
        [s.is_demand_window_slot for s in slots],
    ) == first
    assert config.price_block_target_ceiling_pct == 95.0


# --- the four gates the 15 Oct study found closed when no flagged slot is in view ----
# (scripts/study_1104_offseason.py on branch study/1104-offseason-gates). Each is
# opened by the same thing: a deadline, so ``terminal_penalty_idx`` is set and the
# evening's slots are flagged.


def test_gate_1_cheap_price_gate_is_lifted_by_the_water_level():
    """The 29c trough is above the 20c cheap price; the block's water level admits it."""
    slots = _slots()
    result, config = _plan(slots, switch=True)
    assert config.effective_cheap_price == 0.20
    assert config.pre_dw_charge_thresholds is not None
    assert any(
        0.29 <= slots[i].buy_price <= 0.30
        for i, d in enumerate(result.decisions)
        if d.action in _CHARGE
    )


def test_gate_2_cycle_hurdle_does_not_defer_the_pre_block_charge():
    """Hurdle stays at the operator's $0.25 and the charge still lands before the block."""
    result, config = _plan(_slots(), switch=True)
    assert config.min_cycle_saving == 0.25
    assert _charge_hours(result)


def test_gate_3_serving_the_block_is_not_futile_cycling():
    from custom_components.localshift.engine.penalties import (
        get_futile_cycling_penalty_factor,
    )

    kwargs = dict(
        action=PlannerAction.CHARGE_GRID_NORMAL,
        slot_idx=5,
        soc_after_charge_pct=40.0,
        charge_kwh=2.0,
        config=OptimizerConfig(),
    )
    bare = _slots()
    blocked = _slots()
    apply_price_block(blocked, _config(True))
    legacy = get_futile_cycling_penalty_factor(
        slots=bare, terminal_penalty_idx=None, **kwargs
    )
    with_block = get_futile_cycling_penalty_factor(
        slots=blocked, terminal_penalty_idx=9, **kwargs
    )
    assert legacy > with_block


def test_gate_4_tomorrows_solar_does_not_cover_tonights_deficit():
    """Without a deadline the solar-sufficiency gate simulates to the end of the
    horizon, so a sunny tomorrow strips every grid charge today. The block ends it
    at the block's entry."""
    from custom_components.localshift.engine.constraints import (
        check_global_solar_sufficiency,
    )

    slots = _slots(n=30)
    for s in slots[18:]:
        s.solar_kwh = 3.0
    config = _config(True)
    assert check_global_solar_sufficiency(10.0, 0, slots, config, None)
    block = apply_price_block(slots, config)
    assert block is not None
    assert not check_global_solar_sufficiency(10.0, 0, slots, config, block[0])

    sunny = _slots(n=30)
    for s in sunny[18:]:
        s.solar_kwh = 3.0
    result, _ = _plan(sunny, switch=True)
    assert _charge_hours(result)

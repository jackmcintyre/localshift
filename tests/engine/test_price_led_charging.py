"""Price-led charging when no demand window is in view (#1104 follow-up, study).

With ``switch.localshift_demand_window_from_amber`` on and Amber flagging no
window, nothing funds a charge for a dear evening: the cheap-price gate, the
$0.25 cycle hurdle, the drain-to-load futile-cycling rule and the deadline-only
solar-sufficiency gate all stand in the way. ``price_led_charging`` lets the
DP's own economics decide in that case only. Evidence:
scripts/study_1104_offseason.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.penalties import (
    get_futile_cycling_penalty_factor,
)
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)

_CHARGE = (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST)
_START = datetime(2026, 10, 13, 13, 0)


def _price_13oct(h: float) -> float:
    """The 13 Oct shape: dear flat afternoon, cheapest 15:30-16:30, dear evening."""
    if 15.5 <= h < 16.5:
        return 0.29
    if 17.5 <= h < 21.5:
        return 0.70
    return 0.41


def _slots(
    price, n: int = 20, dw: tuple[float, float] | None = None
) -> list[SlotContext]:
    out = []
    for i in range(n):
        t = _START + timedelta(minutes=30 * i)
        h = t.hour + t.minute / 60.0
        in_dw = dw is not None and dw[0] <= h < dw[1]
        out.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=t.isoformat(),
                slot_interval_minutes=30,
                buy_price=price(h),
                sell_price=0.05,
                solar_kwh=0.0,
                consumption_kwh=0.5,
                is_demand_window_entry=dw is not None and abs(h - dw[0]) < 1e-9,
                is_demand_window_slot=in_dw,
            )
        )
    return out


def _plan(slots, *, switch: bool, soc0: float = 10.0):
    config = OptimizerConfig(
        min_soc_pct=10.0,
        demand_window_target_soc_pct=95.0,
        optimization_mode="self_consumption",
        effective_cheap_price=0.20,
        base_cheap_price=0.20,
        min_cycle_saving=0.25,
        soc_bins=100,
        demand_window_from_amber=switch,
    )
    result = DPPlanner().plan(
        OptimizerInputs(
            cycle_id="price-led", initial_soc_pct=soc0, slots=slots, config=config
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


def test_switch_on_buys_the_cheap_hour_for_the_dear_evening():
    result, config = _plan(_slots(_price_13oct), switch=True)
    assert config.price_led_charging
    hours = _charge_hours(result)
    assert hours, "expected a grid charge ahead of the 70c evening"
    assert all(15.5 <= h < 16.5 for h in hours), hours


def test_switch_off_same_horizon_is_gated_as_before():
    result, config = _plan(_slots(_price_13oct), switch=False)
    assert not config.price_led_charging
    assert _charge_hours(result) == []


def test_window_in_view_is_inert():
    _, config = _plan(_slots(_price_13oct, dw=(17.5, 21.0)), switch=True)
    assert not config.price_led_charging


def test_flat_price_does_not_cycle():
    """#800 negative control: nothing dearer than the charge after losses."""
    result, _ = _plan(_slots(lambda h: 0.18, n=24), switch=True)
    assert _charge_hours(result) == []


def test_drain_into_dearer_load_is_not_futile_when_price_led():
    slots = _slots(_price_13oct)
    charge_idx = 6  # 16:00, 29c; every later slot is 41c or 70c, dearer than 29c/0.874
    kwargs = dict(
        action=PlannerAction.CHARGE_GRID_NORMAL,
        slot_idx=charge_idx,
        slots=slots,
        soc_after_charge_pct=40.0,
        charge_kwh=1.5,
        terminal_penalty_idx=None,
    )
    legacy = get_futile_cycling_penalty_factor(config=OptimizerConfig(), **kwargs)
    led = get_futile_cycling_penalty_factor(
        config=OptimizerConfig(price_led_charging=True), **kwargs
    )
    assert legacy > 0.5
    assert led == 0.0

"""Load-sized export floor.

Battery export is a bet on the load forecast: every kWh sold is one the house
cannot use later. While prices stay elevated, running out is expensive, so the
EXPORT action may not drain below what the house is forecast to need for the
rest of the elevated-price period, times a margin for the forecast being low.

This is a discharge floor for EXPORT only. HOLD still serves load beneath it,
which is the point of holding the energy back, and it never adds a reason to
grid-charge. Consult docs/PLANNING_MODEL.md before changing it.
"""

from __future__ import annotations

from ..const import (
    EXPORT_FLOOR_ELEVATED_PRICE_FACTOR,
    EXPORT_FLOOR_HOLD_PRICE_RATIO,
    EXPORT_FLOOR_NORMAL_RUN_MINUTES,
)
from .types import OptimizerConfig, SlotContext


def _elevated_price(slots: list[SlotContext]) -> float | None:
    """Buy price above which a slot counts as elevated, or None if undefined."""
    prices = sorted(s.buy_price for s in slots)
    if not prices:
        return None
    median = prices[len(prices) // 2]
    if median <= 0:
        return None
    return median * EXPORT_FLOOR_ELEVATED_PRICE_FACTOR


def compute_export_floor_pct_by_slot(
    slots: list[SlotContext], config: OptimizerConfig
) -> list[float]:
    """Return, per slot, the SOC the EXPORT action may not drain below.

    For slot ``i`` this is the discharge floor plus the battery energy needed to
    serve forecast net load in the later slots of the same elevated-price
    period, scaled by ``export_floor_load_margin``. The period runs until prices
    have stayed normal for ``EXPORT_FLOOR_NORMAL_RUN_MINUTES``.

    A later slot only counts when buying there costs at least
    ``EXPORT_FLOOR_HOLD_PRICE_RATIO`` of what slot ``i`` sells for: when the
    sell price dwarfs every later buy price, exporting now and buying back wins
    however wrong the load forecast is, so nothing is held back.
    """
    base = config.discharge_floor_pct
    floors = [base] * len(slots)
    margin = config.export_floor_load_margin
    elevated_price = _elevated_price(slots)
    if (
        margin <= 0
        or elevated_price is None
        or config.battery_capacity_kwh <= 0
        or config.discharge_efficiency <= 0
    ):
        return floors

    for i, slot in enumerate(slots):
        worth_holding_price = slot.sell_price * EXPORT_FLOOR_HOLD_PRICE_RATIO
        reserve_kwh = 0.0
        normal_run_minutes = 0
        for later in slots[i + 1 :]:
            if later.buy_price > elevated_price:
                normal_run_minutes = 0
                if later.buy_price >= worth_holding_price:
                    reserve_kwh += max(0.0, later.consumption_kwh - later.solar_kwh)
                continue
            normal_run_minutes += later.slot_interval_minutes
            if normal_run_minutes >= EXPORT_FLOOR_NORMAL_RUN_MINUTES:
                break

        reserve_pct = (
            reserve_kwh
            * margin
            / config.discharge_efficiency
            / config.battery_capacity_kwh
            * 100.0
        )
        floors[i] = min(config.max_soc_pct, base + reserve_pct)

    return floors


def apply_export_floor(slots: list[SlotContext], config: OptimizerConfig) -> None:
    """Write the export floor onto each slot and publish the first on ``config``."""
    floors = compute_export_floor_pct_by_slot(slots, config)
    for slot, floor in zip(slots, floors, strict=True):
        slot.export_floor_pct = floor
    config.export_floor_pct_now = floors[0] if floors else config.discharge_floor_pct

"""Penalty factor tests (Issue #1265: displaced solar counts as futile)."""

import pytest

from custom_components.localshift.engine.penalties import (
    get_futile_cycling_penalty_factor,
)
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)


def _slots(surplus_kwh: float) -> list[SlotContext]:
    return [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-03-11T{11 + i // 2:02d}:{(i % 2) * 30:02d}:00",
            slot_interval_minutes=30,
            buy_price=0.205,
            sell_price=0.12,
            solar_kwh=0.3 + (surplus_kwh if i else 0.0),
            consumption_kwh=0.3,
        )
        for i in range(8)
    ]


def _factor(surplus_kwh: float) -> float:
    return get_futile_cycling_penalty_factor(
        action=PlannerAction.CHARGE_GRID_NORMAL,
        slot_idx=0,
        slots=_slots(surplus_kwh),
        config=OptimizerConfig(battery_capacity_kwh=13.5),
        soc_after_charge_pct=50.0,
        charge_kwh=2.4,
        terminal_penalty_idx=8,
    )


def test_charge_that_only_displaces_solar_is_fully_penalised():
    assert _factor(2.6) == pytest.approx(1.0, abs=1e-6)


def test_charge_solar_could_not_replace_is_not_penalised():
    assert _factor(0.3) == pytest.approx(0.0, abs=1e-6)

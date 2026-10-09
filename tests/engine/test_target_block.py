"""Price-driven target block detection (docs/PRICE_BLOCK_TARGET.md, slice 1; #1105).

The planner's only funder for an evening pre-charge is the clock demand window,
which has no basis once the seasonal demand charge is off. ``find_target_block``
finds the expensive evening from the forecast instead. It is a pure function:
these tests cover the detector alone, with no planner wiring.

The load-bearing test is the real 2026-09-07 capture. The 16:00 slot there is
13.0c against a 7.1c reference, a 5.9c spread, which is under the 8c bar, so the
block enters at 16:30 and not at 16:00. That is the rule working as specified,
not a defect; the block's need is also well over 100% of the battery, which is
left for the caller to clamp.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.localshift.engine.slots import SlotBuilder
from custom_components.localshift.engine.target_block import (
    TargetBlock,
    find_target_block,
)
from custom_components.localshift.engine.types import OptimizerConfig, SlotContext

_REPO = Path(__file__).resolve().parents[2]
_HARNESS = _REPO / "scripts" / "replay_no_dw.py"
_CAPTURE = _REPO / "simulations" / "replay-nodw" / "2026-09-07.json"

CHEAP = 0.07
DEAR = 0.19


def _slot(
    index: int,
    buy_price: float,
    *,
    consumption_kwh: float = 1.0,
    solar_kwh: float = 0.0,
    minutes: int = 30,
) -> SlotContext:
    return SlotContext(
        slot_index=index,
        timestamp_iso=f"slot-{index}",
        slot_interval_minutes=minutes,
        buy_price=buy_price,
        sell_price=0.0,
        solar_kwh=solar_kwh,
        consumption_kwh=consumption_kwh,
    )


def _slots(prices: list[float], **kwargs: Any) -> list[SlotContext]:
    return [_slot(i, price, **kwargs) for i, price in enumerate(prices)]


def _config(**overrides: Any) -> OptimizerConfig:
    config = OptimizerConfig()
    config.block_min_spread = 0.08
    config.block_min_duration_hours = 2.0
    for name, value in overrides.items():
        setattr(config, name, value)
    return config


def _found(block: TargetBlock | None) -> TargetBlock:
    assert block is not None, "expected a block"
    return block


# ---------------------------------------------------------------------------
# The real 2026-09-07 capture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def capture_slots() -> list[SlotContext]:
    """The slot list the planner builds from the 2026-09-07 09:00 capture.

    Driven through the replay harness (``scripts/replay_no_dw.py``) under its
    ``no_dw`` arm, so the slots are exactly what the off-season planner sees.
    """
    spec = importlib.util.spec_from_file_location("replay_no_dw", _HARNESS)
    assert spec is not None and spec.loader is not None
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)

    built: list[list[SlotContext]] = []
    original = SlotBuilder.build_slots

    def spy(self: SlotBuilder, *args: Any, **kwargs: Any) -> Any:
        out = original(self, *args, **kwargs)
        built.append(out[0])
        return out

    scenario = json.loads(_CAPTURE.read_text())
    with patch.object(SlotBuilder, "build_slots", spy):
        harness.run_arm(scenario, harness.ARMS["no_dw"])

    assert built, "the harness never built a slot list"
    return built[-1]


def _local_time(slot: SlotContext) -> str:
    return datetime.fromisoformat(slot.timestamp_iso).strftime("%m-%d %H:%M")


def test_capture_block_enters_at_1630_and_runs_to_midnight(
    capture_slots: list[SlotContext],
) -> None:
    block = _found(find_target_block(capture_slots, _config()))

    assert _local_time(capture_slots[block.entry_idx]) == "09-07 16:30"
    assert _local_time(capture_slots[block.end_idx]) == "09-08 00:00"


def test_capture_1600_slot_is_under_the_spread_bar(
    capture_slots: list[SlotContext],
) -> None:
    """Why the entry is 16:30: 16:00 is 5.9c over the reference, short of 8c."""
    block = _found(find_target_block(capture_slots, _config()))
    before_entry = capture_slots[block.entry_idx - 1]
    entry = capture_slots[block.entry_idx]
    reference = min(s.buy_price for s in capture_slots[: block.entry_idx - 1])

    assert _local_time(before_entry) == "09-07 16:00"
    assert before_entry.buy_price - reference == pytest.approx(0.059, abs=0.002)
    assert before_entry.buy_price - reference < 0.08
    assert entry.buy_price - reference >= 0.08


def test_capture_need_matches_the_block_net_load(
    capture_slots: list[SlotContext],
) -> None:
    config = _config()
    block = _found(find_target_block(capture_slots, config))
    run = capture_slots[block.entry_idx : block.end_idx + 1]
    net_load = sum(max(0.0, s.consumption_kwh - s.solar_kwh) for s in run)

    assert block.needed_kwh == pytest.approx(net_load / config.discharge_efficiency)
    assert block.needed_pct == pytest.approx(
        block.needed_kwh / config.battery_capacity_kwh * 100.0
    )
    # Sixteen half-hour slots at ~1.79 kWh is more than two batteries' worth.
    # The detector reports that honestly; clamping to battery_target is the
    # caller's job.
    assert block.needed_pct > 200.0


def test_capture_block_widens_when_the_bar_is_lowered(
    capture_slots: list[SlotContext],
) -> None:
    """A bar under the 16:00 slot's 5.9c spread pulls the entry forward to it."""
    block = _found(find_target_block(capture_slots, _config(block_min_spread=0.05)))

    assert _local_time(capture_slots[block.entry_idx]) == "09-07 16:00"


# ---------------------------------------------------------------------------
# What counts as a block
# ---------------------------------------------------------------------------


def test_flat_day_has_no_block() -> None:
    assert find_target_block(_slots([0.15] * 48), _config()) is None


def test_dear_run_of_ninety_minutes_is_too_short() -> None:
    slots = _slots([CHEAP] * 4 + [DEAR] * 3 + [CHEAP] * 4)

    assert find_target_block(slots, _config()) is None


def test_dear_run_of_exactly_two_hours_qualifies() -> None:
    slots = _slots([CHEAP] * 4 + [DEAR] * 4 + [CHEAP] * 4)

    block = _found(find_target_block(slots, _config()))

    assert (block.entry_idx, block.end_idx) == (4, 7)


def test_duration_is_measured_in_time_not_slot_count() -> None:
    """Six 5-minute dear slots are half an hour, however many slots that is."""
    slots = _slots([CHEAP] * 4 + [DEAR] * 6 + [CHEAP] * 4, minutes=5)

    assert find_target_block(slots, _config()) is None
    assert find_target_block(slots, _config(block_min_duration_hours=0.5)) is not None


def test_one_slot_gap_does_not_split_a_run() -> None:
    # Two 1 h dear stretches, neither long enough alone, joined by one gap slot.
    slots = _slots([CHEAP] * 3 + [DEAR] * 2 + [CHEAP] + [DEAR] * 2 + [CHEAP] * 3)

    block = _found(find_target_block(slots, _config()))

    assert (block.entry_idx, block.end_idx) == (3, 7)


def test_two_slot_gap_splits_a_run() -> None:
    slots = _slots([CHEAP] * 3 + [DEAR] * 2 + [CHEAP] * 2 + [DEAR] * 2 + [CHEAP] * 3)

    assert find_target_block(slots, _config()) is None


def test_a_run_never_ends_on_a_gap_slot() -> None:
    slots = _slots([CHEAP] * 3 + [DEAR] * 4 + [CHEAP] * 5)

    block = _found(find_target_block(slots, _config()))

    assert block.end_idx == 6


def test_first_qualifying_run_wins_over_a_later_one() -> None:
    slots = _slots([CHEAP] * 2 + [DEAR] * 4 + [CHEAP] * 6 + [DEAR] * 8)

    block = _found(find_target_block(slots, _config()))

    assert (block.entry_idx, block.end_idx) == (2, 5)


def test_a_short_run_does_not_hide_a_later_qualifying_one() -> None:
    slots = _slots([CHEAP] * 2 + [DEAR] * 2 + [CHEAP] * 6 + [DEAR] * 5 + [CHEAP])

    block = _found(find_target_block(slots, _config()))

    assert (block.entry_idx, block.end_idx) == (10, 14)


def test_block_can_run_to_the_end_of_the_horizon() -> None:
    slots = _slots([CHEAP] * 2 + [DEAR] * 6)

    block = _found(find_target_block(slots, _config()))

    assert (block.entry_idx, block.end_idx) == (2, 7)


# ---------------------------------------------------------------------------
# What counts as dear
# ---------------------------------------------------------------------------


def test_reference_is_the_cheapest_price_before_the_slot_only() -> None:
    """A dear morning followed by a cheap night is not a block: nothing cheaper
    came before it, so there was no charge the battery could have taken."""
    slots = _slots([DEAR] * 8 + [CHEAP] * 8)

    assert find_target_block(slots, _config()) is None


def test_first_slot_is_never_dear() -> None:
    """Slot 0 has no earlier slot to be dearer than."""
    slots = _slots([DEAR] * 6)

    assert find_target_block(slots, _config()) is None


def test_slot_priced_exactly_one_spread_above_reference_is_dear() -> None:
    # 0.07 + 0.08 is 0.15000000000000002 in binary floats; the rule is ">=".
    slots = _slots([0.07] * 2 + [0.15] * 4 + [0.07] * 2)

    block = _found(find_target_block(slots, _config()))

    assert (block.entry_idx, block.end_idx) == (2, 5)


def test_slot_just_under_the_spread_is_not_dear() -> None:
    slots = _slots([0.07] * 2 + [0.149] * 4 + [0.07] * 2)

    assert find_target_block(slots, _config()) is None


def test_dear_price_with_solar_covering_load_is_not_dear() -> None:
    slots = _slots([CHEAP] * 2) + [
        _slot(i, DEAR, consumption_kwh=1.0, solar_kwh=1.5) for i in range(2, 8)
    ]

    assert find_target_block(slots, _config()) is None


def test_dear_test_uses_raw_solar_not_discounted_solar() -> None:
    """The dear rule is ``consumption > solar`` as forecast; accuracy only
    discounts the sizing."""
    slots = _slots([CHEAP] * 2) + [
        _slot(i, DEAR, consumption_kwh=1.0, solar_kwh=1.5) for i in range(2, 8)
    ]

    assert find_target_block(slots, _config(solar_forecast_accuracy=0.0)) is None


def test_spread_knob_moves_the_bar() -> None:
    slots = _slots([0.10] * 2 + [0.16] * 6)

    assert find_target_block(slots, _config(block_min_spread=0.08)) is None
    assert find_target_block(slots, _config(block_min_spread=0.05)) is not None


# ---------------------------------------------------------------------------
# Hysteresis on the entry index
# ---------------------------------------------------------------------------

# Fresh detection on this horizon enters at slot 4 and ends at slot 9.
_HYSTERESIS_PRICES = [CHEAP] * 4 + [DEAR] * 6 + [CHEAP] * 2


def test_no_previous_entry_returns_the_fresh_detection() -> None:
    block = _found(find_target_block(_slots(_HYSTERESIS_PRICES), _config()))

    assert (block.entry_idx, block.end_idx) == (4, 9)


def test_hysteresis_holds_when_new_entry_is_one_slot_later() -> None:
    block = _found(
        find_target_block(_slots(_HYSTERESIS_PRICES), _config(), previous_entry_idx=3)
    )

    assert block.entry_idx == 3
    assert block.end_idx == 9


def test_hysteresis_does_not_hold_when_new_entry_is_two_slots_later() -> None:
    block = _found(
        find_target_block(_slots(_HYSTERESIS_PRICES), _config(), previous_entry_idx=2)
    )

    assert block.entry_idx == 4


def test_hysteresis_holds_when_new_entry_is_one_slot_earlier() -> None:
    block = _found(
        find_target_block(_slots(_HYSTERESIS_PRICES), _config(), previous_entry_idx=5)
    )

    assert block.entry_idx == 5


def test_hysteresis_does_not_hold_when_new_entry_is_two_slots_earlier() -> None:
    block = _found(
        find_target_block(_slots(_HYSTERESIS_PRICES), _config(), previous_entry_idx=6)
    )

    assert block.entry_idx == 4


def test_previous_entry_equal_to_detection_changes_nothing() -> None:
    slots = _slots(_HYSTERESIS_PRICES)

    assert find_target_block(slots, _config(), previous_entry_idx=4) == (
        find_target_block(slots, _config())
    )


def test_held_entry_is_sized_from_the_held_slot() -> None:
    slots = _slots(_HYSTERESIS_PRICES)
    config = _config()

    held = _found(find_target_block(slots, config, previous_entry_idx=3))

    # Slots 3..9 inclusive: seven slots of 1.0 kWh net load.
    assert held.needed_kwh == pytest.approx(7.0 / config.discharge_efficiency)


def test_previous_entry_never_conjures_a_block() -> None:
    assert find_target_block(_slots([0.15] * 12), _config(), previous_entry_idx=5) is (
        None
    )


@pytest.mark.parametrize("previous", [-1, 0, 9, 99])
def test_distant_previous_entry_is_ignored(previous: int) -> None:
    block = _found(
        find_target_block(
            _slots(_HYSTERESIS_PRICES), _config(), previous_entry_idx=previous
        )
    )

    assert block.entry_idx == 4


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


def _sizing_slots() -> list[SlotContext]:
    """Two cheap slots, then four dear slots of 2.0 kWh load and 0.5 kWh solar."""
    return _slots([CHEAP] * 2) + [
        _slot(i, DEAR, consumption_kwh=2.0, solar_kwh=0.5) for i in range(2, 6)
    ]


def test_need_is_net_load_over_discharge_efficiency() -> None:
    config = _config(solar_forecast_accuracy=1.0)

    block = _found(find_target_block(_sizing_slots(), config))

    assert block.needed_kwh == pytest.approx(4 * 1.5 / 0.95)
    assert block.needed_pct == pytest.approx(4 * 1.5 / 0.95 / 13.5 * 100.0)


def test_solar_is_discounted_by_forecast_accuracy() -> None:
    block = _found(
        find_target_block(_sizing_slots(), _config(solar_forecast_accuracy=0.4))
    )

    assert block.needed_kwh == pytest.approx(4 * (2.0 - 0.5 * 0.4) / 0.95)


@pytest.mark.parametrize("accuracy", [-0.5, None, float("nan")])
def test_unusable_accuracy_gives_no_solar_credit(accuracy: float | None) -> None:
    block = _found(
        find_target_block(_sizing_slots(), _config(solar_forecast_accuracy=accuracy))
    )

    assert block.needed_kwh == pytest.approx(4 * 2.0 / 0.95)


def test_missing_accuracy_gives_no_solar_credit() -> None:
    fields = dataclasses.asdict(_config())
    fields.update(block_min_spread=0.08, block_min_duration_hours=2.0)
    del fields["solar_forecast_accuracy"]

    block = _found(find_target_block(_sizing_slots(), SimpleNamespace(**fields)))

    assert block.needed_kwh == pytest.approx(4 * 2.0 / 0.95)


def test_accuracy_above_one_is_capped_at_full_trust() -> None:
    block = _found(
        find_target_block(_sizing_slots(), _config(solar_forecast_accuracy=3.0))
    )

    assert block.needed_kwh == pytest.approx(4 * 1.5 / 0.95)


def test_a_gap_slot_with_surplus_solar_adds_no_negative_load() -> None:
    """A tolerated gap slot whose solar exceeds its load contributes zero, not a
    credit: the sizing never assumes a mid-block top-up."""
    slots = (
        _slots([CHEAP] * 2)
        + _slots([DEAR] * 2)
        + [_slot(4, DEAR, consumption_kwh=1.0, solar_kwh=3.0)]
        + _slots([DEAR] * 2)
    )

    block = _found(find_target_block(slots, _config()))

    assert (block.entry_idx, block.end_idx) == (2, 6)
    assert block.needed_kwh == pytest.approx(4 * 1.0 / 0.95)


def test_need_is_not_clamped_to_the_battery() -> None:
    slots = _slots([CHEAP] * 2 + [DEAR] * 10, consumption_kwh=4.0)

    block = _found(find_target_block(slots, _config()))

    assert block.needed_pct > 100.0


def test_efficiency_and_capacity_come_from_config() -> None:
    config = _config(discharge_efficiency=0.5, battery_capacity_kwh=10.0)

    block = _found(find_target_block(_sizing_slots(), config))

    assert block.needed_kwh == pytest.approx(4 * 1.5 / 0.5)
    assert block.needed_pct == pytest.approx(120.0)


# ---------------------------------------------------------------------------
# Degenerate inputs and purity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prices", [[], [DEAR]])
def test_horizon_too_short_for_a_reference_price(prices: list[float]) -> None:
    assert find_target_block(_slots(prices), _config()) is None


@pytest.mark.parametrize(
    "overrides",
    [{"battery_capacity_kwh": 0.0}, {"discharge_efficiency": 0.0}],
)
def test_unsizeable_battery_returns_none(overrides: dict[str, float]) -> None:
    slots = _slots([CHEAP] * 2 + [DEAR] * 6)

    assert find_target_block(slots, _config(**overrides)) is None


def test_detector_does_not_touch_the_slots() -> None:
    slots = _slots([CHEAP] * 2 + [DEAR] * 6)
    before = [dataclasses.replace(s) for s in slots]

    find_target_block(slots, _config(), previous_entry_idx=1)

    assert slots == before
    assert not any(s.is_demand_window_slot or s.is_demand_window_entry for s in slots)


def test_reason_names_the_block() -> None:
    block = _found(find_target_block(_slots([CHEAP] * 2 + [DEAR] * 6), _config()))

    assert isinstance(block.reason, str)
    assert block.reason


def test_block_is_immutable() -> None:
    block = _found(find_target_block(_slots([CHEAP] * 2 + [DEAR] * 6), _config()))

    with pytest.raises(dataclasses.FrozenInstanceError):
        block.entry_idx = 0  # type: ignore[misc]

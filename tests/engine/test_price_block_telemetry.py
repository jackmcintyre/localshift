"""target_block_* telemetry on the optimizer summary (docs/PRICE_BLOCK_TARGET.md; #1109).

Six attributes on ``sensor.localshift_optimizer_summary`` say what the price
block did on the last plan: whether one was found, when it starts and ends, the
target it was sized to, the energy it needs and why it was chosen. They are
written into the summary on every plan, so a plan made with the switch off
cannot carry values from a plan made with it on.
"""

from __future__ import annotations

import copy
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.computation_engine import ComputationEngine
from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.engine.optimizer_runner import (
    TARGET_BLOCK_TELEMETRY_KEYS,
    run_optimizer,
    target_block_telemetry,
)
from custom_components.localshift.engine.slots import SlotBuilder
from custom_components.localshift.engine.target_block import TargetBlock
from custom_components.localshift.engine.types import OptimizerConfig, SlotContext
from custom_components.localshift.sensors.optimizer import OptimizerSummarySensor
from tests.helpers.price_block_driver import CAPTURE_0907, Run, drive, load

INACTIVE = {
    "target_block_active": False,
    "target_block_entry": None,
    "target_block_end": None,
    "target_block_target_pct": None,
    "target_block_needed_kwh": None,
    "target_block_reason": None,
}


def _telemetry(summary: dict[str, Any] | None) -> dict[str, Any]:
    return {key: (summary or {}).get(key, "MISSING") for key in INACTIVE}


def _slot(index: int) -> SlotContext:
    return SlotContext(
        slot_index=index,
        timestamp_iso=f"2026-09-07T{9 + index // 2:02d}:{30 * (index % 2):02d}:00+10:00",
        slot_interval_minutes=30,
        buy_price=0.10,
        sell_price=0.0,
        solar_kwh=0.0,
        consumption_kwh=1.0,
        is_demand_window_slot=False,
        is_demand_window_entry=False,
    )


_BLOCK = TargetBlock(
    entry_idx=4, end_idx=8, needed_kwh=2.63157894, needed_pct=19.4931773, reason="why"
)


# ---------------------------------------------------------------------------
# The telemetry record
# ---------------------------------------------------------------------------


def test_keys_are_exactly_the_six_attributes() -> None:
    assert set(TARGET_BLOCK_TELEMETRY_KEYS) == set(INACTIVE)


def test_block_found_populates_every_attribute() -> None:
    slots = [_slot(i) for i in range(10)]
    config = OptimizerConfig(
        price_block_target=True, demand_window_target_soc_pct=39.4931773
    )

    assert target_block_telemetry(_BLOCK, slots, config) == {
        "target_block_active": True,
        "target_block_entry": "2026-09-07T11:00:00+10:00",
        # The block ends when its last slot (13:00, 30 minutes) does.
        "target_block_end": "2026-09-07T13:30:00+10:00",
        "target_block_target_pct": 39.49,
        "target_block_needed_kwh": 2.632,
        "target_block_reason": "why",
    }


def test_no_block_is_inactive_with_nothing_populated() -> None:
    slots = [_slot(i) for i in range(10)]
    config = OptimizerConfig(price_block_target=True, demand_window_target_soc_pct=20.0)

    assert target_block_telemetry(None, slots, config) == INACTIVE


def test_switch_off_is_inactive_even_if_handed_a_block() -> None:
    slots = [_slot(i) for i in range(10)]
    config = OptimizerConfig(price_block_target=False)

    assert target_block_telemetry(_BLOCK, slots, config) == INACTIVE


def test_unparseable_slot_time_leaves_the_end_empty() -> None:
    slots = [_slot(i) for i in range(10)]
    slots[8].timestamp_iso = "not a timestamp"
    config = OptimizerConfig(price_block_target=True)

    record = target_block_telemetry(_BLOCK, slots, config)

    assert record["target_block_active"] is True
    assert record["target_block_end"] is None


# ---------------------------------------------------------------------------
# Through the live inline path
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def on_0907() -> Run:
    return drive(load(CAPTURE_0907), price_block_target=True, live=True)


def test_0907_summary_reports_the_block(on_0907: Run) -> None:
    summary = on_0907.data.optimizer_summary
    entry = next(s for s in on_0907.slots if s.is_demand_window_entry)

    assert summary["target_block_active"] is True
    assert summary["target_block_entry"] == entry.timestamp_iso
    assert datetime.fromisoformat(summary["target_block_entry"]).strftime("%H:%M") == (
        "16:30"
    )
    # The last block slot starts at 00:00 and is 30 minutes long.
    assert datetime.fromisoformat(summary["target_block_end"]).strftime("%H:%M") == (
        "00:30"
    )
    assert summary["target_block_target_pct"] == 95.0
    assert summary["target_block_target_pct"] == on_0907.target_soc
    # About 220% of a 13.5 kWh battery.
    assert summary["target_block_needed_kwh"] == pytest.approx(29.67, abs=0.05)
    assert "dear run" in summary["target_block_reason"]


def test_0907_projected_dw_entry_soc_is_reported_for_the_block(on_0907: Run) -> None:
    """#1049 interaction: the *projected* entry SOC follows the block's flags."""
    summary = on_0907.data.optimizer_summary
    entry = next(s for s in on_0907.slots if s.is_demand_window_entry)
    # SOC entering the block is the SOC the slot before it ends on.
    before_entry = on_0907.decisions[entry.slot_index - 1]

    assert before_entry["slot_index"] == entry.slot_index - 1
    assert summary["dw_entry_soc_pct"] is not None
    assert summary["dw_entry_soc_pct"] == pytest.approx(
        before_entry["predicted_soc_pct"], abs=0.01
    )


def test_mild_evening_reports_the_load_sized_target() -> None:
    scenario = copy.deepcopy(load(CAPTURE_0907))
    scenario["input"]["load_power_kw"] = 0.6
    run = drive(scenario, price_block_target=True, live=True)
    summary = run.data.optimizer_summary

    assert summary["target_block_active"] is True
    assert summary["target_block_target_pct"] == pytest.approx(run.target_soc, abs=0.01)
    assert 5.0 < summary["target_block_target_pct"] < 95.0
    assert summary["target_block_needed_kwh"] == pytest.approx(
        (run.target_soc - 5.0) / 100.0 * 13.5, abs=0.01
    )


def test_flat_price_day_reports_no_block() -> None:
    flat = copy.deepcopy(load(CAPTURE_0907))
    for row in flat["input"]["general_forecast"]:
        row["per_kwh"] = 0.15
    flat["input"]["general_price"] = 0.15
    run = drive(flat, price_block_target=True, live=True)

    assert run.flags == "." * len(run.slots)
    assert _telemetry(run.data.optimizer_summary) == INACTIVE


@pytest.mark.parametrize("switch", [False, None], ids=["off", "absent"])
def test_switch_off_reports_no_block(switch: bool | None) -> None:
    run = drive(load(CAPTURE_0907), price_block_target=switch, live=True)

    assert _telemetry(run.data.optimizer_summary) == INACTIVE


def test_turning_the_switch_off_leaves_no_stale_values() -> None:
    """One plan with the switch on, the next with it off, on the same data."""
    run = drive(
        load(CAPTURE_0907), price_block_target=False, live=True, first_pass_switch=True
    )

    assert _telemetry(run.data.optimizer_summary) == INACTIVE
    assert run.data.target_block_entry_idx is None
    assert run.data.target_block_entry_iso is None


def test_turning_the_switch_on_reports_on_the_next_plan() -> None:
    run = drive(
        load(CAPTURE_0907), price_block_target=True, live=True, first_pass_switch=False
    )

    assert run.data.optimizer_summary["target_block_active"] is True


# A cycle that produces no plan. Each is one of run_inline's three exits that
# never reach _write_optimizer_fields, so the summary is the previous plan's.


def _no_slots(engine: ComputationEngine, data: CoordinatorData, stack: ExitStack):
    stack.enter_context(
        patch.object(
            SlotBuilder, "build_slots", lambda self, *a, **kw: ([], MagicMock())
        )
    )


def _unreadable_soc(engine: ComputationEngine, data: CoordinatorData, stack: ExitStack):
    stack.enter_context(
        patch(
            "custom_components.localshift.engine.optimizer_facade"
            "._normalize_initial_soc",
            return_value=(None, {"raw_soc": None, "error": "non_numeric"}),
        )
    )


def _planner_raises(engine: ComputationEngine, data: CoordinatorData, stack: ExitStack):
    stack.enter_context(
        patch.object(engine._dp_planner, "plan", side_effect=RuntimeError("boom"))
    )


_FAILURES = pytest.mark.parametrize(
    "fail",
    [_no_slots, _unreadable_soc, _planner_raises],
    ids=["no-slots", "unreadable-soc", "planner-raises"],
)


@_FAILURES
@pytest.mark.parametrize("switch", [False, True], ids=["switched-off", "still-on"])
def test_failed_cycle_after_a_block_reports_no_block(fail: Any, switch: bool) -> None:
    """A good plan with a block, then a cycle that produces no plan at all."""
    run = drive(
        load(CAPTURE_0907),
        price_block_target=switch,
        live=True,
        first_pass_switch=True,
        before_reported_pass=fail,
    )
    summary = run.data.optimizer_summary

    # The cycle really failed: the planner was never handed slots it solved.
    assert run.slots == []
    assert _telemetry(summary) == INACTIVE
    # The rest of the last good plan's summary is left as it was.
    assert summary["success"] is True
    assert summary["dw_entry_soc_pct"] is not None

    coordinator = MagicMock()
    coordinator.data = run.data
    attrs = OptimizerSummarySensor(coordinator, MagicMock()).extra_state_attributes
    assert {key: attrs[key] for key in INACTIVE} == INACTIVE


def test_failed_first_cycle_leaves_the_empty_summary_empty() -> None:
    """Nothing to reset before the first plan: the summary stays empty."""
    scenario = load(CAPTURE_0907)
    calls: list[int] = []

    def fail_both(
        engine: ComputationEngine, data: CoordinatorData, stack: ExitStack
    ) -> None:
        calls.append(1)

    with patch.object(
        SlotBuilder, "build_slots", lambda self, *a, **kw: ([], MagicMock())
    ):
        run = drive(
            scenario, price_block_target=True, live=True, before_reported_pass=fail_both
        )

    assert calls == [1]
    assert run.data.optimizer_summary == {}


# ---------------------------------------------------------------------------
# Through the batch runner
# ---------------------------------------------------------------------------


@dataclass
class _BatchData:
    soc: float = 50.0
    active_mode: Any = None
    daily_forecast: list[dict[str, Any]] = field(default_factory=list)
    optimizer_result: dict[str, Any] | None = None
    optimizer_decisions: list[dict[str, Any]] = field(default_factory=list)
    optimizer_summary: dict[str, Any] = field(default_factory=dict)
    target_block_entry_idx: int | None = None
    target_block_entry_iso: str | None = None


_DEAR_EVENING = [0.07, 0.07, 0.07, 0.07, 0.19, 0.19, 0.19, 0.19, 0.19, 0.07]


def _run_batch(monkeypatch: pytest.MonkeyPatch, options: dict[str, Any]) -> _BatchData:
    """``run_optimizer`` on a ten-slot day with a dear evening from 11:00."""
    slots = [_slot(i) for i in range(10)]
    for slot, price in zip(slots, _DEAR_EVENING, strict=True):
        slot.buy_price = price
        slot.consumption_kwh = 0.5

    monkeypatch.setattr(
        SlotBuilder, "build_slots", lambda self, *a, **kw: (slots, MagicMock())
    )
    data = _BatchData()
    run_optimizer(data, options)
    return data


def test_batch_runner_reports_the_block(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _run_batch(
        monkeypatch,
        {"price_block_target": True, "battery_target": 95, "minimum_target_soc": 20},
    )
    summary = data.optimizer_summary

    assert summary["success"] is True
    assert summary["target_block_active"] is True
    assert summary["target_block_entry"] == "2026-09-07T11:00:00+10:00"
    assert summary["target_block_end"] == "2026-09-07T13:30:00+10:00"
    needed_kwh = 5 * 0.5 / 0.95
    assert summary["target_block_needed_kwh"] == pytest.approx(needed_kwh, abs=0.001)
    assert summary["target_block_target_pct"] == pytest.approx(
        20.0 + needed_kwh / 13.5 * 100.0, abs=0.01
    )


def test_batch_runner_switch_off_reports_no_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _run_batch(monkeypatch, {"battery_target": 95, "minimum_target_soc": 20})

    assert data.optimizer_summary["success"] is True
    assert _telemetry(data.optimizer_summary) == INACTIVE


# ---------------------------------------------------------------------------
# The sensor
# ---------------------------------------------------------------------------


def _sensor(summary: dict[str, Any] | None) -> OptimizerSummarySensor:
    data = CoordinatorData()
    data.optimizer_summary = summary
    coordinator = MagicMock()
    coordinator.data = data
    return OptimizerSummarySensor(coordinator, MagicMock())


def test_sensor_publishes_the_block() -> None:
    record = {
        "target_block_active": True,
        "target_block_entry": "2026-09-07T16:30:00+10:00",
        "target_block_end": "2026-09-08T00:30:00+10:00",
        "target_block_target_pct": 95.0,
        "target_block_needed_kwh": 29.67,
        "target_block_reason": "dear run of 8.0 h",
    }
    attrs = _sensor({"enabled": True, "success": True, **record}).extra_state_attributes

    assert {key: attrs[key] for key in record} == record


@pytest.mark.parametrize(
    "summary",
    [
        None,
        {},
        {"enabled": True, "success": False, "error_message": "no_slots_available"},
        {"enabled": True, "success": True, **INACTIVE},
    ],
    ids=["no-summary", "empty", "failed-cycle", "inactive"],
)
def test_sensor_reads_inactive_when_the_summary_carries_no_block(
    summary: dict[str, Any] | None,
) -> None:
    attrs = _sensor(summary).extra_state_attributes

    assert {key: attrs[key] for key in INACTIVE} == INACTIVE

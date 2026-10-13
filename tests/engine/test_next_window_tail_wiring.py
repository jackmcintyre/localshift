"""The next-window tail, through the real path (issue #1033).

Every other tail test hands the planner its gap. Live, the gap is measured by
``SlotBuilder.build_slots``, carried on ``SlotBuildMetadata``, read by
``OptimizerFacade.run_inline`` into ``OptimizerInputs``, used by
``DPPlanner.plan`` and published by ``_build_summary``. If any link in that
chain is renamed or swapped, the gap never arrives, the tail is silently off,
and a test that sets the gap by hand stays green. This repo has shipped that
shape before (#959, #965), so this file runs the whole chain once.

The fixture is 14:45 on a low-solar day with the feed the October 2026
captures show: 48 half hours from 14:30, so the last slot ends at 14:30
tomorrow, half an hour short of the next demand window.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta
from unittest.mock import patch

from custom_components.localshift.computation_engine import ComputationEngine
from tests.test_scenarios import (
    create_mock_entry,
    create_mock_get_entity_id,
    create_mock_get_switch_state,
    setup_coordinator_data,
    setup_mock_hass,
)

_NOW = datetime.fromisoformat("2026-10-06T14:45:40+11:00")
_FEED_START = datetime.fromisoformat("2026-10-06T14:30:00+11:00")


def _price(start: datetime) -> float:
    if 15 <= start.hour < 21:
        return 0.30
    return 0.10 if 7 <= start.hour < 15 else 0.15


def _feed(sell: bool = False) -> list[dict]:
    """48 half hours from 14:30: ends 14:30 tomorrow."""
    return [
        {
            "start_time": (
                start := _FEED_START + timedelta(minutes=30 * i)
            ).isoformat(),
            "duration": 30,
            "per_kwh": 0.03 if sell else _price(start),
            "spike_status": "none",
            "estimate": i > 0,
        }
        for i in range(48)
    ]


def _solar(day: str, kw: float) -> list[dict]:
    """A flat ``kw`` from 08:00 to 16:00, in Solcast's half-hour shape."""
    midnight = datetime.fromisoformat(f"{day}T00:00:00+11:00")
    return [
        {
            "period_start": (midnight + timedelta(minutes=30 * i)).isoformat(),
            "pv_estimate": kw if 16 <= i < 32 else 0.0,
            "pv_estimate10": kw if 16 <= i < 32 else 0.0,
            "pv_estimate90": kw if 16 <= i < 32 else 0.0,
        }
        for i in range(48)
    ]


_PAYLOAD = {
    "test_time": _NOW.isoformat(),
    "soc": 100.0,
    "operation_mode": "autonomous",
    "backup_reserve": 10,
    "general_price": 0.10,
    "feed_in_price": 0.03,
    "general_forecast": _feed(),
    "feed_in_forecast": _feed(sell=True),
    "solcast_today": _solar("2026-10-06", 0.5),
    "solcast_tomorrow": _solar("2026-10-07", 0.5),
    "load_power_kw": 0.7,
    "solar_power_kw": 0.5,
    "active_mode": "self_consumption",
}
_OVERRIDES = {
    "battery_target": 95,
    "minimum_target_soc": 5,
    "allow_dw_entry_under_target": False,
    "max_pre_charge_price": 0.20,
    "demand_window_start": "15:00:00",
    "demand_window_end": "21:00:00",
    "optimization_mode": "self_consumption",
    "pricing_source": "amber",
    "comparison_mode": "disabled",
    "ha_timezone": "Australia/Sydney",
}
_SWITCHES = {"allow_dw_entry_under_target": False, "demand_window_block": True}


def _run_engine():
    """One pass of ``compute_derived_values`` on the fixture, nothing stubbed
    between the slot build and the summary."""
    data = setup_coordinator_data(_PAYLOAD)
    data.forecast_horizon_hours = 24.0
    engine = ComputationEngine(
        setup_mock_hass(_PAYLOAD),
        create_mock_entry(_OVERRIDES),
        create_mock_get_entity_id(),
        create_mock_get_switch_state(_SWITCHES),
    )
    patches = [
        patch("homeassistant.util.dt.now", return_value=_NOW),
        patch.object(engine, "_get_historical_hourly_averages", return_value={}),
        patch.object(engine._history_fetcher, "_historical_load_cache", {}),
        patch.object(engine._history_fetcher, "_historical_load_sample_counts", {}),
        patch.object(engine._history_fetcher, "_historical_load_source", "none"),
        patch.object(engine._history_fetcher, "_recent_load_1hr_kw", 0.7),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        engine.compute_derived_values(data)
    return data


def test_gap_reaches_the_planner_and_the_summary_says_applied():
    data = _run_engine()
    summary = data.optimizer_summary
    assert summary["success"] is True
    assert summary["next_window_gap_min"] == 30.0
    assert summary["next_window_tail_status"] == "applied"
    tail_start = summary["next_window_tail_start_idx"]
    assert tail_start is not None
    assert tail_start >= 1

    # The published plan is the stitched one: the decision at the tail start is
    # 21:00, and the tail ends on what the half-hour gap cannot add instead of
    # on the floor.
    decisions = data.optimizer_decisions
    first_tail = datetime.fromisoformat(decisions[tail_start]["timestamp_iso"])
    assert (first_tail.hour, first_tail.minute) == (21, 0)
    required = summary["next_window_required_soc_pct"]
    assert 75.0 < required < 95.0
    assert summary["next_window_entry_soc_pct"] >= required - 1.5
    assert decisions[-1]["predicted_soc_pct"] >= required - 1.5


def test_the_sensor_publishes_what_the_summary_carries():
    """The last link: summary dict to sensor attributes."""
    from unittest.mock import MagicMock

    from custom_components.localshift.sensors.optimizer import (
        OptimizerSummarySensor,
    )

    coordinator = MagicMock()
    coordinator.data = _run_engine()
    attrs = OptimizerSummarySensor(coordinator, MagicMock()).extra_state_attributes
    assert attrs["next_window_tail_status"] == "applied"
    assert attrs["next_window_gap_min"] == 30.0
    assert attrs["next_window_tail_start_idx"] is not None

"""The planner must not schedule export the hardware will not perform (Issue #1097).

2026-10-05 21:00: the planner selected ``export_proactive`` 35 seconds after
Tesla's stored tariff turned off-peak. Tesla's time-based control then held the
battery and grid-charged it at 5 kW. Export only happens at Tesla's top sell
rate, so slots outside it carry ``export_available=False`` and the DP has to
place its export elsewhere.
"""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.engine.constraints import feasible_actions
from custom_components.localshift.engine.negative_fit import (
    derive_negative_fit_avoidance_context,
)
from custom_components.localshift.engine.optimizer_dp import (
    DPPlanner,
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)
from custom_components.localshift.engine.slots import SlotBuilder

AEDT = timezone(timedelta(hours=11))

# Amber 30-minute forecast as LocalShift held it at 21:25 on 2026-10-05,
# from the 22:00 slot onward.
HORIZON_START = datetime(2026, 10, 5, 22, 0, tzinfo=AEDT)
BUY = [
    0.2055, 0.2036, 0.2017, 0.2012, 0.2001, 0.1987, 0.1981, 0.1946, 0.1881,
    0.1842, 0.1797, 0.1738, 0.1738, 0.1758, 0.1770, 0.1777, 0.1776, 0.1777,
    0.1661, 0.1438, 0.1145, 0.0948, 0.0815, 0.0742, 0.0719, 0.0701, 0.0709,
    0.0727, 0.0731, 0.0739, 0.0730, 0.0719, 0.0725, 0.0755, 0.0788, 0.0917,
    0.1071, 0.1204, 0.1347, 0.1540, 0.1773, 0.1893, 0.1898, 0.1944, 0.2016,
    0.2105, 0.2311,
]  # fmt: skip
SELL = [
    0.1291, 0.1273, 0.1256, 0.1252, 0.1242, 0.1229, 0.1224, 0.1192, 0.1133,
    0.1097, 0.1056, 0.1002, 0.1003, 0.1020, 0.1031, 0.1038, 0.1037, 0.1038,
    0.0933, 0.0730, 0.0463, 0.0284, 0.0164, 0.0097, -0.0047, -0.0063, -0.0056,
    -0.0040, -0.0036, -0.0028, -0.0036, -0.0047, -0.0041, -0.0014, 0.0139,
    0.0257, 0.0782, 0.0903, 0.1033, 0.1208, 0.1420, 0.1529, 0.1534, 0.1575,
    0.1641, 0.1722, 0.1523,
]  # fmt: skip


def _in_tesla_off_peak(when: datetime) -> bool:
    return when.hour >= 21 or when.hour < 4


def _live_horizon(*, block_off_peak: bool) -> list[SlotContext]:
    slots = []
    for i, (buy, sell) in enumerate(zip(BUY, SELL, strict=True)):
        start = HORIZON_START + timedelta(minutes=30 * i)
        hour = start.hour + start.minute / 60
        solar_kwh = (
            max(0.0, 3.0 * math.sin(math.pi * (hour - 6.5) / 11.5))
            if 6.5 <= hour <= 18
            else 0.0
        )
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=start.isoformat(),
                slot_interval_minutes=30,
                buy_price=buy,
                sell_price=sell,
                solar_kwh=solar_kwh,
                consumption_kwh=0.28,
                is_demand_window_slot=15 <= hour < 21,
                is_demand_window_entry=hour == 15,
                export_available=not (block_off_peak and _in_tesla_off_peak(start)),
            )
        )
    return slots


def _inputs(slots: list[SlotContext]) -> OptimizerInputs:
    return OptimizerInputs(
        cycle_id="issue-1097",
        initial_soc_pct=72.0,
        slots=slots,
        config=OptimizerConfig(
            battery_capacity_kwh=13.5,
            demand_window_target_soc_pct=95.0,
            soc_bins=100,
            optimization_mode="self_consumption",
            export_price_margin=0.1,
        ),
    )


def _export_slots(slots: list[SlotContext]) -> list[datetime]:
    result = DPPlanner().plan(_inputs(slots))
    assert result.success
    return [
        datetime.fromisoformat(slots[d.slot_index].timestamp_iso)
        for d in result.decisions
        if d.action == PlannerAction.EXPORT_PROACTIVE
    ]


def test_live_horizon_reproduces_the_off_peak_export():
    """Unrestricted, the plan exports inside Tesla's off-peak, as it did live."""
    exports = _export_slots(_live_horizon(block_off_peak=False))

    assert any(_in_tesla_off_peak(start) for start in exports)


def test_live_horizon_moves_export_out_of_tesla_off_peak():
    """Restricted, no export lands before 04:00 and the morning export remains."""
    exports = _export_slots(_live_horizon(block_off_peak=True))

    assert exports, "export should move to the on-peak hours, not disappear"
    assert not any(_in_tesla_off_peak(start) for start in exports)


def _slot(
    *, sell_price: float, buy_price: float, export_available: bool
) -> SlotContext:
    return SlotContext(
        slot_index=0,
        timestamp_iso="2026-10-05T22:00:00+11:00",
        slot_interval_minutes=30,
        buy_price=buy_price,
        sell_price=sell_price,
        solar_kwh=0.0,
        consumption_kwh=0.3,
        export_available=export_available,
    )


@pytest.mark.parametrize("optimization_mode", ["self_consumption", "arbitrage"])
def test_profitable_export_is_not_offered_when_unavailable(optimization_mode):
    config = OptimizerConfig(
        optimization_mode=optimization_mode, export_price_margin=0.02
    )

    offered = feasible_actions(
        80.0, _slot(sell_price=0.50, buy_price=0.20, export_available=True), config
    )
    withheld = feasible_actions(
        80.0, _slot(sell_price=0.50, buy_price=0.20, export_available=False), config
    )

    assert PlannerAction.EXPORT_PROACTIVE in offered
    assert PlannerAction.EXPORT_PROACTIVE not in withheld
    assert PlannerAction.HOLD in withheld


def test_negative_fit_avoidance_export_is_not_offered_when_unavailable():
    """The avoidance branch admitted the live export; it has to honour the flag."""
    slots = _live_horizon(block_off_peak=True)
    inputs = _inputs(slots)
    context = derive_negative_fit_avoidance_context(inputs)
    assert context is not None, "the live horizon has a negative-FIT spill ahead"

    blocked = feasible_actions(
        72.0,
        slots[0],
        inputs.config,
        slot_idx=0,
        negative_fit_avoidance_context=context,
    )

    assert slots[0].sell_price > 0
    assert PlannerAction.EXPORT_PROACTIVE not in blocked


def test_no_avoidance_context_when_every_export_chance_is_unavailable():
    slots = _live_horizon(block_off_peak=False)
    assert derive_negative_fit_avoidance_context(_inputs(slots)) is not None
    for slot in slots:
        slot.export_available = False

    assert derive_negative_fit_avoidance_context(_inputs(slots)) is None


class TestSlotBuilderFlag:
    """SlotBuilder derives the flag from CoordinatorData."""

    @pytest.fixture
    def builder(self):
        return SlotBuilder(
            config_options={
                "demand_window_start": "15:00:00",
                "demand_window_end": "21:00:00",
            },
            ha_timezone="Australia/Sydney",
        )

    def _build(self, builder, data, slot_start):
        ctx, _, _ = builder._process_single_slot(
            i=0,
            slot={
                "start": slot_start,
                "interval_minutes": 30,
                "price": 0.20,
                "price_source": "30min",
            },
            data=data,
            all_solcast=[],
            solar_confidence_factor=1.0,
            base_slot=slot_start,
            local_tz=ZoneInfo("Australia/Sydney"),
            dw_start_time=time(15, 0),
            dw_end_time=time(21, 0),
            prev_in_demand_window=False,
        )
        return ctx

    @pytest.fixture
    def data(self):
        data = CoordinatorData()
        data.load_forecast_slots = [0.5] * 96
        data.export_blocked_periods = [
            (
                datetime(2026, 10, 5, 21, 0, tzinfo=AEDT),
                datetime(2026, 10, 6, 4, 0, tzinfo=AEDT),
            )
        ]
        return data

    def test_slot_inside_tesla_off_peak_is_unavailable(self, builder, data):
        ctx = self._build(builder, data, datetime(2026, 10, 5, 22, 0, tzinfo=AEDT))

        assert ctx.export_available is False

    def test_slot_in_tesla_on_peak_is_available(self, builder, data):
        ctx = self._build(builder, data, datetime(2026, 10, 6, 4, 0, tzinfo=AEDT))

        assert ctx.export_available is True

    def test_slot_inside_watchdog_holdoff_is_unavailable(self, builder, data):
        data.export_blocked_periods = []
        data.export_suppressed_until = datetime(2026, 10, 5, 15, 0, tzinfo=AEDT)

        held = self._build(builder, data, datetime(2026, 10, 5, 14, 30, tzinfo=AEDT))
        after = self._build(builder, data, datetime(2026, 10, 5, 15, 0, tzinfo=AEDT))

        assert held.export_available is False
        assert after.export_available is True

    def test_default_coordinator_data_restricts_nothing(self, builder):
        data = CoordinatorData()
        data.load_forecast_slots = [0.5] * 96

        ctx = self._build(builder, data, datetime(2026, 10, 5, 22, 0, tzinfo=AEDT))

        assert ctx.export_available is True

    def test_untyped_stand_in_data_restricts_nothing(self, builder):
        """Replay harnesses and older tests pass bare mocks for ``data``."""
        data = MagicMock()
        data.feed_in_forecast = []
        data.load_forecast_slots = [0.5] * 96

        ctx = self._build(builder, data, datetime(2026, 10, 5, 22, 0, tzinfo=AEDT))

        assert ctx.export_available is True

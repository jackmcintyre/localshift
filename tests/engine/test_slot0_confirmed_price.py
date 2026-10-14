"""Issue #1119: slot 0 must be priced from the current interval, not a window.

On 2026-10-12 the plan's slot-0 sell price was $9.49 while the confirmed
feed-in price for that same interval was $24.23. The buy side was right: slot
0's buy price is the forecast entry covering "now". The sell side was looked
up over a fixed 15-minute window, so a 5-minute slot got the MEAN of the
current interval and the next two forecasts: (24.23 + 2.26 + 1.99) / 3 = 9.49.

Every test here drives the production path — ``SlotBuilder.build_slots`` (what
``OptimizerFacade.run_inline`` calls) or the whole
``ComputationEngine.compute_derived_values`` — on prices captured from the
live system on 2026-10-12.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.engine.slots import SlotBuilder
from custom_components.localshift.sensors.optimizer import OptimizerSummarySensor

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPTURES = REPO_ROOT / "simulations" / "replay-1119"
AEDT = timezone(timedelta(hours=11))


def _entry(start: datetime, price: float, estimate: bool | None = None) -> dict:
    # Amber starts each interval one second after the boundary.
    entry = {
        "start_time": (start + timedelta(seconds=1)).isoformat(),
        "duration": 5,
        "per_kwh": price,
    }
    if estimate is not None:
        entry["estimate"] = estimate
    return entry


def _data(
    now: datetime,
    sell: list[float],
    buy: list[float],
    estimate: bool | None,
    active_mode: BatteryMode = BatteryMode.SELF_CONSUMPTION,
) -> MagicMock:
    interval = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
    data = MagicMock()
    data.general_forecast = [
        _entry(interval + timedelta(minutes=5 * i), p, estimate if i == 0 else None)
        for i, p in enumerate(buy)
    ]
    data.feed_in_forecast = [
        _entry(interval + timedelta(minutes=5 * i), p, estimate if i == 0 else None)
        for i, p in enumerate(sell)
    ]
    data.general_price = buy[0]
    data.feed_in_price = sell[0]
    data.active_mode = active_mode
    data.solcast_today = []
    data.solcast_tomorrow = []
    data.load_forecast_slots = [0.5] * 96
    data.solcast_analysis_today = None
    data.solcast_analysis_tomorrow = None
    data.export_blocked_periods = []
    data.export_suppressed_until = None
    return data


@pytest.fixture
def builder() -> SlotBuilder:
    return SlotBuilder(
        config_options={
            "demand_window_start": "15:00:00",
            "demand_window_end": "21:00:00",
        },
        ha_timezone="Australia/Sydney",
    )


# Feed-in and general prices the live sensors carried at 15:15:18 on 2026-10-12.
SELL_1515 = [24.2284, 2.2574, 1.9932, 1.9573, 1.4764, 1.9071]
BUY_1515 = [26.7148, 2.6, 2.3, 2.25, 1.75, 2.2]


class TestSlot0SellPriceIsTheCurrentInterval:
    def test_confirmed_spike_is_not_averaged_with_the_next_two_forecasts(self, builder):
        now = datetime(2026, 10, 12, 15, 15, 30, tzinfo=AEDT)
        data = _data(now, SELL_1515, BUY_1515, estimate=False)

        slots, metadata = builder.build_slots(data, now_dt=now)

        assert slots[0].buy_price == pytest.approx(26.7148)
        # main returned 9.493 here: (24.2284 + 2.2574 + 1.9932) / 3.
        assert slots[0].sell_price == pytest.approx(24.2284)
        assert metadata.slot0_price["sell"] == pytest.approx(24.2284)
        assert metadata.slot0_price["sell_source"] == "forecast_current"
        assert metadata.slot0_price["sell_estimate"] is False

    def test_confirmed_trough_is_not_lifted_by_the_next_two_forecasts(self, builder):
        # 16:25 on 2026-10-12: confirmed 36c, main planned on $2.69 and exported.
        now = datetime(2026, 10, 12, 16, 25, 50, tzinfo=AEDT)
        data = _data(
            now,
            sell=[0.3572, 4.1873, 3.5148, 3.1801],
            buy=[0.414, 4.6, 3.9, 3.5],
            estimate=False,
        )

        slots, _ = builder.build_slots(data, now_dt=now)

        assert slots[0].sell_price == pytest.approx(0.3572)

    def test_later_slots_keep_their_existing_lookup(self, builder):
        now = datetime(2026, 10, 12, 15, 15, 30, tzinfo=AEDT)
        data = _data(now, SELL_1515, BUY_1515, estimate=False)

        slots, _ = builder.build_slots(data, now_dt=now)

        assert slots[1].sell_price == pytest.approx((2.2574 + 1.9932 + 1.9573) / 3)

    def test_flat_forecast_is_unchanged(self, builder):
        now = datetime(2026, 10, 12, 11, 2, 0, tzinfo=AEDT)
        data = _data(now, sell=[0.05] * 6, buy=[0.2] * 6, estimate=False)

        slots, _ = builder.build_slots(data, now_dt=now)

        assert slots[0].sell_price == pytest.approx(0.05)
        assert slots[0].export_available is True

    def test_no_covering_feed_in_entry_falls_back_to_the_window(self, builder):
        now = datetime(2026, 10, 12, 15, 15, 30, tzinfo=AEDT)
        data = _data(now, SELL_1515, BUY_1515, estimate=False)
        # Feed-in forecast is forward-only: nothing covers the current interval.
        data.feed_in_forecast = data.feed_in_forecast[1:]

        slots, metadata = builder.build_slots(data, now_dt=now)

        assert metadata.slot0_price["sell_source"] == "forecast_window"
        assert metadata.slot0_price["sell_estimate"] is None
        assert slots[0].export_available is True


class TestUnconfirmedReadingDoesNotStartAnExport:
    """Amber rolls the interval over with `estimate: true` and the PREVIOUS
    interval's price until the real one is confirmed ~20-40 s later."""

    # 15:20:10 on 2026-10-12: the sensor still carried 15:15's $24.23. The
    # confirmed 15:20 price, 13 s later, was $0.71.
    SELL = [24.2284, 1.9932, 1.9573, 1.4764]
    BUY = [26.7148, 2.3, 2.25, 1.75]

    def test_estimate_inside_grace_blocks_an_export_start(self, builder):
        now = datetime(2026, 10, 12, 15, 20, 10, tzinfo=AEDT)
        data = _data(now, self.SELL, self.BUY, estimate=True)

        slots, metadata = builder.build_slots(data, now_dt=now)

        assert slots[0].sell_price == pytest.approx(24.2284)
        assert metadata.slot0_price["sell_estimate"] is True
        assert slots[0].export_available is False
        assert metadata.slot0_price["export_start_deferred"] is True
        assert slots[1].export_available is True

    def test_estimate_does_not_interrupt_an_export_already_running(self, builder):
        now = datetime(2026, 10, 12, 15, 20, 10, tzinfo=AEDT)
        data = _data(
            now,
            self.SELL,
            self.BUY,
            estimate=True,
            active_mode=BatteryMode.PROACTIVE_EXPORT,
        )

        slots, metadata = builder.build_slots(data, now_dt=now)

        assert slots[0].export_available is True
        assert metadata.slot0_price["export_start_deferred"] is False

    def test_estimate_past_the_grace_is_acted_on(self, builder):
        # A feed that never confirms must not block export for the interval.
        now = datetime(2026, 10, 12, 15, 22, 0, tzinfo=AEDT)
        data = _data(now, self.SELL, self.BUY, estimate=True)

        slots, metadata = builder.build_slots(data, now_dt=now)

        assert slots[0].export_available is True
        assert metadata.slot0_price["export_start_deferred"] is False

    def test_confirmed_reading_is_acted_on_immediately(self, builder):
        now = datetime(2026, 10, 12, 15, 20, 10, tzinfo=AEDT)
        data = _data(now, self.SELL, self.BUY, estimate=False)

        slots, _ = builder.build_slots(data, now_dt=now)

        assert slots[0].export_available is True


def _replay(name: str, arm: str = "after") -> dict:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import replay_slot0_price

    scenario = json.loads((CAPTURES / f"{name}.json").read_text())
    return replay_slot0_price.run(scenario, arm)


class TestRealInputsThroughTheEngine:
    """The captured 2026-10-12 inputs through ComputationEngine end to end."""

    def test_1515_plan_commits_slot0_on_the_confirmed_feed_in(self):
        out = _replay("2026-10-12T151530")

        assert out["sell"] == pytest.approx(24.2284, abs=1e-4)
        assert out["slot0_price"]["sell"] == pytest.approx(24.2284, abs=1e-4)
        assert out["slot0_price"]["sell_source"] == "forecast_current"
        assert out["slot0_price"]["buy_source"] == "forecast_current"
        assert out["slot0_price"]["sell_estimate"] is False
        assert out["slot0_price"]["live_sell"] == pytest.approx(24.2284, abs=1e-4)

    def test_1625_plan_does_not_export_into_a_36c_interval(self):
        # main: slot-0 sell $2.69 -> export_proactive. Confirmed price: 36c.
        assert _replay("2026-10-12T162550", "before")["action"] == "export_proactive"

        out = _replay("2026-10-12T162550")

        assert out["sell"] == pytest.approx(0.3572, abs=1e-4)
        assert out["action"] != "export_proactive"

    def test_1520_estimate_does_not_start_an_export(self):
        # The sensor still carried 15:15's $24.23, unconfirmed; main planned
        # an export on it. The confirmed 15:20 price was $0.71.
        assert _replay("2026-10-12T152010", "before")["action"] == "export_proactive"

        out = _replay("2026-10-12T152010")

        assert out["slot0_price"]["sell_estimate"] is True
        assert out["slot0_price"]["export_start_deferred"] is True
        assert out["action"] != "export_proactive"

    @pytest.mark.parametrize("name", ["2026-10-12T153040", "2026-10-12T163040"])
    def test_confirmed_spike_still_exports(self, name):
        # A confirmed $10.5 interval, export already running: nothing here
        # may talk the plan out of it.
        out = _replay(name)

        assert out["slot0_price"]["sell_estimate"] is False
        assert out["slot0_price"]["export_start_deferred"] is False
        assert out["action"] == "export_proactive"


class TestSummarySensorPublishesSlot0Price:
    def test_slot0_price_is_an_attribute(self):
        slot0_price = {"buy": 26.7148, "sell": 24.2284, "sell_estimate": False}
        coordinator = MagicMock()
        coordinator.data.solcast_analysis_today = None
        coordinator.data.solcast_analysis_tomorrow = None
        coordinator.data.solar_absent_confidence = 1.0
        coordinator.data.dw_entry_actual_at = None
        coordinator.data.optimizer_summary = {
            "success": True,
            "enabled": True,
            "slot0_price": slot0_price,
        }
        sensor = OptimizerSummarySensor.__new__(OptimizerSummarySensor)
        sensor.coordinator = coordinator

        assert sensor.extra_state_attributes["slot0_price"] == slot0_price

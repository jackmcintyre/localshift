"""Tests for planner-chosen booking starts (issue #1238)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.localshift import bookings as bk
from custom_components.localshift.const import DOMAIN
from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.engine.booked_load import (
    LOAD_MARGIN,
    active_loads,
    choose_start,
    with_booked_load,
)
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.optimizer_facade import OptimizerFacade
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    SlotContext,
)
from tests.test_bookings import (  # noqa: F401  (fake_store fixture)
    FakeStore,
    make_store,
)

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)


def slots_from(prices, dw=(), interval=30):
    return [
        SlotContext(
            slot_index=i,
            timestamp_iso=(NOW + timedelta(minutes=interval * i)).isoformat(),
            slot_interval_minutes=interval,
            buy_price=price,
            sell_price=0.05,
            solar_kwh=0.0,
            consumption_kwh=0.2,
            is_demand_window_slot=i in dw,
        )
        for i, price in enumerate(prices)
    ]


def inputs_from(slots, soc=60.0):
    return OptimizerInputs(
        cycle_id="t",
        initial_soc_pct=soc,
        slots=slots,
        config=OptimizerConfig(soc_bins=20),
    )


def booking(start, minutes=60, kwh=1.0, status="booked"):
    return {
        "status": status,
        "start_at": start.isoformat(),
        "duration_min": minutes,
        "est_kwh": kwh,
    }


class GridOnlyPlanner:
    """No battery: all load is imported, so cost and import follow consumption."""

    def __init__(self):
        self.seen = []

    def plan(self, inputs):
        self.seen.append(inputs)
        decisions = [
            SimpleNamespace(grid_import_kwh=s.consumption_kwh) for s in inputs.slots
        ]
        cost = sum(s.consumption_kwh * s.buy_price for s in inputs.slots)
        return SimpleNamespace(
            success=True, decisions=decisions, projected_net_cost=cost
        )


class TestBookedLoadInTheForecast:
    def test_kwh_with_margin_is_spread_evenly_over_the_run(self):
        slots = slots_from([0.2] * 6)
        loads = active_loads([booking(NOW + HOUR, 60, 1.0)])
        out = with_booked_load(slots, loads)
        extra = [
            round(o.consumption_kwh - s.consumption_kwh, 6)
            for o, s in zip(out, slots, strict=False)
        ]
        assert extra == [0, 0, 0.6, 0.6, 0, 0]
        assert sum(extra) == pytest.approx(1.0 * LOAD_MARGIN)

    def test_a_run_part_way_through_a_slot_gets_its_share(self):
        out = with_booked_load(
            slots_from([0.2] * 2),
            active_loads([booking(NOW + timedelta(minutes=15), 30, 1.0)]),
        )
        assert [round(o.consumption_kwh - 0.2, 6) for o in out] == [0.6, 0.6]

    def test_only_booked_and_started_loads_count(self):
        rows = [
            booking(NOW, status="done"),
            booking(NOW, status="cancelled"),
            {**booking(NOW), "duration_min": None},
            booking(NOW, status="started"),
        ]
        assert len(active_loads(rows)) == 1

    def test_no_bookings_returns_the_very_same_slots(self):
        slots = slots_from([0.2] * 4)
        assert with_booked_load(slots, active_loads([])) is slots
        assert with_booked_load(slots, active_loads(None)) is slots
        assert (
            with_booked_load(slots, active_loads([booking(NOW, status="done")]))
            is slots
        )


class TestNoBookingsLeavesThePlanAlone:
    def test_real_planner_result_is_identical(self):
        prices = [0.40] * 4 + [0.10] * 8 + [0.45] * 6
        plain = DPPlanner().plan(inputs_from(slots_from(prices, dw=range(14, 18))))
        laid = DPPlanner().plan(
            inputs_from(
                with_booked_load(slots_from(prices, dw=range(14, 18)), active_loads([]))
            )
        )
        strip = lambda r: dataclasses.asdict(r) | {"solve_time_seconds": 0}  # noqa: E731
        assert strip(plain) == strip(laid)

    @pytest.mark.allow_swallowed_failure("inline_optimizer")
    def test_facade_hands_the_planner_the_builders_slots_untouched(self):
        built = slots_from([0.2] * 4)
        seen = []

        class Builder:
            def __init__(self, **_):
                pass

            def build_slots(self, _data, now_dt=None):
                return built, SimpleNamespace(
                    all_solcast=[], next_demand_window_gap_min=None
                )

        class Spy:
            def plan(self, inputs):
                seen.append(inputs.slots)
                raise RuntimeError("stop after the planner is called")

        facade = OptimizerFacade(Spy(), slot_builder_cls=Builder)
        data = CoordinatorData()
        data.soc = 50.0
        facade.run_inline(data, NOW, {})
        assert seen and seen[0] is built
        assert facade.booking_base.slots is built

    @pytest.mark.allow_swallowed_failure("inline_optimizer")
    def test_facade_lays_booked_load_on_the_planners_slots_only(self):
        built = slots_from([0.2] * 4)
        seen = []

        class Builder:
            def __init__(self, **_):
                pass

            def build_slots(self, _data, now_dt=None):
                return built, SimpleNamespace(
                    all_solcast=[], next_demand_window_gap_min=None
                )

        class Spy:
            def plan(self, inputs):
                seen.append(inputs.slots)
                raise RuntimeError("stop")

        facade = OptimizerFacade(Spy(), slot_builder_cls=Builder)
        data = CoordinatorData()
        data.soc = 50.0
        data.load_bookings = [booking(NOW, 30, 1.0)]
        facade.run_inline(data, NOW, {})
        assert seen[0][0].consumption_kwh == pytest.approx(0.2 + 1.2)
        assert facade.booking_base.slots[0].consumption_kwh == 0.2


class TestChooseStart:
    def test_lowest_cost_start_wins(self):
        prices = [0.40] * 4 + [0.10] * 4 + [0.40] * 4
        base = inputs_from(slots_from(prices))
        start, why = choose_start(
            GridOnlyPlanner(), base, [], NOW, NOW + 6 * HOUR, HOUR, 1.0
        )
        assert start == NOW + 2 * HOUR
        assert why == "lowest plan cost"

    def test_candidates_are_on_a_15_minute_grid_within_the_window(self):
        planner = GridOnlyPlanner()
        base = inputs_from(slots_from([0.2] * 12))
        choose_start(planner, base, [], NOW, NOW + 3 * HOUR, HOUR, 1.0)
        # one plain plan, then starts 0:00 .. 2:00 every 15 min
        assert len(planner.seen) == 1 + 9

    def test_a_run_that_ends_past_the_horizon_is_not_a_candidate(self):
        # Cheapest price is the very last slot, but a run starting there would
        # have most of its load fall off the end of the plan and look free.
        prices = [0.40] * 5 + [0.01]
        base = inputs_from(slots_from(prices))
        start, _ = choose_start(
            GridOnlyPlanner(), base, [], NOW, NOW + 8 * HOUR, HOUR, 1.0
        )
        assert start + HOUR <= NOW + 3 * HOUR

    def test_nothing_fits_inside_the_horizon(self):
        base = inputs_from(slots_from([0.2] * 2))
        start, why = choose_start(
            GridOnlyPlanner(), base, [], NOW, NOW + 8 * HOUR, 2 * HOUR, 1.0
        )
        assert start is None
        assert "horizon" in why

    def test_demand_window_import_rejects_starts_before_or_inside_it(self):
        # Slots 4-7 are the window and the cheapest hours are inside it.
        prices = [0.30] * 4 + [0.05] * 4 + [0.30] * 4
        base = inputs_from(slots_from(prices, dw=range(4, 8)))
        start, why = choose_start(
            GridOnlyPlanner(), base, [], NOW, NOW + 6 * HOUR, HOUR, 1.0
        )
        assert start >= NOW + 4 * HOUR
        assert why == "lowest plan cost"

    def test_no_start_avoids_window_import_so_none_is_chosen(self):
        base = inputs_from(slots_from([0.2] * 6, dw=range(4, 6)))
        start, why = choose_start(
            GridOnlyPlanner(), base, [], NOW, NOW + 2 * HOUR, HOUR, 1.0
        )
        assert start is None
        assert "demand window" in why

    def test_without_a_window_in_the_horizon_the_rule_does_not_apply(self):
        base = inputs_from(slots_from([0.2] * 6))
        start, _ = choose_start(
            GridOnlyPlanner(), base, [], NOW, NOW + 2 * HOUR, HOUR, 1.0
        )
        assert start is not None

    def test_earlier_bookings_are_fixed_load_in_every_plan(self):
        planner = GridOnlyPlanner()
        base = inputs_from(slots_from([0.2] * 8))
        earlier = [booking(NOW, 60, 1.0)]
        choose_start(planner, base, earlier, NOW, NOW + 3 * HOUR, HOUR, 1.0)
        assert planner.seen[0].slots[0].consumption_kwh == pytest.approx(0.2 + 0.6)
        assert all(p.slots[0].consumption_kwh >= 0.2 + 0.6 for p in planner.seen)

    def test_the_callers_slots_are_never_written_to(self):
        base = inputs_from(slots_from([0.2] * 8))
        before = [dataclasses.replace(s) for s in base.slots]
        choose_start(DPPlanner(), base, [], NOW, NOW + 3 * HOUR, HOUR, 1.0)
        assert base.slots == before

    def test_failed_baseline_plan_gives_no_choice(self):
        class Failing:
            def plan(self, _):
                return SimpleNamespace(success=False)

        start, why = choose_start(
            Failing(),
            inputs_from(slots_from([0.2] * 4)),
            [],
            NOW,
            NOW + 2 * HOUR,
            HOUR,
            1.0,
        )
        assert (start, why) == (None, "no plan to evaluate against")

    def test_real_planner_picks_a_start_inside_the_window(self):
        prices = [0.40] * 4 + [0.10] * 8 + [0.45] * 6
        base = inputs_from(slots_from(prices, dw=range(14, 18)))
        start, _ = choose_start(DPPlanner(), base, [], NOW, NOW + 7 * HOUR, HOUR, 1.0)
        assert start is not None
        assert NOW <= start <= NOW + 6 * HOUR


class TestBookingUsesThePlan:
    async def _book(self, choose):
        store = make_store()
        return await store.async_book(
            "washer",
            60,
            NOW,
            NOW + 8 * HOUR,
            NOW,
            excess_kw_at=lambda s, e: None,
            est_kwh=1.0,
            choose=choose,
        )

    async def test_the_planned_start_and_reason_are_stored(self):
        async def choose(*_):
            return NOW + 2 * HOUR, "lowest plan cost"

        b = await self._book(choose)
        assert b["start_at"] == (NOW + 2 * HOUR).isoformat()
        assert b["reason"] == "lowest plan cost"
        assert b["duration_min"] == 60

    async def test_without_a_planned_start_the_deadline_pick_stays_with_the_reason(
        self,
    ):
        async def choose(*_):
            return (
                None,
                "every start adds grid import before or during the demand window",
            )

        b = await self._book(choose)
        assert b["start_at"] == (NOW + 7 * HOUR).isoformat()
        assert b["reason"].startswith("every start adds grid import")
        assert "latest start to meet the deadline" in b["reason"]

    async def test_bookings_are_placed_in_order(self):
        order = []

        async def slow(*_):
            order.append("a-start")
            await __import__("asyncio").sleep(0.05)
            order.append("a-end")
            return NOW, "x"

        async def quick(*_):
            order.append("b")
            return NOW, "x"

        store = make_store()
        kw = {"excess_kw_at": lambda s, e: None, "est_kwh": 1.0}
        await __import__("asyncio").gather(
            store.async_book("a", 60, NOW, NOW + 4 * HOUR, NOW, choose=slow, **kw),
            store.async_book("b", 60, NOW, NOW + 4 * HOUR, NOW, choose=quick, **kw),
        )
        assert order == ["a-start", "a-end", "b"]


class TestRealHomeAssistantServiceCall:
    async def test_book_load_plans_the_start_off_the_event_loop(self, tmp_path):
        prices = [0.40] * 4 + [0.10] * 8 + [0.45] * 6
        base = inputs_from(slots_from(prices, dw=range(14, 18)))
        hass = HomeAssistant(str(tmp_path))
        store = make_store()
        facade = SimpleNamespace(booking_base=base, planner=DPPlanner())
        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(
                bookings=store,
                data=CoordinatorData(),
                computation_engine=SimpleNamespace(optimizer_facade=facade),
            )
        )
        bk.async_register_services(hass)
        hass.config_entries = SimpleNamespace(async_loaded_entries=lambda _d: [entry])
        data = {
            "name": "washer",
            "duration_min": 60,
            "est_kwh": 1.0,
            "earliest_start": NOW.isoformat(),
            "latest_finish": (NOW + 7 * HOUR).isoformat(),
        }
        with patch.object(bk.dt_util, "utcnow", return_value=NOW):
            booked = await hass.services.async_call(
                DOMAIN, "book_load", data, blocking=True, return_response=True
            )
        assert booked["reason"] == "lowest plan cost"
        start = datetime.fromisoformat(booked["start_at"])
        assert NOW <= start <= NOW + 6 * HOUR
        assert (start - NOW) % timedelta(minutes=15) == timedelta(0)
        await hass.async_stop(force=True)

    async def test_before_any_plan_exists_the_deadline_pick_is_kept(self, tmp_path):
        hass = HomeAssistant(str(tmp_path))
        facade = SimpleNamespace(booking_base=None, planner=DPPlanner())
        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(
                bookings=make_store(),
                data=CoordinatorData(),
                computation_engine=SimpleNamespace(optimizer_facade=facade),
            )
        )
        bk.async_register_services(hass)
        hass.config_entries = SimpleNamespace(async_loaded_entries=lambda _d: [entry])
        data = {
            "name": "washer",
            "duration_min": 60,
            "est_kwh": 1.0,
            "earliest_start": NOW.isoformat(),
            "latest_finish": (NOW + 7 * HOUR).isoformat(),
        }
        with patch.object(bk.dt_util, "utcnow", return_value=NOW):
            booked = await hass.services.async_call(
                DOMAIN, "book_load", data, blocking=True, return_response=True
            )
        assert "latest start to meet the deadline" in booked["reason"]
        await hass.async_stop(force=True)


class TestReplayCarriesBookings:
    """The replay harness reads ``input.bookings``, so a booked plan is replayable."""

    CAPTURE = "simulations/replay/2026-08-25.json"

    def _replay(self, bookings):
        import json
        from pathlib import Path

        from scripts import replay_plan_baseline as rpb

        scenario = json.loads(Path(self.CAPTURE).read_text())
        if bookings is not None:
            scenario["input"]["bookings"] = bookings
        return rpb.replay(scenario, rpb.LIVE_LIKE)

    def test_inactive_bookings_leave_the_plan_byte_identical(self):
        plain = self._replay(None)
        start = plain["decisions"][0][0]
        inactive = [booking(datetime.fromisoformat(start), status="cancelled")]
        assert self._replay([]) == plain
        assert self._replay(inactive) == plain

    def test_an_active_booking_changes_the_plan(self):
        plain = self._replay(None)
        start = datetime.fromisoformat(plain["decisions"][0][0])
        booked = self._replay([booking(start, 120, 3.0)])
        assert booked != plain


class TestWiring:
    def test_the_engine_and_facade_expose_the_planner_and_facade(self):
        from custom_components.localshift.computation_engine import ComputationEngine

        planner = DPPlanner()
        facade = OptimizerFacade(planner)
        assert facade.planner is planner
        engine = SimpleNamespace(_optimizer_facade=facade)
        assert ComputationEngine.optimizer_facade.fget(engine) is facade

    def test_a_booking_change_reaches_the_planner_data_and_listeners(self):
        from unittest.mock import MagicMock

        from custom_components.localshift.coordinator.coordinator import (
            LocalShiftCoordinator,
        )

        store = make_store()
        store._bookings = {
            "a": booking(NOW) | {"id": "a", "finish_by": NOW.isoformat()}
        }
        me = SimpleNamespace(
            bookings=store, data=CoordinatorData(), notify_listeners=MagicMock()
        )
        with patch.object(bk.dt_util, "utcnow", return_value=NOW - HOUR):
            LocalShiftCoordinator._bookings_changed(me)
        assert [b["id"] for b in me.data.load_bookings] == ["a"]
        me.notify_listeners.assert_called_once()
        me.bookings = None
        LocalShiftCoordinator._bookings_changed(me)
        assert me.notify_listeners.call_count == 2

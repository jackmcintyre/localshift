"""Issue #1120: the default mode must export on a genuine price spike.

In ``self_consumption`` optimisation mode, proactive export was admitted only
when ``sell >= buy + export_price_margin`` in the SAME slot. Amber's feed-in
price is always below its general price for the same interval, so that test
cannot pass on honest prices: on 2026-10-12 feed-in hit $24.23 and $10.52 and
the default mode held through both.

The export is now also admitted when the slot is a genuine spike: the sell
price is an outlier against the horizon's ordinary buy price AND it beats what
the energy would cost to buy back later, grossed up for round-trip losses.

The replay tests drive ``ComputationEngine.compute_derived_values`` on the
inputs captured from the live system on 2026-10-12; the planner tests drive
``DPPlanner.plan``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)
from custom_components.localshift.sensors.optimizer import OptimizerSummarySensor

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPTURES = REPO_ROOT / "simulations" / "replay-1119"


def _replay(instant: str, optimization_mode: str = "self_consumption") -> dict:
    """One captured instant through the real engine, in the given mode."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from replay_slot0_price import run

    scenario = json.loads((CAPTURES / f"2026-10-12T{instant}.json").read_text())
    return run(scenario, "after", optimization_mode)


class TestDefaultModeExportsOnTheRealSpike:
    """2026-10-12, replayed in self_consumption mode."""

    @pytest.mark.parametrize(
        ("instant", "confirmed_sell"),
        [("151530", 24.2284), ("153040", 10.4839), ("163040", 10.5225)],
    )
    def test_confirmed_spike_interval_exports(self, instant, confirmed_sell):
        result = _replay(instant)

        assert result["sell"] == pytest.approx(confirmed_sell)
        # The base branch planned "hold" at all three.
        assert result["action"] == "export_proactive"
        spike = result["spike_export"]
        assert spike["admitted"] is True
        assert spike["exporting"] is True
        # Judged against the dearest later buy price, not the same interval's.
        assert spike["rebuy_price"] < result["buy"]
        assert spike["sell"] >= spike["min_sell"]

    @pytest.mark.parametrize(
        ("instant", "confirmed_sell"), [("161040", 0.6762), ("162550", 0.3572)]
    )
    def test_sub_dollar_interval_between_spikes_does_not_export(
        self, instant, confirmed_sell
    ):
        result = _replay(instant)

        assert result["sell"] == pytest.approx(confirmed_sell)
        assert result["action"] == "hold"
        spike = result["spike_export"]
        assert spike["admitted"] is False
        assert spike["exporting"] is False
        # Buying the energy back in the next interval costs several times more.
        assert spike["rebuy_price"] > 3.0

    def test_unconfirmed_carried_spike_price_does_not_start_an_export(self):
        """15:20:10 still showed 15:15's $24.23; the real price was 71c."""
        result = _replay("152010")

        assert result["slot0_price"]["sell_estimate"] is True
        assert result["action"] == "hold"
        assert result["spike_export"]["skip_debounce"] is False

    def test_rebuy_price_is_published_on_the_summary_sensor(self):
        result = _replay("151530")
        coordinator = MagicMock()
        coordinator.data.solcast_analysis_today = None
        coordinator.data.solcast_analysis_tomorrow = None
        coordinator.data.solar_absent_confidence = 1.0
        coordinator.data.dw_entry_actual_at = None
        coordinator.data.optimizer_summary = {
            "success": True,
            "enabled": True,
            "spike_export": result["spike_export"],
        }
        sensor = OptimizerSummarySensor.__new__(OptimizerSummarySensor)
        sensor.coordinator = coordinator

        attrs = sensor.extra_state_attributes

        assert attrs["spike_export"]["rebuy_price"] == pytest.approx(
            result["spike_export"]["rebuy_price"]
        )
        assert attrs["spike_export"]["rebuy_at"]


# --------------------------------------------------------------------------
# DPPlanner.plan on hand-built horizons
# --------------------------------------------------------------------------


def _slots(prices: list[tuple[float, float]], minutes: int = 30) -> list[SlotContext]:
    """Slots from (buy, sell) pairs, with a steady 0.5 kW household load."""
    from datetime import datetime, timedelta, timezone

    start = datetime(2026, 10, 12, 17, 0, tzinfo=timezone(timedelta(hours=11)))
    return [
        SlotContext(
            slot_index=i,
            timestamp_iso=(start + timedelta(minutes=minutes * i)).isoformat(),
            slot_interval_minutes=minutes,
            buy_price=buy,
            sell_price=sell,
            solar_kwh=0.0,
            consumption_kwh=0.5 * minutes / 60.0,
        )
        for i, (buy, sell) in enumerate(prices)
    ]


def _plan(prices: list[tuple[float, float]], mode: str = "self_consumption"):
    config = OptimizerConfig(optimization_mode=mode, export_price_margin=0.10)
    inputs = OptimizerInputs(
        cycle_id="test-1120",
        initial_soc_pct=90.0,
        slots=_slots(prices),
        config=config,
    )
    result = DPPlanner().plan(inputs)
    assert result.success, result.error_message
    return result, inputs


ORDINARY_EVENING = (
    # 17:00 evening peak, easing overnight, a morning peak, then the day.
    [(0.45, 0.30), (0.48, 0.33), (0.46, 0.31), (0.40, 0.26), (0.33, 0.20)]
    + [(0.25, 0.12)] * 6
    + [(0.20, 0.08)] * 14
    + [(0.34, 0.20), (0.36, 0.22), (0.30, 0.16)]
    + [(0.18, 0.05)] * 10
)


class TestPlannerAdmission:
    def test_spike_slot_exports_in_self_consumption(self):
        prices = [(26.71, 24.23), (2.55, 2.26), (2.26, 1.99)] + [(0.20, 0.10)] * 30

        result, inputs = _plan(prices)

        assert result.decisions[0].action == PlannerAction.EXPORT_PROACTIVE
        assert inputs.slots[0].spike_export_admitted is True
        assert inputs.config.spike_export_now["rebuy_price"] == pytest.approx(2.55)

    def test_ordinary_evening_peak_gains_no_export(self):
        result, inputs = _plan(ORDINARY_EVENING)

        assert not any(s.spike_export_admitted for s in inputs.slots)
        assert not any(
            d.action == PlannerAction.EXPORT_PROACTIVE for d in result.decisions
        )

    def test_high_price_with_a_dearer_interval_still_to_come_does_not_export(self):
        """An outlier price is not enough: buying back later must be cheaper."""
        prices = [(1.10, 1.00), (3.30, 3.00)] + [(0.20, 0.10)] * 30

        _, inputs = _plan(prices)

        assert inputs.slots[0].spike_export_admitted is False
        assert inputs.config.spike_export_now["rebuy_price"] == pytest.approx(3.30)

    def test_spread_without_an_outlier_price_does_not_export(self):
        """Selling 40c against a 15c rebuy is arbitrage, not a spike."""
        prices = [(0.46, 0.40)] + [(0.15, 0.05)] * 30

        result, inputs = _plan(prices)

        assert inputs.slots[0].spike_export_admitted is False
        assert result.decisions[0].action != PlannerAction.EXPORT_PROACTIVE

    def test_forecast_spike_in_a_later_slot_is_never_planned_as_an_export(self):
        """Only the current interval is admitted, so nothing charges to feed it."""
        prices = [(0.20, 0.10)] * 6 + [(26.71, 24.23)] + [(0.20, 0.10)] * 20

        result, inputs = _plan(prices)

        assert not any(s.spike_export_admitted for s in inputs.slots)
        assert not any(
            d.action == PlannerAction.EXPORT_PROACTIVE for d in result.decisions
        )

    def test_single_slot_horizon_has_nothing_to_judge_against(self):
        _, inputs = _plan([(26.71, 24.23)])

        assert inputs.config.spike_export_now["rebuy_price"] is None
        assert inputs.slots[0].spike_export_admitted is False

    def test_forecast_spike_adds_no_grid_charging(self):
        """2026-08-05 fixture: a forecast $1.65 slot must not buy an export.

        Admitting later slots made the planner add an overnight charge slot to
        sell into the forecast spike. The plan must match one with the
        admission switched off entirely.
        """
        from unittest.mock import patch

        from custom_components.localshift.engine import core
        from tests.engine import test_spike_event_precharge as aug5

        with_admission = aug5._plan()
        with patch.object(core, "apply_spike_export_admission", lambda *a, **k: None):
            without = aug5._plan()

        assert [d.action for d in with_admission.decisions] == [
            d.action for d in without.decisions
        ]

    def test_same_slot_rule_still_admits_export(self):
        """The existing gate is kept: sell >= buy + margin still exports."""
        from custom_components.localshift.engine.constraints import feasible_actions

        config = OptimizerConfig(
            optimization_mode="self_consumption", export_price_margin=0.10
        )
        slot = _slots([(0.10, 0.30)])[0]

        assert PlannerAction.EXPORT_PROACTIVE in feasible_actions(50.0, slot, config)


# --------------------------------------------------------------------------
# Ordinary days: real captured inputs, nothing may change
# --------------------------------------------------------------------------

CONTROLS = REPO_ROOT / "simulations" / "replay-1120"


class TestOrdinaryDaysAreUnchanged:
    """Evening-peak instants, when feed-in is at its ordinary daily high."""

    @pytest.mark.parametrize(
        "name",
        [
            "2026-10-05T190040",
            "2026-10-06T190040",
            "2026-10-08T190040",
            "2026-10-09T070040",
            "2026-10-09T190040",
            "2026-10-10T190040",
        ],
    )
    def test_plan_is_identical_with_and_without_spike_admission(self, name):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from replay_spike_export import run

        scenario = json.loads((CONTROLS / f"{name}.json").read_text())

        base = run(scenario, "base")
        branch = run(scenario, "branch")

        assert branch["actions"] == base["actions"]
        assert branch["mode"] == base["mode"]
        assert branch["spike_export"]["admitted"] is False
        assert branch["spike_export"]["skip_debounce"] is False

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
        # The dear evening that follows pays several times more than now.
        assert spike["later_spike_sell"] > 3.0
        assert spike["sell"] < spike["spike_floor"]

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
        verdict = inputs.config.spike_export_now
        # The two dear slots that follow are the tail of the same spike: they
        # pay less than now, and the rebuy price is the night after them.
        assert verdict["later_spike_sell"] == pytest.approx(2.26)
        assert verdict["rebuy_price"] == pytest.approx(0.20)

    def test_ordinary_evening_peak_gains_no_export(self):
        result, inputs = _plan(ORDINARY_EVENING)

        assert not any(s.spike_export_admitted for s in inputs.slots)
        assert not any(
            d.action == PlannerAction.EXPORT_PROACTIVE for d in result.decisions
        )

    def test_high_price_with_a_dearer_interval_still_to_come_does_not_export(self):
        """An outlier price is not enough: the next interval pays three times more."""
        prices = [(1.10, 1.00), (3.30, 3.00)] + [(0.20, 0.10)] * 30

        _, inputs = _plan(prices)

        assert inputs.slots[0].spike_export_admitted is False
        verdict = inputs.config.spike_export_now
        assert verdict["later_spike_sell"] == pytest.approx(3.00)
        assert verdict["min_sell"] == pytest.approx(3.00)

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
# A spike that lasts more than one interval (PR #1125 review)
# --------------------------------------------------------------------------


def _roll_through_spike(
    spike: list[tuple[float, float]], mode: str = "self_consumption"
) -> list[tuple[PlannerAction, dict]]:
    """Plan once per 5-minute interval of ``spike``, as the live loop does.

    Each plan sees the rest of the spike as 5-minute slots followed by an
    ordinary night, and starts from the SOC the previous plan left behind.
    Returns each plan's slot-0 action and spike verdict.
    """
    from datetime import datetime, timedelta, timezone

    night = [(0.20, 0.10)] * 30
    start = datetime(2026, 10, 12, 17, 0, tzinfo=timezone(timedelta(hours=11)))
    soc = 90.0
    out = []
    for i in range(len(spike)):
        remaining = spike[i:]
        slots = []
        clock = start + timedelta(minutes=5 * i)
        for idx, (buy, sell) in enumerate(remaining + night):
            minutes = 5 if idx < len(remaining) else 30
            slots.append(
                SlotContext(
                    slot_index=idx,
                    timestamp_iso=clock.isoformat(),
                    slot_interval_minutes=minutes,
                    buy_price=buy,
                    sell_price=sell,
                    solar_kwh=0.0,
                    consumption_kwh=0.5 * minutes / 60.0,
                )
            )
            clock += timedelta(minutes=minutes)
        config = OptimizerConfig(optimization_mode=mode, export_price_margin=0.10)
        result = DPPlanner().plan(
            OptimizerInputs(
                cycle_id=f"roll-{i}", initial_soc_pct=soc, slots=slots, config=config
            )
        )
        assert result.success, result.error_message
        out.append((result.decisions[0].action, dict(config.spike_export_now)))
        soc = result.decisions[0].predicted_soc_pct
    return out


FLAT_SPIKE = [(11.20, 10.00)] * 4
FALLING_SPIKE = [(16.80, 15.00), (13.40, 12.00), (11.20, 10.00), (8.96, 8.00)]
RISING_SPIKE = list(reversed(FALLING_SPIKE))


class TestSpikeLastingSeveralIntervals:
    """The rest of the same spike is not what the energy costs to replace.

    The rebuy price used to be the dearest buy price in the next 12 hours,
    which included the next interval of the spike being sold into. Feed-in is
    about 0.9x general, so any following interval forecast above roughly 87%
    of the current sell price blocked the export, and a spike was held through
    until its last interval.
    """

    @pytest.mark.parametrize("spike", [FLAT_SPIKE, FALLING_SPIKE])
    def test_every_interval_of_a_flat_or_falling_spike_exports(self, spike):
        rolled = _roll_through_spike(spike)

        assert [action for action, _ in rolled] == [
            PlannerAction.EXPORT_PROACTIVE
        ] * len(spike)
        assert all(verdict["admitted"] for _, verdict in rolled)

    def test_falling_spike_is_judged_against_the_night_not_the_next_interval(self):
        _, verdict = _roll_through_spike(FALLING_SPIKE)[0]

        # $15 now, $12 next: the next interval pays less, so it does not block.
        assert verdict["later_spike_sell"] == pytest.approx(12.00)
        # What the energy costs to replace is the night after the spike.
        assert verdict["rebuy_price"] == pytest.approx(0.20)

    def test_rising_spike_waits_for_its_dearest_interval(self):
        """A dearer interval of the same spike still blocks (owner decision 1)."""
        rolled = _roll_through_spike(RISING_SPIKE)

        actions = [action for action, _ in rolled]
        assert PlannerAction.EXPORT_PROACTIVE not in actions[:3]
        assert actions[3] == PlannerAction.EXPORT_PROACTIVE
        assert [verdict["admitted"] for _, verdict in rolled] == [
            False,
            False,
            False,
            True,
        ]
        assert rolled[0][1]["later_spike_sell"] == pytest.approx(15.00)
        assert rolled[0][1]["min_sell"] == pytest.approx(15.00)

    def test_arbitrage_mode_counts_every_interval_as_the_spike(self):
        """The verdict is what lets the state machine skip the export debounce.

        Arbitrage mode exported all four intervals already, but the first
        three were not "admitted", so they kept the 2-minute debounce.
        """
        rolled = _roll_through_spike(FLAT_SPIKE, mode="arbitrage")

        assert [action for action, _ in rolled] == [PlannerAction.EXPORT_PROACTIVE] * 4
        assert all(verdict["admitted"] for _, verdict in rolled)

    def test_dear_interval_after_a_gap_is_still_a_rebuy_price(self):
        """Only the unbroken run after now is "the same spike"."""
        prices = [(1.10, 1.00), (0.20, 0.10), (3.30, 3.00)] + [(0.20, 0.10)] * 30

        _, inputs = _plan(prices)

        verdict = inputs.config.spike_export_now
        assert verdict["later_spike_sell"] is None
        assert verdict["rebuy_price"] == pytest.approx(3.30)
        assert inputs.slots[0].spike_export_admitted is False


# --------------------------------------------------------------------------
# A spike before the demand window: the export is refilled (PR #1125 review)
# --------------------------------------------------------------------------


class TestSpikeBeforeTheDemandWindow:
    """2026-10-09 12:30, SOC 29.8, with a confirmed spike in the current interval.

    The admission can make the plan grid-charge: selling before the demand
    window leaves a gap to the 95% entry target, and the planner refills it.
    What holds is that the refill is cheap against the sale and the target is
    still met, not that no charging is added.
    """

    @staticmethod
    def _run(sell: float, buy: float, arm: str) -> dict:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from replay_spike_export import run

        scenario = json.loads((CONTROLS / "2026-10-09T123040.json").read_text())
        payload = scenario["input"]
        payload["feed_in_forecast"][0]["per_kwh"] = sell
        payload["general_forecast"][0]["per_kwh"] = buy
        payload["feed_in_price"] = sell
        payload["general_price"] = buy
        return run(scenario, arm)

    @pytest.mark.parametrize(("sell", "buy"), [(1.00, 1.15), (10.00, 11.20)])
    def test_export_is_refilled_cheaply_and_the_target_is_still_met(self, sell, buy):
        base = self._run(sell, buy, "base")
        branch = self._run(sell, buy, "branch")

        assert base["actions"][0] == "hold"
        assert branch["actions"][0] == "export_proactive"
        # The export is paid back with grid charging before the window.
        assert branch["charge_slots"] > base["charge_slots"]
        # Each refill slot costs less than a fifth of what the energy sold for.
        assert branch["max_charge_buy"] < sell / 5
        # ...and the plan still reaches the demand-window target.
        assert branch["dw_entry_soc_pct"] >= 95.0
        assert branch["terminal_shortfall_pct"] == 0.0


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


# --------------------------------------------------------------------------
# The kill switch: switch.localshift_spike_discharge_enabled
# --------------------------------------------------------------------------


def _arm(instant: str, arm: str, optimization_mode: str = "self_consumption") -> dict:
    """A 2026-10-12 capture through the real engine under one arm.

    ``base`` is the base branch (the admission code removed), ``branch`` is the
    switch at its default ON, ``switch_off`` is the switch turned OFF through
    the same switch-state resolver production uses.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from replay_spike_export import run

    scenario = json.loads((CAPTURES / f"2026-10-12T{instant}.json").read_text())
    return run(scenario, arm, optimization_mode)


ALL_INSTANTS = ["151530", "152010", "153040", "161040", "162550", "163010", "163040"]
CONFIRMED_SPIKES = ["151530", "153040", "163040"]


class TestKillSwitch:
    """ON = the rule is live. OFF = the base branch, decision for decision."""

    @pytest.mark.parametrize("instant", CONFIRMED_SPIKES)
    def test_switch_on_exports_the_confirmed_spike(self, instant):
        result = _arm(instant, "branch")

        assert result["actions"][0] == "export_proactive"
        spike = result["spike_export"]
        assert spike["enabled"] is True
        assert spike["admitted"] is True
        assert spike["skip_debounce"] is True

    @pytest.mark.parametrize("instant", ALL_INSTANTS)
    def test_switch_off_gives_exactly_the_base_branch_plan(self, instant):
        base = _arm(instant, "base")
        off = _arm(instant, "switch_off")

        # Every slot of the plan, and the mode it resolves to.
        assert off["actions"] == base["actions"]
        assert off["mode"] == base["mode"]
        assert off["dw_entry_soc_pct"] == base["dw_entry_soc_pct"]

    @pytest.mark.parametrize("instant", CONFIRMED_SPIKES)
    def test_switch_off_holds_through_the_confirmed_spike(self, instant):
        """The switch must change something: ON exported these three."""
        off = _arm(instant, "switch_off")

        assert off["actions"][0] == "hold"
        spike = off["spike_export"]
        assert spike["enabled"] is False
        assert spike["admitted"] is False
        assert spike["exporting"] is False
        assert spike["skip_debounce"] is False

    @pytest.mark.parametrize("instant", CONFIRMED_SPIKES)
    def test_switch_off_removes_the_debounce_exemption_in_arbitrage_too(self, instant):
        """Arbitrage exports any positive sell price whatever the switch says,
        but with the switch OFF it is an ordinary export, debounce and all."""
        on = _arm(instant, "branch", "arbitrage")
        off = _arm(instant, "switch_off", "arbitrage")
        base = _arm(instant, "base", "arbitrage")

        assert on["spike_export"]["skip_debounce"] is True
        assert off["actions"] == base["actions"] == on["actions"]
        assert off["actions"][0] == "export_proactive"
        assert off["spike_export"]["enabled"] is False
        assert off["spike_export"]["skip_debounce"] is False

    def test_summary_sensor_says_the_rule_is_disabled(self):
        off = _arm("151530", "switch_off")
        coordinator = MagicMock()
        coordinator.data.solcast_analysis_today = None
        coordinator.data.solcast_analysis_tomorrow = None
        coordinator.data.solar_absent_confidence = 1.0
        coordinator.data.dw_entry_actual_at = None
        coordinator.data.optimizer_summary = {
            "success": True,
            "enabled": True,
            "spike_export": off["spike_export"],
        }
        sensor = OptimizerSummarySensor.__new__(OptimizerSummarySensor)
        sensor.coordinator = coordinator

        attrs = sensor.extra_state_attributes["spike_export"]

        assert attrs["enabled"] is False
        assert attrs["disabled_by"] == "switch.localshift_spike_discharge_enabled"
        assert attrs["admitted"] is False
        assert attrs["skip_debounce"] is False

    def test_planner_ignores_a_spike_when_the_rule_is_disabled(self):
        prices = [(26.71, 24.23), (2.55, 2.26)] + [(0.20, 0.10)] * 30
        config = OptimizerConfig(
            optimization_mode="self_consumption",
            export_price_margin=0.10,
            spike_export_enabled=False,
        )
        inputs = OptimizerInputs(
            cycle_id="test-1120-off",
            initial_soc_pct=90.0,
            slots=_slots(prices),
            config=config,
        )

        result = DPPlanner().plan(inputs)

        assert result.success, result.error_message
        assert not any(s.spike_export_admitted for s in inputs.slots)
        assert result.decisions[0].action != PlannerAction.EXPORT_PROACTIVE
        assert config.spike_export_now["enabled"] is False

    @pytest.mark.parametrize(("switch_on", "expected"), [(True, True), (False, False)])
    def test_switch_state_reaches_the_planner_config(self, switch_on, expected):
        """The live route: switch bridge -> config_options -> OptimizerConfig.
        A key missing from that dict leaves the entity decorative (#965, #969)."""
        from types import SimpleNamespace

        from custom_components.localshift.computation_engine import ComputationEngine
        from custom_components.localshift.const import (
            CONF_SPIKE_EXPORT_ENABLED,
            SWITCH_SPIKE_DISCHARGE_ENABLED,
        )
        from custom_components.localshift.engine.optimizer_runner import (
            _build_optimizer_config,
        )

        asked: list[str] = []

        def switch_state(key: str) -> bool:
            asked.append(key)
            return switch_on if key == SWITCH_SPIKE_DISCHARGE_ENABLED else False

        engine = ComputationEngine.__new__(ComputationEngine)
        engine.entry = SimpleNamespace(options={})
        engine._get_switch_state = switch_state

        options = engine._build_optimizer_config_options()

        assert SWITCH_SPIKE_DISCHARGE_ENABLED in asked
        assert options[CONF_SPIKE_EXPORT_ENABLED] is expected
        config = _build_optimizer_config(SimpleNamespace(), options)
        assert config.spike_export_enabled is expected

    def test_default_is_on(self):
        """Read, not chosen here: the rule ships enabled because the switch
        already defaulted ON. Changing either default is an owner decision."""
        from types import SimpleNamespace

        from custom_components.localshift.const import (
            SWITCH_DEFAULTS,
            SWITCH_SPIKE_DISCHARGE_ENABLED,
        )
        from custom_components.localshift.engine.optimizer_runner import (
            _build_optimizer_config,
        )

        assert SWITCH_DEFAULTS[SWITCH_SPIKE_DISCHARGE_ENABLED] is True
        assert OptimizerConfig().spike_export_enabled is True
        assert _build_optimizer_config(SimpleNamespace(), {}).spike_export_enabled

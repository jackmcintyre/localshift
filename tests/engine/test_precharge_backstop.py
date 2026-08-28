"""Tests for the pre-charge execution backstop (2026-07-27 incident).

Live incident 2026-07-27: the 15:00-21:00 demand window was entered at 64.0% SOC
against a 95% target (31.0 points short). The DP planned the pre-charge correctly —
``first_charge=NOW`` on plan after plan from 09:40 onwards, with prices 0.13-0.16
against a $0.20 ``max_precharge_price`` ceiling, so price was never the blocker. What
failed was *execution*: the #622-replacement decision token only grants a mode decision
when the price/spike/DW/floor fingerprint changes (~once per 5-minute Amber tick), and
the DP's slot-0 action oscillates hold↔charge on every replan because the cheapest
first-charge slot drifts forward. At nearly every grant instant the fresh plan's slot-0
action was "hold", so the wanted charge was recorded in ``debug_plan_mode_pending``
dozens of times and committed zero times. Counterfactual replay committing the wanted
mode reaches 94.9%.

``OptimizerFacade._precharge_backstop_mode`` is the executor-side backstop: when the
plan already contains a pre-DW grid charge, the live SOC is below the #885 hard
DW-target floor, the current slot is inside the urgency window and the projected
shortfall is material, BOOST_CHARGING is force-committed regardless of the token. Every
gate reuses an existing engine decision (notably ``hard_target_floor``, which is already
dormant whenever pre-charge is not required), so the backstop never invents charge
intent — it only executes intent the DP already expressed.

The per-gate inertness matrix lives with the incident replay in
``tests/state/test_precharge_execution_replay.py::TestPrechargeBackstop``. This module
covers the arithmetic and the degradation paths: which shortfall measure wins, the
threshold's strictness, and the never-raise / dormant-on-legacy-config guarantees.

``TestRunwayBackstop`` (and below) cover the fast-follow found live 2026-07-28: because
that backstop is gated on ``hard_target_floor``, and the solver deliberately suppresses
the floor on any day solar looks sufficient, the backstop is fully dormant on exactly the
days a late cloud event can strand the battery. ``_runway_backstop_mode`` is the second
arm, gated on physical runway slack instead of the floor.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.util import dt as dt_util

from custom_components.localshift.const import (
    DEFAULT_PRECHARGE_RUNWAY_MARGIN_MIN,
    BatteryMode,
)
from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.optimizer_facade import (
    PRECHARGE_BACKSTOP_SHORTFALL_PCT,
    PRECHARGE_RUNWAY_HYSTERESIS_MIN,
    OptimizerFacade,
)
from custom_components.localshift.engine.types import OptimizerConfig, OptimizerResult

# The 14:30 line of the evidence pack: SOC 62.2%, price 11.6 c, plan projects a 72.6%
# DW entry against a 95% target, and the DP has a pre-DW grid charge at slot 4.
CURRENT_SLOT_IDX = 4
TERMINAL_PENALTY_IDX = 10

# --- 2026-07-28 live observation, the incident this module's runway tests encode ------
# 13:38: 59.1% SOC against a 95% target with 82 minutes to the 15:00 demand window. The
# hard DW-target floor was suppressed (solar looked sufficient), so #901's backstop was
# dormant and the recovery was a MANUAL select.localshift_battery_mode override.
LIVE_SOC_PCT = 59.1
LIVE_TARGET_PCT = 95.0
LIVE_MINUTES_TO_DW = 82.0
RUNWAY_MARGIN_MIN = 15.0


def _live_runway_slack_min() -> float:
    """The live slack, scored by the SHIPPED model rather than a napkin rate.

    This deliberately calls ``DPPlanner._boost_minutes_to_close_gap`` instead of
    re-deriving ``gap / (rate * efficiency)`` here. A local duplicate is exactly how this
    module previously came to assert the wrong thing twice over: it used the NAMEPLATE
    PW3 rate (5.0 kW / 13.5 kWh = 37 %/h, efficiency dropped) for 23.8 minutes of slack,
    where the shipped flat formula gave 18.8 and the engine's own tapered charge model
    gives ~4.0 — because a 59.1% -> 95% pre-charge spends most of its time above the
    80% CV-taper knee. Deriving it means no change to the model, the constants, or the
    capacity can leave this module agreeing with a formula the DP does not use.
    """
    slack = LIVE_MINUTES_TO_DW - (
        DPPlanner._boost_minutes_to_close_gap(
            OptimizerConfig(demand_window_target_soc_pct=LIVE_TARGET_PCT), LIVE_SOC_PCT
        )
        or 0.0
    )
    return slack


LIVE_RUNWAY_SLACK_MIN = _live_runway_slack_min()


def _config(**overrides: Any) -> OptimizerConfig:
    """A strict-mode config with the #885 hard floor live and the urgency window open."""
    config = OptimizerConfig(
        demand_window_target_soc_pct=95.0,
        max_precharge_price=0.20,
    )
    config.hard_target_floor = 95.0
    config.urgency_window_start_idx = 0
    # Solver-derived DW-entry slot index, published on the config for the backstop.
    config.terminal_penalty_idx = TERMINAL_PENALTY_IDX
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _data(**overrides: Any) -> CoordinatorData:
    """A frozen evaluation mid-morning: token withheld, backstop permitted."""
    data = CoordinatorData()
    data.active_mode = BatteryMode.SELF_CONSUMPTION
    data.soc = 62.2
    data.general_price = 0.116
    data.demand_window_active = False
    # #622 token frozen — this is the pathology the backstop exists for.
    data.mode_decision_allowed = False
    data.mode_backstop_allowed = True
    data.optimizer_decisions = [
        {"slot_index": 4, "action": "charge_grid_normal", "grid_charge": True},
    ]
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


def _result(shortfall: float = 22.4, dw_entry: float = 72.6) -> Any:
    return SimpleNamespace(terminal_shortfall_pct=shortfall, dw_entry_soc_pct=dw_entry)


def _run(
    facade: OptimizerFacade,
    data: CoordinatorData,
    config: OptimizerConfig,
    result: Any,
) -> None:
    """Drive ``_assign_active_mode`` with a hold-wanting slot-0 plan.

    The apply plan is pinned to SELF_CONSUMPTION on purpose: that is exactly what the
    token sampled all day on 2026-07-27 while the plan's later slots wanted to charge.
    """
    with (
        patch(
            "custom_components.localshift.engine.optimizer_facade.OptimizerSafetyGate"
        ) as mock_gate,
        patch(
            "custom_components.localshift.engine.optimizer_facade._current_slot_debug_info",
            return_value=(CURRENT_SLOT_IDX, True, "14:30", "13:00", 60.0),
        ),
        patch(
            "custom_components.localshift.engine.optimizer_facade._derive_runtime_apply_plan",
            return_value={
                "battery_mode": BatteryMode.SELF_CONSUMPTION.value,
                "action": "hold",
            },
        ),
    ):
        mock_gate.return_value.check_admission.return_value = SimpleNamespace(
            allowed=True, block_reason=None
        )
        facade._assign_active_mode(data, result, config, {})


class TestPrechargeBackstop:
    """FIX 2: the plan's pre-charge is executed even when the token never grants."""

    def test_backstop_forces_boost_when_token_frozen(self, caplog) -> None:
        facade = OptimizerFacade()
        data = _data()

        with caplog.at_level("WARNING"):
            _run(facade, data, _config(), _result())

        assert data.active_mode == BatteryMode.BOOST_CHARGING
        assert data.debug_mode_source == "precharge_backstop"
        assert data.optimizer_precharge_backstop_active is True
        # The commit is a real decision: nothing is left pending.
        assert data.debug_plan_mode_pending is None
        # The operator-visible artefact — a fired backstop always means the token
        # path failed to land in time and deserves post-hoc review.
        assert "PRE-CHARGE BACKSTOP" in caplog.text

    def test_shortfall_takes_the_worse_of_both_published_measures(self) -> None:
        """The two measures diverged in the 2026-07-27 log; the max must win."""
        facade = OptimizerFacade()
        data = _data()
        # terminal_shortfall_pct reads clean, but the projected DW entry is 30 short.
        _run(facade, data, _config(), _result(shortfall=0.0, dw_entry=65.0))

        assert data.active_mode == BatteryMode.BOOST_CHARGING

        # ... and symmetrically, with no dw_entry projection published at all.
        data2 = _data()
        _run(
            facade,
            data2,
            _config(),
            SimpleNamespace(terminal_shortfall_pct=22.4, dw_entry_soc_pct=None),
        )
        assert data2.active_mode == BatteryMode.BOOST_CHARGING

    def test_threshold_is_a_strict_inequality(self) -> None:
        """Exactly at the threshold is not "material" — it must not fire."""
        facade = OptimizerFacade()
        data = _data()
        _run(
            facade,
            data,
            _config(),
            _result(
                shortfall=PRECHARGE_BACKSTOP_SHORTFALL_PCT,
                dw_entry=95.0 - PRECHARGE_BACKSTOP_SHORTFALL_PCT,
            ),
        )

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False

    def test_backstop_check_never_raises(self) -> None:
        """A malformed plan/result must degrade to "no backstop", not kill the cycle."""
        facade = OptimizerFacade()
        data = _data(optimizer_decisions=["not-a-dict"])

        assert (
            facade._precharge_backstop_mode(
                data, _result(), _config(), CURRENT_SLOT_IDX
            )
            is None
        )

    def test_legacy_config_without_terminal_penalty_idx_is_inert(self) -> None:
        """Guards the parallel-safety read: a config predating the new field."""
        facade = OptimizerFacade()
        data = _data()
        config = _config()
        del config.terminal_penalty_idx

        assert (
            facade._precharge_backstop_mode(data, _result(), config, CURRENT_SLOT_IDX)
            is None
        )

    def test_mock_config_without_backstop_permission_is_inert(self) -> None:
        """Existing frozen-path tests pass MagicMocks — permission gate short-circuits."""
        facade = OptimizerFacade()
        data = _data(mode_backstop_allowed=False)

        assert (
            facade._precharge_backstop_mode(
                data, MagicMock(), MagicMock(), CURRENT_SLOT_IDX
            )
            is None
        )


class TestBackstopReachability:
    """The gates the backstop reads must actually exist in production."""

    def test_terminal_penalty_idx_is_published_on_a_real_config(self) -> None:
        """The whole backstop hangs off this one field.

        It was a local in ``DPPlanner._solve`` and never attached to the config, so
        ``_backstop_urgency_window`` read None on every live cycle and the backstop
        was unreachable — dead code that only ever fired in tests that fabricated the
        attribute with ``setattr``. Assert against a REAL OptimizerConfig, which is
        what the fabricating tests could not catch.
        """
        config = OptimizerConfig()

        assert hasattr(config, "terminal_penalty_idx")
        # Constructible as a field, not merely settable as an ad-hoc attribute.
        assert OptimizerConfig(terminal_penalty_idx=10).terminal_penalty_idx == 10

    def test_solver_publishes_terminal_penalty_idx_next_to_its_siblings(self) -> None:
        """It is written by the same _solve pass that writes urgency_window_start_idx."""
        import inspect

        from custom_components.localshift.engine import core

        source = inspect.getsource(core.DPPlanner._solve)
        assert "config.terminal_penalty_idx = terminal_penalty_idx" in source


class TestBackstopPriceAvailability:
    """Issue #330: an unavailable price entity reads 0.0, not None."""

    def test_backstop_inert_when_prices_are_unavailable(self) -> None:
        """A missing Amber sensor sets general_price = 0.0, so the pre-charge price
        ceiling (`0.0 > 0.20` is False) waves the backstop straight through at a
        completely unknown real price. "Grid charging decisions will be deferred"
        applies hardest to a forced charge."""
        facade = OptimizerFacade()
        data = _data(prices_available=False, general_price=0.0)

        _run(facade, data, _config(), _result())

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False

    def test_backstop_still_fires_when_prices_are_available(self) -> None:
        """Negative twin: the new gate must not shut the backstop off wholesale."""
        facade = OptimizerFacade()
        data = _data(prices_available=True)

        _run(facade, data, _config(), _result())

        assert data.active_mode == BatteryMode.BOOST_CHARGING


class TestBackstopRelease:
    """The forced BOOST must be released as ungated-ly as it was committed."""

    def test_forced_boost_is_released_when_its_conditions_clear(self) -> None:
        """The backstop commits WITHOUT a fingerprint change, which breaks the
        property every other commit has: that the decision context just moved and
        will move again. Once ``soc >= hard_target_floor`` the backstop returns None
        and the ordinary frozen path PINS the boost — so on a flat-price stretch (or
        a stuck price sensor) the battery force-charges at full import price until
        the demand window flips."""
        facade = OptimizerFacade()
        data = _data()

        _run(facade, data, _config(), _result())
        assert data.active_mode == BatteryMode.BOOST_CHARGING

        # SOC now at the floor: the backstop's conditions have cleared. The token is
        # STILL frozen and the price context is unchanged — nothing else can release.
        data.soc = 96.0
        _run(facade, data, _config(), _result())

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.debug_mode_source == "precharge_backstop_release"
        assert data.optimizer_precharge_backstop_active is False

    def test_release_happens_once_and_does_not_re_grant(self) -> None:
        """The latch is one-shot: subsequent frozen ticks stay frozen."""
        facade = OptimizerFacade()
        data = _data()
        _run(facade, data, _config(), _result())
        data.soc = 96.0
        _run(facade, data, _config(), _result())

        data.active_mode = BatteryMode.SPIKE_DISCHARGE
        _run(facade, data, _config(), _result())

        # Frozen path pinned it — the release did not fire a second time.
        assert data.active_mode == BatteryMode.SPIKE_DISCHARGE

    def test_release_is_confined_to_the_lock(self) -> None:
        """Out of lock the backstop returns None because PERMISSION is closed, not
        because its conditions cleared. Releasing there would be an ungated commit
        from the very path the permission flag exists to keep inert — and would burn
        the latch, so the real in-lock release never happens."""
        facade = OptimizerFacade()
        data = _data()
        _run(facade, data, _config(), _result())
        assert data.active_mode == BatteryMode.BOOST_CHARGING

        # An out-of-lock recompute: same conditions, permission closed.
        data.mode_backstop_allowed = False
        _run(facade, data, _config(), _result())
        assert data.active_mode == BatteryMode.BOOST_CHARGING
        assert data.debug_mode_source != "precharge_backstop_release"

        # The latch survived, so the genuine in-lock release still fires.
        data.mode_backstop_allowed = True
        data.soc = 96.0
        _run(facade, data, _config(), _result())
        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.debug_mode_source == "precharge_backstop_release"

    def test_no_release_when_an_ordinary_grant_already_moved_the_mode(self) -> None:
        """Nothing to release if the boost is no longer what is committed."""
        facade = OptimizerFacade()
        data = _data()
        _run(facade, data, _config(), _result())
        assert data.active_mode == BatteryMode.BOOST_CHARGING

        # An ordinary token grant lands and moves the mode on.
        data.soc = 96.0
        data.active_mode = BatteryMode.SELF_CONSUMPTION
        _run(facade, data, _config(), _result())

        assert data.debug_mode_source != "precharge_backstop_release"


class TestBackstopPlanIntegrity:
    """It executes intent the DP expressed — and only what the DP expressed."""

    def test_decision_without_a_slot_index_is_not_counted_as_pre_dw_charge(
        self,
    ) -> None:
        """``slot_index`` defaulted to 0, so a malformed decision read as a pre-DW
        charge and armed a forced boost off a plan that never located itself."""
        facade = OptimizerFacade()
        data = _data(
            optimizer_decisions=[{"action": "charge_grid_normal", "grid_charge": True}]
        )

        _run(facade, data, _config(), _result())

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False


class TestPlanChargeGrantIsOneDirectional:
    """A token granted because the plan wanted to START charging may only charge."""

    @staticmethod
    def _run_with_plan(facade, data, config, plan_mode: BatteryMode) -> None:
        with (
            patch(
                "custom_components.localshift.engine.optimizer_facade.OptimizerSafetyGate"
            ) as mock_gate,
            patch(
                "custom_components.localshift.engine.optimizer_facade._current_slot_debug_info",
                return_value=(CURRENT_SLOT_IDX, True, "14:30", "13:00", 60.0),
            ),
            patch(
                "custom_components.localshift.engine.optimizer_facade._derive_runtime_apply_plan",
                return_value={"battery_mode": plan_mode.value, "action": "x"},
            ),
        ):
            mock_gate.return_value.check_admission.return_value = SimpleNamespace(
                allowed=True, block_reason=None
            )
            facade._assign_active_mode(data, _result(), config, {})

    def test_plan_charge_grant_cannot_commit_a_discharge(self) -> None:
        """The trigger is one-directional in what ARMS it; it must be one-directional
        in what it COMMITS too. Otherwise a grant raised because the plan wanted to
        charge is spent on whatever slot-0 drifted to — force-discharging the battery
        on a token granted to charge it."""
        facade = OptimizerFacade()
        # Backstop off, so only the token path is under test.
        data = _data(mode_backstop_allowed=False)
        data.mode_decision_allowed = True
        data.mode_decision_plan_charge_only = True

        self._run_with_plan(facade, data, _config(), BatteryMode.SPIKE_DISCHARGE)

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        # The disagreement stays visible rather than being spent wrongly.
        assert data.debug_plan_mode_pending == BatteryMode.SPIKE_DISCHARGE.value

    def test_plan_charge_grant_commits_a_charge(self) -> None:
        """Negative twin: the grant is still spendable on what it was raised for."""
        facade = OptimizerFacade()
        data = _data(mode_backstop_allowed=False)
        data.mode_decision_allowed = True
        data.mode_decision_plan_charge_only = True

        self._run_with_plan(facade, data, _config(), BatteryMode.GRID_CHARGING)

        assert data.active_mode == BatteryMode.GRID_CHARGING

    def test_ordinary_grant_is_unrestricted(self) -> None:
        """A price/spike/DW/floor grant may still commit any mode."""
        facade = OptimizerFacade()
        data = _data(mode_backstop_allowed=False)
        data.mode_decision_allowed = True
        data.mode_decision_plan_charge_only = False

        self._run_with_plan(facade, data, _config(), BatteryMode.SPIKE_DISCHARGE)

        assert data.active_mode == BatteryMode.SPIKE_DISCHARGE


# ---------------------------------------------------------------------------
# Runway backstop (fast-follow to #901) — the solar-suppressed-floor blind spot
# ---------------------------------------------------------------------------


def _runway_config(**overrides: Any) -> OptimizerConfig:
    """A solar-sufficient day: the hard floor is dormant, suppressed BY SOLAR.

    This is the exact shape ``_compute_hard_target_floor`` produces whenever
    ``check_global_solar_sufficiency`` passes — no floor to measure against, which is
    what makes #901's backstop inert here.
    """
    config = _config(
        demand_window_target_soc_pct=LIVE_TARGET_PCT,
        hard_target_floor=None,
        hard_floor_suppressed_by_solar=True,
        precharge_runway_slack_min=LIVE_RUNWAY_SLACK_MIN,
        precharge_runway_margin_min=RUNWAY_MARGIN_MIN,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _runway_data(**overrides: Any) -> CoordinatorData:
    """The 2026-07-28 live state: 59.1% SOC and a plan with NO pre-DW grid charge.

    The empty decision list is the point. On a solar-sufficient day the DP schedules no
    grid charge at all, so #901's ``_count_pre_dw_charge_slots`` gate — which exists so
    that backstop never invents intent — can never be satisfied. That absence is the gap
    the runway arm covers.
    """
    fields: dict[str, Any] = {"soc": LIVE_SOC_PCT, "optimizer_decisions": []}
    fields.update(overrides)
    return _data(**fields)


def _clean_result() -> Any:
    """What the solver publishes on a solar-sufficient day: no projected shortfall.

    ``terminal_shortfall_pct`` reads 0 because the plan believes solar closes the gap —
    the same blind spot one level down, which is why the runway arm reads the LIVE gap.
    """
    return SimpleNamespace(terminal_shortfall_pct=0.0, dw_entry_soc_pct=LIVE_TARGET_PCT)


class TestRunwayBackstop:
    """#901's backstop is dormant whenever solar suppresses the hard floor.

    ``_compute_hard_target_floor`` returns None on any day the solar-sufficiency check
    passes (deliberately — don't fight #816/#849), and #901 returns None whenever the
    floor is None. So the backstop built to prevent a repeat of 2026-07-27 does not exist
    on a sunny-looking day. Found live 2026-07-28 at 59.1% SOC with 82 minutes to the
    demand window; recovered by a manual override.
    """

    def test_fires_when_solar_suppressed_floor_leaves_no_runway(self, caplog) -> None:
        """The money case: floor None + suppressed-by-solar + clean shortfall + no
        planned charge slot + slack under margin ⇒ forced BOOST."""
        facade = OptimizerFacade()
        data = _runway_data()
        # Slack has collapsed below the margin — a cloud event ate the runway.
        config = _runway_config(precharge_runway_slack_min=RUNWAY_MARGIN_MIN - 3.0)

        with caplog.at_level("WARNING"):
            _run(facade, data, config, _clean_result())

        assert data.active_mode == BatteryMode.BOOST_CHARGING
        assert data.debug_mode_source == "runway_backstop"
        assert data.optimizer_precharge_backstop_active is True
        assert "RUNWAY BACKSTOP" in caplog.text
        # Distinguishable from #901 in logs and telemetry, not a re-use of its identity.
        assert "PRE-CHARGE BACKSTOP" not in caplog.text

    def test_the_901_backstop_returns_none_on_this_exact_input(self) -> None:
        """The gap this fix closes, asserted directly rather than implied.

        Same data, same config, same slot — #901's arm is inert because it is gated on
        ``hard_target_floor``, which the solver set to None.
        """
        facade = OptimizerFacade()

        assert (
            facade._precharge_backstop_mode(
                _runway_data(),
                _clean_result(),
                _runway_config(precharge_runway_slack_min=RUNWAY_MARGIN_MIN - 3.0),
                CURRENT_SLOT_IDX,
            )
            is None
        )

    def test_fires_at_todays_actual_slack(self) -> None:
        """Issue #905: the raised taper knee makes this state no longer starved.

        13:38 on 2026-07-28 — 59.1% SOC, 95% target, 82 minutes to the DW — previously
        left ~4 minutes of slack (with the 80% knee) against a 15-minute margin, so the
        backstop armed. The raised knee (90%, matching measured hardware) gives ~16
        minutes of slack, so the arm correctly stays shut. The test asserts the new
        correct behaviour: no spurious boost when the model is accurate.

        The old behaviour (arm firing) was the *bug* — the over-aggressive taper made
        the DP believe it couldn't reach target when it actually could.
        """
        facade = OptimizerFacade()
        data = _runway_data()

        # With the 90% knee the model now sees enough runway — arm must stay dormant.
        assert LIVE_RUNWAY_SLACK_MIN >= RUNWAY_MARGIN_MIN
        _run(facade, data, _runway_config(), _clean_result())

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False

    def test_does_not_fire_with_runway_to_spare(self) -> None:
        """The anti-#816 assertion: a day with runway to spare is left alone.

        Insurance against the runway running out — not a second opinion on the solar
        forecast. Scored against slack directly rather than a particular live state, so
        the assertion survives recalibration of the charge model.
        """
        facade = OptimizerFacade()
        data = _runway_data()

        _run(
            facade,
            data,
            _runway_config(precharge_runway_slack_min=RUNWAY_MARGIN_MIN + 10.0),
            _clean_result(),
        )

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False

    def test_dormant_when_the_floor_is_none_for_a_non_solar_reason(self) -> None:
        """Policy/structural Nones stay exactly as dormant as they are under #901.

        ``allow_dw_entry_under_target``, "no demand window", a legacy config without the
        field — all produce ``hard_target_floor is None`` too, and none of them mean "a
        solar forecast is the only thing standing between us and a hard floor".
        """
        facade = OptimizerFacade()
        data = _runway_data()
        config = _runway_config(
            hard_floor_suppressed_by_solar=False,
            precharge_runway_slack_min=-30.0,
        )

        _run(facade, data, config, _clean_result())

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False

    def test_legacy_config_without_the_suppression_flag_is_inert(self) -> None:
        """Parallel-safety read, mirroring the #901 terminal_penalty_idx guard."""
        facade = OptimizerFacade()
        config = _runway_config(precharge_runway_slack_min=-30.0)
        del config.hard_floor_suppressed_by_solar

        assert (
            facade._runway_backstop_mode(_runway_data(), config, CURRENT_SLOT_IDX)
            is None
        )

    def test_margin_zero_is_a_kill_switch(self) -> None:
        """0 disables the arm outright — not "fire only at zero slack"."""
        facade = OptimizerFacade()
        data = _runway_data()
        config = _runway_config(
            precharge_runway_margin_min=0.0,
            # Deeply negative: the target is already unreachable at boost rate, the most
            # extreme input the arm can see. Still nothing, because the operator said no.
            precharge_runway_slack_min=-120.0,
        )

        _run(facade, data, config, _clean_result())

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False

    def test_margin_zero_also_disables_the_hysteresis_band(self) -> None:
        """The kill switch is checked BEFORE the hysteresis widening, so a margin of 0
        cannot leave a hysteresis-wide band armed once the latch is set."""
        facade = OptimizerFacade()
        facade._backstop_holding = True

        assert (
            facade._runway_backstop_mode(
                _runway_data(),
                _runway_config(
                    precharge_runway_margin_min=0.0, precharge_runway_slack_min=1.0
                ),
                CURRENT_SLOT_IDX,
            )
            is None
        )

    def test_does_not_fire_when_the_live_gap_is_immaterial(self) -> None:
        """Same strict-inequality materiality threshold #901 uses, on the LIVE gap."""
        facade = OptimizerFacade()
        data = _runway_data(soc=LIVE_TARGET_PCT - PRECHARGE_BACKSTOP_SHORTFALL_PCT)
        config = _runway_config(precharge_runway_slack_min=-30.0)

        _run(facade, data, config, _clean_result())

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False

    def test_does_not_fire_above_the_precharge_price_ceiling(self) -> None:
        """Reuses the operator's existing pre-charge ceiling, unchanged."""
        facade = OptimizerFacade()
        data = _runway_data(general_price=0.25)
        config = _runway_config(precharge_runway_slack_min=-30.0)

        _run(facade, data, config, _clean_result())

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False

    def test_does_not_fire_out_of_lock(self) -> None:
        """Same in-lock confinement as #901: the out-of-lock recompute in
        ``coordinator.async_recompute_and_evaluate`` must never command hardware."""
        facade = OptimizerFacade()

        assert (
            facade._runway_backstop_mode(
                _runway_data(mode_backstop_allowed=False),
                _runway_config(precharge_runway_slack_min=-30.0),
                CURRENT_SLOT_IDX,
            )
            is None
        )

    def test_a_live_hard_floor_no_longer_silences_this_arm(self) -> None:
        """Inverted 2026-08-25. It used to assert "one arm per cycle, never both".

        The exclusion was real but keyed on the wrong quantity. ``_assign_active_mode``
        only calls this arm after ``_precharge_backstop_mode`` has returned None, so one
        arm per cycle is already guaranteed by the call site — and standing down a second
        time on floor LIVENESS turned "#901 declined" into "nobody acts", which is the
        band 2026-08-25 fell into. Ordering is asserted end-to-end instead, in
        ``TestBackstopDeadZone.test_901_still_owns_the_cycle_when_it_qualifies``.
        """
        facade = OptimizerFacade()
        config = _runway_config(
            hard_target_floor=95.0, precharge_runway_slack_min=-30.0
        )

        assert (
            facade._runway_backstop_mode(_runway_data(), config, CURRENT_SLOT_IDX)
            is BatteryMode.BOOST_CHARGING
        )

    def test_dormant_without_a_slack_reading(self) -> None:
        """None slack means "not interpretable" (no DW, unparseable timestamps,
        degenerate battery constants) — never "zero slack"."""
        facade = OptimizerFacade()

        assert (
            facade._runway_backstop_mode(
                _runway_data(),
                _runway_config(precharge_runway_slack_min=None),
                CURRENT_SLOT_IDX,
            )
            is None
        )

    def test_outside_the_urgency_window_is_inert(self) -> None:
        """Same window scope as #901: at/after the DW-entry slot the pre-charge is moot."""
        facade = OptimizerFacade()

        assert (
            facade._runway_backstop_mode(
                _runway_data(),
                _runway_config(precharge_runway_slack_min=-30.0),
                TERMINAL_PENALTY_IDX,
            )
            is None
        )

    def test_runway_check_never_raises(self) -> None:
        """A malformed SOC must degrade to "no backstop", not kill the cycle."""
        facade = OptimizerFacade()

        assert (
            facade._runway_backstop_mode(
                _runway_data(soc="not-a-number"),
                _runway_config(precharge_runway_slack_min=-30.0),
                CURRENT_SLOT_IDX,
            )
            is None
        )


class TestRunwayBackstopHysteresis:
    """Boost charging holds slack roughly constant, so a bare threshold chatters.

    While boosting, the gap closes at the same rate the runway shortens — the property
    that makes the arm safe also parks slack right at the margin. The release therefore
    requires a genuine recovery past ``margin + max(PRECHARGE_RUNWAY_HYSTERESIS_MIN,
    precharge_runway_quantum_min)``.

    The widening keys off ``_runway_holding``, this arm's OWN latch, not the shared
    ``_backstop_holding`` — see ``test_the_901_latch_does_not_widen_the_runway_arm``.
    """

    @staticmethod
    def _fire(facade: OptimizerFacade, data: CoordinatorData) -> None:
        _run(
            facade,
            data,
            _runway_config(precharge_runway_slack_min=RUNWAY_MARGIN_MIN - 3.0),
            _clean_result(),
        )

    def test_holds_inside_the_hysteresis_band(self) -> None:
        """margin < slack < margin + hysteresis ⇒ keep charging."""
        facade = OptimizerFacade()
        data = _runway_data()
        self._fire(facade, data)
        assert data.active_mode == BatteryMode.BOOST_CHARGING

        # Recovered past the margin, but not past the release band.
        in_band = RUNWAY_MARGIN_MIN + PRECHARGE_RUNWAY_HYSTERESIS_MIN / 2.0
        _run(
            facade,
            data,
            _runway_config(precharge_runway_slack_min=in_band),
            _clean_result(),
        )

        assert data.active_mode == BatteryMode.BOOST_CHARGING
        assert data.debug_mode_source == "runway_backstop"
        assert data.optimizer_precharge_backstop_active is True

    def test_releases_exactly_at_the_top_of_the_band(self) -> None:
        """slack >= margin + hysteresis ⇒ release, through #901's release path."""
        facade = OptimizerFacade()
        data = _runway_data()
        self._fire(facade, data)

        released = RUNWAY_MARGIN_MIN + PRECHARGE_RUNWAY_HYSTERESIS_MIN
        _run(
            facade,
            data,
            _runway_config(precharge_runway_slack_min=released),
            _clean_result(),
        )

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.debug_mode_source == "precharge_backstop_release"
        assert data.optimizer_precharge_backstop_active is False

    def test_release_is_one_shot(self) -> None:
        """The latch is spent: later frozen ticks stay frozen (mirrors #901)."""
        facade = OptimizerFacade()
        data = _runway_data()
        self._fire(facade, data)
        _run(
            facade,
            data,
            _runway_config(
                precharge_runway_slack_min=RUNWAY_MARGIN_MIN
                + PRECHARGE_RUNWAY_HYSTERESIS_MIN
            ),
            _clean_result(),
        )

        data.active_mode = BatteryMode.SPIKE_DISCHARGE
        _run(
            facade,
            data,
            _runway_config(
                precharge_runway_slack_min=RUNWAY_MARGIN_MIN
                + PRECHARGE_RUNWAY_HYSTERESIS_MIN
            ),
            _clean_result(),
        )

        assert data.active_mode == BatteryMode.SPIKE_DISCHARGE

    def test_the_closing_gap_also_releases(self) -> None:
        """The other release path: the battery reached target, so the arm's premise is
        gone regardless of what the runway reads."""
        facade = OptimizerFacade()
        data = _runway_data()
        self._fire(facade, data)
        assert data.active_mode == BatteryMode.BOOST_CHARGING

        data.soc = LIVE_TARGET_PCT - 1.0
        _run(
            facade,
            data,
            _runway_config(precharge_runway_slack_min=RUNWAY_MARGIN_MIN - 3.0),
            _clean_result(),
        )

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.debug_mode_source == "precharge_backstop_release"

    def test_the_901_latch_does_not_widen_the_runway_arm(self) -> None:
        """Hysteresis is RELEASE hysteresis — it may only widen an arm already holding.

        ``_backstop_holding`` is shared: whichever arm fires sets it. Keying the runway
        arm's widening off that shared latch meant a hold opened by #901 earlier in the
        day silently raised this arm's *arming* threshold from ``margin`` to
        ``margin + hysteresis``, so it force-committed a full-price grid charge at a
        slack its own threshold says to ignore.
        """
        spare = RUNWAY_MARGIN_MIN + PRECHARGE_RUNWAY_HYSTERESIS_MIN - 5.0
        assert spare > RUNWAY_MARGIN_MIN

        fresh = OptimizerFacade()
        fresh_data = _runway_data()
        _run(
            fresh,
            fresh_data,
            _runway_config(precharge_runway_slack_min=spare),
            _clean_result(),
        )
        assert fresh_data.active_mode == BatteryMode.SELF_CONSUMPTION

        # Same slack, but #901 fired earlier this cycle and left its latch set.
        inherited = OptimizerFacade()
        inherited._backstop_holding = True
        inherited_data = _runway_data()
        _run(
            inherited,
            inherited_data,
            _runway_config(precharge_runway_slack_min=spare),
            _clean_result(),
        )

        assert inherited_data.active_mode == fresh_data.active_mode
        assert inherited_data.optimizer_precharge_backstop_active is False

    def test_the_band_scales_with_the_slot_quantum(self) -> None:
        """A 30-minute slot 0 sawtooths the slack by 30 min; a 10-min band cannot hold it.

        The published slack's time term steps at slot boundaries while its SOC gap term
        moves continuously, so the reading drifts up across a slot and drops by one slot
        width at each boundary. Sized against the constant alone, the arm released and
        re-armed at every boundary through the whole pre-charge window.
        """
        facade = OptimizerFacade()
        data = _runway_data()
        self._fire(facade, data)
        assert data.active_mode == BatteryMode.BOOST_CHARGING

        # Inside a 30-minute sawtooth, well outside the 10-minute constant.
        quantum = 30.0
        _run(
            facade,
            data,
            _runway_config(
                precharge_runway_quantum_min=quantum,
                precharge_runway_slack_min=RUNWAY_MARGIN_MIN + quantum - 1.0,
            ),
            _clean_result(),
        )

        assert data.active_mode == BatteryMode.BOOST_CHARGING
        assert data.debug_mode_source == "runway_backstop"

    def test_a_transient_block_does_not_reset_the_band(self) -> None:
        """A price tick above the ceiling suppresses the charge but keeps the hold.

        Clearing the latch on a transient gate resets the threshold to narrow, so the arm
        re-arms on the very next tick at a slack it was already holding through — the
        exact chatter the band exists to stop. Amber prices crossing
        ``max_precharge_price`` and back is ordinary, not exotic.
        """
        facade = OptimizerFacade()
        data = _runway_data()
        self._fire(facade, data)
        assert facade._runway_holding is True

        data.general_price = 0.21  # above the 0.20 ceiling
        facade._runway_backstop_mode(
            data,
            _runway_config(precharge_runway_slack_min=RUNWAY_MARGIN_MIN - 3.0),
            CURRENT_SLOT_IDX,
        )
        assert facade._runway_holding is True, "a price blip must not spend the latch"

    def test_the_hard_floor_going_live_does_not_drop_the_hold(self) -> None:
        """The coverage hole between the two arms, closed.

        ``_floor_suppressed_by_solar`` hard-declines the moment ``hard_target_floor``
        goes non-None, while #901 independently declines when the plan's projected
        shortfall is immaterial — and both hold at once in the ordinary case, because a
        live floor makes the DP route a pre-DW charge and publish a clean shortfall.
        Neither arm fires, the forced boost drops for one cycle, and the arm re-fires as
        soon as ``check_global_solar_sufficiency`` flickers back. That check is a
        threshold comparison on accuracy-discounted solar recomputed every 5 minutes, so
        it chatters precisely when solar is marginal — the only regime the arm runs in.
        """
        facade = OptimizerFacade()
        data = _runway_data()
        # The plan carries a real pre-DW charge slot, as it does once a floor is live.
        data.optimizer_decisions = [
            {"slot_index": 4, "action": "charge_grid_normal", "grid_charge": True}
        ]
        self._fire(facade, data)
        assert data.active_mode == BatteryMode.BOOST_CHARGING

        _run(
            facade,
            data,
            _runway_config(
                hard_target_floor=LIVE_TARGET_PCT,
                hard_floor_suppressed_by_solar=False,
                precharge_runway_slack_min=RUNWAY_MARGIN_MIN - 3.0,
            ),
            _clean_result(),
        )

        assert data.active_mode == BatteryMode.BOOST_CHARGING
        assert data.debug_mode_source == "runway_backstop"


class TestRunwayMarginKnob:
    """The margin is a live-tunable number entity, read like every other knob."""

    def test_option_is_read_into_the_config(self) -> None:
        from custom_components.localshift.const import (
            CONF_PRECHARGE_RUNWAY_MARGIN_MIN,
        )
        from custom_components.localshift.engine.optimizer_runner import (
            _build_optimizer_config,
        )

        config = _build_optimizer_config(
            SimpleNamespace(), {CONF_PRECHARGE_RUNWAY_MARGIN_MIN: 45.0}
        )

        assert config.precharge_runway_margin_min == 45.0

    def test_default_applies_when_unset(self) -> None:
        from custom_components.localshift.engine.optimizer_runner import (
            _build_optimizer_config,
        )

        config = _build_optimizer_config(SimpleNamespace(), {})

        assert config.precharge_runway_margin_min == DEFAULT_PRECHARGE_RUNWAY_MARGIN_MIN
        assert DEFAULT_PRECHARGE_RUNWAY_MARGIN_MIN == 15.0

    def test_zero_from_the_options_really_disables_the_arm(self) -> None:
        """End-to-end kill switch: slider at 0 ⇒ the arm cannot fire, at any slack."""
        from custom_components.localshift.const import (
            CONF_PRECHARGE_RUNWAY_MARGIN_MIN,
        )
        from custom_components.localshift.engine.optimizer_runner import (
            _build_optimizer_config,
        )

        built = _build_optimizer_config(
            SimpleNamespace(), {CONF_PRECHARGE_RUNWAY_MARGIN_MIN: 0}
        )
        assert built.precharge_runway_margin_min == 0.0

        config = _runway_config(
            precharge_runway_margin_min=built.precharge_runway_margin_min,
            precharge_runway_slack_min=-999.0,
        )
        facade = OptimizerFacade()
        data = _runway_data()

        _run(facade, data, config, _clean_result())

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False

    def test_the_dataclass_default_tracks_the_const_default(self) -> None:
        """One source of truth: a config built in a test and one built from options
        must agree about the margin, or the tests calibrate against a phantom."""
        assert (
            OptimizerConfig().precharge_runway_margin_min
            == DEFAULT_PRECHARGE_RUNWAY_MARGIN_MIN
        )

    def test_the_slider_reaches_the_engine_on_the_live_path(self) -> None:
        """``entry.options`` is NOT what the optimizer sees.

        ``ComputationEngine._build_optimizer_config_options`` hand-copies each option
        into the dict that reaches ``_build_optimizer_config``, so a knob missing from
        that copy is decorative — the slider moves and the engine keeps the default.
        (``max_pre_charge_price`` is currently in exactly that state.)
        """
        from custom_components.localshift.computation_engine import ComputationEngine
        from custom_components.localshift.const import (
            CONF_PRECHARGE_RUNWAY_MARGIN_MIN,
        )
        from custom_components.localshift.engine.optimizer_runner import (
            _build_optimizer_config,
        )

        engine = ComputationEngine.__new__(ComputationEngine)
        engine.entry = SimpleNamespace(options={CONF_PRECHARGE_RUNWAY_MARGIN_MIN: 45.0})
        engine._get_switch_state = lambda _key: False

        options = engine._build_optimizer_config_options()

        assert options[CONF_PRECHARGE_RUNWAY_MARGIN_MIN] == 45.0
        assert (
            _build_optimizer_config(
                SimpleNamespace(), options
            ).precharge_runway_margin_min
            == 45.0
        )

    def test_the_entity_is_actually_exposed(self) -> None:
        """The knob is worthless if it is not on the number platform."""
        from custom_components.localshift.const import (
            CONF_PRECHARGE_RUNWAY_MARGIN_MIN,
            THRESHOLD_RANGES,
        )
        from custom_components.localshift.number import NUMBER_DEFINITIONS

        assert CONF_PRECHARGE_RUNWAY_MARGIN_MIN in [d[0] for d in NUMBER_DEFINITIONS]
        spec = THRESHOLD_RANGES[CONF_PRECHARGE_RUNWAY_MARGIN_MIN]
        assert (spec["min"], spec["max"]) == (0.0, 60.0)


class TestRunwayTelemetryReachesTheSummary:
    """The producer half of the runway telemetry, which had no wiring at all.

    ``sensor.localshift_optimizer_summary`` reads ``precharge_runway_slack_min`` and
    ``hard_floor_suppressed_by_solar`` from the summary dict (falling back to coordinator
    data), but nothing wrote either one: ``_build_summary`` is a pure result->dict
    function with no config handle, and ``CoordinatorData`` has no such fields. So the
    dashboard reported ``null`` / ``false`` forever — including on the cycle the runway
    arm force-commits BOOST and logs slack as its reason — and the operator had no way to
    watch slack degrade toward the margin, which is the entire point of Stage A.

    The sensor-side tests all hand-inject these keys, so they pass either way. This class
    asserts the live path actually produces them.
    """

    @staticmethod
    def _summary(config: OptimizerConfig | None) -> dict[str, Any]:
        facade = OptimizerFacade()
        data = CoordinatorData()
        facade._write_optimizer_fields(
            data,
            OptimizerResult(success=True),
            SimpleNamespace(
                to_parity_dict=lambda: {}, horizon_hours=24, all_solcast=[]
            ),
            {},
            "cycle-1",
            None,
            config,
        )
        return data.optimizer_summary

    def test_the_solver_s_runway_fields_reach_the_summary(self) -> None:
        summary = self._summary(_runway_config(precharge_runway_slack_min=-4.0))

        assert summary["precharge_runway_slack_min"] == -4.0
        assert summary["hard_floor_suppressed_by_solar"] is True
        # The margin rides along so the reading can be judged against its threshold
        # without the operator having to go and read the slider separately.
        assert summary["precharge_runway_margin_min"] == RUNWAY_MARGIN_MIN

    def test_a_null_slack_is_published_as_null_not_dropped(self) -> None:
        """None means "not interpretable", which the sensor must be able to see."""
        summary = self._summary(_runway_config(precharge_runway_slack_min=None))

        assert "precharge_runway_slack_min" in summary
        assert summary["precharge_runway_slack_min"] is None

    def test_a_caller_without_a_config_still_writes_dormant_defaults(self) -> None:
        """The batch/test callers pass no config; the keys must still be present."""
        summary = self._summary(None)

        assert summary["precharge_runway_slack_min"] is None
        assert summary["hard_floor_suppressed_by_solar"] is False


# ---------------------------------------------------------------------------
# The dead zone between the two arms — 2026-08-25
# ---------------------------------------------------------------------------

# 12:26–12:36 on 2026-08-25, from the live optimizer_facade log:
#   initial_soc=22.0% target=95% dw_entry_soc=90.8% shortfall=4.2%
#   charge_slots=12 first_charge=12:45 backstop=False
#   DP optimizer: selected self_consumption (action=hold, slot=0, decision_allowed=False)
# The published runway slack was 2.02 minutes against a 15-minute margin.
DEADZONE_SOC_PCT = 22.0
DEADZONE_SHORTFALL_PCT = 4.2
DEADZONE_DW_ENTRY_PCT = 90.8
DEADZONE_RUNWAY_SLACK_MIN = 2.02


def _deadzone_config(**overrides: Any) -> OptimizerConfig:
    """A day where pre-charge IS required: the hard floor is LIVE, not suppressed."""
    config = _config(
        demand_window_target_soc_pct=LIVE_TARGET_PCT,
        hard_floor_suppressed_by_solar=False,
        precharge_runway_slack_min=DEADZONE_RUNWAY_SLACK_MIN,
        precharge_runway_margin_min=RUNWAY_MARGIN_MIN,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _deadzone_result() -> Any:
    """A projected shortfall that sits UNDER #901's deadband — 4.2 against 5.0."""
    return SimpleNamespace(
        terminal_shortfall_pct=DEADZONE_SHORTFALL_PCT,
        dw_entry_soc_pct=DEADZONE_DW_ENTRY_PCT,
    )


class TestBackstopDeadZone:
    """Neither arm covered a live floor with a sub-deadband projected shortfall.

    Found live 2026-08-25. #901 declined because 4.2% <= PRECHARGE_BACKSTOP_SHORTFALL_PCT
    (5.0), and the runway arm stood down because its own gate required the floor to be
    SUPPRESSED BY SOLAR — but the floor was live. Both off for half an hour at 22% SOC
    against a 95% target with 2.0 minutes of runway slack, while the DP re-planned every
    5 minutes and pushed ``first_charge`` back each time. Recovered by a manual override.

    A deferral drift is precisely the shape a shortfall deadband cannot see: it adds
    ~1.5 points per replan and never steps past 5.0 in a single cycle until the runway
    has already gone.
    """

    def test_the_901_arm_declines_on_a_sub_deadband_shortfall(self) -> None:
        """The first half of the gap, asserted directly rather than implied."""
        facade = OptimizerFacade()

        assert DEADZONE_SHORTFALL_PCT <= PRECHARGE_BACKSTOP_SHORTFALL_PCT
        assert (
            facade._precharge_backstop_mode(
                _data(soc=DEADZONE_SOC_PCT),
                _deadzone_result(),
                _deadzone_config(),
                CURRENT_SLOT_IDX,
            )
            is None
        )

    def test_runway_arm_covers_the_live_floor_sub_deadband_band(self, caplog) -> None:
        """The money case: #901 has passed, so the slack-scored arm must speak."""
        facade = OptimizerFacade()
        data = _data(soc=DEADZONE_SOC_PCT)

        with caplog.at_level("WARNING"):
            _run(facade, data, _deadzone_config(), _deadzone_result())

        assert data.active_mode == BatteryMode.BOOST_CHARGING
        assert data.debug_mode_source == "runway_backstop"
        assert data.optimizer_precharge_backstop_active is True
        assert "RUNWAY BACKSTOP" in caplog.text
        assert "PRE-CHARGE BACKSTOP" not in caplog.text

    def test_a_live_floor_with_runway_to_spare_is_still_left_alone(self) -> None:
        """The fix widens WHEN the arm may speak, not WHETHER slack still gates it.

        Same live floor, same sub-deadband shortfall — but the runway is intact, so
        nothing should fire. Without this the change reads as "arm on every strict day".
        """
        facade = OptimizerFacade()
        data = _data(soc=DEADZONE_SOC_PCT)

        _run(
            facade,
            data,
            _deadzone_config(
                precharge_runway_slack_min=RUNWAY_MARGIN_MIN + 10.0,
            ),
            _deadzone_result(),
        )

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False

    def test_901_still_owns_the_cycle_when_it_qualifies(self, caplog) -> None:
        """Ordering is unchanged: a material shortfall is still #901's, not this arm's.

        The call site only reaches the runway arm after #901 returns None, so widening
        this arm must not relabel a cycle #901 would have taken.
        """
        facade = OptimizerFacade()
        data = _data()

        with caplog.at_level("WARNING"):
            _run(facade, data, _deadzone_config(), _result())

        assert data.active_mode == BatteryMode.BOOST_CHARGING
        assert data.debug_mode_source == "precharge_backstop"
        assert "PRE-CHARGE BACKSTOP" in caplog.text
        assert "RUNWAY BACKSTOP" not in caplog.text

    def test_margin_zero_still_kills_the_widened_arm(self) -> None:
        """The operator's off switch outranks the widening."""
        facade = OptimizerFacade()
        data = _data(soc=DEADZONE_SOC_PCT)

        _run(
            facade,
            data,
            _deadzone_config(
                precharge_runway_margin_min=0.0,
                precharge_runway_slack_min=-120.0,
            ),
            _deadzone_result(),
        )

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False

    def test_price_ceiling_still_blocks_the_widened_arm(self) -> None:
        """A live floor is not a licence to force-charge through a spike."""
        facade = OptimizerFacade()
        data = _data(soc=DEADZONE_SOC_PCT, general_price=0.85)

        _run(facade, data, _deadzone_config(), _deadzone_result())

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False

    def test_allow_dw_entry_under_target_stays_dormant(self) -> None:
        """The policy opt-out nulls the floor, and a null floor is not "required today".

        This is the assertion that keeps ``_precharge_required_today`` honest: it may
        only read a LIVE floor as intent, never the absence of one.
        """
        facade = OptimizerFacade()
        data = _data(soc=DEADZONE_SOC_PCT)

        _run(
            facade,
            data,
            _deadzone_config(hard_target_floor=None),
            _deadzone_result(),
        )

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.optimizer_precharge_backstop_active is False


# ---------------------------------------------------------------------------
# Charge commit anti-chatter (2026-07-30 incident)
# ---------------------------------------------------------------------------


def _dwell_config(**overrides: Any) -> OptimizerConfig:
    """Ordinary pre-charge day config: hard floor live, urgency window open."""
    config = _config(
        demand_window_target_soc_pct=LIVE_TARGET_PCT,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _commit_charge_data(
    data: CoordinatorData, committed_at: datetime | None
) -> CoordinatorData:
    """Put the data into a 'charge committed at X' state."""
    data.active_mode = BatteryMode.GRID_CHARGING
    data.decision_mode = BatteryMode.GRID_CHARGING
    data.decision_timestamp = committed_at
    return data


class TestChargeCommitDwell:
    """A committed grid charge must stay on for at least one slot-0 width.

    Found live 2026-07-30: on that demand window the binary sensor
    ``charge_forced`` toggled eleven times in 124 minutes, including two on-pulses
    of 45 and 46 seconds that produced real grid-import cycles on the Powerwall. The
    runway arm's release has an anti-chatter band (`PRECHARGE_RUNWAY_HYSTERESIS_MIN`
    widening the threshold while holding), but the ordinary token-gated commit path
    that drives ``charge_forced`` had no equivalent damping — each grant committed
    whatever slot-0 said, and slot-0's plan oscillated hold/charge on every replan
    because the cheapest first-charge slot drifts forward and slack is quantised to
    slot-0 width. The result is a sawtooth commit/release with no band at all.

    The fix is a minimum on-duration on the ordinary commit path, sized like the
    runway arm's band: ``max(PRECHARGE_RUNWAY_HYSTERESIS_MIN, quantum)`` where
    ``quantum = precharge_runway_quantum_min`` (slot-0's width in minutes). The
    runway band and the dwell band are the same anti-sawtooth mechanism viewed
    from two sides; they share the same floor constant so tuning one retunes both.
    """

    @staticmethod
    def _run_for_commit_dwell(
        facade: OptimizerFacade,
        data: CoordinatorData,
        config: OptimizerConfig,
        plan_mode: BatteryMode,
    ) -> None:
        """Drive the ordinary commit path with the given plan mode and no backstop."""
        with (
            patch(
                "custom_components.localshift.engine.optimizer_facade.OptimizerSafetyGate"
            ) as mock_gate,
            patch(
                "custom_components.localshift.engine.optimizer_facade._current_slot_debug_info",
                return_value=(CURRENT_SLOT_IDX, True, "14:40", "13:00", 60.0),
            ),
            patch(
                "custom_components.localshift.engine.optimizer_facade._derive_runtime_apply_plan",
                return_value={
                    "battery_mode": plan_mode.value,
                    "action": "hold",
                },
            ),
        ):
            mock_gate.return_value.check_admission.return_value = SimpleNamespace(
                allowed=True, block_reason=None
            )
            facade._assign_active_mode(data, _result(), config, {})

    def test_sub_minute_pulse_is_held(self) -> None:
        """The money case: the 45-second pulse observed on 2026-07-30 at 13:41.

        active_mode was committed to GRID_CHARGING ~45 seconds ago, the plan now
        wants hold, and the token is granted. Without a dwell band this releases
        immediately; with it the mode must stay at GRID_CHARGING and the pending
        plan surfaces on debug_plan_mode_pending.
        """
        facade = OptimizerFacade()
        now = dt_util.now()
        data = _commit_charge_data(
            _data(mode_backstop_allowed=False, mode_decision_allowed=True),
            now - timedelta(seconds=45),
        )
        config = _dwell_config(precharge_runway_quantum_min=5.0)

        self._run_for_commit_dwell(facade, data, config, BatteryMode.SELF_CONSUMPTION)

        assert data.active_mode == BatteryMode.GRID_CHARGING
        assert data.debug_plan_mode_pending == BatteryMode.SELF_CONSUMPTION.value
        # The commit must NOT be recorded (decision_timestamp only updates on a
        # committed mode change).
        assert data.decision_timestamp == now - timedelta(seconds=45)

    def test_dwell_expired_allows_release(self) -> None:
        """Once the committed charge has lived past the band, the release commits."""
        facade = OptimizerFacade()
        now = dt_util.now()
        data = _commit_charge_data(
            _data(mode_backstop_allowed=False, mode_decision_allowed=True),
            now - timedelta(minutes=11),
        )
        config = _dwell_config(precharge_runway_quantum_min=5.0)

        self._run_for_commit_dwell(facade, data, config, BatteryMode.SELF_CONSUMPTION)

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.debug_plan_mode_pending is None

    def test_band_scales_with_slot_quantum(self) -> None:
        """30-minute slot-0 widens the band to 30 min — the 11-minute pulse stays
        held, as it would on the horizon that opens with no 5-minute Amber data."""
        facade = OptimizerFacade()
        now = dt_util.now()
        data = _commit_charge_data(
            _data(mode_backstop_allowed=False, mode_decision_allowed=True),
            now - timedelta(minutes=11),
        )
        config = _dwell_config(precharge_runway_quantum_min=30.0)

        self._run_for_commit_dwell(facade, data, config, BatteryMode.SELF_CONSUMPTION)

        assert data.active_mode == BatteryMode.GRID_CHARGING
        assert data.debug_plan_mode_pending == BatteryMode.SELF_CONSUMPTION.value

    def test_turn_on_is_not_damped(self) -> None:
        """The fix damps releases, not commits. A wanted charge must still start
        immediately — 2026-07-27 proved the cost of any delay on the charge turn-on
        path."""
        facade = OptimizerFacade()
        data = _data(mode_backstop_allowed=False, mode_decision_allowed=True)
        # Decision timestamp is None (freshly-started session).
        config = _dwell_config(precharge_runway_quantum_min=5.0)

        self._run_for_commit_dwell(facade, data, config, BatteryMode.GRID_CHARGING)

        assert data.active_mode == BatteryMode.GRID_CHARGING
        assert data.debug_plan_mode_pending is None

    def test_demand_window_active_exempt(self) -> None:
        """Once the DW is live the pre-charge is moot. The dwell must not drag a
        committed grid charge into the demand window — that would fight demand-block
        behaviour. Mirrors ``_backstop_context_permits``."""
        facade = OptimizerFacade()
        now = dt_util.now()
        data = _commit_charge_data(
            _data(
                mode_backstop_allowed=False,
                mode_decision_allowed=True,
                demand_window_active=True,
            ),
            now - timedelta(minutes=1),
        )
        config = _dwell_config(precharge_runway_quantum_min=5.0)

        self._run_for_commit_dwell(facade, data, config, BatteryMode.SELF_CONSUMPTION)

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.debug_plan_mode_pending is None

    def test_missing_decision_timestamp_fails_open(self) -> None:
        """A committed-at-less-than-one-slot-ago that has lost its timestamp (crash
        restart, stale data) must not hold the mode forever. Fail open: release
        instead of pinning indefinitely."""
        facade = OptimizerFacade()
        data = _commit_charge_data(
            _data(mode_backstop_allowed=False, mode_decision_allowed=True),
            None,
        )
        config = _dwell_config(precharge_runway_quantum_min=5.0)

        self._run_for_commit_dwell(facade, data, config, BatteryMode.SELF_CONSUMPTION)

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION

    def test_naive_timestamp_fails_open(self) -> None:
        """Mixed naive/aware timestamps raise TypeError on subtraction; the gate
        must degrade to 'no hold' rather than propagate the exception."""
        facade = OptimizerFacade()
        data = _commit_charge_data(
            _data(mode_backstop_allowed=False, mode_decision_allowed=True),
            datetime.now(),  # naive — no tz
        )
        config = _dwell_config(precharge_runway_quantum_min=5.0)

        self._run_for_commit_dwell(facade, data, config, BatteryMode.SELF_CONSUMPTION)

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION

    def test_charge_to_charge_not_damped(self) -> None:
        """GRID↔BOOST transitions while the battery is already charging are not the
        pathology. A mode change within ``_CHARGE_MODES`` must still commit."""
        facade = OptimizerFacade()
        now = dt_util.now()
        data = _commit_charge_data(
            _data(mode_backstop_allowed=False, mode_decision_allowed=True),
            now - timedelta(seconds=45),
        )
        config = _dwell_config(precharge_runway_quantum_min=5.0)

        self._run_for_commit_dwell(facade, data, config, BatteryMode.BOOST_CHARGING)

        assert data.active_mode == BatteryMode.BOOST_CHARGING
        assert data.debug_plan_mode_pending is None

    def test_safety_gate_fallback_not_damped(self) -> None:
        """A safety-gate block must release immediately; the fallback path is above
        the gate and may commit a non-charge mode even when a charge was committed
        less than one slot ago. Safety first."""
        facade = OptimizerFacade()
        now = dt_util.now()
        data = _commit_charge_data(
            _data(mode_backstop_allowed=False, mode_decision_allowed=True),
            now - timedelta(seconds=45),
        )
        config = _dwell_config(precharge_runway_quantum_min=5.0)

        with (
            patch(
                "custom_components.localshift.engine.optimizer_facade.OptimizerSafetyGate"
            ) as mock_gate,
            patch(
                "custom_components.localshift.engine.optimizer_facade._current_slot_debug_info",
                return_value=(CURRENT_SLOT_IDX, True, "14:40", "13:00", 60.0),
            ),
            patch(
                "custom_components.localshift.engine.optimizer_facade._derive_runtime_apply_plan",
                return_value={
                    "battery_mode": BatteryMode.SELF_CONSUMPTION.value,
                    "action": "hold",
                },
            ),
        ):
            mock_gate.return_value.check_admission.return_value = SimpleNamespace(
                allowed=False, block_reason="test block"
            )
            facade._assign_active_mode(data, _result(), config, {})

        assert data.active_mode == BatteryMode.SELF_CONSUMPTION
        assert data.debug_mode_source == "fallback"

    def test_runway_latch_behaviour_unchanged(self) -> None:
        """NON-GOAL check: the runway arm's own hysteresis band is untouched."""
        facade = OptimizerFacade()
        data = _runway_data()
        config = _runway_config(precharge_runway_slack_min=-5.0)

        with (
            patch(
                "custom_components.localshift.engine.optimizer_facade.OptimizerSafetyGate"
            ) as mock_gate,
            patch(
                "custom_components.localshift.engine.optimizer_facade._current_slot_debug_info",
                return_value=(CURRENT_SLOT_IDX, True, "14:40", "13:00", 60.0),
            ),
            patch(
                "custom_components.localshift.engine.optimizer_facade._derive_runtime_apply_plan",
                return_value={
                    "battery_mode": BatteryMode.SELF_CONSUMPTION.value,
                    "action": "hold",
                },
            ),
        ):
            mock_gate.return_value.check_admission.return_value = SimpleNamespace(
                allowed=True, block_reason=None
            )
            facade._assign_active_mode(data, _clean_result(), config, {})

        assert data.active_mode == BatteryMode.BOOST_CHARGING
        assert data.debug_mode_source == "runway_backstop"

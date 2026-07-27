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
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.engine.optimizer_facade import (
    PRECHARGE_BACKSTOP_SHORTFALL_PCT,
    OptimizerFacade,
)
from custom_components.localshift.engine.types import OptimizerConfig

# The 14:30 line of the evidence pack: SOC 62.2%, price 11.6 c, plan projects a 72.6%
# DW entry against a 95% target, and the DP has a pre-DW grid charge at slot 4.
CURRENT_SLOT_IDX = 4
TERMINAL_PENALTY_IDX = 10


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

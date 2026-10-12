"""Issue #1120: the default mode commits an export on a genuine spike, promptly.

The planner half is in ``tests/engine/test_spike_export_default_mode.py``. This
is the commit path: the real ``StateMachine.evaluate_state_machine`` over the
real ``ComputationEngine``, on the inputs captured from the live system on
2026-10-12, with the optimisation mode forced to ``self_consumption`` (the
operator had switched it to ``arbitrage`` by hand that afternoon, which is the
only reason anything exported).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from custom_components.localshift.const import BatteryMode
from tests.state.test_export_start_confirmation import _Rig

CAPTURES = Path(__file__).resolve().parents[2] / "simulations" / "replay-1119"


def _rig(instant: str) -> _Rig:
    return _Rig(CAPTURES / f"2026-10-12T{instant}.json", "self_consumption")


def _settle(rig: _Rig) -> None:
    """Two evaluations: the second absorbs the demand-window one-tick lag."""
    rig.tick(0)
    rig.tick(5)


class TestDefaultModeCommitsTheSpikeExport:
    @pytest.mark.parametrize("instant", ["151530", "163040"])
    def test_confirmed_spike_starts_exporting_without_the_debounce(self, instant):
        rig = _rig(instant)

        _settle(rig)

        assert rig.plan_action == "export_proactive"
        assert rig.data.optimizer_summary["spike_export"]["skip_debounce"] is True
        assert rig.data.active_mode == BatteryMode.PROACTIVE_EXPORT
        # The base branch never left self_consumption here; with the 2-minute
        # debounce the command would not be sent until 120 s had passed.
        rig.controller.set_proactive_export.assert_awaited_once()
        assert rig.machine._commanded_mode == BatteryMode.PROACTIVE_EXPORT

    @pytest.mark.parametrize("instant", ["161040", "162550"])
    def test_sub_dollar_interval_stays_out_of_export(self, instant):
        rig = _rig(instant)

        _settle(rig)
        rig.tick(200)

        assert rig.plan_action == "hold"
        assert rig.data.active_mode != BatteryMode.PROACTIVE_EXPORT
        rig.controller.set_proactive_export.assert_not_awaited()

    def test_export_stops_when_the_next_interval_is_not_a_spike(self):
        """15:15 was $24.23, 15:20 confirmed at 71c: the export must end."""
        rig = _rig("151530")
        _settle(rig)
        assert rig.machine._commanded_mode == BatteryMode.PROACTIVE_EXPORT

        rig.confirm(sell=0.7052, buy=0.80)
        rig.tick(20)
        rig.tick(25)

        assert rig.plan_action == "hold"
        assert rig.machine._commanded_mode != BatteryMode.PROACTIVE_EXPORT


class TestDebounceExemptionIsNarrow:
    def test_unconfirmed_spike_price_keeps_the_debounce(self):
        """15:20:10 still carried 15:15's $24.23 as an estimate."""
        rig = _rig("152010")

        _settle(rig)
        for seconds in (30, 60, 95, 110):
            rig.tick(seconds)

        # Past the 90 s grace the estimate is acted on (#1119), but an
        # unconfirmed price never skips the debounce.
        assert rig.slot0["sell_estimate"] is True
        assert rig.data.optimizer_summary["spike_export"]["skip_debounce"] is False
        assert rig.data.active_mode == BatteryMode.PROACTIVE_EXPORT
        rig.controller.set_proactive_export.assert_not_awaited()

    def test_ordinary_export_keeps_the_two_minute_debounce(self):
        rig = _rig("161040")
        _settle(rig)
        summary = rig.data.optimizer_summary

        assert summary["spike_export"]["skip_debounce"] is False
        assert rig.machine._get_debounce_duration(
            BatteryMode.PROACTIVE_EXPORT, rig.data
        ) == timedelta(minutes=2)

    def test_exemption_applies_to_export_only(self):
        rig = _rig("151530")
        _settle(rig)

        assert rig.data.optimizer_summary["spike_export"]["skip_debounce"] is True
        assert rig.machine._get_debounce_duration(
            BatteryMode.PROACTIVE_EXPORT, rig.data
        ) == timedelta(0)
        # No data, no exemption: the summary is the only thing that grants it.
        assert rig.machine._get_debounce_duration(
            BatteryMode.PROACTIVE_EXPORT
        ) == timedelta(minutes=2)


# --------------------------------------------------------------------------
# The kill switch: switch.localshift_spike_discharge_enabled
# --------------------------------------------------------------------------

SWITCH_OFF = {"spike_discharge_enabled": False}
TWO_MINUTES = timedelta(minutes=2)


class TestKillSwitchOnTheCommitPath:
    @pytest.mark.parametrize("instant", ["151530", "153040", "163040"])
    def test_switch_on_exports_and_skips_the_debounce(self, instant):
        rig = _Rig(
            CAPTURES / f"2026-10-12T{instant}.json",
            "self_consumption",
            {"spike_discharge_enabled": True},
        )

        _settle(rig)

        assert rig.data.optimizer_summary["spike_export"]["enabled"] is True
        assert rig.machine._commanded_mode == BatteryMode.PROACTIVE_EXPORT
        rig.controller.set_proactive_export.assert_awaited_once()

    @pytest.mark.parametrize("instant", ["151530", "153040", "163040"])
    def test_switch_off_never_exports_the_spike_in_the_default_mode(self, instant):
        """What the base branch did: hold through $24.23 and $10.5."""
        rig = _Rig(
            CAPTURES / f"2026-10-12T{instant}.json", "self_consumption", SWITCH_OFF
        )

        _settle(rig)
        for seconds in (30, 90, 130, 200):
            rig.tick(seconds)

        spike = rig.data.optimizer_summary["spike_export"]
        assert spike["enabled"] is False
        assert spike["admitted"] is False
        assert spike["skip_debounce"] is False
        assert rig.plan_action == "hold"
        assert rig.data.active_mode != BatteryMode.PROACTIVE_EXPORT
        assert rig.machine._commanded_mode != BatteryMode.PROACTIVE_EXPORT
        rig.controller.set_proactive_export.assert_not_awaited()

    @pytest.mark.parametrize("instant", ["151530", "163040"])
    def test_switch_off_keeps_the_debounce_in_arbitrage(self, instant):
        """Arbitrage still wants the export; OFF only takes away the head start."""
        on = _Rig(CAPTURES / f"2026-10-12T{instant}.json", "arbitrage")
        off = _Rig(CAPTURES / f"2026-10-12T{instant}.json", "arbitrage", SWITCH_OFF)

        _settle(on)
        _settle(off)

        # ON: confirmed spike, command sent at once.
        on.controller.set_proactive_export.assert_awaited_once()
        # OFF: same plan, same desired mode, but the 2-minute debounce stands.
        assert off.plan_action == on.plan_action == "export_proactive"
        assert off.data.active_mode == BatteryMode.PROACTIVE_EXPORT
        assert off.data.optimizer_summary["spike_export"]["skip_debounce"] is False
        assert (
            off.machine._get_debounce_duration(BatteryMode.PROACTIVE_EXPORT, off.data)
            == TWO_MINUTES
        )
        off.controller.set_proactive_export.assert_not_awaited()
        off.tick(60)
        off.controller.set_proactive_export.assert_not_awaited()
        # ...and once it has run out the ordinary export goes ahead.
        off.tick(130)
        off.controller.set_proactive_export.assert_awaited_once()

"""allow_dw_entry_under_target only waives the DW target when solar reaches it (#1266).

Replays real captures with the switch on and off. When solar alone cannot reach
target in the window, all three effects of the switch (hard floor, terminal
penalty placement, next-window tail) must stay off: the plan equals switch-off.
When solar can reach it, the switch still lets the plan enter under target.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts import replay_plan_baseline as rpb

REPO_ROOT = Path(__file__).resolve().parent.parent
# Switch on in the capture; solar cannot reach the 95% target in the window.
SOLAR_CANNOT_REACH = (
    "simulations/replay/2026-08-25.json",
    "simulations/replay-1119/2026-10-12T161040.json",
    # SOC is 100% now but solar-only SOC sits at the floor through the window: the
    # gate counts only SOC inside the window, not the starting SOC.
    "simulations/replay-1120/2026-10-04T173040.json",
    "simulations/replay-1120/2026-10-10T190040.json",
)
# Switch on in the capture; solar reaches target in the window.
SOLAR_REACHES = "simulations/replay/2026-09-03.json"


def _replay(capture: str, switch_on: bool) -> dict:
    scenario = copy.deepcopy(json.loads((REPO_ROOT / capture).read_text()))
    scenario.setdefault("config_overrides", {})["allow_dw_entry_under_target"] = (
        switch_on
    )
    scenario.setdefault("switch_states", {})["allow_dw_entry_under_target"] = switch_on
    return rpb.replay(scenario, "captured")


@pytest.mark.parametrize("capture", SOLAR_CANNOT_REACH)
def test_switch_on_but_solar_cannot_reach_target_matches_switch_off(capture):
    on = _replay(capture, True)
    off = _replay(capture, False)
    assert on["data"]["solar_can_reach_target_in_dw"] is False
    assert on["decisions"] == off["decisions"]
    assert on["result"] == off["result"]
    assert on["summary"].get("next_window_tail_status") == off["summary"].get(
        "next_window_tail_status"
    )


def test_switch_on_and_solar_reaches_target_still_enters_under_target():
    on = _replay(SOLAR_REACHES, True)
    off = _replay(SOLAR_REACHES, False)
    assert on["data"]["solar_can_reach_target_in_dw"] is True
    assert on["summary"].get("next_window_tail_status") == "under_target_mode"
    assert off["summary"].get("next_window_tail_status") != "under_target_mode"

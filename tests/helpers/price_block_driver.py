"""Scenario driver for the price-block wiring tests (#1107).

Kept free of any import introduced by the wiring itself, so the same code can be
run against the commit before it: that is how
``tests/engine/price_block_switch_off_golden.json`` was captured (``capture_all``
at 83badbb), and it is what makes the switch-OFF identity test a comparison
against the pre-change planner and not against itself.

Every scenario is driven the way ``scripts/replay_no_dw.py`` ``run_arm`` drives
it: ``forecast_horizon_hours`` seeded before the first pass, and two passes of
``compute_derived_values`` with the second one reported (#1053). A fresh
``CoordinatorData`` carries a zero horizon that collapses the cheap percentile,
and a single pass reports a plan live would never produce.
"""

from __future__ import annotations

import copy
import hashlib
import json
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from custom_components.localshift.computation_engine import ComputationEngine
from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.engine.types import SlotContext
from scripts.replay_no_dw import LIVE_OVERRIDES, LIVE_SWITCHES, _seed_horizon
from tests.test_scenarios import (
    create_mock_entry,
    create_mock_get_entity_id,
    create_mock_get_switch_state,
    setup_coordinator_data,
    setup_mock_hass,
)

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "tests" / "engine" / "price_block_switch_off_golden.json"
_SCENARIO_DIRS = (
    "simulations/scenarios",
    "simulations/replay",
    "simulations/replay-nodw",
)

CAPTURE_0907 = "simulations/replay-nodw/2026-09-07.json"
CAPTURE_0903 = "simulations/replay/2026-09-03.json"

# Wall-clock and per-run identifiers: the only fields that differ between two
# runs of the same inputs on the same code.
_VOLATILE = ("solve_time_seconds",)


def scenario_paths() -> list[str]:
    """Every committed scenario fixture, as repo-relative posix paths."""
    return [
        path.relative_to(REPO).as_posix()
        for directory in _SCENARIO_DIRS
        for path in sorted((REPO / directory).glob("*.json"))
    ]


@dataclass
class Run:
    """What one driven scenario exposes to the tests."""

    data: CoordinatorData
    slots: list[SlotContext]
    """The slots handed to the planner on the reported (second) pass."""

    @property
    def decisions(self) -> list[dict[str, Any]]:
        return self.data.optimizer_decisions or []

    @property
    def flags(self) -> str:
        """One character per slot: E entry, D in-window, . neither."""
        return "".join(
            "E" if s.is_demand_window_entry else "D" if s.is_demand_window_slot else "."
            for s in self.slots
        )

    @property
    def result(self) -> dict[str, Any]:
        result = dict(self.data.optimizer_result or {})
        for key in _VOLATILE:
            result.pop(key, None)
        return result

    def charge_times(self) -> list[datetime]:
        return [
            datetime.fromisoformat(d["timestamp_iso"])
            for d in self.decisions
            if d["grid_charge"]
        ]

    def digest(self) -> str:
        """Hash of everything the planner was shown and everything it decided."""
        payload = {
            "slots": [
                [
                    s.slot_index,
                    s.timestamp_iso,
                    s.slot_interval_minutes,
                    repr(s.buy_price),
                    repr(s.sell_price),
                    repr(s.solar_kwh),
                    repr(s.consumption_kwh),
                    s.is_demand_window_entry,
                    s.is_demand_window_slot,
                ]
                for s in self.slots
            ],
            "decisions": self.decisions,
            "result": self.result,
        }
        text = json.dumps(payload, sort_keys=True, default=repr)
        return hashlib.sha256(text.encode()).hexdigest()

    def snapshot(self) -> dict[str, Any]:
        """The golden record: readable fields for diagnosis, digest for identity."""
        return {
            "flags": self.flags,
            "actions": [d["action"] for d in self.decisions],
            "projected_net_cost": self.result.get("projected_net_cost"),
            "digest": self.digest(),
        }


def drive(
    scenario: dict[str, Any],
    *,
    price_block_target: bool | None,
    live: bool,
    previous_entry_iso: str | None = None,
) -> Run:
    """Run a scenario through the engine as ``replay_no_dw.run_arm`` does.

    Args:
        scenario: Parsed scenario JSON.
        price_block_target: Switch state. None leaves the switch out of the
            resolver entirely, which is how every pre-existing caller runs.
        live: Apply the harness's live config and switches on top of the
            scenario's own, as the replay arms do. False runs the scenario with
            its own configuration only.
        previous_entry_iso: Seed for the hysteresis, as if a previous plan had
            entered the block at this timestamp.

    """
    payload = dict(scenario["input"])
    if live:
        payload["adaptive_params"] = {}

    test_time = datetime.fromisoformat(
        payload.get("test_time", "2026-02-16T14:00:00+11:00")
    )
    data = setup_coordinator_data(payload)
    hass = setup_mock_hass(payload)
    _seed_horizon(data, payload)
    if previous_entry_iso is not None:
        data.target_block_entry_iso = previous_entry_iso

    overrides = dict(scenario.get("config_overrides", {}))
    switches = dict(scenario.get("switch_states", {}))
    if live:
        overrides.update(LIVE_OVERRIDES)
        switches.update(LIVE_SWITCHES)
    if price_block_target is not None:
        switches["price_block_target"] = price_block_target

    engine = ComputationEngine(
        hass,
        create_mock_entry(overrides),
        create_mock_get_entity_id(),
        create_mock_get_switch_state(switches),
    )

    seen: list[list[SlotContext]] = []
    real_plan = engine._dp_planner.plan

    def recording_plan(inputs: Any) -> Any:
        seen.append(copy.deepcopy(inputs.slots))
        return real_plan(inputs)

    recent_load = payload.get("load_power_kw", 0.5)
    fetcher = engine._history_fetcher
    patches = [
        patch("homeassistant.util.dt.now", return_value=test_time),
        patch.object(engine, "_get_historical_hourly_averages", return_value={}),
        patch.object(fetcher, "_historical_load_cache", {}),
        patch.object(fetcher, "_historical_load_sample_counts", {}),
        patch.object(fetcher, "_historical_load_source", "none"),
        patch.object(fetcher, "_recent_load_1hr_kw", recent_load),
        patch.object(engine._dp_planner, "plan", side_effect=recording_plan),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        engine.compute_derived_values(data)
        seen.clear()
        engine.compute_derived_values(data)

    # The primary plan is the first solve of a pass; a shadow comparison, when a
    # scenario enables one, solves after it.
    return Run(data=data, slots=seen[0] if seen else [])


def load(path: str) -> dict[str, Any]:
    return json.loads((REPO / path).read_text())


def is_live(path: str) -> bool:
    """Replay captures run under the harness's live config; scenarios do not."""
    return not path.startswith("simulations/scenarios/")


def capture_all() -> dict[str, Any]:
    """Build the golden record. Run only against the pre-change code."""
    return {
        path: drive(load(path), price_block_target=None, live=is_live(path)).snapshot()
        for path in scenario_paths()
    }

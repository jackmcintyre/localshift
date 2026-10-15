#!/usr/bin/env python3
"""replay_current_mode.py — what does switch.localshift_planner_knows_current_mode do?

Issue #1158. With the switch on, the planner is told the mode the battery was
left in (``OptimizerInputs.current_action``) and a slot 0 that changes it pays
the switching penalty. With it off, as before, it is told nothing.

Every capture the plan baseline covers is replayed through
``scripts/replay_plan_baseline.py``'s own ``replay`` (same inputs, same pinned
clock, same seeded live-like inputs) under the ``strict`` and ``live_like``
arms, once with the switch off and once on. The checked-in baseline is not
touched: the switch is only ever set on a copy of the arm's switch table here.

Reported per arm:

* plans changed: captures whose slot actions differ between off and on;
* slot-0 actions: off -> on transitions where slot 0 differs, and the
  committed mode the facade chose;
* cost: the plan's projected net cost (the planner's objective, penalties
  included) and its cash cost (sum of import x buy - export x sell over the
  plan's slots), both summed over all captures, on minus off;
* action changes between consecutive slots, summed over all plans: a proxy
  for cycling inside one plan.

What this cannot show: live, the planner re-plans every few minutes and the
penalty is meant to stop slot 0 flipping between those plans. A replay of one
instant per capture sees one plan, so the slot-0 rows are the direct measure
of that, and the cost rows are what each plan expects, not what a day costs.

Usage
-----
  uv run scripts/replay_current_mode.py
  uv run scripts/replay_current_mode.py --detail       one line per changed plan
  uv run scripts/replay_current_mode.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.localshift.const import (  # noqa: E402
    SWITCH_PLANNER_KNOWS_CURRENT_MODE,
)
from scripts import replay_plan_baseline as rpb  # noqa: E402

ARMS = ("strict", rpb.LIVE_LIKE)
_COL = {name: i for i, name in enumerate(rpb.DECISION_COLUMNS)}


def _replay(scenario: dict[str, Any], arm: str, on: bool) -> dict[str, Any]:
    options, switches = rpb.ARMS[arm]
    saved = rpb.ARMS[arm]
    rpb.ARMS[arm] = (options, {**switches, SWITCH_PLANNER_KNOWS_CURRENT_MODE: on})
    try:
        return rpb.replay(scenario, arm)
    finally:
        rpb.ARMS[arm] = saved


def _actions(record: dict[str, Any]) -> list[str]:
    return [row[_COL["action"]] for row in record["decisions"]]


def _cash_cost(record: dict[str, Any]) -> float:
    return sum(
        row[_COL["grid_import_kwh"]] * row[_COL["buy_price"]]
        - row[_COL["grid_export_kwh"]] * row[_COL["sell_price"]]
        for row in record["decisions"]
    )


def _changes(actions: list[str]) -> int:
    return sum(1 for a, b in zip(actions, actions[1:], strict=False) if a != b)


def _projected(record: dict[str, Any]) -> float:
    return float(record["result"].get("projected_net_cost") or 0.0)


def compare(captures: list[Path]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    start_modes: Counter[str] = Counter()
    rows: dict[str, list[dict[str, Any]]] = {arm: [] for arm in ARMS}
    for capture in captures:
        scenario = json.loads(capture.read_text())
        start_modes[scenario["input"].get("active_mode", "(none)")] += 1
        for arm in ARMS:
            off = _replay(scenario, arm, on=False)
            on = _replay(scenario, arm, on=True)
            rows[arm].append({
                "capture": capture.relative_to(REPO_ROOT).as_posix(),
                "start_mode": scenario["input"].get("active_mode"),
                "plan_changed": _actions(off) != _actions(on),
                "record_changed": off != on,
                "slot0_off": off["slot0_action"],
                "slot0_on": on["slot0_action"],
                "mode_off": off["commanded_mode"],
                "mode_on": on["commanded_mode"],
                "projected_off": _projected(off),
                "projected_on": _projected(on),
                "cash_off": _cash_cost(off),
                "cash_on": _cash_cost(on),
                "changes_off": _changes(_actions(off)),
                "changes_on": _changes(_actions(on)),
                "slots_differing": sum(
                    1
                    for a, b in zip(_actions(off), _actions(on), strict=False)
                    if a != b
                ),
            })
    report["captures"] = len(captures)
    report["start_modes"] = dict(start_modes)
    for arm in ARMS:
        r = rows[arm]
        report[arm] = {
            "plans_changed": sum(x["plan_changed"] for x in r),
            "records_changed": sum(x["record_changed"] for x in r),
            "slot0_changed": sum(x["slot0_off"] != x["slot0_on"] for x in r),
            "slot0_transitions": dict(
                Counter(
                    f"{x['slot0_off']} -> {x['slot0_on']}"
                    for x in r
                    if x["slot0_off"] != x["slot0_on"]
                )
            ),
            "mode_changed": sum(x["mode_off"] != x["mode_on"] for x in r),
            "mode_transitions": dict(
                Counter(
                    f"{x['mode_off']} -> {x['mode_on']}"
                    for x in r
                    if x["mode_off"] != x["mode_on"]
                )
            ),
            "slot0_holds_start_mode_off": sum(
                x["mode_off"] == x["start_mode"] for x in r
            ),
            "slot0_holds_start_mode_on": sum(
                x["mode_on"] == x["start_mode"] for x in r
            ),
            "projected_net_cost_off": round(sum(x["projected_off"] for x in r), 4),
            "projected_net_cost_on": round(sum(x["projected_on"] for x in r), 4),
            "cash_cost_off": round(sum(x["cash_off"] for x in r), 4),
            "cash_cost_on": round(sum(x["cash_on"] for x in r), 4),
            "action_changes_off": sum(x["changes_off"] for x in r),
            "action_changes_on": sum(x["changes_on"] for x in r),
            "slots_differing": sum(x["slots_differing"] for x in r),
            "rows": r,
        }
    return report


def _print(report: dict[str, Any], detail: bool) -> None:
    print(f"captures: {report['captures']}  start modes: {report['start_modes']}")
    for arm in ARMS:
        a = report[arm]
        print(f"\n[{arm}]")
        print(f"  plans changed (slot actions):   {a['plans_changed']}")
        print(f"  records changed (anything):     {a['records_changed']}")
        print(f"  slots differing, all plans:     {a['slots_differing']}")
        print(f"  slot 0 changed:                 {a['slot0_changed']}")
        for k, v in sorted(a["slot0_transitions"].items()):
            print(f"      {k}: {v}")
        print(f"  committed mode changed:         {a['mode_changed']}")
        for k, v in sorted(a["mode_transitions"].items()):
            print(f"      {k}: {v}")
        print(
            "  committed mode == start mode:   "
            f"off {a['slot0_holds_start_mode_off']}, "
            f"on {a['slot0_holds_start_mode_on']}"
        )
        d_proj = a["projected_net_cost_on"] - a["projected_net_cost_off"]
        d_cash = a["cash_cost_on"] - a["cash_cost_off"]
        print(
            f"  projected net cost (sum):       off {a['projected_net_cost_off']:.4f}"
            f"  on {a['projected_net_cost_on']:.4f}  delta {d_proj:+.4f}"
        )
        print(
            f"  cash cost (sum):                off {a['cash_cost_off']:.4f}"
            f"  on {a['cash_cost_on']:.4f}  delta {d_cash:+.4f}"
        )
        print(
            "  action changes between slots:   "
            f"off {a['action_changes_off']}  on {a['action_changes_on']}"
        )
        if detail:
            for x in a["rows"]:
                if x["record_changed"]:
                    print(
                        f"    {x['capture']}: start {x['start_mode']} | slot0 "
                        f"{x['slot0_off']} -> {x['slot0_on']} | mode "
                        f"{x['mode_off']} -> {x['mode_on']} | slots differing "
                        f"{x['slots_differing']} | changes {x['changes_off']} -> "
                        f"{x['changes_on']} | cash "
                        f"{x['cash_on'] - x['cash_off']:+.4f}"
                    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--detail", action="store_true")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    rpb.quiet_logging()
    report = compare(rpb.tracked_captures())
    _print(report, args.detail)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

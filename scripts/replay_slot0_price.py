#!/usr/bin/env python3
"""replay_slot0_price.py — what price did slot 0 commit on, before and after #1119?

Issue #1119: on 2026-10-12 the plan's slot-0 sell price was wrong by 3x or more
against the confirmed feed-in price for the same interval. This captures the
real optimizer inputs at chosen instants and replays each one twice through the
real engine (``ComputationEngine.compute_derived_values``):

  before   slot 0 priced the way main priced it — the sell price is the mean of
           every feed-in entry overlapping the 15 minutes from the slot's start
  after    slot 0 priced from the feed-in entry covering the current interval,
           with an unconfirmed (``estimate: true``) reading not allowed to
           start an export during the confirmation grace

Inputs are byte-identical across the two arms. Read the solar caveat in
export_replay_days.py before quoting absolute numbers; slot-0 prices and the
slot-0 action are what this is for.

Usage
-----
  uv run scripts/replay_slot0_price.py capture 2026-10-12T15:15:30+11:00 ...
  uv run scripts/replay_slot0_price.py replay
  uv run scripts/replay_slot0_price.py replay --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

DEFAULT_DIR = REPO_ROOT / "simulations" / "replay-1119"
BATTERY_MODE = "select.localshift_battery_mode"
OPTIMIZATION_MODE = "select.localshift_optimization_mode"


# --------------------------------------------------------------------------
# Capture (read-only: Home Assistant history API)
# --------------------------------------------------------------------------


def capture(instants: list[str], out_dir: Path) -> int:
    import export_replay_days as erd

    url, token = erd.load_credentials()
    out_dir.mkdir(parents=True, exist_ok=True)

    for raw in instants:
        at = datetime.fromisoformat(raw)
        scenario = erd.build_day(url, token, at)
        if scenario is None:
            print(f"  skip {raw}: inputs not retained")
            continue

        # build_day misses three things this replay needs. Its history call
        # returns state CHANGES only, so the attribute-only update Amber makes
        # when an interval rolls over (same price, `estimate: true`) is
        # invisible to it; it drops the `estimate` flag; and it does not
        # record the mode LocalShift had committed. Re-read the two price
        # sensors with every update retained and rebuild from that.
        qs = urllib.parse.urlencode({
            "filter_entity_id": f"{erd.PRICE_GENERAL},{erd.PRICE_FEED_IN}",
            "end_time": at.isoformat(),
            "significant_changes_only": "0",
        })
        start = (at - timedelta(minutes=25)).isoformat()
        blocks = erd.api_get(url, token, f"/api/history/period/{start}?{qs}")
        points = {block[0]["entity_id"]: block for block in blocks if block}
        mode_hist = erd.history(
            url, token, [BATTERY_MODE, OPTIMIZATION_MODE], at - timedelta(hours=6), at
        )
        inp = scenario["input"]
        # The operator switched optimization mode twice on 2026-10-12, and the
        # two modes admit export on different rules, so replay the one in force.
        opt_mode = erd.latest_at_or_before(mode_hist.get(OPTIMIZATION_MODE, []), at)
        if opt_mode and opt_mode["state"] in ("self_consumption", "arbitrage"):
            scenario["config_overrides"]["optimization_mode"] = opt_mode["state"]
        for entity, key, price_key in (
            (erd.PRICE_GENERAL, "general_forecast", "general_price"),
            (erd.PRICE_FEED_IN, "feed_in_forecast", "feed_in_price"),
        ):
            state = erd.latest_at_or_before(points.get(entity, []), at) or {}
            attrs = state.get("attributes", {})
            slots = erd.forecast_to_slots(attrs.get("forecast") or [])
            if slots:
                slots[0]["estimate"] = attrs.get("estimate")
                inp[key] = slots
            inp[price_key] = round(erd.as_float(state) or 0.0, 6)
            inp[f"{key}_sensor_updated"] = state.get("last_updated")
        mode = erd.latest_at_or_before(mode_hist.get(BATTERY_MODE, []), at)
        inp["active_mode"] = mode["state"] if mode else "self_consumption"

        scenario["name"] = f"replay-1119-{at.strftime('%Y-%m-%dT%H%M%S')}"
        path = out_dir / f"{at.strftime('%Y-%m-%dT%H%M%S')}.json"
        path.write_text(json.dumps(scenario, indent=2) + "\n")
        print(
            f"  wrote {path.name}  live_sell={inp['feed_in_price']:.4f}  "
            f"estimate={inp['feed_in_forecast'][0].get('estimate')}  "
            f"mode={inp['active_mode']}  soc={inp['soc']}  "
            f"opt={scenario['config_overrides']['optimization_mode']}"
        )
    return 0


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------


def run(
    scenario: dict[str, Any], arm: str, optimization_mode: str | None = None
) -> dict[str, Any]:
    """Run one captured instant through the real engine under one arm.

    ``optimization_mode`` overrides the mode captured with the scenario.
    """
    from replay_no_dw import LIVE_OVERRIDES, LIVE_SWITCHES, _seed_horizon

    from custom_components.localshift.computation_engine import ComputationEngine
    from custom_components.localshift.const import BatteryMode
    from custom_components.localshift.engine.slots import SlotBuilder
    from tests.test_scenarios import (
        create_mock_entry,
        create_mock_get_entity_id,
        create_mock_get_switch_state,
        setup_coordinator_data,
        setup_mock_hass,
    )

    payload = dict(scenario["input"])
    payload["adaptive_params"] = {}
    test_time = datetime.fromisoformat(payload["test_time"])

    data = setup_coordinator_data(payload)
    data.active_mode = BatteryMode(payload.get("active_mode", "self_consumption"))
    hass = setup_mock_hass(payload)
    _seed_horizon(data, payload)

    overrides = dict(scenario.get("config_overrides", {}))
    overrides.update(LIVE_OVERRIDES)
    if optimization_mode:
        overrides["optimization_mode"] = optimization_mode
    switches = dict(scenario.get("switch_states", {}))
    switches.update(LIVE_SWITCHES)

    engine = ComputationEngine(
        hass,
        create_mock_entry(overrides),
        create_mock_get_entity_id(),
        create_mock_get_switch_state(switches),
    )

    patches = [
        patch("homeassistant.util.dt.now", return_value=test_time),
        patch.object(engine, "_get_historical_hourly_averages", return_value={}),
        patch.object(engine._history_fetcher, "_historical_load_cache", {}),
        patch.object(engine._history_fetcher, "_historical_load_sample_counts", {}),
        patch.object(engine._history_fetcher, "_historical_load_source", "none"),
        patch.object(
            engine._history_fetcher,
            "_recent_load_1hr_kw",
            payload.get("load_power_kw", 0.5),
        ),
    ]
    if arm == "before":
        # main's behaviour: no current-interval pricing step at all, so slot 0
        # keeps the 15-minute forward average like every other slot.
        patches.append(
            patch.object(
                SlotBuilder,
                "_price_slot0_from_current_interval",
                lambda self, *a, **k: None,
            )
        )

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        # The committed mode is an input here, so restore it between the
        # warm-up pass and the reported one.
        engine.compute_derived_values(data)
        data.active_mode = BatteryMode(payload.get("active_mode", "self_consumption"))
        engine.compute_derived_values(data)

    decisions = getattr(data, "optimizer_decisions", None) or []
    summary = getattr(data, "optimizer_summary", None) or {}
    d0 = decisions[0] if decisions else {}
    return {
        "slot0_start": d0.get("timestamp_iso"),
        "buy": d0.get("buy_price"),
        "sell": d0.get("sell_price"),
        "action": d0.get("action"),
        "slot0_price": summary.get("slot0_price"),
        "spike_export": summary.get("spike_export"),
    }


def replay(in_dir: Path, json_out: str | None, opt_mode: str | None) -> int:
    files = sorted(in_dir.glob("*.json"))
    if not files:
        print(f"no captures in {in_dir}")
        return 1

    rows = []
    print(f"optimization mode: {opt_mode or 'as captured'}")
    print(
        f"{'instant':<10}{'mode':<18}{'est':<7}{'live sell':>10}"
        f"{'before sell':>13}{'before act':>19}{'after sell':>12}{'after act':>19}"
    )
    for path in files:
        scenario = json.loads(path.read_text())
        inp = scenario["input"]
        before = run(scenario, "before", opt_mode)
        after = run(scenario, "after", opt_mode)
        at = datetime.fromisoformat(inp["test_time"])
        est = inp["feed_in_forecast"][0].get("estimate")
        print(
            f"{at.strftime('%H:%M:%S'):<10}{inp.get('active_mode', '?'):<18}"
            f"{est!s:<7}{inp['feed_in_price']:>10.4f}"
            f"{before['sell']:>13.4f}{before['action']:>19}"
            f"{after['sell']:>12.4f}{after['action']:>19}"
        )
        rows.append({
            "instant": inp["test_time"],
            "active_mode": inp.get("active_mode"),
            "live_buy": inp["general_price"],
            "live_sell": inp["feed_in_price"],
            "sell_estimate": est,
            "before": before,
            "after": after,
        })

    if json_out:
        Path(json_out).write_text(json.dumps(rows, indent=2) + "\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capture", help="capture inputs at ISO instants")
    cap.add_argument("instants", nargs="+")
    cap.add_argument("--out", default=str(DEFAULT_DIR))
    rep = sub.add_parser("replay", help="replay captures before/after")
    rep.add_argument("--dir", default=str(DEFAULT_DIR))
    rep.add_argument("--json", default=None)
    rep.add_argument(
        "--optimization-mode",
        choices=["self_consumption", "arbitrage"],
        default=None,
        help="override the mode captured with each instant",
    )
    args = parser.parse_args()

    if args.cmd == "capture":
        return capture(args.instants, Path(args.out))
    return replay(Path(args.dir), args.json, args.optimization_mode)


if __name__ == "__main__":
    raise SystemExit(main())

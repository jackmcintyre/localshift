#!/usr/bin/env python3
"""study_1119_slot_sell_price.py — would pricing slots 1-11 at their own 5 minutes change plans?

Issue #1119 leftover. After #1122 slot 0's sell price is the feed-in entry
covering the current interval. Every later 5-minute slot still takes its sell
price from ``get_price_for_slot_or_none``, which averages every feed-in entry
overlapping the 15 minutes from the slot's start: for a 5-minute slot inside
Amber's 5-minute block, that is the slot's own interval and the next two.

This replays every tracked capture through the same path and arms as
``replay_plan_baseline.py`` (``ComputationEngine.compute_derived_values`` with
a decision grant; arms captured / strict / live_like) twice, on byte-identical
inputs:

  current   main as it is
  own5      every 5-minute slot after slot 0 is priced from the feed-in entry
            covering its own interval (the rule slot 0 already uses).
            30-minute slots and slot 0 are untouched.

Nothing under custom_components/ is changed; the variant is a patch applied
for the duration of one replay.

Money. The two arms see different sell prices, so their own plan costs are not
comparable. Both plans are costed at the same prices: each slot's buy price
(identical in both arms) and the sell price of the entry covering that slot,
which is what the slot would actually be paid if the forecast held. ``slot0``
is the 5 minutes that is commanded; ``hour`` is slots 0-11 (where the prices
differ); ``horizon`` is the whole plan, a forecast and not money earned.

Result on main ede344ab (2026-10-15), 84 tracked captures x 3 arms
-------------------------------------------------------------------
71 captures carry 5-minute feed-in entries and get a different sell price in
at least one of slots 1-11 (median largest gap 0.3c/kWh; 28-56c on 12 Oct).
No slot-0 action and no commanded mode changes in any of the 252 replays.
Later-slot actions change in 7 replays, all 12 Oct (16:10:40 strict only,
16:25:50 and 16:30:10 in every arm): an export moves by one slot. Plan value
at the same prices moves by -$0.05 to -$0.19 per plan (-$0.17 at 16:10:40);
none of it is in the 5 minutes that is commanded.

Usage
-----
  uv run scripts/study_1119_slot_sell_price.py
  uv run scripts/study_1119_slot_sell_price.py --only 2026-10-12 --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import replay_plan_baseline as rpb  # noqa: E402

from custom_components.localshift.engine.slot_schedule import (  # noqa: E402
    find_current_entry,
)
from custom_components.localshift.engine.slots import SlotBuilder  # noqa: E402

COLS = list(rpb.DECISION_COLUMNS)
C_TS = COLS.index("timestamp_iso")
C_MIN = COLS.index("slot_interval_minutes")
C_ACT = COLS.index("action")
C_IMP = COLS.index("grid_import_kwh")
C_EXP = COLS.index("grid_export_kwh")
C_BUY = COLS.index("buy_price")
C_SELL = COLS.index("sell_price")
HOUR_SLOTS = 12


@contextmanager
def own_interval_sell() -> Any:
    """Price each 5-minute slot after slot 0 from the entry covering it."""
    original = SlotBuilder._process_single_slot

    def wrapped(self: SlotBuilder, *args: Any, **kwargs: Any) -> Any:
        ctx, counts, in_dw = original(self, *args, **kwargs)
        i, slot = kwargs["i"], kwargs["slot"]
        if i >= 1 and slot["interval_minutes"] == 5:
            feed = kwargs.get("feed_in_forecast")
            if feed is None:
                feed = kwargs["data"].feed_in_forecast
            hit = find_current_entry(feed, slot["start"], self._ha_timezone)
            if hit is not None:
                ctx.sell_price = float(hit[0].get("per_kwh", 0.0))
        return ctx, counts, in_dw

    with patch.object(SlotBuilder, "_process_single_slot", wrapped):
        yield


def has_5min_feed_in(scenario: dict[str, Any]) -> bool:
    feed = scenario["input"].get("feed_in_forecast") or []
    return any(e.get("duration") == 5 for e in feed)


def cost(rows: list[list[Any]], true_sell: list[float], upto: int | None) -> float:
    """Import cost less export revenue, every slot at the same prices."""
    total = 0.0
    for row, sell in list(zip(rows, true_sell, strict=True))[:upto]:
        total += (row[C_IMP] or 0.0) * (row[C_BUY] or 0.0)
        total -= (row[C_EXP] or 0.0) * sell
    return total


def export_kwh(rows: list[list[Any]], upto: int | None) -> float:
    return sum((r[C_EXP] or 0.0) for r in rows[:upto])


def compare_arm(cur: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    a, b = cur["decisions"], new["decisions"]
    same_shape = len(a) == len(b) and all(
        x[C_TS] == y[C_TS] and x[C_MIN] == y[C_MIN] for x, y in zip(a, b, strict=True)
    )
    out: dict[str, Any] = {
        "slots": len(a),
        "same_shape": same_shape,
        "commanded": [cur["commanded_mode"], new["commanded_mode"]],
        "slot0": [cur["slot0_action"], new["slot0_action"]],
    }
    if not same_shape:
        return out
    # The own5 arm's sell column is the covering-entry price for every slot
    # but slot 0 is identical in both arms, and 30-minute slots are unchanged.
    true_sell = [row[C_SELL] or 0.0 for row in b]
    five = [i for i, r in enumerate(a) if r[C_MIN] == 5 and i >= 1]
    out["sell_changed_slots"] = sum(1 for i in five if a[i][C_SELL] != b[i][C_SELL])
    out["max_sell_gap"] = max(
        (abs(a[i][C_SELL] - b[i][C_SELL]) for i in five), default=0.0
    )
    out["action_changed_slots"] = [
        i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x[C_ACT] != y[C_ACT]
    ]
    out["plan_rows_changed"] = sum(
        1
        for x, y in zip(a, b, strict=True)
        if [v for k, v in enumerate(x) if k != C_SELL]
        != [v for k, v in enumerate(y) if k != C_SELL]
    )
    for name, upto in (("slot0", 1), ("hour", HOUR_SLOTS), ("horizon", None)):
        out[f"export_kwh_{name}"] = [export_kwh(a, upto), export_kwh(b, upto)]
        out[f"cost_{name}"] = [cost(a, true_sell, upto), cost(b, true_sell, upto)]
    out["hour_actions"] = [
        "".join(r[C_ACT][0] for r in a[:HOUR_SLOTS]),
        "".join(r[C_ACT][0] for r in b[:HOUR_SLOTS]),
    ]
    return out


def study(capture: Path) -> dict[str, Any]:
    scenario = json.loads(capture.read_text())
    result: dict[str, Any] = {
        "capture": capture.relative_to(REPO_ROOT).as_posix(),
        "five_min_feed_in": has_5min_feed_in(scenario),
        "arms": {},
    }
    for arm in rpb.ARMS:
        cur = rpb.replay(scenario, arm)
        with own_interval_sell():
            new = rpb.replay(scenario, arm)
        result["arms"][arm] = compare_arm(cur, new)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    captures = [
        c
        for c in rpb.tracked_captures()
        if not args.only or any(s in c.as_posix() for s in args.only)
    ]
    results = []
    for capture in captures:
        res = study(capture)
        results.append(res)
        for arm, r in res["arms"].items():
            flag = []
            if r["commanded"][0] != r["commanded"][1]:
                flag.append("COMMANDED")
            if r["slot0"][0] != r["slot0"][1]:
                flag.append("SLOT0")
            if r.get("action_changed_slots"):
                flag.append(f"actions@{r['action_changed_slots'][:8]}")
            if r.get("same_shape") is False:
                flag.append("SHAPE")
            dc = (
                r["cost_horizon"][1] - r["cost_horizon"][0]
                if "cost_horizon" in r
                else 0.0
            )
            dh = r["cost_hour"][1] - r["cost_hour"][0] if "cost_hour" in r else 0.0
            print(
                f"{res['capture']:<52}{arm:<10}5min={res['five_min_feed_in']!s:<6}"
                f"sellΔslots={r.get('sell_changed_slots', '-')!s:<3}"
                f"gap={r.get('max_sell_gap', 0.0):7.4f}  "
                f"Δcost hour={dh:+.4f} horizon={dc:+.4f}  {' '.join(flag)}"
            )
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1) + "\n")
    print(
        f"ran {len(results)} captures at {datetime.now().isoformat(timespec='seconds')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

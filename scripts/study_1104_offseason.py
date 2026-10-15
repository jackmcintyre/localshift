#!/usr/bin/env python3
"""study_1104_offseason.py — what lets the planner buy cheap ahead of a dear block
when #1201's switch is on and Amber flags no demand window?

Study harness for the #1104 follow-up. Not a gate, not wired into CI.

Every capture is replayed through ``replay_plan_baseline.replay`` (the real
planning path, horizon seeded, two passes) on the ``strict`` and ``live_like``
bases, under these arms:

  off        switch off: the clock window, 95% target (today)
  on         #1201 switch on, every Express flag false (October as it was)
  G25        on + cheap-price admission gate bypassed when no window is in view
  F25        on + futile-cycling penalty off when no window is in view
  A25..A00   on + gate bypassed + futile penalty off, cycle hurdle $0.25 .. $0
  P10/P05    on + gate bypassed + futile penalty counts only drain into slots
             priced below charge_price / round-trip efficiency, hurdle $0.10/$0.05

  Q/R/H/S    decompositions (see ARMS); Z* add the solar-sufficiency gate off
  on_clock   switch on with flags true 15:00-21:00 Sydney (synthetic in-season)

On the study branch the engine carries the proposal (``price_led_charging``),
so ``on`` there IS the proposal and equals the patched ``Z05`` arm on every
capture; run the patched arms against #1201's own head to reproduce them.

"No window in view" is literal: no plan slot carries ``is_demand_window_slot``.
With every flag false and the harness setting the feed directly (no reader
padding), that holds on every capture of every switch-on arm.

Scoring
-------
Raw plan cost misleads (an arm looks cheaper by finishing emptier), so each
arm is scored against a reference arm on the same capture as
``cost - ref_cost + (ref_end_soc - end_soc)% x capacity x price``, with the
end-of-plan charge valued at the plan's last buy price (``adj_last``) and at the
cheapest buy price in the horizon (``adj_min``). The truth sits between.

Sawtooth / flap
---------------
* ``overnight_kwh``: stored kWh grid-charged in slots starting 21:00-06:00.
* ``saw``: charge runs separated by a drain back to within 1 point of the plan's
  minimum SOC (charge, drain to floor, charge again) — the #800 signature.
* ``flap``: over one day's captures in time order, slot 0 goes charge / not /
  charge (the #1113 criterion). Reported beside the ``off`` arm as a control.

Closed loop (``--loop``): the synthetic 13 Oct-shaped day and the real 13 Oct
captures are replanned in sequence, the SOC carried from each plan to the next,
so slot-0 flapping and the realised cost of executing the plans are measured.

Usage
-----
  serial.sh uv run scripts/study_1104_offseason.py --json out.json
  serial.sh uv run scripts/study_1104_offseason.py --loop
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import defaultdict
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import replay_plan_baseline as rpb  # noqa: E402

from custom_components.localshift.engine import constraints, core  # noqa: E402
from custom_components.localshift.engine.types import PlannerAction  # noqa: E402

SYD = ZoneInfo("Australia/Sydney")
CAPACITY_KWH = 13.5
SIM = REPO_ROOT / "simulations"
CAPTURE_DIRS = (
    "replay-1120",
    "replay-1119",
    "replay-nodw-flap",
    "replay-nodw",
    "replay",
    "replay-1104-offseason",
)
COL = {name: i for i, name in enumerate(rpb.DECISION_COLUMNS)}

# --------------------------------------------------------------------------
# Arms
# --------------------------------------------------------------------------

ARMS: dict[str, dict[str, Any]] = {
    "off": {"switch": False},
    "on": {"switch": True},
    "G25": {"switch": True, "gate": False},
    "F25": {"switch": True, "futile": "off"},
    "A25": {"switch": True, "gate": False, "futile": "off"},
    "A10": {"switch": True, "gate": False, "futile": "off", "h": 0.10},
    "A05": {"switch": True, "gate": False, "futile": "off", "h": 0.05},
    "A00": {"switch": True, "gate": False, "futile": "off", "h": 0.0},
    "P10": {"switch": True, "gate": False, "futile": "priced", "h": 0.10},
    "P05": {"switch": True, "gate": False, "futile": "priced", "h": 0.05},
    # Q: P, but keeping the original's "cheaper charge window ahead" stop, so a
    # charge for a later dearer export (spike day) is not called futile.
    "Q05": {"switch": True, "gate": False, "futile": "priced_stop", "h": 0.05},
    "Q10": {"switch": True, "gate": False, "futile": "priced_stop", "h": 0.10},
    "Q00": {"switch": True, "gate": False, "futile": "priced_stop", "h": 0.0},
    # Decomposition: is the cheap-price gate bypass needed at all?
    "H05": {"switch": True, "h": 0.05},
    "R05": {"switch": True, "futile": "priced_stop", "h": 0.05},
    # Z: Q plus the global solar-sufficiency gate off when no window is in view.
    # With no deadline that gate simulates solar to the END of the horizon, so
    # tomorrow's midday solar "covers" tonight's deficit and strips every grid
    # charge from the feasible set today.
    # With the engine change on this branch, "on" IS the proposal; "on_clock" is the
    # synthetic in-season day (flags true 15:00-21:00 Sydney), to compare with "off".
    "on_clock": {"switch": True, "flags": "clock"},
    "ZS": {"switch": True, "suff": False},
    "Z25": {"switch": True, "gate": False, "futile": "priced_stop", "suff": False},
    "Z10": {
        "switch": True,
        "gate": False,
        "futile": "priced_stop",
        "h": 0.10,
        "suff": False,
    },
    "Z05": {
        "switch": True,
        "gate": False,
        "futile": "priced_stop",
        "h": 0.05,
        "suff": False,
    },
    "Z00": {
        "switch": True,
        "gate": False,
        "futile": "priced_stop",
        "h": 0.0,
        "suff": False,
    },
    # S: also the solar-opportunity penalty off when no window is in view.
    "Q05S": {
        "switch": True,
        "gate": False,
        "futile": "priced_stop",
        "h": 0.05,
        "solar": "off",
    },
    "Q00S": {
        "switch": True,
        "gate": False,
        "futile": "priced_stop",
        "h": 0.0,
        "solar": "off",
    },
    "A05S": {"switch": True, "gate": False, "futile": "off", "h": 0.05, "solar": "off"},
    "S25": {"switch": True, "solar": "off"},
}


def _no_window(slots: Any) -> bool:
    return not any(getattr(s, "is_demand_window_slot", False) for s in slots or [])


def _gate_bypass(orig):
    cache: dict[int, Any] = {}

    def wrapper(
        soc_pct, slot, config, slot_idx=0, slots=None, terminal_penalty_idx=None, **kw
    ):
        if terminal_penalty_idx is None and slots is not None and _no_window(slots):
            key = id(config)
            if key not in cache:
                cache.clear()
                cache[key] = (
                    config,
                    replace(config, effective_cheap_price=1e9, base_cheap_price=None),
                )
            config = cache[key][1]
        return orig(
            soc_pct,
            slot,
            config,
            slot_idx=slot_idx,
            slots=slots,
            terminal_penalty_idx=terminal_penalty_idx,
            **kw,
        )

    return wrapper


def _futile_off(orig):
    def wrapper(
        *,
        action,
        slot_idx,
        slots,
        config,
        soc_after_charge_pct,
        charge_kwh,
        terminal_penalty_idx=None,
    ):
        if terminal_penalty_idx is None and _no_window(slots):
            return 0.0
        return orig(
            action=action,
            slot_idx=slot_idx,
            slots=slots,
            config=config,
            soc_after_charge_pct=soc_after_charge_pct,
            charge_kwh=charge_kwh,
            terminal_penalty_idx=terminal_penalty_idx,
        )

    return wrapper


def _solar_off(orig):
    def wrapper(
        action,
        grid_import_kwh,
        slot,
        slot_idx,
        slots,
        config,
        terminal_penalty_idx,
        all_solcast=None,
    ):
        if terminal_penalty_idx is None and _no_window(slots):
            return 0.0
        return orig(
            action=action,
            grid_import_kwh=grid_import_kwh,
            slot=slot,
            slot_idx=slot_idx,
            slots=slots,
            config=config,
            terminal_penalty_idx=terminal_penalty_idx,
            all_solcast=all_solcast,
        )

    return wrapper


def _futile_priced(orig, keep_cheaper_stop: bool = False):
    """Drain into a slot dearer than charge_price / round-trip is not futile."""

    def wrapper(
        *,
        action,
        slot_idx,
        slots,
        config,
        soc_after_charge_pct,
        charge_kwh,
        terminal_penalty_idx=None,
    ):
        if not (terminal_penalty_idx is None and _no_window(slots)):
            return orig(
                action=action,
                slot_idx=slot_idx,
                slots=slots,
                config=config,
                soc_after_charge_pct=soc_after_charge_pct,
                charge_kwh=charge_kwh,
                terminal_penalty_idx=terminal_penalty_idx,
            )
        if action not in (
            PlannerAction.CHARGE_GRID_NORMAL,
            PlannerAction.CHARGE_GRID_BOOST,
        ):
            return 0.0
        if charge_kwh <= 0.0:
            return 0.0
        rt = config.charge_efficiency * config.discharge_efficiency
        worth = slots[slot_idx].buy_price / rt
        soc = soc_after_charge_pct
        futile = 0.0
        charge_price = slots[slot_idx].buy_price
        for fut in slots[slot_idx + 1 :]:
            if fut.solar_kwh > fut.consumption_kwh:
                break
            if keep_cheaper_stop and fut.buy_price < charge_price - 0.02:
                break
            net = fut.consumption_kwh - fut.solar_kwh
            if net <= 0.0:
                continue
            avail = max(
                0.0,
                (soc - config.discharge_floor_pct)
                / 100.0
                * config.battery_capacity_kwh,
            )
            used = min(net, avail * config.discharge_efficiency)
            if used <= 0.0:
                break
            soc -= (
                used / config.discharge_efficiency / config.battery_capacity_kwh * 100.0
            )
            if fut.buy_price < worth:
                futile += used
            if soc <= config.discharge_floor_pct:
                break
        return min(1.0, futile / charge_kwh)

    return wrapper


@contextmanager
def candidate(arm: dict[str, Any]) -> Iterator[None]:
    with ExitStack() as stack:
        if arm.get("gate") is False:
            stack.enter_context(
                patch.object(
                    core,
                    "_constraints_feasible_actions",
                    _gate_bypass(core._constraints_feasible_actions),
                )
            )
        futile = arm.get("futile")
        if futile == "off":
            stack.enter_context(
                patch.object(
                    core,
                    "get_futile_cycling_penalty_factor",
                    _futile_off(core.get_futile_cycling_penalty_factor),
                )
            )
        elif futile == "priced":
            stack.enter_context(
                patch.object(
                    core,
                    "get_futile_cycling_penalty_factor",
                    _futile_priced(core.get_futile_cycling_penalty_factor),
                )
            )
        elif futile == "priced_stop":
            stack.enter_context(
                patch.object(
                    core,
                    "get_futile_cycling_penalty_factor",
                    _futile_priced(
                        core.get_futile_cycling_penalty_factor, keep_cheaper_stop=True
                    ),
                )
            )
        if arm.get("suff") is False:
            orig_suff = constraints.check_global_solar_sufficiency

            def suff(soc_pct, slot_idx, slots, config, terminal_penalty_idx=None):
                if terminal_penalty_idx is None and _no_window(slots):
                    return False
                return orig_suff(soc_pct, slot_idx, slots, config, terminal_penalty_idx)

            stack.enter_context(
                patch.object(constraints, "check_global_solar_sufficiency", suff)
            )
        if arm.get("solar") == "off":
            stack.enter_context(
                patch.object(
                    core,
                    "get_solar_opportunity_penalty_factor",
                    _solar_off(core.get_solar_opportunity_penalty_factor),
                )
            )
        yield


def _replay_record(
    scenario: dict[str, Any], base: str, arm_name: str
) -> dict[str, Any]:
    arm = ARMS[arm_name]
    scenario = copy.deepcopy(scenario)
    opts, switches = rpb.ARMS[base]
    opts, switches = dict(opts), dict(switches)
    if arm["switch"]:
        switches["demand_window_from_amber"] = True
        for entry in scenario["input"]["general_forecast"]:
            if arm.get("flags") == "clock":
                hour = datetime.fromisoformat(entry["start_time"]).astimezone(SYD).hour
                entry["demand_window"] = 15 <= hour < 21
            else:
                entry["demand_window"] = False
    if "h" in arm:
        opts["min_cycle_saving"] = arm["h"]
    saved = rpb.ARMS[base]
    rpb.ARMS[base] = (opts, switches)
    try:
        with candidate(arm):
            return rpb.replay(scenario, base)
    finally:
        rpb.ARMS[base] = saved


def run(scenario: dict[str, Any], base: str, arm_name: str) -> dict[str, Any]:
    return metrics(_replay_record(scenario, base, arm_name))


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def _is_charge(action: Any) -> bool:
    return str(action).startswith("charge_grid")


def metrics(record: dict[str, Any]) -> dict[str, Any]:
    rows = record["decisions"]
    if not rows:
        return {"ok": False}
    cost = sum(
        r[COL["grid_import_kwh"]] * r[COL["buy_price"]]
        - r[COL["grid_export_kwh"]] * r[COL["sell_price"]]
        for r in rows
    )
    socs = [r[COL["predicted_soc_pct"]] for r in rows]
    floor = min(socs)
    prev = None
    charged = overnight = 0.0
    runs = saw = 0
    in_run = False
    drained_since_run = False
    for i, r in enumerate(rows):
        soc = r[COL["predicted_soc_pct"]]
        start = socs[i - 1] if i else None
        charging = _is_charge(r[COL["action"]])
        if charging:
            gain = (
                max(0.0, soc - (start if start is not None else soc))
                / 100
                * CAPACITY_KWH
            )
            charged += gain
            hour = datetime.fromisoformat(r[COL["timestamp_iso"]]).astimezone(SYD).hour
            if hour >= 21 or hour < 6:
                overnight += gain
            if not in_run:
                runs += 1
                if runs > 1 and drained_since_run:
                    saw += 1
                drained_since_run = False
        elif soc <= floor + 1.0:
            drained_since_run = True
        in_run = charging
        prev = r
    del prev
    imp_by_band = defaultdict(float)
    for r in rows:
        hour = datetime.fromisoformat(r[COL["timestamp_iso"]]).astimezone(SYD).hour
        band = "pre15" if 6 <= hour < 15 else ("15_21" if 15 <= hour < 21 else "night")
        imp_by_band[band] += r[COL["grid_import_kwh"]]
    return {
        "ok": True,
        "cost": cost,
        "end_soc": socs[-1],
        "last_buy": rows[-1][COL["buy_price"]],
        "min_buy": min(r[COL["buy_price"]] for r in rows),
        "slot0": rows[0][COL["action"]],
        "charged_kwh": charged,
        "overnight_kwh": overnight,
        "charge_runs": runs,
        "saw": saw,
        "imp_pre15": imp_by_band["pre15"],
        "imp_15_21": imp_by_band["15_21"],
        "imp_night": imp_by_band["night"],
        "rows": [
            [
                r[COL["timestamp_iso"]],
                r[COL["action"]],
                round(r[COL["predicted_soc_pct"]], 1),
                round(r[COL["buy_price"]], 3),
                round(r[COL["grid_import_kwh"]], 2),
            ]
            for r in rows
        ],
    }


def adjusted(m: dict[str, Any], ref: dict[str, Any]) -> tuple[float, float, float]:
    raw = m["cost"] - ref["cost"]
    d_kwh = (ref["end_soc"] - m["end_soc"]) / 100.0 * CAPACITY_KWH
    return raw, raw + d_kwh * ref["last_buy"], raw + d_kwh * ref["min_buy"]


# --------------------------------------------------------------------------
# Captures
# --------------------------------------------------------------------------


def load_captures() -> list[tuple[str, dict[str, Any]]]:
    caps: list[tuple[str, dict[str, Any]]] = []
    for d in CAPTURE_DIRS:
        for p in sorted((SIM / d).glob("*.json")):
            caps.append((f"{d}/{p.stem}", json.loads(p.read_text())))
    # Live has Solcast tomorrow; a bare capture does not. Give each capture the
    # next day's measured solar where another capture of that day exists.
    by_day: dict[str, list[dict]] = {}
    for _, sc in caps:
        day = (
            datetime
            .fromisoformat(sc["input"]["test_time"])
            .astimezone(SYD)
            .date()
            .isoformat()
        )
        if sc["input"].get("solcast_today"):
            by_day.setdefault(day, sc["input"]["solcast_today"])
    for _, sc in caps:
        day = datetime.fromisoformat(sc["input"]["test_time"]).astimezone(SYD).date()
        nxt = (day + timedelta(days=1)).isoformat()
        if not sc["input"].get("solcast_tomorrow") and nxt in by_day:
            sc["input"]["solcast_tomorrow"] = by_day[nxt]
    return caps


# --------------------------------------------------------------------------
# Main sweep
# --------------------------------------------------------------------------


def sweep(arms: list[str], bases: list[str], only: list[str]) -> dict[str, Any]:
    caps = [c for c in load_captures() if not only or any(o in c[0] for o in only)]
    out: dict[str, Any] = {}
    for name, sc in caps:
        for base in bases:
            for arm in arms:
                try:
                    out[f"{name}|{base}|{arm}"] = run(sc, base, arm)
                except Exception as exc:  # noqa: BLE001
                    out[f"{name}|{base}|{arm}"] = {"ok": False, "error": repr(exc)}
        print(f"done {name}", file=sys.stderr, flush=True)
    return out


def _day_of(name: str) -> str:
    stem = name.split("/")[-1]
    return stem[:10]


def report(results: dict[str, Any], arms: list[str], bases: list[str]) -> None:
    caps = sorted({k.split("|")[0] for k in results})
    for base in bases:
        print(f"\n=== base {base} ===")
        print(
            f"{'arm':<6}{'n':>4}{'raw vs off':>12}{'adjL vs off':>13}{'adjM vs off':>13}{'adjL vs on':>12}{'adjM vs on':>12}"
            f"{'chg kWh':>9}{'night':>7}{'saw':>5}{'imp<15':>8}{'imp15-21':>9}{'flap':>6}"
        )
        for arm in arms:
            n = 0
            tot = defaultdict(float)
            for cap in caps:
                m = results.get(f"{cap}|{base}|{arm}")
                off = results.get(f"{cap}|{base}|off")
                on = results.get(f"{cap}|{base}|on")
                if not (
                    m and m.get("ok") and off and off.get("ok") and on and on.get("ok")
                ):
                    continue
                n += 1
                r, al, am = adjusted(m, off)
                _, bl, bm = adjusted(m, on)
                tot["raw"] += r
                tot["al"] += al
                tot["am"] += am
                tot["bl"] += bl
                tot["bm"] += bm
                for k in (
                    "charged_kwh",
                    "overnight_kwh",
                    "saw",
                    "imp_pre15",
                    "imp_15_21",
                ):
                    tot[k] += m[k]
            flaps = count_flaps(results, base, arm)
            print(
                f"{arm:<6}{n:>4}{tot['raw']:>12.2f}{tot['al']:>13.2f}{tot['am']:>13.2f}{tot['bl']:>12.2f}{tot['bm']:>12.2f}"
                f"{tot['charged_kwh']:>9.1f}{tot['overnight_kwh']:>7.1f}{int(tot['saw']):>5}{tot['imp_pre15']:>8.1f}{tot['imp_15_21']:>9.1f}{flaps:>6}"
            )


def count_flaps(results: dict[str, Any], base: str, arm: str) -> int:
    seqs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key, m in results.items():
        cap, b, a = key.split("|")
        if b != base or a != arm or not m.get("ok"):
            continue
        seqs[cap.split("/")[0] + ":" + _day_of(cap)].append((cap, m["slot0"]))
    flaps = 0
    for seq in seqs.values():
        seq.sort()
        runs, prev = 0, False
        for _, act in seq:
            ch = _is_charge(act)
            if ch and not prev:
                runs += 1
            prev = ch
        flaps += runs > 1
    return flaps


# --------------------------------------------------------------------------
# Closed loop: the synthetic 13 Oct-shaped day and the real 13 Oct captures
# --------------------------------------------------------------------------

SHAPE_RAW = SIM / "replay-1104-offseason" / "shape" / "2026-10-13-raw.json"
DAY13 = SIM / "replay-1104-offseason"


def _parse_states(states: list[dict[str, str]]) -> list[tuple[datetime, float]]:
    out = []
    for st in states:
        try:
            out.append((datetime.fromisoformat(st["t"]), float(st["v"])))
        except (TypeError, ValueError):
            continue
    out.sort()
    return out


def _value_at(series: list[tuple[datetime, float]], when: datetime) -> float:
    best = series[0][1]
    for t, v in series:
        if t <= when:
            best = v
        else:
            break
    return best


def _bucket_forecast(entries: list[dict[str, Any]]) -> dict[datetime, float]:
    sums: dict[datetime, list[float]] = defaultdict(list)
    for e in entries:
        start = datetime.fromisoformat(e["start_time"]).astimezone(SYD)
        start = start.replace(second=0, microsecond=0)
        half = start.replace(minute=0 if start.minute < 30 else 30)
        sums[half].append(float(e["per_kwh"]))
    return {k: sum(v) / len(v) for k, v in sums.items()}


def shape_profile() -> tuple[list[tuple[datetime, float, float]], dict[str, Any]]:
    """30-min (start, buy, sell) for 13 Oct 00:00 -> 14 Oct 16:00, Sydney.

    Before 14:30 on 13 Oct: settled Express prices (mean of the 5-min values).
    From 14:30: base Amber's forecast as of 14:24 on 13 Oct, the one the issue
    quotes (cheapest 15:30-16:30, dear 17:30-21:30). Past its end: held flat.
    """
    raw = json.loads(SHAPE_RAW.read_text())
    gen = _parse_states(raw["settled_general"])
    fin = _parse_states(raw["settled_feed_in"])
    fc_gen = _bucket_forecast(raw["base_general_fc"])
    fc_fin = _bucket_forecast(raw["base_feed_in_fc"])
    switch = datetime(2026, 10, 13, 14, 30, tzinfo=SYD)
    t = datetime(2026, 10, 13, 0, 0, tzinfo=SYD)
    end = datetime(2026, 10, 14, 16, 0, tzinfo=SYD)
    out = []
    last_b = last_s = 0.0
    while t < end:
        if t < switch:
            pts = [t + timedelta(minutes=5 * k) for k in range(6)]
            b = sum(_value_at(gen, p) for p in pts) / 6
            s = sum(_value_at(fin, p) for p in pts) / 6
        else:
            b = fc_gen.get(t, last_b)
            s = fc_fin.get(t, last_s)
        last_b, last_s = b, s
        out.append((t, b, s))
        t += timedelta(minutes=30)
    return out, raw


def _jitter(t: datetime, at: datetime, amp: float) -> float:
    """Deterministic pseudo-noise in [-amp, amp], different per (slot, replan)."""
    h = (int(t.timestamp()) // 60 * 7919 + int(at.timestamp()) // 60 * 104729) % 1000
    return amp * (h / 500.0 - 1.0)


def synthetic_scenario(
    at: datetime, soc: float, template: dict[str, Any], jitter: float = 0.0
) -> dict[str, Any]:
    profile, _ = shape_profile()
    gen, fin = [], []
    for t, b, s in profile:
        if t < at or t >= at + timedelta(hours=24):
            continue
        n = _jitter(t, at, jitter) if t > at else 0.0
        gen.append({
            "start_time": t.isoformat(),
            "duration": 30,
            "per_kwh": round(b + n, 6),
            "spike_status": "none",
        })
        fin.append({
            "start_time": t.isoformat(),
            "duration": 30,
            "per_kwh": round(s + n, 6),
            "spike_status": "none",
        })
    sc = copy.deepcopy(template)
    sc["name"] = f"synthetic-13oct-{at:%H%M}"
    sc["input"]["test_time"] = at.isoformat()
    sc["input"]["soc"] = soc
    sc["input"]["general_forecast"] = gen
    sc["input"]["feed_in_forecast"] = fin
    sc["input"]["general_price"] = gen[0]["per_kwh"]
    sc["input"]["feed_in_price"] = fin[0]["per_kwh"]
    return sc


def _replay_rows(sc: dict[str, Any], base: str, arm_name: str) -> list[list[Any]]:
    return _replay_record(sc, base, arm_name)["decisions"]


def loop(arm: str, base: str, times: list[datetime], make, price) -> dict[str, Any]:
    """Replan at each time, carrying SOC; execute each plan until the next replan."""
    soc = None
    total = 0.0
    slot0: list[str] = []
    broken = 0
    trace = []
    prev_rows = None
    for k, at in enumerate(times):
        sc = make(at, soc)
        soc_in = sc["input"]["soc"]
        rows = _replay_rows(sc, base, arm)
        now0 = datetime.fromisoformat(rows[0][COL["timestamp_iso"]])
        if prev_rows is not None:
            # Did the previous plan say something different (charge vs not) about now?
            said = [
                r[COL["action"]]
                for r in prev_rows
                if abs(
                    (
                        datetime.fromisoformat(r[COL["timestamp_iso"]]) - now0
                    ).total_seconds()
                )
                < 60
            ]
            if said and _is_charge(said[0]) != _is_charge(rows[0][COL["action"]]):
                broken += 1
        until = times[k + 1] if k + 1 < len(times) else at + timedelta(minutes=30)
        cost = 0.0
        soc_out = None
        for r in rows:
            t = datetime.fromisoformat(r[COL["timestamp_iso"]])
            if t < at - timedelta(minutes=1) or t >= until:
                continue
            b, s = price(t, r)
            cost += r[COL["grid_import_kwh"]] * b - r[COL["grid_export_kwh"]] * s
            soc_out = r[COL["predicted_soc_pct"]]
        total += cost
        slot0.append(rows[0][COL["action"]])
        trace.append((
            at.astimezone(SYD).strftime("%H:%M"),
            round(soc_in, 1),
            rows[0][COL["action"]],
            round(cost, 3),
        ))
        soc = soc_out if soc_out is not None else soc_in
        prev_rows = rows
    runs, prevc = 0, False
    for a in slot0:
        ch = _is_charge(a)
        runs += ch and not prevc
        prevc = ch
    return {
        "cost": total,
        "end_soc": soc,
        "charge_runs_slot0": runs,
        "broken_promises": broken,
        "trace": trace,
    }


def run_loops(arms: list[str], bases: list[str]) -> dict[str, Any]:
    template = json.loads((DAY13 / "2026-10-13T1424.json").read_text())
    tomorrow = json.loads((DAY13 / "2026-10-14T0900.json").read_text())
    template["input"]["solcast_tomorrow"] = tomorrow["input"]["solcast_today"]
    profile, raw = shape_profile()
    prof = {t: (b, s) for t, b, s in profile}

    def synth_price(t, r):
        t = t.astimezone(SYD)
        key = t.replace(minute=0 if t.minute < 30 else 30, second=0, microsecond=0)
        return prof.get(key, (r[COL["buy_price"]], r[COL["sell_price"]]))

    gen = _parse_states(raw["settled_general"])
    fin = _parse_states(raw["settled_feed_in"])

    def settled_price(t, r):
        return _value_at(gen, t), _value_at(fin, t)

    start = datetime(2026, 10, 13, 9, 0, tzinfo=SYD)
    synth_times = [start + timedelta(minutes=30 * k) for k in range(44)]  # to 06:30
    specs = [
        "0900",
        "1030",
        "1130",
        "1230",
        "1330",
        "1424",
        "1505",
        "1600",
        "1700",
        "1800",
        "1900",
        "2000",
        "2130",
    ]
    real = [json.loads((DAY13 / f"2026-10-13T{s}.json").read_text()) for s in specs]
    for sc in real:
        sc["input"]["solcast_tomorrow"] = tomorrow["input"]["solcast_today"]
    by_time = {datetime.fromisoformat(sc["input"]["test_time"]): sc for sc in real}
    real_times = sorted(by_time)

    def make_real(at, soc):
        sc = copy.deepcopy(by_time[at])
        if soc is not None:
            sc["input"]["soc"] = soc
        return sc

    out: dict[str, Any] = {}
    for base in bases:
        for arm in arms:
            for label, jit in (("synthetic", 0.0), ("synthetic_jitter2c", 0.02)):

                def make(at, soc, jit=jit):
                    return synthetic_scenario(
                        at, 10.0 if soc is None else soc, template, jit
                    )

                out[f"{label}|{base}|{arm}"] = loop(
                    arm, base, synth_times, make, synth_price
                )
            out[f"real13|{base}|{arm}"] = loop(
                arm, base, real_times, make_real, settled_price
            )
            print(f"loop done {base} {arm}", file=sys.stderr, flush=True)
    return out


def report_loops(res: dict[str, Any]) -> None:
    print(
        f"\n{'run':<20}{'base':<10}{'arm':<6}{'cost':>8}{'end%':>7}{'adj@20c':>9}{'chg runs':>9}{'broken':>8}"
    )
    for key, m in res.items():
        label, base, arm = key.split("|")
        adj = m["cost"] - (m["end_soc"] - 10.0) / 100 * CAPACITY_KWH * 0.20
        print(
            f"{label:<20}{base:<10}{arm:<6}{m['cost']:>8.2f}{m['end_soc']:>7.1f}{adj:>9.2f}"
            f"{m['charge_runs_slot0']:>9}{m['broken_promises']:>8}"
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--bases", default="strict,live_like")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--json")
    ap.add_argument("--from-json")
    ap.add_argument("--loop", action="store_true")
    args = ap.parse_args()
    rpb.quiet_logging()
    arms = args.arms.split(",")
    bases = args.bases.split(",")
    if args.loop:
        res = run_loops(arms, bases)
        if args.json:
            Path(args.json).write_text(json.dumps(res))
        report_loops(res)
        return 0
    if args.from_json:
        results = json.loads(Path(args.from_json).read_text())
    else:
        results = sweep(arms, bases, args.only)
        if args.json:
            Path(args.json).write_text(json.dumps(results))
    report(results, arms, bases)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

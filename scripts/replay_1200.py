#!/usr/bin/env python3
"""replay_1200.py — does current main still export at 1-7c feed-in? (issue #1200)

On Sunday 2026-10-11, in ``self_consumption`` mode and before the #1116 export
floor, the planner committed PROACTIVE_EXPORT nine times between 07:35 and
11:20 at feed-in prices of 1-7c, taking the battery to the 5% floor while it
expected solar to refill it. This captures the optimizer inputs at the instants
the mode was committed and replays them through the current planning path
(``scripts/replay_plan_baseline.replay``) under its ``captured`` and
``live_like`` arms.

What a capture holds, and where each part came from
---------------------------------------------------
* Prices: the Amber sensors' ``forecast`` attribute as it stood at the
  instant, read with every update retained (``significant_changes_only=0``)
  so the attribute-only confirmations are seen, plus the current interval's
  ``estimate`` flag. ``detailedForecast`` is not retained by the recorder.
* Solar: ``solcast_today`` is the Solcast forecast rebuilt from the recorded
  history of ``sensor.solcast_pv_forecast_power_now`` (its ``estimate``,
  ``estimate10`` and ``estimate90`` attributes, in W, sampled every 5 minutes)
  averaged into Solcast's 30-minute periods. The recorder drops Solcast's
  ``detailedForecast`` list, but this sensor publishes the forecast value for
  "now" every 5 minutes, so the day's forecast survives one point at a time.
  Caveat: a point recorded after a later Solcast poll carries that poll's
  forecast. On 11 Oct Solcast polled at 04:48 and 09:36, and the day total moved
  from 44.18 to 43.99 kWh, so the difference is small. The measured series
  (``scripts/export_replay_days.py``) is kept alongside as
  ``solcast_today_measured`` and used by the ``--solar measured`` variant.
* SOC, load, solar, battery and grid power: the Powerwall sensors at the
  instant.
* Configuration: the LocalShift number, switch and select entities as recorded
  at the instant (see ``RECORDED_CONFIG``), and the battery mode committed just
  before it.

Usage
-----
  uv run scripts/replay_1200.py capture 2026-10-11T07:35:35+11:00 ...
  uv run scripts/replay_1200.py replay
  uv run scripts/replay_1200.py replay --solar measured
  uv run scripts/replay_1200.py replay --neg-scale 2   # deeper negative midday
  uv run scripts/replay_1200.py replay --slots 2026-10-11T073535 --arm captured
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

DEFAULT_DIR = REPO_ROOT / "simulations" / "replay-1200"
BATTERY_MODE = "select.localshift_battery_mode"
SOLCAST_NOW = "sensor.solcast_pv_forecast_power_now"

# number/switch/select entity -> (kind, option or switch key). Read at the
# instant, so the captured arm runs the configuration that was live.
RECORDED_CONFIG: dict[str, tuple[str, str]] = {
    "number.localshift_battery_target": ("option", "battery_target"),
    "number.localshift_minimum_target_soc": ("option", "minimum_target_soc"),
    "number.localshift_cheap_price_percentile": ("option", "cheap_price_percentile"),
    "number.localshift_max_pre_charge_price": ("option", "max_pre_charge_price"),
    "number.localshift_min_cycle_saving": ("option", "min_cycle_saving"),
    "number.localshift_min_hold_saving": ("option", "min_hold_saving"),
    "number.localshift_switching_penalty": ("option", "switching_penalty"),
    "number.localshift_switching_penalty_per_kwh": (
        "option",
        "switching_penalty_per_kwh",
    ),
    "number.localshift_target_shortfall_penalty": ("option", "target_penalty"),
    "number.localshift_pre_charge_runway_margin": (
        "option",
        "precharge_runway_margin_min",
    ),
    "number.localshift_stale_solar_confidence_ceiling": (
        "option",
        "stale_solar_confidence_ceiling",
    ),
    "select.localshift_optimization_mode": ("option", "optimization_mode"),
    "switch.localshift_allow_dw_entry_under_target": (
        "switch",
        "allow_dw_entry_under_target",
    ),
    "switch.localshift_automation_enabled": ("switch", "automation_enabled"),
    "switch.localshift_demand_window_block": ("switch", "demand_window_block"),
    "switch.localshift_spike_discharge_enabled": ("switch", "spike_discharge_enabled"),
    "switch.localshift_spike_discharge_conservative": (
        "switch",
        "spike_discharge_conservative",
    ),
    "switch.localshift_stale_solar_conservative": (
        "switch",
        "stale_solar_conservative",
    ),
}


# --------------------------------------------------------------------------
# Capture (read-only: Home Assistant history API)
# --------------------------------------------------------------------------


def _history_all(
    erd: Any, url: str, token: str, entities: list[str], start: datetime, end: datetime
) -> dict[str, list[dict]]:
    """History with every update retained, attributes included."""
    qs = urllib.parse.urlencode({
        "filter_entity_id": ",".join(entities),
        "end_time": end.isoformat(),
        "significant_changes_only": "0",
    })
    blocks = erd.api_get(url, token, f"/api/history/period/{start.isoformat()}?{qs}")
    return {block[0]["entity_id"]: block for block in blocks if block}


def solcast_from_power_now(samples: list[dict], day_start: datetime) -> list[dict]:
    """Rebuild Solcast's 30-minute forecast from the power_now sensor's history.

    Each recorded state holds the forecast kW for that moment (P50, P10, P90 in
    W). The mean of the points inside a period is that period's average power,
    which is what ``pv_estimate`` means. Periods with no point are zero.
    """
    buckets: dict[int, list[tuple[float, float, float]]] = {}
    for st in samples:
        raw = st.get("last_updated") or st.get("last_changed")
        attrs = st.get("attributes") or {}
        try:
            p50 = float(attrs.get("estimate", st["state"]))
            p10 = float(attrs.get("estimate10", p50))
            p90 = float(attrs.get("estimate90", p50))
        except (KeyError, TypeError, ValueError):
            continue
        when = datetime.fromisoformat(raw)
        idx = int((when - day_start).total_seconds() // 1800)
        if 0 <= idx < 48:
            buckets.setdefault(idx, []).append((p50, p10, p90))

    series: list[dict] = []
    for idx in range(48):
        vals = buckets.get(idx, [])
        n = len(vals) or 1
        series.append({
            "period_start": (day_start + timedelta(minutes=30 * idx)).isoformat(),
            "pv_estimate": round(sum(v[0] for v in vals) / n / 1000.0, 4),
            "pv_estimate10": round(sum(v[1] for v in vals) / n / 1000.0, 4),
            "pv_estimate90": round(sum(v[2] for v in vals) / n / 1000.0, 4),
        })
    return series


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
        inp = scenario["input"]
        day_start = at.replace(hour=0, minute=0, second=0, microsecond=0)

        # Prices, with the attribute-only confirmation updates retained.
        points = _history_all(
            erd,
            url,
            token,
            [erd.PRICE_GENERAL, erd.PRICE_FEED_IN],
            at - timedelta(minutes=25),
            at,
        )
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

        # Solar: the forecast rebuilt from power_now; measured kept alongside.
        solcast_hist = _history_all(
            erd, url, token, [SOLCAST_NOW], day_start, day_start + timedelta(days=1)
        )
        inp["solcast_today_measured"] = inp["solcast_today"]
        inp["solcast_today"] = solcast_from_power_now(
            solcast_hist.get(SOLCAST_NOW, []), day_start
        )
        inp["solar_source"] = "solcast_power_now"
        inp["solcast_rebuilt_kwh"] = round(
            sum(s["pv_estimate"] for s in inp["solcast_today"]) * 0.5, 2
        )

        # Configuration and committed mode, as recorded at the instant.
        cfg = erd.history(
            url, token, [*RECORDED_CONFIG, BATTERY_MODE], at - timedelta(days=3), at
        )
        options = dict(scenario["config_overrides"])
        switches = dict(scenario["switch_states"])
        for entity, (kind, key) in RECORDED_CONFIG.items():
            st = erd.latest_at_or_before(cfg.get(entity, []), at)
            if not st or st["state"] in ("unknown", "unavailable"):
                continue
            if kind == "switch":
                switches[key] = st["state"] == "on"
                if key in options:
                    options[key] = switches[key]
            elif entity.startswith("select."):
                options[key] = st["state"]
            else:
                value = float(st["state"])
                options[key] = int(value) if value.is_integer() else value
        scenario["config_overrides"] = options
        scenario["switch_states"] = switches
        mode = erd.latest_at_or_before(
            cfg.get(BATTERY_MODE, []), at - timedelta(seconds=1)
        )
        inp["active_mode"] = mode["state"] if mode else "self_consumption"

        stamp = at.strftime("%Y-%m-%dT%H%M%S")
        scenario["name"] = f"replay-1200-{stamp}"
        scenario["description"] = (
            f"Real optimizer inputs at {at.isoformat()} (issue #1200). Prices are "
            "the Amber forecast attribute as recorded; solar is the Solcast "
            "forecast rebuilt from sensor.solcast_pv_forecast_power_now history "
            "(measured solar kept as solcast_today_measured); configuration is "
            "the LocalShift entities as recorded. Generated by "
            "scripts/replay_1200.py - do not hand-edit."
        )
        path = out_dir / f"{stamp}.json"
        path.write_text(json.dumps(scenario, indent=2) + "\n")
        print(
            f"  wrote {path.name}  soc={inp['soc']}  buy={inp['general_price']:.4f}  "
            f"sell={inp['feed_in_price']:.4f}  mode_before={inp['active_mode']}  "
            f"solcast={inp['solcast_rebuilt_kwh']}kWh  measured={inp['solar_measured_kwh']}kWh  "
            f"opt={options.get('optimization_mode')}"
        )
    return 0


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------


def _scenario(path: Path, solar: str, neg_scale: float = 1.0) -> dict[str, Any]:
    """Load a capture, with the solar source chosen and, for a sensitivity
    probe, every negative feed-in forecast price multiplied by ``neg_scale``."""
    scenario = json.loads(path.read_text())
    inp = scenario["input"]
    if solar == "measured":
        inp["solcast_today"] = inp["solcast_today_measured"]
    if neg_scale != 1.0:
        for slot in inp["feed_in_forecast"]:
            if slot["per_kwh"] < 0:
                slot["per_kwh"] = round(slot["per_kwh"] * neg_scale, 6)
    return scenario


def summarise(record: dict[str, Any], test_time: datetime) -> dict[str, Any]:
    """Slot 0 and the plan's export, from a replay_plan_baseline record."""
    import replay_plan_baseline as rpb

    cols = list(rpb.DECISION_COLUMNS)
    rows = [dict(zip(cols, r, strict=True)) for r in record["decisions"]]
    d0 = rows[0] if rows else {}
    dw_start = test_time.replace(hour=15, minute=0, second=0, microsecond=0)
    export_total = sum(r["grid_export_kwh"] or 0.0 for r in rows)
    export_before_dw = sum(
        r["grid_export_kwh"] or 0.0
        for r in rows
        if datetime.fromisoformat(r["timestamp_iso"]) < dw_start
    )
    export_actions = [r for r in rows if r["action"] and "export" in r["action"]]
    min_soc = min((r["predicted_soc_pct"] for r in rows), default=None)
    return {
        "commanded_mode": record["commanded_mode"],
        "slot0_action": d0.get("action"),
        "slot0_reason": d0.get("reason_code"),
        "slot0_buy": d0.get("buy_price"),
        "slot0_sell": d0.get("sell_price"),
        "slot0_export_kwh": d0.get("grid_export_kwh"),
        "plan_export_kwh": round(export_total, 3),
        "plan_export_before_15_kwh": round(export_before_dw, 3),
        "export_action_slots": len(export_actions),
        # Grid export in slots whose action is an export: the battery being
        # discharged to the grid. plan_export_kwh also counts solar spill in
        # hold slots once the battery is full.
        "export_action_kwh": round(
            sum(r["grid_export_kwh"] or 0.0 for r in export_actions), 3
        ),
        "min_predicted_soc": min_soc,
        "export_floor_pct": record["data"].get("planner_export_floor_pct"),
        "solar_can_reach_target": record["data"].get("solar_can_reach_target"),
        "mode_source": record["data"].get("debug_mode_source"),
        # The #1103 gate (absent on code before #1121): dollars of spill the
        # negative-FIT window would cost given the battery, the exposure with
        # the battery ignored, the threshold, and whether the window is open.
        "nfit_spill_cost": record["summary"].get("negative_fit_spill_cost"),
        "nfit_spill_exposure": record["summary"].get("negative_fit_spill_exposure"),
        "nfit_threshold": record["summary"].get("negative_fit_spill_threshold"),
        "nfit_window_open": record["summary"].get("negative_fit_window_open"),
    }


def _money(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def replay(
    in_dir: Path, solar: str, json_out: str | None, neg_scale: float = 1.0
) -> int:
    import replay_plan_baseline as rpb

    files = sorted(in_dir.glob("*.json"))
    if not files:
        print(f"no captures in {in_dir}")
        return 1
    print(f"solar: {solar}  negative feed-in x{neg_scale}")
    print(
        f"{'instant':<9}{'live':<11}{'soc':>6}{'buy':>8}{'sell':>8} "
        f"{'arm':<10}{'mode':<17}{'slot0':<17}{'reason':<26}"
        f"{'s0 exp':>7}{'plan exp':>9}{'<15:00':>8}{'exp act':>8}{'minSOC':>7}"
        f"{'spill$':>8}{'expo$':>8}{'open':>6}"
    )
    rows = []
    for path in files:
        scenario = _scenario(path, solar, neg_scale)
        inp = scenario["input"]
        at = datetime.fromisoformat(inp["test_time"])
        for arm in ("captured", "live_like"):
            s = summarise(rpb.replay(scenario, arm), at)
            print(
                f"{at.strftime('%H:%M:%S'):<9}{inp.get('active_mode', '?')[:10]:<11}"
                f"{inp['soc']:>6.1f}{inp['general_price']:>8.4f}{inp['feed_in_price']:>8.4f} "
                f"{arm:<10}{str(s['commanded_mode']):<17}{str(s['slot0_action']):<17}"
                f"{str(s['slot0_reason'])[:25]:<26}"
                f"{s['slot0_export_kwh'] or 0:>7.3f}{s['plan_export_kwh']:>9.3f}"
                f"{s['plan_export_before_15_kwh']:>8.3f}{s['export_action_kwh']:>8.3f}"
                f"{s['min_predicted_soc'] or 0:>7.1f}"
                f"{_money(s['nfit_spill_cost']):>8}{_money(s['nfit_spill_exposure']):>8}"
                f"{str(s['nfit_window_open']):>6}"
            )
            rows.append({
                "instant": inp["test_time"],
                "arm": arm,
                "solar": solar,
                "neg_scale": neg_scale,
                **s,
            })
    if json_out:
        Path(json_out).write_text(json.dumps(rows, indent=2) + "\n")
    return 0


def slots(
    in_dir: Path, name: str, arm: str, solar: str, limit: int, neg_scale: float = 1.0
) -> int:
    """Print one replay's plan slot by slot."""
    import replay_plan_baseline as rpb

    path = in_dir / f"{name}.json"
    record = rpb.replay(_scenario(path, solar, neg_scale), arm)
    cols = list(rpb.DECISION_COLUMNS)
    print(
        json.dumps(
            {
                k: v
                for k, v in record["summary"].items()
                if not isinstance(v, (list, dict))
            },
            indent=0,
        )[:4000]
    )
    print(" | ".join(c[:12] for c in cols))
    for r in record["decisions"][:limit]:
        print(
            " | ".join(
                str(v)[:19] if not isinstance(v, float) else f"{v:.4f}" for v in r
            )
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capture", help="capture inputs at ISO instants")
    cap.add_argument("instants", nargs="+")
    cap.add_argument("--out", default=str(DEFAULT_DIR))
    rep = sub.add_parser("replay", help="replay every capture, two arms")
    rep.add_argument("--dir", default=str(DEFAULT_DIR))
    rep.add_argument("--solar", choices=["solcast", "measured"], default="solcast")
    rep.add_argument("--json", default=None)
    rep.add_argument(
        "--neg-scale",
        type=float,
        default=1.0,
        help="multiply negative feed-in forecast prices (sensitivity probe)",
    )
    sl = sub.add_parser("slots", help="print one replay's plan")
    sl.add_argument("name")
    sl.add_argument("--dir", default=str(DEFAULT_DIR))
    sl.add_argument("--arm", default="captured")
    sl.add_argument("--solar", choices=["solcast", "measured"], default="solcast")
    sl.add_argument("--limit", type=int, default=60)
    sl.add_argument("--neg-scale", type=float, default=1.0)
    args = parser.parse_args()
    if args.cmd == "capture":
        return capture(args.instants, Path(args.out))
    if args.cmd == "replay":
        return replay(Path(args.dir), args.solar, args.json, args.neg_scale)
    return slots(
        Path(args.dir), args.name, args.arm, args.solar, args.limit, args.neg_scale
    )


if __name__ == "__main__":
    raise SystemExit(main())

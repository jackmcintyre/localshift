#!/usr/bin/env python3
"""ha_data_sweep.py — recorder-DB health and economics sweep for LocalShift.

Pulls a window of history straight out of the Home Assistant recorder
database (NOT the REST history API, which silently truncates multi-day
requests) and produces a markdown report: solar forecast accuracy, price
capture, daily grid/battery flows, battery-mode churn, charge timing vs
the day's cheapest hours, and learning decision-outcome distributions.

Method
------
The recorder DB is COPY-FIRST: the live file cannot be opened read-only
by sqlite while HA holds it, so both the db and its -wal are copied to a
temp dir, queried, and deleted (keep with --keep-db). HA records state
CHANGES ONLY — long gaps in a series usually mean a constant value (e.g.
overnight zero solar), not an outage.

Usage
-----
  scripts/ha_data_sweep.py                    # 7 days, ./tmp output
  scripts/ha_data_sweep.py --days 14
  scripts/ha_data_sweep.py --config /path/to/ha/config
  scripts/ha_data_sweep.py --stdout           # print report, no files
  scripts/ha_data_sweep.py --keep-db          # keep the DB copy for ad-hoc SQL

Config dir resolution: --config flag > HA_CONFIG env >
/Volumes/appdata/Home-Assistant-Container (this host's default).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

AEST = timezone(timedelta(hours=10))

DB_NAME = "home-assistant_v2.db"
DEFAULT_CONFIG = "/Volumes/appdata/Home-Assistant-Container"

ENTITIES: dict[str, str] = {
    "general_price": "sensor.amber_express_100h_general_price",
    "feed_in_price": "sensor.amber_express_100h_feed_in_price",
    "solar_forecast": "sensor.solcast_pv_forecast_power_now",
    "solar_power": "sensor.my_home_solar_power",
    "battery_power": "sensor.my_home_battery_power",
    "grid_power": "sensor.my_home_grid_power",
    "load_power": "sensor.my_home_load_power",
    "grid_imported": "sensor.my_home_grid_imported",
    "grid_exported": "sensor.my_home_grid_exported",
    "import_cost": "sensor.my_home_grid_imported_cost",
    "export_comp": "sensor.my_home_grid_exported_compensation",
    "batt_charged": "sensor.my_home_battery_charged",
    "batt_discharged": "sensor.my_home_battery_discharged",
    "batt_from_grid": "sensor.my_home_battery_imported_from_grid",
    "batt_to_grid": "sensor.my_home_grid_exported_from_battery",
}
MODE_ENTITY = "select.localshift_battery_mode"
GAP_ENTITIES = [
    "solar_forecast",
    "solar_power",
    "grid_power",
    "batt_charged",
    "batt_from_grid",
    "grid_exported",
]
# Forecasts are Watts; my_home_* power sensors are kilowatts.
SOLAR_ACTUAL_SCALE = 1000.0


# --------------------------------------------------------------------------
# Pure analysis helpers
# --------------------------------------------------------------------------

def parse_series(rows: list[tuple[float, str | None]]) -> list[tuple[float, float]]:
    """Keep only float-parseable states, preserving order."""
    out = []
    for ts, st in rows:
        try:
            out.append((ts, float(st)))
        except (TypeError, ValueError):
            pass
    return out


def step_value(s: list[tuple[float, float]], t: float) -> float | None:
    """State-machine value at time t: last sample with ts <= t, else None."""
    lo, hi, v = 0, len(s) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if s[mid][0] <= t:
            v = s[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return v


def merge_buckets(
    fc: list[tuple[float, float]],
    ac: list[tuple[float, float]],
    bucket_secs: int = 900,
    actual_scale: float = SOLAR_ACTUAL_SCALE,
) -> dict[int, dict[str, float]]:
    """Merge forecast (W) and actual (scaled to W) series into buckets.

    Last write wins within a bucket; a side missing from a bucket is
    simply absent from that bucket's dict.
    """
    buckets: dict[int, dict[str, float]] = {}
    for ts, v in fc:
        buckets.setdefault(int(ts // bucket_secs), {})["f"] = v
    for ts, v in ac:
        buckets.setdefault(int(ts // bucket_secs), {})["a"] = v * actual_scale
    return buckets


def bucket_pairs(
    buckets: dict[int, dict[str, float]],
    tz: timezone,
    min_w: float = 100.0,
    bucket_secs: int = 900,
) -> dict:
    """Solar bias stats + per-day energy from merged buckets.

    Bias stats use only buckets where either side exceeds min_w (daytime);
    day energy integrates every bucket so totals are comparable.
    """
    n = 0
    bias_sum = 0.0
    by_hour: dict[int, list[float]] = defaultdict(list)
    day_f: dict[str, float] = defaultdict(float)
    day_a: dict[str, float] = defaultdict(float)
    kwh = 1.0 * bucket_secs / 3_600_000.0  # W -> kWh per bucket
    for b in sorted(buckets):
        d = buckets[b]
        ts = b * bucket_secs
        key = datetime.fromtimestamp(ts, tz).strftime("%m-%d")
        if "a" in d:
            day_a[key] += d["a"] * kwh
        if "f" in d:
            day_f[key] += d["f"] * kwh
        if "f" in d and "a" in d and (d["f"] > min_w or d["a"] > min_w):
            n += 1
            bias_sum += d["f"] - d["a"]
            by_hour[datetime.fromtimestamp(ts, tz).hour].append(d["f"] - d["a"])
    tot_f, tot_a = sum(day_f.values()), sum(day_a.values())
    return {
        "n_buckets": n,
        "bias_mean_w": bias_sum / n if n else 0.0,
        "bias_by_hour": {
            h: {"n": len(v), "mean_w": sum(v) / len(v)} for h, v in sorted(by_hour.items())
        },
        "day_energy": {k: (day_f[k], day_a.get(k, 0.0)) for k in sorted(day_a)},
        "energy_ratio": tot_f / tot_a if tot_a > 0 else 0.0,
    }


def price_capture(
    gpow: list[tuple[float, float]],
    gp: list[tuple[float, float]],
    fi: list[tuple[float, float]],
    step: int = 300,
) -> dict:
    """Energy-weighted import price vs the average price while importing.

    Capture ratio < 1 means imports were front-loaded into cheaper moments
    of the import-active periods. Export revenue uses the feed-in price.
    """
    imp_e = imp_c = exp_e = exp_rev = 0.0
    window_prices: list[float] = []
    if not gpow:
        return {
            "import_kwh": 0.0, "export_kwh": 0.0, "avg_paid": 0.0,
            "import_window_avg_price": 0.0, "capture_ratio": 0.0,
            "export_revenue": 0.0,
        }
    dt = step / 3600.0
    t = gpow[0][0]
    while t < gpow[-1][0]:
        p = step_value(gpow, t) or 0.0
        pr = step_value(gp, t)
        fir = step_value(fi, t)
        if pr is not None:
            if p > 0:
                imp_e += p * dt
                imp_c += p * dt * pr
                window_prices.append(pr)
            elif p < 0:
                exp_e += -p * dt
                if fir is not None:
                    exp_rev += -p * dt * fir
        t += step
    avg_paid = imp_c / imp_e if imp_e > 0 else 0.0
    window_avg = (
        sum(window_prices) / len(window_prices) if window_prices else 0.0
    )
    return {
        "import_kwh": imp_e,
        "export_kwh": exp_e,
        "avg_paid": avg_paid,
        "import_window_avg_price": window_avg,
        "capture_ratio": avg_paid / window_avg if window_avg > 0 else 0.0,
        "export_revenue": exp_rev,
    }


def daily_deltas(series: list[tuple[float, float]], tz: timezone) -> dict[str, float]:
    """Per-local-day max-min on cumulative meters (daily-reset safe)."""
    byday: dict[str, list[float]] = defaultdict(list)
    for ts, v in series:
        byday[datetime.fromtimestamp(ts, tz).strftime("%m-%d")].append(v)
    return {d: max(vs) - min(vs) for d, vs in sorted(byday.items())}


def mode_stats(states: list[tuple[float, str]], tz: timezone) -> dict:
    """Transition counts, dwell minutes, destination/pair counts, hour hist."""
    trans = [(b[0], a[1], b[1]) for a, b in zip(states, states[1:], strict=False) if b[1] != a[1]]
    dest = Counter(new for _, _, new in trans)
    pairs = Counter((old, new) for _, old, new in trans)
    hours = Counter(datetime.fromtimestamp(t, tz).hour for t, _, _ in trans)
    dwells = sorted((b[0] - a[0]) / 60 for a, b in zip(states, states[1:], strict=False)
                    if b[1] != a[1])
    return {
        "n_changes": len(trans),
        "dest_counts": dict(dest),
        "pairs": dict(pairs),
        "hour_hist": dict(sorted(hours.items())),
        "dwells_min": dwells,
        "dwell_median_min": dwells[len(dwells) // 2] if dwells else None,
        "dwell_p10_min": dwells[len(dwells) // 10] if dwells else None,
        "dwell_min_min": dwells[0] if dwells else None,
    }


def charge_timing(
    batt: list[tuple[float, float]],
    gp: list[tuple[float, float]],
    day_start: float,
    tz: timezone,
    step: int = 300,
    charge_kw: float = -0.05,
    bulk_kwh: float = 0.3,
) -> dict:
    """Charging energy/prices for one local day vs its cheapest hours.

    Battery power convention: negative = charging.
    """
    hourly = [step_value(gp, day_start + h * 3600) for h in range(24)]
    seen = [x for x in hourly if x is not None]
    charged = paid = 0.0
    per_hour: dict[int, float] = defaultdict(float)
    for i in range(0, 86400, step):
        bp = step_value(batt, day_start + i) or 0.0
        pr = step_value(gp, day_start + i)
        if bp < charge_kw and pr is not None:
            e = -bp * step / 3600.0
            charged += e
            paid += e * pr
            per_hour[datetime.fromtimestamp(day_start + i, tz).hour] += e
    cheapest = sorted(
        (p, h) for h, p in enumerate(hourly) if p is not None
    )[:3]
    return {
        "charged_kwh": charged,
        "avg_paid": paid / charged if charged > 0 else None,
        "bulk_hours": {h: e for h, e in per_hour.items() if e > bulk_kwh},
        "cheapest_hours": [h for _, h in cheapest],
        "day_min_price": min(seen) if seen else None,
        "day_max_price": max(seen) if seen else None,
    }


def decision_stats(data: dict) -> dict:
    """Percentiles by mode, worst decisions, field completeness."""
    comp = data.get("completed_decisions") or []
    pend = data.get("pending_decisions") or []
    scored = [r for r in comp if r.get("outcome_score") is not None]
    by_mode: dict[str, list[float]] = defaultdict(list)
    for r in scored:
        by_mode[r.get("mode_chosen")].append(r["outcome_score"])
    stats = {
        m: {
            "n": len(v),
            "median": pctl(sorted(v), 0.5),
            "p10": pctl(sorted(v), 0.1),
            "p90": pctl(sorted(v), 0.9),
        }
        for m, v in by_mode.items()
    }
    worst = sorted(scored, key=lambda r: r["outcome_score"])[:5]
    ts_list = sorted(r.get("timestamp", "") for r in comp)
    return {
        "completed": len(comp),
        "scored": len(scored),
        "pending": len(pend),
        "by_mode": dict(stats),
        "worst5": worst,
        "missing_cost": sum(
            1 for r in scored if r.get("actual_cost_during_period") is None
        ),
        "span_first": ts_list[0] if ts_list else None,
        "span_last": ts_list[-1] if ts_list else None,
    }


def pctl(sorted_vals: list[float], q: float) -> float:
    """Order statistic at fraction q (0..1) of a pre-sorted list."""
    if not sorted_vals:
        raise ValueError("pctl of empty list")
    idx = min(int(q * len(sorted_vals)), len(sorted_vals) - 1)
    return sorted_vals[idx]


def gap_windows(
    series: list[tuple[float, float]], min_gap_s: float = 3600.0
) -> list[tuple[float, float]]:
    """Consecutive-sample gaps longer than min_gap_s."""
    return [(a[0], b[0]) for a, b in zip(series, series[1:], strict=False)
            if b[0] - a[0] > min_gap_s]


# --------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------

def _md_solar(solar: dict) -> list[str]:
    lines = ["## Solar forecast vs actual", ""]
    if not solar:
        return lines + ["(no data)", ""]
    lines.append(
        f"- daytime buckets: {solar['n_buckets']}, mean bias "
        f"(fc-ac): {solar['bias_mean_w']:+.0f} W, "
        f"energy ratio fc/ac: {solar['energy_ratio']:.2f}"
    )
    lines.append("")
    lines.append("| day | forecast kWh | actual kWh |")
    lines.append("|-----|--------------|------------|")
    for day, (f, a) in solar["day_energy"].items():
        lines.append(f"| {day} | {f:.1f} | {a:.1f} |")
    lines.append("")
    lines.append("bias by local hour: " + " ".join(
        f"{h:02d}:{v['mean_w']:+.0f}" for h, v in solar["bias_by_hour"].items()
    ))
    return lines + [""]


def _md_price(price: dict) -> list[str]:
    lines = ["## Price capture", ""]
    if not price:
        return lines + ["(no data)", ""]
    lines.append(
        f"- import: {price['import_kwh']:.1f} kWh, avg paid "
        f"{price['avg_paid']:.3f} $/kWh vs "
        f"{price['import_window_avg_price']:.3f} while importing — "
        f"capture ratio: **{price['capture_ratio']:.2f}** (<1 beats average)"
    )
    lines.append(
        f"- export: {price['export_kwh']:.1f} kWh, revenue "
        f"${price['export_revenue']:.2f}"
    )
    return lines + [""]


def _md_meters(daily_meters: dict, days_order: list[str]) -> list[str]:
    lines = ["## Daily meters (kWh / $, local days, max-min)", ""]
    cols = list(daily_meters.keys())
    if not cols:
        return lines + ["(no data)", ""]
    header = "| day | " + " | ".join(cols) + " |"
    sep = "|-----" * (len(cols) + 1) + "|"
    lines += [header, sep]
    for day in days_order:
        vals = " | ".join(
            f"{daily_meters[c].get(day, 0.0):.1f}" for c in cols
        )
        lines.append(f"| {day} | {vals} |")
    return lines + [""]


def _md_mode(mode: dict, days: int) -> list[str]:
    lines = ["## Battery mode churn", ""]
    if not mode:
        return lines + ["(no data)", ""]
    lines.append(
        f"- {mode['n_changes']} changes ({mode['n_changes'] / days:.1f}/day), "
        f"dwell median {mode['dwell_median_min']:.0f}m "
        f"p10 {mode['dwell_p10_min']:.0f}m"
    )
    lines.append(f"- destinations: {mode['dest_counts']}")
    lines.append(f"- by local hour: {mode['hour_hist']}")
    return lines + [""]


def _md_charge(charge_days: dict) -> list[str]:
    lines = ["## Charge timing vs day's cheapest hours", ""]
    if not charge_days:
        return lines + ["(no charging days)", ""]
    lines.append("| day | charged kWh | avg paid | day's cheapest hours |")
    lines.append("|-----|-------------|----------|----------------------|")
    for day, ct in charge_days.items():
        paid = f"{ct['avg_paid']:.3f}" if ct["avg_paid"] is not None else "n/a"
        cheap = ", ".join(str(h) for h in ct["cheapest_hours"])
        lines.append(f"| {day} | {ct['charged_kwh']:.1f} | {paid} | {cheap} |")
    return lines + [""]


def _md_decisions(decisions: dict) -> list[str]:
    lines = ["## Learning decision outcomes", ""]
    if not decisions:
        return lines + ["(decision store not found)", ""]
    lines.append(
        f"- completed: {decisions['completed']} "
        f"(scored {decisions['scored']}), pending: {decisions['pending']}, "
        f"missing cost: {decisions['missing_cost']}/{decisions['scored']}"
    )
    lines.append(
        f"- span: {decisions['span_first']} -> {decisions['span_last']}"
    )
    lines.append("")
    lines.append("| mode | n | median | p10 | p90 |")
    lines.append("|------|---|--------|-----|-----|")
    for m, st in decisions["by_mode"].items():
        lines.append(
            f"| {m} | {st['n']} | {st['median']:+.3f} | "
            f"{st['p10']:+.3f} | {st['p90']:+.3f} |"
        )
    lines.append("")
    lines.append("worst 5:")
    for r in decisions["worst5"]:
        lines.append(
            f"- {str(r.get('timestamp', ''))[:16]} {r.get('mode_chosen')} "
            f"score={r['outcome_score']:+.3f} "
            f"cost=${r.get('actual_cost_during_period') or 0:.2f} "
            f"imp={r.get('actual_import_kwh') or 0:.1f}"
        )
    return lines + [""]


def render_markdown(
    days: int,
    config_dir: str,
    coverage: dict[str, dict],
    solar: dict | None,
    price: dict | None,
    daily_meters: dict[str, dict[str, float]],
    mode: dict | None,
    charge_days: dict,
    decisions: dict | None,
    gaps: list,
) -> str:
    """Assemble the full sweep report."""
    lines = [
        "# HA data sweep",
        "",
        f"- days: {days}",
        f"- generated: {datetime.now(AEST).isoformat(timespec='seconds')}",
        f"- config dir: {config_dir}",
        f"- recorder: copied {DB_NAME} (+ wal) to temp, queried read-only",
        "",
    ]
    lines.append("## Coverage (numeric samples in window)")
    lines.append("")
    if coverage:
        lines.append("| entity | n | max gap min |")
        lines.append("|--------|---|-------------|")
        for eid, c in coverage.items():
            lines.append(f"| {eid} | {c['n']} | {c['max_gap_min']:.0f} |")
    else:
        lines.append("(no entities found in window)")
    lines.append("")
    if gaps:
        lines.append("### Gap windows > 60 min (see notes before panicking)")
        lines.append("")
        for label, t1, t2 in gaps:
            lines.append(
                f"- {label}: "
                f"{datetime.fromtimestamp(t1, AEST):%m-%d %H:%M} -> "
                f"{datetime.fromtimestamp(t2, AEST):%m-%d %H:%M} "
                f"({(t2 - t1) / 3600:.1f}h)"
            )
        lines.append("")
    day_sets = [set(m) for m in daily_meters.values()]
    days_order = sorted(set().union(*day_sets)) if day_sets else []
    lines += _md_solar(solar or {})
    lines += _md_price(price or {})
    lines += _md_meters(daily_meters, days_order)
    lines += _md_mode(mode or {}, days)
    lines += _md_charge(charge_days)
    lines += _md_decisions(decisions or {})
    lines += [
        "## Notes",
        "",
        "- HA records state CHANGES ONLY: long gaps usually mean a constant",
        "  value (overnight zero solar), not an outage.",
        "- Solcast forecasts in W; my_home_* power sensors in kW",
        "  (x1000 correction applied).",
        "- Daily meters use max-min per local day (daily-reset safe).",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# I/O layer
# --------------------------------------------------------------------------

def snapshot_db(config_dir: str, tmp_dir: str) -> str:
    """Copy the recorder db + wal into tmp_dir; return the copy path."""
    src = Path(config_dir) / DB_NAME
    if not src.exists():
        sys.exit(f"ERROR: recorder db not found at {src}")
    dst = str(Path(tmp_dir) / DB_NAME)
    shutil.copyfile(src, dst)
    wal = Path(str(src) + "-wal")
    if wal.exists():
        shutil.copyfile(wal, dst + "-wal")
    return dst


def load_series(
    con: sqlite3.Connection, entity_id: str, since: float
) -> list[tuple[float, str]]:
    cur = con.execute(
        "SELECT s.last_updated_ts, s.state FROM states s "
        "JOIN states_meta m ON s.metadata_id=m.metadata_id "
        "WHERE m.entity_id=? AND s.last_updated_ts>=? "
        "ORDER BY s.last_updated_ts",
        (entity_id, since),
    )
    return cur.fetchall()


def find_decision_store(config_dir: str) -> Path | None:
    storage = Path(config_dir) / ".storage"
    hits = sorted(storage.glob("localshift.decision_outcomes.*"))
    return hits[-1] if hits else None


def day_starts(series: list[tuple[float, float]], tz: timezone) -> list[tuple[str, float]]:
    """Distinct local days in a series with their midnight timestamps."""
    seen: dict[str, float] = {}
    for ts, _ in series:
        local = datetime.fromtimestamp(ts, tz)
        key = local.strftime("%m-%d")
        if key not in seen:
            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
            seen[key] = midnight.timestamp()
    return sorted(seen.items(), key=lambda kv: kv[1])


def run_sweep(args) -> str:
    import time as _time

    config_dir = (
        args.config
        or os.environ.get("HA_CONFIG")
        or DEFAULT_CONFIG
    )
    since = _time.time() - args.days * 86400

    # Transient DB copy lives under out-dir (gitignored tmp/): the system
    # temp dir can be sandbox-blocked, and keeping it local to the report
    # makes --keep-db ad-hoc SQL natural.
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tmp_holder = str(out_dir / f"sweep-db-{stamp}")
    Path(tmp_holder).mkdir()
    try:
        db = snapshot_db(config_dir, tmp_holder)
        con = sqlite3.connect(db)

        raw = {k: load_series(con, eid, since) for k, eid in ENTITIES.items()}
        num = {k: parse_series(v) for k, v in raw.items()}

        coverage = {}
        for k in num:
            gaps = [b[0] - a[0] for a, b in zip(num[k], num[k][1:], strict=False)]
            coverage[ENTITIES[k]] = {
                "n": len(num[k]),
                "max_gap_min": max(gaps) / 60 if gaps else 0.0,
            }
        gaps = []
        for k in GAP_ENTITIES:
            gaps += [
                (ENTITIES[k], t1, t2) for t1, t2 in gap_windows(num[k])
            ]

        solar = bucket_pairs(
            merge_buckets(num["solar_forecast"], num["solar_power"]),
            AEST,
        )
        price = price_capture(
            num["grid_power"], num["general_price"], num["feed_in_price"]
        )
        meters = {
            "grid_import_kWh": daily_deltas(num["grid_imported"], AEST),
            "grid_export_kWh": daily_deltas(num["grid_exported"], AEST),
            "import_cost": daily_deltas(num["import_cost"], AEST),
            "export_rev": daily_deltas(num["export_comp"], AEST),
            "batt_charged_kWh": daily_deltas(num["batt_charged"], AEST),
            "batt_discharged_kWh": daily_deltas(num["batt_discharged"], AEST),
            "batt_from_grid_kWh": daily_deltas(num["batt_from_grid"], AEST),
            "batt_to_grid_kWh": daily_deltas(num["batt_to_grid"], AEST),
        }
        mode = mode_stats(load_series(con, MODE_ENTITY, since), AEST)
        charge_days = {
            day: charge_timing(num["battery_power"], num["general_price"],
                               start, AEST)
            for day, start in day_starts(num["battery_power"], AEST)
        }
        decisions = None
        store = find_decision_store(config_dir)
        if store:
            decisions = decision_stats(json.loads(store.read_text())["data"])
        con.close()

        return render_markdown(
            days=args.days,
            config_dir=config_dir,
            coverage=coverage,
            solar=solar,
            price=price,
            daily_meters=meters,
            mode=mode,
            charge_days=charge_days,
            decisions=decisions,
            gaps=gaps,
        )
    finally:
        if args.keep_db:
            print(f"DB copy kept at {tmp_holder}")
        else:
            shutil.rmtree(tmp_holder, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--days", type=int, default=7,
                    help="window length in days (default: 7)")
    ap.add_argument("--config", help=f"HA config dir (default: HA_CONFIG env "
                                     f"or {DEFAULT_CONFIG})")
    ap.add_argument("--out-dir", default="tmp",
                    help="output directory (default: ./tmp)")
    ap.add_argument("--stdout", action="store_true",
                    help="print the report to stdout instead of writing a file")
    ap.add_argument("--keep-db", action="store_true",
                    help="keep the copied recorder DB for ad-hoc SQL")
    args = ap.parse_args()

    report = run_sweep(args)
    if args.stdout:
        print(report)
        return
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"ha-data-sweep-{stamp}.md"
    path.write_text(report)
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()

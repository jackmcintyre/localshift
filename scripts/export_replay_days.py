#!/usr/bin/env python3
"""export_replay_days.py — capture real days as replayable scenario JSON.

Pulls the optimizer inputs that existed at a chosen decision time on each of
the last N days and writes one scenario file per day, in the shape
``tests/test_scenarios.py::setup_coordinator_data`` consumes. The point is to
replay real days offline under different settings and compare the plans.

Why the REST history API and not the recorder DB: ``scripts/ha_data_sweep.py``
copies the sqlite file, which needs the HA config share mounted. This needs
only a token. It asks for one narrow window per day rather than a multi-day
span, which is where the history API truncates.

Two data caveats, both load-bearing — read before trusting a replay:

1. **Prices are real.** The Amber sensors record a ``forecast`` attribute
   (``{time, value}``, ~48 entries, ~15h ahead) which is converted here into
   scenario forecast slots. This is exactly what the optimizer saw.

2. **Solar is measured, not forecast.** Home Assistant's recorder drops the
   large list attributes, so Solcast's ``detailedForecast`` is NOT retained —
   only the day's total kWh survives in the state. Per-slot *forecast* solar
   for a past day is therefore unrecoverable. Instead this reconstructs the
   solar series from ``sensor.my_home_solar_power``, which IS recorded at full
   resolution, averaged into 30-minute buckets (Solcast's ``pv_estimate`` is an
   average kW over the period, so the units line up).

   That means a replay gives every arm perfect solar foresight. Absolute
   numbers are therefore optimistic, but the arms all receive byte-identical
   inputs, so the *comparison between arms* — which is the only thing these
   files are for — stays valid. Days built this way carry
   ``solar_source: "measured"`` in the input; today, captured live, carries
   ``solar_source: "solcast"`` and is the one fully faithful day.

Credentials: HOMEASSISTANT_URL / HOMEASSISTANT_TOKEN from the environment,
falling back to ~/.claude.json.

Usage
-----
  scripts/export_replay_days.py                     # last 10 days at 09:00 local
  scripts/export_replay_days.py --days 14 --hour 13
  scripts/export_replay_days.py --out simulations/replay
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

AEST = timezone(timedelta(hours=10))

PRICE_GENERAL = "sensor.amber_express_100h_general_price"
PRICE_FEED_IN = "sensor.amber_express_100h_feed_in_price"
SOLCAST_TODAY = "sensor.solcast_pv_forecast_forecast_today"
SOLAR_POWER = "sensor.my_home_solar_power"
SCALARS = {
    "soc": "sensor.my_home_percentage_charged",
    "load_power_kw": "sensor.my_home_load_power",
    "solar_power_kw": SOLAR_POWER,
    "battery_power_kw": "sensor.my_home_battery_power",
    "grid_power_kw": "sensor.my_home_grid_power",
}
POINT_ENTITIES = [PRICE_GENERAL, PRICE_FEED_IN, SOLCAST_TODAY, *SCALARS.values()]

# Live configuration, read off sensor.localshift_optimizer_summary.config_options
# on 2026-09-04 so replays reproduce production rather than scenario defaults.
LIVE_CONFIG: dict[str, Any] = {
    "battery_target": 95,
    "minimum_target_soc": 10,
    "demand_window_start": "15:00:00",
    "demand_window_end": "21:00:00",
    "allow_dw_entry_under_target": True,
    "stale_solar_conservative": True,
    "stale_solar_confidence_ceiling": 0.3,
    "optimization_mode": "self_consumption",
    "export_price_margin": 0.1,
    "switching_penalty": 0.08,
    "target_penalty": 0.1,
    "min_cycle_saving": 0.25,
    "min_hold_saving": 0,
    "precharge_runway_margin_min": 15,
    "pricing_source": "amber",
    "comparison_mode": "disabled",
    "optimizer_enabled": True,
    "optimizer_control_mode": "active",
    "ha_timezone": "Australia/Sydney",
}
LIVE_SWITCHES: dict[str, bool] = {
    "automation_enabled": True,
    "spike_discharge_enabled": True,
    "spike_discharge_conservative": True,
    "demand_window_block": True,
    "allow_dw_entry_under_target": True,
    "stale_solar_conservative": True,
}


# --------------------------------------------------------------------------
# Credentials + HTTP
# --------------------------------------------------------------------------


def load_credentials() -> tuple[str, str]:
    """Return (url, token), preferring env vars over ~/.claude.json."""
    url = os.environ.get("HOMEASSISTANT_URL") or os.environ.get("HA_URL")
    token = os.environ.get("HOMEASSISTANT_TOKEN") or os.environ.get(
        "HA_LONG_LIVED_TOKEN"
    )
    if url and token:
        return url.rstrip("/"), token

    cfg = Path.home() / ".claude.json"
    if not cfg.exists():
        sys.exit("ERROR: no HA credentials in env and ~/.claude.json not found")
    blob = cfg.read_text()
    if not url:
        m = re.search(r'"HOMEASSISTANT_URL"\s*:\s*"([^"]+)"', blob)
        url = m.group(1) if m else None
    if not token:
        m = re.search(r'"HOMEASSISTANT_TOKEN"\s*:\s*"([^"]+)"', blob)
        token = m.group(1) if m else None
    if not url or not token:
        sys.exit("ERROR: HOMEASSISTANT_URL / HOMEASSISTANT_TOKEN not found")
    return url.rstrip("/"), token


def api_get(url: str, token: str, path: str) -> Any:
    req = urllib.request.Request(
        f"{url}{path}", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        sys.exit(f"ERROR: HA returned {exc.code} for {path.split('?')[0]}")
    except urllib.error.URLError as exc:
        sys.exit(f"ERROR: cannot reach {url}: {exc.reason}")


def history(
    url: str, token: str, entities: list[str], start: datetime, end: datetime
) -> dict[str, list[dict]]:
    """Fetch history with attributes retained, keyed by entity_id."""
    qs = urllib.parse.urlencode({
        "filter_entity_id": ",".join(entities),
        "end_time": end.isoformat(),
    })
    blocks = api_get(url, token, f"/api/history/period/{start.isoformat()}?{qs}")
    return {block[0]["entity_id"]: block for block in blocks if block}


# --------------------------------------------------------------------------
# Extraction helpers
# --------------------------------------------------------------------------


def latest_at_or_before(states: list[dict], cutoff: datetime) -> dict | None:
    """Last state whose timestamp is <= cutoff; else the earliest available."""
    best: dict | None = None
    for st in states:
        raw = st.get("last_updated") or st.get("last_changed")
        if not raw:
            continue
        if datetime.fromisoformat(raw) <= cutoff:
            best = st
        else:
            break
    return best or (states[0] if states else None)


def as_float(state: dict | None) -> float | None:
    if not state:
        return None
    try:
        return float(state["state"])
    except (KeyError, TypeError, ValueError):
        return None


def forecast_to_slots(entries: list[dict]) -> list[dict]:
    """Convert Amber's ``forecast`` ({time, value}) into scenario slots.

    Duration comes from the gap to the next entry (Amber emits 5-minute
    entries near-term then 30-minute), with the last slot inheriting the
    previous gap. Output matches ``_convert_forecast_to_slot``'s new format.
    """
    parsed: list[tuple[datetime, float]] = []
    for e in entries:
        try:
            parsed.append((datetime.fromisoformat(e["time"]), float(e["value"])))
        except (KeyError, TypeError, ValueError):
            continue
    slots: list[dict] = []
    for i, (when, value) in enumerate(parsed):
        if i + 1 < len(parsed):
            minutes = int((parsed[i + 1][0] - when).total_seconds() / 60)
        else:
            minutes = int((when - parsed[i - 1][0]).total_seconds() / 60) if i else 30
        slots.append({
            "start_time": when.isoformat(),
            "duration": max(minutes, 5),
            "per_kwh": round(value, 6),
            "spike_status": "none",
        })
    return slots


def solar_from_measured(samples: list[dict], day_start: datetime) -> list[dict]:
    """Build a Solcast-shaped series from measured solar power.

    Solcast's ``pv_estimate`` is an average kW over each 30-minute period, so
    each bucket is the mean of the measured kW samples that fall inside it.
    Buckets with no samples are zero (overnight, or a recorder gap).
    """
    buckets: dict[int, list[float]] = {}
    for st in samples:
        raw = st.get("last_updated") or st.get("last_changed")
        value = as_float(st)
        if not raw or value is None:
            continue
        idx = int((datetime.fromisoformat(raw) - day_start).total_seconds() // 1800)
        if 0 <= idx < 48:
            buckets.setdefault(idx, []).append(value)

    series: list[dict] = []
    for idx in range(48):
        vals = buckets.get(idx, [])
        kw = round(sum(vals) / len(vals), 4) if vals else 0.0
        series.append({
            "period_start": (day_start + timedelta(minutes=30 * idx)).isoformat(),
            "pv_estimate": kw,
            "pv_estimate10": kw,
            "pv_estimate90": kw,
        })
    return series


# --------------------------------------------------------------------------


def build_day(url: str, token: str, decision_at: datetime) -> dict[str, Any] | None:
    """Assemble one scenario dict from the state of the world at decision_at."""
    day_start = decision_at.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    points = history(
        url, token, POINT_ENTITIES, decision_at - timedelta(minutes=25), decision_at
    )
    general = latest_at_or_before(points.get(PRICE_GENERAL, []), decision_at)
    feed_in = latest_at_or_before(points.get(PRICE_FEED_IN, []), decision_at)
    if general is None or feed_in is None:
        return None

    general_forecast = forecast_to_slots(
        general.get("attributes", {}).get("forecast") or []
    )
    feed_in_forecast = forecast_to_slots(
        feed_in.get("attributes", {}).get("forecast") or []
    )
    if not general_forecast:
        return None

    scalars: dict[str, float] = {}
    for key, entity in SCALARS.items():
        value = as_float(latest_at_or_before(points.get(entity, []), decision_at))
        if value is not None:
            scalars[key] = round(value, 4)
    soc = scalars.pop("soc", None)
    if soc is None:
        return None

    # Whole-day measured solar, resampled into Solcast-shaped 30-minute buckets.
    solar_hist = history(url, token, [SOLAR_POWER], day_start, day_end)
    solcast_today = solar_from_measured(solar_hist.get(SOLAR_POWER, []), day_start)
    if not any(s["pv_estimate"] for s in solcast_today):
        return None

    solcast_total = as_float(
        latest_at_or_before(points.get(SOLCAST_TODAY, []), decision_at)
    )
    day = decision_at.strftime("%Y-%m-%d")
    measured_kwh = round(sum(s["pv_estimate"] for s in solcast_today) * 0.5, 2)

    return {
        "name": f"replay-{day}",
        "description": (
            f"Real optimizer inputs at {decision_at.isoformat()}. Prices are the "
            "forecast the optimizer actually saw; solar is measured actuals "
            "resampled to 30-min buckets (Solcast per-slot forecasts are not "
            "retained by the recorder). Generated by scripts/export_replay_days.py "
            "— do not hand-edit."
        ),
        "input": {
            "test_time": decision_at.isoformat(),
            "soc": round(soc, 2),
            "operation_mode": "autonomous",
            "backup_reserve": 10,
            "price_spike": False,
            "manual_override": False,
            "target_reached_today": False,
            "general_price": round(as_float(general) or 0.0, 6),
            "feed_in_price": round(as_float(feed_in) or 0.0, 6),
            "general_forecast": general_forecast,
            "feed_in_forecast": feed_in_forecast,
            "solcast_today": solcast_today,
            "solcast_tomorrow": [],
            "solar_source": "measured",
            "solar_measured_kwh": measured_kwh,
            "solcast_forecast_kwh": solcast_total,
            **scalars,
        },
        "config_overrides": dict(LIVE_CONFIG),
        "switch_states": dict(LIVE_SWITCHES),
        # Replays compare arms; they are not fixed-outcome regression tests.
        "expected": {"optimizer_result_success": True},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=10, help="days back (default 10)")
    parser.add_argument(
        "--hour",
        type=int,
        default=9,
        help="local decision hour to capture (default 9, before pre-charge)",
    )
    parser.add_argument(
        "--out",
        default="simulations/replay",
        help="output directory (default simulations/replay)",
    )
    args = parser.parse_args()

    url, token = load_credentials()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(AEST)
    written, skipped = [], []
    for back in range(1, args.days + 1):
        target = (now - timedelta(days=back)).replace(
            hour=args.hour, minute=0, second=0, microsecond=0
        )
        day = target.strftime("%Y-%m-%d")
        scenario = build_day(url, token, target)
        if scenario is None:
            skipped.append(day)
            print(f"  skip {day}: inputs not retained")
            continue
        (out_dir / f"{day}.json").write_text(json.dumps(scenario, indent=2) + "\n")
        i = scenario["input"]
        print(
            f"  wrote {day}.json  soc={i['soc']:>5.1f}%  "
            f"price_slots={len(i['general_forecast']):>2}  "
            f"solar_measured={i['solar_measured_kwh']:>5.1f}kWh  "
            f"solcast_said={i['solcast_forecast_kwh']}"
        )
        written.append(day)

    print(f"\n{len(written)} day(s) written, {len(skipped)} skipped -> {out_dir}")
    if skipped:
        print(f"skipped: {', '.join(skipped)}")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())

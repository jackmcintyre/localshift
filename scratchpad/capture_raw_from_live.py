"""Capture a full-precision RAW horizon from the live optimizer plan.

Emits a drop-in replacement for the ``RAW`` block in ``deferral_replay.py``, so the
anti-cycling probes can be re-run on input that is no longer rounded to whole cents
(see #954, #958).

Usage
-----
    export HA_URL=http://homeassistant:8123          # same vars deploy.sh uses
    export HA_LONG_LIVED_TOKEN=...
    python3 scratchpad/capture_raw_from_live.py > scratchpad/raw_<date>.py

Or, without a token, against a saved copy of the sensor's attributes:

    python3 scratchpad/capture_raw_from_live.py path/to/plan_detailed.json

Selection rule — CORRECTED 2026-08-30 (supersedes the earlier "capture after the
demand window" guidance, which was wrong and produced a useless baseline).

What makes a horizon discriminating is a **solar deficit before the next window**,
not the hour of capture. Price resolution only bites in the *marginal* regime:
surplus solar makes the grid charge unnecessary and a deep deficit makes it
compulsory, and neither cares about a cent. So capture when either holds, at any
hour:

  * an overcast day (Solcast forecast well under the day's load), or
  * a low starting SOC with a target still to reach.

Check before capturing: the plan must contain at least one grid-charge slot
(``charge_slots > 0`` in the "Pre-charge decision" log line). ``scratchpad/raw_2026-08-29.py``
is the counter-example — 31.6 kWh solar against 28.6 kWh load, so the DP
grid-charged zero times and every A/B arm came out identical.
"""

import json
import os
import sys
import urllib.request

ENTITY = "sensor.localshift_optimizer_plan_detailed"


def fetch_from_ha():
    url = os.environ.get("HA_URL", "http://homeassistant:8123").rstrip("/")
    token = os.environ.get("HA_LONG_LIVED_TOKEN")
    if not token:
        sys.exit(
            "HA_LONG_LIVED_TOKEN is not set. Export it (deploy.sh uses the same var), "
            "or pass a saved JSON file as an argument."
        )
    req = urllib.request.Request(
        f"{url}/api/states/{ENTITY}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)["attributes"]


def main():
    if len(sys.argv) > 1:
        attrs = json.load(open(sys.argv[1]))
        attrs = attrs.get("attributes", attrs)
    else:
        attrs = fetch_from_ha()

    decisions = attrs.get("decisions") or []
    if not decisions:
        sys.exit(f"{ENTITY} has no decisions — is the optimizer running?")

    prices = [d["buy_price"] for d in decisions]
    distinct = len(set(prices))
    quantised = all(abs(p * 100 - round(p * 100)) < 1e-9 for p in prices)

    print(f"# Captured from {ENTITY} at {attrs.get('computed_at')}")
    print(f"# {len(decisions)} slots, {distinct} distinct buy prices")
    if quantised:
        print("# WARNING: every price is a whole number of cents — this capture is")
        print("#          still quantised. Check pricing_data_source is amber_express")
        print("#          and that the config entry has been reloaded (see #954).")
    print("#")
    print("# Drop-in replacement for the RAW block in deferral_replay.py.")
    print("RAW = [")
    for d in decisions:
        hhmm = d["timestamp_iso"][11:16]
        print(
            f"    ({d['slot_index']}, \"{hhmm}\", {d['slot_interval_minutes']}, "
            f"{d['buy_price']!r}, {d['sell_price']!r}, "
            f"{round(d['solar_kwh'], 4)!r}, {round(d['consumption_kwh'], 4)!r}),"
        )
    print("]")
    print()
    print(f"INITIAL_SOC = {decisions[0].get('predicted_soc_pct')}")
    print("# DW_ENTRY_IDX / DW_END_IDX: set from the demand-window bounds of this horizon.")


if __name__ == "__main__":
    main()

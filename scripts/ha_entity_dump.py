#!/usr/bin/env python3
"""ha_entity_dump.py — pull every LocalShift entity plus its dependencies
(Solcast, Amber, Powerwall/Tesla) from Home Assistant in complete detail.

For each matched entity it captures the current state, *all* attributes,
last_changed / last_updated, and the originating context. Output is written
as both a full-fidelity JSON file and a human-readable text report, grouped
by dependency (localshift / solcast / amber / powerwall / helpers).

Credentials
-----------
Resolved in this order (first hit wins):
  1. --url / --token CLI flags
  2. HA_URL / HA_TOKEN env vars  (also HOMEASSISTANT_URL / HOMEASSISTANT_TOKEN)
  3. The `home-assistant` MCP server block in ~/.claude.json
The token is never written into the output files.

Usage
-----
  scripts/ha_entity_dump.py                       # dump to ./tmp/
  scripts/ha_entity_dump.py --out-dir /some/dir
  scripts/ha_entity_dump.py --group solcast       # restrict to one group
  scripts/ha_entity_dump.py --history 24          # also pull 24h of history
  scripts/ha_entity_dump.py --stdout              # print report to stdout only
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Dependency groups. Each entity is bucketed into the FIRST group whose regex
# matches its entity_id; "localshift_helpers" catches the input_*/script/
# automation entities the integration drives that aren't named "localshift".
# ---------------------------------------------------------------------------
GROUPS: list[tuple[str, re.Pattern]] = [
    ("localshift", re.compile(r"localshift", re.I)),
    ("solcast", re.compile(r"solcast", re.I)),
    ("amber", re.compile(r"amber", re.I)),
    ("powerwall", re.compile(
        r"powerwall|tesla|my_home_(battery|grid|load|solar|vpp)", re.I)),
    ("localshift_helpers", re.compile(
        r"battery_automation|battery_target|battery_hold|battery_savings|"
        r"battery_charge_cost|battery_set_|battery_force|battery_boost|"
        r"powerwall_charge_start|powerwall_notified|precharge_battery|"
        r"solar_export_hold|solar_can_reach_target", re.I)),
]


def resolve_credentials(args) -> tuple[str, str]:
    url = args.url or os.environ.get("HA_URL") or os.environ.get("HOMEASSISTANT_URL")
    token = args.token or os.environ.get("HA_TOKEN") or os.environ.get("HOMEASSISTANT_TOKEN")
    if url and token:
        return url.rstrip("/"), token

    # Fall back to the home-assistant MCP server config in ~/.claude.json.
    cfg = Path.home() / ".claude.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text())
        except json.JSONDecodeError:
            data = None

        def find_ha(obj):
            if isinstance(obj, dict):
                if "home-assistant" in obj and isinstance(obj["home-assistant"], dict):
                    return obj["home-assistant"].get("env", {})
                for v in obj.values():
                    hit = find_ha(v)
                    if hit:
                        return hit
            elif isinstance(obj, list):
                for v in obj:
                    hit = find_ha(v)
                    if hit:
                        return hit
            return None

        env = find_ha(data) or {}
        url = url or env.get("HOMEASSISTANT_URL")
        token = token or env.get("HOMEASSISTANT_TOKEN")

    if not url or not token:
        sys.exit("ERROR: could not resolve HA url/token. Set HA_URL/HA_TOKEN "
                 "or pass --url/--token, or configure the home-assistant MCP "
                 "server in ~/.claude.json.")
    return url.rstrip("/"), token


def ha_get(url: str, token: str, path: str):
    req = urllib.request.Request(
        f"{url}{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"ERROR: HTTP {e.code} for {path}: {e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        sys.exit(f"ERROR: cannot reach {url}{path}: {e.reason}")


def bucket(entity_id: str) -> str | None:
    for name, pat in GROUPS:
        if pat.search(entity_id):
            return name
    return None


def render_report(grouped: dict[str, list[dict]], meta: dict) -> str:
    lines = []
    lines.append("=" * 78)
    lines.append("LocalShift + dependency entity dump")
    lines.append(f"  host      : {meta['url']}")
    lines.append(f"  pulled at : {meta['pulled_at']}")
    lines.append(f"  ha version: {meta.get('ha_version', '?')}")
    lines.append(f"  entities  : {meta['matched']} matched of {meta['total']} total")
    lines.append("=" * 78)

    for group in [g[0] for g in GROUPS]:
        ents = grouped.get(group, [])
        if not ents:
            continue
        lines.append("")
        lines.append(f"### {group.upper()}  ({len(ents)} entities)")
        lines.append("-" * 78)
        for s in sorted(ents, key=lambda e: e["entity_id"]):
            lines.append(f"\n● {s['entity_id']}")
            lines.append(f"    state        : {s.get('state')!r}")
            lines.append(f"    last_changed : {s.get('last_changed')}")
            lines.append(f"    last_updated : {s.get('last_updated')}")
            attrs = s.get("attributes", {})
            if attrs:
                lines.append("    attributes   :")
                for k in sorted(attrs):
                    v = attrs[k]
                    rendered = json.dumps(v, default=str)
                    if len(rendered) > 2000:
                        rendered = rendered[:2000] + f"... <truncated, {len(rendered)} chars>"
                    lines.append(f"        {k} = {rendered}")
            else:
                lines.append("    attributes   : (none)")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="HA base URL")
    ap.add_argument("--token", help="HA long-lived access token")
    ap.add_argument("--out-dir", default="tmp", help="output directory (default: ./tmp)")
    ap.add_argument("--group", choices=[g[0] for g in GROUPS],
                    help="restrict output to a single dependency group")
    ap.add_argument("--history", type=int, metavar="HOURS",
                    help="also pull N hours of state history per entity")
    ap.add_argument("--stdout", action="store_true",
                    help="print the text report to stdout instead of writing files")
    args = ap.parse_args()

    url, token = resolve_credentials(args)

    cfg = ha_get(url, token, "/api/config")
    states = ha_get(url, token, "/api/states")

    grouped: dict[str, list[dict]] = {}
    for s in states:
        g = bucket(s["entity_id"])
        if g is None:
            continue
        if args.group and g != args.group:
            continue
        grouped.setdefault(g, []).append(s)

    matched = sum(len(v) for v in grouped.values())

    # Optional history pull.
    history: dict[str, list] = {}
    if args.history:
        start = (datetime.now(timezone.utc) - timedelta(hours=args.history)
                 ).strftime("%Y-%m-%dT%H:%M:%S%z")
        eids = [s["entity_id"] for v in grouped.values() for s in v]
        for eid in eids:
            path = f"/api/history/period/{start}?filter_entity_id={eid}&minimal_response"
            try:
                history[eid] = ha_get(url, token, path)
            except SystemExit:
                history[eid] = []

    meta = {
        "url": url,
        "pulled_at": datetime.now(timezone.utc).isoformat(),
        "ha_version": cfg.get("version"),
        "total": len(states),
        "matched": matched,
    }

    report = render_report(grouped, meta)

    if args.stdout:
        print(report)
        return

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = out_dir / f"localshift-entities-{stamp}"

    payload = {"meta": meta, "groups": {g: grouped.get(g, []) for g in grouped}}
    if args.history:
        payload["history"] = history

    json_path = base.with_suffix(".json")
    txt_path = base.with_suffix(".txt")
    json_path.write_text(json.dumps(payload, indent=2, default=str))
    txt_path.write_text(report)

    print(f"Wrote {matched} entities across {len(grouped)} groups:")
    for g in [x[0] for x in GROUPS]:
        if grouped.get(g):
            print(f"  {g:20s} {len(grouped[g]):3d}")
    print(f"\n  JSON   : {json_path}")
    print(f"  Report : {txt_path}")
    if args.history:
        print(f"  History: {args.history}h included in JSON")


if __name__ == "__main__":
    main()

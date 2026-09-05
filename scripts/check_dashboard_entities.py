#!/usr/bin/env python3
"""Validate dashboard.yaml against a live Home Assistant.

Three checks, all of which need live state and so cannot live in the unit
tests:

1. **Every entity the dashboard references exists.** This is the check that
   matters. `button.localshift_reset_learning` sat on the Settings view for a
   month after the parameter-learning cut renamed it to
   `..._reset_learning_data`; the button silently did nothing and no test
   noticed, because no test can know what the live entity registry holds.
2. **Every LocalShift entity appears somewhere on the dashboard.** Catches the
   opposite drift — a sensor ships and nothing surfaces it.
3. **Every markdown card's Jinja renders.** A template that raises renders as a
   card-wide error in the UI and is invisible from the repo.

Credentials come from ``HOMEASSISTANT_URL`` / ``HOMEASSISTANT_TOKEN`` in the
environment, or from the same keys anywhere in ``~/.claude.json``.

Usage::

    python3 scripts/check_dashboard_entities.py
    python3 scripts/check_dashboard_entities.py --dashboard path/to/dashboard.yaml
    python3 scripts/check_dashboard_entities.py --no-unreferenced

Exits non-zero if any check fails. Run it after every deploy that touches the
dashboard — the dashboard is YAML-mode, so HA picks up changes on a Lovelace
reload, with no restart and no validation of its entity references.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DASHBOARD = REPO_ROOT / "custom_components" / "localshift" / "dashboard.yaml"

# Entity ids anywhere in the file — structured `entity:` keys and Jinja alike.
ENTITY_RE = re.compile(
    r"\b(?:sensor|binary_sensor|switch|select|number|button|input_boolean|input_number)"
    r"\.[a-z0-9_]+"
)
# `button.press` is an action name, not an entity.
NOT_ENTITIES = {"button.press"}

LOCALSHIFT_PREFIXES = (
    "sensor.localshift_",
    "binary_sensor.localshift_",
    "switch.localshift_",
    "select.localshift_",
    "number.localshift_",
    "button.localshift_",
)


def load_credentials() -> tuple[str, str]:
    """Return (base_url, token) from the environment or ~/.claude.json."""
    url = os.environ.get("HOMEASSISTANT_URL")
    token = os.environ.get("HOMEASSISTANT_TOKEN")
    if url and token:
        return url.rstrip("/"), token

    claude_json = Path.home() / ".claude.json"
    if claude_json.exists():
        found = _find_credentials(json.loads(claude_json.read_text()))
        if found:
            return found[0].rstrip("/"), found[1]

    raise SystemExit(
        "No Home Assistant credentials. Set HOMEASSISTANT_URL and "
        "HOMEASSISTANT_TOKEN, or add them to ~/.claude.json."
    )


def _find_credentials(node: Any) -> tuple[str, str] | None:
    if isinstance(node, dict):
        if "HOMEASSISTANT_URL" in node and "HOMEASSISTANT_TOKEN" in node:
            return node["HOMEASSISTANT_URL"], node["HOMEASSISTANT_TOKEN"]
        for value in node.values():
            found = _find_credentials(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_credentials(value)
            if found:
                return found
    return None


def ha_get(url: str, token: str, path: str) -> Any:
    request = urllib.request.Request(
        f"{url}/api/{path}", headers={"Authorization": f"Bearer {token}"}
    )
    return json.loads(urllib.request.urlopen(request, timeout=30).read())


def ha_render(url: str, token: str, template: str) -> tuple[bool, str]:
    request = urllib.request.Request(
        f"{url}/api/template",
        data=json.dumps({"template": template}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        return True, urllib.request.urlopen(request, timeout=30).read().decode()
    except urllib.error.HTTPError as err:
        return False, err.read().decode()[:400]


def collect_markdown(node: Any, path: str, out: list[tuple[str, str]]) -> None:
    if isinstance(node, dict):
        if node.get("type") == "markdown" and "content" in node:
            out.append((path, node["content"]))
        for key, value in node.items():
            collect_markdown(value, f"{path}/{key}", out)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            collect_markdown(value, f"{path}[{index}]", out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument(
        "--no-unreferenced",
        action="store_true",
        help="Skip check 2 (LocalShift entities missing from the dashboard).",
    )
    args = parser.parse_args()

    raw = args.dashboard.read_text()
    config = yaml.safe_load(raw)
    url, token = load_credentials()

    live = {state["entity_id"] for state in ha_get(url, token, "states")}
    referenced = {e for e in ENTITY_RE.findall(raw) if e not in NOT_ENTITIES}

    failures = 0

    missing = sorted(e for e in referenced if e not in live)
    print(
        f"[1/3] entity references: {len(referenced)} referenced, {len(missing)} missing"
    )
    for entity in missing:
        failures += 1
        print(f"      MISSING  {entity}")

    if not args.no_unreferenced:
        unreferenced = sorted(
            e for e in live if e.startswith(LOCALSHIFT_PREFIXES) and e not in referenced
        )
        print(f"[2/3] unreferenced LocalShift entities: {len(unreferenced)}")
        for entity in unreferenced:
            failures += 1
            print(f"      UNSURFACED  {entity}")
    else:
        print("[2/3] unreferenced check skipped")

    templates: list[tuple[str, str]] = []
    for view in config.get("views", []):
        collect_markdown(view, view.get("title", "?"), templates)
    broken = 0
    for path, template in templates:
        ok, detail = ha_render(url, token, template)
        if not ok:
            broken += 1
            failures += 1
            print(f"      RENDER FAILED  {path}: {detail}")
    print(f"[3/3] markdown templates: {len(templates)} rendered, {broken} failed")

    if failures:
        print(f"\nFAILED — {failures} problem(s).")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

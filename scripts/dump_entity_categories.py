"""Print the docs/ENTITY_REFERENCE.md § Entity Categories table.

Regenerates the markdown table by running every platform's real
async_setup_entry against mocked hass/coordinator/entry objects and reading
back each entity's unique_id, entity_id (derived the same way Home Assistant
would from the device name + entity name), and entity_category. Paste the
output over the existing table in docs/ENTITY_REFERENCE.md § Entity
Categories after adding, removing, or recategorising an entity.

Usage:
    uv run python scripts/dump_entity_categories.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from homeassistant.util import slugify  # noqa: E402

DEVICE_NAME = "LocalShift"

PLATFORM_MODULES = [
    ("switch", "switch"),
    ("select", "select"),
    ("sensor", "sensor"),
    ("binary_sensor", "binary_sensor"),
    ("number", "number"),
    ("button", "button"),
]


async def _collect() -> list[tuple[str, str, str, str]]:
    """Return (entity_id, unique_id, platform, category) rows, sorted."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "dump_entry"
    entry.data = {}
    entry.options = {}
    entry.runtime_data = MagicMock()

    rows: list[tuple[str, str, str, str]] = []

    for module_name, platform in PLATFORM_MODULES:
        module = __import__(
            f"custom_components.localshift.{module_name}",
            fromlist=["async_setup_entry"],
        )
        add_entities = MagicMock()
        await module.async_setup_entry(hass, entry, add_entities)
        entities = add_entities.call_args[0][0]

        for entity in entities:
            unique_id = entity._attr_unique_id
            category = getattr(entity, "_attr_entity_category", None)
            category_str = category.value if category is not None else "—"
            name = getattr(entity, "_attr_name", None)
            entity_id = (
                f"{platform}.{slugify(f'{DEVICE_NAME} {name}')}"
                if name
                else f"{platform}.{unique_id}"
            )
            rows.append((entity_id, unique_id, platform, category_str))

    rows.sort(key=lambda r: (r[2], r[1]))
    return rows


def main() -> None:
    rows = asyncio.run(_collect())
    print("| Entity ID | Unique ID | Platform | Category |")
    print("|-----------|-----------|----------|----------|")
    for entity_id, unique_id, platform, category in rows:
        print(f"| `{entity_id}` | `{unique_id}` | `{platform}` | {category} |")
    print(f"\n<!-- {len(rows)} entities total -->")


if __name__ == "__main__":
    main()

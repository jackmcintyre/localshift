"""Shared entity_id derivation for the code-vs-docs anti-drift tests.

Both `scripts/dump_entity_categories.py` and
`tests/test_referenced_entities_exist.py` need to answer the same question:
"what entity_id does Home Assistant actually assign to this entity?" This
module is the single place that answers it, so the two cannot drift apart.

IMPORTANT: this mirrors Home Assistant's entity_id derivation, which is NOT
simply the unique_id. HA derives entity_id from the device name + the
entity's own `_attr_name` (slugified) when `_attr_name` is set; only an
entity with no `_attr_name` falls back to `{platform}.{unique_id}`. Several
LocalShift entities set both a unique_id and a display name that differ, so
deriving from unique_id alone silently produces the WRONG entity_id for
those. See docs/ENTITY_REFERENCE.md's "Phase 5 Migration (#447)" note and
issue #971/#977/#978 for the concrete cases this caught.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.util import slugify

DEVICE_NAME = "LocalShift"

# (module name under custom_components.localshift, platform domain)
PLATFORM_MODULES = [
    ("switch", "switch"),
    ("select", "select"),
    ("sensor", "sensor"),
    ("binary_sensor", "binary_sensor"),
    ("number", "number"),
    ("button", "button"),
]


def derive_entity_id(platform: str, unique_id: str, name: str | None) -> str:
    """Derive the entity_id the way Home Assistant would.

    Mirrors scripts/dump_entity_categories.py's derivation exactly -- do not
    let this drift from that script.
    """
    if name:
        return f"{platform}.{slugify(f'{DEVICE_NAME} {name}')}"
    return f"{platform}.{unique_id}"


def _make_mock_entry():
    entry = MagicMock()
    entry.entry_id = "test_entity_ids"
    entry.data = {}
    entry.options = {}
    entry.runtime_data = MagicMock()
    return entry


async def collect_registered_entity_ids() -> set[str]:
    """Run every platform's real async_setup_entry and collect entity_ids.

    Uses the same MagicMock hass/entry style as tests/test_entity_category.py
    and tests/test_switch.py / tests/test_sensor.py -- no real Home Assistant
    instance needed.
    """
    hass = MagicMock()
    entry = _make_mock_entry()

    entity_ids: set[str] = set()

    for module_name, platform in PLATFORM_MODULES:
        module = __import__(
            f"custom_components.localshift.{module_name}",
            fromlist=["async_setup_entry"],
        )
        add_entities = MagicMock()
        await module.async_setup_entry(hass, entry, add_entities)
        add_entities.assert_called_once()
        entities = add_entities.call_args[0][0]

        for entity in entities:
            unique_id = entity._attr_unique_id
            name = getattr(entity, "_attr_name", None)
            entity_ids.add(derive_entity_id(platform, unique_id, name))

    return entity_ids

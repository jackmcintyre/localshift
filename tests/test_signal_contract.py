"""CI guard for the published signal contract (issue #1234).

docs/ENTITY_REFERENCE.md has a "Signals for other automations" section that
lists the entities other Home Assistant automations may rely on. This test
keeps that list honest: every entity the section lists as published and
available must be defined by the integration, and every attribute it
documents for one must still be published by it.

It fails when a listed entity is removed or renamed, or when a documented
attribute disappears. Pure Python: it runs each platform's real
``async_setup_entry`` against mocks (same approach as
tests/test_referenced_entities_exist.py), with no Home Assistant instance.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.coordinator.data import CoordinatorData
from tests.helpers.entity_ids import (
    PLATFORM_MODULES,
    _make_mock_entry,
    derive_entity_id,
)

DOC = Path(__file__).resolve().parent.parent / "docs" / "ENTITY_REFERENCE.md"
SECTION_HEADING = "## Signals for other automations"

# The decided contract (issue #1234). Removing one of these from the docs must
# fail here, not only removing it from the code.
CONTRACT_ENTITIES = {
    "sensor.localshift_load_shift_signal",
    "binary_sensor.localshift_excess_solar_available",
    "binary_sensor.localshift_demand_window",
}

# Plan internals the docs must never present as part of the contract.
NOT_CONTRACT_PREFIXES = ("optimizer_plan", "forecast_")


def _section() -> str:
    """Return the contract section's text (heading to the next `## `)."""
    text = DOC.read_text(encoding="utf-8")
    start = text.find(SECTION_HEADING)
    assert start != -1, f"{SECTION_HEADING!r} missing from {DOC.name}"
    rest = text[start + len(SECTION_HEADING) :]
    end = re.search(r"^## ", rest, re.MULTILINE)
    return rest[: end.start()] if end else rest


def _subsection(text: str, heading: str) -> str:
    """Return the text under a `#### heading` up to the next `#### ` or `### `."""
    start = text.find(heading)
    assert start != -1, f"{heading!r} missing from the contract section"
    rest = text[start + len(heading) :]
    end = re.search(r"^#{1,4} ", rest, re.MULTILINE)
    return rest[: end.start()] if end else rest


def _available_signals() -> dict[str, set[str]]:
    """Map each published-and-available entity id to its documented attributes."""
    block = _subsection(_section(), "#### Available now")
    parts = re.split(r"^##### `([a-z_]+\.[a-z0-9_]+)`", block, flags=re.MULTILINE)
    # parts = [preamble, id1, body1, id2, body2, ...]
    signals: dict[str, set[str]] = {}
    for entity_id, body in zip(parts[1::2], parts[2::2], strict=True):
        attrs: set[str] = set()
        in_table = False
        for line in body.splitlines():
            if line.startswith("| Attribute |"):
                in_table = True
                continue
            if in_table and line.startswith("|"):
                cell = line.split("|")[1].strip()
                match = re.fullmatch(r"`([a-z_0-9]+)`", cell)
                if match:
                    attrs.add(match.group(1))
            elif in_table:
                in_table = False
        signals[entity_id] = attrs
    return signals


async def _defined_entities() -> dict[str, object]:
    """Run every platform's real setup and return {entity_id: entity}."""
    hass = MagicMock()
    entry = _make_mock_entry()
    # Entities read attributes from coordinator.data; give them real defaults.
    entry.runtime_data = MagicMock()
    entry.runtime_data.data = CoordinatorData()

    entities: dict[str, object] = {}
    for module_name, platform in PLATFORM_MODULES:
        module = __import__(
            f"custom_components.localshift.{module_name}",
            fromlist=["async_setup_entry"],
        )
        add_entities = MagicMock()
        await module.async_setup_entry(hass, entry, add_entities)
        for entity in add_entities.call_args[0][0]:
            entity_id = derive_entity_id(
                platform, entity._attr_unique_id, getattr(entity, "_attr_name", None)
            )
            entities[entity_id] = entity
    return entities


class TestSignalContract:
    """The docs' published-signal list matches what the integration defines."""

    def test_section_lists_the_decided_contract(self):
        assert set(_available_signals()) == CONTRACT_ENTITIES

    @pytest.mark.asyncio
    async def test_every_listed_entity_is_defined_by_the_integration(self):
        defined = await _defined_entities()
        assert len(defined) >= 60, "entity collection failed; check would be vacuous"
        missing = sorted(set(_available_signals()) - set(defined))
        assert not missing, (
            "Signal contract lists entities the integration no longer defines "
            f"(removed or renamed): {missing}. Renaming or removing a published "
            "signal needs a deprecation note in docs/ENTITY_REFERENCE.md."
        )

    @pytest.mark.asyncio
    async def test_every_documented_attribute_is_still_published(self):
        defined = await _defined_entities()
        problems = []
        for entity_id, documented in _available_signals().items():
            published = set(defined[entity_id].extra_state_attributes or {})
            gone = sorted(documented - published)
            if gone:
                problems.append(f"{entity_id}: {gone}")
        assert not problems, (
            "Documented attributes no longer published (attributes are only "
            f"ever added, never renamed or removed): {problems}"
        )

    def test_attribute_tables_are_not_empty_where_attributes_exist(self):
        """Guard the parser: a table that parses to nothing proves nothing."""
        signals = _available_signals()
        assert "grid_charge_risk" in signals["sensor.localshift_load_shift_signal"]
        assert (
            "can_add_load_now"
            in signals["binary_sensor.localshift_excess_solar_available"]
        )

    def test_plan_sensors_are_not_in_the_published_list(self):
        for entity_id in _available_signals():
            name = entity_id.split(".", 1)[1].removeprefix("localshift_")
            assert not name.startswith(NOT_CONTRACT_PREFIXES), entity_id

    def test_docs_state_plan_sensors_are_not_part_of_the_contract(self):
        section = _section()
        assert "optimizer_plan*" in section
        assert "forecast_*" in section
        assert "not part of the contract" in section

    def test_house_inputs_are_exactly_away_and_weather(self):
        block = _subsection(_section(), "### Consumed from the house")
        assert set(re.findall(r"`(away_entity|weather_entity)`", block)) == {
            "away_entity",
            "weather_entity",
        }

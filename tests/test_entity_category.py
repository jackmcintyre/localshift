"""Anti-drift gate for entity_category (Issue #787).

Every LocalShift entity's `entity_category` is documented in
`docs/ENTITY_REFERENCE.md` under `## Entity Categories`. This test builds
both sides mechanically -- it runs every platform's real `async_setup_entry`
against mocked hass/coordinator/entry objects to see what the code actually
produces, and parses the doc's table -- rather than hand-maintaining a
second list that could drift from either the code or the doc on its own.

Three ways this can fail, each naming the offending unique_id(s):
  - an entity exists in code but has no row in the doc table
  - a doc row names a unique_id no entity actually has (stale/removed entity)
  - a unique_id's category in code doesn't match its documented category

Follows the mock style of tests/test_switch.py / tests/test_sensor.py: no
real Home Assistant instance is needed, just MagicMock coordinators/entries.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ENTITY_REFERENCE_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "ENTITY_REFERENCE.md"
)

# (module name under custom_components.localshift, platform domain)
PLATFORM_MODULES = [
    ("switch", "switch"),
    ("select", "select"),
    ("sensor", "sensor"),
    ("binary_sensor", "binary_sensor"),
    ("number", "number"),
    ("button", "button"),
]


def _make_mock_entry():
    entry = MagicMock()
    entry.entry_id = "test_entry_category"
    entry.data = {}
    entry.options = {}
    entry.runtime_data = MagicMock()
    return entry


async def _collect_entities_from_code() -> dict[str, str | None]:
    """Run every platform's async_setup_entry and collect unique_id -> category.

    Category is the HA `EntityCategory` value string ("config", "diagnostic")
    or None for an entity with no category.
    """
    hass = MagicMock()
    entry = _make_mock_entry()

    categories: dict[str, str | None] = {}
    seen_platforms: dict[str, str] = {}

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
            category = getattr(entity, "_attr_entity_category", None)
            category_str = category.value if category is not None else None
            categories[unique_id] = category_str
            seen_platforms[unique_id] = platform

    return categories, seen_platforms


def _parse_doc_table() -> dict[str, str | None]:
    """Parse the `## Entity Categories` markdown table into unique_id -> category."""
    text = ENTITY_REFERENCE_PATH.read_text()

    marker = "## Entity Categories"
    start = text.find(marker)
    assert start != -1, (
        f"docs/ENTITY_REFERENCE.md is missing the '{marker}' section entirely"
    )

    # Section runs until the next '---' or level-2 heading after the marker.
    rest = text[start:]
    end_match = re.search(r"\n---\n", rest)
    section = rest[: end_match.start()] if end_match else rest

    doc_categories: dict[str, str | None] = {}
    row_pattern = re.compile(
        r"^\|\s*`[^`]+`\s*\|\s*`([^`]+)`\s*\|\s*`[^`]+`\s*\|\s*([^\|]+?)\s*\|$",
        re.MULTILINE,
    )
    for match in row_pattern.finditer(section):
        unique_id, category = match.group(1), match.group(2).strip()
        doc_categories[unique_id] = None if category in ("—", "-", "") else category

    assert doc_categories, (
        "Parsed zero rows from docs/ENTITY_REFERENCE.md § Entity Categories -- "
        "table format may have changed; update the parser in "
        "tests/test_entity_category.py"
    )
    return doc_categories


class TestEntityCategoryDocSync:
    """Entity categories in code and docs/ENTITY_REFERENCE.md must agree."""

    @pytest.mark.asyncio
    async def test_no_undocumented_entity(self):
        """Every entity constructed by a platform has a row in the doc table."""
        code_categories, _ = await _collect_entities_from_code()
        doc_categories = _parse_doc_table()

        undocumented = sorted(set(code_categories) - set(doc_categories))
        assert not undocumented, (
            "Entities exist in code with no row in docs/ENTITY_REFERENCE.md "
            f"§ Entity Categories: {undocumented}. "
            "Add a row to docs/ENTITY_REFERENCE.md § Entity Categories."
        )

    @pytest.mark.asyncio
    async def test_no_phantom_doc_row(self):
        """Every doc row names a unique_id some entity actually has."""
        code_categories, _ = await _collect_entities_from_code()
        doc_categories = _parse_doc_table()

        phantom = sorted(set(doc_categories) - set(code_categories))
        assert not phantom, (
            "docs/ENTITY_REFERENCE.md § Entity Categories documents unique_ids "
            f"with no matching entity in code: {phantom}. "
            "Remove the stale row(s) from docs/ENTITY_REFERENCE.md § Entity Categories."
        )

    @pytest.mark.asyncio
    async def test_category_matches_doc(self):
        """Each unique_id's code category matches its documented category."""
        code_categories, _ = await _collect_entities_from_code()
        doc_categories = _parse_doc_table()

        common = set(code_categories) & set(doc_categories)
        mismatches = sorted(
            uid for uid in common if code_categories[uid] != doc_categories[uid]
        )
        details = [
            f"{uid}: code={code_categories[uid]!r} doc={doc_categories[uid]!r}"
            for uid in mismatches
        ]
        assert not mismatches, (
            "entity_category mismatch between code and "
            f"docs/ENTITY_REFERENCE.md § Entity Categories: {details}. "
            "Update docs/ENTITY_REFERENCE.md § Entity Categories to match the code "
            "(or fix the code if the doc is right)."
        )

    @pytest.mark.asyncio
    async def test_entity_count_matches_overview(self):
        """The doc table's row count matches the Overview total (sanity check)."""
        code_categories, _ = await _collect_entities_from_code()
        doc_categories = _parse_doc_table()

        assert len(code_categories) == len(doc_categories)

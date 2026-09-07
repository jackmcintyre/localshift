"""CI guard for stale/renamed entity references (Issues #971/#977/#978).

Every `domain.localshift_*` id mentioned under `docs/`, `scripts/`, or
`README.md` must be an entity the integration actually registers today, or
be explicitly documented as a deliberate historical mention (a rename note
in ENTITY_REFERENCE.md's Phase 5 Migration table/provenance lines). This is
the mechanical version of the manual audit that found #971/#977/#978's stale
references -- so the next rename fails CI instead of silently rotting docs
and scripts the way the #447 and #962 renames did.

IMPORTANT SCOPE NOTE: this test compares *code* (what an entity's platform
setup would derive as its entity_id) against *docs/scripts*. It is NOT a
live-registry check. Home Assistant keeps the entity_id assigned at first
registration even if the code's derived id later changes (e.g. a display
name edit) -- dashboard.yaml is known to reference
`button.localshift_reset_learning_data`, a third spelling matching neither
the code-derived id nor any doc, which is exactly this kind of drift. That
gap is intentionally out of scope here; `scripts/check_dashboard_entities.py`
is the complementary live-registry check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.helpers.entity_ids import collect_registered_entity_ids

REPO_ROOT = Path(__file__).resolve().parent.parent

ENTITY_ID_PATTERN = re.compile(
    r"\b(?:sensor|binary_sensor|switch|number|select|button)\.localshift_[a-z0-9_]+"
)

# Deliberate historical mentions of a retired/renamed entity id, scoped to the
# exact file(s) where the mention is a documented rename note -- not a
# blanket exemption. A new reference to one of these ids in any OTHER file
# still fails the drift check below.
RETIRED_IDS: dict[str, frozenset[str]] = {
    "sensor.localshift_forecast_daily": frozenset({"docs/ENTITY_REFERENCE.md"}),
    "sensor.localshift_forecast_grid": frozenset({"docs/ENTITY_REFERENCE.md"}),
    "sensor.localshift_optimizer_shadow_plan": frozenset({"docs/ENTITY_REFERENCE.md"}),
    "sensor.localshift_optimizer_shadow_summary": frozenset({
        "docs/ENTITY_REFERENCE.md"
    }),
    "sensor.localshift_optimizer_comparison": frozenset({"docs/ENTITY_REFERENCE.md"}),
}


def _scan_referenced_entity_ids() -> dict[str, list[str]]:
    """Return {entity_id: [\"relative/path.md:12\", ...]} for every match.

    Scans docs/**/*.md (rglob, so docs/superpowers/ is covered), README.md,
    and scripts/**/*.py + scripts/**/*.sh.
    """
    paths = list((REPO_ROOT / "docs").rglob("*.md"))
    readme = REPO_ROOT / "README.md"
    if readme.exists():
        paths.append(readme)
    paths.extend((REPO_ROOT / "scripts").rglob("*.py"))
    paths.extend((REPO_ROOT / "scripts").rglob("*.sh"))

    found: dict[str, list[str]] = {}
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = str(path.relative_to(REPO_ROOT))
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in ENTITY_ID_PATTERN.finditer(line):
                found.setdefault(match.group(0), []).append(f"{rel}:{lineno}")
    return found


class TestReferencedEntitiesExist:
    """Every entity id mentioned in docs/scripts/README must be real."""

    @pytest.mark.asyncio
    async def test_registered_set_is_sane(self):
        """Sanity check: a mocking failure must fail loudly, not vacuously pass."""
        registered = await collect_registered_entity_ids()

        assert len(registered) >= 60, (
            f"Only {len(registered)} entity ids were collected from the platform "
            "setup functions -- expected >= 60. This usually means a platform's "
            "async_setup_entry raised or mocking broke, which would make the "
            "drift check below pass vacuously. Fix the collection before "
            "trusting a green run of this test file."
        )

    @pytest.mark.asyncio
    async def test_no_stale_or_renamed_entity_references(self):
        """Every referenced id is registered, or is a scoped RETIRED_IDS mention."""
        registered = await collect_registered_entity_ids()
        referenced = _scan_referenced_entity_ids()

        violations = []
        for entity_id, locations in sorted(referenced.items()):
            if entity_id in registered:
                continue
            allowed_files = RETIRED_IDS.get(entity_id)
            if allowed_files is None:
                violations.append((entity_id, locations))
                continue
            offending = [
                loc for loc in locations if loc.split(":")[0] not in allowed_files
            ]
            if offending:
                violations.append((entity_id, offending))

        details = "\n".join(
            f"  {entity_id}: {', '.join(locs)}" for entity_id, locs in violations
        )
        assert not violations, (
            "Stale or renamed entity id(s) referenced outside their allowed "
            f"provenance mentions:\n{details}\n\n"
            "If the entity was genuinely renamed, update the reference to the "
            "current id. If this is a deliberate historical mention (e.g. a "
            "rename note), add it to RETIRED_IDS in "
            "tests/test_referenced_entities_exist.py scoped to that exact file."
        )

    @pytest.mark.asyncio
    async def test_retired_ids_are_actually_retired_and_actually_mentioned(self):
        """Anti-graveyard check: RETIRED_IDS must stay small and honest.

        Every entry must (a) name an id that is NOT currently registered --
        otherwise it is dead weight hiding a real entity -- and (b) actually
        appear in at least one of its allowed files -- otherwise a stale
        allowlist entry could mask a future accidental re-reference without
        anyone noticing it did nothing.
        """
        registered = await collect_registered_entity_ids()
        referenced = _scan_referenced_entity_ids()

        stale_but_registered = sorted(
            entity_id for entity_id in RETIRED_IDS if entity_id in registered
        )
        assert not stale_but_registered, (
            "RETIRED_IDS names id(s) that ARE currently registered -- remove "
            f"them from the allowlist: {stale_but_registered}"
        )

        unused_entries = []
        for entity_id, allowed_files in RETIRED_IDS.items():
            locations = referenced.get(entity_id, [])
            mentioned_files = {loc.split(":")[0] for loc in locations}
            if not (mentioned_files & allowed_files):
                unused_entries.append(entity_id)
        assert not unused_entries, (
            "RETIRED_IDS entry does not appear in any of its allowed files -- "
            f"it is dead weight, remove it: {unused_entries}"
        )

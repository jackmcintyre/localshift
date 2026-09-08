"""Static gate: every ``CoordinatorData`` field must have a production reader.

Issue #981 found eighteen ``CoordinatorData`` fields that were never read
anywhere (some also never written), plus a nineteenth (``preserve_soc``) that
was read but never written so every read collapsed to a hardcoded literal.
Several of the never-read fields had test assertions on them — ``assert
data.primary_decision == BatteryMode.SELF_CONSUMPTION.value`` and similar —
which is exactly why a repo-wide reader census would not have caught this
batch: a test that merely *asserts a field's value* is not a consumer of it,
it is a mirror of whatever the field happens to hold. This gate therefore
scopes its reader census to production code only (``custom_components/``),
which is what gives it teeth: every field deleted for #981 had a test
reader, and none had a production one.

This file walks the AST instead of running anything: it collects every
declared field on ``CoordinatorData`` (only that class — ``PerformanceMetrics``,
``PhysicalResponseWatch`` and ``SyntheticSlotHealth`` are read indirectly
through their own outer field, e.g. ``data.performance_metrics.cost_trend``
reads ``performance_metrics``, and reusing the sibling gate's whole-module
field collector would drag in ~14 of their own inner fields and false-flag
them), then collects every ``<coordinator-data>.<field>`` access anywhere in
the integration *except* ``coordinator/data.py`` itself (the declaration is
not a reader of its own field), and fails when a declared field has none.

Detection is deliberately generous on the read side — the opposite bias from
the sibling gate (``test_sensor_field_writers.py``), which is strict on reads
and loose on writers because a missed writer there only delays a sensor bug.
Here a missed read fails the build on a genuinely live field, so this walker
counts a bare ``data``/``d`` name (not just ``self.coordinator.data`` or an
aliased assignment of it — every production consumer in this integration
receives the snapshot as a parameter spelled ``data``), an augmented
assignment target (``data.f += 1`` reads ``f`` before writing it), a
subscript access through the field (``data.f[k]`` and ``data.f[k] = v`` both
load ``data.f`` first), a mutating method call on the field
(``data.f.append(...)`` loads ``f`` to call a method on it), and
``getattr(data, "f", default)``.

DO NOT make this pass by widening the field census to include ``tests/`` or
``scripts/`` — that is the exact hole #981 exists to close (see above). If a
field is genuinely dead, delete it (and its writer, and its tests) in the
same commit — that is the fix this gate exists to force. Allow-listing a
field here to silence a genuine miss defeats the gate; if the walker is
wrong about a specific access pattern, teach it a new form instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "localshift"
DATA_MODULE = PACKAGE / "coordinator" / "data.py"

_BARE_DATA_NAMES = frozenset({"data", "d"})


def _flatten(target: ast.AST) -> list[ast.AST]:
    """Flatten tuple/list/starred assignment targets into leaf nodes."""
    if isinstance(target, (ast.Tuple, ast.List)):
        leaves: list[ast.AST] = []
        for elt in target.elts:
            leaves.extend(_flatten(elt))
        return leaves
    if isinstance(target, ast.Starred):
        return _flatten(target.value)
    return [target]


def _coordinator_data_fields() -> dict[str, int]:
    """AnnAssign field names declared directly on ``CoordinatorData`` -> line.

    Scoped to that one class (not every dataclass in the module) so a field
    on ``PerformanceMetrics``/``PhysicalResponseWatch``/``SyntheticSlotHealth``
    — read only through its own outer ``CoordinatorData`` field — is never
    considered here at all, rather than showing up as a false miss.
    """
    tree = ast.parse(DATA_MODULE.read_text())
    fields: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "CoordinatorData":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(
                    stmt.target, ast.Name
                ):
                    fields[stmt.target.id] = stmt.lineno
            break
    return fields


def _is_data_expr(node: ast.AST) -> bool:
    """True for ``<anything>.data`` — the coordinator snapshot itself."""
    return isinstance(node, ast.Attribute) and node.attr == "data"


def _data_aliases(tree: ast.Module) -> set[str]:
    """Names bound to a ``...data`` expression, e.g. ``d = self.coordinator.data``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_data_expr(node.value):
            for target in node.targets:
                for leaf in _flatten(target):
                    if isinstance(leaf, ast.Name):
                        names.add(leaf.id)
    return names


def _is_read_source(node: ast.AST, aliases: frozenset[str]) -> bool:
    """True when ``node`` is the coordinator snapshot: an alias, ``self.x.data``,
    or a bare ``data``/``d`` name (every production writer/reader in this
    integration receives the snapshot as a parameter spelled ``data``, e.g.
    ``compute_derived_values(data)``; deliberately generous per the module
    docstring's bias — an over-broad reader only hides a future dead field,
    while an over-strict one fails the build on a live one)."""
    if _is_data_expr(node):
        return True
    return isinstance(node, ast.Name) and (
        node.id in aliases or node.id in _BARE_DATA_NAMES
    )


def _production_reads() -> dict[str, set[str]]:
    """field name -> set of ``relative/path:line`` reads, production code only.

    Scoped to ``custom_components/`` (excluding ``coordinator/data.py``, the
    declaration site) — deliberately never ``tests/`` or ``scripts/``. See
    the module docstring for why a test-inclusive census would not have
    caught the #981 batch.
    """
    reads: dict[str, set[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if path == DATA_MODULE:
            continue
        tree = ast.parse(path.read_text())
        aliases = frozenset(_data_aliases(tree))
        rel = path.relative_to(PACKAGE.parent.parent)

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                # Covers plain reads (`data.f`), subscript reads AND subscript
                # stores (`data.f[k]` / `data.f[k] = v` both load `data.f`
                # first), and the receiver of a mutating method call
                # (`data.f.append(...)` loads `f` to call `.append` on it).
                if _is_read_source(node.value, aliases):
                    reads.setdefault(node.attr, set()).add(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.AugAssign) and isinstance(
                node.target, ast.Attribute
            ):
                # `data.f += v` reads f's prior value before writing it; the
                # AugAssign target's ctx is Store, so the Load branch above
                # never sees it.
                if _is_read_source(node.target.value, aliases):
                    reads.setdefault(node.target.attr, set()).add(
                        f"{rel}:{node.lineno} (augassign)"
                    )
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and _is_read_source(node.args[0], aliases)
            ):
                reads.setdefault(node.args[1].value, set()).add(
                    f"{rel}:{node.lineno} (getattr)"
                )
    return reads


def test_every_coordinator_data_field_has_a_production_reader() -> None:
    fields = _coordinator_data_fields()
    reads = _production_reads()
    dead = {
        field: line for field, line in fields.items() if not reads.get(field)
    }
    assert not dead, (
        "CoordinatorData fields with no production reader outside "
        "coordinator/data.py — each one is either dead or (like #981's "
        "preserve_soc) write-only with every read collapsing to a constant. "
        "Delete the field (and its writer) together, per issue #981:\n"
        + "\n".join(
            f"  {field}  (declared coordinator/data.py:{line})"
            for field, line in sorted(dead.items())
        )
    )

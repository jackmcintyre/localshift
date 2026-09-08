"""Static gate: every coordinator field a sensor reads must have a writer.

Issues #917 and #973 are the same failure at two depths: a sensor published an
attribute sourced from a ``CoordinatorData`` field that no production code ever
writes, so the attribute sat at its dataclass default forever — ``0.0``, ``0``,
``""``, ``{}`` — and looked like a real reading. Nothing failed, nothing
logged, and each one had to be found by a human comparing the attribute to the
code. Nine such fields shipped before this test existed (five forecast cost
accumulators, two consumption provenance fields, ``adaptive_params_values``, and
three ``optimizer_summary`` attributes the engine stopped emitting in #816).

This file walks the AST instead of running anything: it collects every
``<coordinator>.data.<field>`` read (including through a local alias such as
``d = self.coordinator.data`` and via ``getattr(..., "field", ...)``) in
``sensors/``, then collects every write to such a field anywhere in the
integration *except* ``coordinator/data.py`` (the declaration is not a writer)
and fails when a read has none.

DO NOT make this pass by deleting a read from the ignore lists. If a field is
genuinely dead, delete the sensor attribute and the field together in the same
commit — that is the fix this test exists to force. If a field is genuinely
written only in a way this walker cannot see (it is deliberately loose: tuple
unpacking, subscript stores, mutating method calls, ``setattr`` and constructor
keywords are all counted as writers), teach the walker rather than widening the
hole, because a false positive blocks everyone while a false negative only
waits for the next issue.

Detection is deliberately one-sided for the same reason: a missed writer (dead
field slips through) costs a later issue; an invented writer would hide a live
one.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "localshift"
SENSORS_DIR = PACKAGE / "sensors"
DATA_MODULE = PACKAGE / "coordinator" / "data.py"

# Method calls that mutate the receiver, so ``data.x.append(...)`` writes x.
_MUTATING_METHODS = frozenset({
    "append",
    "add",
    "clear",
    "discard",
    "extend",
    "insert",
    "pop",
    "popitem",
    "remove",
    "setdefault",
    "sort",
    "update",
})


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


def _declared_fields() -> set[str]:
    """AnnAssign field names of every dataclass in coordinator/data.py."""
    tree = ast.parse(DATA_MODULE.read_text())
    fields: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(
                    stmt.target, ast.Name
                ):
                    fields.add(stmt.target.id)
    return fields


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


def _is_data_expr(node: ast.AST) -> bool:
    """True for ``<anything>.data`` — the coordinator snapshot itself."""
    return isinstance(node, ast.Attribute) and node.attr == "data"


def _is_field_source(node: ast.AST, aliases: frozenset[str]) -> bool:
    """True when ``node`` is the coordinator snapshot (directly or via an alias)."""
    if _is_data_expr(node):
        return True
    return isinstance(node, ast.Name) and node.id in aliases


def _is_writer_source(node: ast.AST, aliases: frozenset[str]) -> bool:
    """Loose writer-side counterpart of :func:`_is_field_source`.

    Also accepts a bare ``data`` name. Every production writer in this
    integration receives the snapshot as a parameter spelled ``data``
    (``compute_derived_values(data)``, ``accumulate_costs(data)``, ...), so
    treating that name as the snapshot is what makes the writer census real.
    The asymmetry with the strict reader is deliberate: an over-broad writer
    only lets a dead field slip through to a later issue, while an over-broad
    read would fail the build on a live attribute.
    """
    if _is_field_source(node, aliases):
        return True
    return isinstance(node, ast.Name) and node.id == "data"


def _field_aliases(tree: ast.Module, aliases: frozenset[str]) -> dict[str, set[str]]:
    """Writer-side only: names bound to a coordinator field (or a piece of one).

    ``bucket = data.boundary_lag_history.setdefault(src, [])`` followed by
    ``bucket.append(...)`` writes ``boundary_lag_history`` through an alias of
    its sub-dict, and that is the only way state/machine.py populates it —
    ``data`` there is a bare parameter name, not ``self.coordinator.data``, so
    the root check goes through :func:`_is_writer_source` (which accepts a
    bare ``data`` Name) rather than :func:`_is_data_expr` (which does not). A
    writer-side-only alias keeps the read census strict while still counting
    such mutations; a missed form here can only hide a writer, never invent one.
    """
    field_aliases: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        root: ast.AST | None = None
        if isinstance(node.value, ast.Attribute):
            root = node.value.value
        elif isinstance(node.value, ast.Call) and isinstance(
            node.value.func, ast.Attribute
        ):
            root = node.value.func.value
        elif isinstance(node.value, ast.Subscript):
            root = node.value.value
        if isinstance(root, ast.Attribute) and _is_writer_source(root.value, aliases):
            for target in node.targets:
                for leaf in _flatten(target):
                    if isinstance(leaf, ast.Name):
                        field_aliases.setdefault(leaf.id, set()).add(root.attr)
    return field_aliases


def _reads() -> dict[str, set[str]]:
    """field name -> set of ``file:line`` reads, for every sensor module."""
    reads: dict[str, set[str]] = {}
    for path in sorted(SENSORS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        aliases = frozenset(_data_aliases(tree))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if _is_field_source(node.value, aliases):
                    reads.setdefault(node.attr, set()).add(f"{path.name}:{node.lineno}")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and _is_field_source(node.args[0], aliases)
            ):
                reads.setdefault(node.args[1].value, set()).add(
                    f"{path.name}:{node.lineno}"
                )
    return reads


def _assign_writes(
    node: ast.Assign | ast.AnnAssign | ast.AugAssign, aliases: frozenset[str]
) -> list[tuple[str, int, str]]:
    """Field writes from ``x.f = v``, ``x.f += v``, and tuple-unpacking assigns.

    Covers ``x.f = v``, ``x.f += v`` and tuple unpacking such as
    ``(data.a, data.b) = ...`` (computation_engine.py does this), plus
    ``data.f[k] = v`` — a Subscript store still mutates the field, so the
    subscript is unwrapped rather than skipped.
    """
    hits: list[tuple[str, int, str]] = []
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        for leaf in _flatten(target):
            while isinstance(leaf, ast.Subscript):
                leaf = leaf.value
            if isinstance(leaf, ast.Attribute) and _is_writer_source(
                leaf.value, aliases
            ):
                hits.append((leaf.attr, leaf.lineno, "assign"))
    return hits


def _call_writes(
    node: ast.Call, aliases: frozenset[str], field_aliases: dict[str, set[str]]
) -> list[tuple[str, int, str]]:
    """Field writes from mutating method calls and ``setattr(...)``.

    ``data.f.append(...)`` / ``data.f.update(...)`` mutate f directly;
    ``bucket.append(...)`` mutates f through a ``field_aliases`` alias (e.g.
    ``bucket = data.f.setdefault(k, [])`` — state/machine.py writes
    boundary_lag_history only this way, so a bare target scan would
    false-flag it).
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return []
    if func.attr in _MUTATING_METHODS:
        if _is_writer_source(func.value, aliases):
            return [(func.value.attr, func.lineno, func.attr)]
        if isinstance(func.value, ast.Name) and func.value.id in field_aliases:
            return [
                (aliased_field, func.lineno, f"{func.attr} via {func.value.id}")
                for aliased_field in field_aliases[func.value.id]
            ]
        return []
    if (
        func.attr == "setattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
        and _is_writer_source(node.args[0], aliases)
    ):
        return [(node.args[1].value, func.lineno, "setattr")]
    return []


def _writes() -> dict[str, set[str]]:
    """field name -> set of ``relative/path:line`` writes outside coordinator/data.py."""
    writes: dict[str, set[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if path == DATA_MODULE:
            continue
        tree = ast.parse(path.read_text())
        aliases = frozenset(_data_aliases(tree))
        field_aliases = _field_aliases(tree, aliases)
        rel = path.relative_to(PACKAGE.parent.parent)

        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                hits = _assign_writes(node, aliases)
            elif isinstance(node, ast.Call):
                hits = _call_writes(node, aliases, field_aliases)
            else:
                continue
            for name, line, how in hits:
                writes.setdefault(name, set()).add(f"{rel}:{line} ({how})")
    return writes


def test_every_sensor_field_read_has_a_production_writer() -> None:
    reads, writes = _reads(), _writes()
    dead = {
        field: sorted(sites) for field, sites in reads.items() if not writes.get(field)
    }
    assert not dead, (
        "Sensor attributes reading coordinator fields that nothing writes — "
        "each one is permanently at its dataclass default. Delete the sensor "
        "attribute AND the field together (see issues #917/#973), or teach "
        "this walker about a writer form it cannot see:\n"
        + "\n".join(
            f"  {field} <- {', '.join(sites)}" for field, sites in sorted(dead.items())
        )
    )


def test_every_sensor_field_read_is_a_declared_field() -> None:
    declared = _declared_fields()
    typos = {
        field: sorted(sites)
        for field, sites in _reads().items()
        if field not in declared
    }
    assert not typos, (
        "Sensor reads reference names that are not declared dataclass fields "
        "in coordinator/data.py (typo, or a read of a non-field attribute):\n"
        + "\n".join(
            f"  {field} <- {', '.join(sites)}" for field, sites in sorted(typos.items())
        )
    )

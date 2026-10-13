#!/usr/bin/env python3
"""Shrink-only dead-code ratchet for the LocalShift integration (issue #986).

What it does
------------
Runs two detectors over ``custom_components/localshift`` and sorts every
finding into exactly one of two checked-in files:

* ``vulture_whitelist.py`` -- symbols that are LIVE but reached dynamically
  (a string dispatch table plus ``getattr``, Home Assistant calling a method
  by name, a script that reads a field on purpose). Every entry names the
  dynamic access site and the gate re-checks that the site still exists.
* ``dead_code_baseline.txt`` -- everything else: believed dead, or not yet
  decided. It is debt, listed so it can only shrink.

The gate FAILS in both directions:

* a finding that is in neither file -> new dead code was added;
* a baseline entry that is no longer found -> dead code was removed (or wired
  up) and the baseline was not shrunk in the same change.

It also fails when a whitelist entry stops matching anything, or when the
dynamic access site it cites has gone. A whitelisted method whose dispatch
table entry was deleted is dead, and the whitelist must not keep hiding it.

The detectors
-------------
1. vulture at the confidence set in ``pyproject.toml`` (60). Findings are keyed
   by ``kind`` + file + qualified name, never by line number, so unrelated
   edits to a file do not churn the baseline.
2. A reader census for ``CoordinatorData`` fields: a field with no production
   reader is reported as ``unread-field``. Tests and scripts are deliberately
   not counted as readers -- a test that asserts a field's value mirrors the
   field, it does not consume it (issue #981). This detector owns
   ``CoordinatorData``: vulture's own "unused variable" verdict on those
   fields is dropped in favour of it, because it also understands
   ``getattr(data, "name")`` and string tables fed to ``getattr``.

Why the whitelist is matched by qualified name here
---------------------------------------------------
vulture's own whitelist mechanism matches on the bare name across the whole
package: listing ``estimate`` would hide every unused ``estimate`` anywhere.
This gate therefore runs vulture on the package alone and applies the
whitelist itself. A normal entry matches one kind of symbol, in one file, by
qualified name (``method state/machine.py StateMachine._build_hold_config``).
A ``# framework:`` entry can only be ``_.member``, which covers members of
classes that derive from a ``homeassistant`` import, or a bare module-level
hook, which covers functions in the modules Home Assistant loads by name
(``__init__.py`` and the ``PLATFORMS`` modules). Nothing else is accepted
under ``# framework:``.

The two naming conventions Home Assistant defines by pattern are applied here
too, scoped the same way: ``_attr_*`` attributes on classes that derive from
Home Assistant, and ``async_step_*`` methods on config/options flow classes.

The whitelist file is still valid vulture input, so a bare ``uv run vulture``
picks it up from ``[tool.vulture] paths``. That command is red by design (it
prints the baseline, more or less); this script is the pass/fail.

Limits
------
* The scan sees unused NAMES. It cannot vouch for a parameter, or a class
  built only through an injected reference.
* The census counts any load of ``data.<field>`` as a read, so a container
  field that is only ever appended to (``data.log.append(x)``) looks read.
* ``dataclasses.asdict`` / ``replace`` over the snapshot are not modelled.
  Nothing uses them today; if something starts to, fields it serialises would
  be reported unread and the gate would fail loudly rather than pass quietly.
* When a field is write-only, the gate reports the field and its write sites.
  It cannot see that the computation feeding the write is now pointless.

Usage
-----
    uv run python scripts/dead_code_gate.py                 # check (CI)
    uv run python scripts/dead_code_gate.py --update        # shrink baseline
    uv run python scripts/dead_code_gate.py --update --allow-additions
    uv run python scripts/dead_code_gate.py --summary       # counts by kind

``--update`` only ever removes entries. Adding one is a decision to carry new
debt and needs ``--allow-additions`` spelled out.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PACKAGE = "custom_components/localshift"
DEFAULT_WHITELIST = "vulture_whitelist.py"
DEFAULT_BASELINE = "dead_code_baseline.txt"
DEFAULT_DATA_MODULE = "coordinator/data.py"
DEFAULT_DATA_CLASS = "CoordinatorData"
DEFAULT_MIN_CONFIDENCE = 60

UNREAD_FIELD = "unread-field"
# Whitelist owner meaning "any class that derives from the framework".
ANY_OWNER = "_"

BASELINE_HEADER = """\
# Dead-code baseline (issue #986). Shrink-only: see docs/DEVELOPER_GUIDE.md,
# "Dead-code ratchet".
#
# Every line is a symbol the gate reports as unused and that nobody has yet
# deleted, wired up, or shown to be live. It is debt, not an allowlist.
#
#   Deleted or wired up one of these?  uv run python scripts/dead_code_gate.py --update
#   Gate says a NEW symbol is unused?  Delete it, wire it, or -- if it is live
#     through dynamic access -- add it to vulture_whitelist.py with its site.
#     Do not add it here.
#
# Format: <kind> <file, relative to the package> <qualified name>  [# note]
# '@2', '@3' = the 2nd, 3rd symbol with that same key (e.g. another write
# site of the same attribute). Sorted; no line numbers on purpose. Notes
# survive --update.
"""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Finding:
    """One unused symbol, keyed so unrelated edits do not change it.

    ``ordinal`` separates symbols that would otherwise share a key: two dead
    functions of the same name in one scope, or several write sites of one
    attribute. Without it a second one added later would be invisible.
    """

    kind: str
    file: str
    qualname: str
    ordinal: int = 1

    @property
    def name(self) -> str:
        """Last component of the qualified name."""
        return self.qualname.rsplit(".", 1)[-1]

    @property
    def key(self) -> str:
        """Qualified name plus ``@n`` for the 2nd, 3rd... of the same key."""
        return self.qualname + (f"@{self.ordinal}" if self.ordinal > 1 else "")

    def render(self) -> str:
        """Baseline line for this finding. Fields are whitespace-separated."""
        return f"{self.kind:<12} {self.file}  {self.key}"


@dataclass
class WhitelistEntry:
    """One whitelisted symbol and the evidence that it is live."""

    owner: str | None  # None = module level, ANY_OWNER = any framework class
    name: str
    line: int
    why: str = ""
    file: str = ""  # package-relative file of the definition (normal entries)
    kind: str = ""  # detector kind it matches (normal entries)
    # (site file relative to the repo root, text that must be present in it)
    evidence: list[tuple[str, str]] = field(default_factory=list)
    sites: list[str] = field(default_factory=list)
    framework: str = ""

    @property
    def label(self) -> str:
        """The entry as written in the whitelist file."""
        return f"{self.owner}.{self.name}" if self.owner else self.name

    @property
    def qualname(self) -> str:
        """Qualified name this entry matches (scoped and bare entries)."""
        return self.label


@dataclass
class Layout:
    """Where everything lives. Overridable so tests can use a tiny fixture."""

    root: Path
    package: Path
    whitelist: Path
    baseline: Path
    data_module: Path
    data_class: str


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def load_vulture_config(root: Path) -> dict:
    """``[tool.vulture]`` from the root pyproject.toml, or an empty dict."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return {}
    with pyproject.open("rb") as handle:
        return tomllib.load(handle).get("tool", {}).get("vulture", {})


# ---------------------------------------------------------------------------
# Scope index: line number -> qualified name
# ---------------------------------------------------------------------------

_SCOPE_NODES = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass
class _Scope:
    start: int  # first decorator line, or the def line
    def_line: int
    end: int
    chain: tuple[str, ...]
    is_class: bool


class _ModuleIndex:
    """Scopes and attribute-store targets of one module."""

    def __init__(self, source: str) -> None:
        self.tree = ast.parse(source)
        self.scopes: list[_Scope] = []
        self.stores: dict[tuple[int, str], ast.Attribute] = {}
        self._collect(self.tree, ())
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Attribute) and isinstance(
                node.ctx, (ast.Store, ast.Del)
            ):
                self.stores.setdefault((node.lineno, node.attr), node)

    def _collect(self, node: ast.AST, chain: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _SCOPE_NODES):
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                child_chain = (*chain, child.name)
                self.scopes.append(
                    _Scope(
                        start,
                        child.lineno,
                        child.end_lineno or child.lineno,
                        child_chain,
                        isinstance(child, ast.ClassDef),
                    )
                )
                self._collect(child, child_chain)
            else:
                self._collect(child, chain)

    def definition(self, name: str, line: int) -> tuple[str, ...] | None:
        """Chain of the def/class called ``name`` that starts on ``line``."""
        for scope in self.scopes:
            if scope.chain[-1] == name and scope.start <= line <= scope.def_line:
                return scope.chain
        return None

    def enclosing(self, line: int) -> _Scope | None:
        """Innermost scope containing ``line``."""
        best: _Scope | None = None
        for scope in self.scopes:
            if scope.start <= line <= scope.end and (
                best is None or len(scope.chain) > len(best.chain)
            ):
                best = scope
        return best

    def enclosing_class(self, line: int) -> tuple[str, ...]:
        """Chain of the innermost class containing ``line`` (may be empty)."""
        best: tuple[str, ...] = ()
        for scope in self.scopes:
            if (
                scope.is_class
                and scope.start <= line <= scope.end
                and len(scope.chain) > len(best)
            ):
                best = scope.chain
        return best


def _qualname(index: _ModuleIndex, typ: str, name: str, line: int) -> str:
    """Qualified name for a vulture item."""
    if typ in {"function", "method", "property", "class"}:
        chain = index.definition(name, line)
        if chain:
            return ".".join(chain)
    if typ == "attribute":
        # ``self.x = ...`` belongs to the class it is written in; anything
        # else (``data.x = ...``) is keyed by the expression it is set on.
        node = index.stores.get((line, name))
        if node is not None:
            if isinstance(node.value, ast.Name) and node.value.id in {"self", "cls"}:
                owner = index.enclosing_class(line)
                if owner:
                    return ".".join((*owner, name))
            return f"{ast.unparse(node.value)}.{name}"
        return name
    scope = index.enclosing(line)
    chain = scope.chain if scope else ()
    return ".".join((*chain, name))


# ---------------------------------------------------------------------------
# Detector 1: vulture
# ---------------------------------------------------------------------------


def vulture_findings(layout: Layout, config: dict) -> set[Finding]:
    """Run vulture on the package alone and key its findings stably."""
    from vulture import Vulture  # noqa: PLC0415 - optional at import time

    scanner = Vulture(
        ignore_names=config.get("ignore_names") or None,
        ignore_decorators=config.get("ignore_decorators") or None,
    )
    scanner.scavenge([str(layout.package)], exclude=config.get("exclude") or None)
    min_confidence = int(config.get("min_confidence", DEFAULT_MIN_CONFIDENCE))

    indexes: dict[Path, _ModuleIndex] = {}
    seen: Counter[tuple[str, str, str]] = Counter()
    findings: set[Finding] = set()
    items = scanner.get_unused_code(min_confidence=min_confidence)
    for item in sorted(items, key=lambda i: (str(i.filename), i.first_lineno)):
        path = Path(item.filename).resolve()
        if path not in indexes:
            indexes[path] = _ModuleIndex(path.read_text(encoding="utf-8"))
        key = (
            item.typ,
            path.relative_to(layout.package.resolve()).as_posix(),
            _qualname(indexes[path], item.typ, str(item.name), item.first_lineno),
        )
        seen[key] += 1
        findings.add(Finding(*key, ordinal=seen[key]))
    return findings


# ---------------------------------------------------------------------------
# Detector 2: CoordinatorData fields nobody reads
# ---------------------------------------------------------------------------


def declared_fields(source: str, class_name: str) -> list[str]:
    """Annotated field names declared directly on ``class_name``."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return [
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            ]
    return []


def _is_data_attr(node: ast.AST) -> bool:
    """True for ``<anything>.data`` -- the coordinator snapshot itself."""
    return isinstance(node, ast.Attribute) and node.attr == "data"


def _binding_names(target: ast.AST) -> set[str]:
    """Plain names bound by an assignment / loop / comprehension target."""
    return {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}


class _ReaderVisitor(ast.NodeVisitor):
    """Collect ``<snapshot>.<field>`` reads in one module.

    What counts as the snapshot is decided per scope, not by spelling alone
    (issue #1063: a loop variable ``d`` over planner decisions is not the
    coordinator snapshot):

    * any ``<expr>.data`` attribute expression;
    * a function parameter annotated ``CoordinatorData``, or named ``data``
      and not annotated as something else;
    * a local name assigned from one of the above (``d = self.coordinator.data``).

    A name stops being the snapshot inside a comprehension or loop that
    rebinds it.

    ``getattr(<snapshot>, <non-literal>)`` reads fields by name. The names it
    can receive are taken to be the string literals in the module's own
    module- and class-level tables (``_ENERGY_METER_KEYS = (...)``) plus the
    string literals passed to the function that contains the ``getattr``
    (``_summary_or_attr(summary, d, "dw_entry_actual_at")``).
    """

    def __init__(self, data_class: str) -> None:
        self.data_class = data_class
        self.aliases: set[str] = set()
        self.reads: set[str] = set()
        self.dynamic_helpers: set[str] = set()
        self.dynamic_getattr = False
        self.table_strings: set[str] = set()
        self.call_strings: dict[str, set[str]] = {}
        self._functions: list[str] = []

    # -- what is the snapshot ------------------------------------------------

    def _is_source(self, node: ast.AST) -> bool:
        if _is_data_attr(node):
            return True
        return isinstance(node, ast.Name) and node.id in self.aliases

    def _param_is_snapshot(self, arg: ast.arg) -> bool:
        if arg.annotation is None:
            return arg.arg == "data"
        annotation = ast.unparse(arg.annotation)
        if self.data_class in annotation:
            return True
        return arg.arg == "data" and annotation in {"Any", "object"}

    # -- scopes --------------------------------------------------------------

    @staticmethod
    def _params(args: ast.arguments) -> list[ast.arg]:
        params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        params += [extra for extra in (args.vararg, args.kwarg) if extra]
        return params

    def _local_aliases(self, body: list[ast.AST]) -> set[str]:
        """Names assigned from the snapshot anywhere in a function body."""
        found: set[str] = set()
        for stmt in body:
            for sub in ast.walk(stmt):
                value = getattr(sub, "value", None)
                if value is None or not self._is_source(value):
                    continue
                if isinstance(sub, ast.Assign):
                    targets = sub.targets
                elif isinstance(sub, ast.AnnAssign):
                    targets = [sub.target]
                else:
                    continue
                found |= {t.id for t in targets if isinstance(t, ast.Name)}
        return found

    def _visit_function(self, node: ast.AST) -> None:
        saved = set(self.aliases)
        for param in self._params(node.args):  # type: ignore[attr-defined]
            if self._param_is_snapshot(param):
                self.aliases.add(param.arg)
            else:
                self.aliases.discard(param.arg)
        body = node.body  # type: ignore[attr-defined]
        # Aliases assigned anywhere in the body apply to the whole body.
        self.aliases |= self._local_aliases(body if isinstance(body, list) else [body])
        self._functions.append(getattr(node, "name", "<lambda>"))
        self.generic_visit(node)
        self._functions.pop()
        self.aliases = saved

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function
    visit_Lambda = _visit_function

    def _visit_comprehension(self, node: ast.AST) -> None:
        saved = set(self.aliases)
        for generator in node.generators:  # type: ignore[attr-defined]
            if not self._is_source(generator.iter):
                self.aliases -= _binding_names(generator.target)
        self.generic_visit(node)
        self.aliases = saved

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension

    def visit_For(self, node: ast.For) -> None:
        saved = set(self.aliases)
        self.aliases -= _binding_names(node.target)
        self.generic_visit(node)
        self.aliases = saved

    visit_AsyncFor = visit_For

    # -- reads ---------------------------------------------------------------

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # A Load covers plain reads, ``data.f[k]`` (and ``data.f[k] = v``,
        # which loads ``data.f`` first) and ``data.f.append(...)``.
        if isinstance(node.ctx, ast.Load) and self._is_source(node.value):
            self.reads.add(node.attr)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        # ``data.f += v`` reads f before writing it, but the target is a Store.
        target = node.target
        if isinstance(target, ast.Attribute) and self._is_source(target.value):
            self.reads.add(target.attr)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Name)
            and func.id == "getattr"
            and len(node.args) >= 2
            and self._is_source(node.args[0])
        ):
            key = node.args[1]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                self.reads.add(key.value)
            else:
                self.dynamic_getattr = True
                self.dynamic_helpers |= set(self._functions)
        callee = getattr(func, "id", None) or getattr(func, "attr", None)
        if callee:
            literals = [*node.args, *(kw.value for kw in node.keywords)]
            self.call_strings.setdefault(callee, set()).update(_strings(literals))
        self.generic_visit(node)

    def _visit_table(self, node: ast.Assign | ast.AnnAssign) -> None:
        # Only assignments outside any function: module and class constants.
        if not self._functions and node.value is not None:
            self.table_strings |= _strings([node.value])
        self.generic_visit(node)

    visit_Assign = _visit_table
    visit_AnnAssign = _visit_table


def _strings(nodes: list[ast.AST]) -> set[str]:
    """Every string literal inside ``nodes``."""
    return {
        sub.value
        for node in nodes
        for sub in ast.walk(node)
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
    }


def field_reads(source: str, data_class: str = DEFAULT_DATA_CLASS) -> set[str]:
    """Attribute names read off the coordinator snapshot in one module.

    Includes the names a ``getattr(<snapshot>, <non-literal>)`` in the module
    can receive (see ``_ReaderVisitor``). That is generous on purpose -- a
    missed read would fail the build on a live field -- and scoped to the one
    file.
    """
    visitor = _ReaderVisitor(data_class)
    visitor.visit(ast.parse(source))
    reads = set(visitor.reads)
    if visitor.dynamic_getattr:
        reads |= visitor.table_strings
        for helper in visitor.dynamic_helpers:
            reads |= visitor.call_strings.get(helper, set())
    return reads


def census_fields(layout: Layout) -> list[str]:
    """Fields the census examines: annotated names on the data class."""
    if not layout.data_module.is_file():
        return []
    return declared_fields(
        layout.data_module.read_text(encoding="utf-8"), layout.data_class
    )


def unread_field_findings(layout: Layout) -> set[Finding]:
    """``CoordinatorData`` fields with no reader in production code."""
    fields = census_fields(layout)
    if not fields:
        return set()
    reads: set[str] = set()
    for path in sorted(layout.package.rglob("*.py")):
        if path.resolve() == layout.data_module.resolve():
            continue  # the declaration is not a reader of its own field
        reads |= field_reads(path.read_text(encoding="utf-8"), layout.data_class)
    rel = layout.data_module.resolve().relative_to(layout.package.resolve())
    return {
        Finding(UNREAD_FIELD, rel.as_posix(), f"{layout.data_class}.{name}")
        for name in fields
        if name not in reads
    }


def _is_framework_convention(finding: Finding, roots: dict[str, set[str]]) -> bool:
    """``_attr_*`` on an HA-derived class, ``async_step_*`` on a flow class.

    Home Assistant reads any ``_attr_<property>`` off an entity and dispatches
    a flow step to ``async_step_<step_id>``. Those are conventions by pattern,
    so they are globs -- but only where the convention applies.
    """
    parts = finding.qualname.split(".")
    if len(parts) < 2:
        return False
    owner_roots = roots.get(parts[-2], set())
    if finding.kind == "attribute" and parts[-1].startswith("_attr_"):
        return bool(owner_roots)
    if finding.kind == "method" and parts[-1].startswith("async_step_"):
        return any(root.endswith("Flow") for root in owner_roots)
    return False


def collect_findings(layout: Layout, config: dict) -> tuple[set[Finding], list[str]]:
    """Findings from both detectors before the whitelist, and detector problems."""
    problems: list[str] = []
    examined = set(census_fields(layout))
    if not examined:
        problems.append(
            f"the reader census found no fields: class {layout.data_class} in "
            f"{layout.data_module.relative_to(layout.root)} is missing or has no "
            "annotated fields. If it was renamed or moved, update "
            "DEFAULT_DATA_MODULE / DEFAULT_DATA_CLASS in this script; a census "
            "that examines nothing must not pass."
        )
    data_rel = ""
    if layout.data_module.is_file():
        data_rel = (
            layout.data_module
            .resolve()
            .relative_to(layout.package.resolve())
            .as_posix()
        )
    prefix = f"{layout.data_class}."
    roots = framework_roots(layout.package)
    findings = {
        f
        for f in vulture_findings(layout, config)
        # The census owns the fields it examined, and only those: an
        # un-annotated class attribute stays vulture's to report.
        if not (
            f.kind == "variable"
            and f.file == data_rel
            and f.qualname.startswith(prefix)
            and f.qualname[len(prefix) :] in examined
        )
        and not _is_framework_convention(f, roots)
    }
    return findings | unread_field_findings(layout), problems


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

_ENTRY_RE = re.compile(r"^(?:(?P<owner>[A-Za-z_][\w.]*)\.)?(?P<name>[A-Za-z_]\w*)$")
_META_RE = re.compile(
    r"^#\s*(?P<key>site|evidence|why|framework|file|kind):\s*(?P<value>.*)$"
)


def parse_whitelist(text: str) -> list[WhitelistEntry]:
    """Parse the whitelist file.

    A block is a run of ``# key: value`` comment lines followed by one or more
    entry lines; the block's keys apply to each of its entries. A blank line
    or a new comment after entries starts a fresh block. Each ``# evidence:``
    belongs to the ``# site:`` above it, so a block can cite several sites.
    """
    entries: list[WhitelistEntry] = []
    block = _Block()
    in_docstring = False

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line.count('"""') % 2 == 1:
            in_docstring = not in_docstring
            continue
        if in_docstring or line.startswith('"""'):
            continue
        if not line:
            block = _Block()
        elif line.startswith("#"):
            if block.seen_entry:
                block = _Block()
            block.comment(line)
        else:
            entries.append(block.entry(raw, lineno))
    return entries


class _Block:
    """One whitelist block: its ``# key:`` lines, then its entries."""

    def __init__(self) -> None:
        self.meta: dict[str, list] = {}
        self.last_key: str | None = None
        self.seen_entry = False

    def comment(self, line: str) -> None:
        match = _META_RE.match(line)
        if match:
            self.last_key = match["key"]
            value = match["value"].strip()
            if self.last_key == "evidence":
                site = (self.meta.get("site") or [""])[-1]
                value = (site, value)
            self.meta.setdefault(self.last_key, []).append(value)
        elif self.last_key == "why" and line.lstrip("# ").strip():
            self.meta["why"][-1] += " " + line.lstrip("# ").strip()

    def entry(self, raw: str, lineno: int) -> WhitelistEntry:
        match = _ENTRY_RE.match(raw.split("#", 1)[0].strip())
        if not match:
            raise ValueError(f"whitelist line {lineno}: cannot parse {raw!r}")
        self.seen_entry = True
        return WhitelistEntry(
            owner=match["owner"],
            name=match["name"],
            line=lineno,
            why=" ".join(self.meta.get("why", [])),
            sites=list(self.meta.get("site", [])),
            evidence=list(self.meta.get("evidence", [])),
            framework=(self.meta.get("framework") or [""])[-1],
            file=(self.meta.get("file") or [""])[-1],
            kind=(self.meta.get("kind") or [""])[-1],
        )


def _framework_imports(tree: ast.Module, framework: str) -> set[str]:
    """Local names a module binds to something imported from ``framework``."""
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == framework:
                imported |= {alias.asname or alias.name for alias in node.names}
        elif isinstance(node, ast.Import):
            imported |= {
                (alias.asname or alias.name).split(".")[0]
                for alias in node.names
                if alias.name.split(".")[0] == framework
            }
    return imported


def _base_names(node: ast.ClassDef) -> set[str]:
    """Leftmost name of each base: ``Foo``, ``pkg.Foo`` -> pkg, ``Foo[T]``."""
    names: set[str] = set()
    for base in node.bases:
        root: ast.AST | None = base
        while isinstance(root, (ast.Attribute, ast.Subscript)):
            root = root.value
        if isinstance(root, ast.Name):
            names.add(root.id)
    return names


def framework_roots(package: Path, framework: str = "homeassistant") -> dict:
    """Package class name -> the ``framework`` imports it derives from.

    ``{"LocalShiftSwitch": {"CoordinatorEntity", "SwitchEntity"}, ...}``. A
    class that does not derive from the framework is absent. Purely static.
    """
    bases: dict[str, set[str]] = {}
    roots: dict[str, set[str]] = {}
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = _framework_imports(tree, framework)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                names = _base_names(node)
                bases.setdefault(node.name, set()).update(names)
                if names & imported:
                    roots.setdefault(node.name, set()).update(names & imported)
    changed = True
    while changed:
        changed = False
        for name, parents in bases.items():
            inherited = set().union(*(roots.get(p, set()) for p in parents))
            if not inherited <= roots.get(name, set()):
                roots.setdefault(name, set()).update(inherited)
                changed = True
    return roots


def entry_modules(package: Path) -> set[str]:
    """Modules Home Assistant loads by name: ``__init__`` and the platforms.

    The platforms are read from the integration's own ``PLATFORMS`` list, the
    one it hands to ``async_forward_entry_setups``.
    """
    modules = {"__init__.py"}
    for path in sorted(package.rglob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            targets = getattr(node, "targets", None) or [getattr(node, "target", None)]
            if any(isinstance(t, ast.Name) and t.id == "PLATFORMS" for t in targets):
                modules |= {f"{name}.py" for name in _strings([node.value])}
    return modules


@dataclass
class _Context:
    """What whitelist matching needs to know about the package."""

    roots: dict[str, set[str]]
    entry_modules: set[str]


def _entry_matches(entry: WhitelistEntry, finding: Finding, ctx: _Context) -> bool:
    """True when ``entry`` covers ``finding``."""
    if entry.framework:
        if entry.owner == ANY_OWNER:
            # Member of a class that derives from the framework.
            parts = finding.qualname.split(".")
            return (
                len(parts) >= 2
                and parts[-1] == entry.name
                and bool(ctx.roots.get(parts[-2]))
                and finding.kind in {"method", "property"}
            )
        if entry.owner is None:
            # Module-level hook, only in a module the framework loads by name.
            return (
                finding.kind == "function"
                and finding.qualname == entry.name
                and finding.file in ctx.entry_modules
            )
        return False  # rejected by _claim_problems
    return (
        finding.kind == entry.kind
        and finding.file == entry.file
        and finding.qualname == entry.qualname
    )


def _evidence_problems(layout: Layout, entry: WhitelistEntry) -> list[str]:
    """Check that every cited site still contains its evidence."""
    cited = {site for site, _ in entry.evidence}
    problems = [
        f"site {site} has no '# evidence:'."
        for site in entry.sites
        if site not in cited
    ]
    for site, needle in entry.evidence:
        path = layout.root / site
        needle = needle.replace("{name}", entry.name)
        if not path.is_file():
            problems.append(f"site {site} does not exist.")
        elif needle not in path.read_text(encoding="utf-8"):
            problems.append(
                f"{site} no longer contains {needle!r}. If the dynamic access "
                "is gone the symbol is dead: delete it and this entry."
            )
    return problems


def _claim_problems(layout: Layout, entry: WhitelistEntry) -> list[str]:
    """Problems with what an entry claims, before matching it to findings."""
    problems: list[str] = []
    if not entry.why:
        problems.append("no '# why:' line. Say why it is live.")
    if entry.framework:
        # '# framework:' is not a way round the evidence rules. It accepts
        # exactly two shapes, and tests/test_dead_code_gate.py checks both
        # against the real framework.
        if entry.owner not in {ANY_OWNER, None}:
            problems.append(
                "under '# framework:' only '_.member' (a member of a class "
                "that derives from the framework) or a bare module-level hook "
                "is accepted. Give this entry '# file:', '# kind:', '# site:' "
                "and '# evidence:' in a block of its own instead."
            )
        return problems
    if entry.owner == ANY_OWNER:
        problems.append(
            "'_.name' matches every class and is only allowed under "
            "'# framework:'. Name the owning class instead."
        )
        return problems
    if not entry.file or not entry.kind:
        problems.append(
            "needs '# file:' (where it is defined, relative to the package) "
            "and '# kind:' (method, function, variable, ...), so it cannot "
            "cover a same-named symbol somewhere else."
        )
    if not entry.evidence or any(not site for site, _ in entry.evidence):
        problems.append(
            "needs '# site:' (file that reaches it dynamically) followed by "
            "'# evidence:' (text that must be present there)."
        )
        return problems
    return problems + _evidence_problems(layout, entry)


def match_whitelist(
    layout: Layout, entries: list[WhitelistEntry], findings: set[Finding]
) -> list[tuple[WhitelistEntry, set[Finding]]]:
    """Each whitelist entry with the findings it covers."""
    ctx = _Context(framework_roots(layout.package), entry_modules(layout.package))
    return [
        (entry, {f for f in findings if _entry_matches(entry, f, ctx)})
        for entry in entries
    ]


def check_whitelist(
    layout: Layout, entries: list[WhitelistEntry], findings: set[Finding]
) -> tuple[set[Finding], list[str]]:
    """Apply the whitelist. Returns (suppressed findings, problems)."""
    suppressed: set[Finding] = set()
    problems: list[str] = []

    for entry, matched in match_whitelist(layout, entries, findings):
        where = f"{layout.whitelist.name}:{entry.line} {entry.label}"
        claim = _claim_problems(layout, entry)
        problems += [f"{where}: {p}" for p in claim]
        if not matched and not claim:
            problems.append(
                f"{where}: matches nothing the detectors report. The symbol was "
                "deleted or renamed, moved to another file, or is now "
                "referenced directly. Remove or correct the entry."
            )
        suppressed |= matched
    return suppressed, problems


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


_ORDINAL_RE = re.compile(r"^(?P<qualname>.*)@(?P<ordinal>[0-9]+)$")


def parse_baseline(text: str) -> dict[Finding, str]:
    """Baseline entries -> trailing note (may be empty)."""
    entries: dict[Finding, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        body, _, note = raw.partition("  # ")
        parts = body.split(None, 2)
        if len(parts) != 3:
            raise ValueError(f"baseline line {lineno}: cannot parse {raw!r}")
        kind, file, key = parts[0], parts[1], parts[2].strip()
        match = _ORDINAL_RE.match(key)
        finding = (
            Finding(kind, file, match["qualname"], int(match["ordinal"]))
            if match
            else Finding(kind, file, key)
        )
        if finding in entries:
            raise ValueError(f"baseline line {lineno}: duplicate entry {raw!r}")
        entries[finding] = note.strip()
    return entries


def render_baseline(entries: dict[Finding, str]) -> str:
    """Sorted, human-readable baseline text."""
    lines = [BASELINE_HEADER]
    for finding in sorted(entries):
        note = entries[finding]
        lines.append(finding.render() + (f"  # {note}" if note else ""))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@dataclass
class Result:
    """Outcome of one gate run."""

    findings: set[Finding]
    suppressed: set[Finding]
    baseline: dict[Finding, str]
    whitelist_problems: list[str]
    detector_problems: list[str] = field(default_factory=list)

    @property
    def current(self) -> set[Finding]:
        """Findings that the whitelist does not explain."""
        return self.findings - self.suppressed

    @property
    def new(self) -> list[Finding]:
        """Unused symbols that are not in the baseline."""
        return sorted(self.current - set(self.baseline))

    @property
    def gone(self) -> list[Finding]:
        """Baseline entries the detectors no longer report."""
        return sorted(set(self.baseline) - self.current)

    @property
    def ok(self) -> bool:
        """True when the gate passes."""
        return not (
            self.new or self.gone or self.whitelist_problems or self.detector_problems
        )


def run(layout: Layout) -> Result:
    """Run both detectors and compare with the whitelist and the baseline."""
    config = load_vulture_config(layout.root)
    findings, detector_problems = collect_findings(layout, config)
    entries = (
        parse_whitelist(layout.whitelist.read_text(encoding="utf-8"))
        if layout.whitelist.is_file()
        else []
    )
    suppressed, problems = check_whitelist(layout, entries, findings)
    baseline = (
        parse_baseline(layout.baseline.read_text(encoding="utf-8"))
        if layout.baseline.is_file()
        else {}
    )
    return Result(findings, suppressed, baseline, problems, detector_problems)


def report(result: Result, layout: Layout) -> str:
    """Failure text telling the developer exactly what to do."""
    script = "uv run python scripts/dead_code_gate.py"
    out: list[str] = []
    if result.detector_problems:
        out.append(
            "DETECTOR PROBLEMS:\n"
            + "\n".join(f"    {p}" for p in result.detector_problems)
        )
    if result.new:
        out.append(
            f"NEW DEAD CODE: {len(result.new)} unused symbol(s) that are not in "
            f"{layout.baseline.name}:\n"
            + "\n".join(f"    {f.render()}" for f in result.new)
            + "\n\n  For each one, do ONE of:\n"
            "    1. It is dead            -> delete it.\n"
            "    2. It should be called   -> wire it up (an unwired fix looks "
            "exactly like this).\n"
            "    3. It is live through dynamic access (getattr, a string table, "
            "Home Assistant\n"
            f"       calling it by name) -> add it to {layout.whitelist.name} "
            "with '# site:', '# evidence:'\n"
            "       and '# why:'.\n"
            "  Do not add it to the baseline: that file only shrinks. "
            "(Carrying new debt on\n"
            f"  purpose needs: {script} --update --allow-additions)"
        )
    if result.gone:
        out.append(
            f"BASELINE IS STALE: {layout.baseline.name} lists {len(result.gone)} "
            "symbol(s) that are no longer reported\n"
            "(deleted, renamed, or now referenced):\n"
            + "\n".join(f"    {f.render()}" for f in result.gone)
            + "\n\n  Shrink the baseline in this same change, so it can never "
            "silently regrow:\n"
            f"    {script} --update\n"
            "  and commit the result. If you renamed the symbol rather than "
            "removing it, it\n"
            "  will show up above as NEW: decide it properly instead of "
            "re-baselining it."
        )
    if result.whitelist_problems:
        out.append(
            f"WHITELIST PROBLEMS in {layout.whitelist.name}:\n"
            + "\n".join(f"    {p}" for p in result.whitelist_problems)
        )
    return "\n\n".join(out)


def summary(result: Result) -> str:
    """Counts by kind for the three buckets."""
    lines = [
        f"detector findings : {len(result.findings)}",
        f"whitelisted (live): {len(result.suppressed)}",
        f"baseline (debt)   : {len(result.baseline)}",
    ]
    for label, group in (
        ("whitelisted", result.suppressed),
        ("baseline", set(result.baseline)),
    ):
        counts = Counter(f.kind for f in group)
        lines.append(
            f"  {label} by kind: "
            + ", ".join(f"{kind} {n}" for kind, n in sorted(counts.items()))
        )
    return "\n".join(lines)


def update(result: Result, layout: Layout, allow_additions: bool) -> list[Finding]:
    """Rewrite the baseline. Returns additions that were REFUSED."""
    keep = {f: note for f, note in result.baseline.items() if f in result.current}
    refused: list[Finding] = []
    if allow_additions:
        keep.update({f: "" for f in result.new})
    else:
        refused = result.new
    layout.baseline.write_text(render_baseline(keep), encoding="utf-8")
    return refused


def build_layout(args: argparse.Namespace) -> Layout:
    """Resolve the CLI paths against ``--root``."""
    root = Path(args.root).resolve()
    package = root / args.package
    return Layout(
        root=root,
        package=package,
        whitelist=root / args.whitelist,
        baseline=root / args.baseline,
        data_module=package / args.data_module,
        data_class=args.data_class,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Exit 0 = gate passes, 1 = it does not."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--package", default=DEFAULT_PACKAGE)
    parser.add_argument("--whitelist", default=DEFAULT_WHITELIST)
    parser.add_argument("--baseline", default=DEFAULT_BASELINE)
    parser.add_argument("--data-module", default=DEFAULT_DATA_MODULE)
    parser.add_argument("--data-class", default=DEFAULT_DATA_CLASS)
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite the baseline, removing entries that are no longer found",
    )
    parser.add_argument(
        "--allow-additions",
        action="store_true",
        help="with --update: also ADD new findings to the baseline (new debt)",
    )
    parser.add_argument(
        "--summary", action="store_true", help="print counts by kind and exit"
    )
    args = parser.parse_args(argv)
    if args.allow_additions and not args.update:
        parser.error("--allow-additions only makes sense with --update")

    layout = build_layout(args)
    result = run(layout)

    if args.summary:
        print(summary(result))
        return 0

    if args.update:
        removed = result.gone
        refused = update(result, layout, args.allow_additions)
        added = [] if refused else result.new
        print(
            f"{layout.baseline.name}: removed {len(removed)}, added {len(added)}, "
            f"now {len(result.baseline) - len(removed) + len(added)} entries."
        )
        for finding in removed:
            print(f"  - {finding.render()}")
        for finding in added:
            print(f"  + {finding.render()}")
        result = run(layout)
        if result.ok:
            return 0
        print()
        print(report(result, layout))
        if refused:
            print(
                "\n--update only shrinks the baseline; the NEW symbols above "
                "were not added."
            )
        return 1

    if result.ok:
        print(
            f"Dead-code ratchet OK: {len(result.suppressed)} whitelisted as live, "
            f"{len(result.baseline)} in the baseline, nothing new."
        )
        return 0
    print(report(result, layout))
    return 1


if __name__ == "__main__":
    sys.exit(main())

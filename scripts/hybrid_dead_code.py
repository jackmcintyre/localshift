#!/usr/bin/env python3
"""Hybrid dead-code detector for the LocalShift HA integration (issue #837).

Why this exists
---------------
LocalShift is a live Home Assistant custom integration. Two existing detectors
are too noisy to act on:

* ``vulture --min-confidence 80`` is clean, but lowering the threshold floods
  the output with Home Assistant *framework* false positives (``async_setup_entry``,
  ``device_info``, ``extra_state_attributes``, ``icon``, config-flow
  ``async_step_*`` handlers, entity properties...). HA invokes these by name; they
  are not dead.
* ``scripts/find_dead_code.py`` flags "never instantiated" classes, but almost
  every real class is created via HA platform setup, a factory function, an
  ``__all__`` re-export, or string/getattr dispatch -- so its class signal is
  unusable as removal evidence.

This detector combines THREE signals and only calls something "high confidence
dead" when ALL of them agree:

1. STATIC: zero cross-file references in the AST/source (instantiation, calls,
   attribute access, subclassing, decorators, string literals).
2. RUNTIME: zero executed lines in ``coverage.json`` for that function/method
   (when coverage data is available).
3. NOT-SUPPRESSED: not an HA framework entry point, not exported via ``__all__``,
   not a dataclass field consumer, not reachable by dynamic dispatch heuristics.

Static evidence alone is what gates removals; the runtime signal is used to
*prioritise* and to keep the bar conservative (an item with test coverage is
never reported as dead even if the cheap static pass missed a reference).

The script is intentionally dependency-free, deterministic, and fast so it can
run as a NON-BLOCKING CI guardrail. It lives under ``scripts/`` so it is excluded
from vulture, coverage, and the package lint scope.

Usage
-----
    uv run python scripts/hybrid_dead_code.py \
        --source custom_components/localshift \
        --coverage coverage.json \
        --format text

Exit code is always 0 (guardrail, never blocks). Use ``--strict`` to exit 1 when
high-confidence items are found (handy for local triage, never wired into CI).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Suppression allowlists
# ---------------------------------------------------------------------------

# Methods/functions invoked by the Home Assistant framework by convention. These
# are never "called" from within our own source, so a zero-static-reference
# finding on them is always a false positive.
HA_FRAMEWORK_NAMES: frozenset[str] = frozenset(
    {
        # Integration / config-entry lifecycle
        "async_setup",
        "async_setup_entry",
        "async_unload_entry",
        "async_migrate_entry",
        "async_reload_entry",
        "async_remove_entry",
        "async_migrate_config_entry",
        "async_remove_config_entry_device",
        # Config / options flow
        "async_get_options_flow",
        "async_supports_options_flow",
        # Entity lifecycle callbacks
        "async_added_to_hass",
        "async_will_remove_from_hass",
        "async_update",
        "update",
        # Entity command handlers
        "async_turn_on",
        "async_turn_off",
        "async_toggle",
        "async_press",
        "press",
        "async_select_option",
        "select_option",
        "async_set_native_value",
        "set_native_value",
        # Coordinator
        "_async_update_data",
        "_async_setup",
    }
)

# Config-flow step handlers: any method named ``async_step_*`` is dispatched by
# HA via the flow manager, so it is reachable even with no explicit caller.
HA_FRAMEWORK_PREFIXES: tuple[str, ...] = ("async_step_",)

# Entity / descriptor properties that HA reads reflectively. A property is dead
# only if its *whole class* is dead, which the class-level check handles; we never
# flag these individually.
HA_FRAMEWORK_PROPERTIES: frozenset[str] = frozenset(
    {
        "device_info",
        "unique_id",
        "name",
        "icon",
        "entity_picture",
        "entity_category",
        "extra_state_attributes",
        "available",
        "enabled",
        "should_poll",
        "entity_registry_enabled_default",
        "entity_registry_visible_default",
        "native_value",
        "native_min_value",
        "native_max_value",
        "native_step",
        "native_unit_of_measurement",
        "device_class",
        "state_class",
        "suggested_display_precision",
        "mode",
        "options",
        "current_option",
        "is_on",
        "state",
        "assumed_state",
        "capability_attributes",
    }
)

# Dunder methods are part of the data model / protocol surface and are invoked
# implicitly by the interpreter.
DUNDER_RE = re.compile(r"^__\w+__$")

# Serialization / protocol method names that are commonly invoked dynamically
# (json.dumps default=, asdict, comparison protocols, context managers...).
PROTOCOL_NAMES: frozenset[str] = frozenset(
    {
        "to_dict",
        "from_dict",
        "as_dict",
        "asdict",
        "to_json",
        "from_json",
        "serialize",
        "deserialize",
        "__enter__",
        "__exit__",
        "__aenter__",
        "__aexit__",
    }
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Definition:
    """A single defined symbol (class, method, function, or module constant)."""

    name: str  # bare name, e.g. "PriceCalculator" or "compute"
    qualname: str  # "ClassName.method" for methods, else == name
    kind: str  # CLASS | METHOD | FUNCTION | CONSTANT
    file: str  # path relative to repo root
    line: int
    end_line: int
    class_name: str = ""  # owning class for methods
    is_exported: bool = False  # listed in an __all__
    decorators: list[str] = field(default_factory=list)


@dataclass
class Finding:
    """A ranked dead-code candidate with its supporting evidence."""

    definition: Definition
    static_refs: int
    runtime_pct: float | None  # None == no coverage data for this symbol
    confidence: str  # high | medium | low
    reasons: list[str]


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class HybridDeadCodeDetector:
    """Combine static AST references with runtime coverage to find dead code."""

    def __init__(self, source_dir: Path, repo_root: Path) -> None:
        """Initialise the detector for ``source_dir`` rooted at ``repo_root``."""
        self.source_dir = source_dir
        self.repo_root = repo_root
        self.exclude_dir_names = {"__pycache__", ".venv", "worktrees", "tests"}

        self.definitions: list[Definition] = []
        # name -> count of textual references across all files (excludes the
        # defining line itself). Used as the static signal.
        self.name_hits: dict[str, int] = defaultdict(int)
        # Every dotted/bare name that appears in a string literal anywhere
        # (covers string-dispatch, entity registration by class name, etc.).
        self.string_tokens: set[str] = set()
        # Names exported via __all__ in any module.
        self.exported: set[str] = set()
        # Names of all defined classes (for subclassing / factory detection).
        self.class_names: set[str] = set()

    # -- file discovery -----------------------------------------------------

    def _iter_py_files(self) -> list[Path]:
        files = []
        for path in sorted(self.source_dir.rglob("*.py")):
            if any(part in self.exclude_dir_names for part in path.parts):
                continue
            files.append(path)
        return files

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.repo_root))
        except ValueError:
            return str(path)

    # -- pass 1: collect definitions + exports ------------------------------

    def collect(self) -> None:
        """First pass: index every definition, ``__all__`` export and token."""
        files = self._iter_py_files()
        for path in files:
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (OSError, SyntaxError):
                continue
            rel = self._rel(path)
            self._collect_exports(tree)
            self._collect_definitions(tree, rel)
        # Mark exported flag on definitions now that all __all__ are known.
        for d in self.definitions:
            if d.name in self.exported:
                d.is_exported = True

    def _collect_exports(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if "__all__" in targets and isinstance(
                    node.value, (ast.List, ast.Tuple)
                ):
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            self.exported.add(elt.value)

    def _decorator_names(self, node: ast.AST) -> list[str]:
        names = []
        for dec in getattr(node, "decorator_list", []):
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Name):
                names.append(target.id)
            elif isinstance(target, ast.Attribute):
                names.append(target.attr)
        return names

    def _collect_definitions(self, tree: ast.Module, rel: str) -> None:
        for node in tree.body:
            self._collect_node(node, rel, class_name="")

    def _collect_node(self, node: ast.AST, rel: str, class_name: str) -> None:
        if isinstance(node, ast.ClassDef):
            self.class_names.add(node.name)
            self.definitions.append(
                Definition(
                    name=node.name,
                    qualname=node.name,
                    kind="CLASS",
                    file=rel,
                    line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    decorators=self._decorator_names(node),
                )
            )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.definitions.append(
                        Definition(
                            name=child.name,
                            qualname=f"{node.name}.{child.name}",
                            kind="METHOD",
                            file=rel,
                            line=child.lineno,
                            end_line=getattr(child, "end_lineno", child.lineno),
                            class_name=node.name,
                            decorators=self._decorator_names(child),
                        )
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not class_name:
                self.definitions.append(
                    Definition(
                        name=node.name,
                        qualname=node.name,
                        kind="FUNCTION",
                        file=rel,
                        line=node.lineno,
                        end_line=getattr(node, "end_lineno", node.lineno),
                        decorators=self._decorator_names(node),
                    )
                )
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and (
                    target.id.startswith("CONF_") or target.id.startswith("DEFAULT_")
                ):
                    self.definitions.append(
                        Definition(
                            name=target.id,
                            qualname=target.id,
                            kind="CONSTANT",
                            file=rel,
                            line=node.lineno,
                            end_line=getattr(node, "end_lineno", node.lineno),
                        )
                    )

    # -- pass 2: count references -------------------------------------------

    def count_references(self) -> None:
        """Second pass: count textual references to every defined name."""
        # Build the set of names we care about (bare names; methods counted by
        # their attribute name).
        defined_names = {d.name for d in self.definitions}
        # Pre-compile one regex per name -> reused across files.
        patterns = {n: re.compile(rf"\b{re.escape(n)}\b") for n in defined_names}

        # Map (file, line) of each definition so we can subtract the defining
        # occurrence from the hit count.
        self_lines: dict[str, set[tuple[str, int]]] = defaultdict(set)
        for d in self.definitions:
            self_lines[d.name].add((d.file, d.line))

        for path in self._iter_py_files():
            try:
                source = path.read_text(encoding="utf-8")
            except OSError:
                continue
            rel = self._rel(path)
            self._collect_string_tokens(source)
            lines = source.splitlines()
            for lineno, text in enumerate(lines, start=1):
                for name, pat in patterns.items():
                    if pat.search(text):
                        if (rel, lineno) in self_lines.get(name, ()):
                            # The line that defines the symbol -- skip, but a
                            # method def line never equals an attribute call, so
                            # this only matters for class/func/const defs.
                            continue
                        self.name_hits[name] += 1

    def _collect_string_tokens(self, source: str) -> None:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value.strip()
                # Whole-string match (e.g. "OptimizerPlanSensor") and
                # dotted-attr tail (e.g. "module.Class").
                if val:
                    self.string_tokens.add(val)
                    self.string_tokens.add(val.split(".")[-1])

    # -- suppression --------------------------------------------------------

    def _is_suppressed(self, d: Definition) -> tuple[bool, str]:
        """Return (suppressed, reason) for framework / dynamic / exported defs."""
        bare = d.name
        if d.is_exported:
            return True, "exported via __all__"
        if DUNDER_RE.match(bare):
            return True, "dunder / protocol method"
        if bare in PROTOCOL_NAMES:
            return True, "serialization/protocol method"

        if d.kind == "METHOD":
            if bare in HA_FRAMEWORK_NAMES:
                return True, "HA framework entry point"
            if any(bare.startswith(p) for p in HA_FRAMEWORK_PREFIXES):
                return True, "HA config-flow step handler"
            if bare in HA_FRAMEWORK_PROPERTIES:
                return True, "HA entity/descriptor property"
            if "property" in d.decorators:
                # Properties are read reflectively (templates, attrs); only the
                # owning class going dead makes a property dead.
                return True, "property accessor"
            if "abstractmethod" in d.decorators:
                return True, "abstract method (implemented by subclasses)"
            # pytest fixtures / overrides etc. are not in our source tree, but
            # cached_property is reflective like property.
            if "cached_property" in d.decorators:
                return True, "cached_property accessor"

        if d.kind == "FUNCTION" and bare in HA_FRAMEWORK_NAMES:
            return True, "HA framework entry point"

        # Class reachable via string dispatch / registration by name.
        if d.kind == "CLASS" and bare in self.string_tokens:
            return True, "class referenced by string literal"

        # Function/const referenced by string literal (service names, attrs).
        if bare in self.string_tokens:
            return True, "referenced by string literal"

        return False, ""

    # -- runtime signal -----------------------------------------------------

    @staticmethod
    def load_coverage(coverage_path: Path) -> dict[str, dict[str, float]]:
        """Parse coverage.json -> {relfile: {qualname: percent_covered}}.

        Returns an empty mapping if the file is missing or malformed so the
        detector degrades gracefully to a static-only run.
        """
        result: dict[str, dict[str, float]] = {}
        try:
            data = json.loads(coverage_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return result
        for rel, info in data.get("files", {}).items():
            fns = info.get("functions", {})
            per_fn: dict[str, float] = {}
            for qual, fn_info in fns.items():
                pct = fn_info.get("summary", {}).get("percent_covered")
                if pct is not None and qual:
                    per_fn[qual] = float(pct)
            result[rel] = per_fn
        return result

    # -- analysis -----------------------------------------------------------

    def analyze(self, coverage: dict[str, dict[str, float]]) -> list[Finding]:
        """Combine signals and produce ranked findings."""
        findings: list[Finding] = []
        have_coverage = bool(coverage)

        for d in self.definitions:
            suppressed, _reason = self._is_suppressed(d)
            if suppressed:
                continue

            static_refs = self.name_hits.get(d.name, 0)

            runtime_pct = self._runtime_pct(d, coverage)

            # Static dead == no cross-file/in-file references at all.
            static_dead = static_refs == 0
            if not static_dead:
                continue  # has references -> definitely not dead

            reasons = ["zero static references"]

            # Determine confidence using the runtime signal.
            if not have_coverage or runtime_pct is None:
                # No runtime evidence: static-only. Medium at best -- the static
                # pass can miss exotic dynamic use, so do not call it "high".
                confidence = "medium"
                reasons.append(
                    "no runtime data" if have_coverage else "coverage.json absent"
                )
            elif runtime_pct == 0.0:
                confidence = "high"
                reasons.append("0% runtime coverage")
            else:
                # Executed at runtime despite no static reference -> reachable
                # dynamically (dispatch/framework). Suppress to low.
                confidence = "low"
                reasons.append(f"{runtime_pct:.0f}% runtime coverage (reachable)")

            findings.append(
                Finding(
                    definition=d,
                    static_refs=static_refs,
                    runtime_pct=runtime_pct,
                    confidence=confidence,
                    reasons=reasons,
                )
            )

        order = {"high": 0, "medium": 1, "low": 2}
        findings.sort(
            key=lambda f: (
                order[f.confidence],
                f.definition.file,
                f.definition.line,
            )
        )
        return findings

    def _runtime_pct(
        self, d: Definition, coverage: dict[str, dict[str, float]]
    ) -> float | None:
        per_fn = coverage.get(d.file)
        if per_fn is None:
            return None
        if d.kind in ("METHOD", "FUNCTION"):
            return per_fn.get(d.qualname)
        if d.kind == "CLASS":
            # Aggregate over the class's methods (coverage keys "Class.method").
            prefix = f"{d.name}."
            pcts = [v for k, v in per_fn.items() if k.startswith(prefix)]
            if not pcts:
                return None
            return max(pcts)  # any executed method => class is reachable
        return None  # constants have no function coverage


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def render_text(findings: list[Finding], have_coverage: bool) -> str:
    """Render findings as a human-readable, deterministic text report."""
    out: list[str] = []
    out.append("=" * 78)
    out.append("HYBRID DEAD-CODE REPORT (static + runtime coverage) -- issue #837")
    out.append("=" * 78)
    if not have_coverage:
        out.append("NOTE: coverage.json not found -- static-only run (no HIGH items).")
    out.append("")

    buckets: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        buckets[f.confidence].append(f)

    labels = {
        "high": "HIGH CONFIDENCE (zero static refs AND 0% runtime coverage)",
        "medium": "MEDIUM (zero static refs, no runtime evidence)",
        "low": "LOW (zero static refs but executed at runtime -- likely dynamic)",
    }
    for level in ("high", "medium", "low"):
        items = buckets.get(level, [])
        if not items:
            continue
        out.append(f"[{labels[level]}]")
        for f in items:
            d = f.definition
            cov = "n/a" if f.runtime_pct is None else f"{f.runtime_pct:.0f}%"
            out.append(
                f"  {d.file}:{d.line}  {d.kind} {d.qualname}  "
                f"(static_refs={f.static_refs}, runtime={cov})"
            )
            out.append(f"      {'; '.join(f.reasons)}")
        out.append("")

    out.append("=" * 78)
    out.append("SUMMARY")
    out.append("=" * 78)
    for level in ("high", "medium", "low"):
        out.append(f"  {level:>6}: {len(buckets.get(level, []))}")
    out.append(f"  total : {len(findings)}")
    out.append("")
    out.append(
        "Removals require AIRTIGHT static evidence: verify each HIGH item with "
        "`grep -rn` across custom_components/ AND tests/ before deleting."
    )
    return "\n".join(out)


def render_json(findings: list[Finding]) -> str:
    """Render findings as JSON for tooling/CI consumption."""
    payload = [
        {
            "file": f.definition.file,
            "line": f.definition.line,
            "kind": f.definition.kind,
            "name": f.definition.qualname,
            "static_refs": f.static_refs,
            "runtime_pct": f.runtime_pct,
            "confidence": f.confidence,
            "reasons": f.reasons,
        }
        for f in findings
    ]
    return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Always returns 0 unless ``--strict`` is set."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default="custom_components/localshift",
        help="Source directory to scan.",
    )
    parser.add_argument(
        "--coverage",
        default="coverage.json",
        help="Path to coverage.json (optional; enables the runtime signal).",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format.",
    )
    parser.add_argument(
        "--min-confidence",
        choices=["high", "medium", "low"],
        default="medium",
        help="Minimum confidence to report.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any HIGH-confidence item is found (NEVER used in CI).",
    )
    args = parser.parse_args(argv)

    source_dir = Path(args.source).resolve()
    if not source_dir.is_dir():
        print(f"error: source dir not found: {source_dir}", file=sys.stderr)
        return 0  # guardrail: never break the build

    # repo root = nearest parent that is not inside the package.
    repo_root = Path.cwd()

    detector = HybridDeadCodeDetector(source_dir, repo_root)
    detector.collect()
    detector.count_references()

    coverage_path = Path(args.coverage)
    coverage = detector.load_coverage(coverage_path)
    have_coverage = bool(coverage)

    findings = detector.analyze(coverage)

    order = {"high": 0, "medium": 1, "low": 2}
    min_rank = order[args.min_confidence]
    findings = [f for f in findings if order[f.confidence] <= min_rank]

    if args.format == "json":
        print(render_json(findings))
    else:
        print(render_text(findings, have_coverage))

    if args.strict and any(f.confidence == "high" for f in findings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

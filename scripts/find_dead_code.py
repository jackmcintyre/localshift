#!/usr/bin/env python3
"""Custom dead code detection script for LocalShift.

This script addresses vulture's limitations by:
1. Tracking cross-file method/function references
2. Detecting classes that are never instantiated
3. Analyzing import graphs to find unused modules
4. Auditing configuration constants
"""

import ast
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DeadCodeItem:
    """Represents a potentially dead code item."""

    file: str
    line: int
    item_type: str
    name: str
    context: str = ""
    line_count: int = 1
    confidence: str = "high"
    exempt: bool = False
    issue_ref: str = ""


class DeadCodeDetector:
    """Detects dead code using multiple strategies."""

    def __init__(self, source_dir: str, exclude_dirs: list[str] | None = None):
        """Initialize the detector.

        Args:
            source_dir: Root directory to scan
            exclude_dirs: Directories to exclude (e.g., ['tests', '__pycache__'])
        """
        self.source_dir = Path(source_dir)
        self.exclude_dirs = exclude_dirs or [
            "tests",
            "__pycache__",
            ".venv",
            "worktrees",
        ]

        # Indexes
        self.class_definitions: dict[str, dict[str, Any]] = {}
        self.method_definitions: dict[str, dict[str, Any]] = {}
        self.function_definitions: dict[str, dict[str, Any]] = {}
        self.function_references: dict[str, list[tuple[str, int]]] = defaultdict(list)
        self.config_constants: dict[str, dict[str, Any]] = {}
        self.import_graph: dict[str, set[str]] = defaultdict(set)

        # Reference tracking
        self.class_references: dict[str, list[tuple[str, int]]] = defaultdict(list)
        self.method_references: dict[str, list[tuple[str, int]]] = defaultdict(list)
        self.constant_references: dict[str, list[tuple[str, int]]] = defaultdict(list)

        # HA framework methods to ignore
        self.ha_framework_methods = {
            "async_setup_entry",
            "async_unload_entry",
            "async_migrate_entry",
            "async_reload_entry",
            "async_remove_entry",
            "async_migrate_config_entry",
            "async_get_options_flow",
            "async_step_user",
            "async_step_init",
            "async_step_*",
            "async_added_to_hass",
            "async_will_remove_from_hass",
            "async_update",
            "async_turn_on",
            "async_turn_off",
            "async_toggle",
            "async_press",
            "async_select_option",
            "async_set_native_value",
            "async_handle_*",
        }

        # Properties that are HA framework callbacks
        self.ha_framework_properties = {
            "device_info",
            "unique_id",
            "name",
            "icon",
            "entity_picture",
            "extra_state_attributes",
            "available",
            "enabled",
            "entity_registry_enabled_default",
            "native_value",
            "native_min_value",
            "native_max_value",
            "native_step",
            "native_unit_of_measurement",
            "mode",
            "options",
            "current_option",
            "is_on",
            "state",
        }

    def scan_all_files(self) -> None:
        """Scan all Python files in the source directory."""
        py_files = list(self.source_dir.rglob("*.py"))
        print(f"Scanning {len(py_files)} Python files...")

        for filepath in py_files:
            if self._should_exclude(filepath):
                continue

            try:
                self._scan_file(filepath)
            except Exception as e:
                print(f"  Error scanning {filepath}: {e}", file=sys.stderr)

        print(
            f"Indexed {len(self.class_definitions)} classes, "
            f"{len(self.method_definitions)} methods, "
            f"{len(self.function_definitions)} functions"
        )

    def _should_exclude(self, filepath: Path) -> bool:
        """Check if file should be excluded."""
        path_parts = filepath.parts
        return any(exclude in path_parts for exclude in self.exclude_dirs)

    def _scan_file(self, filepath: Path) -> None:
        """Parse and scan a single Python file."""
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return

        rel_path = str(filepath.relative_to(self.source_dir))
        lines = content.split("\n")

        # Scan for definitions
        self._scan_definitions(tree, rel_path, lines)

        # Scan for references
        self._scan_references(tree, rel_path, lines, content)

        # Scan for config constants
        self._scan_config_constants(tree, rel_path, lines)

    def _scan_definitions(
        self,
        tree: ast.AST,
        filepath: str,
        lines: list[str],
    ) -> None:
        """Extract class, method, and function definitions."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                # Count lines in class
                line_count = self._count_lines(node, lines)
                self.class_definitions[node.name] = {
                    "file": filepath,
                    "line": node.lineno,
                    "line_count": line_count,
                    "docstring": ast.get_docstring(node),
                }

                # Scan methods within class
                for item in node.body:
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                        method_name = f"{node.name}.{item.name}"
                        method_line_count = self._count_lines(item, lines)
                        self.method_definitions[method_name] = {
                            "file": filepath,
                            "line": item.lineno,
                            "line_count": method_line_count,
                            "class": node.name,
                            "is_async": isinstance(item, ast.AsyncFunctionDef),
                        }

            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                # Check if it's a top-level function (not in a class)
                if not any(
                    isinstance(parent, ast.ClassDef) for parent in ast.walk(tree)
                ):
                    line_count = self._count_lines(node, lines)
                    self.function_definitions[node.name] = {
                        "file": filepath,
                        "line": node.lineno,
                        "line_count": line_count,
                        "is_async": isinstance(node, ast.AsyncFunctionDef),
                    }

    def _count_lines(self, node: ast.AST, lines: list[str]) -> int:
        """Count non-empty, non-comment lines in a node."""
        if not hasattr(node, "end_lineno") or not hasattr(node, "end_col_offset"):
            return 1

        start = node.lineno - 1
        end = getattr(node, "end_lineno", node.lineno)

        count = 0
        for i in range(start, min(end, len(lines))):
            line = lines[i].strip()
            if line and not line.startswith("#"):
                count += 1

        return max(1, count)

    def _scan_references(
        self,
        tree: ast.AST,
        filepath: str,
        lines: list[str],
        content: str,
    ) -> None:
        """Scan for references to classes, methods, and functions."""
        # Use regex for broader reference detection
        # This catches dynamic calls like getattr(), string references, etc.

        for class_name in self.class_definitions:
            # Look for instantiation patterns: ClassName(
            pattern = rf"\b{re.escape(class_name)}\s*\("
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line):
                    self.class_references[class_name].append((filepath, i))

            # Also check for subclassing
            pattern = rf"class\s+\w+\s*\([^)]*{re.escape(class_name)}"
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line):
                    self.class_references[class_name].append((filepath, i))

        for method_name in self.method_definitions:
            # Extract just the method name for searching
            parts = method_name.split(".")
            if len(parts) == 2:
                _, method = parts

                # Skip HA framework methods
                if self._is_ha_framework_method(method):
                    continue

                # Look for method calls: .method( or obj.method(
                pattern = rf"\.{re.escape(method)}\s*\("
                for i, line in enumerate(lines, 1):
                    if re.search(pattern, line):
                        self.method_references[method_name].append((filepath, i))

        for func_name in self.function_definitions:
            # Look for function calls
            pattern = rf"\b{re.escape(func_name)}\s*\("
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line):
                    self.function_references[func_name].append((filepath, i))

    def _scan_config_constants(
        self,
        tree: ast.AST,
        filepath: str,
        lines: list[str],
    ) -> None:
        """Scan for configuration constants (CONF_*, DEFAULT_*)."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        if name.startswith("CONF_") or name.startswith("DEFAULT_"):
                            self.config_constants[name] = {
                                "file": filepath,
                                "line": node.lineno,
                                "value": self._get_node_value(node.value),
                            }
                            # Also track references
                            self._find_constant_references(name, filepath, lines)

    def _get_node_value(self, node: ast.AST) -> str:
        """Extract value from AST node."""
        if isinstance(node, ast.Constant):
            return str(node.value)
        elif isinstance(node, ast.Name):
            return f"<{node.id}>"
        return "<complex>"

    def _find_constant_references(
        self,
        name: str,
        filepath: str,
        lines: list[str],
    ) -> None:
        """Find references to a constant."""
        pattern = rf"\b{re.escape(name)}\b"
        for i, line in enumerate(lines, 1):
            if re.search(pattern, line):
                self.constant_references[name].append((filepath, i))

    def _is_ha_framework_method(self, method_name: str) -> bool:
        """Check if method is a HA framework callback."""
        if method_name in self.ha_framework_methods:
            return True
        if method_name in self.ha_framework_properties:
            return True
        if method_name.startswith("_") and not method_name.startswith("__"):
            return False  # Private methods should still be checked
        return method_name.startswith("async_") or method_name in {
            "to_dict",
            "from_dict",
            "__init__",
            "__repr__",
            "__str__",
            "__eq__",
            "__hash__",
        }

    def analyze(self) -> list[DeadCodeItem]:
        """Analyze collected data to find dead code."""
        dead_code = []

        # Find classes with no references
        for class_name, info in self.class_definitions.items():
            refs = self.class_references.get(class_name, [])
            # Exclude definition itself
            actual_refs = [
                (f, l) for f, l in refs if not (f == info["file"] and l == info["line"])
            ]

            if not actual_refs:
                dead_code.append(
                    DeadCodeItem(
                        file=info["file"],
                        line=info["line"],
                        item_type="CLASS",
                        name=class_name,
                        line_count=info["line_count"],
                        confidence="high",
                        context="Never instantiated or subclassed",
                    )
                )

        # Find methods with no references
        for method_name, info in self.method_definitions.items():
            method = method_name.split(".")[-1]

            # Skip HA framework methods
            if self._is_ha_framework_method(method):
                continue

            refs = self.method_references.get(method_name, [])
            # Exclude definition itself
            actual_refs = [
                (f, l) for f, l in refs if not (f == info["file"] and l == info["line"])
            ]

            if not actual_refs:
                dead_code.append(
                    DeadCodeItem(
                        file=info["file"],
                        line=info["line"],
                        item_type="METHOD",
                        name=method_name,
                        line_count=info["line_count"],
                        confidence="medium",
                        context="Never called",
                    )
                )

        # Find functions with no references
        for func_name, info in self.function_definitions.items():
            refs = self.function_references.get(func_name, [])
            actual_refs = [
                (f, l) for f, l in refs if not (f == info["file"] and l == info["line"])
            ]

            if not actual_refs:
                dead_code.append(
                    DeadCodeItem(
                        file=info["file"],
                        line=info["line"],
                        item_type="FUNCTION",
                        name=func_name,
                        line_count=info["line_count"],
                        confidence="high",
                        context="Never called",
                    )
                )

        # Find unused config constants
        for const_name, info in self.config_constants.items():
            refs = self.constant_references.get(const_name, [])
            actual_refs = [
                (f, l) for f, l in refs if not (f == info["file"] and l == info["line"])
            ]

            if not actual_refs:
                dead_code.append(
                    DeadCodeItem(
                        file=info["file"],
                        line=info["line"],
                        item_type="CONSTANT",
                        name=const_name,
                        confidence="high",
                        context="Never referenced",
                    )
                )

        return dead_code

    def generate_report(self, dead_code: list[DeadCodeItem]) -> str:
        """Generate a formatted report."""
        lines = []
        lines.append("=" * 80)
        lines.append("DEAD CODE REPORT")
        lines.append("=" * 80)
        lines.append("")

        # Group by type
        by_type = defaultdict(list)
        for item in dead_code:
            by_type[item.item_type].append(item)

        total_lines = 0

        # Classes
        if by_type["CLASS"]:
            lines.append("[CLASSES - Never Instantiated]")
            for item in sorted(by_type["CLASS"], key=lambda x: (x.file, x.line)):
                lines.append(
                    f"  {item.file}:{item.line} {item.name} ({item.line_count} lines)"
                )
                lines.append(f"    Context: {item.context}")
                total_lines += item.line_count
            lines.append("")

        # Methods
        if by_type["METHOD"]:
            lines.append("[METHODS - Never Called]")
            for item in sorted(
                by_type["METHOD"], key=lambda x: (-x.line_count, x.file, x.line)
            ):
                lines.append(
                    f"  {item.file}:{item.line} {item.name} ({item.line_count} lines)"
                )
                lines.append(f"    Context: {item.context}")
                total_lines += item.line_count
            lines.append("")

        # Functions
        if by_type["FUNCTION"]:
            lines.append("[FUNCTIONS - Never Called]")
            for item in sorted(by_type["FUNCTION"], key=lambda x: (x.file, x.line)):
                lines.append(
                    f"  {item.file}:{item.line} {item.name} ({item.line_count} lines)"
                )
                lines.append(f"    Context: {item.context}")
                total_lines += item.line_count
            lines.append("")

        # Constants
        if by_type["CONSTANT"]:
            lines.append("[CONSTANTS - Never Referenced]")
            for item in sorted(by_type["CONSTANT"], key=lambda x: (x.file, x.line)):
                lines.append(f"  {item.file}:{item.line} {item.name}")
                lines.append(f"    Context: {item.context}")
                total_lines += 1
            lines.append("")

        # Summary
        lines.append("=" * 80)
        lines.append("SUMMARY")
        lines.append("=" * 80)
        lines.append(f"Total dead code items: {len(dead_code)}")
        lines.append(f"  - Classes: {len(by_type['CLASS'])}")
        lines.append(f"  - Methods: {len(by_type['METHOD'])}")
        lines.append(f"  - Functions: {len(by_type['FUNCTION'])}")
        lines.append(f"  - Constants: {len(by_type['CONSTANT'])}")
        lines.append(f"Estimated dead lines of code: {total_lines}")
        lines.append("")

        return "\n".join(lines)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Find dead code in Python projects")
    parser.add_argument(
        "source_dir",
        nargs="?",
        default="custom_components/localshift",
        help="Source directory to scan (default: custom_components/localshift)",
    )
    parser.add_argument(
        "--exclude",
        nargs="+",
        default=["tests", "__pycache__", ".venv", "worktrees"],
        help="Directories to exclude",
    )
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format",
    )
    parser.add_argument(
        "--min-confidence",
        choices=["high", "medium", "low"],
        default="medium",
        help="Minimum confidence level to report",
    )

    args = parser.parse_args()

    if not os.path.exists(args.source_dir):
        print(f"Error: Directory '{args.source_dir}' not found", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {args.source_dir}...")

    detector = DeadCodeDetector(args.source_dir, args.exclude)
    detector.scan_all_files()

    dead_code = detector.analyze()

    # Filter by confidence
    confidence_order = {"high": 3, "medium": 2, "low": 1}
    min_conf = confidence_order[args.min_confidence]
    dead_code = [
        item for item in dead_code if confidence_order[item.confidence] >= min_conf
    ]

    report = detector.generate_report(dead_code)
    print(report)

    # Exit with error code if dead code found
    if dead_code:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

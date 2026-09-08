"""Structural guard: no closed-issue diagnostic tags at INFO/WARNING.

Regression gate for #976 (leftover per-cycle INFO diagnostic logging).

Ad-hoc diagnostics tagged ``ISSUE_<n>`` are meant to be temporary: they are
added while chasing a specific bug and should be removed (or demoted to
DEBUG) once the issue closes. #500 closed on 2026-03-04 but its
``ISSUE_500`` tagged ``_LOGGER.info(...)`` calls kept firing every cycle
indefinitely, adding noise nobody was reading.

Behavioural tests cannot catch this class of drift: a test that exercises
the code path proves the log line still fires, not that it should still
fire at INFO. So this check is static — it walks the source of every module
under ``custom_components/localshift``, and fails if any ``_LOGGER.info(``
or ``_LOGGER.warning(`` call's format string contains an ``ISSUE_<digits>``
tag. DEBUG-level ``ISSUE_*`` tags remain allowed — the point is to keep
issue-scoped diagnostics off levels a user actually sees in normal
operation.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOCALSHIFT_DIR = _REPO_ROOT / "custom_components" / "localshift"

_ISSUE_TAG_RE = re.compile(r"ISSUE_\d+")

_GATED_METHODS = {"info", "warning"}


def _iter_python_files(root: Path):
    yield from sorted(root.rglob("*.py"))


def _logger_call_attr(node: ast.Call) -> str | None:
    """Return the attribute name (e.g. 'info') if this call is `<name ending in
    LOGGER/logger>.<attr>(...)`, else None."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr not in _GATED_METHODS:
        return None
    target = func.value
    if not isinstance(target, ast.Name):
        return None
    if not (target.id.endswith("LOGGER") or target.id.endswith("logger")):
        return None
    return func.attr


def _string_parts(node: ast.expr) -> list[str]:
    """Collect every string-literal fragment reachable from a call argument.

    Covers plain ``ast.Constant`` strings (implicit string concatenation is
    already folded into one Constant by the parser) and f-string literal
    fragments inside ``ast.JoinedStr``.
    """
    parts: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            parts.append(sub.value)
    return parts


def _violations_in_file(path: Path) -> list[tuple[int, str, str]]:
    """Return (lineno, level, text) for every offending log call in `path`."""
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        level = _logger_call_attr(node)
        if level is None:
            continue
        if not node.args:
            continue
        # The format-string argument is always the first positional arg.
        text_parts = _string_parts(node.args[0])
        combined = " ".join(text_parts)
        if _ISSUE_TAG_RE.search(combined):
            violations.append((node.lineno, level, combined))
    return violations


def test_no_issue_tagged_diagnostics_at_info_or_warning():
    """No _LOGGER.info()/_LOGGER.warning() call may carry an ISSUE_<n> tag."""
    assert _LOCALSHIFT_DIR.is_dir(), f"missing source dir: {_LOCALSHIFT_DIR}"

    files = list(_iter_python_files(_LOCALSHIFT_DIR))
    assert files, f"expected to find .py files under {_LOCALSHIFT_DIR}"

    all_violations: list[str] = []
    scanned_logger_calls = 0

    for path in files:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _logger_call_attr(node) is not None:
                scanned_logger_calls += 1
        for lineno, level, text in _violations_in_file(path):
            rel = path.relative_to(_REPO_ROOT)
            all_violations.append(f"{rel}:{lineno} _LOGGER.{level}(...) -> {text!r}")

    # Guard against a broken walk silently passing: the integration has
    # plenty of _LOGGER.info()/_LOGGER.warning() calls, so finding zero
    # means the scan itself is broken, not that the codebase is clean.
    assert scanned_logger_calls > 0, (
        "scanned zero _LOGGER.info()/_LOGGER.warning() calls — "
        "the AST walk is broken, not the codebase"
    )

    assert not all_violations, (
        "found ISSUE_<n> tagged diagnostics logged at INFO/WARNING "
        "(demote to DEBUG or delete once the issue closes):\n"
        + "\n".join(all_violations)
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

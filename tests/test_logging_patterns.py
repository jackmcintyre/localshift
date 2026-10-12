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

``_LOGGER.log(<level>, "...")`` is gated too (issue #1035): the format string
is its second argument, and the call is treated as INFO/WARNING-capable unless
the level is the literal ``logging.DEBUG``. A level held in a variable can be
INFO at runtime, which is exactly how the integration uses ``.log()``.
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

# ``_LOGGER.log(level, fmt, ...)`` takes the level as its first argument.
_LEVEL_ARG_METHOD = "log"


def _iter_python_files(root: Path):
    yield from sorted(root.rglob("*.py"))


def _is_literal_debug_level(node: ast.expr) -> bool:
    """True for ``logging.DEBUG`` / a bare ``DEBUG`` name."""
    if isinstance(node, ast.Attribute):
        return node.attr == "DEBUG"
    return isinstance(node, ast.Name) and node.id == "DEBUG"


def _gated_format_arg(node: ast.Call) -> tuple[str, ast.expr | None] | None:
    """Return ``(method, format-string argument)`` for a gated logger call.

    Gated calls are ``<name ending in LOGGER/logger>.info(...)``,
    ``.warning(...)`` and ``.log(<level>, ...)`` where the level is not the
    literal DEBUG. Returns None for anything else. The format argument is the
    first positional for ``.info``/``.warning`` and the second for ``.log``;
    it is None when the call does not have one.
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr not in _GATED_METHODS and func.attr != _LEVEL_ARG_METHOD:
        return None
    target = func.value
    if not isinstance(target, ast.Name):
        return None
    if not (target.id.endswith("LOGGER") or target.id.endswith("logger")):
        return None
    if func.attr == _LEVEL_ARG_METHOD:
        if node.args and _is_literal_debug_level(node.args[0]):
            return None
        return func.attr, node.args[1] if len(node.args) > 1 else None
    return func.attr, node.args[0] if node.args else None


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


def _violations_in_source(source: str, filename: str) -> list[tuple[int, str, str]]:
    """Return (lineno, method, text) for every offending log call in `source`."""
    tree = ast.parse(source, filename=filename)
    violations: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        gated = _gated_format_arg(node)
        if gated is None:
            continue
        method, format_arg = gated
        if format_arg is None:
            continue
        combined = " ".join(_string_parts(format_arg))
        if _ISSUE_TAG_RE.search(combined):
            violations.append((node.lineno, method, combined))
    return violations


def _violations_in_file(path: Path) -> list[tuple[int, str, str]]:
    """Return (lineno, method, text) for every offending log call in `path`."""
    return _violations_in_source(path.read_text(), str(path))


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
            if isinstance(node, ast.Call) and _gated_format_arg(node) is not None:
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


def test_gate_sees_logger_log_with_a_non_debug_level():
    """`_LOGGER.log(level, "ISSUE_n ...")` must not bypass the gate (#1035)."""
    source = (
        "import logging\n"
        "_LOGGER = logging.getLogger(__name__)\n"
        "def f(x, level):\n"
        '    _LOGGER.log(logging.INFO, "ISSUE_888 fixed level %s", x)\n'
        '    _LOGGER.log(level, "ISSUE_889 variable level %s", x)\n'
        '    _LOGGER.log(logging.DEBUG, "ISSUE_890 debug is allowed %s", x)\n'
        '    _LOGGER.log(level, "tag later in the format ISSUE_891 %s", x)\n'
        '    _LOGGER.log(level, "untagged %s", "ISSUE_892 is a value, not the format")\n'
    )
    found = _violations_in_source(source, "probe.py")
    assert [(line, method) for line, method, _ in found] == [
        (4, "log"),
        (5, "log"),
        (7, "log"),
    ]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

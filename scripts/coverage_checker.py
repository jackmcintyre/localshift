"""Coverage checker for TDD pre-commit hook.

Parses pytest-cov JSON output and generates detailed failure messages
when coverage is below the 95% threshold.

The hook uses ``--changed-lines`` mode (issue #1212): 95% of the executable
lines a commit adds or modifies must be covered. The older whole-file mode
(``parse_coverage_json``) is kept for callers that still want it, but the hook
no longer uses it: large modules exercised by many test files can never reach
95% from their name-mapped test file alone.
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

THRESHOLD = 95.0


@dataclass
class CoverageFailure:
    file_path: str
    coverage_pct: float
    uncovered_lines: list[int]
    test_file: str | None = None
    # Changed-lines mode only: how many changed lines were executable.
    changed_executable: int = 0

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "coverage_pct": self.coverage_pct,
            "uncovered_lines": self.uncovered_lines,
            "test_file": self.test_file,
            "changed_executable": self.changed_executable,
        }


@dataclass
class CoverageCheckResult:
    passed: bool
    failures: list[CoverageFailure] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "failures": [f.to_dict() for f in self.failures],
        }


def find_test_file(source_file: str) -> str | None:
    module_name = Path(source_file).stem

    test_flat = f"tests/test_{module_name}.py"
    if Path(test_flat).exists():
        return test_flat

    rel_path = source_file.replace("custom_components/localshift/", "")
    if "/" in rel_path:
        subdir = Path(rel_path).parent
        test_subdir = f"tests/{subdir}/test_{module_name}.py"
        if Path(test_subdir).exists():
            return test_subdir

    return None


def parse_coverage_json(
    json_path: str, staged_files: list[str], threshold: float = 95.0
) -> CoverageCheckResult:
    with open(json_path) as f:
        data = json.load(f)

    failures = []

    for file_path in staged_files:
        if file_path.endswith("__init__.py"):
            continue

        if file_path not in data.get("files", {}):
            continue

        file_data = data["files"][file_path]
        summary = file_data.get("summary", {})
        pct = summary.get("percent_covered", 0.0)

        if pct < threshold:
            executed = set(file_data.get("executed_lines", []))
            all_lines = (
                set(
                    range(
                        min(
                            file_data.get("executed_lines", [1])
                            + file_data.get("missing_lines", [1])
                        ),
                        max(
                            file_data.get("executed_lines", [1])
                            + file_data.get("missing_lines", [1])
                        )
                        + 1,
                    )
                )
                if file_data.get("executed_lines") or file_data.get("missing_lines")
                else set()
            )

            uncovered = sorted(all_lines - executed)

            test_file = find_test_file(file_path)

            failures.append(
                CoverageFailure(
                    file_path=file_path,
                    coverage_pct=pct,
                    uncovered_lines=uncovered,
                    test_file=test_file,
                )
            )

    return CoverageCheckResult(
        passed=len(failures) == 0,
        failures=failures,
    )


def format_failure_report(result: CoverageCheckResult) -> str:
    if result.passed:
        return "✅ All modified files have 95%+ coverage"

    lines = [
        "┌" + "─" * 70 + "┐",
        f"│ ❌ COVERAGE FAILURES - {len(result.failures)} file(s) below 95% threshold"
        + " " * (70 - 44 - len(str(len(result.failures))) - 25)
        + "│",
        "├" + "─" * 70 + "┤",
    ]

    for failure in result.failures:
        file_display = failure.file_path
        if len(file_display) > 60:
            file_display = "..." + file_display[-57:]

        lines.append(
            f"│ File: {file_display}" + " " * (70 - 8 - len(file_display) - 1) + "│"
        )
        lines.append(
            f"│ Coverage: {failure.coverage_pct:.1f}% (need 95%)"
            + " " * (70 - 13 - len(f"{failure.coverage_pct:.1f}") - 15)
            + "│"
        )

        if failure.uncovered_lines:
            ranges = _format_line_ranges(failure.uncovered_lines)
            for i, r in enumerate(ranges[:3]):
                prefix = "Uncovered: " if i == 0 else "            "
                lines.append(
                    f"│ {prefix}{r}" + " " * (70 - 2 - len(prefix) - len(r) - 1) + "│"
                )
            if len(ranges) > 3:
                lines.append(
                    f"│            ... and {len(ranges) - 3} more ranges"
                    + " " * 30
                    + "│"
                )

        if failure.test_file:
            lines.append(
                f"│ Test file: {failure.test_file}"
                + " " * (70 - 13 - len(failure.test_file) - 1)
                + "│"
            )

        lines.append("├" + "─" * 70 + "┤")

    test_files = [f.test_file for f in result.failures if f.test_file]
    cov_modules = [
        f.file_path.replace(".py", "").replace("/", ".") for f in result.failures
    ]

    lines.append("│ Run this to see detailed coverage:" + " " * 34 + "│")
    cmd = f"uv run pytest {' '.join(test_files[:2])}"
    if len(test_files) > 2:
        cmd += " ..."
    lines.append(f"│   {cmd}" + " " * (70 - 5 - len(cmd) - 1) + "│")
    lines.append(
        "│     --cov=" + " --cov=".join(cov_modules[:2]) + " \\" + " " * 30 + "│"
    )
    lines.append("│     --cov-report=term-missing -v" + " " * 35 + "│")
    lines.append("└" + "─" * 70 + "┘")
    lines.append("")
    lines.append("TDD Workflow: .agents/rules/tdd-workflow.md")

    return "\n".join(lines)


def _format_line_ranges(lines: list[int]) -> list[str]:
    if not lines:
        return []

    ranges = []
    start = lines[0]
    end = lines[0]

    for i in range(1, len(lines)):
        if lines[i] == end + 1:
            end = lines[i]
        else:
            if start == end:
                ranges.append(f"L{start}")
            else:
                ranges.append(f"L{start}-{end}")
            start = lines[i]
            end = lines[i]

    if start == end:
        ranges.append(f"L{start}")
    else:
        ranges.append(f"L{start}-{end}")

    return ranges


# --- Changed-line coverage (issue #1212) ------------------------------------

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_added_lines(diff_text: str) -> dict[str, set[int]]:
    """Map each file in a ``git diff -U0`` to the new-side line numbers it adds.

    Modified lines show up as a deletion plus an addition, so they are counted.
    """
    added: dict[str, set[int]] = {}
    current: set[int] | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:]
            if path == "/dev/null":
                current = None
                continue
            path = path.removeprefix("b/")
            current = added.setdefault(path, set())
            continue
        match = _HUNK.match(line)
        if match and current is not None:
            start = int(match.group(1))
            count = 1 if match.group(2) is None else int(match.group(2))
            current.update(range(start, start + count))
    return added


class UnstagedEditsError(Exception):
    """A staged source file also has unstaged edits, so it cannot be measured."""

    def __init__(self, paths: list[str]):
        self.paths = paths
        super().__init__(
            "\n".join(f"stage or stash the remaining edits in {path}" for path in paths)
        )


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=check
    )


def staged_changed_lines(
    files: list[str], root: Path = Path(".")
) -> dict[str, set[int]]:
    """Lines each staged file adds or modifies, as numbered in the index.

    Coverage is measured on the working tree, so the numbers only match the
    coverage data when the working tree holds exactly what is staged. A staged
    file that also has unstaged edits is refused (``UnstagedEditsError``):
    measuring it would credit lines the commit does not contain.

    Renames are detected, so a moved module counts only the lines edited in it.
    """
    dirty = [
        path
        for path in files
        if _git(root, "diff", "--quiet", "--", path, check=False).returncode != 0
    ]
    if dirty:
        raise UnstagedEditsError(dirty)
    diff = _git(
        root,
        "diff",
        "--cached",
        "-M",
        "-U0",
        "--no-color",
        "--no-ext-diff",
        "--src-prefix=a/",
        "--dst-prefix=b/",
    ).stdout
    added = parse_added_lines(diff)
    return {path: added.get(path, set()) for path in files}


def _exclude_regex(root: Path) -> str | None:
    """The project's coverage exclusion regex (pragma: no cover and friends)."""
    try:
        import coverage

        config = root / "pyproject.toml"
        cov = coverage.Coverage(config_file=str(config) if config.exists() else False)
        patterns = cov.config.exclude_list
    except Exception:
        return None
    return "|".join(f"(?:{p})" for p in patterns) or None


def _parse_source(path: Path, root: Path):
    """coverage.py's parser for a source file, or None if it cannot be parsed."""
    try:
        from coverage.parser import PythonParser

        parser = PythonParser(text=path.read_text(), exclude=_exclude_regex(root))
        parser.parse_source()
    except Exception:
        return None
    return parser


def check_changed_line_coverage(
    coverage_data: dict,
    changed: dict[str, set[int]],
    threshold: float = THRESHOLD,
    root: Path = Path("."),
) -> CoverageCheckResult:
    """Fail any file whose changed executable lines are under ``threshold``% hit.

    A changed continuation line counts as the statement it belongs to. Lines
    that are not executable (blank, comment, docstring, excluded) do not count.
    A file missing from the coverage data was never imported by the tests, so
    its changed statements all count as uncovered (every changed line, if the
    file cannot be parsed either).
    """
    failures = []
    files = coverage_data.get("files", {})
    for file_path, lines in changed.items():
        if file_path.endswith("__init__.py") or not lines:
            continue
        parser = _parse_source(root / file_path, root)
        if parser is not None:
            lines = {parser.first_line(n) for n in lines}

        if file_path in files:
            executed = set(files[file_path].get("executed_lines", []))
            missing = set(files[file_path].get("missing_lines", []))
            statements = executed | missing
        elif parser is not None:
            executed = set()
            statements = set(parser.statements)
        else:
            # Neither imported nor parseable: no way to tell which changed
            # lines are executable, so fail closed and count all of them.
            executed = set()
            statements = set(lines)

        executable = lines & statements
        if not executable:
            continue
        hit = executable & executed
        pct = 100.0 * len(hit) / len(executable)
        if pct < threshold:
            failures.append(
                CoverageFailure(
                    file_path=file_path,
                    coverage_pct=pct,
                    uncovered_lines=sorted(executable - hit),
                    test_file=find_test_file(file_path),
                    changed_executable=len(executable),
                )
            )
    return CoverageCheckResult(passed=not failures, failures=failures)


def format_changed_lines_report(result: CoverageCheckResult) -> str:
    if result.passed:
        return f"✅ Changed lines meet the {THRESHOLD:.0f}% coverage threshold"

    lines = [
        f"❌ CHANGED-LINE COVERAGE BELOW {THRESHOLD:.0f}% in "
        f"{len(result.failures)} file(s)",
        "",
    ]
    for failure in result.failures:
        covered = failure.changed_executable - len(failure.uncovered_lines)
        lines.append(f"  File: {failure.file_path}")
        lines.append(
            f"    {failure.coverage_pct:.1f}% of changed executable lines covered "
            f"({covered}/{failure.changed_executable})"
        )
        ranges = _format_line_ranges(failure.uncovered_lines)
        lines.append(f"    Uncovered changed lines: {', '.join(ranges)}")
        if failure.test_file:
            lines.append(f"    Test file: {failure.test_file}")
        lines.append("")
    lines.append("Write tests that execute those lines, then commit again.")
    lines.append("TDD Workflow: .agents/rules/tdd-workflow.md")
    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    changed_mode = bool(args) and args[0] == "--changed-lines"
    if changed_mode:
        args = args[1:]
    if len(args) < 2:
        print(
            "Usage: coverage_checker.py [--changed-lines] <coverage.json> "
            "<file1> [file2 ...]",
            file=sys.stderr,
        )
        sys.exit(1)

    json_path, staged_files = args[0], args[1:]

    if changed_mode:
        with open(json_path) as f:
            data = json.load(f)
        try:
            changed = staged_changed_lines(staged_files)
        except UnstagedEditsError as err:
            print("❌ Staged files also have unstaged edits; coverage would be")
            print("   measured on code this commit does not contain.")
            print(str(err))
            sys.exit(1)
        result = check_changed_line_coverage(data, changed)
        print(format_changed_lines_report(result))
    else:
        result = parse_coverage_json(json_path, staged_files)
        print(format_failure_report(result))

    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()

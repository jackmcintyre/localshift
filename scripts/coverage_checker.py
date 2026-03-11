"""Coverage checker for TDD pre-commit hook.

Parses pytest-cov JSON output and generates detailed failure messages
when coverage is below the 95% threshold for modified files.
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CoverageFailure:
    file_path: str
    coverage_pct: float
    uncovered_lines: list[int]
    test_file: str | None = None

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "coverage_pct": self.coverage_pct,
            "uncovered_lines": self.uncovered_lines,
            "test_file": self.test_file,
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


def main():
    if len(sys.argv) < 3:
        print(
            "Usage: coverage_checker.py <coverage.json> <file1> [file2 ...]",
            file=sys.stderr,
        )
        sys.exit(1)

    json_path = sys.argv[1]
    staged_files = sys.argv[2:]

    result = parse_coverage_json(json_path, staged_files)

    print(format_failure_report(result))

    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()

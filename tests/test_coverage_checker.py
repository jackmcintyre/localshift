"""Unit tests for coverage_checker module.

Tests the Python coverage checking logic used by the TDD pre-commit hook.
"""

import json
from pathlib import Path

from scripts.coverage_checker import (
    CoverageCheckResult,
    CoverageFailure,
    _format_line_ranges,
    find_test_file,
    format_failure_report,
    parse_coverage_json,
)


class TestFormatLineRanges:
    """Test line range formatting for uncovered lines."""

    def test_single_line(self):
        assert _format_line_ranges([42]) == ["L42"]

    def test_consecutive_lines(self):
        assert _format_line_ranges([1, 2, 3, 4, 5]) == ["L1-5"]

    def test_multiple_ranges(self):
        assert _format_line_ranges([1, 2, 3, 10, 11, 20]) == ["L1-3", "L10-11", "L20"]

    def test_empty_list(self):
        assert _format_line_ranges([]) == []

    def test_single_gap(self):
        assert _format_line_ranges([1, 2, 3, 5, 6, 7]) == ["L1-3", "L5-7"]


class TestFindTestFile:
    """Test test file discovery for source files."""

    def test_finds_flat_test_file(self, tmp_path: Path, monkeypatch):
        source = "custom_components/localshift/optimizer.py"
        test_file = tmp_path / "tests" / "test_optimizer.py"
        test_file.parent.mkdir(parents=True)
        test_file.touch()

        monkeypatch.chdir(tmp_path)
        result = find_test_file(source)
        assert result == "tests/test_optimizer.py"

    def test_finds_subdir_test_file(self, tmp_path: Path, monkeypatch):
        source = "custom_components/localshift/engine/optimizer.py"
        test_file = tmp_path / "tests" / "engine" / "test_optimizer.py"
        test_file.parent.mkdir(parents=True)
        test_file.touch()

        monkeypatch.chdir(tmp_path)
        result = find_test_file(source)
        assert result == "tests/engine/test_optimizer.py"

    def test_returns_none_when_not_found(self, tmp_path: Path, monkeypatch):
        source = "custom_components/localshift/nonexistent.py"
        monkeypatch.chdir(tmp_path)
        result = find_test_file(source)
        assert result is None

    def test_skips_init_file(self):
        result = find_test_file("custom_components/localshift/__init__.py")
        assert result is None or "test_" in (result or "")


class TestParseCoverageJson:
    """Test coverage JSON parsing and threshold checking."""

    def test_passes_when_above_threshold(self, tmp_path: Path):
        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text(
            json.dumps({
                "meta": {"version": "1.0"},
                "files": {
                    "custom_components/localshift/optimizer.py": {
                        "executed_lines": [1, 2, 3, 4, 5],
                        "missing_lines": [],
                        "summary": {
                            "num_statements": 5,
                            "covered_lines": 5,
                            "percent_covered": 100.0,
                        },
                    }
                },
            })
        )

        staged_files = ["custom_components/localshift/optimizer.py"]
        result = parse_coverage_json(str(coverage_json), staged_files)

        assert result.passed is True
        assert len(result.failures) == 0

    def test_fails_when_below_threshold(self, tmp_path: Path):
        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text(
            json.dumps({
                "meta": {"version": "1.0"},
                "files": {
                    "custom_components/localshift/optimizer.py": {
                        "executed_lines": [1, 2, 3],
                        "missing_lines": [4, 5, 6, 7],
                        "summary": {
                            "num_statements": 10,
                            "covered_lines": 3,
                            "percent_covered": 78.0,
                        },
                    }
                },
            })
        )

        staged_files = ["custom_components/localshift/optimizer.py"]
        result = parse_coverage_json(str(coverage_json), staged_files)

        assert result.passed is False
        assert len(result.failures) == 1
        assert result.failures[0].coverage_pct == 78.0

    def test_shows_uncovered_lines(self, tmp_path: Path):
        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text(
            json.dumps({
                "meta": {"version": "1.0"},
                "files": {
                    "custom_components/localshift/optimizer.py": {
                        "executed_lines": [1, 2, 5, 8, 9, 10],
                        "missing_lines": [3, 4, 6, 7],
                        "summary": {
                            "num_statements": 10,
                            "covered_lines": 6,
                            "percent_covered": 60.0,
                        },
                    }
                },
            })
        )

        staged_files = ["custom_components/localshift/optimizer.py"]
        result = parse_coverage_json(str(coverage_json), staged_files)

        assert result.passed is False
        uncovered = result.failures[0].uncovered_lines
        assert 3 in uncovered
        assert 4 in uncovered
        assert 6 in uncovered
        assert 7 in uncovered

    def test_checks_per_file_not_project_wide(self, tmp_path: Path):
        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text(
            json.dumps({
                "meta": {"version": "1.0"},
                "files": {
                    "custom_components/localshift/good.py": {
                        "executed_lines": [1, 2, 3, 4, 5],
                        "missing_lines": [],
                        "summary": {
                            "num_statements": 5,
                            "covered_lines": 5,
                            "percent_covered": 100.0,
                        },
                    },
                    "custom_components/localshift/bad.py": {
                        "executed_lines": [1],
                        "missing_lines": [2, 3, 4, 5],
                        "summary": {
                            "num_statements": 10,
                            "covered_lines": 1,
                            "percent_covered": 78.0,
                        },
                    },
                },
            })
        )

        staged_files = [
            "custom_components/localshift/good.py",
            "custom_components/localshift/bad.py",
        ]
        result = parse_coverage_json(str(coverage_json), staged_files)

        assert result.passed is False
        assert len(result.failures) == 1
        assert result.failures[0].file_path == "custom_components/localshift/bad.py"

    def test_skips_init_files(self, tmp_path: Path):
        coverage_json = tmp_path / "coverage.json"
        coverage_json.write_text(
            json.dumps({
                "meta": {"version": "1.0"},
                "files": {
                    "custom_components/localshift/__init__.py": {
                        "executed_lines": [1],
                        "missing_lines": [2, 3, 4, 5],
                        "summary": {
                            "num_statements": 5,
                            "covered_lines": 1,
                            "percent_covered": 20.0,
                        },
                    }
                },
            })
        )

        staged_files = ["custom_components/localshift/__init__.py"]
        result = parse_coverage_json(str(coverage_json), staged_files)

        assert result.passed is True


class TestFormatFailureReport:
    """Test failure report formatting."""

    def test_shows_specific_file_coverage(self):
        result = CoverageCheckResult(
            passed=False,
            failures=[
                CoverageFailure(
                    file_path="custom_components/localshift/optimizer.py",
                    coverage_pct=78.3,
                    uncovered_lines=[45, 46, 47, 48, 49, 50],
                )
            ],
        )

        report = format_failure_report(result)

        assert "optimizer.py" in report
        assert "78.3%" in report
        assert "95%" in report

    def test_shows_uncovered_lines(self):
        result = CoverageCheckResult(
            passed=False,
            failures=[
                CoverageFailure(
                    file_path="custom_components/localshift/foo.py",
                    coverage_pct=80.0,
                    uncovered_lines=[10, 11, 12, 20, 21],
                )
            ],
        )

        report = format_failure_report(result)

        assert "L10-12" in report
        assert "L20-21" in report

    def test_shows_test_file_location(self):
        result = CoverageCheckResult(
            passed=False,
            failures=[
                CoverageFailure(
                    file_path="custom_components/localshift/optimizer.py",
                    coverage_pct=80.0,
                    uncovered_lines=[1],
                    test_file="tests/test_optimizer.py",
                )
            ],
        )

        report = format_failure_report(result)

        assert "tests/test_optimizer.py" in report
        assert "Test file:" in report

    def test_shows_remediation_command(self):
        result = CoverageCheckResult(
            passed=False,
            failures=[
                CoverageFailure(
                    file_path="custom_components/localshift/optimizer.py",
                    coverage_pct=80.0,
                    uncovered_lines=[1],
                    test_file="tests/test_optimizer.py",
                )
            ],
        )

        report = format_failure_report(result)

        assert "uv run pytest" in report
        assert "--cov-report=term-missing" in report

    def test_shows_multiple_failures(self):
        result = CoverageCheckResult(
            passed=False,
            failures=[
                CoverageFailure(
                    file_path="custom_components/localshift/foo.py",
                    coverage_pct=78.0,
                    uncovered_lines=[1],
                    test_file="tests/test_foo.py",
                ),
                CoverageFailure(
                    file_path="custom_components/localshift/bar.py",
                    coverage_pct=85.0,
                    uncovered_lines=[5, 6],
                    test_file="tests/test_bar.py",
                ),
            ],
        )

        report = format_failure_report(result)

        assert "2 file" in report
        assert "foo.py" in report
        assert "bar.py" in report

    def test_success_message_when_passed(self):
        result = CoverageCheckResult(passed=True, failures=[])
        report = format_failure_report(result)

        assert "✅" in report
        assert "95%+" in report

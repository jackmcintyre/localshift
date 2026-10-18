"""Unit tests for coverage_checker module.

Tests the Python coverage checking logic used by the TDD pre-commit hook.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.coverage_checker import (
    CoverageCheckResult,
    CoverageFailure,
    UnstagedEditsError,
    _format_line_ranges,
    check_changed_line_coverage,
    find_test_file,
    format_changed_lines_report,
    format_failure_report,
    parse_added_lines,
    parse_coverage_json,
    staged_changed_lines,
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


# --- Changed-line coverage (issue #1212) ------------------------------------
#
# The hook used to demand 95% of the WHOLE touched file from the name-mapped
# tests only, which large modules (optimizer_facade.py, machine.py) can never
# meet. It now demands 95% of the CHANGED executable lines.

MOD = "custom_components/localshift/mod.py"

BASE_SOURCE = '''"""Module docstring."""


def covered(a):
    """Docstring."""
    return a + 1
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo with one committed source module."""
    _git(tmp_path, "init", "-q", "-b", "work")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    src = tmp_path / MOD
    src.parent.mkdir(parents=True)
    src.write_text(BASE_SOURCE)
    _git(tmp_path, "add", MOD)
    _git(tmp_path, "commit", "-q", "-m", "base", "--no-verify")
    return tmp_path


def _cov(executed: list[int], missing: list[int]) -> dict:
    return {"files": {MOD: {"executed_lines": executed, "missing_lines": missing}}}


class TestParseAddedLines:
    def test_parses_hunks_per_file(self):
        diff = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -3,0 +4,2 @@ def f():\n"
            "+    a = 1\n"
            "+    b = 2\n"
            "@@ -10 +12 @@\n"
            "-old\n"
            "+new\n"
            "diff --git a/y.py b/y.py\n"
            "--- /dev/null\n"
            "+++ b/y.py\n"
            "@@ -0,0 +1,3 @@\n"
            "+1\n+2\n+3\n"
        )
        assert parse_added_lines(diff) == {"x.py": {4, 5, 12}, "y.py": {1, 2, 3}}

    def test_pure_deletion_adds_nothing(self):
        diff = "+++ b/x.py\n@@ -4,2 +3,0 @@\n-a\n-b\n"
        assert parse_added_lines(diff) == {"x.py": set()}


class TestCheckChangedLineCoverage:
    def test_covered_change_passes_even_if_file_is_mostly_uncovered(
        self, tmp_path: Path
    ):
        # Whole file 2/10 covered, but the two changed lines are covered.
        data = _cov(executed=[4, 6], missing=[10, 11, 12, 13, 14, 15, 16, 17])
        result = check_changed_line_coverage(data, {MOD: {4, 6}}, root=tmp_path)
        assert result.passed is True

    def test_uncovered_change_fails_with_its_lines(self, tmp_path: Path):
        data = _cov(executed=[4, 6], missing=[9, 10])
        result = check_changed_line_coverage(data, {MOD: {6, 9, 10}}, root=tmp_path)
        assert result.passed is False
        failure = result.failures[0]
        assert failure.file_path == MOD
        assert failure.uncovered_lines == [9, 10]
        assert failure.changed_executable == 3
        assert round(failure.coverage_pct, 1) == 33.3

    def test_non_executable_changed_lines_do_not_count(self, tmp_path: Path):
        # Lines 1, 2, 5 (docstring, blank, docstring) are neither executed nor
        # missing: a docs/comment-only edit has nothing to cover.
        data = _cov(executed=[4, 6], missing=[9])
        result = check_changed_line_coverage(data, {MOD: {1, 2, 5}}, root=tmp_path)
        assert result.passed is True

    def test_continuation_line_maps_to_its_statement(self, tmp_path: Path):
        src = tmp_path / MOD
        src.parent.mkdir(parents=True)
        src.write_text("def f(a):\n    return g(\n        a,\n    )\n")
        # Line 3 is a continuation of the statement on line 2, which is missed.
        data = _cov(executed=[1], missing=[2])
        result = check_changed_line_coverage(data, {MOD: {3}}, root=tmp_path)
        assert result.passed is False
        assert result.failures[0].uncovered_lines == [2]

    def test_file_absent_from_coverage_counts_as_uncovered(self, tmp_path: Path):
        src = tmp_path / MOD
        src.parent.mkdir(parents=True)
        src.write_text("X = 1\n\n\ndef f():\n    return X\n")
        result = check_changed_line_coverage(
            {"files": {}}, {MOD: {1, 4, 5}}, root=tmp_path
        )
        assert result.passed is False
        assert result.failures[0].uncovered_lines == [1, 4, 5]

    def test_unparseable_file_absent_from_coverage_fails_closed(self, tmp_path: Path):
        # Review fix: with no coverage data and no parse there is no way to
        # tell which changed lines are executable, so all of them count.
        src = tmp_path / MOD
        src.parent.mkdir(parents=True)
        src.write_text("def f(:\n    return 1\n")
        result = check_changed_line_coverage(
            {"files": {}}, {MOD: {1, 2}}, root=tmp_path
        )
        assert result.passed is False
        assert result.failures[0].uncovered_lines == [1, 2]
        assert result.failures[0].changed_executable == 2

    def test_threshold_is_95_percent_of_changed_lines(self, tmp_path: Path):
        executed = list(range(1, 20))  # 19 covered
        data = _cov(executed=executed, missing=[20])
        changed = {MOD: set(range(1, 21))}  # 19/20 = 95% -> pass
        assert check_changed_line_coverage(data, changed, root=tmp_path).passed
        data = _cov(executed=executed[:-1], missing=[19, 20])  # 18/20 = 90%
        assert not check_changed_line_coverage(data, changed, root=tmp_path).passed

    def test_report_names_file_and_changed_lines(self, tmp_path: Path):
        data = _cov(executed=[4], missing=[9, 10])
        result = check_changed_line_coverage(data, {MOD: {4, 9, 10}}, root=tmp_path)
        report = format_changed_lines_report(result)
        assert MOD in report
        assert "L9-10" in report
        assert "changed" in report.lower()


class TestStagedChangedLines:
    def test_reports_staged_added_lines(self, repo: Path):
        src = repo / MOD
        src.write_text(BASE_SOURCE + "\n\ndef added(b):\n    return b * 2\n")
        _git(repo, "add", MOD)
        assert staged_changed_lines([MOD], root=repo) == {MOD: {7, 8, 9, 10}}

    def test_staged_file_with_unstaged_edits_is_refused(self, repo: Path):
        # Review fix: coverage runs on the working tree, so a staged file that
        # also has unstaged edits cannot be measured as committed. Refuse it.
        src = repo / MOD
        src.write_text(BASE_SOURCE + "\n\ndef added(b):\n    return b * 2\n")
        _git(repo, "add", MOD)
        src.write_text(BASE_SOURCE)  # staged-then-removed lines
        with pytest.raises(UnstagedEditsError) as excinfo:
            staged_changed_lines([MOD], root=repo)
        assert excinfo.value.paths == [MOD]
        assert f"stage or stash the remaining edits in {MOD}" in str(excinfo.value)

    def test_rename_counts_only_the_edited_lines(self, repo: Path):
        # Review fix: a git mv must not make every line of the moved module
        # count as changed, only the lines actually edited.
        moved = "custom_components/localshift/moved.py"
        _git(repo, "mv", MOD, moved)
        dst = repo / moved
        dst.write_text(dst.read_text() + "\n\ndef added(b):\n    return b * 2\n")
        _git(repo, "add", moved)
        assert staged_changed_lines([moved], root=repo) == {moved: {7, 8, 9, 10}}


HOOK = Path(__file__).resolve().parents[1] / ".githooks" / "scripts" / "check-tdd.sh"
CHECKER = Path(__file__).resolve().parents[1] / "scripts" / "coverage_checker.py"


class TestHookEndToEnd:
    def _setup_hook(self, repo: Path) -> Path:
        hook_dir = repo / ".githooks" / "scripts"
        hook_dir.mkdir(parents=True)
        hook = hook_dir / "check-tdd.sh"
        hook.write_text(HOOK.read_text())
        hook.chmod(0o755)
        (repo / "scripts").mkdir()
        (repo / "scripts" / "coverage_checker.py").write_text(CHECKER.read_text())
        return hook

    def _run_hook(self, repo: Path, hook: Path) -> subprocess.CompletedProcess:
        """Run the hook with `uv run` shimmed to this interpreter."""
        shim_dir = repo.parent / f"{repo.name}-bin"
        shim_dir.mkdir()
        shim = shim_dir / "uv"
        shim.write_text(
            "#!/bin/bash\n"
            '[ "$1" = run ] && shift\n'
            '[ "$1" = python ] && shift\n'
            f'exec "{sys.executable}" "$@"\n'
        )
        shim.chmod(0o755)
        env = {**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"}
        return subprocess.run(
            [str(hook)], cwd=repo, capture_output=True, text=True, check=False, env=env
        )

    def test_rename_with_uncovered_edit_fails(self, repo: Path):
        # Review fix: --diff-filter=ACM dropped renames, so a git mv plus an
        # uncovered edit skipped both the test-file and the coverage checks.
        hook = self._setup_hook(repo)
        moved = "custom_components/localshift/moved.py"
        _git(repo, "mv", MOD, moved)
        dst = repo / moved
        dst.write_text(dst.read_text() + "\n\ndef added(b):\n    return b * 2\n")
        test = repo / "tests" / "test_moved.py"
        test.parent.mkdir()
        test.write_text(
            "from custom_components.localshift.moved import covered\n\n\n"
            "def test_covered():\n    assert covered(1) == 2\n"
        )
        _git(repo, "add", moved, "tests/test_moved.py")
        proc = self._run_hook(repo, hook)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 1, out
        assert "No production code changes" not in out
        assert "CHANGED-LINE COVERAGE BELOW" in out
        # Only the edited lines count: `def added` runs on import, its body
        # does not.
        assert "(1/2)" in out
        assert "Uncovered changed lines: L10" in out

    def test_staged_file_with_unstaged_edits_is_refused(self, repo: Path):
        hook = self._setup_hook(repo)
        src = repo / MOD
        src.write_text(BASE_SOURCE + "\n\ndef added(b):\n    return b * 2\n")
        _git(repo, "add", MOD)
        src.write_text(BASE_SOURCE)
        proc = self._run_hook(repo, hook)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 1, out
        assert f"stage or stash the remaining edits in {MOD}" in out

    def test_docs_only_commit_skips(self, repo: Path):
        hook = self._setup_hook(repo)
        (repo / "README.md").write_text("docs\n")
        _git(repo, "add", "README.md")
        proc = subprocess.run(
            [str(hook)], cwd=repo, capture_output=True, text=True, check=False
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "No production code changes" in proc.stdout

    def test_cli_changed_lines_mode(self, repo: Path):
        src = repo / MOD
        src.write_text(BASE_SOURCE + "\n\ndef added(b):\n    return b * 2\n")
        _git(repo, "add", MOD)
        cov = repo / "cov.json"
        checker = (
            Path(__file__).resolve().parents[1] / "scripts" / "coverage_checker.py"
        )

        def run(executed: list[int], missing: list[int]) -> int:
            cov.write_text(json.dumps(_cov(executed, missing)))
            return subprocess.run(
                [sys.executable, str(checker), "--changed-lines", str(cov), MOD],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            ).returncode

        assert run(executed=[4, 6, 9, 10], missing=[]) == 0
        assert run(executed=[4, 6, 9], missing=[10]) == 1

"""Tests of the swallowed-failure tripwire itself (tests/swallow_tripwire.py).

Two things are held here:

* the tripwire fires, and only when it should. Each case runs a small pytest
  session in-process (``pytester``) with the real fixture and hook from
  ``tests/conftest.py``, against the real ``OptimizerFacade``;
* every tripwire still points at a live log call. The match is on the log
  call's format string, so rewording a production message would otherwise
  disarm its tripwire without a single test noticing.
"""

from __future__ import annotations

import ast
import importlib
import logging
from pathlib import Path

import pytest

from tests import swallow_tripwire

_INNER_CONFTEST = """
from tests.conftest import (  # noqa: F401
    pytest_runtest_call,
    swallowed_failure_tripwire,
)
"""

# The historical failure, reproduced: SlotBuilder loses a constructor argument
# that the facade still passes through its injected ``self._slot_builder_cls``
# reference. The facade catches the TypeError and the cycle produces no plan.
_INNER_PRELUDE = '''
from datetime import UTC, datetime

import pytest

from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.engine.optimizer_facade import OptimizerFacade
from custom_components.localshift.engine.slots import SlotBuilder


def _break_slot_builder(monkeypatch):
    def narrowed_init(self, config_options, ha_timezone):
        """SlotBuilder.__init__ with solar_accuracy_tracker removed."""

    monkeypatch.setattr(SlotBuilder, "__init__", narrowed_init)


def _run_cycle():
    data = CoordinatorData()
    data.soc = 50.0
    OptimizerFacade().run_inline(
        data=data,
        now_dt=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        config_options={},
    )
    return data
'''


@pytest.fixture
def inner(pytester):
    """Run a test module's source under the real tripwire; return the result."""
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makeini(
        "[pytest]\nasyncio_default_fixture_loop_scope = function\nmarkers =\n"
        "    allow_swallowed_failure: see tests/swallow_tripwire.py\n"
    )

    def run(body: str):
        pytester.makepyfile(_INNER_PRELUDE + body)
        return pytester.runpytest_inprocess("-p", "no:cacheprovider")

    return run


class TestTripwireFires:
    def test_broken_optimizer_without_the_marker_fails(self, inner):
        result = inner(
            """
def test_plans(monkeypatch):
    _break_slot_builder(monkeypatch)
    data = _run_cycle()
    # What the suite used to settle for: nothing raised, so nothing failed.
    assert data.optimizer_result is None
"""
        )
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines([
            "*[[]inline_optimizer[]] Inline DP optimizer failed (non-blocking):*"
            "solar_accuracy_tracker*TypeError at*optimizer_facade.py*",
        ])

    def test_broken_optimizer_with_the_marker_passes(self, inner):
        result = inner(
            """
@pytest.mark.allow_swallowed_failure("inline_optimizer")
def test_plans(monkeypatch):
    _break_slot_builder(monkeypatch)
    assert _run_cycle().optimizer_result is None
"""
        )
        result.assert_outcomes(passed=1)

    def test_marker_for_a_different_path_does_not_cover_it(self, inner):
        result = inner(
            """
@pytest.mark.allow_swallowed_failure("shadow_optimizer")
def test_plans(monkeypatch):
    _break_slot_builder(monkeypatch)
    _run_cycle()
"""
        )
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*[[]inline_optimizer[]]*"])

    def test_trip_inside_a_fixture_is_caught(self, inner):
        result = inner(
            """
@pytest.fixture
def planned(monkeypatch):
    _break_slot_builder(monkeypatch)
    return _run_cycle()


def test_plans(planned):
    assert planned.optimizer_result is None
"""
        )
        result.assert_outcomes(failed=1)

    def test_strict_xfail_sees_the_trip_as_the_failure(self, inner):
        result = inner(
            """
@pytest.mark.xfail(strict=True, reason="known swallowed failure")
def test_plans(monkeypatch):
    _break_slot_builder(monkeypatch)
    _run_cycle()
"""
        )
        result.assert_outcomes(xfailed=1)

    def test_a_test_that_already_failed_keeps_its_own_failure(self, inner):
        result = inner(
            """
def test_plans(monkeypatch):
    _break_slot_builder(monkeypatch)
    _run_cycle()
    assert False, "the test's own assertion"
"""
        )
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*the test's own assertion*"])
        result.stdout.no_fnmatch_line("*did not opt in*")


class TestTripwireStaysQuiet:
    def test_healthy_cycle_passes(self, inner):
        result = inner(
            """
def test_no_slots_is_not_a_failure():
    # No forecast at all: the facade logs "no slots available" and returns.
    # That is a handled condition, not a swallowed exception.
    assert _run_cycle().optimizer_result is None
"""
        )
        result.assert_outcomes(passed=1)

    def test_similar_text_from_elsewhere_does_not_trip(self, inner):
        result = inner(
            """
import logging


def test_other_logger_same_words():
    logging.getLogger("somewhere.else").warning(
        "Inline DP optimizer failed (non-blocking): %s", "not the facade"
    )
    print("Inline DP optimizer failed (non-blocking): printed")
"""
        )
        result.assert_outcomes(passed=1)


class TestOptInsCannotRot:
    def test_marker_on_a_test_that_never_trips_fails(self, inner):
        result = inner(
            """
@pytest.mark.allow_swallowed_failure("inline_optimizer")
def test_plans():
    _run_cycle()
"""
        )
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*never reached that path*stale opt-in*"])

    def test_unknown_path_name_fails(self, inner):
        result = inner(
            """
@pytest.mark.allow_swallowed_failure("optimiser")
def test_plans():
    pass
"""
        )
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*unknown path(s) [[]'optimiser'[]]*"])


class TestLoggingIsUnchanged:
    """The probe level must not leak records a test would not otherwise see."""

    FACADE = "custom_components.localshift.engine.optimizer_facade"

    def test_debug_records_stay_hidden_by_default(self, caplog):
        logging.getLogger(self.FACADE).debug("probe-level leak check")
        assert "probe-level leak check" not in caplog.text

    def test_debug_records_appear_when_a_test_asks_for_them(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=self.FACADE):
            logging.getLogger(self.FACADE).debug("asked for by logger")
        with caplog.at_level(logging.DEBUG):
            logging.getLogger(self.FACADE).debug("asked for at the root")
        assert "asked for by logger" in caplog.text
        assert "asked for at the root" in caplog.text

    def test_warnings_still_reach_caplog(self, caplog):
        logging.getLogger(self.FACADE).warning("ordinary warning")
        assert "ordinary warning" in caplog.text


def _logged_format_strings(module_name: str) -> set[str]:
    """Format strings of every ``_LOGGER.<level>(...)`` call in a module.

    A format argument that is a module-level name (a named message constant)
    is resolved to its value.
    """
    module = importlib.import_module(module_name)
    tree = ast.parse(Path(module.__file__).read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "_LOGGER"
        ):
            continue
        # ``_LOGGER.log(level, fmt, ...)`` carries the format second.
        args = node.args[1:] if node.func.attr == "log" else node.args
        if not args:
            continue
        fmt = args[0]
        if isinstance(fmt, ast.Constant) and isinstance(fmt.value, str):
            found.add(fmt.value)
        elif isinstance(fmt, ast.Name):
            value = getattr(module, fmt.id, None)
            if isinstance(value, str):
                found.add(value)
    return found


@pytest.mark.parametrize("wire", swallow_tripwire.TRIPWIRES, ids=lambda wire: wire.key)
def test_tripwire_matches_a_live_log_call(wire):
    assert wire.message in _logged_format_strings(wire.logger), (
        f"Tripwire '{wire.key}' matches the format string {wire.message!r}, but "
        f"no _LOGGER call in {wire.logger} logs it any more. The except block "
        "was reworded, moved or removed: update tests/swallow_tripwire.py, or "
        "the path it guards is now unguarded."
    )


def test_tripwire_keys_are_unique():
    keys = [wire.key for wire in swallow_tripwire.TRIPWIRES]
    assert len(keys) == len(set(keys))

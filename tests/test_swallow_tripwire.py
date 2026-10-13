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
    """Watching the loggers must not change anything a test can observe."""

    FACADE = "custom_components.localshift.engine.optimizer_facade"

    def test_levels_are_left_alone(self):
        logger = logging.getLogger(self.FACADE)
        assert logger.level == logging.NOTSET
        assert not logger.isEnabledFor(logging.DEBUG)

    def test_debug_records_stay_hidden_by_default(self, caplog):
        logging.getLogger(self.FACADE).debug("leak check")
        assert "leak check" not in caplog.text

    def test_debug_records_appear_when_a_test_asks_for_them(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=self.FACADE):
            logging.getLogger(self.FACADE).debug("asked for by logger")
        with caplog.at_level(logging.DEBUG):
            logging.getLogger(self.FACADE).debug("asked for at the root")
        assert "asked for by logger" in caplog.text
        assert "asked for at the root" in caplog.text

    def test_records_still_name_the_line_that_logged(self, caplog):
        logger = logging.getLogger(self.FACADE)
        logger.warning("from a level method")
        logger.log(logging.WARNING, "from log()")
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("from exception()")
        by_message = {record.getMessage(): record for record in caplog.records}
        for message in ("from a level method", "from log()", "from exception()"):
            record = by_message[message]
            assert record.filename == "test_swallow_tripwire.py", message
            assert record.funcName == "test_records_still_name_the_line_that_logged"
        assert by_message["from exception()"].exc_info is not None

    def test_a_debug_level_path_trips_without_any_record(self, caplog):
        """The DEBUG paths are caught even though no record is ever created."""
        hits = swallow_tripwire.begin()
        try:
            try:
                raise RuntimeError("backstop broke")
            except RuntimeError as exc:
                logging.getLogger(self.FACADE).debug(
                    "Pre-charge backstop check failed (non-fatal): %s", exc
                )
        finally:
            swallow_tripwire.end(hits)
        assert [hit.key for hit in hits] == ["precharge_backstop"]
        assert "backstop broke [RuntimeError at tests/" in hits[0].detail
        assert caplog.records == []


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


def _watched_logger_problem(wire) -> str | None:
    """Why a tripwire is not watching the logger its module logs through."""
    module = importlib.import_module(wire.logger)
    if getattr(module, "_LOGGER", None) is logging.getLogger(wire.logger):
        return None
    return (
        f"Tripwire '{wire.key}' wraps logging.getLogger({wire.logger!r}), but "
        f"that module's _LOGGER is now {getattr(module, '_LOGGER', None)!r}. "
        "Its log calls no longer pass through the wrapped logger, so the "
        "tripwire is disarmed: point it at the logger the module really uses."
    )


@pytest.mark.parametrize("wire", swallow_tripwire.TRIPWIRES, ids=lambda wire: wire.key)
def test_tripwire_watches_the_logger_the_module_logs_through(wire):
    assert _watched_logger_problem(wire) is None


@pytest.mark.parametrize(
    "rebound",
    [
        lambda name: logging.getLogger(f"{name}.detail"),
        lambda name: logging.LoggerAdapter(logging.getLogger(name), {}),
    ],
    ids=["child logger", "adapter"],
)
def test_a_rebound_module_logger_is_caught(monkeypatch, rebound):
    """Rebinding ``_LOGGER`` would bypass the wrappers without a sound."""
    wire = swallow_tripwire.TRIPWIRES[0]
    module = importlib.import_module(wire.logger)
    monkeypatch.setattr(module, "_LOGGER", rebound(wire.logger))

    problem = _watched_logger_problem(wire)

    assert problem is not None
    assert "disarmed" in problem


def test_tripwire_keys_are_unique():
    keys = [wire.key for wire in swallow_tripwire.TRIPWIRES]
    assert len(keys) == len(set(keys))

"""Swallowed-failure tripwire: a caught-and-logged failure fails the test.

Why this exists
---------------
The plan and command paths catch broadly and carry on. That is right for a
live battery controller, and it is exactly what hides a broken change in
tests: a removed argument once made the inline DP optimizer raise on every
cycle, ``OptimizerFacade.run_inline`` logged a warning and moved on, and the
suite stayed green. The only thing that noticed was one scenario assertion.

So each ``except`` listed in ``TRIPWIRES`` is treated as a failure when a test
reaches it, unless the test says it meant to::

    @pytest.mark.allow_swallowed_failure("inline_optimizer")
    def test_run_inline_logs_and_swallows_unexpected_exception(): ...

The opt-in names the path, and the path must then actually be hit: a marker
that no longer matches what the test does fails too, so opt-ins cannot rot
into a blanket exemption.

How it detects
--------------
By the log record the ``except`` block emits: logger name plus the log call's
own format string (``LogRecord.msg``, before arguments are merged in). Not by
searching captured output, so a test that merely prints or asserts on a
similar sentence does not trip it, and a path cannot trip it from another
module.

Several of these paths log at DEBUG, which the suite does not normally
create records for. The tripwire therefore lowers those loggers to a private
level just under DEBUG and drops, in a logger filter, every record the logger
would not have produced on its own. Nothing a test can observe through
``caplog`` changes; the cost is a ``LogRecord`` allocation per suppressed
call.

``tests/test_swallow_tripwire.py`` holds each format string below to the
production source, so rewording a log line fails a test instead of silently
disarming its tripwire.
"""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass
from pathlib import Path

from custom_components.localshift.engine.optimizer_facade import (
    INLINE_OPTIMIZER_FAILED_MSG,
)

MARKER = "allow_swallowed_failure"

_FACADE = "custom_components.localshift.engine.optimizer_facade"
_CORE = "custom_components.localshift.engine.core"
_MACHINE = "custom_components.localshift.state.machine"

# Below DEBUG (10) and not a level anything else uses, so the filter can tell
# "still at the level the tripwire set" from "a test changed it".
_PROBE_LEVEL = 9

_REPO_PREFIX = str(Path(__file__).resolve().parent.parent) + "/"


@dataclass(frozen=True)
class Tripwire:
    """One swallow-and-continue ``except`` block that a test must not reach."""

    key: str
    logger: str
    message: str
    hides: str


TRIPWIRES: tuple[Tripwire, ...] = (
    Tripwire(
        "inline_optimizer",
        _FACADE,
        INLINE_OPTIMIZER_FAILED_MSG,
        "the whole cycle: no plan, no decisions, the mode left where it was",
    ),
    Tripwire(
        "planner_solve",
        _CORE,
        "DPPlanner.plan() failed for cycle %s: %s",
        "the solve: the planner returns success=False instead of raising",
    ),
    Tripwire(
        "spike_funding_detection",
        _CORE,
        "Spike funding detection failed%s, using baseline: %s",
        "spike pre-charge: the plan silently falls back to the baseline solve",
    ),
    Tripwire(
        "spike_funded_solve",
        _CORE,
        "Spike-funded solve failed%s, using baseline: %s",
        "spike pre-charge: the plan silently falls back to the baseline solve",
    ),
    Tripwire(
        "next_window_tail",
        _CORE,
        "Next-window tail failed, keeping the main plan (logged at WARNING "
        "once a day per distinct failure, repeats at DEBUG): %s",
        "the horizon tail: the plan past the handled demand window is dropped",
    ),
    Tripwire(
        "shadow_optimizer",
        _FACADE,
        "Shadow optimizer failed: %s",
        "the shadow comparison run",
    ),
    Tripwire(
        "precharge_decision_log",
        _FACADE,
        "Pre-charge decision log failed (non-fatal): %s",
        "the per-cycle pre-charge decision line and the SOC-divergence warning",
    ),
    Tripwire(
        "precharge_backstop",
        _FACADE,
        "Pre-charge backstop check failed (non-fatal): %s",
        "the pre-charge backstop: it returns None, so it can never force a charge",
    ),
    Tripwire(
        "runway_backstop",
        _FACADE,
        "Runway backstop check failed (non-fatal): %s",
        "the runway backstop: it returns None, so it can never force a charge",
    ),
    Tripwire(
        "mode_transition",
        _MACHINE,
        "Exception during mode transition to %s: %s",
        "a battery command: the string-dispatched executor raised and the "
        "transition was reported as merely failed",
    ),
)

KEYS: frozenset[str] = frozenset(t.key for t in TRIPWIRES)


@dataclass(frozen=True)
class Hit:
    """One trip: which path, and what it swallowed."""

    key: str
    detail: str


def _describe(record: logging.LogRecord) -> str:
    """The logged message plus where the swallowed exception was raised."""
    try:
        text = record.getMessage()
    except Exception:  # noqa: BLE001 - a bad format must not mask the trip
        text = str(record.msg)
    exc = record.exc_info[1] if record.exc_info else None
    if exc is None:
        args = record.args if isinstance(record.args, tuple) else ()
        exc = next((a for a in reversed(args) if isinstance(a, BaseException)), None)
    if exc is not None and exc.__traceback__ is not None:
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        where = frame.filename
        if where.startswith(_REPO_PREFIX):
            where = where[len(_REPO_PREFIX) :]
        text += f" [{type(exc).__name__} at {where}:{frame.lineno}]"
    return text


# Hit lists, innermost test last. A stack rather than a single slot so a pytest
# session run inside a test (pytester, in-process) records into its own tests
# and leaves the outer test's list alone.
_stack: list[list[Hit]] = []


class _LoggerFilter(logging.Filter):
    """Records trips on one logger; hides the records the probe level let in."""

    def __init__(self, logger: logging.Logger, messages: dict[str, str]) -> None:
        super().__init__()
        self._logger = logger
        self._messages = messages
        self._own_level = logger.level

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.msg
        key = self._messages.get(msg) if isinstance(msg, str) else None
        if key is not None and _stack:
            _stack[-1].append(Hit(key, _describe(record)))
        if self._logger.level != _PROBE_LEVEL:
            # A test set its own level on this logger: it gets what it asked for.
            return True
        parent = self._logger.parent
        floor = self._own_level or (
            parent.getEffectiveLevel() if parent else logging.WARNING
        )
        return record.levelno >= floor


_installed = False


def _install() -> None:
    """Attach the filters, once per process. Never removed: it is inert when
    no test is recording, and the probe level changes nothing observable."""
    global _installed
    if _installed:
        return
    _installed = True
    by_logger: dict[str, dict[str, str]] = {}
    for wire in TRIPWIRES:
        by_logger.setdefault(wire.logger, {})[wire.message] = wire.key
    for name, messages in by_logger.items():
        logger = logging.getLogger(name)
        logger.addFilter(_LoggerFilter(logger, messages))
        if logger.level == logging.NOTSET or logger.level > _PROBE_LEVEL:
            logger.setLevel(_PROBE_LEVEL)


def begin() -> list[Hit]:
    """Start recording for one test. Returns the list its trips land in."""
    _install()
    hits: list[Hit] = []
    _stack.append(hits)
    return hits


def end(hits: list[Hit]) -> None:
    """Stop recording for the test that owns ``hits``."""
    for index in range(len(_stack) - 1, -1, -1):
        if _stack[index] is hits:
            del _stack[index]
            return


def verdict(hits: list[Hit], allowed: set[str], *, final: bool) -> str | None:
    """Why this test fails the tripwire, or None if it does not.

    ``final`` is False for trips arriving after the test body (teardown): only
    unexpected trips are judged then, since the unused-opt-in check has already
    run against the body.
    """
    unknown = sorted(allowed - KEYS)
    if unknown and final:
        return (
            f"@pytest.mark.{MARKER} names unknown path(s) {unknown}. "
            f"Known: {sorted(KEYS)}"
        )
    unexpected = [hit for hit in hits if hit.key not in allowed]
    if unexpected:
        hides = {wire.key: wire.hides for wire in TRIPWIRES}
        lines = [
            "A failure was caught and logged instead of raised, and this test "
            "did not opt in to it:"
        ]
        seen: set[tuple[str, str]] = set()
        for hit in unexpected:
            if (hit.key, hit.detail) in seen:
                continue
            seen.add((hit.key, hit.detail))
            lines.append(f"  [{hit.key}] {hit.detail}")
            lines.append(f"      hides: {hides[hit.key]}")
        lines.append(
            "If the code under test is meant to fail here, mark the test "
            f'@pytest.mark.{MARKER}("<path>"). If it is not, this is a real '
            "failure the production code swallowed: fix it, do not mark it."
        )
        return "\n".join(lines)
    if final:
        unused = sorted(allowed - {hit.key for hit in hits})
        if unused:
            return (
                f"@pytest.mark.{MARKER} opts in to {unused}, but the test never "
                "reached that path. Remove the stale opt-in."
            )
    return None

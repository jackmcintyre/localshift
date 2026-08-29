"""Structural guard: every TickScheduler handler must be reachable in production.

Regression gate for the medium-tick backfill defect.

``SubscriptionManager`` is wired to the *coordinator's* ``_handle_*`` methods,
not to ``TickScheduler``'s. When ``_handle_medium_tick`` was left as a duplicate
inline copy of the scheduler's body, ``TickScheduler.handle_medium_tick`` lost
its only production caller — and with it ``_backfill_solar_actual`` and
``_flush_completed_periods``. The solar-accuracy sampler therefore never
recorded a period and never evicted a pending forecast, while the unit tests
stayed green because they invoke ``handle_medium_tick`` directly.

Behavioural tests cannot catch that: calling the orphan proves it works, not
that anything calls it. So this check is static — it reads the source and
asserts each coordinator handler is a thin delegate to its scheduler twin.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_COORDINATOR_DIR = (
    Path(__file__).resolve().parents[2]
    / "custom_components"
    / "localshift"
    / "coordinator"
)
_COORDINATOR_PY = _COORDINATOR_DIR / "coordinator.py"
_TICK_SCHEDULER_PY = _COORDINATOR_DIR / "tick_scheduler.py"


def _class_def(path: Path, class_name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise AssertionError(f"{class_name} not found in {path.name}")


def _methods(class_def: ast.ClassDef) -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in class_def.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _delegates_to(method: ast.FunctionDef, scheduler_method: str) -> bool:
    """True if the method body contains ``self._tick_scheduler.<scheduler_method>(...)``."""
    for node in ast.walk(method):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != scheduler_method:
            continue
        owner = func.value
        if (
            isinstance(owner, ast.Attribute)
            and owner.attr == "_tick_scheduler"
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "self"
        ):
            return True
    return False


SCHEDULER_HANDLERS = sorted(
    name
    for name in _methods(_class_def(_TICK_SCHEDULER_PY, "TickScheduler"))
    if name.startswith("handle_")
)


def test_scheduler_exposes_handlers() -> None:
    """Sanity check that the introspection found the handler set at all."""
    assert SCHEDULER_HANDLERS, "no handle_* methods found on TickScheduler"


@pytest.mark.parametrize("scheduler_handler", SCHEDULER_HANDLERS)
def test_coordinator_handler_delegates_to_scheduler(scheduler_handler: str) -> None:
    """Each TickScheduler.handle_* has a coordinator handler that calls it.

    An inline reimplementation on the coordinator side silently orphans the
    scheduler method and anything only it reaches.
    """
    coordinator_handler = f"_{scheduler_handler}"
    coordinator_methods = _methods(_class_def(_COORDINATOR_PY, "LocalShiftCoordinator"))

    assert coordinator_handler in coordinator_methods, (
        f"TickScheduler.{scheduler_handler} is orphaned: no "
        f"LocalShiftCoordinator.{coordinator_handler} to route to it."
    )

    assert _delegates_to(coordinator_methods[coordinator_handler], scheduler_handler), (
        f"LocalShiftCoordinator.{coordinator_handler} does not call "
        f"self._tick_scheduler.{scheduler_handler}(). SubscriptionManager "
        f"registers the coordinator method, so TickScheduler."
        f"{scheduler_handler} would never run in production. Delegate rather "
        f"than reimplementing the body inline."
    )

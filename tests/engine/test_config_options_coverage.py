"""Structural guard: every option the optimizer reads must reach it.

Regression gate for the decorative-slider defect (#965, #969).

``ComputationEngine._build_optimizer_config_options`` hand-copies each option
from ``entry.options`` into the dict that reaches
``optimizer_runner._build_optimizer_config`` on the live path. The runner reads
every knob as ``config_options.get(CONF_X, DEFAULT_X)``, so a knob missing from
that copy does not fail — the slider moves, the engine silently keeps the
default. ``max_pre_charge_price`` sat in that state until 2026-09-05 (#965);
``charge_taper_start_pct``, ``charge_taper_min_factor`` and
``switching_penalty_per_kwh`` were still in it on 2026-09-06 (#969).

Behavioural tests cannot catch the class: a test that sets one option and
checks it arrived proves that one option, not the next one. So this check is
static — it reads both sources and asserts the set of keys the runner (and the
facade) read is a subset of the set of keys the engine forwards.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

_PKG = Path(__file__).resolve().parents[2] / "custom_components" / "localshift"
_COMPUTATION_ENGINE = _PKG / "computation_engine.py"
_RUNNER = _PKG / "engine" / "optimizer_runner.py"
_FACADE = _PKG / "engine" / "optimizer_facade.py"

# ``config_options.get(CONF_X`` / ``config_options[CONF_X`` in the engine modules.
_READ_RE = re.compile(r"config_options(?:\.get\(|\[)\s*(CONF_\w+)")


def _builder_node() -> ast.FunctionDef:
    tree = ast.parse(_COMPUTATION_ENGINE.read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "_build_optimizer_config_options"
        ):
            return node
    raise AssertionError(
        "_build_optimizer_config_options not found in computation_engine.py"
    )


def _forwarded_keys() -> set[str]:
    """CONF_* keys used as dict keys inside ``_build_optimizer_config_options``."""
    return {
        key.id
        for sub in ast.walk(_builder_node())
        if isinstance(sub, ast.Dict)
        for key in sub.keys
        if isinstance(key, ast.Name) and key.id.startswith("CONF_")
    }


def _read_keys(path: Path) -> set[str]:
    return set(_READ_RE.findall(path.read_text()))


def test_engine_forwards_something() -> None:
    """Sanity: the introspection found the dict at all."""
    assert len(_forwarded_keys()) >= 10


@pytest.mark.parametrize("reader", [_RUNNER, _FACADE], ids=["runner", "facade"])
def test_every_option_the_optimizer_reads_is_forwarded(reader: Path) -> None:
    """A CONF read by the engine but absent from the forwarded dict is a
    decorative slider: the number entity writes entry.options, the engine reads
    this dict, and the DEFAULT_* fallback wins forever."""
    missing = sorted(_read_keys(reader) - _forwarded_keys())
    assert not missing, (
        f"{reader.name} reads {missing} from config_options but "
        "ComputationEngine._build_optimizer_config_options never forwards them. "
        "Add each key to that dict (with its DEFAULT_* fallback) or the slider "
        "does nothing on the live path (#965, #969)."
    )


def test_every_number_entity_the_runner_reads_is_forwarded() -> None:
    """Same property stated from the platform side: any number entity whose
    CONF key the runner reads must be forwarded."""
    from custom_components.localshift.number import NUMBER_DEFINITIONS

    entity_confs = {conf for conf, _name, _default in NUMBER_DEFINITIONS}
    runner_reads = _read_keys(_RUNNER)
    decorative = sorted((entity_confs & runner_reads) - _forwarded_keys())
    assert not decorative, f"decorative number entities: {decorative}"


@pytest.mark.parametrize(
    ("conf_name", "attr", "value"),
    [
        ("CONF_CHARGE_TAPER_START_PCT", "charge_taper_start_pct", 85.0),
        ("CONF_CHARGE_TAPER_MIN_FACTOR", "charge_taper_min_factor", 0.35),
        ("CONF_SWITCHING_PENALTY_PER_KWH", "switching_penalty_per_kwh", 0.15),
    ],
)
def test_the_969_sliders_reach_the_engine_on_the_live_path(
    conf_name: str, attr: str, value: float
) -> None:
    """The three knobs #969 found decorative now flow entry.options -> dict -> config."""
    from custom_components.localshift import const
    from custom_components.localshift.computation_engine import ComputationEngine
    from custom_components.localshift.engine.optimizer_runner import (
        _build_optimizer_config,
    )

    conf = getattr(const, conf_name)
    engine = ComputationEngine.__new__(ComputationEngine)
    engine.entry = SimpleNamespace(options={conf: value})
    engine._get_switch_state = lambda _key: False

    options = engine._build_optimizer_config_options()

    assert options[conf] == value
    assert getattr(_build_optimizer_config(SimpleNamespace(), options), attr) == value

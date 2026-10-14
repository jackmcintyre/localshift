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
_SLOTS = _PKG / "engine" / "slots.py"

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


def _forwarded_option_names() -> set[str]:
    """The same dict's keys as option-name strings (CONF_* resolved, literals kept)."""
    from custom_components.localshift import const

    names: set[str] = set()
    for sub in ast.walk(_builder_node()):
        if not isinstance(sub, ast.Dict):
            continue
        for key in sub.keys:
            if isinstance(key, ast.Name):
                names.add(getattr(const, key.id))
            elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                names.add(key.value)
    return names


def _slot_builder_option_reads() -> set[str]:
    """Option names ``SlotBuilder`` reads from the dict it is constructed with.

    The runner/facade regex above cannot see these: the slot builder keeps the
    dict as ``self._config_options`` and reads it by string key through
    ``_parse_time_option`` (#1151). A read this function cannot resolve to a
    name fails the test rather than passing unseen.
    """
    builder = next(
        node
        for node in ast.walk(ast.parse(_SLOTS.read_text()))
        if isinstance(node, ast.ClassDef) and node.name == "SlotBuilder"
    )
    return {
        _option_name(key)
        for method in builder.body
        if isinstance(method, ast.FunctionDef)
        for node in ast.walk(method)
        # _parse_time_option's own .get() is by parameter; its call sites
        # carry the names.
        if (key := _options_key(node, method.name == "_parse_time_option"))
    }


def _is_options_dict(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "_config_options"


def _options_key(node: ast.AST, in_time_helper: bool) -> ast.expr | None:
    """The key expression, if ``node`` reads the slot builder's options dict."""
    if isinstance(node, ast.Subscript) and _is_options_dict(node.value):
        return node.slice
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return None
    if node.func.attr == "_parse_time_option":
        return node.args[0]
    if node.func.attr == "get" and _is_options_dict(node.func.value):
        return None if in_time_helper else node.args[0]
    return None


def _option_name(key: ast.expr) -> str:
    from custom_components.localshift import const

    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return key.value
    if isinstance(key, ast.Name) and key.id.startswith("CONF_"):
        return getattr(const, key.id)
    raise AssertionError(
        f"slots.py:{key.lineno}: SlotBuilder reads config_options with a key "
        "this test cannot resolve; use a string literal or a CONF_* constant"
    )


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


def test_every_option_the_slot_builder_reads_is_forwarded() -> None:
    """Same defect class, one module over (#1151): ``SlotBuilder`` is built from
    this dict too, so a demand-window time missing from it leaves the planner on
    the 15:00-21:00 default while the state machine uses the configured times."""
    reads = _slot_builder_option_reads()
    assert {"demand_window_start", "demand_window_end"} <= reads, (
        f"introspection no longer sees the demand-window reads (found {reads})"
    )
    missing = sorted(reads - _forwarded_option_names())
    assert not missing, (
        f"SlotBuilder reads {missing} from config_options but "
        "ComputationEngine._build_optimizer_config_options never forwards them, "
        "so the planner silently keeps the default (#1151)."
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


def test_away_reserve_reaches_the_engine_on_the_live_path() -> None:
    """A4/docs/holiday-away/plan.md item 4: CONF_AWAY_RESERVE flows
    entry.options -> dict -> config.discharge_floor_pct, only while away."""
    from custom_components.localshift.computation_engine import ComputationEngine
    from custom_components.localshift.const import CONF_AWAY_RESERVE
    from custom_components.localshift.engine.optimizer_runner import (
        _build_optimizer_config,
    )

    engine = ComputationEngine.__new__(ComputationEngine)
    engine.entry = SimpleNamespace(options={CONF_AWAY_RESERVE: 40})
    engine._get_switch_state = lambda _key: False

    options = engine._build_optimizer_config_options()
    assert options[CONF_AWAY_RESERVE] == 40

    away_config = _build_optimizer_config(SimpleNamespace(away_active=True), options)
    assert away_config.away_reserve_floor_pct == 40
    assert away_config.discharge_floor_pct == 40

    not_away_config = _build_optimizer_config(
        SimpleNamespace(away_active=False), options
    )
    assert not_away_config.away_reserve_floor_pct is None


def _slot_flags(
    options: dict[str, str], now_local: str
) -> tuple[list[tuple[str, int]], tuple[str, int]]:
    """Build slots the way the live path does (engine dict -> SlotBuilder).

    Returns (demand-window slots, entry slot), each slot as (HH:MM, width).
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from custom_components.localshift.computation_engine import ComputationEngine
    from custom_components.localshift.engine.slots import SlotBuilder

    tz = ZoneInfo("Australia/Sydney")
    now = datetime.fromisoformat(now_local).replace(tzinfo=tz)

    def entries(first: datetime, minutes: int, count: int) -> list[dict[str, object]]:
        return [
            {
                "start_time": (first + timedelta(minutes=minutes * i)).isoformat(),
                "end_time": (first + timedelta(minutes=minutes * (i + 1))).isoformat(),
                "duration": minutes,
                "per_kwh": 0.25,
            }
            for i in range(count)
        ]

    # Amber's shape: an hour of 5-minute intervals, then 30-minute ones.
    start = now.replace(minute=now.minute - now.minute % 30, second=0)
    forecast = entries(start, 5, 12) + entries(start + timedelta(hours=1), 30, 46)
    data = SimpleNamespace(
        general_forecast=forecast,
        feed_in_forecast=forecast,
        solcast_today=[],
        solcast_tomorrow=[],
        load_forecast_slots=[0.5] * 288,
    )

    engine = ComputationEngine.__new__(ComputationEngine)
    engine.entry = SimpleNamespace(options=options)
    engine._get_switch_state = lambda _key: False

    builder = SlotBuilder(engine._build_optimizer_config_options(), str(tz))
    slots, _meta = builder.build_slots(data, now_dt=now)

    def label(slot: object) -> tuple[str, int]:
        local = datetime.fromisoformat(slot.timestamp_iso).astimezone(tz)
        return local.strftime("%H:%M"), slot.slot_interval_minutes

    window = [label(s) for s in slots if s.is_demand_window_slot]
    return window, label(next(s for s in slots if s.is_demand_window_entry))


_LIVE_WINDOW = {"demand_window_start": "14:55:00", "demand_window_end": "21:00:00"}


def test_configured_window_reaches_the_planner_in_five_minute_slots() -> None:
    """#1151, live options (14:55-21:00). Close to the window the horizon is in
    5-minute slots, so the entry slot is the one starting 14:55: the same
    instant the state machine turns the demand window on. Before the fix the
    planner ignored the option and flagged 15:00."""
    window, entry = _slot_flags(_LIVE_WINDOW, "2026-10-13T14:32:00")

    assert entry == ("14:55", 5)
    assert window[0] == ("14:55", 5)
    assert window[-1][0] < "21:00"


def test_off_boundary_start_falls_to_the_next_thirty_minute_slot() -> None:
    """Far from the window the horizon is in 30-minute slots, and a slot counts
    as demand-window only if it STARTS inside the window. 14:55 is not a
    30-minute boundary, so the 14:30 slot stays outside and the entry slot is
    15:00: five minutes late, never early."""
    window, entry = _slot_flags(_LIVE_WINDOW, "2026-10-13T09:02:00")

    assert entry == ("15:00", 30)
    assert ("14:30", 30) not in window


def test_a_moved_window_moves_the_planner() -> None:
    """The issue's reproduction: 18:00-22:00 configured, planner flagged 15:00."""
    window, entry = _slot_flags(
        {"demand_window_start": "18:00:00", "demand_window_end": "22:00:00"},
        "2026-10-13T09:02:00",
    )

    assert entry == ("18:00", 30)
    assert [t for t, _w in window] == [
        "18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30",
    ]  # fmt: skip

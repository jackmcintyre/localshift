"""Operator controls for the price block (docs/PRICE_BLOCK_TARGET.md slice 1; #1106).

Two controls, added before anything reads them:

- ``number.localshift_block_min_spread``: how far above the cheapest earlier
  price a slot must sit to count as part of the expensive block.
- ``switch.localshift_price_block_target``: whether the planner keys its
  deadline on that block at all. Default OFF.

The tests that matter are the live-path ones. A control that exists as an
entity but is missing from ``ComputationEngine._build_optimizer_config_options``
is decorative: the slider moves and the engine keeps the default forever (#965,
#969). So each control is followed from ``entry.options`` / the switch state,
through that dict, into the ``OptimizerConfig`` the runner builds, and onto the
summary sensor's ``config_options`` attribute.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.computation_engine import ComputationEngine
from custom_components.localshift.const import (
    CONF_BLOCK_MIN_SPREAD,
    CONF_PRICE_BLOCK_TARGET,
    DEFAULT_BLOCK_MIN_DURATION_HOURS,
    DEFAULT_BLOCK_MIN_SPREAD,
    DEFAULT_PRICE_BLOCK_TARGET,
    SWITCH_DEFAULTS,
    SWITCH_ICONS,
    SWITCH_NAMES,
    SWITCH_PRICE_BLOCK_TARGET,
    THRESHOLD_RANGES,
)
from custom_components.localshift.engine.optimizer_runner import (
    _build_optimizer_config,
)
from custom_components.localshift.engine.types import OptimizerConfig
from custom_components.localshift.number import NUMBER_DEFINITIONS, LocalShiftNumber
from custom_components.localshift.sensors.optimizer import OptimizerSummarySensor
from custom_components.localshift.switch import SWITCH_CATEGORIES, SWITCH_KEYS
from custom_components.localshift.utils.entity_configs import LOCALSHIFT_ENTITY_CONFIG
from tests.test_scenarios import (
    create_mock_entry,
    create_mock_get_entity_id,
    create_mock_get_switch_state,
    setup_coordinator_data,
    setup_mock_hass,
)

_PKG = Path(__file__).resolve().parent.parent / "custom_components" / "localshift"


def _engine(options: dict, switches: dict[str, bool]) -> ComputationEngine:
    """A ComputationEngine with just enough state to build its option dict."""
    engine = ComputationEngine.__new__(ComputationEngine)
    engine.entry = SimpleNamespace(options=options)
    engine._get_switch_state = lambda key: switches.get(key, False)
    return engine


# ---------------------------------------------------------------------------
# Constants and OptimizerConfig fields
# ---------------------------------------------------------------------------


def test_design_defaults() -> None:
    """The design constants. They are not tuning targets for this slice."""
    assert DEFAULT_BLOCK_MIN_SPREAD == 0.08
    assert DEFAULT_BLOCK_MIN_DURATION_HOURS == 2.0
    assert DEFAULT_PRICE_BLOCK_TARGET is False


def test_optimizer_config_carries_the_three_fields_at_their_defaults() -> None:
    config = OptimizerConfig()

    assert config.block_min_spread == DEFAULT_BLOCK_MIN_SPREAD
    assert config.block_min_duration_hours == DEFAULT_BLOCK_MIN_DURATION_HOURS
    assert config.price_block_target is DEFAULT_PRICE_BLOCK_TARGET


def test_block_spread_is_a_separate_knob_from_the_cycle_hurdle() -> None:
    """Moving one must not move the other (docs/PRICE_BLOCK_TARGET.md)."""
    config = _build_optimizer_config(
        SimpleNamespace(), {CONF_BLOCK_MIN_SPREAD: 0.12, "min_cycle_saving": 0.25}
    )

    assert config.block_min_spread == 0.12
    assert config.min_cycle_saving == 0.25


# ---------------------------------------------------------------------------
# The number entity
# ---------------------------------------------------------------------------


def test_block_min_spread_number_is_defined() -> None:
    definitions = {conf: (name, default) for conf, name, default in NUMBER_DEFINITIONS}

    assert definitions[CONF_BLOCK_MIN_SPREAD] == (
        "Block Min Spread",
        DEFAULT_BLOCK_MIN_SPREAD,
    )


def test_block_min_spread_range() -> None:
    spec = THRESHOLD_RANGES[CONF_BLOCK_MIN_SPREAD]

    assert (spec["min"], spec["max"], spec["step"]) == (0.00, 0.50, 0.01)
    assert spec["unit"] == "$/kWh"
    assert spec["icon"].startswith("mdi:")


def test_block_min_spread_number_reads_and_writes_entry_options() -> None:
    entry = MagicMock()
    entry.entry_id = "test"
    entry.options = {}
    number = LocalShiftNumber(
        MagicMock(),
        entry,
        CONF_BLOCK_MIN_SPREAD,
        "Block Min Spread",
        DEFAULT_BLOCK_MIN_SPREAD,
    )

    assert number.unique_id == "localshift_block_min_spread"
    assert number.native_value == 0.08
    assert number.native_min_value == 0.00
    assert number.native_max_value == 0.50
    assert number.native_step == 0.01

    entry.options = {CONF_BLOCK_MIN_SPREAD: 0.12}
    assert number.native_value == 0.12


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------


def test_price_block_target_switch_is_registered_and_off_by_default() -> None:
    assert SWITCH_PRICE_BLOCK_TARGET == "price_block_target"
    assert SWITCH_PRICE_BLOCK_TARGET in SWITCH_KEYS
    assert SWITCH_DEFAULTS[SWITCH_PRICE_BLOCK_TARGET] is False
    assert SWITCH_NAMES[SWITCH_PRICE_BLOCK_TARGET] == "Price Block Target"
    assert SWITCH_ICONS[SWITCH_PRICE_BLOCK_TARGET].startswith("mdi:")


def test_price_block_target_switch_is_a_config_entity() -> None:
    """Same category as allow_dw_entry_under_target: a mode toggle, not a
    routine operational control."""
    from homeassistant.helpers.entity import EntityCategory

    assert SWITCH_CATEGORIES[SWITCH_PRICE_BLOCK_TARGET] is EntityCategory.CONFIG


def test_price_block_target_switch_is_in_the_entity_validation_map() -> None:
    assert (
        LOCALSHIFT_ENTITY_CONFIG["switch.localshift_price_block_target"]
        == LOCALSHIFT_ENTITY_CONFIG["switch.localshift_allow_dw_entry_under_target"]
    )


@pytest.mark.parametrize("filename", ["strings.json", "translations/en.json"])
def test_controls_have_strings(filename: str) -> None:
    entity = json.loads((_PKG / filename).read_text())["entity"]

    for section, key in (
        ("switch", "price_block_target"),
        ("number", "block_min_spread"),
    ):
        entry = entity[section][key]
        assert entry["name"]
        assert entry["description"]


# ---------------------------------------------------------------------------
# The live path: entity -> config_options dict -> OptimizerConfig
# ---------------------------------------------------------------------------


def test_untouched_controls_reach_the_runner_at_their_defaults() -> None:
    options = _engine({}, {})._build_optimizer_config_options()

    assert options[CONF_BLOCK_MIN_SPREAD] == DEFAULT_BLOCK_MIN_SPREAD
    assert options[CONF_PRICE_BLOCK_TARGET] is False

    config = _build_optimizer_config(SimpleNamespace(), options)
    assert config.block_min_spread == DEFAULT_BLOCK_MIN_SPREAD
    assert config.price_block_target is False


@pytest.mark.parametrize("value", [0.0, 0.05, 0.12, 0.50])
def test_moving_the_slider_changes_what_the_runner_receives(value: float) -> None:
    options = _engine(
        {CONF_BLOCK_MIN_SPREAD: value}, {}
    )._build_optimizer_config_options()

    assert options[CONF_BLOCK_MIN_SPREAD] == value
    assert _build_optimizer_config(SimpleNamespace(), options).block_min_spread == value


@pytest.mark.parametrize("state", [True, False])
def test_flipping_the_switch_changes_what_the_runner_receives(state: bool) -> None:
    options = _engine(
        {}, {SWITCH_PRICE_BLOCK_TARGET: state}
    )._build_optimizer_config_options()

    assert options[CONF_PRICE_BLOCK_TARGET] is state
    assert (
        _build_optimizer_config(SimpleNamespace(), options).price_block_target is state
    )


def test_switch_state_comes_from_the_switch_not_from_entry_options() -> None:
    """The switch entity persists under ``switch_state_*``; a stray option
    named like the CONF key must not be read as the switch."""
    options = _engine(
        {CONF_PRICE_BLOCK_TARGET: True}, {SWITCH_PRICE_BLOCK_TARGET: False}
    )._build_optimizer_config_options()

    assert options[CONF_PRICE_BLOCK_TARGET] is False


def test_runner_does_not_take_block_duration_from_options() -> None:
    """``block_min_duration_hours`` is a design constant on OptimizerConfig, not
    an operator knob: there is no entity for it, so nothing may override it."""
    config = _build_optimizer_config(
        SimpleNamespace(), {"block_min_duration_hours": 0.5}
    )

    assert config.block_min_duration_hours == DEFAULT_BLOCK_MIN_DURATION_HOURS


# ---------------------------------------------------------------------------
# The summary sensor's config_options attribute, end to end
# ---------------------------------------------------------------------------


def _summary_attributes(options: dict, switches: dict[str, bool]) -> dict:
    """Run one real planning cycle and read the summary sensor's attributes."""
    from simulations.schema import Scenario, discover_scenarios

    path = next(p for p in discover_scenarios() if p.stem == "inverted-night")
    scenario = Scenario.from_json(path)
    test_time = datetime.fromisoformat(scenario.input["test_time"])

    data = setup_coordinator_data(scenario.input)
    engine = ComputationEngine(
        setup_mock_hass(scenario.input),
        create_mock_entry({**scenario.config_overrides, **options}),
        create_mock_get_entity_id(),
        create_mock_get_switch_state({**scenario.switch_states, **switches}),
    )
    with (
        patch("homeassistant.util.dt.now", return_value=test_time),
        patch.object(engine, "_get_historical_hourly_averages", return_value={}),
        patch.object(engine._history_fetcher, "_historical_load_cache", {}),
        patch.object(engine._history_fetcher, "_historical_load_sample_counts", {}),
        patch.object(engine._history_fetcher, "_historical_load_source", "none"),
        patch.object(
            engine._history_fetcher,
            "_recent_load_1hr_kw",
            scenario.input.get("load_power_kw", 0.5),
        ),
    ):
        engine.compute_derived_values(data)
    assert data.optimizer_result["success"] is True

    coordinator = MagicMock()
    coordinator.data = data
    entry = MagicMock()
    entry.entry_id = "test"
    sensor = OptimizerSummarySensor(coordinator, entry)
    with patch("homeassistant.util.dt.now", return_value=test_time):
        return sensor.extra_state_attributes


def test_summary_sensor_publishes_the_controls_at_their_defaults() -> None:
    published = _summary_attributes({}, {})["config_options"]

    assert published[CONF_BLOCK_MIN_SPREAD] == DEFAULT_BLOCK_MIN_SPREAD
    assert published[CONF_PRICE_BLOCK_TARGET] is False


def test_summary_sensor_publishes_the_controls_as_set() -> None:
    published = _summary_attributes(
        {CONF_BLOCK_MIN_SPREAD: 0.12}, {SWITCH_PRICE_BLOCK_TARGET: True}
    )["config_options"]

    assert published[CONF_BLOCK_MIN_SPREAD] == 0.12
    assert published[CONF_PRICE_BLOCK_TARGET] is True

"""Issue #1214: the engine reports comparison_mode from entry.data.

The options flow stores the pricing step's values, comparison_mode among them,
in entry.data, and the comparison logic (StateReader) reads it from there. The
engine's reported config read entry.options, so it always showed the default.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.localshift.computation_engine import ComputationEngine
from custom_components.localshift.const import (
    COMPARISON_MODE_DISABLED,
    COMPARISON_MODE_ENABLED,
    CONF_COMPARISON_MODE,
    DEFAULT_COMPARISON_MODE,
)


def _reported_mode(data: dict, options: dict) -> str:
    engine = ComputationEngine.__new__(ComputationEngine)
    engine.entry = SimpleNamespace(data=data, options=options)
    engine._get_switch_state = lambda _key: False
    return engine._build_optimizer_config_options()["comparison_mode"]


@pytest.mark.parametrize("mode", [COMPARISON_MODE_ENABLED, COMPARISON_MODE_DISABLED])
def test_configured_mode_in_entry_data_reaches_the_engine(mode: str) -> None:
    assert _reported_mode({CONF_COMPARISON_MODE: mode}, {}) == mode


def test_entry_data_wins_over_entry_options() -> None:
    reported = _reported_mode(
        {CONF_COMPARISON_MODE: COMPARISON_MODE_ENABLED},
        {CONF_COMPARISON_MODE: COMPARISON_MODE_DISABLED},
    )
    assert reported == COMPARISON_MODE_ENABLED


def test_older_installs_fall_back_to_entry_options() -> None:
    reported = _reported_mode({}, {CONF_COMPARISON_MODE: COMPARISON_MODE_ENABLED})
    assert reported == COMPARISON_MODE_ENABLED


def test_default_when_neither_is_set() -> None:
    assert _reported_mode({}, {}) == DEFAULT_COMPARISON_MODE

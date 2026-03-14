"""Tests for const.py"""

import pytest

from custom_components.localshift.const import (
    DOMAIN,
    BatteryMode,
    CONF_OPTIMIZATION_MODE,
    DEFAULT_OPTIMIZATION_MODE,
    SELECT_BATTERY_MODE,
    SELECT_OPTIMIZATION_MODE,
    SELECT_OPTIONS,
    SELECT_NAMES,
    SELECT_ICONS,
)


def test_imports():
    """Test that constants are importable."""
    assert DOMAIN == "localshift"


def test_select_imports():
    """Test select-related constants are importable."""
    assert SELECT_BATTERY_MODE == "battery_mode"
    assert SELECT_OPTIMIZATION_MODE == "optimization_mode"
    assert isinstance(SELECT_OPTIONS, dict)
    assert isinstance(SELECT_NAMES, dict)
    assert isinstance(SELECT_ICONS, dict)


def test_battery_mode_enum():
    """Test BatteryMode enum values."""
    expected = [
        "self_consumption",
        "grid_charging",
        "boost_charging",
        "spike_discharge",
        "proactive_export",
        "demand_block",
        "hold",
        "manual",
    ]
    for mode in BatteryMode:
        assert mode.value in expected


def test_optimization_mode_constant():
    """Test CONF_OPTIMIZATION_MODE constant."""
    assert CONF_OPTIMIZATION_MODE == "optimization_mode"


def test_default_optimization_mode_constant():
    """Test DEFAULT_OPTIMIZATION_MODE constant."""
    assert DEFAULT_OPTIMIZATION_MODE == "self_consumption"

import pytest
from custom_components.localshift.const import TESLA_OVERRIDE_TOLERANCE_PERCENT


def test_tesla_override_tolerance_percent_exists():
    assert TESLA_OVERRIDE_TOLERANCE_PERCENT == 1.0


def test_tesla_override_tolerance_percent_value():
    assert isinstance(TESLA_OVERRIDE_TOLERANCE_PERCENT, float)
    assert TESLA_OVERRIDE_TOLERANCE_PERCENT > 0


def test_tesla_override_tolerance_percent_not_old_name():
    try:
        from custom_components.localshift.const import (
            TESLA_OVERRIDE_RESERVE_TOLERANCE_PERCENT,
        )

        assert False, "TESLA_OVERRIDE_RESERVE_TOLERANCE_PERCENT should not exist"
    except ImportError:
        assert True

"""Tests for constants module."""
from custom_components.localshift.const import (
    MAX_NEGATIVE_FIT_HEADROOM_PCT,
    NEGATIVE_FIT_OVERFLOW_BUFFER_FACTOR,
)


def test_negative_fit_constants_defined():
    """Verify negative-FIT avoidance constants are defined."""
    assert MAX_NEGATIVE_FIT_HEADROOM_PCT == 20.0
    assert NEGATIVE_FIT_OVERFLOW_BUFFER_FACTOR == 0.8

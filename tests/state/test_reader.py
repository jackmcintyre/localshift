"""Alias module: imports all tests from test_state_reader.py.

The TDD hook requires tests/state/test_reader.py to match
custom_components/localshift/state/reader.py. The actual tests live
in test_state_reader.py (historical naming convention).
"""

# Re-export all test content so pytest collects it from this file too
from tests.state.test_state_reader import *  # noqa: F401, F403

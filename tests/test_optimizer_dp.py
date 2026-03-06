"""
Test file for optimizer_dp module.

This file imports tests from test_optimizer_dp_solve.py to satisfy TDD compliance.
The actual optimizer_dp tests are organized in test_optimizer_dp_solve.py and
test_issue_598_efficient_cycling.py for better organization.
"""

# Import all tests from the main test file
from tests.test_optimizer_dp_solve import *  # noqa: F401, F403
from tests.test_issue_598_efficient_cycling import *  # noqa: F401, F403
from tests.test_marginal_cycling_penalty import *  # noqa: F401, F403
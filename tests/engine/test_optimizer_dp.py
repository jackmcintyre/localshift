# Test file for optimizer_dp.py - aggregates all optimizer tests
# This allows the TDD hook to run full coverage for the module

# Import from all test files that cover optimizer_dp
from tests.test_futile_cycling_penalty import *  # noqa: F401, F403
from tests.test_optimizer_dp_solve import *  # noqa: F401, F403
from tests.test_solar_opportunity_penalty import *  # noqa: F401, F403
from tests.test_optimizer_self_consumption import *  # noqa: F401, F403
from tests.test_optimizer_hard_constraint import *  # noqa: F401, F403
from tests.test_optimizer_active_mode import *  # noqa: F401, F403
from tests.test_switching_penalty import *  # noqa: F401, F403

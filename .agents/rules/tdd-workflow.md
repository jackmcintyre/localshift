---
trigger: always_on
---

# ⚠️ MANDATORY: TEST-DRIVEN DEVELOPMENT

**TDD is REQUIRED for all code changes. No exceptions.**

## Why TDD?

- **Catches bugs early** - Tests fail before code is written
- **Documents intent** - Tests describe expected behavior
- **Enables refactoring** - Tests catch regressions
- **Improves design** - Writing tests first improves API design
- **Quality assurance** - 95% minimum test coverage enforced

## Required TDD Cycle

### RED → GREEN → REFACTOR

**You MUST explicitly state each phase:**

1. **RED Phase**: Write failing test first
   - Create test file in `tests/`
   - Write test describing expected behavior
   - Run test: `uv run pytest tests/test_file.py -v`
   - **State: "TDD cycle: RED - test fails as expected"**
   - Test MUST fail (proves test is valid)

2. **GREEN Phase**: Write minimal code to pass
   - Implement simplest solution in `custom_components/localshift/`
   - Run test: `uv run pytest tests/test_file.py -v`
   - **State: "TDD cycle: GREEN - test passes"**
   - Test MUST pass (proves implementation works)

3. **REFACTOR Phase**: Clean up while keeping tests green
   - Improve code structure
   - Run all tests: `uv run pytest`
   - **State: "TDD cycle: REFACTOR - all tests pass"**
   - All tests MUST pass

## Workflow Integration

### Before ANY Implementation

```bash
# 1. Verify worktree (per worktrees.md)
git branch --show-current  # Must NOT be main
git worktree list

# 2. Create/update test file FIRST
touch tests/test_new_feature.py
# or update existing test file

# 3. Write failing test
vim tests/test_new_feature.py

# 4. Run test to confirm failure (RED)
uv run pytest tests/test_new_feature.py -v
# State: "TDD cycle: RED - test fails as expected"

# 5. Then implement
vim custom_components/localshift/new_module.py

# 6. Run test to confirm pass (GREEN)
uv run pytest tests/test_new_feature.py -v
# State: "TDD cycle: GREEN - test passes"
```

### Coverage Requirement

**Minimum 95% test coverage enforced:**

```bash
# Check coverage before commit
uv run pytest --cov=custom_components/localshift --cov-report=term-missing

# Coverage report must show >= 95%
# If below 95%, write more tests before committing
```

**Coverage thresholds:**
- New code: Must maintain or improve overall coverage
- Modified code: Must have test coverage for changed logic
- Branch coverage: Considered in 95% threshold

## Test Patterns for LocalShift

### Pattern 1: Using Existing Fixtures

```python
# tests/test_new_feature.py
import pytest
from custom_components.localshift.new_module import NewClass

class TestNewFeature:
    @pytest.mark.asyncio
    async def test_new_behavior(self, mock_hass_with_states, coordinator_data):
        """Test X behaves as Y under Z conditions."""
        # ARRANGE: Set up test conditions
        instance = NewClass(mock_hass_with_states, coordinator_data)
        
        # ACT: Execute behavior being tested
        result = await instance.some_method()
        
        # ASSERT: Verify expected outcome
        assert result == expected_value
```

### Pattern 2: Parametrized Tests

```python
@pytest.mark.parametrize("input,expected", [
    (0.1, "low"),
    (0.5, "medium"),
    (0.9, "high"),
])
def test_derived_value(computation_engine, coordinator_data, input, expected):
    """Test derived value calculation."""
    coordinator_data.input = input
    computation_engine.compute_derived_values(coordinator_data)
    assert coordinator_data.output == expected
```

### Pattern 3: State Machine Tests

```python
class TestBatteryMode:
    @pytest.mark.asyncio
    async def test_mode_transition(self, state_machine, coordinator_data):
        """Test mode transitions under specific conditions."""
        # ARRANGE
        coordinator_data.soc = 20.0
        coordinator_data.general_price = 0.50
        
        # ACT
        await state_machine.evaluate()
        
        # ASSERT
        assert coordinator_data.battery_mode == "EXPECTED_MODE"
```

## Testing Commands

```bash
# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/test_file.py -v

# Run specific test
uv run pytest tests/test_file.py::TestClass::test_name -v

# Run with coverage
uv run pytest --cov=custom_components/localshift --cov-report=term-missing

# Run in parallel
uv run pytest -n auto

# Run tests matching pattern
uv run pytest -k "pattern" -v
```

## Pre-Commit Hook Enforcement

**Before every commit, the following is automatically checked:**

1. **Test file exists** when code file is modified
2. **Coverage threshold** (95%) is met
3. **Tests pass** for modified code

**If checks fail, commit is blocked:**
```bash
❌ Pre-commit check failed:
   - No test file found for custom_components/localshift/new_module.py
   - Expected: tests/test_new_module.py
   
   Run: touch tests/test_new_module.py
   Write failing test first (TDD workflow)
```

## Hard Stop: TDD Violations

**STOP immediately if:**

| Violation | Action |
|-----------|--------|
| Implementing before test | Stop, write test first |
| Test missing for new code | Stop, create test file |
| Coverage below 95% | Stop, write more tests |
| Skipping test run | Stop, run tests before commit |
| Test doesn't fail initially | Test is invalid, rewrite it |

## Exceptions

TDD not required for:
- Documentation-only changes (`.md` files)
- Configuration-only changes (`.yaml`, `.json` without code)
- Cosmetic changes (comments, formatting)
- Test fixture updates (files in `tests/fixtures/`)

**However, even for exceptions:**
- Run full test suite: `uv run pytest`
- Ensure no regressions
- Maintain 95% coverage

## Integration with Existing Workflows

```
1. Worktree workflow → Create worktree first
2. TDD workflow → Write failing test second
3. Implementation → Make test pass third
4. Coverage check → Verify >= 95%
5. Lint/format → Run `ruff` fourth
6. Pre-commit hooks → Automatic validation
7. PR creation → Monitor CI fifth
```

## Example TDD Session

```
User: Add new battery mode "ECONOMY"

Agent: 
✅ Step 1: Verified worktree (branch: issue/123)

📝 Step 2: Creating failing test...
   - Created: tests/test_economy_mode.py
   - Test: test_economy_mode_activates_at_low_price()

🔍 Step 3: Running test (RED phase)...
   $ uv run pytest tests/test_economy_mode.py -v
   ❌ FAILED - EconomyMode not implemented
   
   State: "TDD cycle: RED - test fails as expected"

⚙️  Step 4: Implementing economy mode...
   - Modified: custom_components/localshift/state_machine.py
   - Added: economy mode logic

🔍 Step 5: Running test (GREEN phase)...
   $ uv run pytest tests/test_economy_mode.py -v
   ✅ PASSED
   
   State: "TDD cycle: GREEN - test passes"

🧹 Step 6: Refactoring (optional)...
   - Cleaned up code structure
   
🔍 Step 7: Running all tests...
   $ uv run pytest
   ✅ All 655 tests pass
   
   State: "TDD cycle: REFACTOR - all tests pass"

📊 Step 8: Checking coverage...
   $ uv run pytest --cov=custom_components/localshift
   ✅ Coverage: 96.2% (above 95% threshold)

🎨 Step 9: Running lint/format...
   $ uv run ruff check custom_components/localshift
   $ uv run ruff format --check custom_components/localshift
   ✅ No issues

✅ Ready to commit and create PR
```

## Why This Matters

- **Quality** - Tests catch bugs before production
- **Confidence** - Refactor safely with test coverage
- **Documentation** - Tests document expected behavior
- **Onboarding** - New developers learn from tests
- **Coverage** - 95% ensures critical paths are tested
- **Enforcement** - Pre-commit hooks prevent violations

## Troubleshooting

### Test Won't Fail (RED phase)

If test passes before implementation:
- Test is invalid (not testing anything)
- Mock is too permissive
- Assertion is wrong
- Rewrite test to actually test the behavior

### Coverage Below 95%

**Coverage is checked PER MODIFIED FILE, not project-wide.**

When coverage fails, the pre-commit hook shows:
- **Specific file** with low coverage
- **Exact coverage percentage** vs 95% requirement  
- **Uncovered line ranges** (e.g., L45-52, L78-85)
- **Test file location** for that source file
- **One-liner command** to re-run with details

Example failure output:
```
┌──────────────────────────────────────────────────────────────────────┐
│ ❌ COVERAGE FAILURES - 1 file(s) below 95% threshold                 │
├──────────────────────────────────────────────────────────────────────┤
│ File: custom_components/localshift/optimizer.py                      │
│ Coverage: 78.3% (need 95%)                                           │
│ Uncovered: L45-52, L78-85                                            │
│ Test file: tests/test_optimizer.py                                   │
├──────────────────────────────────────────────────────────────────────┤
│ Run this to see detailed coverage:                                   │
│   uv run pytest tests/test_optimizer.py                              │
│     --cov=custom_components.localshift.optimizer \                   │
│     --cov-report=term-missing -v                                     │
└──────────────────────────────────────────────────────────────────────┘
```

**Remediation steps:**
1. Open the test file shown
2. Write tests targeting the uncovered line ranges
3. Re-run the command shown at the bottom
4. Re-attempt commit when coverage ≥ 95%

**For detailed HTML coverage report:**
```bash
uv run pytest --cov-report=html
open htmlcov/index.html
```

### Pre-Commit Hook Blocks Commit

If commit is blocked by coverage:
1. Read the structured error message
2. Note the specific file and uncovered lines
3. Open the test file shown
4. Write tests for the uncovered lines
5. Re-run the suggested command
6. Re-attempt commit when coverage passes

If commit is blocked by missing test file:
1. Read the error message for expected test file location
2. Create test file: `touch tests/test_module.py`
3. Write failing test (RED phase)
4. Run test to confirm failure
5. Implement (GREEN phase)
6. Re-attempt commit
---
name: test-coverage-agent
description: Monitor and maintain test coverage quality for LocalShift
license: MIT
compatibility: opencode
metadata:
  audience: developers
  workflow: testing
  triggers:
    - post_commit
    - pre_pr
    - manual
---

## What I Do

Monitor, analyze, and improve test coverage for the LocalShift Home Assistant integration. I help identify coverage gaps, suggest missing test cases, and ensure code quality as the codebase grows.

## When to Use Me

- "What's our test coverage?"
- "Are there any untested functions?"
- "Which modules need more tests?"
- "Check coverage before PR"
- "Find untested code in the battery controller"
- "What tests should I add for this new feature?"

## Commands

### Check Overall Coverage

```bash
# Run full test suite with coverage
uv run pytest --cov=custom_components/localshift --cov-report=term-missing --cov-report=html

# Quick coverage check
uv run pytest --cov=custom_components/localshift --cov-report=term
```

### Check Specific Module Coverage

```bash
# Coverage for specific module
uv run pytest tests/test_battery_controller.py --cov=custom_components/localshift/battery_controller.py --cov-report=term-missing

# Coverage for computation engine
uv run pytest tests/test_computation_engine.py --cov=custom_components/localshift/computation_engine.py --cov-report=term-missing
```

### Find Untested Code

```bash
# Show missing lines
uv run pytest --cov=custom_components/localshift --cov-report=term-missing | grep -A5 "Missing"

# Generate HTML report for visual review
uv run pytest --cov=custom_components/localshift --cov-report=html
echo "Open htmlcov/index.html in browser"
```

## Coverage Analysis Workflows

### 1. Pre-PR Coverage Check

```bash
# Run full suite
uv run pytest --cov=custom_components/localshift --cov-report=term --cov-fail-under=80

# Check if coverage dropped
# Compare against baseline (stored in .coverage-baseline)
```

### 2. Module-by-Module Analysis

Priority modules for coverage:

| Module | Priority | Current Target |
|--------|----------|----------------|
| `battery_controller.py` | High | 90%+ |
| `state_machine.py` | High | 90%+ |
| `computation_engine.py` | High | 85%+ |
| `coordinator.py` | High | 85%+ |
| `entity_validator.py` | Medium | 80%+ |
| `weather_correlation.py` | Medium | 80%+ |
| `computation_engine_lib/` | Medium | 75%+ |

### 3. Identifying Test Gaps

Common untested patterns:

**Error handling:**
```python
# Often missed - exception branches
try:
    result = risky_operation()
except SpecificException as e:
    # This branch often untested
    handle_error(e)
```

**Edge cases:**
```python
# Boundary conditions
if value == 0:
    # Edge case
    pass
elif value < 0:
    # Negative handling
    pass
```

**Configuration paths:**
```python
# Different config options
if config.get("option"):
    # Option enabled path
else:
    # Default path (often tested)
```

## Suggesting Test Cases

When coverage is missing, I'll suggest tests:

### Example: Battery Controller

**Missing coverage:** Error handling in `set_battery_mode()`

**Suggested test:**
```python
async def test_set_battery_mode_api_failure(
    battery_controller: BatteryController,
    mock_hass: HomeAssistant,
) -> None:
    """Test handling of API failure when setting battery mode."""
    # Arrange
    mock_hass.services.async_call = AsyncMock(
        side_effect=HomeAssistantError("API timeout")
    )
    
    # Act & Assert
    with pytest.raises(HomeAssistantError):
        await battery_controller.set_battery_mode(
            BatteryMode.SELF_CONSUMPTION
        )
```

### Example: State Machine

**Missing coverage:** State transition edge cases

**Suggested test:**
```python
async def test_state_transition_same_mode_no_op(
    state_machine: StateMachine,
) -> None:
    """Test that transitioning to current mode is a no-op."""
    # Arrange
    state_machine.current_mode = BatteryMode.SELF_CONSUMPTION
    
    # Act
    result = await state_machine.transition_to(BatteryMode.SELF_CONSUMPTION)
    
    # Assert
    assert result is False  # No transition occurred
```

## Coverage Targets

### Minimum Thresholds

- **Overall project:** 80%
- **Core modules:** 85%
- **Critical paths:** 90%
- **Entity platforms:** 75%

### Coverage Regression Prevention

Set up pre-commit hook:

```yaml
# .pre-commit-config.yaml addition
- repo: local
  hooks:
    - id: coverage-check
      name: Check test coverage
      entry: uv run pytest --cov=custom_components/localshift --cov-fail-under=80
      language: system
      pass_filenames: false
      always_run: true
```

## Integration with CI

The project already has `.github/workflows/test.yml`. Ensure it includes:

```yaml
- name: Run tests with coverage
  run: |
    uv run pytest --cov=custom_components/localshift \
      --cov-report=xml \
      --cov-report=term-missing \
      --cov-fail-under=80

- name: Upload coverage to Codecov
  uses: codecov/codecov-action@v3
  with:
    file: ./coverage.xml
    fail_ci_if_error: true
```

## Tips

1. **Focus on behavior, not lines:** A single well-designed test can cover multiple code paths
2. **Test error paths:** These are often the least tested but most critical
3. **Use parametrization:** Test multiple scenarios efficiently
4. **Mock external services:** Don't test Teslemetry API, test your code's reaction
5. **Async testing:** Use `pytest-asyncio` properly for async code
6. **Check the gaps:** Use `--cov-report=term-missing` to see exactly which lines are untested

## Quick Commands Reference

```bash
# Full coverage report
uv run pytest --cov=custom_components/localshift --cov-report=html

# Check specific file
uv run pytest --cov=custom_components/localshift/battery_controller.py --cov-report=term-missing

# Fail if coverage below threshold
uv run pytest --cov=custom_components/localshift --cov-fail-under=80

# Coverage diff (if baseline exists)
uv run pytest --cov=custom_components/localshift --cov-report=term:skip-covered
```

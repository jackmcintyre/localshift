# Testing

## Commands

```bash
uv run pytest                          # All tests
uv run pytest tests/test_file.py -v   # Specific file
uv run pytest --cov=custom_components/localshift --cov-report=term-missing
```

## Fixtures (conftest.py)

```python
mock_hass              # Basic MagicMock
mock_entry             # ConfigEntry
mock_hass_with_states  # Realistic entity states
```

## TDD Workflow (REQUIRED)

1. **RED**: Write failing test first
   ```bash
   uv run pytest tests/test_new.py -v
   # State: "TDD cycle: RED - test fails as expected"
   ```

2. **GREEN**: Implement minimal solution
   ```bash
   uv run pytest tests/test_new.py -v
   # State: "TDD cycle: GREEN - test passes"
   ```

3. **REFACTOR**: Clean up, run all tests
   ```bash
   uv run pytest
   # State: "TDD cycle: REFACTOR - all tests pass"
   ```

## Coverage

- **95% minimum** per modified file
- Check: `uv run pytest --cov=custom_components/localshift --cov-report=term-missing`

## Patterns

```python
class TestMyClass:
    @pytest.mark.asyncio
    async def test_behavior(self, mock_hass_with_states):
        # test code
```

## See Also

- `../AGENTS.md` - Root rules
- `../../.agents/rules/tdd-workflow.md` - Detailed TDD

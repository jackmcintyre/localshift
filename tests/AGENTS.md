# LocalShift Tests - Agent Guidelines

## Overview

Test suite for LocalShift integration. 58 test files, pytest-based, async/await support, 95% coverage requirement.

## Test Configuration

- **pytest.ini**: `testpaths = tests`, `asyncio_mode = auto`
- **conftest.py**: Central fixtures (768 lines)
- **Coverage**: 95% minimum per modified file
- **Run**: `uv run pytest` (not bare `pytest`)

## Directory Structure

```
tests/
├── __init__.py
├── conftest.py              # Central fixtures
├── fixtures/
│   ├── __init__.py
│   └── ha_entities.py      # MockState, MockStates (619 lines)
├── test_*.py               # Root-level tests (27 files)
├── engine/                  # Optimizer tests
│   ├── test_optimizer_dp.py         # Test aggregator
│   ├── test_price_calculator.py
│   └── test_optimizer_runner.py
├── forecast/               # Forecast tests (9 files)
├── state/                  # State machine tests (3 files)
├── services/               # Service tests (2 files)
├── utils/                  # Utility tests (2 files)
├── learning/               # Learning tests (1 file)
└── coordinator/            # Coordinator tests (1 file)
```

## Naming Conventions

- **Files**: `test_*.py` (e.g., `test_computation_engine.py`)
- **Classes**: `Test*` (e.g., `TestLoadForecastSlots`)
- **Functions**: `test_*` (e.g., `test_force_discharge_active`)
- **Async**: `async def test_*` (pytest-asyncio auto-detects)

## Fixture Categories

### Simple Mocks (conftest.py)

```python
mock_hass          # Basic MagicMock
mock_entry         # ConfigEntry
mock_hass_with_states  # Realistic entity states
mock_hass_with_forecasts  # Includes forecasts
```

### Edge Case Fixtures

```python
mock_hass_unavailable_entities   # All unavailable
mock_hass_unknown_entities       # All unknown
mock_hass_missing_entities       # states.get() returns None
mock_hass_price_spike            # Price spike active
mock_hass_low_battery            # SOC at 10%
mock_hass_full_battery           # SOC at 100%
mock_hass_negative_prices        # Negative prices
```

### HA Entity Mocks (fixtures/ha_entities.py)

```python
from tests.fixtures.ha_entities import MockState, MockStates

# Create entity state
state = MockState(
    entity_id="sensor.test",
    state="on",
    attributes={"friendly_name": "Test"}
)

# Or use factory
from tests.fixtures.ha_entities import create_powerwall_soc_state
soc_state = create_powerwall_soc_state(75.0)
```

## TDD Workflow (REQUIRED)

**RED → GREEN → REFACTOR**

```bash
# 1. Write failing test
vim tests/test_new_feature.py

# 2. Run to confirm failure (RED)
uv run pytest tests/test_new_feature.py -v
# State: "TDD cycle: RED - test fails as expected"

# 3. Implement minimal code
vim custom_components/localshift/new_module.py

# 4. Run to confirm pass (GREEN)
uv run pytest tests/test_new_feature.py -v
# State: "TDD cycle: GREEN - test passes"

# 5. Refactor, run all tests
uv run pytest
# State: "TDD cycle: REFACTOR - all tests pass"
```

## Coverage Check

```bash
# Before commit - MUST be >= 95%
uv run pytest --cov=custom_components/localshift --cov-report=term-missing

# HTML report
uv run pytest --cov-report=html
```

## Test Patterns

### Pattern 1: Basic Test

```python
import pytest
from custom_components.localshift.module import MyClass

class TestMyClass:
    @pytest.mark.asyncio
    async def test_behavior(self, mock_hass_with_states):
        instance = MyClass(mock_hass_with_states)
        result = await instance.method()
        assert result == expected
```

### Pattern 2: Parametrized

```python
@pytest.mark.parametrize("input,expected", [
    (0.1, "low"),
    (0.5, "medium"),
])
def test_derived(computation_engine, input, expected):
    coordinator_data.input = input
    computation_engine.compute(coordinator_data)
    assert coordinator_data.output == expected
```

### Pattern 3: State Machine

```python
class TestBatteryMode:
    @pytest.mark.asyncio
    async def test_transition(self, state_machine, coordinator_data):
        coordinator_data.soc = 20.0
        await state_machine.evaluate()
        assert coordinator_data.battery_mode == "EXPECTED"
```

## Test Aggregator Pattern

Some files import from others for coverage aggregation:

```python
# tests/engine/test_optimizer_dp.py
from tests.test_futile_cycling_penalty import *  # noqa: F401, F403
from tests.test_optimizer_dp_solve import *  # noqa: F401, F403
```

## Commands

```bash
# Run all tests
uv run pytest

# Run specific file
uv run pytest tests/test_file.py -v

# Run specific test
uv run pytest tests/test_file.py::TestClass::test_name -v

# Run with coverage
uv run pytest --cov=custom_components/localshift --cov-report=term-missing

# Run parallel
uv run pytest -n auto

# Run matching pattern
uv run pytest -k "spike" -v
```

## Critical Rules

1. **TDD REQUIRED** - Write failing test first
2. **State TDD phase explicitly** - "TDD cycle: RED/GREEN/REFACTOR"
3. **Coverage >= 95%** per modified file
4. **Use uv** - `uv run pytest` (not bare pytest)
5. **Use fixtures** - Don't mock HA manually
6. **Test MUST fail first** - Otherwise test is invalid

## See Also

- `../AGENTS.md` - Project overview
- `../custom_components/localshift/AGENTS.md` - Integration guide
- `../custom_components/localshift/engine/AGENTS.md` - Optimizer guide
- `../docs/DEVELOPER_GUIDE.md` - Full developer guide
- `../.agents/rules/tdd-workflow.md` - Detailed TDD rules

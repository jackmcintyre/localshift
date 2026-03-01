# AGENTS.md - Agent Coding Guidelines for LocalShift

This file provides guidelines for agentic coding agents working on the LocalShift project.

## Project Overview

LocalShift is a Home Assistant integration for automated Tesla Powerwall battery control based on pricing data, solar forecasts, and demand window timing.

- **Python**: 3.13+
- **Main code**: `custom_components/localshift/`
- **Tests**: `tests/`

---

## Build, Lint, and Test Commands

### Running All Tests

```bash
uv run pytest
```

### Running a Single Test

```bash
uv run pytest tests/test_state_machine.py
uv run pytest tests/test_state_machine.py::test_specific_test_name
```

### Linting

```bash
uv run ruff check custom_components/localshift
```

### Formatting Check

```bash
uv run ruff format --check custom_components/localshift
```

### Format and Fix

```bash
uv run ruff format custom_components/localshift
uv run ruff check --fix custom_components/localshift
```

### Type Checking

```bash
uv run pyright
```

### Dead Code Detection

```bash
uv run vulture
```

### All Checks (before committing)

```bash
uv run ruff check custom_components/localshift && \
uv run ruff format --check custom_components/localshift && \
uv run pytest
```

---

## Code Style Guidelines

### General Rules

- **Line length**: 88 characters (ruff default)
- **Python version**: 3.13+
- **Type hints**: Use Python 3.13+ typing features (no `from __future__ import annotations` needed)
- **Docstrings**: Google-style for classes and public methods
- **No trailing whitespace**: Remove on save
- **No unused imports**: Remove before committing

### Imports

**Standard order** (ruff I001):
1. Standard library
2. Third-party (Home Assistant, external libs)
3. Local imports

```python
import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .coordinator import LocalShiftCoordinator
```

**Avoid**:
- Relative imports with more than one `.` (e.g., `from ....module`)
- Unused imports
- Import aliases unless necessary

### Naming Conventions

| Element | Convention | Example |
|---------|------------|---------|
| Modules | snake_case | `state_machine.py` |
| Classes | PascalCase | `LocalShiftCoordinator` |
| Functions | snake_case | `def async_setup_entry` |
| Constants | UPPER_SNAKE | `DEFAULT_UPDATE_INTERVAL` |
| Type aliases | PascalCase | `LocalShiftConfigEntry` |
| Private methods | _snake_case | `def _internal_method` |
| Private variables | _snake_case | `self._internal_state` |

### Type Hints

- **Use explicit types** for function parameters and return values
- **Use `type` alias** for ConfigEntry generics:
  ```python
  type LocalShiftConfigEntry = ConfigEntry[LocalShiftCoordinator]
  ```
- **Avoid `Any`** unless absolutely necessary
- **Use `Optional[X]`** instead of `X | None` for compatibility

### Error Handling

- **Use exceptions sparingly** - prefer explicit checks
- **Log errors** with `_LOGGER.error()` before raising
- **Never swallow exceptions** without logging
- **Use custom exceptions** for domain-specific errors

```python
# Good
if value < 0:
    _LOGGER.error("Invalid value: %s", value)
    raise ValueError("Value must be non-negative")

# Avoid
try:
    process()
except Exception:
    pass  # Never do this
```

### Logging

- Use module-level logger:
  ```python
  _LOGGER = logging.getLogger(__name__)
  ```
- Use lazy formatting (printf-style):
  ```python
  _LOGGER.debug("Processing %s items", len(items))
  ```
- Log levels: DEBUG (detailed), INFO (operations), WARNING (unexpected), ERROR (failed)

### Async/Await

- **Always use `async def`** for coroutines
- **Never block** - use `await` for all I/O
- **Use `asyncio.gather`** for parallel operations when safe
- **Handle cancellation** gracefully in long-running tasks

### Home Assistant Patterns

- **Config entries**: Use typed `ConfigEntry[T]` with runtime_data
- **Entities**: Implement Home Assistant entity classes
- **Coordinators**: Use `DataUpdateCoordinator` for entity updates
- **Services**: Register via `hass.services.async_register`

---

## Development Workflow

### Before Any Task

1. Run `git worktree list` to check current state
2. Create a worktree if not in one:
   ```bash
   git worktree add worktrees/issue-{NNN} -b issue/{NNN}
   ```
   Work inside that worktree for the duration of the task.

### After Making Changes

1. Run lint: `uv run ruff check custom_components/localshift`
2. Run format check: `uv run ruff format --check custom_components/localshift`
3. Run tests: `uv run pytest`
4. Deploy to HA: `./deploy.sh --reserve && ./deploy.sh`
5. Check logs: `tail -100 /homeassistant/home-assistant.log | grep -i localshift`

### Commit Guidelines

- Reference issue: `Fixes #NNN` or `Closes #NNN`
- Ask user to commit (do not auto-commit)
- Open PR after commit

---

## Documentation Requirements

### Inline Docs

- Add docstrings to all public classes and methods
- Add comments for complex logic (>20 lines)
- Use Google-style docstrings

### File-Specific Updates

| Modified | Update |
|----------|--------|
| Sensors | `docs/ENTITY_REFERENCE.md` |
| Core engine | `docs/ARCHITECTURE.md` |
| Config flow | `docs/DEVELOPER_GUIDE.md` |

---

## File Structure

```
/config/home/localshift
├── custom_components/localshift/
│   ├── __init__.py          # Entry point
│   ├── config_flow.py       # HA config flow
│   ├── const.py             # Constants
│   ├── coordinator.py       # Data coordinator
│   ├── sensor.py             # Sensor entities
│   ├── binary_sensor.py     # Binary sensor entities
│   ├── switch.py             # Switch entities
│   ├── number.py             # Number entities
│   ├── button.py             # Button entities
│   ├── computation_engine.py # Core calculations
│   ├── state_machine.py      # State management
│   └── ...
├── tests/                    # Test suite
├── docs/                    # Documentation
├── pyproject.toml          # Project config
├── deploy.sh               # HA deployment script
└── AGENTS.md              # This file
```

---

## Testing Best Practices

- **Test file naming**: `test_*.py`
- **Test class naming**: `Test*`
- **Test function naming**: `test_*`
- **Use fixtures**: Define in `conftest.py`
- **Mock HA**: Use `hass` fixture from conftest
- **Async tests**: Mark with `async def test_*` (pytest-asyncio handles it)

---

## Entity Counts (as of 2026-02-28)

- Sensors: 29
- Binary Sensors: 13
- Switches: 13
- Numbers: 6
- Selects: 1
- Buttons: 3
- **Total**: 64

When adding/removing entities, update `docs/ENTITY_REFERENCE.md`, `README.md`, and this file.

# AGENTS.md - Agent Coding Guidelines for LocalShift

LocalShift is a Home Assistant integration for automated Tesla Powerwall battery control. This document provides guidelines for agentic coding agents.

**Python**: 3.13+ | **Main code**: `custom_components/localshift/` | **Tests**: `tests/`

---

## Build, Lint, and Test Commands

Use `uv` for all commands (see pyproject.toml).

- **Run all tests**: `uv run pytest`
- **Single test**: `uv run pytest tests/test_state_machine.py::test_specific_name`
- **Lint**: `uv run ruff check custom_components/localshift`
- **Format check**: `uv run ruff format --check custom_components/localshift`
- **Format**: `uv run ruff format custom_components/localshift`
- **Type check**: `uv run pyright`
- **Dead code**: `uv run vulture`
- **All checks**: `uv run ruff check custom_components/localshift && uv run ruff format --check custom_components/localshift && uv run pytest`

---

## Code Style Guidelines

### General Rules
- Line length: 88 characters (ruff default)
- Python 3.13+ features allowed (no `from __future__ import annotations` needed but acceptable)
- Type hints: Use explicit types. For ConfigEntry generics: `type LocalShiftConfigEntry = ConfigEntry[LocalShiftCoordinator]`
- Docstrings: Google-style for classes and public methods
- No trailing whitespace, no unused imports
- Avoid `Any`; use `Optional[X]` instead of `X | None` for compatibility

### Imports
Order: (1) Standard library, (2) Third-party, (3) Local. Example:
```python
import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .coordinator import LocalShiftCoordinator
```
Avoid: relative imports with more than one `.`, unused imports, aliases unless necessary.

### Naming Conventions
| Element | Convention | Example |
|---------|------------|---------|
| Modules | snake_case | `state_machine.py` |
| Classes | PascalCase | `LocalShiftCoordinator` |
| Functions | snake_case | `def async_setup_entry` |
| Constants | UPPER_SNAKE | `DEFAULT_UPDATE_INTERVAL` |
| Type aliases | PascalCase | `LocalShiftConfigEntry` |
| Private methods/vars | _snake_case | `def _internal_method` |

### Error Handling
- Use exceptions sparingly; prefer explicit checks
- Log errors before raising: `_LOGGER.error("Invalid: %s", value)`
- Never swallow exceptions without logging
- Use custom exceptions for domain-specific errors

### Logging
- Module-level: `_LOGGER = logging.getLogger(__name__)`
- Lazy formatting: `_LOGGER.debug("Processing %s items", len(items))`
- Levels: DEBUG (detailed), INFO (operations), WARNING (unexpected), ERROR (failed)

### Async/Await
- Always `async def` for coroutines
- Never block; use `await` for all I/O
- Use `asyncio.gather` for parallel operations when safe
- Handle cancellation gracefully in long-running tasks

### Home Assistant Patterns
- Config entries: Use typed `ConfigEntry[T]` with `entry.runtime_data`
- Entities: Implement Home Assistant entity classes
- Coordinators: Use `DataUpdateCoordinator` for entity updates
- Services: Register via `hass.services.async_register`

---

## Development Workflow

### Before Any Task
1. Check current worktree: `git worktree list`
2. If not in a worktree, create one:
   ```bash
   git worktree add worktrees/issue-{NNN} -b issue/{NNN}
   ```
   Work exclusively inside that worktree for the task.

### After Making Changes
1. Lint: `uv run ruff check custom_components/localshift`
2. Format check: `uv run ruff format --check custom_components/localshift`
3. Test: `uv run pytest`
4. Deploy: `./deploy.sh --reserve && ./deploy.sh`
5. Check logs: `tail -100 /homeassistant/home-assistant.log | grep -i localshift`

### Commit Guidelines
- Reference issue: `Fixes #NNN` or `Closes #NNN`
- Ask user to commit; do not auto-commit
- Open PR after commit

---

## Testing Best Practices
- Test files: `test_*.py`, classes: `Test*`, functions: `test_*`
- Use fixtures defined in `conftest.py`
- Mock HA with `hass` fixture
- Async tests: `async def test_*` (pytest-asyncio auto-detects)

---

## Notes
- No Cursor or Copilot rules found in repository
- Follow existing code patterns when modifying files
- Update documentation (`docs/ENTITY_REFERENCE.md`, `docs/ARCHITECTURE.md`, `docs/DEVELOPER_GUIDE.md`) when adding/removing entities
- Entity counts: Sensors 29, Binary Sensors 13, Switches 13, Numbers 6, Selects 1, Buttons 3 (Total 64)

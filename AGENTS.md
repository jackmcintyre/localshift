# AGENTS.md - Agent Coding Guidelines for LocalShift

LocalShift is a Home Assistant integration for automated Tesla Powerwall battery control. This document provides guidelines for agentic coding agents.

**Python**: 3.13+ | **Main code**: `custom_components/localshift/` | **Tests**: `tests/`

---

## ⚠️ ENFORCED: Worktree-Only Workflow

**A git hook blocks all commits on `main` branch.**

Before starting ANY task:
1. Run `git worktree list` - verify you're in a worktree
2. Run `git branch --show-current` - must NOT be `main`
3. If on main, create worktree: `git worktree add worktrees/issue-{NNN} -b issue/{NNN}`

See `.opencode/rules` for full workflow.

---

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

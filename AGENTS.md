# LocalShift Agent Rules

## Critical (Never Skip)

| Rule | Command |
|------|---------|
| Worktree required | `git worktree add worktrees/issue-{N} -b issue/{N}` |
| Branch protected | NEVER commit to `main` or `test` directly |
| TDD required | Write failing test first, then implement |
| Coverage ≥95% | `uv run pytest --cov=custom_components/localshift --cov-report=term-missing` |

## Project Context

- **Domain**: Home Assistant integration for Tesla Powerwall
- **Code**: `custom_components/localshift/`
- **Tests**: `tests/`
- **Deploy**: `./deploy.sh --reserve && ./deploy.sh` (ask user to run)
- **Python**: 3.13+, type hints required

## Optimizer Changes (CRITICAL)

When modifying DP optimizer, you MUST consult `docs/PLANNING_MODEL.md` first:

| Question | Answer → |
|----------|----------|
| Impossible/forbidden? | Add to `feasible_actions()` |
| Required by deadline? | Add to `terminal_cost()` |
| Discouraged/preferred? | Add penalty to `stage_cost()` |

## Common Tasks

| Task | Do This |
|------|---------|
| Add entity | Create in platform file, update `docs/ENTITY_REFERENCE.md` |
| Modify state | Edit `state/` directory |
| Change optimizer | Edit `engine/`, consult PLANNING_MODEL.md |
| Config flow | Edit `config_flow/` |
| Add test | `tests/test_*.py`, use fixtures from `conftest.py` |

## Navigation

Use SymDex instead of grep/read:
- `symdex_search_symbols("function")` - find symbol
- `symdex_get_file_outline("file.py")` - file structure
- `symdex_search_text("text")` - search content

## Reference Files (load when needed)

| File | When |
|------|------|
| `custom_components/localshift/AGENTS.md` | Entity/platform changes |
| `custom_components/localshift/engine/AGENTS.md` | Optimizer changes |
| `tests/AGENTS.md` | Test patterns |
| `docs/AGENTS.md` | Documentation index |
| `.agents/rules/tdd-workflow.md` | Detailed TDD guide |
| `.agents/rules/pr-ci-checks.md` | CI/PR workflow |
| `.agents/rules/worktrees.md` | Git worktree details |

## Verification Commands

```bash
git branch --show-current  # Must NOT be main/test
git worktree list          # Verify in worktree
uv run ruff check custom_components/localshift
uv run pytest
```

## Entity Counts
Sensors 30 | Binary Sensors 10 | Switches 8 | Numbers 4 | Selects 2 | Buttons 2 (Total 56)

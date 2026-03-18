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

## Additional Tools

**OpenViking** - Semantic search for code understanding:
- Use for: "explain how X works", "find patterns", "analyze code"
- Commands: `ov search "query"`, `ov read viking://...`
- Start if not running: `~/.local/bin/ov-start`

**context-mode** - Efficient large file handling:
- Use for: log files, large data files, full file analysis
- Commands: `ctx_execute_file`, `ctx_batch_execute`
- Better than Read for files >100 lines

## Reference Files (load when needed)

| File | When |
|------|------|
| `custom_components/localshift/AGENTS.md` | Entity/platform changes |
| `custom_components/localshift/engine/AGENTS.md` | Optimizer changes |
| `tests/AGENTS.md` | Test patterns |
| `docs/INDEX.md` | **Primary documentation index (use this first!)** |
| `docs/AGENTS.md` | Documentation index (legacy, see INDEX.md) |
| `.agents/rules/tdd-workflow.md` | Detailed TDD guide |
| `.agents/rules/pr-ci-checks.md` | CI/PR workflow |
| `.agents/rules/worktrees.md` | Git worktree details |

## Documentation-First Workflow

**NEW:** The `doc-first` skill automatically checks relevant documentation before any code analysis. It activates on tasks like "analyze", "review", "refactor", "explain", and related queries. The skill:

1. Determines domain (optimizer, entities, state machine, etc.)
2. Identifies required docs from `docs/INDEX.md`
3. Extracts key constraints and patterns
4. Presents summary before analysis begins

**Manual override:** If you need to skip this, say "skip documentation check" explicitly.

**When manual review needed:** Always consult `docs/INDEX.md` to understand which docs apply to your task.

## Verification Commands

```bash
git branch --show-current  # Must NOT be main/test
git worktree list          # Verify in worktree
uv run ruff check custom_components/localshift
uv run pytest
```

## Entity Counts
Sensors 30 | Binary Sensors 10 | Switches 8 | Numbers 4 | Selects 2 | Buttons 2 (Total 56)

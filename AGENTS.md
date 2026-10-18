# LocalShift Agent Rules

## Critical (Never Skip)

| Rule | Command |
|------|---------|
| Worktree required | `git worktree add worktrees/issue-{N} -b issue/{N}` |
| Branch protected | NEVER commit to `main` or `test` directly |
| TDD required | Write failing test first, then implement |
| Coverage ≥95% of changed lines | Enforced by the pre-commit hook (`.githooks/scripts/check-tdd.sh`); see `.agents/rules/tdd-workflow.md` |

## Project Context

- **Domain**: Home Assistant integration for Tesla Powerwall
- **Code**: `custom_components/localshift/`
- **Tests**: `tests/`
- **Deploy**: Deploys are done only by the HA host's pull deployer, which ships every merge to `main`. There is no other deploy path. Agents never run `deploy.sh` (kept for a human in an emergency; it refuses without `--i-am-not-the-deployer`), `rollback.sh` or restart HA.
- **Python**: 3.13+, type hints required
- **Where development runs**: a shared dev host, with deploys moving to the HA host's pull deployer (in progress). Worktrees live under `worktrees/` in the repo. Tools marked *optional* below may not be installed in your environment; skip them if absent.

## House rules that affect deploys

- **HA restarts**: Jack restarts HA; the coordinator pre-checks. Merged changes go live at the next restart; never restart HA yourself.
- **No HA restart during the nightly backup window.**
- **The move to the HA host's pull deployer is in progress.** Until it ships `main` merges, a merged change waits for it; do not work around that by deploying by hand.

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

Use the right tool for the task. SymDex, OpenViking and hass-cli are optional: use them if available, otherwise fall back to `grep` and reading files.

**SymDex** *(optional, if available)* - Precise code navigation:
- `symdex_search_symbols("function")` - find symbol/class by exact name
- `symdex_get_file_outline("file.py")` - file structure
- `symdex_search_text("text")` - exact content search
- Use when: you know the name, need structure, or want fast lookups

**OpenViking** *(optional, if available)* - Semantic code understanding:
- `ov search "query"` - semantic search across codebase
- `ov read viking://resources/localshift/path/to/file` - read file content
- `ov abstract viking://...` - get directory summary
- Use when: exploring how/why code works, finding patterns, conceptual queries

**@homeassistant subagent** - HA gateway, read-only:
- Reads entity state, history and lists (via hass-cli if available)
- Agents are read-only against HA: no turning things on/off, setting values or calling services
- Use: `@homeassistant check battery state`
- Skill: `homeassistant`

**Quick decision guide:**

| You need... | Use |
|--------------|-----|
| Find function `foo` by name | SymDex if available, else `grep` |
| Understand how auth works | OpenViking if available, else `grep` + read |
| See file structure | SymDex if available |
| Find code related to "grid charging" | OpenViking if available, else `grep` |
| Navigate to line N | SymDex if available |
| Any HA read | `@homeassistant` subagent |

## Additional Tools

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
Sensors 35 | Binary Sensors 11 | Switches 8 | Numbers 7 | Selects 2 | Buttons 2 (Total 65)

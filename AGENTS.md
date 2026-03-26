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
- **Deploy**: Use skill `deploy-and-validate` when work is complete to deploy and validate
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

Use the right tool for the task:

**SymDex** - Precise code navigation:
- `symdex_search_symbols("function")` - find symbol/class by exact name
- `symdex_get_file_outline("file.py")` - file structure
- `symdex_search_text("text")` - exact content search
- Use when: you know the name, need structure, or want fast lookups

**OpenViking** - Semantic code understanding:
- `ov search "query"` - semantic search across codebase
- `ov read viking://resources/localshift/path/to/file` - read file content
- `ov abstract viking://...` - get directory summary
- Use when: exploring how/why code works, finding patterns, conceptual queries
- Start if not running: `~/.local/bin/ov-start`

**hass-cli** - Lightweight HA queries:
- `hass-cli state get <entity>` - get entity state
- `hass-cli entity list` - list all entities
- `hass-cli raw GET "api/..."` - direct API access
- Use when: quick read-only HA queries without MCP overhead
- Skill: `homeassistant-cli`

**Quick decision guide:**

| You need... | Use |
|--------------|-----|
| Find function `foo` by name | SymDex |
| Understand how auth works | OpenViking |
| See file structure | SymDex |
| Find code related to "grid charging" | OpenViking |
| Navigate to line N | SymDex |
| Quick HA entity state | hass-cli |
| Control HA device | MCP (`@homeassistant`) |

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
Sensors 35 | Binary Sensors 11 | Switches 8 | Numbers 5 | Selects 2 | Buttons 2 (Total 63)

# AGENTS.md - Agent Coding Guidelines for LocalShift

---

# ⚠️ STOP: MANDATORY FIRST ACTION

**You MUST verify you're NOT on the `main` branch before ANY file edit.**

## Verification Steps

1. Run: `git branch --show-current`
2. If output is `"main"`: **STOP HERE**
   - Create worktree: `git worktree add worktrees/issue-{NNN} -b issue/{NNN}`
   - Change directory: `cd worktrees/issue-{NNN}`
   - Then proceed with edits
3. If output is NOT `"main"`: Proceed with the task

## Why This Matters

- **Git hooks block all commits on main** - No exceptions
- **Editing on main wastes time** - You'll be forced to move changes anyway
- **Main is read-only** - All changes go through PR

**The enforcement is real. Start in a worktree.**

---

## Project Overview

LocalShift is a Home Assistant integration for automated Tesla Powerwall battery control.

**Python**: 3.13+ | **Main code**: `custom_components/localshift/` | **Tests**: `tests/`

**Verify protection is active:** `.githooks/scripts/verify-safety.sh`

---

## ⚠️ PREFER: SymDex for Code Navigation

**Use SymDex MCP tools instead of grep/read for code exploration (~200 tokens vs ~7,500 per lookup).**

| Task | Tool |
|------|------|
| Find function/class | `symdex_search_symbols("optimizer")` |
| See file structure | `symdex_get_file_outline("sensor.py")` |
| Search text | `symdex_search_text("battery_soc")` |
| Get repo structure | `symdex_get_repo_outline()` |

**Note:** `symdex watch` should be running in background (auto-reindexes on file changes).

---

## ⚠️ ENFORCED: Worktree-Only Workflow

**Git hooks in `.githooks/` block all main branch commits and pushes.**

### Before Starting ANY Task

1. Run `git worktree list` - verify you're in a worktree
2. Run `git branch --show-current` - must NOT be `main`
3. If on main, create worktree: `git worktree add worktrees/issue-{NNN} -b issue/{NNN}`

### Emergency Bypass

**Only in emergencies (logged, audit required):** `GIT_EMERGENCY_PUSH=1 git push origin main`

See `.opencode/rules` and `.githooks/README.md` for full workflow.

---

### After Making Changes

1. Lint: `uv run ruff check custom_components/localshift`
2. Format check: `uv run ruff format --check custom_components/localshift`
3. Test: `uv run pytest`
4. **Deploy: Ask user to run** `./deploy.sh --reserve && ./deploy.sh` (do NOT auto-deploy)
5. Check logs: `tail -100 /homeassistant/home-assistant.log | grep -i localshift`

### Deployment Protocol

- **Agents do NOT run deploy.sh directly** - always ask the user to deploy
- When ready to test: "Ready to deploy. Please run: `./deploy.sh --reserve && ./deploy.sh`"
- For HA restart: "Restart recommended. Please run: `./deploy.sh --restart` (you'll be prompted to confirm)"
- User controls all deployments - ensures human-in-the-loop before HA changes

### Deploying to Test Environment

The `test` branch is a persistent branch for continuous testing/deployment. Changes merge into `test` via PR (requires approval), then auto-deploy via watch mode.

#### Workflow

1. **Create worktree from test branch:**
   ```bash
   git worktree add worktrees/deploy-test -b test
   cd worktrees/deploy-test
   ```

2. **Run watch mode:**
   ```bash
   ./deploy.sh --reserve
   ./deploy.sh --watch
   ```

   Watch mode will:
   - Auto-deploy on every code change (file save, etc.)
   - Auto-release reservation after each deploy
   - Auto-reload integration via HA API
   - Wait 2 seconds between deploys (debounce)

3. **Merge changes to test:**
   - Push your worktree branch: `git push origin issue/NNN`
   - Create PR targeting `test` branch
   - PR requires maintainer/lead approval
   - CI must pass before merge is allowed
   - Once merged, the watch process will deploy automatically

4. **Stop watch mode:**
   Press Ctrl+C - the trap will release any active reservation.

**Note:** Only one watch process can run at a time (enforced by reservation system).

### Commit Guidelines

- Reference issue: `Fixes #NNN` or `Closes #NNN`
- Ask user to commit; do not auto-commit

### PR Target Branches

**⚠️ DEFAULT for feature worktrees: target `test`**

When working in a feature worktree (issue/*, feature/*, docs/*, etc.), always create PRs targeting the `test` branch:

```bash
gh pr create --base test --title "..."
```

**Why target test by default:**
- All changes should be validated in live Home Assistant before production
- Enables rapid iteration with watch mode auto-deploy
- Catches integration issues early with real Powerwall/solar data
- Production releases are deliberate, not accidental

**Only target `main` when:**
- Hotfixes that need immediate production deployment
- Documentation-only changes with no code impact
- User explicitly requests direct-to-main release

**Workflow:** Feature worktree → PR to `test` → merge → validate → PR `test`→`main` for production

### PR Creation & CI Monitoring

**After creating a PR:**

1. **Check CI status immediately:**
   ```bash
   ./scripts/check-pr-ci.sh [PR_NUMBER]
   # Or: gh pr checks <PR_NUMBER>
   ```

2. **Interactive monitoring:**
   - Ask user: "Wait for CI results? [W] or Continue [C]"
   - If wait: Monitor every 30s until complete
   - If continue: Check periodically, notify when done

3. **Report results:**
   - ✅ All passed: PR ready for review
   - ❌ Failed: Show failure details, offer to fix

**Full workflow:** `.agents/rules/pr-ci-checks.md`

---

## Testing Best Practices

- Test files: `test_*.py`, classes: `Test*`, functions: `test_*`
- Use fixtures defined in `conftest.py`
- Mock HA with `hass` fixture
- Async tests: `async def test_*` (pytest-asyncio auto-detects)

---

## ⚠️ REQUIRED: Planning Model for Optimizer Changes

**When modifying the DP optimizer, you MUST consult `docs/PLANNING_MODEL.md`.**

### Core Pattern

The optimizer uses a **Soft-Constrained DP** approach:

```
Hard Constraints (feasible_actions)  →  What CAN I do?
Soft Penalties (stage_cost)          →  What SHOULD I do?
Terminal Cost (terminal_cost)        →  What MUST I achieve?
```

### Decision Guide

Before adding any optimizer feature:

| Question | Answer Yes → |
|----------|-------------|
| Is it impossible/forbidden? | Add to `feasible_actions()` |
| Is it a requirement by deadline? | Add to `terminal_cost()` |
| Is it discouraged/preferred? | Add penalty to `stage_cost()` |

### Examples

- **Hard constraint**: Never export during quiet hours → `feasible_actions()`
- **Soft penalty**: Discourage charging during peak demand → `stage_cost()`
- **Terminal cost**: Require minimum SOC for overnight → `terminal_cost()`

**Full documentation:** `docs/PLANNING_MODEL.md`

---

## ⚠️ ENFORCED: TEST-DRIVEN DEVELOPMENT

**TDD is REQUIRED for all code changes. No exceptions.**

### TDD Workflow: RED → GREEN → REFACTOR

**You MUST explicitly state each phase:**

1. **RED Phase**: Write failing test first
   ```bash
   # Create test file
   touch tests/test_new_feature.py
   
   # Write test describing expected behavior
   # Run test to confirm failure
   uv run pytest tests/test_new_feature.py -v
   ```
   State: "TDD cycle: RED - test fails as expected"

2. **GREEN Phase**: Implement minimal solution
   ```bash
   # Implement in production code
   vim custom_components/localshift/new_module.py
   
   # Run test to confirm pass
   uv run pytest tests/test_new_feature.py -v
   ```
   State: "TDD cycle: GREEN - test passes"

3. **REFACTOR Phase**: Clean up while keeping tests green
   ```bash
   # Refactor if needed
   # Run all tests
   uv run pytest
   ```
   State: "TDD cycle: REFACTOR - all tests pass"

### Coverage Requirement

**Minimum 95% test coverage enforced:**

```bash
# Check coverage before commit
uv run pytest --cov=custom_components/localshift --cov-report=term-missing
```

### Pre-Commit Hook Enforcement

**Automatically checks before commit:**
- Test file exists for modified code
- Coverage >= 95%
- Tests pass

### Integration with Workflow

1. Create worktree (per worktrees.md)
2. **Write failing test first** (TDD RED)
3. Implement solution (TDD GREEN)
4. Refactor if needed (TDD REFACTOR)
5. Verify coverage >= 95%
6. Run lint/format
7. Commit and create PR

**Full workflow:** `.agents/rules/tdd-workflow.md`

---

## Notes

- No Cursor or Copilot rules found in repository
- Follow existing code patterns when modifying files
- Update documentation when adding/removing entities:
  - `docs/ENTITY_REFERENCE.md` - Entity definitions
  - `docs/ARCHITECTURE.md` - System architecture
  - `docs/DEVELOPER_GUIDE.md` - Development guide
  - `docs/PLANNING_MODEL.md` - Optimizer extension guide (CRITICAL for optimizer changes)
- Entity counts: Sensors 27, Binary Sensors 10, Switches 8, Numbers 4, Selects 2, Buttons 2 (Total 53)
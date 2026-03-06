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
- Open PR after commit

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

## Notes

- No Cursor or Copilot rules found in repository
- Follow existing code patterns when modifying files
- Update documentation (`docs/ENTITY_REFERENCE.md`, `docs/ARCHITECTURE.md`, `docs/DEVELOPER_GUIDE.md`) when adding/removing entities
- Entity counts: Sensors 27, Binary Sensors 10, Switches 8, Numbers 4, Selects 2, Buttons 2 (Total 53)
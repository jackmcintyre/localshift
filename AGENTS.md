# AGENTS.md - Agent Coding Guidelines for LocalShift

---

# ⚠️ STOP: MANDATORY FIRST ACTION

**You MUST verify you're NOT on the `main` or `test` branches before ANY file edit.**

## Verification Steps

1. Run: `git branch --show-current`
2. If output is `"main"` or `"test"`: **STOP HERE**
   - Create worktree: `git worktree add worktrees/issue-{NNN} -b issue/{NNN} test`
   - Change directory: `cd worktrees/issue-{NNN}`
   - Then proceed with edits
3. If output is NOT `"main"` and NOT `"test"`: Proceed with the task

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
2. Run `git branch --show-current` - must NOT be `main` or `test`
3. If on main or test, create worktree: `git worktree add worktrees/issue-{NNN} -b issue/{NNN} test`

### Emergency Bypass

**Only in emergencies (logged, audit required):** `GIT_EMERGENCY_PUSH=1 git push origin main` or `GIT_EMERGENCY_PUSH=1 git push origin test`

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

## Test Branch Workflow

The development workflow uses two long-lived branches:

- `test`: Integration branch where all changes must be PR'd first. Auto-deploys to a test Home Assistant instance after merges.
- `main`: Production branch for stable releases. Manual deployment.

### Feature Development

1. Create a feature branch from `test`:
   ```bash
   git worktree add worktrees/issue-{NNN} -b issue/{NNN} test
   cd worktrees/issue-{NNN}
   ```
2. Make changes, lint, test, and deploy from the worktree using `./deploy.sh` (ask user). The same HA instance is used for test deployments.
3. Push your feature branch and open a PR targeting `test`.
4. After PR merges to `test`, the **test deployment daemon** automatically deploys to HA (within ~30 seconds). Monitor logs to verify.
5. Once validated in HA, create a PR from `test` → `main`.
6. After merge to `main`, deploy to production manually with `./deploy.sh --reserve && ./deploy.sh`.

### Test Deployment Daemon

The daemon runs in `worktrees/test-deploy` and synchronizes `origin/test` to the HA instance automatically.

- Start: `./scripts/start-test-deploy.sh`
- Stop: `./scripts/stop-test-deploy.sh`
- Status: `./scripts/status-test-deploy.sh`
- Logs: `tail -f logs/test-deploy.log`

The daemon checks `origin/test` every 30 seconds. On changes, it merges and runs `deploy.sh --force`. Merge conflicts stop the daemon and require manual resolution.

## Notes

- No Cursor or Copilot rules found in repository
- Follow existing code patterns when modifying files
- Update documentation (`docs/ENTITY_REFERENCE.md`, `docs/ARCHITECTURE.md`, `docs/DEVELOPER_GUIDE.md`) when adding/removing entities
- Entity counts: Sensors 27, Binary Sensors 10, Switches 8, Numbers 4, Selects 2, Buttons 2 (Total 53)
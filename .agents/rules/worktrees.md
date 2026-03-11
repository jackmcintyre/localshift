---
trigger: always_on
---

# ⚠️ MANDATORY: BEFORE ANY TASK

**This applies to ALL tasks, no matter how simple. Failure to follow this is a process violation.**

## Why Worktrees?

Git hooks block all commits on `main`. Editing on main wastes time - you'll be forced to move changes anyway. Start in a worktree from the beginning.

## Required Workflow

1. **FIRST ACTION:** Run `git branch --show-current` to verify you're NOT on main
2. **If output is "main":**
   - **STOP** - Do not make ANY file edits
   - Create worktree: `git worktree add worktrees/issue-{NNN} -b issue/{NNN}`
   - Change to worktree: `cd worktrees/issue-{NNN}`
3. **If output is NOT "main":**
   - Verify with `git worktree list` that current directory is a worktree
   - Proceed with the task

## Quick Verification

```bash
# Should show branch name (NOT "main")
git branch --show-current

# Should show your current path as a worktree entry
git worktree list
```

---

## ⚠️ PR Creation from Worktrees

**All feature PRs from worktrees MUST target `test`, not `main`.**

When your work is complete and you're asked to create a PR:

```bash
# From your worktree, create PR targeting test branch
gh pr create --base test --title "..."
```

**Why test by default:**
- Changes are validated in live Home Assistant before production
- Enables watch mode for rapid iteration
- Catches integration issues early

**Only target `main` if:**
- User explicitly requests direct-to-main
- Hotfix requiring immediate production deployment
- Documentation-only with no code changes

---

## ⚠️ HARD STOP SIGNALS

**When user issues any of these commands, IMMEDIATELY stop all work and respond:**

| Trigger Phrase | Action |
|---------------|--------|
| "wrong branch" | Stop editing, check git branch, create worktree if needed |
| "stop" | Halt all operations, await further instructions |
| "wait" | Pause operations, ask user for clarification |
| "hold on" | Stop and confirm what to do next |
| "not on main" | Verify branch immediately, create worktree if on main |
| "check branch" | Run `git branch --show-current` and `git worktree list` |
| ALL CAPS warnings | Treat as urgent - stop and acknowledge |

**Response protocol:**
1. STOP immediately - no further edits
2. Acknowledge the signal
3. Run verification commands
4. Explain current state and what action will be taken
5. Wait for user confirmation before proceeding

---

## ⚠️ FOUND CHANGES ON MAIN (Crashed Session / Orphaned Changes)

**If you discover staged or unstaged changes on main (e.g., from a crashed session):**

1. **STOP** - Do not immediately commit
2. **Alert the user** that changes exist on main and ask for direction:
   - Option A: Move changes to a worktree (recommended)
   - Option B: Commit directly on main (only if user explicitly confirms)
3. **To move changes to a worktree:**
   ```bash
   # Stash changes if needed
   git stash push -m "orphaned changes from crashed session"
   
   # Create worktree
   git worktree add worktrees/{task-name} -b task/{task-name}
   
   # In worktree, apply the stash
   cd worktrees/{task-name}
   git stash pop
   ```
4. **Only commit directly on main** if:
   - User explicitly confirms (e.g., "commit as is")
   - Changes are trivial (typo fixes, documentation)
   - You have explained the process violation

---

## Why This Matters

- **Git hooks block main commits** - No exceptions (unless emergency bypass)
- **Time saved** - Editing on main means re-doing work in a worktree
- **Traceability** - Each task has its own branch and history
- **Safety** - Main always reflects production-ready code

**The enforcement is real. Work with it, not against it.**
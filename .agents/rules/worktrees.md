---
trigger: always_on
---

# ⚠️ MANDATORY: BEFORE ANY TASK

**This applies to ALL tasks, no matter how simple. Failure to follow this is a process violation.**

1. **FIRST ACTION:** Run `git worktree list` to check current worktree status
2. **If output shows only the main repo (no worktree path matches current directory):**
   - For GitHub issues: `git worktree add /Users/jackmcintyre/worktrees/issue-{NNN} -b issue/{NNN}`
   - For ad-hoc tasks: `git worktree add /Users/jackmcintyre/worktrees/{task-name} -b task/{task-name}`
3. **Change directory to the worktree before making ANY file changes**
4. **Only then proceed with the actual task**

This ensures isolation between tasks and prevents changes to the main repo.

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
   git worktree add /Users/jackmcintyre/worktrees/{task-name} -b task/{task-name}
   
   # In worktree, apply the stash
   cd /Users/jackmcintyre/worktrees/{task-name}
   git stash pop
   ```
4. **Only commit directly on main** if:
   - User explicitly confirms (e.g., "commit as is")
   - Changes are trivial (typo fixes, documentation)
   - You have explained the process violation

**This prevents accidental commits on main from orphaned changes.**
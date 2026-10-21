---
trigger: on_git_operation
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

**All PRs from worktrees target `main`.**

When your work is complete and you're asked to create a PR:

```bash
# From your worktree, create PR targeting main
gh pr create --base main --title "..."
```

**Why main:**
- `main` is the only integration branch: every merged PR is based on it
- The `main` ruleset requires the owner's review plus passing CI
- Merges to `main` auto-ship via the HA host's pull deployer; there is no separate staging step
- Agents never commit to `main` or `test` directly: always work in a worktree branch and open a PR

The `test` branch is not a PR target. Leave it alone.

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

## PR Body Formatting Guidelines

**ALWAYS use heredoc for PR bodies to preserve markdown formatting:**

```bash
# CORRECT - preserves backticks, pipes, newlines
gh pr create --base main --title "Fix thing" --body "$(cat <<'EOF'
This PR does `thing` with | table | support |
- Item 1
- Item 2
EOF
)"

# WRONG - causes escaped characters like \` and broken tables
gh pr create --base main --title "Fix thing" --body "This has \`backticks\` and broken|tables"
```

**Why this matters:** Shell escaping converts `\`` to `\\`` and breaks table formatting.

**Auto-link PRs to issues:**

Branches named `issue/{NNN}` automatically extract the issue number. Use this pattern:

```bash
# Extract issue number from branch name (e.g., issue/42 → 42)
ISSUE_NUM=$(git branch --show-current | grep -oP 'issue/\K\d+')

# Create PR with auto-linking
gh pr create --base main --title "TITLE" --body "$(cat <<'EOF'
## Summary
Brief description

## Related Issue
Closes #${ISSUE_NUM}

## Changes
- Change 1
- Change 2

## Testing
- [ ] Tested in worktree
- [ ] CI passes
EOF
)"
```

**One-liner version:**
```bash
gh pr create --base main --title "TITLE" --body "$(cat <<'EOF
## Summary
Brief description

## Related Issue
Closes #$(git branch --show-current | grep -oP 'issue/\K\d+')

## Changes
- Change 1

## Testing
- [ ] Tested in worktree
- [ ] CI passes
EOF
)"
```

**Why this works:**
- GitHub recognizes `Closes #NNN` and auto-links the PR to the issue
- When the PR merges, the issue automatically closes
- Branch naming convention (`issue/{NNN}`) makes extraction reliable

---

## Why This Matters

- **Git hooks block main commits** - No exceptions (unless emergency bypass)
- **Time saved** - Editing on main means re-doing work in a worktree
- **Traceability** - Each task has its own branch and history
- **Safety** - Main always reflects production-ready code

**The enforcement is real. Work with it, not against it.**
# Git Hooks - Branch Protection System

This directory contains version-controlled git hooks that enforce the worktree-only workflow and protect the main branch.

## Hooks Overview

### `pre-commit` (HARD BLOCK)
**Purpose:** Prevents ALL commits directly on the main branch.

**Behavior:**
- Exits with code 1 if current branch is `main`
- Logs blocked attempts to `.git-bypass.log`
- No bypass method documented in error message

**Recovery:** If you accidentally commit on main, run `.githooks/scripts/recover-main.sh`

---

### `pre-push` (HARD BLOCK)
**Purpose:** Prevents pushing from main branch (defense-in-depth if pre-commit is bypassed).

**Behavior:**
- Exits with code 1 if pushing from `main` branch
- Logs blocked attempts to `.git-bypass.log`
- Emergency override available (see below)

**Emergency Override:**
```bash
GIT_EMERGENCY_PUSH=1 git push origin main
```
This bypass is logged to `.git-bypass.log` and should only be used in true emergencies.

---

### `post-checkout` (WARNING)
**Purpose:** Inform users when they land on the main branch.

**Behavior:**
- Displays warning message with recent main activity
- Shows recommended worktree command
- Does NOT block - informational only

---

### `prepare-commit-msg` (WARNING)
**Purpose:** Add reviewer awareness for commits that mention main branch.

**Behavior:**
- Prepends warning comment to commit messages mentioning "main"
- Does NOT block - comment for reviewer awareness only

---

## Configuration

To activate these hooks, run once:

```bash
git config core.hooksPath .githooks
```

Verify configuration:

```bash
git config --get core.hooksPath
# Should return: .githooks
```

---

## Scripts

### `scripts/recover-main.sh`
Emergency recovery tool for when main branch gets corrupted.

**Features:**
- Auto-creates backup branch (`backup-main-YYYYMMDD-HHMMSS`)
- Shows current state vs origin/main
- Provides recovery options:
  - A: Reset to origin/main (loses local commits, preserved in backup)
  - B: Pull from origin (merge/rebase)
  - C: Interactive cherry-pick
  - D: Abort

**Usage:**
```bash
.githooks/scripts/recover-main.sh
```

---

### `scripts/verify-safety.sh`
Audit script to verify git protection is configured correctly.

**Checks:**
- `core.hooksPath` is set to `.githooks`
- All hooks exist and are executable
- No worktrees are on main branch
- Current branch is not main
- Main branch sync status with origin

**Usage:**
```bash
.githooks/scripts/verify-safety.sh
```

Run this weekly or after cloning the repository.

---

## Logging

All bypass attempts and recovery actions are logged to `.git-bypass.log` in the project root.

**Log format:**
```
2026-03-03T09:15:23+11:00 - pre-commit blocked commit on main
2026-03-03T09:20:45+11:00 - pre-push blocked push from main to origin
2026-03-03T09:25:12+11:00 - RECOVERY: Created backup branch backup-main-20260303-092512
2026-03-03T09:26:30+11:00 - RECOVERY: Reset main to origin/main (was ahead by 3 commits)
```

---

## Workflow Rules

### ✅ DO:
1. Create worktree for each task: `git worktree add worktrees/issue-{NNN} -b issue/{NNN}`
2. Work in the worktree directory
3. Commit and push from the issue branch
4. Create pull request for review
5. Merge via GitHub UI after approval

### ❌ DON'T:
1. Commit directly on main branch (blocked)
2. Push from main branch (blocked)
3. Create worktrees on main branch
4. Use `git commit --no-verify` to bypass hooks

---

## Emergency Procedures

### If you accidentally commit on main:

1. **Don't panic** - the commit didn't push
2. Run: `.githooks/scripts/recover-main.sh`
3. Choose option A to reset main to origin/main
4. Your work is preserved in the backup branch

### If you need to emergency-push from main:

```bash
GIT_EMERGENCY_PUSH=1 git push origin main
```

This is logged and should only be used when:
- GitHub is down and you need to restore from backup
- You're the only person who can fix a critical production issue
- Explicitly authorized by project maintainer

---

## Troubleshooting

**Hooks not running?**
```bash
# Check configuration
git config --get core.hooksPath

# Should return: .githooks
# If not, set it:
git config core.hooksPath .githooks
```

**Hooks not executable?**
```bash
chmod +x .githooks/*
chmod +x .githooks/scripts/*
```

**Worktree shows "prunable"?**
```bash
# Check if branch still exists
git branch -a | grep <branch-name>

# If branch was merged and deleted, remove worktree:
rm -rf worktrees/<worktree-name>
```

---

## For Project Maintainers

To update hooks:
1. Edit files in `.githooks/`
2. Commit and push changes
3. Team members pull and hooks are automatically updated

This is the advantage of version-controlled hooks vs `.git/hooks/`.

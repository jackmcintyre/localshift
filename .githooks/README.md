# Git Hooks - Branch Protection System

This directory contains version-controlled git hooks that enforce the worktree-only workflow and protect the `main` and `test` branches.

## Protected Branches

- **`main`** - Production branch
- **`test`** - Continuous deployment branch for testing

Both branches require changes to go through pull requests from feature worktrees.

## Hooks Overview

### `pre-commit` (HARD BLOCK)
**Purpose:** Prevents ALL commits directly on protected branches (`main`, `test`).

**Behavior:**
- Exits with code 1 if current branch is `main` or `test`
- Logs blocked attempts to `.git-bypass.log`
- No bypass method documented in error message

**Recovery:** If you accidentally commit on a protected branch, run `.githooks/scripts/recover-main.sh`

---

### `pre-push` (HARD BLOCK)
**Purpose:** Prevents pushing from protected branches (defense-in-depth if pre-commit is bypassed).

**Behavior:**
- Exits with code 1 if pushing from `main` or `test` branch
- Logs blocked attempts to `.git-bypass.log`
- Emergency override available (see below)

**Emergency Override:**
```bash
GIT_EMERGENCY_PUSH=1 git push origin main
GIT_EMERGENCY_PUSH=1 git push origin test
```
This bypass is logged to `.git-bypass.log` and should only be used in true emergencies.

---

### `post-checkout` (WARNING)
**Purpose:** Inform users when they land on a protected branch.

**Behavior:**
- Displays warning message with recent branch activity
- Shows recommended worktree command
- Does NOT block - informational only

---

### `prepare-commit-msg` (WARNING)
**Purpose:** Add reviewer awareness for commits that mention protected branches.

**Behavior:**
- Prepends warning comment to commit messages mentioning "main" or "test"
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
- No worktrees are on protected branches (`main`, `test`)
- Current branch is not protected (`main`, `test`)
- Protected branches sync status with origin

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
2026-03-03T09:16:45+11:00 - pre-commit blocked commit on test
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
4. Create pull request targeting `test` (for testing) or `main` (for production)
5. Merge via GitHub UI after approval

### ❌ DON'T:
1. Commit directly on `main` or `test` branches (blocked)
2. Push from `main` or `test` branches (blocked)
3. Create worktrees on protected branches
4. Use `git commit --no-verify` to bypass hooks

---

## Emergency Procedures

### If you accidentally commit on a protected branch:

1. **Don't panic** - the commit didn't push
2. Run: `.githooks/scripts/recover-main.sh`
3. Choose option A to reset the branch to origin
4. Your work is preserved in the backup branch

### If you need to emergency-push from a protected branch:

```bash
GIT_EMERGENCY_PUSH=1 git push origin main
GIT_EMERGENCY_PUSH=1 git push origin test
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

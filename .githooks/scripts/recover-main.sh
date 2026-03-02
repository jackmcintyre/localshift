#!/bin/bash
# Emergency recovery script for main branch corruption
# Auto-creates backup branch before any destructive operation

set -e

BACKUP_BRANCH="backup-main-$(date +%Y%m%d-%H%M%S)"
LOG_FILE=".git-bypass.log"

log_action() {
    echo "$(date -Iseconds) - RECOVERY: $1" >> "$LOG_FILE"
}

echo "=========================================="
echo "Main Branch Recovery Tool"
echo "=========================================="
echo ""

# Check current state
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
    echo "ERROR: Not on main branch (currently on $CURRENT_BRANCH)"
    echo "Checkout main first: git checkout main"
    exit 1
fi

# Get commit counts
LOCAL_COMMITS=$(git rev-list --count HEAD)
ORIGIN_COMMITS=$(git rev-list --count origin/main 2>/dev/null || echo "0")
AHEAD_COUNT=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo "0")
BEHIND_COUNT=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo "0")

echo "Current state:"
echo "  Local commits: $LOCAL_COMMITS"
echo "  Origin commits: $ORIGIN_COMMITS"
echo "  Ahead of origin: $AHEAD_COUNT"
echo "  Behind origin: $BEHIND_COUNT"
echo ""

if [ "$AHEAD_COUNT" -eq 0 ] && [ "$BEHIND_COUNT" -eq 0 ]; then
    echo "main is in sync with origin/main - no recovery needed"
    exit 0
fi

# Show recent commits
echo "Recent commits on main:"
git log --oneline -5 | sed 's/^/  /'
echo ""

# Auto-create backup branch
echo "Creating backup branch: $BACKUP_BRANCH"
git branch "$BACKUP_BRANCH"
log_action "Created backup branch $BACKUP_BRANCH from current main"
echo "Backup created successfully"
echo ""

# Present options
echo "Recovery options:"
echo ""
echo "  A) Reset main to origin/main (loses $AHEAD_COUNT local commits, preserved in backup)"
echo "  B) Pull from origin (merge/rebase, may create merge commit)"
echo "  C) Interactive cherry-pick (select specific commits to keep)"
echo "  D) Abort (no changes, backup branch still created)"
echo ""
read -p "Choose option (A/B/C/D): " choice

case "$choice" in
    A|a)
        echo ""
        echo "Resetting main to origin/main..."
        git reset --hard origin/main
        log_action "Reset main to origin/main (was ahead by $AHEAD_COUNT commits)"
        echo "Reset complete. Backup preserved in: $BACKUP_BRANCH"
        echo ""
        echo "To review what was reset:"
        echo "  git show $BACKUP_BRANCH"
        echo "  git diff origin/main..$BACKUP_BRANCH"
        ;;
    B|b)
        echo ""
        echo "Pulling from origin (using --rebase to avoid merge commits)..."
        git pull --rebase origin main
        log_action "Pulled from origin/main with rebase"
        echo "Pull complete."
        ;;
    C|c)
        echo ""
        echo "Use git cherry-pick to selectively apply commits from $BACKUP_BRANCH"
        echo "After cherry-picking, reset main: git reset --hard origin/main"
        echo "Commits available for cherry-pick:"
        git log --oneline origin/main..$BACKUP_BRANCH | sed 's/^/  /'
        ;;
    D|d|*)
        echo "Aborted. Backup branch preserved: $BACKUP_BRANCH"
        echo "To manually reset later: git reset --hard origin/main"
        ;;
esac

echo ""
echo "=========================================="
echo "Recovery complete"
echo "=========================================="
log_action "Recovery completed - choice: $choice"

#!/bin/bash
set -euo pipefail

# Status of Test Deploy Daemon

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
PID_FILE="$REPO_ROOT/run/test-deploy.pid"
LOG_FILE="$REPO_ROOT/logs/test-deploy.log"
WORKTREE_DIR="$REPO_ROOT/worktrees/test-deploy"

echo "=== Test Deploy Daemon Status ==="
echo ""

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Status: RUNNING (PID $PID)"
    else
        echo "Status: STOPPED (stale PID file)"
    fi
else
    echo "Status: STOPPED"
fi

echo ""
if [ -d "$WORKTREE_DIR" ]; then
    echo "Worktree: $WORKTREE_DIR"
    cd "$WORKTREE_DIR"
    BRANCH=$(git rev-parse --abbrev-ref HEAD)
    COMMIT=$(git log -1 --oneline)
    echo "Branch: $BRANCH"
    echo "Latest: $COMMIT"
    echo "Origin/test:"
    git log -1 --oneline origin/test 2>/dev/null || echo "  (unavailable)"
else
    echo "Worktree: NOT FOUND"
fi

echo ""
echo "Log file: $LOG_FILE"
if [ -f "$LOG_FILE" ]; then
    echo "--- Last 10 lines ---"
    tail -n 10 "$LOG_FILE"
else
    echo "Log file not created yet"
fi

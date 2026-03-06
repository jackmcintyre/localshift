#!/bin/bash
set -euo pipefail

# Start Test Deploy Daemon
# Creates worktree if needed and starts the background daemon that auto-deploys from test branch.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
WORKTREE_DIR="$REPO_ROOT/worktrees/test-deploy"
PID_FILE="$REPO_ROOT/run/test-deploy.pid"
LOG_FILE="$REPO_ROOT/logs/test-deploy.log"

# Ensure run and logs directories exist
mkdir -p "$REPO_ROOT/run" "$REPO_ROOT/logs"

# Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "Daemon already running (PID $OLD_PID)"
        exit 0
    else
        echo "Stale PID file found, removing"
        rm -f "$PID_FILE"
    fi
fi

# Ensure worktree exists
if [ ! -d "$WORKTREE_DIR" ]; then
    echo "Creating worktree at $WORKTREE_DIR"
    git worktree add "$WORKTREE_DIR" test
fi

# Enter worktree and ensure on test branch, then pull latest
cd "$WORKTREE_DIR"
git checkout test >/dev/null 2>&1
git fetch origin test >/dev/null 2>&1
git pull origin test >/dev/null 2>&1

# Start daemon
echo "Starting test deploy daemon..."
cd "$WORKTREE_DIR"
nohup ./scripts/run-test-deploy-daemon.sh >> "$LOG_FILE" 2>&1 &
DAEMON_PID=$!
echo $DAEMON_PID > "$PID_FILE"
echo "Daemon started (PID $DAEMON_PID)"
echo "Log file: $LOG_FILE"
echo ""
echo "To monitor: tail -f $LOG_FILE"
echo "To stop:   $REPO_ROOT/scripts/stop-test-deploy.sh"

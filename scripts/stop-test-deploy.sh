#!/bin/bash
set -euo pipefail

# Stop Test Deploy Daemon
# Kills the background daemon and releases any HA reservation.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
PID_FILE="$REPO_ROOT/run/test-deploy.pid"
WORKTREE_DIR="$REPO_ROOT/worktrees/test-deploy"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    echo "Stopping daemon (PID $PID)..."
    kill "$PID" 2>/dev/null || true
    # Wait a bit
    sleep 2
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Daemon still running, forcing kill..."
        kill -9 "$PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    echo "Daemon stopped"
else
    echo "No PID file - daemon may not be running"
fi

# Release reservation if any (just in case)
if [ -d "$WORKTREE_DIR" ]; then
    echo "Releasing any HA reservation..."
    (cd "$WORKTREE_DIR" && ./deploy.sh --release > /dev/null 2>&1 || true)
    echo "Reservation released (if any)"
fi

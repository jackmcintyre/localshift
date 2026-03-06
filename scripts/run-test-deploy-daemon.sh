#!/bin/bash
set -euo pipefail

# Run Test Deploy Daemon
# This script runs in a loop, checking for updates on origin/test and deploying them.
# Intended to be run in the background by start-test-deploy.sh.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKTREE_DIR="$SCRIPT_DIR/../worktrees/test-deploy"
cd "$WORKTREE_DIR"

# Ensure we're on test branch
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "test" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Daemon worktree is on branch '$CURRENT_BRANCH', not 'test'" >&2
    exit 1
fi

# Log function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "Test deploy daemon started (branch: test)"
log "Monitoring origin/test for changes"
log "Worktree: $WORKTREE_DIR"

# Main loop
while true; do
    # Fetch updates from origin
    if git fetch origin test >/dev/null 2>&1; then
        LOCAL=$(git rev-parse @)
        REMOTE=$(git rev-parse origin/test 2>/dev/null || echo "")
        
        if [ -n "$REMOTE" ] && [ "$LOCAL" != "$REMOTE" ]; then
            log "Changes detected on origin/test"
            log "Local:  $LOCAL"
            log "Remote: $REMOTE"
            log "Merging..."
            
            if git merge origin/test --no-edit; then
                log "Merge successful, deploying..."
                ./deploy.sh --no-reload --force
                log "Deployment complete"
            else
                log "MERGE FAILED! Manual intervention required."
                log "Resolve conflicts in $WORKTREE_DIR, then restart daemon."
                exit 1
            fi
        fi
    else
        log "WARNING: git fetch failed"
    fi
    
    sleep 30
done

# Test Deployment Daemon

This document describes the automated test deployment system for LocalShift.

## Overview

The test deployment daemon monitors the `test` branch for updates and automatically deploys them to the Home Assistant test instance. This allows rapid validation of changes before promoting to production.

## Requirements

- The HA instance must be accessible at `/homeassistant` (or set `HA_CONFIG`).
- Environment variables `HA_LONG_LIVED_TOKEN`, `HA_URL` (optional, defaults to http://homeassistant:8123) must be set when starting the daemon.
- The `test` branch must exist and be tracked on `origin`.

## Directory Structure

- `worktrees/test-deploy/` - Dedicated worktree for the daemon (tracking `test` branch)
- `logs/test-deploy.log` - Daemon output log
- `run/test-deploy.pid` - PID file for the running daemon

## Starting the Daemon

```bash
./scripts/start-test-deploy.sh
```

This script will:
- Create the `worktrees/test-deploy` worktree if it doesn't exist (from `test` branch)
- Pull the latest from `origin/test`
- Start the `run-test-deploy-daemon.sh` process in the background
- Write its PID to `run/test-deploy.pid`

You can start the daemon from any directory within the repository.

## Stopping the Daemon

```bash
./scripts/stop-test-deploy.sh
```

This will:
- Send SIGTERM to the daemon process (followed by SIGKILL if needed)
- Remove the PID file
- Release any HA reservation (just in case)

## Checking Status

```bash
./scripts/status-test-deploy.sh
```

Shows:
- Whether the daemon is running (PID)
- Worktree location and current branch
- Latest commit on `test` and `origin/test`
- Tail of the log file

## How It Works

The daemon (`scripts/run-test-deploy-daemon.sh`) runs an infinite loop:

1. `git fetch origin test` to get updates.
2. Compare local HEAD with `origin/test`.
3. If different, attempt `git merge origin/test --no-edit`.
   - On success, runs `./deploy.sh --no-reload --force` to copy files to HA and reload integration. The `--force` flag bypasses reservation checks.
   - On failure (merge conflict), logs an error and exits. The daemon stops; you must resolve the conflict manually and restart.
4. Sleep 30 seconds, repeat.

The daemon runs with `set -euo pipefail` and traps signals for clean exit.

## Environment Variables

The daemon inherits environment from the shell that started it. The `deploy.sh` script uses:

- `HA_CONFIG` - Path to Home Assistant config directory (default: `/homeassistant`)
- `HA_URL` - Home Assistant API URL (default: `http://homeassistant:8123`)
- `HA_LONG_LIVED_TOKEN` - Long-lived access token for API calls (required for auto-reload)

Set these before running `start-test-deploy.sh`, e.g.:

```bash
export HA_LONG_LIVED_TOKEN="your_token_here"
export HA_URL="http://homeassistant:8123"
./scripts/start-test-deploy.sh
```

If the token is not set, `deploy.sh` will skip API reload (you may need to manually restart HA).

## Logs

The daemon writes to `logs/test-deploy.log`. Use `tail -f` to monitor live:

```bash
tail -f logs/test-deploy.log
```

Log entries include timestamps and messages about fetch, merge, deployment, errors.

## Manual Deployment from Test Worktree

If you need to manually force a deployment (e.g., after resolving a conflict), you can run directly from the worktree:

```bash
cd worktrees/test-deploy
./deploy.sh --force
```

This bypasses the reservation system and immediately copies the current worktree state to HA.

## Handling Merge Conflicts

If the daemon exits due to a merge conflict, you'll see an error in the logs. To resolve:

1. Stop the daemon if still running: `./scripts/stop-test-deploy.sh`
2. Enter the worktree: `cd worktrees/test-deploy`
3. Check status: `git status` - you'll see conflicting files.
4. Resolve conflicts manually (edit files, `git add`, `git commit`).
5. Ensure you're on the `test` branch and it's up to date with `origin/test`.
6. Restart the daemon: `./scripts/start-test-deploy.sh`

The daemon will resume normal operation.

## Troubleshooting

**Daemon not starting?**
- Check if a stale PID file exists in `run/test-deploy.pid`; delete it.
- Ensure `git worktree add` works (you have permission, etc.)

**No deployments happening?**
- Verify the daemon is running: `ps -p $(cat run/test-deploy.pid)`
- Check that the worktree is on `test` branch: `git -C worktrees/test-deploy branch --show-current`
- Ensure `origin/test` is set and reachable: `git -C worktrees/test-deploy fetch origin test`
- Check the log for errors.

**HA not updating?**
- Confirm `HA_LONG_LIVED_TOKEN` is set and valid.
- Check HA logs: `tail -f /homeassistant/home-assistant.log | grep -i localshift`
- Verify the `deploy.sh` script runs without errors manually.

**Daemon died unexpectedly?**
- Look at `logs/test-deploy.log` for the last messages.
- Common causes: git fetch failure, merge conflict, deploy error.
- After fixing, restart with `./scripts/start-test-deploy.sh`.

## Resetting the Daemon

If you need to start fresh:

```bash
./scripts/stop-test-deploy.sh
rm -rf worktrees/test-deploy
./scripts/start-test-deploy.sh
```

**Caution**: This discards any uncommitted changes in the test-deploy worktree (should be none, but just in case).

## Interaction with CI/CD

- PRs to `test` trigger CI (lint, test, duplicate check).
- The daemon only deploys after PR merges to `test` (i.e., when `origin/test` advances).
- There may be a delay of up to 30 seconds from merge to deployment.

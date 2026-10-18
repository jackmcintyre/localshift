---
name: deploy-and-validate
description: Use when asked to deploy LocalShift, ship a change to Home Assistant, or validate a deploy. Agents never deploy; this explains how a change goes live instead.
---

# Deploy and Validate (retired: agents do not deploy)

This skill used to walk an agent through reserving Home Assistant, running
`deploy.sh` and checking the result. That path is retired. **Agents never
deploy LocalShift, never run `deploy.sh` or a rollback, and never restart HA.**

## How a change goes live

1. The change merges to `main` through a reviewed PR.
2. The HA host's pull deployer ships `main` merges. It waits for its own gates
   (the preflight in `scripts/deploy_check.py --preflight`, its hold period and
   the reservation), copies the integration, and runs
   `scripts/write_build_stamp.sh` so the running copy can report its commit.
3. Jack restarts HA. The new code loads at that restart, not before.
4. After the restart the deployer judges the result with
   `scripts/deploy_check.py --before/--after --expect-recompile`. On a failed
   check it restores the previous files itself and asks for another restart.

So approving a LocalShift PR means "live at the next HA restart once the pull
deployer has shipped it".

## What an agent can do

- Open and update PRs. That is the whole of "deploying" from an agent's side.
- Keep `scripts/deploy_check.py` and `scripts/write_build_stamp.sh` working:
  the deployer runs them, so their arguments and exit codes are an interface.
  They take every path and credential from arguments or the environment
  (`--component-dir`, `HA_URL`/`HA_TOKEN`); never add a machine-specific
  default.
- Read which commit is running from `sensor.localshift_build`, when you have
  read access to HA. Do not write to HA to find out.

`deploy.sh` is kept only for a human in an emergency and refuses to run without
`--i-am-not-the-deployer`. That flag is not for agents.

# Mac deploy kit (backlog run 2), archived for the devbox move

These are the scripts that actually deployed LocalShift to Home Assistant during
backlog run 2 (14–17 Oct 2026). They ran from the Mac checkout's untracked
`tmp/run/` directory, and are copied here verbatim, as a reference for designing
the pull deployer on nectaron. They are not meant to run from this path: the
hard-coded paths (`/Users/jackmcintyre/localshift`, `tmp/run`, and
`/Volumes/appdata/Home-Assistant-Container`, the SMB mount of the HA config)
are Mac-only.

The runbook and handoff notes that drove them are in `docs/deploy-run/`.

## Scripts

| Script | Role |
|---|---|
| `deploy_step.sh <commit> <label>` | Everything up to the restart. Runs `scripts/deploy_check.py --preflight` (refuses 15:00–21:00 and within 30 min of a charge or backstop). Verifies the share is byte-identical to `live_commit`. Reserves HA through `deploy.sh --reserve` from a detached worktree at `<commit>`, dumps entities (before), and copies with `deploy.sh --no-reload`, which backs up to `$HA_CONFIG/backups/localshift.backup.<ts>` and prunes after 7 days. It then waits for the SMB share to be quiet for 60 s and verifies the copy byte for byte. Exit non-zero means do not restart. |
| *(restart)* | Done by the agent through the Home Assistant MCP, `ha_restart(confirm=True)`, after the HA pre-check in the runbook. Never between 01:30 and 02:00. |
| `post_step.sh [deploy_check flags]` | Waits for a log that starts after the restart and shows two plans. It then dumps entities (after) and runs `deploy_check.py --before/--after --expect-recompile --wait-settled 900`. On pass it promotes `pending_commit` to `live_commit` and releases the reservation. |
| `rollback.sh` | Moves the failed component aside and restores `last_backup`. The caller then restarts and re-runs `post_step.sh`. |
| `fetch_log.py` | Fetches the live HA log over the API, because the SMB share serves stale pages after a restart. |

Credentials: `fetch_log.py` and `scripts/ha_entity_dump.py` resolve the HA URL
and token at run time from `HA_URL`/`HA_TOKEN`, or from the `home-assistant`
MCP entry in the Claude config. Nothing here stores a token.

`tmp/run/deploy_check.py` on the Mac is an older (14 Oct) copy that the
scripts do not call. They use the tracked `scripts/deploy_check.py`.

## State files (not copied)

These lived in `tmp/run/`. Values as of 18 Oct 2026, after the 17 Oct deploy:

| File | Held | Value |
|---|---|---|
| `live_commit` | Commit currently on the HA share, verified by the last passing `post_step.sh` | `7596939c43d2cc8a3d35495a46107f57f47f2d57` (#1197 + #1201 + #1202) |
| `pending_commit` | Commit copied but not yet judged | same as `live_commit` (no step in flight) |
| `last_backup` | Backup that `rollback.sh` restores | `backups/localshift.backup.20261017_211539` under the HA config |
| `before_dump`, `after_dump` | Paths of the entity dumps compared by `deploy_check.py` | `localshift-entities-20261017-211539.json`, `…-212516.json` |
| `restart_requested_at` | Epoch of the last restart request, used to ignore pre-restart logs | `1792232342` (17 Oct 2026 21:19:02 AEDT) |
| `deploy-wt/`, `live-tree/`, `new-tree/` | Scratch worktree and `git archive` extracts used for the byte-for-byte diffs | regenerated every step |

The reservation file lives on the share, at
`custom_components/localshift.reserve` (30-minute TTL). It was released after
the 17 Oct pass.

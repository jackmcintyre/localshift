# LocalShift handoff: backlog run 2 close-out (17 Oct 2026, ~21:40)

For a fresh session in `~/localshift`. Run 2's full record is `tmp/backlog-run.md` (sections "Run 2 needs Jack", "Run 2 items", "Run 2 deploys", "Run 2 log"); the plan it ran to is `tmp/evening-runbook-2026-10-15.md`. The pipeline, confidence classes and safety rules are in `tmp/localshift-handoff-backlog-unattended-2026-10-14.md` and `-2026-10-15.md`. This file says only where things stand now and what is left.

## State (verified from the system, 17 Oct 21:30-21:40)

- Live = `7596939c` = origin/main (#1197 + #1201 + #1202 on top of #1196/#1199; #1203 is `.github/workflows` only). `tmp/run/live_commit` = 7596939c. Reservation released.
- Rollback target for tonight's step: `backups/localshift.backup.20261017_211539` (== 1469cad6), recorded in `tmp/run/last_backup`. Old card file: `backups/localshift-cards.js.v2.0.0.20261017`.
- HA restarted once tonight (21:19:48). deploy_check 15/17; both FAILs are the pre-accepted new switches (health 68/68 -> 70/70; config_options `demand_window_from_amber` and `planner_knows_current_mode` None -> False). Output: `tmp/run/post_step-7596939c.out`.
- Switches: `planner_knows_current_mode` off, `demand_window_from_amber` off, `spike_discharge_enabled` off (#1199), automation on, dry run off, integration_status ok.
- #1201 storage steps applied: `www/localshift/localshift-cards.js` == 7596939c's copy, Lovelace resource `42ac68aa…` -> `/local/localshift/localshift-cards.js?v=2.1.0`, served file confirmed. Plan decisions carry `is_demand_window`.
- Base Amber integration still installed (by design).

## Not done tonight, and why

1. **Forecast-key repoint (runbook step 3).** Not possible through the options flow: under `pricing_data_source = amber_express` the Entity Mappings form has no `pricing_general_forecast` / `pricing_feed_in_forecast` fields (`custom_components/localshift/config_flow/schemas.py:226`). Stored data still says `sensor.100h_general_forecast` / `sensor.100h_feed_in_forecast`. An options save also does not reload the integration (`custom_components/localshift/__init__.py:64`). Options and recommendation are in "Run 2 needs Jack". Recommended fix: a small PR adding a one-time config-entry migration that rewrites both keys to the Express price ids when the source is Express, with a test that the validator is clean with base Amber absent.
2. **Base Amber removal.** Jack's call. Blocked on item 1 (else both keys go BROKEN and status degraded) and on the homeassistant-100h side's "day of verified Energy costs on Express" report, which this run did not find. Energy prefs already read the Express price sensors.

## Watch items

- Start-up race seen tonight: Express price sensors unavailable for ~2 s after the restart (21:20:17-19), so the first two plans skipped. Harmless; worth one look if it repeats on the next restart.
- #1159 short charges: 15 Oct 1 (4 min 59 s), 16 Oct 1 (45 s, the day's only charge), 17 Oct 0 (no charges at all). Same plan-flip shape each time, not a commit-path fault. Still class C watch, no build.
- Before anyone turns on `switch.localshift_demand_window_from_amber`: capture a 13 Oct-shaped day and replay it; off-season the switch alone is ~$1/10 days dearer (see #1201 notes in the run log). #1104 price-led charging deferred to after 1 Nov (branch `study/1104-offseason-gates`).
- Nothing commented on #1158 / #1104 / #1202 for tonight's deploy (the scheduled task did not ask for it). Post a deploy-result comment on each if the next session closes them out.
- Devbox Projects Coordinator was sent the pre-restart CLEAR line only; it has not been sent a post-night pass/fail line.

## Rules still in force

One restart at a time; HA pre-check (homeassistant-100h merges in the last 10 min, `sensor.house_live_commit` == main and `deployed`) before every restart; never 01:30-02:00; never edit a script while it runs; `post_step.sh` does not record live_commit or release on a non-zero exit, so a pre-accepted FAIL needs `cp tmp/run/pending_commit tmp/run/live_commit` and `HA_CONFIG=/Volumes/appdata/Home-Assistant-Container ./deploy.sh --release` from `tmp/run/deploy-wt`. No attribution trailers. The deploy gate still has no `--allow-added-entity` / `--allow-config-change` flag (the 15 Oct "needs Jack" item); adding one is the obvious small tooling PR.

## Suggested skills

- `ha-entity-dump`: before/after snapshots for the next deploy.
- `code-review` or `bmad-review-edge-case-hunter`: independent review of the forecast-key migration PR.
- `routed-build` or `bmad-quick-dev`: building that PR.
- `handoff`: when the next session ends.

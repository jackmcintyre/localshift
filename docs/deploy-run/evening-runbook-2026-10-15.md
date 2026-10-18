# LocalShift evening runbook: 15 and 16 Oct 2026

Written 15 Oct about 11:00 by backlog run 2. Two scheduled triggers in the run-2 session (21:07 on 15 Oct and 21:07 on 16 Oct) point here. If that session has died, a fresh session in `~/localshift` can run this file as it stands. Read `tmp/backlog-run.md` ("Run 2" section) first, then the two handoffs it names. Rules that apply: one restart at a time, never edit a script while it runs, roll back and stop on any failed invariant that is not explained by inputs, and no attribution trailers.

Scripts (reuse, do not edit while running): `tmp/run/deploy_step.sh <commit> <label>` (preflight, share == `tmp/run/live_commit`, reserve, before dump, copy, wait for the share to go quiet, verify copy), restart via the Home Assistant MCP `ha_restart(confirm=True)`, `tmp/run/post_step.sh [flags]` (waits for a log that starts after the restart with two plans, then runs `deploy_check --wait-settled 900`, releases on pass), and `tmp/run/rollback.sh` on fail (then restart, then re-run post_step.sh to prove the old state is back, revert the merge on main, comment, STOP).

## Before anything (both nights)

0. HA-wide rules from the 🏠 Home Assistant Coordinator session (15 Oct): never restart HA between 01:30 and 02:00 (daily backup to local and S3, ~17 min). A restart applies any HA config copied but not yet reloaded, so before each restart message the HA coordinator, which since 15 Oct ~17:30 is session `local_c94d274b-b41b-45da-836e-2e7765a2f52b` ("Home Assistant Coordinator approach"; SendMessage to that id; the old "🏠 Home Assistant Coordinator" seat is retired) to confirm no Mac lighting-deploy is waiting on a reload; if it does not answer within 10 minutes, check `git -C ~/projects/homeassistant-100h log -1` and its open deploys, and proceed only if nothing is in flight.

1. Re-verify: share == `tmp/run/live_commit` (deploy_step.sh does this and refuses otherwise). Check `__pycache__` on the share is newer than the last restart (first timestamp of the API log).
2. Confirm no hold message from the "Localshift handoff plan assessment" session (ReadNotifications). Honour any hold.
3. `uv run python scripts/deploy_check.py --preflight` must pass (outside 15:00-21:00, no charge or backstop within 30 min).

## 15 Oct, 21:07: one restart, neutral and small batch

Commit: main tip at 21:07, which must contain ONLY these since `c0de4089`: #1194 (ecf75da9), #1193 (05885ee3), #1195 (ede344ab), #1198 (#987 last rows), #1192 (tooling). Check with `git log --oneline c0de4089..origin/main`. If #1196, #1197, #1199 or #1201 appear, STOP: they must not deploy before the night of 16 Oct (#1188 hold). If #1198 has not merged, deploy ede344ab.

Nothing touches `state/machine.py`: verify with `git diff --stat c0de4089 <commit> -- custom_components/localshift/state/machine.py` (must be empty).

Expected check outcome: all invariants pass. No entity or attribute removed on purpose (#1193 removes CoordinatorData fields, not sensor attributes). Parity numbers on the optimizer summary will move (#1195: night and negative-FIT slots no longer counted as defaults); that is a value change, not a key change. Projected DW entry may move with forecasts: accept a failure on that check alone only with input evidence, as on 14 Oct.

After PASS:
- Merge, in this order, each only after CI is green on top of the previous: #1196, #1199, #1197, #1201 (Jack approved #1201 'merge, switch stays off' on 15 Oct; it deploys 17 Oct) (Jack approved #1199 on 15 Oct; #1196 and #1197 approved by review and Jack's 15 Oct decisions). Do NOT deploy them tonight.
- Comment on #981, #987, #1005 with the deploy result. Update `tmp/backlog-run.md`.
- Keep the Mac awake for tomorrow night: start `caffeinate -i -s -t <seconds until 16 Oct 23:59>` as a background task (today's assertion ends about 23:40). The 16 Oct trigger only fires if the machine is awake.
- #1159 watch: count boosts/charges shorter than 5 minutes today from `select.localshift_battery_mode` history (00:00 to now); add the count to the run log. Known so far: grid_charging 09:25:39-09:30:37 (4 min 58 s).

## 16 Oct, 21:07: decision-path deploys, one at a time

Only after 21:00 on 16 Oct (#1188 evidence hold ends). Read the #1188 watch session's findings first; if it reports a problem in the decision-token path, hold and park for Jack.

1. #1196 (#1152 cancel transition on unload) alone. Check passes as normal; it only acts on unload.
2. #1199 (#1120 spike export ships off) alone. Expected and intended: `switch.localshift_spike_discharge_enabled` goes from on to off after the migration; the deploy_check switch invariant covers automation/dry-run/mode only, so that change is expected. Confirm the log shows the migration warning once.
3. #1197 (#1158 planner knows current mode, switch default off) alone. Expected: one NEW entity, `switch.localshift_planner_knows_current_mode`, state off; plans unchanged with it off.

Restart budget: three restarts on 16 Oct, nothing more. If time runs past about 23:00, stop and leave the rest for the next evening.

## Rollback target

Before 15 Oct 21:07: `backups/localshift.backup.20261015_075255` is the PREVIOUS state; deploy_step.sh records the one it creates in `tmp/run/last_backup`, which is the one to restore.

## 17 Oct, evening (not yet scheduled): #1104 narrow slice + base-Amber repoint
- 16 Oct 22:58 change (Jack): #1197 did not deploy on 16 Oct (run stopped after #1199; Jack then accepted #1199's 16/17 as a pass). #1197 and #1201 deploy TOGETHER as one step (a98f5dd0; #1201 sits on top of #1197). Intended new entities, both OFF: switch.localshift_planner_knows_current_mode, switch.localshift_demand_window_from_amber. Pre-accepted check lines: those two new entities, and the two pricing_* config_options lines from the options save. At most three restarts.

- Deploy #1201 (#1104 slice; approved round 3, +86 lines; switch.localshift_demand_window_from_amber ships OFF) alone. With the switch off, plans must be identical. Expected new: one switch entity (off), `is_demand_window` on each plan decision. Two HA-storage steps the PR could not do: copy `localshift-cards.js` into `<config>/www/localshift/` and bump the LocalShift dashboard resource to `?v=2.1.0`. Expected live side effect of the spike fix: Express spike intervals now set the two spike diagnostic binary sensors (plans unaffected).
- Before anyone turns the switch on: capture a 13 Oct-shaped day (cheapest power inside 15:00-21:00) as a replay and measure it; off-season the switch alone is ~$1/10 days dearer because the 95% target is what funds midday charging (Jack's 15 Oct note).
- Then repoint LocalShift's `pricing_general_forecast` -> sensor.amber_express_100h_general_price and `pricing_feed_in_forecast` -> sensor.amber_express_100h_feed_in_price through LocalShift's options flow (entity_mappings step; saving reloads the integration, so count it as a restart and run the before/after check). BOTH keys in the SAME save: once base Amber is removed, any key still naming a sensor.100h_* entity goes BROKEN in EntityValidator and flips integration_status to degraded (#1202 review). Verify both in the stored entry data after the save.
- Also repoint LocalShift's own YAML dashboard (PR being built by workflow 5 on 15 Oct; check `gh pr list` for 'repoint LocalShift's dashboard'; merge it after #1201) `custom_components/localshift/dashboard.yaml` (old base-Amber ids at lines ~144, 149, 685, 687, 689, 1115; replacements in the shared notes file `amber-express-ha-side-2026-10-15.md`) in a small PR, merged before the 17 Oct deploy.
- Base Amber removal waits for the homeassistant-100h session's report (a day of Energy-dashboard costs on Express) and for this repoint.

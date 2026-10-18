# LocalShift handoff: unattended backlog run 2

Written 2026-10-15 for a fresh session in `~/localshift`. This run follows the same pattern as the 14 Oct run and its handoff, `~/localshift/tmp/localshift-handoff-backlog-unattended-2026-10-14.md`. **Read that one first.** Its pipeline, the four confidence classes (A to D), the two before/after comparisons and the safety rules all still apply. This file covers only what changed and what this run should do.

## The job

Work the open LocalShift backlog unattended: build, test, merge, deploy, validate live, move on. Park anything that needs Jack. The run is finished when every item below is merged, deployed and validated, or parked with a comment that says what blocked it.

## State at handoff (verified 15 Oct, about 09:30)

- Live is `c0de4089`, identical to `origin/main` (verified by tree diff against the share). It was deployed 07:56 today and `deploy_check` passed 17/17.
- Rollback target: `backups/localshift.backup.20261015_075255` (== `24516737`).
- No reservation is held. Local `~/localshift` is clean on `main`.
- No open PR worth merging. #1186 is open but **parked by Jack**; do not merge or rebase it (see its last comment).
- Open issues: 13 (#1001 closed today). Jack's decisions on #1158, #1120, #1152 and #1001 are recorded as comments on each issue; read them.
- Run log: continue `~/localshift/tmp/backlog-run.md`. It has the whole 14 Oct run plus this morning's deploys. `git status` stays clean with it present (checked 15 Oct).
- Today is the first live day of #1188, the fix for planned charges never being committed. A separate session is watching today's charge. Do not deploy anything that touches `state/machine.py` or the decision-token path before 21:00 on 16 Oct, so #1188 gets a clean day of evidence.

## Work list, in order

| # | Item | Class | What "done" means |
|---|---|---|---|
| 1 | #981 close-out | A | Confirm which of its ~38 named fields still exist in `coordinator/data.py` (11 names still match; several may be generic words caught by the backtick grep). Remove any real dead ones, with the replay identical and the dead-code gate shrunk. Close with a summary |
| 2 | #1071 close-out | A | Jack's default from 14 Oct stands: leave the 3 remaining symbols parked in the baseline. Comment and close |
| 3 | #987 remaining four items | B (small) | Night-time / negative-FIT parity counters, `_find_nearest_negative_fit_window` scanning from the top of the hour, `calculate_spike_reserve_soc` using instantaneous load, and `check_automation_ready` treating SOC 0 as unavailable. One PR or two; each fix needs a test that fails without it |
| 4 | #1005 | B | Confirm on main whether SocSimulator / ExcessSolarSignals still forecast a different load from the planner for the same slot. Fix if so (same day-aware path), with a test; close if already fixed |
| 5 | #1143 item 3 | B | Is boost charging feeding back into the load forecast? Prove or disprove from recorder data and code. Fix with a test if it is. Item 2 (11 Oct midday SOC) is analysis only: write it up as a comment, no build |
| 6 | #1152 | B | Jack: **abort** on unload. Cancel an in-flight transition on unload and let the new instance's start-up health check reconcile. Test the cancel path. Deploy singly. The transition code is in `state/machine.py`, so this falls under the #1188 hold: build and merge, deploy no earlier than 21:00 on 16 Oct |
| 7 | #1158 | B, behind a switch | Jack: **build behind a new switch, default OFF.** Wire `current_action` into the live path behind the switch, and attach the replay comparison (live-like arm, with vs without) to the PR. With the switch off, the replay baseline must be identical. Touches the decision path, so deploy no earlier than the night of 16 Oct |
| 8 | #1120 | B, behind a switch | Jack: **build, ship switched off.** Build the reactive spike-export path. Prove it on the 12 Oct capture (`scripts/replay_spike_export.py`, `simulations/replay-1120/`). Wire it to the existing `switch.localshift_spike_discharge_enabled`, defaulting **OFF** in this release; existing installs currently have it ON with no reader, so handle the stored state deliberately and say how in the PR. Fix `docs/ENTITY_REFERENCE.md`. Largest item: expect it to need its own review rounds |
| 9 | #1119 leftovers | offline only | Later slots still use a 15-minute mean sell price. Produce a replay study of whether 5-minute pricing on slots 1-11 changes plans, and propose; do not ship without Jack |
| 10 | #1160 / #1133 | C | Identify the hourly external writer of `switch.my_home_allow_charging_from_grid` (flips at about :20-:28). The 13 Oct evening capture saw nothing; see #1133's last comment. Raise Teslemetry and stream-client logging around one :22 mark, restore it after, and record what is found. Instrumentation only unless the cause is proven |
| 11 | #1159 watch | C | Do not build. Each evening, count boosts shorter than 5 minutes that day from `select.localshift_battery_mode` history, and add the count to the run log. Report the trend to Jack at the end of the run |

Leave alone: #1104 (parked by Jack), #1186 (parked by Jack).

## What the last run taught (new rules for this one)

- **A reboot can strand a half-finished step.** On 14 Oct the Mac rebooted at about 05:49, killing the run after step 6 had been copied to the share but never loaded. **Before any deploy, and whenever the run resumes,** compare the share with the commit you believe is live, and check that `__pycache__` on the share is newer than the last restart. If the share and the running code can differ, settle that first.
- **Never edit a script while it is running.** The 14 Oct run re-executed `deploy.sh` this way, because bash reads scripts incrementally.
- **Take the after snapshot only after Home Assistant has gone down and come back.** Wait for the HTTP endpoint to stop returning 200, then to return it again. A snapshot taken during shutdown reads every entity as missing and false-fails all 17 checks; that happened at 07:42 today. Folding this wait into `deploy_check.py` is a good small task for this run.
- **The projected-entry check moves with forecasts.** Accept a failure on that check alone only when replaying the same inputs through the old code shows the same move. Write the evidence in the run log, as the last run did.
- **Cap review rounds.** If a fix needs a third review round, or its production lines keep growing between rounds, stop and park it for Jack with the findings. #1182 and #1186 grew to +119 over four rounds and were parked. Report net production lines per PR (memory `watch-production-line-growth.md`).
- **Restart budget.** Batch class A and neutral changes, at most one evening sequence per night, and only after 21:00 unless the preflight passes and nothing charges within 30 minutes. Last night took eight restarts; aim for three or fewer.
- **Copy time:** about 3 to 10 minutes per deploy over SMB. Allow for it in the evening window.
- **Another session may be watching live.** If a message arrives from "Localshift handoff plan assessment" asking for a hold, honour it and reply with confirmation.

## Kickoff prompt for Jack to paste

```
/loop Read ~/localshift/tmp/localshift-handoff-backlog-unattended-2026-10-15.md, then the 14 Oct handoff it points to, and work the LocalShift backlog unattended in the order given. Use a workflow for each item's build and independent review, at most three agents at once. Each iteration: pick the next item, classify it, build it, pass the gates for its class, merge, deploy only in an allowed window with deploy_check --preflight, run the before/after check after a real down-and-up restart, update tmp/backlog-run.md, comment on the issue, move on. Roll back and stop on any failed post-deploy invariant. Do not ask me questions; park anything that needs my decision under "needs Jack" and continue. No commit attribution trailers.
```

## Working preferences

- Brief Jack as a senior technical peer: result first, then the why and the trade-off. No preamble, no emoji.
- Never say "done"; say what was checked and what it showed.
- One pytest process at a time with `-p no:xdist`, at most three agents. Only CI's pyright counts (memory `pyright-local-does-not-match-ci.md`), though `npx pyright <file>` matched CI today.
- When piping a gate's output through `tail`, the exit code is the pipe's, not the gate's. Capture the gate's exit code separately.

## Suggested skills

- `workflow-authoring`, before writing the per-item workflow.
- `loop`, the outer driver.
- `ha-entity-dump`, for before and after snapshots.
- `code-review` or `bmad-review-edge-case-hunter`, for independent review of class B items, especially #1120, #1152 and #1158.
- `handoff`, at the end of the run or when context runs short.

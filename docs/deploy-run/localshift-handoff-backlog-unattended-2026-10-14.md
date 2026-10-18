# LocalShift handoff: work the backlog unattended

Written 2026-10-14 10:25 AEDT for a fresh session in `~/localshift`.

## The job

Work through the open LocalShift backlog with Jack out of the loop. Each issue goes through the same pipeline: build, test, merge, deploy, validate live, move on. Jack wants to be able to trust the result without watching, so the session's confidence has to come from evidence it gathers itself, mainly by comparing the system before and after each change.

The run is finished when every issue in scope is either merged, deployed and validated, or parked with a comment saying exactly what blocked it. Jack decides what is "done"; report what was checked.

## State at handoff (verified 10:12)

- Live on Home Assistant is `0e651d97`, byte-identical to `origin/main`. Deployed and restarted 10:08 today by the previous session. Nothing is undeployed and there are no open PRs.
- `~/localshift` is on `main` at `0e651d97`, clean.
- 39 open issues. All carry a `cluster:` label except #1158.
- 24 git worktrees are left over from yesterday's runs, three of them locked. Prune the unlocked ones whose branches are merged before starting. Leave the locked ones: a lock may mean another session still owns it.
- Not yet exercised on the new code: #1142's shutdown save (proves itself on the next restart) and #1150 (on the next unload). The first deploy of this run is the test of #1142; check the demand-window entry record and the day's cost totals survive it.
- The previous session has a one-shot check at 12:17 today on the day's plan. It is that session's job, not this one's.

Today's plan, for context when choosing deploy times: solar-only, battery at 16% at 10:11, no grid-charge slots, projected demand-window entry 100%, but resting on an optimistic morning solar forecast. If solar falls short the backstop acts from about 12:30. Assessment detail: `/var/folders/3h/bkjtjxqs0bvc_m7j9b__21d80000gn/T/localshift-handoff-plan-assessment-2026-10-14.md` (the issue-shape table in it is still the best checklist of known failure modes).

## How to run it: a loop outside, a workflow inside, deploys in the main session

Three layers, each doing the thing it is good at.

1. **Outer driver: `/loop` in self-paced mode (or `/goal` if Jack prefers it).** One issue or one small batch per iteration. The loop is what keeps the session going for hours and what lets it wait out a deploy window or an observation period without burning tokens. The goal form of the same thing is the completion condition in "The job" above.
2. **Inner rigour: a Workflow per issue for build and review.** Build agent in its own worktree, then an independent review agent that is given the diff and the issue but not the builder's reasoning, then a verify step. Keep it under four concurrent agents. Workflows need Jack's explicit opt-in, which the kickoff prompt below gives.
3. **Deploy and live validation: the main session only, never a workflow agent.** Home Assistant is one shared instance with a reservation lock. Only one thing may deploy at a time, and the session that deploys must be the one that validates and, if needed, rolls back.

Jack launches the run himself by pasting the kickoff prompt. Do not start a long autonomous run on his behalf from a background task.

## Where the confidence comes from

Sort every issue into one of four classes before touching it. The class decides what counts as proof.

| Class | Examples | Proof required before merge | Proof required after deploy |
|---|---|---|---|
| A. Meant to change nothing | Dead-code cluster, test fixes, docs | Tests green; `scripts/replay_plan_baseline.py` shows no plan difference; `scripts/dead_code_gate.py` passes with the baseline shrunk; CI green | Live invariants hold (below); same-inputs replay identical |
| B. Meant to change a plan | #1151, #1141, #1132, #1119 leftovers | A test or replay capture that fails on main and passes on the branch; replay-baseline differences confined to the captures the fix is meant to affect, each one explained in the PR; regenerated baseline committed in the same PR | Live invariants hold; same-inputs replay differs only where predicted |
| C. Only observable live | #1160, #1133, #1152, parts of #1159 and #1143 | Evidence from the log or logbook that identifies the cause. If the cause is not identified, ship instrumentation only | An observation window long enough to see the behaviour (hours, sometimes a day), with the before and after counts compared |
| D. Jack's call | #1104 (parked by Jack), #1001, anything that ships new behaviour switched on | Not attempted | Listed in the "needs Jack" section of the run log |

Class A is the place to start. It is the bulk of the backlog (19 dead-code issues), it has the strongest automatic proof, and it shakes out the pipeline before anything that changes behaviour goes through it.

### The two before/after comparisons

Live inputs move every five minutes, so a plan read before a restart and a plan read after it are not comparable on their own. Two comparisons get around that.

**Same inputs, two versions of the code (the strong one).** Just before deploying, capture the live inputs as a replay scenario (`scripts/export_replay_days.py` shows the shape; `simulations/` holds existing captures). Replay that capture through the code at the old commit and at the new commit. Class A must produce identical plans. Class B must differ only in the way the PR said it would. This is deterministic and runs offline, so it can gate the deploy rather than follow it.

**Live invariants across the restart (the safety net).** Run the `ha-entity-dump` skill immediately before the deploy and again once the planner has produced two plans after the restart. Then compare:

- Entity count and health unchanged (`sensor.localshift_entity_health` was 68/68); no entity newly unavailable.
- No attribute keys lost from `sensor.localshift_optimizer_summary` or `sensor.localshift_optimizer_plan_detailed`, other than ones the change removed on purpose.
- `config_options` in the summary identical.
- `switch.localshift_automation_enabled` on, `switch.localshift_dry_run` off, optimization mode unchanged. These have come up wrong after restarts before.
- Summary state `success`, plan has its usual slot count, `Pre-charge decision` lines appearing every minute in the log.
- Initial SOC within a few percent of the Powerwall's own reading; projected demand-window entry within about 3 points of the pre-deploy figure unless the change was meant to move it.
- Zero `ERROR` or `Traceback` lines mentioning localshift since the restart. Three start-up warnings are known and benign so far: one "Cannot read Tesla tariff" and two "invalid SOC non_positive, skipping" in the first second.
- Compiled files on the share carry the restart time, proving the new code loaded.

No script does this comparison today. **The first slice of the run is to build one** (suggested `scripts/deploy_check.py`: takes two entity dumps plus the log path, prints a pass/fail table, exits non-zero on any failed invariant). Prove it by running it against the two dumps around a no-op restart, then by feeding it a deliberately broken dump to confirm it can fail. Every later deploy in the run goes through it. A check that has never been seen to fail is not evidence.

### Rules that keep an unattended run safe

- **Deploy windows.** No restart between 15:00 and 21:00 (demand window). No restart when the current plan has a grid-charge slot or the backstop within the next 30 minutes. Read the plan before reserving.
- **Batching.** Class A changes may be merged one at a time and deployed in batches of up to five, since the replay gate already proved each one neutral. Class B and C changes are deployed one at a time so any live change is attributable to one PR.
- **Rollback is automatic.** `deploy.sh` writes a backup under `backups/` on the share each time. If any invariant fails after a restart: restore the backup, restart, re-run the check to confirm the old state is back, revert the merge on main, comment on the issue with the failed invariant, and stop the loop. A failed deploy ends the run; it is not something to work around.
- **Stop conditions.** Stop and notify Jack after a rollback, after two consecutive issues that had to be parked, after CI stays red through two fix attempts on one PR, or if anything outside LocalShift looks affected. Otherwise keep going.
- **Never weaken a gate to get through it.** Regenerating the replay baseline, widening a tolerance or adding to the dead-code whitelist is only legitimate when the PR explains why the new value is correct.
- **Behaviour changes do not ship switched on without Jack.** If a class B fix adds or alters a behaviour a household would notice, put it behind its existing switch or queue it under class D. A fix that restores documented behaviour is fine to ship.
- **Machine limits.** One pytest process at a time, no xdist, at most three agents running at once. This laptop has been crashed by fan-out before.

## Progress Jack can glance at

Keep a run log at `~/localshift/tmp/backlog-run.md`. `tmp/` is not git-ignored in this repo (checked 14 Oct), so add `tmp/` to `.git/info/exclude` first: that ignores it locally without a commit and keeps the tree clean for deploys. One row per issue: class, PR, merge commit, deploy time, check result, and for parked items the reason. Update it at the end of every iteration, and keep a "needs Jack" section at the top. Post the same outcome as a comment on each issue so the tracker and the log agree.

## Things that have bitten before (pointers, not repeats)

All in `~/.claude/projects/-Users-jackmcintyre-localshift/memory/`:

- `claude-runs-deploys-and-validation.md`: the exact deploy recipe (reserve, `--no-reload`, re-diff the share, restart through the Home Assistant MCP, release). Used cleanly today.
- `deploy-off-the-live-branch-trap.md` and `localshift-deploy-gotchas.md`: confirm the share matches the commit live is meant to be on before copying over it.
- `pyright-local-does-not-match-ci.md`: only the CI type check counts. CI runs only on PRs to `main`.
- `read-issue-comments-before-briefing.md`: run `gh issue view N --comments` before scoping. Jack parks issues by comment.
- `watch-production-line-growth.md`: report net production lines per PR and push back on fixes that grow through review rounds.
- `routed-build-runC-2026-09-08.md`: name-based dead-code analysis is unsound in this repo because of injected class references. This is why the replay gate exists; trust it over grep.

Also: `docs/DEVELOPER_GUIDE.md`, section "Proving a change is behaviour-neutral", and `docs/PLANNING_MODEL.md` for what a correct plan is.

One reading-the-log trap from today: the log lives on a network share and a read taken seconds after a restart can be stale. Re-read before concluding the planner has gone quiet.

## Suggested order

1. Prune worktrees; build and prove `scripts/deploy_check.py`.
2. Dead-code cluster, smallest first (#1058, #1061, #1062, #1064, #1065, #1069 are one-file changes), then the larger deletions (#981, #982, #983, #984, #980 remainders, #1071).
3. Live bugs with an offline reproduction: #1151, #1141, #1140, #1132, #1148.
4. Live-only bugs, starting with identification: #1160 and #1133 look like the same hourly writer; #1159 has two occurrences logged with timings in its comments.
5. Export and spike cluster (#1117 to #1120) offline only, using `scripts/replay_spike_export.py` and `scripts/replay_slot0_price.py`. Spike export has never fired live, so nothing here can be validated on the real system until a spike happens. Say so in each PR.
6. Give #1158 a cluster label and place it.

Leave alone: #1104 and anything under it. Jack parked it on 9 Oct. It has since gained a 13 Oct evidence comment arguing the other way, which makes it a decision for him, not a reason to build; list it under "needs Jack".

Note on #1151: its comment corrects the issue body. The live system's stored demand-window start is 14:55, so the bug has live impact today.

## Kickoff prompt for Jack to paste

```
/loop Read /var/folders/3h/bkjtjxqs0bvc_m7j9b__21d80000gn/T/localshift-handoff-backlog-unattended-2026-10-14.md and work the LocalShift backlog as it describes, unattended. Use a workflow for each issue's build and independent review, at most three agents at once. Each iteration: pick the next issue in the suggested order, classify it A to D, build it, pass the gates for its class, merge, deploy in an allowed window, run the before/after check, update tmp/backlog-run.md, then move on. Roll back and stop on any failed post-deploy invariant. Do not ask me questions; park anything that needs my decision under "needs Jack" and continue. No commit attribution trailers.
```

## Working preferences that matter here

- Brief Jack as a senior member of the technical team: result first, then the why and the trade-off. No preamble, no emoji.
- Settle questions from the system. Real decisions that are his go in the structured picker, but in this run they are parked, not asked.
- Never say "done". Say what was checked and what the check showed.
- No `Co-Authored-By` or session trailers on commits or PRs.

## Suggested skills

- `workflow-authoring`: before writing the per-issue workflow script.
- `loop`: the outer driver.
- `ha-entity-dump`: the before and after snapshots for every deploy.
- `code-review`: the independent review step inside the workflow, or as a second pass on class B changes.
- `bmad-review-edge-case-hunter`: for class B planner fixes, where the risk is a boundary the fix did not consider.
- `handoff`: at the end of the run, or when the context is close to full.

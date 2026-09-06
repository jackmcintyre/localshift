# routed-build-auto: state of the 9/5 backlog, and the next two runs

Prepared 2026-09-06 mid-run; updated 2026-09-07 once all eight tasks landed and their PRs opened.

## Where the tree is

| ref | sha | note |
|---|---|---|
| run base (both runs so far) | b65ac9d3 | main at the time of triage |
| origin/main now | 46c81be1 | PR #988 (#968 + #969, midnight reset + config-options coverage), deployed live 9/6 |
| task 1 branch | `wf/940-941-943-boundary-lag-telemetry` 130a4f62 | hand-committed from the verified tree, pushed |
| task 2 branch | `wf/fix-github-issues-942-and-944-together-i-176982` e20e53ab | committed + pushed by the run |
| task 3 branch | `wf/946-949-slot-schedule-hardening` 9e8c9b49 | hand-committed: loop halted on a fallback self-report; gates green by hand, loop review did NOT run — review this one properly at PR time |
| task 4 (#955) | `wf/fix-github-issue-955-in-custom-component-179710` 43e93860 | committed + pushed by the loop; 3 review findings filed as issues |
| task 5 (#934) | `wf/934-manual-override-restore-echo` aca9822e | hand-committed: 3 implement passes, verify green, 2 review passes addressed, 3rd review died on the session limit; gates re-run by hand (3440 tests). Behaviour note: a no-user-context re-assertion of the persisted manual mode while automation is on is now ignored |
| task 6 (#956) | `wf/implement-github-issue-956-a-sustained-s-213234` e2698f35 | loop-committed + pushed |
| task 7 (#787) | `wf/implement-github-issue-787-review-and-se-146166` 8b20a4c0 | loop-committed + pushed |
| task 8 (#679) | `wf/implement-github-issue-679-per-day-of-we-212647` 7554bc0a | loop-committed + pushed | check `git ls-remote --heads origin 'wf/*'` (the clone's fetch refspec only tracks main) |

None of the run's branches overlap the six files #988 touched, so rebases onto 46c81be1 are clean.

## Status 2026-09-07

The 9/5 backlog is **complete**: all eight tasks are on `wf/` branches off b65ac9d3, all eight
verified standalone against the full CI gate set (`ruff check`, `ruff format --check`, `vulture
--min-confidence 80`, full pytest), all eight merge cleanly into main 46c81be1 **individually**,
and all eight have open PRs:

| PR | issues | branch | loop review? |
|---|---|---|---|
| #1009 | #940 #941 #943 | `wf/940-941-943-boundary-lag-telemetry` | yes, approved |
| #1010 | #942 #944 | `wf/fix-github-issues-942-and-944-together-i-176982` | yes, approved after 2 passes |
| #1011 | #946-#949 | `wf/946-949-slot-schedule-hardening` | **no — needs a human read** |
| #1012 | #956 | `wf/implement-github-issue-956-a-sustained-s-213234` | yes |
| #1013 | #955 | `wf/fix-github-issue-955-in-custom-component-179710` | yes |
| #1014 | #934 | `wf/934-manual-override-restore-echo` | partial — final diff unreviewed |
| #1015 | #787 | `wf/implement-github-issue-787-review-and-se-146166` | yes |
| #1016 | #679 | `wf/implement-github-issue-679-per-day-of-we-212647` | yes |

**They conflict with each other, not with main.** Recommended merge order, rebasing each one
after the previous lands: 1009 -> 1010 -> 1011 -> 1012 -> 1013 -> 1014 -> 1015 -> 1016.
Shared files driving that order: `state/machine.py` (1009, 1010, 1014), `coordinator/data.py`
(1009, 1010, 1012, 1014), `engine/slot_schedule.py` (1011, 1012), `sensors/status.py`
(1010, 1012, 1015), `docs/ENTITY_REFERENCE.md` (1009, 1010, 1012, 1015).

Reviews filed a further 12 findings as #997-#1008. Fold them into run C rather than fixing by hand.

## Before launching run B or C

1. **Merge the eight PRs above first, or accept a large conflict pile.** Run B's tasks touch
   `state/machine.py`, `sensors/`, `select.py`, `switch.py`, `number.py` and `slot_schedule.py`,
   all of which the open PRs also touch. Building run B off today's main means rebasing six new
   branches after those eight land.
2. **The routed-build.js fixes are committed** in `claude-config` on branch
   `fix/routed-build-worktree-and-fallback-files` (81785ee), **not pushed and not merged**.
   Merge that before the next run or both failure modes recur.
3. **Budget one run per session-limit window.** Session limits ended three of the seven 9/5-9/7
   runs; each run burns 1.5-4M subagent tokens and the practical ceiling is ~2 tasks per 5-hour
   window. Run B is 6 tasks, so it is roughly three windows. Run C is stacked, so a limit
   mid-stack halts everything behind it — only start it with a full window free.
4. **Launch from a clean worktree at the current main tip.** Tree must be clean and the main
   checkout `~/localshift` must not be sitting on a `wf/` branch.

## Run B: bug fixes (independent branches)

File: `scratchpad/routed-build-backlog-2026-09-06-runB-bugfixes.json` — 6 tasks, `push: true`, no stacking.

| # | issues | shape |
|---|---|---|
| 1 | #972 + #974 | hardware reserve correctness (spike-discharge health-check reserve, proactive-export floor) |
| 2 | #971 + #977 + #978 | stale entity references (daily summary, snapshot script, docs) + a referenced-vs-registered entity-id test |
| 3 | #973 | dead sensor attributes + always-compute terminal diagnostics + reader/writer AST test |
| 4 | #975 | one owner for recompute-and-evaluate on entity writes |
| 5 | #976 | per-cycle INFO log demotion + ISSUE_N lint test |
| 6 | #985 | drop the ignored DPPlanner(config) parameter, audit the 34 call sites |

Does not depend on the eight PRs being merged, but see point 1 above: launching before they land means rebasing all six of these afterwards.

## Run C: dead-code sweep + telemetry follow-ups (stacked)

File: `scratchpad/routed-build-backlog-2026-09-06-runC-deadcode.json` — 8 tasks, `stack: true`.

Stacked on purpose: #979-#984 all edit `coordinator/data.py`, `computation_engine.py`, `const.py` and the same test files, so independent branches would conflict on every merge, and #986 (the CI gate) has to run last against the cleaned tree or it fails on the very code the earlier tasks delete. The cost is that one halt stops the rest; the mitigation is that each task is a mechanical delete verified by grep and the full suite.

Order: #966/#967/#989/#990/#991 (needs PRs #1009 and #1010 merged) -> #982 (+ #970 by removal) -> #983 -> #980 -> #979 -> #981 -> #984 -> #986.

Decision embedded in task 2: **#970 resolved by removal** (ForecastCorrectionProvider deleted), consistent with #962. If you would rather wire it, edit that task before launching.

Held back, deliberately: #987 (modelling consistency: efficiency constants, parity counters, spike reserve load source, SOC-0 readiness) changes optimizer behaviour at the margins and deserves a replay before/after, not an unattended build. #945 needs a live recorder-size check. #809/#810/#812/#813/#814/#845/#510-slice-3 remain parked architecture.

## What to check when a run reports

- `counts.done` vs the task count, and every branch in `branches[]` with `pushed: true`.
- `routingTally`: on both runs so far omniroute (glm-5.3 via zai) hit `tool_loop_cap` on plan and implement and Claude finished the work; the context stage of task 2 burned 510k tokens in for 1.8k out. Until `lib/omni-agent.mjs` compacts its message history (proposal A in `claude-config/docs/routed-build-proposals-2026-08-28.md`) this repo's 50 KB files will keep collapsing the routed path and the run is effectively Claude-built at routed-loop cost.
- Review findings get filed as issues automatically (label `routed-build`); fold them into the next backlog rather than fixing them by hand.

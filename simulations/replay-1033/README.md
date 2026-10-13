# Plan tail before a window past the horizon: replay (issue #1033)

Does the plan's tail still drain to the SOC floor when the next demand window
opens just after the horizon ends, and does anything else move?

```
uv run scripts/replay_next_window_tail.py --solar-scale 1.0 0.25
uv run scripts/replay_next_window_tail.py --solar-scale 0.25 --detail
```

Every instant runs twice through `ComputationEngine.compute_derived_values` on
byte-identical inputs. Config is the live set in `replay_no_dw.LIVE_OVERRIDES`
(95% target, 5% floor, strict entry, self_consumption).

**What `before` is.** Both arms are this branch. `before` is this branch with
`DPPlanner._plan_next_window_tail` replaced by a no-op; `after` is this branch
as it is. `before` is not main, and nothing here compares this branch with
main: the replay isolates what the tail does and nothing else.

**What is not covered.** Every instant is from 2026-10-02 to 10-12, spring in
Sydney. There are no winter days: no flat or cheap-overnight price day, and no
day short of solar except by scaling October's down. The overnight result
below is zero in both arms because October overnight prices sit above the
next morning's; it does not show what the tail does when they do not. That
case is covered by unit tests on synthetic prices
(`tests/engine/test_next_window_tail.py::TestWinterSawtooth`), not by replay.

**The run checks itself.** The script exits non-zero unless, for every instant:
slot 0's action and the commanded mode are the same in both arms, every
decision before the tail start is the same, and an instant whose tail was not
applied (any status but `applied`) is identical in both arms, cost included.
The numbers below are from one run on 2026-10-13, which exited 0.

## Instants

77 in total. This directory holds no captures of its own.

- 60 from `simulations/replay-1120`: six a day, 2026-10-02 to 10-11.
- 7 from `simulations/replay-1119`: the afternoon of 2026-10-12.
- 10 spliced: one more instant per day, half an hour before the afternoon
  capture, the before-the-window case no capture covers. The feed is the
  12:30-stamped capture's entries from stamp 14:30 on, continued by the same
  day's 15:05-stamped capture, cut at 24 hours. SOC and load are the later
  capture's. Real prices, assembled instant.

File stamps are UTC+11 on every day, but daylight saving started on
2026-10-04. The script labels every instant with its Australia/Sydney local
time, and so do the tables here. On 10-02 and 10-03 that is an hour behind the
file stamp: the files stamped 15:05 are 14:05 local (real before-the-window
instants), and the instant spliced at stamp 14:35 is 13:35 local.

That is why 10-02's spliced row shows a gap of 90 minutes. An earlier version
of this table called it "10-02 14:35" beside a 14:05 capture with a gap of 60,
which read as the gap growing as time passed. It is the other way round: the
spliced instant is half an hour before the capture, its feed ends half an hour
earlier (13:30 against 14:00), and its gap is 30 minutes longer. 10-03 is the
same pair at 30 and 0 minutes, because the clocks going forward that night
moves the next day's window an hour earlier against the feed.

Two caveats. Solar is measured, not forecast, and a capture has none for the
day after, so tomorrow's solar is the next captured day's measured series
(none for 10-11 and 10-12, which therefore see a zero-solar tomorrow). And load
is the capture instant's reading held flat, which overstates a day captured
during a busy half hour (10-11: 2.3 kW all day).

## How often the horizon ends short of the window

In these captures the feed carries 48 half-hours from the current one, so it
never contains the same time tomorrow. (The 2026-09-07 report had tomorrow's
entry slot by 15:07, so the feed's exact length is not constant; no capture
from that day reaches that far.) Measured gap from the end of the last slot to the next
15:00, by capture time:

| Instant (local) | Instants | Gap | `next_window_tail_status` |
|---|---|---|---|
| 06:00, 07:00, 11:30, 12:30 | 20 | 600 to 660 min (feed ends 04:00 or 05:00) | `gap_too_long`: the gap alone refills the battery |
| 13:35 spliced (10-02, 10-03) | 2 | 90 and 30 min | `applied` |
| 14:05 (10-02, 10-03) | 2 | 60 and 0 min | `applied` |
| 14:35 spliced (10-04 to 10-11) | 8 | 30 min | `applied` |
| 15:05, 15:15, 15:20 | 10 | 0 | `applied` |
| 15:30 to 20:30 | 26 | none: the feed ends inside a window | `no_gap` |
| 20:30 (10-03), 21:30 | 9 | 1050 min | `gap_too_long` |

22 `applied`, 29 `gap_too_long`, 26 `no_gap`. No instant gave
`whole_plan_skipped`, `tail_too_close`, `no_tail_slots`, `solve_unsuccessful` or `failed`: the
captured feed is never truncated, so those paths are exercised by unit tests
only.

So the exposed stretch runs from when the feed first reaches into tomorrow
afternoon until 15:30. The captures bracket that start between 12:30 and 14:05
and cannot place it closer. At one plan every five minutes that is 17 to 36
plans a day.

## Result

| | Tomorrow's solar x 1.0 | x 0.25 |
|---|---|---|
| Tail applied | 22 of 77 | 22 of 77 |
| Instants with a changed plan | 5 of 77 | 21 of 77 |
| Decisions before the tail changed | 0 | 0 |
| Slot-0 action changed | 0 | 0 |
| Commanded mode changed | 0 | 0 |
| Tail not applied, plan or cost differs | 0 of 55 | 0 of 55 |
| Overnight (21:00 to 06:00) grid charge, all instants, before -> after | 0.00 -> 0.00 kWh | 0.00 -> 0.00 kWh |
| Mean projected net cost, changed instants | -5.889 -> -4.347 | -1.479 -> -0.628 |
| Mean projected net cost, unchanged instants | -0.375 -> -0.375 | 0.156 -> 0.156 |
| Mean solve time, unchanged instants | 0.042 -> 0.046 s | 0.076 -> 0.077 s |
| Mean solve time, changed instants | 0.062 -> 0.126 s | 0.040 -> 0.075 s |
| Max solve time, any instant | 0.13 -> 0.23 s | 0.20 -> 0.23 s |

The rows from "Decisions before the tail changed" to "Tail not applied" are
the ones the script asserts. At x 1.0 most October tomorrows fill the battery
from solar: the tail is applied on the same 22 instants, the second solve
reproduces the first tail slot for slot on 17 of them, and the plan is
byte-identical. The cost rises on the changed instants because the plan now
contains the charge it was always going to need; nothing about what is
executed today is different. Solve times are a laptop's and move a few
hundredths between runs. The second solve only runs in the exposed stretch,
and roughly doubles the solve there.

The five changed instants at x 1.0:

| Instant (local) | Gap min | Required % | End SOC % | Grid charge kWh | Net cost $ |
|---|---|---|---|---|---|
| 10-02 14:05 | 60 | 74 | 68.8 -> 80.1 | 0 -> 1.65 | 0.293 -> 0.483 |
| 10-11 14:35 spliced | 30 | 85 | 5.0 -> 78.7 | 0 -> 22.61 | 4.833 -> 5.837 |
| 10-11 15:05 | 0 | 95 | 5.0 -> 73.6 | 0 -> 23.15 | 5.078 -> 5.809 |
| 10-12 15:15 | 0 | 95 | 5.0 -> 94.8 | 0 -> 20.82 | -11.377 -> -8.231 |
| 10-12 15:20 | 0 | 95 | 5.0 -> 97.9 | 0 -> 15.94 | -28.272 -> -25.635 |

The 21 changed instants at x 0.25 (end-of-horizon SOC, before -> after):

| Instant (local) | Gap min | Required % | End SOC % | Grid charge kWh | Net cost $ |
|---|---|---|---|---|---|
| 10-02 13:35 spliced | 90 | 63 | 5.0 -> 62.7 | 0 -> 9.97 | 0.531 -> 1.749 |
| 10-02 14:05 | 60 | 74 | 5.0 -> 73.7 | 0 -> 12.12 | 0.539 -> 1.791 |
| 10-03 13:35 spliced | 30 | 85 | 5.0 -> 88.1 | 0 -> 13.02 | 1.953 -> 2.655 |
| 10-03 14:05 | 0 | 95 | 5.0 -> 97.1 | 0 -> 14.55 | 1.965 -> 2.743 |
| 10-04 14:35 spliced | 30 | 85 | 13.6 -> 89.5 | 0 -> 11.09 | -0.246 -> 0.758 |
| 10-04 15:05 | 0 | 95 | 13.4 -> 98.3 | 0 -> 12.40 | -0.293 -> 0.828 |
| 10-05 14:35 spliced | 30 | 85 | 77.1 -> 88.4 | 0 -> 1.65 | -0.323 -> -0.208 |
| 10-05 15:05 | 0 | 95 | 79.9 -> 97.0 | 0 -> 2.50 | -0.440 -> -0.266 |
| 10-06 14:35 spliced | 30 | 85 | 63.6 -> 86.1 | 0 -> 3.30 | -0.359 -> -0.115 |
| 10-06 15:05 | 0 | 95 | 66.1 -> 98.3 | 0 -> 4.72 | -0.469 -> -0.116 |
| 10-07 14:35 spliced | 30 | 85 | 54.9 -> 88.6 | 0 -> 4.95 | -0.559 -> -0.060 |
| 10-07 15:05 | 0 | 95 | 56.8 -> 95.9 | 0 -> 5.74 | -0.676 -> -0.152 |
| 10-08 14:35 spliced | 30 | 85 | 37.1 -> 87.9 | 0 -> 7.45 | -0.722 -> -0.165 |
| 10-08 15:05 | 0 | 95 | 38.8 -> 95.1 | 0 -> 8.26 | -0.613 -> -0.002 |
| 10-09 14:35 spliced | 30 | 85 | 47.2 -> 86.7 | 0 -> 5.80 | -0.495 -> 0.015 |
| 10-09 15:05 | 0 | 95 | 47.1 -> 96.5 | 0 -> 7.24 | -0.488 -> 0.139 |
| 10-10 15:05 | 0 | 95 | 92.8 -> 100.0 | 0 -> 1.05 | -0.628 -> -0.552 |
| 10-11 14:35 spliced | 30 | 85 | 5.0 -> 78.7 | 0 -> 22.61 | 4.833 -> 5.837 |
| 10-11 15:05 | 0 | 95 | 5.0 -> 73.6 | 0 -> 23.15 | 5.078 -> 5.809 |
| 10-12 15:15 | 0 | 95 | 5.0 -> 94.8 | 0 -> 20.82 | -11.377 -> -8.231 |
| 10-12 15:20 | 0 | 95 | 5.0 -> 97.9 | 0 -> 15.94 | -28.272 -> -25.635 |

Every charge slot in every one of them is between 08:00 and 15:00 the next
day. 10-11 stops short of its requirement: the last three slots are priced at
19 to 22c against a 20c pre-charge ceiling and the flat 2.3 kW load drains
what was put in. The hard floor reports that as its feasible maximum (68.6%),
which is what it does on any day.

The required SOC is a lower bound and it moves. Solar in the gap is not
credited, and the requirement steps up as the gap shrinks from one feed update
to the next: 63% at a 90-minute gap, 74% at 60, 85% at 30, 95% at 0. The
published tail for the same tomorrow therefore changes through the afternoon
(10-02: 62.7% at 13:35, 73.7% at 14:05).

## Is the re-planned tail the plan the next half hour would make?

This section is from the first run of this replay and was not regenerated on
2026-10-13: the comparison was done by hand and the script does not perform
or assert it. The same comparison on synthetic flat, level and cheap-overnight
prices is now a unit test (`TestWinterSawtooth`).

Checked for the instants captured inside a window with a zero gap, by adding
one more entry to the feed so tomorrow's 15:00 entry slot is the last slot and
the ordinary single-window solve plans it (no tail re-plan), then comparing the
slots after 21:00 with the re-planned tail of the capture as recorded.

- Tomorrow's solar x 0: 8 of 8 identical, action for action, SOC to 0.0 points.
- x 0.25: 9 of 10 identical. On 10-10 the tail charges one more slot (ends 100%
  where the single solve ends 92.8%, 2.2 points under target). The single
  solve starts at 100% SOC, where the hard floor's solar check passes; the tail
  starts from the 21:00 SOC, where it does not.

## A longer horizon would not have fixed the reported plan

Also from the first run, done by hand and not regenerated on 2026-10-13.

2026-10-03 14:05 is the shape of the 2026-09-07 report: today's window is
still ahead and the feed ends as tomorrow's opens. With tomorrow's solar at
zero and tomorrow's entry slot added to the feed, the main solve still holds every slot
and ends at 5.0%: the first entry in the horizon is today's, already
satisfied, and it is the only one a solve looks at. The re-planned tail on the
unextended feed ends at 98%.

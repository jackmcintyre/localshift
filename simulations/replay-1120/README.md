# Spike export in the default mode: replay (issue #1120)

Two questions about the spike-export admission in `engine/spike_export.py`:
does `self_consumption` mode now export on a real spike, and does anything
change on an ordinary day.

```
uv run scripts/replay_spike_export.py replay
uv run scripts/replay_spike_export.py capture --days 10   # refresh the controls
```

Every instant runs twice through `ComputationEngine.compute_derived_values`,
in `self_consumption` mode, on byte-identical inputs: `base` has the spike
admission switched off (the base branch, #1122), `branch` is this change.

## The spike: 2026-10-12

Captures are the seven in `simulations/replay-1119/`. The operator had switched
to `arbitrage` by hand that afternoon; here the mode is forced back to the
default.

| Instant | Price confirmed | Sell | Buy (same interval) | Dearest later sell in the same dear run | Rebuy price | Sell needed | Base | Branch | Skips debounce |
|---|---|---|---|---|---|---|---|---|---|
| 15:15:30 | yes | 24.2284 | 26.7148 | 2.87 | 0.48 | 2.87 | hold | export | yes |
| 15:20:10 | no (still 15:15's price) | 24.2284 | 26.7148 | 2.87 | 0.48 | 2.87 | hold | hold | no |
| 15:30:40 | yes | 10.4839 | 11.5958 | 2.93 | 0.71 | 2.93 | hold | export | yes |
| 16:10:40 | yes | 0.6762 | 0.7649 | 3.31 | 0.71 | 3.31 | hold | hold | no |
| 16:25:50 | yes | 0.3572 | 0.4140 | 3.63 | 0.42 | 3.63 | hold | hold | no |
| 16:30:10 | no (still 16:25's price) | 0.3572 | 0.4140 | 3.23 | 0.42 | 3.23 | hold | hold | no |
| 16:30:40 | yes | 10.5225 | 11.5958 | 3.28 | 0.48 | 3.28 | hold | export | yes |

"Sell needed" is the largest of three numbers: 4x the horizon's median buy
price (about $0.80 that afternoon); the rebuy price / 0.874 round-trip
efficiency + the 10c export margin; and the dearest later sell price in the
same dear run. The run is the unbroken stretch of slots after the current one
whose sell price is at or above that 4x floor. On 12 Oct it was the whole
evening, forecast at about $3. Those slots are somewhere else to sell, not
what the energy costs to replace, so they are compared sell against sell and
left out of the rebuy price, which is then the dearest buy price in the next
12 hours after the run ends.

The three confirmed spike intervals clear the bar by 3.2x to 8.4x; the two
sub-$1 intervals miss it by 5x and 10x. Nothing sits near the line.

### A spike lasting more than one interval

12 Oct only shows isolated single-interval spikes. The first cut of this rule
put the rest of the spike in the rebuy price, and because feed-in is about
0.9x general, a following interval forecast above roughly 87% of the current
sell price blocked the export. `DPPlanner.plan` rolled forward one 5-minute
interval at a time, self_consumption mode, SOC 90
(`TestSpikeLastingSeveralIntervals`):

| Spike (sell price per interval) | First cut | Now |
|---|---|---|
| Flat: 10 / 10 / 10 / 10 | hold, hold, hold, export | export in all four |
| Falling: 15 / 12 / 10 / 8 | hold, hold, hold, export at $8 | export in all four |
| Rising: 8 / 10 / 12 / 15 | hold, hold, hold, export at $15 | unchanged |

The rising case is deliberate: a dearer interval of the same spike still
blocks. These are hand-built horizons, not captures.

### A spike before the demand window

All seven 12 Oct instants are inside the 15:00-21:00 window. Before it, a
spike export leaves a gap to the 95% entry target and the planner refills it
from the grid. The 2026-10-09 12:30 control (SOC 29.8) with only the current
feed-in and general prices changed to a confirmed spike
(`TestSpikeBeforeTheDemandWindow`):

| Current sell / buy | Arm | Slot 0 | Grid-charge slots | Dearest charge price | Projected window entry |
|---|---|---|---|---|---|
| 1.00 / 1.15 | base | hold | 1 | 0.0997 | 100.0% |
| 1.00 / 1.15 | branch | export | 4 | 0.0997 | 95.26% |
| 10.00 / 11.20 | base | hold | 1 | 0.0997 | 100.0% |
| 10.00 / 11.20 | branch | export | 4 | 0.0997 | 95.26% |

So the rule does add grid charging here: export, then a recharge inside the
hour, in slots that were already allowed to charge. It is profitable and the
plan still meets the target, but with a quarter of a point to spare, relying
on the pre-charge path to land it.

Only the current interval can be admitted. An earlier cut admitted any slot in
the horizon, and on the 2026-08-05 spike fixture
(`tests/engine/test_spike_event_precharge.py`) the planner then added an
overnight grid-charge slot so it could sell into the forecast $1.65 interval.
That is charge/discharge cycling on a forecast, so a forecast spike is never
planned as an export; `test_forecast_spike_adds_no_grid_charging` holds that.

15:20:10 is the #1122 case: the sensor still carried the previous interval's
$24.23 as an estimate (the real price, 71c, arrived 13 s later), so the export
start is deferred.

## Controls: ordinary days

Six instants a day (07:00, 12:30, 15:05, 17:30, 19:00, 21:30) for the ten days
before the spike, plus the four single-instant days already in
`simulations/replay/` and `simulations/replay-nodw/`. A control day is one
whose captured horizons never carry a feed-in price of $1 or more.

| Day | Instants | Max feed-in in any horizon | Plan slots | Slot 0 counted as spike | Export slots base / branch | Mode transitions base / branch | Plans that differ |
|---|---|---|---|---|---|---|---|
| 2026-08-25 | 1 | 0.14 | 43 | 0 | 0 / 0 | 7 / 7 | 0 |
| 2026-08-27 | 1 | 0.16 | 43 | 0 | 0 / 0 | 3 / 3 | 0 |
| 2026-09-03 | 1 | 0.14 | 43 | 0 | 0 / 0 | 0 / 0 | 0 |
| 2026-09-07 | 1 | 0.15 | 43 | 0 | 0 / 0 | 7 / 7 | 0 |
| 2026-10-02 | 6 | 0.20 | 328 | 0 | 0 / 0 | 15 / 15 | 0 |
| 2026-10-03 | 6 | 0.16 | 328 | 0 | 0 / 0 | 11 / 11 | 0 |
| 2026-10-04 | 6 | 0.17 | 318 | 0 | 5 / 5 | 19 / 19 | 0 |
| 2026-10-05 | 6 | 0.19 | 328 | 0 | 0 / 0 | 16 / 16 | 0 |
| 2026-10-06 | 6 | 0.26 | 328 | 0 | 14 / 14 | 26 / 26 | 0 |
| 2026-10-07 | 6 | 0.17 | 328 | 0 | 0 / 0 | 11 / 11 | 0 |
| 2026-10-08 | 6 | 0.23 | 328 | 0 | 0 / 0 | 9 / 9 | 0 |
| 2026-10-09 | 6 | 0.31 | 328 | 0 | 6 / 6 | 23 / 23 | 0 |
| 2026-10-10 | 6 | 0.25 | 328 | 0 | 0 / 0 | 8 / 8 | 0 |

13 control days, 58 instants, 3,114 plan slots. Every plan is identical, slot
for slot, with and without the admission. The export slots that do appear are
ones the base branch already plans, the same slots in both arms.

2026-10-11 is captured but is not a control: its evening horizons forecast a
$1.52 feed-in price. Its six plans are also identical in both arms (6 export
slots, 26 transitions each).

"Mode transitions" counts changes of action from one plan slot to the next. It
is a property of each plan, not of the live mode history, which a replay of
single instants cannot produce.

## Caveats

- Solar is measured, not forecast (see `scripts/export_replay_days.py`), so
  every arm has perfect solar foresight. The comparison between arms is what
  these files are for.
- Prices come from the sensors' recorded `forecast` attribute. Production
  reads `detailedForecast`, which the recorder does not keep. Issue #1119
  reports the live horizon's last slot sometimes carrying the current
  interval's price; that is why the rebuy price looks 12 hours ahead and not
  to the end of the horizon, where such a slot would block every spike.
- Recorder retention is about ten days, so the October controls cannot be
  recaptured later. They are committed for that reason.

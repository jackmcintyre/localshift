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

| Instant | Price confirmed | Sell | Buy (same interval) | Rebuy price | Sell needed | Base | Branch | Skips debounce |
|---|---|---|---|---|---|---|---|---|
| 15:15:30 | yes | 24.2284 | 26.7148 | 3.17 | 3.73 | hold | export | yes |
| 15:20:10 | no (still 15:15's price) | 24.2284 | 26.7148 | 3.17 | 3.73 | hold | hold | no |
| 15:30:40 | yes | 10.4839 | 11.5958 | 3.24 | 3.81 | hold | export | yes |
| 16:10:40 | yes | 0.6762 | 0.7649 | 3.66 | 4.29 | hold | hold | no |
| 16:25:50 | yes | 0.3572 | 0.4140 | 4.63 | 5.39 | hold | hold | no |
| 16:30:10 | no (still 16:25's price) | 0.3572 | 0.4140 | 3.89 | 4.55 | hold | hold | no |
| 16:30:40 | yes | 10.5225 | 11.5958 | 3.63 | 4.25 | hold | export | yes |

The rebuy price is the dearest buy price in the next 12 hours of the plan.
"Sell needed" is the larger of 4x the horizon's median buy price (about $0.80
that afternoon) and rebuy price / 0.874 round-trip efficiency + the 10c export
margin. The three confirmed spike intervals clear it by 2.5x to 6.5x; the two
sub-$1 intervals miss it by 6x to 15x. Nothing sits near the line.

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

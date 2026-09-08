# No-demand-window replay

Real days captured from Home Assistant (`scripts/export_replay_days.py`) and
replayed through the optimizer with the demand window removed, to answer one
question before anything goes live: **what does the planner do in the
shoulder season, when the tariff's demand charge does not apply and the only
thing that matters is the energy rate?**

```bash
scripts/export_replay_days.py --days 10 --hour 9 --out simulations/replay-nodw
uv run scripts/replay_no_dw.py --arms dw,no_dw,gateless_mcs25,gateless_mcs10,gateless_mcs0
uv run scripts/replay_no_dw.py --day 2026-09-07 --arms dw,no_dw --detail
```

Ten days, 2026-08-29 to 2026-09-07, captured at 09:00. Only
`2026-09-07.json` is committed (the one solar-deficit day, which recorder
retention would otherwise lose); the rest are gitignored and regenerable
until the recorder rolls them off. Read the solar caveat in
`simulations/replay/README.md` before quoting absolute numbers: solar is
measured, not forecast, so every arm has perfect solar foresight, and the
comparison between arms is the only thing these files are for.

## What the arms are

| arm | demand window | grid-charge admission | cycle hurdle |
|---|---|---|---|
| `dw` | 15:00–21:00 daily, 95% target, strict entry (live config 2026-09-08) | cheap-price gate (50th percentile) plus the DW water level up to $0.20 | $0.25/kWh |
| `no_dw` | none — no slot flagged, so no terminal target, hard floor, water level, urgency window or import ban | cheap-price gate only | $0.25/kWh |
| `gateless_mcs25` | none | **none** — every slot offers grid charge; the DP's cost function decides | $0.25/kWh |
| `gateless_mcs10` | none | none | $0.10/kWh |
| `gateless_mcs0` | none | none | $0 |

"Gateless" is what "trust the rate all day" means literally: the planner may
buy in any slot, and only its own import/export/round-trip arithmetic (plus
the cycle hurdle) says whether it should.

## Result (2026-09-08)

Projected net cost per day, $. Nine of ten days are byte-identical across
`dw`, `no_dw` and `gateless_mcs25`: solar filled the battery and the demand
window never bound. The differences are all on 2026-09-07, the one day
solar could not cover the evening.

| day | solar kWh | `dw` | `no_dw` | `gateless_mcs25` | `gateless_mcs10` | `gateless_mcs0` |
|---|---|---|---|---|---|---|
| 2026-08-29 | 25.0 | 0.310 | 0.310 | 0.310 | 0.310 | 0.325 |
| 2026-08-30 | 30.5 | -0.065 | = | = | = | = |
| 2026-08-31 | 30.4 | -0.167 | = | = | = | = |
| 2026-09-01 | 30.9 | -2.082 | = | = | = | -2.163 |
| 2026-09-02 | 21.2 | 0.004 | = | = | = | = |
| 2026-09-03 | 30.9 | 0.059 | = | = | = | 0.051 |
| 2026-09-04 | 31.3 | -0.076 | = | = | = | -0.085 |
| 2026-09-05 | 27.1 | -1.631 | = | = | = | = |
| 2026-09-06 | 32.9 | -0.189 | = | = | = | = |
| **2026-09-07** | 25.6 | **6.054** | **6.934** | **6.934** | **6.393** | **6.048** |

What 2026-09-07 looked like: battery at 23% at 09:00, buy price 7–8c all
morning, 16–19c from 16:30 to 21:00, 14–16c overnight, and a load forecast of
3.6 kW flat (the weather-extrapolation artefact from 2026-09-05, which
inflates the dollar figures on this day but not their ordering).

- **`dw`** grid-charged 13.2 kWh at 7–8c before 15:00 and rode the evening
  on the battery. Its cost function has no demand-charge term, so this is
  the cheaper plan *on energy alone* — the demand window was acting as a
  proxy for "prepare for the expensive evening".
- **`no_dw`** charged nothing and imported 18 kWh at 16–19c: **+$0.88**. The
  only path that admitted 7–8c slots was the demand window's water-level
  machinery. With it gone, the cheap-price gate (a percentile over an
  8-hour lookahead that ends before the peak, then EMA-smoothed) lands at
  7c, inside the cheap block, and excludes it. Opening the percentile to 100
  changed nothing; the gate's arithmetic cannot see the evening.
- **`gateless_mcs25`** also charged nothing: with the gate removed, the
  $0.25/kWh cycle hurdle alone rejects the trade. A 7c→18c spread is about
  9c/kWh net of round-trip loss, well under the hurdle.
- **`gateless_mcs10`** charged 15 kWh, but late — at 10c in the 15:00–16:30
  slots rather than at 7c in the morning — because the futile-cycling
  penalty does not count an expensive evening as a "useful period", only
  solar surplus, a demand window or a cheaper charge slot. Still +$0.34.
- **`gateless_mcs0`** reproduced the `dw` plan's economics (6.048 vs 6.054)
  with no deadline at all, and beat it on 2026-09-01 (-$0.08). Its cost is
  micro-cycles on three sunny days, one of them a $0.015 loss.

**No arm charged overnight on any day at any hurdle.** The #800 sawtooth did
not reappear once the gate was removed, which is consistent with the #406
double-credit having been its sole driver.

## What this settles

1. Removing the demand window naively is strictly worse: neutral on sunny
   days, and a loss on every solar-deficit day, because the planner's
   ability to pre-charge for a dear evening lives entirely inside the
   demand-window machinery.
2. The DP's cost function already knows how to plan a no-demand-charge day.
   What stops it is admission, not economics: a time-relative cheap-price
   gate that cannot see the peak, and a $0.25 cycle hurdle sized to kill a
   sawtooth that no longer exists.
3. A price-driven admission rule — qualify grid charge on the spread to the
   dearest upcoming load block, and count that block as a useful period for
   the cycling penalty — is the season-agnostic replacement. Spike funding
   (#910) is already this shape, restricted to 4× median outliers.

## Harness note

Both this harness and `scripts/replay_adaptive_arms.py` construct a fresh
`CoordinatorData`, which carries `forecast_horizon_hours = 0.0`. The
cheap-price calculation reads that field before the optimizer facade writes
the real span, so on the first pass the configured percentile is scaled by
0.1 and the short-horizon uncertainty penalty is maxed. Live never sees this
because the previous cycle's value persists, and the calculator's EMA
hysteresis means a wrong first value is only partly corrected later. This
harness seeds the horizon from the price forecast before the first pass and
runs two passes. The adaptive-arms harness does not, and its absolute numbers
should be read with that in mind (all its arms were affected equally).

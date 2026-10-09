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
uv run scripts/replay_no_dw.py --arms dw,no_dw,block      # slice 1 gate (below)
uv run scripts/replay_no_dw.py --flap simulations/replay-nodw-flap   # flap test (below)
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

## Slice 1 gate (2026-10-09)

The acceptance gate for slice 1 of `docs/PRICE_BLOCK_TARGET.md` (#1104, #1110):
a `block` arm, which is the live config with the clock demand window removed
and `switch.localshift_price_block_target` on.

| arm | demand window | deadline and target | block spread | cycle hurdle |
|---|---|---|---|---|
| `block` | none | the detected expensive block; target sized to the block's net load, clamped to 5–95% | $0.08/kWh | $0.25/kWh |

```bash
uv run scripts/replay_no_dw.py --arms dw,no_dw,block
uv run scripts/replay_no_dw.py --dir simulations/replay --arms dw,no_dw,block
```

Whenever `dw` and `block` both run the harness prints a per-day gate: whether
the block plan is identical to `dw` (same cost and the same action, SOC, import
and export in every slot), below it or above it, and how much the block arm
grid-charges between 21:00 and 06:00.

### Which days it ran on

All ten days of the original study, 2026-08-29 to 2026-09-07, captured at
09:00. Only `2026-09-07.json` was on disk in this directory. The recorder
turned out to retain about forty days, not ten, so 2026-08-31 to 2026-09-06
were re-exported from Home Assistant history on 2026-10-09. The re-export
of 2026-09-07 is byte-identical to the committed file, and the re-exports of
2026-08-31 to 2026-09-03 are byte-identical to the copies already in
`simulations/replay/`, so the exporter still reproduces the study's inputs.
2026-08-29 and 2026-08-30 have rolled off the recorder; those two were copied
from `simulations/replay/`, where the same exporter wrote them at the same
hour. No day was unavailable. The `dw` and `no_dw` columns reproduce the
2026-09-08 table above to the cent on all ten days.

### Result

Projected net cost per day, $. Block times are the start of the block's first
slot and the end of its last.

| day | solar kWh | `dw` | `no_dw` | `block` | `block` − `dw` | plan vs `dw` | block | target % | overnight grid charge kWh |
|---|---|---|---|---|---|---|---|---|---|
| 2026-08-29 | 25.0 | 0.310 | 0.310 | 0.310 | 0.000 | identical | 17:30–23:00 | 59.4 | 0 |
| 2026-08-30 | 30.5 | -0.065 | -0.065 | -0.065 | 0.000 | identical | 17:00–04:00 | 50.9 | 0 |
| 2026-08-31 | 30.4 | -0.167 | -0.167 | -0.167 | 0.000 | identical | 17:00–22:00 | 26.9 | 0 |
| 2026-09-01 | 30.9 | -2.082 | -2.082 | -2.082 | 0.000 | identical | 17:00–20:30 | 38.4 | 0 |
| 2026-09-02 | 21.2 | 0.004 | 0.004 | 0.004 | 0.000 | identical | 16:30–22:00 | 27.6 | 0 |
| 2026-09-03 | 30.9 | 0.059 | 0.059 | 0.059 | 0.000 | identical | 17:00–04:00 | 44.8 | 0 |
| 2026-09-04 | 31.3 | -0.076 | -0.076 | -0.076 | 0.000 | identical | 17:30–19:30 | 17.0 | 0 |
| 2026-09-05 | 27.1 | -1.631 | -1.631 | -1.631 | 0.000 | identical | none found | – | 0 |
| 2026-09-06 | 32.9 | -0.189 | -0.189 | -0.189 | 0.000 | identical | 17:00–04:00 | 47.3 | 0 |
| **2026-09-07** | 25.6 | **6.054** | **6.934** | **6.030** | **-0.024** | different | 16:30–00:30 | 95.0 | 0 |

Against the acceptance in #1110:

- **Identical to `dw` on the nine sunny days: yes**, nine of nine, slot for slot.
- **At or below `dw` on 2026-09-07: yes**, 6.030 against 6.054 (−$0.024), and
  $0.90 better than `no_dw`. The block enters at 16:30, not at the clock
  window's 15:00, and its need is 29.7 kWh, about 220% of the battery, so the
  clamp pins the target at 95%. The plan grid-charges 16.5 kWh before 16:30 at
  up to 13c (`dw`: 13.2 kWh before 15:00 at up to 8c) and none after.
- **No overnight grid charging in the block arm: yes**, zero on every day.

Harness verdict: `slice 1 gate: PASS (10 day(s): 9 identical to dw, 1 below, 0 above)`.

### The four older captures, outside the acceptance set

`simulations/replay/` also holds 2026-08-25 to 2026-08-28, captured for the
Slice 0 study. They are not part of the #1110 acceptance (they predate the
shoulder-season study and three of them are short-solar winter days), but the
gate was run on them too and one of them is above `dw`:

| day | solar kWh | `dw` | `no_dw` | `block` | `block` − `dw` | plan vs `dw` | block | target % | overnight grid charge kWh |
|---|---|---|---|---|---|---|---|---|---|
| 2026-08-25 | 6.7 | 1.633 | 1.206 | 1.206 | -0.427 | different | none found | – | 0 |
| **2026-08-26** | 12.0 | **1.712** | **2.257** | **1.902** | **+0.190** | different | 17:30–21:00 | 39.0 | 0 |
| 2026-08-27 | 16.9 | 0.304 | 0.000 | 0.000 | -0.304 | different | 17:00–23:30 | 29.9 | 0 |
| 2026-08-28 | 29.8 | -0.201 | -0.201 | -0.201 | 0.000 | identical | 17:00–02:30 | 34.1 | 0 |

Harness verdict on that directory: `FAIL (10 day(s): 7 identical to dw, 2
below, 1 above)`; the ten are these four plus 08-29 to 09-03 again.

- **2026-08-26 is $0.19 dearer than `dw`.** The cheapest pre-evening price is
  10.8c, so a slot is dear from 18.8c. The evening sits at 18.8–19.2c until
  21:00 and then eases to 17–18c for the rest of the night: still 6–7c above
  the trough, but under the 8c spread. The block is therefore 17:30–21:00, the
  target is sized to those slots only (4.6 kWh, 39%), the battery reaches the
  floor at 22:30, and 6.7 kWh is imported overnight at 17–18c that `dw` had
  bought at 11c. It is still $0.36 better than `no_dw`. This is the spread
  doing what it says, not a detector fault, and the knob was left at $0.08;
  it is the first measured dollar cost of that value and is for the operator
  to weigh, not for the harness to tune away.
- **2026-08-25 and 2026-08-27 are below `dw`** because `dw` buys 12.4 and
  4.2 kWh to reach a 95% readiness target and the block arm does not (no block
  on a flat 08-25; a 30% target on 08-27 that solar meets). The cost function
  has no demand-charge term, so in season that saving is not real money. These
  three days are an in-season question for slice 2, where the charge window
  keeps the 95% target.

### Wider sweep, 2026-09-08 to 2026-10-08 (not part of the acceptance)

Because the recorder still held them, the 31 days after the study were
captured at 09:00 as well and put through the same gate. They are not
committed; `scripts/export_replay_days.py --date <day> --time 09:00` rebuilds
any of them while the recorder has it.

Harness verdict: `FAIL (31 day(s): 21 identical to dw, 6 below, 4 above)`.
Overnight grid charging in the block arm is zero on all 31. The ten days that
differ:

| day | `dw` | `no_dw` | `block` | `block` − `dw` | note |
|---|---|---|---|---|---|
| 2026-09-10 | 0.349 | 0.367 | 0.367 | +0.018 | no block: the evening clears the 8c spread for less than the 2 h minimum |
| 2026-09-11 | 1.378 | 1.866 | 1.349 | -0.029 | |
| 2026-09-14 | 0.695 | 0.696 | 0.696 | +0.000 | under a tenth of a cent |
| **2026-09-21** | **7.430** | **7.961** | **7.669** | **+0.239** | see below |
| 2026-09-22 | 0.847 | 0.377 | 0.377 | -0.471 | no block; `dw` buys 7.3 kWh for readiness |
| 2026-09-23 | 1.441 | 1.202 | 1.132 | -0.309 | |
| 2026-09-26 | 2.721 | 2.745 | 2.724 | +0.004 | one boost slot at 15.1c just before a 15.6c block |
| 2026-09-27 | 4.350 | 3.864 | 3.864 | -0.486 | no block; `dw` buys 18 kWh for readiness |
| 2026-09-28 | 0.206 | 0.000 | 0.000 | -0.206 | no block |
| 2026-10-06 | -0.619 | -0.646 | -0.646 | -0.027 | |

Summed over the 31 days `block` is $1.27 below `dw` and `no_dw` is $0.37 below
it, most of both being readiness charging the cost function does not price.

**2026-09-21 is the second day above `dw`, and for a different reason than
2026-08-26.** The afternoon shoulder is almost as dear as the evening: 17.5,
17.9, 16.6 and 17.2c from 15:00 to 16:30 against a 17.8c threshold, then
19–20c. The block therefore starts at 17:00. The battery spends 15:00–16:00
carrying a 3.75 kW load forecast, and the 95% target at 17:00 then forces two
boost slots at 16.6 and 17.2c (8.4 kWh imported) to displace 19–20c energy,
which loses money after round-trip loss. `dw` simply arrives full at 15:00 and rides. The
entry target is a hard deadline, and when the slots just before the block are
nearly as dear as the block it buys at the top. Nothing was tuned in response.

### Flap test (#1111)

```bash
scripts/export_replay_days.py --date 2026-09-07 \
    --time 09:00,11:00,13:00,14:30 --out simulations/replay-nodw-flap
uv run scripts/replay_no_dw.py --flap simulations/replay-nodw-flap
```

The block's entry time sets the planner's deadline, so the same day is captured
at 09:00, 11:00, 13:00 and 14:30 and the block arm is run on each. PASS when
the entry moves by at most one 30-minute slot between consecutive captures and
the slot-0 action never goes charge / hold / charge. A block that appears or
disappears between two captures counts as a move of more than one slot. Each
capture is a cold start for the detector; the harness also prints the entry
with the previous capture's entry carried into the hysteresis, as live would
carry it.

Captures are in `simulations/replay-nodw-flap/`, committed: 2026-09-07, plus
every day of the wider sweep on which the block arm grid-charged (09-11, 09-21,
09-23, 09-26). The 09:00 capture of 2026-09-07 is byte-identical to
`simulations/replay-nodw/2026-09-07.json`.

The table and analysis below are the original #1111 result, kept as history:
every capture planned cold, each alone. The harness still prints that as its
cold-start column. The verdict is now read from a run that carries the block's
state between captures; that result is in the next section.

**Verdict (2026-10-09, #1111, cold start): FAIL. Two of five solar-deficit days pass.**

| day | 09:00 | 11:00 | 13:00 | 14:30 | slot-0 action | verdict |
|---|---|---|---|---|---|---|
| 2026-09-07 | 16:30 | 16:30 | 16:30 | 16:30 | hold, hold, hold, hold | PASS |
| 2026-09-11 | 17:00 | none | none | none | hold, hold, hold, hold | **FAIL** |
| 2026-09-21 | 17:00 | 15:00 | 17:00 | none | charge, hold, hold, hold | **FAIL** |
| 2026-09-23 | 18:00 | none | none | none | hold, hold, hold, hold | **FAIL** |
| 2026-09-26 | 17:30 | 17:00 | 17:30 | 18:00 | boost, boost, hold, hold | PASS |

Block entry time per capture. Per pair:

- **2026-09-07** 09→11 +0 min, 11→13 +0, 13→14:30 +0. PASS.
- **2026-09-11** 09→11 block disappears (FAIL), 11→13 none→none, 13→14:30
  none→none.
- **2026-09-21** 09→11 −120 min (FAIL), 11→13 +120 min (FAIL), 13→14:30 block
  disappears (FAIL).
- **2026-09-23** 09→11 block disappears (FAIL), then none→none twice.
- **2026-09-26** 09→11 −30 min, 11→13 +30, 13→14:30 +30. PASS.

Carrying the previous capture's entry through the hysteresis does not change
any verdict. The slot-0 action never went charge / hold / charge on any day;
every failure is the boundary.

Two mechanisms, both visible in the captured price forecasts:

1. **The trough rolls off the horizon.** `p_ref` is the cheapest price among
   the slots still ahead. Within one plan it cannot rise, but between plans it
   does, as the cheap morning slots become the past. On 2026-09-11 the trough
   is 8.2c at 09:00, 9.4c at 11:00 and 11.1c at 13:00, so the dear threshold
   climbs 16.2 → 17.4 → 19.1c while the evening stays at 17.0–17.5c. The block
   exists at 09:00 and is gone by 11:00. 2026-09-23 is the same (trough 12.3 →
   12.8 → 15.7c against an evening that also softened from 21.9 to 20.4c), and
   so is the 14:30 capture of 2026-09-21. The consequence is worse than a
   moved boundary: the 09:00 plan holds at slot 0 and schedules 9.1 kWh of
   pre-charge for later (09-11), and by the time "later" arrives the deadline
   that funded it no longer exists. The single 09:00 capture in the gate above
   therefore overstates what the block arm would do across a real day.
2. **Forecast revision on a knife edge.** On 2026-09-21 the 11:00 forecast
   put 15:00 and 15:30 at 22.3 and 21.0c (17.5 and 17.9c two hours earlier),
   which pulls the entry from 17:00 to 15:00; by 13:00 they are back to 14.4
   and 17.6c and the entry returns to 17:00.

What the test does not exercise: solar is measured, so every capture of a day
sees the same solar and forecast revisions to solar are absent; and each later
capture starts from the SOC live actually reached that day under the clock
window, not from where the block arm would have taken it, so the slot-0 column
is weaker evidence than the entry column. The entry time depends on neither.

Per #1111 the hysteresis was not widened. The lever the issue names is a
minimum dwell on the entry time. That would cover the second mechanism; the
first needs the block's existence held as well as its entry, or a `p_ref` that
remembers the cheapest price the day has already offered. That is a design
decision for the follow-up, not something this gate settles.

### Flap test with trough memory and entry dwell (#1114, 2026-10-09)

The fix decided for #1111's two mechanisms, built behind the same switch:

- **Trough memory.** The cheapest buy price actually observed in the current
  slot since the previous block ended, within the last 24 h, is kept on the
  coordinator data and passed to the detector, whose reference becomes
  `min(remembered trough, cheapest earlier slot in the horizon)`. It is dropped
  when the adopted block's entry arrives and nothing is remembered again until
  that block has ended.
- **Entry dwell.** A changed detection (entry moved by more than the one-slot
  hysteresis, or the block appearing or disappearing) is adopted only after it
  has persisted for 60 minutes (`ENTRY_DWELL_MINUTES`, an internal constant).
  Until then the previously adopted block stands, re-sized on the current
  forecast.

`--flap` now carries that state from each capture to the next in time order,
as live carries it from plan to plan, and the PASS / FAIL is read from that
run. The cold-start result is printed beside it, along with the `dw` arm's
cost and slot-0 action on the same captures.

**What the replay can and cannot seed.** A capture is the price sensor at one
instant. Its forecast list opens with the five-minute interval that had just
ended at capture time, at the price live saw as current (one interval, a few
when the sensor was slightly stale). That is a genuinely observed past price
and the harness feeds it to the trough at its own timestamp, for every capture.
Nothing earlier is in a capture, so the first capture of a day starts cold
apart from that one interval: no overnight or early-morning price is assumed,
where live would have had everything since the previous evening.

This matters. A capture taken exactly on the half-hour builds its slot 0 from
the *forecast* for the interval just starting, not the price of the interval
just ended, and on 2026-09-11 those differ by 3–4c (8.2c observed at 08:55,
11.6c forecast for 09:00). Without the past interval the trough on that day is
11.6c and the block is still lost, at 13:00 once the dwell runs out.

**Verdict (2026-10-09, state carried): FAIL. Three of five days pass on the
committed two-hour captures and two of five on 30-minute captures. The #1114
acceptance is not met.**

Committed captures (`simulations/replay-nodw-flap/`), block entry per capture:

| day | 09:00 | 11:00 | 13:00 | 14:30 | slot-0 action | verdict | cold start |
|---|---|---|---|---|---|---|---|
| 2026-09-07 | 16:30 | 16:30 | 16:30 | 16:30 | hold, hold, hold, hold | PASS | PASS |
| 2026-09-11 | 17:00 | 17:00 | 17:00 | 17:00 | hold, hold, charge, hold | PASS | FAIL |
| 2026-09-21 | 17:00 | 17:00 | 17:00 | 17:00 | charge, hold, charge, hold | **FAIL** | FAIL |
| 2026-09-23 | 18:00 | 18:00 | none | none | hold, hold, hold, hold | **FAIL** | FAIL |
| 2026-09-26 | 17:00 | 17:00 | 17:00 | 17:00 | boost, boost, boost, hold | PASS | PASS |

Denser captures, every 30 minutes from 09:00 to 15:00 (13 per day, not
committed; `export_replay_days.py --date <day> --time 09:00,09:30,...,15:00`
rebuilds them while the recorder holds the day):

| day | block entry through the day | verdict | failing criterion | `dw` arm slot-0 also charge / hold / charge |
|---|---|---|---|---|
| 2026-09-07 | 16:30 at all 13 | PASS | – | no |
| 2026-09-11 | 17:00 at all 13 | **FAIL** | slot-0 action only | no |
| 2026-09-21 | 17:00 ×3, 14:30 from 10:30 ×6, 16:30 from 13:30 ×4 | **FAIL** | entry −150 min and +120 min; slot-0 action | yes |
| 2026-09-23 | 18:00 at all 13 | PASS | – | yes |
| 2026-09-26 | 17:00 at all 13 | **FAIL** | slot-0 action only | yes |

What changed against the #1111 result:

- **Mechanism 1 (the trough rolls off) is fixed where the trough was real.**
  2026-09-11 holds 17:00 at every capture on both cadences (cold: gone by
  11:00). 2026-09-26 holds 17:00 at all 13 half-hour captures, where the cold
  detector's block appears or disappears six times.
- **Mechanism 2 (forecast revision) is absorbed at two-hour cadence and not at
  30 minutes.** On 2026-09-21 the 11:00 revision to 15:00 is held pending and
  gone by 13:00, so the entry stays at 17:00. The half-hour captures show the
  revision actually lasted from 09:30 to about 12:00: it outlasts the dwell,
  is adopted at 10:30, and is reversed at 13:30.

What still fails, and why no dwell value fixes it:

1. **2026-09-21, entry, 30-minute captures.** Measured at other dwell values,
   as evidence and not as a setting: the entry still moves at 60, 90 and 120
   minutes and holds at 17:00 only from 180. The constant was left at 60.
2. **2026-09-23, two-hour captures: the block is dropped.** The 09:00 block
   rested on a *forecast* trough of 12.3c for midday that never arrived (the
   afternoon cleared at 17–20c) and on an evening that softened from 21.9 to
   20.4c. Nothing observed justifies it, the detection disappears at 11:00 and
   the dwell lets it go at 13:00. At 180 minutes it goes at 14:30 instead. Only
   a day-long latch keeps it, which is the design that was ruled out. On the
   half-hour captures the block survives because a 11.5c interval at 09:25 is
   in the record, and even there the disappearance is pending from 14:30 and
   would be adopted at 15:30, after the last capture. Whether a block that was
   justified only by a forecast should count as a flap when it goes once and
   stays gone is a question about the criterion, not something tuned here.
3. **Slot-0 action goes charge / hold / charge** on 2026-09-21 (both
   cadences), 2026-09-11 and 2026-09-26 (30-minute). The entry is steady on
   the last two, so this is not the boundary. Two things drive it. The
   load-sized target moves with the load forecast from capture to capture
   (2026-09-11: 95, 71, 74, 95, 95, 95, 95, 95, 95, 76, 95, 51, 95%), and a
   capture whose target is already met holds. And the planner re-picks the
   cheapest slot as the price forecast is revised, which the `dw` arm does
   too: it goes charge / hold / charge on three of the five days at 30-minute
   cadence. On 2026-09-11 it does not, so there the alternation is the block
   arm's own. The #1111 caveat applies with more force at this cadence: each
   capture starts from the SOC live reached under the clock window.

**Cost of persisting the block (2026-09-11 and 2026-09-23).** Projected net
cost of the carried block arm against `dw` and against no block at all, from
the same capture. Each row is one plan from the SOC live had reached, so the
rows are not additive.

| capture | block | target % | grid charge scheduled | block arm $ | `dw` $ | block − `dw` | no block $ |
|---|---|---|---|---|---|---|---|
| 09-11 11:00 | 17:00–00:30 | 95.0 | 14.0 kWh at 9.4–10.5c, then 1.9 kWh at 15.0c at 16:30 | 4.748 | 4.740 | +0.008 | 5.184 |
| 09-11 13:00 | 17:00–00:30 | 95.0 | 8.0 kWh at 11.8–12.2c, then 1.7 kWh at 15.2c at 16:30 | 5.490 | 5.479 | +0.011 | 5.699 |
| 09-11 14:30 | 17:00–01:00 | 39.9 | none (battery at 89%) | -0.123 | -0.123 | 0.000 | -0.123 |
| 09-23 11:00 | 18:00–20:30, held, disappearance pending | 38.3 | 8.1 kWh at 12.8–13.6c, then 2.2 kWh at 18.3c at 16:30 | 4.150 | 4.075 | +0.075 | 4.127 |
| 09-23 13:00 | none | – | none | 3.370 | 3.519 | -0.149 | 3.370 |
| 09-23 14:30 | none | – | none | 2.547 | 2.702 | -0.155 | 2.547 |

At those three hours the persisted block is within a cent of `dw` on
2026-09-11 and $0.21–0.44 better than losing the block. The half-hour captures
are less kind. Of 13 captures per day the block arm is above `dw` by more than
ten cents on five:

| capture | block arm $ | `dw` $ | block − `dw` | no block $ | block − no block |
|---|---|---|---|---|---|
| 09-11 12:00 | 10.704 | 10.395 | +0.309 | 9.947 | +0.757 |
| 09-11 12:30 | 5.228 | 4.908 | +0.320 | 5.019 | +0.209 |
| 09-23 09:30 | 3.934 | 3.409 | +0.525 | 3.595 | +0.339 |
| 09-23 10:00 | 3.748 | 3.243 | +0.505 | 3.470 | +0.278 |
| 09-23 11:00 | 4.391 | 4.075 | +0.316 | 4.127 | +0.264 |

Two things are visible in those plans, and both are the remembered trough
funding a deadline the remaining prices cannot fund well:

- **The top-up just before entry buys at the top.** Every plan that still has
  a shortfall takes the last slot or two before the block: 15.0–15.2c at 16:30
  for a 16.5–17.5c evening on 2026-09-11, 18.3c at 16:30 and 19.5c at 17:30
  for a 19–21c evening on 2026-09-23. After round-trip loss (87%) those are
  break-even at best and a loss at worst. This is the hard entry target doing
  what it did on 2026-09-21 in the sweep.
- **On 2026-09-23 the block arm does not buy the day's cheapest slots.** At
  09:30 `dw` charges at 12.4c from 13:30 to 14:30; the block arm sits at the
  floor through those slots and charges 9.4 kWh from 15:00 to 16:30 at
  12.9–16.8c under `TARGET_SHORTFALL_RISK`. Why the deadline machinery funds a
  47% load-sized target from the last slots and not the cheapest was not
  established here; nothing in `feasible_actions`, the urgency window or the
  cycle-hurdle exemption was changed.

2026-09-11 at 12:00 is partly the inflated load forecast that capture carries
(a 55 kWh need): `dw` is itself $0.45 above no block there.

**Ten-day gate and 31-day sweep, re-run.** `slice 1 gate: PASS (10 day(s): 9
identical to dw, 1 below, 0 above)` and, on the sweep, `FAIL (31 day(s): 21
identical to dw, 6 below, 4 above)`, worst +$0.239 on 2026-09-21, zero
overnight grid charging on all 41 days. Both are the previous result to the
cent, and they could not have been otherwise: each is a single cold 09:00 plan,
where the remembered trough is the current slot (already in the horizon) and
there is no earlier detection to dwell against. They prove the change leaves a
cold first plan alone, and nothing about the memory or the dwell. With each
capture's own past interval seeded into the trough the sweep reads 21
identical, 7 below, 3 above (2026-09-26 moves from +$0.004 to −$0.021 as its
entry goes from 17:30 to 17:00), the gate is unchanged, and no day is dearer
than before.

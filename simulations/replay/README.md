# Replay days

Real days captured from Home Assistant and replayed offline through the
optimizer, so a proposed behaviour change can be measured against days that
actually happened before it reaches live.

```bash
scripts/export_replay_days.py --days 10 --hour 9     # capture
scripts/replay_adaptive_arms.py                      # compare arms
```

Three days spanning the solar range are committed so the harness runs from a
clean checkout; the rest are gitignored — regenerate them with the exporter.
Recorder retention is about ten days, so anything older cannot be recaptured.

## What is real and what is not

**Prices are exactly what the optimizer saw.** The Amber sensors record a
`forecast` attribute that survives in the recorder and is converted straight
into scenario slots.

**Solar is measured, not forecast.** Home Assistant's recorder drops large list
attributes, so Solcast's `detailedForecast` is not retained for past days —
only the day's total kWh survives. The exporter therefore rebuilds the solar
series from `sensor.my_home_solar_power`, which *is* recorded at full
resolution, averaged into 30-minute buckets to match Solcast's `pv_estimate`
(an average kW over the period).

The consequence: a replay hands every arm perfect solar foresight, so absolute
numbers are optimistic. Every arm receives byte-identical inputs, so
*differences between arms* remain valid — and comparing arms is the only thing
these files are for. Do not quote a replay's absolute cost as a prediction.

## Result: the Slice 0 gate (2026-09-04, re-run 2026-09-08 after a harness fix)

Ten days, three arms, changing only the adaptive-parameter offsets.

| arm | offsets |
|---|---|
| `current` | what live runs — `cheap_price_bias` pinned at its `+5` bound by the contextual ratchet, plus the learned `-0.5` SOC offsets |
| `learned` | the learned base without the ratchet (`cheap_price_bias +3`) |
| `zero` | no offsets — what removing the learning layer gives |

Planned SOC at demand-window start, `zero` minus `current`:

| day | solar (kWh) | current | zero | delta |
|---|---|---|---|---|
| 2026-08-25 | 6.7 | 94.36 | 97.57 | **+3.21** |
| 2026-08-26 | 12.0 | 97.67 | 97.67 | 0.00 |
| 2026-08-27 | 16.9 | 94.62 | 94.62 | 0.00 |
| 2026-08-28 | 29.8 | 100.00 | 100.00 | 0.00 |
| 2026-08-29 | 25.0 | 97.81 | 97.81 | 0.00 |
| 2026-08-30 | 30.5 | 100.00 | 100.00 | 0.00 |
| 2026-08-31 | 30.4 | 100.00 | 100.00 | 0.00 |
| 2026-09-01 | 30.9 | 100.00 | 100.00 | 0.00 |
| 2026-09-02 | 21.2 | 100.00 | 100.00 | 0.00 |
| 2026-09-03 | 30.9 | 100.00 | 100.00 | 0.00 |

Worst delta 3.21pp against a 1pp decision rule, on 10 of 10 comparable days;
one day breaches, and it breaches upward: `zero` enters the demand window
3.2pp *higher* than `current`. `learned` was identical to `current` on every
day: the ratchet's extra +2c changed nothing the +3c base had not already
changed.

Two secondary findings:

- **Cost.** Identical on nine days. On 2026-08-25 `zero` is **$0.055 dearer**
  ($1.649 vs $1.595): it buys 0.44 kWh more pre-charge for the extra 3.2pp.
- **Shortfall +1.00pp** on the three solar-constrained days is not a
  regression. `grid_charge_soc_headroom` and `overnight_drain_safety_margin`
  each sat at `-0.5`, so the learned offsets were quietly lowering the
  demand-window target by 1pp. Removing them restores the operator's
  configured 95%, and the shortfall is measured against that higher bar.

Verdict by the letter of the rule: one breach, so the bias would be re-exposed
as a knob before removal. The breach is in `zero`'s favour on the metric the
rule protects, at five cents of cost; whether that counts is the operator's
call, not the harness's. The layer was retired and deployed live on
2026-09-04 on the strength of the pre-fix table below; on 2026-09-08, with
the corrected table in hand, the operator accepted the upward breach and the
layer stays retired.

### Harness fix, 2026-09-08 — what moved and why

The 2026-09-04 run had a warm-up bug. A fresh `CoordinatorData` carries
`forecast_horizon_hours = 0.0`, and two things read it before the optimizer
facade writes the real span: the cheap-price percentile is scaled by
`max(0.1, horizon/24)`, and every grid-charge slot pays the short-horizon
uncertainty penalty at its full factor. Live never sees either because the
previous cycle's value carries over. The script now seeds the horizon from the
forecast's own span before the first pass and runs two passes, reporting the
second — the same warm-up `scripts/replay_no_dw.py` uses — so the reported
pass carries the facade's own horizon exactly as live does.

Numbers that moved, old -> new:

| day | what | before | after |
|---|---|---|---|
| 2026-08-26 | planned SOC at DW start, all three arms | 29.31 | 97.67 |
| 2026-08-26 | pre-charge kWh, all three arms | 1.65 | 11.70 |
| 2026-08-25 | `current` planned SOC at DW start | 97.88 | 94.36 |
| 2026-08-25 | `zero` planned SOC at DW start | 97.57 | 97.57 |
| 2026-08-25/26/27 | projected net cost, all arms | $2.30 / $2.33 / $0.31 | $1.59–1.65 / $1.80 / $0.19 |

The 2026-08-26 row was the bug outright: the old harness starved that day's
pre-charge on every arm, so its 29% said nothing about the offsets. The cost
columns on the three solar-constrained days fell as the full-strength
uncertainty penalty came off.

The 2026-08-25 breach is real but narrow. With the +5c bias, that day sits on
a knife-edge between a 12-slot pre-charge plan (DW entry 99.6%) and a 10-slot
one (96.0%); sweeping the seeded horizon shows it flips between the two on a
difference of five minutes of horizon, worth about $0.0002/kWh of penalty.
`zero` plans 12 slots at every horizon tested. The old -0.31pp was the same
knife-edge landing the other way under the full-strength penalty, so the
previous table never measured the offsets cleanly on that day either.

A note on the metric. As of issue #973, `dw_entry_soc_pct` from the optimizer
summary is computed whenever the plan has a demand window at all, regardless
of whether solar can reach target — it used to be gated on solar *cannot*
reach target, which left it absent by design on good days. This replay
predates that fix, so the table above instead reads the planned SOC at
demand-window start out of `data.optimizer_decisions`, which was populated on
every day either way.

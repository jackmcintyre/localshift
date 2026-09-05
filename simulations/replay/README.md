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

## Result: the Slice 0 gate (2026-09-04)

Ten days, three arms, changing only the adaptive-parameter offsets.

| arm | offsets |
|---|---|
| `current` | what live runs — `cheap_price_bias` pinned at its `+5` bound by the contextual ratchet, plus the learned `-0.5` SOC offsets |
| `learned` | the learned base without the ratchet (`cheap_price_bias +3`) |
| `zero` | no offsets — what removing the learning layer gives |

Planned SOC at demand-window start, `zero` minus `current`:

| day | solar (kWh) | current | zero | delta |
|---|---|---|---|---|
| 2026-08-25 | 6.7 | 97.88 | 97.57 | **-0.31** |
| 2026-08-26 | 12.0 | 29.31 | 29.31 | 0.00 |
| 2026-08-27 | 16.9 | 94.62 | 94.62 | 0.00 |
| 2026-08-28 | 29.8 | 100.00 | 100.00 | 0.00 |
| 2026-08-29 | 25.0 | 97.81 | 97.81 | 0.00 |
| 2026-08-30 | 30.5 | 100.00 | 100.00 | 0.00 |
| 2026-08-31 | 30.4 | 100.00 | 100.00 | 0.00 |
| 2026-09-01 | 30.9 | 100.00 | 100.00 | 0.00 |
| 2026-09-02 | 21.2 | 100.00 | 100.00 | 0.00 |
| 2026-09-03 | 30.9 | 100.00 | 100.00 | 0.00 |

Worst delta 0.31pp against a 1pp decision rule, on 10 of 10 comparable days.
`learned` was identical to `current` on every day: the ratchet's extra +2c
changed nothing the +3c base had not already changed.

Two secondary findings, both arguing for removal rather than against it:

- **Cost.** Identical on nine days. On 2026-08-25 `zero` was **$0.055 cheaper**.
- **Shortfall +1.00pp** on the three solar-constrained days is not a
  regression. `grid_charge_soc_headroom` and `overnight_drain_safety_margin`
  each sat at `-0.5`, so the learned offsets were quietly lowering the
  demand-window target by 1pp. Removing them restores the operator's
  configured 95%, and the shortfall is measured against that higher bar.

Verdict: the offsets can be removed outright.

A note on the metric. `dw_entry_soc_pct` from the optimizer summary is only
computed when solar *cannot* reach target (`engine/core.py:728`), so it is
absent by design on good days — seven of these ten. The table above therefore
reads the planned SOC at demand-window start out of `data.optimizer_decisions`,
which is populated on every day.

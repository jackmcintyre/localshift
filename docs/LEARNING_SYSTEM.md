# The learning system (retired September 2026)

LocalShift used to carry a parameter-learning layer: it scored each mode
decision, ran Thompson sampling over six optimizer parameters, mined the
decision history for biases, layered contextual adjustments on top, and
compared itself against a time-of-use baseline. It was removed. This page
records what it was, why it went, and what would have to be true to bring it
back — so the next person with the idea starts from the evidence rather than
from scratch.

## What it did

| Piece | Module | Job |
|---|---|---|
| Outcome scoring | `engine/outcomes.py` | Score each decision 0–1 after a 30-minute window |
| Parameter optimizer | `engine/parameters.py` | Thompson sampling over 6 parameters |
| Pattern analyzer | `engine/pattern_analyzer.py` | Mine the decision history for biases by hour, weekday, weather, season |
| Contextual controller | `engine/optimization_controller.py` | Heuristic overlay for SOC emergencies, low forecast accuracy, approaching demand window |
| Counterfactual | `engine/counterfactual.py` | Compare actual cost against a TOU baseline |

Its output reached the optimizer as six offsets on `data.adaptive_params`,
applied in `engine/optimizer_runner.py` — most importantly `cheap_price_bias`,
which shifts the price threshold below which grid charging is allowed.

## Why it was retired

**It never produced a measurable gain.** `sensor.localshift_decision_quality`
across 75 days: mean 55.38, standard deviation 3.00, and a drift between the
first and last 25 days of **+0.32 points**. Nothing.

**Its own metric could not have detected a gain.** Holding the mode mixture at
what the optimizer actually produces and varying only outcome quality, the
whole span from a catastrophic day to a flawless one is **12.7 points** —
against 3.0 points of ordinary day-to-day noise. A real improvement is worth
one or two points, permanently under the noise floor.

**The reward has no gradient where the system operates.** `_compute_target_score`
is a step function: a linear gradient only within 15pp of target, flat zero
from 15–20pp, flat −0.10 beyond. Typical daytime SOC of 10–70% lands entirely
in the flat zone, so a HOLD at 10% and a HOLD at 70% score identically. For
HOLD specifically the far-penalty branch is unreachable by construction, and
with hold costs near zero the score collapses to a near-constant 0.56 — about
half of all decisions.

**The frame was wrong, not just the tuning.** The reward is scored over a
30-minute window (`MAX_DECISION_DURATION`), but the outcome that matters —
demand-window entry SOC at 15:00 — arrives hours later. A HOLD at 40% at 10am
on a sunny day is *correct*, because the plan is to solar-charge to target by
15:00. No amount of reshaping a per-decision reward fixes that; it needs
episode-level credit assignment. This is why #626, #915 and #925 each tuned the
scoring function and none of them changed the outcome.

**Meanwhile it did change live behaviour, unintentionally.** Contextual
adjustments compounded: `OptimizationController.evaluate()` rebased on its own
previous output every tick and every rule was additive, so `cheap_price_bias`
ratcheted to its `+5` bound within two ticks of an SOC dip and stayed there
until the next daily optimizer run — with `contextual_adjustments_active`
reporting an empty list. Live had never once run without a +3 to +5 c/kWh
inflation of the grid-charge gate.

**Three investigations, one outcome.** #170 built it (February). #913 found the
core inert (August) and #914 repaired two of three defects. A third review in
September found the ratchet, the step-function reward, and a counterfactual
that appended a result on every 5-minute tick — making `advantage_7d` a sum of
roughly 900 duplicate evaluations of the same day, and `is_degrading()`
compare a per-tick figure against a per-day threshold.

## What removing it changed

A ten-day offline replay over real captured days, varying only the offsets
(`scripts/replay_adaptive_arms.py`, full table in
[`simulations/replay/README.md`](../../simulations/replay/README.md)):

- Planned demand-window entry SOC moved by at most **0.31pp**, on 10 of 10 days.
- Projected cost was identical on nine days and **$0.055 cheaper** on the tenth.
- Terminal shortfall rose 1.00pp on the three solar-constrained days — not a
  regression: `grid_charge_soc_headroom` and `overnight_drain_safety_margin`
  both sat at `-0.5`, so the learned offsets had been quietly lowering the
  demand-window target by 1pp. Removal restores the operator's configured 95%.

## What survives

- **The decision records.** `engine/outcomes.py` still records every mode
  decision, the conditions at the time, and the measured outcome, surfaced on
  `sensor.localshift_learning_decision_history`. Nothing reads them back. They
  are kept because they are the only durable asset the layer produced, and any
  future attempt would need exactly this history.
- **`compute_outcome_score`** still runs, as an attribute on each record. Treat
  it as uninformative — the analysis above is about this function.
- **Solar forecast bias correction** (`forecast/solar_accuracy.py`) is a
  separate, healthy loop and was untouched.
- **Weather/load correlation** (`learning/correlation.py`) feeds the load
  forecast and is independent of parameter learning.
- **`data.adaptive_params`** remains at its zero default. The optimizer reads
  the offsets unconditionally and zero is the identity, so the field stays
  rather than threading a removal through seven call sites.

## Bringing it back

`tests/test_learning_layer_retired.py` fails if a retired module or field
returns. That is deliberate. If you are replacing it, delete that test in the
same commit so the decision is explicit in history — do not edit it to pass.

Before writing code, clear these three bars:

1. **A metric that can see the answer.** Whatever you optimise must resolve a
   good day from a bad one by more than its own day-to-day noise. Establish
   that first, on replayed days, before building anything that consumes it.
2. **Episode-level credit assignment.** Per-decision scoring over a 30-minute
   window cannot represent "did we reach target at 15:00, and at what cost".
3. **An offline win before a live wire.** Beat the deterministic optimizer on
   replayed real days (`scripts/export_replay_days.py` captures them). The
   optimizer plus its execution guards is what carries this system; a learning
   layer has to show it adds something on top.

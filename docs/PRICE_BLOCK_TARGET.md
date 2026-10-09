# Price-driven target blocks

**Status:** design, 2026-09-08. Slice 1 is built behind
`switch.localshift_price_block_target` (default off) and has not passed its
flap test: see "Trough memory and entry dwell" below. Evidence in
`simulations/replay-nodw/README.md`; harness `scripts/replay_no_dw.py`.

## The problem

The tariff's demand charge is seasonal. The planner's demand window is not: it
is a clock window (15:00–21:00) applied every day of the year, and it does
three jobs at once.

| job | what it drives | belongs to |
|---|---|---|
| **A. import ban** | no grid charging inside the window (`feasible_actions`), Demand Block mode at execution | the demand charge |
| **B. deadline + target** | terminal shortfall penalty, hard floor (#886), water-level funding thresholds, urgency window, boost unlocks, the min-cycle-saving exemption for pre-window slots | the expensive evening |
| **C. useful period** | where the futile-cycling simulation stops draining | the expensive evening |

Only A is about the demand charge. B and C are about the expensive evening,
which exists all year and is the thing the planner actually needs to prepare
for. The replay shows the consequence of conflating them: with the window
removed, the planner has no way to fund a 7c-morning-for-18c-evening
pre-charge (+$0.88 on 2026-09-07), and with it kept, the planner enforces a
95% readiness target and a charging ban that have no basis once the demand
charge is off. Neither state is right for the shoulder season.

The DP's cost function already knows how to plan a no-demand-charge day: with
every admission gate removed it reproduces the demand-window plan to the cent
(replay, `gateless_mcs0`). What it lacks is a *funder* for the expensive
block. Today the only funder is the demand-window machinery.

## Two concepts instead of one

**`charge_window`** — the demand-charge window. Sourced from Amber's per-slot
`demand_window` flag (seasonal by construction; the Express feed carries it
today and the provider currently drops it at normalisation), with the
configured clock window as the fallback when the feed has no flag. Drives
**A only**. Off-season there is no charge window and no ban.

**`target_block`** — the expensive block. Derived from the price and load
forecast on every plan: the first contiguous run of slots that is materially
dearer than the cheapest charge the battery could take beforehand and that
carries positive net load. Drives **B and C**. It exists all year. In season
it will normally coincide with the charge window; out of season it is
whatever the evening price shape says it is.

The target SOC follows the same split. Inside a charge window the target is
`battery_target` (95%), because readiness has value beyond the energy. For a
price-only block the target is **load-sized**: enough SOC to carry the block's
accuracy-discounted net load, plus the existing drain margin, clamped to
`[minimum_target_soc, battery_target]`. On a mild shoulder evening that is
60%, not 95%.

Nothing downstream changes. The terminal penalty, hard floor, water level,
urgency window, boost unlocks, useful-period stop and every #800 guard keyed
on "pre-window slot" all read the same two slot flags they read today. The
block sets those flags; the machinery follows.

## Why not a spread-based admission rule

The obvious alternative is to generalise spike funding (#910): admit a charge
slot whenever the spread to a dearer future slot beats the cycle hurdle. That
is what #908 shipped, and `engine/spike_event.py` records why it failed: a
spread test alone fires on roughly half of ordinary days, because on any
ordinary day the overnight trough sits a full hurdle below the morning peak.
Qualification then sits on a knife edge, forecast jitter flips it between
re-plans, and the committed action flips with it. That is the #800 sawtooth
as the battery experiences it. #910 fixed it by restricting funding to
4×-median outliers, which is why it is inert on 2026-09-07.

The block-target design does not admit slots by marginal spread. It funds a
*deadline* from the *cheapest sufficient set* of slots, via the water level
`compute_pre_dw_charge_thresholds` already computes, with the urgency ramp
and hysteresis already measured across three months of live operation. The
min-cycle-saving exemption keeps exactly its current scope: pre-block slots
at or below the water level. Post-block and far-out slots face the full gate,
which is the #800 protection, unchanged.

## Block detection (concrete, slice 1)

For the planning horizon after `now`:

1. `p_ref(i)` = the cheapest buy price in any slot before `i` (the cheapest
   charge the battery could have taken), or the remembered trough when that is
   cheaper (#1114, below). Within one plan it is monotone non-increasing in
   `i`, so it cannot jitter a boundary on its own. Between plans that was not
   true of the horizon alone: the reference rose as the cheap morning became
   the past, and the block vanished mid-morning (#1111).
2. A slot is *dear* when `buy_price(i) >= p_ref(i) + block_min_spread` and
   `consumption_kwh(i) > solar_kwh(i)`.
3. The block is the first maximal run of dear slots (single-slot gaps
   tolerated) of at least `block_min_duration` (default 2 h). Entry index →
   `terminal_penalty_idx`; the run's slots get `is_demand_window_slot`; the
   first gets `is_demand_window_entry`.
4. Target SOC = `clamp(minimum_target_soc + needed_pct + headroom,
   minimum_target_soc, battery_target)` where `needed_pct` is the block's
   net load, discounted by solar-forecast accuracy exactly as
   `check_global_solar_sufficiency` discounts it, divided by discharge
   efficiency and capacity.
5. If a charge window is active on the day (slice 2) the block is the charge
   window and the target is `battery_target`, i.e. today's behaviour.

`block_min_spread` is a new operator knob. It is **not** `min_cycle_saving`.
The cycle hurdle governs speculative arbitrage and is set at $0.25/kWh to keep
the planner off the knife edge described above. The block spread answers a
different question — is this evening expensive enough to prepare for — and on
2026-09-07 the honest answer is yes at a 10c spread, which the $0.25 hurdle
would reject. Default $0.08/kWh (about the round-trip loss on a typical
Amber evening) — decided 2026-09-08 as a separate knob, not a reuse of
`min_cycle_saving`; the replay makes the dollar consequence of any value
visible before it ships.

Hysteresis: the entry index is held from the previous plan when the new
detection lands within one slot of it. Whether that is sufficient is measured
(see the flap test below), not assumed. It was not (#1111), which is what the
next section is for.

## Trough memory and entry dwell (#1114)

The flap test failed on three of five solar-deficit days for two reasons, and
the fix decided on 2026-10-09 addresses each. Neither is an operator entity.
`find_target_block` stays a pure function; the state lives on the coordinator
data and is handled where the flags are written (`apply_price_block_flags`).

**Trough memory.** On every live plan the current slot's buy price is noted.
What is kept is the cheapest price observed since the previous block ended,
within the last 24 hours, with the time it was last seen, and the detector's
reference becomes `min(remembered trough, cheapest earlier slot in the
horizon)`. The trough can only lower the reference, and it never makes the
current slot dear.

- *Reset.* When the adopted block's entry arrives the trough is dropped, and
  nothing is remembered again until that block has ended. The reset is at the
  entry and not the end on purpose: a trough kept into the block would make
  every remaining slot dear against a price the battery can no longer buy at.
- *First run and restart.* Nothing is remembered and nothing survives a
  restart. The first plan detects on the horizon alone, exactly as before
  #1114, so a block that only the forgotten trough justified is gone until the
  horizon justifies it again. No deadline is invented from a price nobody
  recorded.
- *Day rollover.* Nothing is keyed on the calendar day. A cheap price at 23:30
  funds the next evening.
- *A day with no block.* The memory is a rolling 24-hour minimum, so
  yesterday's trough expires on its own and the next-cheapest price still
  inside the window takes over.

**Entry dwell.** A changed detection is adopted only after it has persisted
for `ENTRY_DWELL_MINUTES` (60). A change is the entry moving by more than the
one-slot hysteresis, a block appearing, or a block disappearing. Until then
the previously adopted block stands whole, its need re-sized on the current
forecast. A revision that reverts inside the dwell never reaches the planner;
a different candidate restarts the clock; a candidate wobbling by one slot is
the same candidate.

- *The very first plan* (and the first after a restart) has nothing to hold,
  so its detection is adopted at once. That includes "no block": a block that
  appears later has to wait out the dwell like any other change.
- *A held block whose entry arrives* while a change is still pending was
  honoured as held: the battery was prepared for it. It is retired like any
  other block, the trough goes with it, and the fresh detection on that plan
  is adopted at once.
- *The shadow comparison* solves on a different price series. It gets a plain
  detection on its own prices and neither reads the trough nor moves the
  dwell.

Both are visible on `sensor.localshift_optimizer_summary`:
`target_block_trough_price` and `target_block_trough_at`, and for a change
that has not yet been adopted `target_block_pending_change` (`appear`, `move`
or `disappear`), `target_block_pending_entry` and
`target_block_pending_since`. `target_block_entry` is always the block the
planner actually worked to.

**Result (2026-10-09): the flap test still fails.** With the state carried
between captures the block's boundary is steady where the trough was really
observed (2026-09-11, 2026-09-26), but three things remain, none of which a
dwell value fixes: a revision that outlasts 60 minutes on 2026-09-21 at
30-minute cadence, a block on 2026-09-23 that rested on a forecast trough
which never arrived and is correctly let go, and a slot-0 action that
alternates because the load-sized target moves from plan to plan. Holding the
block also has a measured cost on some plans: the top-up just before entry
buys at the top. Numbers in `simulations/replay-nodw/README.md`.

## Slices

Vertical, each reaching the real runtime. The replay is the gate for every
one.

**Slice 1 — price block, off-season, live in dry run.** Block detector and
load-sized target behind a new switch (`price_block_target`, default off).
Provider and slot flags otherwise untouched: the block writes the existing
demand-window flags, so the import-ban side effect inside the block is
accepted for this slice (charging inside the expensive block is not something
we want anyway). Acceptance, all on the replay harness with a new `block`
arm: identical to `dw` on the nine sunny days; at or below `dw` on
2026-09-07 ($6.05); **flap test** clean — the same day captured at 09:00,
11:00, 13:00 and 14:30 must not move the committed action back and forth.
Then deploy with the switch on and `dry_run` on for two evenings, reading the
plan sensor and the `dw_entry_*` telemetry, before handing it control.

**Slice 2 — the charge window from Amber.** `ForecastSlot` keeps
`demand_window`; `SlotContext` gains `is_charge_window_slot`; the import ban,
Demand Block mode, and the negative-FIT DW guard key on it; the clock window
becomes the fallback. In season the block is the charge window and the target
is `battery_target`. Acceptance: the in-season replay (captured winter days
in `simulations/replay/`) is byte-identical to today; the shoulder replay
shows no import ban.

**Slice 3 — observability and UI.** Season state, block entry time, block
target and its sizing inputs on the summary sensor and dashboard; the
`solar_can_reach_target` and `dw_entry_*` sensors renamed or aliased to the
block. This is the "where we sourced it and the UI" work deferred from the
2026-09-08 discussion.

## Risks

- **Load forecast quality sizes the target.** The 2026-09-05 weather
  extrapolation doubled the afternoon load forecast; a load-sized target
  inherits that. Wrong-high buys extra at the cheap price (bounded by
  `battery_target`); wrong-low imports at the peak. The clamp bounds both,
  and the replay's 2026-09-07 day *is* the inflated case.
- **Boundary jitter.** The block's entry moves the terminal index, and the
  water level with it. `p_ref` cannot jitter within a plan; between plans it
  did, and the threshold crossing can at any time. The flap test is the
  acceptance criterion. One-slot hysteresis was not enough (#1111); trough
  memory and a 60-minute entry dwell were added (#1114) and are not enough
  either, as measured.
- **Target jitter.** The load-sized target follows the load forecast, which
  follows the last hour's load. On 2026-09-11 it read between 51% and 95%
  across thirteen half-hourly plans with the block's boundary unmoved, and the
  slot-0 action alternated with it. Nothing damps this yet.
- **A remembered trough is a price that is gone.** It keeps the deadline alive
  after the cheap slots have passed, and the hard entry target then buys
  whatever is left, including the slot just before the block at close to the
  block's own price.
- **Perfect-foresight replay.** Solar in the captures is measured, not
  forecast. The two-evening dry run is the real-solar check.
- **Interaction with spike funding.** #910 excludes demand-window slots from
  the event side. With a price block wearing the same flags, a spike inside
  the block is funded by the block target instead. That is the intended
  precedence, and it should be stated in the spike-funding tests.

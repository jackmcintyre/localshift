# LocalShift Planning Model: Soft-Constrained Dynamic Programming

## Overview

LocalShift uses a **Soft-Constrained Dynamic Programming** approach to compute optimal battery control decisions. This pattern combines hard constraints (physical limits, safety bounds) with soft penalties (economic costs, preferences) to create a clean, extensible planning system.

## Core Pattern

```
┌─────────────────────────────────────────────────────────────────┐
│                     TWO-LAYER DESIGN                            │
│                                                                  │
│  LAYER 1: Hard Constraints (feasibility)                       │
│  ─────────────────────────────────────────────                  │
│  • What CAN I do?                                               │
│  • Physical limits, safety bounds                               │
│  • Prunes action space                                          │
│                                                                  │
│  LAYER 2: Soft Penalties (optimization)                        │
│  ─────────────────────────────────────────────                  │
│  • What SHOULD I do?                                            │
│  • Economic costs, preferences                                  │
│  • Guides decision via cost minimization                        │
│                                                                  │
│  TERMINAL COST: Goal State Incentive                           │
│  ─────────────────────────────────────────────                  │
│  • What MUST I achieve?                                         │
│  • Encodes requirements (e.g., demand window target)            │
│  • Creates forward-looking incentive                            │
└─────────────────────────────────────────────────────────────────┘
```

## Component Mapping

| Layer | Implementation | Location |
|-------|----------------|----------|
| Hard Constraints | `feasible_actions()` | `optimizer_dp.py:1341` |
| Soft Penalties | `stage_cost()` | `optimizer_dp.py:1721` |
| Terminal Cost | `terminal_cost()` | `optimizer_dp.py:1818` |

## Hard Constraints: `feasible_actions()`

Determines which actions are physically/legal possible for a given state.

### Current Constraints

| Constraint | Effect | Code Location |
|-----------|--------|---------------|
| SOC floor/ceiling | `can_charge = soc < max_soc`, `can_discharge = soc > min_soc` | L1373-1374 |
| Demand window | No grid import during DW slots | L1415 |
| Price thresholds | Only charge if price is cheap (self-consumption mode) | L1419-1428 |
| Solar sufficiency | Suppress grid charging when solar covers deficit | L1378-1416 |
| Export profitability | Only export if sell price exceeds threshold | L1437-1446 |
| Spike export in self_consumption (Issue #1120) | The same-slot test (`sell >= buy + export_price_margin`) cannot pass on Amber prices, where feed-in is always below general for the same interval. The current interval (slot 0 only; a forecast spike is never planned as an export, so the rule cannot become a reason to charge beforehand) is also admitted for EXPORT when it is a genuine spike: sell price at least `SPIKE_EXPORT_OUTLIER_FACTOR` (4) times the horizon's median buy price, AND sell price >= rebuy price / round-trip efficiency + `export_price_margin`, where the rebuy price is the dearest buy price in the next `SPIKE_EXPORT_REBUY_LOOKAHEAD_HOURS` (12) once the spike is over, AND no later interval of the same spike pays more. "The same spike" is the unbroken run of slots after slot 0 whose sell price is at or above the outlier floor; those slots are somewhere else to sell, so they are left out of the rebuy price and compared sell against sell (with them in the rebuy price, a flat or falling spike was held through until its last interval). A dear slot after a gap is still a rebuy price. Set as `SlotContext.spike_export_admitted` on slot 0, with the verdict on `OptimizerConfig.spike_export_now`. It only widens the feasible set: the DP still prices the export, the load-sized export floor still bounds it, and it does not change which slots may grid-charge. It can make the plan charge more in slots that were already allowed to: before the demand window a spike export leaves a gap to the entry target and the DP refills it (2026-10-09 12:30 capture with a $1.00 spike: one charge slot becomes four at about 9.6c, projected entry 100% -> 95.3%). That is an export then a recharge within the hour, priced by the DP, not prevented by this rule. The outlier factor is the anti-flap property and must not be lowered to catch "nearly spikes" (same reasoning as `spike_event`). The state machine skips the 2-minute export debounce for such a slot only when the price is confirmed (`spike_export.skip_debounce`). `switch.localshift_spike_discharge_enabled` (ships OFF and is turned off once on upgrade, `OptimizerConfig.spike_export_enabled`) switches the whole rule off: no slot is admitted and nothing skips the debounce, in `arbitrage` as well as `self_consumption` | `engine/spike_export.py`, `engine/constraints.py`, `state/machine.py` (`_spike_export_confirmed`) |
| Negative-FIT DW guardrail | In avoidance mode, DW export allowed only if net benefit >= $0.02/kWh | `engine/constraints.py` / `engine/core.py` |
| Negative-FIT materiality | The avoidance window only opens when the spill it would cost (surplus the battery cannot store on the do-nothing trajectory, times the magnitude of the non-positive sell price) reaches `NEGATIVE_FIT_MIN_SPILL_COST` ($0.25 for the whole window; a constant, deliberately not the `min_cycle_saving` slider, which is $/kWh for grid charging). Below that the context is not created, so export falls back to the ordinary value test. Once open, the window is not re-tested against the spill cost, which its own pre-discharge drives down; it stays open while the forecast's exposure (all window surplus at its non-positive price, battery ignored) is at least `NEGATIVE_FIT_SPILL_RELEASE_FRACTION` (0.5) of the threshold. The open state is carried between cycles on `CoordinatorData.negative_fit_window_open` (in memory; a restart re-tests from cold). Published on the optimizer summary as `negative_fit_spill_cost`, `negative_fit_spill_exposure`, `negative_fit_spill_threshold`, `negative_fit_suppressed`, `negative_fit_window_open`, `negative_fit_held_open` (#1103) | `engine/negative_fit.py`, `engine/optimizer_facade.py` |
| Negative-FIT recoverability floor | In avoidance mode EXPORT may not take SOC below `target - recoverable`, where `recoverable` is forecast net solar from the next slot to the recovery deadline, times charge efficiency, times `min(NEGATIVE_FIT_RECOVERY_SOLAR_FACTOR (0.8), solar_forecast_accuracy)`. The accuracy is the same per-cycle `OptimizerConfig.solar_forecast_accuracy` the target-reach checks discount by (#816), so it is 1.0 with no tracker and the floor is then the fixed 0.8 one. A tracker whose samples measure accuracy at zero (MAPE at or over 100) is the exception to reading that figure: `get_forecast_accuracy` reports it as 1.0, so the floor asks the tracker directly (`forecast_accuracy_measured_as_zero`) and counts on no solar, which puts the floor at target. Accuracy only ever raises the floor, and a worse measurement never lowers it. Published as `negative_fit_recovery_solar_factor` and `negative_fit_floor_now_pct` (#1103) | `engine/negative_fit.py`, `engine/constraints.py` |
| Away discharge floor | While away is active, `can_discharge = soc > config.discharge_floor_pct` (`max(min_soc_pct, away_reserve_floor_pct)`), and the HOLD/EXPORT transitions clamp their drain at it too | `engine/constraints.py:58,112`, `engine/transitions.py:108,285` (docs/holiday-away/plan.md item 4) |
| Load-sized export floor | EXPORT may not drain below forecast net load over the rest of the elevated-price period (buy price above 1.5x the horizon median, ending after 60 min of normal prices), times `export_floor_load_margin` (1.5). A later slot only counts if its buy price is at least half the current slot's sell price, so an extreme sell price releases the floor. Set per slot as `SlotContext.export_floor_pct`; raises the discharge floor for EXPORT only, so HOLD still serves load beneath it and it never unlocks grid charging. The PROACTIVE_EXPORT hardware reserve stops at the same floor | `engine/export_floor.py`, `engine/constraints.py`, `engine/transitions.py`, `state/machine.py` |
| Unconfirmed current price (Issue #1119) | Slot 0's sell price is the feed-in entry covering now, not the 15-minute lookup later slots use. While that entry is still Amber's `estimate` (the previous interval's price, carried until the real one is confirmed) and the interval is under 90 s old, slot 0 may not START an export: `SlotContext.export_available` is cleared unless PROACTIVE_EXPORT is already the committed mode. Past 90 s the estimate is acted on. Buy-side decisions are not gated. Amber's confirmation is an attribute update, so a confirmation at the carried price moves nothing in the decision context: if a decision token was spent while the start was deferred, the state machine grants one more once the deferral lifts (confirmed, or grace expired), and that token may only commit PROACTIVE_EXPORT (`mode_decision_export_start_only`) | `engine/slots.py` (`_price_slot0_from_current_interval`), `engine/constraints.py`, `state/machine.py` (`_update_export_start_epoch`), `engine/optimizer_facade.py` |

### When to Add Hard Constraints

Add to `feasible_actions()` when:
- **Physical impossibility**: Battery cannot charge when full
- **Safety requirements**: Never drop below minimum SOC
- **Regulatory/contractual**: Demand window has no grid import
- **Feature gates**: Disable certain actions based on configuration

### Example: Adding a New Hard Constraint

```python
# Scenario: Never export during "quiet hours" (10 PM - 6 AM)
@staticmethod
def feasible_actions(...) -> list[PlannerAction]:
    actions = []
    
    # ... existing constraints ...
    
    # Quiet hours constraint
    slot_hour = datetime.fromisoformat(slot.timestamp_iso).hour
    is_quiet_hours = slot_hour >= 22 or slot_hour < 6
    
    # Export constraints
    if can_discharge and not is_quiet_hours:  # NEW: quiet hours check
        if config.optimization_mode == "self_consumption":
            if slot.sell_price >= min_profitable_sell:
                actions.append(PlannerAction.EXPORT_PROACTIVE)
    
    return actions
```

## Soft Penalties: `stage_cost()`

Encodes preferences, costs, and behavioral biases into a scalar cost.

### Current Penalty Terms

| Penalty | Formula | Purpose | Code Location |
|---------|---------|---------|---------------|
| `import_cost` | `grid_import × buy_price` | Direct cost of buying from grid | L1754 |
| `export_revenue` | `grid_export × sell_price` | Revenue from selling (negative cost) | L1755 |
| `switching_penalty` | `max(flat_knob, per_kWh_floor × rate_kw × slot_hours)` when action ≠ current | Stability, hysteresis against flip-flopping | `cost.py:52-64` |
| `uncertainty_penalty` | Scales with horizon gap | Risk aversion when forecast is short | L1765-1774 |
| `self_consumption_value` | `battery_for_load × slot.buy_price` (`self_consumption` mode only) | **Diagnostic only, not part of `net_cost`.** The retail import the battery avoided by covering load. That saving is already in a lower `import_cost`, so it is reported but never subtracted | `engine/cost.py` (`stage_cost`) |
| `solar_opportunity_penalty` | `grid_import × $0.03/kWh` when future solar available | Discourage grid charging when solar can charge for free | L1071-1160 |

### The Net Cost Formula

```python
net_cost = (
    import_cost
    - export_revenue
    + shortfall_penalty
    + uncertainty_penalty
    + switching_penalty
    + solar_opportunity_penalty
    + futile_cycling_penalty
)
```

This is `ObjectiveTerms.net_cost` in `engine/types.py`. Note: `self_consumption_value` is **not** subtracted. The import the battery avoids is already captured by a lower `import_cost`; subtracting the credit as well double-counted it and drove the #406 / #800 overnight sawtooth. The term is still computed and serialized for diagnostics.

**Anti-cycling:** Protection against wasteful cycling is handled by the `futile_cycling_penalty` term, which penalises grid charging when forward simulation shows the charged energy will drain through household load before reaching a useful period (solar surplus or demand window). The `switching_penalty` provides additional stability by discouraging frequent mode changes. Per Issue #919, the effective switching penalty is now the max of the flat knob `config.switching_penalty` and a slot-energy-scaled floor `config.switching_penalty_per_kwh × max(charge_rate_kw, discharge_rate_kw) × slot_hours`. The floor makes the hurdle price-scale-aware: a flat $0.08 knob is trivially paid through by Amber's 5-min price jitter (which produced 105 mode changes / 7 days in live data, median dwell 10 min), but a $0.40/kWh floor equals $0.50 per 15-min slot at 5 kW and suppresses the marginal SC↔X flips while preserving genuine spike capture and demand-window pre-charge (which carry dollar-scale value). There is no per-kWh cycle penalty; the former `cycle_penalty` term was removed in issue #804 because it indiscriminately penalised all charge/discharge energy regardless of whether cycling was economically beneficial.

**Minimum cycle saving (hard gate):** Separately from the soft penalties above, `_compute_best_action` drops a grid-charge action entirely when it beats the HOLD alternative by a positive but sub-threshold margin — specifically when `0 < (hold_total_cost - charge_total_cost) < config.min_cycle_saving × charge_kwh`. This rules out "micro" arbitrage (cycling the battery for a few c/kWh) while preserving genuine pre-charge and price-spike capture: because the margin is the DP's real cost difference, it already credits the demand-window target, evening-peak avoidance, and backup value via `future_cost`, and unlike a soft penalty it cannot be "paid through". Configured by `number.localshift_min_cycle_saving` (default $0.25/kWh; `0` disables).

### When to Add Soft Penalties

Add to `stage_cost()` when:
- **Economic trade-offs**: Cost vs. benefit analysis
- **Behavioral bias**: Encourage/discourage certain patterns
- **Risk management**: Account for uncertainty or risk
- **Multi-objective optimization**: Balance competing goals

### Example: Adding a New Soft Penalty

```python
# Scenario: Penalize grid charging during peak demand hours (environmental concern)
@staticmethod
def stage_cost(...) -> ObjectiveTerms:
    # ... existing terms ...
    
    # NEW: Peak demand penalty
    peak_demand_penalty = 0.0
    if action in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST):
        slot_hour = datetime.fromisoformat(slot.timestamp_iso).hour
        is_peak_hour = slot_hour in range(17, 21)  # 5 PM - 9 PM
        if is_peak_hour:
            peak_demand_penalty = grid_import_kwh * 0.02  # $0.02/kWh penalty
    
    return ObjectiveTerms(
        # ... existing fields ...
        peak_demand_penalty=peak_demand_penalty,
        net_cost=net_cost + peak_demand_penalty,
    )
```

**Note**: You'll also need to update `ObjectiveTerms` dataclass to include the new field.

## Terminal Cost: `terminal_cost()`

Encodes requirements and goal states that must be achieved by a specific point.

### Current Terminal Costs

| Requirement | Formula | Applied At | Code Location |
|-------------|---------|------------|---------------|
| Demand window target | `max(0, target - SOC) × $0.03/%` | DW entry slot | L1818-1829 |
| Residual energy salvage (Issue #811) | `usable_kwh × min(cheapest_buy × 0.5, $0.05/kWh)` as a *credit* | Horizon boundary (`dp[n_slots]`) | `cost.terminal_salvage_value` |

The salvage credit prices residual battery energy at the horizon end at a bounded
fraction of the cheapest observed buy price, instead of zero. Zero-pricing made the
planner willing to dump value near the horizon cutoff — the rolling horizon keeps
advancing, so that energy genuinely displaces a future import. The bounds (half the
cheapest buy, absolute $0.05/kWh cap, floor energy excluded) keep it subordinate:
charging purely to harvest the credit always loses at least half the outlay, so it
cannot regress into overnight reserve-holding. It never touches the strict-mode
DW-entry penalty rows. Disable via `OptimizerConfig.terminal_salvage_enabled=False`.

### One window per solve, and the tail for the next one (Issue #1033)

A solve handles one demand window: the first entry slot in the horizon. The
funding water level, the hard floor, the urgency window and the boost unlock are
all computed from that one entry index and the SOC at the start of the plan.

The price feed is at most 24 hours long (in the October 2026 captures, 48
half-hours from the current one). In the afternoon the horizon therefore ends
at or just short of tomorrow's window: before 15:00 the one window in view is
today's, and from 15:00 until tomorrow's entry slot enters the feed there is
none (the window in progress at slot 0 is not a target). That was 15:30 in the
October captures and by 15:07 in the 2026-09-07 report. Nothing asked for charge before tomorrow's window, so the published
tail drained to the SOC floor until the entry slot entered the feed. A longer
horizon does not help, because there are no prices past the feed, and before
15:00 it would not help even with them: the first entry is still today's.

`DPPlanner._plan_next_window_tail` plans that tail a second time. The slots
after the window the main solve handled become their own solve, starting from
the SOC the main solve left and ending on a zero-energy entry slot that stands
in for the window. It is the same `_solve` that plans those slots once the real
entry slot is in the feed, with the same gates.

| Piece | Rule |
|---|---|
| When | `SlotBuilder` reports `next_demand_window_gap_min`, the minutes from the end of the last slot to the next window start. The tail is planned when that gap is short enough that the battery could not refill in it |
| Target at the horizon end | The demand-window target less what normal-rate grid charging can add in the gap, CV taper included (`_soc_required_before_gap`). Equal to the target when the feed ends as the window opens; at the SOC floor after about five hours, where the tail is left alone. Solar in the gap is not credited |
| What stays fixed | Every decision up to the end of the window the main solve handled, and every summary field about that window. The tail solve works on a copy of the config, so `terminal_penalty_idx`, the hard floor and the runway telemetry still describe the window the backstops act on |
| Published | `next_window_tail_start_idx`, `next_window_required_soc_pct` and `next_window_entry_soc_pct` on the optimizer summary, all null when the tail was left alone; `next_window_tail_status` (always set: `applied`, or the reason it was not) and `next_window_gap_min`. See `docs/ENTITY_REFERENCE.md` for the status values |
| Not applied | Under `allow_dw_entry_under_target` (its target already sits on the horizon boundary); when a later window is already inside the horizon; when the required SOC is at or below the SOC floor or the away reserve; when the tail would start at slot 0, or less than 30 minutes after slot 0 starts; when `OptimizerConfig.next_window_tail_enabled` is False |

The tail cannot change an executed command. It starts after a demand window the
main solve handled or was already inside, so it starts at index 1 or later and
slot 0 is always the main solve's. Index 1 is not enough on its own: the
executor picks the slot to act on by wall clock when it applies the plan, so a
plan built at 20:55 with the tail at 21:00 could have a tail decision executed
if the clock crossed 21:00 during the cycle. The tail is therefore refused
(`tail_too_close`) unless its first slot starts at least 30 minutes after slot
0 starts (`NEXT_WINDOW_TAIL_MIN_LEAD_MIN`). Slot 0 is at most five minutes
wide, so that is at least 25 minutes of clock. If the main solve has no window and the plan
does not start inside one (a truncated or stale feed that ends short of the
next window), the tail would be the whole plan; that is refused, the main plan
stands, and the status reads `whole_plan_skipped`. The backstops read
`terminal_penalty_idx`, the hard floor and the runway slack off the main
solve's config, which the tail never writes, and count pre-charge slots only
before today's entry slot.

Known limits. There is no switch entity; `next_window_tail_enabled` is a code
default. The published tail is a lower bound: solar in the gap is not
credited, and the required SOC steps up as the gap shrinks from one feed
update to the next. The tail only arises because the feed stops at 24 hours;
a longer feed would put tomorrow's entry slot in view and the status would
read `no_tail_slots` or `no_gap`.

### Why Terminal Cost Works

The terminal cost creates a "backwards incentive" through DP:

```
At DW entry (slot T):
  penalty = shortfall × rate

At slot T-1:
  DP considers: "If I don't charge now, I'll pay penalty at T"
  Result: Charge if penalty > cost of charging

At slot T-2:
  DP considers: "If I wait, I might miss cheap prices before T"
  Result: Charge early when prices are cheap
```

This single penalty creates **all** the pre-charging behavior without explicit heuristics.

### When to Add Terminal Costs

Add to `terminal_cost()` when:
- **Hard requirements**: Must achieve a specific state by deadline
- **Target tracking**: SOC, energy reserves, capacity margins
- **Constraint satisfaction**: Cumulative constraints over horizon

### Example: Adding a New Terminal Cost

```python
# Scenario: Minimum SOC required for overnight backup
@staticmethod
def terminal_cost(
    final_soc_pct: float, 
    target_soc_pct: float,
    config: OptimizerConfig
) -> float:
    # Existing: demand window shortfall
    dw_shortfall = max(0.0, target_soc_pct - final_soc_pct)
    dw_penalty = dw_shortfall * config.target_shortfall_penalty_per_pct
    
    # NEW: overnight backup requirement
    backup_shortfall = max(0.0, config.overnight_min_soc_pct - final_soc_pct)
    backup_penalty = backup_shortfall * config.overnight_penalty_per_pct
    
    return dw_penalty + backup_penalty
```

**Note**: You'll need to determine where this penalty should be applied (which slot index) in `_determine_terminal_penalty_idx()`.

## Decision Flow

```
1. Backward Induction (DP)
   For each slot t from T → 0:
     For each SOC bin:
       actions = feasible_actions(soc, slot, config)  # Hard constraints
       For each action:
         next_soc = transition(soc, action, slot, config)
         cost = stage_cost(action, ...) + dp[t+1][next_bin]
       dp[t][bin] = min(cost over actions)

2. Terminal Cost
   At DW entry slot:
     dp[T][bin] = terminal_cost(soc, target, config)

3. Forward Reconstruction
   For each slot t from 0 → T:
     action = dp[t][current_bin].action  # Already optimal
     Apply action, update SOC
```

## Extending the Model: Decision Guide

When adding a new feature, ask:

| Question | Yes → | No → |
|----------|-------|------|
| Is it physically impossible? | Hard constraint | Continue |
| Is it unsafe/forbidden? | Hard constraint | Continue |
| Is it economically discouraged? | Soft penalty | Continue |
| Is it a behavioral bias? | Soft penalty | Continue |
| Is it a requirement by deadline? | Terminal cost | Continue |
| Is it an optional goal? | Soft penalty | Continue |

### Decision Tree

```
New Feature
    │
    ├─► Impossible/Forbidden?
    │       └─► Add to feasible_actions()
    │
    ├─► Requirement by deadline?
    │       └─► Add to terminal_cost()
    │
    └─► Otherwise
            └─► Add penalty term to stage_cost()
```

## Configuration: Tuning Parameters

All penalty rates are configurable via `OptimizerConfig`:

```python
@dataclass
class OptimizerConfig:
    # Penalty rates
    target_shortfall_penalty_per_pct: float = 0.030  # $/%-point
    switching_penalty: float = 0.02                  # $ per mode switch (flat knob)
    switching_penalty_per_kwh: float = 0.0           # $/kWh floor: scales hurdle to slot energy (0 disables; production default 0.40)
    self_consumption_value_per_kwh: float = 0.25     # $/kWh
    export_price_margin: float = 0.02                # $/kWh above self-consumption
```

Tuning guidelines:
- **Increase penalty** → Stronger discouragement (e.g., higher switching penalty = fewer mode changes)
- **Decrease penalty** → Weaker discouragement (e.g., lower export margin = more exports)
- **Set to zero** → Disable penalty (use caution, may lead to undesired behavior)
- **`switching_penalty` (flat knob)** — existing hard dollar hurdle; a per-switch cost the DP pays when slot-0 action differs from the commanded mode. The UI knob `number.localshift_switching_penalty` lets operators set a floor in dollars (default $0.02).
- **`switching_penalty_per_kwh`** — per-kWh scale factor for the effective floor (`cost.py:52-64`). The effective penalty = `max(flat_knob, per_kwh × max(charge_rate, discharge_rate) × slot_hours)`. The per-kWh floor auto-scales the hurdle to slot duration so the Schmitt band is comparable across 5-min Amber slots (≈$0.17 at 5 kW, $0.50 at 15 min) and longer forecast slots. Default $0.40/kWh ≈ $0.50 per 15-min slot at 5 kW, which blocks the marginal SC↔X flips that caused the #919 thrash while preserving spike/DW value. Set to 0.0 to revert to legacy flat-knob-only behaviour.

## Testing New Features

When adding a constraint or penalty:

1. **Unit tests**: Test `feasible_actions()` or `stage_cost()` in isolation
   ```python
   def test_quiet_hours_constraint():
       slot = SlotContext(timestamp_iso="2024-01-01T23:00:00", ...)
       actions = DPPlanner.feasible_actions(soc=50, slot=slot, config=config)
       assert PlannerAction.EXPORT_PROACTIVE not in actions
   ```

2. **Scenario tests**: Test end-to-end behavior in `tests/test_scenarios_dp.py`
   ```python
   def test_peak_demand_penalty_discourages_charging():
       # Peak hour slot with cheap price
       # Verify optimizer still prefers waiting over charging
   ```

3. **Comparison tests**: Compare with baseline (no penalty) to validate effect
   ```python
   # Run with penalty enabled vs disabled
   # Verify behavior change is as expected
   ```

## Control Philosophy

These rules define the intended operating philosophy for `self_consumption` mode.
They are **not** just preferences — they are part of the system's identity.
Future changes should not violate them without explicit discussion.

### Principles

1. **Economics over comfort.** The goal is to minimize electricity cost, not to keep the battery full.
2. **Grid import is acceptable overnight.** If the battery runs down and the house uses the grid, that is often the correct outcome. Overnight charging is generally wasteful and should stay penalized.
3. **Low overnight SOC is not a bug.** The battery spending hours at 10% SOC is acceptable if that is the cheapest path. Do not interpret this as a problem that needs fixing.
4. **Proactive charging needs strong justification.** Charging before a deadline is justified only when target-feasibility says so, not just to avoid low SOC or to feel "ready."
5. **Anti-goal: do not optimize for overnight reserve.** In `self_consumption`, there is no implicit objective to "hold reserve overnight at all costs." If you find yourself adding reserve-holding behavior, stop and check whether it was requested.

### Anti-Goals

These are behaviors that should **not** appear in `self_consumption` unless explicitly requested:

- Holding a meaningful reserve overnight (e.g., 25–35%) without explicit reason
- Charging from the grid to avoid falling below minimum SOC
- Reducing anti-cycling penalties to enable more overnight charging
- Interpreting low overnight SOC as a reason to change the planning model

### Guardrail Questions

Before proposing changes to optimizer behavior, ask:

- "Is this adding reserve-holding behavior?" If yes, it needs explicit approval.
- "Am I treating low overnight SOC as a bug?" If yes, reconsider the framing.
- "Does this change make proactive charging easier?" If yes, make sure it is gated by a real deadline, not just comfort.

### Exception: the away discharge floor

The away discharge floor (`OptimizerConfig.away_reserve_floor_pct` /
`discharge_floor_pct`, docs/holiday-away/plan.md item 4) is a deliberate,
Jack-requested exception to "holding a meaningful reserve without explicit
reason" above (24 Sep 2026) — it is exactly the reserve-holding behavior the
anti-goals list warns against, made explicit rather than accidental. Two
things keep it from eroding the philosophy elsewhere:

- **It is scoped to "away is active".** It is `None` — no floor at all — the
  rest of the time, so everyday self-consumption is untouched.
- **It is still only a hard constraint, never a soft charge target.** It
  removes discharge actions from `feasible_actions()` and clamps how far
  HOLD/EXPORT may drain; it never adds a reason to grid-charge. The planner
  does not plan a top-up for an away trip that starts below the floor — see
  `optimizer_runner.py`'s starting-SOC clamp, which stays at `min_soc_pct`,
  never at the away floor. The hardware is a different matter: the backup
  reserve is written at the away value even when SOC is below it, and the
  Powerwall is kept from grid-charging up to it only by
  `grid_charging_allowed=False`, which Tesla's cloud periodically resets
  (#394). So a trip that starts below the away reserve may be topped up
  from the grid by the Powerwall itself (a few kWh at most). That is
  accepted: the reserve exists for outage cover (#1092).

If a future change is tempted to give the away floor a soft-penalty
counterpart (e.g. to *encourage* charging up to it), that is new
reserve-holding behavior beyond what was requested here and needs the same
explicit approval as any other addition to the anti-goals list.

## References

- `optimizer_dp.py` - Core DP implementation
- `ARCHITECTURE.md` - System architecture overview
- `FORECAST_DRIVEN_CONTROL.md` - Design principles
- `OPTIMIZER_DP_ROLLOUT.md` - Rollout history

## Academic Background

This pattern is known in the literature as:

- **Penalty Methods in Dynamic Programming** - Soft constraints via cost function
- **Discrete-Time Optimal Control** - Sequential decision-making with constraints
- **Economic Model Predictive Control (EMPC)** - Cost-optimized control for energy systems

Key papers:
- Bertsekas, D. P. "Dynamic Programming and Optimal Control" - Chapter on constrained DP
- Rawlings, J. B. & Mayne, D. Q. "Model Predictive Control: Theory and Design" - EMPC foundations

"""engine/types.py — Optimizer type definitions.

Extracted from optimizer_dp.py (issue #641 refactor).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

# const.py is dependency-free (stdlib only) and imports nothing from the engine, so this
# cannot cycle. Imported so the runway-margin dataclass default and the live number
# entity's default are literally the same object rather than two literals that drift.
from ..const import DEFAULT_PRECHARGE_RUNWAY_MARGIN_MIN

if TYPE_CHECKING:
    from custom_components.localshift.forecast.solar_accuracy import (
        SolarAccuracyTracker,
    )

# -----------------------------------------------------------------------------
# Action vocabulary
# -----------------------------------------------------------------------------


class PlannerAction(StrEnum):
    """Discrete control actions the optimizer may assign to a forecast slot."""

    HOLD = "hold"
    """Battery operates in self-consumption mode; no active grid import/export."""

    CHARGE_GRID_NORMAL = "charge_grid_normal"
    """Charge battery from grid at normal rate (buy_price < threshold)."""

    CHARGE_GRID_BOOST = "charge_grid_boost"
    """Charge battery from grid at maximum rate (emergency / strong opportunity)."""

    EXPORT_PROACTIVE = "export_proactive"
    """Discharge battery to grid during high sell-price window."""

    HOLD_STRICT = "hold_strict"
    """Strict SOC hold: preserve SOC by meeting all load deficit from grid (zero discharge).

    Issue #906: unlike ordinary HOLD which discharges to meet load, HOLD_STRICT
    forbids battery discharge entirely — the deficit is imported from the grid.
    Used to save SOC for a dearer period (e.g. morning price peak) when overnight
    prices are flat/inverted and the round-trip loss of discharging+recharging is
    wasteful. Gated by min_hold_saving threshold so it only fires when the saving
    exceeds a configured dollar-per-kWh bar.
    """


# -----------------------------------------------------------------------------
# Reason codes
# -----------------------------------------------------------------------------


class PlannerReasonCode(StrEnum):
    """Classification codes for optimizer slot decisions (used in diagnostics/debugging)."""

    TARGET_SHORTFALL_RISK = "TARGET_SHORTFALL_RISK"
    """Grid charge needed because solar cannot meet demand-window SOC target."""

    CHEAP_IMPORT_WINDOW = "CHEAP_IMPORT_WINDOW"
    """Grid charge justified by low import price relative to shadow cost of storage."""

    SOLAR_SURPLUS_CAPTURE = "SOLAR_SURPLUS_CAPTURE"
    """Holding to absorb forecast solar surplus; grid charge unnecessary."""

    NEGATIVE_FIT_AVOIDANCE = "NEGATIVE_FIT_AVOIDANCE"
    """Export avoided because sell price is negative or below profitability floor."""

    HIGH_SELL_PRICE_EXPORT = "HIGH_SELL_PRICE_EXPORT"
    """Proactive export justified by high sell price; headroom created for solar."""

    SOC_FLOOR_CONSTRAINT = "SOC_FLOOR_CONSTRAINT"
    """Action constrained by minimum SOC safety floor."""

    SOC_CEILING_CONSTRAINT = "SOC_CEILING_CONSTRAINT"
    """Action constrained by maximum SOC (battery full)."""

    DEMAND_WINDOW_CONSTRAINT = "DEMAND_WINDOW_CONSTRAINT"
    """Action constrained by demand window entry requirements."""

    IDLE = "IDLE"
    """No economic or constraint reason to act; holding in self-consumption."""

    SOLAR_OPPORTUNITY_WAIT = "SOLAR_OPPORTUNITY_WAIT"
    """Holding instead of grid charging because solar will be available soon."""

    UNCLASSIFIED = "UNCLASSIFIED"
    """Reason not yet classified (should not appear in stable production)."""


# -----------------------------------------------------------------------------
# Per-slot context (normalized inputs)
# -----------------------------------------------------------------------------


@dataclass
class SlotContext:
    """
    Normalized representation of a single forecast slot.

    All energy quantities are in kWh; power in kW; prices in $/kWh.
    Slot interval is in minutes and must be explicit (no assumption of 30-min slots).
    """

    slot_index: int
    """0-based index within the planning horizon."""

    timestamp_iso: str
    """ISO 8601 UTC timestamp for this slot (used for alignment / audit)."""

    slot_interval_minutes: int
    """Duration of this slot in minutes (typically 5 or 30)."""

    buy_price: float
    """Import price in $/kWh for this slot."""

    sell_price: float
    """Export (FIT) price in $/kWh. May be negative."""

    solar_kwh: float
    """Forecast solar generation for this slot in kWh."""

    consumption_kwh: float
    """Forecast household consumption for this slot in kWh."""

    is_demand_window_entry: bool = False
    """True if this slot is the demand-window entry boundary."""

    is_demand_window_slot: bool = False
    """True if this slot falls within the demand window."""

    price_source: str = "unknown"
    """Source of price data (e.g. '5min', '30min', 'synthetic')."""


# -----------------------------------------------------------------------------
# Optimizer configuration
# -----------------------------------------------------------------------------


@dataclass
class OptimizerConfig:
    """
    Tunable parameters controlling optimizer constraints and objective weights.

    All default values are conservative starting points; tune via comparison analytics.
    """

    # --- Battery hardware constraints ---
    battery_capacity_kwh: float = 13.5
    """Usable battery capacity in kWh."""

    charge_rate_kw: float = 3.3
    """Maximum battery charge rate from grid in kW (matches CHARGE_RATE_GRID_KW)."""

    boost_charge_rate_kw: float = 5.0
    """Maximum battery charge rate in boost mode in kW."""

    solar_charge_rate_kw: float = 5.0
    """Maximum solar-to-battery charge rate in kW (Powerwall 3 inverter limit)."""

    discharge_rate_kw: float = 5.0
    """Maximum battery discharge rate in kW."""

    charge_efficiency: float = 0.92
    """Charging efficiency (energy lost going into battery)."""

    discharge_efficiency: float = 0.95
    """Round-trip discharging efficiency (0–1)."""

    min_soc_pct: float = 10.0
    """Minimum allowed SOC (%)."""

    max_soc_pct: float = 100.0
    """Maximum allowed SOC (%)."""

    # --- Demand window target ---
    demand_window_target_soc_pct: float = 80.0
    """Required SOC (%) at demand window entry."""

    allow_dw_entry_under_target: bool = False
    """If True, allow reaching target during DW via solar (instead of by DW start)."""

    stale_solar_conservative: bool = True
    """If True, cap confidence when Solcast data is stale or absent."""

    stale_solar_confidence_ceiling: float = 0.3
    """Confidence ceiling applied when stale_solar_conservative and data is stale."""

    solar_forecast_accuracy: float = 1.0
    """Forecast accuracy (0-1) used to discount projected solar in the pre-charge
    feasibility gate (``check_global_solar_sufficiency``).

    Set per-plan from ``get_forecast_accuracy(inputs.solar_accuracy_tracker)`` so the
    hard gate is no more optimistic than the shortfall cost model
    (``reason_codes._is_target_shortfall_risk``), which already discounts projected
    solar by the same accuracy. Default 1.0 (full trust) preserves legacy behavior
    for unit tests and standalone use. See the 2026-06-09 DW undercharge / #816."""

    # --- Objective weights ---
    target_shortfall_penalty_per_pct: float = 0.030
    """Penalty applied per % SOC below target at demand-window entry ($/%-point).

    This should be calibrated to the actual cost of importing 1% SOC from the grid
    at the cheapest available price, with a small safety multiplier:

        penalty = effective_cheap_price ($/kWh) * battery_capacity_kwh / 100 * safety_factor

    Example: 0.15 $/kWh * 13.5 kWh / 100 * 1.5 = $0.030 per %-point

    Do NOT use the original default of 1.0 — it is ~53x the actual remediation cost
    and causes the optimizer to grid-charge compulsively. See issue #438.

    In production, this value is computed in optimizer_shadow_runner._build_optimizer_config()
    from the live tariff data; the dataclass default here is a reasonable fallback
    for unit tests and standalone use.
    """

    terminal_salvage_enabled: bool = True
    """If True, credit a bounded salvage value for residual energy at the horizon
    boundary (Issue #811).

    Residual energy above the floor displaces a post-horizon grid import, so a
    zero boundary price made the planner too willing to dump value near the end
    of the modeled horizon. The credit is bounded — at most half the cheapest
    observed buy price, capped absolutely at ``TERMINAL_SALVAGE_MAX_PER_KWH`` —
    so charging purely to harvest it always loses money and the planner cannot
    regress into overnight reserve-holding. The strict-mode demand-window entry
    penalty is unaffected; the credit lives only on the horizon boundary row.
    """

    # --- SOC discretization ---
    soc_bins: int = 100
    """Number of SOC bins for DP state space (higher = more precise, slower).
    Raised 50 -> 100 on 2026-08-25. At 50 bins each bin spans 1.84 SOC points (~0.25 kWh)
    while a single 5-minute boost slot moves ~2.84 points — 1.5 bins. The value function
    could not resolve "charge now" from "charge in 15 minutes", so the DP deferred the
    head of a demand-window pre-charge; and because backward induction transitions from
    BIN CENTRES while ``_forward_reconstruct`` transitions from the CONTINUOUS soc, the
    residual accumulated downward across the pre-charge run (measured: the realised
    trajectory left the DP's own assumed successor bin at 7 of the first 16 steps, always
    downward through the charging region, ~9 SOC points in total).

    The result was a plan that entered the demand window under target while its own
    terminal table priced that outcome at $677 — the DP was not paying through a penalty,
    it could not see the state that avoided it. Live 2026-08-25: 91.41% against a 95%
    target, first charge deferred to 12:45; at 100 bins the same horizon starts charging
    at 12:30 and reaches 95.56% with zero shortfall, for $0.09 more.

    100 is the knee, not a guess: 100/200/400/900 bins all converge on the same plan
    (95.1-95.6%, shortfall 0, first charge at slot 0) and only the solve time changes
    (0.071s / 0.201s / 0.667s / 3.061s against 0.029s at 50). 100 buys the whole
    correction for ~40ms on a cycle that runs every 5 minutes.

    See scratchpad/probe_bins.py for the sweep and scratchpad/probe_recon_drift.py for the
    bin-drift measurement.
    """

    # --- Optimization mode (Issue #406) ---
    optimization_mode: str = "self_consumption"
    """Optimization strategy: 'self_consumption' (default) or 'arbitrage'."""

    self_consumption_value_per_kwh: float = 0.15
    """Value of using battery energy for household load ($/kWh). Auto-derived from average buy price.

    DEPRECATED (Issue #610): No longer used in optimizer hot path. Replaced by slot.buy_price
    for slot-specific credit calculation. Kept for backward compatibility with optimizer_runner.py.
    """

    effective_cheap_price: float = 0.10
    """Price threshold for grid charging in self-consumption mode ($/kWh)."""

    terminal_penalty_idx: int | None = None
    """Solver-derived (set by ``DPPlanner._solve``): index of the demand-window ENTRY slot,
    i.e. the slot the terminal shortfall penalty is applied at. None ⇒ no demand window in
    the rolling horizon.

    Published alongside ``urgency_window_start_idx`` (which is meaningless without it) so
    consumers outside the solver can express "before the demand window" without
    re-deriving the window bounds. The pre-charge execution backstop
    (``OptimizerFacade._backstop_urgency_window``) is the first such consumer: before this
    field existed it read ``getattr(config, "terminal_penalty_idx", None)``, which was
    always None, and the backstop was unreachable in production."""

    urgency_window_start_idx: int | None = None
    """Solver-derived (set by ``DPPlanner._solve``): index of the first slot within the
    urgency window before the demand-window entry (Issue #800 follow-up). The window width is
    deficit-derived (floor 4h, cap 8h) via ``dp_math.urgency_window_hours`` — a deep SOC
    deficit needs more pre-charge runway than a fixed 4h allows (2026-06-11 incident).

    The urgency-inflated ``effective_cheap_price`` is a "now" value that is only legitimate
    for slots close to the upcoming demand window (its urgency ramp tracks the same window).
    When the plan
    recomputes after the day's demand window has begun, the first DW-entry in the rolling
    horizon is *tomorrow* evening, so tonight's overnight slots are technically "pre-DW" —
    but they are far outside the urgency window, so gating them on the inflated price
    re-introduces the overnight sawtooth. ``cheap_threshold_for_slot`` therefore applies the
    inflated price only to slots in ``[urgency_window_start_idx, terminal_penalty_idx)`` and
    gates everything else on the un-inflated base. None ⇒ legacy (base only post-DW)."""

    max_normal_gain_pct_to_terminal: list[float] | None = None
    """Solver-derived (set by ``DPPlanner._solve``): per-slot upper bound on the SOC
    %-points normal-rate grid charging could add from that slot to the demand-window entry
    (2026-06-11 shortfall incident).

    Computed by ``constraints.compute_max_normal_gain_pct_to_terminal``. The shortfall-aware
    boost gate in ``feasible_actions`` compares ``soc_pct + gain[slot_idx]`` against the
    demand-window target: when even this optimistic normal-rate ceiling falls short, boost is
    unlocked so the DP keeps a feasible path to target rather than eating the terminal
    shortfall penalty. None ⇒ legacy behavior (boost only at very-cheap prices) for existing
    tests and direct callers."""

    max_precharge_price: float | None = None
    """Operator price ceiling for target-driven pre-charge ($/kWh), from
    ``CONF_MAX_PRECHARGE_PRICE`` (default 0.20).

    Target-first eligibility (2026-06-12): reaching the demand-window target is a
    constraint the operator has authorized paying up to this price for — the same ceiling
    the live urgency ramp in ``price_calculator`` already uses. Plumbed into the engine so
    ``compute_pre_dw_charge_thresholds`` can size the pre-DW charge gate to the target
    instead of leaving the target structurally unreachable on days with few cheap slots.
    None ⇒ feature inert (legacy gate behaviour) for unit tests and direct callers."""

    pre_dw_charge_thresholds: list[float] | None = None
    """Solver-derived (set by ``DPPlanner._solve``): per-slot charge-price threshold for
    slots before the demand-window entry (target-first eligibility, 2026-06-12).

    Computed by ``constraints.compute_pre_dw_charge_thresholds`` as the max of (a) the
    legacy cheap threshold, (b) the urgency ramp evaluated at that slot's own time
    (time-consistent — a morning plan sees the same unlocks afternoon re-plans will), and
    (c) the "water level": the marginal price of the cheapest set of pre-DW slots whose
    combined charge capacity closes the SOC deficit to target, clamped to
    ``max_precharge_price``. ``cheap_threshold_for_slot`` returns these for pre-DW slots
    when present; slots at/after the DW entry are unaffected (still gated on
    ``base_cheap_price`` — the #800 overnight-sawtooth protection is untouched).
    None ⇒ legacy behaviour."""

    spike_precharge_enabled: bool = True
    """Operator kill switch for spike-event pre-charge funding.

    False ⇒ ``DPPlanner.plan`` never computes funding slots and the planner behaves
    exactly as it did before the feature existed."""

    spike_funding_slots: frozenset[int] | None = None
    """Solver-derived (set by ``DPPlanner.plan``): slots whose grid charge is admitted to
    the feasible set so it can fund a later expensive interval outside the demand window.

    Computed by ``spike_event.find_funding_slots``. Without this, a forecast price spike
    has no funder at all: ``cheap_threshold_for_slot`` only ever widens for the
    demand-window target, so an overnight trough two cents above ``base_cheap_price``
    blocks arbitrage worth dollars per kWh (2026-08-05: a $1.65 print met at the 10% floor).

    Consumed in exactly one place: ``constraints.feasible_actions``, which admits
    CHARGE_GRID_NORMAL for these slots. It widens the CHOICE SET only — every other
    screen still applies, in particular the min-cycle-saving gate. #908 also exempted
    these slots from that gate; that disarmed the only hard anti-cycling protection
    wherever qualification fired and reopened the #800 sawtooth, so the exemption is
    gone (see ``core._is_urgency_precharge``).

    Deliberately NOT routed through ``cheap_threshold_for_slot``: that function also feeds
    the futile-cycling penalty and the floor-routing helpers, so changing its return value
    would mutate the objective function rather than only the choice set.

    None ⇒ feature inert (legacy behaviour)."""

    hard_target_floor: float | None = None
    """Solver-derived (set by ``DPPlanner._solve``): the hard DW-target feasibility floor
    (issue #885) — ``min(target, max feasible/eligible SOC at DW entry)``.

    When set (strict mode: ``allow_dw_entry_under_target=False``, self-consumption, a
    demand window exists, and solar alone does not reach target), two things happen:

    - ``_initialize_dp_tables`` prunes DW-entry states below this floor with an effectively
      infinite penalty, so backward induction MUST route a charging path that clears it
      (through the cheapest eligible pre-DW slots), rather than paying through the soft
      shortfall penalty and holding under target, and
    - ``feasible_actions`` unlocks boost charging in eligible pre-DW slots while
      ``soc_pct < hard_target_floor``, so the DP actually HAS a fast-enough path to the
      floor when normal-rate charging would arrive at the DW under target (the live
      "boost downshifted to grid" failure).

    Strictly bounded to pre-DW slots (``slot_idx < terminal_penalty_idx``); never forces
    charging inside/after the DW or overnight (guards the #800 sawtooth). None ⇒ gate
    dormant (legacy soft-penalty behaviour) for unit tests and direct callers."""

    hard_floor_suppressed_by_solar: bool = False
    """Solver-derived (set by ``DPPlanner._solve``): True when ``hard_target_floor`` is
    None *specifically because solar alone was projected to reach the target*, and the
    gate would otherwise have been live.

    ``hard_target_floor`` goes None for several structurally different reasons, and the
    pre-charge execution backstop (#901) — which is gated on that floor — must be able to
    tell them apart. This flag is True only when ALL of the following hold:

    - the strict-mode policy preconditions are met (``allow_dw_entry_under_target`` is
      False), AND
    - the solar-sufficiency branch fired: either ``can_solar_reach_target`` (solar reaches
      target anywhere during the DW) or ``check_global_solar_sufficiency`` (solar reaches
      target by the DW entry from the current SOC), AND
    - ``compute_max_feasible_terminal_soc`` is non-None, i.e. the gate is structurally
      applicable (self-consumption, a demand window exists, the entry is not slot 0).

    False for every OTHER None reason — ``allow_dw_entry_under_target``, no demand window,
    non-self-consumption, DW entry at slot 0 — so backstops keyed on this flag stay exactly
    as dormant as they are today in those cases.

    Motivation (2026-07-28): the #901 backstop returns None whenever ``hard_target_floor``
    is None, and the floor is deliberately suppressed on any day solar *looks* sufficient
    (don't fight #816/#849). A mid-afternoon cloud event then leaves no backstop at all.
    The 2026-07-27 incident also proved solar confidence unreliable (a blended "high" 0.87
    against a 52% Solcast day-confidence), so this flag is deliberately only an
    *eligibility* signal — it says "the only thing standing between us and a hard floor is
    a solar forecast" — and must be paired with ``precharge_runway_slack_min`` before it
    authorizes anything."""

    precharge_runway_slack_min: float | None = None
    """Solver-derived (set by ``DPPlanner._solve``): spare minutes of pre-charge runway —
    minutes from the plan's start to the demand-window entry, MINUS the minutes boost
    charging would need to close the SOC gap to target.

    Formally ``minutes_to_dw_entry - minutes_of_boost_needed``, where minutes-needed is
    integrated through ``transitions._tapered_stored_kwh`` — the SAME CV-taper charge model
    the DP's own transitions use — rather than a flat ``gap / (rate × efficiency)``. The
    taper is not a rounding error: above ``charge_taper_start_pct`` the Powerwall derates
    toward ``charge_taper_min_factor``, and every pre-charge to a 95% target crosses that
    knee. On the 2026-07-28 live case (59.1% → 95%) the flat form claimed 63 minutes where
    the engine's own model needs 78 — a 15-minute overstatement, larger than the whole
    default margin. Negative ⇒ the target is already physically unreachable at boost rate.

    Both terms are biased to UNDER-report slack, the only safe error direction for a
    guardrail: the time term is anchored at the END of slot 0 (the DP is clock-free, and
    slot 0 already contains ``now``, so its start would credit runway already spent).

    This — not solar confidence — is the quantity that matters for the "cloud event with no
    runway left" failure the #901 backstop was built for. Two honest caveats, both measured
    rather than assumed:

    - It is NOT self-limiting on a sunny day. ``gap_pp`` is the LIVE gap, which projected
      solar has not yet closed, so a low-SOC morning under a strong forecast can read a
      small slack and arm the guardrail even though solar alone would have reached target.
      That is the deliberate insurance premium of the design — the arm asks "if solar
      stopped RIGHT NOW, could grid still get there?" — and
      ``precharge_runway_margin_min`` (0 ⇒ off) is the operator's control over how much of
      that premium to pay.
    - While boost charging runs at the modelled rate it holds roughly constant (the gap
      closes at the rate the runway shortens), so the hold ends via the closing-gap gate,
      not via slack recovery. It is quantized to ``precharge_runway_quantum_min``.

    None when it is not interpretable: no demand window, the DW entry is slot 0, timestamps
    are unparseable, or degenerate battery capacity/rate/efficiency. A non-positive gap
    yields the full remaining runway (nothing to charge ⇒ all slack)."""

    precharge_runway_quantum_min: float = 0.0
    """Solver-derived (set by ``DPPlanner._solve``): the width of slot 0, in minutes.

    ``precharge_runway_slack_min``'s time term can only step at slot boundaries — the DP is
    clock-free, so there is no continuous ``now`` to measure from — while its SOC-gap term
    moves continuously. The published slack therefore carries a sawtooth of exactly this
    amplitude: it drifts up across a slot and drops by one slot width at each boundary.

    Consumers must size any hysteresis band against this value rather than a hardcoded
    constant. A 10-minute band clears a 5-minute slot 0 but not a 30-minute one, and slot 0
    IS 30 minutes whenever the hybrid schedule has no 5-minute Amber data — precisely the
    degraded conditions in which the arm is most likely to be live."""

    precharge_runway_margin_min: float = DEFAULT_PRECHARGE_RUNWAY_MARGIN_MIN
    """Operator-tunable slack threshold (minutes) for the runway-gated pre-charge arm.

    Declared here so the engine carries the knob alongside the two solver-derived fields
    above; the arming logic that reads it lives outside the DP core — it is
    ``OptimizerFacade._runway_backstop_mode`` that compares it against
    ``precharge_runway_slack_min``. The arm fires when the slack falls below this margin —
    i.e. when losing this many more minutes of runway would make the target physically
    unreachable at boost rate. Non-positive ⇒ the arm is disabled outright (kill switch).

    Default tracks ``const.DEFAULT_PRECHARGE_RUNWAY_MARGIN_MIN`` (15.0) so the dataclass and
    the live ``number.localshift_precharge_runway_margin_min`` slider cannot drift apart.
    Calibration: the 2026-07-28 live observation (59.1% SOC, 95% target, 82 min to DW) sits
    at ~4 minutes of slack against the engine's own tapered charge model — well under the
    margin, so the arm fires, which is the whole point (that state was recovered by a MANUAL
    ``boost_charging`` override). An earlier flat-rate formula scored the same state at
    ~19 minutes and would have stayed shut. Raising the margin buys more insurance at the
    cost of more grid charging on days solar would have sufficed; 0 disables the arm. The DP
    itself never reads this value — it cannot change a plan."""

    pre_dw_funding_water_level: float | None = None
    """Solver-derived (set by ``DPPlanner._solve`` via ``compute_pre_dw_charge_thresholds``):
    the raw target-funding water level — the marginal buy price of the cheapest set of
    pre-DW slots whose combined boost capacity closes the SOC deficit to the demand-window
    target ($/kWh), clamped to ``max_precharge_price``. None when there is no deficit (or
    the thresholds are inert).

    Distinct from the (b)/(c) max in ``pre_dw_charge_thresholds``: this carries ONLY the
    funding component, un-floored by the ramp base, so the min-cycle-saving exemption can
    tell a genuinely target-funding charge (price ≤ water level, part of the
    cheapest-sufficient set) from a merely legacy-cheap one whose energy drains to the SOC
    floor before the demand window (the 2026-06-13 overnight sawtooth regression)."""

    base_cheap_price: float | None = None
    """Un-inflated "genuinely cheap" threshold (percentile-derived), $/kWh.

    ``effective_cheap_price`` is computed for *now* and may be inflated by today's
    low-solar urgency (``_calculate_urgency_adjusted_price``) so the optimizer will
    pay more to reach *today's* demand-window target. That urgency rationale does not
    hold for slots at/after the demand window (i.e. tomorrow), so applying the inflated
    threshold there wrongly classifies tomorrow-night slots as "cheap" and produces
    net-negative overnight sawtooth charging. Past the demand-window entry, grid
    charging is gated on this un-inflated base instead. Falls back to
    ``effective_cheap_price`` when ``None`` (backward compatibility)."""

    switching_penalty: float = 0.02
    """Penalty applied when switching away from the currently commanded action ($/switch).

    The *effective* penalty is the max of this flat knob and the slot-energy-scaled
    floor ``switching_penalty_per_kwh × max(charge_rate_kw, discharge_rate_kw) ×
    slot_hours``. The floor makes the hurdle price-scale-aware (per #919): a flat
    $0.08 knob is trivially paid through by Amber's 5-min price jitter, but a
    $0.40/kWh floor equals $0.50 per 15-min slot at 5 kW and suppresses the
    marginal SC↔X flips that caused 105 changes / 7 days in live data. Zero disables
    the floor and restores legacy flat-knob-only behaviour (dataclass default = 0.0
    so existing unit tests remain unaffected).
    """

    switching_penalty_per_kwh: float = 0.0
    """Scale factor for the slot-energy-scaled floor on mode-switch penalty ($/kWh).

    Effective penalty = max(flat_knob, this × max(charge_rate_kw, discharge_rate_kw)
    × slot_hours). Product of the slot's kW rating × duration in hours is the
    energy at stake in any mode switch, so the resulting $/switch hurdle is
    comparable across slot granularities (5-min Amber, 15-min fixed). Default
    0.40 → $0.50 per 15-min slot at 5 kW, sufficient to suppress sub-threshold
    SC↔X churn while preserving spike / DW pre-charge value. 0.0 disables.
    """

    export_price_margin: float = 0.02
    """Minimum profit margin for proactive export above self-consumption value ($/kWh)."""

    min_cycle_saving: float = 0.0
    """Minimum saving over holding ($/kWh charged) required to justify cycling the
    battery via a grid charge. 0.0 disables the gate (legacy behaviour).

    Applied in ``core._compute_best_action``: a grid charge is dropped when it beats the
    HOLD alternative by a positive but sub-threshold margin. Because the margin is the
    DP's real cost difference (``hold_total_cost - charge_total_cost``), it already
    credits every value source — evening-peak avoidance, demand-window target, backup
    readiness — so genuine pre-charge and spike capture are preserved while thin
    speculative arbitrage is dropped. Dataclass default is 0.0 so unit tests are
    unaffected; production sets it from ``CONF_MIN_CYCLE_SAVING`` (default 0.25) in
    ``optimizer_runner``."""

    min_hold_saving: float = 0.0
    """Minimum saving over ordinary HOLD ($/kWh held) required to select HOLD_STRICT.

    Issue #906: HOLD_STRICT preserves SOC by importing the entire load deficit from
    the grid instead of discharging the battery. It only fires when the saved
    round-trip loss (charged later at a dearer price) exceeds this threshold.
    0.0 disables the action entirely (legacy behaviour). Production default is 0.0
    as a kill switch for the first live night.
    """

    forecast_horizon_hours: float = 24.0
    """Actual hours of forecast available (Issue #431)."""

    hold_soc: bool = False
    """If True, force HOLD action to maintain current SOC (no discharge).

    Issue #559 Root Cause 3: when the system signal is HOLD, the optimizer's
    HOLD action should strictly preserve SOC by meeting all load from grid import,
    with zero battery discharge.  The original transition math allowed discharge
    because it was cheaper than importing at ~$0.21 (discharge cost = $0.05 cycle
    + ~$0.15 shadow value = $0.20).  This flag overrides that economic logic and
    treats HOLD as a hard constraint: "Do Not Discharge."
    """

    # --- Charge curve modeling ---
    charge_taper_start_pct: float = 90.0
    """SOC percentage above which charge rate begins tapering.

    A lithium battery (and the Powerwall inverter) holds near-constant power up to a
    "knee", then enters the constant-voltage phase where charge power falls toward zero
    as it approaches full. Below this SOC the configured charge rate is delivered in full;
    above it the rate is linearly derated toward ``charge_taper_min_factor`` at 100%.
    Modelling this stops the planner from believing it can add the last ~15-20% as fast as
    the bulk-charge region, which previously produced over-optimistic last-minute top-ups
    that fell short of target (the rate is lower than expected as the battery fills).

    Default raised from 80% to 90% (Issue #905): live measurement on 2026-07-29 showed
    the Powerwall held a flat 5.0 kW from 80% through 88% SOC with no derating
    whatsoever, while the old 80% knee was already derating to 3.3 kW by 88%. The 90%
    knee keeps the model accurate across the measured no-derate band; the portion above
    88% remains unvalidated (see ``charge_taper_min_factor``)."""

    charge_taper_min_factor: float = 0.2
    """Fraction of nominal charge rate still available at 100% SOC (end of the taper).

    The taper ramps the rate linearly from 1.0 at ``charge_taper_start_pct`` down to this
    floor at ``max_soc_pct``. Kept > 0 so the model never predicts an infinitely-slow
    final approach (which would make the target unreachable in finite slots).

    Unvalidated above ~88% SOC (Issue #905): the live measurement that drove the knee
    raise was supply-limited by solar surplus and never reached full-rate grid charge
    above 88%. Published reports suggest the floor is closer to 0.66-0.70x for some
    firmware versions; pinning the floor requires a deliberate full-rate overnight
    charge test."""

    # --- Anti-sawtooth protection ---
    min_soc_floor_buffer_pct: float = 1.0
    """Buffer above min_soc_pct where anti-sawtooth protection applies."""

    min_floor_charge_gain_pct: float = 2.0
    """Minimum SOC gain required to justify charging within floor buffer."""


# -----------------------------------------------------------------------------
# Per-slot decision output
# -----------------------------------------------------------------------------


@dataclass
class ObjectiveTerms:
    """Breakdown of objective cost for a single slot/action combination."""

    import_cost: float = 0.0
    """Cost of grid import in this slot (positive = cost)."""

    export_revenue: float = 0.0
    """Revenue from grid export in this slot (positive = revenue)."""

    shortfall_penalty: float = 0.0
    """Terminal penalty applied at demand window boundary (only for terminal slots)."""

    self_consumption_value: float = 0.0
    """Diagnostic only: value of battery energy used for household load, at retail
    (``battery_for_load * buy_price``). NOT subtracted from ``net_cost``.

    Issue #406 / #800 overnight sawtooth (root-caused 2026-06-29): subtracting this
    DOUBLE-COUNTED the self-consumption benefit. When the battery serves load,
    ``_transition_hold_deficit`` already reduces ``grid_import_kwh`` by the battery's
    contribution (``transitions.py``: ``grid_import = max(0, load_deficit - battery_to_load)``),
    so the avoided import is already reflected in a lower ``import_cost``. Crediting it
    again here valued stored energy at ~2x retail, which made thin overnight
    charge-and-drain cycling look profitable (~5c/kWh apparent profit on a ~2c/kWh
    round-trip loss) and is why every soft anti-cycling penalty got "paid through".
    A deterministic replay of the 2026-06-29 live plan confirmed removing this credit
    eliminates the overnight sawtooth (11.3 -> 0.0 kWh) while the demand-window
    pre-charge and target are preserved. Kept as a serialized field for diagnostics."""

    switching_penalty: float = 0.0
    """Penalty applied if the action involves a mode switch."""

    uncertainty_penalty: float = 0.0
    """Penalty for grid actions when forecast horizon is restricted (Issue #431)."""

    solar_opportunity_penalty: float = 0.0
    """Penalty for grid charging when future solar can charge battery for free (Issue #607)."""

    futile_cycling_penalty: float = 0.0
    """Penalty for grid charging when energy will drain through house load before reaching
    a useful period (solar surplus or demand window). Issue #638."""

    @property
    def net_cost(self) -> float:
        """Net slot cost = import - revenue + penalties.

        ``self_consumption_value`` is deliberately NOT subtracted: the avoided import
        is already captured by a reduced ``import_cost`` (see the field docstring and
        ``transitions._transition_hold_deficit``). Subtracting it double-counted the
        benefit and drove the #406 / #800 overnight sawtooth.
        """
        return (
            self.import_cost
            - self.export_revenue
            + self.shortfall_penalty
            + self.uncertainty_penalty
            + self.switching_penalty
            + self.solar_opportunity_penalty
            + self.futile_cycling_penalty
        )

    def to_dict(self) -> dict:
        """Serialize to dict for sensor attributes and shadow output."""
        return {
            "import_cost": self.import_cost,
            "export_revenue": self.export_revenue,
            "shortfall_penalty": self.shortfall_penalty,
            "self_consumption_value": self.self_consumption_value,
            "uncertainty_penalty": self.uncertainty_penalty,
            "switching_penalty": self.switching_penalty,
            "solar_opportunity_penalty": self.solar_opportunity_penalty,
            "futile_cycling_penalty": self.futile_cycling_penalty,
            "net_cost": self.net_cost,
        }


@dataclass
class PlannedSlotDecision:
    """
    Optimizer output for a single forecast slot.

    Compatible fields are provided so the existing forecast pipeline
    can derive legacy boolean flags from action.
    """

    slot_index: int
    timestamp_iso: str
    slot_interval_minutes: int

    action: PlannerAction
    """The optimizer's chosen action for this slot."""

    reason_code: PlannerReasonCode
    """Primary reason/classification for this decision."""

    objective_terms: ObjectiveTerms
    """Per-slot objective term breakdown for debugging."""

    predicted_soc_pct: float
    """Predicted battery SOC (%) at the end of this slot."""

    grid_import_kwh: float
    """Grid import energy for this slot (kWh)."""

    grid_export_kwh: float
    """Grid export energy for this slot (kWh)."""

    # --- Slot context passthroughs (for dashboard debug display) ---
    solar_kwh: float = 0.0
    """Forecast solar generation for this slot (kWh), copied from SlotContext."""

    consumption_kwh: float = 0.0
    """Forecast household consumption for this slot (kWh), copied from SlotContext."""

    buy_price: float = 0.0
    """Import price ($/kWh), copied from SlotContext."""

    sell_price: float = 0.0
    """Export (FIT) price ($/kWh), copied from SlotContext."""

    is_solar_opportunity: bool = False
    """True if this slot was identified as a solar opportunity wait period (#610)."""

    # --- Derived compatibility flags (set from action) ---
    @property
    def grid_charge(self) -> bool:
        return self.action in (
            PlannerAction.CHARGE_GRID_NORMAL,
            PlannerAction.CHARGE_GRID_BOOST,
        )

    @property
    def grid_charge_boost(self) -> bool:
        return self.action == PlannerAction.CHARGE_GRID_BOOST

    @property
    def proactive_export(self) -> bool:
        return self.action == PlannerAction.EXPORT_PROACTIVE


# -----------------------------------------------------------------------------
# Full optimizer result
# -----------------------------------------------------------------------------


@dataclass
class OptimizerResult:
    """Full output from a DPPlanner.plan() call."""

    success: bool
    """True if optimizer produced a complete valid plan."""

    planner_version: str = "dp_v1"
    """Version identifier for this planner (used in comparison records)."""

    solve_time_seconds: float = 0.0
    """Wall-clock time taken to solve (for performance diagnostics)."""

    total_slots: int = 0
    """Total number of slots in the planning horizon."""

    states_explored: int = 0
    """Number of DP states evaluated."""

    decisions: list[PlannedSlotDecision] = field(default_factory=list)
    """Ordered list of per-slot decisions from first slot to end of horizon."""

    projected_import_kwh: float = 0.0
    """Total projected grid import over horizon (kWh)."""

    projected_export_kwh: float = 0.0
    """Total projected grid export over horizon (kWh)."""

    projected_net_cost: float = 0.0
    """Total projected net cost over horizon ($)."""

    terminal_shortfall_pct: float = 0.0
    """Residual SOC shortfall (%) at demand window entry, if any."""

    spike_funding_slot_count: int = 0
    """How many slots qualified as spike-event pre-charge funding this cycle.

    0 means nothing qualified — the common case on an ordinary day, and the signal
    that the feature stayed fully inert."""

    spike_funding_accepted: bool = False
    """True when the spike-funded plan beat the baseline and was kept.

    Read this together with ``spike_funding_net_cost_delta`` — False alone does NOT
    mean the guard caught a bad plan. Measured over a 200-scenario sweep, ~40% of
    cycles that qualify end in a dead tie (the DP simply declines the extra option),
    and those are indistinguishable from genuine rejections on this flag alone."""

    spike_funding_net_cost_delta: float = 0.0
    """Baseline minus spike-funded projected net cost ($), when funding slots existed.

    > 0  the widening saved money and was adopted.
    == 0 it qualified but changed nothing — the common, benign case.
    < 0  the guard REJECTED it as worse. A persistent run of these means the
         qualification rule is too permissive and should be tightened; an occasional
         one is expected, because the min-cycle-saving gate makes this DP
         non-monotone."""

    can_solar_reach_target: bool = False
    """True if solar alone can reach DW target (no grid charge, no export). Phase 4, #441."""

    can_solar_reach_target_in_dw: bool = False
    """True if solar alone reaches target at any point during DW (allow_dw_entry_under_target mode). Issue #505."""

    error_message: str | None = None
    """Error description if success=False."""

    reason_code_histogram: dict[str, int] = field(default_factory=dict)
    """Count of each reason code across all slots (for diagnostics)."""

    # Terminal cost diagnostic fields (PR #789 wiring)
    forecast_accuracy: float | None = None
    """Forecast accuracy (0-1.0) used for solar discount. None if no terminal penalty."""

    accuracy_discount_factor: float | None = None
    """Discount factor applied to solar gain (0.5-1.0)."""

    peak_soc_pct: float | None = None
    """Peak SOC (%) across all planned slots."""

    dw_entry_soc_pct: float | None = None
    """SOC (%) at demand window entry slot. None if no DW."""


# -----------------------------------------------------------------------------
# Negative FIT avoidance context
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class NegativeFitAvoidanceContext:
    """Immutable context for recoverability-based negative-FIT avoidance.

    The planner may proactively discharge at positive FIT before a bad-price
    spill window when conservative future solar can still recover the battery
    to target by the relevant deadline.
    """

    risk_window_start_idx: int
    """Index of the first slot in the spill-risk window (first sell_price <= 0)."""

    risk_window_end_idx: int
    """Index of the last non-positive-FIT slot at or before the recovery deadline
    (inclusive).

    The window spans first-to-last bad-FIT slot within that bound, so it may
    contain positive-FIT slots; those are the export opportunities the avoidance
    branch acts on. Bounding at the deadline keeps a 24h+ horizon from sizing
    today's pre-discharge off tomorrow's negative middle.
    """

    required_headroom_kwh: float
    """Estimated storage space (kWh) needed to absorb spill during risk window."""

    recovery_deadline_idx: int | None
    """Slot index by which target must be recoverable (demand window or horizon end)."""

    conservative_recovery_kwh_by_slot: tuple[float, ...]
    """Conservative recoverable solar (kWh) from each slot to recovery deadline."""

    recoverability_floor_pct_by_slot: tuple[float, ...]
    """Precomputed recoverability floor (%) for each slot based on future recovery potential."""


# -----------------------------------------------------------------------------
# Optimizer inputs
# -----------------------------------------------------------------------------


@dataclass
class OptimizerInputs:
    """
    Full inputs for a single planning cycle.

    The coordinator is responsible for populating this from
    coordinator data, forecast series, and config.
    """

    cycle_id: str
    """Unique identifier for this planning cycle (for audit/comparison)."""

    initial_soc_pct: float
    """Battery SOC at the start of the planning horizon (%)."""

    slots: list[SlotContext]
    """Ordered list of forecast slots from now to end of horizon."""

    current_action: PlannerAction | None = None
    """Currently commanded action (to apply switching penalty against first slot)."""

    config: OptimizerConfig = field(default_factory=OptimizerConfig)
    """Optimizer configuration and constraints."""

    all_solcast: list[dict[str, Any]] = field(default_factory=list)
    """Full solar forecast (today + tomorrow) for penalty calculation (Issue #607)."""

    solcast_analysis_today: Any | None = None
    """Solcast analysis for today with confidence data (Issue #794)."""

    solcast_analysis_tomorrow: Any | None = None
    """Solcast analysis for tomorrow with confidence data (Issue #794)."""

    solar_absent_confidence: float = 1.0
    """Absent-forecast confidence passed to ConfidenceResolver (issue stale-solar-fix)."""

    solar_accuracy_tracker: SolarAccuracyTracker | None = None
    """Tracker for forecast accuracy to apply discount to terminal cost (Issue #785)."""

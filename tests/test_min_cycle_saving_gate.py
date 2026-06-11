"""Tests for the minimum-cycle-saving gate (anti-micro-cycling).

The gate (``core._compute_best_action``) drops a grid charge that beats simply holding by
a positive but sub-threshold margin — i.e. it is not worth a battery cycle. Because the
margin is the DP's real cost difference (``hold_total_cost - charge_total_cost``), it
credits every value source the optimizer sees (evening-peak avoidance, the demand-window
target, backup readiness), so genuine pre-charge and spike capture survive while thin
speculative arbitrage is dropped.

Runs ENTIRELY OFFLINE. Two worlds:
  * the real 2026-06-04 plan (56 slots) that motivated the gate, and
  * a synthetic winter pre-charge day (low SOC, weak solar, evening demand window).
"""

from __future__ import annotations

from custom_components.localshift.engine.optimizer_dp import (
    DPPlanner,
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)

CHARGE_ACTIONS = {
    PlannerAction.CHARGE_GRID_NORMAL,
    PlannerAction.CHARGE_GRID_BOOST,
}

PROD_DEFAULT = 0.25  # production DEFAULT_MIN_CYCLE_SAVING

# --- World 1: the real 2026-06-04 plan ------------------------------------------------
# (buy, sell, solar_kwh, consumption_kwh, interval_min); slots 23/24 are the live
# CHEAP_IMPORT_WINDOW micro-charge (~1c/kWh saving), slot 35 (07:00) the morning peak.
_LIVE_ROWS = [
    (0.1305, 0.1006, 0, 0.085, 5),
    (0.1305, 0.1017, 0, 0.085, 5),
    (0.1306, 0.1021, 0, 0.085, 5),
    (0.1311, 0.1022, 0, 0.085, 5),
    (0.1319, 0.1023, 0, 0.085, 5),
    (0.131, 0.1022, 0, 0.085, 5),
    (0.1315, 0.1021, 0, 0.085, 5),
    (0.1317, 0.1019, 0, 0.085, 5),
    (0.1307, 0.1016, 0, 0.085, 5),
    (0.1305, 0.1015, 0, 0.556, 30),
    (0.1312, 0.1021, 0, 0.6, 30),
    (0.1315, 0.1023, 0, 0.558, 30),
    (0.1312, 0.1021, 0, 0.562, 30),
    (0.1306, 0.1015, 0, 0.527, 30),
    (0.1308, 0.1017, 0, 0.526, 30),
    (0.1297, 0.0622, 0, 0.828, 30),
    (0.1299, 0.0624, 0, 0.837, 30),
    (0.1301, 0.0625, 0, 0.372, 30),
    (0.1284, 0.061, 0, 0.372, 30),
    (0.1248, 0.0577, 0, 0.308, 30),
    (0.1227, 0.0558, 0, 0.308, 30),
    (0.1214, 0.0546, 0, 0.258, 30),
    (0.1202, 0.0536, 0, 0.258, 30),
    (0.119, 0.0524, 0, 0.215, 30),
    (0.1187, 0.0521, 0, 0.215, 30),
    (0.1186, 0.0521, 0, 0.222, 30),
    (0.1189, 0.0524, 0, 0.222, 30),
    (0.119, 0.0525, 0, 0.371, 30),
    (0.1214, 0.0546, 0, 0.371, 30),
    (0.124, 0.057, 0, 0.206, 30),
    (0.1277, 0.0603, 0, 0.207, 30),
    (0.135, 0.067, 0, 0.415, 30),
    (0.1414, 0.0728, 0, 0.415, 30),
    (0.1443, 0.0754, 0, 0.341, 30),
    (0.1501, 0.0807, 0, 0.341, 30),
    (0.1559, 0.086, 0.012, 0.821, 30),
    (0.1482, 0.079, 0.053, 0.82, 30),
    (0.1336, 0.0657, 0.157, 0.354, 30),
    (0.1268, 0.0595, 0.319, 0.354, 30),
    (0.1235, 0.0565, 0.479, 0.619, 30),
    (0.1215, 0.0547, 0.623, 0.619, 30),
    (0.1228, 0.0435, 0.735, 0.909, 30),
    (0.1204, 0.0414, 0.884, 0.909, 30),
    (0.1193, 0.0404, 0.985, 0.893, 30),
    (0.118, 0.0392, 1.027, 0.892, 30),
    (0.1149, 0.0363, 1.019, 0.52, 30),
    (0.1145, 0.036, 0.968, 0.52, 30),
    (0.1153, 0.0368, 0.923, 0.423, 30),
    (0.1167, 0.038, 0.869, 0.423, 30),
    (0.1199, 0.0409, 0.767, 0.407, 30),
    (0.1244, 0.0451, 0.593, 0.407, 30),
    (0.1316, 0.0639, 0.388, 0.396, 30),
    (0.1421, 0.0735, 0.141, 0.397, 30),
    (0.155, 0.1238, 0.039, 0.485, 30),
    (0.1624, 0.1305, 0.006, 0.485, 30),
    (0.1275, 0.0988, 0, 0.381, 30),
]


def _live_slots(rows=None):
    rows = rows or _LIVE_ROWS
    return [
        SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-06-04T{i:02d}:00:00",
            slot_interval_minutes=iv,
            buy_price=buy,
            sell_price=sell,
            solar_kwh=solar,
            consumption_kwh=cons,
        )
        for i, (buy, sell, solar, cons, iv) in enumerate(rows)
    ]


def _live_config(min_saving):
    return OptimizerConfig(
        battery_capacity_kwh=13.5,
        charge_efficiency=0.92,
        discharge_efficiency=0.95,
        min_soc_pct=10.0,
        demand_window_target_soc_pct=95.0,
        optimization_mode="self_consumption",
        effective_cheap_price=0.16,
        base_cheap_price=0.121,
        target_shortfall_penalty_per_pct=0.08,
        soc_bins=50,
        min_cycle_saving=min_saving,
    )


def _charge_slots(result):
    return [d.slot_index for d in result.decisions if d.action in CHARGE_ACTIONS]


def _plan_live(min_saving, rows=None):
    inputs = OptimizerInputs(
        cycle_id="t",
        initial_soc_pct=88.0,
        slots=_live_slots(rows),
        config=_live_config(min_saving),
    )
    return DPPlanner().plan(inputs)


def test_gate_disabled_reproduces_micro_arbitrage():
    """min_cycle_saving=0 (disabled) -> the live overnight charge happens."""
    charges = _charge_slots(_plan_live(0.0))
    assert 23 in charges and 24 in charges, (
        f"baseline should reproduce the live 01:00/01:30 charge, got {charges}"
    )


def test_gate_blocks_micro_overnight_arbitrage():
    """At 25c, tonight's ~1c/kWh arbitrage isn't worth a cycle -> removed entirely."""
    charges = _charge_slots(_plan_live(PROD_DEFAULT))
    assert charges == [], (
        f"min-cycle-saving gate should block the micro arbitrage, got {charges}"
    )


def test_gate_preserves_spike_capture():
    """A forecast morning spike saves far more than 25c/kWh -> charging returns.

    With the spike the optimizer charges in the run-up to it (just-in-time, to minimise
    drain) rather than at the far-overnight trough — both capture the spike; what matters
    is that the gate does NOT suppress it (cf. test_gate_blocks_micro, same threshold and
    no spike -> no charge at all).
    """
    rows = list(_LIVE_ROWS)
    rows[35] = (2.00, 1.90, 0.012, 0.821, 30)  # $2/kWh spike at 07:00
    charges = _charge_slots(_plan_live(PROD_DEFAULT, rows=rows))
    assert charges, "a $2 spike (huge saving) must still be captured"
    assert any(30 <= c <= 35 for c in charges), (
        f"charge should be positioned to reach the 07:00 spike, got {charges}"
    )


# --- World 2: synthetic winter pre-charge day -----------------------------------------
_PC_N = 48
_PC_DW_ENTRY = 36  # 18:00
_PC_DW_SLOTS = set(range(36, 43))  # 18:00-21:00


def _precharge_slots():
    out = []
    for i in range(_PC_N):
        hh, mm = divmod(i * 30, 60)
        solar = max(0.0, 0.5 - abs(i - 23) * 0.06) if 16 <= i <= 30 else 0.0
        if i < 12:
            buy = 0.12
        elif i < 28:
            buy = 0.135
        elif i < _PC_DW_ENTRY:
            buy = 0.15
        else:
            buy = 0.20  # evening demand-window peak
        out.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=f"2026-06-04T{hh:02d}:{mm:02d}:00",
                slot_interval_minutes=30,
                buy_price=buy,
                sell_price=buy * 0.4,
                solar_kwh=solar,
                consumption_kwh=0.4,
                is_demand_window_slot=(i in _PC_DW_SLOTS),
                is_demand_window_entry=(i == _PC_DW_ENTRY),
            )
        )
    return out


def _soc_at_dw(result):
    return next(
        d.predicted_soc_pct for d in result.decisions if d.slot_index == _PC_DW_ENTRY
    )


def _plan_precharge(min_saving):
    cfg = OptimizerConfig(
        battery_capacity_kwh=13.5,
        charge_efficiency=0.92,
        discharge_efficiency=0.95,
        min_soc_pct=10.0,
        demand_window_target_soc_pct=80.0,
        optimization_mode="self_consumption",
        effective_cheap_price=0.16,
        base_cheap_price=0.121,
        target_shortfall_penalty_per_pct=0.08,
        soc_bins=50,
        min_cycle_saving=min_saving,
    )
    inputs = OptimizerInputs(
        cycle_id="pc", initial_soc_pct=20.0, slots=_precharge_slots(), config=cfg
    )
    return DPPlanner().plan(inputs)


def test_gate_preserves_target_seeking_precharge():
    """The gate must NOT block charging needed to reach the demand-window target.

    This is the #804 trap: the per-kWh ENERGY spread (charge 0.15 -> DW 0.20) is thin,
    but the charge's all-in saving over holding includes the avoided terminal shortfall,
    which the benefit-margin gate credits via future_cost. So pre-charge survives even
    though its raw price spread is under the threshold.
    """
    base = _soc_at_dw(_plan_precharge(0.0))
    gated = _soc_at_dw(_plan_precharge(PROD_DEFAULT))
    assert base >= 78.0, "fixture sanity: baseline should pre-charge to target"
    assert gated >= 78.0, (
        f"gate wrongly blocked target-seeking pre-charge: SOC@DW-entry={gated:.1f}% "
        f"(target 80%)"
    )


# --- World 3: high-SOC taper-region pre-charge (2026-06-11 sub-target incident) --------
# Live plan computed 14:25 with SOC 88%, target 95%, DW at 15:00: the gate dropped each
# early 5-min pre-charge slot (its per-slot margin over deferring was < 0.25/kWh), so the
# optimizer HELD the first slots and only started charging late — entering the DW at 91.8%
# (a 3.2pt shortfall). Because target pre-charge happens entirely in the 80-95% taper band,
# the deferred late charge is too slow to recover. The urgency-window exemption lets the
# needed pre-charge through while the gate stays active for speculative/overnight charges.

_W3_TARGET = 95.0
_W3_DW_ENTRY = 7  # 15:00


def _taper_precharge_slots():
    # 7 five-minute pre-DW slots (14:25-14:55) then the 15:00 demand window.
    pre = [
        (0.184, 0.107, 0.140),
        (0.135, 0.107, 0.140),
        (0.125, 0.107, 0.139),
        (0.125, 0.107, 0.142),
        (0.125, 0.107, 0.143),
        (0.125, 0.107, 0.144),
        (0.125, 0.107, 0.146),
    ]
    slots = []
    mins = 25
    for i, (solar, load, buy) in enumerate(pre):
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=f"2026-06-11T14:{mins:02d}:00",
                slot_interval_minutes=5,
                buy_price=buy,
                sell_price=0.06,
                solar_kwh=solar,
                consumption_kwh=load,
            )
        )
        mins += 5
    dw = [
        (0.447, 0.613, 0.156),
        (0.234, 0.476, 0.170),
        (0.048, 0.499, 0.182),
        (0.010, 0.510, 0.188),
        (0.000, 0.591, 0.188),
    ]
    hh, mm = 15, 0
    for j, (solar, load, buy) in enumerate(dw):
        slots.append(
            SlotContext(
                slot_index=_W3_DW_ENTRY + j,
                timestamp_iso=f"2026-06-11T{hh:02d}:{mm:02d}:00",
                slot_interval_minutes=30,
                buy_price=buy,
                sell_price=0.10,
                solar_kwh=solar,
                consumption_kwh=load,
                is_demand_window_entry=(j == 0),
                is_demand_window_slot=True,
            )
        )
        mm += 30
        if mm >= 60:
            mm, hh = 0, hh + 1
    # post-DW exit so the DW block closes
    slots.append(
        SlotContext(
            slot_index=_W3_DW_ENTRY + len(dw),
            timestamp_iso="2026-06-11T17:30:00",
            slot_interval_minutes=30,
            buy_price=0.18,
            sell_price=0.10,
            solar_kwh=0.0,
            consumption_kwh=0.5,
        )
    )
    return slots


def _plan_taper(min_saving):
    cfg = OptimizerConfig(
        battery_capacity_kwh=13.5,
        charge_rate_kw=3.3,
        boost_charge_rate_kw=5.0,
        charge_efficiency=0.92,
        discharge_efficiency=0.95,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        demand_window_target_soc_pct=_W3_TARGET,
        optimization_mode="self_consumption",
        effective_cheap_price=0.19,
        base_cheap_price=0.19,
        charge_taper_start_pct=80.0,
        charge_taper_min_factor=0.2,
        target_shortfall_penalty_per_pct=0.03,
        soc_bins=50,
        switching_penalty=0.08,
        min_cycle_saving=min_saving,
    )
    inputs = OptimizerInputs(
        cycle_id="taper",
        initial_soc_pct=88.0,
        slots=_taper_precharge_slots(),
        config=cfg,
        current_action=PlannerAction.HOLD,
    )
    return DPPlanner().plan(inputs)


def _dw_entry_soc(result):
    return next(
        d.predicted_soc_pct for d in result.decisions if d.slot_index == _W3_DW_ENTRY
    )


def test_gate_does_not_procrastinate_taper_region_precharge():
    """The gate must not defer urgency-window pre-charge into a sub-target DW entry.

    RED before the urgency-window exemption: the gate drops the early 5-min charges, the
    optimizer holds them, and the deferred late charge (in the taper band) enters the DW
    well under target. GREEN after: it charges in the urgency window and reaches target.
    """
    gated = _plan_taper(PROD_DEFAULT)
    early_actions = [d.action for d in gated.decisions[:_W3_DW_ENTRY]]
    n_charge = sum(a in CHARGE_ACTIONS for a in early_actions)
    # It must charge through the urgency window, not hold and start late.
    assert n_charge >= 5, (
        f"gate procrastinated pre-charge; pre-DW actions={[a.value for a in early_actions]}"
    )
    entry = _dw_entry_soc(gated)
    assert entry >= 94.0, f"DW-entry SOC {entry:.1f}% below target after exemption"


def test_taper_precharge_matches_ungated_with_exemption():
    """With the exemption, the gate no longer changes the urgency-window pre-charge plan.

    The whole pre-DW window is urgency pre-charge, so a 0.25 gate and a disabled gate must
    now reach the same DW-entry SOC (the gate is fully exempted there).
    """
    assert (
        abs(_dw_entry_soc(_plan_taper(PROD_DEFAULT)) - _dw_entry_soc(_plan_taper(0.0)))
        < 0.5
    )

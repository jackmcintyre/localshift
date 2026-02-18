# backlog-crit-001: Proactive Export Fires Below Cost — Unprofitable Overnight Discharge

**Priority:** 🔴 CRIT  
**Status:** ✅ COMPLETED  
**Created:** 2026-02-19  
**Completed:** 2026-02-19

---

## Summary

The proactive export logic had two critical flaws that caused the battery to discharge and
sell energy at a loss — selling grid-charged energy at prices well below what was paid to
charge it overnight.

---

## Observed Behaviour

At 06:44 on 2026-02-19, with overnight charge cost ~$0.145–$0.16/kWh:
- System was in `proactive_export` mode, selling at **$0.09/kWh** (net loss ~5c/kWh)
- Forecast showed future PE=Y slots at **$0.025–$0.04/kWh** during solar hours
- Battery was at **69% SOC** with **target = 100%** — actively exporting while below target
- SOC was forecast to drop to 41.6% before solar picked up, costing additional solar
  kWh just to refill what was unnecessarily exported

---

## Root Causes

### Bug 1: No Profitability Floor
`_should_proactive_export_at_slot()` used a **relative threshold only** — `use_price >= 80%
of max_FIT_before_fill`. If all sell prices in the window were low (e.g. $0.025–$0.04), the
threshold became $0.02, and any positive price qualified. There was no absolute floor
anchored to the cost of the energy being sold.

### Bug 2: Exporting While Below Target SOC
The method checked `predicted_soc > export_min_soc_pct` (the minimum reserve, ~20%) but
NOT `predicted_soc >= target_pct` (the desired target, 100%). With target=100% and SOC at
39–69%, the system was exporting, deepening the deficit and requiring solar to spend the
first hours of daylight refilling exported energy instead of working toward the target.

---

## Fix Applied

Two guard conditions added to `_should_proactive_export_at_slot()` in
`computation_engine_lib/forecast_computer.py`:

### Fix 1 — Profitability Floor
```python
# PROFITABILITY FLOOR: Never export below the effective cheap price.
# This prevents selling grid-charged energy at a loss.
if use_price <= effective_cheap_price:
    _LOGGER.debug("PROACTIVE_EXPORT: BLOCKED - sell $%.3f <= cheap_price floor $%.3f", ...)
    return False, 0.0
```
`effective_cheap_price` is the threshold at which buying was justified. Selling below this
price means the energy is worth more stored than exported.

### Fix 2 — Above-Target Gate
```python
# ABOVE-TARGET GATE: Only export when battery is at or above the target SOC.
if predicted_soc < (target_pct - 2.0):
    _LOGGER.debug("PROACTIVE_EXPORT: BLOCKED - SOC %.1f%% < target %.1f%%", ...)
    return False, 0.0
```
A 2% hysteresis prevents blocking exports right at the boundary.

### Supporting Changes
- Added `target_pct: float` and `effective_cheap_price: float` parameters to
  `_should_proactive_export_at_slot()` method signature
- Updated the call site in `compute_forecast()` to pass `target_pct` and
  `data.effective_cheap_price`

---

## Impact

- **Before fix:** System exported 1.6+ kWh at $0.09–$0.10/kWh while having paid
  $0.145–$0.16/kWh overnight, plus planned further exports at $0.025–$0.04/kWh
- **After fix:** Proactive export is gated by (a) profitability relative to the buy
  threshold and (b) having reached the battery target first

---

## Files Changed

- `custom_components/localshift/computation_engine_lib/forecast_computer.py`
  - `_should_proactive_export_at_slot()`: added `target_pct` + `effective_cheap_price`
    parameters and two blocking guard conditions
  - `compute_forecast()`: updated call site to pass new parameters

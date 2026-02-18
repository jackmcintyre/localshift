# Backlog Item: Hybrid 5-min / 15-min Forecast + Lookup Fix

**ID:** backlog-high-021
**Priority:** 🟠 HIGH
**Status:** ✅ COMPLETED
**Created:** 2026-02-18
**Completed:** 2026-02-18

---

## Summary

Fixes the root cause of mode-switching delays (Issue 3 in `MODE_SWITCHING_DELAY_ANALYSIS.md`)
and simultaneously resolves the `is_before_dw` wrap-around bug (Issue 4).

---

## Problem

### Issue 3 — Forecast Slot Mismatch (root cause of delays)

`compute_forecast()` was rounding the start slot **up** to the next 15-min boundary while
`_get_forecast_entry_for_now()` was rounding **down** to the nearest 15-min boundary.
This meant the current slot was often not present in the forecast, causing the system to
fall back to `SELF_CONSUMPTION` for up to ~14 minutes at a time.

### Issue 4 — `is_before_dw` wrap-around bug

When `now_dt.hour >= target_hour` (e.g. it's 4pm, DW=3pm), the original code:
```python
is_before_dw = slot_hour >= now_dt.hour or slot_hour < target_hour
```
incorrectly marked late-afternoon slots (16–23) as "before DW", potentially triggering
spurious grid charging in post-DW hours.

---

## Solution

### `forecast_computer.py`

- **`base_slot` calculation:** Changed from "round up to next 15-min" to "round down to
  current 5-min boundary". This guarantees a slot always exists for "right now".
- **Hybrid 5-min / 15-min loop:** Near-term window (first 2 hours) generates 24 × 5-min
  slots using `get_solar_for_5min_slot()` with `load_kw * (5/60)` consumption and
  charge rates scaled by `slot_fraction = slot_minutes / 60`. Remaining 22 hours use
  88 × 15-min slots as before.
- **`is_before_dw` fix:** Simplified to `is_before_dw = slot_hour < target_hour`. Because
  the 24h forecast is always chronological, this correctly partitions both today's
  post-DW slots and tomorrow's pre-DW slots without needing a date check.
- **`slot_interval_minutes` field:** Added to each forecast entry dict (5 or 15) for
  observability and future use.

### `solar_utils.py`

- Added `get_solar_for_5min_slot()` — mirrors `get_solar_for_15min_slot()` but returns
  `period_kwh / 6` (5 min = 1/6 of the 30-min Solcast period).

### `computation_engine_lib/__init__.py`

- Exported `get_solar_for_5min_slot`.

### `computation_engine.py` — `_get_forecast_entry_for_now()`

- Replaced the exact-match + fallback gap logic with a granularity-agnostic scan:
  finds the most-recent entry whose timestamp ≤ now. Works for any mix of slot sizes.
- Removed the 15-min "fallback gap" logic (no longer needed).
- Added tz-aware comparison guard (normalises `now_dt` via `dt_util.as_local`).

---

## Files Changed

- `custom_components/localshift/computation_engine_lib/solar_utils.py`
- `custom_components/localshift/computation_engine_lib/__init__.py`
- `custom_components/localshift/computation_engine_lib/forecast_computer.py`
- `custom_components/localshift/computation_engine.py`

---

## Testing

All 47 existing tests pass. Pre-commit (ruff, ruff-format, vulture, pyright, pytest)
all green.

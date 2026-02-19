# Health Check Missing export_mode Verification

**ID:** backlog-high-020
**Priority:** HIGH
**Status:** COMPLETED
**Created:** 2026-02-19
**Updated:** 2026-02-19

---

## Summary

Health check only verifies operation_mode and backup_reserve, but not export_mode, potentially missing battery state inconsistencies.

---

## Description

The state machine health check (`_get_expected_state_for_mode`) only validates:
- `operation_mode` (autonomous/backup)
- `backup_reserve` (percentage)

But does NOT verify `export_mode` (pv_only/battery_ok), which is also critical for battery control.

**Current Health Check (incomplete):**
```python
def _get_expected_state_for_mode(self, mode: BatteryMode) -> dict:
    # Only checks operation_mode and backup_reserve
    # MISSING: export_mode verification
```

**Risk:** Battery could be in inconsistent state where operation_mode and export_mode don't match the intended battery mode, leading to unexpected charging/discharging behavior.

---

## Affected Files

- `custom_components/localshift/state_machine.py` - Incomplete health check
- `custom_components/localshift/battery_controller.py` - Sets both operation_mode and export_mode

---

## Steps to Reproduce

1. Manually set conflicting operation_mode and export_mode via HA UI
2. Trigger state machine health check
3. Health check passes despite inconsistent battery state
4. Battery behavior may be unpredictable

---

## Proposed Solution

Extend health check to verify export_mode consistency:

```python
def _get_expected_state_for_mode(self, mode: BatteryMode) -> dict:
    expected = {
        "operation_mode": ...,
        "backup_reserve": ...,
        "export_mode": ...  # ADD THIS
    }
    return expected
```

**Export Mode Logic:**
- `SELF_CONSUMPTION`: `pv_only` (don't export battery)
- `GRID_CHARGING`: `battery_ok` (allow grid charging)
- `BOOST_CHARGING`: `battery_ok` (allow grid charging)
- `SPIKE_DISCHARGE`: `battery_ok` (allow discharge)
- `PROACTIVE_EXPORT`: `battery_ok` (allow discharge)
- `DEMAND_BLOCK`: `pv_only` (block export during peak)
- `MANUAL`: Don't check (user override)

---

## Notes

Export mode is as critical as operation mode for proper battery control. Inconsistent states could lead to unexpected battery behavior or financial loss.

---

## Related Items

- Phase 1.1.3 in CODE_REVIEW_PLAN.md - Health check logic review
- backlog-crit-002 - State machine testing (would catch this)
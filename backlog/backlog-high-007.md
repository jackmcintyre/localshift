# Inconsistent State Machine Priority Order

**ID:** backlog-high-007  
**Priority:** HIGH  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

The priority chain order in state machine doesn't match documentation.

---

## Description

The priority chain order doesn't match documentation:
- Documentation says: "Automation disabled" should be FIRST priority
- Code checks: demand_window_active BEFORE automation_enabled

This means automation disabled users still get demand block enforcement, which may be unexpected.

---

## Affected Files

- `custom_components/amber_powerwall/coordinator.py` (Step 14)

---

## Proposed Solution

Reorder to match documented priority:
1. Automation disabled → MANUAL
2. Price spike → SPIKE_DISCHARGE
3. Manual override → MANUAL
4. Demand window → DEMAND_BLOCK
5. ... rest of chain

---

## Notes

Priority order should match documented behavior.

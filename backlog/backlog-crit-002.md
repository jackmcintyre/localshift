# Missing Unit Tests for State Machine

**ID:** backlog-crit-002
**Priority:** CRIT
**Status:** PROPOSED
**Created:** 2026-02-19
**Updated:** 2026-02-19

---

## Summary

Core state machine logic has no unit tests, creating high risk of undetected bugs in battery mode transitions.

---

## Description

The `state_machine.py` file contains critical logic for battery mode transitions, debounce timers, and health checks. This code directly controls battery charging/discharging behavior and has no unit test coverage.

**Missing Test Coverage:**
- State transition logic and debounce timers
- Health check validation
- Mode transition failure handling
- Startup grace period logic
- Re-entrant call prevention

**Risk:** Bugs in state machine could cause:
- Battery damage from incorrect mode transitions
- Financial loss from improper charging decisions
- System instability from failed transitions

---

## Affected Files

- `custom_components/localshift/state_machine.py` - Core logic untested
- `tests/` - No `test_state_machine.py` file exists

---

## Proposed Solution

Create comprehensive unit tests for `StateMachine` class:

1. **Transition Logic Tests**
   - Test all valid mode transitions
   - Test debounce timer application
   - Test immediate transitions for high-priority modes

2. **Health Check Tests**
   - Test health validation for all modes
   - Test cooldown period enforcement
   - Test export_mode verification

3. **Error Handling Tests**
   - Test failed transition retry logic
   - Test re-entrant call prevention
   - Test startup grace period

4. **Integration Tests**
   - Test state machine with coordinator
   - Test mode transition notifications
   - Test manual override handling

---

## Notes

This is the highest priority test gap due to the critical nature of battery control logic. State machine bugs could cause immediate safety and financial issues.

---

## Related Items

- Phase 1.1 in CODE_REVIEW_PLAN.md - State Machine review
- backlog-high-019 - Day boundary bug in grid charging (related state machine logic)
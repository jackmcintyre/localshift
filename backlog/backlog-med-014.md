# Synchronous Sleep in Async Context

**ID:** backlog-med-014
**Priority:** MED
**Status:** PROPOSED
**Created:** 2026-02-19
**Updated:** 2026-02-19

---

## Summary

Battery controller uses synchronous `asyncio.sleep(1)` in async validation loop, blocking Home Assistant event loop.

---

## Description

The battery controller validation logic uses synchronous sleep in an async context:

```python
async def validate_transition(self, ...):
    for attempt in range(timeout):  # Up to 10 attempts
        await asyncio.sleep(1)  # BLOCKS event loop for 1 second per attempt
        # Check battery state
```

This blocks the entire Home Assistant event loop during validation, potentially affecting responsiveness of other integrations.

**Impact:** During battery transitions, HA becomes unresponsive for up to 10 seconds while validation polls the battery state.

---

## Affected Files

- `custom_components/localshift/battery_controller.py` - Blocking validation loop

---

## Steps to Reproduce

1. Trigger battery mode transition
2. Attempt to use other HA features during transition
3. Observe UI lag or unresponsiveness during validation

---

## Proposed Solution

**Option A: Use async callbacks/events**
Replace polling with event-driven validation:
```python
# Instead of polling loop
await self._wait_for_state_change(expected_state, timeout=10)
```

**Option B: Reduce blocking impact**
- Reduce timeout from 10 to 3-5 seconds
- Add yielding between checks
- Use `asyncio.wait_for()` with proper cancellation

**Option C: Move to background task**
Run validation in background and notify when complete.

---

## Notes

Blocking the event loop affects HA responsiveness. This is particularly problematic during battery transitions when users expect immediate feedback.

---

## Related Items

- Phase 2.1 in CODE_REVIEW_PLAN.md - Battery controller validation
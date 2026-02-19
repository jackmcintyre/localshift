# Private Method Access Breaking Encapsulation

**ID:** backlog-med-016
**Priority:** MED
**Status:** PROPOSED
**Created:** 2026-02-19
**Updated:** 2026-02-19

---

## Summary

Switch and number platforms access private coordinator methods, breaking encapsulation and creating tight coupling.

---

## Description

Entity platforms directly call private coordinator methods:

```python
# In switch.py and number.py
self.coordinator._compute_derived_values()  # PRIVATE METHOD
self.coordinator._notify_listeners()        # PRIVATE METHOD
await self.coordinator.async_evaluate_state_machine()
```

**Problems:**
- Breaks encapsulation - platforms know internal coordinator implementation
- Creates tight coupling - changes to coordinator internals break platforms
- Makes testing harder - platforms depend on private implementation details
- Violates object-oriented design principles

---

## Affected Files

- `custom_components/localshift/switch.py` - Private method calls
- `custom_components/localshift/number.py` - Private method calls
- `custom_components/localshift/coordinator.py` - Private methods being accessed

---

## Steps to Reproduce

1. Change internal coordinator method names
2. Observe platform code breaks
3. Try to test platforms in isolation - difficult due to coupling

---

## Proposed Solution

**Option A: Add public API methods**
```python
# In coordinator.py
async def async_recompute_and_evaluate(self) -> None:
    """Public method for triggering recomputation and state evaluation."""
    self._compute_derived_values()
    self._notify_listeners()
    await self.async_evaluate_state_machine()
```

**Option B: Use events/notifications**
Platforms trigger events that coordinator listens to, rather than calling methods directly.

**Option C: Move logic to platforms**
Platforms handle their own state changes and notify coordinator of config updates.

---

## Notes

This is a design issue that creates maintenance burden. Public APIs should be used instead of accessing private implementation details.

---

## Related Items

- Phase 4.3 in CODE_REVIEW_PLAN.md - Private method access review
# Backlog Item Template

**ID:** backlog-med-006  
**Priority:** MED  
**Status:** COMPLETED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16

---

## Summary

Test suite has 29 failing tests due to multiple issues - missing imports, incorrect fixture usage, and API mismatches.

---

## Description

The pytest suite runs but has 29 failing tests out of 44. The failures fall into several categories:

1. **Missing `patch` import** - `test_computation_engine.py` uses `patch` without importing it from `unittest.mock`

2. **Fixtures called directly** - Multiple test files call fixtures like `mock_entry()` directly instead of using them as function parameters. pytest fixtures should be injected, not called.

3. **Coordinator API mismatch** - `test_coordinator.py` tries to create `AmberPowerwallCoordinator` with 4 arguments but the actual constructor only takes 2 (hass and entry)

4. **Test assertion failures** - Some tests have incorrect expected values that don't match current code behavior

---

## Affected Files

- `tests/test_computation_engine.py` - missing patch import, assertion issues
- `tests/test_coordinator.py` - wrong constructor signature  
- `tests/test_forecast_computer.py` - fixture direct calls
- `tests/test_integration.py` - fixture direct calls

---

## Steps to Reproduce

```bash
python -m pytest
# Shows 29 failed, 15 passed
```

---

## Proposed Solution

1. Add `from unittest.mock import patch` to test_computation_engine.py
2. Convert fixture calls like `mock_entry()` to use fixtures as parameters: `def test_xxx(mock_entry):`
3. Fix AmberPowerwallCoordinator instantiation in test_coordinator.py to use correct signature
4. Review and fix assertion expected values in failing tests

---

## Notes

The original issue of "pytest failing due to homeassistant deps" has been fixed by adding homeassistant as a dev dependency. These are pre-existing test bugs that need fixing.

---

## Related Items

- backlog-med-001 (No Test Coverage) - Related to overall test quality

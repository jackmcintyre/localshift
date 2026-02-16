# Dashboard Setup Complexity

**ID:** backlog-low-001  
**Priority:** LOW  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Dashboard YAML requires manual setup of additional helper sensors.

---

## Description

Dashboard YAML is provided but requires:
1. Manual integration into Lovelace
2. Additional YAML helpers (Riemann sum, utility meters) that are NOT created by the component
3. User must manually create these sensors in configuration.yaml

---

## Affected Files

- README.md

---

## Proposed Solution

Consider:
- Creating helper sensors automatically via integration
- Providing clearer step-by-step setup instructions
- Including automation templates for the required sensors
- OR removing dashboard from integration and documenting as separate optional addon

---

## Notes

User experience improvement for new users.

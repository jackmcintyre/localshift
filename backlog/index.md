# Backlog Index

**Last Updated:** 2026-02-16

This is the master index for all backlog items. Each feature has its own dedicated `.md` file in this directory.

## Priority Legend
- 🔴 **CRIT** - Critical (affects core functionality)
- 🟠 **HIGH** - High priority (reliability & robustness)
- 🟡 **MED** - Medium priority (code quality & maintainability)
- ⚪ **LOW** - Low priority (cosmetic & nice-to-have)

## Status Legend
- 📋 **PROPOSED** - New item, not yet started
- 🔄 **IN_PROGRESS** - Currently being worked on
- ✅ **COMPLETED** - Finished and merged

---

## Backlog Items

| ID | Priority | Status | Title |
|----|----------|--------|-------|
| backlog-crit-001 | 🔴 CRIT | 📋 PROPOSED | Force Charge Detection Logic Bug |
| backlog-high-001 | 🟠 HIGH | 📋 PROPOSED | Add Entity Validation on Config Flow |
| backlog-high-002 | 🟠 HIGH | 📋 PROPOSED | Missing Tomorrow's Forecast Integration |
| backlog-high-003 | 🟠 HIGH | 📋 PROPOSED | No Error Handling for Powerwall API Calls |
| backlog-high-004 | 🟠 HIGH | 📋 PROPOSED | Notification Service Not Validated |
| backlog-high-005 | 🟠 HIGH | 📋 PROPOSED | Arbitrary Sleep Delays Between Commands |
| backlog-high-006 | 🟠 HIGH | 📋 PROPOSED | Demand Window Block Logic Priority Issue |
| backlog-high-007 | 🟠 HIGH | 📋 PROPOSED | Inconsistent State Machine Priority Order |
| backlog-med-001 | 🟡 MED | 📋 PROPOSED | No Test Coverage |
| backlog-med-002 | 🟡 MED | 📋 PROPOSED | Time Precision Inconsistency |
| backlog-med-003 | 🟡 MED | 📋 PROPOSED | Decision Log Limited to 50 Entries |
| backlog-med-004 | 🟡 MED | 📋 PROPOSED | Missing Cleanup for Historical Load Cache |
| backlog-med-005 | 🟡 MED | 📋 PROPOSED | Unused Config Option - ALLOW_EXPORT |
| backlog-low-001 | ⚪ LOW | 📋 PROPOSED | Dashboard Setup Complexity |
| backlog-low-002 | ⚪ LOW | 📋 PROPOSED | Hardcoded Personal Notification Service |
| backlog-low-003 | ⚪ LOW | 📋 PROPOSED | Version Inconsistency |
| backlog-low-004 | ⚪ LOW | 📋 PROPOSED | Missing Type Hints for Internal Methods |

---

## Adding New Items

1. Create new file: `backlog/backlog-{priority}-{NNN}.md`
2. Add entry to this index table
3. Use the template from `backlog/TEMPLATE.md`

## File Naming Convention
- Critical: `backlog-crit-XXX.md`
- High: `backlog-high-XXX.md`
- Medium: `backlog-med-XXX.md`
- Low: `backlog-low-XXX.md`

Where XXX is a sequential number starting from 001.

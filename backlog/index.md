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
| backlog-crit-001 | 🔴 CRIT | ✅ COMPLETED | Force Charge Detection Logic Bug |
| backlog-high-001 | 🟠 HIGH | ✅ COMPLETED | Add Entity Validation on Config Flow |
| backlog-high-002 | 🟠 HIGH | ✅ COMPLETED | Missing Tomorrow's Forecast Integration |
| backlog-high-003 | 🟠 HIGH | ✅ COMPLETED | No Error Handling for Powerwall API Calls |
| backlog-high-004 | 🟠 HIGH | ✅ COMPLETED | Notification Service Not Validated |
| backlog-high-005 | 🟠 HIGH | ✅ COMPLETED | Arbitrary Sleep Delays Between Commands |
| backlog-high-006 | 🟠 HIGH | ✅ COMPLETED | Demand Window Block Logic Priority Issue |
| backlog-high-007 | 🟠 HIGH | ✅ COMPLETED | Inconsistent State Machine Priority Order |
| backlog-high-008 | 🟠 HIGH | ✅ COMPLETED | Proactive Export Not Using Peak FIT Prices |
| backlog-high-009 | 🟠 HIGH | 📋 PROPOSED | Solar Curtailment for Negative FIT |
| backlog-high-010 | 🟠 HIGH | ✅ COMPLETED | Pyright Error: Missing _manual_override_set_at Attribute |
| backlog-high-011 | 🟠 HIGH | ✅ COMPLETED | Template Error: None Values in Cost Sensor Attributes |
| backlog-high-012 | 🟠 HIGH | ✅ COMPLETED | Configurable Grid Export Reserve Delta |
| backlog-high-013 | 🟠 HIGH | 📋 PROPOSED | hours_to_dw Calculation Bug (Boost Charging) |
| backlog-high-014 | 🟠 HIGH | ✅ COMPLETED | Grid Import/Export Totals Always Zero in Debug Forecast |
| backlog-med-001 | 🟡 MED | ✅ COMPLETED | No Test Coverage |
| backlog-med-002 | 🟡 MED | 📋 PROPOSED | Time Precision Inconsistency |
| backlog-med-003 | 🟡 MED | 📋 PROPOSED | Decision Log Limited to 50 Entries |
| backlog-med-004 | 🟡 MED | 📋 PROPOSED | Missing Cleanup for Historical Load Cache |
| backlog-med-005 | 🟡 MED | 📋 PROPOSED | Unused Config Option - ALLOW_EXPORT |
| backlog-med-006 | 🟡 MED | ✅ COMPLETED | Test Suite Has 29 Failing Tests |
| backlog-low-001 | ⚪ LOW | 📋 PROPOSED | Dashboard Setup Complexity |
| backlog-low-002 | ⚪ LOW | ✅ COMPLETED | Hardcoded Personal Notification Service |
| backlog-low-003 | ⚪ LOW | 📋 PROPOSED | Version Inconsistency |
| backlog-low-004 | ⚪ LOW | 📋 PROPOSED | Missing Type Hints for Internal Methods |
| backlog-high-015 | 🟠 HIGH | ✅ COMPLETED | Solar FIT Sensor Shows `unknown` State |
| backlog-high-016 | 🟠 HIGH | ✅ COMPLETED | Forecast Consumption Not Blending Recent Load Data |
| backlog-high-018 | 🟠 HIGH | ✅ COMPLETED | Dashboard Entity ID Mismatch - Load Weight & Solar FIT |
| backlog-med-007 | 🟡 MED | ✅ COMPLETED | Recent Load Diagnostic Fields Not Propagated to CoordinatorData |
| backlog-med-008 | 🟡 MED | ✅ COMPLETED | `can_reach_target` Inconsistency Between Legacy and Detailed Forecast |
| backlog-med-009 | 🟡 MED | ✅ COMPLETED | Automated Documentation Generation via Cline |
| backlog-high-019 | 🟠 HIGH | 📋 PROPOSED | Allow DW Entry Under Target with Solar Forecast |
| backlog-low-005 | ⚪ LOW | 📋 PROPOSED | Dashboard Template Shows `None` for Solar Remaining |

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

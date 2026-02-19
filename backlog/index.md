# Backlog Index

**Last Updated:** 2026-02-19

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
| backlog-crit-002 | 🔴 CRIT | ✅ COMPLETED | Missing Unit Tests for State Machine |
| backlog-crit-003 | 🔴 CRIT | ✅ COMPLETED | Silent 0.0 Returns Hide Missing Forecast Data |
| backlog-high-017 | 🟠 HIGH | 📋 PROPOSED | Excess Solar Load Shifting Sensors |
| backlog-high-019 | 🟠 HIGH | ✅ COMPLETED | Day Boundary Bug in Overnight Grid Charging Decision |
| backlog-high-020 | 🟠 HIGH | ✅ COMPLETED | Health Check Missing export_mode Verification |
| backlog-high-021 | 🟠 HIGH | ✅ COMPLETED | PROACTIVE_EXPORT Has 0 Debounce Risk |
| backlog-high-022 | 🟠 HIGH | ✅ COMPLETED | Forecast SOC Stays Flat at Minimum Despite Solar Excess |
| backlog-high-023 | 🟠 HIGH | ✅ COMPLETED | Demand Window Premature Exit Bug |
| backlog-med-003 | 🟡 MED | 📋 PROPOSED | Decision Log Limited to 50 Entries |
| backlog-med-004 | 🟡 MED | ✅ COMPLETED | Missing Cleanup for Historical Load Cache |
| backlog-med-005 | 🟡 MED | 📋 PROPOSED | Unused Config Option - ALLOW_EXPORT |
| backlog-med-011 | 🟡 MED | 📋 PROPOSED | Remove Redundant Grid Import/Export Sensors |
| backlog-med-012 | 🟡 MED | ✅ COMPLETED | Binary Sensors Include Redundant "binary" in Names |
| backlog-med-023 | 🟡 MED | 📋 PROPOSED | Scenario-Based Simulation Framework |
| backlog-med-024 | 🟡 MED | ✅ COMPLETED | Improve Settings Usability with Friendly Names and Help Text |
| backlog-med-025 | 🟡 MED | 📋 PROPOSED | Notification System Improvements |
| backlog-low-001 | ⚪ LOW | ✅ COMPLETED | Dashboard Setup Complexity |
| backlog-low-004 | ⚪ LOW | 📋 PROPOSED | Missing Type Hints for Internal Methods |
| backlog-med-026 | 🟡 MED | 📋 PROPOSED | Comprehensive Test Suite Improvements |

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

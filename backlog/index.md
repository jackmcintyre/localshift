# Backlog Index

**Last Updated:** 2026-02-18

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
| backlog-crit-001 | 🔴 CRIT | ✅ COMPLETED | Proactive Export Fires Below Cost — Unprofitable Overnight Discharge |
| backlog-high-017 | 🟠 HIGH | 📋 PROPOSED | Excess Solar Load Shifting Sensors |
| backlog-high-018 | 🟠 HIGH | ✅ COMPLETED | Forecast SOC Simulation Does Not Respect Minimum SOC |
| backlog-high-019 | 🟠 HIGH | 📋 PROPOSED | Day Boundary Bug in Overnight Grid Charging Decision |
| backlog-med-003 | 🟡 MED | 📋 PROPOSED | Decision Log Limited to 50 Entries |
| backlog-med-004 | 🟡 MED | 📋 PROPOSED | Missing Cleanup for Historical Load Cache |
| backlog-med-005 | 🟡 MED | 📋 PROPOSED | Unused Config Option - ALLOW_EXPORT |
| backlog-med-011 | 🟡 MED | 📋 PROPOSED | Remove Redundant Grid Import/Export Sensors |
| backlog-med-012 | 🟡 MED | 📋 PROPOSED | Binary Sensors Include Redundant "binary" in Names |
| backlog-med-013 | 🟡 MED | ✅ COMPLETED | Hours to DW = 0.0h when inside demand window (display bug) |
| backlog-low-001 | ⚪ LOW | 📋 PROPOSED | Dashboard Setup Complexity |
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

# Backlog Index

**Last Updated:** 2026-02-20

## ⚠️ Migration Notice

**This backlog has been migrated to GitHub Issues.**

All backlog items are now tracked at: https://github.com/jackmcintyre/localshift/issues

### Issue Mapping

| Old Backlog ID | GitHub Issue | Status |
|----------------|--------------|--------|
| backlog-crit-002 | #11 | ✅ CLOSED |
| backlog-crit-003 | #12 | ✅ CLOSED |
| backlog-high-017 | #1 | 📋 OPEN |
| backlog-high-019 | #13 | ✅ CLOSED |
| backlog-high-020 | #14 | ✅ CLOSED |
| backlog-high-022 | #15 | ✅ CLOSED |
| backlog-high-023 | #16 | ✅ CLOSED |
| backlog-med-004 | #17 | ✅ CLOSED |
| backlog-med-005 | #2 | 📋 OPEN |
| backlog-med-011 | #3 | 📋 OPEN |
| backlog-med-012 | #18 | ✅ CLOSED |
| backlog-med-014 | #7 | 📋 OPEN |
| backlog-med-016 | #8 | 📋 OPEN |
| backlog-med-018 | #9 | 📋 OPEN |
| backlog-med-023 | #4 | 📋 OPEN |
| backlog-med-024 | #19 | ✅ CLOSED |
| backlog-med-025 | #20 | ✅ CLOSED |
| backlog-med-026 | #6 | 📋 OPEN |
| backlog-low-001 | #10 | 📋 OPEN |
| backlog-low-004 | #5 | 📋 OPEN |

---

## New Workflow

### Creating New Backlog Items
```bash
gh issue create --title "Title" --body "Description" --label "priority: medium,status: proposed"
```

### Starting Work on an Issue
```bash
gh issue edit {NNN} --add-label "status: in-progress"
git worktree add /Users/jackmcintyre/worktrees/issue-{NNN} -b issue/{NNN}
```

### Completing an Issue
```bash
gh issue close {NNN} --comment "Completed in #{PR_NUMBER}"
```

---

## Priority Labels (GitHub)

- `priority: critical` - Critical (affects core functionality)
- `priority: high` - High priority (reliability & robustness)
- `priority: medium` - Medium priority (code quality & maintainability)
- `priority: low` - Low priority (cosmetic & nice-to-have)

## Status Labels (GitHub)

- `status: proposed` - New item, not yet started
- `status: in-progress` - Currently being worked on
- `status: blocked` - Blocked by dependency or external factor
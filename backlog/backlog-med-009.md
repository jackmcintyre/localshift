# Backlog Item Template

**ID:** backlog-med-009  
**Priority:** MED  
**Status:** COMPLETED  
**Created:** 2026-02-17  
**Updated:** 2026-02-17

---

## Summary

Automated documentation generation using Cline for both inline docstrings and /docs/ directory maintenance.

---

## Description

Implement Cline rules that automatically prompt for documentation updates when editing code. This applies to ALL Python files in the project.

**Scope:**
- All Python files in `custom_components/amber_powerwall/`
- Dual output: Inline docstrings AND `/docs/` directory updates

**Two Main Components:**

1. **Inline Documentation**
   - Check for missing/incomplete docstrings on classes and public methods
   - Add Google-style docstrings matching existing codebase patterns
   - Methods >20 lines should have inline comments for complex logic

2. **/docs/ Directory Maintenance**
   - When editing sensor files → prompt to update `docs/ENTITY_REFERENCE.md`
   - When editing core logic (computation_engine, state_machine, battery_controller) → prompt to update `docs/ARCHITECTURE.md`
   - When making significant logic changes → prompt to add entry to `docs/CHANGE_DETECTION.md`

**Implementation via .clinerules:**

Add documentation rules to `.clinerules` that instruct Cline to check and prompt for documentation updates during normal file editing.

**Documentation Responsibilities Matrix:**

| File Category | Inline Docs | /docs/ Updates |
|---------------|--------------|----------------|
| Sensors (`sensor.py`, `binary_sensor.py`) | ✅ | Update `ENTITY_REFERENCE.md` |
| Core Engine (`computation_engine.py`) | ✅ | Update `ARCHITECTURE.md` |
| State Management (`state_machine.py`) | ✅ | Update `ARCHITECTURE.md` |
| Battery Control (`battery_controller.py`) | ✅ | Update `ARCHITECTURE.md` |
| Config Flow (`config_flow.py`) | ✅ | Update `DEVELOPER_GUIDE.md` |

---

## Affected Files

- `.clinerules` - Add documentation automation rules
- `docs/ARCHITECTURE.md` - Update on core logic changes
- `docs/ENTITY_REFERENCE.md` - Update on sensor changes
- `docs/CHANGE_DETECTION.md` - Update on significant changes
- `docs/DEVELOPER_GUIDE.md` - Update on config flow changes

---

## Proposed Solution

Add the following to `.clinerules`:

```markdown
# Documentation Automation Rules

## Inline Documentation
- When editing any Python file, check for:
  - Missing docstrings on classes and public methods
  - Methods >20 lines without inline comments
- Add Google-style docstrings following existing codebase patterns

## /docs/ Directory Maintenance
- After modifying sensor implementations → Update `docs/ENTITY_REFERENCE.md`
- After modifying core computation logic → Update `docs/ARCHITECTURE.md`
- After architectural changes → Update `docs/CHANGE_DETECTION.md`
- Prompt user to include docs updates when completing edits

## Documentation Prompts
When completing an edit that affects documentation, include:
1. Summary of what changed
2. Updated inline docstrings (if applicable)
3. Suggested /docs/ updates to make
```

---

## Notes

- This is a MED priority item focused on code quality and maintainability
- Uses existing Google-style docstring format already present in codebase
- Starts with simple prompts first (no pre-commit hook blocking commits)
- Can be enhanced later with pre-commit hooks if needed

---

## Related Items

- backlog-low-004: Missing Type Hints for Internal Methods (related code quality)
- backlog-med-002: Time Precision Inconsistency (may need documentation)

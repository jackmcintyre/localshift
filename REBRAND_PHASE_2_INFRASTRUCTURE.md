# Phase 2: Core Infrastructure (Directory & Config)

**Phase:** 2 of 11  
**Status:** NOT STARTED  
**Estimated Time:** 15 minutes

---

## Overview

This phase updates the core infrastructure files that define the integration's identity:
- Directory rename
- Configuration files (manifest.json, pyproject.toml, hacs.json)
- Domain constant (const.py)

**Critical:** This phase must be completed before any Python code changes, as import paths depend on the directory structure.

---

## 2.1 Directory Rename

### Action

```bash
git mv custom_components/amber_powerwall custom_components/localshift
```

### Why Git MV?

Using `git mv` instead of regular `mv` preserves file history in git, which is important for blame/history tracking.

### Verification

```bash
# Verify directory exists
ls -la custom_components/localshift/

# Verify git knows about the move
git status
```

Expected output: `renamed: custom_components/amber_powerwall/... -> custom_components/localshift/...`

### Checklist
- [ ] Executed git mv command
- [ ] Verified new directory exists
- [ ] Verified git status shows rename
- [ ] Old directory no longer exists

---

## 2.2 manifest.json

**File:** `custom_components/localshift/manifest.json`

### Current Content
```json
{
  "domain": "amber_powerwall",
  "name": "Amber Powerwall",
  "codeowners": ["@jackmcintyre"],
  "config_flow": true,
  "dependencies": [],
  "documentation": "https://github.com/jackmcintyre/ha-solar-battery-automation",
  "integration_type": "device",
  "iot_class": "calculated",
  "requirements": [],
  "version": "0.1.0"
}
```

### New Content
```json
{
  "domain": "localshift",
  "name": "LocalShift",
  "codeowners": ["@jackmcintyre"],
  "config_flow": true,
  "dependencies": [],
  "documentation": "https://github.com/jackmcintyre/ha-solar-battery-automation",
  "integration_type": "device",
  "iot_class": "calculated",
  "requirements": [],
  "version": "0.0.2"
}
```

### Changes Required
1. **domain**: `"amber_powerwall"` → `"localshift"`
2. **name**: `"Amber Powerwall"` → `"LocalShift"`
3. **version**: `"0.1.0"` → `"0.0.2"`

### Implementation

**Option 1: Complete file replacement** (recommended for small JSON files)
Use write_to_file with the new content above.

**Option 2: Replace individual fields**
```bash
# Using replace_in_file
# SEARCH/REPLACE for domain
# SEARCH/REPLACE for name
# SEARCH/REPLACE for version
```

### Verification
```bash
# Verify JSON is valid
python3 -c "import json; print(json.load(open('custom_components/localshift/manifest.json')))"

# Should output the new JSON with no errors
```

### Checklist
- [ ] domain updated to "localshift"
- [ ] name updated to "LocalShift"
- [ ] version updated to "0.0.2"
- [ ] JSON syntax validated
- [ ] File saved

---

## 2.3 const.py

**File:** `custom_components/localshift/const.py`

### Current Domain Constant

```python
# -----------------------------------------------------------------------------
# Domain
# -----------------------------------------------------------------------------

DOMAIN = "amber_powerwall"
```

### New Domain Constant

```python
# -----------------------------------------------------------------------------
# Domain
# -----------------------------------------------------------------------------

DOMAIN = "localshift"
```

### Implementation

**SEARCH:**
```python
DOMAIN = "amber_powerwall"
```

**REPLACE:**
```python
DOMAIN = "localshift"
```

### Verification

```bash
# Verify the change
grep -n "DOMAIN =" custom_components/localshift/const.py

# Should show:
# 12:DOMAIN = "localshift"
```

### Impact Analysis

The DOMAIN constant is used throughout the codebase. Changing it here will affect:
- Entity unique IDs (but we're changing those in Phase 3 anyway)
- Config entry creation
- Service registration
- Event names

All of these will automatically use the new domain once this is updated.

### Checklist
- [ ] DOMAIN constant updated
- [ ] No other "amber_powerwall" strings in const.py (verify with grep)
- [ ] File saved

---

## 2.4 pyproject.toml

**File:** `pyproject.toml` (in repository root)

### Current Content (relevant sections)

```toml
[project]
name = "amber-powerwall"
version = "0.0.1"
description = "Amber Powerwall custom component for Home Assistant"
requires-python = ">=3.11"
dependencies = []

[tool.setuptools.packages.find]
include = ["custom_components.amber_powerwall*"]

[tool.ruff.lint.per-file-ignores]
"custom_components/amber_powerwall/*.py" = ["D"]

[tool.vulture]
min_confidence = 80
paths = ["custom_components/amber_powerwall"]
exclude = ["tests/"]

[tool.pyright]
pythonVersion = "3.11"
typeCheckingMode = "basic"
include = ["custom_components/amber_powerwall"]
exclude = [
  "**/__pycache__",
  ".git",
  ".claude",
]
```

### New Content

```toml
[project]
name = "localshift"
version = "0.0.2"
description = "LocalShift battery automation for Home Assistant"
requires-python = ">=3.11"
dependencies = []

[tool.setuptools.packages.find]
include = ["custom_components.localshift*"]

[tool.ruff.lint.per-file-ignores]
"custom_components/localshift/*.py" = ["D"]

[tool.vulture]
min_confidence = 80
paths = ["custom_components/localshift"]
exclude = ["tests/"]

[tool.pyright]
pythonVersion = "3.11"
typeCheckingMode = "basic"
include = ["custom_components/localshift"]
exclude = [
  "**/__pycache__",
  ".git",
  ".claude",
]
```

### Changes Required

1. **[project] section:**
   - name: `"amber-powerwall"` → `"localshift"`
   - version: `"0.0.1"` → `"0.0.2"`
   - description: `"Amber Powerwall custom component..."` → `"LocalShift battery automation..."`

2. **[tool.setuptools.packages.find]:**
   - include: `["custom_components.amber_powerwall*"]` → `["custom_components.localshift*"]`

3. **[tool.ruff.lint.per-file-ignores]:**
   - path: `"custom_components/amber_powerwall/*.py"` → `"custom_components/localshift/*.py"`

4. **[tool.vulture]:**
   - paths: `["custom_components/amber_powerwall"]` → `["custom_components/localshift"]`

5. **[tool.pyright]:**
   - include: `["custom_components/amber_powerwall"]` → `["custom_components/localshift"]`

### Implementation Strategy

**Option 1: SEARCH/REPLACE blocks** (recommended)

```toml
------- SEARCH
name = "amber-powerwall"
version = "0.0.1"
description = "Amber Powerwall custom component for Home Assistant"
=======
name = "localshift"
version = "0.0.2"
description = "LocalShift battery automation for Home Assistant"
+++++++ REPLACE
```

```toml
------- SEARCH
include = ["custom_components.amber_powerwall*"]
=======
include = ["custom_components.localshift*"]
+++++++ REPLACE
```

```toml
------- SEARCH
"custom_components/amber_powerwall/*.py" = ["D"]
=======
"custom_components/localshift/*.py" = ["D"]
+++++++ REPLACE
```

```toml
------- SEARCH
paths = ["custom_components/amber_powerwall"]
=======
paths = ["custom_components/localshift"]
+++++++ REPLACE
```

```toml
------- SEARCH
include = ["custom_components/amber_powerwall"]
=======
include = ["custom_components/localshift"]
+++++++ REPLACE
```

### Verification

```bash
# Verify no old references remain
grep -n "amber_powerwall\|amber-powerwall" pyproject.toml

# Should return: (no matches)

# Verify TOML syntax
python3 -c "import tomli; tomli.load(open('pyproject.toml', 'rb'))"
# Or if tomli not available:
python3 -c "import configparser; c = configparser.ConfigParser(); c.read('pyproject.toml')"
```

### Checklist
- [ ] project.name updated
- [ ] project.version updated to 0.0.2
- [ ] project.description updated
- [ ] setuptools.packages.find.include updated
- [ ] ruff.lint.per-file-ignores path updated
- [ ] vulture.paths updated
- [ ] pyright.include updated
- [ ] TOML syntax validated
- [ ] No old references remain (grep verified)
- [ ] File saved

---

## 2.5 hacs.json

**File:** `hacs.json` (in repository root)

### Current Content

```json
{
  "name": "Amber Powerwall",
  "render_readme": true
}
```

### New Content

```json
{
  "name": "LocalShift",
  "render_readme": true
}
```

### Implementation

**SEARCH:**
```json
  "name": "Amber Powerwall",
```

**REPLACE:**
```json
  "name": "LocalShift",
```

### Verification

```bash
# Verify JSON is valid
python3 -c "import json; print(json.load(open('hacs.json')))"

# Verify change
grep "name" hacs.json
# Should show: "name": "LocalShift",
```

### Checklist
- [ ] name updated to "LocalShift"
- [ ] JSON syntax validated
- [ ] File saved

---

## Phase 2 Completion Checklist

### Directory Structure
- [ ] `custom_components/amber_powerwall/` → `custom_components/localshift/` (git mv)
- [ ] Old directory no longer exists
- [ ] Git knows about the rename

### Files Updated
- [ ] manifest.json: domain, name, version
- [ ] const.py: DOMAIN constant
- [ ] pyproject.toml: all 5 path references
- [ ] hacs.json: name

### Verification Commands Run
- [ ] All JSON files validated
- [ ] grep for old references (should find none)
- [ ] git status shows renames correctly

### Pre-commit Check
```bash
# Run pre-commit on changed files
pre-commit run --files custom_components/localshift/manifest.json \
                       custom_components/localshift/const.py \
                       pyproject.toml \
                       hacs.json
```

- [ ] Pre-commit passes

---

## Common Issues & Solutions

### Issue 1: Git doesn't recognize rename
**Symptom:** `git status` shows delete + add instead of rename

**Solution:**
```bash
git add -A
git status
# Git should now show rename
```

### Issue 2: Import errors after directory rename
**Symptom:** Python can't find modules

**Solution:** This is expected! Imports will be fixed in Phase 4. Don't test Python code yet.

### Issue 3: Pre-commit fails on pyproject.toml
**Symptom:** TOML formatting errors

**Solution:**
```bash
# Let pre-commit auto-fix
pre-commit run --files pyproject.toml

# Review and accept changes
git diff pyproject.toml
```

---

## Next Steps

After completing Phase 2:
1. Verify all checklist items are complete
2. Run verification commands
3. Commit these changes (optional checkpoint):
   ```bash
   git add -A
   git commit -m "Phase 2: Core infrastructure - directory rename and config updates"
   ```
4. Proceed to Phase 3: Entity Naming

**DO NOT** proceed to Phase 3 until all Phase 2 items are verified. The directory rename must be complete first.

---

**Phase Status:** ✅ COMPLETED

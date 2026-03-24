# Simplify Anti-Cycling Cost Stack Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the direct cycle penalty and reduced self-consumption credit from `stage_cost()`, keeping only the futile-cycling penalty as the sole anti-cycling mechanism.

**Architecture:** The change is a focused deletion — remove `cycle_penalty_per_kwh` from `OptimizerConfig`, remove `cycle_penalty` from `ObjectiveTerms`, restore self-consumption credit to full `buy_price`, and clean up all references across source files, config, entities, tests, and docs. No new logic is introduced.

**Spec:** `docs/superpowers/specs/2026-03-25-simplify-anti-cycling-cost-stack-design.md`

**Working directory:** `/config/home/localshift/worktrees/issue-804`

**Run tests with:** `uv run pytest --tb=short -q`

---

## Chunk 1: Core cost model changes

### Task 1: Update `ObjectiveTerms` in `types.py`

**Files:**
- Modify: `custom_components/localshift/engine/types.py`

Remove `cycle_penalty` from the `ObjectiveTerms` dataclass. This will cause compilation errors everywhere the field is used — those are the roadmap for subsequent tasks.

- [ ] **Step 1: Write a failing test that asserts `cycle_penalty` is no longer a field**

In `tests/engine/test_cost.py`, find `test_cycle_penalty_applied` (around line 77). Verify it references `terms.cycle_penalty`. Note: this test currently passes — after this change it should fail.

- [ ] **Step 2: Remove `cycle_penalty` from `ObjectiveTerms`**

In `custom_components/localshift/engine/types.py`, make these changes:

```python
# REMOVE this field from ObjectiveTerms dataclass (around line 261):
cycle_penalty: float = 0.0

# REMOVE from net_cost property (around line 290):
+ self.cycle_penalty

# REMOVE from to_dict() method (around line 303):
"cycle_penalty": self.cycle_penalty,
```

- [ ] **Step 3: Run tests to see the failures cascade**

```bash
uv run pytest --tb=short -q 2>&1 | head -60
```

Expected: Multiple failures referencing `cycle_penalty` — this is the expected state. Do NOT fix yet.

---

### Task 2: Remove cycle penalty from `cost.py`

**Files:**
- Modify: `custom_components/localshift/engine/cost.py`

- [ ] **Step 1: Read the current `stage_cost()` function**

Read `custom_components/localshift/engine/cost.py` lines 11-142 to understand the current structure.

- [ ] **Step 2: Remove cycle penalty calculation and restore full SC credit**

Make these changes to `cost.py`:

a) Remove cycle penalty lines (around lines 51-52):
```python
# REMOVE:
cycle_kwh = grid_import_kwh + grid_export_kwh
cycle_penalty = cycle_kwh * config.cycle_penalty_per_kwh
```

b) Remove the comment explaining reduced SC credit (find the comment around line 81 that mentions "subtract cycle_penalty_per_kwh to avoid subsidizing marginal cycling" and remove it).

c) Restore full buy_price SC credit (around lines 104-107):
```python
# BEFORE:
sc_multiplier = max(0.0, slot.buy_price - config.cycle_penalty_per_kwh)
self_consumption_value = battery_for_load * sc_multiplier

# AFTER:
self_consumption_value = battery_for_load * slot.buy_price
```

d) Remove `cycle_penalty=cycle_penalty` from the `ObjectiveTerms(...)` return (around line 136).

- [ ] **Step 3: Run tests to confirm no new failures (same count as Task 1)**

```bash
uv run pytest --tb=short -q 2>&1 | head -60
```

---

### Task 3: Remove `cycle_penalty_per_kwh` from `OptimizerConfig`

**Files:**
- Modify: `custom_components/localshift/engine/types.py`

- [ ] **Step 1: Remove the field**

In `custom_components/localshift/engine/types.py`, remove from `OptimizerConfig`:
```python
# REMOVE (around line 192):
cycle_penalty_per_kwh: float = 0.08  # $/kWh (battery wear + round-trip efficiency)
```

- [ ] **Step 2: Run tests to see remaining failures**

```bash
uv run pytest --tb=short -q 2>&1 | head -80
```

Expected: Failures in tests that pass `cycle_penalty_per_kwh=...` to `OptimizerConfig(...)`, plus failures in `optimizer_runner.py` which reads the config option.

---

### Task 4: Remove `CONF_CYCLE_PENALTY` and `DEFAULT_CYCLE_PENALTY` from `const.py`

**Files:**
- Modify: `custom_components/localshift/const.py`

- [ ] **Step 1: Remove constants**

In `custom_components/localshift/const.py`:
```python
# REMOVE:
CONF_CYCLE_PENALTY = "cycle_penalty"
DEFAULT_CYCLE_PENALTY = 0.08

# REMOVE from THRESHOLD_RANGES dict:
CONF_CYCLE_PENALTY: {"min": 0.00, "max": 0.20, "step": 0.01, "unit": "$/kWh"},
```

- [ ] **Step 2: Run to see import failures**

```bash
uv run pytest --tb=short -q 2>&1 | grep "ImportError\|cannot import" | head -20
```

---

### Task 5: Remove cycle penalty from runtime source files

**Files:**
- Modify: `custom_components/localshift/engine/optimizer_runner.py`
- Modify: `custom_components/localshift/number.py`
- Modify: `custom_components/localshift/config_flow/__init__.py`
- Modify: `custom_components/localshift/computation_engine.py`

- [ ] **Step 1: Remove from `optimizer_runner.py`**

Read `optimizer_runner.py` around lines 295-350. Remove:
```python
# REMOVE import (at top of file):
from ..const import ..., CONF_CYCLE_PENALTY, DEFAULT_CYCLE_PENALTY, ...

# REMOVE (around line 297):
cycle_penalty = float(config_options.get(CONF_CYCLE_PENALTY, DEFAULT_CYCLE_PENALTY))

# REMOVE from OptimizerConfig constructor (around line 346):
cycle_penalty_per_kwh=cycle_penalty,
```

- [ ] **Step 2: Remove from `number.py`**

Read `number.py` around line 50. Remove the cycle penalty entry from `NUMBER_DEFINITIONS`:
```python
# REMOVE this entry (around line 50):
(CONF_CYCLE_PENALTY, "Cycle Penalty", DEFAULT_CYCLE_PENALTY),
```
Also remove `CONF_CYCLE_PENALTY` and `DEFAULT_CYCLE_PENALTY` from the import at the top of the file.

- [ ] **Step 3: Remove from `config_flow/__init__.py`**

Read the file around lines 697-698 and 771-783. Remove:
```python
# REMOVE from defaults dict (around line 697-698):
CONF_CYCLE_PENALTY: current.get(CONF_CYCLE_PENALTY, DEFAULT_CYCLE_PENALTY),

# REMOVE from _build_advanced_schema() (around line 772-773):
vol.Optional(CONF_CYCLE_PENALTY, default=values.get(CONF_CYCLE_PENALTY, DEFAULT_CYCLE_PENALTY)): ...,
```
Also remove `CONF_CYCLE_PENALTY` and `DEFAULT_CYCLE_PENALTY` from the import at the top.

- [ ] **Step 4: Remove from `computation_engine.py`**

Read around lines 555-556. Remove:
```python
# REMOVE:
CONF_CYCLE_PENALTY: self.entry.options.get(CONF_CYCLE_PENALTY, DEFAULT_CYCLE_PENALTY),
```
Also remove from imports.

- [ ] **Step 5: Run tests to see remaining failures**

```bash
uv run pytest --tb=short -q 2>&1 | head -80
```

Expected: Only test failures remain (no more source code errors). This completes the production code changes.

- [ ] **Step 6: Commit production code changes**

```bash
git add custom_components/
git commit -m "refactor: remove cycle penalty mechanisms from cost model (#804)

Keep only futile-cycling penalty as the sole anti-cycling mechanism.
Remove cycle_penalty_per_kwh from OptimizerConfig, cycle_penalty from
ObjectiveTerms, and restore self-consumption credit to full buy_price.
Removes number entity, config flow field, and all runtime references."
```

---

## Chunk 2: Test updates

### Task 6: Fix `tests/engine/test_cost.py`

**Files:**
- Modify: `tests/engine/test_cost.py`

- [ ] **Step 1: Read the failing tests**

Read `tests/engine/test_cost.py` and identify all tests referencing `cycle_penalty` or `cycle_penalty_per_kwh`.

- [ ] **Step 2: Update tests**

Key changes:
- Remove `test_cycle_penalty_applied` test (around line 77) — the field no longer exists
- Around line 153, remove: `assert hasattr(terms, "cycle_penalty")`
- Around lines 303, 316, 327 — remove `cycle_penalty=...` from all `ObjectiveTerms(...)` constructor calls and update expected `net_cost` calculations accordingly
- Update any `OptimizerConfig(cycle_penalty_per_kwh=...)` calls — remove the kwarg
- Update SC credit assertions: the expected value is now `battery_for_load * slot.buy_price` not `battery_for_load * (buy_price - 0.08)`

- [ ] **Step 3: Run just this test file**

```bash
uv run pytest tests/engine/test_cost.py -v
```

Expected: PASS

---

### Task 7: Fix `tests/test_cost.py`

**Files:**
- Modify: `tests/test_cost.py`

- [ ] **Step 1: Read and update**

Remove `cycle_penalty` assertion (around line 83). Update SC credit test comment (around line 42 which mentions `cycle_penalty $0.08`).

- [ ] **Step 2: Run**

```bash
uv run pytest tests/test_cost.py -v
```

Expected: PASS

---

### Task 8: Fix `tests/test_self_consumption_philosophy.py`

**Files:**
- Modify: `tests/test_self_consumption_philosophy.py`

This file has three changes:

- [ ] **Step 1: Remove `cycle_penalty_per_kwh` fixture param and direct usage**

Around line 60, the `make_config()` helper has `cycle_penalty_per_kwh: float = 0.08` as a parameter. Remove this parameter and its usage in the `OptimizerConfig(...)` call around line 70.

Also find and remove `cycle_penalty_per_kwh=0.08` at line 223 (inside `test_stage_cost_applies_anti_cycling_penalties` which calls `_make_config()` directly).

- [ ] **Step 2: Remove the `cycle_penalty` assertion**

Around line 247:
```python
# REMOVE:
assert terms.cycle_penalty > 0, "Cycle penalty should be positive"
```

- [ ] **Step 3: Update `test_penalties_are_not_recently_reduced()`**

Around lines 271-296, this test imports `DEFAULT_CYCLE_PENALTY` and asserts it's >= 0.08. Remove the `DEFAULT_CYCLE_PENALTY` import and assertion. Keep the `DEFAULT_TARGET_PENALTY` assertion — it's still valid.

```python
# BEFORE:
from custom_components.localshift.const import (
    DEFAULT_CYCLE_PENALTY,
    DEFAULT_TARGET_PENALTY,
)
assert DEFAULT_CYCLE_PENALTY >= 0.08, ...

# AFTER:
from custom_components.localshift.const import (
    DEFAULT_TARGET_PENALTY,
)
# Remove the cycle penalty assertion entirely
```

Consider adding a replacement assertion that verifies `DEFAULT_TARGET_PENALTY` is still within bounds (it should already be there).

- [ ] **Step 4: Run**

```bash
uv run pytest tests/test_self_consumption_philosophy.py -v
```

Expected: PASS

---

### Task 9: Fix `tests/test_solar_opportunity_penalty.py`

**Files:**
- Modify: `tests/test_solar_opportunity_penalty.py`

- [ ] **Step 1: Identify all SC credit assertions**

Search for references to `buy_price - cycle_penalty` and `$0.06` and `$0.14 - $0.08` around lines 530-576. These tests assert the old reduced SC credit formula.

- [ ] **Step 2: Update SC credit expectations**

The self-consumption value should now be `battery_for_load * slot.buy_price`. For example:
- Old expected: `1.0 * (0.14 - 0.08) = 0.06`
- New expected: `1.0 * 0.14 = 0.14`

Update test assertions and comments accordingly.

- [ ] **Step 3: Remove `cycle_penalty_per_kwh` from any `OptimizerConfig(...)` calls in this file**

- [ ] **Step 4: Update stale comments**

Around lines 812 and 856, find and remove/update any comments that reference `cycle_penalty` as part of the SC credit explanation.

- [ ] **Step 5: Run**

```bash
uv run pytest tests/test_solar_opportunity_penalty.py -v
```

Expected: PASS

---

### Task 10: Fix `tests/test_futile_cycling_penalty.py`

**Files:**
- Modify: `tests/test_futile_cycling_penalty.py`

- [ ] **Step 1: Update SC credit assertions**

Around lines 487-510, find assertions about SC value being `0.25 * (0.14 - cycle_penalty)`. Update to `0.25 * 0.14`.

Remove `cycle_penalty_per_kwh` from any `OptimizerConfig(...)` calls.

Remove any `ObjectiveTerms(cycle_penalty=...)` calls — remove the kwarg (around lines 150-152).

- [ ] **Step 2: Run**

```bash
uv run pytest tests/test_futile_cycling_penalty.py -v
```

Expected: PASS

---

### Task 11: Fix `tests/test_optimizer_runner_integration.py`

**Files:**
- Modify: `tests/test_optimizer_runner_integration.py`

- [ ] **Step 1: Remove cycle_penalty tests**

Around lines 200-230, find and remove:
- `test_config_cycle_penalty_default` (asserts `config.cycle_penalty_per_kwh == 0.08`)
- `test_config_cycle_penalty_from_options` (asserts reading `cycle_penalty` from options)
- `test_config_cycle_penalty_uses_default_when_not_in_options`

- [ ] **Step 2: Run**

```bash
uv run pytest tests/test_optimizer_runner_integration.py -v
```

Expected: PASS

---

### Task 12: Fix remaining test files (simple `cycle_penalty_per_kwh` kwarg removal)

**Files:**
- Modify: `tests/test_optimizer_dp_solve.py` (lines 758, 2439)
- Modify: `tests/engine/test_terminal_cost_accuracy.py` (line 430)
- Modify: `tests/engine/test_terminal_cost_regression.py` (line 112)
- Modify: `tests/engine/test_reason_codes.py` (lines 271, 345)
- Modify: `tests/engine/test_solar.py` (line 432)
- Modify: `tests/test_switching_penalty.py` (line 35)
- Modify: `tests/test_optimizer_scaffold.py` (line 242)

These files simply need `cycle_penalty_per_kwh=...` removed from `OptimizerConfig(...)` constructor calls, and `cycle_penalty=...` removed from any `ObjectiveTerms(...)` calls.

- [ ] **Step 1: Fix `test_optimizer_dp_solve.py`**

Remove `cycle_penalty=0.05` (line 758) and `cycle_penalty=0.0` (line 2439) from `OptimizerConfig(...)` calls. Note: these use the short form `cycle_penalty=` not `cycle_penalty_per_kwh=` — check what field name is used.

- [ ] **Step 2: Fix remaining files**

In each file, remove `cycle_penalty_per_kwh=0.08` (or any value) from `OptimizerConfig(...)` calls. For `test_optimizer_scaffold.py` line 242, remove `cycle_penalty=0.05` from `ObjectiveTerms(...)`.

- [ ] **Step 3: Run all tests**

```bash
uv run pytest --tb=short -q
```

Expected: All tests pass (apart from the 6 pre-existing errors in `test_solcast.py`).

- [ ] **Step 4: Commit test updates**

```bash
git add tests/
git commit -m "test: update tests to remove cycle_penalty references (#804)"
```

---

## Chunk 3: Documentation and entity count updates

### Task 13: Update `docs/PLANNING_MODEL.md`

**Files:**
- Modify: `docs/PLANNING_MODEL.md`

- [ ] **Step 1: Read the cost model section**

Find the cost model table (around line 97) and the formula (around line 109).

- [ ] **Step 2: Remove cycle_penalty row and update formula**

Remove the `cycle_penalty` row from the cost table:
```markdown
# REMOVE:
| `cycle_penalty` | `(import + export) × $0.08/kWh` | Anti-wear, discourages marginal cycling | L1757 |
```

Remove from the formula display (around line 109):
```
# REMOVE:
    + cycle_penalty
```

Update the `OptimizerConfig` reference (around line 270):
```python
# REMOVE:
cycle_penalty_per_kwh: float = 0.08              # $/kWh (battery wear + efficiency)
```

Also update the tuning guideline example (around line 277):
```markdown
# BEFORE:
- **Increase penalty** → Stronger discouragement (e.g., higher cycle penalty = less cycling)

# AFTER:
- **Increase penalty** → Stronger discouragement (e.g., higher switching penalty = fewer mode changes)
```

Add a note explaining that anti-cycling is now handled solely by the futile-cycling penalty.

---

### Task 14: Update `docs/ENTITY_REFERENCE.md`

**Files:**
- Modify: `docs/ENTITY_REFERENCE.md`

- [ ] **Step 1: Update the overview counts**

Around lines 7-16, update:
- Header line: `**60 entities**` → `**59 entities**`
- Table row: `| Numbers | 6 |` → `| Numbers | 5 |`

- [ ] **Step 2: Remove the cycle_penalty entity section**

Find `### 5. number.localshift_cycle_penalty` (around line 1317) and remove the entire section.

- [ ] **Step 3: Renumber the following section**

After removing section 5, the next section `### 6. number.localshift_target_shortfall_penalty` (around line 1339) becomes section 5. Update its heading.

- [ ] **Step 4: Remove from example config**

Find the example config block (around line 1515) that contains `cycle_penalty: 0.0`. Remove that line.

---

### Task 15: Update `AGENTS.md` (root) entity count

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Find and update entity count**

Note: `AGENTS.md` has a stale count (Numbers 4, Total 59); `ENTITY_REFERENCE.md` is the source of truth (Numbers 6, Total 60).

Find the line in `AGENTS.md`: `Sensors 33 | Binary Sensors 10 | Switches 8 | Numbers 4 | Selects 2 | Buttons 2 (Total 59)`

Update to: `Sensors 33 | Binary Sensors 10 | Switches 8 | Numbers 5 | Selects 2 | Buttons 2 (Total 59)`

(Numbers 6 → 5, Total stays 59 because AGENTS.md was already 1 behind ENTITY_REFERENCE.md)

---

### Task 16: Update `.opencode/skills/dp-optimizer-modification/SKILL.md`

**Files:**
- Modify: `.opencode/skills/dp-optimizer-modification/SKILL.md`

- [ ] **Step 1: Remove cycle_penalty from cost model reference**

Find the cost model table (around line 75) and formula (around line 85). Remove the `cycle_penalty` row and `+ cycle_penalty` from the formula.

---

### Task 17: Final test run, lint check, and commit docs

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest --tb=short -q
```

Expected: All tests pass (6 pre-existing errors in `test_solcast.py` acceptable).

- [ ] **Step 2: Run lint**

```bash
uv run ruff check custom_components/localshift
```

Expected: No new errors introduced by this change (264 pre-existing errors are acceptable).

- [ ] **Step 3: Commit docs**

```bash
git add docs/ AGENTS.md .opencode/
git commit -m "docs: update cost model docs and entity references for #804"
```

---

## Chunk 4: PR

### Task 18: Create pull request

- [ ] **Step 1: Push branch**

```bash
git push -u origin issue/804
```

- [ ] **Step 2: Create PR**

```bash
ISSUE_NUM=804
gh pr create --base test --title "refactor: simplify anti-cycling cost stack (#804)" --body "$(cat <<'EOF'
## Summary

Removes two redundant anti-cycling mechanisms from the optimizer cost model, keeping only the context-aware futile-cycling penalty.

**Removed:**
- Direct cycle penalty (`cycle_penalty_per_kwh` × all grid throughput)
- Reduced self-consumption credit (`buy_price - cycle_penalty` instead of full `buy_price`)

**Kept:**
- Futile-cycling penalty (only fires when forward simulation shows energy would drain before a useful period)

## Why

The two removed mechanisms double-counted the same round-trip cost. A useful overnight pre-charge that saves 5c net (buy 17c, use at 22c) was taxed 16c by the combined penalties, making it look like a net loss. This suppressed genuinely beneficial charging.

With #801, #802, and #805 resolved, the planner now has accurate load data and solar credit. The futile-cycling penalty can do its job without a blunt fallback.

## Changes

- `engine/cost.py` — simplified `stage_cost()`
- `engine/types.py` — removed `cycle_penalty_per_kwh` from `OptimizerConfig`, `cycle_penalty` from `ObjectiveTerms`
- `const.py`, `optimizer_runner.py`, `number.py`, `config_flow/`, `computation_engine.py` — removed all cycle_penalty references
- Tests updated throughout
- Docs updated: `PLANNING_MODEL.md`, `ENTITY_REFERENCE.md`, `AGENTS.md`, skill file

## Related Issue
Closes #804

## Testing
- [ ] All tests pass
- [ ] No new lint errors
- [ ] Overnight SOC monitoring recommended post-deploy
EOF
)"
```

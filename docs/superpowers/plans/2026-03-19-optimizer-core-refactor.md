# Optimizer Core Refactor Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `engine/core.py` into focused helper modules, remove duplicated logic, and keep `DPPlanner` as the public entrypoint with identical behavior.

**Architecture:** `core.py` retains solver orchestration and DP table logic. Specialized helpers (transitions, negative-FIT, penalties, reason codes, solar) move into dedicated modules and are called from `core.py`. `cost.py` and `constraints.py` remain the single sources of truth for cost/feasibility logic.

**Tech Stack:** Python 3.13+, Home Assistant integration, pytest, localshift DP optimizer.

---

## File Structure Map

**Create:**
- `custom_components/localshift/engine/transitions.py`
- `custom_components/localshift/engine/negative_fit.py`
- `custom_components/localshift/engine/penalties.py`
- `custom_components/localshift/engine/reason_codes.py`
- `custom_components/localshift/engine/solar.py`

**Modify:**
- `custom_components/localshift/engine/core.py`
- `custom_components/localshift/engine/optimizer_dp.py`
- `custom_components/localshift/engine/cost.py`
- `custom_components/localshift/engine/constraints.py`
- `tests/engine/test_core.py`
- `tests/engine/test_terminal_cost_accuracy.py`
- `tests/test_optimizer_dp_solve.py`
- `tests/test_optimizer_hard_constraint.py`
- `tests/test_optimizer_self_consumption.py`

**Optional (only if required):**
- `custom_components/localshift/engine/__init__.py` (only if exports need updates)

## Chunk 1: Inventory + Duplication Cleanup

### Task 1: Inventory call sites

- [ ] **Step 0: Verify worktree and branch**

Run:
- `git branch --show-current`
- `git worktree list`
Expected: branch is `issue/793`, and current path is listed as a worktree.

- [ ] **Step 0.05: Read documentation index**

Read: `docs/INDEX.md`
Confirm: optimizer changes must read `docs/PLANNING_MODEL.md`.

- [ ] **Step 0.1: Read required optimizer doc**

Read: `docs/PLANNING_MODEL.md`
Confirm: hard constraints in `feasible_actions()`, soft penalties in `stage_cost()`, deadlines in `terminal_cost()`, optimizer remains pure/deterministic.

- [ ] **Step 0.2: Read suggested optimizer context (optional but recommended)**

Read: `docs/OPTIMIZER_DP_ROLLOUT.md`

- [ ] **Step 1: Run symbol search for call-site inventory**

Run:
- `symdex_search_text("DPPlanner.stage_cost")`
- `symdex_search_text("DPPlanner.terminal_cost")`
- `symdex_search_text("DPPlanner.feasible_actions")`
- `symdex_search_text("_determine_export_actions")`
- `symdex_search_text("_check_global_solar_sufficiency")`
- `symdex_search_text("check_global_solar_sufficiency")`
- `symdex_search_text("_transition_")`
- `symdex_search_text("_classify_reason")`
- `symdex_search_text("_derive_negative_fit_avoidance_context")`

Expected: capture all matches for later migration.

### Task 2: Remove duplicated DPPlanner static methods

**Files:**
- Modify: `custom_components/localshift/engine/core.py`
- Modify: `custom_components/localshift/engine/optimizer_dp.py`
- Modify: `tests/engine/test_core.py`
- Modify: `tests/engine/test_terminal_cost_accuracy.py`

- [ ] **Step 1: Write failing test to enforce wrapper removal**

```python
def test_dpplanner_no_longer_exposes_cost_and_constraints_wrappers():
    from custom_components.localshift.engine.core import DPPlanner

    assert not hasattr(DPPlanner, "stage_cost")
    assert not hasattr(DPPlanner, "terminal_cost")
    assert not hasattr(DPPlanner, "feasible_actions")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_core.py::test_dpplanner_no_longer_exposes_cost_and_constraints_wrappers -v`
Expected: FAIL until the wrappers are removed from `DPPlanner`.

- [ ] **Step 3: Implement duplication cleanup**

Update `core.py` to remove:
- `DPPlanner.stage_cost()`
- `DPPlanner.terminal_cost()`
- `DPPlanner.feasible_actions()`
- `DPPlanner._determine_export_actions()`
- `DPPlanner._check_global_solar_sufficiency()`

Update call sites to import:
- `stage_cost`, `terminal_cost` from `engine.cost`
- `feasible_actions`, `check_global_solar_sufficiency` from `engine.constraints`

Ensure `optimizer_dp.py` only re-exports `engine.cost` and `engine.constraints` functions and does **not** re-export removed `DPPlanner` wrappers.

Update tests importing those static methods to import from the standalone modules. Update **all** matches found in the inventory step, not just the listed files.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/engine/test_core.py tests/engine/test_terminal_cost_accuracy.py -v`
Expected: PASS

Note: coverage and full verification are handled in Chunk 4.

- [ ] **Step 5: Commit**

```bash
git add -A custom_components/localshift/engine tests
git commit -m "refactor: remove DPPlanner cost/constraint wrappers"
```

## Chunk 2: Extract Transitions + Negative-FIT

### Task 3: Extract transition helpers

**Files:**
- Create: `custom_components/localshift/engine/transitions.py`
- Modify: `custom_components/localshift/engine/core.py`
- Test: `tests/test_optimizer_dp_solve.py`

Pre-step: verify worktree/branch (see Chunk 1 Step 0). If not already done, run `git branch --show-current` and `git worktree list` and confirm not on `main`/`test`.
Pre-step: documentation check was completed in Chunk 1 (docs index + planning model). If not done, read `docs/INDEX.md` then `docs/PLANNING_MODEL.md` before proceeding.

- [ ] **Step 0.5: Inventory call sites for penalty helpers**

Run:
- `symdex_search_text("_get_solar_opportunity_penalty_factor")`
- `symdex_search_text("_get_futile_cycling_penalty_factor")`
Expected: identify all call sites to update when moving helpers.

- [ ] **Step 1: Write failing test for transitions module behavior**

```python
def test_transition_returns_expected_tuple_shape(sample_slot, default_config):
    from custom_components.localshift.engine.transitions import transition
    from custom_components.localshift.engine.types import PlannerAction

    result = transition(50.0, PlannerAction.HOLD, sample_slot, default_config)
    assert isinstance(result, tuple)
    assert len(result) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_optimizer_dp_solve.py::test_transition_returns_expected_tuple_shape -v`
Expected: FAIL with `ModuleNotFoundError` or missing function

- [ ] **Step 3: Move transition logic into new module**

Create `transitions.py` and move:
- `transition()`
- `_transition_hold()`
- `_transition_hold_surplus()`
- `_transition_hold_deficit()`
- `_transition_charge_grid()`
- `_charge_grid_with_solar()`
- `_charge_grid_with_deficit()`
- `_clip_charge_to_max_soc()`
- `_transition_export()`

Ensure `transitions.py` starts with `from __future__ import annotations`.

Update `core.py` to import and call `transition()`. Ensure any constants/types used by moved helpers are imported in `transitions.py` to avoid circular dependencies.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_optimizer_dp_solve.py::test_transition_returns_expected_tuple_shape -v`
Expected: PASS

Note: coverage verification is handled in Chunk 4.

- [ ] **Step 5: Commit**

```bash
git branch --show-current  # must not be main/test
git add custom_components/localshift/engine/transitions.py \
  custom_components/localshift/engine/core.py \
  tests/test_optimizer_dp_solve.py
git commit -m "refactor: move DP transitions to module"
```

### Task 4: Extract negative-FIT helpers

**Files:**
- Create: `custom_components/localshift/engine/negative_fit.py`
- Modify: `custom_components/localshift/engine/core.py`
- Test: `tests/engine/test_core.py`

Pre-step: verify worktree/branch (see Chunk 1 Step 0). If not already done, run `git branch --show-current` and `git worktree list` and confirm not on `main`/`test`.
Pre-step: documentation check was completed in Chunk 1 (docs index + planning model). If not done, read `docs/INDEX.md` then `docs/PLANNING_MODEL.md` before proceeding.

- [ ] **Step 0.5: Inventory call sites for reason helpers**

Run:
- `symdex_search_text("_classify_reason")`
- `symdex_search_text("_classify_hold_reason")`
- `symdex_search_text("_classify_export_reason")`
- `symdex_search_text("_classify_charge_reason")`
- `symdex_search_text("_is_target_shortfall_risk")`
Expected: identify all call sites to update when moving helpers.

- [ ] **Step 1: Write failing test for negative-FIT module import**

```python
def test_negative_fit_derivation_export():
    from custom_components.localshift.engine.negative_fit import _derive_negative_fit_avoidance_context

    assert callable(_derive_negative_fit_avoidance_context)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_core.py::test_negative_fit_derivation_export -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Move negative-FIT helpers into new module**

Create `negative_fit.py` and move:
- `_find_risk_window()`
- `_compute_required_headroom()`
- `_compute_recovery_by_slot()`
- `_compute_floor_by_slot()`
- `_compute_recoverability_floor_pct()`
- `_derive_negative_fit_avoidance_context()`

Ensure `negative_fit.py` starts with `from __future__ import annotations`.

Update `core.py` to call `_derive_negative_fit_avoidance_context()` from the new module. Ensure any constants/types used by moved helpers are imported in `negative_fit.py` to avoid circular dependencies.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/engine/test_core.py::test_negative_fit_derivation_export -v`
Expected: PASS

Note: coverage verification is handled in Chunk 4.

- [ ] **Step 5: Commit**

```bash
git branch --show-current  # must not be main/test
git add custom_components/localshift/engine/negative_fit.py \
  custom_components/localshift/engine/core.py \
  tests/engine/test_core.py
git commit -m "refactor: extract negative-FIT helpers"
```

## Chunk 3: Extract Penalties + Reason Codes + Solar

### Task 5: Extract penalty-factor helpers

**Files:**
- Create: `custom_components/localshift/engine/penalties.py`
- Modify: `custom_components/localshift/engine/core.py`
- Test: `tests/test_optimizer_dp_solve.py`

Pre-step: verify worktree/branch (see Chunk 1 Step 0). If not already done, run `git branch --show-current` and `git worktree list` and confirm not on `main`/`test`.
Pre-step: documentation check was completed in Chunk 1 (docs index + planning model). If not done, read `docs/INDEX.md` then `docs/PLANNING_MODEL.md` before proceeding.

- [ ] **Step 0.5: Inventory call sites for penalty helpers**

Run:
- `symdex_search_text("_get_solar_opportunity_penalty_factor")`
- `symdex_search_text("_get_futile_cycling_penalty_factor")`
Expected: identify all call sites to update when moving helpers.

- [ ] **Step 1: Write failing test for penalty helpers**

```python
def test_penalty_helpers_exported():
    from custom_components.localshift.engine.penalties import (
        _get_solar_opportunity_penalty_factor,
        _get_futile_cycling_penalty_factor,
    )

    assert callable(_get_solar_opportunity_penalty_factor)
    assert callable(_get_futile_cycling_penalty_factor)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_optimizer_dp_solve.py::test_penalty_helpers_exported -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Move penalty helpers into new module**

Create `penalties.py` and move:
- `_get_solar_opportunity_penalty_factor()`
- `_get_futile_cycling_penalty_factor()`

Ensure `penalties.py` starts with `from __future__ import annotations` and keeps type hints.

Update `core.py` and any other call sites found in Step 0.5 to import from the new module.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_optimizer_dp_solve.py::test_penalty_helpers_exported -v`
Expected: PASS

Note: coverage verification is handled in Chunk 4.

- [ ] **Step 5: Commit**

```bash
git branch --show-current  # must not be main/test
git add custom_components/localshift/engine/penalties.py \
  custom_components/localshift/engine/core.py \
  tests/test_optimizer_dp_solve.py
git commit -m "refactor: move penalty factor helpers"
```

### Task 6: Extract reason code helpers

**Files:**
- Create: `custom_components/localshift/engine/reason_codes.py`
- Modify: `custom_components/localshift/engine/core.py`
- Test: `tests/engine/test_core.py`

Pre-step: verify worktree/branch (see Chunk 1 Step 0). If not already done, run `git branch --show-current` and `git worktree list` and confirm not on `main`/`test`.
Pre-step: documentation check was completed in Chunk 1 (docs index + planning model). If not done, read `docs/INDEX.md` then `docs/PLANNING_MODEL.md` before proceeding.

- [ ] **Step 1: Write failing test for reason code helpers**

```python
def test_reason_code_helpers_exported():
    from custom_components.localshift.engine.reason_codes import _classify_reason

    assert callable(_classify_reason)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_core.py::test_reason_code_helpers_exported -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Move reason code helpers into new module**

Create `reason_codes.py` and move:
- `_classify_reason()`
- `_classify_hold_reason()`
- `_classify_export_reason()`
- `_classify_charge_reason()`
- `_is_target_shortfall_risk()`

Ensure `reason_codes.py` starts with `from __future__ import annotations` and keeps type hints.

Update `core.py` and any other call sites found in Step 0.5 to import from the new module.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/engine/test_core.py::test_reason_code_helpers_exported -v`
Expected: PASS

Note: coverage verification is handled in Chunk 4.

- [ ] **Step 5: Commit**

```bash
git branch --show-current  # must not be main/test
git add custom_components/localshift/engine/reason_codes.py \
  custom_components/localshift/engine/core.py \
  tests/engine/test_core.py
git commit -m "refactor: extract reason code helpers"
```

### Task 7: Extract solar helpers

**Files:**
- Create: `custom_components/localshift/engine/solar.py`
- Modify: `custom_components/localshift/engine/core.py`
- Test: `tests/engine/test_core.py`

Pre-step: verify worktree/branch (see Chunk 1 Step 0). If not already done, run `git branch --show-current` and `git worktree list` and confirm not on `main`/`test`.
Pre-step: documentation check was completed in Chunk 1 (docs index + planning model). If not done, read `docs/INDEX.md` then `docs/PLANNING_MODEL.md` before proceeding.

- [ ] **Step 1: Write failing test for solar helpers**

```python
def test_solar_helpers_exported():
    from custom_components.localshift.engine.solar import _can_solar_reach_target

    assert callable(_can_solar_reach_target)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_core.py::test_solar_helpers_exported -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Move solar helpers into new module**

Create `solar.py` and move:
- `_can_solar_reach_target()`
- `_projected_solar_soc_gain_pct()`
- `_projected_solcast_gain_pct()`
- `_get_forecast_accuracy()`

Ensure `solar.py` starts with `from __future__ import annotations` and keeps type hints.

Update `core.py` and any other call sites found in Step 0.5 to import and call these helpers.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/engine/test_core.py::test_solar_helpers_exported -v`
Expected: PASS

Note: coverage verification is handled in Chunk 4.

- [ ] **Step 5: Commit**

```bash
git branch --show-current  # must not be main/test
git add custom_components/localshift/engine/solar.py \
  custom_components/localshift/engine/core.py \
  tests/engine/test_core.py
git commit -m "refactor: extract solar helpers"
```

## Chunk 4: Full Verification and Cleanup

### Task 8: Run full optimizer test set

**Files:**
- Test: `tests/engine/test_core.py`
- Test: `tests/engine/test_terminal_cost_accuracy.py`
- Test: `tests/test_optimizer_dp_solve.py`
- Test: `tests/test_optimizer_hard_constraint.py`
- Test: `tests/test_optimizer_self_consumption.py`

- [ ] **Step 0: Verify worktree and branch**

Run:
- `git branch --show-current`
- `git worktree list`
Expected: branch is `issue/793`, and current path is listed as a worktree.

- [ ] **Step 1: Run optimizer test suite**

Run: `uv run pytest tests/engine/test_core.py tests/engine/test_terminal_cost_accuracy.py tests/test_optimizer_dp_solve.py tests/test_optimizer_hard_constraint.py tests/test_optimizer_self_consumption.py -v`
Expected: PASS

- [ ] **Step 1.5: Run lint check**

Run: `uv run ruff check custom_components/localshift`
Expected: PASS (no findings)

- [ ] **Step 2: Run coverage check**

Run: `uv run pytest --cov=custom_components/localshift --cov-report=term-missing`
Expected: Coverage >= 95%

- [ ] **Step 3: Commit final integration tweaks (if any)**

```bash
git branch --show-current  # must not be main/test
git add custom_components/localshift/engine/core.py \
  custom_components/localshift/engine/*.py \
  tests
git commit -m "refactor: finalize core optimizer split"
```

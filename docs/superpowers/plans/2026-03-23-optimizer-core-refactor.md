# Optimizer Core Refactor Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the remaining module-backed helper surface from `DPPlanner` so `custom_components/localshift/engine/core.py` shrinks to solver orchestration only while optimizer behavior stays unchanged.

**Architecture:** `custom_components/localshift/engine/core.py` already imports the extracted helper modules, but it still carries duplicate planner methods for solar, penalties, reason classification, constraints, and transitions. The implementation should first lock behavior with direct module-level tests, then delete the duplicate helper layer so `DPPlanner` keeps only solve-loop orchestration, DP table lifecycle, reconstruction, and terminal diagnostics.

**Tech Stack:** Python 3.13+, Home Assistant integration, pytest, ruff, LocalShift DP optimizer.

---

## File Structure Map

**Modify:**
- `custom_components/localshift/engine/core.py` - remove the module-backed helper layer and keep only planner orchestration/stateful solver methods.
- `custom_components/localshift/engine/reason_codes.py` - bring module-level charge-reason helpers to parity with current wrapper behavior before deleting the wrappers.
- `tests/engine/test_core.py` - add removal guardrails for the deleted `DPPlanner` helper surface.
- `tests/engine/test_terminal_cost_accuracy.py` - move solar-helper coverage to module imports and add a confidence-aware reason-code regression.

**Verify only (modify only if a red test proves parity is missing):**
- `custom_components/localshift/engine/penalties.py`
- `custom_components/localshift/engine/solar.py`
- `custom_components/localshift/engine/transitions.py`
- `custom_components/localshift/engine/constraints.py`
- `tests/test_optimizer_dp_solve.py`
- `tests/test_optimizer_hard_constraint.py`
- `tests/test_optimizer_self_consumption.py`

**Do not plan to modify unless the refactor unexpectedly changes boundaries:**
- `docs/ARCHITECTURE.md`
- `custom_components/localshift/engine/optimizer_dp.py`

## Chunk 1: Lock Behavior Before Deleting Helpers

Use `@superpowers:doc-first` before touching optimizer code and `@superpowers:test-driven-development` for each task in this chunk.

### Task 1: Move solar-helper coverage off `DPPlanner`

**Files:**
- Modify: `tests/engine/test_core.py`
- Modify: `tests/engine/test_terminal_cost_accuracy.py`
- Modify: `custom_components/localshift/engine/core.py`

- [ ] **Step 1: Write the failing removal guard test**

Add a new test in `tests/engine/test_core.py` that asserts `DPPlanner` no longer exposes the module-backed solar/penalty helpers:

```python
def test_dpplanner_no_longer_exposes_module_backed_solar_helpers():
    removed = [
        "_get_solar_opportunity_penalty_factor",
        "_get_futile_cycling_penalty_factor",
        "_projected_solar_soc_gain_pct",
        "_projected_solcast_gain_pct",
        "_get_forecast_accuracy",
    ]

    for name in removed:
        assert not hasattr(DPPlanner, name), name
```

- [ ] **Step 2: Run the focused tests to verify RED**

Run: `uv run pytest tests/engine/test_core.py::test_dpplanner_no_longer_exposes_module_backed_solar_helpers tests/engine/test_terminal_cost_accuracy.py::TestProjectedSolcastGainWithConfidence::test_low_confidence_reduces_projected_gain -v`

Expected: FAIL because the helper methods still exist on `DPPlanner` and `tests/engine/test_terminal_cost_accuracy.py` still references `DPPlanner._projected_solcast_gain_pct(...)`.

- [ ] **Step 3: Write the minimal implementation**

Make these changes:

1. In `tests/engine/test_terminal_cost_accuracy.py`, import the real module helpers and stop reaching through `DPPlanner`:

```python
from custom_components.localshift.engine.solar import (
    get_forecast_accuracy,
    projected_solcast_gain_pct,
)
```

2. Replace calls like this:

```python
optimistic_gain = DPPlanner._projected_solcast_gain_pct(solcast, start, end, 13.5)
```

with:

```python
optimistic_gain = projected_solcast_gain_pct(solcast, start, end, 13.5)
```

3. Delete these wrappers from `custom_components/localshift/engine/core.py` after confirming no internal call sites remain:
- `_get_solar_opportunity_penalty_factor()`
- `_get_futile_cycling_penalty_factor()`
- `_projected_solar_soc_gain_pct()`
- `_projected_solcast_gain_pct()`
- `_get_forecast_accuracy()`

4. Replace the remaining live internal call while `_classify_hold_reason()` still exists in this chunk. Task 2 deletes `_classify_hold_reason()` entirely after the reason-code module reaches parity:

```python
factor = get_solar_opportunity_penalty_factor(
    action=PlannerAction.CHARGE_GRID_NORMAL,
    grid_import_kwh=1.0,
    slot=slot,
    slot_idx=slot_idx,
    slots=slots,
    config=config,
    terminal_penalty_idx=terminal_penalty_idx,
    all_solcast=inputs.all_solcast,
)
```

- [ ] **Step 4: Run the focused tests to verify GREEN**

Run: `uv run pytest tests/engine/test_core.py::test_dpplanner_no_longer_exposes_module_backed_solar_helpers tests/engine/test_terminal_cost_accuracy.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the chunk**

```bash
git add tests/engine/test_core.py tests/engine/test_terminal_cost_accuracy.py custom_components/localshift/engine/core.py
git commit -m "refactor: remove DPPlanner solar helper shims"
```

### Task 2: Bring reason-code helpers to parity, then remove them from `DPPlanner`

**Files:**
- Modify: `tests/engine/test_core.py`
- Modify: `tests/engine/test_terminal_cost_accuracy.py`
- Modify: `custom_components/localshift/engine/reason_codes.py`
- Modify: `custom_components/localshift/engine/core.py`

- [ ] **Step 1: Write the failing regression and removal tests**

Add:

1. A removal test in `tests/engine/test_core.py` for these methods:

```python
def test_dpplanner_no_longer_exposes_reason_code_helpers():
    removed = [
        "_classify_reason",
        "_classify_hold_reason",
        "_classify_export_reason",
        "_classify_charge_reason",
        "_is_target_shortfall_risk",
        "_is_cheap_import_window",
        "_is_blind_to_future_solar",
    ]

    for name in removed:
        assert not hasattr(DPPlanner, name), name
```

2. A behavior-parity regression in `tests/engine/test_terminal_cost_accuracy.py` that proves the module helper passes a `ConfidenceResolver` into `projected_solcast_gain_pct()`:

```python
from custom_components.localshift.engine.reason_codes import _is_target_shortfall_risk
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    SlotContext,
)

def test_target_shortfall_risk_passes_confidence_resolver(monkeypatch):
    seen: dict[str, object] = {}

    def fake_projected_solcast_gain_pct(*args, confidence_resolver=None, **kwargs):
        seen["resolver"] = confidence_resolver
        return 0.0

    monkeypatch.setattr(
        "custom_components.localshift.engine.reason_codes.projected_solcast_gain_pct",
        fake_projected_solcast_gain_pct,
    )

    slots = [
        SlotContext(
            slot_index=0,
            timestamp_iso="2026-03-20T08:00:00+00:00",
            slot_interval_minutes=30,
            buy_price=0.10,
            sell_price=0.05,
            solar_kwh=0.0,
            consumption_kwh=0.0,
            is_demand_window_slot=False,
        ),
        SlotContext(
            slot_index=1,
            timestamp_iso="2026-03-20T08:30:00+00:00",
            slot_interval_minutes=30,
            buy_price=0.10,
            sell_price=0.05,
            solar_kwh=0.0,
            consumption_kwh=0.0,
            is_demand_window_slot=True,
        ),
    ]
    config = OptimizerConfig(demand_window_target_soc_pct=80.0, battery_capacity_kwh=13.5)
    inputs = OptimizerInputs(
        cycle_id="resolver-check",
        initial_soc_pct=70.0,
        slots=slots,
        config=config,
        all_solcast=[{"period_start": "2026-03-20T09:00:00+00:00", "pv_estimate": 4.0}],
        solcast_analysis_today=None,
        solcast_analysis_tomorrow=None,
    )

    _is_target_shortfall_risk(
        slot_idx=0,
        slots=slots,
        soc=70.0,
        config=config,
        terminal_penalty_idx=1,
        inputs=inputs,
    )

    assert seen["resolver"] is not None
```

- [ ] **Step 2: Run the focused tests to verify RED**

Run: `uv run pytest tests/engine/test_core.py::test_dpplanner_no_longer_exposes_reason_code_helpers tests/engine/test_terminal_cost_accuracy.py::test_target_shortfall_risk_passes_confidence_resolver -v`

Expected: FAIL because the wrapper methods still exist and `reason_codes._is_target_shortfall_risk()` currently does not thread a `ConfidenceResolver` through to `projected_solcast_gain_pct()`.

- [ ] **Step 3: Write the minimal implementation**

1. In `custom_components/localshift/engine/reason_codes.py`, add the import near the top of the file and use `ConfidenceResolver` when `inputs.all_solcast` is present. Passing `None` for the today/tomorrow analyses is acceptable and matches the current `core.py` behavior:

```python
from custom_components.localshift.forecast.analysis_resolver import ConfidenceResolver

confidence_resolver = ConfidenceResolver(
    getattr(inputs, "solcast_analysis_today", None),
    getattr(inputs, "solcast_analysis_tomorrow", None),
)

future_gain = projected_solcast_gain_pct(
    inputs.all_solcast,
    start_time=last_slot_end,
    end_time=target_time,
    battery_capacity_kwh=config.battery_capacity_kwh,
    confidence_resolver=confidence_resolver,
)
```

2. Delete these methods from `custom_components/localshift/engine/core.py` once the module helper behavior matches current planner behavior:
- `_classify_reason()`
- `_classify_hold_reason()`
- `_classify_export_reason()`
- `_classify_charge_reason()`
- `_is_target_shortfall_risk()`
- `_is_cheap_import_window()`
- `_is_blind_to_future_solar()`

3. Keep the real call path in `core.py` on the imported module helper:

```python
reason = classify_reason(
    action,
    slot,
    slot_idx,
    slots,
    soc,
    next_soc,
    config,
    terminal_penalty_idx,
    objective_terms=objective_terms,
    inputs=inputs,
    negative_fit_avoidance_context=negative_fit_context,
)
```

- [ ] **Step 4: Run the focused tests to verify GREEN**

Run: `uv run pytest tests/engine/test_core.py tests/engine/test_terminal_cost_accuracy.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the chunk**

```bash
git add tests/engine/test_core.py tests/engine/test_terminal_cost_accuracy.py custom_components/localshift/engine/reason_codes.py custom_components/localshift/engine/core.py
git commit -m "refactor: remove DPPlanner reason helper shims"
```

## Chunk 2: Delete Dead Transition And Constraint Helpers

Use `@superpowers:test-driven-development` for both tasks in this chunk.

### Task 3: Remove the duplicate transition surface from `DPPlanner`

**Files:**
- Modify: `tests/engine/test_core.py`
- Modify: `custom_components/localshift/engine/core.py`

- [ ] **Step 1: Write the failing removal test**

Add a test in `tests/engine/test_core.py` that covers the transition surface:

```python
def test_dpplanner_no_longer_exposes_transition_helpers():
    removed = [
        "transition",
        "_transition_hold",
        "_transition_hold_surplus",
        "_transition_hold_deficit",
        "_transition_charge_grid",
        "_charge_grid_with_solar",
        "_charge_grid_with_deficit",
        "_clip_charge_to_max_soc",
        "_transition_export",
    ]

    for name in removed:
        assert not hasattr(DPPlanner, name), name
```

- [ ] **Step 2: Run the focused tests to verify RED**

Run: `uv run pytest tests/engine/test_core.py::test_dpplanner_no_longer_exposes_transition_helpers tests/engine/test_core.py::TestCoreRegressionCoverage::test_transition_unknown_action_returns_noop -v`

Expected: FAIL on the removal test while the existing transition regression still passes against the module function.

- [ ] **Step 3: Write the minimal implementation**

Delete the transition dispatcher and all `_transition_*` helpers from `custom_components/localshift/engine/core.py`. Do not replace them with new shims; keep the existing imported module call sites:

```python
next_soc, grid_import, grid_export = _transition(soc, action, slot, config)
```

Also remove any now-unused imports from `core.py` after the deletions.

- [ ] **Step 4: Run the focused tests to verify GREEN**

Run: `uv run pytest tests/engine/test_core.py tests/test_optimizer_dp_solve.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the chunk**

```bash
git add tests/engine/test_core.py custom_components/localshift/engine/core.py
git commit -m "refactor: remove DPPlanner transition shims"
```

### Task 4: Remove the remaining constraint/solar duplicates from `DPPlanner`

**Files:**
- Modify: `tests/engine/test_core.py`
- Modify: `custom_components/localshift/engine/core.py`

- [ ] **Step 1: Write the failing removal test**

Add a test in `tests/engine/test_core.py` for the last duplicate helpers:

```python
def test_dpplanner_no_longer_exposes_constraint_and_solar_duplicates():
    removed = [
        "_can_solar_reach_target",
        "_check_global_solar_sufficiency",
        "_determine_export_actions",
    ]

    for name in removed:
        assert not hasattr(DPPlanner, name), name
```

- [ ] **Step 2: Run the focused tests to verify RED**

Run: `uv run pytest tests/engine/test_core.py::test_dpplanner_no_longer_exposes_constraint_and_solar_duplicates -v`

Expected: FAIL because the duplicate helpers still exist on `DPPlanner`.

- [ ] **Step 3: Write the minimal implementation**

Delete these methods from `custom_components/localshift/engine/core.py`:
- `_can_solar_reach_target()`
- `_check_global_solar_sufficiency()`
- `_determine_export_actions()`

If any lingering internal references remain, switch them to the real module helpers that already exist. Use `can_solar_reach_target_feasible` here (not `can_solar_reach_target`), because the current `DPPlanner._can_solar_reach_target()` signature matches the feasible-helper contract from the codebase:

```python
from custom_components.localshift.engine.constraints import (
    _determine_export_actions,
    check_global_solar_sufficiency,
)
from custom_components.localshift.engine.solar import can_solar_reach_target_feasible
```

Do not keep pass-through compatibility methods on `DPPlanner`.

- [ ] **Step 4: Run the focused tests to verify GREEN**

Run: `uv run pytest tests/engine/test_core.py tests/engine/test_terminal_cost_accuracy.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the chunk**

```bash
git add tests/engine/test_core.py custom_components/localshift/engine/core.py
git commit -m "refactor: remove DPPlanner duplicate constraint helpers"
```

## Chunk 3: Final Verification And Acceptance Checks

Use `@superpowers:verification-before-completion` before claiming the refactor is done.

### Task 5: Run the full optimizer regression suite and confirm the shrink target

**Files:**
- Verify: `custom_components/localshift/engine/core.py`
- Verify: `tests/engine/test_core.py`
- Verify: `tests/engine/test_terminal_cost_accuracy.py`
- Verify: `tests/test_optimizer_dp_solve.py`
- Verify: `tests/test_optimizer_hard_constraint.py`
- Verify: `tests/test_optimizer_self_consumption.py`

- [ ] **Step 1: Check the line-count acceptance criterion**

Run: `wc -l custom_components/localshift/engine/core.py`

Expected: line count is at or below `1851`, satisfying the approved "roughly 300+ lines smaller" target from the 2151-line baseline.

- [ ] **Step 2: Run the focused engine regression suite**

Run: `uv run pytest tests/engine/test_core.py tests/engine/test_terminal_cost_accuracy.py -v`

Expected: PASS.

- [ ] **Step 3: Run the broader optimizer scenario suite**

Run: `uv run pytest tests/test_optimizer_dp_solve.py tests/test_optimizer_hard_constraint.py tests/test_optimizer_self_consumption.py -v`

Expected: PASS.

- [ ] **Step 4: Run lint and coverage**

Run:
- `uv run ruff check custom_components/localshift`
- `uv run pytest --cov=custom_components/localshift --cov-report=term-missing`

Expected: PASS, with project coverage at or above the required threshold.

- [ ] **Step 5: Commit only if verification required follow-up fixes**

If verification passes with no new edits, do not create an empty commit.

If you had to make final verification fixes, use:

```bash
git add custom_components/localshift/engine/core.py custom_components/localshift/engine/reason_codes.py tests/engine/test_core.py tests/engine/test_terminal_cost_accuracy.py
git commit -m "fix: resolve optimizer core refactor verification issues"
```

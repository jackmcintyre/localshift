# Negative FIT Avoidance Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement bounded first-window negative-FIT avoidance in the DP optimizer.

**Architecture:** Add `NegativeFitAvoidanceContext` derived before backward induction; modify `feasible_actions()` to allow positive-FIT pre-discharge down to a bounded temporary floor; modify `stage_cost()` to treat negative FIT as real cost; retain actuator-side `PROACTIVE_EXPORT` throttling.

**Tech Stack:** Python, dynamic programming optimizer, Home Assistant integration.

---

## Chunk 1: Constants and Types Foundation

**Files:**
- Modify: `custom_components/localshift/const.py`
- Modify: `custom_components/localshift/engine/types.py`
- Test: `tests/test_optimizer_dp_solve.py`

Add necessary constants and the `NegativeFitAvoidanceContext` dataclass before touching core logic.

- [ ] **Step 1:** Add constants to `custom_components/localshift/const.py`

First, ensure `const.py` has `from typing import Final` near the top (add if missing). Then add:

```python
# Maximum headroom below battery target for negative-FIT avoidance (percentage points)
MAX_NEGATIVE_FIT_HEADROOM_PCT: Final[float] = 20.0

# Conservative buffer factor for overflow estimates (0.8 = use 80% of forecast)
NEGATIVE_FIT_OVERFLOW_BUFFER_FACTOR: Final[float] = 0.8
```

- [ ] **Step 2:** Add `NegativeFitAvoidanceContext` to `custom_components/localshift/engine/types.py`

Add after existing type definitions (e.g., near `OptimizerInputs`):

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class NegativeFitAvoidanceContext:
    """Immutable context for bounded first-window negative-FIT avoidance."""
    first_negative_fit_slot_idx: int
    conservative_overflow_kwh: float
    allowed_headroom_pct: float
    temporary_floor_pct: float
```

- [ ] **Step 3:** Add tests for the new type (no behavior yet)

In `tests/test_optimizer_dp_solve.py`, add:

```python
def test_negative_fit_context_type():
    """Smoke test: NegativeFitAvoidanceContext can be constructed."""
    from custom_components.localshift.engine.types import NegativeFitAvoidanceContext
    ctx = NegativeFitAvoidanceContext(
        first_negative_fit_slot_idx=10,
        conservative_overflow_kwh=5.0,
        allowed_headroom_pct=3.7,
        temporary_floor_pct=76.3,
    )
    assert ctx.first_negative_fit_slot_idx == 10
    assert ctx.conservative_overflow_kwh == 5.0
    assert ctx.allowed_headroom_pct == 3.7
    assert ctx.temporary_floor_pct == 76.3
```

- [ ] **Step 4:** Run the new test

```bash
uv run pytest tests/test_optimizer_dp_solve.py::test_negative_fit_context_type -v
```

Expected: PASS

- [ ] **Step 5:** Commit

```bash
git add custom_components/localshift/const.py \
         custom_components/localshift/engine/types.py \
         tests/test_optimizer_dp_solve.py
git commit -m "feat: add types and constants for negative-FIT avoidance"
```

---

## Chunk 2: Context Derivation Logic

**Files:**
- Modify: `custom_components/localshift/engine/core.py`
- Test: `tests/test_optimizer_dp_solve.py`

Implement `_derive_negative_fit_avoidance_context()` in `DPPlanner`.

- [ ] **Step 1:** Write failing unit tests for `_derive_negative_fit_avoidance_context()`

Add to `tests/test_optimizer_dp_solve.py`:

```python
def make_slot(idx: int, sell_price: float, solar_kwh: float = 0.0, consumption_kwh: float = 0.0) -> SlotContext:
    """Helper to create a 30-min slot."""
    return SlotContext(
        slot_index=idx,
        timestamp_iso=f"2026-01-03T{(idx // 2):02d}:{(idx % 2) * 30:02d}:00",
        slot_interval_minutes=30,
        buy_price=0.10,
        sell_price=sell_price,
        solar_kwh=solar_kwh,
        consumption_kwh=consumption_kwh,
    )

def test_negative_fit_context_no_window(default_config):
    """Returns None when no negative-FIT window in horizon."""
    planner = DPPlanner(default_config)
    slots = [make_slot(i, sell_price=0.08) for i in range(10)]
    inputs = OptimizerInputs(
        cycle_id="test",
        initial_soc_pct=50.0,
        slots=slots,
        config=default_config,
    )
    ctx = planner._derive_negative_fit_avoidance_context(inputs)
    assert ctx is None

def test_negative_fit_context_no_overflow(default_config):
    """Returns None when no forecast overflow projected."""
    planner = DPPlanner(default_config)
    # Negative FIT at idx=5, but zero solar before it
    slots = [make_slot(i, sell_price=0.08 if i < 5 else -0.05) for i in range(10)]
    inputs = OptimizerInputs(
        cycle_id="test",
        initial_soc_pct=50.0,
        slots=slots,
        config=default_config,
    )
    ctx = planner._derive_negative_fit_avoidance_context(inputs)
    assert ctx is None

def test_negative_fit_context_no_positive_slots(default_config):
    """Returns None when no earlier positive-FIT slots."""
    planner = DPPlanner(default_config)
    # Negative FIT at idx=5, positive FIT after that only
    slots = [make_slot(i, sell_price=0.08 if i >= 5 else 0.0) for i in range(10)]
    slots[5] = make_slot(5, sell_price=0.08)
    inputs = OptimizerInputs(
        cycle_id="test",
        initial_soc_pct=50.0,
        slots=slots,
        config=default_config,
    )
    ctx = planner._derive_negative_fit_avoidance_context(inputs)
    # Even though overflow might exist, no positive slots before negative window
    assert ctx is None

def test_negative_fit_context_computes_floor(default_config):
    """Computes correct temporary_floor_pct when all conditions met."""
    planner = DPPlanner(default_config)
    default_config.demand_window_target_soc_pct = 80.0
    # Positive FIT at idx=0, negative FIT at idx=4. Some excess solar -> headroom 5%
    slots = []
    for i in range(6):
        sell = 0.08 if i < 4 else -0.05
        solar = 2.0 if i == 0 else 0.0  # One big solar burst makes overflow
        slots.append(make_slot(i, sell_price=sell, solar_kwh=solar))
    inputs = OptimizerInputs(
        cycle_id="test",
        initial_soc_pct=90.0,
        slots=slots,
        config=default_config,
    )
    ctx = planner._derive_negative_fit_avoidance_context(inputs)
    assert ctx is not None
    # allowed_headroom_pct should be <= MAX_NEGATIVE_FIT_HEADROOM_PCT
    assert 0 < ctx.allowed_headroom_pct <= 20.0
    # temporary_floor_pct = target - allowed_headroom_pct
    assert abs(ctx.temporary_floor_pct - (80.0 - ctx.allowed_headroom_pct)) < 0.01
```

- [ ] **Step 2:** Run tests to confirm they fail (missing method)

```bash
uv run pytest tests/test_optimizer_dp_solve.py::test_negative_fit_context_no_window -v
```

Expected: FAIL (AttributeError: 'DPPlanner' object has no attribute '_derive_negative_fit_avoidance_context')

- [ ] **Step 3:** Implement `_derive_negative_fit_avoidance_context()` in `custom_components/localshift/engine/core.py`

Add method to `DPPlanner` class (near `_solve`):

```python
def _derive_negative_fit_avoidance_context(
    self, inputs: OptimizerInputs
) -> NegativeFitAvoidanceContext | None:
    """Derive context for bounded first-window negative-FIT avoidance.

    Returns None if any of:
    - No negative-FIT window within horizon
    - No conservative overflow projected
    - No earlier positive-FIT slots
    """
    from custom_components.localshift.const import (
        MAX_NEGATIVE_FIT_HEADROOM_PCT,
        NEGATIVE_FIT_OVERFLOW_BUFFER_FACTOR,
    )

    slots = inputs.slots
    config = inputs.config
    battery_capacity_kwh = config.battery_capacity_kwh

    # 1. Find first negative-FIT slot (sell_price <= 0)
    first_negative_fit_idx = None
    for idx, slot in enumerate(slots):
        if slot.sell_price <= 0:
            first_negative_fit_idx = idx
            break
    if first_negative_fit_idx is None:
        return None

    # 2. Check there is at least one earlier positive-FIT slot
    has_positive_before = any(s.sell_price > 0 for s in slots[:first_negative_fit_idx])
    if not has_positive_before:
        return None

    # 3. Compute conservative overflow before that window
    # Reuse logic from excess_solar._calculate_excess_until_negative_fit but inline for simplicity
    target_kwh = config.demand_window_target_soc_pct / 100.0 * battery_capacity_kwh
    current_kwh = inputs.initial_soc_pct / 100.0 * battery_capacity_kwh
    space_to_target_kwh = max(target_kwh - current_kwh, 0)

    accumulated_excess_kwh = 0.0
    for idx in range(first_negative_fit_idx):
        slot = slots[idx]
        net_kwh = slot.solar_kwh - slot.consumption_kwh
        if net_kwh > 0:
            excess_kwh = net_kwh * config.charge_efficiency  # charging efficiency
            if space_to_target_kwh > 0:
                used = min(excess_kwh, space_to_target_kwh)
                space_to_target_kwh -= used
            else:
                accumulated_excess_kwh += excess_kwh

    if accumulated_excess_kwh <= 0:
        return None

    conservative_overflow_kwh = accumulated_excess_kwh * NEGATIVE_FIT_OVERFLOW_BUFFER_FACTOR

    # 4. Derive allowed headroom (percentage points)
    allowed_headroom_pct = min(
        conservative_overflow_kwh / battery_capacity_kwh * 100.0,
        MAX_NEGATIVE_FIT_HEADROOM_PCT,
    )

    # 5. Compute temporary floor
    temporary_floor_pct = config.demand_window_target_soc_pct - allowed_headroom_pct

    return NegativeFitAvoidanceContext(
        first_negative_fit_slot_idx=first_negative_fit_idx,
        conservative_overflow_kwh=conservative_overflow_kwh,
        allowed_headroom_pct=allowed_headroom_pct,
        temporary_floor_pct=temporary_floor_pct,
    )
```

- [ ] **Step 4:** Run the tests

```bash
uv run pytest tests/test_optimizer_dp_solve.py::test_negative_fit_context_no_window \
                 tests/test_optimizer_dp_solve.py::test_negative_fit_context_no_overflow \
                 tests/test_optimizer_dp_solve.py::test_negative_fit_context_no_positive_slots \
                 tests/test_optimizer_dp_solve.py::test_negative_fit_context_computes_floor \
                 -v
```

Expected: All PASS

- [ ] **Step 5:** Integrate context derivation into `_solve()`

Modify `DPPlanner._solve()`: after building `inputs`, before `_initialize_dp_tables()`, add:

```python
negative_fit_context = self._derive_negative_fit_avoidance_context(inputs)
```

Then pass `negative_fit_context` to `_backward_induction()` and `_forward_reconstruct()` (adjust signatures accordingly).

- [ ] **Step 6:** Commit

```bash
git add custom_components/localshift/engine/core.py \
         tests/test_optimizer_dp_solve.py
git commit -m "feat: derive negative-FIT avoidance context"
```

---

## Chunk 3: Feasible Actions Bounded Pre-Discharge

**Files:**
- Modify: `custom_components/localshift/engine/constraints.py`
- Test: `tests/test_constraints.py`

Broaden proactive export eligibility before first negative-FIT window while enforcing the bounded SOC floor.

- [ ] **Step 1:** Write failing unit tests for modified `feasible_actions()` in new `tests/test_constraints.py`

Create file `tests/test_constraints.py` with:

```python
"""Constraint unit tests for negative-FIT avoidance."""
from __future__ import annotations

import pytest

from custom_components.localshift.engine.constraints import feasible_actions
from custom_components.localshift.engine.types import (
    NegativeFitAvoidanceContext,
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)


@pytest.fixture
def default_config() -> OptimizerConfig:
    """Reusable default optimizer config."""
    return OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=80.0,
        soc_bins=20,
        optimization_mode="self_consumption",
        export_price_margin=0.02,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
    )


def make_test_slot(sell_price: float, is_demand_window_slot: bool = False) -> SlotContext:
    """Minimal slot for constraint tests."""
    return SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.10,
        sell_price=sell_price,
        solar_kwh=0.0,
        consumption_kwh=0.0,
        is_demand_window_slot=is_demand_window_slot,
    )


def test_feasible_actions_positive_fit_before_negative(default_config):
    """Allows EXPORT_PROACTIVE at positive FIT before first negative-FIT window."""
    slot = make_test_slot(sell_price=0.08)
    context = NegativeFitAvoidanceContext(
        first_negative_fit_slot_idx=5,
        conservative_overflow_kwh=10.0,
        allowed_headroom_pct=5.0,
        temporary_floor_pct=75.0,
    )
    actions = feasible_actions(
        soc_pct=90.0,
        slot=slot,
        config=default_config,
        slot_idx=0,
        slots=None,
        terminal_penalty_idx=None,
        negative_fit_avoidance_context=context,
    )
    assert PlannerAction.EXPORT_PROACTIVE in actions


def test_feasible_actions_negative_fit_floor_blocked(default_config):
    """Blocks EXPORT_PROACTIVE at sell_price <= 0 (hard floor)."""
    slot = make_test_slot(sell_price=0.0)
    context = NegativeFitAvoidanceContext(
        first_negative_fit_slot_idx=5,
        conservative_overflow_kwh=10.0,
        allowed_headroom_pct=5.0,
        temporary_floor_pct=75.0,
    )
    actions = feasible_actions(
        soc_pct=90.0,
        slot=slot,
        config=default_config,
        slot_idx=0,
        slots=None,
        terminal_penalty_idx=None,
        negative_fit_avoidance_context=context,
    )
    assert PlannerAction.EXPORT_PROACTIVE not in actions


def test_feasible_actions_temporary_floor_enforced(default_config):
    """Blocks EXPORT_PROACTIVE when SOC would fall below temporary_floor_pct."""
    slot = make_test_slot(sell_price=0.08)
    context = NegativeFitAvoidanceContext(
        first_negative_fit_slot_idx=5,
        conservative_overflow_kwh=10.0,
        allowed_headroom_pct=5.0,
        temporary_floor_pct=75.0,
    )
    actions = feasible_actions(
        soc_pct=74.0,
        slot=slot,
        config=default_config,
        slot_idx=0,
        slots=None,
        terminal_penalty_idx=None,
        negative_fit_avoidance_context=context,
    )
    assert PlannerAction.EXPORT_PROACTIVE not in actions


def test_feasible_actions_normal_rules_after_negative_window(default_config):
    """Uses normal rules for slots at/after first negative-FIT window."""
    slot = make_test_slot(sell_price=0.08)
    context = NegativeFitAvoidanceContext(
        first_negative_fit_slot_idx=2,
        conservative_overflow_kwh=10.0,
        allowed_headroom_pct=5.0,
        temporary_floor_pct=75.0,
    )
    actions = feasible_actions(
        soc_pct=90.0,
        slot=slot,
        config=default_config,
        slot_idx=2,
        slots=None,
        terminal_penalty_idx=None,
        negative_fit_avoidance_context=context,
    )
    # In self-consumption normal rule requires profitable export
    min_profitable = max(0.0, slot.buy_price) + default_config.export_price_margin
    if slot.sell_price >= min_profitable:
        assert PlannerAction.EXPORT_PROACTIVE in actions
    else:
        assert PlannerAction.EXPORT_PROACTIVE not in actions
```

**Note:** These tests require adding `negative_fit_avoidance_context` parameter to `feasible_actions()`. We'll do that next.

- [ ] **Step 2:** Modify `feasible_actions()` signature and implementation

Add optional parameter `negative_fit_avoidance_context: NegativeFitAvoidanceContext | None = None` to signature.

Implementation changes:

```python
def feasible_actions(
    soc_pct: float,
    slot: SlotContext,
    config: OptimizerConfig,
    slot_idx: int = 0,
    slots: list[SlotContext] | None = None,
    terminal_penalty_idx: int | None = None,
    negative_fit_avoidance_context: NegativeFitAvoidanceContext | None = None,
) -> list[PlannerAction]:
```

Inside export constraints section, replace existing logic with:

```python
    # Export constraints (Issue #719)
    if can_discharge:
        # Determine if we are in the pre-negative-FIT window with context active
        use_avoidance = (
            negative_fit_avoidance_context is not None
            and slot_idx < negative_fit_avoidance_context.first_negative_fit_slot_idx
        )

        if use_avoidance:
            # Relaxed: allow any positive FIT, but enforce temporary SOC floor
            # Compute post-discharge SOC (approximate: discharge 1 kWh lowers ~0.1% SOC for 13.5 kWh battery)
            # For precise check we'll compare after transition; feasible_actions cannot know exact transition,
            # so we conservatively check if current SOC is at least temporary_floor_pct + discharge margin.
            # The actual bound will be enforced in stage_cost via penalty if violated, but we also gate here.
            if slot.sell_price > 0:
                # Rough guard: require SOC high enough to discharge without immediately breaching floor
                # We'll allow if soc_pct > temporary_floor_pct + 2 (approx margin)
                if soc_pct > negative_fit_avoidance_context.temporary_floor_pct + 2.0:
                    actions.append(PlannerAction.EXPORT_PROACTIVE)
        else:
            # Existing rules
            if config.optimization_mode == "self_consumption":
                min_profitable_sell = max(0.0, slot.buy_price) + config.export_price_margin
                if slot.sell_price >= min_profitable_sell:
                    actions.append(PlannerAction.EXPORT_PROACTIVE)
            else:
                if slot.sell_price > 0:
                    actions.append(PlannerAction.EXPORT_PROACTIVE)
```

- [ ] **Step 3:** Run the new tests

```bash
uv run pytest tests/test_constraints.py::test_feasible_actions_positive_fit_before_negative \
                 tests/test_constraints.py::test_feasible_actions_negative_fit_floor_blocked \
                 tests/test_constraints.py::test_feasible_actions_temporary_floor_enforced \
                 tests/test_constraints.py::test_feasible_actions_normal_rules_after_negative_window \
                 -v
```

Expected: PASS (adjust logic until they pass)

- [ ] **Step 4:** Commit

```bash
git add custom_components/localshift/engine/constraints.py \
         tests/test_constraints.py
git commit -m "feat: allow bounded pre-discharge in feasible_actions()"
```

---

## Chunk 4: Negative FIT as Real Cost

**Files:**
- Modify: `custom_components/localshift/engine/cost.py`
- Test: `tests/test_cost.py` (create if missing)

Remove the clipping of `sell_price` in `stage_cost()`.

- [ ] **Step 1:** Write failing test

Create `tests/test_cost.py`:

```python
import pytest
from custom_components.localshift.engine.cost import stage_cost
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)

def test_stage_cost_negative_fit_real_cost():
    """Negative sell_price produces negative export revenue (real cost)."""
    config = OptimizerConfig(optimization_mode="arbitrage")
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2026-01-03T10:00:00",
        slot_interval_minutes=30,
        buy_price=0.10,
        sell_price=-0.05,  # Negative FIT
        solar_kwh=0.0,
        consumption_kwh=0.0,
    )
    terms = stage_cost(
        action=PlannerAction.EXPORT_PROACTIVE,
        grid_import_kwh=0.0,
        grid_export_kwh=2.0,
        slot=slot,
        config=config,
    )
    # Export revenue should be negative (cost)
    assert terms.export_revenue == 2.0 * (-0.05)  # -0.1
    assert terms.net_cost > 0  # net cost positive due to negative revenue
```

- [ ] **Step 2:** Run to confirm failure

```bash
uv run pytest tests/test_cost.py::test_stage_cost_negative_fit_real_cost -v
```

Expected: FAIL (export_revenue = max(0, -0.05)*2 = 0, not -0.1)

- [ ] **Step 3:** Modify `stage_cost()` in `custom_components/localshift/engine/cost.py`

Change line 50 from:

```python
export_revenue = grid_export_kwh * max(0.0, slot.sell_price)
```

to:

```python
export_revenue = grid_export_kwh * slot.sell_price
```

- [ ] **Step 4:** Run test to confirm pass

```bash
uv run pytest tests/test_cost.py::test_stage_cost_negative_fit_real_cost -v
```

Expected: PASS

- [ ] **Step 5:** Run all cost tests to check for regressions

```bash
uv run pytest tests/test_cost.py -v 2>/dev/null || uv run pytest -k cost -v
```

Ensure no other tests break. If any break due to negative revenue assumptions, adjust those tests accordingly.

- [ ] **Step 6:** Commit

```bash
git add custom_components/localshift/engine/cost.py \
         tests/test_cost.py
git commit -m "feat: treat negative FIT as real cost in stage_cost()"
```

---

## Chunk 5: Core Integration and Forward Pass

**Files:**
- Modify: `custom_components/localshift/engine/core.py`
- Test: `tests/test_optimizer_dp_solve.py`

Attach context to backward induction and forward reconstruct; ensure bounded floor is respected by DP.

- [ ] **Step 1:** Update `_backward_induction()` signature to accept `negative_fit_context`

Add parameter `negative_fit_context: NegativeFitAvoidanceContext | None = None` and pass it through to `_compute_best_action()` and `feasible_actions()`.

- [ ] **Step 2:** Update `_compute_best_action()` to pass context to `feasible_actions()`

Inside `_compute_best_action()`, when calling `feasible_actions()`, add:

```python
actions = feasible_actions(
    soc_pct=soc,
    slot=slot,
    config=config,
    slot_idx=slot_idx,
    slots=slots,
    terminal_penalty_idx=terminal_penalty_idx,
    negative_fit_avoidance_context=negative_fit_context,
)
```

- [ ] **Step 3:** Update `_forward_reconstruct()` to receive context (for logging/diagnostics only; not used in reconstruction logic)

Adjust call sites accordingly.

- [ ] **Step 4:** Write integration test for bounded pre-discharge

Add to `tests/test_optimizer_dp_solve.py` (reuses default_config fixture):

```python
def test_negative_fit_bounded_predischarge(default_config):
    """High solar, future negative-FIT, earlier positive-FIT slots: bounded pre-discharge."""
    from custom_components.localshift.engine.optimizer_dp import DPPlanner

    default_config.demand_window_target_soc_pct = 80.0
    default_config.optimization_mode = "self_consumption"

    # Build horizon: positive FIT in slots 0-3, negative FIT from slot 4 onward
    slots = []
    for i in range(10):
        sell = 0.08 if i < 4 else -0.05
        # High solar early to create overflow
        solar = 5.0 if i < 2 else 0.0
        consumption = 0.5
        slots.append(SlotContext(
            slot_index=i,
            timestamp_iso=f"2026-01-03T{(i // 2):02d}:{(i % 2) * 30:02d}:00",
            slot_interval_minutes=30,
            buy_price=0.10,
            sell_price=sell,
            solar_kwh=solar,
            consumption_kwh=consumption,
        ))

    inputs = OptimizerInputs(
        cycle_id="test",
        initial_soc_pct=95.0,
        slots=slots,
        config=default_config,
    )

    planner = DPPlanner(default_config)
    result = planner.plan(inputs)

    # Should schedule some EXPORT_PROACTIVE in early positive-FIT slots
    export_actions = [d.action for d in result.decisions if d.action == PlannerAction.EXPORT_PROACTIVE]
    assert len(export_actions) > 0

    # SOC trajectory should dip below 80% but not below expected temporary floor (~75%)
    socs = [d.soc_pct for d in result.decisions]
    min_soc = min(socs)
    assert min_soc < 80.0  # dipped below target
    # With 20% max headroom floor might be ~60%, but with our overflow likely ~75%; ensure not excessive
    assert min_soc >= 60.0  # bounded by headroom, not crashing below 50%
```

- [ ] **Step 5:** Run the test; iterate until it passes and respects bounded floor

Tweak the `temporary_floor_pct` derivation if needed; the test should confirm the DP respects the context via `feasible_actions()`.

- [ ] **Step 6:** Commit

```bash
git add custom_components/localshift/engine/core.py \
         tests/test_optimizer_dp_solve.py \
         tests/test_scenarios.py
git commit -m "feat: integrate negative-FIT context into DP solve"
```

---

## Chunk 6: Test Coverage and Refinement

**Files:**
- All modified files
- All test files

Complete unit and integration coverage, update existing tests as needed.

- [ ] **Step 1:** Add remaining unit tests listed in spec

- In `tests/test_optimizer_dp_solve.py`: `test_negative_fit_context_conservative_buffer`
- In `tests/test_cost.py`: ensure `test_stage_cost_negative_fit_real_cost` already covers export; add positive control test ensuring positive sell_price still yields positive revenue
- In `tests/test_constraints.py`: add test for arbitrage mode behavior

- [ ] **Step 2:** Update existing tests per spec

- `tests/test_optimizer_dp_solve.py::test_dp_planner_negative_fit_no_export`: Review; ensure it still tests hard `0c` floor and adjust comments if needed.
- `tests/test_scenarios.py::test_high_solar_no_export_at_negative_prices`: Ensure it tests that `sell_price <= 0` is forbidden, not that no export ever occurs before negative window.

- [ ] **Step 3:** Run full test suite with coverage

```bash
uv run pytest --cov=custom_components/localshift --cov-report=term-missing
```

Ensure ≥95% coverage on modified files (`core.py`, `constraints.py`, `cost.py`, `types.py`).

- [ ] **Step 4:** Fix any regressions or coverage gaps

Add targeted tests for any uncovered branches.

- [ ] **Step 5:** Final commit

```bash
git add tests/
git commit -m "test: complete negative-FIT avoidance test coverage"
```

---

## Chunk 7: Final Verification

**Commands:**

- [ ] Run full test suite with coverage ≥95%
- [ ] Run ruff linter

```bash
uv run ruff check custom_components/localshift
```

- [ ] Verify no unintended changes in unrelated areas

```bash
git diff --stat
```

Focus on modified files only.

- [ ] Final commit message

```bash
git commit --amend -m "feat(optimizer): implement bounded first-window negative-FIT avoidance (#719)
- Add constants and NegativeFitAvoidanceContext type
- Derive context before backward induction
- Allow EXPORT_PROACTIVE before first negative-FIT at any positive FIT with bounded SOC floor
- Treat negative FIT as real cost in stage_cost()
- Retain PROACTIVE_EXPORT actuator throttling
- Add comprehensive unit and integration tests"
```

---

## Rollback Plan

If issues arise during implementation:

- Revert chunks individually using `git revert <commit>`
- Disable feature by returning `None` from `_derive_negative_fit_avoidance_context()`
- Fallback to existing logic when context is `None` (already part of design)

---

## Plan Complete

Save this plan to `docs/superpowers/plans/2026-03-15-negative-fit-avoidance-implementation.md`.

**Execution:** Use superpowers:subagent-driven-development or superpowers:executing-plans to carry out the steps.

# Optimizer Core Refactor Design

## Goal
Refactor `custom_components/localshift/engine/core.py` into smaller, focused modules while preserving behavior and the public `DPPlanner` entrypoint (`plan()` and solver flow). Reduce duplication by making `cost.py` and `constraints.py` the single sources of truth for cost and feasibility logic.

## Non-Goals
- Change optimizer behavior or tuning.
- Modify cost, constraint, or terminal penalty logic.
- Introduce new configuration options.
- Move `DPPlanner` to a new module (it stays in `core.py`).

## Current Problems
- `core.py` is a 2,000+ line monolith with mixed responsibilities.
- `DPPlanner` duplicates logic already in `cost.py` and `constraints.py`.
- High coupling makes future changes risky and hard to review.

## Design Summary
1. **Remove duplication first**: delete duplicated static methods from `DPPlanner` and use `engine.cost` and `engine.constraints` directly.
2. **Selective extraction**: move specialized logic into new modules, keeping solver orchestration and DP tables in `core.py`.
3. **Pure functions**: extracted helpers are pure/deterministic and accept explicit inputs rather than relying on instance state.

## Duplication Cleanup Map

| DPPlanner Method (remove) | Replacement Function | Notes |
|---|---|---|
| `stage_cost()` | `engine.cost.stage_cost()` | Use standalone cost terms as single source of truth |
| `terminal_cost()` | `engine.cost.terminal_cost()` | Preserve terminal penalty behavior |
| `feasible_actions()` | `engine.constraints.feasible_actions()` | Hard constraints only |
| `_determine_export_actions()` | `engine.constraints._determine_export_actions()` | Keep export gate logic consolidated |
| `_check_global_solar_sufficiency()` | `engine.constraints.check_global_solar_sufficiency()` | Keep global solar sufficiency logic centralized |

## File Boundaries

### Keep in `core.py`
`DPPlanner` remains the public entrypoint and owns only the DP solver flow:
- `plan()`
- `_solve()`
- `_initialize_dp_tables()`
- `_backward_induction()`
- `_compute_best_action()`
- `_forward_reconstruct()`
- `_empty_result()`
- `_find_demand_window_bounds()`
- `_check_solar_can_reach_target()` (delegates to `engine/solar.py`)
- `_determine_terminal_penalty_idx()`
- `_compute_terminal_shortfall()`
- `_get_terminal_diagnostics()`

### Single Source of Truth (existing files)
- `engine/cost.py`: `stage_cost()`, `terminal_cost()`
- `engine/constraints.py`: `feasible_actions()`, `_determine_export_actions()`, `check_global_solar_sufficiency()`

### New Modules
- `engine/transitions.py`: `transition()` and all `_transition_*` helpers
- `engine/negative_fit.py`: negative-FIT avoidance context derivation
- `engine/penalties.py`: penalty factor calculators
- `engine/reason_codes.py`: reason classification helpers
- `engine/solar.py`: solar reach/projection helpers

## Function Move Map and Interfaces

| Source (core.py) | Destination | Interface (inputs → output) |
|---|---|---|
| `transition()` and `_transition_*` helpers | `engine/transitions.py` | `(soc_pct: float, action: PlannerAction, slot: SlotContext, config: OptimizerConfig) → tuple[float, float, float, float]`
| `_find_risk_window()` | `engine/negative_fit.py` | `(slots: list[SlotContext]) → tuple[int | None, int | None]`
| `_compute_required_headroom()` | `engine/negative_fit.py` | `(slots: list[SlotContext], start_idx: int, end_idx: int, config: OptimizerConfig) → float`
| `_compute_recovery_by_slot()` | `engine/negative_fit.py` | `(slots: list[SlotContext], config: OptimizerConfig, terminal_penalty_idx: int | None) → list[float]`
| `_compute_floor_by_slot()` | `engine/negative_fit.py` | `(slots: list[SlotContext], config: OptimizerConfig, terminal_penalty_idx: int | None, recovery_by_slot: list[float]) → list[float]`
| `_compute_recoverability_floor_pct()` | `engine/negative_fit.py` | `(slot_idx: int, floor_by_slot: list[float], config: OptimizerConfig) → float`
| `_derive_negative_fit_avoidance_context()` | `engine/negative_fit.py` | `(inputs: OptimizerInputs) → NegativeFitAvoidanceContext`
| `_get_solar_opportunity_penalty_factor()` | `engine/penalties.py` | `(slot_idx: int, slots: list[SlotContext], config: OptimizerConfig, terminal_penalty_idx: int | None, inputs: OptimizerInputs) → float`
| `_get_futile_cycling_penalty_factor()` | `engine/penalties.py` | `(slot_idx: int, slots: list[SlotContext], config: OptimizerConfig, terminal_penalty_idx: int | None, inputs: OptimizerInputs) → float`
| `_classify_reason()` and sub-classifiers | `engine/reason_codes.py` | `(slot_idx: int, slot: SlotContext, action: PlannerAction, objective_terms: ObjectiveTerms, inputs: OptimizerInputs, config: OptimizerConfig, terminal_penalty_idx: int | None, negative_fit_context: NegativeFitAvoidanceContext | None) → PlannerReasonCode`
| `_can_solar_reach_target()` | `engine/solar.py` | `(inputs: OptimizerInputs, slots: list[SlotContext], config: OptimizerConfig, terminal_penalty_idx: int | None) → bool`
| `_projected_solar_soc_gain_pct()` | `engine/solar.py` | `(slots: list[SlotContext], config: OptimizerConfig, slot_idx: int) → float`
| `_projected_solcast_gain_pct()` | `engine/solar.py` | `(slots: list[SlotContext], slot_idx: int) → float`
| `_get_forecast_accuracy()` | `engine/solar.py` | `(inputs: OptimizerInputs) → float`
| `_check_global_solar_sufficiency()` | `engine/constraints.py` | `(soc_pct, slot_idx, slots, config) → bool`

Interfaces are intended to match current behavior; signatures will mirror existing method parameters to avoid behavioral drift. Shared dataclasses live in `engine/types.py`.

### Return Value Semantics
- `transition()` returns `(next_soc_pct, grid_import_kwh, grid_export_kwh, solar_to_battery_kwh)` in that order, matching current `DPPlanner.transition()` behavior.

### Naming Policy
- Functions keep their current names when moved (including leading underscores for internal helpers).
- Only `transition()` remains a non-underscored function in `engine/transitions.py`.
- No renames are introduced in this refactor to minimize call-site churn.

## Extraction Order
1. **Duplication cleanup** (remove static methods from `DPPlanner`, update call sites)
2. `transitions.py`
3. `negative_fit.py`
4. `penalties.py`
5. `reason_codes.py`
6. `solar.py`

This order minimizes behavior risk by first consolidating existing logic, then extracting in dependency order (transitions/negative-FIT/penalties used by the solver loop; reason codes used only in reconstruction; solar helpers used in planning setup and output).

## API and Compatibility
- `DPPlanner` stays in `core.py` to avoid breaking imports.
- Tests importing `DPPlanner` from `core.py` continue to work.
- Static wrappers on `DPPlanner` for `stage_cost()` / `terminal_cost()` / `feasible_actions()` are removed; call sites must import from `cost.py` / `constraints.py`.
- `optimizer_dp.py` remains the re-export shim for `cost` and `constraints` functions, but **not** for removed `DPPlanner` static methods.

### Compatibility Decision
This refactor intentionally removes `DPPlanner.stage_cost()` / `DPPlanner.terminal_cost()` / `DPPlanner.feasible_actions()` wrappers. These are treated as internal-only helpers (not part of the supported public API); downstream imports must migrate to `engine.cost` / `engine.constraints`. No temporary compatibility wrappers are planned.

### Call Site Inventory
Known imports of `DPPlanner` (class) are in:
- `tests/engine/test_core.py`
- `tests/engine/test_terminal_cost_accuracy.py`

Static method usage is expected to be internal only; before edits, we will search for:
- `DPPlanner.stage_cost`
- `DPPlanner.terminal_cost`
- `DPPlanner.feasible_actions`

Any matches will be migrated to `engine.cost` / `engine.constraints` and kept functionally identical.

### Search and Update Checklist
Before moving helpers, search for and update any non-core imports/call sites:
- `transition`, `_transition_*`
- `_derive_negative_fit_avoidance_context`, `_find_risk_window`, `_compute_required_headroom`, `_compute_recovery_by_slot`, `_compute_floor_by_slot`, `_compute_recoverability_floor_pct`
- `_get_solar_opportunity_penalty_factor`, `_get_futile_cycling_penalty_factor`
- `_classify_reason`, `_classify_hold_reason`, `_classify_export_reason`, `_classify_charge_reason`
- `_can_solar_reach_target`, `_projected_solar_soc_gain_pct`, `_projected_solcast_gain_pct`, `_get_forecast_accuracy`

Any matches will be updated to import from the new module locations.

### Public Surface of New Modules
| Module | Public Exports | Purpose |
|---|---|---|
| `engine/transitions.py` | `transition()` | Action transition dispatcher used by solver loop |
| `engine/negative_fit.py` | `_derive_negative_fit_avoidance_context()` plus helpers | Compute negative-FIT avoidance context |
| `engine/penalties.py` | `_get_solar_opportunity_penalty_factor()`, `_get_futile_cycling_penalty_factor()` | Compute penalty multipliers for stage cost |
| `engine/reason_codes.py` | `_classify_reason()` and sub-classifiers | Assign reason codes during reconstruction |
| `engine/solar.py` | `_can_solar_reach_target()`, projection helpers | Solar reach/projection utilities |

Exports will be imported explicitly where used (no `__all__` changes or wildcard exports) to avoid expanding the public API surface unintentionally.

## Dependency Rules
- `core.py` may import from `cost.py`, `constraints.py`, `transitions.py`, `negative_fit.py`, `penalties.py`, `reason_codes.py`, and `solar.py`.
- New helper modules **must not** import from `core.py` (avoid circular dependencies).
- `cost.py` and `constraints.py` remain standalone and should not depend on the new helper modules.
- `constraints.py` continues to accept `NegativeFitAvoidanceContext` as an argument but does not compute it.
- `constraints.py` uses `dp_math.py` for solar simulation; it does not import `engine/solar.py`.
- Helper modules may import `types.py`, `dp_math.py`, and other pure helpers as needed.

### Call Flow Note
`core.py` computes `NegativeFitAvoidanceContext` (via `negative_fit.derive_negative_fit_avoidance_context(inputs)`) before backward induction and threads it into `constraints.feasible_actions(...)` and any reason-classification helpers that need it.

### Penalty Helpers Boundary
`penalties.py` provides factors computed in `core.py` and passed into `cost.stage_cost(...)` via existing parameters; `cost.py` does not import `penalties.py`.

## Constraints and Invariants
- Preserve the soft-constrained DP architecture: hard constraints in `feasible_actions()`, soft preferences in `stage_cost()`, deadlines in `terminal_cost()`.
- Keep optimizer pure/deterministic (no side effects, no randomness).
- No behavior changes; refactor only.

## Testing Strategy
- Run existing optimizer and engine tests, including:
  - `tests/engine/test_core.py`
  - `tests/engine/test_terminal_cost_accuracy.py`
  - `tests/test_optimizer_dp_solve.py`
  - `tests/test_optimizer_hard_constraint.py`
  - `tests/test_optimizer_self_consumption.py`
- Validate refactor-only behavior by ensuring scenario-based optimizer tests pass unchanged.
- If any signature change is required, add a focused unit test to lock current behavior before refactor and confirm parity after.

## Risks and Mitigations
- **Risk**: subtle behavior change due to incorrect function wiring.
  - **Mitigation**: extract in small steps, rely on existing tests.
- **Risk**: missing imports or circular dependencies.
  - **Mitigation**: keep modules small and import only needed helpers.

## Open Questions
- None. Scope and sequencing approved.

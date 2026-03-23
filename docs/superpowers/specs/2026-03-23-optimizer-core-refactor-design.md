# Optimizer Core Refactor Design

## Goal
Refactor `custom_components/localshift/engine/core.py` into a smaller, solver-focused module for issue `#751` while preserving optimizer behavior and keeping `DPPlanner` as the public entrypoint.

## Non-Goals
- Change optimizer policy, tuning, or reason-code semantics.
- Change the planning-model split between `feasible_actions()`, `stage_cost()`, and `terminal_cost()`.
- Move `DPPlanner` out of `custom_components/localshift/engine/core.py`.
- Add compatibility shims for removed private helper methods.

## Context
- `custom_components/localshift/engine/core.py` is still the main optimizer hotspot at roughly 2,100 lines.
- The repo already contains extracted helper modules in `custom_components/localshift/engine/` for transitions, solar projections, penalties, negative-FIT logic, and reason classification.
- `core.py` still carries a duplicate helper layer that mirrors those modules and keeps `DPPlanner` wider than necessary.
- `docs/PLANNING_MODEL.md` and `custom_components/localshift/engine/AGENTS.md` require the optimizer to remain pure, deterministic, and split along these boundaries:
  - hard constraints in `feasible_actions()`
  - soft preferences in `stage_cost()`
  - deadline requirements in `terminal_cost()`

## Design Summary
1. Keep `DPPlanner` as the only supported public optimizer entrypoint.
2. Narrow `DPPlanner` to solver orchestration, DP table lifecycle, reconstruction, and terminal diagnostics.
3. Remove the remaining module-backed helper surface from `DPPlanner` and call imported functions directly from `core.py`.
4. Preserve current runtime behavior and output shape; this is a structural refactor, not an optimizer redesign.

## Architecture

### `core.py`
`custom_components/localshift/engine/core.py` remains the home of `DPPlanner`, but only for solver-specific responsibilities:
- `plan()`
- `_solve()`
- `_empty_result()`
- `_find_demand_window_bounds()`
- `_determine_terminal_penalty_idx()`
- `_initialize_dp_tables()`
- `_get_terminal_diagnostics()`
- `_backward_induction()`
- `_compute_best_action()`
- `_forward_reconstruct()`
- `_compute_terminal_shortfall()`

These methods stay because they coordinate DP tables, planner-owned reconstruction state, or result assembly.

### Helper Modules
The following files remain the single sources of truth for pure helper logic:
- `custom_components/localshift/engine/cost.py`
  - `stage_cost()`
  - `terminal_cost()`
- `custom_components/localshift/engine/constraints.py`
  - `feasible_actions()`
  - `_determine_export_actions()`
  - `check_global_solar_sufficiency()`
- `custom_components/localshift/engine/transitions.py`
  - `transition()` and transition subhelpers
- `custom_components/localshift/engine/negative_fit.py`
  - `derive_negative_fit_avoidance_context()` and supporting calculations
- `custom_components/localshift/engine/penalties.py`
  - `get_solar_opportunity_penalty_factor()`
  - `get_futile_cycling_penalty_factor()`
- `custom_components/localshift/engine/reason_codes.py`
  - `classify_reason()` and sub-classifiers
- `custom_components/localshift/engine/solar.py`
  - `can_solar_reach_target()`
  - `can_solar_reach_target_feasible()` (already extracted and stays as-is)
  - `projected_solar_soc_gain_pct()`
  - `projected_solcast_gain_pct()`
  - `get_forecast_accuracy()`

## Removal Map
The refactor removes the remaining helper methods from `DPPlanner` once their call sites use direct module imports.

| Remove from `DPPlanner` | Use instead |
| --- | --- |
| `_get_solar_opportunity_penalty_factor()` | `penalties.get_solar_opportunity_penalty_factor()` |
| `_get_futile_cycling_penalty_factor()` | `penalties.get_futile_cycling_penalty_factor()` |
| `_can_solar_reach_target()` | `solar.can_solar_reach_target()` |
| `_classify_reason()` and `_classify_*()` | `reason_codes.classify_reason()` and helpers |
| `_is_target_shortfall_risk()` | `reason_codes._is_target_shortfall_risk()` |
| `_is_cheap_import_window()` | `reason_codes._is_cheap_import_window()` |
| `_is_blind_to_future_solar()` | `reason_codes._is_blind_to_future_solar()` |
| `_projected_solar_soc_gain_pct()` | `solar.projected_solar_soc_gain_pct()` |
| `_projected_solcast_gain_pct()` | `solar.projected_solcast_gain_pct()` |
| `_get_forecast_accuracy()` | `solar.get_forecast_accuracy()` |
| `_check_global_solar_sufficiency()` | `constraints.check_global_solar_sufficiency()` |
| `_determine_export_actions()` | `constraints._determine_export_actions()` |
| `transition()` and `_transition_*()` | `transitions.transition()` and helpers |

The compatibility decision is explicit: underscore-prefixed helper methods are not treated as supported API and will be removed rather than preserved as pass-through shims.

## Data Flow
The solver flow remains the same:
1. `DPPlanner.plan()` validates inputs and delegates to `_solve()`.
2. `_solve()` computes demand-window bounds, terminal-penalty placement, negative-FIT context, and DP table setup.
3. `_backward_induction()` iterates slot/state combinations.
4. `_compute_best_action()` evaluates each feasible action by calling:
   - `feasible_actions()` for hard constraints
   - `transition()` for next-state and grid-flow math
   - `stage_cost()` for per-step scoring
   - `terminal_cost()` through initialized terminal tables
5. `_forward_reconstruct()` rebuilds the winning path and uses `classify_reason()` to produce human-readable reason codes.

The refactor changes the call graph shape, not the algorithm:
- `core.py` depends on small pure modules.
- Those helper modules do not depend on `DPPlanner`.
- Data crosses module boundaries through explicit parameters rather than planner instance methods.

## Compatibility And Contracts
- `DPPlanner` remains importable from `custom_components/localshift/engine/core.py`.
- Planner outputs, reason codes, and scenario behavior must remain unchanged.
- Tests and internal call sites that currently reach into removed helper methods must migrate to module imports or planner-level behavioral assertions.
- `transition()` keeps the real current contract: `(next_soc_pct, grid_import_kwh, grid_export_kwh)`.

## Implementation Notes
- Follow the existing extraction direction already present in the repo rather than introducing a new solver architecture.
- Prefer deleting duplicate logic only after the direct-import call path is in place and covered by tests.
- Keep helper modules pure and free of `core.py` imports to avoid circular dependencies.
- Do not move constraint logic into `stage_cost()` or preference logic into `feasible_actions()`.
- Do not introduce reserve-seeking behavior in `self_consumption` mode.

## Testing Strategy
- Shift helper-level tests away from `DPPlanner` private methods and toward direct module imports.
- Keep planner integration coverage focused on public optimizer behavior and scenario outcomes.
- Run targeted regression suites at minimum:
  - `uv run pytest tests/engine/test_core.py -v`
  - `uv run pytest tests/engine/test_terminal_cost_accuracy.py -v`
  - `uv run pytest tests/test_optimizer_dp_solve.py -v`
  - `uv run pytest tests/test_optimizer_hard_constraint.py -v`
  - `uv run pytest tests/test_optimizer_self_consumption.py -v`
- Final verification must include:
  - `uv run ruff check custom_components/localshift`
  - `uv run pytest --cov=custom_components/localshift --cov-report=term-missing`

## Acceptance Criteria
- `custom_components/localshift/engine/core.py` is reduced by roughly 300+ lines from its current baseline and is more focused on solver orchestration.
- Module-backed helper methods are removed from `DPPlanner`.
- No remaining tests depend on the removed `DPPlanner` private helper surface.
- Optimizer behavior remains consistent with the current planning model and existing scenario tests.
- Lint, targeted tests, and project coverage checks pass.

## Open Questions
None.

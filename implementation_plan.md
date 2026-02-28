# Implementation Plan

[Overview]
Implement a phased migration from the legacy multi-pass forecasting planner to a deterministic DP-based optimizer that first runs in shadow/assist modes, then (optionally) in active control mode with strong safety gates.

The current forecasting/control system is functionally rich but difficult to reason about because planning behavior emerges from multiple interacting passes, forecast-time heuristics, and runtime state-machine follow-up behavior. The immediate goal is not a one-shot rewrite, but an incremental replacement strategy that preserves operational safety, keeps Home Assistant custom-component constraints in mind (single-process Python execution, no heavy external solver dependency, predictable coordinator cycle time), and continuously validates outcomes against the incumbent planner.

The implementation should treat the optimizer as a first-class planning subsystem with clear contracts: normalized slot inputs, explicit action vocabulary, deterministic state transitions, objective term accounting, and cycle-level comparison telemetry. Rollout must remain reversible at every stage via config feature flags and control mode settings. Existing forecast-driven architecture and coordinator lifecycle remain the integration backbone; the optimizer integrates through a narrow adapter boundary (shadow runner and comparator) to avoid broad coupling.

At phase level, the feature should proceed as: **Phase A (scaffold baseline) → Phase B (input/config parity) → Phase C (real DP solve) → Phase D (comparison/analytics hardening) → Phase E (assist UX) → Phase F (active control pilot) → Phase G (stabilization/docs/release)**. This ordering prioritizes correctness and observability before behavior change.

[Types]
The type changes establish explicit planner-domain contracts that make decision logic inspectable and testable.

### Core Planning Types
- `custom_components/localshift/computation_engine_lib/optimizer_dp.py`
  - `PlannerAction (StrEnum)`
    - Values: `hold`, `charge_grid_normal`, `charge_grid_boost`, `export_proactive`
    - Validation: must map 1:1 to compat flags (`grid_charge`, `grid_charge_boost`, `proactive_export`).
  - `PlannerReasonCode (StrEnum)`
    - Action-classification taxonomy for diagnostics and future learning.
    - Must remain serializable as strings for state attributes.
  - `SlotContext (dataclass)`
    - Required fields: `slot_index: int`, `timestamp_iso: str`, `slot_interval_minutes: int`, `buy_price: float`, `sell_price: float`, `solar_kwh: float`, `consumption_kwh: float`.
    - Optional flags: `is_demand_window_entry`, `is_demand_window_slot`, `price_source`.
    - Constraints: `slot_interval_minutes > 0`, non-null numeric values, chronological sequence preserved externally.
  - `OptimizerConfig (dataclass)`
    - Encodes battery constraints, efficiencies, objective penalties, and SOC discretization (`soc_bins`).
    - Constraints: min/max SOC bounds coherent; rates and capacity > 0; bins sufficiently granular for target precision.
  - `ObjectiveTerms (dataclass)`
    - Per-step objective decomposition with `net_cost` property and `to_dict()` serialization.
  - `PlannedSlotDecision (dataclass)`
    - Output contract per slot; includes action, reason, SOC projection, import/export quantities.
  - `OptimizerInputs (dataclass)` and `OptimizerResult (dataclass)`
    - Horizon-level in/out envelopes; `OptimizerResult` must support success/failure status and detailed diagnostics.

### Comparison Types
- `custom_components/localshift/computation_engine_lib/planner_comparator.py`
  - `MismatchType (StrEnum)`
    - Includes action, quantity, target-attainment, and profitability mismatch classes.
  - `SlotMismatch (dataclass)` and `PlannerComparisonRecord (dataclass)`
    - Serializable cycle-level delta record for shadow/assist observability.

### Coordinator Data Contract Extensions
- `custom_components/localshift/coordinator_data.py`
  - Shadow fields already introduced (`optimizer_shadow_result`, `optimizer_shadow_decisions`, `optimizer_shadow_summary`, `optimizer_comparison`) remain canonical integration points.
  - Future phases may add status fields such as `optimizer_runtime_mode`, `optimizer_last_apply_status`, `optimizer_safety_block_reason` for active-mode governance.

[Files]
The file plan focuses on preserving existing planner behavior while layering optimizer capability by phase.

### New files to create
- `worktrees/issue-403/implementation_plan.md`
  - Phase-level implementation blueprint (this document).
- `worktrees/issue-403/docs/OPTIMIZER_DP_ROLLOUT.md` (Phase E/F)
  - Operator/developer rollout and safety checklist.
- `worktrees/issue-403/tests/test_optimizer_dp_solve.py` (Phase C)
  - DP solve correctness and invariants.
- `worktrees/issue-403/tests/test_optimizer_shadow_runner_integration.py` (Phase B/D)
  - Adapter/parity and serialization tests.
- `worktrees/issue-403/tests/test_optimizer_active_mode.py` (Phase F)
  - Active-mode guardrail behavior.

### Existing files to modify
- `custom_components/localshift/computation_engine_lib/optimizer_dp.py`
  - Phase C core DP implementation replacing scaffold `_solve()` stub.
- `custom_components/localshift/computation_engine_lib/optimizer_shadow_runner.py`
  - Phase B config parity mapping; Phase D richer metrics; Phase F apply-path branching.
- `custom_components/localshift/computation_engine_lib/planner_comparator.py`
  - Phase D mismatch taxonomy completion and quantity/profitability logic.
- `custom_components/localshift/computation_engine_lib/forecast_computer.py`
  - Maintain deterministic slot identity and ensure compatibility mapping remains stable.
- `custom_components/localshift/coordinator.py`
  - Phase E/F mode-aware execution, telemetry updates, safety fallback handling.
- `custom_components/localshift/coordinator_data.py`
  - Phase E/F additional runtime and safety fields.
- `custom_components/localshift/const.py`
  - Phase E/F control-mode lifecycle constants and defaults.
- `custom_components/localshift/sensor.py` and `custom_components/localshift/binary_sensor.py` (Phase E)
  - Expose optimizer status/comparison metrics for debugging and operator trust.
- `docs/ARCHITECTURE.md`
  - Add optimizer subsystem architecture and rollout state.
- `docs/FORECAST_DRIVEN_CONTROL.md`
  - Clarify legacy-plan vs optimizer-plan interaction and eventual control ownership.
- `docs/TROUBLESHOOTING.md`
  - Add optimizer-specific diagnostics and fallback procedures.

### Files not to change in early phases
- `custom_components/localshift/state_machine.py`
  - Keep transition machinery stable until active-mode pilot.
- `custom_components/localshift/battery_controller.py`
  - Reuse existing actuation surface; no direct optimizer coupling initially.

[Functions]
Function-level work should progress from adapter completeness to deterministic solve and finally active-control integration.

### New functions (planned)
- `custom_components/localshift/computation_engine_lib/optimizer_dp.py`
  - `_build_soc_grid(config: OptimizerConfig) -> list[float]`
  - `_map_soc_to_bin(soc_pct: float, grid: list[float]) -> int`
  - `_enumerate_actions(state, slot, config) -> list[PlannerAction]` (or expand `feasible_actions`)
  - `_forward_transition(...) -> TransitionResult` (optional internal dataclass)
  - `_backtrack_optimal_path(dp_tables, inputs) -> list[PlannedSlotDecision]`
- `custom_components/localshift/computation_engine_lib/optimizer_shadow_runner.py`
  - `_compute_legacy_slot_costs(legacy_slots) -> tuple[...]` for tighter net-cost parity.
  - `_derive_runtime_apply_plan(...)` (Phase F active path only).

### Modified functions (exact)
- `DPPlanner._solve(self, inputs: OptimizerInputs) -> OptimizerResult`
  - Replace HOLD-only stub with deterministic DP over `(slot_index, soc_bin)`.
- `DPPlanner.transition(...)`
  - Enforce SOC/rate/efficiency clipping and demand-window constraints correctly.
- `DPPlanner.stage_cost(...)`
  - Align import/export sign semantics and cycle penalty with project conventions.
- `PlannerComparator._compare_slot(...)`
  - Extend beyond action mismatch to quantity/profitability/target classes.
- `run_shadow_optimizer(...)`
  - Respect `optimizer_control_mode` lifecycle (`shadow`/`assist` now; `active` in gated phase).
- `LocalShiftCoordinator._compute_derived_values()` integration path
  - Maintain ordering guarantees and non-disruptive listener behavior while adding mode-aware optimizer outputs.

### Removed functions
- None planned in phase-level rollout; deprecations should be additive and reversible until post-active stabilization.

[Classes]
Class evolution focuses on making optimizer internals production-grade while minimizing external coupling.

### New classes (planned)
- `DPTransitionTrace` (optional, `optimizer_dp.py`)
  - Purpose: compact debug trace for selected states/actions in comparison diagnostics.
- `OptimizerSafetyGate` (optional, `optimizer_shadow_runner.py` or separate module)
  - Purpose: centralized active-mode admission checks (feature enabled, mode active, forecast freshness, slot alignment).

### Modified classes
- `DPPlanner`
  - From scaffold/no-op to full solver with deterministic tie-breaking and bounded complexity.
- `PlannerComparator`
  - From action-only mismatch detector to multi-dimensional plan delta analyzer.
- `LocalShiftCoordinator`
  - Add explicit optimizer runtime-mode orchestration while keeping existing state-machine interface unchanged.
- `CoordinatorData`
  - Expand optimizer telemetry and runtime status fields.

### Removed classes
- None in planned feature scope.

[Dependencies]
The feature should remain dependency-light and compatible with Home Assistant custom-component constraints.

No new external runtime dependencies are required for phase-level implementation. The DP solver should be implemented in pure Python using existing stdlib and project tooling, avoiding heavy optimization libraries (e.g., OR-Tools, SciPy) to keep deployment simple and deterministic in HA environments.

Development/testing continues with existing toolchain from `pyproject.toml` (`pytest`, `pytest-asyncio`, `pytest-xdist`, `ruff`, `homeassistant`). If benchmarking support is needed, use optional internal timing helpers rather than new packages.

[Testing]
Testing must prove safety, parity visibility, and deterministic behavior before any active-control use.

### Test strategy by phase
- Phase B (adapter parity)
  - Validate slot mapping completeness, timestamp/interval alignment, and serialization stability.
- Phase C (DP core)
  - Unit tests for feasibility, transition clipping, stage cost sign correctness, and backtracking determinism.
  - Scenario tests with representative solar/price patterns including negative FIT windows and demand-window constraints.
- Phase D (comparison)
  - Verify mismatch classification counts and top mismatch ranking behavior.
- Phase E (assist)
  - Validate diagnostics payloads and sensor attributes remain bounded/serializable.
- Phase F (active pilot)
  - Guardrail tests ensuring instant fallback to legacy behavior on any optimizer failure or safety gate miss.

### Required regression policy
- Continue running existing suite to detect unrelated baseline failures separately.
- Maintain dedicated optimizer test subset for quick iteration.
- Require deterministic snapshot-style assertions for core plan outputs on fixed inputs.

[Implementation Order]
Implement in strict phase order: complete plan parity and observability first, then solver correctness, then controlled activation.

1. **Phase A — Baseline stabilization (already scaffolded)**
   - Confirm current shadow modules and deterministic slot identity are stable.
2. **Phase B — Input/config parity completion**
   - Map all relevant options and constraints into `OptimizerConfig`; verify no hidden defaults drift.
3. **Phase C — DP solver implementation**
   - Implement full `_solve()` with SOC discretization, action feasibility, transition/cost recursion, and backtracking.
4. **Phase D — Comparator hardening**
   - Expand mismatch taxonomy and delta metrics; ensure high-signal diagnostics for debugging.
5. **Phase E — Assist-mode operator visibility**
   - Surface optimizer summaries/comparison in entities/diagnostics/docs for trust-building.
6. **Phase F — Active-mode pilot (gated)**
   - Introduce strict safety gate and reversible runtime switch; begin with conservative rollout controls.
7. **Phase G — Stabilization and documentation**
   - Tune performance, close edge cases, update architecture/troubleshooting/entity docs, finalize release notes.

## Phase A — Detailed Plan (Scaffold Baseline)

### Goal
Lock down the existing scaffolded optimizer path as a stable, deterministic foundation before adding behavioral complexity.

### Scope
- Confirm all scaffold artifacts are present and internally consistent.
- Ensure the shadow path is strictly side-effect free.
- Validate deterministic slot identity in forecast output.
- Establish baseline observability payloads used by later phases.

### Tasks
1. **Scaffold audit**
   - Verify `optimizer_dp.py`, `optimizer_shadow_runner.py`, `planner_comparator.py` are imported and reachable from runtime path.
   - Verify no direct battery-control call sites exist in shadow modules.
2. **Data contract validation**
   - Verify `CoordinatorData` shadow fields are always initialized and serialization-safe.
   - Verify `forecast_computer.py` emits `slot_index` + `timestamp_iso` for every slot.
3. **Determinism checks**
   - Repeat fixed-input planner runs and assert stable decision ordering and reason histograms.
4. **Baseline test enforcement**
   - Keep scaffold test suite green and mandatory for subsequent phase PRs.

### Deliverables
- Stable scaffold modules (already present) treated as immutable baseline.
- Documented baseline constraints in this plan.
- Passing baseline tests (`tests/test_optimizer_scaffold.py`).

### Acceptance Criteria
- Shadow mode produces only telemetry mutations (`optimizer_shadow_*`, `optimizer_comparison`).
- No runtime control behavior changes when optimizer flag is enabled in `shadow` mode.
- Forecast slot identity fields are present and unique by index.

### Risks
- Silent drift in scaffold contracts as future phases evolve.

### Mitigations
- Add CI guard tests around schema/serialization fields.
- Treat Phase A contracts as backward-compat guarantees.

## Phase B — Detailed Plan (Input/Config Parity)

### Goal
Ensure the optimizer receives the same effective planning inputs and constraints as the legacy planner so comparisons are meaningful.

### Scope
- Complete `OptimizerConfig` mapping from integration options and constants.
- Align slot context fields and semantics with legacy `daily_forecast` payload.
- Normalize demand-window context and target semantics.
- Validate pricing and energy sign conventions.

### Tasks
1. **Config mapping matrix**
   - Create explicit mapping table: option/const → optimizer config field.
   - Cover battery capacity, charge/boost/discharge rates, efficiencies, SOC bounds, target SOC, penalties, and bin granularity.
2. **Slot context parity**
   - Guarantee `slot_index`, `timestamp_iso`, `slot_interval_minutes`, `buy_price`, `sell_price`, `solar_kwh`, `consumption_kwh` are populated and typed.
   - Ensure compatibility with hybrid slot cadence (5-min/30-min).
3. **Demand-window alignment**
   - Map and persist demand-window entry/slot flags for each slot.
   - Verify target shortfall is computed against the same boundary as legacy logic.
4. **Telemetry parity checks**
   - Compare legacy aggregate import/export/net-cost calculations against optimizer serialization model.
5. **Tests**
   - Add adapter-focused tests validating mapping completeness and fallback defaults.

### Deliverables
- Completed config-to-optimizer mapping implementation in shadow runner.
- Adapter parity tests in `tests/test_optimizer_shadow_runner_integration.py`.
- Documented mapping table in this plan section (or companion rollout doc).

### Acceptance Criteria
- Every required optimizer input field is populated for every cycle.
- Unknown/missing optional values fail safe (no crashes; explicit defaults logged).
- Legacy and optimizer comparisons are based on aligned slot identity and equivalent constraint context.

### Risks
- Hidden legacy assumptions not represented in optimizer inputs.

### Mitigations
- Add strict adapter validation with explicit error messages.
- Add a “parity completeness” diagnostic key to shadow summary.

## Phase C — Detailed Plan (DP Solver Implementation)

### Goal
Replace scaffold no-op planning with a deterministic dynamic-programming solve that optimizes cost under battery and demand-window constraints.

### Scope
- Implement full `_solve()` in `DPPlanner`.
- Add SOC discretization grid and stable tie-breaking rules.
- Formalize action feasibility and transition clipping behavior.
- Ensure objective decomposition is traceable per slot.

### Tasks
1. **State-space design**
   - Define DP state as `(slot_idx, soc_bin)`.
   - Build SOC grid from `min_soc_pct` to `max_soc_pct` with `soc_bins` precision.
2. **Action evaluation loop**
   - Enumerate feasible actions per state.
   - Compute transition (`next_soc`, import/export kWh) with rate and efficiency constraints.
   - Compute stage objective terms (`import_cost`, `export_revenue`, `cycle_penalty`).
3. **Terminal objective enforcement**
   - Apply demand-window shortfall penalty using `terminal_cost` semantics.
   - Ensure target boundary timing matches parity rules from Phase B.
4. **Backtracking and decision reconstruction**
   - Reconstruct optimal action path and populate `PlannedSlotDecision` list in chronological order.
   - Populate aggregate result fields and reason histograms.
5. **Determinism and complexity controls**
   - Implement deterministic tie-break ordering for equal-cost candidates.
   - Keep solve time bounded for coordinator cycle compatibility.
6. **Tests**
   - Add solver correctness tests for representative scenarios and edge conditions.

### Deliverables
- Fully implemented `DPPlanner._solve()` with deterministic outputs.
- New solver tests in `tests/test_optimizer_dp_solve.py`.
- Baseline performance data (solve-time envelope) documented.

### Acceptance Criteria
- Same fixed inputs always produce identical full decision sequences.
- Result contains one decision per input slot with valid SOC/import/export bounds.
- Solve times remain operationally acceptable for HA custom-component runtime.

### Risks
- State explosion from high bin count and long horizons.
- Numerical artifacts around SOC discretization boundaries.

### Mitigations
- Start with conservative bin count and benchmark iteratively.
- Use explicit clipping + rounding policy at transitions.
- Add targeted tests for near-boundary SOC behavior.

## Phase D — Detailed Plan (Comparator & Analytics Hardening)

### Goal
Upgrade plan comparison from basic action mismatch detection to high-signal, decision-useful diagnostics that explain optimizer vs legacy divergence.

### Scope
- Expand mismatch classification coverage.
- Add quantity/profitability/target-attainment deltas.
- Improve top-mismatch ranking and summarization.
- Ensure comparison payloads remain compact and UI-safe.

### Tasks
1. **Mismatch taxonomy completion**
   - Implement `IMPORT_QUANTITY_MISMATCH`, `EXPORT_QUANTITY_MISMATCH`, `TARGET_ATTAINMENT_MISMATCH`, and `PROFITABILITY_MISMATCH` paths.
2. **Delta computation rigor**
   - Normalize per-slot and aggregate import/export/net-cost deltas.
   - Ensure threshold constants are documented and tested.
3. **Ranking/triage quality**
   - Rank mismatches by configurable significance score (e.g., absolute projected cost impact + action severity).
4. **Summary rollups**
   - Add concise cycle summaries for sensor/diagnostics rendering.
5. **Performance and payload control**
   - Keep `top_mismatches` bounded and serialization deterministic.
6. **Tests**
   - Add comparator-focused tests for each mismatch type and threshold edge.

### Deliverables
- Enhanced `PlannerComparator` logic and robust mismatch record generation.
- Comparator test coverage proving classification correctness.
- Stable diagnostics-ready comparison payload schema.

### Acceptance Criteria
- Comparator can explain materially different plans without manual log inspection.
- All mismatch classes are reachable under controlled test scenarios.
- Comparison payload size remains bounded and stable for HA state attributes.

### Risks
- Overly noisy mismatch reporting reducing operator confidence.

### Mitigations
- Use explicit significance thresholds and bounded top-N reporting.
- Include clear `reason_detail` strings for each mismatch category.

## Phase E — Detailed Plan (Assist-Mode UX & Observability)

### Goal
Make optimizer behavior understandable and actionable to operators by exposing reliable assist-mode telemetry, diagnostics, and documentation before active control is considered.

### Scope
- Expand sensor/diagnostics visibility for shadow and assist modes.
- Surface cycle-level summaries, mismatch trends, and failure reasons.
- Document interpretation guidance for operators.

### Tasks
1. **Assist-mode summaries**
   - Add concise `optimizer_shadow_summary` fields for solve status, timing, slot count, and key deltas.
2. **Diagnostics integration**
   - Extend diagnostics output with optimizer runtime details and recent comparison snapshots.
3. **Entity surface updates**
   - Add/extend sensor attributes to expose actionable optimizer stats without overwhelming payload size.
4. **Operator documentation**
   - Add `docs/OPTIMIZER_DP_ROLLOUT.md` with mode definitions (`shadow`, `assist`, future `active`), troubleshooting, and interpretation examples.
   - Update `docs/ARCHITECTURE.md` and `docs/TROUBLESHOOTING.md` with optimizer sections.
5. **Tests**
   - Validate telemetry field presence, serialization stability, and bounded attribute payloads.

### Deliverables
- Assist-mode observability surface across sensors/diagnostics/docs.
- Rollout guide for interpreting comparison outputs.

### Acceptance Criteria
- Operators can identify why optimizer differs from legacy in a given cycle using UI-visible data.
- Diagnostic payloads remain stable and bounded.
- Assist mode remains non-invasive (no control actuation path).

### Risks
- Attribute payload bloat causing truncation or degraded UX.

### Mitigations
- Keep top-N mismatch lists bounded.
- Provide condensed summaries plus “details in diagnostics” structure.

## Phase F — Detailed Plan (Active-Control Pilot)

### Goal
Introduce an optional, tightly gated active-control mode where optimizer decisions can drive runtime behavior with immediate fallback protections.

### Scope
- Add active-mode admission/safety gate logic.
- Define integration path from optimizer decisions to existing control surfaces.
- Preserve legacy planner as authoritative fallback.
- Pilot with conservative activation controls and rich failure telemetry.

### Tasks
1. **Safety gate implementation**
   - Validate prerequisites each cycle: feature enabled, control mode active, solve success, slot alignment valid, forecast freshness acceptable.
   - Block active path on any failed gate and emit explicit reason.
2. **Apply-path design**
   - Map `PlannedSlotDecision.action` to existing mode/actuation interfaces without bypassing safety conventions.
   - Ensure unsupported/ambiguous actions fall back to legacy decisions.
3. **Fallback policy**
   - On optimizer error, timeout, or invalid payload: revert immediately to legacy control for current cycle.
   - Keep fallback sticky for cooldown window if repeated optimizer failures occur.
4. **Pilot controls**
   - Add/confirm explicit active-mode setting and guard rails (off by default).
   - Support staged rollout criteria (e.g., minimum recent shadow success rate before enabling active).
5. **Tests**
   - Add active-mode tests for admission success, blocked conditions, and fallback behavior.

### Deliverables
- Active-mode pilot implementation behind strict gate.
- Runtime status fields indicating apply success/failure and safety-block reason.
- Active-mode test coverage for safety-critical branches.

### Acceptance Criteria
- Active mode can be toggled on only when configured and safe.
- Any optimizer failure path defaults to legacy control within same cycle.
- No unsafe battery-control commands are issued from malformed optimizer outputs.

### Risks
- Misapplied optimizer action causing undesired battery behavior.

### Mitigations
- Centralized gate + fallback.
- Conservative rollout (opt-in, default off, explicit diagnostics).

# Documentation Index for Agents

## Current vs Historical Docs

**Current (Authoritative):** PLANNING_MODEL.md, ARCHITECTURE.md, ENTITY_REFERENCE.md, DEVELOPER_GUIDE.md, LOAD_SHIFTING_GUIDE.md, INDEX.md

**Historical / Design Context:** FORECAST_DRIVEN_CONTROL.md, OPTIMIZER_DP_ROLLOUT.md, CHANGE_DETECTION.md, LEARNING_SYSTEM.md, LOAD_FORECASTING.md, NOTIFICATIONS.md, TROUBLESHOOTING.md

> **Note:** Historical docs contain valuable context but may contain stale terminology or outdated examples. Always verify against current code.

---

## Quick Reference

Use this index to quickly determine which documentation files to consult before working on different parts of the codebase.

---

## Optimizer Changes (DP Planner)

**Any modification to `engine/optimizer_dp.py`, `engine/constraints.py`, or related planning logic.**

### Must Read (Critical)
- **[PLANNING_MODEL.md](PLANNING_MODEL.md)** — Core principles and constraint guide
  - Defines the soft-constrained DP pattern: `feasible_actions()` / `stage_cost()` / `terminal_cost()`
  - When to add to each function
  - Pure/stateless requirements

### Should Read
- **[OPTIMIZER_DP_ROLLOUT.md](OPTIMIZER_DP_ROLLOUT.md)** — Rollout patterns and safety gates
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — Common optimizer issues

### Key Constraints

| Constraint | Location | Description |
|------------|----------|-------------|
| Hard constraints only | `feasible_actions()` | What the optimizer CAN do |
| Soft preferences | `stage_cost()` | Penalties for undesirable choices |
| Deadline requirements | `terminal_cost()` | Must-achieve goals by horizon end |
| Purity | All optimizer code | Deterministic, stateless, no side effects |
| Feasibility check | All changes | Ask: "Does this belong in feasible_actions(), stage_cost(), or terminal_cost()?" |

### Anti-Patterns
- ✗ Adding soft preferences to `feasible_actions()` (hard constraints only)
- ✗ Modifying cost without adjusting `terminal_cost()` (imbalance)
- ✗ Introducing state or randomness (breaks determinism)
- ✗ Forgetting to document constraint changes

### Related Files
- `engine/optimizer_dp.py` — Main DP solver
- `engine/constraints.py` — Hard constraint functions
- `engine/optimizer_runner.py` — Coordinator integration
- `tests/test_optimizer_dp_solve.py` — Test patterns

---

## Entity Platform Changes

**Adding, removing, or modifying sensor, binary_sensor, switch, number, select, or button entities.**

### Must Read
- **[ENTITY_REFERENCE.md](ENTITY_REFERENCE.md)** — Complete entity catalog with descriptions
- **[AGENTS.md](../AGENTS.md)** — Root agent rules (Critical section)

### Should Read
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — Entity platform responsibilities

### Key Constraints

| Constraint | Description |
|------------|-------------|
| Entity count limits | Sensors: 30, Binary: 10, Switches: 8, Numbers: 4, Selects: 2, Buttons: 2 |
| Read-only pattern | Entities read from `self.coordinator.data`, don't compute |
| Platform files | Each platform in its own file (sensor.py, switch.py, etc.) |
| Documentation required | MUST update `ENTITY_REFERENCE.md` for every add/remove |
| Architecture changes | If entity dependencies change, update `ARCHITECTURE.md` |
| Async only | All platform setup uses `async_forward_entry_setups()` |

### Entity Pattern
```python
class LocalShiftSensor(SensorEntity):
    def __init__(self, coordinator, key: str) -> None:
        self.coordinator = coordinator
        self._key = key

    @property
    def native_value(self) -> Any:
        return self.coordinator.data.get(self._key)
```

### Related Files
- `sensor.py`, `binary_sensor.py`, `switch.py`, `number.py`, `select.py`, `button.py`
- `coordinator/data.py` — `CoordinatorData` structure
- `tests/` — Test patterns in `tests/AGENTS.md`

---

## State Machine Changes

**Modifying `state/machine.py`, transition logic, or debounce behavior.**

### Must Read
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — State machine section (lines 415-527)

### Should Read
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — State transition issues

### Key Constraints

| Mechanism | Value | Purpose |
|-----------|-------|---------|
| Debounce timers | 2-5 minutes | Prevent rapid oscillations |
| Validation timeout | 10 seconds | Wait for hardware confirmation |
| Health check cooldown | 5 minutes | Avoid command spam |
| State change handling | Synchronous notify + async evaluate | Race-free transitions |

### Critical Rules
- Debounce timers reset when mode changes away (full debounce from continuous stable period)
- Validation timeout reduced from 20s → 10s (early-exit on success)
- Health check only re-issues correction if `_MIN_CORRECTION_INTERVAL` elapsed
- Always notify HA listeners in `try/finally` blocks

### Anti-Patterns
- ✗ Short-circuiting debounce (must serve full timer from stable period)
- ✗ Blocking notify in exception paths (use `try/finally`)
- ✗ Ignoring Teslemetry state lag (15-30s cloud delay)

---

## Forecast System Changes

**Modifying forecast computation, solar/load prediction, or accuracy tracking.**

### Must Read
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — Forecast computer and data flow
- **[FORECAST_DRIVEN_CONTROL.md](FORECAST_DRIVEN_CONTROL.md)** — Design rationale

### Should Read
- **[LOAD_FORECASTING.md](LOAD_FORECASTING.md)** — Load prediction methods
- **[CHANGE_DETECTION.md](CHANGE_DETECTION.md)** — When forecasts trigger recompute

### Key Constraints

| Principle | Description |
|-----------|-------------|
| Forecast as plan | The forecast IS the battery control plan |
| Change detection | Only recompute on significant changes (price, SOC, forecast age, solar updates) |
| Near-term correction | 1-minute fast tick detects load deviation (>1kW for 10min, >3kW for 5min) |
| Granularity | 5-minute slots for near-term (2h), 15-minute for long-term (22h) |
| Multi-horizon | Two-tier slot structure (5-min + 15-min) in one timeline |

### Change Detection Triggers
- Price change (any change in buy/sell)
- SOC change (≥1% threshold)
- Forecast age (>5 minutes)
- Solar forecast updates

### Anti-Patterns
- ✗ Recomputing on every state change (wasteful)
- ✗ Ignoring near-term deviation (misses correction opportunities)
- ✗ Single granularity for entire horizon (inefficient)

---

## Learning System Changes

**Modifying the adaptive learning system (`learning/`, `engine/parameters.py`, etc.).**

### Must Read
- **[LEARNING_SYSTEM.md](LEARNING_SYSTEM.md)** — Complete architecture and data flow
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — Learning system section (lines 593-723)

### Should Read
- **[LEARNING_SYSTEM.md](LEARNING_SYSTEM.md)** — Learning system architecture (verify against current code)

### Key Constraints

| Component | Purpose |
|-----------|---------|
| DecisionOutcomeTracker | Records decisions and backfills outcomes |
| ParameterOptimizer | Thompson sampling for parameter tuning |
| PatternAnalyzer | Detects systematic biases (weekly) |
| OptimizationController | Real-time contextual adjustments |

### Safety Rails
- Warm-up: No adjustments until 50+ decisions
- Step limits: Max 1 step per daily update
- Bounds: All parameters within defined min/max
- Rollback: Revert if 7-day score decreases for 3 consecutive days

### Adaptive Parameters
```yaml
cheap_price_bias: [-5.0, +5.0] c/kWh
solar_confidence_factor: [0.5, 1.5]
overnight_drain_safety_margin: [-5.0, +10.0] %
grid_charge_soc_headroom: [-5.0, +10.0] %
export_threshold_adjustment: [-3.0, +3.0] c/kWh
consumption_forecast_bias: [-0.5, +0.5] kW
```

### Multi-Objective Scoring
```
score = 0.50 × cost_score
      + 0.20 × export_avoidance_score
      + 0.20 × target_achievement_score
      + 0.10 × cycle_reduction_score
```

### Anti-Patterns
- ✗ Adjusting parameters before warm-up period
- ✗ Ignoring step limits (causes instability)
- ✗ Breaking purity (learning system must be side-effect-free)

---

## Config Flow / Integration Changes

**Modifying `config_flow/` or integration options.**

### Must Read
- **[DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)** — Development patterns
- **[AGENTS.md](AGENTS.md)** — Reference index (legacy, see note above)

### Should Read
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — Config flow placement

### Key Constraints
- Config flow is mandatory for HA integrations
- Options flow handles runtime configuration changes
- Validation in `validators.py`, schemas in `schemas.py`
- User-facing strings in `strings.json`

---

## General Development

**Any other code change.**

### Must Read
- **[AGENTS.md](../AGENTS.md)** — Root rules (worktree, TDD, coverage)
- **[docs/AGENTS.md](AGENTS.md)** — This index

### Should Read
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — System overview
- **[DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)** — Patterns and conventions

### Critical Rules (All Tasks)

1. **Worktree required** — Never work on `main` or `test` directly
2. **TDD required** — Write failing test first, then implement
3. **Coverage ≥95%** — `uv run pytest --cov=custom_components/localshift --cov-report=term-missing`
4. **Python 3.13+** — With `from __future__ import annotations`
5. **Type hints required** — On all public APIs
6. **SymDex navigation** — Use `symdex_search_symbols()` instead of grep

---

## How to Use This Index

**As an agent:** When tasked with code modification, consult this index first to determine which docs are mandatory. Use `symdex_search_text()` to quickly extract key constraints from those docs.

**As a developer:** Keep this file updated when adding new subsystems. Add new sections with "Must Read" and "Key Constraints" tables.

**As a reviewer:** Verify that the agent consulted relevant docs before proposing changes.

---

## Quick Commands

```bash
# Search indexed docs
symdex_search_text("feasible_actions constraint")
symdex_search_text("debounce timer")

# Read full doc if needed
Read docs/PLANNING_MODEL.md
Read docs/ARCHITECTURE.md

# Verify documentation requirements
# (Check that relevant sections above were followed)
```

---

## Updating This Index

When adding new subsystems or major features:

1. Add a new section with "Must Read" and "Should Read"
2. Create a "Key Constraints" table summarizing critical rules
3. Add an "Anti-Patterns" list if needed
4. Reference specific file locations where applicable

Keep it concise — agents use this to decide what to read, not to learn everything.

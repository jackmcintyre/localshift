# Backlog Item: Scenario-Based Simulation Framework

**ID:** backlog-med-023  
**Priority:** MED  
**Status:** PROPOSED  
**Created:** 2026-02-19  
**Updated:** 2026-02-19  

---

## Summary

Create a scenario-based simulation framework to validate battery automation logic against expected outcomes, with pre-commit hook integration for automated regression testing.

---

## Description

The battery automation logic involves complex interactions between:
- Grid charging decisions (cheap price detection, boost charging)
- Proactive export decisions (FIT price optimization)
- Demand window constraints
- Solar forecasting and SOC predictions

Currently, changes to this logic are tested with unit tests for specific functions, but there's no way to validate end-to-end behavior against known scenarios. A simulation framework would:

1. Capture real-world scenarios from Home Assistant states
2. Define expected outcomes for each scenario
3. Run simulations during pre-commit to catch regressions
4. Provide clear pass/fail feedback with diffs

---

## Architecture

```
simulations/
├── __init__.py
├── conftest.py                   # Pytest fixtures for scenario loading
├── scenario.py                   # Scenario dataclass & YAML loader
├── runner.py                     # CLI entry point for running simulations
├── comparator.py                 # Compare actual vs expected results
├── capture_from_ha.py            # Called by HA automation to capture scenarios
├── scenarios/
│   ├── _template.yaml            # Template for new scenarios
│   ├── captured/                 # Auto-captured scenarios from HA
│   │   └── scenario_{timestamp}.yaml
│   └── grid-charging/            # Organized by category
│       └── cheap-overnight.yaml
└── tests/
    └── test_scenarios.py         # Pytest wrapper for CI integration
```

---

## Scenario Format (YAML)

```yaml
name: "Cheap Overnight Grid Charge"
description: "Battery at 40%, cheap price at 2am, should grid charge"
status: "approved"  # captured | reviewed | approved

input:
  time: "2026-02-16T02:00:00+11:00"
  soc: 40
  general_price: 0.08
  feed_in_price: 0.02
  battery_target: 90
  demand_window_start: "18:00"
  demand_window_end: "22:00"
  
  solcast_today:
    - period_start: "2026-02-16T06:00:00+11:00"
      pv_estimate10: 2.5
      
  general_forecast:
    - start_time: "2026-02-16T02:00:00+11:00"
      per_kwh: 0.08
      
  feed_in_forecast:
    - start_time: "2026-02-16T02:00:00+11:00"
      per_kwh: 0.02
      
  switches:
    automation_enabled: true
    spike_discharge_enabled: true
    demand_window_block: false

expected:
  active_mode: "GRID_CHARGING"
  grid_import_kwh: ">0"
  effective_cheap_price: "<0.15"
  daily_forecast:
    - hour: 18
      predicted_soc: ">=90"
```

---

## Components

### 1. Scenario Dataclass (`scenario.py`)

```python
@dataclass
class Scenario:
    name: str
    description: str
    status: str  # captured, reviewed, approved
    input: ScenarioInput
    expected: dict[str, Any]
    
@dataclass
class ScenarioInput:
    time: datetime
    soc: float
    general_price: float
    feed_in_price: float
    # ... all fields needed for CoordinatorData
```

### 2. HA Capture Script (`capture_from_ha.py`)

Called by Home Assistant automation via shell_command:
- Calls HA REST API to fetch all relevant entity states
- Builds scenario YAML with input data populated
- Runs simulation to populate expected values
- Saves to `simulations/scenarios/captured/scenario_{timestamp}.yaml`
- Sets status: "captured" (needs human review)

**HA Configuration:**
```yaml
# configuration.yaml
shell_command:
  capture_scenario: "python3 /path/to/simulations/capture_from_ha.py --name {{ name }}"

# scripts/capture_scenario.yaml
alias: Capture Simulation Scenario
sequence:
  - service: shell_command.capture_scenario
    data:
      name: "scenario_{{ now().strftime('%Y%m%d_%H%M') }}"
```

### 3. Simulation Runner (`runner.py`)

CLI entry point:
```bash
python -m simulations.runner --all                    # Run all approved scenarios
python -m simulations.runner scenarios/grid-charging/ # Run specific directory
python -m simulations.runner --fail-fast              # Stop on first failure
```

Process:
1. Load scenario YAML
2. Create mock `CoordinatorData` with input values
3. Create `ComputationEngine` with mocked dependencies
4. Call `engine.compute_derived_values(data)`
5. Compare actual outputs against expected values
6. Return pass/fail with detailed diff

### 4. Result Comparator (`comparator.py`)

Supports flexible expectation syntax:
- Exact match: `active_mode: "GRID_CHARGING"`
- Comparison: `grid_import_kwh: ">0"`, `effective_cheap_price: "<0.15"`
- Range: `predicted_soc: "80-95"`
- Nested checks: `daily_forecast[].predicted_soc`

### 5. Pre-Commit Integration

```yaml
# .pre-commit-config.yaml
- repo: local
  hooks:
    - id: simulation-tests
      name: Simulation Tests
      entry: python -m simulations.runner --all --fail-fast
      language: system
      pass_filenames: false
      always_run: true
```

### 6. Pytest Integration (`tests/test_scenarios.py`)

```python
import pytest
from simulations.scenario import discover_scenarios
from simulations.runner import run_scenario

@pytest.fixture(params=discover_scenarios("simulations/scenarios/"), ids=lambda s: s.name)
def scenario(request):
    return request.param

def test_scenario(scenario):
    if scenario.status != "approved":
        pytest.skip(f"Scenario not approved: {scenario.status}")
    result = run_scenario(scenario)
    assert result.passed, result.report
```

---

## Workflow

### Capture Phase (Manual)
1. User notices interesting situation in HA
2. Triggers HA script to capture scenario
3. Script saves YAML with `status: captured`
4. Expected values auto-populated from current computation

### Review Phase (Manual)
1. User opens captured YAML
2. Reviews expected values
3. Corrects any that don't match desired behavior
4. Changes status to `approved`
5. Commits to repo

### Validation Phase (Automated)
1. Pre-commit hook runs on every commit
2. Loads all `status: approved` scenarios
3. Runs simulations
4. PASS: commit proceeds
5. FAIL: commit blocked with diff output

---

## Output Examples

### Pass Output
```
SIMULATION PASSED: 15 scenarios
  ✓ grid-charging/cheap-overnight.yaml (3/3 checks)
  ✓ grid-charging/boost-before-dw.yaml (4/4 checks)
  ✓ proactive-export/high-fit-afternoon.yaml (2/2 checks)
```

### Fail Output
```
SIMULATION FAILED: scenarios/grid-charging/cheap-overnight.yaml

Check: active_mode
  Expected: GRID_CHARGING
  Actual:   SELF_CONSUMPTION
  Reason: effective_cheap_price (0.18) > general_price (0.12)

Check: grid_import_kwh
  Expected: >0
  Actual:   0.0
  Status:   FAILED

Commit blocked. Fix code or update expected values if behavior changed.
```

---

## Affected Files

New files to create:
- `simulations/__init__.py`
- `simulations/conftest.py`
- `simulations/scenario.py`
- `simulations/runner.py`
- `simulations/comparator.py`
- `simulations/capture_from_ha.py`
- `simulations/scenarios/_template.yaml`
- `simulations/tests/test_scenarios.py`

Files to modify:
- `.pre-commit-config.yaml` (add simulation hook)

---

## Implementation Steps

1. Create `simulations/` directory structure
2. Implement `scenario.py` with YAML loader
3. Implement `comparator.py` with flexible matching
4. Implement `runner.py` with CLI interface
5. Implement `capture_from_ha.py` for HA integration
6. Add pytest fixtures in `conftest.py`
7. Create `test_scenarios.py` for pytest integration
8. Add pre-commit hook configuration
9. Create template and example scenarios
10. Add documentation

---

## Notes

- Scenarios with `status: captured` are skipped by pre-commit (allows incremental build-up)
- Only `status: approved` scenarios block commits on failure
- This encourages building a knowledge base over time without blocking workflow
- The framework reuses existing `ComputationEngine` - no code duplication

---

## Related Items

- backlog-crit-002: Missing Unit Tests for State Machine
- backlog-med-012: Binary Sensors Include Redundant "binary" in Names
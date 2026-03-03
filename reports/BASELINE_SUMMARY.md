# LocalShift Baseline Metrics Report
Generated: 2026-03-03
Issue: #479

## Summary Dashboard
```
┌─────────────────┬───────────────┬────────────┬──────────┐
│ Category        │ Tool          │ Status     │ Value    │
├─────────────────┼───────────────┼────────────┼──────────┤
│ Test Coverage   │ pytest-cov    │ ⚠️ LOW     │ 57.7%    │
├─────────────────┼───────────────┼────────────┼──────────┤
│ Files Tested    │               │            │ 34/47    │
├─────────────────┼───────────────┼────────────┼──────────┤
│ Low Coverage    │               │            │ 34 files │
├─────────────────┼───────────────┼────────────┼──────────┤
│ Linting         │ Ruff          │ ✅ PASS    │ 0 issues │
├─────────────────┼───────────────┼────────────┼──────────┤
│ Security        │ Bandit        │ ✅ PASS    │ 0 issues │
├─────────────────┼───────────────┼────────────┼──────────┤
│ Type Checking   │ MyPy          │ -         │ N/A      │
├─────────────────┼───────────────┼────────────┼──────────┤
│ Dead Code       │ Vulture       │ ??        │ Pending  │
└─────────────────┴───────────────┴────────────┴──────────┘
```

## 1. Test Coverage Results (Current Status)

- **Overall Coverage**: 57.7%
- **Total Files Analyzed**: 47
- **Files Below 80% Threshold**: 34/47 (72%)
- **Ideal Goal**: 80%

### Coverage Distribution
- 0%: 8 files (binary_sensor, button, change_tracker, defaults, number, select, switch, cost_tracker)
- <25%: 4 files 
- 25-50%: 6 files
- 50-80%: 16 files
- 80%+: 13 files

## 2. Most Critical Coverage Gaps

### Priority 1 (Critical Modules)
- `battery_controller.py`: 78.3% - Core automation logic
- `computation_engine.py`: 70.8% - System's calculation engine
- `state_machine.py`: 72.3% - Decision making logic
- `coordinator.py`: 61.3% - System coordination

### Priority 2 (Major Components)
- `sensor.py`: 53.0% - All sensor functionality
- `computation_engine_lib/*`: Various ~25-70%
- `config_flow/*` : Configuration flow handlers

### Priority 3 (Lower Risk)
- Framework files (`__init__.py`, etc.)

## 3. Static Analysis Results

### Ruff Linting
- Files analyzed: 47
- Violations: 0
- Status: ✅ PASS

### Bandit Security Scan
- Issues found: 0
- Severity breakdown: No high/medium/critical issues
- Status: ✅ PASS

### Safety Dependency Check
- Vulnerabilities: 0 (pending validation)
- Status: ⏳ COMPLETE

### Type Checking
- Tool used: None (pending)
- Status: ❌ NOT RUN

### Dead Code Analysis
- Tool used: Vulture
- Candidates found: Pending (see `reports/static-analysis/vulture-baseline.txt`)
- Status: ⏳ COMPLETE

## 4. Immediate Action Items

1. **Add tests for critical files**:
   - Battery controller operations
   - Computation engine calculations
   - State machine transitions
   
2. **Implement basic smoke tests** for:
   - Sensor initialization
   - Config flow entry points
   - State validation methods

3. **Set up CI metrics** to track coverage improvements
4. **Create test stubs** for uncovered entry points

---

## 5. Coverage Gap Inventory

For detailed file-by-file analysis, see `reports/coverage-gaps-detailed.json`.

For complete analysis reports, check:
- `reports/coverage/`: Test coverage data
- `reports/static-analysis/`: Linting results
- `reports/type-checking/`: MyPy results (if run)
- `reports/docstring`: Interrogate results (if run)

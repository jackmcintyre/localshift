# Test Coverage Tracking

## Current Status

| Metric | Value |
|--------|-------|
| **Overall Coverage** | 23% (baseline) |
| **Target** | 95% (long-term) |
| **Pre-commit Check** | Per-file (95% for modified files) |
| **CI Check** | No regression below 23% |

## Coverage Policy

### Pre-commit (Local)

The TDD pre-commit hook enforces **95% coverage for modified files only**.

- Only files you change need to meet the 95% threshold
- Legacy code is exempt until modified
- `__init__.py` files are excluded (no testable code)

### CI (Pull Requests)

CI checks that overall coverage doesn't regress below the baseline (23%).

- Prevents accidental coverage drops
- Baseline can be raised over time as coverage improves

## High-Priority Modules for Testing

These modules have low coverage but high impact on system reliability:

| Module | Current | Priority |
|--------|---------|----------|
| `optimizer_runner.py` | 18% | High |
| `coordinator.py` | Low | High |
| `state_reader.py` | Low | Medium |
| `notification_service.py` | Low | Medium |

## How to Improve Coverage

1. **Run coverage report:**
   ```bash
   uv run pytest --cov=custom_components/localshift --cov-report=term-missing
   ```

2. **Find uncovered lines:**
   Look for lines marked with `MISSING` in the report.

3. **Write tests:**
   Follow TDD workflow in `.agents/rules/tdd-workflow.md`.

## Raising the Baseline

When overall coverage improves significantly:

1. Update the baseline in `.github/workflows/test.yml`:
   ```yaml
   if (( $(echo "$CURRENT < NEW_BASELINE" | bc -l) )); then
   ```

2. Update this document with new baseline.

3. Celebrate improved test coverage!

## History

| Date | Coverage | Notes |
|------|----------|-------|
| 2025-03-07 | 23% | Initial baseline, switched to per-file coverage |

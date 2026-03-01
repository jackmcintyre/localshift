# DP Optimizer Rollout Guide

This document describes the DP (Dynamic Programming) optimizer rollout phases and how to interpret the optimizer comparison data.

## Overview

The DP optimizer is a deterministic planning subsystem that computes optimal battery control decisions using dynamic programming. It runs alongside the legacy planner in **shadow mode** to enable A/B comparison before any control changes.

## Modes

| Mode | Description | Control Behavior |
|------|-------------|------------------|
| `shadow` | Optimizer runs but legacy planner controls | Legacy decisions applied |
| `assist` | Optimizer provides recommendations | Legacy decisions applied |
| `active` | Optimizer controls battery (future) | Optimizer decisions applied |

**Current Status**: Shadow/Assist mode only. The optimizer does NOT control the battery.

## Enabling the Optimizer

The optimizer is controlled via integration configuration:

1. Go to **Settings → Devices & Services → LocalShift**
2. Click **Configure**
3. Toggle **Enable Optimizer** (or similar option)

When enabled, the optimizer runs every coordinator cycle and populates shadow telemetry sensors.

## Interpreting the Comparison Sensor

The `sensor.localshift_optimizer_comparison` sensor shows the side-by-side comparison between legacy and optimizer plans.

### State Values

| State | Meaning |
|-------|---------|
| `None` | Optimizer disabled or no data |
| `-1` | Comparison failed |
| `0` | Plans match perfectly |
| `N > 0` | N slots differ between plans |

### Key Attributes

| Attribute | Description |
|-----------|-------------|
| `net_cost_delta` | Optimizer cost - Legacy cost (negative = optimizer cheaper) |
| `import_kwh_delta` | Optimizer import - Legacy import (negative = optimizer imports less) |
| `export_kwh_delta` | Optimizer export - Legacy export (positive = optimizer exports more) |
| `mismatch_by_type` | Count of mismatches by classification type |
| `top_mismatches` | Top 5 slots with largest disagreements |
| `legacy_meets_dw_target` | Whether legacy plan reaches demand window SOC target |
| `optimizer_meets_dw_target` | Whether optimizer plan reaches demand window SOC target |

### Cost Delta Interpretation

- **Negative `net_cost_delta`**: Optimizer projects lower cost than legacy
- **Positive `net_cost_delta`**: Legacy projects lower cost than optimizer
- Values are in dollars per forecast horizon (typically 24 hours)

## Mismatch Types

| Type | Description | Example |
|------|-------------|---------|
| `ACTION_MISMATCH` | Different action types chosen | Legacy: hold, Optimizer: charge |
| `IMPORT_QUANTITY_MISMATCH` | Same action, different import qty | Both charge but different kWh |
| `EXPORT_QUANTITY_MISMATCH` | Same action, different export qty | Both export but different kWh |
| `TARGET_ATTAINMENT_MISMATCH` | DW target met by only one plan | Legacy meets target, optimizer doesn't |
| `PROFITABILITY_MISMATCH` | Action differs due to cost optimization | Optimizer avoids costly legacy action |

## When to Trust the Optimizer

The optimizer is trustworthy when:

1. **Low mismatch count** (0-3 slots differ)
2. **Negative net_cost_delta** (optimizer cheaper)
3. **Both meet DW target** or optimizer meets it better
4. **Parity completeness ≥ 95%** (input data quality)
5. **Alignment valid** (slots properly matched)

## Diagnostics

Download diagnostics from **Settings → Devices & Services → LocalShift → Download Diagnostics**.

The diagnostics include an `optimizer` section with:

- Enabled status and mode
- Last cycle success/failure
- Solve time in seconds
- Parity completeness percentage
- Comparison summary (mismatch count, cost delta)
- Top 3 mismatches with details

## Rollback / Safety

The optimizer is **non-invasive** in shadow/assist modes:

- No battery control commands are sent
- Legacy planner remains authoritative
- Disabling the optimizer immediately stops shadow computation
- All comparison data is retained for analysis

To disable:
1. Go to **Settings → Devices & Services → LocalShift**
2. Click **Configure**
3. Toggle off **Enable Optimizer**

## FAQ

### Why is the optimizer more expensive than legacy?

Possible reasons:
- Different SOC trajectory assumptions
- More conservative demand window preparation
- Efficiency penalties modeled differently
- Input data differences (check parity_completeness_pct)

### Why do plans differ even when prices are similar?

Small differences can accumulate from:
- SOC discretization (50 bins by default)
- Tie-breaking rules (deterministic but may differ from legacy heuristics)
- Cycle penalty calculations
- Solar forecast weighting

### How do I know if the optimizer is ready for active mode?

Active mode should only be enabled after:
1. Running in shadow mode for at least 7 days
2. Consistent optimizer advantages (negative net_cost_delta)
3. High parity completeness (>98%)
4. Zero comparison failures
5. Understanding mismatch patterns

Active mode is a **future feature** and is not yet available.

## Related Sensors

| Sensor | Purpose |
|--------|---------|
| `sensor.localshift_optimizer_shadow_plan` | Per-slot optimizer decisions |
| `sensor.localshift_optimizer_shadow_summary` | Aggregate optimizer metrics |
| `sensor.localshift_optimizer_comparison` | Legacy vs optimizer comparison |

## Related Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Troubleshooting guide
- [FORECAST_DRIVEN_CONTROL.md](FORECAST_DRIVEN_CONTROL.md) - Forecast-based control

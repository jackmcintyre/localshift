# DP Optimizer Rollout Guide

> **⚠️ Historical Document** — The DP optimizer migration is complete. Shadow and assist modes were removed. This doc is retained for historical context only; the optimizer is now the sole control path.
> 
> **Current authoritative doc:** [PLANNING_MODEL.md](PLANNING_MODEL.md)

---

> **Rollout Complete (March 2026)** — The DP optimizer migration is finished. The optimizer is now the sole control path. Shadow and assist modes were removed in #448. This document remains as a historical reference for the rollout process and comparison methodology.

This document describes the DP (Dynamic Programming) optimizer rollout phases and how to interpret the optimizer comparison data.

## Overview

The DP optimizer is a deterministic planning subsystem that computes optimal battery control decisions using dynamic programming. It runs alongside the legacy planner in **shadow mode** to enable A/B comparison before any control changes.

## Modes

| Mode | Description | Control Behavior |
|------|-------------|------------------|
| `shadow` | Optimizer runs but legacy planner controls | Legacy decisions applied |
| `assist` | Optimizer provides recommendations | Legacy decisions applied |
| `active` | Optimizer controls battery (with safety gate) | Optimizer decisions applied |

**Current Status**: All modes supported. In shadow/assist mode, the optimizer does NOT control the battery. In active mode, optimizer controls the battery with safety gates.

## Enabling the Optimizer

The optimizer is controlled via integration configuration:

1. Go to **Settings → Devices & Services → LocalShift**
2. Click **Configure**
3. Toggle **Enable Optimizer** (or similar option)

When enabled, the optimizer runs every coordinator cycle and populates shadow telemetry sensors.

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

### What happens when active mode is enabled?

When active mode is enabled:
1. The safety gate checks all prerequisites each cycle
2. If all checks pass, optimizer decisions are applied
3. If any check fails, it falls back to legacy control immediately
4. Repeated failures trigger a cooldown period before re-attempting
5. You can always disable active mode or the optimizer entirely

### What's the safety gate?

The safety gate validates:
- Optimizer is enabled
- Control mode is set to "active"
- Last solve was successful
- Forecast data is fresh (within 30 minutes)
- Not in cooldown period after recent failures

## Related Sensors

| Sensor | Purpose |
|--------|---------|
| `sensor.localshift_optimizer_plan_detailed` | Per-slot optimizer decisions |
| `sensor.localshift_optimizer_summary` | Aggregate optimizer metrics |

## Related Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Troubleshooting guide
- [FORECAST_DRIVEN_CONTROL.md](FORECAST_DRIVEN_CONTROL.md) - Forecast-based control

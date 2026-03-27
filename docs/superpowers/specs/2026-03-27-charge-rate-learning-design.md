# Charge-Rate Learning for Grid Charge/Boost

**Date:** 2026-03-27
**Status:** Approved
**Issue:** Grid charge/boost planning ignores SOC-dependent charge slowdown; boost still planned too late at high SOC.

## Problem

The optimizer assumes fixed grid and boost charge rates (3.3 kW / 5.0 kW). In practice, battery charging slows as SOC increases. This causes grid charge/boost to be scheduled too late, especially near demand window/high SOC, because the planner overestimates how fast the battery will charge.

## Goals

- Learn SOC-dependent effective charge rates from device telemetry.
- Integrate learned rates into the optimizer at its slot cadence so planning reflects real charge slowdowns.
- Keep defaults as fallback when learning is disabled or data is insufficient.
- Replace the blunt 80% boost cap with learned tapering behavior.

## Non-Goals

- Changing discharge modeling or load forecasting.
- Introducing new UI entities in this phase.
- Rewriting the optimizer or planning model structure.

## Solution

Build a learning pipeline that derives SOC-dependent effective charge rates for grid normal and grid boost using Home Assistant telemetry. Apply these learned rates per slot when constructing the optimizer configuration, so the planner’s timing adapts to slower high-SOC charging.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Data source | HA telemetry (`sensor.my_home_battery_power`, `sensor.my_home_percentage_charged`) | Device-reported values are the most trustworthy signal | 
| Power sign | Negative power = charging | Matches device convention provided by user |
| Window | 30-day rolling | Stable correlation, still adaptive |
| Cadence | Optimizer slot size | Avoids aliasing between learning and planning |
| Cap | 10 kW physical cap | Minimal guardrail, allows PW3 peak rates |
| 80% boost cap | Remove hard cap | Learned curve should taper at higher SOC |
| Fallback | Defaults (3.3/5.0 kW) | Stable behavior when learning is off/insufficient |
| Optimizer integration | SOC-dependent charge rate in transitions/simulator | Physical modeling belongs in the state transition, not stage_cost |

## Data Pipeline

1. Pull history for the last 30 days for battery power and SOC.
2. Resample to optimizer slot size.
3. Compute SOC delta per slot and effective charging power when charging (power < 0 by this device convention).
4. Separate samples into two regimes based on planner mode: grid normal vs grid boost.
5. Fit two SOC -> effective charge-rate curves, each with confidence and sample-count metadata.
6. Apply outlier trimming (e.g., top/bottom 1-2%) before fitting.
7. Persist curves and diagnostics in the existing learning storage.

## Optimizer Integration

- Add a SOC-dependent charge-rate model to `OptimizerConfig` (curve or lookup table).
- Use the model inside the SOC transition/charge simulator so charge effects reflect SOC-dependent rates. This keeps the optimizer pure (deterministic given inputs) and aligns with DP constraints: physical modeling lives in transitions, not `stage_cost()`.
- For each slot, compute the effective charge rate from the SOC-dependent curve (cap at 10 kW).
- Planner behavior remains unchanged except it now uses realistic charging rates and naturally stops planning boost “too late.”
- If learning is disabled or insufficient data, fall back to defaults (3.3/5.0 kW) and preserve current behavior.
- Remove the hard 80% boost cap from planning logic (existing `BOOST_CHARGE_MAX_SOC`) so tapering is learned rather than forced.

## Robustness and Learning Status

- Minimum sample threshold before activating learned curves; otherwise use defaults.
- Record diagnostics: sample count, last update, fit error/confidence.
- Expose learning status so it is clear if updates are “stuck” or disabled.

## Learning System Integration

- Introduce a dedicated charge-rate learner component in the existing learning pipeline (e.g., `ChargeRateLearner` alongside `PatternAnalyzer`).
- Store learned curves and diagnostics in the learning storage under a new key (separate from adaptive scalar parameters) to avoid conflating Thompson sampling with curve fitting.
- Surface diagnostics via existing learning sensors (sample count, last update, confidence, active/disabled).

## Telemetry Access and Cadence

- Use Home Assistant recorder history to fetch the last 30 days for:
  - `sensor.my_home_battery_power`
  - `sensor.my_home_percentage_charged`
- Resample to optimizer slot size and compute deltas.
- Update cadence: recompute curves once per day (or on existing medium tick) to avoid heavy recomputation per cycle.
- If history is unavailable or incomplete, skip update and keep the last good curve (or defaults if none).

## HA Access Rule Update

Update the Home Assistant access guidance to:

- Always source `~/.config/localshift/ha.env` before any HA CLI access.
- Explicitly block workaround methods that bypass the env file.

## Testing Strategy

1. Unit tests for SOC-dependent rate fitting and outlier trimming.
2. Unit tests for negative power = charging interpretation.
3. Unit tests for cap enforcement (10 kW) and fallback behavior.
4. Unit tests for insufficient/sparse data (fallback to defaults).
5. Unit tests for noisy telemetry handling (outlier trimming).
6. Integration test: learned taper at high SOC reduces late boost scheduling vs fixed rate.
7. Integration test: end-to-end planner comparison (learned vs fixed).
8. Learning diagnostics test: sample threshold and last-updated metadata.
9. HA history failure test: skip update and preserve last good curve.

## Risks and Mitigations

- **Noisy telemetry:** Use slot-level aggregation and outlier trimming.
- **Sparse data:** Enforce minimum sample count and fallback defaults.
- **Behavior change at high SOC:** Keep minimal physical cap only; rely on learned tapering.

## References

- `docs/PLANNING_MODEL.md` (DP optimizer constraints)
- `docs/LEARNING_SYSTEM.md` (learning pipeline and storage)
- `docs/INDEX.md` (documentation map)

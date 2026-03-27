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
- Allow configurable telemetry entity IDs and power sign override (no new entities).

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
| 80% boost cap | Do not add hard cap | No hard cap currently enforced; rely on learned taper |
| Fallback | Defaults (3.3/5.0 kW) | Stable behavior when learning is off/insufficient |
| Optimizer integration | SOC-dependent charge rate in transitions/simulator | Physical modeling belongs in the state transition, not stage_cost |
| Curve representation | Piecewise linear SOC bins | Predictable, easy to test, avoids overfitting |

## Data Pipeline

1. Pull history for the last 30 days for battery power and SOC.
2. Resample to optimizer slot size.
3. Compute SOC delta per slot and effective charging power when charging (power < 0 by this device convention).
4. Separate samples into two regimes based on planner mode: grid normal vs grid boost.
5. Fit two SOC -> effective charge-rate curves, each with confidence and sample-count metadata.
6. Apply outlier trimming (e.g., top/bottom 1-2%) before fitting.
7. Persist curves and diagnostics in the existing learning storage.

## Optimizer Integration

- Add SOC-dependent charge-rate models to `OptimizerConfig` (one for normal, one for boost), e.g. `ChargeRateCurve` with `rate_at_soc(soc: float) -> float`.
- Update `_transition_charge_grid()` (and any SOC simulator helpers) to use `charge_rate_curve.rate_at_soc(current_soc)` instead of a fixed `charge_rate_kw`.
- This keeps the optimizer pure (deterministic given inputs) and aligns with DP constraints: physical modeling lives in transitions, not `stage_cost()`.
- For each slot, compute the effective charge rate from the SOC-dependent curve (cap at 10 kW).
- Planner behavior remains unchanged except it now uses realistic charging rates and naturally stops planning boost “too late.”
- If learning is disabled or insufficient data, fall back to defaults (3.3/5.0 kW) and preserve current behavior.
- Keep existing float fields as defaults; curve fields are optional and only used when valid.
- Do not add a hard SOC cap for boost; rely on learned tapering and physical rate limits only.

## PLANNING_MODEL Alignment

| Feature | Classification | Implementation |
|---------|----------------|----------------|
| SOC-dependent charge rate | Physical model | SOC transition / charge simulator |
| Charge timing preference | Soft preference | Unchanged; still in `stage_cost()` if present |

## Robustness and Learning Status

- Minimum sample threshold before activating learned curves; otherwise use defaults.
- Record diagnostics: sample count, last update, fit error/confidence.
- Expose learning status so it is clear if updates are “stuck” or disabled.
- Mark curves stale if no updates for 7+ days; surface warning in diagnostics.

## Learning System Integration

- Introduce a dedicated charge-rate learner component in the existing learning pipeline (e.g., `ChargeRateLearner` alongside `PatternAnalyzer`).
- Store learned curves and diagnostics in the learning storage under a new key, e.g. `localshift.charge_rate_curves.{entry_id}` (separate from adaptive scalar parameters).
- Integrate into `LearningOrchestrator` lifecycle (load, update on medium tick, persist on save).
- Use decision outcomes to label samples as normal vs boost by aligning decision timestamps with telemetry windows. Slots without a charge action are ignored.
- Labeling rules:
  - If decision action is `CHARGE_GRID_BOOST`, classify as boost.
  - If decision action is `CHARGE_GRID_NORMAL`, classify as normal.
  - Otherwise skip the slot.
- If a decision record is missing for a slot, skip it (do not infer from telemetry alone).
- Minimum samples per regime (normal/boost) before activating learned curves (e.g., 50+ per regime).
- Surface diagnostics via existing learning sensors (sample count, last update, confidence, active/disabled).

## Telemetry Access and Cadence

- Use Home Assistant recorder history to fetch the last 30 days for:
  - Configured battery power entity
  - Configured battery SOC entity
- Entity IDs are resolved from config entry options with defaults to the LocalShift battery sensors when available.
- Resample to optimizer slot size and compute deltas.
- Use HA history helpers (recorder/statistics) rather than raw HTTP; fail gracefully on missing history.
- Update cadence: recompute curves once per day (or on existing medium tick) to avoid heavy recomputation per cycle.
- If history is unavailable or incomplete, skip update and keep the last good curve (or defaults if none).
- If either power or SOC history is missing, skip update and log diagnostics.
- If configured entity IDs change, invalidate curves and re-learn from history.

## Power Sign Convention

- Battery power sign can vary by system. Derive charging direction by comparing power sign with SOC delta over a short calibration window.
- If power sign and SOC delta disagree for >3 consecutive slots, invert the sign for learning.
- Allow an explicit override in config options if automatic detection fails.

## Curve Representation

- Use SOC bins (e.g., 0-100% in 5% steps) and piecewise linear interpolation between bins.
- Clamp outputs to 0-10 kW, and interpolate only within covered SOC; extrapolate to nearest bin at edges.
- Track per-bin sample counts to compute confidence and detect sparse regions.
- Optionally apply monotonic smoothing (non-increasing with SOC) to avoid noisy spikes at high SOC.
- The curve represents grid power draw (kW). Transition code continues to apply `charge_efficiency` to convert grid power into stored energy.

ChargeRateCurve interface (example):

```python
class ChargeRateCurve:
    def rate_at_soc(self, soc_pct: float) -> float: ...
    @property
    def sample_count(self) -> int: ...
    @property
    def confidence(self) -> float: ...
```

Confidence definition (example):

- `confidence = min(1.0, sample_count / min_samples) * (1.0 - normalized_mad)`
- `normalized_mad` is median absolute deviation of residuals, scaled to 0-1

## Lifecycle and Persistence

- On startup, load curves from `localshift.charge_rate_curves.{entry_id}`; if missing or invalid, fall back to defaults.
- Persist curves and diagnostics after recomputation, include a schema version for migration.
- On config entry option changes (entity IDs or power sign override), invalidate curves and re-learn.

## Multi-Battery Considerations

- If multiple Powerwalls are present, use aggregated power and SOC entities (home-level).
- If per-battery entities are configured, allow future extension to learn per-battery curves, but default to aggregated behavior.

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
8. Integration test: regime separation (normal vs boost labeling).
9. Learning diagnostics test: sample threshold and last-updated metadata.
10. Edge SOC tests: interpolation/extrapolation at 0% and 100%.
11. Staleness test: no new samples for N days triggers stale warning.
12. HA history failure test: skip update and preserve last good curve.
13. Learning disabled toggle test: curves persist but are not applied while disabled.
14. Partial history test: power present but SOC missing (and vice versa) -> no update.
15. Config change test: entity ID change invalidates curves and re-learns.
16. charge_efficiency test: ensure curve represents grid power and efficiency is applied once.
17. Power sign calibration failure test: auto-detect fails after N attempts and requires override.
18. Dual-history failure test: power and SOC history both unavailable -> no update.
19. Monotonic smoothing test: curve enforces non-increasing rate at higher SOC.
20. Config entry recreation test: curves invalidated and defaults applied.

## Risks and Mitigations

- **Noisy telemetry:** Use slot-level aggregation and outlier trimming.
- **Sparse data:** Enforce minimum sample count and fallback defaults.
- **Behavior change at high SOC:** Keep minimal physical cap only; rely on learned tapering.

## References

- `docs/PLANNING_MODEL.md` (DP optimizer constraints)
- `docs/LEARNING_SYSTEM.md` (learning pipeline and storage)
- `docs/INDEX.md` (documentation map)

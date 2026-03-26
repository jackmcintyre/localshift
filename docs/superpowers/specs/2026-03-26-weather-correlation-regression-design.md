# Weather Correlation Regression Rewrite

**Date:** 2026-03-26
**Status:** Approved
**Issue:** Load forecast inflation from corrupted EMA-based weather coefficients

## Problem

The weather correlation learning system uses per-observation EMA (exponential moving average) to learn temperature-load coefficients. Three compounding bugs cause wildly inflated load forecasts:

1. **Small temp_delta amplification**: `implied = (actual_load - base) / temp_delta` — when temperature is barely outside the threshold (delta < 1°C), normal load variation produces absurd implied coefficients (5-12 kW/°C).
2. **No coefficient magnitude cap**: The 0.1/0.9 EMA blending drifts to arbitrarily high values over 12,000+ samples.
3. **No output cap on predict_load()**: Raw math goes straight to the optimizer — `base + 12.76 × delta = 40 kW` predictions are possible.

The result: the optimizer sees 12-15 kW load forecasts for hours that historically average 0.5 kW, causing excessive grid import planning.

### Evidence

Actual coefficients found in production storage (before tactical reset):

| Hour | Coefficient | Value | Impact |
|------|-------------|-------|--------|
| 8 | heating | 2.739 kW/°C | At 13°C (delta 5): +13.7 kW |
| 9 | heating | 3.586 kW/°C | At 14°C (delta 4): +14.3 kW |
| 17 | cooling | 3.511 kW/°C | — |
| 21 | cooling | 12.761 kW/°C | 1°C above threshold: +12.8 kW |

HA statistics confirmed hour 8 actual mean load is 0.57 kW (7-day average). The coefficient produced a 12 kW prediction — a 21× overestimate.

## Solution

Replace the EMA-based learning with OLS (Ordinary Least Squares) regression using sufficient statistics. Regression fits a best-fit line across ALL observations simultaneously, making it inherently resistant to individual outliers and small-delta amplification.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Algorithm | OLS regression | Fits all data at once; outlier-resistant |
| Storage | Sufficient statistics | Fixed-size per day, no raw samples needed |
| Window | 30-day sliding | Balances stability vs seasonal adaptation |
| Buckets | 24 hours × 3 zones = 72 | Per-hour granularity, no weekday split |
| Output cap | Relative (3× historical base) | Adapts to home's actual usage |
| Confidence | Sample count + R² dual gate | Only apply when temperature actually explains load |
| Prediction | Additive adjustment only | Historical base + slope × delta; never replace base |
| Scope | Full module rewrite | Split monolith into 3 focused modules |

## Module Structure

```
learning/
  correlation.py      — Regression engine: learn, predict, storage, diagnostics
  temperature.py      — Forecast fetching + caching (extracted, no logic changes)
  anomaly.py          — Anomaly detection (extracted, no logic changes)
```

### correlation.py — Regression Engine

Public API (signature-compatible with current system):

```python
class WeatherCorrelation:
    def learn_from_sample(self, hour: int, temperature: float, actual_load_kw: float) -> None
    def predict_load(self, hour: int, temperature: float, base_load_kw: float) -> tuple[float, str]
    def get_coefficients_for_hour(self, hour: int) -> HourlyRegressionResult | None
    def get_diagnostics(self) -> dict[str, Any]
    def get_temperature_forecast(self) -> list[TemperatureForecast]  # delegates to temperature.py
    def get_current_temperature(self) -> float | None  # delegates to temperature.py
    def detect_weather_anomaly(self, current_temp: float) -> WeatherAnomalyResult  # delegates to anomaly.py
    def record_daily_temperature(self, temperature: float, ...) -> None  # delegates to anomaly.py
    async def async_initialize(self) -> None
    async def async_save(self) -> None
    async def async_reset(self) -> None  # NEW: clears regression stats
```

### temperature.py — Forecast Provider (Extracted)

```python
class TemperatureForecastProvider:
    def get_temperature_forecast(self) -> list[TemperatureForecast]
    async def async_get_temperature_forecast(self, force_refresh: bool = False) -> list[TemperatureForecast]
    def get_current_temperature(self) -> float | None
```

Pure extraction of ~300 lines of service calls, response parsing, and caching. No logic changes.

### anomaly.py — Anomaly Detector (Extracted)

```python
class WeatherAnomalyDetector:
    def record_daily_temperature(self, temperature: float, date_key: str | None = None) -> None
    def detect_weather_anomaly(self, current_temp: float) -> WeatherAnomalyResult
```

Pure extraction. Preserves 14-day history, ±2σ detection. No logic changes.

## Core Algorithm

### Data Structures

```python
@dataclass
class ZoneStats:
    n: int = 0            # sample count
    sum_x: float = 0.0    # Σ(temp_delta)
    sum_y: float = 0.0    # Σ(load_kw)
    sum_xx: float = 0.0   # Σ(temp_delta²)
    sum_xy: float = 0.0   # Σ(temp_delta × load_kw)
    sum_yy: float = 0.0   # Σ(load_kw²)  — needed for R²

@dataclass
class HourlyRegressionData:
    heating: ZoneStats     # T < heating_threshold
    mild: ZoneStats        # heating_threshold ≤ T ≤ cooling_threshold
    cooling: ZoneStats     # T > cooling_threshold

@dataclass
class DailySnapshot:
    date: str              # ISO date "YYYY-MM-DD"
    data: HourlyRegressionData

@dataclass
class HourlyRegressionResult:
    """Returned by get_coefficients_for_hour(). Replaces HourlyTemperatureCoefficients."""
    heating_slope: float      # kW/°C for heating zone
    cooling_slope: float      # kW/°C for cooling zone
    base_load_kw: float       # average mild-zone load
    heating_r_squared: float
    cooling_r_squared: float
    sample_count: int         # total across all zones
    confidence: str           # "low" / "medium" / "high"
```

### Zone Classification

- **Heating zone** (T < heating_threshold): `delta = heating_threshold - temperature` (positive, larger = colder)
- **Cooling zone** (T > cooling_threshold): `delta = temperature - cooling_threshold` (positive, larger = hotter)
- **Mild zone** (heating_threshold ≤ T ≤ cooling_threshold): No delta — accumulate `n` and `sum_y` for average base load

### Learning: learn_from_sample(hour, temperature, actual_load_kw)

1. Classify observation into zone by temperature
2. Compute `temp_delta` (if heating or cooling zone)
3. **Guard: skip if `temp_delta < MIN_TEMP_DELTA` (1.0°C)** — prevents small-delta amplification
4. Get or create today's `DailySnapshot` for this hour
5. Accumulate into the appropriate `ZoneStats`:
   - `n += 1`
   - `sum_x += delta`
   - `sum_y += load`
   - `sum_xx += delta²`
   - `sum_xy += delta × load`
   - `sum_yy += load²`
6. Prune snapshots older than 30 days

### Fitting: fit_slope(aggregated_stats)

1. Aggregate `ZoneStats` across all retained daily snapshots (sum the sums)
2. If `n < MIN_SAMPLES_PER_ZONE` (20): return `(slope=0.0, r_squared=0.0)`
3. Compute OLS slope:
   ```
   denom = n × sum_xx - sum_x²
   if denom ≤ 0: return (slope=0.0, r_squared=0.0)   # degenerate: all deltas identical
   slope = (n × sum_xy - sum_x × sum_y) / denom
   ```
4. **Clamp slope ≥ 0** — physically, more temperature deviation can only increase load
5. **Clamp slope ≤ MAX_SLOPE_KW_PER_DEGREE** (2.0 kW/°C)
6. Compute R²:
   ```
   ss_xy = n × sum_xy - sum_x × sum_y
   ss_xx = n × sum_xx - sum_x²
   ss_yy = n × sum_yy - sum_y²
   if ss_xx ≤ 0 or ss_yy ≤ 0: r² = 0.0   # degenerate: no variance in x or y
   else: r² = ss_xy² / (ss_xx × ss_yy)
   ```
7. Return `(slope, r_squared)`

### Coefficient Lookup: get_coefficients_for_hour(hour)

Returns `HourlyRegressionResult` for a given hour, or `None` if no data exists.

1. If no daily snapshots exist for this hour: return `None`
2. Aggregate `ZoneStats` across all daily snapshots for this hour (sum the sums for each zone)
3. Run `fit_slope()` on the aggregated heating zone → `(heating_slope, heating_r²)`
4. Run `fit_slope()` on the aggregated cooling zone → `(cooling_slope, cooling_r²)`
5. Compute `base_load_kw` from mild zone: `sum_y / n` if `n > 0`, else `0.0`
6. Compute total `sample_count` across all three zones
7. Determine confidence:
   - `"high"` if any zone has `n ≥ MIN_SAMPLES` AND `r² ≥ 0.30`
   - `"medium"` if any zone has `n ≥ MIN_SAMPLES` AND `r² ≥ MIN_R_SQUARED` (0.10)
   - `"low"` otherwise
8. Return `HourlyRegressionResult(heating_slope, cooling_slope, base_load_kw, heating_r², cooling_r², sample_count, confidence)`

**Note:** The caller (LoadForecaster) checks confidence from this method *before* calling `predict_load()`. `predict_load()` also has its own internal confidence gate. This double-gate is intentional — belt and suspenders.

### Prediction: predict_load(hour, temperature, base_load_kw)

1. If mild zone (heating_threshold ≤ T ≤ cooling_threshold): return `(base_load_kw, "weather_none")`
2. Determine zone and delta
3. Fit slope from aggregated stats for the relevant zone
4. **Confidence gate**: require `n ≥ MIN_SAMPLES` AND `r² ≥ MIN_R_SQUARED` (0.10)
5. If gate fails: return `(base_load_kw, "low_confidence")`
6. Compute: `predicted = base_load_kw + slope × delta`
7. **Safety cap**: `predicted = min(predicted, max(base_load_kw, 0.1) × MAX_LOAD_MULTIPLIER)` (3.0×; floor of 0.1 kW prevents zero-base zeroing out the adjustment)
8. Return `(max(0.0, predicted), f"weather_{zone}")`

## Sliding Window

Daily snapshots are stored per hour. Each day's data is one `DailySnapshot` containing the day's accumulated `ZoneStats` for each zone.

**Pruning**: On each `learn_from_sample()` call, remove snapshots with `date` older than 30 days.

**Storage size**: 30 days × 24 hours × 3 zones × 6 numbers = 12,960 floats. With JSON overhead, roughly 150-200 KB.

## Storage Format (v2)

```json
{
    "version": 2,
    "weather_entity_id": "weather.crows_nest_hourly",
    "cooling_threshold": 24.0,
    "heating_threshold": 18.0,
    "daily_regression_stats": {
        "8": [
            {
                "date": "2026-03-25",
                "heating": {"n": 12, "sum_x": 48.0, "sum_y": 7.2, "sum_xx": 210.0, "sum_xy": 30.5, "sum_yy": 5.8},
                "mild": {"n": 0, "sum_x": 0, "sum_y": 0, "sum_xx": 0, "sum_xy": 0, "sum_yy": 0},
                "cooling": {"n": 0, "sum_x": 0, "sum_y": 0, "sum_xx": 0, "sum_xy": 0, "sum_yy": 0}
            }
        ]
    },
    "temperature_history": {"2026-03-25": 22.5, "2026-03-24": 21.0},
    "learning_stats": {}
}
```

### Migration (v1 → v2)

On load, detect version 1 data:
1. Discard `hourly_coefficients` entirely (EMA-corrupted)
2. Preserve `temperature_history` and config fields (`weather_entity_id`, thresholds)
3. Initialize empty `daily_regression_stats`
4. Log migration event

`STORAGE_VERSION` bumps to 2.

## Diagnostics

`get_diagnostics()` returns:

```python
{
    "total_samples": 5400,
    "active_hours": 18,
    "average_heating_slope": 0.25,
    "average_cooling_slope": 0.18,
    "average_r_squared": 0.35,
    "hourly_coefficients": {
        "8": {
            "heating_slope": 0.31,
            "heating_r_squared": 0.42,
            "heating_samples": 120,
            "cooling_slope": 0.0,
            "cooling_r_squared": 0.0,
            "cooling_samples": 15,
            "mild_avg_kw": 0.72,
            "mild_samples": 90,
            "confidence": "medium"
        }
    }
}
```

Sensor attribute renames:
- `weather_cooling_coefficient` → `weather_avg_cooling_slope`
- `weather_heating_coefficient` → `weather_avg_heating_slope`
- NEW: `weather_avg_r_squared`

## Reset Button Fix

`ResetLearningDataButton.async_press()` adds a call to clear weather correlation data:

```python
weather_correlation = getattr(self.coordinator._computation_engine, '_weather_correlation', None)
if weather_correlation is not None:
    await weather_correlation.async_reset()
```

`async_reset()` clears `daily_regression_stats`, preserves `temperature_history`, calls `async_save()`.

## Constants

```python
STORAGE_VERSION = 2
MIN_SAMPLES_PER_ZONE = 20        # minimum before regression is used
MIN_R_SQUARED = 0.10             # minimum fit quality
MAX_SLOPE_KW_PER_DEGREE = 2.0    # physical cap on learned slope
MAX_LOAD_MULTIPLIER = 3.0        # output cap: predicted ≤ 3× historical base
SLIDING_WINDOW_DAYS = 30         # rolling window
MIN_TEMP_DELTA = 1.0             # ignore observations with delta < 1°C
```

## Testing Strategy

1. **Unit tests for ZoneStats and OLS fit**: Known inputs with hand-calculated expected slopes and R²
2. **Unit tests for learn_from_sample**: Zone classification, daily snapshot creation, accumulation, window pruning
3. **Unit tests for predict_load**: Confidence gate, additive adjustment, safety cap, mild zone passthrough
4. **Integration test**: Learn 100+ samples → predict → verify reasonable output
5. **Migration test**: Load v1 storage → verify v2 structure, coefficients discarded, temperature_history preserved
6. **Reset test**: Verify async_reset clears regression stats but preserves anomaly history
7. **Extraction tests**: Verify temperature.py and anomaly.py behave identically to the extracted code
8. **Regression test**: Replay the exact scenario from the bug (hour 8, temp 13°C, historical 0.72 kW) and verify the output is bounded

## Why This Fixes the Original Bug

The original bug had three root causes. This design eliminates all three:

| Root Cause | Old System | New System |
|------------|-----------|------------|
| Small temp_delta amplification | `implied = load / delta` per observation | Regression fits ALL data; `MIN_TEMP_DELTA` guard rejects < 1°C |
| No coefficient cap | EMA drifts to any value | `MAX_SLOPE_KW_PER_DEGREE` caps slope at 2.0 kW/°C |
| No output cap | `predict_load` returns raw math | `MAX_LOAD_MULTIPLIER` caps at 3× historical base |
| Base load replacement | Learned base overrides historical | Additive only: historical base is never replaced |
| Confidence without quality | Sample count alone | Sample count + R² dual gate |

## Breaking Changes

- **Sensor attribute renames**: `weather_cooling_coefficient` → `weather_avg_cooling_slope`, `weather_heating_coefficient` → `weather_avg_heating_slope`. Any dashboard cards or automations referencing the old attribute names will need updating.
- **Storage format v2**: Existing learned coefficients are discarded on migration (intentional — the EMA data is corrupted). Learning restarts from zero. Temperature history is preserved.

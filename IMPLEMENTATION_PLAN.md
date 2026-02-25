# Statistics API Integration - Implementation Plan

**Branch:** `feature/statistics-api`
**Epic Issue:** #265
**Created:** 2026-02-25

## Overview

This plan outlines the implementation of Home Assistant's Statistics API integration for LocalShift, enabling long-term data analysis, decision backtesting, and forecast accuracy validation.

## Issues in Scope

| Issue | Title | Priority | Dependencies |
|-------|-------|----------|--------------|
| #266 | Add state_class to forecast sensors | High | None |
| #267 | Create StatisticsBackfiller module | High | #266 |
| #268 | Extend HistoryFetcher for long-term stats | Medium | #266 |
| #269 | Cost reconciliation against metered data | Medium | #267 |
| #270 | Long-term forecast accuracy tracking | Low | #267 |

## Execution Phases

### Phase 1: Enable Statistics on Sensors (#266)
**Duration:** ~1 hour
**Goal:** Add `state_class` attribute to forecast sensors

#### Files to Modify
- [ ] `custom_components/localshift/sensor.py`

#### Changes Required

1. **SolarBatteryForecastSensor** - Add `SensorStateClass.MEASUREMENT`
2. **ForecastAccuracySensor** - Add `SensorStateClass.MEASUREMENT`
3. **ForecastGridSensor** - Add `SensorStateClass.MEASUREMENT`
4. **ForecastPricesSensor** - Add `SensorStateClass.MEASUREMENT`

#### Testing
- [ ] Create `tests/test_sensor_state_class.py`
- [ ] Verify statistics appear in HA after 24h (manual)

#### Documentation
- [ ] Update `docs/ENTITY_REFERENCE.md` with statistics support notes

---

### Phase 2: Create StatisticsBackfiller Module (#267)
**Duration:** ~3 hours
**Goal:** Ground-truth validation of decision outcomes

#### Files to Create
- [ ] `custom_components/localshift/computation_engine_lib/statistics_backfiller.py`

#### Files to Modify
- [ ] `custom_components/localshift/coordinator.py` - Integrate backfiller
- [ ] `custom_components/localshift/sensor.py` - Add BackfillStatusSensor
- [ ] `custom_components/localshift/coordinator_data.py` - Add backfill_report field
- [ ] `custom_components/localshift/config_flow/schemas.py` - Add config options

#### Implementation Details

**StatisticsBackfiller class:**
```python
class StatisticsBackfiller:
    """Fetches statistics and validates decision outcomes."""
    
    async def async_backfill_decision_outcomes(days: int = 7) -> BackfillReport:
        """Main entry point for backfill operation."""
        
    async def _fetch_statistics(start_time, end_time) -> dict:
        """Fetch statistics from HA recorder."""
        
    def _validate_decisions(decisions, statistics) -> BackfillReport:
        """Compare estimated vs actual outcomes."""
        
    def _sum_statistics_in_range(rows, start, end) -> float:
        """Sum statistics for a time range."""
```

**BackfillReport dataclass:**
- decisions_validated: int
- discrepancies_found: int
- total_import_validated_kwh: float
- total_export_validated_kwh: float
- avg_variance_pct: float
- last_run: datetime
- errors: list[str]

**Configuration options:**
- `grid_import_entity`: Entity for grid import statistics
- `grid_export_entity`: Entity for grid export statistics
- `battery_charge_entity`: Entity for battery charge statistics
- `battery_discharge_entity`: Entity for battery discharge statistics
- `backfill_schedule`: Time to run daily backfill (default: "02:00")

#### Testing
- [ ] Create `tests/test_statistics_backfiller.py`
- [ ] Test backfill returns valid report
- [ ] Test handling of no decisions
- [ ] Test discrepancy detection

#### Documentation
- [ ] Update `docs/ARCHITECTURE.md` with StatisticsBackfiller section

---

### Phase 3a: Extend HistoryFetcher (#268)
**Duration:** ~2 hours
**Goal:** Enable 90+ day lookback for seasonal patterns

#### Files to Modify
- [ ] `custom_components/localshift/computation_engine_lib/history_fetcher.py`
- [ ] `custom_components/localshift/config_flow/schemas.py`

#### Implementation Details

**New methods:**
```python
async def async_get_extended_hourly_averages(
    self, entity_id: str, days: int = 90
) -> tuple[dict[int, float], dict[int, float], str]:
    """Get hourly averages using Statistics API for extended lookback."""
    
async def _entity_supports_statistics(self, entity_id: str) -> bool:
    """Check if entity has state_class for statistics support."""
    
def detect_seasonal_profile(self, extended_stats) -> dict:
    """Detect seasonal patterns from extended statistics."""
```

**Configuration options:**
- `use_extended_history`: Enable extended lookback (default: False)
- `extended_history_days`: Days to look back (default: 90)

#### Testing
- [ ] Create `tests/test_history_fetcher_extended.py`
- [ ] Test extended fetch uses Statistics API
- [ ] Test fallback to recorder for non-statistics entities
- [ ] Test seasonal profile detection

#### Documentation
- [ ] Update `docs/ARCHITECTURE.md` with Extended History section

---

### Phase 3b: Cost Reconciliation (#269)
**Duration:** ~2 hours
**Goal:** Validate cost estimates against metered data

#### Files to Modify
- [ ] `custom_components/localshift/cost_tracker.py`
- [ ] `custom_components/localshift/sensor.py`
- [ ] `custom_components/localshift/coordinator_data.py`

#### Implementation Details

**New dataclass:**
```python
@dataclass
class ReconciliationReport:
    timestamp: datetime
    estimated_import_cost: float
    actual_import_cost: float
    import_variance_pct: float
    estimated_export_revenue: float
    actual_export_revenue: float
    export_variance_pct: float
    is_significant: bool
```

**New methods in CostTracker:**
```python
async def async_reconcile_with_statistics(
    self, grid_import_entity, grid_export_entity
) -> ReconciliationReport:
    """Reconcile accumulated costs against metered statistics."""
    
async def _fetch_statistics_for_period(
    self, entity_id, start, end
) -> dict:
    """Fetch statistics for a time period."""
```

**New sensor:**
- `CostReconciliationSensor` - Shows variance percentage

#### Testing
- [ ] Create `tests/test_cost_reconciliation.py`
- [ ] Test variance detection
- [ ] Test handling of zero actual cost
- [ ] Test warning for significant variance

#### Documentation
- [ ] Update `docs/ARCHITECTURE.md` with Cost Reconciliation section

---

### Phase 4: Long-term Forecast Accuracy (#270)
**Duration:** ~3 hours
**Goal:** Multi-horizon forecast validation

#### Files to Modify
- [ ] `custom_components/localshift/computation_engine_lib/forecast_accuracy.py`
- [ ] `custom_components/localshift/sensor.py`
- [ ] `custom_components/localshift/coordinator.py`
- [ ] `custom_components/localshift/coordinator_data.py`

#### Implementation Details

**New dataclass:**
```python
@dataclass
class ExtendedAccuracyMetrics:
    accuracy_24h: float
    accuracy_7d: float
    accuracy_30d: float
    bias: float  # Systematic over/under prediction
    mape: float  # Mean Absolute Percentage Error
    sample_count: int
```

**New class:**
```python
class ExtendedForecastAccuracyEngine:
    """Extended forecast accuracy tracking with persistent storage."""
    
    async def async_load(self) -> None:
        """Load persisted forecasts from storage."""
        
    async def async_save(self) -> None:
        """Persist forecasts to storage."""
        
    def persist_forecast(self, forecast) -> None:
        """Store a forecast for later validation."""
        
    async def compute_extended_accuracy(self, current_soc) -> ExtendedAccuracyMetrics:
        """Compute extended accuracy metrics."""
```

**New sensors:**
- `ForecastAccuracy24hSensor`
- `ForecastAccuracy7dSensor`
- `ForecastBiasSensor`

#### Testing
- [ ] Create `tests/test_extended_forecast_accuracy.py`
- [ ] Test forecast persistence
- [ ] Test accuracy computation
- [ ] Test bias detection
- [ ] Test MAPE calculation
- [ ] Test storage persistence

#### Documentation
- [ ] Update `docs/ARCHITECTURE.md` with Extended Forecast Accuracy section

---

## Logging Strategy

All new modules should use structured logging:

```python
import logging

_LOGGER = logging.getLogger(__name__)

# Info for key operations
_LOGGER.info("Statistics backfill completed: %d decisions validated", count)

# Debug for detailed operations
_LOGGER.debug("Fetching statistics for %s: period=%s", entity_id, period)

# Warning for anomalies
_LOGGER.warning("Cost variance exceeds threshold: %.1f%%", variance_pct)

# Error for failures
_LOGGER.error("Failed to fetch statistics: %s", err)
```

## MCP Validation

Use Home Assistant MCP tools during development:

```python
# Test statistics fetching
ha_get_statistics(
    entity_ids=["sensor.my_home_grid_imported", "sensor.my_home_grid_exported"],
    period="day",
    start_time="7d"
)

# Verify sensors have state_class
ha_get_state(entity_id="sensor.localshift_forecast_battery")

# Search for new sensors
ha_search_entities(query="backfill")
ha_search_entities(query="reconciliation")
ha_search_entities(query="forecast_accuracy")
```

## Commit Strategy

Commit after each phase completion:

1. `feat(sensors): add state_class to forecast sensors (#266)`
2. `feat(core): create StatisticsBackfiller module (#267)`
3. `feat(history): extend HistoryFetcher for long-term stats (#268)`
4. `feat(cost): add cost reconciliation against metered data (#269)`
5. `feat(forecast): add long-term forecast accuracy tracking (#270)`

## PR Checklist

Before opening PR:
- [ ] All unit tests pass
- [ ] Pre-commit hooks pass
- [ ] Documentation updated
- [ ] Deployed and verified via logs
- [ ] MCP validation successful

## Notes

- Work on one issue at a time, in dependency order
- Run tests after each change
- Deploy frequently to verify integration
- Check logs after each deploy: `tail -100 /homeassistant/home-assistant.log | grep -i localshift`
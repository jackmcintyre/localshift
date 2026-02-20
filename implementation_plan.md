# Implementation Plan

[Overview]
Implement day-of-week aware consumption prediction by maintaining separate weekday and weekend hourly load profiles, improving forecast accuracy for households with different consumption patterns on weekdays vs weekends.

This enhancement addresses issue #60 by modifying the `HistoryFetcher` to separate historical load data into two profiles: weekday (Monday-Friday) and weekend (Saturday-Sunday). The system currently uses a simple 7-day average that blends all days together, losing important consumption patterns. The new implementation will extend the history window to 28 days (4 weeks) for better statistical significance, maintain separate hourly averages for each day-type, and automatically select the appropriate profile based on the target day being forecasted. This improves accuracy for households with distinct weekday/weekend routines (e.g., work-from-home patterns, weekend appliance usage).

[Types]
New data structures for managing separate consumption profiles.

```python
# Profile type enumeration (add to const.py)
class ConsumptionProfileType(str, Enum):
    """Types of consumption profiles."""
    COMBINED = "combined"      # Fallback: all days averaged together
    WEEKDAY = "weekday"        # Monday-Friday profile
    WEEKEND = "weekend"        # Saturday-Sunday profile

# Internal cache structure (within HistoryFetcher)
_weekday_hourly_avg_kw: dict[int, float]      # Hour -> average kW for weekdays
_weekend_hourly_avg_kw: dict[int, float]      # Hour -> average kW for weekends  
_weekday_sample_counts: dict[int, int]        # Hour -> sample count for weekdays
_weekend_sample_counts: dict[int, int]        # Hour -> sample count for weekends
_profile_source: str                          # "weekday_weekend" or "combined_fallback"

# Constants (add to const.py)
MIN_SAMPLES_PER_HOUR = 3      # Minimum samples before using profile-specific data
HISTORY_WINDOW_DAYS = 28      # 4-week rolling window for better statistics
```

[Files]
Modifications to existing files and their purposes.

**Modified Files:**

1. **`custom_components/localshift/computation_engine_lib/history_fetcher.py`**
   - Add new cache properties for weekday/weekend profiles
   - Modify `_fetch_historical_data_sync()` to separate weekday/weekend samples
   - Extend history window from 7 to 28 days
   - Add `get_profile_for_day()` method for day-aware profile selection
   - Add fallback logic when insufficient samples per profile

2. **`custom_components/localshift/computation_engine_lib/forecast_computer.py`**
   - Modify `_estimate_hourly_consumption_kw()` to accept and use day-appropriate profile
   - Update all consumption estimation calls to pass day context

3. **`custom_components/localshift/computation_engine.py`**
   - Update `_get_historical_hourly_averages()` to return day-appropriate profile
   - Add new properties for weekday/weekend diagnostics
   - Modify `_compute_daily_15min_forecast()` to pass day context

4. **`custom_components/localshift/coordinator_data.py`**
   - Add new diagnostic fields:
     - `consumption_profile_type: str`  # "weekday", "weekend", or "combined"
     - `weekday_sample_counts: dict[int, int]`
     - `weekend_sample_counts: dict[int, int]`
     - `weekday_hourly_profile_kw: dict[int, float]`
     - `weekend_hourly_profile_kw: dict[int, float]`

5. **`custom_components/localshift/sensor.py`**
   - Update `DailyForecastSensor.extra_state_attributes` to include new diagnostic fields

6. **`custom_components/localshift/const.py`**
   - Add `HISTORY_WINDOW_DAYS = 28` constant
   - Add `MIN_SAMPLES_PER_HOUR = 3` constant

7. **`docs/ARCHITECTURE.md`**
   - Document new day-of-week aware consumption prediction
   - Update data flow diagrams

8. **`docs/ENTITY_REFERENCE.md`**
   - Document new diagnostic attributes

**New Test File:**

9. **`tests/test_history_fetcher_weekday_weekend.py`**
   - Unit tests for weekday/weekend profile separation
   - Tests for profile selection based on day-of-week
   - Tests for fallback behavior with insufficient samples

[Functions]
Function modifications with exact names and required changes.

**New Functions:**

1. **`HistoryFetcher._separate_samples_by_day_type()`**
   - File: `custom_components/localshift/computation_engine_lib/history_fetcher.py`
   - Signature: `def _separate_samples_by_day_type(self, rows: list[dict], local_tz) -> tuple[dict[int, list[float]], dict[int, list[float]]]`
   - Purpose: Separate statistics rows into weekday and weekend hourly buckets
   - Returns: `(weekday_by_hour, weekend_by_hour)` where each is `{hour: [values]}`

2. **`HistoryFetcher._calculate_profiles()`**
   - File: `custom_components/localshift/computation_engine_lib/history_fetcher.py`
   - Signature: `def _calculate_profiles(self, weekday_by_hour: dict[int, list[float]], weekend_by_hour: dict[int, list[float]]) -> tuple[dict, dict, dict, dict]`
   - Purpose: Calculate averages and sample counts for both profiles
   - Returns: `(weekday_avg, weekend_avg, weekday_counts, weekend_counts)`

3. **`HistoryFetcher.get_profile_for_day()`**
   - File: `custom_components/localshift/computation_engine_lib/history_fetcher.py`
   - Signature: `def get_profile_for_day(self, target_date: datetime) -> tuple[dict[int, float], dict[int, int], str]`
   - Purpose: Get appropriate hourly profile based on target day's day-of-week
   - Returns: `(hourly_avg_kw, sample_counts, source)`

4. **`HistoryFetcher.get_weekday_profile()`**
   - File: `custom_components/localshift/computation_engine_lib/history_fetcher.py`
   - Signature: `def get_weekday_profile(self) -> tuple[dict[int, float], dict[int, int]]`
   - Purpose: Get weekday profile for diagnostics
   - Returns: `(weekday_avg, weekday_counts)`

5. **`HistoryFetcher.get_weekend_profile()`**
   - File: `custom_components/localshift/computation_engine_lib/history_fetcher.py`
   - Signature: `def get_weekend_profile(self) -> tuple[dict[int, float], dict[int, int]]`
   - Purpose: Get weekend profile for diagnostics
   - Returns: `(weekend_avg, weekend_counts)`

**Modified Functions:**

6. **`HistoryFetcher.__init__()`** (`history_fetcher.py`)
   - Current location: lines ~25-45
   - Modification: Add initialization of new cache properties for weekday/weekend profiles

7. **`HistoryFetcher._fetch_historical_data_sync()`** (`history_fetcher.py`)
   - Current location: lines ~80-180
   - Modification: 
     - Change `timedelta(days=7)` to `timedelta(days=28)`
     - Call `_separate_samples_by_day_type()` to split samples
     - Call `_calculate_profiles()` to compute both profiles
     - Store results in new cache properties
     - Implement fallback to combined profile when insufficient samples

8. **`HistoryFetcher.async_get_historical_hourly_averages()`** (`history_fetcher.py`)
   - Current location: lines ~50-80
   - Modification: Return both profiles and let caller choose

9. **`HistoryFetcher.get_cached_hourly_averages()`** (`history_fetcher.py`)
   - Current location: lines ~330-335
   - Modification: Return combined profile for backward compatibility

10. **`ForecastComputer._estimate_hourly_consumption_kw()`** (`forecast_computer.py`)
    - Current location: lines ~80-140
    - Modification: Accept optional `target_date` parameter to select appropriate profile

11. **`ForecastComputer.compute_forecast()`** (`forecast_computer.py`)
    - Current location: lines ~700-1100
    - Modification: Pass slot's day-of-week when calling `_estimate_hourly_consumption_kw()`

12. **`ComputationEngine._get_historical_hourly_averages()`** (`computation_engine.py`)
    - Current location: lines ~580-590
    - Modification: Return day-appropriate profile based on current day

[Classes]
Class modifications with specific changes.

**Modified Classes:**

1. **`HistoryFetcher`** (`history_fetcher.py`)
   - Add properties:
     - `_weekday_hourly_avg_kw: dict[int, float]`
     - `_weekend_hourly_avg_kw: dict[int, float]`
     - `_weekday_sample_counts: dict[int, int]`
     - `_weekend_sample_counts: dict[int, int]`
     - `_profile_source: str`
   - Add methods:
     - `_separate_samples_by_day_type()`
     - `_calculate_profiles()`
     - `get_profile_for_day()`
     - `get_weekday_profile()`
     - `get_weekend_profile()`
   - Modify methods:
     - `__init__()` - initialize new properties
     - `_fetch_historical_data_sync()` - separate and calculate profiles
     - `clear_historical_cache()` - clear new caches

2. **`CoordinatorData`** (`coordinator_data.py`)
   - Add fields:
     - `consumption_profile_type: str = "combined"`
     - `weekday_sample_counts: dict[int, int] = field(default_factory=dict)`
     - `weekend_sample_counts: dict[int, int] = field(default_factory=dict)`
     - `weekday_hourly_profile_kw: dict[int, float] = field(default_factory=dict)`
     - `weekend_hourly_profile_kw: dict[int, float] = field(default_factory=dict)`

3. **`DailyForecastSensor`** (`sensor.py`)
   - Modify `extra_state_attributes` property to include new diagnostic fields

[Dependencies]
No new external dependencies required.

All functionality uses existing Home Assistant APIs:
- `homeassistant.components.recorder.statistics` - Already used for historical data
- `homeassistant.util.dt` - Already used for datetime handling
- Python standard library `datetime` - For day-of-week detection

[Testing]
Test file requirements and validation strategies.

**New Test File: `tests/test_history_fetcher_weekday_weekend.py`**

Test cases:
1. **`test_separate_samples_by_day_type()`**
   - Create mock statistics data with known timestamps
   - Verify weekday samples go to weekday bucket
   - Verify weekend samples go to weekend bucket
   - Verify correct hour assignment

2. **`test_calculate_profiles()`**
   - Provide sample data for both profiles
   - Verify correct average calculation
   - Verify correct sample count tracking

3. **`test_get_profile_for_day_weekday()`**
   - Call with Monday-Friday dates
   - Verify returns weekday profile

4. **`test_get_profile_for_day_weekend()`**
   - Call with Saturday-Sunday dates
   - Verify returns weekend profile

5. **`test_fallback_to_combined_insufficient_weekday_samples()`**
   - Provide < 3 samples for some weekday hours
   - Verify falls back to combined profile for those hours

6. **`test_fallback_to_combined_insufficient_weekend_samples()`**
   - Provide < 3 samples for some weekend hours
   - Verify falls back to combined profile for those hours

7. **`test_28_day_window()`**
   - Verify history fetch uses 28-day window
   - Verify old data is excluded

**Modified Test File: `tests/test_forecast_computer.py`**

Add test cases:
8. **`test_estimate_hourly_consumption_with_day_context()`**
   - Verify weekday profile used for weekday slots
   - Verify weekend profile used for weekend slots

[Implementation Order]
Numbered steps showing the logical order of changes.

1. **Add constants to `const.py`**
   - Add `HISTORY_WINDOW_DAYS = 28`
   - Add `MIN_SAMPLES_PER_HOUR = 3`

2. **Add diagnostic fields to `CoordinatorData`**
   - Add `consumption_profile_type`
   - Add `weekday_sample_counts`, `weekend_sample_counts`
   - Add `weekday_hourly_profile_kw`, `weekend_hourly_profile_kw`

3. **Modify `HistoryFetcher.__init__()`**
   - Initialize new cache properties

4. **Implement `HistoryFetcher._separate_samples_by_day_type()`**
   - Separate statistics rows by day type

5. **Implement `HistoryFetcher._calculate_profiles()`**
   - Calculate averages and counts for both profiles

6. **Modify `HistoryFetcher._fetch_historical_data_sync()`**
   - Extend window to 28 days
   - Call new separation and calculation methods
   - Store results in new caches
   - Implement fallback logic

7. **Implement `HistoryFetcher.get_profile_for_day()`**
   - Return appropriate profile based on day-of-week

8. **Implement diagnostic getter methods**
   - `get_weekday_profile()`
   - `get_weekend_profile()`

9. **Modify `HistoryFetcher.clear_historical_cache()`**
   - Clear new cache properties

10. **Modify `ForecastComputer._estimate_hourly_consumption_kw()`**
    - Accept day context parameter
    - Use day-appropriate profile

11. **Modify `ForecastComputer.compute_forecast()`**
    - Pass day context for each slot

12. **Modify `ComputationEngine`**
    - Add properties for new diagnostic data
    - Update forecast computation to use day-aware profiles

13. **Update `DailyForecastSensor` attributes**
    - Include new diagnostic fields

14. **Write unit tests**
    - Create `tests/test_history_fetcher_weekday_weekend.py`
    - Add tests to `tests/test_forecast_computer.py`

15. **Update documentation**
    - Update `docs/ARCHITECTURE.md`
    - Update `docs/ENTITY_REFERENCE.md`

16. **Run pre-commit and fix issues**
    - Run `pre-commit run --all-files`
    - Fix any linting/formatting issues

17. **Create pull request**
    - Reference issue #60 in PR body
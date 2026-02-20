# Implementation Plan

[Overview]
Implement weather-aware consumption prediction using a degree-day model that learns the correlation between temperature and household load.

Household consumption is heavily influenced by HVAC usage, particularly during temperature extremes. This implementation enables the LocalShift integration to learn temperature/load correlations and adjust consumption predictions based on forecasted temperatures, leading to more accurate demand window planning and reduced unexpected grid imports.

[Types]

The following type definitions and data structures are required:

```python
# In new file: weather_correlation.py

@dataclass
class HourlyTemperatureCoefficients:
    """Temperature sensitivity coefficients for a single hour.
    
    Attributes:
        base_load_kw: Minimum load at mild temperatures (18-24°C band)
        cooling_coefficient: Additional kW per degree above 24°C
        heating_coefficient: Additional kW per degree below 18°C
        sample_count: Number of data points used for learning
        last_updated: When coefficients were last recalculated
        confidence: low/medium/high based on sample count
    """
    base_load_kw: float = 0.0
    cooling_coefficient: float = 0.0  # kW per °C above cooling threshold
    heating_coefficient: float = 0.0  # kW per °C below heating threshold
    sample_count: int = 0
    last_updated: str = ""  # ISO format datetime
    confidence: str = "low"


@dataclass  
class WeatherCorrelationData:
    """Complete weather correlation data structure for storage.
    
    Attributes:
        version: Schema version for migrations
        weather_entity_id: Configured weather entity
        cooling_threshold: Temperature above which cooling load increases
        heating_threshold: Temperature below which heating load increases
        hourly_coefficients: Dict mapping hour (0-23) to coefficients
        learning_stats: Aggregated statistics for diagnostics
    """
    version: int = 1
    weather_entity_id: str = ""
    cooling_threshold: float = 24.0  # °C
    heating_threshold: float = 18.0  # °C
    hourly_coefficients: dict[int, HourlyTemperatureCoefficients] = field(default_factory=dict)
    learning_stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class TemperatureForecast:
    """Temperature forecast for a time slot.
    
    Attributes:
        slot_time: The datetime this forecast applies to
        temperature: Forecasted temperature in °C
        condition: Weather condition (sunny, cloudy, etc.)
    """
    slot_time: datetime
    temperature: float | None = None
    condition: str = "unknown"
```

Constants to add to `const.py`:

```python
# Weather configuration
CONF_WEATHER_ENTITY = "weather_entity"
DEFAULT_WEATHER_ENTITY = "weather.home"

# Temperature thresholds (configurable)
CONF_COOLING_THRESHOLD = "cooling_threshold"
CONF_HEATING_THRESHOLD = "heating_threshold"
DEFAULT_COOLING_THRESHOLD = 24.0  # °C
DEFAULT_HEATING_THRESHOLD = 18.0  # °C

# Learning configuration
CONF_WEATHER_LEARNING_ENABLED = "weather_learning_enabled"
DEFAULT_WEATHER_LEARNING_ENABLED = True
```

[Files]

### New Files to Create

1. **`custom_components/localshift/weather_correlation.py`** (~400 lines)
   - `WeatherCorrelation` class - Main learning and prediction engine
   - `HourlyTemperatureCoefficients` dataclass
   - `WeatherCorrelationData` dataclass
   - Storage load/save methods using HA's `storage` module
   - Learning algorithm (incremental regression)
   - Prediction method for consumption adjustment

### Files to Modify

2. **`custom_components/localshift/const.py`**
   - Add `CONF_WEATHER_ENTITY`, `CONF_COOLING_THRESHOLD`, `CONF_HEATING_THRESHOLD`, `CONF_WEATHER_LEARNING_ENABLED`
   - Add default values for weather-related settings
   - Add to `DEFAULT_ENTITY_IDS` dict

3. **`custom_components/localshift/config_flow.py`**
   - Add weather entity selector in `async_step_solcast` or new step
   - Add temperature threshold configuration in options flow
   - Pre-fill dropdown with first available weather entity

4. **`custom_components/localshift/computation_engine_lib/history_fetcher.py`**
   - Add method to fetch historical temperature data from HA statistics
   - `async_get_historical_temperatures()` - fetch temperature stats for learning
   - Integrate with weather entity's historical data

5. **`custom_components/localshift/computation_engine_lib/forecast_computer.py`**
   - Modify `_estimate_hourly_consumption_kw()` to apply weather adjustment
   - Add method `_get_weather_adjusted_load()` that uses temperature forecast
   - Add temperature forecast fetching from weather entity
   - Apply learned coefficients when temperature data available

6. **`custom_components/localshift/computation_engine.py`**
   - Initialize `WeatherCorrelation` instance
   - Add weather correlation to coordinator lifecycle (start/stop)
   - Trigger learning updates on periodic tick
   - Pass weather data to forecast computer

7. **`custom_components/localshift/coordinator.py`**
   - Add weather entity to monitored entities
   - Handle weather state changes
   - Store reference to WeatherCorrelation instance

8. **`custom_components/localshift/coordinator_data.py`**
   - Add fields for weather diagnostics:
     - `weather_temperature_current: float`
     - `weather_temperature_forecast: dict[int, float]` (hour -> temp)
     - `weather_correlation_confidence: str`
     - `weather_adjustment_applied: bool`

9. **`custom_components/localshift/sensor.py`**
   - Add `WeatherCorrelationSensor` to expose learned coefficients as attributes
   - Add temperature-related attributes to existing sensors

10. **`custom_components/localshift/state_reader.py`**
    - Add method to read weather entity state and forecast
    - `_read_weather_state()` - populate weather data into CoordinatorData

### Files to Update for Documentation

11. **`docs/ARCHITECTURE.md`**
    - Add WeatherCorrelation component to architecture diagram
    - Document weather data flow

12. **`docs/ENTITY_REFERENCE.md`**
    - Document new weather-related sensor attributes

[Functions]

### New Functions

1. **`WeatherCorrelation.__init__()`** (`weather_correlation.py`)
   - Initialize with hass, entry, storage
   - Load persisted coefficients from storage

2. **`WeatherCorrelation.async_load()`** (`weather_correlation.py`)
   - Load data from HA storage
   - Return WeatherCorrelationData

3. **`WeatherCorrelation.async_save()`** (`weather_correlation.py`)
   - Persist coefficients to HA storage

4. **`WeatherCorrelation.learn_from_sample()`** (`weather_correlation.py`)
   - Update coefficients based on observed temperature/load pair
   - Signature: `(hour: int, temperature: float, actual_load_kw: float) -> None`
   - Incremental learning algorithm

5. **`WeatherCorrelation.predict_load()`** (`weather_correlation.py`)
   - Apply learned coefficients to predict load given temperature
   - Signature: `(hour: int, temperature: float, base_load_kw: float) -> float`

6. **`WeatherCorrelation.recalculate_coefficients()`** (`weather_correlation.py`)
   - Full recalculation from historical data
   - Called periodically (daily) to refine model

7. **`WeatherCorrelation.get_temperature_forecast()`** (`weather_correlation.py`)
   - Fetch forecasted temperatures from weather entity
   - Return dict mapping hours to temperatures

8. **`HistoryFetcher.async_get_historical_temperatures()`** (`history_fetcher.py`)
   - Fetch historical temperature data from weather entity or statistics
   - Return list of (datetime, temperature) tuples

9. **`ForecastComputer._get_weather_adjusted_load()`** (`forecast_computer.py`)
   - Apply weather correlation to base load estimate
   - Signature: `(hour: int, base_load_kw: float, temperature: float | None) -> tuple[float, str]`

### Modified Functions

10. **`ForecastComputer._estimate_hourly_consumption_kw()`** (`forecast_computer.py`)
    - Current location: lines ~150-200
    - Modification: Call `_get_weather_adjusted_load()` after base calculation
    - Apply temperature-based adjustment when temperature forecast available

11. **`ConfigFlow.async_step_solcast()`** (`config_flow.py`)
    - Add weather entity selector
    - Validate weather entity exists

12. **`OptionsFlow.async_step_init()`** (`config_flow.py`)
    - Add temperature threshold sliders
    - Add enable/disable toggle for weather learning

13. **`LocalShiftCoordinator.async_start()`** (`coordinator.py`)
    - Initialize WeatherCorrelation instance
    - Load persisted coefficients

14. **`LocalShiftCoordinator._handle_periodic_tick()`** (`coordinator.py`)
    - Trigger learning update with current temperature/load observation

15. **`StateReader.read_all_external_state()`** (`state_reader.py`)
    - Add call to `_read_weather_state()`

[Classes]

### New Classes

1. **`WeatherCorrelation`** (`weather_correlation.py`)
   - Main class for weather-based consumption prediction
   - Key methods:
     - `async_initialize()` - Load from storage, setup
     - `async_learn()` - Process new observation
     - `predict_adjustment()` - Calculate load adjustment
     - `get_diagnostics()` - Return learning stats
   - Uses HA's `storage.Store` for persistence

### Modified Classes

2. **`HistoryFetcher`** (`history_fetcher.py`)
   - Add temperature fetching capability
   - New property: `_historical_temperatures: list[tuple[datetime, float]]`
   - New method: `async_get_historical_temperatures()`

3. **`ForecastComputer`** (`forecast_computer.py`)
   - Add weather correlation reference
   - Modify consumption estimation to include weather adjustment
   - New property: `_weather_correlation: WeatherCorrelation | None`

4. **`CoordinatorData`** (`coordinator_data.py`)
   - Add weather-related fields (see Files section)

5. **`LocalShiftCoordinator`** (`coordinator.py`)
   - Add WeatherCorrelation instance
   - Add weather entity to monitored entities

[Dependencies]

### New Dependencies

No new external dependencies required. All functionality uses built-in Home Assistant APIs:

- `homeassistant.helpers.storage.Store` - For persistent coefficient storage
- `homeassistant.components.weather` - For weather entity integration
- `homeassistant.components.recorder.statistics` - For historical temperature data (already used)

### Integration Dependencies

The feature depends on:
- Weather entity configured in Home Assistant (e.g., `weather.home`, BOM integration, OpenWeatherMap)
- Weather entity must provide temperature forecasts (most weather integrations do)

[Testing]

### Test Files Required

1. **`tests/test_weather_correlation.py`** (~300 lines)
   - Test `HourlyTemperatureCoefficients` dataclass
   - Test `WeatherCorrelationData` serialization
   - Test `learn_from_sample()` coefficient updates
   - Test `predict_load()` accuracy
   - Test storage save/load
   - Test edge cases (no data, extreme temperatures)

2. **`tests/test_forecast_computer_weather.py`** (~150 lines)
   - Test `_get_weather_adjusted_load()` function
   - Test integration with `_estimate_hourly_consumption_kw()`
   - Test fallback when no temperature data

3. **`tests/test_config_flow_weather.py`** (~100 lines)
   - Test weather entity validation
   - Test options flow for temperature thresholds

### Test Scenarios

1. **Learning Accuracy**
   - Given: 7 days of temperature/load observations
   - When: Model learns coefficients
   - Then: Predictions within 20% of actual for temperature extremes

2. **Hot Day Prediction**
   - Given: 35°C forecast for 2 PM, learned cooling_coefficient = 0.15 kW/°C
   - When: Predicting load
   - Then: Load = base + (35-24) × 0.15 = base + 1.65 kW

3. **Cold Day Prediction**
   - Given: 10°C forecast for 7 AM, learned heating_coefficient = 0.10 kW/°C
   - When: Predicting load
   - Then: Load = base + (18-10) × 0.10 = base + 0.8 kW

4. **Mild Day Prediction**
   - Given: 22°C forecast (within 18-24°C band)
   - When: Predicting load
   - Then: Load = base (no adjustment)

5. **No Weather Entity**
   - Given: Weather entity not configured
   - When: System starts
   - Then: Falls back to historical averages only (no errors)

6. **Storage Persistence**
   - Given: Learned coefficients from 30 days of data
   - When: Home Assistant restarts
   - Then: Coefficients loaded from storage, predictions continue

[Implementation Order]

1. **Phase 1: Data Structures and Storage**
   - Add constants to `const.py`
   - Create `HourlyTemperatureCoefficients` and `WeatherCorrelationData` dataclasses
   - Add weather fields to `CoordinatorData`

2. **Phase 2: WeatherCorrelation Class**
   - Implement `WeatherCorrelation` class with storage
   - Implement `learn_from_sample()` method
   - Implement `predict_load()` method
   - Write unit tests for WeatherCorrelation

3. **Phase 3: Configuration**
   - Add weather entity selector to config flow
   - Add temperature threshold options
   - Validate weather entity configuration

4. **Phase 4: Data Collection**
   - Add `_read_weather_state()` to StateReader
   - Add `async_get_historical_temperatures()` to HistoryFetcher
   - Wire weather entity into coordinator

5. **Phase 5: Integration with ForecastComputer**
   - Add `_get_weather_adjusted_load()` method
   - Modify `_estimate_hourly_consumption_kw()` to use weather adjustment
   - Add temperature forecast fetching

6. **Phase 6: Learning Loop**
   - Trigger learning on periodic tick
   - Store observations (temperature, actual load)
   - Update coefficients incrementally

7. **Phase 7: Diagnostics and Sensors**
   - Add `WeatherCorrelationSensor` for visibility
   - Add weather attributes to forecast sensor
   - Update documentation

8. **Phase 8: Testing and Validation**
   - Complete test coverage
   - Integration testing
   - Manual validation with real data
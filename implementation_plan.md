# Implementation Plan

[Overview]
Add a "Conservative Spike Discharge" mode that intelligently manages battery exports during price spikes to maximise revenue while ensuring sufficient reserve to avoid grid imports during the spike and any overlapping demand window.

This feature addresses the unique characteristics of price spikes: prices can reach $20/kWh (100x normal), the goal is to export as much as possible at peak prices, but we must avoid the severe penalty of importing during a spike (especially during a demand window). The system will calculate a dynamic reserve based on consumption forecasts using existing load estimation methodology, and only export at a configurable top percentage of spike prices.

Key design decisions from requirements:
- Percentage-based price threshold (configurable, default top 25% of spike prices)
- Simple consumption estimation using existing load forecast methodology
- Reserve for FULL demand window duration if spike overlaps DW (extreme penalty for DW imports)
- New switch to enable/disable conservative mode (off by default, opt-in)

[Types]
New constants and configuration types for spike discharge behavior.

```python
# New switch key for conservative spike mode
SWITCH_SPIKE_DISCHARGE_CONSERVATIVE = "spike_discharge_conservative"

# New config option for price percentile threshold
CONF_SPIKE_PRICE_PERCENTILE = "spike_price_percentile"
DEFAULT_SPIKE_PRICE_PERCENTILE = 75  # Only export at top 25% of spike prices
```

New data fields in CoordinatorData:
```python
spike_end_time: datetime | None = None  # Estimated end of current spike
spike_max_price: float = 0.0  # Maximum price within spike window
spike_price_threshold: float = 0.0  # Price threshold for top X% percentile
spike_reserve_soc: float = 0.0  # Calculated reserve SOC for spike survival
spike_hours_remaining: float = 0.0  # Hours until spike ends
spike_in_conservative_mode: bool = False  # Whether conservative mode is active
```

[Files]
Modifications to existing files:

1. **`custom_components/localshift/const.py`**
   - Add `SWITCH_SPIKE_DISCHARGE_CONSERVATIVE` constant
   - Add `CONF_SPIKE_PRICE_PERCENTILE` constant with default (75)
   - Add to `SWITCH_DEFAULTS` dict (default: False)
   - Add to `SWITCH_ICONS` dict (icon: "mdi:shield-check")
   - Add to `SWITCH_NAMES` dict
   - Add entry to `THRESHOLD_RANGES` for configurable percentile (min: 50, max: 95, step: 5)

2. **`custom_components/localshift/coordinator_data.py`**
   - Add spike analysis fields to `CoordinatorData` dataclass

3. **`custom_components/localshift/computation_engine_lib/utils.py`**
   - Add `analyze_spike_window()` function to extract spike window details
   - Add `calculate_spike_price_threshold()` function for percentile calculation

4. **`custom_components/localshift/computation_engine.py`**
   - Add `_calculate_spike_reserve_soc()` method for reserve calculation
   - Add `_analyze_spike()` method to orchestrate spike analysis
   - Modify `compute_derived_values()` to call spike analysis
   - Modify `_compute_active_mode()` to check conservative mode and price threshold

5. **`custom_components/localshift/battery_controller.py`**
   - Modify `set_force_discharge()` to accept optional `reserve_soc` parameter
   - Or add new `set_conservative_spike_discharge()` method

6. **`custom_components/localshift/switch.py`**
   - Add new switch entity for `spike_discharge_conservative`

7. **`custom_components/localshift/number.py`**
   - Add new number entity for `spike_price_percentile` configuration

8. **`custom_components/localshift/strings.json`**
   - Add string definitions for new switch and number entity

9. **`custom_components/localshift/translations/en.json`**
   - Add translation strings for new UI elements

[Functions]
New functions to implement:

1. **`analyze_spike_window()`** in `utils.py`
   - Purpose: Analyze feed-in forecast for spike window details
   - Signature: `def analyze_spike_window(forecasts: list[dict], now_dt: datetime, max_lookahead_hours: float = 8.0) -> tuple[datetime | None, float, list[float]]`
   - Returns: (spike_end_time, max_price, all_spike_prices)

2. **`calculate_spike_price_threshold()`** in `utils.py`
   - Purpose: Calculate price threshold for top X% of spike prices
   - Signature: `def calculate_spike_price_threshold(spike_prices: list[float], percentile: float) -> float`
   - Returns: Price threshold

3. **`_calculate_spike_reserve_soc()`** in `computation_engine.py`
   - Purpose: Calculate reserve SOC needed to survive spike + DW
   - Uses existing `_get_expected_load_kw()` methodology
   - Handles DW overlap case (reserve for full DW duration)

4. **`_analyze_spike()`** in `computation_engine.py`
   - Purpose: Orchestrate spike analysis and set data fields

Modified functions:

1. **`_compute_active_mode()`** in `computation_engine.py`
   - Add logic to check conservative mode switch
   - Add logic to check current FIT price against threshold
   - Route to appropriate discharge method based on mode

2. **`set_force_discharge()`** or new method in `battery_controller.py`
   - Accept optional `reserve_soc` parameter for conservative mode

[Classes]
No new classes required. Modifications to existing classes:

1. **`CoordinatorData`** - Add new data fields
2. **`ComputationEngine`** - Add new methods
3. **`BatteryController`** - Modify existing method signature
4. **`LocalShiftSwitch`** - Add new switch entity (already exists)
5. **`LocalShiftNumber`** - Add new number entity (already exists)

[Dependencies]
No new dependencies required. Uses existing:
- Existing load estimation methodology
- Existing forecast data structures
- Existing percentile calculation utilities

[Testing]
Test requirements:

1. Add unit tests for `analyze_spike_window()` in `tests/test_computation_engine.py`
2. Add unit tests for `calculate_spike_price_threshold()`
3. Add unit tests for `_calculate_spike_reserve_soc()`
4. Add integration tests for spike discharge behavior
5. Run full test suite after changes

[Implementation Order]
Sequential steps to implement:

1. Add constants to `const.py`
2. Add data fields to `coordinator_data.py`
3. Add utility functions to `utils.py`
4. Add spike reserve calculation to `computation_engine.py`
5. Modify `_compute_active_mode()` for price threshold check
6. Modify battery controller for dynamic reserve
7. Add switch entity in `switch.py`
8. Add number entity in `number.py`
9. Update `strings.json` and `translations/en.json`
10. Add unit tests
11. Run full test suite and verify

---

## Algorithm Flow

```
1. When price_spike == True:
   │
   ├─► Analyze feed_in_forecast for spike window
   │   ├─ Find spike_end_time (last slot with spike_status)
   │   ├─ Collect all prices within spike window
   │   └─ Calculate max_price and price_threshold
   │
   ├─► Calculate spike_reserve_soc
   │   ├─ If DW overlaps spike:
   │   │   └─ reserve = consumption_for_full_DW + buffer
   │   └─ Else:
   │       └─ reserve = consumption_until_spike_end + buffer
   │
   ├─► Check current FIT price against threshold
   │   ├─ If FIT >= threshold (top X%):
   │   │   └─ Export with reserve = spike_reserve_soc
   │   └─ Else:
   │       └─ Stay in self-consumption (wait for better price)
   │
   └─► Set battery mode with calculated reserve
```

---

## Configuration Options

| Option | Default | Range | Description |
|--------|---------|-------|-------------|
| `spike_discharge_conservative` | False | on/off | Enable conservative mode |
| `spike_price_percentile` | 75 | 50-95 | Only export at prices above this percentile |
</parameter>
</write_to_file>
# Implementation Plan

[Overview]
Simplify forecast system by removing hybrid timescale complexity and using 15-minute slots throughout, while preserving 5-minute real-time price reactivity for current decisions.

The current hybrid timescale implementation (24×5-min near-term + 88×15-min long-term) has introduced multiple bugs due to misaligned boundaries between the main forecast loop and simulation functions. This plan removes the hybrid complexity while maintaining the ability to react to 5-minute price changes for real-time decisions. The forecast table will use 15-minute slots exclusively (96 slots = 24 hours), which always aligns with Solcast 30-minute periods. Real-time price decisions (grid charging, exports) will use the actual 5-minute spot price for the current slot only, with forecast prices for future slots.

[Types]
No new types required; constants will be simplified.

Constants to modify:
- Remove: `NEAR_TERM_COUNT = 24` 
- Remove: `LONG_TERM_COUNT = 88`
- Add: `TOTAL_SLOTS = 96` (24 hours × 4 slots/hour)

[Files]
Two files require modification:

1. **`custom_components/localshift/computation_engine_lib/forecast_computer.py`**
   - Remove `_align_to_15min_boundary()` function (no longer needed)
   - Remove `_get_hybrid_slots_for_simulation()` function (no longer needed)
   - Simplify `compute_forecast()` to use 15-min slots only
   - Simplify `_simulate_future_soc_with_solar_only()` to use 15-min slots
   - Simplify `_simulate_overnight_drain_to_solar()` to use 15-min slots
   - Simplify `_simulate_minimum_soc_without_exports()` to use 15-min slots
   - Simplify `_simulate_overnight_drain_after_export()` to use 15-min slots
   - Simplify `_find_battery_fill_point()` to use 15-min slots
   - Simplify `_calculate_solar_energy_between_slots()` to use 15-min slots
   - Fix `_should_grid_charge_at_slot()` to only use spot price for current slot

2. **`tests/test_hybrid_timescale.py`**
   - Update tests to reflect 15-min only approach
   - Rename to `tests/test_forecast_timescale.py` or keep name but update content

[Functions]
Multiple functions require simplification:

1. **`compute_forecast()`** (forecast_computer.py)
   - Current: Uses hybrid timescale with complex boundary alignment
   - Change: Simple loop `for i in range(TOTAL_SLOTS)` with `base_slot + timedelta(minutes=15*i)`
   - Purpose: Generate 96 × 15-min slots aligned to :00, :15, :30, :45

2. **`_simulate_future_soc_with_solar_only()`** (forecast_computer.py)
   - Current: Uses `_get_hybrid_slots_for_simulation()` helper
   - Change: Simple loop with 15-min slots via `get_solar_for_15min_slot()`
   - Purpose: Consistent SOC simulation matching main forecast

3. **`_simulate_overnight_drain_to_solar()`** (forecast_computer.py)
   - Current: Uses `_get_hybrid_slots_for_simulation()` helper
   - Change: Simple loop with 15-min slots
   - Purpose: Consistent overnight drain simulation

4. **`_simulate_minimum_soc_without_exports()`** (forecast_computer.py)
   - Current: Uses `_get_hybrid_slots_for_simulation()` helper
   - Change: Simple loop with 15-min slots
   - Purpose: Consistent minimum SOC calculation

5. **`_simulate_overnight_drain_after_export()`** (forecast_computer.py)
   - Current: Uses `_get_hybrid_slots_for_simulation()` helper
   - Change: Simple loop with 15-min slots
   - Purpose: Consistent post-export drain simulation

6. **`_find_battery_fill_point()`** (forecast_computer.py)
   - Current: Uses hybrid timescale (NEAR_TERM_COUNT + LONG_TERM_COUNT)
   - Change: Simple loop with `for i in range(TOTAL_SLOTS)`
   - Purpose: Find when battery reaches 100% from solar

7. **`_calculate_solar_energy_between_slots()`** (forecast_computer.py)
   - Current: Uses hybrid timescale
   - Change: Simple 15-min slot loop
   - Purpose: Calculate solar energy between time points

8. **`_should_grid_charge_at_slot()`** (forecast_computer.py)
   - Current: Uses `general_price_current` for ALL slots
   - Change: Only use `general_price_current` if `is_current_slot=True`, otherwise use `slot_price`
   - Purpose: Fix price bug - use forecast price for future slots

9. **REMOVE: `_align_to_15min_boundary()`** (forecast_computer.py)
   - Reason: No longer needed with 15-min only approach

10. **REMOVE: `_get_hybrid_slots_for_simulation()`** (forecast_computer.py)
    - Reason: No longer needed with 15-min only approach

[Classes]
No class modifications required; all changes are within `ForecastComputer` class methods.

[Dependencies]
No new dependencies required.

[Testing]
Test requirements:

1. **Update `tests/test_hybrid_timescale.py`**:
   - Update constants test to expect `TOTAL_SLOTS = 96`
   - Remove NEAR_TERM_COUNT and LONG_TERM_COUNT tests
   - Update slot generation tests for 15-min only

2. **Add boundary alignment tests**:
   - Test coordinator runs at :00, :05, :10, :15, etc.
   - Verify all produce identical slot times (aligned to :00, :15, :30, :45)

3. **Add price decision tests**:
   - Test that current slot uses spot price
   - Test that future slots use forecast price

4. **Run full test suite** after changes

[Implementation Order]
Sequential steps to minimize conflicts:

1. Update constants: Remove `NEAR_TERM_COUNT` and `LONG_TERM_COUNT`, add `TOTAL_SLOTS = 96`
2. Remove `_align_to_15min_boundary()` function
3. Remove `_get_hybrid_slots_for_simulation()` function
4. Simplify `compute_forecast()` to use 15-min slots only
5. Simplify `_find_battery_fill_point()` to use 15-min slots only
6. Simplify `_calculate_solar_energy_between_slots()` to use 15-min slots only
7. Simplify `_simulate_future_soc_with_solar_only()` to use 15-min slots only
8. Simplify `_simulate_overnight_drain_to_solar()` to use 15-min slots only
9. Simplify `_simulate_minimum_soc_without_exports()` to use 15-min slots only
10. Simplify `_simulate_overnight_drain_after_export()` to use 15-min slots only
11. Fix `_should_grid_charge_at_slot()` to only use spot price for current slot
12. Update `tests/test_hybrid_timescale.py` for new 15-min only approach
13. Run full test suite and verify all tests pass
14. Manual verification with real forecast data
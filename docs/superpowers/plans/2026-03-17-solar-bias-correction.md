# Solar Bias Correction Fix Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix solar forecast bias correction overcorrection by adding sample gate, bounds, and precedence over solar_confidence_factor.

**Architecture:** Add MIN_SOLAR_CORRECTION_SAMPLES=20 gate to SolarAccuracyTracker, clamp corrections to [0.5, 1.5], and modify SlotBuilder to skip solar_confidence_factor when bias correction is active.

**Tech Stack:** Python 3.13+, pytest, Home Assistant storage API

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `custom_components/localshift/forecast/solar_accuracy.py` | Modify | Add sample gate and bounds |
| `custom_components/localshift/engine/slots.py` | Modify | Add tracker param, check bias readiness |
| `custom_components/localshift/engine/optimizer_facade.py` | Modify | Pass tracker to SlotBuilder |
| `tests/forecast/test_solar_accuracy.py` | Modify | Add tests for sample gate and bounds |
| `tests/test_slots.py` | Modify | Add tests for bias precedence |

---

## Chunk 1: Add Sample Gate and Bounds to SolarAccuracyTracker

### Task 1: Add MIN_SOLAR_CORRECTION_SAMPLES Constant

**Files:**
- Modify: `custom_components/localshift/forecast/solar_accuracy.py:1-50`

- [ ] **Step 1: Add the constant after imports**

```python
# Add after line 20 (after existing constants like MAX_PERIOD_RECORDS)
MIN_SOLAR_CORRECTION_SAMPLES = 20
```

- [ ] **Step 2: Verify syntax**

Run: `cd /config/home/localshift/worktrees/issue-756 && uv run python -c "from custom_components.localshift.forecast.solar_accuracy import MIN_SOLAR_CORRECTION_SAMPLES; print(MIN_SOLAR_CORRECTION_SAMPLES)"`
Expected: `20`

- [ ] **Step 3: Commit**

```bash
git add custom_components/localshift/forecast/solar_accuracy.py
git commit -m "feat(solar): add MIN_SOLAR_CORRECTION_SAMPLES constant"
```

---

### Task 2: Add has_sufficient_samples Method

**Files:**
- Modify: `custom_components/localshift/forecast/solar_accuracy.py` (SolarAccuracyTracker class)

- [ ] **Step 1: Write the failing test**

```python
# In tests/forecast/test_solar_accuracy.py, add to TestSolarAccuracyTracker class

def test_has_sufficient_samples_false_when_not_enough(self, tracker):
    """Test has_sufficient_samples returns False with < 20 samples."""
    assert tracker.has_sufficient_samples() is False
    
    # Add some records but not enough
    for i in range(10):
        tracker.record_forecast(
            period_start=datetime(2024, 1, 1, 6, 0) + timedelta(minutes=30 * i),
            forecast_kwh=1.0,
            weather_condition="sunny",
        )
        tracker.backfill_actual(
            period_start=datetime(2024, 1, 1, 6, 0) + timedelta(minutes=30 * i),
            actual_kwh=0.8,
        )
    
    assert tracker.has_sufficient_samples() is False


def test_has_sufficient_samples_true_when_enough(self, tracker):
    """Test has_sufficient_samples returns True with >= 20 samples."""
    # Add 20 records
    for i in range(20):
        tracker.record_forecast(
            period_start=datetime(2024, 1, 1, 6, 0) + timedelta(minutes=30 * i),
            forecast_kwh=1.0,
            weather_condition="sunny",
        )
        tracker.backfill_actual(
            period_start=datetime(2024, 1, 1, 6, 0) + timedelta(minutes=30 * i),
            actual_kwh=0.8,
        )
    
    assert tracker.has_sufficient_samples() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /config/home/localshift/worktrees/issue-756 && uv run pytest tests/forecast/test_solar_accuracy.py::TestSolarAccuracyTracker::test_has_sufficient_samples_false_when_not_enough -v`
Expected: FAIL with `AttributeError: 'SolarAccuracyTracker' object has no attribute 'has_sufficient_samples'`

- [ ] **Step 3: Write minimal implementation**

```python
# In SolarAccuracyTracker class, add after the metrics property:

def has_sufficient_samples(self) -> bool:
    """Check if we have enough samples for bias correction.
    
    Returns:
        True if sample_count >= MIN_SOLAR_CORRECTION_SAMPLES.
    """
    return self._metrics.sample_count >= MIN_SOLAR_CORRECTION_SAMPLES
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /config/home/localshift/worktrees/issue-756 && uv run pytest tests/forecast/test_solar_accuracy.py::TestSolarAccuracyTracker::test_has_sufficient_samples_false_when_not_enough tests/forecast/test_solar_accuracy.py::TestSolarAccuracyTracker::test_has_sufficient_samples_true_when_enough -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/forecast/solar_accuracy.py tests/forecast/test_solar_accuracy.py
git commit -m "feat(solar): add has_sufficient_samples method with tests"
```

---

### Task 3: Add Sample Gate to get_bias_correction

**Files:**
- Modify: `custom_components/localshift/forecast/solar_accuracy.py` (get_bias_correction method)
- Modify: `tests/forecast/test_solar_accuracy.py`

- [ ] **Step 1: Write the failing test**

```python
# In tests/forecast/test_solar_accuracy.py, add to TestSolarAccuracyTracker class

def test_get_bias_correction_returns_1_with_insufficient_samples(self, tracker):
    """Test get_bias_correction returns 1.0 with < 20 samples."""
    # Add 5 records with consistent bias
    for i in range(5):
        tracker.record_forecast(
            period_start=datetime(2024, 1, 1, 6, 0) + timedelta(minutes=30 * i),
            forecast_kwh=2.0,
            weather_condition="sunny",
        )
        tracker.backfill_actual(
            period_start=datetime(2024, 1, 1, 6, 0) + timedelta(minutes=30 * i),
            actual_kwh=1.0,  # 50% overestimate
        )
    
    # Should return 1.0 (no correction) because not enough samples
    result = tracker.get_bias_correction("morning", "sunny", "summer")
    assert result == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /config/home/localshift/worktrees/issue-756 && uv run pytest tests/forecast/test_solar_accuracy.py::TestSolarAccuracyTracker::test_get_bias_correction_returns_1_with_insufficient_samples -v`
Expected: FAIL (returns correction based on 5 samples instead of 1.0)

- [ ] **Step 3: Modify get_bias_correction to add sample gate**

```python
# Replace the get_bias_correction method with:

def get_bias_correction(
    self,
    time_of_day: str,
    weather: str,
    season: str | None = None,
) -> float:
    """Get bias correction factor for given context.

    Returns a multiplier clamped to [0.5, 1.5] to apply to forecasts.
    A positive bias means forecasts overestimate, so we reduce solar_kwh.
    A negative bias means forecasts underestimate, so we increase solar_kwh.

    Args:
        time_of_day: Time bucket ('morning', 'afternoon', 'evening', 'night')
        weather: Weather condition ('sunny', 'cloudy', 'rainy', etc.)
        season: Season ('summer', 'autumn', 'winter', 'spring'), optional for coarser granularity

    Returns:
        Bias correction factor. Returns 1.0 if insufficient samples (< MIN_SOLAR_CORRECTION_SAMPLES).
        Values < 1.0 reduce forecast (overestimate), > 1.0 increase (underestimate).

    """
    normalized_weather = self._normalize_weather(weather)
    result = self._compute_context_bias(time_of_day, normalized_weather, season)
    if result is None:
        return 1.0

    weighted_bias, sample_count = result
    
    # Require minimum samples before applying correction
    if sample_count < MIN_SOLAR_CORRECTION_SAMPLES:
        _LOGGER.debug(
            "Bias correction skipped for %s/%s/%s: only %d samples (need %d)",
            time_of_day,
            normalized_weather,
            season or "any",
            sample_count,
            MIN_SOLAR_CORRECTION_SAMPLES,
        )
        return 1.0

    correction = 1.0 - weighted_bias
    
    # Clamp to safe bounds
    clamped = max(0.5, min(1.5, correction))
    
    _LOGGER.debug(
        "Bias correction for %s/%s/%s: bias=%.2f%%, samples=%d, correction=%.2f",
        time_of_day,
        normalized_weather,
        season or "any",
        weighted_bias * 100,
        sample_count,
        clamped,
    )
    return clamped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /config/home/localshift/worktrees/issue-756 && uv run pytest tests/forecast/test_solar_accuracy.py::TestSolarAccuracyTracker::test_get_bias_correction_returns_1_with_insufficient_samples -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/forecast/solar_accuracy.py tests/forecast/test_solar_accuracy.py
git commit -m "feat(solar): add sample gate to get_bias_correction"
```

---

### Task 4: Add Bounds Test for get_bias_correction

**Files:**
- Modify: `tests/forecast/test_solar_accuracy.py`

- [ ] **Step 1: Write the test**

```python
# In tests/forecast/test_solar_accuracy.py, add to TestSolarAccuracyTracker class

def test_get_bias_correction_clamped_to_bounds(self, tracker):
    """Test get_bias_correction clamps extreme values to [0.5, 1.5]."""
    # Add 25 records with extreme overestimate (100% overestimate)
    for i in range(25):
        tracker.record_forecast(
            period_start=datetime(2024, 1, 1, 6, 0) + timedelta(minutes=30 * i),
            forecast_kwh=2.0,
            weather_condition="sunny",
        )
        tracker.backfill_actual(
            period_start=datetime(2024, 1, 1, 6, 0) + timedelta(minutes=30 * i),
            actual_kwh=1.0,  # 50% bias = overestimate
        )
    
    # Bias is 0.5, so correction would be 1.0 - 0.5 = 0.5
    # This is at the lower bound
    result = tracker.get_bias_correction("morning", "sunny", "summer")
    assert result == 0.5


def test_get_bias_correction_clamped_to_upper_bound(self, tracker):
    """Test get_bias_correction clamps to 1.5 upper bound."""
    # Add 25 records with extreme underestimate (forecasts are 50% of actual)
    for i in range(25):
        tracker.record_forecast(
            period_start=datetime(2024, 1, 1, 6, 0) + timedelta(minutes=30 * i),
            forecast_kwh=1.0,
            weather_condition="sunny",
        )
        tracker.backfill_actual(
            period_start=datetime(2024, 1, 1, 6, 0) + timedelta(minutes=30 * i),
            actual_kwh=2.0,  # -50% bias = underestimate
        )
    
    # Bias is -0.5, so correction would be 1.0 - (-0.5) = 1.5
    # This is at the upper bound
    result = tracker.get_bias_correction("morning", "sunny", "summer")
    assert result == 1.5
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd /config/home/localshift/worktrees/issue-756 && uv run pytest tests/forecast/test_solar_accuracy.py::TestSolarAccuracyTracker::test_get_bias_correction_clamped_to_bounds tests/forecast/test_solar_accuracy.py::TestSolarAccuracyTracker::test_get_bias_correction_clamped_to_upper_bound -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/forecast/test_solar_accuracy.py
git commit -m "test(solar): add bounds tests for get_bias_correction"
```

---

### Task 5: Add Sample Gate to get_additive_correction

**Files:**
- Modify: `custom_components/localshift/forecast/solar_accuracy.py` (get_additive_correction method)
- Modify: `tests/forecast/test_solar_accuracy.py`

- [ ] **Step 1: Write the failing test**

```python
# In tests/forecast/test_solar_accuracy.py, add to TestSolarAccuracyTracker class

def test_get_additive_correction_returns_0_with_insufficient_samples(self, tracker):
    """Test get_additive_correction returns 0.0 with < 20 samples."""
    # Add 5 records
    for i in range(5):
        tracker.record_forecast(
            period_start=datetime(2024, 1, 1, 6, 0) + timedelta(minutes=30 * i),
            forecast_kwh=2.0,
            weather_condition="sunny",
        )
        tracker.backfill_actual(
            period_start=datetime(2024, 1, 1, 6, 0) + timedelta(minutes=30 * i),
            actual_kwh=1.0,
        )
    
    # Should return 0.0 because not enough samples
    result = tracker.get_additive_correction("morning", "sunny", "summer")
    assert result == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /config/home/localshift/worktrees/issue-756 && uv run pytest tests/forecast/test_solar_accuracy.py::TestSolarAccuracyTracker::test_get_additive_correction_returns_0_with_insufficient_samples -v`
Expected: FAIL (returns non-zero based on 5 samples)

- [ ] **Step 3: Modify get_additive_correction to add sample gate**

```python
# Replace the get_additive_correction method with:

def get_additive_correction(
    self,
    time_of_day: str,
    weather: str,
    season: str | None = None,
) -> float:
    """Get additive correction offset for given context.
    
    Returns 0.0 if insufficient samples (< MIN_SOLAR_CORRECTION_SAMPLES).
    """
    normalized_weather = self._normalize_weather(weather)
    result = self._compute_context_additive_bias(time_of_day, normalized_weather, season)
    if result is None:
        return 0.0

    weighted_bias, sample_count = result
    
    # Require minimum samples before applying correction
    if sample_count < MIN_SOLAR_CORRECTION_SAMPLES:
        return 0.0

    return max(
        -MAX_ADDITIVE_OFFSET_KWH,
        min(MAX_ADDITIVE_OFFSET_KWH, weighted_bias),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /config/home/localshift/worktrees/issue-756 && uv run pytest tests/forecast/test_solar_accuracy.py::TestSolarAccuracyTracker::test_get_additive_correction_returns_0_with_insufficient_samples -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/forecast/solar_accuracy.py tests/forecast/test_solar_accuracy.py
git commit -m "feat(solar): add sample gate to get_additive_correction"
```

---

## Chunk 2: Modify SlotBuilder for Bias Correction Precedence

### Task 6: Add solar_accuracy_tracker Parameter to SlotBuilder

**Files:**
- Modify: `custom_components/localshift/engine/slots.py` (SlotBuilder class)
- Modify: `tests/test_slots.py`

- [ ] **Step 1: Write the failing test**

```python
# In tests/test_slots.py, add to TestSlotBuilderInit class

def test_init_accepts_optional_solar_accuracy_tracker(self):
    """Test SlotBuilder accepts optional solar_accuracy_tracker parameter."""
    from unittest.mock import MagicMock
    
    mock_tracker = MagicMock()
    builder = SlotBuilder(
        config_options={},
        ha_timezone="Australia/Sydney",
        solar_accuracy_tracker=mock_tracker,
    )
    
    assert builder._solar_accuracy_tracker is mock_tracker


def test_init_defaults_solar_accuracy_tracker_to_none(self):
    """Test SlotBuilder defaults solar_accuracy_tracker to None."""
    builder = SlotBuilder(
        config_options={},
        ha_timezone="Australia/Sydney",
    )
    
    assert builder._solar_accuracy_tracker is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /config/home/localshift/worktrees/issue-756 && uv run pytest tests/test_slots.py::TestSlotBuilderInit::test_init_accepts_optional_solar_accuracy_tracker -v`
Expected: FAIL with `TypeError: SlotBuilder.__init__() got an unexpected keyword argument 'solar_accuracy_tracker'`

- [ ] **Step 3: Modify SlotBuilder.__init__**

```python
# Replace the __init__ method in SlotBuilder class with:

def __init__(
    self,
    config_options: dict[str, Any],
    ha_timezone: str,
    solar_accuracy_tracker: Any = None,
) -> None:
    """Store DW time config and timezone for slot generation.

    Args:
        config_options: Integration config options (for DW start/end parsing).
        ha_timezone: Home Assistant timezone string (e.g., "Australia/Sydney").
        solar_accuracy_tracker: Optional tracker for bias correction readiness check.

    """
    self._config_options = config_options
    self._ha_timezone = ha_timezone
    self._solar_accuracy_tracker = solar_accuracy_tracker
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /config/home/localshift/worktrees/issue-756 && uv run pytest tests/test_slots.py::TestSlotBuilderInit -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/engine/slots.py tests/test_slots.py
git commit -m "feat(slots): add solar_accuracy_tracker parameter to SlotBuilder"
```

---

### Task 7: Modify _get_solar_kwh for Bias Precedence

**Files:**
- Modify: `custom_components/localshift/engine/slots.py` (_get_solar_kwh method)
- Modify: `tests/test_slots.py`

- [ ] **Step 1: Write the failing test**

```python
# In tests/test_slots.py, add new test class after TestGetSolarKwh

class TestGetSolarKwhBiasPrecedence:
    """Tests for bias correction precedence over solar_confidence_factor."""

    @pytest.fixture
    def builder_with_tracker(self):
        """Create SlotBuilder with mock tracker."""
        from unittest.mock import MagicMock
        
        mock_tracker = MagicMock()
        mock_tracker.has_sufficient_samples.return_value = True
        
        return SlotBuilder(
            config_options={},
            ha_timezone="Australia/Sydney",
            solar_accuracy_tracker=mock_tracker,
        ), mock_tracker

    @pytest.fixture
    def builder_without_tracker(self):
        """Create SlotBuilder without tracker."""
        return SlotBuilder(
            config_options={},
            ha_timezone="Australia/Sydney",
        )

    def test_returns_raw_solar_when_bias_ready(self, builder_with_tracker):
        """Test returns raw solar when bias correction has sufficient samples."""
        builder, mock_tracker = builder_with_tracker
        
        solcast = [{"period_start": "2024-01-01T06:00:00", "pv_estimate": 2.0}]
        slot_start = datetime(2024, 1, 1, 6, 0, tzinfo=ZoneInfo("Australia/Sydney"))
        
        result = builder._get_solar_kwh(solcast, slot_start, 30, 0.8)
        
        # Should return raw solar (2.0) without applying solar_confidence_factor
        assert result == 2.0

    def test_applies_confidence_factor_when_bias_not_ready(self, builder_without_tracker):
        """Test applies solar_confidence_factor when bias correction not ready."""
        builder = builder_without_tracker
        
        solcast = [{"period_start": "2024-01-01T06:00:00", "pv_estimate": 2.0}]
        slot_start = datetime(2024, 1, 1, 6, 0, tzinfo=ZoneInfo("Australia/Sydney"))
        
        result = builder._get_solar_kwh(solcast, slot_start, 30, 0.8)
        
        # Should apply solar_confidence_factor: 2.0 * 0.8 = 1.6
        assert result == 1.6

    def test_applies_confidence_factor_when_tracker_has_insufficient_samples(self):
        """Test applies solar_confidence_factor when tracker has < 20 samples."""
        from unittest.mock import MagicMock
        
        mock_tracker = MagicMock()
        mock_tracker.has_sufficient_samples.return_value = False
        
        builder = SlotBuilder(
            config_options={},
            ha_timezone="Australia/Sydney",
            solar_accuracy_tracker=mock_tracker,
        )
        
        solcast = [{"period_start": "2024-01-01T06:00:00", "pv_estimate": 2.0}]
        slot_start = datetime(2024, 1, 1, 6, 0, tzinfo=ZoneInfo("Australia/Sydney"))
        
        result = builder._get_solar_kwh(solcast, slot_start, 30, 0.8)
        
        # Should apply solar_confidence_factor because tracker not ready
        assert result == 1.6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /config/home/localshift/worktrees/issue-756 && uv run pytest tests/test_slots.py::TestGetSolarKwhBiasPrecedence -v`
Expected: FAIL (returns 1.6 instead of 2.0 when bias ready)

- [ ] **Step 3: Modify _get_solar_kwh**

```python
# Replace the _get_solar_kwh method in SlotBuilder class with:

def _get_solar_kwh(
    self,
    all_solcast: list[dict[str, Any]],
    slot_start: datetime,
    interval_minutes: int,
    solar_confidence_factor: float,
) -> float:
    """Get solar kWh for a slot.

    If bias correction has sufficient samples, return raw solar
    (bias correction will be applied by OptimizerFacade).
    Otherwise, apply solar_confidence_factor as fallback.

    Args:
        all_solcast: Solar forecast data.
        slot_start: Start time of the slot.
        interval_minutes: Slot duration in minutes.
        solar_confidence_factor: Fallback multiplier when bias not ready.

    Returns:
        Solar energy in kWh for the slot.

    """
    raw_solar = get_solar_for_slot_by_interval(
        all_solcast, slot_start, interval_minutes
    )
    
    # Check if bias correction is ready (has enough samples)
    if self._solar_accuracy_tracker is not None:
        if self._solar_accuracy_tracker.has_sufficient_samples():
            # Bias correction will be applied by OptimizerFacade
            # Return raw solar without solar_confidence_factor
            return max(0.0, raw_solar)
    
    # Fall back to solar_confidence_factor when bias not ready
    return max(0.0, raw_solar * solar_confidence_factor)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /config/home/localshift/worktrees/issue-756 && uv run pytest tests/test_slots.py::TestGetSolarKwhBiasPrecedence -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/engine/slots.py tests/test_slots.py
git commit -m "feat(slots): bias correction supersedes solar_confidence_factor"
```

---

## Chunk 3: Wire Up OptimizerFacade

### Task 8: Pass Tracker to SlotBuilder in OptimizerFacade

**Files:**
- Modify: `custom_components/localshift/engine/optimizer_facade.py`

- [ ] **Step 1: Locate the SlotBuilder instantiation**

In `OptimizerFacade.run_inline()`, find where `SlotBuilder` is instantiated and add the tracker parameter.

- [ ] **Step 2: Modify the SlotBuilder instantiation**

```python
# In OptimizerFacade.run_inline(), modify the slot_builder instantiation:

# Before:
slot_builder = self._slot_builder_cls(
    config_options=config_options, ha_timezone=ha_timezone
)

# After:
slot_builder = self._slot_builder_cls(
    config_options=config_options,
    ha_timezone=ha_timezone,
    solar_accuracy_tracker=self._solar_accuracy_tracker,
)
```

- [ ] **Step 3: Verify syntax**

Run: `cd /config/home/localshift/worktrees/issue-756 && uv run python -c "from custom_components.localshift.engine.optimizer_facade import OptimizerFacade; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add custom_components/localshift/engine/optimizer_facade.py
git commit -m "feat(optimizer): pass solar_accuracy_tracker to SlotBuilder"
```

---

## Chunk 4: Run Full Test Suite and Verify

### Task 9: Run All Tests

- [ ] **Step 1: Run the full test suite**

Run: `cd /config/home/localshift/worktrees/issue-756 && uv run pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 2: Run coverage check**

Run: `cd /config/home/localshift/worktrees/issue-756 && uv run pytest --cov=custom_components/localshift --cov-report=term-missing tests/`
Expected: Coverage >= 95%

- [ ] **Step 3: Run linting**

Run: `cd /config/home/localshift/worktrees/issue-756 && uv run ruff check custom_components/localshift`
Expected: No errors

---

### Task 10: Final Commit and Push

- [ ] **Step 1: Verify all changes are committed**

Run: `cd /config/home/localshift/worktrees/issue-756 && git status`
Expected: Working tree clean

- [ ] **Step 2: Push to remote**

Run: `cd /config/home/localshift/worktrees/issue-756 && git push -u origin issue/756`
Expected: Push successful

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Add MIN_SOLAR_CORRECTION_SAMPLES constant | solar_accuracy.py |
| 2 | Add has_sufficient_samples method | solar_accuracy.py, test_solar_accuracy.py |
| 3 | Add sample gate to get_bias_correction | solar_accuracy.py, test_solar_accuracy.py |
| 4 | Add bounds tests | test_solar_accuracy.py |
| 5 | Add sample gate to get_additive_correction | solar_accuracy.py, test_solar_accuracy.py |
| 6 | Add tracker param to SlotBuilder | slots.py, test_slots.py |
| 7 | Modify _get_solar_kwh for precedence | slots.py, test_slots.py |
| 8 | Wire up OptimizerFacade | optimizer_facade.py |
| 9 | Run full test suite | - |
| 10 | Final commit and push | - |

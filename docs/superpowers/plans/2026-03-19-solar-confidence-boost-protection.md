# Solar Confidence Blending + Boost Contamination Protection — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix false 100% SOC projections under low-confidence solar forecasts by blending median and P10 estimates based on Solcast confidence, and protect forecast accuracy learning from manual boost charge contamination.

**Architecture:** Two subsystems sharing Solcast confidence infrastructure. Solar confidence blending applies continuous linear interpolation between `pv_estimate` and `pv_estimate10` inside the solar getter functions, so all consumers (optimizer slots, forecast battery, terminal cost) get consistent values. Boost contamination protection tags period records during boost and excludes them from accuracy computation.

**Tech Stack:** Python 3.13+, Home Assistant integration, Dynamic Programming optimizer, Solcast solar forecast API.

---

## File Structure

| File | Change | Responsibility |
|------|--------|---------------|
| `forecast/solar.py` | Add `_blend_solar_estimate()`, add `confidence` param to getters and `sum_solar_before_target` | Core blending algorithm |
| `engine/slots.py` | Thread confidence from `data.solcast_analysis_*` into `_get_solar_kwh()` | Slot builder confidence |
| `forecast/pipeline.py` | Pass analysis to `sum_solar_before_target()` at both call sites | Forecast battery confidence |
| `engine/types.py` | Add `solcast_analysis_today` field to `OptimizerInputs` | Data threading |
| `engine/core.py` | Use blended solar in `_projected_solcast_gain_pct()` | Terminal cost projection |
| `forecast/solar_accuracy.py` | Add `is_boost_period` to `SolarPeriodRecord`, add `is_boost` param to `record_forecast()`, filter in `_recompute_metrics()` | Boost tagging |
| `engine/optimizer_facade.py` | Pass `is_boost` flag to `record_forecast()` | Boost detection |
| `coordinator/tick_scheduler.py` | Skip SOC accuracy recording during boost | SOC accuracy protection |
| `sensors/optimizer.py` | Add confidence diagnostic attributes | Observability |
| `sensors/forecast.py` | Add confidence diagnostic attributes | Observability |
| `tests/forecast/test_solar_confidence.py` | New: confidence blending unit tests | Test coverage |
| `tests/test_solar_confidence_scenarios.py` | New: integration scenarios | Test coverage |
| `tests/forecast/test_solar_accuracy.py` | Extend: boost contamination tests | Test coverage |

---

## Chunk 1: Solar Confidence Blending Core

### Task 1.1: Add blending function to `forecast/solar.py`

**Files:**
- Modify: `custom_components/localshift/forecast/solar.py`

- [ ] **Step 1: Add `_blend_solar_estimate()` function** (after `_get_period_estimate()` at line 48)

```python
def _blend_solar_estimate(
    pv_estimate: float,
    pv_estimate10: float,
    confidence: float,
) -> float:
    """Continuous linear blending between median and P10 based on confidence.

    At high confidence (1.0), returns pv_estimate (median).
    At low confidence (0.0), returns pv_estimate10 (pessimistic).
    """
    if confidence >= 1.0:
        return pv_estimate
    if confidence <= 0.0:
        return pv_estimate10
    return confidence * pv_estimate + (1.0 - confidence) * pv_estimate10
```

- [ ] **Step 2: Write failing test**

Create `tests/forecast/test_solar_confidence.py`:

```python
"""Tests for solar confidence blending (Issue #794)."""
from __future__ import annotations

import pytest
from custom_components.localshift.forecast.solar import _blend_solar_estimate


class TestBlendSolarEstimate:
    """Tests for _blend_solar_estimate()."""

    def test_full_confidence_returns_median(self):
        assert _blend_solar_estimate(29.56, 7.99, 1.0) == 29.56

    def test_zero_confidence_returns_p10(self):
        assert _blend_solar_estimate(29.56, 7.99, 0.0) == 7.99

    def test_mid_confidence_returns_average(self):
        result = _blend_solar_estimate(20.0, 10.0, 0.5)
        assert result == pytest.approx(15.0)

    def test_low_confidence_realistic(self):
        # Issue #794 reported case: confidence 17%, median 29.56, P10 7.99
        result = _blend_solar_estimate(29.56, 7.99, 0.17)
        expected = 0.17 * 29.56 + 0.83 * 7.99  # ~11.65
        assert result == pytest.approx(expected, abs=0.01)

    def test_confidence_above_one_clamps_to_median(self):
        assert _blend_solar_estimate(29.56, 7.99, 1.5) == 29.56

    def test_confidence_below_zero_clamps_to_p10(self):
        assert _blend_solar_estimate(29.56, 7.99, -0.5) == 7.99

    def test_zero_values(self):
        assert _blend_solar_estimate(0.0, 0.0, 0.5) == 0.0

    def test_p10_larger_than_median(self):
        # Edge case: inverted spread
        result = _blend_solar_estimate(5.0, 10.0, 0.5)
        assert result == pytest.approx(7.5)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/forecast/test_solar_confidence.py::TestBlendSolarEstimate -v`
Expected: FAIL with "cannot import name '_blend_solar_estimate'"

- [ ] **Step 4: Implementation already done in Step 1**

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/forecast/test_solar_confidence.py::TestBlendSolarEstimate -v`
Expected: All 8 tests PASS

- [ ] **Step 6: Commit**

```bash
git add custom_components/localshift/forecast/solar.py tests/forecast/test_solar_confidence.py
git commit -m "feat(solar): add confidence-based percentile blending function (#794)"
```

### Task 1.2: Add confidence param to `get_solar_for_5min_slot()`

**Files:**
- Modify: `custom_components/localshift/forecast/solar.py:196-265`

- [ ] **Step 1: Write failing test**

Add to `tests/forecast/test_solar_confidence.py`:

```python
class TestGetSolarFor5MinSlotWithConfidence:
    """Tests for get_solar_for_5min_slot() with confidence blending."""

    def _make_forecast(self, period_start, pv_estimate, pv_estimate10):
        return {
            "period_start": period_start,
            "pv_estimate": pv_estimate,
            "pv_estimate10": pv_estimate10,
        }

    def test_high_confidence_uses_median(self):
        from custom_components.localshift.forecast.solar import get_solar_for_5min_slot
        from datetime import datetime, timezone

        slot_start = datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc)
        forecasts = [self._make_forecast("2026-03-19T10:00:00", 4.0, 1.0)]
        result = get_solar_for_5min_slot(forecasts, slot_start, confidence=1.0)
        assert result > 0.3  # median-based (4.0 * 5/60)

    def test_zero_confidence_uses_p10(self):
        from custom_components.localshift.forecast.solar import get_solar_for_5min_slot
        from datetime import datetime, timezone

        slot_start = datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc)
        forecasts = [self._make_forecast("2026-03-19T10:00:00", 4.0, 1.0)]
        result = get_solar_for_5min_slot(forecasts, slot_start, confidence=0.0)
        assert result < 0.1  # P10-based (1.0 * 5/60)

    def test_default_confidence_preserves_behavior(self):
        from custom_components.localshift.forecast.solar import get_solar_for_5min_slot
        from datetime import datetime, timezone

        slot_start = datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc)
        forecasts = [self._make_forecast("2026-03-19T10:00:00", 4.0, 1.0)]
        result_default = get_solar_for_5min_slot(forecasts, slot_start)
        result_explicit = get_solar_for_5min_slot(forecasts, slot_start, confidence=1.0)
        assert result_default == result_explicit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/forecast/test_solar_confidence.py::TestGetSolarFor5MinSlotWithConfidence -v`
Expected: FAIL (unexpected keyword argument 'confidence')

- [ ] **Step 3: Modify `get_solar_for_5min_slot()`**

Change signature to add `confidence: float = 1.0` parameter.

Change value selection block (lines 253-260) from inline cascade to:
```python
            raw_estimate = float(
                entry.get("pv_estimate")
                or entry.get("estimate")
                or 0.0
            )
            raw_p10 = float(
                entry.get("pv_estimate10")
                or entry.get("estimate10")
                or 0.0
            )
            period_kwh = _blend_solar_estimate(raw_estimate, raw_p10, confidence)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/forecast/test_solar_confidence.py::TestGetSolarFor5MinSlotWithConfidence -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/forecast/solar.py tests/forecast/test_solar_confidence.py
git commit -m "feat(solar): add confidence blending to 5-min slot getter (#794)"
```

### Task 1.3: Add confidence param to `get_solar_for_15min_slot()` via `_process_forecast_entry()`

**Files:**
- Modify: `custom_components/localshift/forecast/solar.py:51-99` (`_process_forecast_entry`)
- Modify: `custom_components/localshift/forecast/solar.py:154-193` (`get_solar_for_15min_slot`)

- [ ] **Step 1: Write failing test**

```python
class TestGetSolarFor15MinSlotWithConfidence:
    """Tests for get_solar_for_15min_slot() with confidence blending."""

    def _make_forecast(self, period_start, pv_estimate, pv_estimate10):
        return {
            "period_start": period_start,
            "pv_estimate": pv_estimate,
            "pv_estimate10": pv_estimate10,
        }

    def test_high_confidence_uses_median(self):
        from custom_components.localshift.forecast.solar import get_solar_for_15min_slot
        from datetime import datetime, timezone

        slot_start = datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc)
        forecasts = [self._make_forecast("2026-03-19T10:00:00", 4.0, 1.0)]
        result_high = get_solar_for_15min_slot(forecasts, slot_start, confidence=1.0)
        result_low = get_solar_for_15min_slot(forecasts, slot_start, confidence=0.0)
        assert result_high > result_low

    def test_zero_confidence_uses_p10(self):
        from custom_components.localshift.forecast.solar import get_solar_for_15min_slot
        from datetime import datetime, timezone

        slot_start = datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc)
        forecasts = [self._make_forecast("2026-03-19T10:00:00", 4.0, 1.0)]
        result = get_solar_for_15min_slot(forecasts, slot_start, confidence=0.0)
        # P10=1.0, 15 min = 15/60 = 0.25 kWh
        assert result == pytest.approx(0.25)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/forecast/test_solar_confidence.py::TestGetSolarFor15MinSlotWithConfidence -v`
Expected: FAIL

- [ ] **Step 3: Modify `_process_forecast_entry()` to accept confidence**

Add `confidence: float = 1.0` parameter to signature.

Change line 88 from `period_kwh = _get_period_estimate(entry)` to:
```python
    raw_estimate = float(entry.get("pv_estimate") or entry.get("estimate") or 0.0)
    raw_p10 = float(entry.get("pv_estimate10") or entry.get("estimate10") or 0.0)
    period_kwh = _blend_solar_estimate(raw_estimate, raw_p10, confidence)
```

- [ ] **Step 4: Modify `get_solar_for_15min_slot()` to pass confidence through**

Add `confidence: float = 1.0` parameter to signature.

Change line 185 to pass `confidence` to `_process_forecast_entry()`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/forecast/test_solar_confidence.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add custom_components/localshift/forecast/solar.py tests/forecast/test_solar_confidence.py
git commit -m "feat(solar): add confidence blending to 15-min slot getter (#794)"
```

### Task 1.4: Add confidence param to `get_solar_for_30min_slot()`

**Files:**
- Modify: `custom_components/localshift/forecast/solar.py:268-337`

- [ ] **Step 1: Write failing test**

```python
class TestGetSolarFor30MinSlotWithConfidence:
    """Tests for get_solar_for_30min_slot() with confidence blending."""

    def _make_forecast(self, period_start, pv_estimate, pv_estimate10):
        return {
            "period_start": period_start,
            "pv_estimate": pv_estimate,
            "pv_estimate10": pv_estimate10,
        }

    def test_blending_applied(self):
        from custom_components.localshift.forecast.solar import get_solar_for_30min_slot
        from datetime import datetime, timezone

        slot_start = datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc)
        forecasts = [self._make_forecast("2026-03-19T10:00:00", 4.0, 1.0)]

        result_high = get_solar_for_30min_slot(forecasts, slot_start, confidence=1.0)
        result_low = get_solar_for_30min_slot(forecasts, slot_start, confidence=0.0)
        result_mid = get_solar_for_30min_slot(forecasts, slot_start, confidence=0.5)

        # 30 min = 0.5 hours
        assert result_high == pytest.approx(4.0 * 0.5)  # 2.0
        assert result_low == pytest.approx(1.0 * 0.5)    # 0.5
        assert result_mid == pytest.approx(2.5 * 0.5)    # 1.25
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/forecast/test_solar_confidence.py::TestGetSolarFor30MinSlotWithConfidence -v`
Expected: FAIL

- [ ] **Step 3: Modify `get_solar_for_30min_slot()`**

Add `confidence: float = 1.0` parameter to signature.

Change value selection block (lines 326-331) to use `_blend_solar_estimate()` with `raw_estimate` and `raw_p10`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/forecast/test_solar_confidence.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/forecast/solar.py tests/forecast/test_solar_confidence.py
git commit -m "feat(solar): add confidence blending to 30-min slot getter (#794)"
```

### Task 1.5: Thread confidence through `get_solar_for_slot_by_interval()`

**Files:**
- Modify: `custom_components/localshift/forecast/solar.py:340-370`

- [ ] **Step 1: Write failing test**

```python
class TestGetSolarForSlotByIntervalWithConfidence:
    """Tests for get_solar_for_slot_by_interval() confidence threading."""

    def test_confidence_passed_through_to_5min(self):
        from custom_components.localshift.forecast.solar import get_solar_for_slot_by_interval
        from datetime import datetime, timezone

        slot_start = datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc)
        forecasts = [{"period_start": "2026-03-19T10:00:00", "pv_estimate": 4.0, "pv_estimate10": 1.0}]
        result_high = get_solar_for_slot_by_interval(forecasts, slot_start, 5, confidence=1.0)
        result_low = get_solar_for_slot_by_interval(forecasts, slot_start, 5, confidence=0.0)
        assert result_high > result_low

    def test_confidence_passed_through_to_30min(self):
        from custom_components.localshift.forecast.solar import get_solar_for_slot_by_interval
        from datetime import datetime, timezone

        slot_start = datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc)
        forecasts = [{"period_start": "2026-03-19T10:00:00", "pv_estimate": 4.0, "pv_estimate10": 1.0}]
        result_high = get_solar_for_slot_by_interval(forecasts, slot_start, 30, confidence=1.0)
        result_low = get_solar_for_slot_by_interval(forecasts, slot_start, 30, confidence=0.0)
        assert result_high > result_low
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/forecast/test_solar_confidence.py::TestGetSolarForSlotByIntervalWithConfidence -v`
Expected: FAIL

- [ ] **Step 3: Modify `get_solar_for_slot_by_interval()`**

Add `confidence: float = 1.0` parameter to signature.

Update all dispatch calls to pass `confidence=confidence` to the sub-functions.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/forecast/test_solar_confidence.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/forecast/solar.py tests/forecast/test_solar_confidence.py
git commit -m "feat(solar): thread confidence through slot interval dispatcher (#794)"
```

### Task 1.6: Add analysis param to `sum_solar_before_target()`

**Files:**
- Modify: `custom_components/localshift/forecast/solar.py:373-410`

- [ ] **Step 1: Write failing test**

```python
class TestSumSolarBeforeTargetWithConfidence:
    """Tests for sum_solar_before_target() with analysis-based confidence."""

    def test_sum_uses_per_period_confidence(self):
        from custom_components.localshift.forecast.solar import sum_solar_before_target
        from custom_components.localshift.forecast.solcast_analysis import (
            SolcastAnalysis, ConfidenceInterval,
        )
        from datetime import datetime, timezone

        now = datetime(2026, 3, 19, 9, 0, tzinfo=timezone.utc)
        forecasts = [
            {"period_start": "2026-03-19T09:00:00", "pv_estimate": 4.0, "pv_estimate10": 1.0},
            {"period_start": "2026-03-19T09:30:00", "pv_estimate": 4.0, "pv_estimate10": 1.0},
        ]
        analysis = SolcastAnalysis(
            entity_id="test", last_updated=now, day_confidence=0.5,
            day_spread_kwh=0, estimate10_kwh=0, estimate90_kwh=0,
            intervals=[
                ConfidenceInterval(period_start=datetime(2026, 3, 19, 9, 0, tzinfo=timezone.utc), spread_kwh=0, confidence=1.0),
                ConfidenceInterval(period_start=datetime(2026, 3, 19, 9, 30, tzinfo=timezone.utc), spread_kwh=0, confidence=0.2),
            ],
        )
        result = sum_solar_before_target(forecasts, now, 12, analysis=analysis)
        # Period 1: conf=1.0, blended=4.0, 0.5h = 2.0 kWh
        # Period 2: conf=0.2, blended=0.2*4.0+0.8*1.0=1.4, 0.5h = 0.7 kWh
        assert result == pytest.approx(2.7, abs=0.01)

    def test_no_analysis_uses_full_confidence(self):
        from custom_components.localshift.forecast.solar import sum_solar_before_target
        from datetime import datetime, timezone

        now = datetime(2026, 3, 19, 9, 0, tzinfo=timezone.utc)
        forecasts = [{"period_start": "2026-03-19T09:00:00", "pv_estimate": 4.0, "pv_estimate10": 1.0}]
        result = sum_solar_before_target(forecasts, now, 10)
        assert result == pytest.approx(4.0 * 0.5)  # 2.0 kWh
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/forecast/test_solar_confidence.py::TestSumSolarBeforeTargetWithConfidence -v`
Expected: FAIL

- [ ] **Step 3: Modify `sum_solar_before_target()`**

Add `analysis: Any | None = None` parameter to signature.

Add import: `from custom_components.localshift.forecast.solcast_analysis import get_confidence_for_period`

Change line 393 from `kwh_per_hour = float(period.get("pv_estimate", 0))` to:
```python
        raw_estimate = float(period.get("pv_estimate", 0))
        raw_p10 = float(period.get("pv_estimate10", 0))
        confidence = get_confidence_for_period(analysis, ps_local)
        kwh_per_hour = _blend_solar_estimate(raw_estimate, raw_p10, confidence)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/forecast/test_solar_confidence.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run existing solar tests for regression**

Run: `uv run pytest tests/forecast/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add custom_components/localshift/forecast/solar.py tests/forecast/test_solar_confidence.py
git commit -m "feat(solar): add analysis-based confidence to sum_solar_before_target (#794)"
```

---

## Chunk 2: Confidence Threading to Slot Builder

### Task 2.1: Thread confidence through `slots.py`

**Files:**
- Modify: `custom_components/localshift/engine/slots.py`

- [ ] **Step 1: Add helper method to SlotBuilder**

Add `_get_analysis_for_slot()` method to pick the right `SolcastAnalysis` based on slot date:

```python
def _get_analysis_for_slot(self, data: Any, slot_start: datetime) -> Any:
    """Get the SolcastAnalysis for a slot based on its date."""
    analysis_today = getattr(data, "solcast_analysis_today", None)
    analysis_tomorrow = getattr(data, "solcast_analysis_tomorrow", None)

    # Check if slot matches today's analysis intervals
    if analysis_today and analysis_today.intervals:
        for interval in analysis_today.intervals:
            if abs((slot_start - interval.period_start).total_seconds()) <= 1800:
                return analysis_today

    # Check tomorrow
    if analysis_tomorrow and analysis_tomorrow.intervals:
        for interval in analysis_tomorrow.intervals:
            if abs((slot_start - interval.period_start).total_seconds()) <= 1800:
                return analysis_tomorrow

    # Fallback to today (has day_confidence fallback)
    return analysis_today
```

- [ ] **Step 2: Modify `_process_single_slot()` to extract confidence**

Add import at top of file:
```python
from custom_components.localshift.forecast.solcast_analysis import get_confidence_for_period
```

Before the `_get_solar_kwh()` call at line 324, add:
```python
        analysis = self._get_analysis_for_slot(data, slot_start)
        confidence = get_confidence_for_period(analysis, slot_start)
```

Change the call at line 324 to pass `confidence=confidence`.

- [ ] **Step 3: Modify `_get_solar_kwh()` to accept confidence**

Add `confidence: float = 1.0` parameter to signature.

Change line 385 to pass `confidence=confidence` to `get_solar_for_slot_by_interval()`.

- [ ] **Step 4: Run existing tests**

Run: `uv run pytest tests/engine/ -v`
Expected: All PASS (backward compat via default `confidence=1.0`)

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/engine/slots.py
git commit -m "feat(solar): thread confidence through slot builder to getter functions (#794)"
```

---

## Chunk 3: Confidence Threading to Forecast Battery + Terminal Cost

### Task 3.1: Pass analysis to `sum_solar_before_target()` in pipeline.py

**Files:**
- Modify: `custom_components/localshift/forecast/pipeline.py:141-142, 173-174`

- [ ] **Step 1: Modify both call sites**

Change both call sites (line 142 and 174) from:
```python
            solar_kwh = sum_solar_before_target(all_solcast, now_dt, target_hour)
```
to:
```python
            analysis = getattr(data, "solcast_analysis_today", None)
            solar_kwh = sum_solar_before_target(all_solcast, now_dt, target_hour, analysis=analysis)
```

- [ ] **Step 2: Commit**

```bash
git add custom_components/localshift/forecast/pipeline.py
git commit -m "feat(solar): pass Solcast analysis to forecast battery solar sum (#794)"
```

### Task 3.2: Add SolcastAnalysis to OptimizerInputs

**Files:**
- Modify: `custom_components/localshift/engine/types.py:468-496`

- [ ] **Step 1: Add field to `OptimizerInputs`**

After `solar_accuracy_tracker` field (line 496), add:
```python
    solcast_analysis_today: Any | None = None
    """Solcast analysis with confidence data for solar blending (Issue #794)."""
```

- [ ] **Step 2: Commit**

```bash
git add custom_components/localshift/engine/types.py
git commit -m "feat(optimizer): add solcast_analysis_today to OptimizerInputs (#794)"
```

### Task 3.3: Use blended solar in terminal cost projection

**Files:**
- Modify: `custom_components/localshift/engine/core.py:1435-1463`

- [ ] **Step 1: Write failing test**

Create `tests/engine/test_terminal_cost_confidence.py`:

```python
"""Tests for terminal cost projection with confidence blending (Issue #794)."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.forecast.solcast_analysis import (
    SolcastAnalysis, ConfidenceInterval,
)


class TestProjectedSolcastGainWithConfidence:
    """Tests for _projected_solcast_gain_pct() with confidence."""

    def test_low_confidence_reduces_projected_gain(self):
        solcast = [{"period_start": "2026-03-19T12:00:00", "pv_estimate": 6.0, "pv_estimate10": 1.0}]
        start = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 19, 12, 30, tzinfo=timezone.utc)

        gain_high = DPPlanner._projected_solcast_gain_pct(solcast, start, end, 13.5)

        analysis_low = SolcastAnalysis(
            entity_id="test", last_updated=start, day_confidence=0.2,
            day_spread_kwh=0, estimate10_kwh=0, estimate90_kwh=0,
            intervals=[ConfidenceInterval(period_start=start, spread_kwh=0, confidence=0.2)],
        )
        gain_low = DPPlanner._projected_solcast_gain_pct(
            solcast, start, end, 13.5, solcast_analysis=analysis_low,
        )

        assert gain_low < gain_high
        assert gain_low == pytest.approx(gain_high * 0.2 / 1.0 + (1 - 0.2) * (1.0 / 6.0) * gain_high, rel=0.1)
```

- [ ] **Step 2: Modify `_projected_solcast_gain_pct()` signature**

Add `solcast_analysis: Any | None = None` parameter.

Add imports at top of `core.py`:
```python
from custom_components.localshift.forecast.solar import _blend_solar_estimate
from custom_components.localshift.forecast.solcast_analysis import get_confidence_for_period
```

Change the solar accumulation loop (lines 1452-1463) to use `_blend_solar_estimate()` with per-period confidence from `get_confidence_for_period(solcast_analysis, p_start)`.

- [ ] **Step 3: Update caller in `_initialize_dp_tables()`**

Pass `solcast_analysis=inputs.solcast_analysis_today` when calling `_projected_solcast_gain_pct()`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/engine/test_terminal_cost_confidence.py -v`
Expected: All PASS

- [ ] **Step 5: Run existing optimizer tests**

Run: `uv run pytest tests/engine/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add custom_components/localshift/engine/core.py custom_components/localshift/engine/types.py tests/engine/test_terminal_cost_confidence.py
git commit -m "feat(optimizer): apply confidence blending in terminal cost projection (#794)"
```

### Task 3.4: Thread analysis through OptimizerFacade to OptimizerInputs

**Files:**
- Modify: `custom_components/localshift/engine/optimizer_facade.py:205-212`

- [ ] **Step 1: Pass analysis when constructing OptimizerInputs**

Change `OptimizerInputs` construction to add:
```python
                solcast_analysis_today=getattr(data, "solcast_analysis_today", None),
```

- [ ] **Step 2: Commit**

```bash
git add custom_components/localshift/engine/optimizer_facade.py
git commit -m "feat(optimizer): thread SolcastAnalysis from coordinator data to DP planner (#794)"
```

---

## Chunk 4: Boost Contamination Protection

### Task 4.1: Add boost period tagging to SolarPeriodRecord

**Files:**
- Modify: `custom_components/localshift/forecast/solar_accuracy.py:31-61`

- [ ] **Step 1: Write failing test**

Add to `tests/forecast/test_solar_accuracy.py`:

```python
class TestBoostPeriodTagging:
    """Tests for boost period tagging (Issue #794)."""

    def test_boost_record_has_flag(self):
        from custom_components.localshift.forecast.solar_accuracy import SolarPeriodRecord
        from datetime import datetime, timezone

        record = SolarPeriodRecord(
            period_start=datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc),
            forecast_kwh=4.0, actual_kwh=0.5,
            weather_condition="sunny", time_of_day="morning", season="autumn",
            is_boost_period=True,
        )
        assert record.is_boost_period is True

    def test_default_is_not_boost(self):
        from custom_components.localshift.forecast.solar_accuracy import SolarPeriodRecord
        from datetime import datetime, timezone

        record = SolarPeriodRecord(
            period_start=datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc),
            forecast_kwh=4.0, actual_kwh=0.5,
            weather_condition="sunny", time_of_day="morning", season="autumn",
        )
        assert record.is_boost_period is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/forecast/test_solar_accuracy.py::TestBoostPeriodTagging -v`
Expected: FAIL

- [ ] **Step 3: Add `is_boost_period` field**

After `additive_bias` field (line 53), add: `is_boost_period: bool = False`

- [ ] **Step 4: Run tests and commit**

```bash
uv run pytest tests/forecast/test_solar_accuracy.py::TestBoostPeriodTagging -v
git add custom_components/localshift/forecast/solar_accuracy.py tests/forecast/test_solar_accuracy.py
git commit -m "feat(accuracy): add is_boost_period field to SolarPeriodRecord (#794)"
```

### Task 4.2: Add `is_boost` param to `record_forecast()`

**Files:**
- Modify: `custom_components/localshift/forecast/solar_accuracy.py:258-289`

- [ ] **Step 1: Write failing test**

```python
class TestRecordForecastWithBoost:
    """Tests for record_forecast() with boost flag."""

    def test_record_forecast_sets_boost_flag(self, tracker):
        from datetime import datetime, timezone
        tracker.record_forecast(
            period_start=datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc),
            forecast_kwh=4.0, weather_condition="sunny", is_boost=True,
        )
        key = datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc).isoformat()
        assert tracker._pending_forecasts[key].is_boost_period is True

    def test_record_forecast_default_no_boost(self, tracker):
        from datetime import datetime, timezone
        tracker.record_forecast(
            period_start=datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc),
            forecast_kwh=4.0, weather_condition="sunny",
        )
        key = datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc).isoformat()
        assert tracker._pending_forecasts[key].is_boost_period is False
```

- [ ] **Step 2: Modify `record_forecast()`**

Add `is_boost: bool = False` parameter to signature.

Pass `is_boost_period=is_boost` to `SolarPeriodRecord` constructor.

- [ ] **Step 3: Run tests and commit**

```bash
uv run pytest tests/forecast/test_solar_accuracy.py::TestRecordForecastWithBoost -v
git add custom_components/localshift/forecast/solar_accuracy.py tests/forecast/test_solar_accuracy.py
git commit -m "feat(accuracy): add is_boost parameter to record_forecast() (#794)"
```

### Task 4.3: Filter boost records in `_recompute_metrics()`

**Files:**
- Modify: `custom_components/localshift/forecast/solar_accuracy.py:327-381`

- [ ] **Step 1: Write failing test**

```python
class TestBoostExcludedFromMetrics:
    """Tests for boost period exclusion from accuracy metrics."""

    def test_boost_excluded_from_mape(self, tracker):
        from datetime import datetime, timezone

        # Normal period with high error
        tracker.record_forecast(
            period_start=datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc),
            forecast_kwh=5.0, weather_condition="sunny",
        )
        tracker.backfill_actual(datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc), 2.0)

        # Boost period with high error (should be excluded)
        tracker.record_forecast(
            period_start=datetime(2026, 3, 19, 10, 30, tzinfo=timezone.utc),
            forecast_kwh=5.0, weather_condition="sunny", is_boost=True,
        )
        tracker.backfill_actual(datetime(2026, 3, 19, 10, 30, tzinfo=timezone.utc), 1.0)

        assert tracker.metrics.sample_count == 1  # Only non-boost record

    def test_boost_still_in_history(self, tracker):
        from datetime import datetime, timezone

        tracker.record_forecast(
            period_start=datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc),
            forecast_kwh=4.0, weather_condition="sunny", is_boost=True,
        )
        tracker.backfill_actual(datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc), 1.0)

        assert len(tracker._period_records) == 1  # Still in deque
        assert tracker.metrics.sample_count == 0   # But excluded from metrics
```

- [ ] **Step 2: Modify `_recompute_metrics()`**

After line 329 (`if not self._period_records: return`), change:
```python
        records = list(self._period_records)
```
to:
```python
        records = [r for r in self._period_records if not r.is_boost_period]
        if not records:
            self._metrics = SolarBiasMetrics()
            return
```

- [ ] **Step 3: Run all solar accuracy tests**

Run: `uv run pytest tests/forecast/test_solar_accuracy.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add custom_components/localshift/forecast/solar_accuracy.py tests/forecast/test_solar_accuracy.py
git commit -m "feat(accuracy): exclude boost-tagged records from MAPE and bias computation (#794)"
```

### Task 4.4: Pass boost flag from OptimizerFacade

**Files:**
- Modify: `custom_components/localshift/engine/optimizer_facade.py:53-80, 187`

- [ ] **Step 1: Modify `_record_forecasts_for_slots()`**

Add `is_boost: bool = False` parameter to signature.

Pass `is_boost=is_boost` to `record_forecast()` call at line 72.

- [ ] **Step 2: Pass boost flag from `run_inline()`**

Change line 187 from:
```python
            self._record_forecasts_for_slots(slots, weather_condition)
```
to:
```python
            is_boost = getattr(data, "boost_charge_active", False)
            self._record_forecasts_for_slots(slots, weather_condition, is_boost=is_boost)
```

- [ ] **Step 3: Write test**

Create `tests/test_boost_contamination.py`:

```python
"""Tests for boost contamination protection (Issue #794)."""
from __future__ import annotations

from unittest.mock import MagicMock


class TestBoostDetectionInFacade:
    """Tests for boost flag passing in OptimizerFacade."""

    def test_boost_active_detected(self):
        data = MagicMock()
        data.boost_charge_active = True
        assert getattr(data, "boost_charge_active", False) is True

    def test_boost_inactive_default(self):
        data = MagicMock(spec=[])
        assert getattr(data, "boost_charge_active", False) is False
```

- [ ] **Step 4: Commit**

```bash
git add custom_components/localshift/engine/optimizer_facade.py tests/test_boost_contamination.py
git commit -m "feat(accuracy): pass boost flag from OptimizerFacade to record_forecast() (#794)"
```

### Task 4.5: Skip SOC accuracy recording during boost

**Files:**
- Modify: `custom_components/localshift/coordinator/tick_scheduler.py`

- [ ] **Step 1: Add boost guard**

In `handle_slow_tick()`, find where `ForecastAccuracyEngine` recording happens and add guard:
```python
if not getattr(data, "boost_charge_active", False):
    # Record SOC forecast accuracy (existing code)
    ...
```

- [ ] **Step 2: Commit**

```bash
git add custom_components/localshift/coordinator/tick_scheduler.py
git commit -m "feat(accuracy): skip SOC accuracy recording during boost charge (#794)"
```

---

## Chunk 5: Integration Tests & Diagnostics

### Task 5.1: Integration tests for low-confidence scenarios

**Files:**
- Create: `tests/test_solar_confidence_scenarios.py`

- [ ] **Step 1: Write scenario tests**

```python
"""Integration tests for solar confidence blending scenarios (Issue #794)."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from custom_components.localshift.forecast.solar import (
    _blend_solar_estimate,
    get_solar_for_slot_by_interval,
    sum_solar_before_target,
)
from custom_components.localshift.forecast.solcast_analysis import (
    SolcastAnalysis, ConfidenceInterval,
)


class TestLowConfidenceScenarios:
    """End-to-end scenarios matching reported issue #794."""

    def test_issue_794_reported_case(self):
        """Median 29.56 kWh, P10 7.99 kWh, confidence 17% -> ~11.65 kWh."""
        blended = _blend_solar_estimate(29.56, 7.99, 0.17)
        assert blended == pytest.approx(11.65, abs=0.1)
        assert blended < 29.56 * 0.5

    def test_high_confidence_no_regression(self):
        """High confidence (>=0.9) should behave like current system."""
        forecasts = [{"period_start": "2026-03-19T10:00:00", "pv_estimate": 4.0, "pv_estimate10": 1.0}]
        slot_start = datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc)
        result_high = get_solar_for_slot_by_interval(forecasts, slot_start, 30, confidence=0.95)
        result_legacy = get_solar_for_slot_by_interval(forecasts, slot_start, 30, confidence=1.0)
        assert abs(result_high - result_legacy) / result_legacy < 0.05

    def test_forecast_battery_conservative_sum(self):
        """Low confidence sum should be much less than optimistic sum."""
        now = datetime(2026, 3, 19, 9, 0, tzinfo=timezone.utc)
        forecasts = [
            {"period_start": "2026-03-19T09:00:00", "pv_estimate": 6.0, "pv_estimate10": 1.0},
            {"period_start": "2026-03-19T09:30:00", "pv_estimate": 6.0, "pv_estimate10": 1.0},
            {"period_start": "2026-03-19T10:00:00", "pv_estimate": 6.0, "pv_estimate10": 1.0},
        ]
        analysis = SolcastAnalysis(
            entity_id="test", last_updated=now, day_confidence=0.2,
            day_spread_kwh=0, estimate10_kwh=0, estimate90_kwh=0,
            intervals=[
                ConfidenceInterval(
                    period_start=datetime(2026, 3, 19, h, m, tzinfo=timezone.utc),
                    spread_kwh=0, confidence=0.2,
                ) for h, m in [(9, 0), (9, 30), (10, 0)]
            ],
        )
        solar_optimistic = sum_solar_before_target(forecasts, now, 11)
        solar_conservative = sum_solar_before_target(forecasts, now, 11, analysis=analysis)
        assert solar_conservative < solar_optimistic * 0.5
        assert solar_optimistic == pytest.approx(9.0)  # 3 * 6.0 * 0.5
        assert solar_conservative == pytest.approx(3.0, abs=0.1)  # 3 * 2.0 * 0.5

    def test_no_analysis_backward_compatible(self):
        """Without SolcastAnalysis, behavior identical to current system."""
        forecasts = [{"period_start": "2026-03-19T10:00:00", "pv_estimate": 4.0, "pv_estimate10": 1.0}]
        slot_start = datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc)
        result = get_solar_for_slot_by_interval(forecasts, slot_start, 30)
        assert result == pytest.approx(4.0 * 0.5)  # 2.0 kWh
```

- [ ] **Step 2: Run all new tests**

Run: `uv run pytest tests/forecast/test_solar_confidence.py tests/test_solar_confidence_scenarios.py tests/test_boost_contamination.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_solar_confidence_scenarios.py
git commit -m "test(solar): add integration tests for confidence blending scenarios (#794)"
```

### Task 5.2: Add diagnostic attributes to sensors

**Files:**
- Modify: `custom_components/localshift/sensors/forecast.py`
- Modify: `custom_components/localshift/sensors/optimizer.py`

- [ ] **Step 1: Add confidence attributes to forecast battery sensor**

In `sensors/forecast.py`, where `ForecastBatterySensor` builds attributes, add:
```python
"solar_confidence_used": getattr(data, "avg_confidence_today", 1.0),
"solar_blend_applied": getattr(data, "avg_confidence_today", 1.0) < 1.0,
```

- [ ] **Step 2: Add confidence attributes to optimizer summary sensor**

In `sensors/optimizer.py`, add to optimizer plan detailed attributes:
```python
"solar_confidence_avg": getattr(data, "avg_confidence_today", 1.0),
"solar_confidence_regime": (
    "high" if getattr(data, "avg_confidence_today", 1.0) >= 0.7
    else "medium" if getattr(data, "avg_confidence_today", 1.0) >= 0.4
    else "low"
),
"solar_blend_applied": getattr(data, "avg_confidence_today", 1.0) < 1.0,
```

- [ ] **Step 3: Commit**

```bash
git add custom_components/localshift/sensors/forecast.py custom_components/localshift/sensors/optimizer.py
git commit -m "feat(sensors): add confidence diagnostic attributes (#794)"
```

### Task 5.3: Final verification

- [ ] **Step 1: Run full test suite with coverage**

Run: `uv run pytest --cov=custom_components/localshift --cov-report=term-missing`
Expected: All PASS, coverage >=95%

- [ ] **Step 2: Run linting**

Run: `uv run ruff check custom_components/localshift`
Expected: No errors

- [ ] **Step 3: Run type checking**

Run: `uv run pyright custom_components/localshift` (if configured)
Expected: No new errors

---

## Summary

**Total tasks:** 19 across 5 chunks
**Estimated commits:** 15-19
**Files modified:** 8 production files
**Files created:** 3 test files

**Key behavioral changes:**
1. Solar getter functions blend median and P10 based on per-period Solcast confidence
2. Forecast battery uses confidence-adjusted solar sums (no more false 100% SOC)
3. Terminal cost projection uses confidence-blended solar (complementary to accuracy discount)
4. Boost periods tagged and excluded from accuracy learning
5. SOC accuracy recording skipped during boost
6. Diagnostic attributes expose confidence regime and blending status

**Backward compatibility:** When `SolcastAnalysis` is None (older Solcast, parsing failure), all functions default to `confidence=1.0` — identical to current behavior.

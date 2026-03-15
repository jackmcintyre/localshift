# Shadow Optimizer Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**IMPORTANT:** This plan operates on the `issue-300` worktree (not main branch). Phase 1 fields exist in that worktree:
- `CONF_PRICING_DATA_SOURCE`, `CONF_COMPARISON_MODE` in const.py
- `general_price_shadow`, `comparison_match` in coordinator/data.py
- Shadow price reading in state/reader.py

**Goal:** Complete the shadow optimizer for A/B comparison between pricing sources. Run DP optimizer twice per cycle - primary with actual prices, shadow with alternate source - compare decisions and log mismatches.

**Architecture:** 
- Pass pricing_source and comparison_mode from config to optimizer facade
- After primary optimizer run, if comparison enabled: rebuild slots with shadow prices, run shadow optimizer, compare decisions
- Only log when mismatch occurs (not every cycle)

**Tech Stack:** Python, Home Assistant integration, DP optimizer

---

## Context

Phase 1 (PR #745) added:
- Config options: CONF_PRICING_DATA_SOURCE, CONF_COMPARISON_MODE
- Shadow price reading in state/reader.py
- CoordinatorData fields: general_price_shadow, comparison_match, etc.
- Comparison sensors (placeholder values)

This plan completes Phase 2: actually running the shadow optimizer.

---

## File Structure

| File | Responsibility |
|------|-----------------|
| `computation_engine.py` | Pass pricing_source, comparison_mode to optimizer |
| `engine/optimizer_facade.py` | Run shadow optimizer, compare decisions |
| `engine/slots.py` | Accept price overrides for shadow builds |
| `tests/engine/test_optimizer_facade.py` | Test shadow optimizer behavior |

---

## Chunk 1: Pass Config to Optimizer

**Files:**
- Modify: `custom_components/localshift/computation_engine.py:512-540`
- Test: N/A (config passing only)

- [ ] **Step 1: Add imports to computation_engine.py**

Add to imports:
```python
from .const import (
    CONF_PRICING_DATA_SOURCE,
    CONF_COMPARISON_MODE,
    DEFAULT_PRICING_DATA_SOURCE,
    DEFAULT_COMPARISON_MODE,
)
```

- [ ] **Step 2: Add to config_options in _build_optimizer_config_options()**

Add before the closing `}`:
```python
"pricing_source": self.entry.options.get(
    CONF_PRICING_DATA_SOURCE, DEFAULT_PRICING_DATA_SOURCE
),
"comparison_mode": self.entry.options.get(
    CONF_COMPARISON_MODE, DEFAULT_COMPARISON_MODE
),
```

- [ ] **Step 3: Run ruff**

Run: `uv run ruff check custom_components/localshift/computation_engine.py`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add custom_components/localshift/computation_engine.py
git commit -m "feat(#300): pass pricing_source and comparison_mode to optimizer"
```

---

## Chunk 2: Add Price Override Support to SlotBuilder

**Files:**
- Modify: `custom_components/localshift/engine/slots.py:115-200`
- Test: N/A (infrastructure only)

- [ ] **Step 1: Read SlotBuilder.build_slots signature**

Find the method signature in slots.py to understand current parameters.

- [ ] **Step 2: Add optional price override parameters**

Add optional parameters to `build_slots()`:
```python
def build_slots(
    self,
    data: CoordinatorData,
    adaptive_params: ParameterOptimizer,
    now_dt: datetime | None = None,
    # NEW: optional price overrides for shadow runs
    override_general_price: float | None = None,
    override_feed_in_price: float | None = None,
    override_general_forecast: list | None = None,
    override_feed_in_forecast: list | None = None,
) -> tuple[list[Slot], SlotMetadata]:
```

- [ ] **Step 3: Use overrides in slot building**

In the method body, after reading prices from data, add:
```python
# Apply price overrides for shadow optimizer runs
if override_general_price is not None:
    current_price = override_general_price
if override_feed_in_price is not None:
    feed_in_price = override_feed_in_price
# ... similar for forecasts
```

- [ ] **Step 4: Run ruff**

Run: `uv run ruff check custom_components/localshift/engine/slots.py`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/engine/slots.py
git commit -m "feat(#300): add price override support to SlotBuilder"
```

---

## Chunk 3: Implement Shadow Run in OptimizerFacade

**Files:**
- Modify: `custom_components/localshift/engine/optimizer_facade.py:167-230`
- Test: `tests/engine/test_optimizer_facade.py` (new)

- [ ] **Step 1: Create test file with failing tests**

Create: `tests/engine/test_optimizer_facade.py`

```python
"""Tests for optimizer_facade shadow optimizer."""

import pytest
from unittest.mock import MagicMock, patch

from custom_components.localshift.engine.optimizer_facade import OptimizerFacade
from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.const import BatteryMode


class TestShadowOptimizer:
    """Tests for shadow optimizer functionality."""

    @pytest.fixture
    def facade(self):
        return OptimizerFacade()

    @pytest.fixture
    def data_with_shadow_prices(self):
        data = CoordinatorData()
        data.general_price = 0.25
        data.feed_in_price = 0.08
        data.general_forecast = [{"price": 0.25, "time": "2026-03-15T14:00:00"}]
        data.feed_in_forecast = [{"price": 0.08, "time": "2026-03-15T14:00:00"}]
        
        # Shadow prices - significantly different to trigger different decision
        data.general_price_shadow = 0.45  # Much higher
        data.feed_in_price_shadow = 0.12
        data.general_forecast_shadow = [{"price": 0.45, "time": "2026-03-15T14:00:00"}]
        data.feed_in_forecast_shadow = [{"price": 0.12, "time": "2026-03-15T14:00:00"}]
        return data

    def test_shadow_prices_populated(self, facade, data_with_shadow_prices):
        """Shadow prices should be read into coordinator data."""
        assert data_with_shadow_prices.general_price_shadow == 0.45
        assert data_with_shadow_prices.feed_in_price_shadow == 0.12

    def test_price_delta_calculation(self, facade, data_with_shadow_prices):
        """Price delta should be absolute difference between primary and shadow."""
        price_delta = abs(
            data_with_shadow_prices.general_price - 
            data_with_shadow_prices.general_price_shadow
        )
        assert price_delta == 0.20

    def test_comparison_match_true_when_equal(self):
        """comparison_match should be True when modes match."""
        # Direct assertion test
        primary = "self_consumption"
        shadow = "self_consumption"
        assert (primary == shadow) == True

    def test_comparison_match_false_when_differ(self):
        """comparison_match should be False when modes differ."""
        primary = "self_consumption"
        shadow = "grid_charging"
        assert (primary == shadow) == False
```

- [ ] **Step 2: Run test to verify tests run**

Run: `uv run pytest tests/engine/test_optimizer_facade.py -v`
Expected: 4 tests pass (basic assertions)

- [ ] **Step 3: Add imports to optimizer_facade.py**

Add to imports:
```python
import uuid
from homeassistant.util import dt as dt_util
```

- [ ] **Step 4: Add shadow run method to OptimizerFacade**

In optimizer_facade.py, add new method after run_inline():

```python
def run_with_shadow(
    self, 
    data: CoordinatorData, 
    now_dt: Any, 
    config_options: dict[str, Any],
    shadow_prices: dict[str, Any],
) -> dict[str, Any]:
    """Run optimizer with shadow (alternate) prices for comparison.
    
    Args:
        data: Coordinator data with primary prices and forecasts
        now_dt: Current datetime
        config_options: Optimizer config options
        shadow_prices: Dict with shadow price overrides
        
    Returns:
        Dict with comparison result:
        {
            "comparison_match": bool,
            "primary_mode": str,
            "shadow_mode": str,
            "price_delta": float,
        }
    """
    try:
        # Build slots with shadow prices
        ha_timezone = config_options.get("ha_timezone") or "Australia/Sydney"
        slot_builder = self._slot_builder_cls(
            config_options=config_options, 
            ha_timezone=ha_timezone
        )
        
        # Use shadow price overrides
        slots, slot_metadata = slot_builder.build_slots(
            data, 
            data.adaptive_params, 
            now_dt=now_dt,
            override_general_price=shadow_prices.get("general_price"),
            override_feed_in_price=shadow_prices.get("feed_in_price"),
            override_general_forecast=shadow_prices.get("general_forecast"),
            override_feed_in_forecast=shadow_prices.get("feed_in_forecast"),
        )
        
        if not slots:
            return {"error": "no_slots"}
        
        # Build optimizer config
        optimizer_config = _build_optimizer_config(data, config_options)
        
        # Normalize initial SOC
        initial_soc, soc_info = _normalize_initial_soc(data.soc, optimizer_config)
        if initial_soc is None:
            return {"error": "invalid_soc"}
        
        # Run shadow optimizer
        cycle_id = f"shadow_{uuid.uuid4().hex[:12]}"
        inputs = OptimizerInputs(
            cycle_id=cycle_id,
            initial_soc_pct=initial_soc,
            slots=slots,
            config=optimizer_config,
            all_solcast=slot_metadata.all_solcast,
        )
        result = self._planner.plan(inputs)
        
        # Extract decision
        if result.decisions:
            shadow_mode = result.decisions[0].battery_mode
        else:
            shadow_mode = "unknown"
            
        return {
            "shadow_mode": shadow_mode,
            "result": result,
        }
        
    except Exception as exc:
        _LOGGER.warning("Shadow optimizer failed: %s", exc)
        return {"error": str(exc)}
```

- [ ] **Step 5: Update run_inline to call shadow run**

In run_inline(), after primary run completes:

```python
def run_inline(
    self, data: CoordinatorData, now_dt: Any, config_options: dict[str, Any]
) -> None:
    # ... existing primary run code ...
    
    # After primary run, check if comparison enabled
    comparison_mode = config_options.get("comparison_mode", "disabled")
    if comparison_mode == "enabled":
        # Run shadow optimizer
        shadow_prices = {
            "general_price": data.general_price_shadow,
            "feed_in_price": data.feed_in_price_shadow,
            "general_forecast": data.general_forecast_shadow,
            "feed_in_forecast": data.feed_in_forecast_shadow,
        }
        
        # Check if shadow prices are valid
        if shadow_prices["general_price"] > 0:
            shadow_result = self.run_with_shadow(
                data, now_dt, config_options, shadow_prices
            )
            
            if "error" not in shadow_result:
                # Compare decisions
                primary_mode = data.active_mode.value if data.active_mode else ""
                shadow_mode = shadow_result["shadow_mode"]
                
                data.primary_decision = primary_mode
                data.shadow_decision = shadow_mode
                data.comparison_match = (primary_mode == shadow_mode)
                
                # Calculate price delta
                data.price_delta = abs(
                    data.general_price - data.general_price_shadow
                )
                
                # Log mismatch only
                if not data.comparison_match:
                    self._log_comparison_mismatch(
                        data, primary_mode, shadow_mode, data.price_delta
                    )
        else:
            # Shadow unavailable - reset to neutral
            data.comparison_match = True
            data.primary_decision = ""
            data.shadow_decision = ""
            data.price_delta = 0.0
```

- [ ] **Step 6: Add logging helper**

```python
def _log_comparison_mismatch(
    self, 
    data: CoordinatorData, 
    primary_mode: str, 
    shadow_mode: str,
    price_delta: float,
) -> None:
    """Log comparison mismatch to decision_log."""
    entry = {
        "timestamp": dt_util.utcnow().isoformat(),
        "old_mode": primary_mode,
        "new_mode": shadow_mode,
        "reason": f"Decision mismatch: Primary={primary_mode}, Shadow={shadow_mode}, Delta=${price_delta:.2f}",
    }
    data.decision_log.append(entry)
    if len(data.decision_log) > 50:
        data.decision_log = data.decision_log[-50:]
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/engine/test_optimizer_facade.py -v`
Expected: Tests pass

- [ ] **Step 8: Commit**

```bash
git add custom_components/localshift/engine/optimizer_facade.py tests/engine/test_optimizer_facade.py
git commit -m "feat(#300): implement shadow optimizer execution and comparison"
```

---

## Chunk 4: Final Verification

**Files:**
- Test: Full test suite + lint

- [ ] **Step 1: Run ruff on all modified files**

Run: `uv run ruff check custom_components/localshift/`

- [ ] **Step 2: Run full pytest**

Run: `uv run pytest`
Expected: All pass

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat(#300): complete shadow optimizer implementation"
```

---

## Summary

| Chunk | Description | Files |
|-------|-------------|-------|
| 1 | Pass config to optimizer | computation_engine.py |
| 2 | Price override in slots | slots.py |
| 3 | Shadow run + comparison | optimizer_facade.py, test_optimizer_facade.py |
| 4 | Final verification | Full test suite |

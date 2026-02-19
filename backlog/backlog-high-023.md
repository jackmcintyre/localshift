# backlog-high-023: Demand Window Premature Exit Bug

**Priority:** HIGH - Reliability & Robustness

**Status:** COMPLETED

**Date Completed:** 2026-02-19

## Description

During an active demand window, the battery would prematurely exit DEMAND_BLOCK mode when SOC drops below the configured battery target percentage. This causes the battery to switch to SELF_CONSUMPTION mode during the demand window, defeating the purpose of demand blocking.

## Root Cause

The mode selection logic checked `solar_can_reach_target_in_dw` during both entry AND stay decisions. However, this check should only apply to the entry decision (before the demand window starts). Once inside a demand window, the system should stay in DEMAND_BLOCK regardless of current SOC.

## Solution

Modified `computation_engine.py` to unconditionally set `active_mode = BatteryMode.DEMAND_BLOCK` when `demand_window_active` is True, removing the SOC-based conditional logic that was causing premature exit.

## Files Changed

- `custom_components/localshift/computation_engine.py`

## Test Verification

- Pre-commit hooks passed (ruff, ruff-format, vulture, pyright, pytest)

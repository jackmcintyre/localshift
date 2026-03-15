"""Optimizer facade for DP planner/runner orchestration."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from ..const import BatteryMode as _BatteryMode
from ..coordinator.data import CoordinatorData
from .optimizer_dp import DPPlanner, OptimizerInputs
from .optimizer_runner import (
    OptimizerSafetyGate,
    _build_optimizer_config,
    _build_summary,
    _derive_runtime_apply_plan,
    _find_current_slot_index,
    _normalize_initial_soc,
    _serialize_decision,
    _serialize_result,
)
from .slots import SlotBuilder

_LOGGER = logging.getLogger(__name__)


class OptimizerFacade:
    """Facade that runs the DP optimizer and writes results to CoordinatorData."""

    def __init__(
        self,
        planner: DPPlanner | None = None,
        slot_builder_cls: type[Any] = SlotBuilder,
    ) -> None:
        """Initialize the optimizer facade.

        Args:
            planner: Optional DP planner instance (defaults to new DPPlanner).
            slot_builder_cls: Slot builder class for creating time slots.

        """
        self._planner = planner or DPPlanner()
        self._slot_builder_cls = slot_builder_cls
        self._solar_accuracy_tracker: Any = None

    def set_solar_accuracy_tracker(self, tracker: Any) -> None:
        """Set the solar accuracy tracker for bias correction."""
        self._solar_accuracy_tracker = tracker

    def _record_forecasts_for_slots(
        self, slots: list[Any], weather_condition: str
    ) -> None:
        """Record solar forecasts for accuracy tracking.

        Args:
            slots: List of time slots with solar forecast data.
            weather_condition: Current weather condition for tracking.

        """
        if self._solar_accuracy_tracker is None:
            return

        recorded = 0
        for slot in slots:
            period_start = datetime.fromisoformat(slot.timestamp_iso)
            if not self._is_backfillable_period_start(period_start):
                continue

            self._solar_accuracy_tracker.record_forecast(
                period_start=period_start,
                forecast_kwh=slot.solar_kwh,
                weather_condition=weather_condition,
            )
            recorded += 1

        if recorded > 0:
            _LOGGER.debug("Recorded %d solar forecasts for accuracy tracking", recorded)

    def _apply_bias_correction_to_slots(
        self, slots: list[Any], weather_condition: str
    ) -> None:
        """Apply bias correction to solar forecasts based on historical accuracy.

        Args:
            slots: List of time slots to correct.
            weather_condition: Current weather condition for bias lookup.

        """
        if self._solar_accuracy_tracker is None:
            return

        corrected = 0
        for slot in slots:
            slot_dt = datetime.fromisoformat(slot.timestamp_iso)
            time_of_day = self._get_time_of_day(slot_dt)
            season = self._get_season(slot_dt)
            original = slot.solar_kwh
            slot.solar_kwh = self._solar_accuracy_tracker.apply_bias_correction(
                slot.solar_kwh,
                time_of_day,
                weather_condition,
                season,
            )
            if abs(slot.solar_kwh - original) > 0.001:
                corrected += 1

        if corrected > 0:
            _LOGGER.info(
                "Applied solar bias correction for weather=%s to %d slots",
                weather_condition,
                corrected,
            )

    def _apply_cloud_scale_factor_to_slots(
        self, slots: list[Any], data: CoordinatorData, now_dt: datetime
    ) -> None:
        scale_factor = getattr(data, "cloud_event_solar_scale_factor", None)
        if scale_factor is None:
            return

        window_end = now_dt + timedelta(minutes=30)
        applied = 0
        for slot in slots:
            slot_dt = datetime.fromisoformat(slot.timestamp_iso)
            slot_end = slot_dt + timedelta(
                minutes=getattr(slot, "slot_interval_minutes", 30)
            )
            if slot_dt >= window_end or slot_end <= now_dt:
                continue
            slot.solar_kwh *= scale_factor
            applied += 1

        if applied > 0:
            _LOGGER.info(
                "Applied cloud event scale factor %.3f to %d slots",
                scale_factor,
                applied,
            )

    @staticmethod
    def _is_backfillable_period_start(period_start: datetime) -> bool:
        return period_start.minute in (0, 30)

    @staticmethod
    def _get_time_of_day(dt: datetime) -> str:
        if 6 <= dt.hour < 12:
            return "morning"
        if 12 <= dt.hour < 18:
            return "afternoon"
        if 18 <= dt.hour < 21:
            return "evening"
        return "night"

    @staticmethod
    def _get_season(dt: datetime) -> str:
        if dt.month in (12, 1, 2):
            return "summer"
        if dt.month in (3, 4, 5):
            return "autumn"
        if dt.month in (6, 7, 8):
            return "winter"
        return "spring"

    def run_inline(
        self, data: CoordinatorData, now_dt: Any, config_options: dict[str, Any]
    ) -> None:
        """Run DP optimizer inline so active_mode has no cycle lag."""
        try:
            ha_timezone = config_options.get("ha_timezone") or (
                str(dt_util.DEFAULT_TIME_ZONE)
                if dt_util.DEFAULT_TIME_ZONE
                else "Australia/Sydney"
            )
            slot_builder = self._slot_builder_cls(
                config_options=config_options, ha_timezone=ha_timezone
            )
            slots, slot_metadata = slot_builder.build_slots(
                data, data.adaptive_params, now_dt=now_dt
            )

            weather_condition = getattr(data, "weather_condition", None) or "unknown"
            self._record_forecasts_for_slots(slots, weather_condition)
            self._apply_bias_correction_to_slots(slots, weather_condition)
            self._apply_cloud_scale_factor_to_slots(slots, data, now_dt)

            if not slots:
                _LOGGER.warning("DP optimizer: no slots available, skipping")
                return

            optimizer_config = _build_optimizer_config(data, config_options)

            initial_soc, soc_info = _normalize_initial_soc(data.soc, optimizer_config)
            if initial_soc is None:
                _LOGGER.warning(
                    "DP optimizer: invalid SOC %s, skipping", soc_info.get("error")
                )
                return

            cycle_id = uuid.uuid4().hex[:12]
            inputs = OptimizerInputs(
                cycle_id=cycle_id,
                initial_soc_pct=initial_soc,
                slots=slots,
                config=optimizer_config,
                all_solcast=slot_metadata.all_solcast,
            )
            result = self._planner.plan(inputs)

            self._write_optimizer_fields(
                data, result, slot_metadata, config_options, cycle_id
            )

            self._assign_active_mode(data, result, optimizer_config, config_options)

            # Run shadow optimizer for comparison if enabled
            self._run_shadow_comparison(data, now_dt, config_options, slot_metadata)

        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "Inline DP optimizer failed (non-blocking): %s", exc, exc_info=True
            )

    def _write_optimizer_fields(
        self,
        data: CoordinatorData,
        result: Any,
        slot_metadata: Any,
        config_options: dict[str, Any],
        cycle_id: str,
    ) -> None:
        """Write optimizer results to coordinator data fields.

        Args:
            data: Coordinator data to update.
            result: Optimizer result object.
            slot_metadata: Metadata about the time slots.
            config_options: Configuration options dictionary.
            cycle_id: Unique identifier for this optimization cycle.

        """
        data.optimizer_result = _serialize_result(result)
        data.optimizer_decisions = [_serialize_decision(d) for d in result.decisions]
        data.optimizer_summary = _build_summary(
            result=result,
            cycle_id=cycle_id,
            cycle_timestamp_iso=dt_util.utcnow().isoformat(),
            parity_info=slot_metadata.to_parity_dict(),
            config_options=config_options,
        )

        data.forecast_horizon_hours = slot_metadata.horizon_hours

        data.solar_can_reach_target = result.can_solar_reach_target
        allow_dw_under_target = config_options.get("allow_dw_entry_under_target", False)
        data.solar_can_reach_target_in_dw = (
            result.can_solar_reach_target_in_dw if allow_dw_under_target else False
        )

    def _assign_active_mode(
        self,
        data: CoordinatorData,
        result: Any,
        optimizer_config: Any,
        config_options: dict[str, Any],
    ) -> None:
        """Assign active battery mode based on optimizer decisions.

        Args:
            data: Coordinator data to update.
            result: Optimizer result with decisions.
            optimizer_config: Optimizer configuration.
            config_options: Configuration options dictionary.

        """
        alignment = {
            "valid": True,
            "issues": [],
            "warnings": [],
        }

        safety_gate = OptimizerSafetyGate(config_options)
        gate_result = safety_gate.check_admission(data, result, alignment)

        if not gate_result.allowed:
            _LOGGER.info(
                "DP optimizer safety gate blocked: %s — defaulting to SELF_CONSUMPTION",
                gate_result.block_reason,
            )

            data.active_mode = _BatteryMode.SELF_CONSUMPTION
            data.optimizer_last_apply_status = "blocked"
            data.optimizer_safety_block_reason = gate_result.block_reason or ""
            _LOGGER.warning(
                "Optimizer safety gate failed — defaulting to SELF_CONSUMPTION"
            )
            return

        current_slot_idx = _find_current_slot_index(data)
        apply_plan = _derive_runtime_apply_plan(
            data.optimizer_decisions, current_slot_idx, optimizer_config
        )
        data.optimizer_apply_plan = apply_plan

        battery_mode_str = apply_plan.get("battery_mode", "")
        try:
            new_mode = _BatteryMode(battery_mode_str)
            if new_mode != data.active_mode:
                decision_time = dt_util.now()
                if decision_time is not None:
                    data.decision_timestamp = decision_time
                    data.decision_mode = new_mode
                    _LOGGER.info(
                        "Decision lag tracking: mode change %s → %s at %s",
                        data.active_mode.value,
                        new_mode.value,
                        decision_time.isoformat(),
                    )
            data.active_mode = new_mode
            data.optimizer_last_apply_status = "ready_to_apply"
            data.optimizer_safety_block_reason = ""
            _LOGGER.info(
                "DP optimizer: selected %s (action=%s, slot=%d)",
                battery_mode_str,
                apply_plan.get("action"),
                current_slot_idx,
            )
        except ValueError:
            _LOGGER.warning(
                "DP optimizer: invalid battery_mode '%s' — defaulting to SELF_CONSUMPTION",
                battery_mode_str,
            )

            data.active_mode = _BatteryMode.SELF_CONSUMPTION
            data.optimizer_last_apply_status = "fallback"

    def _run_shadow_comparison(
        self,
        data: CoordinatorData,
        now_dt: Any,
        config_options: dict[str, Any],
        slot_metadata: Any,
    ) -> None:
        """Run shadow optimizer and compare decisions if comparison mode enabled."""
        comparison_mode = config_options.get("comparison_mode", "disabled")
        if comparison_mode != "enabled":
            return

        # Check if shadow prices are available
        if data.general_price_shadow <= 0:
            # Shadow unavailable - reset to neutral
            data.comparison_match = True
            data.primary_decision = ""
            data.shadow_decision = ""
            data.price_delta = 0.0
            _LOGGER.debug("Shadow optimizer: shadow prices unavailable, skipping")
            return

        try:
            ha_timezone = config_options.get("ha_timezone") or (
                str(dt_util.DEFAULT_TIME_ZONE)
                if dt_util.DEFAULT_TIME_ZONE
                else "Australia/Sydney"
            )
            slot_builder = self._slot_builder_cls(
                config_options=config_options, ha_timezone=ha_timezone
            )

            # Build slots with shadow prices
            shadow_slots, _ = slot_builder.build_slots(
                data,
                data.adaptive_params,
                now_dt=now_dt,
                override_general_forecast=data.general_forecast_shadow,
                override_feed_in_forecast=data.feed_in_forecast_shadow,
            )

            if not shadow_slots:
                _LOGGER.warning("Shadow optimizer: no slots available")
                return

            optimizer_config = _build_optimizer_config(data, config_options)
            initial_soc, soc_info = _normalize_initial_soc(data.soc, optimizer_config)
            if initial_soc is None:
                _LOGGER.warning("Shadow optimizer: invalid SOC")
                return

            cycle_id = f"shadow_{uuid.uuid4().hex[:12]}"
            inputs = OptimizerInputs(
                cycle_id=cycle_id,
                initial_soc_pct=initial_soc,
                slots=shadow_slots,
                config=optimizer_config,
                all_solcast=slot_metadata.all_solcast,
            )
            result = self._planner.plan(inputs)

            # Extract shadow decision
            if result.decisions:
                shadow_mode = result.decisions[0].battery_mode
            else:
                shadow_mode = "unknown"

            # Compare decisions
            primary_mode = data.active_mode.value if data.active_mode else ""
            data.primary_decision = primary_mode
            data.shadow_decision = shadow_mode
            data.comparison_match = primary_mode == shadow_mode

            # Calculate price delta
            data.price_delta = abs(data.general_price - data.general_price_shadow)

            # Log mismatch only
            if not data.comparison_match:
                self._log_comparison_mismatch(
                    data, primary_mode, shadow_mode, data.price_delta
                )
                _LOGGER.info(
                    "Shadow optimizer: decision mismatch - Primary=%s, Shadow=%s, Delta=$%.2f",
                    primary_mode,
                    shadow_mode,
                    data.price_delta,
                )

        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Shadow optimizer failed: %s", exc)

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

"""Optimizer facade for DP planner/runner orchestration."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from ..const import BatteryMode as _BatteryMode
from ..coordinator.data import CoordinatorData
from ..forecast.solar import get_solar_for_slot_by_interval
from .optimizer_dp import DPPlanner, OptimizerInputs
from .optimizer_runner import (
    OptimizerSafetyGate,
    _build_optimizer_config,
    _build_summary,
    _current_slot_debug_info,
    _derive_runtime_apply_plan,
    _find_current_slot_index,
    _normalize_initial_soc,
    _serialize_decision,
    _serialize_result,
)
from .slots import SlotBuilder

_LOGGER = logging.getLogger(__name__)

# Projected DW-entry shortfall (%-points) above which the execution backstop takes
# over from the token-gated commit path (2026-07-27 incident). Deliberately NOT a
# config entity: this is a failure detector, not a tuning knob — it only fires when
# the plan already wants a pre-DW charge that the decision token never sampled.
PRECHARGE_BACKSTOP_SHORTFALL_PCT = 5.0

# FLOOR on the extra runway slack (minutes) required to RELEASE the runway backstop once
# it is holding, on top of ``precharge_runway_margin_min``. Without a band the arm sits
# exactly on its own threshold: boost charging holds slack roughly constant (the gap
# closes at the same rate the runway shortens), so slack hovers at the margin and the arm
# chatters on/off every cycle.
#
# A floor, not the band itself: the published slack is quantized to slot 0's width, so the
# effective band is ``max(this, precharge_runway_quantum_min)`` — see
# ``_runway_slack_below_margin``. 10 minutes clears a 5-minute slot 0; nothing about it
# clears a 30-minute one, which is what the horizon opens with when there is no 5-minute
# Amber data.
PRECHARGE_RUNWAY_HYSTERESIS_MIN = 10.0

# Modes that constitute "the battery is being charged from the grid". Mirrors
# ``state.machine._CHARGE_MODES``; a decision token granted purely because the plan
# wanted to START charging may commit one of these and nothing else.
_CHARGE_MODES = (_BatteryMode.GRID_CHARGING, _BatteryMode.BOOST_CHARGING)
_CHARGE_MODE_VALUES = frozenset(m.value for m in _CHARGE_MODES)


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
        # True while a forced BOOST from the pre-charge execution backstop is the
        # committed mode. The backstop commits OUTSIDE a fingerprint change, which
        # breaks the property every other commit has — that a change of mode implies
        # the decision context just moved, so another change is imminent. Without
        # this latch a forced boost can outlive its own conditions indefinitely on a
        # flat-price stretch or a stuck price sensor. See _release_backstop_hold.
        # Shared by both forcing arms (#901's floor-gated backstop and the runway arm)
        # — one latch, one MODE-release path (see _release_backstop_hold).
        self._backstop_holding: bool = False
        # The runway arm's OWN hold state, deliberately NOT the shared latch. Hysteresis
        # is release hysteresis: it may only widen the threshold of an arm that is
        # already holding. Reading _backstop_holding here would let a hold opened by
        # #901 widen the runway arm's *arming* threshold by
        # PRECHARGE_RUNWAY_HYSTERESIS_MIN, so a cycle in which #901 fired first would
        # force a grid charge at a slack this arm's own threshold says to ignore.
        # It also survives a transient block (price ceiling, unavailable prices, the
        # other arm's eligibility flag flickering) so a one-cycle blip cannot silently
        # reset the band to narrow — the failure the band exists to prevent.
        self._runway_holding: bool = False

    def set_solar_accuracy_tracker(self, tracker: Any) -> None:
        """Set the solar accuracy tracker for bias correction."""
        self._solar_accuracy_tracker = tracker

    def _record_forecasts_for_slots(
        self,
        slots: list[Any],
        weather_condition: str,
        is_boost: bool = False,
        all_solcast: list[Any] | None = None,
    ) -> None:
        """Record solar forecasts for accuracy tracking.

        Args:
            slots: List of time slots with solar forecast data.
            weather_condition: Current weather condition for tracking.
            all_solcast: Raw Solcast entries, used to capture the pure-P10
                energy for each period (issue #916 over-forecast learning).

        """
        if self._solar_accuracy_tracker is None:
            return

        recorded = 0
        for slot in slots:
            # Only 30-min slots represent a full accuracy period. The hybrid
            # schedule's near-term 5-min slots can land on a :00/:30 boundary
            # and would otherwise overwrite the pending with ~5 minutes of
            # forecast energy — compared against a 30-min actual, that reads
            # as a systematic ~6x under-forecast.
            if getattr(slot, "slot_interval_minutes", 30) != 30:
                continue
            period_start = datetime.fromisoformat(slot.timestamp_iso)
            if not self._is_backfillable_period_start(period_start):
                continue

            forecast_p10_kwh = (
                get_solar_for_slot_by_interval(
                    all_solcast, period_start, 30, confidence=0.0
                )
                if all_solcast
                else 0.0
            )
            self._solar_accuracy_tracker.record_forecast(
                period_start=period_start,
                forecast_kwh=slot.solar_kwh,
                weather_condition=weather_condition,
                is_boost=is_boost,
                forecast_p10_kwh=forecast_p10_kwh,
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
                config_options=config_options,
                ha_timezone=ha_timezone,
                solar_accuracy_tracker=self._solar_accuracy_tracker,
            )
            slots, slot_metadata = slot_builder.build_slots(
                data, data.adaptive_params, now_dt=now_dt
            )

            weather_condition = getattr(data, "weather_condition", None) or "unknown"
            self._record_forecasts_for_slots(
                slots,
                weather_condition,
                is_boost=getattr(data, "boost_charge_active", False),
                all_solcast=getattr(slot_metadata, "all_solcast", None),
            )
            self._apply_bias_correction_to_slots(slots, weather_condition)
            self._apply_cloud_scale_factor_to_slots(slots, data, now_dt)

            if not slots:
                _LOGGER.warning("DP optimizer: no slots available, skipping")
                self._mark_mode_debug_fallback(data)
                return

            optimizer_config = _build_optimizer_config(data, config_options)

            initial_soc, soc_info = _normalize_initial_soc(data.soc, optimizer_config)
            if initial_soc is None:
                _LOGGER.warning(
                    "DP optimizer: invalid SOC %s, skipping", soc_info.get("error")
                )
                self._mark_mode_debug_fallback(data)
                return

            cycle_id = uuid.uuid4().hex[:12]
            inputs = OptimizerInputs(
                cycle_id=cycle_id,
                initial_soc_pct=initial_soc,
                slots=slots,
                config=optimizer_config,
                all_solcast=slot_metadata.all_solcast,
                solcast_analysis_today=getattr(data, "solcast_analysis_today", None),
                solcast_analysis_tomorrow=getattr(
                    data, "solcast_analysis_tomorrow", None
                ),
                solar_absent_confidence=getattr(data, "solar_absent_confidence", 1.0),
                solar_accuracy_tracker=self._solar_accuracy_tracker,
            )
            result = self._planner.plan(inputs)

            self._write_optimizer_fields(
                data,
                result,
                slot_metadata,
                config_options,
                cycle_id,
                soc_info,
                optimizer_config,
            )

            self._assign_active_mode(data, result, optimizer_config, config_options)

            # Logged AFTER _assign_active_mode, not before: the line reports
            # optimizer_precharge_backstop_active, which _assign_active_mode is what
            # sets. Logging first reported the PREVIOUS cycle's backstop state on
            # every line — in a change whose whole purpose is making the next miss
            # reconstructable from the log alone.
            self._log_precharge_decision(data, result, initial_soc, optimizer_config)

            # Run shadow optimizer for comparison if enabled
            self._run_shadow_comparison(data, now_dt, config_options, slot_metadata)

        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "Inline DP optimizer failed (non-blocking): %s", exc, exc_info=True
            )
            self._mark_mode_debug_fallback(data)

    def _write_optimizer_fields(
        self,
        data: CoordinatorData,
        result: Any,
        slot_metadata: Any,
        config_options: dict[str, Any],
        cycle_id: str,
        initial_soc_info: dict[str, Any] | None = None,
        optimizer_config: Any | None = None,
    ) -> None:
        """Write optimizer results to coordinator data fields.

        Args:
            data: Coordinator data to update.
            result: Optimizer result object.
            slot_metadata: Metadata about the time slots.
            config_options: Configuration options dictionary.
            cycle_id: Unique identifier for this optimization cycle.
            initial_soc_info: SOC-normalization diagnostics from
                ``_normalize_initial_soc``. Threaded through so the summary's
                ``initial_soc_pct`` is populated on the live path (previously
                dropped — it read null while ``dw_entry_soc_pct`` was set).
            optimizer_config: The solved ``OptimizerConfig``. Threaded through for the
                same reason: the runway telemetry the DP writes onto it has no other
                route to the summary sensor. Optional so the batch/test callers that
                only need the result-derived fields are unaffected.

        """
        data.optimizer_result = _serialize_result(result)
        data.optimizer_decisions = [_serialize_decision(d) for d in result.decisions]
        data.optimizer_summary = _build_summary(
            result=result,
            cycle_id=cycle_id,
            cycle_timestamp_iso=dt_util.utcnow().isoformat(),
            parity_info=slot_metadata.to_parity_dict(),
            config_options=config_options,
            initial_soc_info=initial_soc_info,
        )

        # Actual DW-entry telemetry (2026-07-27). Injected here rather than inside
        # ``_build_summary`` because that is a pure result→dict function shared with
        # the batch/test path and has no coordinator handle. The CoordinatorData
        # fields remain the single source of truth; the summary only mirrors them so
        # the projected and actual DW-entry SOC sit side by side in one payload.
        dw_entry_actual_at = getattr(data, "dw_entry_actual_at", None)
        dw_entry_actual_date = getattr(data, "dw_entry_actual_date", None)
        data.optimizer_summary["dw_entry_actual_soc_pct"] = getattr(
            data, "dw_entry_actual_soc_pct", None
        )
        data.optimizer_summary["dw_entry_actual_at"] = (
            dw_entry_actual_at.isoformat() if dw_entry_actual_at is not None else None
        )
        data.optimizer_summary["dw_entry_actual_shortfall_pct"] = getattr(
            data, "dw_entry_actual_shortfall_pct", None
        )
        data.optimizer_summary["dw_entry_actual_target_pct"] = getattr(
            data, "dw_entry_actual_target_pct", None
        )
        data.optimizer_summary["dw_entry_actual_date"] = (
            dw_entry_actual_date.isoformat()
            if dw_entry_actual_date is not None
            else None
        )

        # Runway telemetry (2026-07-28). Injected here for the same reason as the block
        # above: these are solver-derived OptimizerConfig fields and ``_build_summary``
        # is a pure result→dict function with no config handle, so without this the
        # sensor's ``precharge_runway_slack_min`` / ``hard_floor_suppressed_by_solar``
        # attributes read null/false forever — including on the cycle the runway arm
        # force-commits BOOST and logs the slack as its reason. The operator needs to be
        # able to watch slack degrade toward the margin; that is the whole point of
        # publishing it.
        data.optimizer_summary["precharge_runway_slack_min"] = getattr(
            optimizer_config, "precharge_runway_slack_min", None
        )
        data.optimizer_summary["hard_floor_suppressed_by_solar"] = bool(
            getattr(optimizer_config, "hard_floor_suppressed_by_solar", False)
        )
        data.optimizer_summary["precharge_runway_margin_min"] = getattr(
            optimizer_config, "precharge_runway_margin_min", None
        )

        data.forecast_horizon_hours = slot_metadata.horizon_hours

        data.solar_can_reach_target = result.can_solar_reach_target
        allow_dw_under_target = config_options.get("allow_dw_entry_under_target", False)
        data.solar_can_reach_target_in_dw = (
            result.can_solar_reach_target_in_dw if allow_dw_under_target else False
        )

    def _log_precharge_decision(
        self,
        data: CoordinatorData,
        result: Any,
        initial_soc: float,
        optimizer_config: Any,
    ) -> None:
        """Emit one diagnostic line per cycle describing the pre-charge decision.

        Restores observability after the 2026-06-30 silent miss (battery entered
        the demand window at ~10% while the summary reported a healthy DW-entry
        SOC). Logs the SOC the planner actually used, the target, the planned
        DW-entry/peak SOC, and whether/when a grid charge is scheduled — so a
        future miss is diagnosable from the log alone. Never raises.

        Carries ``dw_entry_actual`` (2026-07-27) alongside the projection: the
        projected ``dw_entry_soc_pct`` rolls over to tomorrow's window the instant
        today's DW starts, which is what erased the 64%-vs-95% miss from the log.
        """
        try:
            decisions = getattr(result, "decisions", None) or []
            charge_decisions = [
                d for d in decisions if getattr(d, "grid_charge", False)
            ]
            first_charge = charge_decisions[0] if charge_decisions else None
            target_soc = getattr(optimizer_config, "demand_window_target_soc_pct", None)
            dw_entry = getattr(result, "dw_entry_soc_pct", None)
            shortfall = getattr(result, "terminal_shortfall_pct", None)
            peak = getattr(result, "peak_soc_pct", None)
            _LOGGER.info(
                "Pre-charge decision: initial_soc=%.1f%% target=%s dw_entry_soc=%s "
                "peak_soc=%s shortfall=%s dw_active=%s charge_slots=%d "
                "first_charge=%s dw_entry_actual=%s backstop=%s",
                initial_soc,
                f"{target_soc:.0f}%" if target_soc is not None else "n/a",
                f"{dw_entry:.1f}%" if dw_entry is not None else "n/a",
                f"{peak:.1f}%" if peak is not None else "n/a",
                f"{shortfall:.1f}%" if shortfall is not None else "n/a",
                getattr(data, "demand_window_active", False),
                len(charge_decisions),
                getattr(first_charge, "timestamp_iso", None),
                self._format_dw_entry_actual(data),
                getattr(data, "optimizer_precharge_backstop_active", False),
            )
            self._warn_soc_divergence(data, initial_soc, dw_entry, target_soc)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Pre-charge decision log failed (non-fatal): %s", exc)

    @staticmethod
    def _format_dw_entry_actual(data: CoordinatorData) -> str:
        """Render today's captured DW-entry actual as ``64.0%@15:00``, or ``n/a``.

        ``getattr`` guarded because the capture is a coordinator-side concern: a
        cycle that runs before the first capture of the day (or a bare
        ``CoordinatorData`` in a unit test) simply reads ``n/a``.
        """
        soc = getattr(data, "dw_entry_actual_soc_pct", None)
        at = getattr(data, "dw_entry_actual_at", None)
        if soc is None or at is None:
            return "n/a"
        return f"{soc:.1f}%@{at:%H:%M}"

    def _warn_soc_divergence(
        self,
        data: CoordinatorData,
        initial_soc: float,
        dw_entry: float | None,
        target_soc: float | None,
    ) -> None:
        """Loudly flag the silent-failure mode and set a coordinator flag.

        Trips when a demand window is active but the real SOC is far below target
        — i.e. pre-charge appears to have been missed. Sets
        ``data.optimizer_soc_underprepared`` (a sensor / coordinator notification
        can surface it) and emits a WARNING so the failure is never silent again.

        The message carries the *captured* DW-entry actual as well as the live SOC
        (2026-07-27): by the time this trips, the battery may have recovered some
        charge inside the window, so the live number understates the miss.
        """
        threshold = target_soc if target_soc is not None else 95.0
        in_dw = getattr(data, "demand_window_active", False)
        # Materially short = more than 20 points below target while the DW is live.
        underprepared = bool(in_dw and initial_soc < (threshold - 20.0))
        data.optimizer_soc_underprepared = underprepared
        if underprepared:
            _LOGGER.warning(
                "SOC UNDERPREPARED: demand window active but battery at %.1f%% "
                "(target %.0f%%, planned DW-entry %s, entered at %s). Pre-charge "
                "appears to have been missed — verify the optimizer scheduled a "
                "charge.",
                initial_soc,
                threshold,
                f"{dw_entry:.1f}%" if dw_entry is not None else "n/a",
                self._format_dw_entry_actual(data),
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

        # Surface the current-slot lookup that drives the apply plan. found=False
        # means the silent idx=0 fallback is in effect — exactly what these
        # debug fields exist to expose (Mode Source / Forecast Slot Found).
        current_slot_idx, slot_found, slot_hhmm, first_hhmm, gap_seconds = (
            _current_slot_debug_info(data)
        )
        data.debug_forecast_slot_found = slot_found
        data.debug_forecast_slot_time = slot_hhmm
        data.debug_first_forecast_slot_time = first_hhmm
        data.debug_time_gap_seconds = gap_seconds

        # Cleared up-front so the flag can never go stale on any branch below (the
        # safety-block return and the invalid-mode fallback both exit without
        # reaching the backstop check).
        data.optimizer_precharge_backstop_active = False

        # #622 gate replacement: the mode may be (re-)decided only on an allowed
        # evaluation. When frozen, every branch below records its block-status /
        # debug observability but must NOT touch active_mode, decision_timestamp
        # or decision_mode — the previously-decided mode is pinned. The would-be
        # plan mode is surfaced via debug_plan_mode_pending instead.
        decision_allowed = data.mode_decision_allowed

        safety_gate = OptimizerSafetyGate(config_options)
        gate_result = safety_gate.check_admission(data, result, alignment)

        if not gate_result.allowed:
            _LOGGER.info(
                "DP optimizer safety gate blocked: %s — defaulting to SELF_CONSUMPTION",
                gate_result.block_reason,
            )

            data.optimizer_last_apply_status = "blocked"
            data.optimizer_safety_block_reason = gate_result.block_reason or ""
            self._commit_or_hold_mode(
                data,
                _BatteryMode.SELF_CONSUMPTION,
                decision_allowed,
                mode_source="fallback",
            )
            _LOGGER.warning(
                "Optimizer safety gate failed — defaulting to SELF_CONSUMPTION"
            )
            return

        apply_plan = _derive_runtime_apply_plan(
            data.optimizer_decisions, current_slot_idx, optimizer_config
        )
        data.optimizer_apply_plan = apply_plan

        battery_mode_str = apply_plan.get("battery_mode", "")
        try:
            new_mode = _BatteryMode(battery_mode_str)
            data.optimizer_last_apply_status = "ready_to_apply"
            data.optimizer_safety_block_reason = ""

            # A token granted purely by the plan-charge trigger is one-directional in
            # what committed it, so it must be one-directional in what it commits.
            # Otherwise a grant raised because the plan wanted to START charging is
            # spent on whatever slot-0 has drifted to by the time the facade re-plans
            # — including a discharge. Fall back to frozen: the wanted charge stays
            # visible on debug_plan_mode_pending and the execution backstop (below)
            # remains the deterministic path.
            if (
                decision_allowed
                and getattr(data, "mode_decision_plan_charge_only", False)
                and new_mode not in _CHARGE_MODES
            ):
                _LOGGER.debug(
                    "Plan-charge grant not spent: fresh plan selected %s, "
                    "not a charge mode — holding",
                    battery_mode_str,
                )
                decision_allowed = False

            _LOGGER.info(
                "DP optimizer: selected %s (action=%s, slot=%d, decision_allowed=%s)",
                battery_mode_str,
                apply_plan.get("action"),
                current_slot_idx,
                decision_allowed,
            )
            # Execution backstop (2026-07-27): strictly after the safety gate, so a
            # blocked cycle can never reach it. Force-commits the pre-charge the plan
            # already contains when the token has starved it out.
            backstop_mode = self._precharge_backstop_mode(
                data, result, optimizer_config, current_slot_idx
            )
            backstop_source = "precharge_backstop"
            if backstop_mode is None:
                # #901's arm is gated on hard_target_floor, which the solver suppresses
                # on any day solar looks sufficient — so its None here is "no floor to
                # measure against", not "nothing to worry about". The runway arm covers
                # exactly that blind spot, keyed on physical slack instead of a floor.
                backstop_mode = self._runway_backstop_mode(
                    data, optimizer_config, current_slot_idx
                )
                if backstop_mode is not None:
                    backstop_source = "runway_backstop"
            data.optimizer_precharge_backstop_active = backstop_mode is not None
            if backstop_mode is not None:
                self._backstop_holding = True
                self._commit_or_hold_mode(
                    data, backstop_mode, True, mode_source=backstop_source
                )
                return
            if self._release_backstop_hold(data):
                self._commit_or_hold_mode(
                    data, new_mode, True, mode_source="precharge_backstop_release"
                )
                return
            # Ordinary optimizer commit path. Apply a dwell band to release of a
            # committed charge: the slot-0 sawtooth that flapped charge_forced 11x
            # in 124 min on 2026-07-30 is suppressed here without touching the
            # runway arm's own hysteresis (that path is above this check).
            if decision_allowed and self._should_hold_charge_dwell(
                data, new_mode, optimizer_config
            ):
                data.debug_plan_mode_pending = new_mode.value
                return
            self._commit_or_hold_mode(
                data, new_mode, decision_allowed, mode_source="optimizer"
            )
        except ValueError:
            _LOGGER.warning(
                "DP optimizer: invalid battery_mode '%s' — defaulting to SELF_CONSUMPTION",
                battery_mode_str,
            )

            data.optimizer_last_apply_status = "fallback"
            self._commit_or_hold_mode(
                data,
                _BatteryMode.SELF_CONSUMPTION,
                decision_allowed,
                mode_source="fallback",
            )

    def _release_backstop_hold(self, data: CoordinatorData) -> bool:
        """Return True when a forced BOOST must be released this cycle.

        The backstop is the only path that commits a mode WITHOUT a decision-context
        change. Every other commit carries an implicit guarantee — the fingerprint
        just moved, so it will move again shortly and the mode will be revisited.
        A forced BOOST has no such guarantee: once its conditions clear
        (``soc >= hard_target_floor``, typically) the backstop simply returns None
        and the ordinary frozen path PINS the boost. On a flat-price stretch or a
        stuck price sensor the fingerprint never changes, the plan-charge trigger is
        one-directional and will not grant a wanted STOP, and the battery keeps
        force-charging at full import price until the demand window flips.

        So the release is made symmetric with the force: the same latch that opened
        an ungated commit closes it with one.
        """
        if not self._backstop_holding:
            return False
        # Confined to the same in-lock window as the force. The release is an ungated
        # commit too, so the out-of-lock compute_derived_values in
        # ``coordinator.async_recompute_and_evaluate`` — where the backstop returns
        # None purely because permission is closed, not because its conditions
        # cleared — must not consume the latch or command hardware.
        if not getattr(data, "mode_backstop_allowed", False):
            return False
        self._backstop_holding = False
        # Only an ungated release is needed while the forced boost is still what is
        # committed. If an ordinary grant already moved the mode on, there is nothing
        # to release — drop the latch and let the normal path decide.
        return data.active_mode in _CHARGE_MODES

    def _precharge_backstop_mode(
        self,
        data: CoordinatorData,
        result: Any,
        optimizer_config: Any,
        current_slot_idx: int,
    ) -> Any | None:
        """Return BOOST_CHARGING when the pre-charge execution backstop must fire.

        2026-07-27 incident: the demand window was entered at 64% against a 95%
        target. The DP planned the pre-charge correctly — ``first_charge=NOW`` on
        plan after plan — but the #622 decision token only grants on a
        price/spike/DW/floor change, and at every grant instant the freshly-computed
        plan's slot-0 action happened to be "hold" (the cheapest first-charge slot
        drifts forward on every replan). The wanted charge was recorded dozens of
        times in ``debug_plan_mode_pending`` and never committed.

        This is the executor-side backstop for that class of failure: when the plan
        *already contains* a pre-DW grid charge, the live SOC is below the #885 hard
        DW-target floor, we are inside the urgency window and the projected shortfall
        is material, the mode is force-committed regardless of the token. Every gate
        reuses an existing engine decision — notably ``hard_target_floor``, which is
        already dormant whenever pre-charge is not required (``allow_dw_entry_under_
        target``, solar alone reaches target, no demand window, non-self-consumption)
        — so this never invents charge intent it only executes intent the DP
        expressed. BOOST is the right mode because ``constraints.feasible_actions``
        unlocks boost under exactly this predicate (``soc_pct < hard_target_floor``
        in a pre-DW slot).

        Never raises (mirrors ``_log_precharge_decision``): a failing backstop check
        must never take down the optimizer cycle.
        """
        try:
            if not self._backstop_context_permits(data):
                return None

            # Primary reuse: the #885/#886 hard DW-target feasibility floor. None ⇒
            # pre-charge is not required today; the backstop is dormant with it.
            hard_floor = getattr(optimizer_config, "hard_target_floor", None)
            if hard_floor is None:
                return None

            soc = getattr(data, "soc", None)
            if soc is None or soc >= hard_floor:
                return None

            window = self._backstop_urgency_window(optimizer_config, current_slot_idx)
            if window is None:
                return None
            urgency_start_idx, terminal_penalty_idx = window

            shortfall = self._projected_shortfall_pct(result, optimizer_config)
            if shortfall <= PRECHARGE_BACKSTOP_SHORTFALL_PCT:
                return None

            pre_dw_charge_slots = self._count_pre_dw_charge_slots(
                data.optimizer_decisions, terminal_penalty_idx
            )
            if pre_dw_charge_slots == 0:
                return None

            if self._price_above_precharge_ceiling(data, optimizer_config):
                return None

            _LOGGER.warning(
                "PRE-CHARGE BACKSTOP: forcing BOOST_CHARGING — soc=%.1f%% "
                "hard_floor=%.1f%% projected shortfall=%.1f%% slot=%d "
                "(urgency window %d..%d), plan has %d pre-DW charge slot(s) but the "
                "decision token never committed one.",
                soc,
                hard_floor,
                shortfall,
                current_slot_idx,
                urgency_start_idx,
                terminal_penalty_idx,
                pre_dw_charge_slots,
            )
            return _BatteryMode.BOOST_CHARGING
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Pre-charge backstop check failed (non-fatal): %s", exc)
            return None

    def _runway_backstop_mode(
        self,
        data: CoordinatorData,
        optimizer_config: Any,
        current_slot_idx: int,
    ) -> Any | None:
        """Return BOOST_CHARGING when the runway-insurance arm must fire.

        Fast-follow to #901 (found live 2026-07-28). The #901 backstop is gated on
        ``hard_target_floor``, and ``_compute_hard_target_floor`` deliberately returns
        None on any day the solar-sufficiency check passes — don't fight #816/#849. So
        the backstop built to prevent a repeat of 2026-07-27 is FULLY DORMANT on exactly
        the days that look fine in the morning, with nothing to catch an afternoon cloud
        event that craters solar with too little runway left to close the gap at the PW3
        boost rate. Observed live: 59.1% SOC against a 95% target with 82 minutes to the
        demand window — recoverable only via a manual ``boost_charging`` override.

        The armed quantity is deliberately runway SLACK
        (``precharge_runway_slack_min``: minutes to DW entry minus minutes of boost
        charging needed to close the gap), NOT solar confidence — 2026-07-27 proved
        confidence unreliable (a blended "high" 0.87 against a 52% Solcast day
        confidence).

        Slack is a WORST-CASE question — "if solar stopped right now, could grid still
        reach target in time?" — and it is not self-limiting on a sunny day: the gap it
        measures is the LIVE gap, which projected solar has not yet closed. A low-SOC
        morning under a strong forecast therefore reads a small slack and can arm this
        arm even though solar alone would have reached target. That false-fire is the
        deliberate insurance premium of the design, and ``precharge_runway_margin_min``
        (0 ⇒ off) is the operator's control over how much of it to pay. Do not "fix" it
        by crediting projected solar into the gap: that reinstates exactly the forecast
        trust the arm exists to survive, and would have left 2026-07-28 uncovered.

        Every gate is shared with #901 except three deliberate differences:

        - ``_count_pre_dw_charge_slots`` is NOT checked. #901 requires a planned pre-DW
          grid charge so it "never invents intent" — but on a solar-sufficient day the
          plan contains no grid-charge slot at all. That absence IS the gap this arm
          exists to cover, so requiring the slot would make it as dormant as #901.
        - the shortfall is the LIVE gap (``target - soc``), not the plan's
          ``terminal_shortfall_pct``, which reads 0 on a solar-sufficient day — the same
          blind spot one level down.
        - the arm runs whenever pre-charge is required today
          (``_precharge_required_today``), not only when solar suppressed the floor. The
          call site reaches this arm only after #901 has declined, so gating on floor
          liveness left a live-floor / sub-deadband band uncovered. See that helper for
          the 2026-08-25 incident.

        The gates are ordered in two deliberate groups. The first group is the arm's OWN
        conditions: when one of them clears, the hold is genuinely over and
        ``_runway_holding`` drops. The second group is transient blocks — a price tick
        above the ceiling, an unavailable price sensor, the other arm's eligibility flag
        flickering as ``check_global_solar_sufficiency`` chatters on a marginal-solar
        afternoon. Those must stop the forced charge but MUST NOT clear the latch: doing
        so resets the hysteresis band to narrow, and the arm re-fires on the very next
        tick at a slack it was already holding through. That is the mode-flap this
        ordering exists to prevent.

        Never raises: a failing arm must degrade to "no backstop", never take down the
        optimizer cycle.
        """
        try:
            # Out-of-lock recomputes must observe without mutating: the latch is hold
            # state for the in-lock path only (mirrors _release_backstop_hold).
            if not getattr(data, "mode_backstop_allowed", False):
                return None

            # --- Group 1: the arm's own conditions. Clearing one ends the hold. ---
            gap = self._live_target_gap_pct(data, optimizer_config)
            if gap is None or gap <= PRECHARGE_BACKSTOP_SHORTFALL_PCT:
                self._runway_holding = False
                return None

            armed = self._runway_slack_below_margin(optimizer_config)
            if armed is None:
                # Slack recovered past the band, is uninterpretable, or the margin knob
                # disabled the arm — all genuine ends to the hold.
                self._runway_holding = False
                return None
            slack, threshold = armed

            # --- Group 2: transient blocks. They suppress, they do not release. ---
            if not self._backstop_context_permits(data):
                return None

            # A hold already opened by THIS arm survives the hard floor going live: #901
            # only fires on a material *projected* shortfall, so when the floor flickers
            # on it routinely declines too, and releasing here would drop the forced
            # boost for exactly one cycle before re-arming.
            if not (
                self._precharge_required_today(optimizer_config) or self._runway_holding
            ):
                return None

            window = self._backstop_urgency_window(optimizer_config, current_slot_idx)
            if window is None:
                return None
            urgency_start_idx, terminal_penalty_idx = window

            if self._price_above_precharge_ceiling(data, optimizer_config):
                return None

            self._runway_holding = True
            _LOGGER.warning(
                "RUNWAY BACKSTOP: forcing BOOST_CHARGING — soc=%.1f%% target=%.1f%% "
                "gap=%.1f%% runway slack=%.1f min < %.1f min slot=%d (urgency window "
                "%d..%d, hard_floor=%s). The #901 pre-charge backstop declined this "
                "cycle — either the floor is suppressed because solar was projected "
                "sufficient, or the projected shortfall sits under its %.1f-point "
                "deadband — and there is no longer enough runway to reach target at "
                "boost rate if solar fails.",
                float(getattr(data, "soc", 0.0)),
                float(getattr(optimizer_config, "demand_window_target_soc_pct", 0.0)),
                gap,
                slack,
                threshold,
                current_slot_idx,
                urgency_start_idx,
                terminal_penalty_idx,
                getattr(optimizer_config, "hard_target_floor", None),
                PRECHARGE_BACKSTOP_SHORTFALL_PCT,
            )
            return _BatteryMode.BOOST_CHARGING
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Runway backstop check failed (non-fatal): %s", exc)
            return None

    @staticmethod
    def _floor_suppressed_by_solar(optimizer_config: Any) -> bool:
        """True only when the hard floor is dormant BECAUSE solar looked sufficient.

        The solar suppression is the ONLY None-reason either backstop may act on —
        ``allow_dw_entry_under_target``, no demand window, a non-self-consumption mode
        and a legacy config without the field are policy or structural decisions that
        must stay exactly as dormant as they are under #901.
        """
        if getattr(optimizer_config, "hard_target_floor", None) is not None:
            return False
        return bool(getattr(optimizer_config, "hard_floor_suppressed_by_solar", False))

    @staticmethod
    def _precharge_required_today(optimizer_config: Any) -> bool:
        """True when today genuinely needs a pre-charge, by either engine signal.

        Found live 2026-08-25. This predicate used to be ``_floor_suppressed_by_solar``
        alone, on the reasoning that "a live floor means #901 owns the cycle and this arm
        stays out of the way". That mutual exclusion was keyed on the wrong quantity. The
        call site already reaches this arm ONLY when ``_precharge_backstop_mode`` returned
        None, so by construction #901 has already declined by the time we get here —
        standing down again on floor *liveness* turns "#901 declined" into "nobody acts".

        That leaves an uncovered band, and it is not a narrow one: a LIVE hard floor, an
        SOC far below it, and a projected shortfall under #901's
        ``PRECHARGE_BACKSTOP_SHORTFALL_PCT`` deadband. On 2026-08-25 the system sat in it
        for half an hour — 22% SOC against a 95% target, hard floor live, projected
        shortfall 3.8–4.2% (under the 5.0 deadband, so #901 declined) and runway slack of
        2.0 minutes against a 15-minute margin. Both arms off. The DP re-planned every 5
        minutes and deferred ``first_charge`` each time (12:35 → 12:40 → 12:45) while the
        achievable DW entry decayed 96.1% → 94.5% → 90.8%. Recovered by a manual override.

        A deferral drift is exactly the shape the deadband cannot see: it adds ~1.5 points
        per replan and never steps past 5.0 in one cycle until the runway is already gone.
        The fix is NOT to lower the deadband — it exists to stop the mode flap — but to let
        the arm that measures physical slack speak when the arm that measures projected
        shortfall has passed.

        A live floor is itself the engine's statement that pre-charge is required today,
        so admitting it here cannot arm the backstop on a day pre-charge was not wanted:
        every policy/structural None-reason still yields a None floor AND a False
        suppression flag, and stays dormant.
        """
        if getattr(optimizer_config, "hard_target_floor", None) is not None:
            return True
        return bool(getattr(optimizer_config, "hard_floor_suppressed_by_solar", False))

    @staticmethod
    def _live_target_gap_pct(
        data: CoordinatorData, optimizer_config: Any
    ) -> float | None:
        """%-points between the live SOC and the DW target, or None if unreadable.

        Deliberately NOT ``result.terminal_shortfall_pct``: on a solar-sufficient day the
        plan believes solar closes the gap and publishes a shortfall of 0, which is the
        same blind spot that makes the hard floor dormant in the first place.
        """
        soc = getattr(data, "soc", None)
        target = getattr(optimizer_config, "demand_window_target_soc_pct", None)
        if soc is None or target is None:
            return None
        return float(target) - float(soc)

    def _runway_slack_below_margin(
        self, optimizer_config: Any
    ) -> tuple[float, float] | None:
        """Return ``(slack, threshold)`` when runway slack has fallen under the margin.

        ``precharge_runway_slack_min`` is None when the quantity is not interpretable (no
        demand window, unparseable timestamps, degenerate battery constants) — which is
        never the same thing as zero slack, so the arm stays dormant.

        The widening reads ``_runway_holding``, this arm's own latch, NOT the shared
        ``_backstop_holding``: release hysteresis may only widen the threshold of an arm
        that is already holding, and inheriting #901's latch would widen this arm's
        *arming* threshold on a cycle it never opened.

        The band is ``max(PRECHARGE_RUNWAY_HYSTERESIS_MIN, precharge_runway_quantum_min)``
        because the published slack carries a sawtooth of one slot-0 width: the DP has no
        clock, so the time term steps at slot boundaries while the SOC gap closes
        continuously. A fixed 10-minute band clears a 5-minute slot 0 and not a 30-minute
        one, which would re-arm the arm at every boundary for the whole pre-charge window.

        Applied only AFTER the kill switch: a margin of 0 disables the arm outright rather
        than leaving a hysteresis-wide band armed.
        """
        slack = getattr(optimizer_config, "precharge_runway_slack_min", None)
        if slack is None:
            return None
        margin = float(
            getattr(optimizer_config, "precharge_runway_margin_min", 0.0) or 0.0
        )
        if margin <= 0.0:
            return None
        threshold = margin
        if self._runway_holding:
            quantum = float(
                getattr(optimizer_config, "precharge_runway_quantum_min", 0.0) or 0.0
            )
            threshold += max(PRECHARGE_RUNWAY_HYSTERESIS_MIN, quantum)
        if float(slack) >= threshold:
            return None
        return float(slack), threshold

    @staticmethod
    def _backstop_context_permits(data: CoordinatorData) -> bool:
        """The three gates that depend only on coordinator state, not on the plan."""
        # In-lock only. Set by the state machine alongside the decision token and
        # cleared in its finally block, so the out-of-lock recompute in
        # ``coordinator.async_recompute_and_evaluate`` can never command hardware.
        if not getattr(data, "mode_backstop_allowed", False):
            return False

        # Issue #330: an unavailable price entity reads as general_price = 0.0, not
        # None, so the pre-charge price ceiling would wave the backstop through at a
        # completely unknown real price — potentially straight through a spike.
        # "Grid charging decisions will be deferred" applies to a forced charge most
        # of all.
        if not getattr(data, "prices_available", True):
            return False

        # Pre-DW only — once the DW is live the pre-charge is moot and forcing a
        # charge would fight the demand-block behaviour.
        return not getattr(data, "demand_window_active", False)

    @staticmethod
    def _backstop_urgency_window(
        optimizer_config: Any, current_slot_idx: int
    ) -> tuple[int, int] | None:
        """Return ``(urgency_start_idx, terminal_penalty_idx)`` when the current slot
        sits inside the urgency pre-charge window, else None.

        Same window the urgency-inflated pre-charge price is legitimate in, and
        strictly before the DW-entry slot — the scope
        ``constraints.hard_floor_needs_boost`` already uses at DP level. Either index
        being None ⇒ no demand window in the horizon (or a direct unit-test caller),
        so the backstop stays dormant.
        """
        urgency_start_idx = getattr(optimizer_config, "urgency_window_start_idx", None)
        terminal_penalty_idx = getattr(optimizer_config, "terminal_penalty_idx", None)
        if urgency_start_idx is None or terminal_penalty_idx is None:
            return None
        if not urgency_start_idx <= current_slot_idx < terminal_penalty_idx:
            return None
        return urgency_start_idx, terminal_penalty_idx

    @staticmethod
    def _projected_shortfall_pct(result: Any, optimizer_config: Any) -> float:
        """Worst of the two published DW-entry shortfall measures (%-points).

        The 2026-07-27 log carried both ``terminal_shortfall_pct`` and a
        ``target - dw_entry_soc_pct`` gap and they can diverge, so the backstop takes
        the max rather than trusting either alone.
        """
        shortfall = float(getattr(result, "terminal_shortfall_pct", None) or 0.0)
        target = getattr(optimizer_config, "demand_window_target_soc_pct", None)
        dw_entry = getattr(result, "dw_entry_soc_pct", None)
        if target is not None and dw_entry is not None:
            shortfall = max(shortfall, float(target) - float(dw_entry))
        return shortfall

    @staticmethod
    def _count_pre_dw_charge_slots(
        decisions: list[Any] | None, terminal_penalty_idx: int
    ) -> int:
        """Count charge slots the plan schedules before the demand-window entry.

        Reads the *serialized* decisions off ``data`` — ``_write_optimizer_fields``
        runs before ``_assign_active_mode``, so these are this cycle's plan. This is
        what keeps the backstop honest: it never invents charge intent, it only
        executes intent the DP already expressed but the token never sampled.

        A decision with no ``slot_index`` is NOT counted. Defaulting it to 0 would
        make a malformed decision read as a pre-DW charge and arm a forced boost off
        a plan that never located itself in time.
        """
        return sum(
            1
            for d in (decisions or [])
            if d.get("grid_charge")
            and isinstance(d.get("slot_index"), int)
            and d["slot_index"] < terminal_penalty_idx
        )

    @staticmethod
    def _price_above_precharge_ceiling(
        data: CoordinatorData, optimizer_config: Any
    ) -> bool:
        """True when the live price exceeds the operator's pre-charge ceiling.

        Reuses ``CONF_MAX_PRECHARGE_PRICE`` (already on the config) rather than
        introducing a backstop-specific price knob.
        """
        ceiling = getattr(optimizer_config, "max_precharge_price", None)
        price = getattr(data, "general_price", None)
        return ceiling is not None and price is not None and price > ceiling

    @staticmethod
    def _should_hold_charge_dwell(
        data: CoordinatorData,
        new_mode: Any,
        optimizer_config: Any,
    ) -> bool:
        """Return True when the ordinary commit path should hold a charge release.

        Prevents the slot-0 sawtooth that flapped ``charge_forced`` 11 times in
        124 minutes on 2026-07-30: the plan oscillates hold/charge every replan
        because the cheapest first-charge slot drifts forward and slack is
        quantised to slot-0 width. The runway arm already has an anti-chatter
        band via ``PRECHARGE_RUNWAY_HYSTERESIS_MIN``; this is the equivalent
        damping on the ordinary token-gated commit path.

        The band is ``max(PRECHARGE_RUNWAY_HYSTERESIS_MIN, quantum)`` where
        ``quantum = precharge_runway_quantum_min`` (slot-0's width in minutes).
        Tuning one side of the runway band retunes this one too, by construction.
        """
        # Only damp releases of a committed charge; commits and charge-to-charge
        # transitions are not the pathology.
        if data.active_mode not in _CHARGE_MODES:
            return False
        if new_mode in _CHARGE_MODES:
            return False
        # Demand-window active takes priority — never trap a charge into a DW.
        if getattr(data, "demand_window_active", False):
            return False
        ts = getattr(data, "decision_timestamp", None)
        if ts is None:
            # Fresh session / no timestamp: fail open, commit immediately.
            return False
        now = dt_util.now()
        try:
            elapsed = (now - ts).total_seconds() / 60.0
        except TypeError:
            # Naive/aware timestamp mismatch: fail open rather than propagate.
            return False
        quantum = getattr(optimizer_config, "precharge_runway_quantum_min", 0.0) or 0.0
        band = max(PRECHARGE_RUNWAY_HYSTERESIS_MIN, quantum)
        return elapsed < band

    @staticmethod
    def _commit_or_hold_mode(
        data: CoordinatorData,
        new_mode: Any,
        decision_allowed: bool,
        mode_source: str,
    ) -> None:
        """Commit a freshly-decided mode, or hold the pinned mode when frozen.

        #622 gate replacement. On an allowed evaluation this commits ``new_mode``
        to ``data.active_mode`` (with decision-lag tracking) exactly as before.
        On a frozen evaluation it leaves ``active_mode`` / ``decision_timestamp``
        / ``decision_mode`` untouched and records the would-be mode in
        ``debug_plan_mode_pending`` so the dashboard can show "plan wants X,
        decision held at Y".

        ``decision_allowed=True`` is also passed by the pre-charge execution
        backstop, which is not a token grant but an in-lock override — see
        ``_precharge_backstop_mode``.
        """
        if not decision_allowed:
            # Frozen: pin the previously-decided mode, surface the pending plan.
            data.debug_plan_mode_pending = (
                new_mode.value if new_mode != data.active_mode else None
            )
            return

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
        data.debug_mode_source = mode_source
        data.debug_plan_mode_pending = None

    @staticmethod
    def _mark_mode_debug_fallback(data: CoordinatorData) -> None:
        """Mark the mode-decision debug fields as a non-optimizer fallback.

        Called from run_inline early exits so the debug_* fields never go stale
        when the optimizer did not produce a decision this tick.

        ``debug_plan_mode_pending`` is cleared with them: a stale pending left by an
        earlier tick would otherwise feed the state machine's plan-charge epoch on a
        tick where the optimizer produced no decision at all — a phantom grant.
        """
        if data.debug_plan_mode_pending in _CHARGE_MODE_VALUES:
            # Dropping a legitimately-pending charge is the correct conservative
            # call — a token must never be granted off a plan that does not exist —
            # but it costs the plan-charge trigger one cycle, so say so rather than
            # discarding it silently.
            _LOGGER.info(
                "Pending plan charge (%s) discarded: this cycle produced no "
                "optimizer decision. The trigger re-arms on the next good plan.",
                data.debug_plan_mode_pending,
            )
        data.debug_mode_source = "fallback"
        data.debug_forecast_slot_found = False
        data.debug_forecast_slot_time = ""
        data.debug_plan_mode_pending = None
        data.optimizer_precharge_backstop_active = False

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
                solcast_analysis_today=getattr(data, "solcast_analysis_today", None),
                solcast_analysis_tomorrow=getattr(
                    data, "solcast_analysis_tomorrow", None
                ),
                solar_absent_confidence=getattr(data, "solar_absent_confidence", 1.0),
            )
            result = self._planner.plan(inputs)

            # Extract shadow decision using same flow as primary
            shadow_decisions = [_serialize_decision(d) for d in result.decisions]
            if shadow_decisions:
                # Find current slot index for shadow run
                current_slot_idx = _find_current_slot_index(data)
                shadow_apply_plan = _derive_runtime_apply_plan(
                    shadow_decisions, current_slot_idx, optimizer_config
                )
                shadow_mode = shadow_apply_plan.get("battery_mode", "unknown")
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

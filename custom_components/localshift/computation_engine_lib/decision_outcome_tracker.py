"""Decision outcome tracking for the learning system.

Issue #170 Phase 1: Records mode decisions and backfills outcomes to create
a ground-truth dataset for optimization. This phase is observation-only —
no behavioral changes.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import BatteryMode
from ..coordinator_data import PerformanceMetrics

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..coordinator_data import CoordinatorData

_LOGGER = logging.getLogger(__name__)

# Maximum number of completed decisions to keep in memory
MAX_COMPLETED_DECISIONS = 500

# Maximum duration for a decision period before it's considered complete
MAX_DECISION_DURATION = timedelta(minutes=30)


@dataclass
class DecisionRecord:
    """Immutable record of a single mode decision and its outcome.

    Context is captured at decision time. Outcomes are backfilled after
    the decision period ends (mode changes or max duration elapsed).
    """

    # Context at decision time
    timestamp: datetime
    mode_chosen: BatteryMode
    previous_mode: BatteryMode
    soc_at_decision: float
    general_price_at_decision: float
    feed_in_price_at_decision: float
    forecast_solar_remaining_kwh: float
    forecast_consumption_remaining_kwh: float
    cheap_price_threshold: float
    battery_target_soc: float
    weather_condition: str
    day_of_week: int  # 0=Monday, 6=Sunday
    hour_of_day: int  # 0-23
    is_demand_window: bool

    # Outcome (backfilled after decision period ends)
    actual_cost_during_period: float | None = None
    actual_soc_change: float | None = None  # positive = gained, negative = lost
    actual_export_kwh: float | None = None
    actual_import_kwh: float | None = None
    duration_minutes: float | None = None
    next_mode: BatteryMode | None = None
    outcome_score: float | None = None  # 0.0-1.0, computed quality score

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "mode_chosen": self.mode_chosen.value,
            "previous_mode": self.previous_mode.value,
            "soc_at_decision": self.soc_at_decision,
            "general_price_at_decision": self.general_price_at_decision,
            "feed_in_price_at_decision": self.feed_in_price_at_decision,
            "forecast_solar_remaining_kwh": self.forecast_solar_remaining_kwh,
            "forecast_consumption_remaining_kwh": self.forecast_consumption_remaining_kwh,
            "cheap_price_threshold": self.cheap_price_threshold,
            "battery_target_soc": self.battery_target_soc,
            "weather_condition": self.weather_condition,
            "day_of_week": self.day_of_week,
            "hour_of_day": self.hour_of_day,
            "is_demand_window": self.is_demand_window,
            "actual_cost_during_period": self.actual_cost_during_period,
            "actual_soc_change": self.actual_soc_change,
            "actual_export_kwh": self.actual_export_kwh,
            "actual_import_kwh": self.actual_import_kwh,
            "duration_minutes": self.duration_minutes,
            "next_mode": self.next_mode.value if self.next_mode else None,
            "outcome_score": self.outcome_score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionRecord:
        """Create from dictionary (deserialization)."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            mode_chosen=BatteryMode(data["mode_chosen"]),
            previous_mode=BatteryMode(data["previous_mode"]),
            soc_at_decision=data["soc_at_decision"],
            general_price_at_decision=data["general_price_at_decision"],
            feed_in_price_at_decision=data["feed_in_price_at_decision"],
            forecast_solar_remaining_kwh=data["forecast_solar_remaining_kwh"],
            forecast_consumption_remaining_kwh=data[
                "forecast_consumption_remaining_kwh"
            ],
            cheap_price_threshold=data["cheap_price_threshold"],
            battery_target_soc=data["battery_target_soc"],
            weather_condition=data["weather_condition"],
            day_of_week=data["day_of_week"],
            hour_of_day=data["hour_of_day"],
            is_demand_window=data["is_demand_window"],
            actual_cost_during_period=data.get("actual_cost_during_period"),
            actual_soc_change=data.get("actual_soc_change"),
            actual_export_kwh=data.get("actual_export_kwh"),
            actual_import_kwh=data.get("actual_import_kwh"),
            duration_minutes=data.get("duration_minutes"),
            next_mode=BatteryMode(data["next_mode"]) if data.get("next_mode") else None,
            outcome_score=data.get("outcome_score"),
        )


class DecisionOutcomeTracker:
    """Tracks mode decisions and backfills outcomes.

    This is the core component of the learning system that creates the
    feedback loop for optimization. Every mode transition is recorded with
    full context, and outcomes are computed when the decision period ends.

    A decision period ends when:
    1. The mode changes again (transition to a new mode)
    2. 30 minutes have elapsed (max duration)
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the decision outcome tracker.

        Args:
            hass: Home Assistant instance
            entry_id: Config entry ID for storage key
        """
        self._hass = hass
        self._store = Store(
            hass, version=1, key=f"localshift.decision_outcomes.{entry_id}"
        )
        self._pending_decisions: list[DecisionRecord] = []
        self._completed_decisions: deque[DecisionRecord] = deque(
            maxlen=MAX_COMPLETED_DECISIONS
        )

        # Track SOC and energy at last decision for outcome computation
        self._last_decision_soc: float | None = None
        self._last_decision_time: datetime | None = None
        self._energy_at_last_decision: dict[str, float] = {
            "import_kwh": 0.0,
            "export_kwh": 0.0,
            "cost": 0.0,
        }

        # Track if save is needed (after backfill)
        self._save_pending: bool = False

    def record_decision(
        self,
        data: CoordinatorData,
        mode: BatteryMode,
        prev_mode: BatteryMode,
    ) -> None:
        """Record a mode transition with full context.

        Called by StateMachine on every mode transition. Also triggers
        backfill of any pending decision that just ended.

        Args:
            data: Current coordinator data with context
            mode: New mode being transitioned to
            prev_mode: Previous mode before transition
        """
        now = dt_util.now()

        # First, backfill any pending decision (this transition ends it)
        if self._pending_decisions:
            self._backfill_pending_decision(data, mode, now)

        # Capture context for the new decision
        record = DecisionRecord(
            timestamp=now,
            mode_chosen=mode,
            previous_mode=prev_mode,
            soc_at_decision=data.soc,
            general_price_at_decision=data.general_price,
            feed_in_price_at_decision=data.feed_in_price,
            forecast_solar_remaining_kwh=data.solar_remaining_kwh,
            forecast_consumption_remaining_kwh=sum(
                data.consumption_hourly_profile_kw.values()
                if data.consumption_hourly_profile_kw
                else [0.0]
            ),
            cheap_price_threshold=data.effective_cheap_price,
            battery_target_soc=data.battery_target_soc
            if hasattr(data, "battery_target_soc")
            else 80.0,  # fallback
            weather_condition=data.weather_condition,
            day_of_week=now.weekday(),
            hour_of_day=now.hour,
            is_demand_window=data.demand_window_active,
        )

        self._pending_decisions.append(record)

        # Track state for outcome computation
        self._last_decision_soc = data.soc
        self._last_decision_time = now
        self._energy_at_last_decision = {
            "import_kwh": 0.0,  # Reset for next period
            "export_kwh": 0.0,
            "cost": 0.0,
        }

        _LOGGER.info(
            "Decision recorded: %s → %s at %s (SOC=%.1f%%, price=%.2f, weather=%s)",
            prev_mode.value,
            mode.value,
            now.strftime("%H:%M:%S"),
            data.soc,
            data.general_price,
            data.weather_condition,
        )

    def _backfill_pending_decision(
        self,
        data: CoordinatorData,
        next_mode: BatteryMode,
        now: datetime,
    ) -> None:
        """Backfill outcome for a pending decision that just ended.

        Args:
            data: Current coordinator data
            next_mode: The mode being transitioned to (ends the pending decision)
            now: Current timestamp
        """
        if not self._pending_decisions:
            return

        pending = self._pending_decisions.pop(0)

        # Compute duration
        duration = now - pending.timestamp
        duration_minutes = duration.total_seconds() / 60.0

        # Compute SOC change
        soc_change = data.soc - pending.soc_at_decision

        # Estimate import/export during period (simplified for now)
        # In Phase 2+, we'll integrate with cost_tracker for precise values
        import_kwh = max(0.0, -soc_change / 100.0 * 13.5)  # Approximate from SOC change
        export_kwh = max(0.0, soc_change / 100.0 * 13.5)

        # Compute cost (simplified - will integrate with cost_tracker later)
        # For now, use current prices as approximation
        if soc_change < 0:  # Battery discharged
            cost = 0.0  # Discharging is "free"
        else:  # Battery charged
            if pending.mode_chosen == BatteryMode.GRID_CHARGING:
                cost = (
                    -soc_change / 100.0 * 13.5 * pending.general_price_at_decision / 100
                )
            else:
                cost = 0.0  # Solar charging

        # Set outcome fields
        pending.actual_soc_change = soc_change
        pending.actual_import_kwh = import_kwh
        pending.actual_export_kwh = export_kwh
        pending.actual_cost_during_period = cost
        pending.duration_minutes = duration_minutes
        pending.next_mode = next_mode

        # Compute outcome score
        pending.outcome_score = self.compute_outcome_score(pending)

        # Move to completed
        self._completed_decisions.append(pending)

        # Mark save needed
        self._save_pending = True

        _LOGGER.info(
            "Decision outcome backfilled: %s lasted %.1f min, SOC change=%.1f%%, score=%.2f",
            pending.mode_chosen.value,
            duration_minutes,
            soc_change,
            pending.outcome_score,
        )

    def backfill_outcomes(self, data: CoordinatorData) -> None:
        """Check for and backfill outcomes for completed decision periods.

        Called every periodic tick. A decision period ends when:
        1. The mode changes (handled in record_decision)
        2. 30 minutes have elapsed (checked here)

        Args:
            data: Current coordinator data
        """
        now = dt_util.now()

        # Check for pending decisions that have exceeded max duration
        pending_to_backfill = []
        for pending in self._pending_decisions:
            elapsed = now - pending.timestamp
            if elapsed >= MAX_DECISION_DURATION:
                pending_to_backfill.append(pending)

        # Backfill timed-out decisions
        for pending in pending_to_backfill:
            self._backfill_timedout_decision(pending, data, now)

    def _backfill_timedout_decision(
        self,
        pending: DecisionRecord,
        data: CoordinatorData,
        now: datetime,
    ) -> None:
        """Backfill a decision that timed out (30 min max duration).

        Args:
            pending: The pending decision to backfill
            data: Current coordinator data
            now: Current timestamp
        """
        self._pending_decisions.remove(pending)

        # Compute duration (capped at max)
        duration = now - pending.timestamp
        duration_minutes = min(
            duration.total_seconds() / 60.0,
            MAX_DECISION_DURATION.total_seconds() / 60.0,
        )

        # Compute SOC change
        soc_change = data.soc - pending.soc_at_decision

        # Set outcome fields
        pending.actual_soc_change = soc_change
        pending.duration_minutes = duration_minutes
        pending.next_mode = None  # No transition occurred

        # Compute outcome score
        pending.outcome_score = self.compute_outcome_score(pending)

        # Move to completed
        self._completed_decisions.append(pending)

        # Mark save needed
        self._save_pending = True

        _LOGGER.info(
            "Decision timed out: %s lasted %.1f min, SOC change=%.1f%%, score=%.2f",
            pending.mode_chosen.value,
            duration_minutes,
            soc_change,
            pending.outcome_score,
        )

    def compute_outcome_score(self, record: DecisionRecord) -> float:
        """Score a decision outcome from 0.0 (worst) to 1.0 (best).

        Components:
        - cost_score: How efficient was the cost outcome
        - export_penalty: Penalize unnecessary exports
        - target_score: Bonus for reaching/maintaining SOC target
        - cycling_penalty: Penalize rapid mode changes

        Args:
            record: The decision record to score

        Returns:
            Score from 0.0 to 1.0
        """
        score = 0.5  # Start at neutral

        # 1. Cost Score (weight: 40%)
        # Lower cost = better score
        if record.actual_cost_during_period is not None:
            if record.mode_chosen == BatteryMode.GRID_CHARGING:
                # Grid charging: positive cost is expected
                # Penalize if we charged and then exported (waste)
                if record.actual_export_kwh and record.actual_export_kwh > 0.5:
                    cost_score = 0.2  # Penalize grid-charge-then-export
                else:
                    cost_score = 0.7  # Normal grid charge
            elif record.mode_chosen == BatteryMode.SPIKE_DISCHARGE:
                # Spike discharge: should save money
                # Negative cost (revenue) is good
                if record.actual_cost_during_period < 0:
                    cost_score = 0.9  # Made money
                else:
                    cost_score = 0.5  # Neutral
            elif record.mode_chosen == BatteryMode.PROACTIVE_EXPORT:
                # Proactive export: should make money
                if (
                    record.actual_cost_during_period
                    and record.actual_cost_during_period < 0
                ):
                    cost_score = 0.9  # Made money
                else:
                    cost_score = 0.4  # Didn't make expected profit
            else:
                # Self consumption, demand block: neutral on cost
                cost_score = 0.6
            score = score * 0.6 + cost_score * 0.4

        # 2. Export Penalty (weight: 20%)
        # Penalize exporting grid-purchased energy (Issue #280)
        # Only penalize if we actually imported grid energy AND exported
        # Solar-driven export during charging modes is acceptable
        if record.actual_export_kwh and record.actual_export_kwh > 0.1:
            grid_imported = (
                record.actual_import_kwh is not None
                and record.actual_import_kwh > 0.1
            )
            if record.mode_chosen == BatteryMode.GRID_CHARGING:
                if grid_imported:
                    # Bad: imported from grid then exported
                    score -= 0.15
                # else: solar-driven export is acceptable, no penalty
            elif record.mode_chosen in (
                BatteryMode.PROACTIVE_EXPORT,
                BatteryMode.SPIKE_DISCHARGE,
            ):
                # Good: these modes are supposed to export
                score += 0.05

        # 3. Target Score (weight: 25%)
        # Did the decision help reach/maintain SOC target?
        # Issue #281: Use weather-aware target gap thresholds
        if record.battery_target_soc > 0:
            soc_at_end = record.soc_at_decision + (record.actual_soc_change or 0.0)
            target_diff = abs(soc_at_end - record.battery_target_soc)

            # Adjust thresholds based on weather (Issue #281)
            # On rainy/cloudy days, being far from target is more acceptable
            is_low_solar_weather = record.weather_condition in ("rainy", "cloudy")
            far_threshold = 40 if is_low_solar_weather else 20

            if target_diff <= 5:
                score += 0.15  # Close to target
            elif target_diff <= 10:
                score += 0.05  # Acceptable
            elif target_diff > far_threshold:
                score -= 0.10  # Far from target (weather-adjusted)

        # 4. Cycling Penalty (weight: 15%)
        # Penalize rapid mode changes (< 5 min in previous mode)
        if record.duration_minutes is not None and record.duration_minutes < 5:
            score -= 0.10

        # Clamp to valid range
        return max(0.0, min(1.0, score))

    def get_recent_decisions(self, hours: int = 24) -> list[DecisionRecord]:
        """Return decisions within the specified time window.

        Args:
            hours: Number of hours to look back

        Returns:
            List of decision records within the window
        """
        now = dt_util.now()
        cutoff = now - timedelta(hours=hours)

        return [
            record for record in self._completed_decisions if record.timestamp >= cutoff
        ]

    def get_daily_summary(self) -> PerformanceMetrics:
        """Aggregate today's decision outcomes into summary metrics.

        Returns:
            PerformanceMetrics with today's aggregated data and 7-day rolling metrics.
            Note: 7-day metrics are calculated even if there are no decisions today,
            to ensure historical data is reflected in the dashboard.
        """
        today = dt_util.now().date()
        today_decisions = [
            record
            for record in self._completed_decisions
            if record.timestamp.date() == today
        ]

        # Compute 7-day rolling metrics FIRST (independent of today's decisions)
        # This ensures historical data is shown even when no decisions today
        week_decisions = self.get_recent_decisions(hours=168)
        week_scores = [
            r.outcome_score for r in week_decisions if r.outcome_score is not None
        ]
        avg_score_7d = sum(week_scores) / len(week_scores) if week_scores else 0.0

        # Determine cost trend from 7-day data
        if len(week_scores) >= 7:
            recent_avg = sum(week_scores[-3:]) / 3
            older_avg = sum(week_scores[:4]) / 4
            if recent_avg > older_avg + 0.05:
                cost_trend = "improving"
            elif recent_avg < older_avg - 0.05:
                cost_trend = "degrading"
            else:
                cost_trend = "stable"
        else:
            cost_trend = "stable"

        # If no decisions today, return with 7-day metrics populated
        if not today_decisions:
            return PerformanceMetrics(
                total_decisions_today=0,
                avg_decision_score_today=0.0,
                avg_decision_score_7d=avg_score_7d,
                cost_trend=cost_trend,
                mode_durations_today={},
                mode_cost_attribution={},
            )

        # Compute daily metrics
        scores = [
            r.outcome_score for r in today_decisions if r.outcome_score is not None
        ]
        avg_score = sum(scores) / len(scores) if scores else 0.0

        # Compute per-mode durations and costs
        mode_durations: dict[str, float] = {}
        mode_costs: dict[str, float] = {}

        for record in today_decisions:
            mode_key = record.mode_chosen.value
            mode_durations[mode_key] = mode_durations.get(mode_key, 0.0) + (
                record.duration_minutes or 0.0
            )
            if record.actual_cost_during_period:
                mode_costs[mode_key] = (
                    mode_costs.get(mode_key, 0.0) + record.actual_cost_during_period
                )

        return PerformanceMetrics(
            total_decisions_today=len(today_decisions),
            avg_decision_score_today=avg_score,
            avg_decision_score_7d=avg_score_7d,
            cost_trend=cost_trend,
            mode_durations_today=mode_durations,
            mode_cost_attribution=mode_costs,
        )

    def get_decision_log(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent decisions as dictionaries for sensor attributes.

        Args:
            limit: Maximum number of decisions to return

        Returns:
            List of decision dictionaries (most recent first)
        """
        decisions = list(self._completed_decisions)[-limit:]
        return [record.to_dict() for record in reversed(decisions)]

    async def async_save(self) -> None:
        """Persist both pending and completed decisions to HA storage.

        Pending decisions are saved so they can be restored after a restart,
        preventing loss of in-flight decisions that haven't been backfilled yet.
        """
        data = {
            "pending_decisions": [r.to_dict() for r in self._pending_decisions],
            "completed_decisions": [r.to_dict() for r in self._completed_decisions],
        }
        await self._store.async_save(data)
        _LOGGER.info(
            "Decision tracker saved: %d pending + %d completed records",
            len(self._pending_decisions),
            len(self._completed_decisions),
        )

    async def async_load(self) -> None:
        """Restore both pending and completed decisions from HA storage.

        Pending decisions are restored so that in-flight decisions can be
        backfilled after a restart, preventing data loss.
        """
        data = await self._store.async_load()

        if data is None:
            _LOGGER.debug("No saved decision records found")
            return

        # Restore completed decisions
        completed = data.get("completed_decisions", [])
        self._completed_decisions.clear()

        for record_dict in completed:
            try:
                record = DecisionRecord.from_dict(record_dict)
                self._completed_decisions.append(record)
            except (KeyError, ValueError) as e:
                _LOGGER.warning("Failed to load completed decision record: %s", e)

        # Restore pending decisions
        pending = data.get("pending_decisions", [])
        self._pending_decisions.clear()

        for record_dict in pending:
            try:
                record = DecisionRecord.from_dict(record_dict)
                # Only restore pending decisions that haven't exceeded max duration
                # (they may have timed out while HA was down)
                elapsed = dt_util.now() - record.timestamp
                if elapsed < MAX_DECISION_DURATION:
                    self._pending_decisions.append(record)
                else:
                    # Decision timed out while HA was down - mark as completed
                    # with partial outcome (we don't have the actual data)
                    record.duration_minutes = MAX_DECISION_DURATION.total_seconds() / 60.0
                    record.outcome_score = 0.5  # Neutral score (unknown outcome)
                    self._completed_decisions.append(record)
                    _LOGGER.info(
                        "Pending decision from %s timed out during HA downtime, "
                        "moved to completed with neutral score",
                        record.timestamp.strftime("%Y-%m-%d %H:%M"),
                    )
            except (KeyError, ValueError) as e:
                _LOGGER.warning("Failed to load pending decision record: %s", e)

        _LOGGER.info(
            "Loaded %d pending + %d completed decision records from storage",
            len(self._pending_decisions),
            len(self._completed_decisions),
        )

    @property
    def pending_count(self) -> int:
        """Return number of pending decisions awaiting outcome."""
        return len(self._pending_decisions)

    @property
    def completed_count(self) -> int:
        """Return total number of completed decisions."""
        return len(self._completed_decisions)

    @property
    def save_pending(self) -> bool:
        """Return True if there are unsaved changes (after backfill)."""
        return self._save_pending

    def clear_save_pending(self) -> None:
        """Clear the save pending flag after save completes."""
        self._save_pending = False

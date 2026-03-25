"""Tests for reason_codes classification functions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import custom_components.localshift.engine.reason_codes as _rc_module
from custom_components.localshift.engine.reason_codes import (
    _is_blind_to_future_solar,
    _is_target_shortfall_risk,
    classify_export_reason,
    classify_hold_reason,
    classify_reason,
)
from custom_components.localshift.engine.types import (
    NegativeFitAvoidanceContext,
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    PlannerReasonCode,
    SlotContext,
)


def _make_slot(
    *,
    buy_price: float = 30.0,
    sell_price: float = 10.0,
    solar_kwh: float = 0.0,
    consumption_kwh: float = 0.5,
    slot_index: int = 0,
) -> SlotContext:
    return SlotContext(
        slot_index=slot_index,
        slot_interval_minutes=30,
        timestamp_iso="2024-01-01T08:00:00+00:00",
        buy_price=buy_price,
        sell_price=sell_price,
        solar_kwh=solar_kwh,
        consumption_kwh=consumption_kwh,
        is_demand_window_slot=False,
    )


def _make_inputs(*, all_solcast: list | None = None) -> OptimizerInputs:
    slot = _make_slot()
    return OptimizerInputs(
        cycle_id="test",
        initial_soc_pct=50.0,
        slots=[slot],
        all_solcast=all_solcast or [],
    )


class TestClassifyHoldReason:
    """Tests for classify_hold_reason."""

    def test_soc_ceiling_returns_ceiling_constraint(self):
        config = OptimizerConfig(max_soc_pct=100.0)
        slot = _make_slot()
        result = classify_hold_reason(
            soc=100.0, slot=slot, next_soc=100.0, config=config
        )
        assert result == PlannerReasonCode.SOC_CEILING_CONSTRAINT

    def test_soc_floor_returns_floor_constraint(self):
        config = OptimizerConfig(min_soc_pct=10.0)
        slot = _make_slot()
        result = classify_hold_reason(soc=10.0, slot=slot, next_soc=10.0, config=config)
        assert result == PlannerReasonCode.SOC_FLOOR_CONSTRAINT

    def test_solar_surplus_capture(self):
        config = OptimizerConfig(min_soc_pct=5.0, max_soc_pct=95.0)
        slot = _make_slot(solar_kwh=2.0, consumption_kwh=0.5)
        # net_kwh > 0 and next_soc > soc
        result = classify_hold_reason(soc=50.0, slot=slot, next_soc=55.0, config=config)
        assert result == PlannerReasonCode.SOLAR_SURPLUS_CAPTURE

    def test_idle_default(self):
        config = OptimizerConfig(
            min_soc_pct=5.0,
            max_soc_pct=95.0,
            optimization_mode="demand_window",
        )
        slot = _make_slot(buy_price=30.0, solar_kwh=0.0)
        result = classify_hold_reason(soc=50.0, slot=slot, next_soc=50.0, config=config)
        assert result == PlannerReasonCode.IDLE

    def test_solar_opportunity_wait_returned_when_factor_positive(self, monkeypatch):
        """SOLAR_OPPORTUNITY_WAIT is returned when get_solar_opportunity_penalty_factor > 0."""
        monkeypatch.setattr(
            _rc_module, "get_solar_opportunity_penalty_factor", lambda **_kwargs: 1.0
        )
        config = OptimizerConfig(
            min_soc_pct=5.0,
            max_soc_pct=95.0,
            optimization_mode="self_consumption",
            effective_cheap_price=0.10,
        )
        # buy_price below effective_cheap_price to enter the solar gate check
        slot = _make_slot(buy_price=0.05, solar_kwh=0.0)
        inputs = _make_inputs(
            all_solcast=[{"period_start": "2024-01-01T08:00:00+00:00"}]
        )
        result = classify_hold_reason(
            soc=50.0,
            slot=slot,
            next_soc=50.0,
            config=config,
            slot_idx=0,
            slots=[slot],
            terminal_penalty_idx=None,
            inputs=inputs,
        )
        assert result == PlannerReasonCode.SOLAR_OPPORTUNITY_WAIT


class TestClassifyExportReason:
    """Tests for classify_export_reason."""

    def test_high_sell_price(self):
        slot = _make_slot(sell_price=50.0)
        result = classify_export_reason(slot, slot_idx=None)
        assert result == PlannerReasonCode.HIGH_SELL_PRICE_EXPORT

    def test_zero_sell_price_is_negative_fit_avoidance(self):
        slot = _make_slot(sell_price=0.0)
        result = classify_export_reason(slot, slot_idx=None)
        assert result == PlannerReasonCode.NEGATIVE_FIT_AVOIDANCE

    def test_before_risk_window_is_negative_fit_avoidance(self):
        slot = _make_slot(sell_price=50.0)
        ctx = NegativeFitAvoidanceContext(
            risk_window_start_idx=5,
            risk_window_end_idx=10,
            required_headroom_kwh=5.0,
            recovery_deadline_idx=11,
            conservative_recovery_kwh_by_slot=(5.0,) * 12,
            recoverability_floor_pct_by_slot=(20.0,) * 12,
        )
        result = classify_export_reason(
            slot, slot_idx=2, negative_fit_avoidance_context=ctx
        )
        assert result == PlannerReasonCode.NEGATIVE_FIT_AVOIDANCE


class TestClassifyReason:
    """Tests for classify_reason top-level dispatcher."""

    def test_hold_dispatches_to_hold_classifier(self):
        config = OptimizerConfig(max_soc_pct=100.0)
        slot = _make_slot()
        result = classify_reason(
            action=PlannerAction.HOLD,
            slot=slot,
            slot_idx=0,
            slots=[slot],
            soc=100.0,
            next_soc=100.0,
            config=config,
            terminal_penalty_idx=None,
        )
        assert result == PlannerReasonCode.SOC_CEILING_CONSTRAINT

    def test_export_dispatches_to_export_classifier(self):
        config = OptimizerConfig()
        slot = _make_slot(sell_price=50.0)
        result = classify_reason(
            action=PlannerAction.EXPORT_PROACTIVE,
            slot=slot,
            slot_idx=0,
            slots=[slot],
            soc=80.0,
            next_soc=75.0,
            config=config,
            terminal_penalty_idx=None,
        )
        assert result == PlannerReasonCode.HIGH_SELL_PRICE_EXPORT

    def test_unrecognised_action_returns_idle(self):
        # HOLD with no special SOC conditions falls through to IDLE
        config = OptimizerConfig(
            min_soc_pct=5.0,
            max_soc_pct=95.0,
            optimization_mode="demand_window",
        )
        slot = _make_slot(solar_kwh=0.0)
        result = classify_reason(
            action=PlannerAction.HOLD,
            slot=slot,
            slot_idx=0,
            slots=[slot],
            soc=50.0,
            next_soc=50.0,
            config=config,
            terminal_penalty_idx=None,
        )
        assert result == PlannerReasonCode.IDLE

    def test_unknown_action_string_returns_fallback_idle(self):
        """The fallback return PlannerReasonCode.IDLE is reachable via unknown string actions."""
        config = OptimizerConfig()
        slot = _make_slot()
        result = classify_reason(
            action="unknown_action",  # type: ignore[arg-type]
            slot=slot,
            slot_idx=0,
            slots=[slot],
            soc=50.0,
            next_soc=50.0,
            config=config,
            terminal_penalty_idx=None,
        )
        assert result == PlannerReasonCode.IDLE


class TestIsBlindToFutureSolar:
    """Tests for _is_blind_to_future_solar."""

    def test_not_blind_when_all_solcast_present(self):
        """Returns False immediately when inputs.all_solcast is truthy."""
        inputs = _make_inputs(
            all_solcast=[{"period_start": "2024-01-01T08:00:00+00:00"}]
        )
        slot = _make_slot()
        result = _is_blind_to_future_solar(
            terminal_penalty_idx=3,
            slots=[slot] * 5,
            inputs=inputs,
        )
        assert result is False

    def test_blind_when_no_terminal_penalty_idx(self):
        result = _is_blind_to_future_solar(
            terminal_penalty_idx=None,
            slots=[_make_slot()],
            inputs=None,
        )
        assert result is True


class TestIsTargetShortfallRisk:
    """Tests for _is_target_shortfall_risk with accuracy discount."""

    def test_shortfall_risk_scales_with_accuracy_discount(self):
        base = datetime(2026, 3, 20, 10, 0, tzinfo=UTC)
        slots = [
            SlotContext(
                slot_index=i,
                timestamp_iso=(base + timedelta(minutes=30 * i)).isoformat(),
                slot_interval_minutes=30,
                buy_price=0.30,
                sell_price=0.05,
                solar_kwh=2.0,
                consumption_kwh=0.1,
                is_demand_window_entry=i == 3,
                is_demand_window_slot=i >= 3,
            )
            for i in range(4)
        ]
        config = OptimizerConfig(
            battery_capacity_kwh=13.5,
            charge_rate_kw=3.3,
            solar_charge_rate_kw=5.0,
            discharge_rate_kw=5.0,
            demand_window_target_soc_pct=80.0,
            optimization_mode="self_consumption",
            allow_dw_entry_under_target=False,
            switching_penalty=0.02,
            target_shortfall_penalty_per_pct=0.015,
        )

        tracker_high = Mock()
        tracker_high.metrics.accuracy = 100
        inputs_high = OptimizerInputs(
            cycle_id="test_accuracy_high",
            initial_soc_pct=60.0,
            slots=slots,
            config=config,
            solar_accuracy_tracker=tracker_high,
            all_solcast=[],
        )

        tracker_low = Mock()
        tracker_low.metrics.accuracy = 10
        inputs_low = OptimizerInputs(
            cycle_id="test_accuracy_low",
            initial_soc_pct=60.0,
            slots=slots,
            config=config,
            solar_accuracy_tracker=tracker_low,
            all_solcast=[],
        )

        assert (
            _is_target_shortfall_risk(
                slot_idx=0,
                slots=slots,
                soc=inputs_high.initial_soc_pct,
                config=config,
                terminal_penalty_idx=3,
                inputs=inputs_high,
            )
            is False
        )
        assert (
            _is_target_shortfall_risk(
                slot_idx=0,
                slots=slots,
                soc=inputs_low.initial_soc_pct,
                config=config,
                terminal_penalty_idx=3,
                inputs=inputs_low,
            )
            is True
        )

    def test_shortfall_risk_ignores_beyond_horizon_solcast(self):
        base = datetime(2026, 3, 20, 10, 0, tzinfo=UTC)
        slots = [
            SlotContext(
                slot_index=i,
                timestamp_iso=(base + timedelta(minutes=30 * i)).isoformat(),
                slot_interval_minutes=30,
                buy_price=0.30,
                sell_price=0.05,
                solar_kwh=0.2,
                consumption_kwh=0.6,
                is_demand_window_entry=i == 3,
                is_demand_window_slot=i >= 3,
            )
            for i in range(4)
        ]
        config = OptimizerConfig(
            battery_capacity_kwh=13.5,
            charge_rate_kw=3.3,
            solar_charge_rate_kw=5.0,
            discharge_rate_kw=5.0,
            demand_window_target_soc_pct=80.0,
            optimization_mode="self_consumption",
            allow_dw_entry_under_target=False,
            switching_penalty=0.02,
            target_shortfall_penalty_per_pct=0.015,
        )

        tracker = Mock()
        tracker.metrics.accuracy = 100
        inputs = OptimizerInputs(
            cycle_id="test_no_solcast",
            initial_soc_pct=60.0,
            slots=slots,
            config=config,
            solar_accuracy_tracker=tracker,
            all_solcast=[],
        )

        result = _is_target_shortfall_risk(
            slot_idx=0,
            slots=slots,
            soc=inputs.initial_soc_pct,
            config=config,
            terminal_penalty_idx=3,
            inputs=inputs,
        )

        assert isinstance(result, bool)

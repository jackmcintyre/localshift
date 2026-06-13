from datetime import datetime
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.sensors.status import (
    AutomationReadySensor,
    DecisionLagSensor,
    EntityHealthSensor,
    ForecastAccuracySensor,
    ForecastStatusSensor,
    IntegrationStatusSensor,
)


def _sensor(cls, **data_attrs):
    coordinator = MagicMock()
    entry = MagicMock()
    for key, val in data_attrs.items():
        setattr(coordinator.data, key, val)
    return cls(coordinator, entry)


class TestIntegrationStatusSensor:
    def test_import(self):
        assert IntegrationStatusSensor is not None

    def test_update_from_coordinator(self):
        sensor = _sensor(IntegrationStatusSensor, integration_status="ok")
        sensor._update_from_coordinator()
        assert sensor._attr_native_value == "ok"

    def test_extra_state_attributes(self):
        sensor = _sensor(
            IntegrationStatusSensor,
            integration_status="ok",
            integration_status_message="All good",
            entity_errors=[],
            entity_warnings=[],
            required_entities_healthy=True,
            last_entity_check="2026-03-12T12:00:00",
        )
        attrs = sensor.extra_state_attributes
        assert attrs["message"] == "All good"
        assert attrs["required_entities_healthy"] is True
        assert attrs["error_count"] == 0

    def test_icon_ok(self):
        sensor = _sensor(IntegrationStatusSensor)
        sensor._attr_native_value = "ok"
        assert sensor.icon == "mdi:check-circle"

    def test_icon_degraded(self):
        sensor = _sensor(IntegrationStatusSensor)
        sensor._attr_native_value = "degraded"
        assert sensor.icon == "mdi:alert-circle"

    def test_icon_other(self):
        sensor = _sensor(IntegrationStatusSensor)
        sensor._attr_native_value = "error"
        assert sensor.icon == "mdi:close-circle"


class TestEntityHealthSensor:
    def test_update_from_coordinator(self):
        sensor = _sensor(
            EntityHealthSensor,
            entity_health={
                "sensor.a": {"status": "ok"},
                "sensor.b": {"status": "error"},
            },
            localshift_entity_health={"sensor.c": {"status": "ok"}},
        )
        sensor._update_from_coordinator()
        assert sensor._attr_native_value == "2/3"

    def test_extra_state_attributes(self):
        dep_health = {"sensor.a": {"status": "ok", "category": "required"}}
        ls_health = {"sensor.b": {"status": "ok", "category": "optional"}}
        sensor = _sensor(
            EntityHealthSensor,
            entity_health=dep_health,
            localshift_entity_health=ls_health,
            orphaned_localshift_entities={},
            entity_errors=[],
            entity_warnings=[],
        )
        attrs = sensor.extra_state_attributes
        assert "entities" in attrs
        assert "summary" in attrs
        assert attrs["summary"]["dependencies"]["total"] == 1
        assert attrs["summary"]["localshift"]["healthy"] == 1
        assert attrs["summary"]["orphaned_count"] == 0

    def test_unrecorded_attributes_excludes_large_dicts(self):
        """Test that large entity dicts are excluded from recorder.

        Issue #467: Entity health dicts can exceed 16KB limit.
        """
        sensor = _sensor(
            EntityHealthSensor,
            entity_health={},
            localshift_entity_health={},
            orphaned_localshift_entities={},
        )

        assert hasattr(sensor, "_unrecorded_attributes")
        assert "entities" in sensor._unrecorded_attributes
        assert "dependencies" in sensor._unrecorded_attributes
        assert "localshift_entities" in sensor._unrecorded_attributes
        assert "orphaned_entities" in sensor._unrecorded_attributes
        assert "errors" in sensor._unrecorded_attributes
        assert "warnings" in sensor._unrecorded_attributes

    def test_orphaned_entities_exposed_in_attributes(self):
        """Orphaned registry entries are surfaced in orphaned_entities attribute."""
        orphans = {
            "number.localshift_cycle_penalty": {
                "state": "unavailable",
                "disabled": False,
                "restored": True,
            }
        }
        sensor = _sensor(
            EntityHealthSensor,
            entity_health={},
            localshift_entity_health={},
            orphaned_localshift_entities=orphans,
            entity_errors=[],
            entity_warnings=[
                "1 orphaned localshift entity: number.localshift_cycle_penalty"
            ],
        )
        attrs = sensor.extra_state_attributes
        assert attrs["orphaned_entities"] == orphans
        assert attrs["summary"]["orphaned_count"] == 1

    def test_no_orphans_yields_empty_orphaned_entities(self):
        """When no orphans exist, orphaned_entities is empty and count is 0."""
        sensor = _sensor(
            EntityHealthSensor,
            entity_health={"sensor.a": {"status": "ok"}},
            localshift_entity_health={},
            orphaned_localshift_entities={},
            entity_errors=[],
            entity_warnings=[],
        )
        attrs = sensor.extra_state_attributes
        assert attrs["orphaned_entities"] == {}
        assert attrs["summary"]["orphaned_count"] == 0


class TestForecastAccuracySensor:
    def test_update_with_value(self):
        sensor = _sensor(ForecastAccuracySensor, forecast_accuracy_soc_1h=95.5)
        sensor._update_from_coordinator()
        assert sensor._attr_native_value == pytest.approx(95.5)

    def test_update_with_none(self):
        sensor = _sensor(ForecastAccuracySensor, forecast_accuracy_soc_1h=None)
        sensor._update_from_coordinator()
        assert sensor._attr_native_value is None

    def test_extra_state_attributes(self):
        sensor = _sensor(
            ForecastAccuracySensor,
            forecast_error_soc_15min=1.0,
            forecast_error_soc_1h=2.0,
            forecast_error_soc_4h=3.0,
            forecast_accuracy_soc_15min=99.0,
            forecast_accuracy_soc_1h=98.0,
            forecast_accuracy_soc_4h=97.0,
            forecast_error_buy_price_1h=0.001,
            forecast_error_sell_price_1h=0.002,
            forecast_comparisons_made=5,
            forecast_last_comparison_time="2026-03-12T12:00:00",
            forecast_first_prediction_time="2026-03-12T08:00:00",
            forecast_history_count=10,
        )
        attrs = sensor.extra_state_attributes
        assert attrs["soc_error_15min"] == pytest.approx(1.0)
        assert attrs["comparisons_made"] == 5
        assert attrs["soc_accuracy_1h"] == pytest.approx(98.0)

    def test_extra_state_attributes_none_accuracy(self):
        sensor = _sensor(
            ForecastAccuracySensor,
            forecast_error_soc_15min=0.0,
            forecast_error_soc_1h=0.0,
            forecast_error_soc_4h=0.0,
            forecast_accuracy_soc_15min=None,
            forecast_accuracy_soc_1h=None,
            forecast_accuracy_soc_4h=None,
            forecast_error_buy_price_1h=0.0,
            forecast_error_sell_price_1h=0.0,
            forecast_comparisons_made=0,
            forecast_last_comparison_time=None,
            forecast_first_prediction_time=None,
            forecast_history_count=0,
        )
        attrs = sensor.extra_state_attributes
        assert attrs["soc_accuracy_15min"] is None
        assert attrs["soc_accuracy_1h"] is None
        assert attrs["soc_accuracy_4h"] is None


class TestForecastStatusSensor:
    def test_update_from_coordinator(self):
        sensor = _sensor(ForecastStatusSensor, forecast_status="ready")
        sensor._update_from_coordinator()
        assert sensor._attr_native_value == "ready"

    def test_extra_state_attributes(self):
        sensor = _sensor(
            ForecastStatusSensor,
            forecast_ready=True,
            solcast_today=[1, 2, 3],
            solcast_tomorrow=[4, 5],
            debug_mode_source="optimizer",
        )
        attrs = sensor.extra_state_attributes
        assert attrs["solcast_today_entries"] == 3
        assert attrs["forecast_ready"] is True

    def test_icon_ready(self):
        sensor = _sensor(ForecastStatusSensor)
        sensor._attr_native_value = "ready"
        assert sensor.icon == "mdi:check-circle"

    def test_icon_partial(self):
        sensor = _sensor(ForecastStatusSensor)
        sensor._attr_native_value = "partial"
        assert sensor.icon == "mdi:alert-circle"

    def test_icon_stale(self):
        sensor = _sensor(ForecastStatusSensor)
        sensor._attr_native_value = "stale"
        assert sensor.icon == "mdi:close-circle"

    def test_icon_other(self):
        sensor = _sensor(ForecastStatusSensor)
        sensor._attr_native_value = "unknown"
        assert sensor.icon == "mdi:weather-sunny-alert"


class TestAutomationReadySensor:
    def test_update_ready(self):
        sensor = _sensor(AutomationReadySensor, automation_ready=True)
        sensor._update_from_coordinator()
        assert sensor._attr_native_value == "ready"

    def test_update_not_ready(self):
        sensor = _sensor(AutomationReadySensor, automation_ready=False)
        sensor._update_from_coordinator()
        assert sensor._attr_native_value == "not_ready"

    def test_extra_state_attributes(self):
        sensor = _sensor(
            AutomationReadySensor,
            automation_ready=True,
            automation_ready_status={"check1": True},
            automation_ready_missing=[],
            soc=80.0,
            operation_mode="auto",
            backup_reserve=20.0,
            prices_available=True,
            forecast_status="ready",
        )
        attrs = sensor.extra_state_attributes
        assert attrs["automation_ready"] is True
        assert attrs["soc"] == 80.0

    def test_icon_ready(self):
        sensor = _sensor(AutomationReadySensor)
        sensor._attr_native_value = "ready"
        assert sensor.icon == "mdi:check-decagram"

    def test_icon_not_ready(self):
        sensor = _sensor(AutomationReadySensor)
        sensor._attr_native_value = "not_ready"
        assert sensor.icon == "mdi:decagram-outline"


class TestDecisionLagSensor:
    def test_update_with_value(self):
        sensor = _sensor(DecisionLagSensor, decision_lag_seconds=1.234)
        sensor._update_from_coordinator()
        assert sensor._attr_native_value == pytest.approx(1.23)

    def test_update_with_none(self):
        sensor = _sensor(DecisionLagSensor, decision_lag_seconds=None)
        sensor._update_from_coordinator()
        assert sensor._attr_native_value is None

    def test_extra_state_attributes_no_history(self):
        sensor = _sensor(
            DecisionLagSensor,
            decision_lag_seconds=None,
            decision_lag_history=[],
            decision_timestamp=None,
            implementation_timestamp=None,
        )
        attrs = sensor.extra_state_attributes
        assert attrs["avg_lag_24h"] is None
        assert attrs["max_lag_24h"] is None
        assert attrs["min_lag_24h"] is None
        assert attrs["total_transitions"] == 0
        assert attrs["decision_timestamp"] is None
        assert attrs["current_lag"] is None

    def test_extra_state_attributes_with_history(self):
        now = datetime(2026, 3, 12, 12, 0, 0)
        history = [
            {"lag_seconds": 1.0},
            {"lag_seconds": 2.0},
            {"lag_seconds": 3.0},
        ]
        sensor = _sensor(
            DecisionLagSensor,
            decision_lag_seconds=3.0,
            decision_lag_history=history,
            decision_timestamp=now,
            implementation_timestamp=now,
        )
        attrs = sensor.extra_state_attributes
        assert attrs["avg_lag_24h"] == pytest.approx(2.0)
        assert attrs["max_lag_24h"] == pytest.approx(3.0)
        assert attrs["min_lag_24h"] == pytest.approx(1.0)
        assert attrs["total_transitions"] == 3

    def test_icon_with_decision_timestamp(self):
        sensor = _sensor(DecisionLagSensor, decision_timestamp=MagicMock())
        assert sensor.icon == "mdi:timer-sand"

    def test_icon_without_decision_timestamp(self):
        sensor = _sensor(DecisionLagSensor, decision_timestamp=None)
        assert sensor.icon == "mdi:timer-outline"

"""Tests for entity_validator staleness behavior."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from homeassistant.util import dt as dt_util

from custom_components.localshift.const import CONF_TESLEMETRY_SOC
from custom_components.localshift.entity_validator import (
    STALENESS_THRESHOLDS,
    EntityStatus,
    EntityValidator,
)
from tests.fixtures.ha_entities import MockState, MockStates


def _build_validator(soc_state: MockState) -> EntityValidator:
    """Create an EntityValidator with a single SOC entity state."""
    hass = MagicMock()
    states = MockStates({"sensor.my_home_percentage_charged": soc_state})
    hass.states.get = states.get
    hass.states.async_all = states.async_all

    def _get_entity_id(config_key: str) -> str:
        if config_key == CONF_TESLEMETRY_SOC:
            return "sensor.my_home_percentage_charged"
        return ""

    return EntityValidator(hass, _get_entity_id)


def test_soc_staleness_threshold_is_30_minutes() -> None:
    """SOC staleness threshold should tolerate equilibrium periods."""
    assert STALENESS_THRESHOLDS[CONF_TESLEMETRY_SOC] == timedelta(minutes=30)


def test_soc_freshness_prefers_last_reported_when_available() -> None:
    """Fresh telemetry should be accepted even if value has not changed."""
    now = dt_util.now()
    soc_state = MockState(
        entity_id="sensor.my_home_percentage_charged",
        state="50.0",
        attributes={"unit_of_measurement": "%"},
        last_changed=now - timedelta(hours=1),
        last_updated=now - timedelta(hours=1),
    )
    soc_state.last_reported = now - timedelta(minutes=2)

    validator = _build_validator(soc_state)
    health = validator.check_entity(CONF_TESLEMETRY_SOC)

    assert health.status == EntityStatus.OK


def test_soc_freshness_falls_back_to_last_updated_when_last_reported_missing() -> None:
    """Validator should still detect stale feeds without last_reported support."""
    now = dt_util.now()
    soc_state = MockState(
        entity_id="sensor.my_home_percentage_charged",
        state="50.0",
        attributes={"unit_of_measurement": "%"},
        last_changed=now - timedelta(minutes=31),
        last_updated=now - timedelta(minutes=31),
    )

    validator = _build_validator(soc_state)
    health = validator.check_entity(CONF_TESLEMETRY_SOC)

    assert health.status == EntityStatus.STALE
    assert "data is stale" in health.error_message


def test_soc_stable_for_20_minutes_is_not_stale() -> None:
    """Unchanged SOC within 30-minute window should remain healthy."""
    now = dt_util.now()
    soc_state = MockState(
        entity_id="sensor.my_home_percentage_charged",
        state="98.5",
        attributes={"unit_of_measurement": "%"},
        last_changed=now - timedelta(minutes=20),
        last_updated=now - timedelta(minutes=20),
    )

    validator = _build_validator(soc_state)
    health = validator.check_entity(CONF_TESLEMETRY_SOC)

    assert health.status == EntityStatus.OK


def test_soc_stable_for_45_minutes_is_stale() -> None:
    """Unchanged SOC beyond threshold should be marked stale."""
    now = dt_util.now()
    soc_state = MockState(
        entity_id="sensor.my_home_percentage_charged",
        state="98.5",
        attributes={"unit_of_measurement": "%"},
        last_changed=now - timedelta(minutes=45),
        last_updated=now - timedelta(minutes=45),
    )

    validator = _build_validator(soc_state)
    health = validator.check_entity(CONF_TESLEMETRY_SOC)

    assert health.status == EntityStatus.STALE

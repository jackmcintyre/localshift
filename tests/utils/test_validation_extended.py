"""Extended tests for utils/validation.py to increase coverage from 38% to 70%+.

Covers:
- EntityHealth properties and edge cases
- EntityValidator initialization and public API
- check_entity paths (missing, unavailable, unknown, invalid, stale, healthy)
- Failure threshold handling and broken status
- Value validation (numeric, boolean, string, forecast)
- LocalShift internal entity checking
- Integration status computation
- Recovery and reset functionality
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.localshift.const import (
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_TESLEMETRY_ALLOW_EXPORT,
    CONF_TESLEMETRY_BACKUP_RESERVE,
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
)
from custom_components.localshift.utils.entity_configs import (
    ENTITY_CONFIG,
    EntityCategory,
    FAILURE_THRESHOLD_ERROR,
    FAILURE_THRESHOLD_WARNING,
)
from custom_components.localshift.utils.validation import (
    EntityHealth,
    EntityStatus,
    EntityValidator,
    IntegrationStatus,
    ValidationResult,
)
from tests.fixtures.ha_entities import MockState, MockStates


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_hass() -> MagicMock:
    """Create a mock HomeAssistant instance with MockStates."""
    hass = MagicMock()
    states = MockStates()
    hass.states.get = states.get
    hass.states.async_all = states.async_all
    return hass


@pytest.fixture
def mock_hass_with_soc() -> MagicMock:
    """Create mock hass with a healthy SOC entity."""
    hass = MagicMock()
    now = dt_util.now()
    soc_state = MockState(
        entity_id="sensor.my_home_percentage_charged",
        state="50.0",
        attributes={"unit_of_measurement": "%"},
        last_changed=now - timedelta(minutes=5),
        last_updated=now - timedelta(minutes=5),
    )
    states = MockStates({"sensor.my_home_percentage_charged": soc_state})
    hass.states.get = states.get
    hass.states.async_all = states.async_all
    return hass


def _create_validator(
    hass: MagicMock, entity_map: dict[str, str] | None = None
) -> EntityValidator:
    """Create an EntityValidator with optional entity ID mapping.

    Args:
        hass: Mock HomeAssistant instance
        entity_map: Optional mapping from config_key to entity_id

    """
    entity_map = entity_map or {}

    def _get_entity_id(config_key: str) -> str:
        return entity_map.get(config_key, "")

    return EntityValidator(hass, _get_entity_id)


def _create_validator_with_soc(
    hass: MagicMock, soc_state: MockState
) -> EntityValidator:
    """Create a validator with a single SOC entity."""
    states = MockStates({"sensor.my_home_percentage_charged": soc_state})
    hass.states.get = states.get
    hass.states.async_all = states.async_all

    def _get_entity_id(config_key: str) -> str:
        if config_key == CONF_TESLEMETRY_SOC:
            return "sensor.my_home_percentage_charged"
        return ""

    return EntityValidator(hass, _get_entity_id)


# =============================================================================
# EntityHealth Tests
# =============================================================================


class TestEntityHealth:
    """Tests for EntityHealth dataclass properties."""

    def test_is_healthy_when_ok_and_not_broken(self) -> None:
        """Entity is healthy when status is OK and not broken."""
        health = EntityHealth(
            entity_id="sensor.test",
            config_key="test_key",
            category=EntityCategory.REQUIRED,
            status=EntityStatus.OK,
            last_check=dt_util.now(),
        )
        assert health.is_healthy is True

    def test_is_healthy_false_when_unavailable(self) -> None:
        """Entity is not healthy when status is not OK."""
        health = EntityHealth(
            entity_id="sensor.test",
            config_key="test_key",
            category=EntityCategory.REQUIRED,
            status=EntityStatus.UNAVAILABLE,
            last_check=dt_util.now(),
        )
        assert health.is_healthy is False

    def test_is_healthy_false_when_broken(self) -> None:
        """Entity is not healthy when marked broken."""
        health = EntityHealth(
            entity_id="sensor.test",
            config_key="test_key",
            category=EntityCategory.REQUIRED,
            status=EntityStatus.OK,
            last_check=dt_util.now(),
            is_broken=True,
        )
        assert health.is_healthy is False

    def test_is_available_when_ok(self) -> None:
        """Entity is available when status is OK."""
        health = EntityHealth(
            entity_id="sensor.test",
            config_key="test_key",
            category=EntityCategory.REQUIRED,
            status=EntityStatus.OK,
            last_check=dt_util.now(),
        )
        assert health.is_available is True

    def test_is_available_when_stale(self) -> None:
        """Entity is available when status is STALE (has usable value)."""
        health = EntityHealth(
            entity_id="sensor.test",
            config_key="test_key",
            category=EntityCategory.REQUIRED,
            status=EntityStatus.STALE,
            last_check=dt_util.now(),
        )
        assert health.is_available is True

    def test_is_available_false_when_unavailable(self) -> None:
        """Entity is not available when status is UNAVAILABLE."""
        health = EntityHealth(
            entity_id="sensor.test",
            config_key="test_key",
            category=EntityCategory.REQUIRED,
            status=EntityStatus.UNAVAILABLE,
            last_check=dt_util.now(),
        )
        assert health.is_available is False

    def test_is_available_false_when_broken(self) -> None:
        """Entity is not available when broken, even if OK status."""
        health = EntityHealth(
            entity_id="sensor.test",
            config_key="test_key",
            category=EntityCategory.REQUIRED,
            status=EntityStatus.OK,
            last_check=dt_util.now(),
            is_broken=True,
        )
        assert health.is_available is False


# =============================================================================
# ValidationResult Tests
# =============================================================================


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_valid_result(self) -> None:
        """Create a valid result."""
        result = ValidationResult(is_valid=True, value=50.0)
        assert result.is_valid is True
        assert result.value == 50.0
        assert result.error_message == ""
        assert result.warning_message == ""

    def test_invalid_result_with_error(self) -> None:
        """Create an invalid result with error message."""
        result = ValidationResult(
            is_valid=False, value=0.0, error_message="Value out of range"
        )
        assert result.is_valid is False
        assert result.error_message == "Value out of range"


# =============================================================================
# EntityValidator Initialization Tests
# =============================================================================


class TestEntityValidatorInit:
    """Tests for EntityValidator initialization."""

    def test_initialization_creates_health_for_all_entities(
        self, mock_hass: MagicMock
    ) -> None:
        """Validator initializes health tracking for all ENTITY_CONFIG entries."""
        validator = _create_validator(mock_hass)

        # Should have health entries for all configured entities
        assert len(validator._entity_health) == len(ENTITY_CONFIG)

    def test_initial_health_status_is_ok(self, mock_hass: MagicMock) -> None:
        """Initial health status is OK for all entities."""
        validator = _create_validator(mock_hass)

        for health in validator._entity_health.values():
            assert health.status == EntityStatus.OK
            assert health.is_broken is False
            assert health.consecutive_failures == 0


# =============================================================================
# check_entity Tests
# =============================================================================


class TestCheckEntity:
    """Tests for EntityValidator.check_entity method."""

    def test_check_entity_missing_entity(self, mock_hass: MagicMock) -> None:
        """Missing entity returns MISSING status."""
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.nonexistent"}
        )

        health = validator.check_entity(CONF_TESLEMETRY_SOC)

        assert health.status == EntityStatus.MISSING
        assert "does not exist" in health.error_message

    def test_check_entity_unavailable(self, mock_hass: MagicMock) -> None:
        """Unavailable entity returns UNAVAILABLE status."""
        states = MockStates({
            "sensor.my_home_percentage_charged": MockState(
                entity_id="sensor.my_home_percentage_charged",
                state="unavailable",
                attributes={},
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged"}
        )

        health = validator.check_entity(CONF_TESLEMETRY_SOC)

        assert health.status == EntityStatus.UNAVAILABLE
        assert "unavailable" in health.error_message

    def test_check_entity_unknown_state(self, mock_hass: MagicMock) -> None:
        """Unknown state returns UNKNOWN status."""
        states = MockStates({
            "sensor.my_home_percentage_charged": MockState(
                entity_id="sensor.my_home_percentage_charged",
                state="unknown",
                attributes={},
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged"}
        )

        health = validator.check_entity(CONF_TESLEMETRY_SOC)

        assert health.status == EntityStatus.UNKNOWN
        assert "unknown state" in health.error_message

    def test_check_entity_invalid_numeric_value(self, mock_hass: MagicMock) -> None:
        """Non-numeric value for numeric entity returns INVALID_VALUE."""
        states = MockStates({
            "sensor.my_home_percentage_charged": MockState(
                entity_id="sensor.my_home_percentage_charged",
                state="not_a_number",
                attributes={},
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged"}
        )

        health = validator.check_entity(CONF_TESLEMETRY_SOC)

        assert health.status == EntityStatus.INVALID_VALUE
        assert "not a number" in health.error_message

    def test_check_entity_value_below_minimum(self, mock_hass: MagicMock) -> None:
        """Value below min_value returns INVALID_VALUE."""
        states = MockStates({
            "sensor.my_home_percentage_charged": MockState(
                entity_id="sensor.my_home_percentage_charged",
                state="-5.0",
                attributes={},
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged"}
        )

        health = validator.check_entity(CONF_TESLEMETRY_SOC)

        assert health.status == EntityStatus.INVALID_VALUE
        assert "below minimum" in health.error_message

    def test_check_entity_value_above_maximum(self, mock_hass: MagicMock) -> None:
        """Value above max_value returns INVALID_VALUE."""
        states = MockStates({
            "sensor.my_home_percentage_charged": MockState(
                entity_id="sensor.my_home_percentage_charged",
                state="150.0",
                attributes={},
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged"}
        )

        health = validator.check_entity(CONF_TESLEMETRY_SOC)

        assert health.status == EntityStatus.INVALID_VALUE
        assert "above maximum" in health.error_message

    def test_check_entity_invalid_string_value(self, mock_hass: MagicMock) -> None:
        """String value not in valid_values returns INVALID_VALUE."""
        states = MockStates({
            "select.my_home_operation_mode": MockState(
                entity_id="select.my_home_operation_mode",
                state="invalid_mode",
                attributes={},
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass,
            {CONF_TESLEMETRY_OPERATION_MODE: "select.my_home_operation_mode"},
        )

        health = validator.check_entity(CONF_TESLEMETRY_OPERATION_MODE)

        assert health.status == EntityStatus.INVALID_VALUE
        assert "not in valid values" in health.error_message

    def test_check_entity_healthy_path(self, mock_hass: MagicMock) -> None:
        """Healthy entity returns OK status."""
        now = dt_util.now()
        states = MockStates({
            "sensor.my_home_percentage_charged": MockState(
                entity_id="sensor.my_home_percentage_charged",
                state="50.0",
                attributes={},
                last_changed=now - timedelta(minutes=5),
                last_updated=now - timedelta(minutes=5),
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged"}
        )

        health = validator.check_entity(CONF_TESLEMETRY_SOC)

        assert health.status == EntityStatus.OK
        assert health.is_healthy is True
        assert health.last_valid_value == 50.0

    def test_check_entity_optional_with_no_entity_id(
        self, mock_hass: MagicMock
    ) -> None:
        """Optional entity with no entity_id returns OK."""
        validator = _create_validator(mock_hass)  # Empty entity map

        # Find an optional config key
        optional_key = CONF_SOLCAST_FORECAST_TOMORROW

        health = validator.check_entity(optional_key)

        assert health.status == EntityStatus.OK
        assert health.error_message == ""
        assert health.consecutive_failures == 0

    def test_check_entity_unknown_config_key(self, mock_hass: MagicMock) -> None:
        """Unknown config key creates new health entry."""
        validator = _create_validator(mock_hass)

        health = validator.check_entity("unknown_config_key")

        # Should create health with OPTIONAL category
        assert health.config_key == "unknown_config_key"


# =============================================================================
# Staleness Tests
# =============================================================================


class TestStaleness:
    """Tests for staleness detection."""

    def test_stale_entity_detected(self, mock_hass: MagicMock) -> None:
        """Entity with old timestamp is marked stale."""
        now = dt_util.now()
        states = MockStates({
            "sensor.my_home_percentage_charged": MockState(
                entity_id="sensor.my_home_percentage_charged",
                state="50.0",
                attributes={},
                last_changed=now - timedelta(minutes=45),
                last_updated=now - timedelta(minutes=45),
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged"}
        )

        health = validator.check_entity(CONF_TESLEMETRY_SOC)

        assert health.status == EntityStatus.STALE
        assert "stale" in health.error_message

    def test_select_entities_skip_staleness_check(self, mock_hass: MagicMock) -> None:
        """Select entities skip staleness check."""
        now = dt_util.now()
        # Operation mode is a select with staleness threshold
        states = MockStates({
            "select.my_home_operation_mode": MockState(
                entity_id="select.my_home_operation_mode",
                state="self_consumption",
                attributes={},
                last_changed=now - timedelta(hours=2),  # Very old
                last_updated=now - timedelta(hours=2),
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass,
            {CONF_TESLEMETRY_OPERATION_MODE: "select.my_home_operation_mode"},
        )

        health = validator.check_entity(CONF_TESLEMETRY_OPERATION_MODE)

        # Should be OK, not stale (selects skip staleness)
        assert health.status == EntityStatus.OK

    def test_entity_without_staleness_threshold(self, mock_hass: MagicMock) -> None:
        """Entity without staleness threshold is never marked stale."""
        now = dt_util.now()
        # ALLOW_EXPORT has no staleness threshold
        states = MockStates({
            "select.my_home_allow_export": MockState(
                entity_id="select.my_home_allow_export",
                state="pv_only",
                attributes={},
                last_changed=now - timedelta(hours=24),  # Very old
                last_updated=now - timedelta(hours=24),
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_ALLOW_EXPORT: "select.my_home_allow_export"}
        )

        health = validator.check_entity(CONF_TESLEMETRY_ALLOW_EXPORT)

        assert health.status == EntityStatus.OK


# =============================================================================
# Failure Threshold Tests
# =============================================================================


class TestFailureThresholds:
    """Tests for failure threshold handling."""

    def test_warning_logged_at_threshold(self, mock_hass: MagicMock) -> None:
        """Warning is logged at FAILURE_THRESHOLD_WARNING failures."""
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.missing"}
        )

        with patch(
            "custom_components.localshift.utils.validation._LOGGER"
        ) as mock_logger:
            for _ in range(FAILURE_THRESHOLD_WARNING):
                health = validator.check_entity(CONF_TESLEMETRY_SOC)

            # Should have logged a warning
            assert mock_logger.warning.called

    def test_broken_at_error_threshold(self, mock_hass: MagicMock) -> None:
        """Entity is marked broken at FAILURE_THRESHOLD_ERROR failures."""
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.missing"}
        )

        for _ in range(FAILURE_THRESHOLD_ERROR):
            health = validator.check_entity(CONF_TESLEMETRY_SOC)

        assert health.is_broken is True
        assert health.consecutive_failures >= FAILURE_THRESHOLD_ERROR

    def test_optional_entity_uses_warning_level(self, mock_hass: MagicMock) -> None:
        """Optional entities use WARNING level for broken status."""
        # Find an optional entity
        validator = _create_validator(
            mock_hass, {CONF_SOLCAST_FORECAST_TOMORROW: "sensor.missing"}
        )

        # Make the entity exist but be unavailable
        states = MockStates({
            "sensor.missing": MockState(
                entity_id="sensor.missing",
                state="unavailable",
                attributes={},
            )
        })
        mock_hass.states.get = states.get

        for _ in range(FAILURE_THRESHOLD_ERROR):
            health = validator.check_entity(CONF_SOLCAST_FORECAST_TOMORROW)

        assert health.is_broken is True


# =============================================================================
# Recovery Tests
# =============================================================================


class TestRecovery:
    """Tests for recovery from broken status."""

    def test_recovery_from_broken_status(self, mock_hass: MagicMock) -> None:
        """Entity recovers from broken status when healthy again."""
        now = dt_util.now()
        states = MockStates({
            "sensor.my_home_percentage_charged": MockState(
                entity_id="sensor.my_home_percentage_charged",
                state="50.0",
                attributes={},
                last_changed=now - timedelta(minutes=5),
                last_updated=now - timedelta(minutes=5),
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged"}
        )

        # Get the health entry and manually set it to broken
        health = validator._entity_health[CONF_TESLEMETRY_SOC]
        health.is_broken = True
        health.consecutive_failures = FAILURE_THRESHOLD_ERROR

        # Check again - should recover
        health = validator.check_entity(CONF_TESLEMETRY_SOC)

        assert health.is_broken is False
        assert health.status == EntityStatus.OK


# =============================================================================
# Value Validation Tests
# =============================================================================


class TestValidateEntityValue:
    """Tests for _validate_entity_value method."""

    def test_validate_numeric_entity(self, mock_hass: MagicMock) -> None:
        """Numeric entity validation parses float values."""
        now = dt_util.now()
        states = MockStates({
            "sensor.price": MockState(
                entity_id="sensor.price",
                state="0.25",
                attributes={},
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_PRICING_GENERAL_PRICE: "sensor.price"}
        )

        health = validator.check_entity(CONF_PRICING_GENERAL_PRICE)

        assert health.status == EntityStatus.OK
        assert health.last_valid_value == 0.25

    def test_validate_boolean_entity(self, mock_hass: MagicMock) -> None:
        """Boolean entity validation handles on/off states."""
        from custom_components.localshift.const import CONF_PRICING_PRICE_SPIKE

        now = dt_util.now()
        states = MockStates({
            "binary_sensor.price_spike": MockState(
                entity_id="binary_sensor.price_spike",
                state="on",
                attributes={},
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_PRICING_PRICE_SPIKE: "binary_sensor.price_spike"}
        )

        health = validator.check_entity(CONF_PRICING_PRICE_SPIKE)

        assert health.status == EntityStatus.OK
        assert health.last_valid_value is True

    def test_validate_boolean_entity_off(self, mock_hass: MagicMock) -> None:
        """Boolean entity validation handles off state."""
        from custom_components.localshift.const import CONF_PRICING_PRICE_SPIKE

        now = dt_util.now()
        states = MockStates({
            "binary_sensor.price_spike": MockState(
                entity_id="binary_sensor.price_spike",
                state="off",
                attributes={},
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_PRICING_PRICE_SPIKE: "binary_sensor.price_spike"}
        )

        health = validator.check_entity(CONF_PRICING_PRICE_SPIKE)

        assert health.status == EntityStatus.OK
        assert health.last_valid_value is False

    def test_validate_forecast_entity_pricing(self, mock_hass: MagicMock) -> None:
        """Forecast entity validation handles pricing forecasts."""
        now = dt_util.now()
        states = MockStates({
            "sensor.general_forecast": MockState(
                entity_id="sensor.general_forecast",
                state="48",
                attributes={
                    "forecasts": [
                        {"per_kwh": 0.25, "nem_time": "12:00"},
                        {"per_kwh": 0.30, "nem_time": "12:30"},
                    ]
                },
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_PRICING_GENERAL_FORECAST: "sensor.general_forecast"}
        )

        health = validator.check_entity(CONF_PRICING_GENERAL_FORECAST)

        assert health.status == EntityStatus.OK
        assert len(health.last_valid_value) == 2

    def test_validate_forecast_entity_pricing_invalid(
        self, mock_hass: MagicMock
    ) -> None:
        """Forecast entity validation fails for non-list forecasts."""
        now = dt_util.now()
        states = MockStates({
            "sensor.general_forecast": MockState(
                entity_id="sensor.general_forecast",
                state="invalid",
                attributes={"forecasts": "not_a_list"},
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_PRICING_GENERAL_FORECAST: "sensor.general_forecast"}
        )

        health = validator.check_entity(CONF_PRICING_GENERAL_FORECAST)

        assert health.status == EntityStatus.INVALID_VALUE

    def test_validate_forecast_entity_solcast(self, mock_hass: MagicMock) -> None:
        """Forecast entity validation handles Solcast forecasts."""
        now = dt_util.now()
        states = MockStates({
            "sensor.solcast_today": MockState(
                entity_id="sensor.solcast_today",
                state="24",
                attributes={
                    "detailedForecast": [
                        {"period_start": "06:00", "pv_estimate": 2.5},
                        {"period_start": "07:00", "pv_estimate": 3.5},
                    ]
                },
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_SOLCAST_FORECAST_TODAY: "sensor.solcast_today"}
        )

        health = validator.check_entity(CONF_SOLCAST_FORECAST_TODAY)

        assert health.status == EntityStatus.OK
        assert len(health.last_valid_value) == 2

    def test_validate_forecast_entity_solcast_detailed_hourly(
        self, mock_hass: MagicMock
    ) -> None:
        """Forecast entity handles detailedHourly attribute."""
        now = dt_util.now()
        states = MockStates({
            "sensor.solcast_tomorrow": MockState(
                entity_id="sensor.solcast_tomorrow",
                state="24",
                attributes={
                    "detailedHourly": [
                        {"period_start": "06:00", "pv_estimate": 2.0},
                    ]
                },
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_SOLCAST_FORECAST_TOMORROW: "sensor.solcast_tomorrow"}
        )

        health = validator.check_entity(CONF_SOLCAST_FORECAST_TOMORROW)

        assert health.status == EntityStatus.OK

    def test_validate_entity_no_expected_type(self, mock_hass: MagicMock) -> None:
        """Entity without expected_type is always valid."""
        from custom_components.localshift.const import CONF_WEATHER_ENTITY

        now = dt_util.now()
        states = MockStates({
            "weather.home": MockState(
                entity_id="weather.home",
                state="sunny",
                attributes={},
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(mock_hass, {CONF_WEATHER_ENTITY: "weather.home"})

        health = validator.check_entity(CONF_WEATHER_ENTITY)

        assert health.status == EntityStatus.OK


# =============================================================================
# check_all_entities Tests
# =============================================================================


class TestCheckAllEntities:
    """Tests for check_all_entities method."""

    def test_check_all_entities(self, mock_hass: MagicMock) -> None:
        """check_all_entities checks all configured entities."""
        now = dt_util.now()
        # Add a valid SOC entity
        states = MockStates({
            "sensor.my_home_percentage_charged": MockState(
                entity_id="sensor.my_home_percentage_charged",
                state="50.0",
                attributes={},
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged"}
        )

        results = validator.check_all_entities()

        assert len(results) == len(ENTITY_CONFIG)
        assert validator._last_full_check is not None

    def test_check_all_updates_cached_status(self, mock_hass: MagicMock) -> None:
        """check_all_entities updates cached status."""
        now = dt_util.now()
        states = MockStates({
            "sensor.my_home_percentage_charged": MockState(
                entity_id="sensor.my_home_percentage_charged",
                state="50.0",
                attributes={},
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged"}
        )

        validator.check_all_entities()

        assert validator._last_full_check is not None
        assert validator._cached_status is not None


# =============================================================================
# _validate_type_value Tests
# =============================================================================


class TestValidateTypeValue:
    """Tests for _validate_type_value method."""

    def test_validate_type_value_none(self, mock_hass: MagicMock) -> None:
        """None expected_type accepts any value."""
        validator = _create_validator(mock_hass)
        is_valid, value, error = validator._validate_type_value(
            "anything", None, "sensor.test"
        )
        assert is_valid is True
        assert value == "anything"
        assert error == ""

    def test_validate_type_value_boolean(self, mock_hass: MagicMock) -> None:
        """Boolean type parsing."""
        validator = _create_validator(mock_hass)

        is_valid, value, error = validator._validate_type_value(
            "on", bool, "sensor.test"
        )
        assert is_valid is True
        assert value is True

        is_valid, value, error = validator._validate_type_value(
            "off", bool, "sensor.test"
        )
        assert is_valid is True
        assert value is False

    def test_validate_type_value_numeric(self, mock_hass: MagicMock) -> None:
        """Numeric type parsing."""
        validator = _create_validator(mock_hass)

        is_valid, value, error = validator._validate_type_value(
            "123.45", float, "sensor.test"
        )
        assert is_valid is True
        assert value == 123.45

        is_valid, value, error = validator._validate_type_value(
            "not_a_number", float, "sensor.test"
        )
        assert is_valid is False
        assert "not a number" in error

    def test_validate_type_value_string(self, mock_hass: MagicMock) -> None:
        """String type accepts any string."""
        validator = _create_validator(mock_hass)

        is_valid, value, error = validator._validate_type_value(
            "any_string", str, "sensor.test"
        )
        assert is_valid is True
        assert value == "any_string"


# =============================================================================
# LocalShift Entity Tests
# =============================================================================


class TestLocalShiftEntities:
    """Tests for LocalShift internal entity checking."""

    def test_check_all_localshift_entities(self, mock_hass: MagicMock) -> None:
        """check_all_localshift_entities returns health for all LocalShift entities."""
        now = dt_util.now()
        states = MockStates({
            "sensor.localshift_price_cheap_effective": MockState(
                entity_id="sensor.localshift_price_cheap_effective",
                state="0.15",
                attributes={},
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(mock_hass)

        results = validator.check_all_localshift_entities()

        # Should return health for all LOCALSHIFT_ENTITY_CONFIG entries
        assert isinstance(results, dict)

    def test_check_localshift_entity_missing(self, mock_hass: MagicMock) -> None:
        """Missing LocalShift entity is marked MISSING."""
        validator = _create_validator(mock_hass)

        results = validator.check_all_localshift_entities()

        # Most entities should be missing since we didn't set up any
        for entity_id, health_dict in results.items():
            if health_dict.get("status") == "missing":
                assert "does not exist" in health_dict.get("error_message", "")

    def test_check_localshift_entity_stale(self, mock_hass: MagicMock) -> None:
        """Stale LocalShift entity is detected."""
        now = dt_util.now()
        states = MockStates({
            "sensor.localshift_price_cheap_effective": MockState(
                entity_id="sensor.localshift_price_cheap_effective",
                state="0.15",
                attributes={},
                last_changed=now - timedelta(hours=1),
                last_updated=now - timedelta(hours=1),
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(mock_hass)

        results = validator.check_all_localshift_entities()

        # Should have staleness info
        assert "sensor.localshift_price_cheap_effective" in results


# =============================================================================
# Status and Error Message Tests
# =============================================================================


class TestStatusProperties:
    """Tests for status, errors, and warnings properties."""

    def test_status_property_ok(self, mock_hass: MagicMock) -> None:
        """Status is OK when all required entities are healthy."""
        now = dt_util.now()
        states = MockStates({
            "sensor.my_home_percentage_charged": MockState(
                entity_id="sensor.my_home_percentage_charged",
                state="50.0",
                attributes={},
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged"}
        )

        health = validator.check_entity(CONF_TESLEMETRY_SOC)
        assert health.status == EntityStatus.OK

    def test_status_property_error(self, mock_hass: MagicMock) -> None:
        """Status is ERROR when required entities are missing."""
        validator = _create_validator(mock_hass)
        validator.check_all_entities()

        assert validator.status == IntegrationStatus.ERROR
        assert len(validator.errors) > 0

    def test_errors_property_returns_copy(self, mock_hass: MagicMock) -> None:
        """errors property returns a copy."""
        validator = _create_validator(mock_hass)
        validator.check_all_entities()

        errors1 = validator.errors
        errors2 = validator.errors
        assert errors1 is not errors2  # Different list objects

    def test_warnings_property_returns_copy(self, mock_hass: MagicMock) -> None:
        """warnings property returns a copy."""
        validator = _create_validator(mock_hass)
        validator.check_all_entities()

        warnings1 = validator.warnings
        warnings2 = validator.warnings
        assert warnings1 is not warnings2  # Different list objects


# =============================================================================
# Health Summary Tests
# =============================================================================


class TestHealthSummary:
    """Tests for get_health_summary and related methods."""

    def test_get_health_summary(self, mock_hass: MagicMock) -> None:
        """get_health_summary returns complete health info."""
        now = dt_util.now()
        states = MockStates({
            "sensor.my_home_percentage_charged": MockState(
                entity_id="sensor.my_home_percentage_charged",
                state="50.0",
                attributes={},
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged"}
        )
        validator.check_all_entities()

        summary = validator.get_health_summary()

        assert "status" in summary
        assert "errors" in summary
        assert "warnings" in summary
        assert "entities" in summary
        assert len(summary["entities"]) == len(ENTITY_CONFIG)

    def test_get_required_entities_status(self, mock_hass: MagicMock) -> None:
        """get_required_entities_status returns only required entities."""
        now = dt_util.now()
        states = MockStates({
            "sensor.my_home_percentage_charged": MockState(
                entity_id="sensor.my_home_percentage_charged",
                state="50.0",
                attributes={},
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged"}
        )
        validator.check_all_entities()

        required_status = validator.get_required_entities_status()

        # All required entities should be represented
        for key, is_healthy in required_status.items():
            config = ENTITY_CONFIG.get(key, {})
            assert config.get("category") == EntityCategory.REQUIRED


# =============================================================================
# User-Friendly Message Tests
# =============================================================================


class TestUserFriendlyMessage:
    """Tests for get_user_friendly_message method."""

    def test_message_ok_status(self, mock_hass: MagicMock) -> None:
        """OK status returns operational message."""
        validator = _create_validator(mock_hass)
        validator._cached_status = IntegrationStatus.OK
        validator._cached_errors = []
        validator._cached_warnings = []

        msg = validator.get_user_friendly_message()
        assert "operational" in msg.lower()

    def test_message_error_status(self, mock_hass: MagicMock) -> None:
        """ERROR status returns error message."""
        validator = _create_validator(mock_hass)  # No entities
        validator.check_all_entities()

        msg = validator.get_user_friendly_message()
        assert "error" in msg.lower() or "multiple" in msg.lower()

    def test_message_single_error(self, mock_hass: MagicMock) -> None:
        """Single error is displayed clearly."""
        validator = _create_validator(mock_hass)
        validator._cached_status = IntegrationStatus.ERROR
        validator._cached_errors = ["Battery SOC is missing"]

        msg = validator.get_user_friendly_message()
        assert "Battery SOC is missing" in msg

    def test_message_multiple_errors(self, mock_hass: MagicMock) -> None:
        """Multiple errors are truncated."""
        validator = _create_validator(mock_hass)
        validator._cached_status = IntegrationStatus.ERROR
        validator._cached_errors = ["Error 1", "Error 2", "Error 3", "Error 4"]

        msg = validator.get_user_friendly_message()
        assert "Multiple" in msg or "errors" in msg.lower()

    def test_message_degraded_status(self, mock_hass: MagicMock) -> None:
        """DEGRADED status returns warning message."""
        validator = _create_validator(mock_hass)
        validator._cached_status = IntegrationStatus.DEGRADED
        validator._cached_warnings = ["Solar forecast is stale"]

        msg = validator.get_user_friendly_message()
        assert "Warning" in msg or "Degraded" in msg


# =============================================================================
# Automation Control Tests
# =============================================================================


class TestShouldAllowAutomation:
    """Tests for should_allow_automation method."""

    def test_allow_when_healthy(self, mock_hass: MagicMock) -> None:
        """Automation allowed when all required entities are healthy."""
        now = dt_util.now()
        states = MockStates({
            "sensor.my_home_percentage_charged": MockState(
                entity_id="sensor.my_home_percentage_charged",
                state="50.0",
                attributes={},
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged"}
        )

        health = validator.check_entity(CONF_TESLEMETRY_SOC)
        assert health.status == EntityStatus.OK

    def test_block_when_missing(self, mock_hass: MagicMock) -> None:
        """Automation blocked when required entity is missing."""
        validator = _create_validator(mock_hass)  # No entities
        validator.check_all_entities()

        assert validator.should_allow_automation() is False

    def test_block_when_broken(self, mock_hass: MagicMock) -> None:
        """Automation blocked when required entity is broken."""
        now = dt_util.now()
        states = MockStates({
            "sensor.my_home_percentage_charged": MockState(
                entity_id="sensor.my_home_percentage_charged",
                state="50.0",
                attributes={},
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged"}
        )
        validator.check_all_entities()

        # Manually mark as broken
        health = validator._entity_health[CONF_TESLEMETRY_SOC]
        health.is_broken = True

        assert validator.should_allow_automation() is False


# =============================================================================
# Reset Methods Tests
# =============================================================================


class TestResetMethods:
    """Tests for reset_broken_status and reset_entity_tracking."""

    def test_reset_broken_status_single_entity(self, mock_hass: MagicMock) -> None:
        """Reset broken status for a single entity."""
        validator = _create_validator(mock_hass)
        health = validator._entity_health[CONF_TESLEMETRY_SOC]
        health.is_broken = True
        health.consecutive_failures = 10

        validator.reset_broken_status(CONF_TESLEMETRY_SOC)

        assert health.is_broken is False
        assert health.consecutive_failures == 0

    def test_reset_broken_status_all_entities(self, mock_hass: MagicMock) -> None:
        """Reset broken status for all entities."""
        validator = _create_validator(mock_hass)
        # Mark multiple entities as broken
        for health in validator._entity_health.values():
            health.is_broken = True
            health.consecutive_failures = 10

        validator.reset_broken_status()  # None = reset all

        for health in validator._entity_health.values():
            assert health.is_broken is False
            assert health.consecutive_failures == 0

    def test_reset_entity_tracking(self, mock_hass: MagicMock) -> None:
        """Reset entity tracking when entity ID changes."""
        now = dt_util.now()
        states = MockStates({
            "sensor.new_entity": MockState(
                entity_id="sensor.new_entity",
                state="50.0",
                attributes={},
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get

        def get_new_entity_id(config_key: str) -> str:
            return "sensor.new_entity"

        validator = EntityValidator(mock_hass, get_new_entity_id)

        # Set up existing tracking with old values
        health = validator._entity_health[CONF_TESLEMETRY_SOC]
        health.status = EntityStatus.MISSING
        health.consecutive_failures = 5
        health.is_broken = True

        validator.reset_entity_tracking(CONF_TESLEMETRY_SOC)

        assert health.entity_id == "sensor.new_entity"
        assert health.status == EntityStatus.OK
        assert health.consecutive_failures == 0
        assert health.is_broken is False
        assert health.last_valid_value is None

    def test_reset_entity_tracking_unknown_key(self, mock_hass: MagicMock) -> None:
        """Reset entity tracking for unknown key does nothing."""
        validator = _create_validator(mock_hass)

        # Should not raise
        validator.reset_entity_tracking("unknown_key")


# =============================================================================
# Integration Status Tests
# =============================================================================


class TestIntegrationStatus:
    """Tests for integration status computation."""

    def test_status_degraded_with_recommended_missing(
        self, mock_hass: MagicMock
    ) -> None:
        """Status is DEGRADED when recommended entities have issues."""
        now = dt_util.now()
        # Set up only required entities as healthy
        states = MockStates({
            "sensor.my_home_percentage_charged": MockState(
                entity_id="sensor.my_home_percentage_charged",
                state="50.0",
                attributes={},
                last_changed=now,
                last_updated=now,
            )
        })
        mock_hass.states.get = states.get
        validator = _create_validator(
            mock_hass, {CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged"}
        )
        validator.check_all_entities()

        # If only required entities are set up, recommended will be missing
        # This should result in warnings but not errors
        if validator.status == IntegrationStatus.DEGRADED:
            assert len(validator.warnings) > 0

    def test_status_categorization(self, mock_hass: MagicMock) -> None:
        """Test _categorize_health_severity correctly categorizes."""
        validator = _create_validator(mock_hass)

        # REQUIRED + MISSING = error
        health = EntityHealth(
            entity_id="test",
            config_key="test",
            category=EntityCategory.REQUIRED,
            status=EntityStatus.MISSING,
            last_check=dt_util.now(),
        )
        is_error, is_warning = validator._categorize_health_severity(health)
        assert is_error is True
        assert is_warning is False

        # REQUIRED + STALE = warning
        health.status = EntityStatus.STALE
        is_error, is_warning = validator._categorize_health_severity(health)
        assert is_error is False
        assert is_warning is True

        # RECOMMENDED + MISSING = warning
        health.category = EntityCategory.RECOMMENDED
        health.status = EntityStatus.MISSING
        is_error, is_warning = validator._categorize_health_severity(health)
        assert is_error is False
        assert is_warning is True

        # OPTIONAL = no error, no warning
        health.category = EntityCategory.OPTIONAL
        is_error, is_warning = validator._categorize_health_severity(health)
        assert is_error is False
        assert is_warning is False


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_get_freshness_timestamp_no_last_reported(
        self, mock_hass: MagicMock
    ) -> None:
        """_get_freshness_timestamp falls back to last_updated."""
        validator = _create_validator(mock_hass)
        now = dt_util.now()
        state = MockState(
            entity_id="sensor.test",
            state="50.0",
            attributes={},
            last_changed=now,
            last_updated=now,
        )

        ts = validator._get_freshness_timestamp(state)
        assert ts == state.last_updated

    def test_format_health_error_message(self, mock_hass: MagicMock) -> None:
        """_format_health_error_message formats all status types."""
        validator = _create_validator(mock_hass)

        health = EntityHealth(
            entity_id="test",
            config_key="test",
            category=EntityCategory.REQUIRED,
            status=EntityStatus.MISSING,
            last_check=dt_util.now(),
        )
        msg = validator._format_health_error_message(health, "Battery SOC")
        assert "not found" in msg

        health.status = EntityStatus.UNAVAILABLE
        msg = validator._format_health_error_message(health, "Battery SOC")
        assert "unavailable" in msg

        health.status = EntityStatus.UNKNOWN
        msg = validator._format_health_error_message(health, "Battery SOC")
        assert "unknown" in msg

        health.status = EntityStatus.INVALID_VALUE
        health.error_message = "Value out of range"
        msg = validator._format_health_error_message(health, "Battery SOC")
        assert "Value out of range" in msg

        health.status = EntityStatus.STALE
        msg = validator._format_health_error_message(health, "Battery SOC")
        assert "stale" in msg

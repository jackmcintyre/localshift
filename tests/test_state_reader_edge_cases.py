"""Tests for StateReader edge cases and entity availability handling.

These tests verify that the StateReader correctly handles:
- Unavailable entities (state="unavailable")
- Unknown entities (state="unknown")
- Missing entities (hass.states.get() returns None)
- Partial availability (some entities available, some not)
- Malformed state values
- Missing attributes
"""

from custom_components.localshift.coordinator_data import CoordinatorData
from custom_components.localshift.state_reader import StateReader

# =============================================================================
# BASIC STATE READ TESTS
# =============================================================================


class TestStateReaderBasicReads:
    """Tests for basic state reading functionality."""

    def test_read_float_from_available_entity(self, state_reader):
        """Test reading a float from an available entity."""
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        # Should have read the SOC value from the mock state
        assert data.soc == 50.0

    def test_read_string_from_available_entity(self, state_reader):
        """Test reading a string from an available entity."""
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert data.operation_mode == "autonomous"

    def test_read_bool_from_binary_sensor(self, state_reader):
        """Test reading a boolean from a binary sensor."""
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        # Price spike should be False in default state
        assert data.price_spike is False


# =============================================================================
# UNAVAILABLE ENTITY TESTS
# =============================================================================


class TestStateReaderUnavailableEntities:
    """Tests for handling unavailable entities."""

    def test_unavailable_soc_returns_default(self, state_reader_unavailable):
        """Test that unavailable SOC returns default value (0.0)."""
        data = CoordinatorData()
        state_reader_unavailable.read_all_external_state(data)

        # Should return default value when entity is unavailable
        assert data.soc == 0.0

    def test_unavailable_operation_mode_returns_default(self, state_reader_unavailable):
        """Test that unavailable operation mode returns empty string."""
        data = CoordinatorData()
        state_reader_unavailable.read_all_external_state(data)

        assert data.operation_mode == ""

    def test_unavailable_price_returns_default(self, state_reader_unavailable):
        """Test that unavailable price returns default value (0.0)."""
        data = CoordinatorData()
        state_reader_unavailable.read_all_external_state(data)

        assert data.general_price == 0.0
        assert data.feed_in_price == 0.0

    def test_unavailable_binary_sensor_returns_false(self, state_reader_unavailable):
        """Test that unavailable binary sensor returns False."""
        data = CoordinatorData()
        state_reader_unavailable.read_all_external_state(data)

        assert data.price_spike is False


# =============================================================================
# UNKNOWN ENTITY TESTS
# =============================================================================


class TestStateReaderUnknownEntities:
    """Tests for handling entities with unknown state."""

    def test_unknown_soc_returns_default(
        self, mock_hass_unknown_entities, mock_entry, mock_entity_validator
    ):
        """Test that unknown SOC returns default value."""
        state_reader = StateReader(
            mock_hass_unknown_entities, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert data.soc == 0.0

    def test_unknown_operation_mode_returns_default(
        self, mock_hass_unknown_entities, mock_entry, mock_entity_validator
    ):
        """Test that unknown operation mode returns empty string."""
        state_reader = StateReader(
            mock_hass_unknown_entities, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert data.operation_mode == ""


# =============================================================================
# MISSING ENTITY TESTS
# =============================================================================


class TestStateReaderMissingEntities:
    """Tests for handling missing entities (hass.states.get() returns None)."""

    def test_missing_soc_returns_default(self, state_reader_missing):
        """Test that missing SOC entity returns default value."""
        data = CoordinatorData()
        state_reader_missing.read_all_external_state(data)

        assert data.soc == 0.0

    def test_missing_operation_mode_returns_default(self, state_reader_missing):
        """Test that missing operation mode entity returns empty string."""
        data = CoordinatorData()
        state_reader_missing.read_all_external_state(data)

        assert data.operation_mode == ""

    def test_missing_all_entities_no_crash(self, state_reader_missing):
        """Test that reading with all entities missing doesn't crash."""
        data = CoordinatorData()

        # Should not raise any exceptions
        state_reader_missing.read_all_external_state(data)

        # All values should be defaults
        assert data.soc == 0.0
        assert data.operation_mode == ""
        assert data.general_price == 0.0
        assert data.feed_in_price == 0.0
        assert data.price_spike is False


# =============================================================================
# PARTIAL AVAILABILITY TESTS
# =============================================================================


class TestStateReaderPartialAvailability:
    """Tests for handling partial entity availability."""

    def test_partial_availability_reads_available_entities(
        self, mock_hass_partial_availability, mock_entry, mock_entity_validator
    ):
        """Test that available entities are read correctly when some are unavailable."""
        state_reader = StateReader(
            mock_hass_partial_availability, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        # SOC is unavailable, should be default
        assert data.soc == 0.0

        # General price is unavailable, should be default
        assert data.general_price == 0.0

        # Operation mode should still be readable
        assert data.operation_mode == "autonomous"

        # Feed-in price should still be readable
        assert data.feed_in_price == 0.08

    def test_partial_availability_missing_entities(
        self, mock_hass_partial_availability, mock_entry, mock_entity_validator
    ):
        """Test that missing entities return defaults."""
        state_reader = StateReader(
            mock_hass_partial_availability, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        # Solar power entity is missing entirely
        assert data.solar_power_kw == 0.0

        # Price spike entity is missing
        assert data.price_spike is False


# =============================================================================
# MALFORMED STATE VALUE TESTS
# =============================================================================


class TestStateReaderMalformedValues:
    """Tests for handling malformed state values."""

    def test_non_numeric_soc_returns_default(
        self, mock_hass_with_states, mock_entry, mock_entity_validator
    ):
        """Test that non-numeric SOC returns default value."""
        # Override SOC with non-numeric value (using DEFAULT_ENTITY_IDS naming)
        mock_hass_with_states._mock_states.set(
            "sensor.my_home_percentage_charged", "not_a_number", {}
        )

        state_reader = StateReader(
            mock_hass_with_states, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert data.soc == 0.0

    def test_non_numeric_price_returns_default(
        self, mock_hass_with_states, mock_entry, mock_entity_validator
    ):
        """Test that non-numeric price returns default value."""
        mock_hass_with_states._mock_states.set(
            "sensor.100h_general_price", "invalid", {}
        )

        state_reader = StateReader(
            mock_hass_with_states, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert data.general_price == 0.0

    def test_empty_string_soc_returns_default(
        self, mock_hass_with_states, mock_entry, mock_entity_validator
    ):
        """Test that empty string SOC returns default value."""
        mock_hass_with_states._mock_states.set(
            "sensor.my_home_percentage_charged", "", {}
        )

        state_reader = StateReader(
            mock_hass_with_states, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert data.soc == 0.0

    def test_negative_soc_is_accepted(
        self, mock_hass_with_states, mock_entry, mock_entity_validator
    ):
        """Test that negative SOC values are accepted (edge case)."""
        mock_hass_with_states._mock_states.set(
            "sensor.my_home_percentage_charged", "-5.0", {}
        )

        state_reader = StateReader(
            mock_hass_with_states, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        # Negative SOC is technically a valid float, so it should be read
        # (validation happens elsewhere)
        assert data.soc == -5.0

    def test_very_large_soc_is_accepted(
        self, mock_hass_with_states, mock_entry, mock_entity_validator
    ):
        """Test that very large SOC values are accepted (edge case)."""
        mock_hass_with_states._mock_states.set(
            "sensor.my_home_percentage_charged", "999999.99", {}
        )

        state_reader = StateReader(
            mock_hass_with_states, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        # Should be read as-is; validation happens elsewhere
        assert data.soc == 999999.99


# =============================================================================
# ATTRIBUTE READING TESTS
# =============================================================================


class TestStateReaderAttributes:
    """Tests for reading entity attributes."""

    def test_missing_forecast_attribute_returns_empty_list(
        self, mock_hass_with_states, mock_entry, mock_entity_validator
    ):
        """Test that missing forecast attribute returns empty list."""
        # Set entity without forecasts attribute (using DEFAULT_ENTITY_IDS naming)
        mock_hass_with_states._mock_states.set(
            "sensor.100h_general_forecast",
            "on",
            {},  # No attributes
        )

        state_reader = StateReader(
            mock_hass_with_states, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert data.general_forecast == []

    def test_none_forecast_attribute_returns_empty_list(
        self, mock_hass_with_states, mock_entry, mock_entity_validator
    ):
        """Test that None forecast attribute returns empty list."""
        mock_hass_with_states._mock_states.set(
            "sensor.100h_general_forecast",
            "on",
            {"forecasts": None},
        )

        state_reader = StateReader(
            mock_hass_with_states, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert data.general_forecast == []

    def test_valid_forecast_attribute_is_read(
        self, mock_hass_with_forecasts, mock_entry, mock_entity_validator
    ):
        """Test that valid forecast attribute is read correctly."""
        state_reader = StateReader(
            mock_hass_with_forecasts, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        # Should have forecast data
        assert len(data.general_forecast) > 0
        assert len(data.feed_in_forecast) > 0


# =============================================================================
# PARAMETRIZED AVAILABILITY TESTS
# =============================================================================


class TestStateReaderParametrized:
    """Parametrized tests for various availability scenarios."""

    def test_read_all_external_state_never_crashes(
        self, mock_hass_various_states, mock_entry, mock_entity_validator
    ):
        """Test that read_all_external_state never crashes regardless of availability."""
        state_reader = StateReader(
            mock_hass_various_states, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()

        # Should never raise an exception
        state_reader.read_all_external_state(data)

        # Data should be populated (with defaults if unavailable/missing)
        assert isinstance(data.soc, float)
        assert isinstance(data.operation_mode, str)
        assert isinstance(data.general_price, float)

    def test_soc_value_available(
        self, mock_hass_with_states, mock_entry, mock_entity_validator
    ):
        """Test that SOC value is correct when entity is available."""
        state_reader = StateReader(
            mock_hass_with_states, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert data.soc == 50.0

    def test_soc_value_unavailable(
        self, mock_hass_unavailable_entities, mock_entry, mock_entity_validator
    ):
        """Test that SOC value is default when entity is unavailable."""
        state_reader = StateReader(
            mock_hass_unavailable_entities, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert data.soc == 0.0

    def test_soc_value_unknown(
        self, mock_hass_unknown_entities, mock_entry, mock_entity_validator
    ):
        """Test that SOC value is default when entity is unknown."""
        state_reader = StateReader(
            mock_hass_unknown_entities, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert data.soc == 0.0

    def test_soc_value_missing(
        self, mock_hass_missing_entities, mock_entry, mock_entity_validator
    ):
        """Test that SOC value is default when entity is missing."""
        state_reader = StateReader(
            mock_hass_missing_entities, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert data.soc == 0.0


# =============================================================================
# SOLCAST FORECAST EDGE CASES
# =============================================================================


class TestStateReaderSolcastForecasts:
    """Tests for Solcast forecast reading edge cases."""

    def test_solcast_unavailable_returns_empty_list(
        self, mock_hass_unavailable_entities, mock_entry, mock_entity_validator
    ):
        """Test that unavailable Solcast entity returns empty list."""
        # Add Solcast entities to unavailable states
        mock_hass_unavailable_entities._mock_states.set_unavailable(
            "sensor.solcast_today"
        )
        mock_hass_unavailable_entities._mock_states.set_unavailable(
            "sensor.solcast_tomorrow"
        )

        state_reader = StateReader(
            mock_hass_unavailable_entities, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert data.solcast_today == []
        assert data.solcast_tomorrow == []

    def test_solcast_missing_attribute_returns_empty_list(
        self, mock_hass_with_states, mock_entry, mock_entity_validator
    ):
        """Test that Solcast without forecast attribute returns empty list."""
        mock_hass_with_states._mock_states.set(
            "sensor.solcast_today",
            "0",
            {"friendly_name": "Solcast"},  # No forecast attribute
        )

        state_reader = StateReader(
            mock_hass_with_states, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert data.solcast_today == []

    def test_solcast_with_detailed_forecast_attribute(
        self, mock_hass_with_states, mock_entry, mock_entity_validator
    ):
        """Test reading Solcast with detailedForecast attribute."""
        forecast_data = [
            {"period_start": "2026-02-16T06:00:00", "pv_estimate": 1.5},
            {"period_start": "2026-02-16T07:00:00", "pv_estimate": 2.5},
        ]

        mock_hass_with_states._mock_states.set(
            "sensor.solcast_today",
            "2",
            {"detailedForecast": forecast_data},
        )

        state_reader = StateReader(
            mock_hass_with_states, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert len(data.solcast_today) == 2

    def test_solcast_with_detailed_hourly_attribute(
        self, mock_hass_with_states, mock_entry, mock_entity_validator
    ):
        """Test reading Solcast with detailedHourly attribute."""
        forecast_data = [
            {"period_start": "2026-02-16T06:00:00", "pv_estimate": 0.5},
        ]

        mock_hass_with_states._mock_states.set(
            "sensor.solcast_today",
            "1",
            {"detailedHourly": forecast_data},
        )

        state_reader = StateReader(
            mock_hass_with_states, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert len(data.solcast_today) == 1

    def test_solcast_with_forecast_attribute(
        self, mock_hass_with_states, mock_entry, mock_entity_validator
    ):
        """Test reading Solcast with forecast attribute."""
        forecast_data = [
            {"start": "2026-02-16T06:00:00", "pv_estimate": 3.0},
        ]

        mock_hass_with_states._mock_states.set(
            "sensor.solcast_today",
            "1",
            {"forecast": forecast_data},
        )

        state_reader = StateReader(
            mock_hass_with_states, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert len(data.solcast_today) == 1


# =============================================================================
# PRICE SPIKE EDGE CASES
# =============================================================================


class TestStateReaderPriceSpike:
    """Tests for price spike reading."""

    def test_price_spike_on(
        self, mock_hass_price_spike, mock_entry, mock_entity_validator
    ):
        """Test reading price spike when it's on."""
        state_reader = StateReader(
            mock_hass_price_spike, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert data.price_spike is True

    def test_price_spike_off(self, state_reader):
        """Test reading price spike when it's off."""
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        assert data.price_spike is False

    def test_price_spike_unavailable(self, state_reader_unavailable):
        """Test reading price spike when entity is unavailable."""
        data = CoordinatorData()
        state_reader_unavailable.read_all_external_state(data)

        assert data.price_spike is False

    def test_price_spike_missing(self, state_reader_missing):
        """Test reading price spike when entity is missing."""
        data = CoordinatorData()
        state_reader_missing.read_all_external_state(data)

        assert data.price_spike is False


# =============================================================================
# INTEGRATION-STYLE TESTS
# =============================================================================


class TestStateReaderIntegration:
    """Integration-style tests combining multiple scenarios."""

    def test_full_state_read_with_all_entities(
        self, mock_hass_with_forecasts, mock_entry, mock_entity_validator
    ):
        """Test reading all entities with complete data."""
        state_reader = StateReader(
            mock_hass_with_forecasts, mock_entry, mock_entity_validator
        )
        data = CoordinatorData()
        state_reader.read_all_external_state(data)

        # Verify all expected fields are populated
        assert data.soc == 50.0
        assert data.operation_mode == "autonomous"
        assert data.backup_reserve == 20.0
        assert data.grid_power_kw == 0.0
        assert data.load_power_kw == 0.5
        assert data.solar_power_kw == 3.0
        assert data.battery_power_kw == -2.5
        assert data.general_price == 0.25
        assert data.feed_in_price == 0.08
        assert data.price_spike is False

        # Forecasts should be populated
        assert len(data.general_forecast) > 0
        assert len(data.feed_in_forecast) > 0
        assert len(data.solcast_today) > 0
        assert len(data.solcast_tomorrow) > 0

    def test_state_read_idempotent(self, state_reader):
        """Test that reading state multiple times is idempotent."""
        data = CoordinatorData()

        state_reader.read_all_external_state(data)
        soc1 = data.soc
        price1 = data.general_price

        state_reader.read_all_external_state(data)
        soc2 = data.soc
        price2 = data.general_price

        assert soc1 == soc2
        assert price1 == price2

    def test_state_read_clears_previous_data(
        self, mock_hass_with_states, mock_entry, mock_entity_validator
    ):
        """Test that reading state overwrites previous data."""
        state_reader = StateReader(
            mock_hass_with_states, mock_entry, mock_entity_validator
        )

        # Set up data with non-default values
        data = CoordinatorData()
        data.soc = 99.0
        data.general_price = 5.0
        data.operation_mode = "backup"

        # Read state (should overwrite)
        state_reader.read_all_external_state(data)

        # Values should be from mock states, not previous values
        assert data.soc == 50.0
        assert data.general_price == 0.25
        assert data.operation_mode == "autonomous"

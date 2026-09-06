"""Tests for config flow schema builders."""

import pytest
import voluptuous as vol

from custom_components.localshift.config_flow.schemas import (
    build_pricing_schema,
    build_pricing_source_schema,
    build_solcast_schema,
    build_user_schema,
)
from custom_components.localshift.const import (
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_GENERAL_FORECAST,
    PRICING_SOURCE_AMBER,
    PRICING_SOURCE_AMBER_EXPRESS,
)


def schema_defaults(schema: vol.Schema) -> dict:
    """Extract the callable defaults from a voluptuous schema.

    Issue #955: defaults are stored as marker attributes and (in this module)
    as zero-argument callables, so calling them yields the value the frontend
    would pre-fill the form with.
    """
    return {key.schema: key.default() for key in schema.schema}


def assert_prefilled_form_validates(schema: vol.Schema) -> None:
    """Assert the schema accepts its own pre-filled defaults.

    A schema whose default value is rejected by its own validator can never
    be submitted without the user editing the offending field, which is the
    bug reported in issue #955.
    """
    schema(schema_defaults(schema))


class TestBuildUserSchema:
    """Tests for build_user_schema."""

    def test_returns_voluptuous_schema(self):
        result = build_user_schema()
        assert isinstance(result, vol.Schema)

    def test_with_defaults(self):
        defaults = {"teslemetry_operation_mode": "select.test"}
        result = build_user_schema(defaults=defaults)
        assert isinstance(result, vol.Schema)


class TestBuildPricingSourceSchema:
    """Tests for build_pricing_source_schema."""

    def test_returns_voluptuous_schema(self):
        result = build_pricing_source_schema()
        assert isinstance(result, vol.Schema)

    def test_with_defaults(self):
        defaults = {"pricing_data_source": "amber"}
        result = build_pricing_source_schema(defaults=defaults)
        assert isinstance(result, vol.Schema)


class TestBuildPricingSchema:
    """Tests for build_pricing_schema."""

    def test_returns_voluptuous_schema_amber(self):
        result = build_pricing_schema(pricing_source=PRICING_SOURCE_AMBER)
        assert isinstance(result, vol.Schema)

    def test_returns_voluptuous_schema_amber_express(self):
        result = build_pricing_schema(pricing_source=PRICING_SOURCE_AMBER_EXPRESS)
        assert isinstance(result, vol.Schema)

    def test_forecast_fields_present_for_amber(self):
        result = build_pricing_schema(pricing_source=PRICING_SOURCE_AMBER)
        schema_dict = result.schema
        field_keys = [k.schema for k in schema_dict.keys()]
        assert any("forecast" in str(k) for k in field_keys)

    def test_forecast_fields_optional_for_amber_express(self):
        result = build_pricing_schema(pricing_source=PRICING_SOURCE_AMBER_EXPRESS)
        assert isinstance(result, vol.Schema)

    def test_amber_preserves_existing_forecast_defaults(self):
        """Issue #955: stored forecast mappings must survive as form defaults."""
        defaults = {
            CONF_PRICING_GENERAL_FORECAST: "sensor.100h_general_forecast",
            CONF_PRICING_FEED_IN_FORECAST: "sensor.100h_feed_in_forecast",
        }
        schema = build_pricing_schema(defaults=defaults, pricing_source=PRICING_SOURCE_AMBER)

        extracted = schema_defaults(schema)
        assert extracted[CONF_PRICING_GENERAL_FORECAST] == "sensor.100h_general_forecast"
        assert extracted[CONF_PRICING_FEED_IN_FORECAST] == "sensor.100h_feed_in_forecast"

        assert_prefilled_form_validates(schema)

    def test_amber_forecast_defaults_fall_back_to_prefix_when_missing(self):
        """Issue #955: absent forecast keys derive a usable default entity id."""
        schema = build_pricing_schema(
            defaults={"pricing_general_price": "sensor.100h_general_price"},
            pricing_source=PRICING_SOURCE_AMBER,
        )

        extracted = schema_defaults(schema)
        assert extracted[CONF_PRICING_GENERAL_FORECAST] == "sensor.100h_general_forecast"
        assert extracted[CONF_PRICING_FEED_IN_FORECAST] == "sensor.100h_feed_in_forecast"

        assert_prefilled_form_validates(schema)

    def test_amber_forecast_defaults_fall_back_when_empty_string(self):
        """Issue #955: an empty-string forecast value (Express legacy) falls back."""
        defaults = {
            CONF_PRICING_GENERAL_FORECAST: "",
            CONF_PRICING_FEED_IN_FORECAST: "",
        }
        schema = build_pricing_schema(defaults=defaults, pricing_source=PRICING_SOURCE_AMBER)

        extracted = schema_defaults(schema)
        assert extracted[CONF_PRICING_GENERAL_FORECAST] == "sensor.100h_general_forecast"
        assert extracted[CONF_PRICING_FEED_IN_FORECAST] == "sensor.100h_feed_in_forecast"

        assert_prefilled_form_validates(schema)

    def test_amber_express_forecast_fields_still_absent(self):
        """Regression: Express must keep the forecast fields off the form."""
        defaults = {
            CONF_PRICING_GENERAL_FORECAST: "sensor.100h_general_forecast",
            CONF_PRICING_FEED_IN_FORECAST: "sensor.100h_feed_in_forecast",
        }
        schema = build_pricing_schema(
            defaults=defaults, pricing_source=PRICING_SOURCE_AMBER_EXPRESS
        )

        field_keys = {key.schema for key in schema.schema}
        assert CONF_PRICING_GENERAL_FORECAST not in field_keys
        assert CONF_PRICING_FEED_IN_FORECAST not in field_keys

        assert_prefilled_form_validates(schema)

    def test_user_input_overrides_defaults(self):
        """user_input should take precedence over the supplied defaults."""
        defaults = {
            CONF_PRICING_GENERAL_FORECAST: "sensor.100h_general_forecast",
            CONF_PRICING_FEED_IN_FORECAST: "sensor.100h_feed_in_forecast",
        }
        user_input = {
            CONF_PRICING_GENERAL_FORECAST: "sensor.custom_general_forecast",
            CONF_PRICING_FEED_IN_FORECAST: "sensor.custom_feed_in_forecast",
        }
        schema = build_pricing_schema(
            defaults=defaults,
            user_input=user_input,
            pricing_source=PRICING_SOURCE_AMBER,
        )

        extracted = schema_defaults(schema)
        assert extracted[CONF_PRICING_GENERAL_FORECAST] == "sensor.custom_general_forecast"
        assert extracted[CONF_PRICING_FEED_IN_FORECAST] == "sensor.custom_feed_in_forecast"


class TestBuildSolcastSchema:
    """Tests for build_solcast_schema."""

    def test_returns_voluptuous_schema(self):
        notify_services = ["notify.mobile_app"]
        weather_entities = ["weather.home"]
        result = build_solcast_schema(
            notify_services=notify_services,
            weather_entities=weather_entities,
        )
        assert isinstance(result, vol.Schema)

    def test_with_include_notify_false(self):
        notify_services = ["notify.mobile_app"]
        weather_entities = ["weather.home"]
        result = build_solcast_schema(
            notify_services=notify_services,
            weather_entities=weather_entities,
            include_notify=False,
        )
        assert isinstance(result, vol.Schema)

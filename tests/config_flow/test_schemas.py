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
    CONF_NOTIFY_SERVICE,
    CONF_PRICING_GENERAL_PRICE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_TESLEMETRY_SOC,
    CONF_WEATHER_ENTITY,
    PRICING_SOURCE_AMBER,
    PRICING_SOURCE_AMBER_EXPRESS,
)


class TestBuildUserSchema:
    """Tests for build_user_schema."""

    def test_returns_voluptuous_schema(self):
        result = build_user_schema()
        assert isinstance(result, vol.Schema)

    def test_with_defaults(self):
        defaults = {"teslemetry_operation_mode": "select.test"}
        result = build_user_schema(defaults=defaults)
        assert isinstance(result, vol.Schema)

    def test_with_user_input_overrides_defaults(self):
        user_input = {CONF_TESLEMETRY_SOC: "sensor.custom_soc"}
        result = build_user_schema(user_input=user_input)
        schema_dict = result.schema
        soc_key = next(k for k in schema_dict.keys() if k.schema == CONF_TESLEMETRY_SOC)
        assert soc_key.schema == CONF_TESLEMETRY_SOC


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

    def test_with_user_input_defaults(self):
        user_input = {CONF_PRICING_GENERAL_PRICE: "sensor.custom_price"}
        result = build_pricing_schema(user_input=user_input)
        assert isinstance(result, vol.Schema)


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

    def test_with_user_input_defaults(self):
        notify_services = ["notify.mobile_app"]
        weather_entities = ["weather.home"]
        user_input = {
            CONF_NOTIFY_SERVICE: "notify.custom",
            CONF_WEATHER_ENTITY: "weather.custom",
            CONF_SOLCAST_FORECAST_TODAY: "sensor.custom_today",
        }
        result = build_solcast_schema(
            notify_services=notify_services,
            weather_entities=weather_entities,
            user_input=user_input,
        )
        schema_dict = result.schema
        today_key = next(
            k for k in schema_dict.keys() if k.schema == CONF_SOLCAST_FORECAST_TODAY
        )
        assert today_key.schema == CONF_SOLCAST_FORECAST_TODAY

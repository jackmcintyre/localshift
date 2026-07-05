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


class TestAdvancedSchemaBounds:
    """Tests that the advanced-options slider bounds match THRESHOLD_RANGES.

    Issue #898: the options-flow NumberSelector for target_penalty capped at
    0.100 while THRESHOLD_RANGES (used by the NumberEntity) allowed 0.200. A
    user who set 0.15 via the number entity would be silently clamped to 0.100
    on re-opening the options flow. Assert every advanced-schema selector's max
    matches the corresponding THRESHOLD_RANGES entry so this drift can't recur.
    """

    @pytest.mark.parametrize(
        "conf_key",
        [
            "cheap_price_percentile",
            "max_pre_charge_price",
            "battery_target",
            "minimum_target_soc",
            "target_penalty",
            "min_cycle_saving",
        ],
    )
    def test_advanced_schema_selector_max_matches_threshold_ranges(
        self, conf_key: str
    ):
        from custom_components.localshift.config_flow import LocalShiftOptionsFlow
        from custom_components.localshift.const import THRESHOLD_RANGES

        flow = LocalShiftOptionsFlow()
        schema = flow._build_advanced_schema({})

        # Find the selector for this conf_key and compare its max to const.
        expected_max = THRESHOLD_RANGES[conf_key]["max"]
        for field, selector_obj in schema.schema.items():
            # voluptuous Required/Optional markers expose the key via .schema
            field_key = getattr(field, "schema", field)
            if field_key == conf_key:
                # selector_obj.config is the NumberSelectorConfig dict
                actual_max = selector_obj.config["max"]
                assert actual_max == expected_max, (
                    f"{conf_key}: options-flow max ({actual_max}) != "
                    f"THRESHOLD_RANGES max ({expected_max})"
                )
                return
        pytest.fail(f"{conf_key} not found in advanced schema")

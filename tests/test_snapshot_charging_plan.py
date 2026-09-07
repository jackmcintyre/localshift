"""Unit tests for scripts/snapshot_charging_plan.py.

Issue #977: this script has no other executable check, and its job is
specifically to read live-looking entity ids — so a missing/renamed entity
must be caught here rather than silently rendering "unknown" everywhere.
"""

from unittest.mock import MagicMock

from scripts.snapshot_charging_plan import (
    SnapshotGenerator,
    safe_attr,
    safe_state,
)


class TestSafeState:
    """Tests for the safe_state helper."""

    def test_safe_state_returns_default_when_entity_none(self):
        assert safe_state(None) == "unknown"

    def test_safe_state_returns_custom_default(self):
        assert safe_state(None, default="missing") == "missing"

    def test_safe_state_returns_state_value(self):
        assert safe_state({"state": "42.5"}) == "42.5"


class TestSafeAttr:
    """Tests for the safe_attr helper."""

    def test_safe_attr_returns_default_when_entity_none(self):
        assert safe_attr(None, "foo", "bar") == "bar"

    def test_safe_attr_returns_default_when_attr_missing(self):
        entity = {"attributes": {}}
        assert safe_attr(entity, "foo", "bar") == "bar"

    def test_safe_attr_returns_value(self):
        entity = {"attributes": {"foo": 123}}
        assert safe_attr(entity, "foo", 0) == 123


class TestSnapshotGeneratorMissingEntities:
    """Tests for the Issue #977 missing-entity guard."""

    def test_get_entity_records_missing_id(self):
        client = MagicMock()
        client.get_state.return_value = None
        generator = SnapshotGenerator(client)

        generator.get_entity("sensor.localshift_does_not_exist")

        assert "sensor.localshift_does_not_exist" in generator._missing

    def test_get_entity_does_not_record_present_id(self):
        client = MagicMock()
        client.get_state.return_value = {"state": "42", "attributes": {}}
        generator = SnapshotGenerator(client)

        generator.get_entity("sensor.localshift_optimizer_plan")

        assert generator._missing == set()

    def test_get_entity_caches_missing_lookup(self):
        """A missing entity is only fetched once, not re-queried per read."""
        client = MagicMock()
        client.get_state.return_value = None
        generator = SnapshotGenerator(client)

        generator.get_entity("sensor.localshift_does_not_exist")
        generator.get_entity("sensor.localshift_does_not_exist")

        assert client.get_state.call_count == 1
        assert generator._missing == {"sensor.localshift_does_not_exist"}

    def test_state_and_attr_still_return_defaults_for_missing_entity(self):
        """A missing entity still renders as 'unknown' in the markdown body —
        the guard reports it separately rather than breaking generation."""
        client = MagicMock()
        client.get_state.return_value = None
        generator = SnapshotGenerator(client)

        assert generator.state("sensor.localshift_does_not_exist") == "unknown"
        assert generator.attr("sensor.localshift_does_not_exist", "foo", 0) == 0
        assert "sensor.localshift_does_not_exist" in generator._missing

    def test_multiple_missing_entities_all_recorded(self):
        client = MagicMock()
        client.get_state.return_value = None
        generator = SnapshotGenerator(client)

        generator.get_entity("sensor.localshift_forecast_daily")
        generator.get_entity("sensor.localshift_forecast_grid")

        assert generator._missing == {
            "sensor.localshift_forecast_daily",
            "sensor.localshift_forecast_grid",
        }


class TestForecastInfoUsesRenamedSensors:
    """Issue #977: sensor.localshift_forecast_daily was renamed in #447."""

    def test_forecast_info_reads_optimizer_plan_not_forecast_daily(self):
        def get_state(entity_id):
            if entity_id == "sensor.localshift_optimizer_plan":
                return {
                    "state": "12",
                    "attributes": {"total_slots": 96},
                }
            if entity_id == "sensor.localshift_forecast_status":
                return {
                    "state": "ready",
                    "attributes": {
                        "solcast_today_entries": 48,
                        "solcast_tomorrow_entries": 48,
                    },
                }
            return {"state": "0", "attributes": {}}

        client = MagicMock()
        client.get_state.side_effect = get_state
        generator = SnapshotGenerator(client)

        result = generator._forecast_info()

        assert "sensor.localshift_forecast_daily" not in generator._missing
        assert "Optimizer Plan Entries" in result
        assert "| 12 |" in result
        assert "| 96 |" in result
        assert "| 48 |" in result


class TestForecastTableUsesRenamedSensors:
    """Issue #977: sensor.localshift_forecast_grid was renamed in #447."""

    def test_forecast_table_reads_optimizer_plan_grid(self):
        def get_state(entity_id):
            if entity_id == "sensor.localshift_optimizer_plan":
                return {
                    "state": "1",
                    "attributes": {
                        "slots": [
                            {
                                "slot_idx": 0,
                                "action": "GRID_CHARGE",
                                "reason_code": "cheap_price",
                            }
                        ]
                    },
                }
            if entity_id == "sensor.localshift_optimizer_plan_grid":
                return {
                    "state": "0",
                    "attributes": {
                        "action_breakdown": {"GRID_CHARGE": 1},
                        "projected_import_kwh": 3.5,
                        "projected_export_kwh": 0.0,
                    },
                }
            if entity_id == "sensor.localshift_forecast_prices":
                return {
                    "state": "0.2",
                    "attributes": {
                        "buy_prices": [{"time": "00:00", "price": 0.2}],
                        "sell_prices": [{"time": "00:00", "price": 0.05}],
                    },
                }
            return {"state": "0", "attributes": {}}

        client = MagicMock()
        client.get_state.side_effect = get_state
        generator = SnapshotGenerator(client)

        result = generator._forecast_table()

        assert "sensor.localshift_forecast_grid" not in generator._missing
        assert "GRID_CHARGE" in result
        assert "cheap_price" in result
        assert "3.5" in result

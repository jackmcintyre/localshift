"""Tests for sensor.py async_setup_entry and backward compatibility."""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestSensorAsyncSetup:
    @pytest.fixture
    def mock_hass(self):
        return MagicMock()

    @pytest.fixture
    def mock_entry(self):
        entry = MagicMock()
        entry.runtime_data = MagicMock()
        return entry

    @pytest.fixture
    def mock_async_add_entities(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_async_setup_entry(
        self, mock_hass, mock_entry, mock_async_add_entities
    ):
        from custom_components.localshift.sensor import async_setup_entry

        await async_setup_entry(mock_hass, mock_entry, mock_async_add_entities)

        mock_async_add_entities.assert_called_once()
        entities = mock_async_add_entities.call_args[0][0]
        assert len(entities) == 35  # 32 + 3 new Solcast sensors (Issue #778)
        assert any(
            type(entity).__name__ == "LoadDeviationSensor" for entity in entities
        )

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_all_sensors(
        self, mock_hass, mock_entry, mock_async_add_entities
    ):
        from custom_components.localshift.sensor import async_setup_entry

        await async_setup_entry(mock_hass, mock_entry, mock_async_add_entities)

        entities = mock_async_add_entities.call_args[0][0]
        entity_class_names = [type(e).__name__ for e in entities]
        assert "EffectiveCheapPriceSensor" in entity_class_names
        assert "OptimizerPlanSensor" in entity_class_names
        assert "IntegrationStatusSensor" in entity_class_names
        assert "OptimizerAdvantageSensor" in entity_class_names


class TestSensorImports:
    def test_import_from_sensor(self):
        from custom_components.localshift.sensor import (
            EffectiveCheapPriceSensor,
            LocalShiftSensorBase,
        )

        assert EffectiveCheapPriceSensor is not None
        assert LocalShiftSensorBase is not None

    def test_all_exports(self):
        from custom_components.localshift import sensor

        for name in sensor.__all__:
            assert hasattr(sensor, name)

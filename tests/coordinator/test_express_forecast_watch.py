"""Amber Express: the coordinator neither watches nor names the unused
forecast mappings (issue #1210).

Under Express the forecast is read from the price entity's
``detailedForecast`` attribute, so:
- ``async_start`` must not subscribe to the two forecast entities, and the
  validator it builds must not track them;
- the synthetic-slot warning must point at the price entity's forecast
  attribute, not at a forecast entity nothing reads.
Under the base "amber" source both stay as they were.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.const import (
    CONF_PRICING_DATA_SOURCE,
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    PRICING_SOURCE_AMBER,
    PRICING_SOURCE_AMBER_EXPRESS,
)
from custom_components.localshift.coordinator.entity_monitor import EntityMonitor
from tests.coordinator.test_entity_monitor_synthetic_degradation import (
    _degraded_tracker,
    _mock_validator,
)

GENERAL_FORECAST = "sensor.general_forecast"
FEED_IN_FORECAST = "sensor.feed_in_forecast"


async def _start_and_capture_watched(coordinator) -> list[str]:
    with (
        patch(
            "custom_components.localshift.services.subscription_manager.async_track_state_change_event"
        ) as mock_track_state,
        patch(
            "custom_components.localshift.services.subscription_manager.async_track_time_interval"
        ) as mock_track_time,
        patch(
            "custom_components.localshift.services.subscription_manager.async_track_time_change"
        ) as mock_track_time_change,
    ):
        mock_track_state.return_value = MagicMock()
        mock_track_time.return_value = MagicMock()
        mock_track_time_change.return_value = MagicMock()

        await coordinator.async_start()

        return list(mock_track_state.call_args.args[1])


@pytest.mark.usefixtures("mock_storage")
class TestWatchedEntities:
    @pytest.mark.asyncio
    async def test_express_does_not_watch_forecast_entities(
        self, coordinator, mock_recorder
    ) -> None:
        coordinator.entry.data[CONF_PRICING_DATA_SOURCE] = PRICING_SOURCE_AMBER_EXPRESS

        watched = await _start_and_capture_watched(coordinator)

        assert GENERAL_FORECAST not in watched
        assert FEED_IN_FORECAST not in watched
        assert "sensor.general_price" in watched
        assert "sensor.feed_in_price" in watched

    @pytest.mark.asyncio
    async def test_express_validator_does_not_track_forecast_keys(
        self, coordinator, mock_recorder
    ) -> None:
        coordinator.entry.data[CONF_PRICING_DATA_SOURCE] = PRICING_SOURCE_AMBER_EXPRESS

        await _start_and_capture_watched(coordinator)

        tracked = coordinator._entity_validator.get_health_summary()["entities"]
        assert CONF_PRICING_GENERAL_FORECAST not in tracked
        assert CONF_PRICING_FEED_IN_FORECAST not in tracked
        assert CONF_PRICING_GENERAL_PRICE in tracked

    @pytest.mark.asyncio
    @pytest.mark.parametrize("source", [PRICING_SOURCE_AMBER, None])
    async def test_amber_still_watches_and_tracks_forecast_entities(
        self, coordinator, mock_recorder, source
    ) -> None:
        if source is not None:
            coordinator.entry.data[CONF_PRICING_DATA_SOURCE] = source

        watched = await _start_and_capture_watched(coordinator)

        assert GENERAL_FORECAST in watched
        assert FEED_IN_FORECAST in watched
        tracked = coordinator._entity_validator.get_health_summary()["entities"]
        assert CONF_PRICING_GENERAL_FORECAST in tracked
        assert CONF_PRICING_FEED_IN_FORECAST in tracked


# =============================================================================
# Synthetic-slot warning text
# =============================================================================

PRICE_ENTITY = "sensor.test_general_price"
FORECAST_ENTITY = "sensor.test_general_forecast"


def _monitor(pricing_source: str, **data_attrs) -> tuple[EntityMonitor, MagicMock]:
    mock_coordinator = MagicMock()
    mock_coordinator._entity_validator = _mock_validator()
    mock_coordinator.entry.entry_id = "cfg_test"
    mock_coordinator.entry.data = {CONF_PRICING_DATA_SOURCE: pricing_source}
    mock_coordinator.get_entity_id.side_effect = {
        CONF_PRICING_GENERAL_PRICE: PRICE_ENTITY,
        CONF_PRICING_GENERAL_FORECAST: FORECAST_ENTITY,
    }.get
    mock_data = MagicMock()
    mock_data.entity_warnings = []
    mock_data.integration_status = "ok"
    for key, value in data_attrs.items():
        setattr(mock_data, key, value)
    mock_coordinator.data = mock_data
    return EntityMonitor(mock_coordinator), mock_coordinator


class TestSyntheticSlotWarningText:
    def test_express_warning_names_price_entity_forecast_attribute(self) -> None:
        monitor, coordinator = _monitor(
            PRICING_SOURCE_AMBER_EXPRESS, synthetic_slot_health=_degraded_tracker()
        )

        monitor.check_entity_health()

        (warning,) = coordinator.data.entity_warnings
        assert f"detailedForecast attribute of {PRICE_ENTITY}" in warning
        assert FORECAST_ENTITY not in warning

    def test_amber_warning_still_names_forecast_entity(self) -> None:
        monitor, coordinator = _monitor(
            PRICING_SOURCE_AMBER, synthetic_slot_health=_degraded_tracker()
        )

        monitor.check_entity_health()

        (warning,) = coordinator.data.entity_warnings
        assert warning.endswith(f"check {FORECAST_ENTITY}")

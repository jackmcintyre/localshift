import pytest
from unittest.mock import AsyncMock, patch
from custom_components.localshift.coordinator import LocalShiftCoordinator


class TestPriceChangeDetection:
    def test_price_change_detection(self):
        # Mock the data object with price attributes
        mock_data = AsyncMock()
        mock_data.general_price = 0.25
        mock_data.feed_in_price = 0.10

        # Create coordinator with mock data
        coordinator = LocalShiftCoordinator(hass=None, entry=None)
        coordinator.data = mock_data

        # Test price change detection
        with patch(
            "custom_components.localshift.coordinator._LOGGER.debug"
        ) as mock_debug:
            # First call should detect change (initial state)
            assert coordinator._has_price_changed() is True
            mock_debug.assert_called_once()

            # Reset mock and test no log on unchanged price
            mock_debug.reset_mock()
            assert coordinator._has_price_changed() is False
            mock_debug.assert_not_called()

            # Changed general price should detect change
            mock_data.general_price = 0.30
            assert coordinator._has_price_changed() is True

            # Changed feed_in price should detect change
            mock_data.feed_in_price = 0.15
            assert coordinator._has_price_changed() is True

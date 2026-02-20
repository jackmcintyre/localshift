"""Realistic Home Assistant entity state simulation for tests.

This module provides mock classes and factories that simulate real Home Assistant
entity behavior, including:
- State objects with .state and .attributes properties
- Entity availability states (available, unavailable, unknown)
- Realistic attribute structures for different entity types

The goal is to make unit tests catch bugs that would only appear in production
by simulating real HA behavior rather than using oversimplified mocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class MockState:
    """Mock of homeassistant.core.State with realistic behavior.

    This class mimics the behavior of Home Assistant's State class,
    including the state value, attributes, and common edge cases.
    """

    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)
    last_changed: datetime | None = None
    last_updated: datetime | None = None

    def __post_init__(self):
        """Set default timestamps if not provided."""
        if self.last_changed is None:
            self.last_changed = datetime.now()
        if self.last_updated is None:
            self.last_updated = self.last_changed

    @property
    def domain(self) -> str:
        """Return the domain of the entity (e.g., 'sensor' from 'sensor.temperature')."""
        return self.entity_id.split(".")[0]

    @property
    def object_id(self) -> str:
        """Return the object ID (e.g., 'temperature' from 'sensor.temperature')."""
        return self.entity_id.split(".", 1)[1]

    def as_dict(self) -> dict[str, Any]:
        """Return state as dictionary (mimics HA State.as_dict())."""
        return {
            "entity_id": self.entity_id,
            "state": self.state,
            "attributes": self.attributes,
            "last_changed": self.last_changed.isoformat()
            if self.last_changed
            else None,
            "last_updated": self.last_updated.isoformat()
            if self.last_updated
            else None,
        }


class MockStates:
    """Mock of homeassistant.core.States class.

    Provides a realistic simulation of the hass.states interface,
    including get(), async_all(), and entity availability handling.
    """

    def __init__(self, states: dict[str, MockState] | None = None):
        """Initialize with optional pre-populated states.

        Args:
            states: Dictionary mapping entity_id to MockState objects
        """
        self._states: dict[str, MockState] = states or {}

    def get(self, entity_id: str) -> MockState | None:
        """Get state for an entity, returning None if not found.

        This mimics hass.states.get() behavior exactly.
        """
        return self._states.get(entity_id)

    def async_all(self, domain_filter: str | None = None) -> list[MockState]:
        """Return all states, optionally filtered by domain.

        Args:
            domain_filter: Optional domain to filter by (e.g., 'sensor')

        Returns:
            List of MockState objects
        """
        if domain_filter is None:
            return list(self._states.values())

        return [
            state
            for state in self._states.values()
            if state.entity_id.startswith(f"{domain_filter}.")
        ]

    def set(
        self, entity_id: str, state: str, attributes: dict[str, Any] | None = None
    ) -> None:
        """Set or update an entity's state."""
        self._states[entity_id] = MockState(
            entity_id=entity_id,
            state=state,
            attributes=attributes or {},
        )

    def remove(self, entity_id: str) -> bool:
        """Remove an entity. Returns True if entity existed."""
        if entity_id in self._states:
            del self._states[entity_id]
            return True
        return False

    def set_unavailable(self, entity_id: str) -> None:
        """Mark an entity as unavailable."""
        self._states[entity_id] = MockState(
            entity_id=entity_id,
            state="unavailable",
            attributes={},
        )

    def set_unknown(self, entity_id: str) -> None:
        """Mark an entity as unknown."""
        self._states[entity_id] = MockState(
            entity_id=entity_id,
            state="unknown",
            attributes={},
        )


# =============================================================================
# Entity State Factories
# =============================================================================


def create_sensor_state(
    entity_id: str,
    value: float | int | str,
    unit: str | None = None,
    device_class: str | None = None,
    extra_attributes: dict[str, Any] | None = None,
) -> MockState:
    """Create a realistic sensor state.

    Args:
        entity_id: The entity ID (e.g., 'sensor.temperature')
        value: The sensor value
        unit: Unit of measurement
        device_class: Device class (e.g., 'temperature', 'power')
        extra_attributes: Additional attributes

    Returns:
        MockState with realistic sensor attributes
    """
    attributes: dict[str, Any] = {}
    if unit:
        attributes["unit_of_measurement"] = unit
    if device_class:
        attributes["device_class"] = device_class
    if extra_attributes:
        attributes.update(extra_attributes)

    return MockState(
        entity_id=entity_id,
        state=str(value),
        attributes=attributes,
    )


def create_powerwall_soc_state(
    value: float, entity_id: str = "sensor.tesla_powerwall_soc"
) -> MockState:
    """Create a realistic Tesla Powerwall SOC sensor state.

    Args:
        value: State of charge percentage (0-100)
        entity_id: Entity ID for the SOC sensor

    Returns:
        MockState with realistic Powerwall SOC attributes
    """
    return create_sensor_state(
        entity_id=entity_id,
        value=value,
        unit="%",
        device_class="battery",
        extra_attributes={
            "friendly_name": "Powerwall State of Charge",
            "icon": "mdi:battery",
        },
    )


def create_powerwall_operation_mode_state(
    mode: str = "autonomous",
    entity_id: str = "sensor.tesla_powerwall_operation_mode",
) -> MockState:
    """Create a realistic Tesla Powerwall operation mode sensor state.

    Args:
        mode: Operation mode (autonomous, backup, self_consumption, etc.)
        entity_id: Entity ID for the operation mode sensor

    Returns:
        MockState with realistic operation mode attributes
    """
    return MockState(
        entity_id=entity_id,
        state=mode,
        attributes={
            "friendly_name": "Powerwall Operation Mode",
            "icon": "mdi:home-battery",
            "options": [
                "self_consumption",
                "autonomous",
                "backup",
                "economy",
            ],
        },
    )


def create_power_state(
    value: float,
    entity_id: str,
    friendly_name: str,
) -> MockState:
    """Create a realistic power sensor state.

    Args:
        value: Power value in kW
        entity_id: Entity ID for the power sensor
        friendly_name: Friendly name for the sensor

    Returns:
        MockState with realistic power sensor attributes
    """
    return create_sensor_state(
        entity_id=entity_id,
        value=value,
        unit="kW",
        device_class="power",
        extra_attributes={
            "friendly_name": friendly_name,
            "icon": "mdi:flash",
        },
    )


def create_price_state(
    value: float,
    entity_id: str,
    friendly_name: str,
) -> MockState:
    """Create a realistic price sensor state.

    Args:
        value: Price value in $/kWh
        entity_id: Entity ID for the price sensor
        friendly_name: Friendly name for the sensor

    Returns:
        MockState with realistic price sensor attributes
    """
    return create_sensor_state(
        entity_id=entity_id,
        value=value,
        unit="$/kWh",
        extra_attributes={
            "friendly_name": friendly_name,
            "icon": "mdi:currency-usd",
        },
    )


def create_solcast_forecast_state(
    forecasts: list[dict[str, Any]],
    entity_id: str = "sensor.solcast_today",
    attribute_name: str = "detailedForecast",
) -> MockState:
    """Create a realistic Solcast forecast sensor state.

    Args:
        forecasts: List of forecast dictionaries with period_start and pv_estimate
        entity_id: Entity ID for the Solcast sensor
        attribute_name: Attribute containing the forecast list

    Returns:
        MockState with realistic Solcast forecast attributes
    """
    return MockState(
        entity_id=entity_id,
        state=str(len(forecasts)),
        attributes={
            "friendly_name": "Solcast Forecast",
            attribute_name: forecasts,
            "icon": "mdi:solar-power",
        },
    )


def create_amber_price_forecast_state(
    forecasts: list[dict[str, Any]],
    entity_id: str,
    friendly_name: str,
) -> MockState:
    """Create a realistic Amber price forecast sensor state.

    Args:
        forecasts: List of forecast dictionaries with prices
        entity_id: Entity ID for the forecast sensor
        friendly_name: Friendly name for the sensor

    Returns:
        MockState with realistic Amber forecast attributes
    """
    return MockState(
        entity_id=entity_id,
        state="on",  # Amber forecast sensors are typically binary-ish
        attributes={
            "friendly_name": friendly_name,
            "forecasts": forecasts,
            "icon": "mdi:currency-usd",
        },
    )


def create_binary_sensor_state(
    entity_id: str,
    is_on: bool,
    device_class: str | None = None,
    extra_attributes: dict[str, Any] | None = None,
) -> MockState:
    """Create a realistic binary sensor state.

    Args:
        entity_id: The entity ID (e.g., 'binary_sensor.price_spike')
        is_on: Whether the binary sensor is on
        device_class: Device class (e.g., 'power', 'battery_charging')
        extra_attributes: Additional attributes

    Returns:
        MockState with realistic binary sensor attributes
    """
    attributes: dict[str, Any] = {}
    if device_class:
        attributes["device_class"] = device_class
    if extra_attributes:
        attributes.update(extra_attributes)

    return MockState(
        entity_id=entity_id,
        state="on" if is_on else "off",
        attributes=attributes,
    )


def create_sun_state(
    elevation: float = 45.0,
    rising: bool = True,
    entity_id: str = "sun.sun",
) -> MockState:
    """Create a realistic sun.sun state.

    Args:
        elevation: Sun elevation in degrees
        rising: Whether the sun is rising
        entity_id: Entity ID for the sun entity

    Returns:
        MockState with realistic sun attributes
    """
    return MockState(
        entity_id=entity_id,
        state="above_horizon" if elevation > 0 else "below_horizon",
        attributes={
            "elevation": elevation,
            "rising": rising,
            "azimuth": 180.0,
            "next_dawn": "2026-02-17T05:30:00",
            "next_dusk": "2026-02-17T19:30:00",
            "next_noon": "2026-02-17T12:00:00",
            "next_rising": "2026-02-17T06:00:00",
            "next_setting": "2026-02-17T18:00:00",
            "friendly_name": "Sun",
            "icon": "mdi:white-balance-sunny",
        },
    )


# =============================================================================
# Pre-built Entity Sets
# =============================================================================


def create_default_entity_states(
    soc: float = 50.0,
    operation_mode: str = "autonomous",
    backup_reserve: float = 20.0,
    grid_power_kw: float = 0.0,
    load_power_kw: float = 0.5,
    solar_power_kw: float = 3.0,
    battery_power_kw: float = -2.5,
    general_price: float = 0.25,
    feed_in_price: float = 0.08,
    price_spike: bool = False,
    allow_export: str = "pv_only",
) -> dict[str, MockState]:
    """Create a complete set of entity states for LocalShift testing.

    This creates realistic states for all entities that LocalShift monitors,
    with sensible defaults that can be overridden for specific test scenarios.

    Entity IDs match DEFAULT_ENTITY_IDS in const.py:
    - Teslemetry: my_home_* entities
    - Pricing: 100h_* entities (Amber Electric)
    - Solcast: solcast_pv_forecast_* entities

    Args:
        soc: Battery state of charge percentage
        operation_mode: Powerwall operation mode
        backup_reserve: Backup reserve percentage
        grid_power_kw: Grid power in kW (positive = import)
        load_power_kw: Load power in kW
        solar_power_kw: Solar power in kW
        battery_power_kw: Battery power in kW (positive = charging)
        general_price: General usage price in $/kWh
        feed_in_price: Feed-in tariff in $/kWh
        price_spike: Whether there's a price spike
        allow_export: Allow export state (pv_only, battery_ok)

    Returns:
        Dictionary mapping entity IDs to MockState objects
    """
    return {
        # Teslemetry entities (my_home_* naming)
        "sensor.my_home_percentage_charged": create_powerwall_soc_state(
            soc, entity_id="sensor.my_home_percentage_charged"
        ),
        "select.my_home_operation_mode": MockState(
            entity_id="select.my_home_operation_mode",
            state=operation_mode,
            attributes={
                "friendly_name": "Operation Mode",
                "icon": "mdi:home-battery",
                "options": [
                    "self_consumption",
                    "autonomous",
                    "backup",
                ],
            },
        ),
        "number.my_home_backup_reserve": create_sensor_state(
            "number.my_home_backup_reserve",
            backup_reserve,
            unit="%",
        ),
        "sensor.my_home_grid_power": create_power_state(
            grid_power_kw,
            "sensor.my_home_grid_power",
            "Grid Power",
        ),
        "sensor.my_home_load_power": create_power_state(
            load_power_kw,
            "sensor.my_home_load_power",
            "Load Power",
        ),
        "sensor.my_home_solar_power": create_power_state(
            solar_power_kw,
            "sensor.my_home_solar_power",
            "Solar Power",
        ),
        "sensor.my_home_battery_power": create_power_state(
            battery_power_kw,
            "sensor.my_home_battery_power",
            "Battery Power",
        ),
        "select.my_home_allow_export": MockState(
            entity_id="select.my_home_allow_export",
            state=allow_export,
            attributes={
                "friendly_name": "Allow Export",
                "options": ["pv_only", "battery_ok"],
            },
        ),
        # Pricing entities (100h_* naming for Amber Electric)
        "sensor.100h_general_price": create_price_state(
            general_price,
            "sensor.100h_general_price",
            "General Price",
        ),
        "sensor.100h_feed_in_price": create_price_state(
            feed_in_price,
            "sensor.100h_feed_in_price",
            "Feed-in Price",
        ),
        "binary_sensor.100h_price_spike": create_binary_sensor_state(
            "binary_sensor.100h_price_spike",
            price_spike,
        ),
        # Sun entity
        "sun.sun": create_sun_state(),
    }


def create_unavailable_entity_states(entity_ids: list[str]) -> dict[str, MockState]:
    """Create unavailable states for specified entities.

    Args:
        entity_ids: List of entity IDs to mark as unavailable

    Returns:
        Dictionary mapping entity IDs to unavailable MockState objects
    """
    return {
        entity_id: MockState(entity_id, "unavailable", {}) for entity_id in entity_ids
    }


def create_unknown_entity_states(entity_ids: list[str]) -> dict[str, MockState]:
    """Create unknown states for specified entities.

    Args:
        entity_ids: List of entity IDs to mark as unknown

    Returns:
        Dictionary mapping entity IDs to unknown MockState objects
    """
    return {entity_id: MockState(entity_id, "unknown", {}) for entity_id in entity_ids}


# =============================================================================
# Sample Forecast Data
# =============================================================================


def create_sample_solcast_forecast(
    num_periods: int = 24,
    start_hour: int = 6,
    peak_kw: float = 5.0,
) -> list[dict[str, Any]]:
    """Create sample Solcast forecast data.

    Creates a realistic solar forecast curve that peaks at midday.

    Args:
        num_periods: Number of forecast periods
        start_hour: Starting hour for forecasts
        peak_kw: Peak generation in kW

    Returns:
        List of forecast dictionaries
    """
    import math

    forecasts = []
    base_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    for i in range(num_periods):
        hour = start_hour + i
        # Create a bell curve peaking at noon
        hour_of_day = hour % 24
        if 6 <= hour_of_day <= 18:
            # Bell curve centered at noon
            normalized = (hour_of_day - 12) / 6
            factor = math.exp(-2 * normalized**2)
            pv_estimate = peak_kw * factor
        else:
            pv_estimate = 0.0

        period_start = base_date.replace(hour=hour % 24)
        if hour >= 24:
            period_start = period_start.replace(day=period_start.day + 1)

        forecasts.append(
            {
                "period_start": period_start.isoformat(),
                "pv_estimate": round(pv_estimate, 3),
                "pv_estimate10": round(pv_estimate * 0.9, 3),
                "pv_estimate90": round(pv_estimate * 1.1, 3),
            }
        )

    return forecasts


def create_sample_amber_price_forecast(
    num_periods: int = 48,
    start_hour: int = 0,
    base_price: float = 0.25,
    spike_at: list[int] | None = None,
    negative_at: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Create sample Amber price forecast data.

    Args:
        num_periods: Number of 30-min forecast periods
        start_hour: Starting hour for forecasts
        base_price: Base price in $/kWh
        spike_at: Hours where price spikes should occur
        negative_at: Hours where negative prices should occur

    Returns:
        List of forecast dictionaries
    """
    spike_at = spike_at or []
    negative_at = negative_at or []

    forecasts = []
    base_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    for i in range(num_periods):
        period_start = base_date.replace(
            hour=(start_hour + i // 2) % 24, minute=(i % 2) * 30
        )
        hour = (start_hour + i // 2) % 24

        # Determine price
        if hour in negative_at:
            price = -0.05  # Negative price
        elif hour in spike_at:
            price = 2.50  # Spike price
        elif 6 <= hour < 10 or 17 <= hour < 21:
            # Peak periods
            price = base_price * 1.5
        elif 10 <= hour < 17:
            # Off-peak (solar hours)
            price = base_price * 0.5
        else:
            # Shoulder
            price = base_price

        from datetime import timedelta

        end_time = period_start + timedelta(minutes=30)
        forecasts.append(
            {
                "duration_minutes": 30,
                "date": period_start.strftime("%Y-%m-%d"),
                "nem_time": period_start.strftime("%H:%M"),
                "start_time": period_start.isoformat(),
                "end_time": end_time.isoformat(),
                "per_kwh": round(price, 4),
                "spot_per_kwh": round(price * 0.9, 4),
                "renewables": 0.5,
            }
        )

    return forecasts

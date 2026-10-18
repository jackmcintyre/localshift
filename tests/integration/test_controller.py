"""BatteryController transition recipes, against a stateful fake Powerwall.

Issue #1215: export and discharge recipes turn grid charging off.

The health check expects grid charging off in PROACTIVE_EXPORT and
SPIKE_DISCHARGE, and corrects a mismatch by re-running the mode's recipe. The
recipes had no grid-charging step, so when Tesla's hourly cloud reversion (#491)
turned grid charging back on during an export, every correction rewrote the
same export, reserve and operation values and left grid charging on.

These tests drive a real ``BatteryController`` against a stateful fake
Powerwall (entity states that change when a service is called), so a correction
is judged by what it actually writes.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import (
    CONF_MINIMUM_TARGET_SOC,
    TESLEMETRY_EXPORT_BATTERY_OK,
    TESLEMETRY_EXPORT_PV_ONLY,
    BatteryMode,
)
from custom_components.localshift.integration.controller import BatteryController
from custom_components.localshift.state.machine import StateMachine

MINIMUM_TARGET_SOC = 20.0

ENTITY_IDS = {
    "teslemetry_soc": "sensor.powerwall_soc",
    "teslemetry_operation_mode": "select.powerwall_operation_mode",
    "teslemetry_backup_reserve": "number.powerwall_backup_reserve",
    "teslemetry_allow_export": "select.powerwall_allow_export",
    "teslemetry_allow_charging_from_grid": "switch.powerwall_allow_charging_from_grid",
}
GRID_CHARGING = ENTITY_IDS["teslemetry_allow_charging_from_grid"]
OPERATION_MODE = ENTITY_IDS["teslemetry_operation_mode"]
BACKUP_RESERVE = ENTITY_IDS["teslemetry_backup_reserve"]
EXPORT_MODE = ENTITY_IDS["teslemetry_allow_export"]


class FakePowerwall:
    """A Home Assistant stand-in whose Powerwall entities obey service calls."""

    def __init__(self, **states: str) -> None:
        self.states_by_id: dict[str, str] = dict(states)
        self.writes: list[tuple[str, str, object]] = []
        self.hass = MagicMock()
        self.hass.states.get = self._get
        self.hass.services.async_call = AsyncMock(side_effect=self._call)

    def _get(self, entity_id: str | None):
        if entity_id not in self.states_by_id:
            return None
        return SimpleNamespace(state=self.states_by_id[entity_id])

    async def _call(self, domain, service, data, blocking=False) -> None:
        entity_id = data["entity_id"]
        if domain == "switch":
            value: object = "on" if service == "turn_on" else "off"
        elif domain == "number":
            value = data["value"]
        else:
            value = data["option"]
        self.writes.append((domain, entity_id, value))
        self.states_by_id[entity_id] = str(value)

    def index_of(self, entity_id: str, value: object | None = None) -> int:
        """Position of the first write to *entity_id* (optionally of *value*)."""
        for index, (_, written_id, written) in enumerate(self.writes):
            if written_id == entity_id and (value is None or written == value):
                return index
        raise AssertionError(f"no write to {entity_id} ({value!r}): {self.writes}")


def _exporting_powerwall(reserve: int, grid_charging: str = "on") -> FakePowerwall:
    """Hardware mid-export, with grid charging reverted on by the cloud."""
    return FakePowerwall(**{
        ENTITY_IDS["teslemetry_soc"]: "50",
        OPERATION_MODE: "autonomous",
        BACKUP_RESERVE: str(reserve),
        EXPORT_MODE: TESLEMETRY_EXPORT_BATTERY_OK,
        GRID_CHARGING: grid_charging,
    })


def _option(key, default=None):
    return {CONF_MINIMUM_TARGET_SOC: MINIMUM_TARGET_SOC}.get(key, default)


def _controller(powerwall: FakePowerwall) -> BatteryController:
    return BatteryController(powerwall.hass, ENTITY_IDS.get, get_option_func=_option)


def _data(coordinator_data):
    coordinator_data.soc = 50.0
    coordinator_data.operation_mode = "autonomous"
    coordinator_data.grid_services_active = False
    coordinator_data.storm_watch_active = False
    return coordinator_data


def _machine(controller, mock_entity_validator, dry_run: bool = False):
    notifications = MagicMock()
    notifications.send_health_correction_notification = AsyncMock()
    return StateMachine(
        controller,
        notifications,
        lambda key: dry_run if key == "dry_run" else False,
        _option,
        mock_entity_validator,
    )


# (mode, controller call, reserve the recipe writes from SOC 50)
EXPORT_RECIPES = [
    pytest.param(
        BatteryMode.PROACTIVE_EXPORT,
        lambda controller, data: controller.set_proactive_export(data),
        45,
        id="proactive_export",
    ),
    pytest.param(
        BatteryMode.SPIKE_DISCHARGE,
        lambda controller, data: controller.set_force_discharge(data),
        int(MINIMUM_TARGET_SOC),
        id="spike_discharge",
    ),
]


@pytest.mark.asyncio
@pytest.mark.usefixtures("mock_battery_sleep")
@pytest.mark.parametrize(("mode", "run_recipe", "reserve"), EXPORT_RECIPES)
async def test_recipe_turns_grid_charging_off_before_the_reserve(
    coordinator_data, mode, run_recipe, reserve
):
    """The recipe writes grid charging off, and before the reserve moves.

    Off first, as in the self-consumption recipe: with grid charging on and a
    reserve above SOC, autonomous mode would charge from the grid.
    """
    powerwall = _exporting_powerwall(reserve=80)

    assert await run_recipe(_controller(powerwall), _data(coordinator_data)) is True

    assert powerwall.states_by_id[GRID_CHARGING] == "off"
    assert powerwall.index_of(GRID_CHARGING, "off") < powerwall.index_of(BACKUP_RESERVE)


@pytest.mark.asyncio
@pytest.mark.usefixtures("mock_battery_sleep")
@pytest.mark.parametrize(("mode", "run_recipe", "reserve"), EXPORT_RECIPES)
async def test_recipe_expects_grid_charging_off(
    coordinator_data, mode, run_recipe, reserve
):
    """The recipe validates grid charging off, as the health check does."""
    powerwall = _exporting_powerwall(reserve=reserve)
    controller = _controller(powerwall)
    health_expectation = _machine(controller, MagicMock())._get_expected_state_for_mode(
        mode
    )

    with patch.object(
        controller._validator,
        "validate_transition",
        AsyncMock(return_value=True),
    ) as validate:
        assert await run_recipe(controller, _data(coordinator_data)) is True

    assert validate.await_args.kwargs["expected_grid_charging_allowed"] is False
    assert health_expectation[3] is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("mock_battery_sleep")
@pytest.mark.parametrize(("mode", "run_recipe", "reserve"), EXPORT_RECIPES)
async def test_recipe_fails_when_grid_charging_will_not_turn_off(
    coordinator_data, mode, run_recipe, reserve
):
    """A grid-charging write that never takes aborts before any other write."""
    powerwall = _exporting_powerwall(reserve=reserve)

    async def _switch_ignored(domain, service, data, blocking=False):
        if domain != "switch":
            await FakePowerwall._call(powerwall, domain, service, data, blocking)

    powerwall.hass.services.async_call.side_effect = _switch_ignored

    assert await run_recipe(_controller(powerwall), _data(coordinator_data)) is False
    assert powerwall.writes == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("mock_battery_sleep")
@pytest.mark.parametrize(("mode", "run_recipe", "reserve"), EXPORT_RECIPES)
async def test_health_check_correction_turns_grid_charging_off(
    coordinator_data, mock_entity_validator, mode, run_recipe, reserve
):
    """The issue: a correction in export with grid charging on turns it off."""
    powerwall = _exporting_powerwall(reserve=reserve)
    machine = _machine(_controller(powerwall), mock_entity_validator)
    machine._commanded_mode = mode
    machine._proactive_export_reserve = 45.0
    machine._spike_discharge_reserve = MINIMUM_TARGET_SOC
    data = _data(coordinator_data)
    data.backup_reserve = reserve

    await machine._perform_health_check(data)

    assert ("switch", GRID_CHARGING, "off") in powerwall.writes
    assert powerwall.states_by_id[GRID_CHARGING] == "off"
    assert powerwall.states_by_id[EXPORT_MODE] == TESLEMETRY_EXPORT_BATTERY_OK


@pytest.mark.asyncio
@pytest.mark.usefixtures("mock_battery_sleep")
@pytest.mark.parametrize(("mode", "run_recipe", "reserve"), EXPORT_RECIPES)
async def test_dry_run_correction_writes_nothing(
    coordinator_data, mock_entity_validator, mode, run_recipe, reserve
):
    """DRY RUN: the correction logs and writes nothing, grid charging included."""
    powerwall = _exporting_powerwall(reserve=reserve)
    machine = _machine(_controller(powerwall), mock_entity_validator, dry_run=True)
    machine._commanded_mode = mode
    machine._proactive_export_reserve = 45.0
    machine._spike_discharge_reserve = MINIMUM_TARGET_SOC
    data = _data(coordinator_data)
    data.backup_reserve = reserve

    await machine._perform_health_check(data)

    assert powerwall.writes == []
    assert powerwall.states_by_id[GRID_CHARGING] == "on"


@pytest.mark.asyncio
@pytest.mark.usefixtures("mock_battery_sleep")
async def test_self_consumption_recipe_already_turned_grid_charging_off(
    coordinator_data,
):
    """Control: SELF_CONSUMPTION / DEMAND_BLOCK / HOLD share a recipe that
    already had the step, first, with a matching expectation.
    """
    powerwall = FakePowerwall(**{
        OPERATION_MODE: "self_consumption",
        BACKUP_RESERVE: "10",
        EXPORT_MODE: TESLEMETRY_EXPORT_PV_ONLY,
        GRID_CHARGING: "on",
    })

    assert await _controller(powerwall).set_self_consumption(coordinator_data) is True

    assert powerwall.writes[0] == ("switch", GRID_CHARGING, "off")

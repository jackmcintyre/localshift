"""Hardware-reserve half of the away floor (docs/holiday-away/plan.md item 4).

Covers ``mode_configs.calculate_self_consumption_reserve`` /
``resolve_away_reserve_pct``, the SELF_CONSUMPTION/DEMAND_BLOCK/HOLD/
PROACTIVE_EXPORT mode builders, the controller's ``set_self_consumption`` /
``set_proactive_export`` / ``set_self_consumption_reserve``, and the
``_step_self_consumption_reserve`` mid-trip re-step + release.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.integration.controller import BatteryController
from custom_components.localshift.state.machine import StateMachine
from custom_components.localshift.state.mode_configs import (
    calculate_self_consumption_reserve,
    resolve_away_reserve_pct,
)

# ---------------------------------------------------------------------------
# C1: the shared formula
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("preserve_soc", "away_reserve_pct", "expected"),
    [
        (None, None, 10.0),
        (None, 30.0, 30.0),
        (45.0, 30.0, 45.0),
        (25.0, 30.0, 30.0),
        (None, 95.0, 80.0),  # clamped to BACKUP_RESERVE_MAX_VALID internally
    ],
)
def test_c1_calculate_self_consumption_reserve(
    preserve_soc, away_reserve_pct, expected
):
    assert (
        calculate_self_consumption_reserve(preserve_soc, away_reserve_pct) == expected
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (30, 30.0),
        (5, 10.0),  # below AWAY_RESERVE_MIN clamps up
        (95, 80.0),  # above BACKUP_RESERVE_MAX_VALID clamps down
        ("not-a-number", 30.0),  # DEFAULT_AWAY_RESERVE
        (None, 30.0),
    ],
)
def test_c1_resolve_away_reserve_pct_clamps(raw, expected):
    assert resolve_away_reserve_pct(raw) == expected


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_battery_controller():
    controller = MagicMock()
    controller.set_self_consumption = AsyncMock(return_value=True)
    controller.set_self_consumption_reserve = AsyncMock(return_value=True)
    controller.set_proactive_export = AsyncMock(return_value=True)
    controller.set_force_charge = AsyncMock(return_value=True)
    controller.set_boost_charge = AsyncMock(return_value=True)
    controller.set_force_discharge = AsyncMock(return_value=True)
    controller.verify_current_state = AsyncMock(return_value=True)
    controller.read_fresh_soc = MagicMock(return_value=None)
    return controller


@pytest.fixture
def mock_notification_service():
    service = MagicMock()
    service.send_transition_notification = AsyncMock()
    service.send_transition_failed_notification = AsyncMock()
    service.send_health_correction_notification = AsyncMock()
    service.send_manual_override_timeout_notification = AsyncMock()
    service.send_automation_disabled_notification = AsyncMock()
    service.send_tesla_override_notification = AsyncMock()
    return service


def _make_machine(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    options: dict | None = None,
):
    options = options or {}
    return StateMachine(
        mock_battery_controller,
        mock_notification_service,
        lambda key: False,  # all switches off (dry_run=False, manual_override=False)
        lambda key, default=None: options.get(key, default),
        mock_entity_validator,
    )


# ---------------------------------------------------------------------------
# C2: SELF_CONSUMPTION / DEMAND_BLOCK builder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode", [BatteryMode.SELF_CONSUMPTION, BatteryMode.DEMAND_BLOCK]
)
def test_c2_self_consumption_config_away(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
    mode,
):
    machine = _make_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        options={"away_reserve": 30},
    )
    coordinator_data.away_active = True
    coordinator_data.preserve_soc = None
    config = machine._get_mode_config(mode, coordinator_data)
    assert config.backup_reserve == 30.0
    assert config.self_consumption_reserve == 30.0


@pytest.mark.parametrize(
    "mode", [BatteryMode.SELF_CONSUMPTION, BatteryMode.DEMAND_BLOCK]
)
def test_c2_self_consumption_config_not_away(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
    mode,
):
    machine = _make_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        options={"away_reserve": 30},
    )
    coordinator_data.away_active = False
    coordinator_data.preserve_soc = None
    config = machine._get_mode_config(mode, coordinator_data)
    assert config.backup_reserve == 10.0


# ---------------------------------------------------------------------------
# C3: HOLD floor
# ---------------------------------------------------------------------------


def test_c3_hold_floors_at_away_reserve(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
):
    mock_battery_controller.read_fresh_soc = MagicMock(return_value=15.0)
    machine = _make_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        options={"away_reserve": 30, "minimum_target_soc": 20},
    )
    coordinator_data.away_active = True
    coordinator_data.soc = 15.0
    config = machine._get_mode_config(BatteryMode.HOLD, coordinator_data)
    # max(away=30, min_soc=20, fresh_soc=15) == 30
    assert config.backup_reserve == 30.0


def test_c3_hold_ignores_away_when_not_active(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
):
    mock_battery_controller.read_fresh_soc = MagicMock(return_value=15.0)
    machine = _make_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        options={"away_reserve": 30, "minimum_target_soc": 20},
    )
    coordinator_data.away_active = False
    coordinator_data.soc = 15.0
    config = machine._get_mode_config(BatteryMode.HOLD, coordinator_data)
    assert config.backup_reserve == 20.0  # max(min_soc=20, fresh_soc=15)


# ---------------------------------------------------------------------------
# C4: PROACTIVE_EXPORT floor
# ---------------------------------------------------------------------------


def test_c4_proactive_export_floors_at_away_reserve(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
):
    machine = _make_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        options={"away_reserve": 30, "minimum_target_soc": 20},
    )
    coordinator_data.away_active = True
    coordinator_data.soc = 32.0  # soc - 5 = 27, below the away floor of 30
    config = machine._get_mode_config(BatteryMode.PROACTIVE_EXPORT, coordinator_data)
    assert config.backup_reserve == 30.0


def test_c4_proactive_export_unaffected_when_not_away(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
):
    machine = _make_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        options={"away_reserve": 30, "minimum_target_soc": 20},
    )
    coordinator_data.away_active = False
    coordinator_data.soc = 32.0
    config = machine._get_mode_config(BatteryMode.PROACTIVE_EXPORT, coordinator_data)
    assert config.backup_reserve == 27.0  # max(20, 32-5)


# ---------------------------------------------------------------------------
# C5: controller writes
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_hass():
    return MagicMock()


def _make_controller(mock_hass, options: dict | None = None):
    options = options or {}
    controller = BatteryController(
        mock_hass,
        get_entity_id_func=lambda key: f"entity.{key}",
        get_option_func=lambda key, default=None: options.get(key, default),
    )
    controller._service_client = MagicMock()
    controller._service_client.set_grid_charging_allowed = AsyncMock(return_value=True)
    controller._service_client.set_export_mode = AsyncMock(return_value=True)
    controller._service_client.set_operation_mode = AsyncMock(return_value=True)
    controller._service_client.set_backup_reserve = AsyncMock(return_value=True)
    controller._validator = MagicMock()
    controller._validator.validate_transition = AsyncMock(return_value=True)
    controller._validator.get_hardware_state_snapshot = MagicMock(
        return_value={"operation_mode": "x", "backup_reserve": 0, "export_mode": "y"}
    )
    return controller


@pytest.mark.asyncio
async def test_c5_controller_writes_away_reserve(mock_hass, coordinator_data):
    controller = _make_controller(mock_hass, options={"away_reserve": 30})
    coordinator_data.away_active = True
    coordinator_data.preserve_soc = None
    await controller.set_self_consumption(coordinator_data)
    controller._service_client.set_backup_reserve.assert_awaited_with(30.0)


@pytest.mark.asyncio
async def test_c5_controller_preserve_soc_above_away_wins(mock_hass, coordinator_data):
    controller = _make_controller(mock_hass, options={"away_reserve": 30})
    coordinator_data.away_active = True
    await controller.set_self_consumption(coordinator_data, preserve_soc=45.0)
    controller._service_client.set_backup_reserve.assert_awaited_with(45.0)


@pytest.mark.asyncio
async def test_c5_controller_not_away_gives_ten(mock_hass, coordinator_data):
    controller = _make_controller(mock_hass, options={"away_reserve": 30})
    coordinator_data.away_active = False
    coordinator_data.preserve_soc = None
    await controller.set_self_consumption(coordinator_data)
    controller._service_client.set_backup_reserve.assert_awaited_with(10)


@pytest.mark.asyncio
async def test_set_self_consumption_reserve_dry_run_skips_the_write(mock_hass):
    controller = _make_controller(mock_hass)
    result = await controller.set_self_consumption_reserve(30.0, dry_run=True)
    assert result is True
    controller._service_client.set_backup_reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_self_consumption_reserve_writes_the_reserve(mock_hass):
    controller = _make_controller(mock_hass)
    result = await controller.set_self_consumption_reserve(30.0)
    assert result is True
    controller._service_client.set_backup_reserve.assert_awaited_once_with(30.0)


@pytest.mark.asyncio
async def test_c4_controller_proactive_export_floors_at_away_reserve(
    mock_hass, coordinator_data
):
    """docs/holiday-away/plan.md item 4: the controller's own PROACTIVE_EXPORT
    floor (not just the state-machine builder's)."""
    controller = _make_controller(
        mock_hass, options={"away_reserve": 30, "minimum_target_soc": 20}
    )
    coordinator_data.away_active = True
    coordinator_data.soc = 32.0  # soc - 5 = 27, below the away floor of 30

    result = await controller.set_proactive_export(coordinator_data)

    assert result is True
    # max(minimum_target=20, away=30) = 30; max(30, 32-5=27) = 30.
    controller._service_client.set_backup_reserve.assert_awaited_with(30.0)


@pytest.mark.asyncio
async def test_c5_controller_magicmock_away_active_means_not_away(mock_hass):
    """A bare MagicMock's truthy ``away_active`` must not arm the away reserve."""
    controller = _make_controller(mock_hass, options={"away_reserve": 30})
    data = MagicMock()
    data.preserve_soc = None
    await controller.set_self_consumption(data)
    controller._service_client.set_backup_reserve.assert_awaited_with(10)


# ---------------------------------------------------------------------------
# C6-C10: _step_self_consumption_reserve (mid-trip re-step and release)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c6_stable_mode_steps_reserve_once(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
):
    machine = _make_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        options={"away_reserve": 30},
    )
    machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
    machine._self_consumption_reserve = 10.0
    coordinator_data.away_active = True
    coordinator_data.preserve_soc = None
    coordinator_data.manual_override = False

    stepped = await machine._step_self_consumption_reserve(coordinator_data)

    assert stepped is True
    mock_battery_controller.set_self_consumption_reserve.assert_awaited_once_with(30.0)
    assert machine._self_consumption_reserve == 30.0


@pytest.mark.asyncio
async def test_c6_no_write_once_tracked_matches(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
):
    machine = _make_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        options={"away_reserve": 30},
    )
    machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
    machine._self_consumption_reserve = 30.0  # already stepped
    coordinator_data.away_active = True
    coordinator_data.preserve_soc = None

    stepped = await machine._step_self_consumption_reserve(coordinator_data)

    assert stepped is False
    mock_battery_controller.set_self_consumption_reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_c7_release_when_away_ends(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
):
    machine = _make_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        options={"away_reserve": 30},
    )
    machine._commanded_mode = BatteryMode.DEMAND_BLOCK
    machine._self_consumption_reserve = 30.0
    coordinator_data.away_active = False
    coordinator_data.preserve_soc = None

    stepped = await machine._step_self_consumption_reserve(coordinator_data)

    assert stepped is True
    mock_battery_controller.set_self_consumption_reserve.assert_awaited_once_with(10.0)
    assert machine._self_consumption_reserve == 10.0


@pytest.mark.asyncio
async def test_c8_slider_moved_mid_trip(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
):
    machine = _make_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        options={"away_reserve": 40},
    )
    machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
    machine._self_consumption_reserve = 30.0
    coordinator_data.away_active = True
    coordinator_data.preserve_soc = None

    stepped = await machine._step_self_consumption_reserve(coordinator_data)

    assert stepped is True
    mock_battery_controller.set_self_consumption_reserve.assert_awaited_once_with(40.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", [BatteryMode.GRID_CHARGING, BatteryMode.BOOST_CHARGING, BatteryMode.HOLD]
)
async def test_c9_no_restep_for_other_modes(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
    mode,
):
    machine = _make_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        options={"away_reserve": 30},
    )
    machine._commanded_mode = mode
    machine._self_consumption_reserve = 10.0
    coordinator_data.away_active = True

    stepped = await machine._step_self_consumption_reserve(coordinator_data)

    assert stepped is False
    mock_battery_controller.set_self_consumption_reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_c6_perform_health_check_returns_early_after_stepping(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
):
    """The reserve write is this tick's correction — _perform_health_check must
    not also run its normal verify/correct pass on the same tick."""
    machine = _make_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        options={"away_reserve": 30},
    )
    machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
    machine._self_consumption_reserve = 10.0
    coordinator_data.away_active = True
    coordinator_data.preserve_soc = None

    await machine._perform_health_check(coordinator_data)

    mock_battery_controller.set_self_consumption_reserve.assert_awaited_once_with(30.0)
    mock_battery_controller.verify_current_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_c9_perform_health_check_skips_restep_under_manual_override(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
):
    machine = _make_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        options={"away_reserve": 30},
    )
    machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
    machine._self_consumption_reserve = 10.0
    coordinator_data.away_active = True
    coordinator_data.manual_override = True

    await machine._perform_health_check(coordinator_data)

    mock_battery_controller.set_self_consumption_reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_c10_failed_write_leaves_tracked_unchanged(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
):
    mock_battery_controller.set_self_consumption_reserve = AsyncMock(return_value=False)
    machine = _make_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        options={"away_reserve": 30},
    )
    machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
    machine._self_consumption_reserve = 10.0
    coordinator_data.away_active = True
    coordinator_data.preserve_soc = None

    stepped = await machine._step_self_consumption_reserve(coordinator_data)

    assert stepped is False
    assert machine._self_consumption_reserve == 10.0  # unchanged, retried next tick


# ---------------------------------------------------------------------------
# #1082 re-step while away: PROACTIVE_EXPORT must never step below the away floor
# ---------------------------------------------------------------------------


def _exporting_machine(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    tracked: float,
    fresh_soc: float,
):
    machine = _make_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        options={"away_reserve": 30, "minimum_target_soc": 20},
    )
    machine._commanded_mode = BatteryMode.PROACTIVE_EXPORT
    machine._proactive_export_reserve = tracked
    mock_battery_controller.read_fresh_soc = MagicMock(return_value=fresh_soc)
    mock_battery_controller.set_proactive_export_reserve = AsyncMock(return_value=True)
    return machine


@pytest.mark.asyncio
async def test_export_restep_floors_at_away_reserve(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
):
    """SOC drained to a 32% reserve while away: step to 30, not SOC - 5 = 26.6."""
    machine = _exporting_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        tracked=32.0,
        fresh_soc=31.6,
    )
    coordinator_data.away_active = True

    stepped = await machine._step_proactive_export_reserve(coordinator_data)

    assert stepped is True
    mock_battery_controller.set_proactive_export_reserve.assert_awaited_once_with(
        30.0, False
    )
    assert machine._proactive_export_reserve == 30.0


@pytest.mark.asyncio
async def test_export_restep_holds_at_away_floor(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
):
    """At the away floor, the re-step writes nothing more."""
    machine = _exporting_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        tracked=30.0,
        fresh_soc=30.2,
    )
    coordinator_data.away_active = True

    stepped = await machine._step_proactive_export_reserve(coordinator_data)

    assert stepped is False
    mock_battery_controller.set_proactive_export_reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_export_restep_lifts_reserve_when_away_starts(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
):
    """Away turns on mid-export with the reserve at 21.6: lift it to the floor."""
    machine = _exporting_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        tracked=21.6,
        fresh_soc=24.0,
    )
    coordinator_data.away_active = True

    stepped = await machine._step_proactive_export_reserve(coordinator_data)

    assert stepped is True
    mock_battery_controller.set_proactive_export_reserve.assert_awaited_once_with(
        30.0, False
    )


@pytest.mark.asyncio
async def test_export_restep_not_away_keeps_minimum_target_floor(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
):
    """Not away: #1082's behaviour is unchanged (steps to SOC - 5 above 20)."""
    machine = _exporting_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        tracked=27.0,
        fresh_soc=26.6,
    )
    coordinator_data.away_active = False

    stepped = await machine._step_proactive_export_reserve(coordinator_data)

    assert stepped is True
    mock_battery_controller.set_proactive_export_reserve.assert_awaited_once_with(
        pytest.approx(21.6), False
    )


# ---------------------------------------------------------------------------
# #1093: a reserve-only write is LocalShift's own command for override suppression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserve_step_stamps_self_command(
    mock_battery_controller,
    mock_notification_service,
    mock_entity_validator,
    coordinator_data,
):
    """After the away release (80 -> 10), a bounced 80 still reads as self-inflicted."""
    from homeassistant.util import dt as dt_util

    machine = _make_machine(
        mock_battery_controller,
        mock_notification_service,
        mock_entity_validator,
        options={"away_reserve": 80},
    )
    machine._commanded_mode = BatteryMode.SELF_CONSUMPTION
    machine._self_consumption_reserve = 80.0
    coordinator_data.away_active = False
    coordinator_data.preserve_soc = None

    assert await machine._step_self_consumption_reserve(coordinator_data) is True

    assert machine._last_reserve_step is not None
    assert machine._last_successful_transition is None  # no transition grace armed
    assert machine._is_signature_self_inflicted(dt_util.now()) is True
    # The tracked reserve is now 10, so only the stamp explains the signature.
    machine._last_reserve_step = None
    assert machine._is_signature_self_inflicted(dt_util.now()) is False

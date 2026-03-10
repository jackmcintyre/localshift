"""Battery control functionality for Tesla Powerwall."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..const import (
    BACKUP_RESERVE_MAX_VALID,
    TESLEMETRY_EXPORT_BATTERY_OK,
    TESLEMETRY_EXPORT_PV_ONLY,
)
from ..state.validator import TransitionValidator
from .client import PowerwallServiceClient

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..coordinator.data import CoordinatorData


_LOGGER = logging.getLogger(__name__)

# Transition timeouts per mode (seconds)
# Boost charging needs longer due to Tesla API behavior with autonomous mode
TRANSITION_TIMEOUTS = {
    "autonomous": 15,  # autonomous mode takes longer to propagate
    "backup": 10,
    "self_consumption": 10,
}


@dataclass(frozen=True)
class TransitionExpectation:
    """Expected outcomes for a mode transition."""

    operation_mode: str
    backup_reserve: float | int
    export_mode: str | None = None
    grid_charging_allowed: bool | None = None
    timeout: int = 10


@dataclass(frozen=True)
class TransitionStep:
    """Single step within a transition recipe."""

    action: Callable[[], Awaitable[bool]]
    failure_message: str


@dataclass(frozen=True)
class TransitionRecipe:
    """Declarative recipe for a mode transition."""

    name: str
    steps: Sequence[TransitionStep]
    expectation: TransitionExpectation
    on_validation_failure: Callable[[], None] | None = None
    on_validation_success: Callable[[], None] | None = None


class BatteryController:
    """Controls Tesla Powerwall battery modes."""

    def __init__(
        self,
        hass: HomeAssistant,
        get_entity_id_func: callable,
    ) -> None:
        """Initialize the battery controller.

        Args:
            hass: Home Assistant instance
            get_entity_id_func: Function to get entity IDs by config key

        """
        self.hass = hass
        self._get_entity_id = get_entity_id_func
        self._service_client = PowerwallServiceClient(hass, get_entity_id_func)
        self._validator = TransitionValidator(hass, get_entity_id_func)

    def read_fresh_soc(self) -> float | None:
        """Read the absolute latest SOC directly from the Home Assistant state machine.

        Bypasses the coordinator's caching delay which can be minutes behind during
        mode transitions.

        Issue #559 Root Cause 4: during transitions (e.g., GRID_CHARGING -> HOLD),
        the state machine was using a cached, stale SOC from the coordinator, causing
        the hardware reserve to drop immediately before the Tesla API updated.  This
        method reads the live state to avoid that staleness.

        Returns:
            Current SOC percentage (0-100) if available, None if unavailable.

        """
        from ..const import CONF_TESLEMETRY_SOC  # noqa: PLC0415

        try:
            soc_entity_id = self._get_entity_id(CONF_TESLEMETRY_SOC)
            state = self.hass.states.get(soc_entity_id)
            if state and state.state not in (None, "unknown", "unavailable"):
                return float(state.state)
        except (ValueError, TypeError, AttributeError):
            pass
        return None

    async def _run_transition(self, recipe: TransitionRecipe) -> bool:
        """Execute a transition recipe and validate the result.

        Args:
            recipe: Transition recipe containing steps and expectations.

        Returns:
            True if transition succeeded and validated, False otherwise.

        """
        for step in recipe.steps:
            if not await step.action():
                _LOGGER.error(step.failure_message)
                return False

        if not await self._validator.validate_transition(
            expected_operation_mode=recipe.expectation.operation_mode,
            expected_backup_reserve=recipe.expectation.backup_reserve,
            expected_export_mode=recipe.expectation.export_mode,
            expected_grid_charging_allowed=recipe.expectation.grid_charging_allowed,
            timeout=recipe.expectation.timeout,
        ):
            if recipe.on_validation_failure:
                recipe.on_validation_failure()
            return False

        if recipe.on_validation_success:
            recipe.on_validation_success()
        return True

    async def set_self_consumption(
        self,
        data: CoordinatorData,
        dry_run: bool = False,
        preserve_soc: float | None = None,
    ) -> bool:
        """Set battery to self consumption mode (reserve=10, self_consumption).

        Issue #350: When preserve_soc is set, use that as the backup reserve
        instead of the default 10%. This prevents battery discharge when
        charging is needed to meet a demand window target.

        Issue #522: preserve_soc parameter allows state machine to override
        data.preserve_soc for HOLD mode (preserve current SOC).

        Args:
            data: Coordinator data
            dry_run: If True, log action without executing
            preserve_soc: Optional override for backup reserve percentage

        Returns:
            True if successful, False otherwise.

        """
        # Note: manual_override is managed by button handlers and state machine
        # Self-consumption is the default automated mode, so we don't set manual_override here

        # Determine backup reserve: use parameter override, then data.preserve_soc, else default to 10%
        reserve = (
            preserve_soc
            if preserve_soc is not None
            else (data.preserve_soc if data.preserve_soc is not None else 10)
        )

        if dry_run:
            _LOGGER.info(
                "DRY RUN: set_self_consumption (reserve=%s, preserve_soc=%s)",
                reserve,
                data.preserve_soc,
            )
            return True

        _LOGGER.info(
            "Setting battery to self consumption mode (reserve=%s, preserve_soc=%s)",
            reserve,
            data.preserve_soc,
        )

        def _log_validation_failure() -> None:
            """Log validation failure for self consumption mode."""
            _LOGGER.error("Self consumption mode validation failed")

        recipe = TransitionRecipe(
            name="self_consumption",
            steps=(
                TransitionStep(
                    action=lambda: self._service_client.set_grid_charging_allowed(
                        False
                    ),
                    failure_message=(
                        "Aborting self_consumption mode: Failed to disable grid charging"
                    ),
                ),
                TransitionStep(
                    action=lambda: self._service_client.set_export_mode(
                        TESLEMETRY_EXPORT_PV_ONLY
                    ),
                    failure_message=(
                        "Aborting self_consumption mode: Failed to set export mode"
                    ),
                ),
                TransitionStep(
                    action=lambda: self._service_client.set_operation_mode(
                        "self_consumption"
                    ),
                    failure_message=(
                        "Aborting self_consumption mode: Failed to set operation mode"
                    ),
                ),
                TransitionStep(
                    action=lambda: self._service_client.set_backup_reserve(reserve),
                    failure_message=(
                        "Aborting self_consumption mode: Failed to set backup reserve"
                    ),
                ),
            ),
            expectation=TransitionExpectation(
                operation_mode="self_consumption",
                backup_reserve=reserve,
                export_mode=TESLEMETRY_EXPORT_PV_ONLY,
                grid_charging_allowed=False,
                timeout=10,
            ),
            on_validation_failure=_log_validation_failure,
        )

        if not await self._run_transition(recipe):
            return False

        _LOGGER.info(
            "Successfully completed self_consumption mode transition with validation (reserve=%s)",
            reserve,
        )
        return True

    @staticmethod
    def _clamp_backup_reserve(target: float) -> int:
        """Clamp backup reserve for Tesla firmware compatibility.

        Tesla's July 2025 firmware silently resets backup reserve values
        81-99% to 80%. Valid values are 0-80% or 100%.

        Args:
            target: Desired backup reserve percentage

        Returns:
            Clamped reserve value that Tesla firmware will accept.

        """
        if target <= BACKUP_RESERVE_MAX_VALID:
            # 0-80%: Tesla accepts these values directly
            return int(target)
        elif target >= 100:
            # 100%: Tesla accepts this value
            return 100
        else:
            # 81-99%: Clamp to 80% (Tesla would reset anyway)
            # SOC monitoring in state machine will stop at actual target
            return BACKUP_RESERVE_MAX_VALID

    async def set_force_charge(
        self,
        data: CoordinatorData,
        dry_run: bool = False,
        target_soc: float | None = None,
    ) -> bool:
        """Set battery to force charge mode (backup, reserve=target).

        Uses backup mode for 3.3 kW grid charging. The Powerwall naturally
        stops charging when SOC reaches the backup reserve level.

        For target 81-99%, reserve is clamped to 80% due to Tesla firmware
        restriction. SOC monitoring in state machine handles the gap.

        Args:
            data: Coordinator data
            dry_run: If True, log action without executing
            target_soc: Target SOC to charge to. If None, uses battery_target config.

        Returns:
            True if successful, False otherwise.

        """
        # Note: manual_override is managed by button handlers and state machine
        # This method can be called manually (via button) or automatically (via state machine)

        # Get target SOC - use provided value or fall back to battery_target config
        if target_soc is None:
            # Import here to avoid circular import

            # This will be set by state machine which has access to get_option
            # For now, use 100 as safe default
            target_soc = 100.0

        # Clamp reserve for Tesla firmware compatibility
        reserve = self._clamp_backup_reserve(target_soc)

        if dry_run:
            _LOGGER.info(
                "DRY RUN: set_force_charge (target=%.0f%%, reserve=%d%%)",
                target_soc,
                reserve,
            )
            return True

        # Capture initial state for diagnostics
        initial_state = self._validator.get_hardware_state_snapshot()
        transition_start = time.monotonic()
        _LOGGER.info(
            "[TRANSITION] Starting force charge mode (target=%.0f%%, reserve=%d%%) | Initial state: op=%s, reserve=%s, export=%s, grid_charging=%s",
            target_soc,
            reserve,
            initial_state["operation_mode"],
            initial_state["backup_reserve"],
            initial_state["export_mode"],
            initial_state.get("grid_charging_allowed", "unknown"),
        )

        timeout = TRANSITION_TIMEOUTS.get("backup", 10)

        def _log_validation_failure() -> None:
            """Log validation failure for force charge mode with state details."""
            final_state = self._validator.get_hardware_state_snapshot()
            elapsed = time.monotonic() - transition_start
            _LOGGER.error(
                "[TRANSITION] Force charge FAILED after %.2fs | Final state: op=%s, reserve=%s, export=%s",
                elapsed,
                final_state["operation_mode"],
                final_state["backup_reserve"],
                final_state["export_mode"],
            )

        recipe = TransitionRecipe(
            name="force_charge",
            steps=(
                TransitionStep(
                    action=lambda: self._service_client.set_grid_charging_allowed(True),
                    failure_message=(
                        "Aborting force charge mode: Failed to enable grid charging"
                    ),
                ),
                TransitionStep(
                    action=lambda: self._service_client.set_export_mode(
                        TESLEMETRY_EXPORT_PV_ONLY
                    ),
                    failure_message=(
                        "Aborting force charge mode: Failed to set export mode"
                    ),
                ),
                TransitionStep(
                    action=lambda: self._service_client.set_backup_reserve(reserve),
                    failure_message=(
                        "Aborting force charge mode: Failed to set backup reserve"
                    ),
                ),
                TransitionStep(
                    action=lambda: self._service_client.set_operation_mode("backup"),
                    failure_message=(
                        "Aborting force charge mode: Failed to set operation mode"
                    ),
                ),
            ),
            expectation=TransitionExpectation(
                operation_mode="backup",
                backup_reserve=reserve,
                export_mode=TESLEMETRY_EXPORT_PV_ONLY,
                timeout=timeout,
            ),
            on_validation_failure=_log_validation_failure,
        )

        if not await self._run_transition(recipe):
            return False

        elapsed = time.monotonic() - transition_start
        _LOGGER.info(
            "[TRANSITION] Force charge SUCCESS in %.2fs (target=%.0f%%, reserve=%d%%)",
            elapsed,
            target_soc,
            reserve,
        )
        return True

    async def set_boost_charge(
        self, data: CoordinatorData, dry_run: bool = False
    ) -> bool:
        """Set battery to boost charge mode (autonomous, reserve=100).

        Returns:
            True if successful, False otherwise.

        """
        # Note: manual_override is managed by button handlers and state machine
        # This method can be called manually (via button) or automatically (via state machine)

        if dry_run:
            _LOGGER.info("DRY RUN: set_boost_charge")
            return True

        # Capture initial state for diagnostics
        initial_state = self._validator.get_hardware_state_snapshot()
        transition_start = time.monotonic()
        _LOGGER.info(
            "[TRANSITION] Starting boost charge mode | Initial state: op=%s, reserve=%s, export=%s, grid_charging=%s",
            initial_state["operation_mode"],
            initial_state["backup_reserve"],
            initial_state["export_mode"],
            initial_state.get("grid_charging_allowed", "unknown"),
        )
        # Use extended timeout for autonomous mode (15s instead of 10s)
        # Tesla API takes longer to propagate autonomous mode changes
        timeout = TRANSITION_TIMEOUTS.get("autonomous", 15)

        def _log_validation_failure() -> None:
            """Log validation failure for boost charge mode with state details."""
            final_state = self._validator.get_hardware_state_snapshot()
            elapsed = time.monotonic() - transition_start
            _LOGGER.error(
                "[TRANSITION] Boost charge FAILED after %.2fs | Final state: op=%s, reserve=%s, export=%s",
                elapsed,
                final_state["operation_mode"],
                final_state["backup_reserve"],
                final_state["export_mode"],
            )

        recipe = TransitionRecipe(
            name="boost_charge",
            steps=(
                TransitionStep(
                    action=lambda: self._service_client.set_grid_charging_allowed(True),
                    failure_message=(
                        "Aborting boost charge mode: Failed to enable grid charging"
                    ),
                ),
                TransitionStep(
                    action=lambda: self._service_client.set_export_mode(
                        TESLEMETRY_EXPORT_PV_ONLY
                    ),
                    failure_message=(
                        "Aborting boost charge mode: Failed to set export mode"
                    ),
                ),
                TransitionStep(
                    action=lambda: self._service_client.set_backup_reserve(100),
                    failure_message=(
                        "Aborting boost charge mode: Failed to set backup reserve"
                    ),
                ),
                TransitionStep(
                    action=lambda: self._service_client.set_operation_mode(
                        "autonomous"
                    ),
                    failure_message=(
                        "Aborting boost charge mode: Failed to set operation mode"
                    ),
                ),
            ),
            expectation=TransitionExpectation(
                operation_mode="autonomous",
                backup_reserve=100,
                export_mode=TESLEMETRY_EXPORT_PV_ONLY,
                timeout=timeout,
            ),
            on_validation_failure=_log_validation_failure,
        )

        if not await self._run_transition(recipe):
            return False

        elapsed = time.monotonic() - transition_start
        _LOGGER.info("[TRANSITION] Boost charge SUCCESS in %.2fs", elapsed)
        return True

    def _get_minimum_target_soc(self) -> float:
        """Read the minimum target SOC from the configured entity.

        Returns:
            Minimum target SOC percentage (default 10 if entity unavailable).

        """
        entity_id = self._get_entity_id("minimum_target_soc")
        return self._validator.read_float(entity_id, default=10.0)

    async def set_force_discharge(
        self,
        data: CoordinatorData,
        dry_run: bool = False,
        reserve_soc: float | None = None,
    ) -> bool:
        """Set battery to force discharge mode (autonomous, reserve=minimum_target).

        Relies on the Tesla Energy Plan dummy tariff (high sell price
        6am-midnight) to incentivise the Powerwall to export to grid.

        Args:
            data: Coordinator data
            dry_run: If True, log action without executing
            reserve_soc: Optional override for reserve SOC. If None, uses minimum_target_soc.

        Returns:
            True if successful, False otherwise.

        """
        # Note: manual_override is managed by button handlers and state machine
        # This method can be called manually (via button) or automatically (via state machine)

        # Get minimum target SOC for reserve, or use override if provided
        minimum_target = (
            reserve_soc if reserve_soc is not None else self._get_minimum_target_soc()
        )

        if dry_run:
            _LOGGER.info("DRY RUN: set_force_discharge (reserve=%s)", minimum_target)
            return True

        # Capture initial state for diagnostics
        initial_state = self._validator.get_hardware_state_snapshot()
        transition_start = time.monotonic()
        _LOGGER.info(
            "[TRANSITION] Starting force discharge mode (reserve=%s) | Initial state: op=%s, reserve=%s, export=%s",
            minimum_target,
            initial_state["operation_mode"],
            initial_state["backup_reserve"],
            initial_state["export_mode"],
        )
        # Use extended timeout for autonomous mode (15s instead of 10s)
        timeout = TRANSITION_TIMEOUTS.get("autonomous", 15)

        def _log_validation_failure() -> None:
            """Log validation failure for force discharge mode with state details."""
            final_state = self._validator.get_hardware_state_snapshot()
            elapsed = time.monotonic() - transition_start
            _LOGGER.error(
                "[TRANSITION] Force discharge FAILED after %.2fs | Final state: op=%s, reserve=%s, export=%s",
                elapsed,
                final_state["operation_mode"],
                final_state["backup_reserve"],
                final_state["export_mode"],
            )

        recipe = TransitionRecipe(
            name="force_discharge",
            steps=(
                TransitionStep(
                    action=lambda: self._service_client.set_export_mode(
                        TESLEMETRY_EXPORT_BATTERY_OK
                    ),
                    failure_message=(
                        "Aborting force discharge mode: Failed to set export mode"
                    ),
                ),
                TransitionStep(
                    action=lambda: self._service_client.set_backup_reserve(
                        minimum_target
                    ),
                    failure_message=(
                        "Aborting force discharge mode: Failed to set backup reserve"
                    ),
                ),
                TransitionStep(
                    action=lambda: self._service_client.set_operation_mode(
                        "autonomous"
                    ),
                    failure_message=(
                        "Aborting force discharge mode: Failed to set operation mode"
                    ),
                ),
            ),
            expectation=TransitionExpectation(
                operation_mode="autonomous",
                backup_reserve=minimum_target,
                export_mode=TESLEMETRY_EXPORT_BATTERY_OK,
                timeout=timeout,
            ),
            on_validation_failure=_log_validation_failure,
        )

        if not await self._run_transition(recipe):
            return False

        elapsed = time.monotonic() - transition_start
        _LOGGER.info("[TRANSITION] Force discharge SUCCESS in %.2fs", elapsed)
        return True

    async def set_proactive_export(
        self, data: CoordinatorData, dry_run: bool = False
    ) -> bool:
        """Set battery to proactive export mode with dynamic throttling.

        Uses dynamic reserve = max(4, SOC-5) to throttle discharge rate.
        This limits export to ~5% of battery capacity per session,
        creating a "trickle export" rather than massive dump at 8kW.

        Returns:
            True if successful, False otherwise.

        """
        # Note: manual_override is managed by button handlers and state machine
        # This method is only called automatically (via state machine)

        # Dynamic reserve for throttling: SOC - 5%, minimum 4%
        current_soc = data.soc
        reserve = max(4.0, current_soc - 5.0)

        if dry_run:
            _LOGGER.info(
                "DRY RUN: set_proactive_export (reserve=%s, SOC=%s)",
                reserve,
                current_soc,
            )
            return True

        # Capture initial state for diagnostics
        initial_state = self._validator.get_hardware_state_snapshot()
        transition_start = time.monotonic()
        _LOGGER.info(
            "[TRANSITION] Starting proactive export mode (reserve=%s, SOC=%s) | Initial state: op=%s, reserve=%s, export=%s",
            reserve,
            current_soc,
            initial_state["operation_mode"],
            initial_state["backup_reserve"],
            initial_state["export_mode"],
        )
        # Use extended timeout for autonomous mode (15s instead of 10s)
        timeout = TRANSITION_TIMEOUTS.get("autonomous", 15)

        def _log_validation_failure() -> None:
            """Log validation failure for proactive export mode with state details."""
            final_state = self._validator.get_hardware_state_snapshot()
            elapsed = time.monotonic() - transition_start
            _LOGGER.error(
                "[TRANSITION] Proactive export FAILED after %.2fs | Final state: op=%s, reserve=%s, export=%s",
                elapsed,
                final_state["operation_mode"],
                final_state["backup_reserve"],
                final_state["export_mode"],
            )

        recipe = TransitionRecipe(
            name="proactive_export",
            steps=(
                TransitionStep(
                    action=lambda: self._service_client.set_export_mode(
                        TESLEMETRY_EXPORT_BATTERY_OK
                    ),
                    failure_message="Aborting proactive export: Failed to set export mode",
                ),
                TransitionStep(
                    action=lambda: self._service_client.set_backup_reserve(reserve),
                    failure_message=(
                        f"Aborting proactive export: Failed to set backup reserve to {reserve}"
                    ),
                ),
                TransitionStep(
                    action=lambda: self._service_client.set_operation_mode(
                        "autonomous"
                    ),
                    failure_message=(
                        "Aborting proactive export: Failed to set operation mode"
                    ),
                ),
            ),
            expectation=TransitionExpectation(
                operation_mode="autonomous",
                backup_reserve=reserve,
                export_mode=TESLEMETRY_EXPORT_BATTERY_OK,
                timeout=timeout,
            ),
            on_validation_failure=_log_validation_failure,
        )

        if not await self._run_transition(recipe):
            return False

        elapsed = time.monotonic() - transition_start
        _LOGGER.info(
            "[TRANSITION] Proactive export SUCCESS in %.2fs (reserve=%s)",
            elapsed,
            reserve,
        )
        return True

    def _read_float(self, entity_id: str, default: float = 0.0) -> float:
        """Read a float value from an entity's state."""
        return self._validator.read_float(entity_id, default=default)

    def _read_str(self, entity_id: str, default: str = "") -> str:
        """Read a string value from an entity's state."""
        return self._validator.read_str(entity_id, default=default)

    def _read_bool(self, entity_id: str, default: bool = False) -> bool:
        """Read a boolean value from an entity's state (switch on/off)."""
        return self._validator.read_bool(entity_id, default=default)

    async def validate_transition(
        self,
        expected_operation_mode: str,
        expected_backup_reserve: float | int,
        expected_export_mode: str | None = None,
        expected_grid_charging_allowed: bool | None = None,
        timeout: int = 10,
    ) -> bool:
        """Validate that hardware state matches expected values after transition."""
        return await self._validator.validate_transition(
            expected_operation_mode=expected_operation_mode,
            expected_backup_reserve=expected_backup_reserve,
            expected_export_mode=expected_export_mode,
            expected_grid_charging_allowed=expected_grid_charging_allowed,
            timeout=timeout,
        )

    async def verify_current_state(
        self,
        expected_operation_mode: str,
        expected_backup_reserve: float | int,
        expected_export_mode: str | None = None,
        expected_grid_charging_allowed: bool | None = None,
    ) -> bool:
        """Verify current hardware state matches expected values."""
        return await self._validator.verify_current_state(
            expected_operation_mode=expected_operation_mode,
            expected_backup_reserve=expected_backup_reserve,
            expected_export_mode=expected_export_mode,
            expected_grid_charging_allowed=expected_grid_charging_allowed,
        )

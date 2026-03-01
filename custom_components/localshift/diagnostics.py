"""Diagnostics support for LocalShift integration.

Provides Home Assistant diagnostics data for troubleshooting
and integration health monitoring.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Provides comprehensive diagnostics data for troubleshooting including:
    - Entity health status
    - Current sensor values
    - Recent errors and warnings
    - Configuration summary

    Args:
        hass: Home Assistant instance
        entry: Config entry to diagnose

    Returns:
        Dictionary with diagnostics data
    """
    # Get the coordinator from hass.data
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    diagnostics = {
        "integration_status": _get_integration_status(coordinator),
        "entity_health": _get_entity_health(coordinator),
        "current_state": _get_current_state(coordinator),
        "configuration": _get_safe_configuration(entry),
        "recent_errors": _get_recent_errors(coordinator),
        "learning_system": _get_learning_system_status(coordinator),
        "optimizer": _get_optimizer_status(coordinator),
    }

    return diagnostics


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, _device: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a device.

    Args:
        hass: Home Assistant instance
        entry: Config entry
        _device: Device entry (unused - same as config entry diagnostics)

    Returns:
        Dictionary with device-specific diagnostics
    """
    # For this integration, device diagnostics is same as config entry
    return await async_get_config_entry_diagnostics(hass, entry)


def _get_integration_status(coordinator: Any) -> dict[str, Any]:
    """Get overall integration status.

    Args:
        coordinator: LocalShift coordinator instance

    Returns:
        Dictionary with status information
    """
    if coordinator is None:
        return {"status": "not_loaded", "message": "Integration not loaded"}

    # Get status from entity validator if available
    if hasattr(coordinator, "_entity_validator") and coordinator._entity_validator:
        validator = coordinator._entity_validator
        return {
            "status": validator.status.value,
            "message": validator.get_user_friendly_message(),
            "error_count": len(validator.errors),
            "warning_count": len(validator.warnings),
        }

    # Fallback: check if coordinator has data
    if hasattr(coordinator, "data"):
        return {"status": "ok", "message": "Integration running"}

    return {"status": "unknown", "message": "Unable to determine status"}


def _get_entity_health(coordinator: Any) -> dict[str, Any]:
    """Get detailed entity health status.

    Args:
        coordinator: LocalShift coordinator instance

    Returns:
        Dictionary with per-entity health information
    """
    if coordinator is None:
        return {"error": "Coordinator not available"}

    # Get health from entity validator if available
    if hasattr(coordinator, "_entity_validator") and coordinator._entity_validator:
        return coordinator._entity_validator.get_health_summary()

    # Fallback: check basic entity availability
    health = {"entities": {}, "summary": {}}
    if hasattr(coordinator, "data"):
        data = coordinator.data
        # Basic availability check based on data values
        health["entities"]["soc"] = {
            "available": hasattr(data, "soc") and data.soc is not None,
            "value": getattr(data, "soc", None),
        }
        health["entities"]["general_price"] = {
            "available": hasattr(data, "general_price")
            and data.general_price is not None,
            "value": getattr(data, "general_price", None),
        }
        health["entities"]["feed_in_price"] = {
            "available": hasattr(data, "feed_in_price")
            and data.feed_in_price is not None,
            "value": getattr(data, "feed_in_price", None),
        }

    return health


def _get_current_state(coordinator: Any) -> dict[str, Any]:
    """Get current state values for key sensors.

    Args:
        coordinator: LocalShift coordinator instance

    Returns:
        Dictionary with current state values
    """
    if coordinator is None or not hasattr(coordinator, "data"):
        return {"error": "No data available"}

    data = coordinator.data
    state = {}

    # Battery state
    state["battery"] = {
        "soc": getattr(data, "soc", None),
        "operation_mode": getattr(data, "operation_mode", None),
        "backup_reserve": getattr(data, "backup_reserve", None),
        "active_mode": getattr(data, "active_mode", None),
    }

    # Power flows
    state["power"] = {
        "grid_power_kw": getattr(data, "grid_power_kw", None),
        "battery_power_kw": getattr(data, "battery_power_kw", None),
        "solar_power_kw": getattr(data, "solar_power_kw", None),
        "load_power_kw": getattr(data, "load_power_kw", None),
    }

    # Pricing
    state["pricing"] = {
        "general_price": getattr(data, "general_price", None),
        "feed_in_price": getattr(data, "feed_in_price", None),
        "price_spike": getattr(data, "price_spike", None),
        "effective_cheap_price": getattr(data, "effective_cheap_price", None),
    }

    # Forecast status
    state["forecast"] = {
        "solcast_today_entries": len(getattr(data, "solcast_today", [])),
        "solcast_tomorrow_entries": len(getattr(data, "solcast_tomorrow", [])),
        "general_forecast_entries": len(getattr(data, "general_forecast", [])),
        "feed_in_forecast_entries": len(getattr(data, "feed_in_forecast", [])),
    }

    # Automation state
    state["automation"] = {
        "demand_window_active": getattr(data, "demand_window_active", None),
        "boost_charge_needed": getattr(data, "boost_charge_needed", None),
        "solar_can_reach_target": getattr(data, "solar_can_reach_target", None),
        "manual_override": getattr(data, "manual_override", None),
    }

    return state


def _get_safe_configuration(entry: ConfigEntry) -> dict[str, Any]:
    """Get safe configuration summary (redacting sensitive values if any).

    Args:
        entry: Config entry

    Returns:
        Dictionary with configuration summary
    """
    config = {
        "entry_id": entry.entry_id,
        "version": entry.version,
        "domain": entry.domain,
        "title": entry.title,
        "entity_count": len(entry.data) + len(entry.options),
    }

    # List entity IDs (not sensitive)
    config["configured_entities"] = list(entry.data.keys())

    # Options summary
    config["options_configured"] = list(entry.options.keys())

    # Key options (non-sensitive)
    config["key_options"] = {
        "battery_target": entry.options.get("battery_target"),
        "demand_window_start": entry.options.get("demand_window_start"),
        "demand_window_end": entry.options.get("demand_window_end"),
        "forecast_lookahead_hours": entry.options.get("forecast_lookahead_hours"),
    }

    return config


def _get_recent_errors(coordinator: Any) -> dict[str, Any]:
    """Get recent errors and warnings.

    Args:
        coordinator: LocalShift coordinator instance

    Returns:
        Dictionary with error information
    """
    if coordinator is None:
        return {"errors": [], "warnings": []}

    errors = []
    warnings = []

    # Get errors from entity validator
    if hasattr(coordinator, "_entity_validator") and coordinator._entity_validator:
        validator = coordinator._entity_validator
        errors.extend(validator.errors)
        warnings.extend(validator.warnings)

    # Get recent decision log entries (may contain error info)
    if hasattr(coordinator, "data") and coordinator.data:
        data = coordinator.data
        decision_log = getattr(data, "decision_log", [])
        if decision_log:
            # Get last 5 entries
            recent_decisions = (
                decision_log[-5:] if len(decision_log) > 5 else decision_log
            )
            warnings.extend(
                f"Decision: {entry.get('reason', 'unknown')}"
                for entry in recent_decisions
            )

    return {
        "errors": errors,
        "warnings": warnings,
        "total_errors": len(errors),
        "total_warnings": len(warnings),
    }


def _get_learning_system_status(coordinator: Any) -> dict[str, Any]:
    """Get learning system status and metrics.

    Issue #170 Phase 5: Provides learning system diagnostics including
    decision tracker state, performance metrics, and adaptive parameters.

    Args:
        coordinator: LocalShift coordinator instance

    Returns:
        Dictionary with learning system status
    """
    if coordinator is None:
        return {"status": "not_loaded"}

    status: dict[str, Any] = {"status": "unknown"}

    # Get learning status from coordinator data
    if hasattr(coordinator, "data") and coordinator.data:
        data = coordinator.data
        status["status"] = getattr(data, "learning_status", "unknown")

        # Performance metrics
        metrics = getattr(data, "performance_metrics", None)
        if metrics:
            status["performance_metrics"] = {
                "total_decisions_today": metrics.total_decisions_today,
                "avg_decision_score_today": round(metrics.avg_decision_score_today, 3),
                "avg_decision_score_7d": round(metrics.avg_decision_score_7d, 3),
                "cost_trend": metrics.cost_trend,
                "grid_charge_efficiency": round(metrics.grid_charge_efficiency, 3),
                "export_loss_ratio": round(metrics.export_loss_ratio, 3),
                "unnecessary_grid_charge_kwh": round(
                    metrics.unnecessary_grid_charge_kwh, 2
                ),
                "mode_durations_today": metrics.mode_durations_today,
                "mode_cost_attribution": {
                    k: round(v, 3) for k, v in metrics.mode_cost_attribution.items()
                },
            }
        else:
            status["performance_metrics"] = None

        # Recent decision log count
        recent_log = getattr(data, "recent_decision_log", [])
        status["recent_decision_count"] = len(recent_log)

    # Decision tracker state
    if hasattr(coordinator, "decision_tracker"):
        tracker = coordinator.decision_tracker
        status["decision_tracker"] = {
            "pending_decisions": tracker.pending_count,
            "completed_decisions": tracker.completed_count,
        }
    else:
        status["decision_tracker"] = None

    return status


def _get_optimizer_status(coordinator: Any) -> dict[str, Any]:
    """Get DP optimizer shadow mode status and comparison metrics.

    Issue #403 Phase E: Provides optimizer diagnostics for troubleshooting
    and operator trust-building during shadow/assist modes.

    Args:
        coordinator: LocalShift coordinator instance

    Returns:
        Dictionary with optimizer status and recent comparison data
    """
    if coordinator is None:
        return {"status": "not_loaded"}

    status: dict[str, Any] = {"status": "unknown"}

    if not hasattr(coordinator, "data") or coordinator.data is None:
        return {"status": "no_data"}

    data = coordinator.data

    shadow_summary = getattr(data, "optimizer_shadow_summary", None) or {}
    comparison = getattr(data, "optimizer_comparison", None) or {}

    enabled = shadow_summary.get("enabled", False)
    status["enabled"] = enabled

    if not enabled:
        status["status"] = "disabled"
        status["message"] = "Optimizer not enabled in configuration"
        return status

    status["shadow_mode"] = shadow_summary.get("shadow_mode", True)
    status["planner_version"] = shadow_summary.get("planner_version")
    status["last_cycle_success"] = shadow_summary.get("success", False)
    status["last_cycle_time"] = shadow_summary.get("cycle_timestamp_iso")
    status["last_cycle_id"] = shadow_summary.get("cycle_id")
    status["solve_time_seconds"] = shadow_summary.get("solve_time_seconds")
    status["error_message"] = shadow_summary.get("error_message")

    parity_pct = shadow_summary.get("parity_completeness_pct")
    if parity_pct is not None:
        status["parity_completeness_pct"] = parity_pct

    alignment_valid = shadow_summary.get("alignment_valid")
    if alignment_valid is not None:
        status["alignment_valid"] = alignment_valid
        if shadow_summary.get("alignment_issues"):
            status["alignment_issues"] = shadow_summary["alignment_issues"][:3]

    if shadow_summary.get("success", False):
        status["projected_net_cost"] = shadow_summary.get("projected_net_cost")
        status["projected_import_kwh"] = shadow_summary.get("projected_import_kwh")
        status["projected_export_kwh"] = shadow_summary.get("projected_export_kwh")
        status["total_slots"] = shadow_summary.get("total_slots")
        status["terminal_shortfall_pct"] = shadow_summary.get("terminal_shortfall_pct")
        status["reason_code_histogram"] = shadow_summary.get(
            "reason_code_histogram", {}
        )

    if comparison:
        status["comparison"] = {
            "succeeded": comparison.get("comparison_succeeded", True),
            "mismatch_count": comparison.get("mismatch_count", 0),
            "net_cost_delta": comparison.get("net_cost_delta"),
            "import_kwh_delta": comparison.get("import_kwh_delta"),
            "export_kwh_delta": comparison.get("export_kwh_delta"),
            "legacy_meets_dw_target": comparison.get("legacy_meets_dw_target"),
            "optimizer_meets_dw_target": comparison.get("optimizer_meets_dw_target"),
            "mismatch_by_type": comparison.get("mismatch_by_type", {}),
            "comparison_time_ms": comparison.get("comparison_time_ms"),
        }

        top_mismatches = comparison.get("top_mismatches", [])
        if top_mismatches:
            status["comparison"]["top_3_mismatches"] = [
                {
                    "slot_index": m.get("slot_index"),
                    "mismatch_type": m.get("mismatch_type"),
                    "legacy_action": m.get("legacy_action"),
                    "optimizer_action": m.get("optimizer_action"),
                    "reason_detail": m.get("reason_detail"),
                    "net_cost_delta": m.get("legacy_net_cost", 0)
                    - m.get("optimizer_net_cost", 0),
                }
                for m in top_mismatches[:3]
            ]

        summary = comparison.get("summary", {})
        if summary:
            status["comparison"]["summary"] = {
                "total_mismatches": summary.get("total_mismatches"),
                "total_cost_impact": summary.get("total_cost_impact"),
                "most_significant_type": summary.get("most_significant_type"),
            }

    if status.get("last_cycle_success"):
        status["status"] = "running"
    elif enabled:
        status["status"] = "error"
        status["message"] = status.get("error_message", "Unknown error")
    else:
        status["status"] = "disabled"

    return status

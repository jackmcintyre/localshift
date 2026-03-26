#!/usr/bin/env python3
"""
LocalShift Charging Plan Snapshot Script

Generate a point-in-time snapshot of the LocalShift charging plan state
for debugging purposes. Output matches the Debug dashboard view.

Usage:
    python snapshot_charging_plan.py [OPTIONS]

Options:
    --output, -o DIR    Output directory for snapshot file (default: current directory)
    --url URL           Home Assistant URL (default: HA_URL env or http://homeassistant:8123)
    --token TOKEN       Home Assistant long-lived access token (default: HA_TOKEN or HA_LONG_LIVED_TOKEN env)

Environment Variables:
    HA_URL              Home Assistant URL
    HA_TOKEN            Home Assistant long-lived access token
    HA_LONG_LIVED_TOKEN Alternative token variable (used by deploy.sh)

Output:
    Creates a markdown file named charging_plan_snapshot_YYYY-MM-DD.md

Sections Included:
    - Current State (mode, SOC, power flows, prices)
    - Entity Health (integration status, stale/missing entities)
    - Internal State Flags (manual override, boost needed, etc.)
    - Mode Decision Debug (forecast slot info)
    - Configuration & Thresholds
    - Forecast Information
    - Forecast Diagnostics
    - Decision Log
    - Cost Tracking
    - Weather Correlation
    - System Info
    - Thermal Management
    - Learning System
    - Binary Sensors Summary
    - Switches Summary

Examples:
    # Using environment variables
    python snapshot_charging_plan.py

    # Specify output directory
    python snapshot_charging_plan.py --output /path/to/snapshots

    # Override URL and token
    python snapshot_charging_plan.py --url http://localhost:8123 --token your_token

    # Using with deploy.sh environment
    ./deploy.sh --reserve
    python scripts/snapshot_charging_plan.py --output snapshots/
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any


class HomeAssistantClient:
    """Simple Home Assistant REST API client using urllib."""

    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def get_state(self, entity_id: str) -> dict | None:
        """Get state for a single entity."""
        try:
            req = urllib.request.Request(
                f"{self.url}/api/states/{entity_id}",
                headers=self.headers,
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(
                f"Warning: HTTP {e.code} for {entity_id}: {e.reason}", file=sys.stderr
            )
            return None
        except urllib.error.URLError as e:
            print(f"Warning: Failed to get {entity_id}: {e.reason}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"Warning: Failed to get {entity_id}: {e}", file=sys.stderr)
            return None

    def get_states(self, entity_ids: list[str]) -> dict[str, dict]:
        """Get states for multiple entities."""
        results = {}
        for entity_id in entity_ids:
            state = self.get_state(entity_id)
            if state:
                results[entity_id] = state
        return results


def safe_state(entity_data: dict | None, default: str = "unknown") -> str:
    """Safely get state from entity data."""
    if entity_data is None:
        return default
    return entity_data.get("state", default)


def safe_attr(entity_data: dict | None, attr: str, default: Any = None) -> Any:
    """Safely get attribute from entity data."""
    if entity_data is None:
        return default
    return entity_data.get("attributes", {}).get(attr, default)


def format_timestamp() -> str:
    """Format current timestamp for snapshot."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def format_filename() -> str:
    """Format filename with date."""
    return f"charging_plan_snapshot_{datetime.now().strftime('%Y-%m-%d')}.md"


class SnapshotGenerator:
    """Generate charging plan snapshot markdown."""

    def __init__(self, ha_client: HomeAssistantClient):
        self.ha = ha_client
        self._cache: dict[str, dict] = {}

    def get_entity(self, entity_id: str) -> dict | None:
        """Get entity with caching."""
        if entity_id not in self._cache:
            self._cache[entity_id] = self.ha.get_state(entity_id)
        return self._cache[entity_id]

    def state(self, entity_id: str, default: str = "unknown") -> str:
        """Get entity state."""
        return safe_state(self.get_entity(entity_id), default)

    def attr(self, entity_id: str, attr: str, default: Any = None) -> Any:
        """Get entity attribute."""
        return safe_attr(self.get_entity(entity_id), attr, default)

    def generate(self) -> str:
        """Generate the full snapshot markdown."""
        sections = [
            self._header(),
            self._current_state(),
            self._entity_health(),
            self._internal_state_flags(),
            self._mode_decision(),
            self._config_thresholds(),
            self._forecast_info(),
            self._forecast_diagnostics(),
            self._forecast_table(),
            self._decision_log(),
            self._cost_tracking(),
            self._weather_correlation(),
            self._system_info(),
            self._thermal_management(),
            self._learning_system(),
            self._binary_sensors(),
            self._switches(),
            self._footer(),
        ]
        return "\n".join(sections)

    def _header(self) -> str:
        return f"""# LocalShift Charging Plan Snapshot
**Timestamp:** {format_timestamp()}
**Purpose:** Point-in-time snapshot for debugging charging plan behavior

---"""

    def _current_state(self) -> str:
        mode = self.state("sensor.localshift_battery_mode")
        soc = self.state("sensor.my_home_percentage_charged")
        battery_power = self.state("sensor.my_home_battery_power")
        grid_power = self.state("sensor.my_home_grid_power")
        solar_power = self.state("sensor.my_home_solar_power")
        buy_price = self.state("sensor.100h_general_price")
        sell_price = self.state("sensor.100h_feed_in_price")
        price_spike = self.state("binary_sensor.100h_price_spike")
        demand_window = self.state("binary_sensor.localshift_demand_window")

        return f"""
## CURRENT STATE

| Metric | Value |
|--------|-------|
| **Mode** | `{mode}` |
| **SOC** | {soc}% |
| **Battery Power** | {battery_power} kW |
| **Grid Power** | {grid_power} kW |
| **Solar Power** | {solar_power} kW |
| **Buy Price** | ${buy_price}/kWh |
| **Sell Price** | ${sell_price}/kWh |
| **Price Spike** | {price_spike} |
| **Demand Window** | {demand_window} |"""

    def _entity_health(self) -> str:
        integration_status = self.state("sensor.localshift_integration_status")
        entity_health = self.state("sensor.localshift_entity_health")
        entities = self.attr("sensor.localshift_entity_health", "entities", {})
        errors = self.attr("sensor.localshift_entity_health", "errors", [])
        warnings = self.attr("sensor.localshift_entity_health", "warnings", [])

        # Find stale and missing entities
        stale = []
        missing = []
        for name, info in (entities or {}).items():
            if info.get("status") == "stale":
                stale.append(
                    f"- {info.get('entity_id')}: {info.get('error_message', 'stale')}"
                )
            elif info.get("status") == "missing":
                missing.append(
                    f"- {info.get('entity_id')}: {info.get('error_message', 'missing')}"
                )

        stale_str = "\n".join(stale) if stale else "None"
        missing_str = "\n".join(missing) if missing else "None"
        errors_str = "\n".join(f"- {e}" for e in (errors or [])) if errors else "None"
        warnings_str = (
            "\n".join(f"- {w}" for w in (warnings or [])) if warnings else "None"
        )

        return f"""
## ENTITY HEALTH

| Metric | Value |
|--------|-------|
| **Integration Status** | `{integration_status}` |
| **Entity Health** | {entity_health} |

**Errors:** {len(errors or [])}
{errors_str}

**Warnings:** {len(warnings or [])}
{warnings_str}

**Stale Entities:**
{stale_str}

**Missing Entities:**
{missing_str}"""

    def _internal_state_flags(self) -> str:
        automation = self.state("switch.localshift_automation_enabled")
        target_reached = self.attr(
            "sensor.localshift_forecast_battery", "target_reached_today"
        )
        spike_coming = self.state("binary_sensor.localshift_price_spike_coming")
        max_price = self.attr(
            "binary_sensor.localshift_price_spike_coming", "max_forecast_price", 0
        )
        max_buy = self.attr(
            "binary_sensor.localshift_price_spike_coming", "max_buy_forecast_price", 0
        )
        mode = self.state("sensor.localshift_battery_mode")
        can_reach = self.state("binary_sensor.localshift_solar_can_reach_target")
        boost_needed = self.state("binary_sensor.localshift_charge_boost_needed")

        return f"""
## INTERNAL STATE FLAGS

| Flag | Value |
|------|-------|
| **Manual Override** | {automation == "off"} |
| **Target Reached Today** | {target_reached} |
| **Forecast Spike Within Window** | {spike_coming} |
| **Max Forecast Price** | ${max_price}/kWh |
| **Max Buy Forecast Price** | ${max_buy}/kWh |
| **Force Discharge Active** | {mode == "force_discharge"} |
| **Force Charge Active** | {mode == "force_charge"} |
| **Boost Charge Active** | {mode == "boost_charging"} |
| **Solar Can Reach Target** | {can_reach} |
| **Boost Charge Needed** | {boost_needed} |"""

    def _mode_decision(self) -> str:
        mode_source = self.attr(
            "sensor.localshift_forecast_diagnostics", "debug_mode_source", "unknown"
        )
        slot_found = self.attr(
            "sensor.localshift_forecast_diagnostics", "debug_forecast_slot_found", False
        )
        slot_time = self.attr(
            "sensor.localshift_forecast_diagnostics", "debug_forecast_slot_time", "-"
        )
        first_slot = self.attr(
            "sensor.localshift_forecast_diagnostics",
            "debug_first_forecast_slot_time",
            "-",
        )
        time_gap = self.attr(
            "sensor.localshift_forecast_diagnostics", "debug_time_gap_seconds", 0
        )
        dry_run = self.state("switch.localshift_dry_run")

        return f"""
## MODE DECISION DEBUG

| Attribute | Value |
|-----------|-------|
| **Mode Source** | {mode_source} |
| **Forecast Slot Found** | {slot_found} |
| **Forecast Slot Time** | {slot_time} |
| **First Forecast Slot** | {first_slot} |
| **Time Gap** | {time_gap}s |
| **Dry Run** | {dry_run == "on"} |"""

    def _config_thresholds(self) -> str:
        cheap_pct = self.state("number.localshift_cheap_price_percentile")
        max_precharge = self.state("number.localshift_max_pre_charge_price")
        target = self.state("number.localshift_battery_target")
        effective_cheap = self.state("sensor.localshift_price_cheap_effective")
        charge_stop = self.state("sensor.localshift_price_cheap_charge_stop")
        weighted_fit = self.state("sensor.localshift_solar_weighted_avg_fit")
        solar_remaining = self.attr(
            "sensor.localshift_solar_weighted_avg_fit", "total_solar_remaining_kwh", 0
        )

        return f"""
## CONFIGURATION & THRESHOLDS

| Setting | Value |
|---------|-------|
| **Cheap Price Percentile** | {cheap_pct}% |
| **Max Pre-charge Price** | ${max_precharge}/kWh |
| **Battery Target** | {target}% |
| **Effective Cheap Price** | ${effective_cheap}/kWh |
| **Cheap Charge Stop Price** | ${charge_stop}/kWh |
| **Solar Weighted Avg FIT** | ${weighted_fit}/kWh |
| **Solar Remaining** | {solar_remaining} kWh |"""

    def _forecast_info(self) -> str:
        predicted_soc = self.attr(
            "sensor.localshift_forecast_battery", "predicted_soc", 0
        )
        deficit = self.attr("sensor.localshift_forecast_battery", "deficit_kwh", 0)
        solar_before = self.attr(
            "sensor.localshift_forecast_battery", "solar_before_dw_kwh", 0
        )
        net_solar = self.attr("sensor.localshift_forecast_battery", "net_solar_kwh", 0)
        hours_to_dw = self.attr(
            "sensor.localshift_forecast_battery", "hours_to_target_time", 0
        )
        can_reach = self.attr(
            "sensor.localshift_forecast_battery", "can_reach_target", True
        )
        boost_needed = self.attr(
            "sensor.localshift_forecast_battery", "boost_needed", False
        )
        daily_entries = self.state("sensor.localshift_forecast_daily")
        slot_count = self.attr("sensor.localshift_forecast_daily", "slot_count", 0)
        solcast_today = self.attr(
            "sensor.localshift_forecast_daily", "solcast_today_entries", 0
        )
        solcast_tomorrow = self.attr(
            "sensor.localshift_forecast_daily", "solcast_tomorrow_entries", 0
        )

        return f"""
## FORECAST INFORMATION

| Attribute | Value |
|-----------|-------|
| **Predicted SOC** | {predicted_soc}% |
| **Deficit** | {deficit} kWh |
| **Solar Before DW** | {solar_before} kWh |
| **Net Solar** | {net_solar} kWh |
| **Hours to DW** | {hours_to_dw}h |
| **Can Reach Target** | {can_reach} |
| **Boost Needed** | {boost_needed} |
| **Daily Forecast Entries** | {daily_entries} |
| **Slot Count** | {slot_count} |
| **Solcast Today Entries** | {solcast_today} |
| **Solcast Tomorrow Entries** | {solcast_tomorrow} |"""

    def _forecast_diagnostics(self) -> str:
        current_load = self.attr(
            "sensor.localshift_forecast_diagnostics", "current_load_kw", 0
        )
        recent_load = self.attr(
            "sensor.localshift_forecast_diagnostics", "recent_load_1hr_kw", 0
        )
        stat_id = self.attr(
            "sensor.localshift_forecast_diagnostics", "recent_load_1hr_statistic_id", ""
        )
        samples = self.attr(
            "sensor.localshift_forecast_diagnostics", "recent_load_1hr_samples", 0
        )
        error = self.attr(
            "sensor.localshift_forecast_diagnostics", "recent_load_1hr_last_error", ""
        )
        weighting = self.attr(
            "sensor.localshift_forecast_diagnostics", "consumption_weighting", 0
        )
        source = self.attr(
            "sensor.localshift_forecast_diagnostics", "consumption_source", ""
        )
        profile_hours = self.attr(
            "sensor.localshift_forecast_diagnostics", "consumption_profile_hours", 0
        )
        fallback_hours = self.attr(
            "sensor.localshift_forecast_diagnostics", "consumption_fallback_hours", 0
        )

        return f"""
## FORECAST DIAGNOSTICS

| Attribute | Value |
|-----------|-------|
| **Current Load** | {current_load} kW |
| **Recent 1hr Load** | {recent_load} kW |
| **Recent 1hr Statistic ID** | {stat_id} |
| **Recent 1hr Samples** | {samples} |
| **Recent 1hr Error** | {error or "None"} |
| **Consumption Weighting** | {weighting} |
| **Consumption Source** | {source} |
| **Consumption Profile Hours** | {profile_hours} |
| **Consumption Fallback Hours** | {fallback_hours} |"""

    def _forecast_table(self) -> str:
        """Generate detailed forecast table with slot-by-slot breakdown."""
        # Get forecast slots from forecast_daily
        forecast_slots = self.attr(
            "sensor.localshift_forecast_daily", "forecast_slots", []
        )
        grid_interaction = self.attr(
            "sensor.localshift_forecast_grid", "grid_interaction", []
        )
        buy_prices = self.attr("sensor.localshift_forecast_prices", "buy_prices", [])
        sell_prices = self.attr("sensor.localshift_forecast_prices", "sell_prices", [])

        # Summary stats
        grid_slots = self.attr(
            "sensor.localshift_forecast_grid", "grid_charge_slots", 0
        )
        export_slots = self.attr(
            "sensor.localshift_forecast_grid", "proactive_export_slots", 0
        )
        total_import = self.attr(
            "sensor.localshift_forecast_grid", "total_grid_import_kwh", 0
        )
        total_export = self.attr(
            "sensor.localshift_forecast_grid", "total_grid_export_kwh", 0
        )

        # Build header
        lines = [
            "## FORECAST TABLE",
            "",
            f"**Grid Charge Slots:** {grid_slots} | **Proactive Export Slots:** {export_slots}",
            f"**Total Import:** {total_import} kWh | **Total Export:** {total_export} kWh",
            "",
            "| Time | SOC% | Solar | Load | Net | Buy$ | Sell$ | GridIn | GridOut | GC | Boost | PE |",
            "|:----:|-----:|------:|-----:|----:|-----:|------:|-------:|--------:|:--:|:-----:|:--:|",
        ]

        # Build rows - limit to first 48 slots (12 hours) to keep output manageable
        max_slots = 48
        for i, slot in enumerate((forecast_slots or [])[:max_slots]):
            grid = (
                (grid_interaction or [])[i] if i < len(grid_interaction or []) else {}
            )
            buy = (buy_prices or [])[i] if i < len(buy_prices or []) else {}
            sell = (sell_prices or [])[i] if i < len(sell_prices or []) else {}

            time_str = slot.get("time", "-")
            soc = slot.get("predicted_soc", 0)
            solar = slot.get("solar_kwh", 0)
            load = slot.get("consumption_kwh", 0)
            net = slot.get("net_kwh", 0)
            buy_price = buy.get("price", 0) if isinstance(buy, dict) else 0
            sell_price = sell.get("price", 0) if isinstance(sell, dict) else 0
            grid_in = grid.get("grid_import_kwh", 0) if isinstance(grid, dict) else 0
            grid_out = grid.get("grid_export_kwh", 0) if isinstance(grid, dict) else 0
            gc = "Y" if grid.get("grid_charge") else "-"
            boost = "Y" if grid.get("grid_charge_boost") else "-"
            pe = "Y" if grid.get("proactive_export") else "-"

            lines.append(
                f"| {time_str} | {soc:.1f} | {solar:.3f} | {load:.3f} | {net:.3f} | "
                f"${buy_price:.3f} | ${sell_price:.3f} | {grid_in:.4f} | {grid_out:.4f} | "
                f"{gc} | {boost} | {pe} |"
            )

        if len(forecast_slots or []) > max_slots:
            lines.append(
                f"| ... | ({len(forecast_slots) - max_slots} more slots) | | | | | | | | | | |"
            )

        return "\n".join(lines)

    def _decision_log(self) -> str:
        reason = self.attr(
            "sensor.localshift_decision_log", "reason", "No decisions yet"
        )
        soc = self.attr("sensor.localshift_decision_log", "soc", 0)
        buy_price = self.attr("sensor.localshift_decision_log", "buy_price", 0)
        sell_price = self.attr("sensor.localshift_decision_log", "sell_price", 0)
        timestamp = self.attr("sensor.localshift_decision_log", "timestamp", "")
        history = self.attr("sensor.localshift_decision_log", "history", [])

        history_lines = []
        for entry in (history or [])[-5:]:
            history_lines.append(
                f"- {entry.get('timestamp', '')}: {entry.get('reason', '')} (SOC: {entry.get('soc')}%)"
            )
        history_str = "\n".join(history_lines) if history_lines else "No history"

        return f"""
## DECISION LOG

| Attribute | Value |
|-----------|-------|
| **Latest Decision** | {reason} |
| **Latest SOC** | {soc}% |
| **Latest Buy Price** | ${buy_price}/kWh |
| **Latest Sell Price** | ${sell_price}/kWh |
| **Latest Timestamp** | {timestamp} |

**Recent History:**
{history_str}"""

    def _cost_tracking(self) -> str:
        net_cost = self.state("sensor.localshift_cost_electricity_net")
        import_cost = self.attr(
            "sensor.localshift_cost_electricity_net", "grid_import_cost", 0
        )
        export_revenue = self.attr(
            "sensor.localshift_cost_electricity_net", "grid_export_revenue", 0
        )
        battery_savings = self.attr(
            "sensor.localshift_cost_electricity_net", "battery_savings", 0
        )
        charge_cost = self.attr(
            "sensor.localshift_cost_electricity_net", "battery_charge_cost", 0
        )

        return f"""
## COST TRACKING

| Metric | Value |
|--------|-------|
| **Net Cost Today** | ${net_cost} |
| **Import Cost** | ${import_cost} |
| **Export Revenue** | ${export_revenue} |
| **Battery Savings** | ${battery_savings} |
| **Battery Charge Cost** | ${charge_cost} |"""

    def _weather_correlation(self) -> str:
        entity = self.attr(
            "sensor.localshift_forecast_diagnostics",
            "weather_entity_id",
            "not configured",
        )
        temp = self.attr(
            "sensor.localshift_forecast_diagnostics",
            "weather_temperature_current",
            "N/A",
        )
        condition = self.attr(
            "sensor.localshift_forecast_diagnostics", "weather_condition", "unknown"
        )
        learning = self.attr(
            "sensor.localshift_forecast_diagnostics", "weather_learning_enabled", False
        )
        confidence = self.attr(
            "sensor.localshift_forecast_diagnostics",
            "weather_correlation_confidence",
            "low",
        )
        samples = self.attr(
            "sensor.localshift_forecast_diagnostics", "weather_sample_count", 0
        )
        cooling = self.attr(
            "sensor.localshift_forecast_diagnostics", "weather_avg_cooling_slope", 0
        )
        heating = self.attr(
            "sensor.localshift_forecast_diagnostics", "weather_avg_heating_slope", 0
        )
        r_squared = self.attr(
            "sensor.localshift_forecast_diagnostics", "weather_avg_r_squared", 0
        )
        adjustment = self.attr(
            "sensor.localshift_forecast_diagnostics",
            "weather_adjustment_applied",
            False,
        )

        return f"""
## WEATHER CORRELATION

| Attribute | Value |
|-----------|-------|
| **Weather Entity** | {entity} |
| **Current Temperature** | {temp}°C |
| **Weather Condition** | {condition} |
| **Learning Enabled** | {learning} |
| **Confidence** | {confidence} |
| **Total Samples** | {samples} |
| **Cooling Slope** | {cooling} kW/°C |
| **Heating Slope** | {heating} kW/°C |
| **Average R²** | {r_squared} |
| **Adjustment Applied** | {adjustment} |"""

    def _system_info(self) -> str:
        op_mode = self.state("select.my_home_operation_mode")
        backup = self.state("number.my_home_backup_reserve")
        allow_export = self.state("select.my_home_allow_export")

        return f"""
## SYSTEM INFO

| Setting | Value |
|---------|-------|
| **Operation Mode** | {op_mode} |
| **Backup Reserve** | {backup}% |
| **Allow Export** | {allow_export} |"""

    def _thermal_management(self) -> str:
        enabled = self.state("binary_sensor.localshift_thermal_management_enabled")
        mode = self.state("sensor.localshift_daily_thermal_mode")
        locked = self.attr("sensor.localshift_daily_thermal_mode", "mode_locked", False)
        determined = self.attr(
            "sensor.localshift_daily_thermal_mode", "determined_at", ""
        )
        preconditioning = self.state("binary_sensor.localshift_preconditioning_active")
        solar_taper = self.state("binary_sensor.localshift_solar_taper_active")
        cooling_trigger = self.state("number.localshift_cooling_trigger_temp")
        heating_trigger = self.state("number.localshift_heating_trigger_temp")

        return f"""
## THERMAL MANAGEMENT

| Attribute | Value |
|-----------|-------|
| **Thermal Management Enabled** | {enabled} |
| **Daily Thermal Mode** | {mode} |
| **Mode Locked** | {locked} |
| **Mode Determined At** | {determined or "N/A"} |
| **Preconditioning Active** | {preconditioning} |
| **Solar Taper Active** | {solar_taper} |
| **Cooling Trigger Temp** | {cooling_trigger}°C |
| **Heating Trigger Temp** | {heating_trigger}°C |"""

    def _learning_system(self) -> str:
        status = self.state("sensor.localshift_learning_status")
        quality = self.state("sensor.localshift_decision_quality")
        decisions = self.attr(
            "sensor.localshift_learning_status", "total_decisions_today", 0
        )
        avg_today = self.attr(
            "sensor.localshift_learning_status", "avg_decision_score_today", 0
        )
        avg_7d = self.attr(
            "sensor.localshift_learning_status", "avg_decision_score_7d", 0
        )
        trend = self.attr("sensor.localshift_learning_status", "cost_trend", "stable")
        grid_eff = self.attr(
            "sensor.localshift_decision_quality", "grid_charge_efficiency", 0
        )
        export_loss = self.attr(
            "sensor.localshift_decision_quality", "export_loss_ratio", 0
        )

        return f"""
## LEARNING SYSTEM

| Attribute | Value |
|-----------|-------|
| **Learning Status** | {status} |
| **Decision Quality** | {quality}% |
| **Decisions Today** | {decisions} |
| **Avg Score Today** | {avg_today} |
| **Avg Score 7d** | {avg_7d} |
| **Cost Trend** | {trend} |
| **Grid Charge Efficiency** | {grid_eff}% |
| **Export Loss Ratio** | {export_loss}% |"""

    def _binary_sensors(self) -> str:
        sensors = [
            ("Price Spike Coming", "binary_sensor.localshift_price_spike_coming"),
            ("Discharge Forced", "binary_sensor.localshift_discharge_forced"),
            ("Charge Forced", "binary_sensor.localshift_charge_forced"),
            ("Charge Boost", "binary_sensor.localshift_charge_boost"),
            (
                "Price Expensive Coming",
                "binary_sensor.localshift_price_expensive_coming",
            ),
            (
                "Solar Can Reach Target",
                "binary_sensor.localshift_solar_can_reach_target",
            ),
            ("Charge Boost Needed", "binary_sensor.localshift_charge_boost_needed"),
            ("Demand Window", "binary_sensor.localshift_demand_window"),
            (
                "Excess Solar Available",
                "binary_sensor.localshift_excess_solar_available",
            ),
            ("Tesla Override Active", "binary_sensor.localshift_tesla_override_active"),
            (
                "Preconditioning Active",
                "binary_sensor.localshift_preconditioning_active",
            ),
            ("Solar Taper Active", "binary_sensor.localshift_solar_taper_active"),
            (
                "Thermal Management Enabled",
                "binary_sensor.localshift_thermal_management_enabled",
            ),
        ]

        lines = [
            "## BINARY SENSORS SUMMARY",
            "",
            "| Sensor | State |",
            "|--------|-------|",
        ]
        for name, entity_id in sensors:
            state = self.state(entity_id, "unavailable")
            lines.append(f"| **{name}** | {state} |")

        return "\n".join(lines)

    def _switches(self) -> str:
        switches = [
            ("Automation Enabled", "switch.localshift_automation_enabled"),
            ("Spike Discharge Enabled", "switch.localshift_spike_discharge_enabled"),
            (
                "Spike Discharge Conservative",
                "switch.localshift_spike_discharge_conservative",
            ),
            ("Dry Run", "switch.localshift_dry_run"),
            ("Demand Window Block", "switch.localshift_demand_window_block"),
            (
                "Allow DW Entry Under Target",
                "switch.localshift_allow_dw_entry_under_target",
            ),
            ("Notifications Enabled", "switch.localshift_notifications_enabled"),
            ("Enable Learning", "switch.localshift_enable_learning"),
        ]

        lines = ["## SWITCHES SUMMARY", "", "| Switch | State |", "|--------|-------|"]
        for name, entity_id in switches:
            state = self.state(entity_id, "unavailable")
            lines.append(f"| **{name}** | {state} |")

        return "\n".join(lines)

    def _footer(self) -> str:
        return f"""
---

*Snapshot generated: {format_timestamp()}*
"""


def main():
    parser = argparse.ArgumentParser(
        description="Generate LocalShift charging plan snapshot"
    )
    parser.add_argument(
        "--output",
        "-o",
        default=".",
        help="Output directory for snapshot file (default: current directory)",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("HA_URL", "http://homeassistant:8123"),
        help="Home Assistant URL (default: HA_URL env or http://homeassistant:8123)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("HA_TOKEN") or os.environ.get("HA_LONG_LIVED_TOKEN", ""),
        help="Home Assistant long-lived access token (default: HA_TOKEN or HA_LONG_LIVED_TOKEN env)",
    )
    args = parser.parse_args()

    if not args.token:
        print(
            "Error: No HA token provided. Set HA_TOKEN or HA_LONG_LIVED_TOKEN env or use --token",
            file=sys.stderr,
        )
        sys.exit(1)

    # Create client and generator
    client = HomeAssistantClient(args.url, args.token)
    generator = SnapshotGenerator(client)

    # Generate snapshot
    print("Generating snapshot...", file=sys.stderr)
    markdown = generator.generate()

    # Write to file
    output_path = os.path.join(args.output, format_filename())
    with open(output_path, "w") as f:
        f.write(markdown)

    print(f"Snapshot saved to: {output_path}", file=sys.stderr)
    print(markdown)


if __name__ == "__main__":
    main()

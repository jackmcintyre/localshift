"""Tests for agent tool configuration, specifically HomeAssistant MCP access control."""

import json
import os
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()
CONFIG_PATH = REPO_ROOT / "opencode.json"


def load_config() -> dict:
    """Load the OpenCode configuration."""
    with CONFIG_PATH.open("r") as f:
        return json.load(f)


def test_homeassistant_agent_has_ha_tools():
    """Ensure the 'homeassistant' subagent has HA tools enabled."""
    config = load_config()
    ha_agent = config.get("agent", {}).get("homeassistant", {})
    ha_tools = ha_agent.get("tools", {})
    assert "homeassistant_*" in ha_tools, (
        "homeassistant agent must enable homeassistant_* tools"
    )
    assert ha_tools["homeassistant_*"] is True, (
        "homeassistant_* must be true in homeassistant agent"
    )


def test_global_tools_do_not_include_ha_tools():
    """Ensure global tools configuration does NOT enable homeassistant_* tools."""
    config = load_config()
    global_tools = config.get("tools", {})
    # Either the key is absent or its value is falsy/not True
    if "homeassistant_*" in global_tools:
        pytest.fail(
            f"Global tools should not enable homeassistant_*; found: {global_tools['homeassistant_*']}"
        )
    # Alternatively, we can assert it's not present:
    assert "homeassistant_*" not in global_tools, (
        "Remove global homeassistant_* tool enablement"
    )


def test_other_agents_lack_ha_tools():
    """Sanity: No other agent should have homeassistant_* tools enabled."""
    config = load_config()
    agents = config.get("agent", {})
    for name, agent_cfg in agents.items():
        if name == "homeassistant":
            continue
        agent_tools = agent_cfg.get("tools", {})
        if "homeassistant_*" in agent_tools:
            pytest.fail(f"Agent '{name}' should not enable homeassistant_* tools")

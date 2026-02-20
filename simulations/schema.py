"""Scenario schema and loader for simulation testing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Scenario:
    """Represents a test scenario for battery automation logic.

    A scenario captures:
    - Input state (SOC, prices, forecasts, etc.)
    - Configuration overrides
    - Switch states
    - Expected outputs after computation
    """

    name: str
    description: str
    input: dict[str, Any]
    expected: dict[str, Any]
    config_overrides: dict[str, Any] = field(default_factory=dict)
    switch_states: dict[str, bool] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def from_json(cls, path: Path) -> Scenario:
        """Load a scenario from a JSON file.

        Args:
            path: Path to the JSON scenario file

        Returns:
            Scenario instance

        Raises:
            ValueError: If required fields are missing
        """
        with open(path) as f:
            data = json.load(f)

        required = {"name", "input", "expected"}
        missing = required - set(data.keys())
        if missing:
            raise ValueError(f"Scenario {path} missing required fields: {missing}")

        return cls(
            name=data["name"],
            description=data.get("description", ""),
            input=data["input"],
            expected=data["expected"],
            config_overrides=data.get("config_overrides", {}),
            switch_states=data.get("switch_states", {}),
            path=path,
        )

    @property
    def id(self) -> str:
        """Unique identifier for the scenario (filename without extension)."""
        if self.path:
            return self.path.stem
        return self.name.lower().replace(" ", "-")


def discover_scenarios(base_path: Path | None = None) -> list[Path]:
    """Discover all scenario JSON files in the scenarios directory.

    Args:
        base_path: Base directory to search (defaults to simulations/scenarios/)

    Returns:
        List of paths to scenario JSON files
    """
    if base_path is None:
        # Default to simulations/scenarios/ relative to this file
        base_path = Path(__file__).parent / "scenarios"

    if not base_path.exists():
        return []

    # Find all .json files, excluding templates (starting with _)
    scenarios = []
    for path in base_path.rglob("*.json"):
        if not path.name.startswith("_"):
            scenarios.append(path)

    # Sort for consistent test ordering
    return sorted(scenarios)


def load_all_scenarios(base_path: Path | None = None) -> list[Scenario]:
    """Load all scenarios from the scenarios directory.

    Args:
        base_path: Base directory to search

    Returns:
        List of Scenario instances
    """
    return [Scenario.from_json(p) for p in discover_scenarios(base_path)]

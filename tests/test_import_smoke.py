"""Every module under custom_components/localshift imports.

A deleted symbol that something still imports should fail here, by module
name, rather than surface as a collection error in whichever test file
happens to import the casualty first (or not at all, for a module no test
imports). The list comes from the files on disk, so a new module is covered
without touching this test.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PACKAGE_DIR = _REPO_ROOT / "custom_components" / "localshift"


def _module_names() -> list[str]:
    names = []
    for path in sorted(_PACKAGE_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(_REPO_ROOT).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join(parts))
    return names


def test_the_package_was_found():
    assert len(_module_names()) > 50


@pytest.mark.parametrize("module_name", _module_names())
def test_module_imports(module_name):
    try:
        importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - any import-time failure is the finding
        pytest.fail(
            f"{module_name} does not import: {type(exc).__name__}: {exc}",
            pytrace=False,
        )

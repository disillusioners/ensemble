"""Pytest fixtures for plugin_subsystem tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.plugin_subsystem._manifest_fixtures import (
    VALID_MINIMAL_MANIFEST,
    build_plugin,
)


@pytest.fixture
def valid_minimal_plugin(tmp_path: Path) -> Path:
    return build_plugin(tmp_path, VALID_MINIMAL_MANIFEST, name="test-plugin")


@pytest.fixture
def default_registry():
    from daemon.plugin_subsystem.path_type_registry import load_default_registry

    return load_default_registry()

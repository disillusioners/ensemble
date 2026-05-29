"""Pytest configuration for E2E tests - uses real MCP SDK.

This conftest.py ensures E2E tests use the real MCP SDK by removing
mocked MCP modules from sys.modules before tests run.
"""

import sys
import pytest


def pytest_configure(config):
    """Remove mocked MCP modules before any tests run."""
    # Find and remove any mocked MCP modules
    modules_to_remove = []
    for key in list(sys.modules.keys()):
        if key.startswith('mcp'):
            modules_to_remove.append(key)

    for mod in modules_to_remove:
        del sys.modules[mod]


@pytest.fixture(scope="session", autouse=True)
def ensure_real_mcp():
    """Ensure real MCP modules are loaded for this test session."""
    # Force reimport of the MCP modules
    for key in list(sys.modules.keys()):
        if key.startswith('mcp'):
            del sys.modules[key]

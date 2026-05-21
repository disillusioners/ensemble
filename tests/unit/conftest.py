"""Shared pytest fixtures for MCP tests."""
import os

import pytest


@pytest.fixture
def allow_local():
    """Allow local (loopback/private) URLs in tests. Default is now ALLOW=true, this fixture is for explicit opt-in."""
    # Set MCP_ALLOW_LOCAL (this is now the default, but some tests may want to be explicit)
    original_local = os.environ.get("MCP_ALLOW_LOCAL")
    os.environ["MCP_ALLOW_LOCAL"] = "true"
    yield
    if original_local is None:
        del os.environ["MCP_ALLOW_LOCAL"]
    else:
        os.environ["MCP_ALLOW_LOCAL"] = original_local


@pytest.fixture
def strict_local():
    """Block local (loopback/private) URLs in tests (strict SSRF mode)."""
    # Set MCP_ALLOW_LOCAL=false for strict SSRF blocking
    original_local = os.environ.get("MCP_ALLOW_LOCAL")
    os.environ["MCP_ALLOW_LOCAL"] = "false"
    yield
    if original_local is None:
        del os.environ["MCP_ALLOW_LOCAL"]
    else:
        os.environ["MCP_ALLOW_LOCAL"] = original_local

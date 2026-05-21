"""Shared pytest fixtures for MCP tests."""
import os

import pytest


@pytest.fixture
def allow_local():
    """Allow local (loopback/private) URLs in tests (SSRF protection is enabled by default)."""
    # Set MCP_ALLOW_LOCAL (new env var)
    original_local = os.environ.get("MCP_ALLOW_LOCAL")
    os.environ["MCP_ALLOW_LOCAL"] = "true"
    yield
    if original_local is None:
        del os.environ["MCP_ALLOW_LOCAL"]
    else:
        os.environ["MCP_ALLOW_LOCAL"] = original_local

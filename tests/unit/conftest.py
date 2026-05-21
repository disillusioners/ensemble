"""Shared pytest fixtures for MCP tests."""
import os

import pytest


@pytest.fixture
def allow_loopback():
    """Allow loopback URLs in tests (SSRF protection is enabled by default)."""
    original = os.environ.get("MCP_ALLOW_LOOPBACK")
    os.environ["MCP_ALLOW_LOOPBACK"] = "true"
    yield
    if original is None:
        del os.environ["MCP_ALLOW_LOOPBACK"]
    else:
        os.environ["MCP_ALLOW_LOOPBACK"] = original

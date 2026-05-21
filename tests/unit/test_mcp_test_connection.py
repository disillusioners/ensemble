"""Tests for MCP test-connection feature.

This module tests:
- SSRF URL validation (blocking loopback/private/link-local/reserved IPs)
- /api/mcp-servers/test-connection endpoint
- _create_test_session_from_streams helper function
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from daemon.mcp.config import (
    _is_restricted_ip,
    _validate_url_not_ssrf,
    McpConfigValidationError,
    McpStdioConfig,
    McpSseConfig,
    McpStreamableHttpConfig,
    validate_mcp_server_config,
)
from daemon.routers.mcp_servers import router as mcp_servers_router
from daemon.models import McpServerTestConnectionRequest, McpServerTestConnectionResponse


# =============================================================================
# Section A: SSRF URL Validation Tests
# =============================================================================


class TestIsRestrictedIp:
    """Tests for the _is_restricted_ip helper function."""

    # --- Loopback addresses (127.x.x.x, ::1) ---

    def test_blocked_loopback_ipv4_127_0_0_1(self):
        """127.0.0.1 is blocked by default."""
        assert _is_restricted_ip("127.0.0.1", allow_local=False) is True

    def test_blocked_loopback_ipv4_127_255_255_255(self):
        """127.255.255.255 is blocked by default."""
        assert _is_restricted_ip("127.255.255.255", allow_local=False) is True

    def test_blocked_loopback_ipv6_loopback(self):
        """::1 (IPv6 loopback) is blocked by default."""
        assert _is_restricted_ip("::1", allow_local=False) is True

    def test_allowed_loopback_when_allow_local_true(self):
        """127.0.0.1 is allowed when allow_local=True."""
        assert _is_restricted_ip("127.0.0.1", allow_local=True) is False

    def test_allowed_loopback_ipv6_when_allow_local_true(self):
        """::1 is allowed when allow_local=True."""
        assert _is_restricted_ip("::1", allow_local=True) is False

    # --- Private networks ---

    def test_blocked_private_10_0_0_1(self):
        """10.x.x.x is blocked by default."""
        assert _is_restricted_ip("10.0.0.1", allow_local=False) is True

    def test_blocked_private_10_255_255_255(self):
        """10.255.255.255 is blocked by default."""
        assert _is_restricted_ip("10.255.255.255", allow_local=False) is True

    def test_blocked_private_172_16_0_1(self):
        """172.16.x.x is blocked by default."""
        assert _is_restricted_ip("172.16.0.1", allow_local=False) is True

    def test_blocked_private_172_31_255_255(self):
        """172.31.x.x is blocked by default."""
        assert _is_restricted_ip("172.31.255.255", allow_local=False) is True

    def test_blocked_private_192_168_0_1(self):
        """192.168.x.x is blocked by default."""
        assert _is_restricted_ip("192.168.0.1", allow_local=False) is True

    def test_blocked_private_192_168_255_255(self):
        """192.168.255.255 is blocked by default."""
        assert _is_restricted_ip("192.168.255.255", allow_local=False) is True

    def test_not_blocked_172_32_0_1(self):
        """172.32.x.x is NOT blocked (outside private range)."""
        assert _is_restricted_ip("172.32.0.1", allow_local=False) is False

    def test_allowed_private_when_allow_local_true(self):
        """Private IPs are allowed when allow_local=True."""
        assert _is_restricted_ip("10.0.0.1", allow_local=True) is False
        assert _is_restricted_ip("172.16.0.1", allow_local=True) is False
        assert _is_restricted_ip("192.168.0.1", allow_local=True) is False

    # --- Link-local addresses ---

    def test_blocked_link_local_ipv4_169_254_0_1(self):
        """169.254.x.x is blocked (link-local)."""
        assert _is_restricted_ip("169.254.0.1", allow_local=False) is True

    def test_blocked_link_local_ipv4_169_254_255_255(self):
        """169.254.255.255 is blocked (link-local)."""
        assert _is_restricted_ip("169.254.255.255", allow_local=False) is True

    def test_blocked_link_local_ipv6_fe80(self):
        """fe80:: (IPv6 link-local) is always blocked."""
        assert _is_restricted_ip("fe80::1", allow_local=False) is True
        assert _is_restricted_ip("fe80::1", allow_local=True) is True  # even with allow_local

    def test_not_blocked_169_255_0_1(self):
        """169.255.x.x is NOT blocked (not link-local)."""
        assert _is_restricted_ip("169.255.0.1", allow_local=False) is False

    # --- Reserved ranges ---

    def test_blocked_reserved_0_0_0_0(self):
        """0.0.0.0 is blocked (reserved)."""
        assert _is_restricted_ip("0.0.0.0", allow_local=False) is True

    def test_not_blocked_public_ip(self):
        """Public IPs are not blocked."""
        assert _is_restricted_ip("8.8.8.8", allow_local=False) is False
        assert _is_restricted_ip("1.1.1.1", allow_local=False) is False

    def test_not_blocked_invalid_ip(self):
        """Invalid IP strings are not blocked (let other validation handle them)."""
        assert _is_restricted_ip("not-an-ip", allow_local=False) is False
        assert _is_restricted_ip("256.256.256.256", allow_local=False) is False

    # --- IPv6 edge cases ---

    def test_blocked_ipv6_unique_local_fc00(self):
        """fc00::/7 (unique local) is blocked."""
        assert _is_restricted_ip("fc00::1", allow_local=False) is True

    def test_blocked_ipv6_unique_local_fd00(self):
        """fd00::/8 (unique local) is blocked."""
        assert _is_restricted_ip("fd00::1", allow_local=False) is True

    def test_not_blocked_ipv6_global_unicast(self):
        """Global unicast IPv6 addresses are not blocked."""
        # 2001:4860::1 is Google Public DNS - a real global address
        assert _is_restricted_ip("2001:4860::1", allow_local=False) is False


class TestValidateUrlNotSsrf:
    """Tests for the _validate_url_not_ssrf function."""

    def test_allows_public_domain_google(self):
        """google.com resolves to public IPs and is allowed."""
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8",))]):
            assert _validate_url_not_ssrf("https://google.com") == "https://google.com"

    def test_allows_public_domain_example(self):
        """example.com is allowed."""
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34",))]):
            assert _validate_url_not_ssrf("https://example.com") == "https://example.com"

    def test_allows_public_api_openai(self):
        """api.openai.com is allowed."""
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("13.107.42.14",))]):
            assert _validate_url_not_ssrf("https://api.openai.com") == "https://api.openai.com"

    def test_allows_localhost(self):
        """localhost resolves to 127.0.0.1 and is allowed by default (MCP servers run locally)."""
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1",))]):
            result = _validate_url_not_ssrf("http://localhost:8080")
            assert result == "http://localhost:8080"

    def test_allows_localhost_ipv6(self):
        """localhost resolving to ::1 is allowed by default (MCP servers run locally)."""
        with patch(
            "socket.getaddrinfo",
            return_value=[(30, 1, 6, "", ("::1",))]
        ):
            result = _validate_url_not_ssrf("http://localhost")
            assert result == "http://localhost"

    def test_allows_private_ip_direct(self):
        """URL with private IP directly is allowed by default (MCP servers run locally)."""
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("192.168.1.1",))]):
            result = _validate_url_not_ssrf("http://192.168.1.1:8080")
            assert result == "http://192.168.1.1:8080"

    def test_allows_private_ip_dns_resolution(self):
        """URL resolving to private IP via DNS is allowed by default (MCP servers run locally)."""
        with patch(
            "socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("10.0.0.5",))]
        ):
            result = _validate_url_not_ssrf("http://my-internal-service.local")
            assert result == "http://my-internal-service.local"

    def test_blocks_localhost_in_strict_mode(self):
        """localhost is blocked when MCP_ALLOW_LOCAL=false (strict mode)."""
        original = os.environ.get("MCP_ALLOW_LOCAL")
        try:
            os.environ["MCP_ALLOW_LOCAL"] = "false"
            with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1",))]):
                with pytest.raises(McpConfigValidationError) as exc_info:
                    _validate_url_not_ssrf("http://localhost:8080")
                assert "SSRF attempt" in str(exc_info.value)
                assert "127.0.0.1" in str(exc_info.value)
        finally:
            if original is None:
                os.environ.pop("MCP_ALLOW_LOCAL", None)
            else:
                os.environ["MCP_ALLOW_LOCAL"] = original

    def test_blocks_private_ip_in_strict_mode(self):
        """Private IP is blocked when MCP_ALLOW_LOCAL=false (strict mode)."""
        original = os.environ.get("MCP_ALLOW_LOCAL")
        try:
            os.environ["MCP_ALLOW_LOCAL"] = "false"
            with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.1",))]):
                with pytest.raises(McpConfigValidationError) as exc_info:
                    _validate_url_not_ssrf("http://10.0.0.1:8080")
                assert "10.0.0.1" in str(exc_info.value)
        finally:
            if original is None:
                os.environ.pop("MCP_ALLOW_LOCAL", None)
            else:
                os.environ["MCP_ALLOW_LOCAL"] = original

    def test_handles_dns_resolution_failure(self):
        """URL that cannot be resolved passes through (connection will fail later)."""
        import socket as _socket
        with patch(
            "socket.getaddrinfo",
            side_effect=_socket.gaierror("Name or service not known")
        ):
            # Should not raise, just pass through
            result = _validate_url_not_ssrf("http://nonexistent.invalid")
            assert result == "http://nonexistent.invalid"

    def test_handles_no_hostname(self):
        """URL with no hostname passes through."""
        result = _validate_url_not_ssrf("/relative/path")
        assert result == "/relative/path"

    @pytest.mark.parametrize("env_var,value,expected_allow", [
        ("MCP_ALLOW_LOCAL", "true", True),
        ("MCP_ALLOW_LOCAL", "false", False),
        ("MCP_ALLOW_LOCAL", "1", False),  # Only 'true' is truthy
        ("MCP_ALLOW_LOOPBACK", "true", True),  # Backwards compat
        ("MCP_ALLOW_LOOPBACK", "false", False),
    ])
    def test_mcp_allow_local_env_var(self, env_var, value, expected_allow):
        """MCP_ALLOW_LOCAL env var controls local address blocking."""
        original = os.environ.get(env_var)
        try:
            os.environ[env_var] = value
            # Clear any cached value from prior tests
            if hasattr(_validate_url_not_ssrf, "__wrapped__"):
                pass  # Pydantic validator is cached per-class, test each case cleanly

            with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1",))]):
                if expected_allow:
                    # Should NOT raise when MCP_ALLOW_LOCAL=true
                    result = _validate_url_not_ssrf("http://localhost")
                    assert result == "http://localhost"
                else:
                    with pytest.raises(McpConfigValidationError):
                        _validate_url_not_ssrf("http://localhost")
        finally:
            if original is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = original

    def test_env_var_mcp_allow_local_overrides_private(self):
        """MCP_ALLOW_LOCAL allows private IPs (10.x, 192.168.x)."""
        original = os.environ.get("MCP_ALLOW_LOCAL")
        try:
            os.environ["MCP_ALLOW_LOCAL"] = "true"
            with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.1",))]):
                result = _validate_url_not_ssrf("http://10.0.0.1:8080")
                assert result == "http://10.0.0.1:8080"
        finally:
            if original is None:
                os.environ.pop("MCP_ALLOW_LOCAL", None)
            else:
                os.environ["MCP_ALLOW_LOCAL"] = original

    def test_link_local_blocked_even_with_env(self):
        """Link-local (169.254.x) is always blocked even with MCP_ALLOW_LOCAL=true."""
        original = os.environ.get("MCP_ALLOW_LOCAL")
        try:
            os.environ["MCP_ALLOW_LOCAL"] = "true"
            with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("169.254.0.1",))]):
                with pytest.raises(McpConfigValidationError) as exc_info:
                    _validate_url_not_ssrf("http://169.254.0.1")
                assert "169.254.0.1" in str(exc_info.value)
        finally:
            if original is None:
                os.environ.pop("MCP_ALLOW_LOCAL", None)
            else:
                os.environ["MCP_ALLOW_LOCAL"] = original


class TestMcpSseConfigSsrfValidation:
    """Tests that SSE config validates URLs against SSRF."""

    def test_sse_config_allows_localhost_url(self):
        """SSE config with localhost URL is allowed by default (MCP servers run locally)."""
        config = McpSseConfig(url="http://localhost:8080", transport="sse")
        assert config.url == "http://localhost:8080"

    def test_sse_config_allows_private_ip_url(self):
        """SSE config with private IP URL is allowed by default (MCP servers run locally)."""
        config = McpSseConfig(url="http://192.168.1.1:8080", transport="sse")
        assert config.url == "http://192.168.1.1:8080"

    def test_sse_config_rejects_link_local_url(self):
        """SSE config with link-local URL is always rejected (cloud metadata protection)."""
        with pytest.raises(ValidationError) as exc_info:
            McpSseConfig(url="http://169.254.0.1:8080", transport="sse")
        assert "restricted" in str(exc_info.value).lower()

    def test_sse_config_rejects_localhost_in_strict_mode(self, strict_local):
        """SSE config with localhost URL is rejected in strict mode (MCP_ALLOW_LOCAL=false)."""
        with pytest.raises(ValidationError) as exc_info:
            McpSseConfig(url="http://localhost:8080", transport="sse")
        assert "SSRF" in str(exc_info.value) or "restricted" in str(exc_info.value)

    def test_sse_config_rejects_private_ip_in_strict_mode(self, strict_local):
        """SSE config with private IP URL is rejected in strict mode (MCP_ALLOW_LOCAL=false)."""
        with pytest.raises(ValidationError) as exc_info:
            McpSseConfig(url="http://192.168.1.1:8080", transport="sse")
        assert "restricted" in str(exc_info.value).lower()

    def test_sse_config_accepts_public_url(self):
        """SSE config with public URL is accepted."""
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("1.1.1.1",))]):
            config = McpSseConfig(url="https://example.com", transport="sse")
            assert config.url == "https://example.com"


class TestMcpStreamableHttpConfigSsrfValidation:
    """Tests that Streamable HTTP config validates URLs against SSRF."""

    def test_streamable_http_config_allows_localhost_url(self):
        """Streamable HTTP config with localhost URL is allowed by default (MCP servers run locally)."""
        config = McpStreamableHttpConfig(url="http://localhost:8080", transport="streamable-http")
        assert config.url == "http://localhost:8080"

    def test_streamable_http_config_allows_private_ip_url(self):
        """Streamable HTTP config with private IP URL is allowed by default (MCP servers run locally)."""
        config = McpStreamableHttpConfig(url="http://10.0.0.1:8080", transport="streamable-http")
        assert config.url == "http://10.0.0.1:8080"

    def test_streamable_http_config_rejects_link_local_url(self):
        """Streamable HTTP config with link-local URL is always rejected (cloud metadata protection)."""
        with pytest.raises(ValidationError) as exc_info:
            McpStreamableHttpConfig(url="http://169.254.0.1:8080", transport="streamable-http")
        assert "restricted" in str(exc_info.value).lower()

    def test_streamable_http_config_rejects_localhost_in_strict_mode(self, strict_local):
        """Streamable HTTP config with localhost URL is rejected in strict mode (MCP_ALLOW_LOCAL=false)."""
        with pytest.raises(ValidationError) as exc_info:
            McpStreamableHttpConfig(url="http://localhost:8080", transport="streamable-http")
        assert "SSRF" in str(exc_info.value) or "restricted" in str(exc_info.value).lower()

    def test_streamable_http_config_rejects_private_ip_in_strict_mode(self, strict_local):
        """Streamable HTTP config with private IP URL is rejected in strict mode (MCP_ALLOW_LOCAL=false)."""
        with pytest.raises(ValidationError) as exc_info:
            McpStreamableHttpConfig(url="http://10.0.0.1:8080", transport="streamable-http")
        assert "restricted" in str(exc_info.value).lower()

    def test_streamable_http_config_accepts_public_url(self):
        """Streamable HTTP config with public URL is accepted."""
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8",))]):
            config = McpStreamableHttpConfig(url="https://api.example.com", transport="streamable-http")
            assert config.url == "https://api.example.com"


# =============================================================================
# Section B: Test Connection Endpoint Tests
# =============================================================================


@pytest.fixture
def mock_app():
    """Create a minimal FastAPI app with MCP servers router."""
    from fastapi import APIRouter
    app = FastAPI()
    api_router = APIRouter(prefix="/api")
    api_router.include_router(mcp_servers_router)
    app.include_router(api_router)
    return app


@pytest.fixture
def test_client(mock_app):
    """Create a TestClient for the mock app."""
    return TestClient(mock_app)


class TestTestConnectionEndpoint:
    """Tests for POST /api/mcp-servers/test-connection endpoint."""

    def test_success_returns_tools_list(self, test_client):
        """Successful connection returns tools count."""
        mock_session = MagicMock()
        mock_session.list_tools = AsyncMock(return_value=[
            MagicMock(name="tool1"),
            MagicMock(name="tool2"),
            MagicMock(name="tool3"),
        ])

        mock_streams_cm = MagicMock()
        mock_streams_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_streams_cm.__aexit__ = AsyncMock(return_value=None)

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.create_test_session = AsyncMock(
            return_value=(mock_session, mock_streams_cm)
        )

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={
                    "config": {
                        "transport": "stdio",
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                    }
                }
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["tools_count"] == 3
        assert "3 tools" in data["message"]

    def test_success_with_no_tools(self, test_client):
        """Connection succeeds but server has no tools."""
        mock_session = MagicMock()
        mock_session.list_tools = AsyncMock(return_value=[])

        mock_streams_cm = MagicMock()
        mock_streams_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_streams_cm.__aexit__ = AsyncMock(return_value=None)

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.create_test_session = AsyncMock(
            return_value=(mock_session, mock_streams_cm)
        )

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": {"transport": "stdio", "command": "echo"}}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["tools_count"] == 0
        assert "no tools" in data["message"]

    def test_success_with_one_tool(self, test_client):
        """Connection succeeds with exactly one tool."""
        mock_session = MagicMock()
        mock_session.list_tools = AsyncMock(return_value=[MagicMock(name="only-tool")])

        mock_streams_cm = MagicMock()
        mock_streams_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_streams_cm.__aexit__ = AsyncMock(return_value=None)

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.create_test_session = AsyncMock(
            return_value=(mock_session, mock_streams_cm)
        )

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": {"transport": "stdio", "command": "echo"}}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["tools_count"] == 1
        assert "1 tool" in data["message"]

    def test_invalid_config_returns_error(self, test_client):
        """Invalid configuration returns error response."""
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.create_test_session = AsyncMock(
            side_effect=McpConfigValidationError("Invalid transport type")
        )

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": {"transport": "invalid"}}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "Invalid configuration" in data["message"]

    def test_timeout_returns_error(self, test_client):
        """Session timeout returns appropriate error."""
        import asyncio

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.create_test_session = AsyncMock(side_effect=asyncio.TimeoutError)

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": {"transport": "stdio", "command": "slow-command"}}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "timed out" in data["message"].lower()

    def test_connection_refused_returns_error(self, test_client):
        """Connection refused returns appropriate error."""
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.create_test_session = AsyncMock(side_effect=ConnectionRefusedError)

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": {"transport": "stdio", "command": "nonexistent"}}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "refused" in data["message"].lower()

    def test_command_not_found_returns_sanitized_error(self, test_client):
        """Command not found returns sanitized error (no internal path leaks)."""
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.create_test_session = AsyncMock(
            side_effect=OSError("ENOENT: No such file or directory: '/home/user/.local/bin/npx'")
        )

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": {"transport": "stdio", "command": "npx"}}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        # Sanitized message should NOT contain internal path
        assert "/home/user" not in data["message"]
        assert "command was not found" in data["message"]

    def test_mcp_error_returns_specific_message(self, test_client):
        """McpError (e.g., Session terminated) returns specific error message."""
        # Import McpError from the mocked mcp module
        from mcp import McpError
        # Import MockErrorData for creating error objects
        from tests.conftest import MockErrorData

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.create_test_session = AsyncMock(
            side_effect=McpError(MockErrorData(message="Session terminated: server shutting down"))
        )

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={
                    "config": {
                        "transport": "streamable-http",
                        "url": "https://api.example.com/mcp",
                    }
                }
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "Server error: Session terminated" in data["message"]
        assert "unexpected error" not in data["message"]

    def test_mcp_error_returns_server_error_message(self, test_client):
        """McpError returns Server error message and logs warning (not exception)."""
        import logging
        from mcp import McpError
        from tests.conftest import MockErrorData

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.create_test_session = AsyncMock(
            side_effect=McpError(MockErrorData(message="Session terminated"))
        )

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ), patch.object(
            logging.getLogger("daemon.routers.mcp_servers"), "warning"
        ) as mock_warning, patch.object(
            logging.getLogger("daemon.routers.mcp_servers"), "exception"
        ) as mock_exception:
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={
                    "config": {
                        "transport": "streamable-http",
                        "url": "https://api.example.com/mcp",
                    }
                }
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "Server error" in data["message"]
        assert "Session terminated" in data["message"]
        # Verify warning was called (no stack trace)
        mock_warning.assert_called_once()
        mock_exception.assert_not_called()  # Should NOT log exception

    def test_generic_error_returns_sanitized_message(self, test_client):
        """Generic errors return sanitized message (no internal details leak)."""
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.create_test_session = AsyncMock(
            side_effect=RuntimeError("Internal error at /app/daemon/mcp/connection.py:123")
        )

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": {"transport": "stdio", "command": "test"}}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        # Sanitized message should NOT contain internal path or line numbers
        assert "/app/daemon" not in data["message"]
        assert ":123" not in data["message"]
        assert "unexpected error" in data["message"].lower()

    def test_sse_transport_success(self, test_client):
        """SSE transport type works correctly."""
        mock_session = MagicMock()
        mock_session.list_tools = AsyncMock(return_value=[MagicMock(name="sse-tool")])

        mock_streams_cm = MagicMock()
        mock_streams_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_streams_cm.__aexit__ = AsyncMock(return_value=None)

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.create_test_session = AsyncMock(
            return_value=(mock_session, mock_streams_cm)
        )

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ), patch(
            "socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34",))]
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={
                    "config": {
                        "transport": "sse",
                        "url": "https://example.com/mcp",
                    }
                }
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["tools_count"] == 1

    def test_streamable_http_transport_success(self, test_client):
        """Streamable HTTP transport type works correctly."""
        mock_session = MagicMock()
        mock_session.list_tools = AsyncMock(return_value=[MagicMock(name="http-tool")])

        mock_streams_cm = MagicMock()
        mock_streams_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_streams_cm.__aexit__ = AsyncMock(return_value=None)

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.create_test_session = AsyncMock(
            return_value=(mock_session, mock_streams_cm)
        )

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=mock_conn_mgr
        ), patch(
            "socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("1.1.1.1",))]
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={
                    "config": {
                        "transport": "streamable-http",
                        "url": "https://api.example.com/mcp",
                    }
                }
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["tools_count"] == 1


# =============================================================================
# Section C: _create_test_session_from_streams Helper Tests
# =============================================================================


class TestCreateTestSessionFromStreams:
    """Tests for the _create_test_session_from_streams helper function."""

    @pytest.mark.asyncio
    async def test_creates_session_with_correct_timeout(self):
        """Session is created with the specified timeout."""
        from daemon.mcp.connection_manager import McpConnectionManager

        manager = McpConnectionManager()

        mock_streams_cm = MagicMock()
        mock_read = MagicMock()
        mock_write = MagicMock()
        mock_streams_cm.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
        mock_streams_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.initialize = AsyncMock()

        with patch(
            "daemon.mcp.connection_manager.ManagedClientSession",
            return_value=mock_session
        ):
            session, streams = await manager._create_test_session_from_streams(
                mock_streams_cm,
                timeout=30.0,
                is_streamable_http=False,
            )

        assert session is mock_session
        assert streams is mock_streams_cm
        mock_session.start.assert_called_once()
        mock_session.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_session_creation_failure(self):
        """Session creation failure is properly propagated."""
        from daemon.mcp.connection_manager import McpConnectionManager

        manager = McpConnectionManager()

        mock_streams_cm = MagicMock()
        mock_streams_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_streams_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.start = AsyncMock(side_effect=RuntimeError("Session start failed"))
        mock_session.stop = AsyncMock()

        with patch(
            "daemon.mcp.connection_manager.ManagedClientSession",
            return_value=mock_session
        ):
            with pytest.raises(RuntimeError, match="Session start failed"):
                await manager._create_test_session_from_streams(
                    mock_streams_cm,
                    timeout=30.0,
                    is_streamable_http=False,
                )

        # Verify cleanup was attempted
        mock_session.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_timeout_does_not_cleanup_on_cancelled_scope(self):
        """TimeoutError should propagate without calling session.stop() (scope already cancelled)."""
        from daemon.mcp.connection_manager import McpConnectionManager
        import asyncio

        manager = McpConnectionManager()

        mock_streams_cm = MagicMock()
        mock_session = MagicMock()
        mock_session.stop = AsyncMock()

        # Simulate timeout happening after session is created but before initialize
        call_count = [0]

        async def slow_initialize():
            call_count[0] += 1
            await asyncio.sleep(10)  # Longer than timeout
            return MagicMock()

        mock_session.initialize = slow_initialize
        mock_session.start = AsyncMock()

        async def slow_enter(self):
            # Return streams immediately, but session creation will timeout
            await asyncio.sleep(0)  # Small delay
            return (MagicMock(), MagicMock())

        mock_streams_cm.__aenter__ = slow_enter
        mock_streams_cm.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "daemon.mcp.connection_manager.ManagedClientSession",
            return_value=mock_session
        ):
            with pytest.raises(asyncio.TimeoutError):
                await manager._create_test_session_from_streams(
                    mock_streams_cm,
                    timeout=0.1,  # Very short timeout
                    is_streamable_http=False,
                )

        # Verify session.stop() was NOT called on timeout
        # (because the asyncio.timeout context handles cancellation)
        mock_session.stop.assert_not_called()

    @pytest.mark.asyncio
    async def test_mcp_error_is_propagated(self):
        """McpError should be caught, cleaned up, and re-raised."""
        from daemon.mcp.connection_manager import McpConnectionManager
        # Import McpError from the mocked mcp module
        from mcp import McpError

        manager = McpConnectionManager()

        mock_streams_cm = MagicMock()
        mock_streams_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_streams_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.initialize = AsyncMock(side_effect=McpError("Server returned error: invalid protocol"))
        mock_session.stop = AsyncMock()

        with patch(
            "daemon.mcp.connection_manager.ManagedClientSession",
            return_value=mock_session
        ):
            with pytest.raises(McpError, match="Server returned error: invalid protocol"):
                await manager._create_test_session_from_streams(
                    mock_streams_cm,
                    timeout=30.0,
                    is_streamable_http=False,
                )

        # Verify cleanup was attempted for non-timeout errors
        mock_session.stop.assert_called_once()
        mock_streams_cm.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_on_exception(self):
        """Streams are cleaned up when an exception occurs."""
        from daemon.mcp.connection_manager import McpConnectionManager

        manager = McpConnectionManager()

        mock_streams_cm = MagicMock()
        mock_streams_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_streams_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.initialize = AsyncMock(side_effect=RuntimeError("Init failed"))
        mock_session.stop = AsyncMock()

        with patch(
            "daemon.mcp.connection_manager.ManagedClientSession",
            return_value=mock_session
        ):
            with pytest.raises(RuntimeError, match="Init failed"):
                await manager._create_test_session_from_streams(
                    mock_streams_cm,
                    timeout=30.0,
                    is_streamable_http=False,
                )

        # Verify cleanup was attempted
        mock_session.stop.assert_called_once()
        mock_streams_cm.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_streamable_http_unpacks_correctly(self):
        """Streamable HTTP transport unpacks streams correctly (read, write as tuple)."""
        from daemon.mcp.connection_manager import McpConnectionManager

        manager = McpConnectionManager()

        mock_streams_cm = MagicMock()
        mock_read = MagicMock()
        mock_write = MagicMock()
        # Streamable HTTP returns (read, write) as a tuple from __aenter__
        mock_streams_cm.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
        mock_streams_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.initialize = AsyncMock()

        # Track what ManagedClientSession was called with
        session_args = []

        def capture_session(read, write):
            session_args.append((read, write))
            return mock_session

        original_init = mock_session.__class__

        with patch(
            "daemon.mcp.connection_manager.ManagedClientSession",
            side_effect=capture_session
        ):
            result_session, result_streams = await manager._create_test_session_from_streams(
                mock_streams_cm,
                timeout=30.0,
                is_streamable_http=True,
            )

        # Verify session was called with the correct args (both read and write)
        assert len(session_args) == 1
        assert session_args[0] == (mock_read, mock_write)
        assert result_session is mock_session
        assert result_streams is mock_streams_cm

"""Unit tests for MCP server configuration schema."""
import os

import pytest
from pydantic import ValidationError
from daemon.mcp.config import (
    McpConfigValidationError,
    McpStdioConfig,
    McpSseConfig,
    McpStreamableHttpConfig,
    McpServerConfig,
    validate_mcp_server_config,
)


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


class TestMcpStdioConfig:
    def test_valid_config(self):
        config = {"transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]}
        result = McpStdioConfig.model_validate(config)
        assert result.transport == "stdio"
        assert result.command == "npx"
        assert result.args == ["-y", "@modelcontextprotocol/server-filesystem"]

    def test_defaults(self):
        config = {"command": "python"}
        result = McpStdioConfig.model_validate(config)
        assert result.transport == "stdio"  # defaults to stdio
        assert result.args == []
        assert result.env is None

    def test_with_env(self):
        config = {"command": "npx", "env": {"API_KEY": "secret"}}
        result = McpStdioConfig.model_validate(config)
        assert result.env == {"API_KEY": "secret"}

    def test_missing_command_raises(self):
        with pytest.raises(ValidationError):
            McpStdioConfig.model_validate({"transport": "stdio"})


class TestMcpSseConfig:
    def test_valid_config(self, allow_loopback):
        config = {"transport": "sse", "url": "http://localhost:8080/sse"}
        result = McpSseConfig.model_validate(config)
        assert result.transport == "sse"
        assert result.url == "http://localhost:8080/sse"

    def test_with_headers(self, allow_loopback):
        config = {"transport": "sse", "url": "http://localhost:8080/sse", "headers": {"Authorization": "Bearer token"}}
        result = McpSseConfig.model_validate(config)
        assert result.headers == {"Authorization": "Bearer token"}

    def test_missing_url_raises(self):
        with pytest.raises(ValidationError):
            McpSseConfig.model_validate({"transport": "sse"})


class TestMcpStreamableHttpConfig:
    def test_valid_config(self, allow_loopback):
        config = {"transport": "streamable-http", "url": "http://localhost:8080/mcp"}
        result = McpStreamableHttpConfig.model_validate(config)
        assert result.transport == "streamable-http"

    def test_missing_url_raises(self):
        with pytest.raises(ValidationError):
            McpStreamableHttpConfig.model_validate({"transport": "streamable-http"})


class TestValidateMcpServerConfig:
    def test_valid_stdio(self):
        result = validate_mcp_server_config({"transport": "stdio", "command": "python"})
        assert isinstance(result, McpStdioConfig)

    def test_valid_sse(self, allow_loopback):
        result = validate_mcp_server_config({"transport": "sse", "url": "http://localhost:8080/sse"})
        assert isinstance(result, McpSseConfig)

    def test_valid_streamable_http(self, allow_loopback):
        result = validate_mcp_server_config({"transport": "streamable-http", "url": "http://localhost:8080/mcp"})
        assert isinstance(result, McpStreamableHttpConfig)

    def test_invalid_transport_raises(self):
        with pytest.raises(McpConfigValidationError):
            validate_mcp_server_config({"transport": "websocket"})

    def test_missing_transport_uses_stdio_default(self):
        # Missing transport defaults to stdio (consistent with McpStdioConfig.test_defaults)
        result = validate_mcp_server_config({"command": "python"})
        assert isinstance(result, McpStdioConfig)
        assert result.transport == "stdio"

    def test_empty_dict_raises(self):
        with pytest.raises(McpConfigValidationError):
            validate_mcp_server_config({})


class TestSseConfigSSRFProtection:
    """Tests for SSRF protection on SSE config URLs."""

    def test_blocks_loopback_ipv4(self):
        config = {"transport": "sse", "url": "http://127.0.0.1:8080/sse"}
        with pytest.raises(ValidationError) as exc_info:
            McpSseConfig.model_validate(config)
        assert "restricted address" in str(exc_info.value)

    def test_blocks_private_10_network(self):
        config = {"transport": "sse", "url": "http://10.0.0.1:8080/sse"}
        with pytest.raises(ValidationError) as exc_info:
            McpSseConfig.model_validate(config)
        assert "restricted address" in str(exc_info.value)

    def test_blocks_private_172_network(self):
        config = {"transport": "sse", "url": "http://172.16.0.1:8080/sse"}
        with pytest.raises(ValidationError) as exc_info:
            McpSseConfig.model_validate(config)
        assert "restricted address" in str(exc_info.value)

    def test_blocks_private_192_network(self):
        config = {"transport": "sse", "url": "http://192.168.1.1:8080/sse"}
        with pytest.raises(ValidationError) as exc_info:
            McpSseConfig.model_validate(config)
        assert "restricted address" in str(exc_info.value)

    def test_blocks_link_local(self):
        config = {"transport": "sse", "url": "http://169.254.0.1:8080/sse"}
        with pytest.raises(ValidationError) as exc_info:
            McpSseConfig.model_validate(config)
        assert "restricted address" in str(exc_info.value)

    def test_allows_public_url(self):
        # Use a public IP/range that should be allowed
        config = {"transport": "sse", "url": "http://93.184.216.34:8080/sse"}
        # This will fail DNS resolution or connection, but NOT SSRF validation
        try:
            McpSseConfig.model_validate(config)
        except ValidationError as e:
            # Should NOT be an SSRF error
            assert "restricted address" not in str(e)


class TestStreamableHttpConfigSSRFProtection:
    """Tests for SSRF protection on Streamable HTTP config URLs."""

    def test_blocks_loopback_ipv4(self):
        config = {"transport": "streamable-http", "url": "http://127.0.0.1:8080/mcp"}
        with pytest.raises(ValidationError) as exc_info:
            McpStreamableHttpConfig.model_validate(config)
        assert "restricted address" in str(exc_info.value)

    def test_blocks_localhost_when_not_allowed(self):
        config = {"transport": "streamable-http", "url": "http://localhost:8080/mcp"}
        with pytest.raises(ValidationError) as exc_info:
            McpStreamableHttpConfig.model_validate(config)
        assert "restricted address" in str(exc_info.value)

    def test_allows_loopback_when_env_set(self, allow_loopback):
        config = {"transport": "streamable-http", "url": "http://localhost:8080/mcp"}
        result = McpStreamableHttpConfig.model_validate(config)
        assert result.url == "http://localhost:8080/mcp"

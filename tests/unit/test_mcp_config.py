"""Unit tests for MCP server configuration schema."""
import pytest
from pydantic import ValidationError
from daemon.mcp.config import (
    McpStdioConfig,
    McpSseConfig,
    McpStreamableHttpConfig,
    McpServerConfig,
    validate_mcp_server_config,
)


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
    def test_valid_config(self):
        config = {"transport": "sse", "url": "http://localhost:8080/sse"}
        result = McpSseConfig.model_validate(config)
        assert result.transport == "sse"
        assert result.url == "http://localhost:8080/sse"

    def test_with_headers(self):
        config = {"transport": "sse", "url": "http://localhost:8080/sse", "headers": {"Authorization": "Bearer token"}}
        result = McpSseConfig.model_validate(config)
        assert result.headers == {"Authorization": "Bearer token"}

    def test_missing_url_raises(self):
        with pytest.raises(ValidationError):
            McpSseConfig.model_validate({"transport": "sse"})


class TestMcpStreamableHttpConfig:
    def test_valid_config(self):
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

    def test_valid_sse(self):
        result = validate_mcp_server_config({"transport": "sse", "url": "http://localhost:8080/sse"})
        assert isinstance(result, McpSseConfig)

    def test_valid_streamable_http(self):
        result = validate_mcp_server_config({"transport": "streamable-http", "url": "http://localhost:8080/mcp"})
        assert isinstance(result, McpStreamableHttpConfig)

    def test_invalid_transport_raises(self):
        with pytest.raises(ValueError):
            validate_mcp_server_config({"transport": "websocket"})

    def test_missing_transport_raises(self):
        with pytest.raises(ValueError):
            validate_mcp_server_config({"command": "python"})

    def test_empty_dict_raises(self):
        with pytest.raises(ValueError):
            validate_mcp_server_config({})

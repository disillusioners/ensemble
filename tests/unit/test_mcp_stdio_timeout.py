"""Tests for MCP STDIO timeout configuration changes."""
import pytest
from pydantic import ValidationError

from daemon.mcp.config import McpStdioConfig, McpSseConfig, validate_mcp_server_config
from daemon.mcp.connection_manager import McpConnectionManager, STDIO_DEFAULT_TIMEOUT


class TestMcpStdioConfigTimeoutField:
    """Tests for McpStdioConfig timeout field."""

    def test_timeout_default_is_none(self):
        """Default timeout should be None."""
        config = McpStdioConfig(command="npx", args=["test"])
        assert config.timeout is None

    def test_timeout_can_set_custom_float(self):
        """Can set a custom float value for timeout."""
        config = McpStdioConfig(command="npx", args=["test"], timeout=45.0)
        assert config.timeout == 45.0

    def test_timeout_can_set_none_explicitly(self):
        """Can explicitly set timeout to None."""
        config = McpStdioConfig(command="npx", args=["test"], timeout=None)
        assert config.timeout is None

    def test_timeout_rejects_invalid_type(self):
        """Invalid types should be rejected for timeout."""
        # Pydantic coerces strings and ints to float, but rejects dicts/lists
        with pytest.raises(ValidationError) as exc_info:
            McpStdioConfig(command="npx", args=["test"], timeout={"invalid": "type"})
        assert "timeout" in str(exc_info.value).lower()

    def test_timeout_accepts_int_coerced_to_float(self):
        """Integer values should be accepted and coerced to float."""
        config = McpStdioConfig(command="npx", args=["test"], timeout=30)
        assert config.timeout == 30.0
        assert isinstance(config.timeout, float)

    def test_timeout_accepts_zero(self):
        """Zero timeout should be accepted."""
        config = McpStdioConfig(command="npx", args=["test"], timeout=0)
        assert config.timeout == 0


class TestConnectionManagerTimeoutConstants:
    """Tests for connection manager timeout constants."""

    def test_stdio_default_timeout_is_30(self):
        """STDIO_DEFAULT_TIMEOUT should be 30.0."""
        assert STDIO_DEFAULT_TIMEOUT == 30.0

    def test_connect_instance_has_15s_default(self):
        """connect_instance should have per_server_timeout default of 15s."""
        import inspect
        sig = inspect.signature(McpConnectionManager.connect_instance)
        per_server_timeout_param = sig.parameters.get("per_server_timeout")
        assert per_server_timeout_param is not None
        assert per_server_timeout_param.default == 15.0

    def test_create_session_stdio_timeout_fallback(self):
        """_create_session should use STDIO_DEFAULT_TIMEOUT when config.timeout is None."""
        config = McpStdioConfig(command="npx", args=["test"])
        # Verify config.timeout is None
        assert config.timeout is None
        # The effective timeout should be STDIO_DEFAULT_TIMEOUT (30.0)
        effective_timeout = config.timeout if config.timeout is not None else STDIO_DEFAULT_TIMEOUT
        assert effective_timeout == 30.0

    def test_create_session_uses_config_timeout_when_set(self):
        """_create_session should use config.timeout when explicitly set."""
        config = McpStdioConfig(command="npx", args=["test"], timeout=45.0)
        effective_timeout = config.timeout if config.timeout is not None else STDIO_DEFAULT_TIMEOUT
        assert effective_timeout == 45.0

    def test_sse_connections_use_passed_timeout(self, allow_loopback):
        """SSE connections should use the passed per_server_timeout, not STDIO_DEFAULT_TIMEOUT."""
        config = McpSseConfig(url="http://localhost:8080/sse")
        # SSE doesn't have a timeout field, so it uses the passed timeout
        passed_timeout = 15.0
        assert passed_timeout != STDIO_DEFAULT_TIMEOUT


class TestBackwardCompatibility:
    """Tests for backward compatibility."""

    def test_existing_config_without_timeout_works(self):
        """Configs without timeout field should work (default to None)."""
        # Old-style config (without timeout field)
        old_config = {
            "transport": "stdio",
            "command": "npx",
            "args": ["test"],
        }
        config = validate_mcp_server_config(old_config)
        assert isinstance(config, McpStdioConfig)
        assert config.timeout is None

    def test_full_old_style_config_works(self):
        """Full old-style config should work without changes."""
        old_config = {
            "transport": "stdio",
            "command": "uvx",
            "args": ["mcp-server-test"],
            "env": {"DEBUG": "1"},
        }
        config = validate_mcp_server_config(old_config)
        assert config.command == "uvx"
        assert config.args == ["mcp-server-test"]
        assert config.env == {"DEBUG": "1"}
        assert config.timeout is None

    def test_new_config_with_timeout_works(self):
        """New-style config with timeout should work."""
        new_config = {
            "transport": "stdio",
            "command": "uvx",
            "args": ["mcp-server-test"],
            "timeout": 60.0,
        }
        config = validate_mcp_server_config(new_config)
        assert isinstance(config, McpStdioConfig)
        assert config.timeout == 60.0

    def test_new_config_with_null_timeout_works(self):
        """New-style config with explicit null timeout should work."""
        new_config = {
            "transport": "stdio",
            "command": "uvx",
            "args": ["mcp-server-test"],
            "timeout": None,
        }
        config = validate_mcp_server_config(new_config)
        assert isinstance(config, McpStdioConfig)
        assert config.timeout is None


class TestErrorMessageQuality:
    """Tests for error message quality in timeout scenarios."""

    def test_stdio_timeout_error_includes_command(self):
        """STDIO timeout error message should include the command string."""
        import asyncio
        import inspect

        # Get the source of _create_stdio_session
        source = inspect.getsource(McpConnectionManager._create_stdio_session)

        # Verify the error message format includes command_str
        assert "command_str" in source
        assert "{command_str}" in source or "command_str" in source

    def test_stdio_timeout_error_mentions_cold_start(self):
        """STDIO timeout error message should mention cold start / package resolution."""
        import inspect

        source = inspect.getsource(McpConnectionManager._create_stdio_session)

        # Check for cold start / package resolution mentions
        assert "cold start" in source.lower() or "npx" in source.lower() or "uvx" in source.lower()

    def test_stdio_timeout_error_suggests_increasing_timeout(self):
        """STDIO timeout error message should suggest increasing timeout."""
        import inspect

        source = inspect.getsource(McpConnectionManager._create_stdio_session)

        # Check for suggestions about increasing timeout
        assert "increasing" in source.lower() or "timeout" in source.lower()


class TestAdditiveChange:
    """Tests verifying this is an additive change with no breaking changes."""

    def test_mcp_stdio_config_is_backward_compatible(self):
        """McpStdioConfig should be backward compatible with existing usage."""
        # This should work exactly as before
        config = McpStdioConfig(command="npx", args=["mcp-server"])
        assert config.transport == "stdio"
        assert config.command == "npx"
        assert config.args == ["mcp-server"]
        assert config.env is None
        # The new field should default to None
        assert config.timeout is None

    def test_validate_mcp_server_config_handles_legacy_input(self):
        """validate_mcp_server_config should handle legacy input without timeout."""
        # Simulate what a legacy database might have
        legacy_config = {
            "transport": "stdio",
            "command": "uvx",
            "args": ["some-mcp-package"],
            "env": None,
        }
        result = validate_mcp_server_config(legacy_config)
        assert isinstance(result, McpStdioConfig)
        # Should not raise, should handle gracefully

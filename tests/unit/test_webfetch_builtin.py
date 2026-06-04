"""Tests for WebFetch built-in MCP server definition.

This module tests the WebFetchServerDefinition class including:
- Schema definition (get_config_schema)
- Config building (build_config)
- Config parsing (parse_config)
- Integration with registry
"""

import pytest

from daemon.mcp.builtin_servers.webfetch import WebFetchServerDefinition
from daemon.mcp.builtin_servers import get_registry


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def clean_registry():
    """Reset MCP server registry to its import-time state before each test.

    The BuiltinServerRegistry is a module-level singleton. Without this
    fixture, mutations from other test files (e.g., unregister calls) would
    leak into these tests.
    """
    from daemon.mcp.builtin_servers import _registry
    from daemon.mcp.builtin_servers.context7 import Context7ServerDefinition

    _registry._definitions.clear()
    _registry.register(WebFetchServerDefinition())
    _registry.register(Context7ServerDefinition())
    yield


@pytest.fixture
def webfetch_definition():
    """Create a WebFetchServerDefinition instance."""
    return WebFetchServerDefinition()


@pytest.fixture
def registry():
    """Get the global registry."""
    return get_registry()


# =============================================================================
# Test Properties
# =============================================================================


class TestWebFetchProperties:
    """Tests for WebFetchServerDefinition property values."""

    def test_name(self, webfetch_definition):
        """Test that name is 'webfetch'."""
        assert webfetch_definition.name == "webfetch"

    def test_display_name(self, webfetch_definition):
        """Test that display_name is 'WebFetch'."""
        assert webfetch_definition.display_name == "WebFetch"

    def test_description(self, webfetch_definition):
        """Test that description is set correctly."""
        assert "Fetch and read web page content" in webfetch_definition.description
        assert "agents" in webfetch_definition.description

    def test_schema_version(self, webfetch_definition):
        """Test that schema_version is '2'."""
        assert webfetch_definition.schema_version == "2"


# =============================================================================
# Test Base Config
# =============================================================================


class TestWebFetchBaseConfig:
    """Tests for get_base_config()."""

    def test_base_config_transport(self, webfetch_definition):
        """Test that base config includes stdio transport."""
        base_config = webfetch_definition.get_base_config()
        assert base_config.get("transport") == "stdio"

    def test_base_config_command(self, webfetch_definition):
        """Test that base config includes uvx command."""
        base_config = webfetch_definition.get_base_config()
        assert base_config.get("command") == "uvx"

    def test_base_config_args(self, webfetch_definition):
        """Test that base config includes mcp-server-fetch args."""
        base_config = webfetch_definition.get_base_config()
        assert base_config.get("args") == ["mcp-server-fetch"]


# =============================================================================
# Test Schema
# =============================================================================


class TestWebFetchSchema:
    """Tests for get_config_schema()."""

    def test_schema_returns_three_fields(self, webfetch_definition):
        """Test that schema returns exactly 3 fields."""
        schema = webfetch_definition.get_config_schema()
        assert len(schema) == 3

    def test_schema_user_agent_field(self, webfetch_definition):
        """Test user_agent field configuration."""
        schema = webfetch_definition.get_config_schema()
        user_agent = next(f for f in schema if f["key"] == "user_agent")

        assert user_agent["key"] == "user_agent"
        assert user_agent["label"] == "User Agent"
        assert user_agent["type"] == "text"
        assert user_agent["section"] == "args"
        assert user_agent["arg_format"] == "key_value"
        assert user_agent["default"] == "Mozilla/5.0 (compatible; MCP-WebFetch/1.0)"
        assert user_agent["required"] is False
        assert "User-Agent" in user_agent["description"]

    def test_schema_ignore_robots_txt_field(self, webfetch_definition):
        """Test ignore_robots_txt field configuration."""
        schema = webfetch_definition.get_config_schema()
        ignore_robots = next(f for f in schema if f["key"] == "ignore_robots_txt")

        assert ignore_robots["key"] == "ignore_robots_txt"
        assert ignore_robots["label"] == "Ignore robots.txt"
        assert ignore_robots["type"] == "boolean"
        assert ignore_robots["section"] == "args"
        assert ignore_robots["arg_format"] == "flag"
        assert ignore_robots["default"] is False
        assert ignore_robots["required"] is False

    def test_schema_proxy_url_field(self, webfetch_definition):
        """Test proxy_url field configuration."""
        schema = webfetch_definition.get_config_schema()
        proxy_url = next(f for f in schema if f["key"] == "proxy_url")

        assert proxy_url["key"] == "proxy_url"
        assert proxy_url["label"] == "Proxy URL"
        assert proxy_url["type"] == "text"
        assert proxy_url["section"] == "args"
        assert proxy_url["arg_format"] == "key_value"
        assert proxy_url["default"] is None
        assert proxy_url["required"] is False


# =============================================================================
# Test build_config
# =============================================================================


class TestWebFetchBuildConfig:
    """Tests for build_config() method."""

    def test_build_config_includes_base_config(self, webfetch_definition):
        """Test that build_config includes base config."""
        result = webfetch_definition.build_config({})

        assert result.get("transport") == "stdio"
        assert result.get("command") == "uvx"
        assert "mcp-server-fetch" in result.get("args", [])

    def test_build_config_default_user_agent(self, webfetch_definition):
        """Test that default user_agent is included when no values provided."""
        result = webfetch_definition.build_config({})

        args = result.get("args", [])
        assert "--user-agent" in args
        ua_idx = args.index("--user-agent")
        assert args[ua_idx + 1] == "Mozilla/5.0 (compatible; MCP-WebFetch/1.0)"

    def test_build_config_default_ignores_robots_txt(self, webfetch_definition):
        """Test that ignore_robots_txt (False) is omitted when using defaults."""
        result = webfetch_definition.build_config({})

        args = result.get("args", [])
        # When default is False, the flag should be omitted entirely
        assert "--ignore-robots-txt" not in args
        assert "--no-ignore-robots-txt" not in args

    def test_build_config_ignore_robots_txt_true(self, webfetch_definition):
        """Test that --ignore-robots-txt flag is emitted when True."""
        result = webfetch_definition.build_config({"ignore_robots_txt": True})

        args = result.get("args", [])
        assert "--ignore-robots-txt" in args
        assert "--no-ignore-robots-txt" not in args

    def test_build_config_ignore_robots_txt_false(self, webfetch_definition):
        """Test that --no-ignore-robots-txt is NOT emitted when False."""
        result = webfetch_definition.build_config({"ignore_robots_txt": False})

        args = result.get("args", [])
        # When False, the flag should be omitted entirely
        assert "--ignore-robots-txt" not in args
        assert "--no-ignore-robots-txt" not in args

    def test_build_config_custom_user_agent(self, webfetch_definition):
        """Test that custom user_agent value is used."""
        result = webfetch_definition.build_config({"user_agent": "CustomBot/1.0"})

        args = result.get("args", [])
        assert "--user-agent" in args
        ua_idx = args.index("--user-agent")
        assert args[ua_idx + 1] == "CustomBot/1.0"

    def test_build_config_proxy_url_none_omitted(self, webfetch_definition):
        """Test that proxy_url is omitted when None."""
        result = webfetch_definition.build_config({"proxy_url": None})

        args = result.get("args", [])
        assert "--proxy-url" not in args

    def test_build_config_proxy_url_set(self, webfetch_definition):
        """Test that proxy_url value is included when set."""
        result = webfetch_definition.build_config({"proxy_url": "http://proxy:8080"})

        args = result.get("args", [])
        assert "--proxy-url" in args
        proxy_idx = args.index("--proxy-url")
        assert args[proxy_idx + 1] == "http://proxy:8080"

    def test_build_config_proxy_url_https(self, webfetch_definition):
        """Test that proxy_url with https:// is accepted."""
        result = webfetch_definition.build_config({"proxy_url": "https://secure-proxy:8080"})

        args = result.get("args", [])
        assert "--proxy-url" in args
        proxy_idx = args.index("--proxy-url")
        assert args[proxy_idx + 1] == "https://secure-proxy:8080"

    def test_build_config_proxy_url_invalid_scheme(self, webfetch_definition):
        """Test that proxy_url with invalid scheme raises error."""
        with pytest.raises(ValueError, match="http:// or https://"):
            webfetch_definition.build_config({"proxy_url": "ftp://proxy:8080"})

    def test_build_config_proxy_url_no_scheme(self, webfetch_definition):
        """Test that proxy_url without scheme raises error."""
        with pytest.raises(ValueError, match="http:// or https://"):
            webfetch_definition.build_config({"proxy_url": "proxy:8080"})

    def test_build_config_all_fields(self, webfetch_definition):
        """Test building config with all fields provided."""
        result = webfetch_definition.build_config({
            "user_agent": "MyBot/2.0",
            "ignore_robots_txt": True,
            "proxy_url": "http://proxy:8080",
        })

        args = result.get("args", [])
        assert "mcp-server-fetch" in args
        assert "--user-agent" in args
        assert "MyBot/2.0" in args
        assert "--ignore-robots-txt" in args
        assert "--proxy-url" in args
        assert "http://proxy:8080" in args

    def test_build_config_args_order(self, webfetch_definition):
        """Test that args are in correct order: base + user_agent + flag + proxy."""
        result = webfetch_definition.build_config({
            "user_agent": "TestBot",
            "ignore_robots_txt": True,
            "proxy_url": "http://proxy:8080",
        })

        args = result.get("args", [])
        # Should be: mcp-server-fetch, --user-agent, value, --ignore-robots-txt, --proxy-url, value
        expected_order = [
            "mcp-server-fetch",
            "--user-agent",
            "TestBot",
            "--ignore-robots-txt",
            "--proxy-url",
            "http://proxy:8080",
        ]
        assert args == expected_order


# =============================================================================
# Test parse_config
# =============================================================================


class TestWebFetchParseConfig:
    """Tests for parse_config() method."""

    def test_parse_config_roundtrip(self, webfetch_definition):
        """Test that build_config output can be parsed back."""
        original_values = {
            "user_agent": "RoundtripBot",
            "ignore_robots_txt": True,
        }
        built_config = webfetch_definition.build_config(original_values)
        parsed = webfetch_definition.parse_config(built_config)

        assert parsed["user_agent"] == "RoundtripBot"
        assert parsed["ignore_robots_txt"] is True

    def test_parse_config_default_values(self, webfetch_definition):
        """Test parsing default config."""
        built_config = webfetch_definition.build_config({})
        parsed = webfetch_definition.parse_config(built_config)

        # Default user_agent should be parsed
        assert parsed["user_agent"] == "Mozilla/5.0 (compatible; MCP-WebFetch/1.0)"
        # Default ignore_robots_txt is False - should NOT be in parsed (flag omitted)
        assert "ignore_robots_txt" not in parsed

    def test_parse_config_with_proxy(self, webfetch_definition):
        """Test parsing config with proxy_url."""
        original_values = {"proxy_url": "http://proxy:8080"}
        built_config = webfetch_definition.build_config(original_values)
        parsed = webfetch_definition.parse_config(built_config)

        assert parsed["proxy_url"] == "http://proxy:8080"

    def test_parse_config_skips_base_args(self, webfetch_definition):
        """Test that parse_config correctly skips base args."""
        # Build config with user values
        built_config = webfetch_definition.build_config({
            "user_agent": "TestAgent",
            "ignore_robots_txt": True,
        })

        # Verify base args are at the start
        args = built_config.get("args", [])
        assert args[0] == "mcp-server-fetch"

        # Parse should work correctly
        parsed = webfetch_definition.parse_config(built_config)
        assert parsed["user_agent"] == "TestAgent"
        assert parsed["ignore_robots_txt"] is True

    def test_parse_config_all_fields(self, webfetch_definition):
        """Test parsing full config with all fields."""
        original_values = {
            "user_agent": "FullBot",
            "ignore_robots_txt": True,
            "proxy_url": "http://full:8080",
        }
        built_config = webfetch_definition.build_config(original_values)
        parsed = webfetch_definition.parse_config(built_config)

        assert parsed["user_agent"] == "FullBot"
        assert parsed["ignore_robots_txt"] is True
        assert parsed["proxy_url"] == "http://full:8080"

    def test_parse_config_false_flag_recovered(self, webfetch_definition):
        """Test that False flag value results in omission (not recovered, defaults apply)."""
        built_config = webfetch_definition.build_config({"ignore_robots_txt": False})
        parsed = webfetch_definition.parse_config(built_config)

        # False flag is omitted, not present in parsed
        assert "ignore_robots_txt" not in parsed


# =============================================================================
# Test Registry Integration
# =============================================================================


class TestWebFetchRegistryIntegration:
    """Tests for WebFetch registration in global registry."""

    def test_webfetch_registered_in_registry(self, registry):
        """Test that WebFetch is registered in the global registry."""
        webfetch = registry.get_by_name("webfetch")
        assert webfetch is not None
        assert isinstance(webfetch, WebFetchServerDefinition)

    def test_registry_contains_webfetch(self, registry):
        """Test that webfetch is in registry definitions."""
        assert "webfetch" in registry.definitions

    def test_registry_get_all_includes_webfetch(self, registry):
        """Test that get_all() includes WebFetch."""
        all_defs = registry.get_all()
        webfetch_names = [d.name for d in all_defs]
        assert "webfetch" in webfetch_names


# =============================================================================
# Test End-to-End Integration
# =============================================================================


class TestWebFetchEndToEnd:
    """End-to-end integration tests for WebFetch."""

    def test_full_config_generation(self, webfetch_definition):
        """Test complete config generation workflow."""
        # User provides custom values
        user_values = {
            "user_agent": "AgentBot/1.0",
            "ignore_robots_txt": True,
            "proxy_url": "http://agent-proxy:3128",
        }

        # Build config
        config = webfetch_definition.build_config(user_values)

        # Verify structure
        assert config["transport"] == "stdio"
        assert config["command"] == "uvx"
        assert "mcp-server-fetch" in config["args"]

        # Verify user values are in args
        args = config["args"]
        assert "--user-agent" in args
        assert "AgentBot/1.0" in args
        assert "--ignore-robots-txt" in args
        assert "--proxy-url" in args
        assert "http://agent-proxy:3128" in args

        # Parse back
        parsed = webfetch_definition.parse_config(config)

        # Verify values match
        assert parsed["user_agent"] == "AgentBot/1.0"
        assert parsed["ignore_robots_txt"] is True
        assert parsed["proxy_url"] == "http://agent-proxy:3128"

    def test_default_only_config(self, webfetch_definition):
        """Test config generation with only defaults."""
        config = webfetch_definition.build_config({})

        # Should include base config and default user_agent
        assert config["transport"] == "stdio"
        assert config["command"] == "uvx"
        assert "mcp-server-fetch" in config["args"]
        assert "--user-agent" in config["args"]

        # Should include default user_agent value
        args = config["args"]
        assert "Mozilla/5.0 (compatible; MCP-WebFetch/1.0)" in args

        # Default ignore_robots_txt is False, so flag should be OMITTED
        assert "--ignore-robots-txt" not in args
        assert "--no-ignore-robots-txt" not in args

        # Default proxy_url is None, so --proxy-url should NOT be included
        assert "--proxy-url" not in args


# =============================================================================
# Test Bootstrap Integration
# =============================================================================


class TestWebFetchBootstrapIntegration:
    """Integration tests for WebFetch bootstrap with InstanceManager."""

    @pytest.fixture
    def bootstrap_engine(self):
        """Create in-memory SQLite engine for bootstrap tests."""
        from sqlalchemy import create_engine
        from sqlmodel import SQLModel

        engine = create_engine(
            "sqlite:///:memory:",
            echo=False,
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(engine)
        yield engine
        engine.dispose()

    @pytest.fixture
    def bootstrap_repo(self, bootstrap_engine):
        """Create MCP server repository for bootstrap tests."""
        from daemon.repositories.mcp_server.repository import SQLModelMcpServerRepository

        return SQLModelMcpServerRepository(bootstrap_engine)

    @pytest.fixture
    def mock_config(self):
        """Create mock Config for InstanceManager."""
        from daemon.config import (
            Config, LLMConfig, DaemonConfig, LimitsConfig, PersistenceConfig,
            QueueConfig, CompactionConfig, ServicesConfig, JobSystemConfig, AgentsConfig,
            McpPoolConfig
        )
        from unittest.mock import MagicMock

        config = MagicMock(spec=Config)
        config.llm = MagicMock(spec=LLMConfig)
        config.llm.base_url = "https://api.openai.com/v1"
        config.llm.api_key = "test-key"
        config.llm.model = "gpt-4"
        config.llm.model_vision = None
        config.llm.temperature = 0.7
        config.llm.request_timeout = 60

        config.daemon = MagicMock(spec=DaemonConfig)
        config.daemon.host = "0.0.0.0"
        config.daemon.port = 8079

        config.limits = MagicMock(spec=LimitsConfig)
        config.limits.max_instances = 100
        config.limits.max_children_per_instance = 10
        config.limits.instance_timeout_minutes = 60
        config.limits.message_rate_limit = 60
        config.limits.graph_recursion_limit = 100
        config.limits.llm_concurrency = 10

        config.persistence = MagicMock(spec=PersistenceConfig)
        config.persistence.db_path = ":memory:"

        config.queue = MagicMock(spec=QueueConfig)
        config.queue.discard_on_startup = None
        config.queue.llm_retry_transient_attempts = 10
        config.queue.llm_retry_timeout_attempts = 3

        config.compaction = MagicMock(spec=CompactionConfig)
        config.compaction.enabled = False

        config.services = MagicMock(spec=ServicesConfig)
        config.services.worker_poll_interval = 0.5
        config.services.stale_task_recovery_interval = 60
        config.services.task_timeout_minutes = 60
        config.services.max_task_retries = 3
        config.services.task_retry_backoff_base = 60
        config.services.task_retry_backoff_max = 3600
        config.services.stale_task_cancel_grace_seconds = 10
        config.services.graph_timeout_minutes = 55

        config.agents = MagicMock(spec=AgentsConfig)
        config.agents.directory = "./agents"

        config.mcp_pool = MagicMock(spec=McpPoolConfig)
        config.mcp_pool.enabled = True
        config.mcp_pool.default_pool_size = 1
        config.mcp_pool.servers = {}
        config.mcp_pool.health_check_interval = 60
        config.mcp_pool.health_check_timeout = 5

        config.job_system = MagicMock(spec=JobSystemConfig)
        config.job_system.default_max_retries = 3
        config.job_system.retry_backoff_base_seconds = 60
        config.job_system.retry_backoff_max_seconds = 3600
        config.job_system.retry_backoff_multiplier = 2.0
        config.job_system.dlq_enabled = True
        config.job_system.event_dispatch_enabled = True
        config.job_system.observer_health_check_interval_seconds = 300
        config.job_system.idempotency_key_ttl_hours = 24
        config.job_system.job_retry_scheduler_enabled = None

        return config

    @pytest.fixture
    def instance_manager_with_repo(self, bootstrap_engine, bootstrap_repo, mock_config):
        """Create InstanceManager with in-memory DB and test repository."""
        from unittest.mock import patch, MagicMock
        from asyncio import Future

        # Patch database engine creation to use our in-memory engine
        with patch("daemon.manager.create_engine_from_config") as mock_create_engine, \
             patch("daemon.manager.get_checkpointer") as mock_checkpointer, \
             patch("daemon.migrations.runner.MigrationRunner") as mock_migration:

            mock_create_engine.return_value = bootstrap_engine
            async_mock = MagicMock()
            async_mock.return_value = None
            mock_checkpointer.return_value = async_mock

            # Create mock migration runner
            mock_runner_instance = MagicMock()
            mock_runner_instance.run_pending_migrations.return_value = []
            mock_migration.return_value = mock_runner_instance

            # Import here to avoid circular dependencies
            from daemon.manager import InstanceManager

            # Create manager
            manager = InstanceManager(mock_config)

            # Override the MCP server repository with our test repo
            manager._mcp_server_repository = bootstrap_repo

            yield manager

            # Cleanup
            if hasattr(manager, "_shutting_down"):
                manager._shutting_down = True

    def test_bootstrap_creates_webfetch_server(self, instance_manager_with_repo):
        """Test that bootstrap creates webfetch server in DB with is_builtin=True."""
        manager = instance_manager_with_repo

        # Call bootstrap
        manager._bootstrap_builtin_servers()

        # Verify server was created
        server = manager._mcp_server_repository.get_mcp_server_by_name("webfetch")
        assert server is not None, "WebFetch server should be created by bootstrap"
        assert server.is_builtin is True, "Server should be marked as builtin"

    def test_schema_drift_removes_stale_flag(self, instance_manager_with_repo):
        """Test that schema drift resets stale config with obsolete flags.

        When schema_version changes from 1 to 2, bootstrap should update
        the config to the new defaults (no --no-ignore-robots-txt flag).
        """
        manager = instance_manager_with_repo
        repo = manager._mcp_server_repository

        # Delete any existing webfetch server first (bootstrap may have created one)
        existing = repo.get_mcp_server_by_name("webfetch")
        if existing:
            repo.delete_mcp_server(existing.id)

        # Create a server entry with old config that includes --no-ignore-robots-txt
        # and schema_version="1" (old schema that used the negative flag)
        old_config = {
            "transport": "stdio",
            "command": "uvx",
            "args": ["mcp-server-fetch", "--no-ignore-robots-txt"],
        }
        repo.create_mcp_server(
            name="webfetch",
            description="WebFetch MCP server",
            config=old_config,
            is_builtin=True,
            config_schema=[],
            config_schema_version="1",
        )

        # Verify the old config is stored
        server = repo.get_mcp_server_by_name("webfetch")
        assert "--no-ignore-robots-txt" in server.config.get("args", [])
        assert server.config_schema_version == "1"

        # Run bootstrap - should detect schema drift and reset config
        manager._bootstrap_builtin_servers()

        # Verify config was refreshed - stale flag should be removed
        updated_server = repo.get_mcp_server_by_name("webfetch")
        args = updated_server.config.get("args", [])
        assert "--no-ignore-robots-txt" not in args, \
            "Stale --no-ignore-robots-txt flag should be removed"
        assert "--ignore-robots-txt" not in args, \
            "Default False value should result in no flag"
        assert updated_server.config_schema_version == "2", \
            "Schema version should be updated to 2"

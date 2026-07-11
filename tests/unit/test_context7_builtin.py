"""Tests for Context7 built-in MCP server definition.

This module tests the Context7ServerDefinition class including:
- Schema definition (get_config_schema returns empty list)
- Config building (build_config returns base config only)
- Config parsing (parse_config returns empty dict)
- Integration with registry
- Bootstrap integration
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from daemon.mcp.builtin_servers.context7 import Context7ServerDefinition
from daemon.mcp.builtin_servers import get_registry


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def context7_definition():
    """Create a Context7ServerDefinition instance."""
    return Context7ServerDefinition()


@pytest.fixture
def registry():
    """Get the global registry."""
    return get_registry()


# =============================================================================
# Test Properties
# =============================================================================


class TestContext7Properties:
    """Tests for Context7ServerDefinition property values."""

    def test_name(self, context7_definition):
        """Test that name is 'context7'."""
        assert context7_definition.name == "context7"

    def test_display_name(self, context7_definition):
        """Test that display_name is 'Context7'."""
        assert context7_definition.display_name == "Context7"

    def test_description(self, context7_definition):
        """Test that description is set correctly."""
        assert "library documentation" in context7_definition.description
        assert "AI coding assistants" in context7_definition.description

    def test_schema_version(self, context7_definition):
        """Test that schema_version is '2'."""
        assert context7_definition.schema_version == "2"


# =============================================================================
# Test Base Config
# =============================================================================


class TestContext7BaseConfig:
    """Tests for get_base_config()."""

    def test_base_config_complete(self, context7_definition):
        """Test that base config includes all required fields."""
        base_config = context7_definition.get_base_config()

        assert base_config.get("transport") == "stdio"
        assert base_config.get("command") == "npx"
        assert base_config.get("args") == ["-y", "@upstash/context7-mcp"]

    def test_base_config_transport(self, context7_definition):
        """Test that base config includes stdio transport."""
        base_config = context7_definition.get_base_config()
        assert base_config.get("transport") == "stdio"

    def test_base_config_command(self, context7_definition):
        """Test that base config includes npx command."""
        base_config = context7_definition.get_base_config()
        assert base_config.get("command") == "npx"

    def test_base_config_args(self, context7_definition):
        """Test that base config includes correct args."""
        base_config = context7_definition.get_base_config()
        assert base_config.get("args") == ["-y", "@upstash/context7-mcp"]


# =============================================================================
# Test Schema
# =============================================================================


class TestContext7ConfigSchema:
    """Tests for get_config_schema()."""

    def test_schema_returns_empty_list(self, context7_definition):
        """Test that schema returns empty list (no config fields)."""
        schema = context7_definition.get_config_schema()
        assert schema == []

    def test_schema_length(self, context7_definition):
        """Test that schema has zero fields."""
        schema = context7_definition.get_config_schema()
        assert len(schema) == 0


# =============================================================================
# Test build_config
# =============================================================================


class TestContext7BuildConfig:
    """Tests for build_config() method."""

    def test_build_config_with_empty_config(self, context7_definition):
        """Test that build_config with empty config returns base config only."""
        result = context7_definition.build_config({})

        assert result.get("transport") == "stdio"
        assert result.get("command") == "npx"
        assert result.get("args") == ["-y", "@upstash/context7-mcp"]

    def test_build_config_no_extra_fields(self, context7_definition):
        """Test that build_config doesn't add extra fields."""
        result = context7_definition.build_config({})

        # Should only have base config fields
        assert set(result.keys()) == {"transport", "command", "args"}

    def test_build_config_with_arbitrary_values_ignored(self, context7_definition):
        """Test that arbitrary values passed to build_config are ignored (no schema fields)."""
        result = context7_definition.build_config({
            "some_field": "value",
            "another_field": 123,
        })

        # Should still be just the base config
        assert result == context7_definition.get_base_config()


# =============================================================================
# Test parse_config
# =============================================================================


class TestContext7ParseConfig:
    """Tests for parse_config() method."""

    def test_parse_config_returns_empty_dict(self, context7_definition):
        """Test that parse_config returns empty dict (no config fields to parse)."""
        stored_config = {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@upstash/context7-mcp"],
        }
        result = context7_definition.parse_config(stored_config)

        assert result == {}

    def test_parse_config_with_any_config(self, context7_definition):
        """Test that parse_config ignores any config fields (none defined)."""
        stored_config = {
            "args": ["-y", "@upstash/context7-mcp", "--some-arg", "value"],
            "env": {"SOME_VAR": "value"},
        }
        result = context7_definition.parse_config(stored_config)

        # Should be empty since there are no schema fields
        assert result == {}


# =============================================================================
# Test Registry Integration
# =============================================================================


class TestContext7Registry:
    """Tests for Context7 registration in global registry."""

    def test_context7_registered_in_registry(self, registry):
        """Test that Context7 is registered in the global registry."""
        context7 = registry.get_by_name("context7")
        assert context7 is not None
        assert isinstance(context7, Context7ServerDefinition)

    def test_registry_contains_context7(self, registry):
        """Test that context7 is in registry definitions."""
        assert "context7" in registry.definitions

    def test_registry_get_all_includes_context7(self, registry):
        """Test that get_all() includes Context7."""
        all_defs = registry.get_all()
        context7_names = [d.name for d in all_defs]
        assert "context7" in context7_names


# =============================================================================
# Test Bootstrap Integration
# =============================================================================


class TestContext7Bootstrap:
    """Tests for Context7 bootstrap integration."""

    @pytest.fixture
    def bootstrap_engine(self):
        """Create in-memory SQLite engine for bootstrap tests."""
        from sqlmodel import SQLModel
        from sqlalchemy import create_engine

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
        from daemon.repositories.mcp_server import SQLModelMcpServerRepository

        return SQLModelMcpServerRepository(bootstrap_engine)

    @pytest.fixture
    def mock_config(self):
        """Create mock Config for InstanceManager."""
        from daemon.config import (
            Config,
            LLMConfig,
            DaemonConfig,
            LimitsConfig,
            PersistenceConfig,
            QueueConfig,
            CompactionConfig,
            ServicesConfig,
            JobSystemConfig,
            AgentsConfig,
        )

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

        config.mcp_pool = MagicMock()
        config.mcp_pool.enabled = False
        config.mcp_pool.default_pool_size = 1
        config.mcp_pool.servers = {}
        config.mcp_pool.health_check_interval = 60
        config.mcp_pool.health_check_timeout = 5
        config.mcp_pool.tool_call_timeout = 120

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

        # Skill Evolution: disabled in this fixture (manager reads config.skill_evolution)
        config.skill_evolution = None

        return config

    @pytest.fixture
    def instance_manager_with_repo(self, bootstrap_engine, bootstrap_repo, mock_config):
        """Create InstanceManager with in-memory DB and test repository."""
        from unittest.mock import patch

        # Patch database engine creation to use our in-memory engine
        with patch("daemon.manager.create_engine_from_config") as mock_create_engine, \
             patch("daemon.manager.get_checkpointer") as mock_checkpointer, \
             patch("daemon.migrations.runner.MigrationRunner") as mock_migration:

            mock_create_engine.return_value = bootstrap_engine
            mock_checkpointer.return_value = AsyncMock()

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

    def test_bootstrap_creates_context7_server(self, instance_manager_with_repo):
        """Test that bootstrap creates context7 server in DB with is_builtin=True."""
        manager = instance_manager_with_repo

        # Call bootstrap
        manager._bootstrap_builtin_servers()

        # Verify server was created
        server = manager._mcp_server_repository.get_mcp_server_by_name("context7")
        assert server is not None, "Context7 server should be created by bootstrap"
        assert server.is_builtin is True, "Server should be marked as builtin"
        assert server.config_schema is not None, "Server should have config schema"

    def test_bootstrap_context7_schema_empty(self, instance_manager_with_repo):
        """Test that bootstrap creates context7 with empty schema."""
        manager = instance_manager_with_repo

        # Call bootstrap
        manager._bootstrap_builtin_servers()

        # Verify schema is empty
        server = manager._mcp_server_repository.get_mcp_server_by_name("context7")
        assert server.config_schema == []

    def test_bootstrap_context7_config_matches_base(self, instance_manager_with_repo):
        """Test that bootstrap creates context7 with correct base config."""
        manager = instance_manager_with_repo

        # Call bootstrap
        manager._bootstrap_builtin_servers()

        # Verify config matches base config
        server = manager._mcp_server_repository.get_mcp_server_by_name("context7")
        assert server.config.get("transport") == "stdio"
        assert server.config.get("command") == "npx"
        assert server.config.get("args") == ["-y", "@upstash/context7-mcp"]

    def test_schema_drift_refreshes_config(self, instance_manager_with_repo):
        """Test that schema drift resets stale config to defaults.

        When schema_version changes, bootstrap should update the config
        to the new defaults rather than preserving stale values.
        """
        manager = instance_manager_with_repo
        repo = manager._mcp_server_repository

        # Delete any existing context7 server first (bootstrap may have created one)
        existing = repo.get_mcp_server_by_name("context7")
        if existing:
            repo.delete_mcp_server(existing.id)

        # Create a server entry with old package name and schema_version="1"
        old_config = {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@upstreamapi/context7-mcp"],
        }
        repo.create_mcp_server(
            name="context7",
            description="Context7 MCP server",
            config=old_config,
            is_builtin=True,
            config_schema=[],
            config_schema_version="1",
        )

        # Verify the old config is stored
        server = repo.get_mcp_server_by_name("context7")
        assert server.config.get("args") == ["-y", "@upstreamapi/context7-mcp"]
        assert server.config_schema_version == "1"

        # Run bootstrap - should detect schema drift and reset config
        manager._bootstrap_builtin_servers()

        # Verify config was refreshed with new package name
        updated_server = repo.get_mcp_server_by_name("context7")
        assert updated_server.config.get("args") == ["-y", "@upstash/context7-mcp"], \
            "Package name should be updated to @upstash/context7-mcp"
        assert updated_server.config_schema_version == "2", \
            "Schema version should be updated to 2"


# =============================================================================
# Test npx Unavailable
# =============================================================================


class TestContext7NpxUnavailable:
    """Tests for behavior when npx is not available."""

    @pytest.mark.asyncio
    async def test_connection_error_logged_when_npx_unavailable(self, caplog):
        """Test that error is logged when npx is not available.

        When the stdio_client raises FileNotFoundError (npx not found),
        the error should be logged for diagnostics.
        """
        import logging

        from daemon.mcp.builtin_servers.context7 import Context7ServerDefinition
        from daemon.mcp.connection_manager import McpConnectionManager

        context7 = Context7ServerDefinition()
        config = context7.get_base_config()

        # Create a mock server
        mock_server = MagicMock()
        mock_server.config = config
        mock_server.name = "context7"

        # Create manager
        manager = McpConnectionManager()
        manager._active_sessions = {}

        # Mock the _create_session method to simulate npx not found
        with patch.object(manager, "_create_session") as mock_create:
            # Simulate the error that occurs when npx command is not found
            mock_create.side_effect = FileNotFoundError(
                "[Errno 2] No such file or directory: 'npx'"
            )

            with caplog.at_level(logging.ERROR):
                # connect_instance catches errors and logs them
                await manager.connect_instance("test-instance", [mock_server])

            # Verify error was logged
            assert any("npx" in record.message.lower() or "not found" in record.message.lower()
                      for record in caplog.records if record.levelname == "ERROR")

    @pytest.mark.asyncio
    async def test_os_error_logged(self, caplog):
        """Test that OSError from missing npx is logged."""
        import logging

        from daemon.mcp.builtin_servers.context7 import Context7ServerDefinition
        from daemon.mcp.connection_manager import McpConnectionManager

        context7 = Context7ServerDefinition()
        config = context7.get_base_config()

        # Create a mock server
        mock_server = MagicMock()
        mock_server.config = config
        mock_server.name = "context7"

        # Create manager
        manager = McpConnectionManager()
        manager._active_sessions = {}

        # Mock to raise OSError (what happens when the subprocess fails to start)
        with patch.object(manager, "_create_session") as mock_create:
            mock_create.side_effect = OSError("npx: command not found")

            with caplog.at_level(logging.ERROR):
                await manager.connect_instance("test-instance", [mock_server])

            # Verify error was logged
            assert any("npx" in record.message.lower() or "command not found" in record.message.lower()
                      for record in caplog.records if record.levelname == "ERROR")

    @pytest.mark.asyncio
    async def test_stdio_client_file_not_found_error(self):
        """Test that FileNotFoundError from stdio_client is raised.

        This directly tests the _create_stdio_session method's behavior
        when mcp.stdio_client raises FileNotFoundError.
        """
        from daemon.mcp.builtin_servers.context7 import Context7ServerDefinition
        from daemon.mcp.connection_manager import McpConnectionManager
        from daemon.mcp.config import McpStdioConfig

        context7 = Context7ServerDefinition()
        config = context7.get_base_config()

        # Create a stdio config from the base config
        stdio_config = McpStdioConfig(
            command=config["command"],
            args=config["args"],
            env=config.get("env", {}),
        )

        # Create manager
        manager = McpConnectionManager()
        manager._active_sessions = {}
        manager._stream_contexts = {}

        # Mock mcp.stdio_client to raise FileNotFoundError
        with patch("daemon.mcp.stdio_wrapper.mcp.stdio_client") as mock_stdio:
            mock_stdio.side_effect = FileNotFoundError(
                "npx not found"
            )

            # Attempting to create stdio session should raise
            with pytest.raises(FileNotFoundError):
                await manager._create_stdio_session(
                    stdio_config,
                    "test-instance",
                    "context7",
                    timeout=5.0,
                )

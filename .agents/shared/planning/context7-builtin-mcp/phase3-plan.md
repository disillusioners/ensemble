# Phase 3: Testing

## Objective
Add comprehensive unit tests for the Context7 built-in server definition, following the same test patterns as `test_webfetch_builtin.py` and `test_builtin_mcp_servers.py`. Verify that existing tests still pass.

## Coupling
- **Depends on**: Phase 2 (Context7 registered in registry)
- **Coupling type**: tight — tests import and exercise the registered definition
- **Shared files with other phases**: `daemon/mcp/builtin_servers/context7.py`, `daemon/mcp/builtin_servers/__init__.py`
- **Why this coupling**: Tests verify the complete integration from definition through registration

## Context
- Test framework: **pytest** with **pytest-asyncio**
- Test location: `tests/unit/`
- Reference tests: `tests/unit/test_webfetch_builtin.py` (432 lines, 4 test classes)
- Reference tests: `tests/unit/test_builtin_mcp_servers.py` (1373 lines, `TestBootstrap` class at line 990)
- In-memory SQLite for DB tests: `create_engine("sqlite:///:memory:")`
- Registry fixture pattern: register test definitions, unregister in teardown
- **Existing registry tests use name-based assertions (`assert "webfetch" in ...`), NOT size-based (`len() == N`). Verified — no existing assertions break from adding Context7.**

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `test_context7_builtin.py` | New test file in `tests/unit/` | `tests/unit/test_context7_builtin.py` |
| 2 | Test `Context7ServerDefinition` properties | Verify `name`, `display_name`, `description`, `schema_version` | `tests/unit/test_context7_builtin.py` |
| 3 | Test base config | Verify stdio transport, npx command, correct args | `tests/unit/test_context7_builtin.py` |
| 4 | Test empty config schema | Verify `get_config_schema()` returns `[]` | `tests/unit/test_context7_builtin.py` |
| 5 | Test `build_config({})` | Verify it returns the correct base config with no extra fields | `tests/unit/test_context7_builtin.py` |
| 6 | Test `parse_config()` roundtrip | Verify `parse_config(base_config)` returns `{}` — ABC contract, no configurable fields | `tests/unit/test_context7_builtin.py` |
| 7 | Test registry integration | Verify Context7 is in the global registry | `tests/unit/test_context7_builtin.py` |
| 8 | Test bootstrap integration | Verify `_bootstrap_builtin_servers()` creates Context7 DB entry, idempotency, `is_builtin=True` | `tests/unit/test_context7_builtin.py` |
| 9 | Test npx unavailability | Mock `mcp.stdio_client` to raise `FileNotFoundError`; verify server entry still created, connection fails gracefully | `tests/unit/test_context7_builtin.py` |
| 10 | Verify existing tests pass | Run full test suite to confirm no regressions | All test files |

## Key Files
- `tests/unit/test_context7_builtin.py` — **NEW** — Context7 tests
- `tests/unit/test_webfetch_builtin.py` — Reference for property/config/registry test patterns
- `tests/unit/test_builtin_mcp_servers.py` — Reference for `TestBootstrap` pattern (line 990)
- `tests/conftest.py` — Shared fixtures

## Test Structure Reference

Follow the class-based pattern from `test_webfetch_builtin.py` and `test_builtin_mcp_servers.py`:

```python
# tests/unit/test_context7_builtin.py
"""Tests for Context7 built-in MCP server definition.

This module tests the Context7ServerDefinition class including:
- Schema definition (get_config_schema) — empty
- Config building (build_config)
- Config parsing (parse_config) roundtrip
- Integration with registry
- Bootstrap DB creation
- npx unavailability graceful degradation
"""

import asyncio
import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch
from sqlmodel import SQLModel, create_engine, Session as SQLModelSession

from daemon.mcp.builtin_servers.context7 import Context7ServerDefinition
from daemon.mcp.builtin_servers import get_registry, BuiltinServerRegistry
from daemon.repositories.mcp_server import McpServer, SQLModelMcpServerRepository


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


@pytest.fixture
def bootstrap_engine():
    """Create in-memory SQLite engine for bootstrap tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def bootstrap_repo(bootstrap_engine):
    """Create MCP server repository for bootstrap tests."""
    return SQLModelMcpServerRepository(bootstrap_engine)


@pytest.fixture
def mock_config():
    """Create mock Config for InstanceManager."""
    from daemon.config import (
        Config, LLMConfig, DaemonConfig, LimitsConfig,
        PersistenceConfig, QueueConfig, CompactionConfig,
        ServicesConfig, JobSystemConfig, AgentsConfig,
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
    config.persistence.checkpointer_db_path = ":memory:"

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
def instance_manager_with_repo(bootstrap_engine, bootstrap_repo, mock_config):
    """Create InstanceManager with in-memory DB and test repository."""
    with patch("daemon.manager.create_engine_from_config") as mock_create_engine, \
         patch("daemon.manager.get_checkpointer") as mock_checkpointer, \
         patch("daemon.migrations.runner.MigrationRunner") as mock_migration:

        mock_create_engine.return_value = bootstrap_engine
        mock_checkpointer.return_value = AsyncMock()

        mock_runner_instance = MagicMock()
        mock_runner_instance.run_pending_migrations.return_value = []
        mock_migration.return_value = mock_runner_instance

        from daemon.manager import InstanceManager
        manager = InstanceManager(mock_config)
        manager._mcp_server_repository = bootstrap_repo
        yield manager
        if hasattr(manager, '_shutting_down'):
            manager._shutting_down = True


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
        """Test that description mentions documentation."""
        desc = context7_definition.description.lower()
        assert "documentation" in desc or "docs" in desc

    def test_schema_version(self, context7_definition):
        """Test that schema_version is '1'."""
        assert context7_definition.schema_version == "1"


# =============================================================================
# Test Base Config
# =============================================================================


class TestContext7BaseConfig:
    """Tests for get_base_config()."""

    def test_base_config_transport(self, context7_definition):
        """Test that base config includes stdio transport."""
        assert context7_definition.get_base_config()["transport"] == "stdio"

    def test_base_config_command(self, context7_definition):
        """Test that base config includes npx command."""
        assert context7_definition.get_base_config()["command"] == "npx"

    def test_base_config_args(self, context7_definition):
        """Test that base config includes correct npx args."""
        assert context7_definition.get_base_config()["args"] == ["-y", "@upstreamapi/context7-mcp"]


# =============================================================================
# Test Schema (empty)
# =============================================================================


class TestContext7Schema:
    """Tests for get_config_schema() — returns empty list."""

    def test_empty_schema(self, context7_definition):
        """Test that Context7 has no configurable fields."""
        assert context7_definition.get_config_schema() == []


# =============================================================================
# Test build_config
# =============================================================================


class TestContext7BuildConfig:
    """Tests for build_config()."""

    def test_default_config_returns_base(self, context7_definition):
        """Test that build_config({}) returns the base config exactly."""
        config = context7_definition.build_config({})
        assert config["transport"] == "stdio"
        assert config["command"] == "npx"
        assert config["args"] == ["-y", "@upstreamapi/context7-mcp"]

    def test_no_env_vars(self, context7_definition):
        """Test that default config has no env vars."""
        config = context7_definition.build_config({})
        assert "env" not in config or config.get("env") == {}


# =============================================================================
# Test parse_config roundtrip (W1 — ABC contract)
# =============================================================================


class TestContext7ParseConfig:
    """Tests for parse_config() roundtrip — part of ABC contract."""

    def test_parse_config_returns_empty(self, context7_definition):
        """parse_config on base config should return {} — no user fields."""
        config = context7_definition.build_config({})
        parsed = context7_definition.parse_config(config)
        assert parsed == {}

    def test_parse_config_with_extra_keys(self, context7_definition):
        """parse_config should ignore unknown keys in stored config."""
        config = {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@upstreamapi/context7-mcp"],
            "env": {"SOME_VAR": "value"},
        }
        parsed = context7_definition.parse_config(config)
        # Should still be empty — no schema fields to parse
        assert parsed == {}


# =============================================================================
# Test Registry Integration
# =============================================================================


class TestContext7Registry:
    """Tests for Context7 registration in global registry."""

    def test_registered_in_global_registry(self, registry):
        """Test that Context7 is in the global registry."""
        assert registry.get_by_name("context7") is not None

    def test_registry_returns_context7_instance(self, registry):
        """Test that get_by_name returns Context7ServerDefinition."""
        defn = registry.get_by_name("context7")
        assert isinstance(defn, Context7ServerDefinition)

    def test_registry_includes_both_builtins(self, registry):
        """Test that registry includes both webfetch and context7."""
        names = {d.name for d in registry.get_all()}
        assert "webfetch" in names
        assert "context7" in names


# =============================================================================
# Test Bootstrap Integration (C2 — follows TestBootstrap pattern)
# =============================================================================


class TestContext7Bootstrap:
    """Tests for _bootstrap_builtin_servers() with Context7.

    Follows the pattern from test_builtin_mcp_servers.py TestBootstrap (line 990).
    """

    def test_bootstrap_creates_context7_server(self, instance_manager_with_repo):
        """Test that bootstrap creates Context7 server in DB with is_builtin=True."""
        manager = instance_manager_with_repo

        # Bootstrap is called in InstanceManager.__init__, but call again
        # to ensure our repo override is used
        manager._bootstrap_builtin_servers()

        server = manager._mcp_server_repository.get_mcp_server_by_name("context7")
        assert server is not None, "Context7 server should be created by bootstrap"
        assert server.is_builtin is True, "Context7 should be marked as builtin"
        assert server.config["transport"] == "stdio"
        assert server.config["command"] == "npx"
        assert server.config["args"] == ["-y", "@upstreamapi/context7-mcp"]

    def test_bootstrap_idempotent(self, instance_manager_with_repo):
        """Test that running bootstrap twice doesn't create duplicate Context7."""
        manager = instance_manager_with_repo

        manager._bootstrap_builtin_servers()
        manager._bootstrap_builtin_servers()

        servers = manager._mcp_server_repository.list_mcp_servers()
        context7_servers = [s for s in servers if s.name == "context7"]
        assert len(context7_servers) == 1, "Should have exactly one Context7 server after two bootstrap calls"

    def test_bootstrap_creates_with_empty_schema(self, instance_manager_with_repo):
        """Test that bootstrap stores empty config schema."""
        manager = instance_manager_with_repo
        manager._bootstrap_builtin_servers()

        server = manager._mcp_server_repository.get_mcp_server_by_name("context7")
        assert server.config_schema == []
        assert server.config_schema_version == "1"


# =============================================================================
# Test npx Unavailability (C1 — correct mock target)
# =============================================================================


class TestContext7NpxUnavailable:
    """Tests for graceful degradation when npx is not available.

    The actual failure path: when an instance tries to connect, 
    McpConnectionManager._create_stdio_session() calls 
    mcp.stdio_client(server_params).__aenter__() which spawns a subprocess.
    If 'npx' is not on PATH, this raises FileNotFoundError from the OS.

    Mock at mcp.stdio_client level, NOT shutil.which (which is never called).
    """

    @pytest.mark.asyncio
    async def test_connection_failure_file_not_found(self):
        """Test that FileNotFoundError from stdio_client is handled gracefully.

        Simulates: npx not on PATH → subprocess.Popen raises FileNotFoundError
        → streams_cm.__aenter__() propagates it
        → connect_instance catches it via asyncio.gather(return_exceptions=True)
        """
        from mcp import StdioServerParameters
        from daemon.mcp.connection_manager import McpConnectionManager
        from daemon.repositories.mcp_server.models import McpServer

        manager = McpConnectionManager()

        # Create a mock server with Context7's config
        server = McpServer(
            id="test-id",
            name="context7",
            config={
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@upstreamapi/context7-mcp"],
            },
            is_active=True,
            is_builtin=True,
            created_at=datetime.now().isoformat(),
        )

        # Mock mcp.stdio_client to raise FileNotFoundError
        with patch("daemon.mcp.connection_manager.mcp.stdio_client") as mock_stdio:
            # Create an async context manager that raises FileNotFoundError on enter
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(side_effect=FileNotFoundError(
                "[Errno 2] No such file or directory: 'npx'"
            ))
            mock_cm.__aexit__ = AsyncMock(return_value=None)
            mock_stdio.return_value = mock_cm

            # Should NOT raise — connect_instance uses return_exceptions=True
            await manager.connect_instance("test-instance", [server])

            # Verify no connections were stored for this server
            session = manager.get_session("test-instance", "context7")
            assert session is None, "No session should be stored when npx is unavailable"

    @pytest.mark.asyncio
    async def test_other_servers_still_connect_on_npx_failure(self):
        """Test that other MCP servers still connect when Context7's npx fails.

        Simulates: npx not available → Context7 fails → another server succeeds.
        """
        from daemon.mcp.connection_manager import McpConnectionManager
        from daemon.repositories.mcp_server.models import McpServer

        manager = McpConnectionManager()

        # Context7 server (will fail)
        context7_server = McpServer(
            id="ctx7-id",
            name="context7",
            config={
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@upstreamapi/context7-mcp"],
            },
            is_active=True,
            is_builtin=True,
            created_at=datetime.now().isoformat(),
        )

        # Another server that succeeds (mock SSE)
        other_server = McpServer(
            id="other-id",
            name="other-server",
            config={
                "transport": "sse",
                "url": "http://localhost:3000/sse",
            },
            is_active=True,
            is_builtin=False,
            created_at=datetime.now().isoformat(),
        )

        # Mock Context7's stdio to fail
        with patch("daemon.mcp.connection_manager.mcp.stdio_client") as mock_stdio, \
             patch("daemon.mcp.connection_manager.sse_client") as mock_sse:

            # stdio_client raises FileNotFoundError
            mock_stdio_cm = AsyncMock()
            mock_stdio_cm.__aenter__ = AsyncMock(side_effect=FileNotFoundError("npx not found"))
            mock_stdio_cm.__aexit__ = AsyncMock(return_value=None)
            mock_stdio.return_value = mock_stdio_cm

            # sse_client succeeds
            mock_read = AsyncMock()
            mock_write = AsyncMock()
            mock_sse_cm = AsyncMock()
            mock_sse_cm.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
            mock_sse_cm.__aexit__ = AsyncMock(return_value=None)
            mock_sse.return_value = mock_sse_cm

            # Mock ClientSession.initialize
            with patch("daemon.mcp.connection_manager.ClientSession") as MockSession:
                mock_session = AsyncMock()
                mock_session.initialize = AsyncMock()
                MockSession.return_value = mock_session

                await manager.connect_instance("test-instance", [context7_server, other_server])

                # Context7 should NOT be connected
                assert manager.get_session("test-instance", "context7") is None

                # Other server SHOULD be connected
                assert manager.get_session("test-instance", "other-server") is mock_session
```

## Constraints
- Follow existing test patterns and naming conventions
- Use in-memory SQLite for any DB-related tests
- Clean up registry state in fixtures if modifying the registry
- All existing tests must pass — no regressions
- **Do NOT use `shutil.which()` mocking** — it's never called. Mock at `mcp.stdio_client` level
- **Bootstrap tests must use `InstanceManager` with patched engine** (pattern from `test_builtin_mcp_servers.py:TestBootstrap`)
- Existing registry tests use name-based assertions — verified safe (no `len() == N` assertions)

## Deliverables
- [ ] `tests/unit/test_context7_builtin.py` exists with comprehensive tests
- [ ] `TestContext7Properties` — 4 property tests
- [ ] `TestContext7BaseConfig` — 3 base config tests
- [ ] `TestContext7Schema` — 1 empty schema test
- [ ] `TestContext7BuildConfig` — 2 config building tests
- [ ] `TestContext7ParseConfig` — 2 roundtrip tests (W1)
- [ ] `TestContext7Registry` — 3 registry integration tests
- [ ] `TestContext7Bootstrap` — 3 bootstrap tests (C2)
- [ ] `TestContext7NpxUnavailable` — 2 npx failure tests with correct mock target (C1)
- [ ] All tests pass: `pytest tests/unit/test_context7_builtin.py -v`
- [ ] Full suite passes: `pytest tests/ -v`

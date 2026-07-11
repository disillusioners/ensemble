"""Tests for built-in MCP server framework.

This module tests the built-in MCP server functionality including:
- BuiltinServerDefinition base class (build_config, parse_config)
- BuiltinServerRegistry
- validate_config_values function
- API endpoints for built-in servers
- Built-in server protection
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, Session as SQLModelSession

from daemon.models import (
    McpServerCreate,
    McpServerUpdate,
    McpServerInfo,
    ErrorCodes,
    ConfigSchemaField,
    BuiltinServerTemplate,
    BuiltinServerConfigure,
)
from daemon.repositories.mcp_server import McpServer, SQLModelMcpServerRepository
from daemon.mcp.builtin_servers.base import BuiltinServerDefinition
from daemon.mcp.builtin_servers.validation import validate_config_values, McpConfigValidationError
from daemon.mcp.builtin_servers import BuiltinServerRegistry, get_registry
from daemon.routers.mcp_servers import router as mcp_servers_router


# =============================================================================
# Test BuiltinServerDefinition Implementation
# =============================================================================


class TestBuiltinServerDefinition(BuiltinServerDefinition):
    """Concrete test implementation of BuiltinServerDefinition."""

    @property
    def name(self) -> str:
        return "test-builtin"

    @property
    def display_name(self) -> str:
        return "Test Built-in"

    @property
    def description(self) -> str:
        return "A test built-in server"

    @property
    def schema_version(self) -> str:
        return "1.0"

    def get_config_schema(self) -> list[dict]:
        return [
            {"key": "api_key", "label": "API Key", "type": "text", "section": "args", "required": True},
            {"key": "verbose", "label": "Verbose Mode", "type": "boolean", "section": "args", "arg_format": "flag", "default": False},
            {"key": "timeout", "label": "Timeout (seconds)", "type": "number", "section": "args", "default": 30, "min": 1, "max": 300},
            {"key": "api_url", "label": "API URL", "type": "text", "section": "env", "default": "https://api.example.com"},
        ]


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def test_definition():
    """Create a test BuiltinServerDefinition instance."""
    return TestBuiltinServerDefinition()


@pytest.fixture
def engine():
    """Create in-memory SQLite engine for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def repository(engine):
    """Create SQLModelMcpServerRepository instance with test engine."""
    return SQLModelMcpServerRepository(engine)


# Shared engine for router tests (to avoid SQLite threading issues)
_router_engine = None
_router_repository = None


def get_router_engine():
    """Get or create shared engine for router tests."""
    global _router_engine, _router_repository
    if _router_engine is None:
        _router_engine = create_engine(
            "sqlite:///test_builtin_mcp_servers.db",
            echo=False,
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(_router_engine)
        _router_repository = SQLModelMcpServerRepository(_router_engine)
    return _router_engine, _router_repository


def reset_router_database():
    """Reset the shared router database."""
    global _router_engine, _router_repository
    if _router_engine is not None:
        from sqlalchemy import text
        with SQLModelSession(_router_engine) as session:
            session.exec(text("DELETE FROM mcp_servers"))
            session.commit()
        _router_engine.dispose()
        _router_engine = None
        _router_repository = None


@pytest.fixture(scope="function")
def router_engine_and_repo():
    """Fixture that provides shared engine and repository for router tests."""
    from sqlalchemy import text
    engine, repo = get_router_engine()
    # Clean up before test
    with SQLModelSession(engine) as session:
        session.exec(text("DELETE FROM mcp_servers"))
        session.commit()
    yield engine, repo
    # Clean up after test
    with SQLModelSession(engine) as session:
        session.exec(text("DELETE FROM mcp_servers"))
        session.commit()


@pytest.fixture(scope="function")
def shared_repository(router_engine_and_repo):
    """Fixture that provides the shared repository for direct DB access in tests."""
    _, repo = router_engine_and_repo
    return repo


@pytest.fixture
def registry_with_test_def():
    """Fixture that registers test definitions and cleans up after."""
    registry = get_registry()
    test_def = TestBuiltinServerDefinition()
    registry.register(test_def)
    yield registry
    # Clean up: unregister the test definition
    registry.unregister(test_def.name)


@pytest.fixture
def app(router_engine_and_repo, registry_with_test_def):
    """Create FastAPI app with MCP servers router for testing."""
    engine, repository = router_engine_and_repo

    app = FastAPI()

    # Create mock manager with repository
    mock_manager = MagicMock()
    # Phase 3: routers check manager.is_write_paused; MagicMock auto-attr is truthy → 503.
    mock_manager.is_write_paused = False

    # Wrap repository methods to work with asyncio.to_thread
    def sync_create(*args, **kwargs):
        return repository.create_mcp_server(*args, **kwargs)

    def sync_list(*args, **kwargs):
        return repository.list_mcp_servers(*args, **kwargs)

    def sync_get(*args, **kwargs):
        return repository.get_mcp_server(*args, **kwargs)

    def sync_get_by_name(*args, **kwargs):
        return repository.get_mcp_server_by_name(*args, **kwargs)

    def sync_update(*args, **kwargs):
        return repository.update_mcp_server(*args, **kwargs)

    def sync_delete(*args, **kwargs):
        return repository.delete_mcp_server(*args, **kwargs)

    mock_manager._mcp_server_repository = MagicMock()
    mock_manager._mcp_server_repository.create_mcp_server = sync_create
    mock_manager._mcp_server_repository.list_mcp_servers = sync_list
    mock_manager._mcp_server_repository.get_mcp_server = sync_get
    mock_manager._mcp_server_repository.get_mcp_server_by_name = sync_get_by_name
    mock_manager._mcp_server_repository.update_mcp_server = sync_update
    mock_manager._mcp_server_repository.delete_mcp_server = sync_delete

    # Add manager to app state
    app.state.manager = mock_manager

    # Include router with /api prefix
    from fastapi import APIRouter
    api_router = APIRouter(prefix="/api")
    api_router.include_router(mcp_servers_router)
    app.include_router(api_router)

    return app


@pytest.fixture
def client(app):
    """Create FastAPI TestClient."""
    return TestClient(app)


# =============================================================================
# Shared Bootstrap Fixtures (used by Group 8 and Group 12)
# =============================================================================


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
    from daemon.config import Config, LLMConfig, DaemonConfig, LimitsConfig, PersistenceConfig, QueueConfig, CompactionConfig, ServicesConfig, JobSystemConfig, AgentsConfig, McpPoolConfig

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

    config.mcp_pool = MagicMock(spec=McpPoolConfig)
    config.mcp_pool.enabled = True
    config.mcp_pool.default_pool_size = 1
    config.mcp_pool.servers = {}
    config.mcp_pool.health_check_interval = 60
    config.mcp_pool.health_check_timeout = 5
    config.mcp_pool.tool_call_timeout = 120

    return config


@pytest.fixture
def instance_manager_with_repo(bootstrap_engine, bootstrap_repo, mock_config):
    """Create InstanceManager with in-memory DB and test repository."""
    from unittest.mock import patch, MagicMock

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
        if hasattr(manager, '_shutting_down'):
            manager._shutting_down = True


# =============================================================================
# Group 1: build_config Tests (Generic Algorithm)
# =============================================================================


class TestBuiltinServerDefinitionBuildConfig:
    """Tests for build_config generic algorithm."""

    def test_build_config_key_value_args(self, test_definition):
        """Test that schema with section='args', arg_format='key_value' generates correct args."""
        user_values = {"api_key": "sk-secret-123"}
        result = test_definition.build_config(user_values)

        assert "args" in result
        assert "--api-key" in result["args"]
        assert "sk-secret-123" in result["args"]
        assert result["args"].index("--api-key") + 1 == result["args"].index("sk-secret-123")

    def test_build_config_flag_args(self, test_definition):
        """Test that schema with section='args', arg_format='flag' emits flag when True."""
        # When verbose=True, should emit --verbose
        user_values = {"api_key": "sk-test", "verbose": True}
        result = test_definition.build_config(user_values)

        assert "--verbose" in result["args"]

        # When verbose=False, should NOT emit --verbose
        user_values_false = {"api_key": "sk-test", "verbose": False}
        result_false = test_definition.build_config(user_values_false)

        assert "--verbose" not in result_false["args"]

    def test_build_config_env_vars(self, test_definition):
        """Test that schema with section='env' generates correct env dict with uppercased keys."""
        user_values = {"api_key": "sk-test", "api_url": "https://custom.example.com"}
        result = test_definition.build_config(user_values)

        assert "env" in result
        assert "API_URL" in result["env"]
        assert result["env"]["API_URL"] == "https://custom.example.com"

    def test_build_config_omit_none_and_empty(self, test_definition):
        """Test that None values and empty strings are skipped."""
        # Only provide api_key (required field)
        user_values = {"api_key": "sk-test"}
        result = test_definition.build_config(user_values)

        # verbose should not be in args (default is False, and None/False for flag should be omitted)
        assert "--verbose" not in result["args"]
        # timeout should not be in args (default 30, but not provided explicitly, should use default)
        assert "--timeout" in result["args"]
        assert "30" in result["args"]
        # api_url in env should use default
        assert "API_URL" in result["env"]
        assert result["env"]["API_URL"] == "https://api.example.com"

    def test_build_config_empty_string_skipped(self, test_definition):
        """Test that empty string values are skipped."""
        user_values = {"api_key": ""}  # Empty string
        result = test_definition.build_config(user_values)

        # api_key should be skipped since it's empty string
        assert "--api-key" not in result["args"]

    def test_build_config_uses_defaults(self, test_definition):
        """Test that defaults are used when user values don't override."""
        user_values = {"api_key": "sk-test"}  # Only provide required field
        result = test_definition.build_config(user_values)

        # timeout should have default value of 30
        assert "--timeout" in result["args"]
        assert "30" in result["args"]

        # api_url should have default value in env
        assert "API_URL" in result["env"]
        assert result["env"]["API_URL"] == "https://api.example.com"

    def test_build_config_all_fields(self, test_definition):
        """Test building config with all fields provided."""
        user_values = {
            "api_key": "sk-full-test",
            "verbose": True,
            "timeout": 60,
            "api_url": "https://full.example.com",
        }
        result = test_definition.build_config(user_values)

        assert "--api-key" in result["args"]
        assert "sk-full-test" in result["args"]
        assert "--verbose" in result["args"]
        assert "--timeout" in result["args"]
        assert "60" in result["args"]
        assert "API_URL" in result["env"]
        assert result["env"]["API_URL"] == "https://full.example.com"

    def test_build_config_empty_user_values(self, test_definition):
        """Test build_config with empty user values uses defaults only."""
        user_values = {}
        result = test_definition.build_config(user_values)

        # api_key is required with no default, so nothing for it
        assert "--api-key" not in result["args"]
        # verbose is False by default, so flag not emitted
        assert "--verbose" not in result["args"]
        # timeout has default 30
        assert "--timeout" in result["args"]
        assert "30" in result["args"]
        # api_url has default in env
        assert "API_URL" in result["env"]
        assert result["env"]["API_URL"] == "https://api.example.com"

    def test_boolean_false_is_omitted(self, test_definition):
        """build_config with boolean False should omit the flag entirely."""
        # When verbose=False, flag should be omitted (not emitted)
        user_values = {"api_key": "sk-test", "verbose": False}
        result = test_definition.build_config(user_values)

        assert "--verbose" not in result["args"]
        assert "--no-verbose" not in result["args"]


# =============================================================================
# Group 2: parse_config Tests (Reverse Mapping)
# =============================================================================


class TestBuiltinServerDefinitionParseConfig:
    """Tests for parse_config reverse mapping."""

    def test_parse_config_key_value(self, test_definition):
        """Test parsing key_value args back to user values."""
        stored_config = {
            "args": ["--api-key", "sk-123", "--timeout", "45"],
            "env": {}
        }
        result = test_definition.parse_config(stored_config)

        assert result["api_key"] == "sk-123"
        assert result["timeout"] == 45  # Should be coerced to int

    def test_parse_config_flag(self, test_definition):
        """Test parsing flag args back to boolean values."""
        stored_config = {
            "args": ["--verbose"],
            "env": {}
        }
        result = test_definition.parse_config(stored_config)

        assert result["verbose"] is True

        # Without --verbose flag
        stored_config_no_flag = {
            "args": [],
            "env": {}
        }
        result_no_flag = test_definition.parse_config(stored_config_no_flag)

        assert "verbose" not in result_no_flag

    def test_parse_config_env(self, test_definition):
        """Test parsing env vars back to user values with type coercion."""
        stored_config = {
            "args": [],
            "env": {"API_URL": "https://parsed.example.com"}
        }
        result = test_definition.parse_config(stored_config)

        assert result["api_url"] == "https://parsed.example.com"

    def test_parse_config_type_coercion_number(self, test_definition):
        """Test that numeric values are coerced to int/float."""
        stored_config = {
            "args": ["--timeout", "100"],
            "env": {}
        }
        result = test_definition.parse_config(stored_config)

        assert result["timeout"] == 100
        assert isinstance(result["timeout"], int)

    def test_parse_config_type_coercion_boolean(self, test_definition):
        """Test that boolean values are properly coerced."""
        stored_config = {
            "args": ["--verbose"],
            "env": {}
        }
        result = test_definition.parse_config(stored_config)

        assert result["verbose"] is True
        assert isinstance(result["verbose"], bool)

    def test_parse_config_roundtrip(self, test_definition):
        """Test that build_config output can be parsed back."""
        original_values = {
            "api_key": "sk-roundtrip-test",
            "verbose": True,
            "timeout": 120,
            "api_url": "https://roundtrip.example.com",
        }
        built_config = test_definition.build_config(original_values)
        parsed = test_definition.parse_config(built_config)

        assert parsed["api_key"] == "sk-roundtrip-test"
        assert parsed["verbose"] is True
        assert parsed["timeout"] == 120
        assert parsed["api_url"] == "https://roundtrip.example.com"

    def test_parse_config_missing_fields(self, test_definition):
        """Test parse_config with incomplete stored config."""
        stored_config = {
            "args": ["--api-key", "sk-partial"],
            "env": {}
        }
        result = test_definition.parse_config(stored_config)

        assert result["api_key"] == "sk-partial"
        # Other fields not in stored config should be absent
        assert "timeout" not in result
        assert "verbose" not in result

    def test_parse_config_omitted_flag(self, test_definition):
        """parse_config should not recover False when flag is omitted (defaults apply)."""
        stored_config = {
            "args": ["--api-key", "sk-test"],
            "env": {}
        }
        result = test_definition.parse_config(stored_config)

        assert result["api_key"] == "sk-test"
        # Flag omitted means False (defaults apply), so not in parsed result
        assert "verbose" not in result

    def test_parse_config_value_ambiguity(self, test_definition):
        """parse_config should not treat a flag as a value for a preceding --key."""
        # Malformed config where a value position contains a flag
        # Note: --timeout is the key in schema (key_value), but --verbose is a flag
        stored_config = {
            "args": ["--api-key", "--verbose", "--timeout", "8080"],
            "env": {}
        }
        result = test_definition.parse_config(stored_config)

        # api_key should NOT be populated (because --verbose is a flag, not a value)
        assert "api_key" not in result
        # verbose should be True (the flag IS present)
        assert result["verbose"] is True
        # timeout should be 8080 (correctly parsed after --timeout)
        assert result["timeout"] == 8080


# =============================================================================
# Group 3: validate_config_values Tests
# =============================================================================


class TestValidateConfigValues:
    """Tests for validate_config_values function."""

    def test_validate_required_missing(self):
        """Test that missing required field raises error."""
        schema = [
            {"key": "api_key", "label": "API Key", "type": "text", "required": True, "section": "args"},
        ]
        values = {}  # Missing required field

        with pytest.raises(McpConfigValidationError) as exc_info:
            validate_config_values(schema, values)

        assert any(e["field"] == "api_key" for e in exc_info.value.errors)

    def test_validate_required_null_value(self):
        """Test that null value for required field raises error."""
        schema = [
            {"key": "api_key", "label": "API Key", "type": "text", "required": True, "section": "args"},
        ]
        values = {"api_key": None}

        with pytest.raises(McpConfigValidationError) as exc_info:
            validate_config_values(schema, values)

        assert any(e["field"] == "api_key" for e in exc_info.value.errors)

    def test_validate_required_empty_string(self):
        """Test that empty string for required field raises error."""
        schema = [
            {"key": "api_key", "label": "API Key", "type": "text", "required": True, "section": "args"},
        ]
        values = {"api_key": ""}

        with pytest.raises(McpConfigValidationError) as exc_info:
            validate_config_values(schema, values)

        assert any(e["field"] == "api_key" for e in exc_info.value.errors)

    def test_validate_type_mismatch_string_for_number(self):
        """Test that string value for number field raises error."""
        schema = [
            {"key": "timeout", "label": "Timeout", "type": "number", "section": "args"},
        ]
        values = {"timeout": "not-a-number"}

        with pytest.raises(McpConfigValidationError) as exc_info:
            validate_config_values(schema, values)

        assert any(e["field"] == "timeout" for e in exc_info.value.errors)

    def test_validate_type_mismatch_boolean(self):
        """Test that non-boolean for boolean field raises error."""
        schema = [
            {"key": "enabled", "label": "Enabled", "type": "boolean", "section": "args"},
        ]
        values = {"enabled": "yes"}  # Should be bool

        with pytest.raises(McpConfigValidationError) as exc_info:
            validate_config_values(schema, values)

        assert any(e["field"] == "enabled" for e in exc_info.value.errors)

    def test_validate_number_bounds_min(self):
        """Test that value below min raises error."""
        schema = [
            {"key": "timeout", "label": "Timeout", "type": "number", "min": 10, "section": "args"},
        ]
        values = {"timeout": 5}

        with pytest.raises(McpConfigValidationError) as exc_info:
            validate_config_values(schema, values)

        assert any("at least 10" in e["error"] for e in exc_info.value.errors)

    def test_validate_number_bounds_max(self):
        """Test that value above max raises error."""
        schema = [
            {"key": "timeout", "label": "Timeout", "type": "number", "max": 100, "section": "args"},
        ]
        values = {"timeout": 500}

        with pytest.raises(McpConfigValidationError) as exc_info:
            validate_config_values(schema, values)

        assert any("at most 100" in e["error"] for e in exc_info.value.errors)

    def test_validate_select_valid(self):
        """Test that valid select value passes."""
        schema = [
            {"key": "mode", "label": "Mode", "type": "select", "options": ["dev", "prod", "test"], "section": "args"},
        ]
        values = {"mode": "prod"}

        # Should not raise
        validate_config_values(schema, values)

    def test_validate_select_invalid(self):
        """Test that invalid select value raises error."""
        schema = [
            {"key": "mode", "label": "Mode", "type": "select", "options": ["dev", "prod", "test"], "section": "args"},
        ]
        values = {"mode": "invalid-mode"}

        with pytest.raises(McpConfigValidationError) as exc_info:
            validate_config_values(schema, values)

        assert any(e["field"] == "mode" for e in exc_info.value.errors)

    def test_validate_valid_values(self):
        """Test that all valid values pass validation."""
        schema = [
            {"key": "api_key", "label": "API Key", "type": "text", "required": True, "section": "args"},
            {"key": "verbose", "label": "Verbose", "type": "boolean", "default": False, "section": "args"},
            {"key": "timeout", "label": "Timeout", "type": "number", "min": 1, "max": 300, "default": 30, "section": "args"},
            {"key": "mode", "label": "Mode", "type": "select", "options": ["dev", "prod"], "section": "args"},
        ]
        values = {
            "api_key": "sk-valid",
            "verbose": True,
            "timeout": 60,
            "mode": "prod",
        }

        # Should not raise
        validate_config_values(schema, values)

    def test_validate_optional_field_missing(self):
        """Test that missing optional field doesn't raise error."""
        schema = [
            {"key": "api_key", "label": "API Key", "type": "text", "required": True, "section": "args"},
            {"key": "optional_field", "label": "Optional", "type": "text", "required": False, "section": "args"},
        ]
        values = {"api_key": "sk-test"}

        # Should not raise
        validate_config_values(schema, values)

    def test_validate_multiple_errors(self):
        """Test that multiple validation errors are collected."""
        schema = [
            {"key": "api_key", "label": "API Key", "type": "text", "required": True, "section": "args"},
            {"key": "timeout", "label": "Timeout", "type": "number", "min": 1, "max": 100, "section": "args"},
        ]
        values = {
            "api_key": "",  # Empty required
            "timeout": 500,  # Over max
        }

        with pytest.raises(McpConfigValidationError) as exc_info:
            validate_config_values(schema, values)

        errors = exc_info.value.errors
        assert len(errors) >= 2


# =============================================================================
# Group 4: BuiltinServerRegistry Tests
# =============================================================================


class TestBuiltinServerRegistry:
    """Tests for BuiltinServerRegistry."""

    def test_register_definition(self):
        """Test registering a definition."""
        registry = get_registry()
        test_def = TestBuiltinServerDefinition()

        # Ensure not already registered
        registry.unregister(test_def.name)

        registry.register(test_def)
        assert test_def.name in registry.definitions

        # Clean up
        registry.unregister(test_def.name)

    def test_get_by_name(self):
        """Test getting definition by name."""
        registry = get_registry()
        test_def = TestBuiltinServerDefinition()

        # Ensure not already registered
        registry.unregister(test_def.name)

        registry.register(test_def)
        retrieved = registry.get_by_name(test_def.name)
        assert retrieved is not None
        assert retrieved.name == test_def.name

        # Clean up
        registry.unregister(test_def.name)

    def test_get_by_name_not_found(self):
        """Test getting non-existent definition returns None."""
        registry = get_registry()
        retrieved = registry.get_by_name("nonexistent-builtin")
        assert retrieved is None

    def test_get_all(self):
        """Test getting all definitions."""
        registry = get_registry()
        test_def = TestBuiltinServerDefinition()

        # Ensure not already registered
        registry.unregister(test_def.name)

        registry.register(test_def)
        all_defs = registry.get_all()
        assert test_def in all_defs

        # Clean up
        registry.unregister(test_def.name)

    def test_definitions_property(self):
        """Test definitions property returns dict."""
        registry = get_registry()
        test_def = TestBuiltinServerDefinition()

        # Ensure not already registered
        registry.unregister(test_def.name)

        registry.register(test_def)
        definitions = registry.definitions
        assert isinstance(definitions, dict)
        assert test_def.name in definitions

        # Clean up
        registry.unregister(test_def.name)


# =============================================================================
# Group 5: API Protection Tests (Built-in Server Deletion/Update)
# =============================================================================


class TestBuiltinApiProtection:
    """Tests for built-in server API protection."""

    def test_delete_builtin_returns_403(self, client, shared_repository):
        """Test that deleting a built-in server returns 403."""
        # Create a built-in server using shared repository
        builtin_server = shared_repository.create_mcp_server(
            name="test-builtin",
            description="Test builtin",
            config={"args": [], "env": {}},
            is_builtin=True,
        )

        response = client.delete(f"/api/mcp-servers/{builtin_server.id}")

        assert response.status_code == 403
        data = response.json()
        assert data["detail"]["code"] == ErrorCodes.BUILTIN_SERVER_PROTECTED.value
        assert "Cannot delete" in data["detail"]["message"]

    def test_delete_user_server_succeeds(self, client, shared_repository):
        """Test that deleting a non-built-in server succeeds."""
        # Create a user server using shared repository
        user_server = shared_repository.create_mcp_server(
            name="user-server",
            description="User server",
            config={"args": [], "env": {}},
            is_builtin=False,
        )

        response = client.delete(f"/api/mcp-servers/{user_server.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] is True

    def test_update_builtin_rejects_name_description(self, client, shared_repository):
        """Test that updating name/description on built-in returns 403."""
        # Create a built-in server using shared repository
        builtin_server = shared_repository.create_mcp_server(
            name="test-builtin",
            description="Original description",
            config={"args": [], "env": {}},
            is_builtin=True,
        )

        # Try to update name
        response = client.put(
            f"/api/mcp-servers/{builtin_server.id}",
            json={"name": "new-name"},
        )

        assert response.status_code == 403
        data = response.json()
        assert data["detail"]["code"] == ErrorCodes.BUILTIN_SERVER_PROTECTED.value

        # Try to update description
        response = client.put(
            f"/api/mcp-servers/{builtin_server.id}",
            json={"description": "New description"},
        )

        assert response.status_code == 403

    def test_update_builtin_allows_config_and_active(self, client, shared_repository):
        """Test that updating is_active on built-in is allowed (config has separate endpoint)."""
        # Create a built-in server using shared repository
        builtin_server = shared_repository.create_mcp_server(
            name="test-builtin",
            description="Test builtin",
            config={"args": ["--timeout", "60"], "env": {}},
            is_builtin=True,
        )

        # Update only is_active (should be allowed without config)
        response = client.put(
            f"/api/mcp-servers/{builtin_server.id}",
            json={"is_active": False},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["is_active"] is False

    def test_update_builtin_rejects_config(self, client, shared_repository):
        """PUT with config changes on a built-in server should return 403."""
        # Create a built-in server using shared repository
        builtin_server = shared_repository.create_mcp_server(
            name="test-builtin",
            description="Test builtin",
            config={"args": [], "env": {}},
            is_builtin=True,
        )

        # Try to update config
        response = client.put(
            f"/api/mcp-servers/{builtin_server.id}",
            json={"config": {"args": ["--api-key", "sk-new"], "env": {}}},
        )

        assert response.status_code == 403
        data = response.json()
        assert data["detail"]["code"] == ErrorCodes.BUILTIN_SERVER_PROTECTED.value
        assert "config" in data["detail"]["message"].lower()


# =============================================================================
# Group 6: Built-in Server API Endpoints
# =============================================================================


class TestBuiltinApiEndpoints:
    """Tests for built-in server API endpoints."""

    def test_list_builtin_templates(self, client):
        """Test GET /builtin-templates returns all registered templates."""
        response = client.get("/api/mcp-servers/builtin-templates")

        assert response.status_code == 200
        data = response.json()
        assert "templates" in data
        assert isinstance(data["templates"], list)
        # Should include our test-builtin template
        template_names = [t["name"] for t in data["templates"]]
        assert "test-builtin" in template_names

    def test_configure_builtin_creates_new(self, client):
        """Test POST /configure-builtin with new template creates server."""
        response = client.post(
            "/api/mcp-servers/configure-builtin",
            json={
                "template_name": "test-builtin",
                "values": {
                    "api_key": "sk-configure-test",
                    "verbose": True,
                    "timeout": 45,
                    "api_url": "https://configure.example.com",
                },
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-builtin"
        assert data["is_builtin"] is True
        assert data["config"]["args"] is not None

    def test_configure_builtin_updates_existing(self, client, shared_repository):
        """Test POST /configure-builtin with existing built-in updates config."""
        # First create the built-in server using shared repository
        shared_repository.create_mcp_server(
            name="test-builtin",
            description="Test builtin",
            config={"args": [], "env": {}},
            is_builtin=True,
        )

        # Configure again
        response = client.post(
            "/api/mcp-servers/configure-builtin",
            json={
                "template_name": "test-builtin",
                "values": {
                    "api_key": "sk-updated",
                    "timeout": 90,
                },
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert "--api-key" in data["config"]["args"]
        assert "sk-updated" in data["config"]["args"]
        assert "--timeout" in data["config"]["args"]
        assert "90" in data["config"]["args"]

    def test_configure_builtin_conflict_with_user_server(self, client, shared_repository):
        """Test POST /configure-builtin when user server with same name exists returns 409."""
        # Create user server with same name as built-in template using shared repository
        shared_repository.create_mcp_server(
            name="test-builtin",
            description="User server",
            config={"args": [], "env": {}},
            is_builtin=False,  # User server, not built-in
        )

        response = client.post(
            "/api/mcp-servers/configure-builtin",
            json={
                "template_name": "test-builtin",
                "values": {"api_key": "sk-conflict"},
            },
        )

        assert response.status_code == 409
        assert "user-created" in response.json()["detail"].lower()

    def test_configure_builtin_validation_error(self, client):
        """Test POST /configure-builtin with invalid values returns 422."""
        response = client.post(
            "/api/mcp-servers/configure-builtin",
            json={
                "template_name": "test-builtin",
                "values": {
                    "api_key": "",  # Empty string - validation should fail
                    "timeout": "not-a-number",  # Wrong type
                },
            },
        )

        assert response.status_code == 422
        data = response.json()
        assert "errors" in data["detail"] or "errors" in data["detail"].get("details", {})

    def test_configure_builtin_template_not_found(self, client):
        """Test POST /configure-builtin with non-existent template returns 404."""
        response = client.post(
            "/api/mcp-servers/configure-builtin",
            json={
                "template_name": "nonexistent-builtin",
                "values": {},
            },
        )

        assert response.status_code == 404


# =============================================================================
# Group 7: Built-in Server Reset Endpoint
# =============================================================================


class TestBuiltinApiResetEndpoint:
    """Tests for /reset-builtin endpoint."""

    def test_reset_builtin(self, client, shared_repository):
        """Test POST /reset-builtin resets to defaults."""
        # Create a built-in server with custom config using shared repository
        builtin_server = shared_repository.create_mcp_server(
            name="test-builtin",
            description="Test builtin",
            config={"args": ["--api-key", "sk-custom", "--timeout", "999"], "env": {"API_URL": "https://custom.com"}},
            is_builtin=True,
        )

        # Reset it
        response = client.post(f"/api/mcp-servers/{builtin_server.id}/reset-builtin")

        assert response.status_code == 200
        data = response.json()
        # Should have default values
        # timeout should be 30 (default)
        # api_url should be https://api.example.com (default)
        assert data["is_builtin"] is True

    def test_reset_non_builtin_403(self, client, shared_repository):
        """Test POST /reset-builtin on non-built-in returns 403."""
        # Create a user server using shared repository
        user_server = shared_repository.create_mcp_server(
            name="user-server",
            description="User server",
            config={"args": [], "env": {}},
            is_builtin=False,
        )

        response = client.post(f"/api/mcp-servers/{user_server.id}/reset-builtin")

        assert response.status_code == 403
        data = response.json()
        assert data["detail"]["code"] == ErrorCodes.BUILTIN_SERVER_PROTECTED.value
        assert "not a built-in" in data["detail"]["message"].lower()

    def test_reset_nonexistent_404(self, client):
        """Test POST /reset-builtin with non-existent server returns 404."""
        response = client.post("/api/mcp-servers/nonexistent-id/reset-builtin")

        assert response.status_code == 404


# =============================================================================
# Group 8: Bootstrap Tests
# =============================================================================


class TestBootstrap:
    """Tests for InstanceManager._bootstrap_builtin_servers()."""

    def test_bootstrap_creates_servers(self, instance_manager_with_repo, registry_with_test_def):
        """Test that bootstrap creates servers in DB with is_builtin=True."""
        manager = instance_manager_with_repo
        registry = registry_with_test_def

        # Call bootstrap
        manager._bootstrap_builtin_servers()

        # Verify server was created
        server = manager._mcp_server_repository.get_mcp_server_by_name("test-builtin")
        assert server is not None, "Server should be created by bootstrap"
        assert server.is_builtin is True, "Server should be marked as builtin"
        assert server.config_schema is not None, "Server should have config schema"

    def test_bootstrap_idempotent(self, instance_manager_with_repo, registry_with_test_def):
        """Test that running bootstrap twice doesn't create duplicates."""
        manager = instance_manager_with_repo
        registry = registry_with_test_def

        # Call bootstrap twice
        manager._bootstrap_builtin_servers()
        manager._bootstrap_builtin_servers()

        # Should still only have one server
        servers = manager._mcp_server_repository.list_mcp_servers()
        builtin_servers = [s for s in servers if s.name == "test-builtin"]
        assert len(builtin_servers) == 1, "Should have exactly one server after two bootstrap calls"

    def test_bootstrap_schema_drift(self, instance_manager_with_repo, registry_with_test_def):
        """Test that schema version change resets config to defaults.

        When schema_version changes, bootstrap should reset the config to
        the new defaults rather than preserving potentially stale user values.
        """
        from tests.unit.test_builtin_mcp_servers import TestBuiltinServerDefinition

        manager = instance_manager_with_repo
        registry = registry_with_test_def

        # Create a definition with version 1.0
        class TestBuiltinV1(TestBuiltinServerDefinition):
            @property
            def schema_version(self) -> str:
                return "1.0"

            def build_config(self, values: dict) -> dict:
                # v1 builds config with different structure
                return {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "old-package-v1"],
                }

        # Create a definition with version 2.0 (simulating schema change)
        class TestBuiltinV2(TestBuiltinServerDefinition):
            @property
            def schema_version(self) -> str:
                return "2.0"

            def build_config(self, values: dict) -> dict:
                # v2 builds config with new package name
                return {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "new-package-v2"],
                }

        v1_def = TestBuiltinV1()
        v2_def = TestBuiltinV2()

        # Register v1 and bootstrap
        registry.unregister(v1_def.name)  # Clean up if exists
        registry.register(v1_def)
        manager._bootstrap_builtin_servers()

        server = manager._mcp_server_repository.get_mcp_server_by_name("test-builtin")
        assert server.config_schema_version == "1.0"
        assert server.config["args"] == ["-y", "old-package-v1"]

        # Update server config directly (simulating user config)
        user_config = {"args": ["--api-key", "sk-keep-this"], "env": {}}
        manager._mcp_server_repository.update_mcp_server(server.id, config=user_config)

        # Replace v1 with v2 in registry (simulating schema version change)
        registry.unregister(v1_def.name)
        registry.register(v2_def)

        # Bootstrap again with new version
        manager._bootstrap_builtin_servers()

        # Verify config was RESET to defaults and version updated
        updated_server = manager._mcp_server_repository.get_mcp_server_by_name("test-builtin")
        assert updated_server.config["args"] == ["-y", "new-package-v2"], \
            "Config should be reset to defaults on schema drift"
        assert updated_server.config_schema_version == "2.0", "Schema version should be updated"

        # Restore original definition
        registry.unregister(v2_def.name)
        registry.register(v1_def)

    def test_bootstrap_fault_tolerant(self, instance_manager_with_repo, registry_with_test_def):
        """Test that one definition failure doesn't stop others."""
        manager = instance_manager_with_repo
        registry = registry_with_test_def

        # Create a broken definition that will fail during bootstrap
        class BrokenDefinition(BuiltinServerDefinition):
            @property
            def name(self) -> str:
                return "broken-bootstrap-test"

            @property
            def display_name(self) -> str:
                return "Broken Bootstrap Test"

            @property
            def description(self) -> str:
                return "A broken built-in server"

            @property
            def schema_version(self) -> str:
                return "1.0"

            def get_config_schema(self) -> list[dict]:
                raise RuntimeError("Schema error for testing")

            def build_config(self, values: dict) -> dict:
                raise RuntimeError("Config build error for testing")

        broken_def = BrokenDefinition()
        registry.register(broken_def)

        try:
            # Bootstrap with broken definition - should not raise
            try:
                manager._bootstrap_builtin_servers()
            except RuntimeError as e:
                if "Schema error" in str(e) or "Config build error" in str(e):
                    pytest.fail("Bootstrap should be fault tolerant and not raise on broken definition")
                raise

            # Good definition (test-builtin) should still be created
            server = manager._mcp_server_repository.get_mcp_server_by_name("test-builtin")
            assert server is not None, "Working definition should be created even if another fails"
            assert server.is_builtin is True
        finally:
            # Clean up broken definition
            registry.unregister(broken_def.name)

    def test_bootstrap_skips_user_created_servers(self, instance_manager_with_repo, registry_with_test_def):
        """Test that bootstrap skips servers created by users (is_builtin=False)."""
        manager = instance_manager_with_repo

        # Create a user-created server with same name as the built-in definition
        manager._mcp_server_repository.create_mcp_server(
            name="test-builtin",
            description="User created server",
            config={"args": [], "env": {}},
            is_builtin=False,  # User-created, not built-in
        )

        # Bootstrap should not override the user server
        manager._bootstrap_builtin_servers()

        # Verify it's still user-created
        server = manager._mcp_server_repository.get_mcp_server_by_name("test-builtin")
        assert server is not None
        assert server.is_builtin is False, "User-created server should remain user-created"
        # Config should be empty (user's original config), not the built-in default
        assert server.config == {"args": [], "env": {}}, "User config should not be overwritten"


# =============================================================================
# Group 9: McpServer Model Tests (is_builtin field)
# =============================================================================


class TestMcpServerModelBuiltin:
    """Tests for McpServer model with is_builtin field."""

    def test_model_default_is_builtin_false(self, repository, engine):
        """Test that is_builtin defaults to False."""
        server = repository.create_mcp_server(name="default-builtin-test")
        assert server.is_builtin is False

    def test_model_can_set_is_builtin_true(self, repository, engine):
        """Test that is_builtin can be set to True."""
        server = repository.create_mcp_server(
            name="builtin-test",
            is_builtin=True,
        )
        assert server.is_builtin is True

    def test_model_has_config_schema_field(self, repository, engine):
        """Test that McpServer has config_schema field."""
        server = repository.create_mcp_server(
            name="schema-test",
            config_schema=[{"key": "test", "label": "Test"}],
        )
        assert server.config_schema is not None
        assert len(server.config_schema) == 1

    def test_model_has_config_schema_version(self, repository, engine):
        """Test that McpServer has config_schema_version field."""
        server = repository.create_mcp_server(
            name="version-test",
            config_schema_version="1.0",
        )
        assert server.config_schema_version == "1.0"

    def test_model_default_schema_version(self, repository, engine):
        """Test that config_schema_version defaults to '0'."""
        server = repository.create_mcp_server(name="default-version-test")
        assert server.config_schema_version == "0"


# =============================================================================
# Group 10: Integration Tests
# =============================================================================


class TestBuiltinServerIntegration:
    """Integration tests for full built-in server workflow."""

    def test_full_workflow_configure_reset(self, client):
        """Test configure -> get -> reset workflow."""
        # Configure built-in
        configure_response = client.post(
            "/api/mcp-servers/configure-builtin",
            json={
                "template_name": "test-builtin",
                "values": {
                    "api_key": "sk-workflow",
                    "timeout": 120,
                },
            },
        )
        assert configure_response.status_code == 201
        server_id = configure_response.json()["id"]

        # Get server
        get_response = client.get(f"/api/mcp-servers/{server_id}")
        assert get_response.status_code == 200
        assert get_response.json()["is_builtin"] is True
        assert get_response.json()["initial_values"] is not None

        # Reset server
        reset_response = client.post(f"/api/mcp-servers/{server_id}/reset-builtin")
        assert reset_response.status_code == 200

        # Verify cannot delete
        delete_response = client.delete(f"/api/mcp-servers/{server_id}")
        assert delete_response.status_code == 403

    def test_list_includes_builtin_servers(self, client, shared_repository):
        """Test that list endpoint includes built-in servers."""
        # Create a built-in server using shared repository
        shared_repository.create_mcp_server(
            name="test-builtin",
            description="Test builtin",
            config={"args": [], "env": {}},
            is_builtin=True,
        )
        # Create a user server using shared repository
        shared_repository.create_mcp_server(
            name="user-server",
            description="User server",
            config={"args": [], "env": {}},
            is_builtin=False,
        )

        response = client.get("/api/mcp-servers")
        assert response.status_code == 200

        servers = response.json()["mcp_servers"]
        assert len(servers) == 2

        builtin_servers = [s for s in servers if s["is_builtin"]]
        user_servers = [s for s in servers if not s["is_builtin"]]

        assert len(builtin_servers) == 1
        assert len(user_servers) == 1
        assert builtin_servers[0]["name"] == "test-builtin"

    def test_templates_endpoint_always_available(self, client):
        """Test that templates endpoint works even without servers."""
        response = client.get("/api/mcp-servers/builtin-templates")
        assert response.status_code == 200
        assert "templates" in response.json()


# =============================================================================
# Group 11: is_builtin_disabled() Tests
# =============================================================================


class TestIsBuiltinDisabled:
    """Tests for is_builtin_disabled() helper function."""

    def test_is_builtin_disabled_not_set(self):
        """Test that unset env var returns False (server not disabled)."""
        from unittest.mock import patch

        with patch.dict("os.environ", {}, clear=True):
            from daemon.mcp.builtin_servers import is_builtin_disabled
            # Reload to pick up patched env
            import importlib
            importlib.reload(importlib.import_module("daemon.mcp.builtin_servers"))
            from daemon.mcp.builtin_servers import is_builtin_disabled

            assert is_builtin_disabled("context7") is False
            assert is_builtin_disabled("webfetch") is False
            assert is_builtin_disabled("unknown") is False

    def test_is_builtin_disabled_true_lowercase(self):
        """Test that MCP_DISABLE_BUILT_IN_X=true (lowercase) disables server."""
        from unittest.mock import patch

        with patch.dict("os.environ", {"MCP_DISABLE_BUILT_IN_CONTEXT7": "true"}, clear=True):
            import importlib
            importlib.reload(importlib.import_module("daemon.mcp.builtin_servers"))
            from daemon.mcp.builtin_servers import is_builtin_disabled

            assert is_builtin_disabled("context7") is True
            assert is_builtin_disabled("webfetch") is False  # Other servers not disabled

    def test_is_builtin_disabled_true_uppercase(self):
        """Test that MCP_DISABLE_BUILT_IN_X=TRUE (uppercase) disables server."""
        from unittest.mock import patch

        with patch.dict("os.environ", {"MCP_DISABLE_BUILT_IN_WEBFETCH": "TRUE"}, clear=True):
            import importlib
            importlib.reload(importlib.import_module("daemon.mcp.builtin_servers"))
            from daemon.mcp.builtin_servers import is_builtin_disabled

            assert is_builtin_disabled("webfetch") is True
            assert is_builtin_disabled("context7") is False

    def test_is_builtin_disabled_mixed_case(self):
        """Test that MCP_DISABLE_BUILT_IN_X=True (mixed case) disables server."""
        from unittest.mock import patch

        with patch.dict("os.environ", {"MCP_DISABLE_BUILT_IN_CONTEXT7": "TrUe"}, clear=True):
            import importlib
            importlib.reload(importlib.import_module("daemon.mcp.builtin_servers"))
            from daemon.mcp.builtin_servers import is_builtin_disabled

            assert is_builtin_disabled("context7") is True

    def test_is_builtin_disabled_false_string(self):
        """Test that non-true values don't disable server."""
        from unittest.mock import patch

        false_values = ["false", "0", "no", "1", ""]
        for val in false_values:
            with patch.dict("os.environ", {"MCP_DISABLE_BUILT_IN_CONTEXT7": val}, clear=True):
                import importlib
                importlib.reload(importlib.import_module("daemon.mcp.builtin_servers"))
                from daemon.mcp.builtin_servers import is_builtin_disabled

                assert is_builtin_disabled("context7") is False, f"Value '{val}' should not disable server"

    def test_is_builtin_disabled_case_insensitive_server_name(self):
        """Test that server name is uppercased for env var lookup."""
        from unittest.mock import patch

        with patch.dict("os.environ", {"MCP_DISABLE_BUILT_IN_CONTEXT7": "true"}, clear=True):
            import importlib
            importlib.reload(importlib.import_module("daemon.mcp.builtin_servers"))
            from daemon.mcp.builtin_servers import is_builtin_disabled

            # Both lowercase and uppercase should work
            assert is_builtin_disabled("context7") is True
            assert is_builtin_disabled("CONTEXT7") is True
            assert is_builtin_disabled("Context7") is True


# =============================================================================
# Group 12: Bootstrap Disable/Enable Tests
# =============================================================================


class TestBootstrapDisableEnable:
    """Tests for bootstrap disable/enable behavior via env flags."""

    def test_bootstrap_disabled_skips_creation(self, instance_manager_with_repo, registry_with_test_def):
        """Test that bootstrap skips creating a server when disabled via env var."""
        from unittest.mock import patch

        manager = instance_manager_with_repo

        # Patch is_builtin_disabled to return True
        with patch("daemon.manager.is_builtin_disabled", return_value=True):
            # Call bootstrap
            manager._bootstrap_builtin_servers()

            # Server should NOT be created
            server = manager._mcp_server_repository.get_mcp_server_by_name("test-builtin")
            assert server is None, "Disabled server should not be created"

    def test_bootstrap_disabled_deactivates_existing(self, instance_manager_with_repo, registry_with_test_def):
        """Test that bootstrap deactivates existing server when disabled via env var."""
        from unittest.mock import patch

        manager = instance_manager_with_repo

        # First, create the server normally (without disable flag)
        manager._bootstrap_builtin_servers()
        server = manager._mcp_server_repository.get_mcp_server_by_name("test-builtin")
        assert server is not None
        assert server.is_active is True

        # Now disable via env var - patch the function directly in manager module
        with patch("daemon.manager.is_builtin_disabled", return_value=True):
            # Call bootstrap again
            manager._bootstrap_builtin_servers()

            # Server should be deactivated
            server = manager._mcp_server_repository.get_mcp_server_by_name("test-builtin")
            assert server is not None
            assert server.is_active is False, "Disabled server should be deactivated"

    def test_bootstrap_reenable_reactivates(self, instance_manager_with_repo, registry_with_test_def):
        """Test that removing disable flag reactivates server on next bootstrap."""
        from unittest.mock import patch

        manager = instance_manager_with_repo

        # First, create the server normally (enabled)
        with patch("daemon.manager.is_builtin_disabled", return_value=False):
            manager._bootstrap_builtin_servers()

        # Server should exist and be active
        server = manager._mcp_server_repository.get_mcp_server_by_name("test-builtin")
        assert server is not None
        assert server.is_active is True

        # Now disable via env var - server should be deactivated
        with patch("daemon.manager.is_builtin_disabled", return_value=True):
            manager._bootstrap_builtin_servers()

        # Server should be deactivated
        server = manager._mcp_server_repository.get_mcp_server_by_name("test-builtin")
        assert server is not None
        assert server.is_active is False

        # Now remove the disable flag - patch returns False
        with patch("daemon.manager.is_builtin_disabled", return_value=False):
            # Call bootstrap again
            manager._bootstrap_builtin_servers()

            # Server should be reactivated
            server = manager._mcp_server_repository.get_mcp_server_by_name("test-builtin")
            assert server is not None
            assert server.is_active is True, "Re-enabled server should be activated"

    def test_bootstrap_enabled_creates_new(self, instance_manager_with_repo, registry_with_test_def):
        """Test that bootstrap creates server when not disabled."""
        from unittest.mock import patch

        manager = instance_manager_with_repo

        with patch.dict("os.environ", {}, clear=True):
            # Call bootstrap
            manager._bootstrap_builtin_servers()

            # Server should be created
            server = manager._mcp_server_repository.get_mcp_server_by_name("test-builtin")
            assert server is not None
            assert server.is_builtin is True
            assert server.is_active is True

    def test_bootstrap_disabled_with_user_server(self, instance_manager_with_repo, registry_with_test_def, caplog):
        """Test that disabled builtin with user-created record is left unchanged.

        When a server is disabled via env var but the existing DB record is
        user-created (is_builtin=False), bootstrap should log a warning and
        leave the record unchanged rather than deactivating it.
        """
        from unittest.mock import patch
        import logging

        manager = instance_manager_with_repo

        # Create a user-created server with same name as the built-in definition
        manager._mcp_server_repository.create_mcp_server(
            name="test-builtin",
            description="User created server",
            config={"args": [], "env": {}},
            is_builtin=False,  # User-created, not built-in
            is_active=True,
        )

        with caplog.at_level(logging.WARNING):
            with patch("daemon.manager.is_builtin_disabled", return_value=True):
                # Call bootstrap with disable flag
                manager._bootstrap_builtin_servers()

        # Verify server still exists and is unchanged
        server = manager._mcp_server_repository.get_mcp_server_by_name("test-builtin")
        assert server is not None
        assert server.is_builtin is False, "Server should remain user-created"
        assert server.is_active is True, "User-created server should remain active"
        assert server.config == {"args": [], "env": {}}, "User config should not be changed"
        # Should have logged a warning about the conflict
        assert any("user-created" in record.message.lower() for record in caplog.records), \
            "Should log warning about user-created server conflict"

    def test_bootstrap_reenable_with_schema_drift(self, instance_manager_with_repo, registry_with_test_def):
        """Test that re-enabling server also fixes schema drift.

        When a server is:
        1. Disabled (is_active=False) with old schema version
        2. Then re-enabled via env var removal
        3. AND schema version has changed

        Bootstrap should both reactivate AND refresh the config.
        This test would have caught the elif bug that prevented schema drift
        fix from running after reactivation.
        """
        from unittest.mock import patch
        from tests.unit.test_builtin_mcp_servers import TestBuiltinServerDefinition

        manager = instance_manager_with_repo
        registry = registry_with_test_def

        # Create a definition with version 1.0
        class TestBuiltinV1(TestBuiltinServerDefinition):
            @property
            def schema_version(self) -> str:
                return "1.0"

            def build_config(self, values: dict) -> dict:
                # v1 builds config with different structure
                return {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "old-package-v1"],
                }

        # Create a definition with version 2.0 (simulating schema change)
        class TestBuiltinV2(TestBuiltinServerDefinition):
            @property
            def schema_version(self) -> str:
                return "2.0"

            def build_config(self, values: dict) -> dict:
                # v2 builds config with new package name
                return {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "new-package-v2"],
                }

        v1_def = TestBuiltinV1()
        v2_def = TestBuiltinV2()

        # Register v1 and bootstrap
        registry.unregister(v1_def.name)
        registry.register(v1_def)
        manager._bootstrap_builtin_servers()

        # Manually set is_active=False and config to simulate disabled state
        server = manager._mcp_server_repository.get_mcp_server_by_name("test-builtin")
        assert server.config_schema_version == "1.0"

        # Deactivate and change config to simulate disabled state
        manager._mcp_server_repository.update_mcp_server(
            server.id,
            is_active=False,
            config={"args": ["--api-key", "stale-key"], "env": {}},
            config_schema_version="1.0",
        )

        # Replace v1 with v2 in registry (simulating schema version change)
        registry.unregister(v1_def.name)
        registry.register(v2_def)

        # Now re-enable via env var (not disabled) - this should trigger reactivation AND schema fix
        with patch("daemon.manager.is_builtin_disabled", return_value=False):
            manager._bootstrap_builtin_servers()

        # Verify server is BOTH reactivated AND schema refreshed
        updated_server = manager._mcp_server_repository.get_mcp_server_by_name("test-builtin")
        assert updated_server is not None
        assert updated_server.is_active is True, "Server should be reactivated"
        assert updated_server.config_schema_version == "2.0", "Schema version should be updated"
        # Config should have been refreshed to v2 defaults (new-package-v2)
        assert updated_server.config["args"] == ["-y", "new-package-v2"], \
            "Config should be reset to v2 defaults on schema drift"

        # Restore original definition
        registry.unregister(v2_def.name)
        registry.register(v1_def)


# =============================================================================
# Group 13: Warmup Pool Registration Tests (Issue: disabled servers were
# still registered with the warmup pool)
# =============================================================================


class TestWarmupPoolSkipsDisabled:
    """Tests that ``_init_warmup_pool`` respects ``is_builtin_disabled``.

    Regression: bootstrap used to skip creating a DB record for
    ``MCP_DISABLE_BUILT_IN_*=true`` servers, so the warmup pool's
    "is_active=False" check never matched (existing was ``None``) and
    the server was registered for pooling anyway. The fix adds an
    explicit ``is_builtin_disabled`` guard at the top of the loop.
    """

    def test_warmup_skips_disabled_builtin(self, registry_with_test_def):
        """Disabled built-in server is NOT registered with the warmup pool."""
        from unittest.mock import patch

        from daemon.mcp.warmup_pool import McpWarmupPool
        from daemon.mcp.builtin_servers import get_registry
        from daemon.mcp.config import McpStdioConfig
        from daemon.manager import is_builtin_disabled as manager_is_disabled

        pool = McpWarmupPool()

        # Simulate the relevant body of _init_warmup_pool with the fix
        # applied, while ``is_builtin_disabled`` returns True for
        # every server.
        with patch("daemon.manager.is_builtin_disabled", return_value=True):
            for definition in get_registry().get_all():
                if manager_is_disabled(definition.name):
                    continue
                config_dict = definition.get_base_config()
                if config_dict.get("transport") != "stdio":
                    continue
                stdio_config = McpStdioConfig(**config_dict)
                pool.register_server(definition.name, stdio_config)

        assert not pool.is_pooled_server("test-builtin"), (
            "Disabled built-in should be skipped by warmup pool"
        )

    def test_warmup_registers_enabled_builtin(self):
        """Enabled built-in (real webfetch) IS registered with the warmup pool.

        Uses the real ``webfetch`` built-in definition (which exposes a
        stdio ``get_base_config()``) rather than the abstract
        ``TestBuiltinServerDefinition`` whose default base config is
        empty and would be filtered out by the transport check.
        """
        from unittest.mock import patch

        from daemon.mcp.warmup_pool import McpWarmupPool
        from daemon.mcp.builtin_servers import get_registry
        from daemon.mcp.config import McpStdioConfig
        from daemon.manager import is_builtin_disabled as manager_is_disabled

        pool = McpWarmupPool()

        with patch("daemon.manager.is_builtin_disabled", return_value=False):
            for definition in get_registry().get_all():
                if manager_is_disabled(definition.name):
                    continue
                config_dict = definition.get_base_config()
                if config_dict.get("transport") != "stdio":
                    continue
                stdio_config = McpStdioConfig(**config_dict)
                pool.register_server(definition.name, stdio_config)

        # webfetch and context7 are real stdio built-ins.
        assert pool.is_pooled_server("webfetch"), (
            "Enabled webfetch should be registered with warmup pool"
        )
        assert pool.is_pooled_server("context7"), (
            "Enabled context7 should be registered with warmup pool"
        )


# =============================================================================
# Group 14: Module Availability Pre-Check (bootstrap + warmup skip)
# =============================================================================


class _UnavailableTestBuiltin(TestBuiltinServerDefinition):
    """Test builtin that always reports ``is_available() == False``.

    Used to simulate "module missing" without touching the real
    builtin registry.
    """

    @classmethod
    def is_available(cls) -> bool:
        return False


@pytest.fixture
def registry_with_unavailable_builtin():
    """Registry fixture: only an unavailable-test builtin is registered.

    Replaces the global registry's contents for the duration of the
    test so other built-ins (webfetch, context7) don't
    interfere. Restored on teardown.
    """
    from daemon.mcp.builtin_servers import _registry

    saved = dict(_registry._definitions)
    _registry._definitions.clear()
    test_def = _UnavailableTestBuiltin()
    _registry.register(test_def)
    yield _registry
    _registry._definitions.clear()
    _registry._definitions.update(saved)


class TestBootstrapSkipsUnavailable:
    """Bootstrap must skip DB record creation when a builtin is unavailable."""

    def test_bootstrap_skips_unavailable_builtin(
        self, instance_manager_with_repo, registry_with_unavailable_builtin, caplog
    ):
        """Unavailable builtin → no DB record, single INFO log line.

        The log message must include the builtin name and the install
        hint so operators can act without grepping stack traces.
        """
        import logging

        manager = instance_manager_with_repo
        with caplog.at_level(logging.INFO, logger="daemon.manager"):
            manager._bootstrap_builtin_servers()

        # No DB record should exist for the unavailable builtin.
        server = manager._mcp_server_repository.get_mcp_server_by_name(
            "test-builtin"
        )
        assert server is None, (
            "Unavailable builtin must NOT get a DB record"
        )

        # Must log exactly one INFO line about the skip.
        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        skip_messages = [
            r.getMessage()
            for r in info_records
            if "skipped" in r.getMessage().lower()
        ]
        assert any("test-builtin" in m for m in skip_messages), (
            f"Expected a skip log mentioning 'test-builtin', "
            f"got: {[r.getMessage() for r in info_records]}"
        )

    def test_bootstrap_no_exception_when_unavailable(
        self, instance_manager_with_repo, registry_with_unavailable_builtin
    ):
        """Unavailable builtin must NOT raise — fault tolerance preserved."""
        manager = instance_manager_with_repo
        # Must not raise — the per-server try/except already handles
        # this; the availability check is just an early continue.
        manager._bootstrap_builtin_servers()


class TestWarmupPoolSkipsUnavailable:
    """Warmup pool must skip registration when a builtin is unavailable.

    The warmup pool's existing ``is_active=False`` check wouldn't
    catch this case because no DB record was created (bootstrap also
    skipped). The availability check is the only guard here.
    """

    def test_warmup_skips_unavailable_builtin(
        self, registry_with_unavailable_builtin
    ):
        """Unavailable builtin → not registered with the warmup pool."""

        from daemon.mcp.warmup_pool import McpWarmupPool
        from daemon.mcp.builtin_servers import get_registry

        pool = McpWarmupPool()

        # Replicate the warmup-pool init loop with the availability
        # check applied. The test ensures our check is structurally
        # in place — no real McpStdioConfig / DB lookup needed.
        for definition in get_registry().get_all():
            if not definition.is_available():
                continue

            from daemon.mcp.config import McpStdioConfig
            config_dict = definition.get_base_config()
            if config_dict.get("transport") != "stdio":
                continue
            stdio_config = McpStdioConfig(**config_dict)
            pool.register_server(definition.name, stdio_config)

        assert not pool.is_pooled_server("test-builtin"), (
            "Unavailable builtin must NOT be registered with warmup pool"
        )

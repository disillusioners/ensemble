"""Tests for MCP Server CRUD operations.

This module tests the MCP Server CRUD functionality including:
- Pydantic schema validation (McpServerCreate, McpServerUpdate, McpServerInfo)
- SQLModel database model (McpServer)
- Repository layer (SQLModelMcpServerRepository)
- API router endpoints (via FastAPI TestClient)
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import SQLModel, create_engine, Session as SQLModelSession

from daemon.models import (
    McpServerCreate,
    McpServerUpdate,
    McpServerInfo,
    McpServerListResponse,
    McpServerDeleteResponse,
)
from daemon.repositories.mcp_server import McpServer, SQLModelMcpServerRepository
from daemon.routers.mcp_servers import router as mcp_servers_router


# =============================================================================
# Fixtures
# =============================================================================


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
            "sqlite:///test_mcp_servers.db",
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
        # Delete all rows from mcp_servers table
        with SQLModelSession(_router_engine) as session:
            session.exec("DELETE FROM mcp_servers")
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


@pytest.fixture
def app(router_engine_and_repo):
    """Create FastAPI app with MCP servers router for testing."""
    import asyncio

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
# Group 1: Model & Schema Tests
# =============================================================================


class TestMcpServerModel:
    """Tests for the SQLModel McpServer database model."""

    def test_model_has_correct_fields(self, engine):
        """Test that McpServer model has all required fields."""
        from sqlalchemy import text

        # Create table and verify columns exist
        SQLModel.metadata.create_all(engine)

        with SQLModelSession(engine) as session:
            # Verify table exists
            result = session.exec(text("SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_servers'"))
            tables = list(result)
            assert len(tables) == 1

    def test_model_default_values(self):
        """Test that McpServer model has correct default values."""
        server = McpServer(name="test-server")
        assert server.name == "test-server"
        assert server.description is None
        assert server.config == {}
        assert server.is_active is True
        assert server.id is not None
        assert server.created_at is not None
        assert server.updated_at is None


class TestMcpServerCreateSchema:
    """Tests for McpServerCreate Pydantic schema."""

    def test_valid_minimal_create(self):
        """Test creating with minimal required fields."""
        schema = McpServerCreate(name="my-server")
        assert schema.name == "my-server"
        assert schema.description is None
        assert schema.config == {}
        assert schema.is_active is True

    def test_valid_full_create(self):
        """Test creating with all fields."""
        config = {"host": "localhost", "port": 8080}
        schema = McpServerCreate(
            name="full-server",
            description="A fully configured server",
            config=config,
            is_active=False,
        )
        assert schema.name == "full-server"
        assert schema.description == "A fully configured server"
        assert schema.config == {"host": "localhost", "port": 8080}
        assert schema.is_active is False

    def test_empty_name_rejected(self):
        """Test that empty name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            McpServerCreate(name="")
        assert "name" in str(exc_info.value)

    def test_whitespace_only_name_accepted(self):
        """Test that whitespace-only name is accepted by Pydantic (validation at DB level)."""
        # Note: Pydantic's min_length=1 allows whitespace-only strings.
        # The unique constraint is enforced at database level, not Pydantic level.
        schema = McpServerCreate(name="   ")
        assert schema.name == "   "

    def test_name_max_length(self):
        """Test that name exceeding max length is rejected."""
        long_name = "x" * 129  # max is 128
        with pytest.raises(ValidationError) as exc_info:
            McpServerCreate(name=long_name)
        assert "name" in str(exc_info.value)

    def test_name_at_max_length_accepted(self):
        """Test that name at exactly max length is accepted."""
        max_name = "x" * 128
        schema = McpServerCreate(name=max_name)
        assert schema.name == max_name


class TestMcpServerUpdateSchema:
    """Tests for McpServerUpdate Pydantic schema."""

    def test_all_fields_optional(self):
        """Test that all fields in update schema are optional."""
        schema = McpServerUpdate()
        assert schema.name is None
        assert schema.description is None
        assert schema.config is None
        assert schema.is_active is None

    def test_partial_update(self):
        """Test partial update with only some fields."""
        schema = McpServerUpdate(name="new-name")
        assert schema.name == "new-name"
        assert schema.description is None
        assert schema.config is None
        assert schema.is_active is None

    def test_empty_name_rejected(self):
        """Test that empty name is rejected."""
        with pytest.raises(ValidationError):
            McpServerUpdate(name="")


class TestMcpServerInfoSchema:
    """Tests for McpServerInfo response schema."""

    def test_info_schema_requires_all_fields(self):
        """Test that McpServerInfo requires all fields."""
        now = datetime.now()
        schema = McpServerInfo(
            id="server-123",
            name="test-server",
            description="A test server",
            config={"key": "value"},
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        assert schema.id == "server-123"
        assert schema.name == "test-server"

    def test_info_schema_optional_fields(self):
        """Test that description and updated_at are optional in response."""
        now = datetime.now()
        schema = McpServerInfo(
            id="server-123",
            name="test-server",
            description=None,
            config={},
            is_active=True,
            created_at=now,
            updated_at=None,
        )
        assert schema.description is None
        assert schema.updated_at is None


class TestConfigJsonField:
    """Tests for config JSON field storage and retrieval."""

    def test_nested_objects_in_config(self, repository, engine):
        """Test that nested objects are preserved in config field."""
        config = {
            "server": {
                "host": "localhost",
                "ports": {
                    "http": 8080,
                    "https": 8443,
                },
            },
        }
        server = repository.create_mcp_server(
            name="nested-config-server",
            config=config,
        )

        # Retrieve and verify
        retrieved = repository.get_mcp_server(server.id)
        assert retrieved.config == config
        assert retrieved.config["server"]["host"] == "localhost"
        assert retrieved.config["server"]["ports"]["https"] == 8443

    def test_arrays_in_config(self, repository, engine):
        """Test that arrays are preserved in config field."""
        config = {
            "servers": ["server1", "server2", "server3"],
            "ports": [80, 443, 8080],
        }
        server = repository.create_mcp_server(
            name="array-config-server",
            config=config,
        )

        # Retrieve and verify
        retrieved = repository.get_mcp_server(server.id)
        assert retrieved.config == config
        assert retrieved.config["servers"] == ["server1", "server2", "server3"]
        assert len(retrieved.config["ports"]) == 3

    def test_mixed_complex_config(self, repository, engine):
        """Test complex config with mixed types."""
        config = {
            "enabled": True,
            "count": 42,
            "name": "test",
            "nested": {
                "array": [1, 2, {"a": "b"}],
                "null": None,
            },
        }
        server = repository.create_mcp_server(
            name="complex-config-server",
            config=config,
        )

        retrieved = repository.get_mcp_server(server.id)
        assert retrieved.config == config
        assert retrieved.config["nested"]["array"][2] == {"a": "b"}

    def test_empty_config(self, repository, engine):
        """Test that empty config is handled correctly."""
        server = repository.create_mcp_server(
            name="empty-config-server",
            config={},
        )
        retrieved = repository.get_mcp_server(server.id)
        assert retrieved.config == {}


# =============================================================================
# Group 2: Repository Tests
# =============================================================================


class TestRepositoryCreate:
    """Tests for repository create operations."""

    def test_create_with_valid_data(self, repository, engine):
        """Test creating an MCP server with valid data."""
        server = repository.create_mcp_server(
            name="test-server",
            description="Test description",
            config={"key": "value"},
            is_active=True,
        )

        assert server.id is not None
        assert server.name == "test-server"
        assert server.description == "Test description"
        assert server.config == {"key": "value"}
        assert server.is_active is True
        assert server.created_at is not None
        assert server.updated_at is None

    def test_create_with_defaults(self, repository, engine):
        """Test creating an MCP server with default values."""
        server = repository.create_mcp_server(name="minimal-server")

        assert server.id is not None
        assert server.name == "minimal-server"
        assert server.description is None
        assert server.config == {}
        assert server.is_active is True


class TestRepositoryGet:
    """Tests for repository get operations."""

    def test_get_existing_server(self, repository, engine):
        """Test getting an existing MCP server by ID."""
        created = repository.create_mcp_server(name="get-test-server")
        retrieved = repository.get_mcp_server(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.name == created.name

    def test_get_nonexistent_id(self, repository, engine):
        """Test getting a non-existent ID returns None."""
        result = repository.get_mcp_server("nonexistent-id-12345")
        assert result is None

    def test_get_by_name(self, repository, engine):
        """Test getting MCP server by name."""
        created = repository.create_mcp_server(name="by-name-server")
        retrieved = repository.get_mcp_server_by_name("by-name-server")

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.name == "by-name-server"

    def test_get_by_nonexistent_name(self, repository, engine):
        """Test getting non-existent name returns None."""
        result = repository.get_mcp_server_by_name("nonexistent-name")
        assert result is None


class TestRepositoryList:
    """Tests for repository list operations."""

    def test_list_empty(self, repository, engine):
        """Test listing MCP servers when none exist."""
        servers = repository.list_mcp_servers()
        assert servers == []

    def test_list_all_servers(self, repository, engine):
        """Test listing all MCP servers."""
        repository.create_mcp_server(name="server-1")
        repository.create_mcp_server(name="server-2")
        repository.create_mcp_server(name="server-3")

        servers = repository.list_mcp_servers()
        assert len(servers) == 3
        names = {s.name for s in servers}
        assert names == {"server-1", "server-2", "server-3"}

    def test_list_with_limit(self, repository, engine):
        """Test listing with limit parameter."""
        for i in range(5):
            repository.create_mcp_server(name=f"server-{i}")

        servers = repository.list_mcp_servers(limit=2)
        assert len(servers) == 2

    def test_list_with_offset(self, repository, engine):
        """Test listing with offset parameter."""
        for i in range(5):
            repository.create_mcp_server(name=f"server-{i}")

        # Get all and verify offset behavior
        all_servers = repository.list_mcp_servers()
        offset_servers = repository.list_mcp_servers(offset=2)
        assert len(all_servers) == 5
        assert len(offset_servers) <= 3

    def test_list_filter_by_active(self, repository, engine):
        """Test listing with is_active filter."""
        repository.create_mcp_server(name="active-server", is_active=True)
        repository.create_mcp_server(name="inactive-server", is_active=False)

        active_servers = repository.list_mcp_servers(is_active=True)
        inactive_servers = repository.list_mcp_servers(is_active=False)

        assert all(s.is_active for s in active_servers)
        assert all(not s.is_active for s in inactive_servers)


class TestRepositoryUpdate:
    """Tests for repository update operations."""

    def test_update_name(self, repository, engine):
        """Test updating server name."""
        server = repository.create_mcp_server(name="original-name")
        updated = repository.update_mcp_server(server.id, name="new-name")

        assert updated is not None
        assert updated.name == "new-name"
        assert updated.updated_at is not None

    def test_update_description(self, repository, engine):
        """Test updating server description."""
        server = repository.create_mcp_server(name="test-server", description=None)
        updated = repository.update_mcp_server(server.id, description="New description")

        assert updated.description == "New description"

    def test_update_config(self, repository, engine):
        """Test updating server config."""
        server = repository.create_mcp_server(name="test-server", config={"old": "value"})
        updated = repository.update_mcp_server(server.id, config={"new": "value"})

        assert updated.config == {"new": "value"}

    def test_update_is_active(self, repository, engine):
        """Test updating server active status."""
        server = repository.create_mcp_server(name="test-server", is_active=True)
        updated = repository.update_mcp_server(server.id, is_active=False)

        assert updated.is_active is False

    def test_update_multiple_fields(self, repository, engine):
        """Test updating multiple fields at once."""
        server = repository.create_mcp_server(name="original")
        updated = repository.update_mcp_server(
            server.id,
            name="new-name",
            description="new description",
            is_active=False,
        )

        assert updated.name == "new-name"
        assert updated.description == "new description"
        assert updated.is_active is False

    def test_update_nonexistent(self, repository, engine):
        """Test updating non-existent server returns None."""
        result = repository.update_mcp_server("nonexistent-id", name="new-name")
        assert result is None


class TestRepositoryDelete:
    """Tests for repository delete operations."""

    def test_delete_existing_server(self, repository, engine):
        """Test deleting an existing server."""
        server = repository.create_mcp_server(name="delete-me")
        result = repository.delete_mcp_server(server.id)

        assert result["deleted"] is True
        assert result["id"] == server.id

        # Verify it's gone
        retrieved = repository.get_mcp_server(server.id)
        assert retrieved is None

    def test_delete_nonexistent_server(self, repository, engine):
        """Test deleting non-existent server returns deleted=False."""
        result = repository.delete_mcp_server("nonexistent-id")

        assert result["deleted"] is False
        assert result["id"] == "nonexistent-id"


class TestRepositoryDuplicateName:
    """Tests for duplicate name handling."""

    def test_unique_name_constraint(self, repository, engine):
        """Test that duplicate names are prevented at database level."""
        repository.create_mcp_server(name="unique-server")

        # The model has unique=True on name field, so SQLAlchemy should raise
        # However, SQLite doesn't enforce unique by default without the constraint
        # The router handles this by checking get_mcp_server_by_name first
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            # Create second server with same name directly
            second_server = McpServer(name="unique-server")
            with SQLModelSession(engine) as session:
                session.add(second_server)
                session.commit()


class TestConfigJsonRoundtrip:
    """Tests for config JSON roundtrip through repository."""

    def test_config_roundtrip_simple(self, repository, engine):
        """Test simple config roundtrip."""
        original_config = {"simple": "value"}
        server = repository.create_mcp_server(
            name="roundtrip-server",
            config=original_config,
        )

        retrieved = repository.get_mcp_server(server.id)
        assert retrieved.config == original_config

    def test_config_roundtrip_complex(self, repository, engine):
        """Test complex nested config roundtrip."""
        original_config = {
            "level1": {
                "level2": {
                    "level3": [1, 2, 3, {"nested": "object"}],
                }
            },
            "array": [{"a": 1}, {"b": 2}],
        }
        server = repository.create_mcp_server(
            name="complex-roundtrip",
            config=original_config,
        )

        retrieved = repository.get_mcp_server(server.id)
        assert retrieved.config == original_config


# =============================================================================
# Group 3: Router (API Endpoint) Tests
# =============================================================================


class TestRouterCreate:
    """Tests for POST /api/mcp-servers endpoint."""

    def test_create_server_success(self, client):
        """Test creating an MCP server successfully."""
        response = client.post(
            "/api/mcp-servers",
            json={
                "name": "new-server",
                "description": "Test server",
                "config": {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                },
                "is_active": True,
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "new-server"
        assert data["description"] == "Test server"
        assert data["config"]["transport"] == "stdio"
        assert data["is_active"] is True
        assert "id" in data
        assert "created_at" in data

    def test_create_server_minimal(self, client):
        """Test creating with minimal required fields and valid config."""
        response = client.post(
            "/api/mcp-servers",
            json={
                "name": "minimal-server",
                "config": {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                },
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "minimal-server"
        assert data["description"] is None
        assert data["config"]["transport"] == "stdio"
        assert data["is_active"] is True

    def test_create_duplicate_name(self, client):
        """Test creating server with duplicate name returns 409."""
        valid_config = {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        }
        # Create first server
        client.post(
            "/api/mcp-servers",
            json={"name": "duplicate-server", "config": valid_config},
        )

        # Try to create second with same name
        response = client.post(
            "/api/mcp-servers",
            json={"name": "duplicate-server", "config": valid_config},
        )

        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]["message"]

    def test_create_invalid_json_config(self, client):
        """Test creating server with invalid config returns validation error."""
        # This should be caught by Pydantic validation
        response = client.post(
            "/api/mcp-servers",
            json={
                "name": "test-server",
                "config": "not-a-dict",  # Should be dict[str, Any]
            },
        )

        assert response.status_code == 422  # Validation error


class TestRouterList:
    """Tests for GET /api/mcp-servers endpoint."""

    def test_list_servers_empty(self, client):
        """Test listing servers when none exist."""
        response = client.get("/api/mcp-servers")

        assert response.status_code == 200
        data = response.json()
        assert data["mcp_servers"] == []

    def test_list_servers(self, client):
        """Test listing servers returns all servers."""
        valid_config = {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        }
        # Create some servers
        client.post("/api/mcp-servers", json={"name": "server-1", "config": valid_config})
        client.post("/api/mcp-servers", json={"name": "server-2", "config": valid_config})

        response = client.get("/api/mcp-servers")

        assert response.status_code == 200
        data = response.json()
        assert len(data["mcp_servers"]) == 2
        names = {s["name"] for s in data["mcp_servers"]}
        assert names == {"server-1", "server-2"}


class TestRouterGet:
    """Tests for GET /api/mcp-servers/{server_id} endpoint."""

    def test_get_server_success(self, client):
        """Test getting a specific server."""
        valid_config = {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        }
        # Create server
        create_response = client.post(
            "/api/mcp-servers",
            json={"name": "get-test-server", "config": valid_config},
        )
        server_id = create_response.json()["id"]

        # Get server
        response = client.get(f"/api/mcp-servers/{server_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == server_id
        assert data["name"] == "get-test-server"
        assert data["config"]["transport"] == "stdio"

    def test_get_nonexistent_server(self, client):
        """Test getting non-existent server returns 404."""
        response = client.get("/api/mcp-servers/nonexistent-id-12345")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]["message"].lower()


class TestRouterUpdate:
    """Tests for PUT /api/mcp-servers/{server_id} endpoint."""

    def test_update_server_success(self, client):
        """Test updating a server successfully."""
        valid_config = {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        }
        # Create server
        create_response = client.post(
            "/api/mcp-servers",
            json={"name": "update-test-server", "config": valid_config, "is_active": True},
        )
        server_id = create_response.json()["id"]

        # Update server
        response = client.put(
            f"/api/mcp-servers/{server_id}",
            json={
                "name": "updated-name",
                "description": "Updated description",
                "is_active": False,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "updated-name"
        assert data["description"] == "Updated description"
        assert data["is_active"] is False

    def test_update_partial(self, client):
        """Test partial update only changes specified fields."""
        valid_config = {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        }
        # Create server
        create_response = client.post(
            "/api/mcp-servers",
            json={"name": "partial-test", "config": valid_config, "description": "Original"},
        )
        server_id = create_response.json()["id"]

        # Update only description
        response = client.put(
            f"/api/mcp-servers/{server_id}",
            json={"description": "New description"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "partial-test"  # Unchanged
        assert data["description"] == "New description"  # Changed

    def test_update_nonexistent_server(self, client):
        """Test updating non-existent server returns 404."""
        response = client.put(
            "/api/mcp-servers/nonexistent-id-12345",
            json={"name": "new-name"},
        )

        assert response.status_code == 404

    def test_update_name_to_duplicate(self, client):
        """Test updating name to existing name returns 409."""
        valid_config = {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        }
        # Create two servers
        client.post("/api/mcp-servers", json={"name": "server-1", "config": valid_config})
        server2_response = client.post(
            "/api/mcp-servers",
            json={"name": "server-2", "config": valid_config},
        )
        server2_id = server2_response.json()["id"]

        # Try to update server2's name to server1's name
        response = client.put(
            f"/api/mcp-servers/{server2_id}",
            json={"name": "server-1"},
        )

        assert response.status_code == 409


class TestRouterDelete:
    """Tests for DELETE /api/mcp-servers/{server_id} endpoint."""

    def test_delete_server_success(self, client):
        """Test deleting a server successfully."""
        valid_config = {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        }
        # Create server
        create_response = client.post(
            "/api/mcp-servers",
            json={"name": "delete-test-server", "config": valid_config},
        )
        server_id = create_response.json()["id"]

        # Delete server
        response = client.delete(f"/api/mcp-servers/{server_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] is True
        assert data["id"] == server_id

        # Verify it's gone
        get_response = client.get(f"/api/mcp-servers/{server_id}")
        assert get_response.status_code == 404

    def test_delete_nonexistent_server(self, client):
        """Test deleting non-existent server returns 404."""
        response = client.delete("/api/mcp-servers/nonexistent-id-12345")

        assert response.status_code == 404


# =============================================================================
# Group 4: Integration Tests
# =============================================================================


class TestFullCrudWorkflow:
    """End-to-end CRUD workflow tests."""

    def test_create_read_update_delete_workflow(self, client):
        """Test complete CRUD workflow."""
        valid_config = {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        }
        # CREATE
        create_response = client.post(
            "/api/mcp-servers",
            json={
                "name": "workflow-test",
                "description": "Initial description",
                "config": {**valid_config, "step": "create"},
            },
        )
        assert create_response.status_code == 201
        server_id = create_response.json()["id"]

        # READ
        get_response = client.get(f"/api/mcp-servers/{server_id}")
        assert get_response.status_code == 200
        assert get_response.json()["name"] == "workflow-test"

        # UPDATE
        update_response = client.put(
            f"/api/mcp-servers/{server_id}",
            json={
                "name": "workflow-test-updated",
                "config": {**valid_config, "step": "update"},
            },
        )
        assert update_response.status_code == 200
        assert update_response.json()["name"] == "workflow-test-updated"

        # DELETE
        delete_response = client.delete(f"/api/mcp-servers/{server_id}")
        assert delete_response.status_code == 200

        # Verify deletion
        get_after_delete = client.get(f"/api/mcp-servers/{server_id}")
        assert get_after_delete.status_code == 404

    def test_list_after_operations(self, client):
        """Test that list reflects all operations."""
        valid_config = {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        }
        # Create multiple servers
        for i in range(3):
            client.post(
                "/api/mcp-servers",
                json={"name": f"list-test-{i}", "config": valid_config},
            )

        # List should have 3
        list_response = client.get("/api/mcp-servers")
        assert len(list_response.json()["mcp_servers"]) == 3

        # Delete one
        server_to_delete = list_response.json()["mcp_servers"][0]["id"]
        client.delete(f"/api/mcp-servers/{server_to_delete}")

        # List should have 2
        list_response = client.get("/api/mcp-servers")
        assert len(list_response.json()["mcp_servers"]) == 2

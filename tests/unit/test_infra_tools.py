"""Comprehensive tool-layer tests for the 9 infra tools in ``daemon/tools/infra.py``.

These tests exercise the LangChain tools produced by
:func:`daemon.tools.infra.create_infra_tools` against a real in-memory
SQLite-backed :class:`SQLModelInfraRepository`. The repository layer is
already covered by ``tests/repositories/infra/`` (124+ tests); here we
test the TOOL LAYER — the actual functions agents call.

Test classes
------------

* :class:`TestInfraAssetCreateTool` — create asset + audit fields + errors.
* :class:`TestInfraAssetGetTool` — fetch by id + not-found + project isolation.
* :class:`TestInfraAssetListTool` — list + filters + pagination + isolation.
* :class:`TestInfraAssetSearchTool` — search by name/type/attributes.
* :class:`TestInfraAssetUpdateTool` — update fields + audit + isolation + errors.
* :class:`TestInfraAssetDeleteTool` — delete + history + isolation + errors.
* :class:`TestInfraTypeRegisterTool` — register/upsert + invalid params.
* :class:`TestInfraTypeListTool` — list types + bootstrap integration.
* :class:`TestInfraHistoryGetTool` — created/updated/deleted history + isolation.
* :class:`TestProjectIsolation` — cross-cutting project isolation for all tools.

Conventions
-----------

* Use ``pytest`` + ``pytest-asyncio`` (asyncio_mode = "auto").
* The engine uses ``StaticPool`` so the in-memory SQLite database is shared
  across threads (mirrors production engine in ``daemon/repositories/factory.py``).
* FK enforcement is enabled via a ``connect`` event listener — without it,
  SQLite silently ignores FK constraints and project isolation tests pass
  for the wrong reason.
* Tools are invoked via ``await tool.ainvoke({...})`` (the async entrypoint
  exposed by LangChain's ``@tool`` decorator).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine


# =============================================================================
# Fixtures
# =============================================================================


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """Enable FK enforcement on every new SQLite connection.

    SQLite disables foreign keys by default. Without this the ``ON DELETE
    SET NULL`` on ``parent_asset_id`` and the FK to ``projects`` are silently
    ignored, and project-isolation tests pass for the wrong reason.
    """

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def engine():
    """In-memory SQLite engine with all infra + projects tables created.

    Uses ``StaticPool`` so the in-memory database survives across threads,
    mirroring the production engine setup in ``daemon/repositories/factory.py``.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _enable_sqlite_foreign_keys(engine)

    # Importing the infra models registers them on SQLModel.metadata.
    # The Project table is also registered via the daemon import chain.
    from daemon.repositories.infra.models import (
        InfraAsset,
        InfraAssetHistory,
        InfraAssetType,
    )

    _ = (InfraAsset, InfraAssetHistory, InfraAssetType)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def infra_repository(engine):
    """A :class:`SQLModelInfraRepository` bound to the test engine."""
    from daemon.repositories.infra import SQLModelInfraRepository

    return SQLModelInfraRepository(engine)


@pytest.fixture
def project_id() -> str:
    """Default project_id used by most tests."""
    return "test-project"


@pytest.fixture
def other_project_id() -> str:
    """Second project_id used by isolation tests."""
    return "other-project"


@pytest.fixture
def seed_projects(engine, project_id, other_project_id):
    """Insert two project rows so the FK is satisfied for cross-project tests.

    Returns a dict with both IDs.
    """
    from daemon.repositories.project.models import Project

    with Session(engine) as session:
        session.add(
            Project(
                project_id=project_id,
                name="Test Project",
                project_type="general",
            )
        )
        session.add(
            Project(
                project_id=other_project_id,
                name="Other Project",
                project_type="general",
            )
        )
        session.commit()
    return {
        "project_id": project_id,
        "other_project_id": other_project_id,
    }


@pytest.fixture
def mock_manager(infra_repository):
    """A minimal mock manager exposing ``infra_repository``.

    The factory ``create_infra_tools`` accepts ``manager`` for parity with
    other factories but does not use it in the tool bodies. A MagicMock
    suffices.
    """
    from unittest.mock import MagicMock

    manager = MagicMock()
    manager.infra_repository = infra_repository
    return manager


@pytest.fixture
def infra_tools(mock_manager, infra_repository):
    """Build the 9 LangChain tools and return them indexed by name.

    Tools are produced by the real ``create_infra_tools`` factory against
    the real in-memory SQLite repository.
    """
    from daemon.tools.infra import create_infra_tools

    tools_list = create_infra_tools(
        mock_manager,
        "test-instance-42",
        repository=infra_repository,
    )
    return {getattr(t, "name", None): t for t in tools_list}


@pytest.fixture
def seeded_types(infra_repository):
    """Bootstrap the 9 default infra asset types into the registry."""
    infra_repository.bootstrap_default_types()


# =============================================================================
# Helper
# =============================================================================


def _parse_json_from_result(result: str) -> dict:
    """Extract the JSON block appended to a tool return string.

    The create / update / register tools return a prefix line followed by a
    JSON dump (``json.dumps(..., indent=2)``). This helper finds the first
    ``{`` and parses from there.
    """
    idx = result.find("{")
    if idx == -1:
        raise ValueError(f"No JSON found in result: {result!r}")
    return json.loads(result[idx:])


# =============================================================================
# Group 1: infra_asset_create
# =============================================================================


class TestInfraAssetCreateTool:
    """Tests for the ``infra_asset_create`` tool."""

    async def test_create_minimal(
        self, infra_tools, infra_repository, seed_projects, project_id
    ):
        """Create asset with only required fields; defaults applied."""
        create = infra_tools["infra_asset_create"]
        result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "web-01"}
        )

        assert "Created infra asset" in result
        data = _parse_json_from_result(result)
        assert data["type"] == "server"
        assert data["name"] == "web-01"
        assert data["project_id"] == project_id
        assert data["attributes"] == {}
        assert data["relationships"] == {}
        assert data["parent_asset_id"] is None

        # Verify it's persisted.
        fetched = infra_repository.get_asset(data["id"])
        assert fetched is not None
        assert fetched.name == "web-01"

    async def test_create_full_fields(
        self, infra_tools, seed_projects, project_id
    ):
        """Create asset with attributes, parent, and relationships."""
        create = infra_tools["infra_asset_create"]

        # First create a parent.
        parent_result = await create.ainvoke(
            {
                "project_id": project_id,
                "type": "k8s_cluster",
                "name": "prod-cluster",
                "attributes": {"version": "1.30"},
            }
        )
        parent_id = _parse_json_from_result(parent_result)["id"]

        result = await create.ainvoke(
            {
                "project_id": project_id,
                "type": "k8s_node",
                "name": "worker-01",
                "attributes": {"cpu_cores": 8, "memory_gb": 32},
                "parent_asset_id": parent_id,
                "relationships": {"cluster": [parent_id]},
            }
        )

        assert "Created infra asset" in result
        data = _parse_json_from_result(result)
        assert data["parent_asset_id"] == parent_id
        assert data["attributes"]["cpu_cores"] == 8
        assert data["relationships"] == {"cluster": [parent_id]}

    async def test_created_by_auto_populated_from_instance_id(
        self, infra_tools, seed_projects, project_id
    ):
        """``created_by`` is auto-populated from the closure-captured instance_id."""
        create = infra_tools["infra_asset_create"]
        result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "audit-01"}
        )
        data = _parse_json_from_result(result)

        # The fixture uses "test-instance-42" as the instance_id.
        assert data["created_by"] == "test-instance-42"
        assert data["updated_by"] == "test-instance-42"

    async def test_create_duplicate_returns_error(
        self, infra_tools, seed_projects, project_id
    ):
        """Duplicate (project_id, type, name) returns ERROR string."""
        create = infra_tools["infra_asset_create"]
        await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "web-01"}
        )
        result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "web-01"}
        )

        assert "ERROR" in result
        assert "already exists" in result

    async def test_create_invalid_project_returns_error(
        self, infra_tools, seed_projects
    ):
        """Nonexistent project_id returns a FK violation error."""
        create = infra_tools["infra_asset_create"]
        result = await create.ainvoke(
            {
                "project_id": "nonexistent-project",
                "type": "server",
                "name": "orphan-01",
            }
        )

        assert "ERROR" in result
        assert "Invalid reference" in result


# =============================================================================
# Group 2: infra_asset_get
# =============================================================================


class TestInfraAssetGetTool:
    """Tests for the ``infra_asset_get`` tool."""

    async def test_get_existing(
        self, infra_tools, seed_projects, project_id
    ):
        """Create then get by ID returns the full asset."""
        create = infra_tools["infra_asset_create"]
        get = infra_tools["infra_asset_get"]

        create_result = await create.ainvoke(
            {
                "project_id": project_id,
                "type": "server",
                "name": "get-me",
                "attributes": {"env": "staging"},
            }
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        result = await get.ainvoke(
            {"project_id": project_id, "asset_id": asset_id}
        )

        assert "ERROR" not in result
        data = json.loads(result)
        assert data["id"] == asset_id
        assert data["name"] == "get-me"
        assert data["attributes"] == {"env": "staging"}

    async def test_get_nonexistent_returns_error(
        self, infra_tools, seed_projects, project_id
    ):
        """Get by nonexistent asset ID returns ERROR."""
        get = infra_tools["infra_asset_get"]
        result = await get.ainvoke(
            {"project_id": project_id, "asset_id": "00000000-0000-0000-0000-000000000000"}
        )

        assert "ERROR" in result
        assert "No infra asset found" in result

    async def test_get_cross_project_returns_error(
        self, infra_tools, seed_projects, project_id, other_project_id
    ):
        """Get from a different project returns not-found (isolation)."""
        create = infra_tools["infra_asset_create"]
        get = infra_tools["infra_asset_get"]

        create_result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "secret-01"}
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        result = await get.ainvoke(
            {"project_id": other_project_id, "asset_id": asset_id}
        )

        assert "ERROR" in result
        assert "No infra asset found" in result


# =============================================================================
# Group 3: infra_asset_list
# =============================================================================


class TestInfraAssetListTool:
    """Tests for the ``infra_asset_list`` tool."""

    async def test_list_empty(self, infra_tools, seed_projects, project_id):
        """List on empty project returns the 'no assets' message."""
        list_tool = infra_tools["infra_asset_list"]
        result = await list_tool.ainvoke({"project_id": project_id})

        assert "No infra assets" in result

    async def test_list_all(
        self, infra_tools, seed_projects, project_id
    ):
        """List returns all assets in the project (top-level only)."""
        create = infra_tools["infra_asset_create"]
        list_tool = infra_tools["infra_asset_list"]

        await create.ainvoke({"project_id": project_id, "type": "server", "name": "a"})
        await create.ainvoke({"project_id": project_id, "type": "server", "name": "b"})
        await create.ainvoke(
            {"project_id": project_id, "type": "datacenter", "name": "c"}
        )

        result = await list_tool.ainvoke({"project_id": project_id})

        assert "3 rows" in result
        assert "a" in result
        assert "b" in result
        assert "c" in result

    async def test_list_filter_by_type(
        self, infra_tools, seed_projects, project_id
    ):
        """Filter by type returns only matching assets."""
        create = infra_tools["infra_asset_create"]
        list_tool = infra_tools["infra_asset_list"]

        await create.ainvoke({"project_id": project_id, "type": "server", "name": "s1"})
        await create.ainvoke({"project_id": project_id, "type": "server", "name": "s2"})
        await create.ainvoke(
            {"project_id": project_id, "type": "datacenter", "name": "dc1"}
        )

        result = await list_tool.ainvoke(
            {"project_id": project_id, "type": "server"}
        )

        assert "2 rows" in result
        assert "s1" in result
        assert "s2" in result
        assert "dc1" not in result

    async def test_list_filter_by_parent(
        self, infra_tools, seed_projects, project_id
    ):
        """Filter by parent_asset_id returns only children."""
        create = infra_tools["infra_asset_create"]
        list_tool = infra_tools["infra_asset_list"]

        parent_result = await create.ainvoke(
            {"project_id": project_id, "type": "k8s_cluster", "name": "cluster"}
        )
        parent_id = _parse_json_from_result(parent_result)["id"]

        await create.ainvoke(
            {"project_id": project_id, "type": "k8s_node", "name": "child-1",
             "parent_asset_id": parent_id}
        )
        await create.ainvoke(
            {"project_id": project_id, "type": "k8s_node", "name": "child-2",
             "parent_asset_id": parent_id}
        )
        # Unparented asset should NOT appear in parent-filtered list.
        await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "orphan"}
        )

        result = await list_tool.ainvoke(
            {"project_id": project_id, "parent_asset_id": parent_id}
        )

        assert "2 rows" in result
        assert "child-1" in result
        assert "child-2" in result
        assert "orphan" not in result

    async def test_list_default_returns_only_unparented(
        self, infra_tools, seed_projects, project_id
    ):
        """Default parent_asset_id=None returns ONLY unparented assets."""
        create = infra_tools["infra_asset_create"]
        list_tool = infra_tools["infra_asset_list"]

        parent_result = await create.ainvoke(
            {"project_id": project_id, "type": "k8s_cluster", "name": "root"}
        )
        parent_id = _parse_json_from_result(parent_result)["id"]

        await create.ainvoke(
            {"project_id": project_id, "type": "k8s_node", "name": "child",
             "parent_asset_id": parent_id}
        )
        await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "top-level"}
        )

        result = await list_tool.ainvoke({"project_id": project_id})

        # Default: only unparented (root + top-level), NOT child.
        assert "2 rows" in result
        assert "root" in result
        assert "top-level" in result
        assert "child" not in result

    async def test_list_pagination(
        self, infra_tools, seed_projects, project_id
    ):
        """Limit/offset pagination works."""
        create = infra_tools["infra_asset_create"]
        list_tool = infra_tools["infra_asset_list"]

        for i in range(5):
            await create.ainvoke(
                {"project_id": project_id, "type": "server", "name": f"srv-{i}"}
            )

        page1 = await list_tool.ainvoke(
            {"project_id": project_id, "limit": 2, "offset": 0}
        )
        page2 = await list_tool.ainvoke(
            {"project_id": project_id, "limit": 2, "offset": 2}
        )

        assert "2 rows" in page1
        assert "2 rows" in page2

    async def test_list_excludes_other_projects(
        self, infra_tools, seed_projects, project_id, other_project_id
    ):
        """Assets from other projects are not visible."""
        create = infra_tools["infra_asset_create"]
        list_tool = infra_tools["infra_asset_list"]

        await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "mine"}
        )
        await create.ainvoke(
            {"project_id": other_project_id, "type": "server", "name": "theirs"}
        )

        result = await list_tool.ainvoke({"project_id": project_id})

        assert "mine" in result
        assert "theirs" not in result


# =============================================================================
# Group 4: infra_asset_search
# =============================================================================


class TestInfraAssetSearchTool:
    """Tests for the ``infra_asset_search`` tool."""

    async def test_search_by_name_substring(
        self, infra_tools, seed_projects, project_id
    ):
        """Search by name substring returns matching assets."""
        create = infra_tools["infra_asset_create"]
        search = infra_tools["infra_asset_search"]

        await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "web-prod-01"}
        )
        await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "web-staging-01"}
        )
        await create.ainvoke(
            {"project_id": project_id, "type": "datacenter", "name": "dc-eu"}
        )

        result = await search.ainvoke(
            {"project_id": project_id, "query": "prod"}
        )

        assert "1 rows" in result
        assert "web-prod-01" in result

    async def test_search_by_name_substring_case_insensitive(
        self, infra_tools, seed_projects, project_id
    ):
        """Search is case-insensitive."""
        create = infra_tools["infra_asset_create"]
        search = infra_tools["infra_asset_search"]

        await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "MyServer"}
        )

        result = await search.ainvoke(
            {"project_id": project_id, "query": "myserver"}
        )

        assert "1 rows" in result
        assert "MyServer" in result

    async def test_search_filter_by_type(
        self, infra_tools, seed_projects, project_id
    ):
        """Search with type filter narrows results."""
        create = infra_tools["infra_asset_create"]
        search = infra_tools["infra_asset_search"]

        await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "shared-01"}
        )
        await create.ainvoke(
            {"project_id": project_id, "type": "datacenter", "name": "shared-02"}
        )

        result = await search.ainvoke(
            {"project_id": project_id, "query": "shared", "type": "server"}
        )

        assert "1 rows" in result
        assert "shared-01" in result
        assert "shared-02" not in result

    async def test_search_no_results(
        self, infra_tools, seed_projects, project_id
    ):
        """Search with no matches returns 'no assets' message."""
        create = infra_tools["infra_asset_create"]
        search = infra_tools["infra_asset_search"]

        await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "web-01"}
        )

        result = await search.ainvoke(
            {"project_id": project_id, "query": "nonexistent"}
        )

        assert "No infra assets match" in result

    async def test_search_project_isolation(
        self, infra_tools, seed_projects, project_id, other_project_id
    ):
        """Search does not leak assets from other projects."""
        create = infra_tools["infra_asset_create"]
        search = infra_tools["infra_asset_search"]

        await create.ainvoke(
            {"project_id": other_project_id, "type": "server", "name": "hidden-01"}
        )

        result = await search.ainvoke(
            {"project_id": project_id, "query": "hidden"}
        )

        assert "No infra assets match" in result


# =============================================================================
# Group 5: infra_asset_update
# =============================================================================


class TestInfraAssetUpdateTool:
    """Tests for the ``infra_asset_update`` tool."""

    async def test_update_name(
        self, infra_tools, seed_projects, project_id
    ):
        """Update name persists and returns the updated asset."""
        create = infra_tools["infra_asset_create"]
        update = infra_tools["infra_asset_update"]

        create_result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "old-name"}
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        result = await update.ainvoke(
            {
                "project_id": project_id,
                "asset_id": asset_id,
                "name": "new-name",
            }
        )

        assert "Updated infra asset" in result
        data = _parse_json_from_result(result)
        assert data["name"] == "new-name"

    async def test_update_attributes(
        self, infra_tools, seed_projects, project_id
    ):
        """Update attributes replaces the entire attributes dict."""
        create = infra_tools["infra_asset_create"]
        update = infra_tools["infra_asset_update"]

        create_result = await create.ainvoke(
            {
                "project_id": project_id,
                "type": "server",
                "name": "attr-srv",
                "attributes": {"env": "staging", "region": "us-east"},
            }
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        result = await update.ainvoke(
            {
                "project_id": project_id,
                "asset_id": asset_id,
                "attributes": {"env": "production"},
            }
        )

        data = _parse_json_from_result(result)
        # The tool replaces (not merges) the whole dict.
        assert data["attributes"] == {"env": "production"}

    async def test_updated_by_auto_populated(
        self, infra_tools, seed_projects, project_id
    ):
        """``updated_by`` is auto-populated from the instance_id."""
        create = infra_tools["infra_asset_create"]
        update = infra_tools["infra_asset_update"]

        create_result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "audit-srv"}
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        result = await update.ainvoke(
            {
                "project_id": project_id,
                "asset_id": asset_id,
                "name": "audit-srv-renamed",
            }
        )

        data = _parse_json_from_result(result)
        assert data["updated_by"] == "test-instance-42"

    async def test_update_nonexistent_returns_error(
        self, infra_tools, seed_projects, project_id
    ):
        """Update on nonexistent ID returns ERROR."""
        update = infra_tools["infra_asset_update"]
        result = await update.ainvoke(
            {
                "project_id": project_id,
                "asset_id": "00000000-0000-0000-0000-000000000000",
                "name": "whatever",
            }
        )

        assert "ERROR" in result
        assert "No infra asset found" in result

    async def test_update_no_fields_returns_error(
        self, infra_tools, seed_projects, project_id
    ):
        """Update with no fields returns an ERROR."""
        create = infra_tools["infra_asset_create"]
        update = infra_tools["infra_asset_update"]

        create_result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "no-change"}
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        result = await update.ainvoke(
            {"project_id": project_id, "asset_id": asset_id}
        )

        assert "ERROR" in result
        assert "No update fields" in result

    async def test_update_cross_project_returns_error(
        self, infra_tools, seed_projects, project_id, other_project_id
    ):
        """Update from a different project returns not-found (isolation)."""
        create = infra_tools["infra_asset_create"]
        update = infra_tools["infra_asset_update"]

        create_result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "protected"}
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        result = await update.ainvoke(
            {
                "project_id": other_project_id,
                "asset_id": asset_id,
                "name": "hacked",
            }
        )

        assert "ERROR" in result
        assert "No infra asset found" in result

    async def test_update_duplicate_name_returns_error(
        self, infra_tools, seed_projects, project_id
    ):
        """Updating to a duplicate name violates UNIQUE constraint."""
        create = infra_tools["infra_asset_create"]
        update = infra_tools["infra_asset_update"]

        await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "existing"}
        )
        create_result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "to-rename"}
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        result = await update.ainvoke(
            {
                "project_id": project_id,
                "asset_id": asset_id,
                "name": "existing",
            }
        )

        assert "ERROR" in result


# =============================================================================
# Group 6: infra_asset_delete
# =============================================================================


class TestInfraAssetDeleteTool:
    """Tests for the ``infra_asset_delete`` tool."""

    async def test_delete_existing(
        self, infra_tools, infra_repository, seed_projects, project_id
    ):
        """Delete removes the asset and returns confirmation."""
        create = infra_tools["infra_asset_create"]
        delete = infra_tools["infra_asset_delete"]
        get = infra_tools["infra_asset_get"]

        create_result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "delete-me"}
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        result = await delete.ainvoke(
            {"project_id": project_id, "asset_id": asset_id}
        )

        assert "Deleted infra asset" in result
        # Verify gone from get.
        get_result = await get.ainvoke(
            {"project_id": project_id, "asset_id": asset_id}
        )
        assert "ERROR" in get_result

    async def test_delete_creates_history(
        self, infra_tools, infra_repository, seed_projects, project_id
    ):
        """Delete records a 'deleted' history entry."""
        create = infra_tools["infra_asset_create"]
        delete = infra_tools["infra_asset_delete"]

        create_result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "hist-delete"}
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        await delete.ainvoke(
            {"project_id": project_id, "asset_id": asset_id}
        )

        history = infra_repository.get_history(asset_id, project_id=project_id)
        change_types = [h.change_type for h in history]
        assert "deleted" in change_types

    async def test_delete_nonexistent(
        self, infra_tools, seed_projects, project_id
    ):
        """Delete on nonexistent ID returns the 'no asset' message."""
        delete = infra_tools["infra_asset_delete"]
        result = await delete.ainvoke(
            {
                "project_id": project_id,
                "asset_id": "00000000-0000-0000-0000-000000000000",
            }
        )

        # Note: delete uses "No infra asset ... to delete" not "ERROR".
        assert "No infra asset" in result
        assert "to delete" in result

    async def test_delete_cross_project_fails(
        self, infra_tools, seed_projects, project_id, other_project_id
    ):
        """Delete from a different project returns 'no asset' (isolation)."""
        create = infra_tools["infra_asset_create"]
        delete = infra_tools["infra_asset_delete"]

        create_result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "safe"}
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        result = await delete.ainvoke(
            {"project_id": other_project_id, "asset_id": asset_id}
        )

        assert "No infra asset" in result
        assert "to delete" in result


# =============================================================================
# Group 7: infra_type_register
# =============================================================================


class TestInfraTypeRegisterTool:
    """Tests for the ``infra_type_register`` tool (GLOBAL scope)."""

    async def test_register_new_type(self, infra_tools, infra_repository):
        """Register a new custom type → stored and returned."""
        register = infra_tools["infra_type_register"]
        result = await register.ainvoke(
            {
                "name": "vm",
                "description": "A virtual machine",
                "schema_def": {
                    "type": "object",
                    "properties": {"cpu": {"type": "integer"}},
                },
            }
        )

        assert "Registered infra type" in result
        data = _parse_json_from_result(result)
        assert data["name"] == "vm"
        assert data["description"] == "A virtual machine"
        assert data["schema_json"]["properties"]["cpu"]["type"] == "integer"

        # Verify persisted.
        t = infra_repository.get_type("vm")
        assert t is not None
        assert t.description == "A virtual machine"

    async def test_register_upsert_updates_existing(self, infra_tools):
        """Registering the same name again updates the row (upsert)."""
        register = infra_tools["infra_type_register"]

        await register.ainvoke(
            {"name": "vm", "description": "original", "schema_def": {"v": 1}}
        )
        result = await register.ainvoke(
            {"name": "vm", "description": "updated", "schema_def": {"v": 2}}
        )

        data = _parse_json_from_result(result)
        assert data["description"] == "updated"
        assert data["schema_json"] == {"v": 2}

    async def test_register_defaults(
        self, infra_tools, infra_repository
    ):
        """Register with minimal params applies defaults."""
        register = infra_tools["infra_type_register"]
        result = await register.ainvoke({"name": "minimal"})

        data = _parse_json_from_result(result)
        assert data["name"] == "minimal"
        assert data["description"] == ""
        assert data["schema_json"] == {}


# =============================================================================
# Group 8: infra_type_list
# =============================================================================


class TestInfraTypeListTool:
    """Tests for the ``infra_type_list`` tool (GLOBAL scope)."""

    async def test_list_empty(self, infra_tools):
        """List with no registered types returns the empty message."""
        list_tool = infra_tools["infra_type_list"]
        result = await list_tool.ainvoke({})

        assert "No infra asset types registered" in result

    async def test_list_shows_bootstrapped_types(
        self, infra_tools, seeded_types
    ):
        """After bootstrap, list shows all 9 default types."""
        list_tool = infra_tools["infra_type_list"]
        result = await list_tool.ainvoke({})

        expected_names = {
            "datacenter",
            "server",
            "rack",
            "k8s_cluster",
            "k8s_node",
            "network",
            "load_balancer",
            "database",
            "storage",
        }
        for name in expected_names:
            assert name in result, f"Expected type {name!r} in list output"
        assert "9" in result  # count

    async def test_list_includes_custom_type(
        self, infra_tools, seeded_types
    ):
        """After registering a custom type, it appears in the list."""
        register = infra_tools["infra_type_register"]
        list_tool = infra_tools["infra_type_list"]

        await register.ainvoke(
            {"name": "custom-vm", "description": "my vm type"}
        )

        result = await list_tool.ainvoke({})

        assert "custom-vm" in result
        assert "my vm type" in result


# =============================================================================
# Group 9: infra_history_get
# =============================================================================


class TestInfraHistoryGetTool:
    """Tests for the ``infra_history_get`` tool."""

    async def test_history_for_created_asset(
        self, infra_tools, seed_projects, project_id
    ):
        """Creating an asset writes a 'created' history row."""
        create = infra_tools["infra_asset_create"]
        history_get = infra_tools["infra_history_get"]

        create_result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "hist-create"}
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        result = await history_get.ainvoke(
            {"project_id": project_id, "asset_id": asset_id}
        )

        assert "1 entries" in result
        assert "created" in result

    async def test_history_for_updated_asset(
        self, infra_tools, seed_projects, project_id
    ):
        """Updating an asset appends an 'updated' history row."""
        create = infra_tools["infra_asset_create"]
        update = infra_tools["infra_asset_update"]
        history_get = infra_tools["infra_history_get"]

        create_result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "hist-update"}
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        await update.ainvoke(
            {"project_id": project_id, "asset_id": asset_id, "name": "hist-updated"}
        )

        result = await history_get.ainvoke(
            {"project_id": project_id, "asset_id": asset_id}
        )

        assert "2 entries" in result
        assert "updated" in result

    async def test_history_for_deleted_asset(
        self, infra_tools, seed_projects, project_id
    ):
        """Deleting an asset appends a 'deleted' history row."""
        create = infra_tools["infra_asset_create"]
        delete = infra_tools["infra_asset_delete"]
        history_get = infra_tools["infra_history_get"]

        create_result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "hist-delete"}
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        await delete.ainvoke(
            {"project_id": project_id, "asset_id": asset_id}
        )

        result = await history_get.ainvoke(
            {"project_id": project_id, "asset_id": asset_id}
        )

        # The deleted history row is still retrievable via snapshot fallback.
        assert "deleted" in result

    async def test_history_chronological_order(
        self, infra_tools, seed_projects, project_id
    ):
        """History is returned newest-first (timestamp descending)."""
        create = infra_tools["infra_asset_create"]
        update = infra_tools["infra_asset_update"]
        history_get = infra_tools["infra_history_get"]

        create_result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "chrono"}
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        await update.ainvoke(
            {"project_id": project_id, "asset_id": asset_id, "name": "chrono-1"}
        )

        result = await history_get.ainvoke(
            {"project_id": project_id, "asset_id": asset_id}
        )

        # Newest first: 'updated' should appear before 'created'.
        updated_pos = result.find("updated")
        created_pos = result.find("created")
        assert updated_pos != -1
        assert created_pos != -1
        assert updated_pos < created_pos

    async def test_history_nonexistent_asset(
        self, infra_tools, seed_projects, project_id
    ):
        """History for a nonexistent asset returns the empty message."""
        history_get = infra_tools["infra_history_get"]
        result = await history_get.ainvoke(
            {
                "project_id": project_id,
                "asset_id": "00000000-0000-0000-0000-000000000000",
            }
        )

        assert "No history found" in result

    async def test_history_project_isolation(
        self, infra_tools, seed_projects, project_id, other_project_id
    ):
        """History rows from project B are not visible to project A."""
        create = infra_tools["infra_asset_create"]
        history_get = infra_tools["infra_history_get"]

        create_result = await create.ainvoke(
            {"project_id": project_id, "type": "server", "name": "iso"}
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        # Query from other_project_id.
        result = await history_get.ainvoke(
            {"project_id": other_project_id, "asset_id": asset_id}
        )

        assert "No history found" in result


# =============================================================================
# Group 10: Cross-cutting Project Isolation
# =============================================================================


class TestProjectIsolation:
    """Verify that NO project-scoped tool leaks data across projects.

    This is a cross-cutting test that creates an asset in project A and
    verifies every project-scoped tool, when queried with project B's
    project_id, returns an empty / not-found result.
    """

    async def test_full_isolation_suite(
        self,
        infra_tools,
        infra_repository,
        seed_projects,
        project_id,
        other_project_id,
    ):
        """Create in project A, verify project B sees nothing."""
        create = infra_tools["infra_asset_create"]
        get = infra_tools["infra_asset_get"]
        list_tool = infra_tools["infra_asset_list"]
        search = infra_tools["infra_asset_search"]
        update = infra_tools["infra_asset_update"]
        delete = infra_tools["infra_asset_delete"]
        history_get = infra_tools["infra_history_get"]

        # Create the asset in project A.
        create_result = await create.ainvoke(
            {
                "project_id": project_id,
                "type": "server",
                "name": "project-a-asset",
                "attributes": {"secret": "classified"},
            }
        )
        asset_id = _parse_json_from_result(create_result)["id"]

        # 1. get from project B → ERROR not found.
        result = await get.ainvoke(
            {"project_id": other_project_id, "asset_id": asset_id}
        )
        assert "ERROR" in result
        assert "No infra asset found" in result

        # 2. list from project B → does not contain the asset.
        result = await list_tool.ainvoke({"project_id": other_project_id})
        assert "project-a-asset" not in result

        # 3. search from project B → no results.
        result = await search.ainvoke(
            {"project_id": other_project_id, "query": "project-a"}
        )
        assert "No infra assets match" in result

        # 4. update from project B → ERROR not found (no mutation).
        result = await update.ainvoke(
            {
                "project_id": other_project_id,
                "asset_id": asset_id,
                "name": "hacked",
            }
        )
        assert "ERROR" in result

        # Verify the asset was NOT renamed.
        unchanged = infra_repository.get_asset(asset_id, project_id=project_id)
        assert unchanged is not None
        assert unchanged.name == "project-a-asset"

        # 5. delete from project B → no asset to delete (no mutation).
        result = await delete.ainvoke(
            {"project_id": other_project_id, "asset_id": asset_id}
        )
        assert "No infra asset" in result

        # Verify the asset was NOT deleted.
        still_exists = infra_repository.get_asset(asset_id, project_id=project_id)
        assert still_exists is not None

        # 6. history_get from project B → no history found.
        result = await history_get.ainvoke(
            {"project_id": other_project_id, "asset_id": asset_id}
        )
        assert "No history found" in result


# =============================================================================
# Group 11: Factory / wiring sanity checks
# =============================================================================


class TestInfraToolsFactory:
    """Sanity checks on the factory itself."""

    def test_factory_returns_9_tools(self, mock_manager, infra_repository):
        """``create_infra_tools`` returns exactly 9 tools."""
        from daemon.tools.infra import create_infra_tools

        tools = create_infra_tools(
            mock_manager,
            "test-instance",
            repository=infra_repository,
        )
        assert len(tools) == 9

    def test_factory_tool_names(
        self, mock_manager, infra_repository
    ):
        """All 9 expected tool names are present."""
        from daemon.tools.infra import create_infra_tools

        tools = create_infra_tools(
            mock_manager,
            "test-instance",
            repository=infra_repository,
        )
        names = {getattr(t, "name", None) for t in tools}
        expected = {
            "infra_asset_create",
            "infra_asset_get",
            "infra_asset_list",
            "infra_asset_search",
            "infra_asset_update",
            "infra_asset_delete",
            "infra_type_register",
            "infra_type_list",
            "infra_history_get",
        }
        assert names == expected

    def test_tools_have_tool_category_attribute(
        self, mock_manager, infra_repository
    ):
        """Each tool has ``_tool_category == "infra"``."""
        from daemon.tools.infra import create_infra_tools

        tools = create_infra_tools(
            mock_manager,
            "test-instance",
            repository=infra_repository,
        )
        for t in tools:
            assert getattr(t, "_tool_category", None) == "infra"

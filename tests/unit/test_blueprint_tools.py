"""Unit tests for project blueprint tools: search, get, list, create, update.

Tests tool logic (authorization, routing to repo vs matcher, formatting) with
mocked manager / repo / matcher instances. The tools are created via
``create_blueprint_tools(manager, instance_id, agent_id)`` and invoked through
LangChain's ``@tool`` async entrypoint (``await tool.ainvoke({...})``).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from daemon.tools.blueprint import create_blueprint_tools


# =============================================================================
# Helpers / Fixtures
# =============================================================================


def _make_blueprint(**overrides):
    """Build a lightweight mock Blueprint-like object."""
    defaults = {
        "id": "bp-001",
        "project_id": "proj-123",
        "slug": "core",
        "name": "Core Blueprint",
        "kind": "core",
        "content": "# Core\n\nConvention A.",
        "status": "published",
        "tags": [{"name": "convention"}],
        "file_refs": ["src/main.py"],
        "version": 1,
        "embedding_model": None,
        "source": "auto",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "last_reviewed_at": None,
        "is_active": True,
    }
    defaults.update(overrides)
    bp = MagicMock()
    for k, v in defaults.items():
        setattr(bp, k, v)
    return bp


def _make_matched(**overrides):
    """Build a lightweight MatchedBlueprint-like object."""
    from daemon.services.blueprint_matcher import MatchedBlueprint

    defaults = {
        "id": "bp-001",
        "name": "Core Blueprint",
        "kind": "core",
        "version": 1,
        "content": "# Core",
        "file_refs": [],
        "score": 1.0,
    }
    defaults.update(overrides)
    return MatchedBlueprint(**defaults)


def make_mock_manager(*, project_id: str = "proj-123"):
    """Create a mock manager exposing _blueprint_repo, _blueprint_matcher, _instance_repository."""
    manager = MagicMock()

    # _instance_repository.get(current_instance_id) -> meta with project_id
    meta = MagicMock()
    meta.project_id = project_id
    manager._instance_repository.get.return_value = meta

    # _blueprint_repo — sync methods
    repo = MagicMock()
    repo.get_by_id.return_value = _make_blueprint()
    repo.get_by_slug.return_value = _make_blueprint(slug="area-x", name="Area X", kind="area")
    repo.list_by_project.return_value = [_make_blueprint(), _make_blueprint(id="bp-002", slug="area-x", name="Area X", kind="area")]
    repo.create.return_value = _make_blueprint(id="bp-new", slug="new-bp", name="New BP", kind="area", content="# New")
    repo.update.return_value = _make_blueprint(content="# Updated")
    manager._blueprint_repo = repo

    # _blueprint_matcher — async match()
    from unittest.mock import AsyncMock

    matcher = MagicMock()
    matcher.match = AsyncMock(return_value=[_make_matched()])
    manager._blueprint_matcher = matcher

    return manager


def _get_tool(tools, name):
    """Find a tool by name in the factory output list."""
    for t in tools:
        if t.name == name:
            return t
    raise ValueError(f"tool {name!r} not found in {[t.name for t in tools]}")


@pytest.fixture
def blueprint_tools():
    """Create blueprint tools with a blueprinter-authorized mock manager."""
    manager = make_mock_manager()
    return create_blueprint_tools(manager, current_instance_id="inst-001", agent_id="blueprinter"), manager


@pytest.fixture
def dev_tools():
    """Create blueprint tools with a non-blueprinter (developer) agent_id."""
    manager = make_mock_manager()
    return create_blueprint_tools(manager, current_instance_id="inst-001", agent_id="developer"), manager


# =============================================================================
# blueprint_search
# =============================================================================


class TestBlueprintSearch:
    """Tests for blueprint_search — routes to the matcher."""

    async def test_search_calls_matcher_with_project_id(self, blueprint_tools):
        tools, manager = blueprint_tools
        search = _get_tool(tools, "blueprint_search")

        result = await search.ainvoke({"query": "conventions"})

        manager._blueprint_matcher.match.assert_awaited_once()
        call_kwargs = manager._blueprint_matcher.match.call_args
        assert call_kwargs.kwargs["project_id"] == "proj-123"
        assert call_kwargs.kwargs["query"] == "conventions"
        assert "Core Blueprint" in result
        assert "core" in result

    async def test_search_no_project_id(self, blueprint_tools):
        tools, manager = blueprint_tools
        # Remove project context
        manager._instance_repository.get.return_value = MagicMock(project_id=None)
        search = _get_tool(tools, "blueprint_search")

        result = await search.ainvoke({"query": "conventions"})

        assert "project_id not available" in result.lower()
        manager._blueprint_matcher.match.assert_not_awaited()

    async def test_search_explicit_project_id(self, blueprint_tools):
        tools, manager = blueprint_tools
        search = _get_tool(tools, "blueprint_search")

        await search.ainvoke({"query": "test", "project_id": "explicit-pid"})

        call_kwargs = manager._blueprint_matcher.match.call_args
        assert call_kwargs.kwargs["project_id"] == "explicit-pid"

    async def test_search_matcher_exception_graceful(self, blueprint_tools):
        tools, manager = blueprint_tools
        from unittest.mock import AsyncMock
        manager._blueprint_matcher.match = AsyncMock(side_effect=RuntimeError("boom"))
        search = _get_tool(tools, "blueprint_search")

        result = await search.ainvoke({"query": "test"})

        assert "error" in result.lower()


# =============================================================================
# blueprint_get
# =============================================================================


class TestBlueprintGet:
    """Tests for blueprint_get — by ID and by slug."""

    async def test_get_by_id_calls_get_by_id(self, blueprint_tools):
        tools, manager = blueprint_tools
        get = _get_tool(tools, "blueprint_get")

        result = await get.ainvoke({"blueprint_id": "bp-001"})

        manager._blueprint_repo.get_by_id.assert_called_once_with("bp-001")
        assert "Core Blueprint" in result
        assert "bp-001" in result

    async def test_get_by_slug_calls_get_by_slug(self, blueprint_tools):
        tools, manager = blueprint_tools
        get = _get_tool(tools, "blueprint_get")

        result = await get.ainvoke({"slug": "area-x"})

        manager._blueprint_repo.get_by_slug.assert_called_once_with("proj-123", "area-x")

    async def test_get_neither_id_nor_slug(self, blueprint_tools):
        tools, manager = blueprint_tools
        get = _get_tool(tools, "blueprint_get")

        result = await get.ainvoke({})

        assert "error" in result.lower()

    async def test_get_not_found(self, blueprint_tools):
        tools, manager = blueprint_tools
        manager._blueprint_repo.get_by_id.return_value = None
        get = _get_tool(tools, "blueprint_get")

        result = await get.ainvoke({"blueprint_id": "missing"})

        assert "not found" in result.lower()


# =============================================================================
# blueprint_list
# =============================================================================


class TestBlueprintList:
    """Tests for blueprint_list — routes to repo.list_by_project."""

    async def test_list_calls_list_by_project(self, blueprint_tools):
        tools, manager = blueprint_tools
        lst = _get_tool(tools, "blueprint_list")

        result = await lst.ainvoke({})

        manager._blueprint_repo.list_by_project.assert_called_once()
        call_args = manager._blueprint_repo.list_by_project.call_args
        assert call_args.args[0] == "proj-123"
        assert "Found 2 blueprint" in result

    async def test_list_with_kind_filter(self, blueprint_tools):
        tools, manager = blueprint_tools
        lst = _get_tool(tools, "blueprint_list")

        await lst.ainvoke({"kind": "area"})

        call_args = manager._blueprint_repo.list_by_project.call_args
        assert call_args.kwargs.get("kind") == "area"

    async def test_list_no_project_id(self, blueprint_tools):
        tools, manager = blueprint_tools
        manager._instance_repository.get.return_value = MagicMock(project_id=None)
        lst = _get_tool(tools, "blueprint_list")

        result = await lst.ainvoke({})

        assert "project_id not available" in result.lower()
        manager._blueprint_repo.list_by_project.assert_not_called()


# =============================================================================
# blueprint_create
# =============================================================================


class TestBlueprintCreate:
    """Tests for blueprint_create — authorization-gated."""

    async def test_create_authorized_blueprinter(self, blueprint_tools):
        tools, manager = blueprint_tools
        create = _get_tool(tools, "blueprint_create")

        result = await create.ainvoke({
            "slug": "new-bp",
            "name": "New BP",
            "kind": "area",
            "content": "# New",
        })

        manager._blueprint_repo.create.assert_called_once()
        call_kwargs = manager._blueprint_repo.create.call_args.kwargs
        assert call_kwargs["project_id"] == "proj-123"
        assert call_kwargs["slug"] == "new-bp"
        assert call_kwargs["kind"] == "area"
        assert "created successfully" in result.lower()
        assert "bp-new" in result

    async def test_create_denied_non_blueprinter(self, dev_tools):
        tools, manager = dev_tools
        create = _get_tool(tools, "blueprint_create")

        result = await create.ainvoke({
            "slug": "new-bp",
            "name": "New BP",
            "kind": "area",
            "content": "# New",
        })

        assert "ERROR" in result
        assert "blueprinter" in result
        manager._blueprint_repo.create.assert_not_called()

    async def test_create_denied_no_project_id(self, blueprint_tools):
        tools, manager = blueprint_tools
        manager._instance_repository.get.return_value = MagicMock(project_id=None)
        create = _get_tool(tools, "blueprint_create")

        result = await create.ainvoke({
            "slug": "new-bp",
            "name": "New BP",
            "kind": "area",
            "content": "# New",
        })

        assert "project_id not available" in result.lower()
        manager._blueprint_repo.create.assert_not_called()


# =============================================================================
# blueprint_update
# =============================================================================


class TestBlueprintUpdate:
    """Tests for blueprint_update — authorization-gated."""

    async def test_update_authorized_blueprinter(self, blueprint_tools):
        tools, manager = blueprint_tools
        update = _get_tool(tools, "blueprint_update")

        result = await update.ainvoke({
            "blueprint_id": "bp-001",
            "content": "# Updated",
        })

        manager._blueprint_repo.update.assert_called_once()
        call_args = manager._blueprint_repo.update.call_args
        assert call_args.args[0] == "bp-001"
        assert call_args.kwargs["content"] == "# Updated"
        assert "updated successfully" in result.lower()

    async def test_update_denied_non_blueprinter(self, dev_tools):
        tools, manager = dev_tools
        update = _get_tool(tools, "blueprint_update")

        result = await update.ainvoke({
            "blueprint_id": "bp-001",
            "content": "# Updated",
        })

        assert "ERROR" in result
        assert "blueprinter" in result
        manager._blueprint_repo.update.assert_not_called()

    async def test_update_no_fields(self, blueprint_tools):
        tools, manager = blueprint_tools
        update = _get_tool(tools, "blueprint_update")

        result = await update.ainvoke({"blueprint_id": "bp-001"})

        assert "error" in result.lower()
        manager._blueprint_repo.update.assert_not_called()

    async def test_update_not_found(self, blueprint_tools):
        tools, manager = blueprint_tools
        manager._blueprint_repo.update.return_value = None
        update = _get_tool(tools, "blueprint_update")

        result = await update.ainvoke({"blueprint_id": "missing", "content": "x"})

        assert "not found" in result.lower()

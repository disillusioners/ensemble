"""Tests for the Infrastructure Tool Category (Phase 2).

These tests exercise the 9 LangChain tools produced by
:func:`daemon.tools.infra.create_infra_tools` against a real
in-memory SQLite-backed :class:`SQLModelInfraRepository`, plus
the factory wiring (the ``"infra"`` category in the tool
registry, and the DevOps agent's ``tools.allow``).

Test classes
------------

* :class:`TestFactory` — ``create_infra_tools`` returns 9 tools
  with the expected names, and the ``"infra"`` category is
  registered in the tool registry.
* :class:`TestAssetCreate` — ``infra_asset_create`` happy path
  + JSON-return shape + audit ``created_by`` is the calling
  instance.
* :class:`TestAssetGet` — ``infra_asset_get`` happy path +
  not-found error path.
* :class:`TestAssetList` — ``infra_asset_list`` listing,
  ``type`` filter, ``parent_asset_id`` filter, and pagination.
* :class:`TestAssetSearch` — ``infra_asset_search`` substring
  match on name + ``type`` filter.
* :class:`TestAssetUpdate` — ``infra_asset_update`` field
  updates, partial updates, no-fields error.
* :class:`TestAssetDelete` — ``infra_asset_delete`` success
  + second-delete idempotent return value.
* :class:`TestTypeRegister` / :class:`TestTypeList` — global
  type-registry tools.
* :class:`TestHistoryGet` — ``infra_history_get`` returns
  ``created`` / ``updated`` / ``deleted`` entries.
* :class:`TestErrorHandling` — invalid project_id, unknown
  type, missing asset for get/update/delete.
* :class:`TestDevOpsAgentAccess` — DevOps agent's
  ``meta.json`` includes ``"infra"`` in its ``tools.allow``,
  and ``resolve_tool_filter`` expands ``"infra"`` to the 9
  tool names.

Conventions
-----------

* Use ``pytest-asyncio`` (mode=auto via ``pyproject.toml``).
* Build the tools once per test via the real
  ``create_infra_tools`` factory; the
  :class:`InstanceManager` is a plain ``MagicMock`` because
  the factory only uses it for parity — the actual persistence
  comes from the real ``SQLModelInfraRepository``.
* Call each tool via ``tool.ainvoke({...})`` and assert on the
  returned string (JSON or markdown table). Tools never raise;
  they return either a success string or ``ERROR: ...``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# =============================================================================
# Constants
# =============================================================================


# The 9 tool names registered by ``create_infra_tools``. Repeated
# as a module constant so the factory, registry, and
# resolve_tool_filter tests can assert against a single source
# of truth.
INFRA_TOOL_NAMES: frozenset[str] = frozenset({
    "infra_asset_create",
    "infra_asset_get",
    "infra_asset_list",
    "infra_asset_search",
    "infra_asset_update",
    "infra_asset_delete",
    "infra_type_register",
    "infra_type_list",
    "infra_history_get",
})


# The DevOps agent's tool allow list lives at
# ``agents/devops/meta.json`` and is the source of truth for
# which categories the agent can use. Phase 2 added ``"infra"``
# to that list.
DEVOPS_META_PATH = Path(__file__).resolve().parents[2] / "agents" / "devops" / "meta.json"


# =============================================================================
# Shared fixtures
# =============================================================================


@pytest.fixture
def infra_tools(infra_repository):
    """Build the 9 LangChain tools and return them indexed by name.

    The ``manager`` arg is a plain ``MagicMock`` because
    ``create_infra_tools`` accepts it for parity with other
    factories but does not actually call any methods on it
    (the infra repository is process-level, not
    instance-specific). ``current_instance_id`` is a fixed
    string so the audit fields are predictable.
    """
    from daemon.tools.infra import create_infra_tools

    manager = MagicMock()
    tools_list = create_infra_tools(
        manager,
        "test-instance",
        repository=infra_repository,
    )
    return {getattr(t, "name", None): t for t in tools_list}


@pytest.fixture
def bootstrap_types(infra_repository):
    """Run ``bootstrap_default_types`` to seed the 9 default types.

    Some tests don't actually need the type registry (the tool
    layer does not validate types against it), but seeding it
    keeps behavior identical to the production startup path.
    """
    infra_repository.bootstrap_default_types()


# =============================================================================
# Group 1: Factory
# =============================================================================


class TestFactory:
    """The ``create_infra_tools`` factory must return 9 tools with
    the expected names, and the ``"infra"`` category must be
    registered in the tool registry."""

    def test_factory_returns_nine_tools(self, infra_repository):
        """Factory returns exactly 9 tools."""
        from daemon.tools.infra import create_infra_tools

        tools_list = create_infra_tools(
            MagicMock(), "test-instance", repository=infra_repository
        )
        assert len(tools_list) == 9

    def test_factory_returns_expected_tool_names(self, infra_tools):
        """The 9 returned tool names match the expected set exactly."""
        assert set(infra_tools.keys()) == set(INFRA_TOOL_NAMES)

    def test_factory_accepts_mock_manager(self, infra_repository):
        """Factory accepts a MagicMock manager without crashing.

        The factory's docstring says the ``manager`` parameter is
        accepted for parity but is not used in any tool body —
        a MagicMock is therefore sufficient and must not raise.
        """
        from daemon.tools.infra import create_infra_tools

        manager = MagicMock()
        # Should not raise.
        result = create_infra_tools(
            manager, "x", repository=infra_repository
        )
        assert len(result) == 9

    def test_infra_category_registered_in_registry(self, infra_tools):
        """The ``"infra"`` category is registered with all 9 names
        after the factory has run ``scan_tools_for_full_docs``.

        This verifies the ``@register_tool_category("infra")``
        decorator on each tool function, the ``_full_doc_``
        attribute wiring, and that ``scan_tools_for_full_docs``
        correctly picks up the metadata.
        """
        from daemon.tools._tool_registry import (
            clear_registry,
            list_tools_by_category,
            scan_tools_for_full_docs,
        )

        clear_registry()
        try:
            # ``infra_tools`` is a dict (already built by the
            # fixture). Pass the underlying list to the scanner.
            scan_tools_for_full_docs(list(infra_tools.values()))
            categories = list_tools_by_category()
            assert "infra" in categories
            assert set(categories["infra"]) == set(INFRA_TOOL_NAMES)
        finally:
            clear_registry()


# =============================================================================
# Group 2: infra_asset_create
# =============================================================================


class TestAssetCreate:
    """Tests for ``infra_asset_create``."""

    @pytest.mark.asyncio
    async def test_create_returns_confirmation_and_json(
        self, infra_tools, seed_projects, project_id
    ):
        """Creating an asset returns a confirmation line plus the
        full JSON of the new asset.
        """
        create_tool = infra_tools["infra_asset_create"]
        result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "web-01",
            "attributes": {"hostname": "host-01", "cpu_cores": 8},
            "relationships": {"linked_to": ["db-01"]},
        })

        assert "Created infra asset" in result
        assert "web-01" in result
        assert "server" in result
        # The full JSON is appended after the confirmation line.
        # Extract the first JSON object and assert on its fields.
        json_blob = self._extract_json_object(result)
        data = json.loads(json_blob)
        assert data["name"] == "web-01"
        assert data["type"] == "server"
        assert data["project_id"] == project_id
        assert data["attributes"] == {"hostname": "host-01", "cpu_cores": 8}
        assert data["relationships"] == {"linked_to": ["db-01"]}
        assert data["id"]  # UUID4 assigned by the repository
        assert data["created_by"] == "test-instance"

    @pytest.mark.asyncio
    async def test_create_minimal_fields(
        self, infra_tools, seed_projects, project_id
    ):
        """Creating with only the required fields works and
        applies the default empty ``attributes`` /
        ``relationships`` dicts.
        """
        create_tool = infra_tools["infra_asset_create"]
        result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "minimal-01",
        })

        data = json.loads(self._extract_json_object(result))
        assert data["name"] == "minimal-01"
        assert data["attributes"] == {}
        assert data["relationships"] == {}
        assert data["parent_asset_id"] is None

    @pytest.mark.asyncio
    async def test_create_duplicate_returns_error(
        self, infra_tools, seed_projects, project_id
    ):
        """Creating a duplicate (project_id, type, name) returns
        an ``ERROR:`` string instead of raising.
        """
        create_tool = infra_tools["infra_asset_create"]
        # First insert succeeds.
        await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "dup-01",
        })
        # Second insert with the same key must error.
        result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "dup-01",
        })

        assert "ERROR" in result
        assert "already exists" in result
        assert "dup-01" in result

    @pytest.mark.asyncio
    async def test_create_with_parent_asset_id(
        self, infra_tools, seed_projects, project_id
    ):
        """Creating a child asset stores the parent_asset_id."""
        create_tool = infra_tools["infra_asset_create"]
        # Create the parent.
        parent_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "k8s_cluster",
            "name": "prod-cluster",
        })
        parent_data = json.loads(self._extract_json_object(parent_result))
        parent_id = parent_data["id"]

        # Create the child.
        child_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "worker-01",
            "parent_asset_id": parent_id,
        })
        child_data = json.loads(self._extract_json_object(child_result))
        assert child_data["parent_asset_id"] == parent_id

    @staticmethod
    def _extract_json_object(text: str) -> str:
        """Pull the first balanced JSON object out of a tool
        return string.

        Tool returns look like::

            Created infra asset: id=..., type=..., ....

            {"id": "...", "type": "...", ...}

        We walk forward from the first ``{`` and track the
        brace depth (and string boundaries) to find the matching
        ``}``. This avoids depending on a fixed offset or
        regex that breaks on nested braces.
        """
        start = text.find("{")
        assert start != -1, f"No JSON object found in: {text!r}"
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        raise AssertionError(f"Unbalanced JSON in tool output: {text!r}")


# =============================================================================
# Group 3: infra_asset_get
# =============================================================================


class TestAssetGet:
    """Tests for ``infra_asset_get``."""

    @pytest.mark.asyncio
    async def test_get_existing_asset_returns_json(
        self, infra_tools, seed_projects, project_id
    ):
        """Getting an existing asset returns the full JSON dict."""
        create_tool = infra_tools["infra_asset_create"]
        get_tool = infra_tools["infra_asset_get"]

        # Create an asset.
        create_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "get-target",
            "attributes": {"ip": "10.0.0.1"},
        })
        asset_id = json.loads(
            TestAssetCreate._extract_json_object(create_result)
        )["id"]

        # Fetch it.
        result = await get_tool.ainvoke({
            "project_id": project_id,
            "asset_id": asset_id,
        })
        data = json.loads(result)
        assert data["id"] == asset_id
        assert data["name"] == "get-target"
        assert data["type"] == "server"
        assert data["attributes"] == {"ip": "10.0.0.1"}

    @pytest.mark.asyncio
    async def test_get_nonexistent_asset_returns_error(
        self, infra_tools, seed_projects
    ):
        """Getting an unknown asset id returns an ``ERROR:``
        string with the id quoted.
        """
        get_tool = infra_tools["infra_asset_get"]
        result = await get_tool.ainvoke({
            "project_id": "test-project",
            "asset_id": "does-not-exist",
        })
        assert "ERROR" in result
        assert "does-not-exist" in result
        # The literal substring is "No infra asset found"; we
        # assert on "found" + "no" so the check is robust to
        # capitalization / minor wording changes.
        assert "found" in result.lower()
        assert "no" in result.lower()

    @pytest.mark.asyncio
    async def test_get_cross_project_returns_not_found(
        self, infra_tools, seed_projects, project_id, other_project_id
    ):
        """An asset that belongs to project A is invisible when
        fetched under project B (project isolation at the
        repository layer).
        """
        create_tool = infra_tools["infra_asset_create"]
        get_tool = infra_tools["infra_asset_get"]

        create_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "p1-only",
        })
        asset_id = json.loads(
            TestAssetCreate._extract_json_object(create_result)
        )["id"]

        result = await get_tool.ainvoke({
            "project_id": other_project_id,
            "asset_id": asset_id,
        })
        assert "ERROR" in result
        assert "found" in result.lower()


# =============================================================================
# Group 4: infra_asset_list
# =============================================================================


class TestAssetList:
    """Tests for ``infra_asset_list``."""

    @pytest.mark.asyncio
    async def test_list_empty_project_returns_no_assets_message(
        self, infra_tools, seed_projects, project_id
    ):
        """Listing a project with no assets returns the
        "No infra assets found" message.
        """
        list_tool = infra_tools["infra_asset_list"]
        result = await list_tool.ainvoke({"project_id": project_id})
        assert "No infra assets found" in result
        assert project_id in result

    @pytest.mark.asyncio
    async def test_list_returns_assets_in_markdown_table(
        self, infra_tools, seed_projects, project_id
    ):
        """Listing a project with assets returns a markdown table
        containing every asset name.
        """
        create_tool = infra_tools["infra_asset_create"]
        list_tool = infra_tools["infra_asset_list"]

        for name in ("a", "b", "c"):
            await create_tool.ainvoke({
                "project_id": project_id,
                "type": "server",
                "name": name,
            })

        result = await list_tool.ainvoke({"project_id": project_id})
        # Header is present.
        assert "| id | type | name |" in result
        # Each asset's name appears as a row.
        for name in ("a", "b", "c"):
            assert f"| {name} |" in result
        # Row count is announced.
        assert "(3 rows)" in result

    @pytest.mark.asyncio
    async def test_list_filter_by_type(
        self, infra_tools, seed_projects, project_id
    ):
        """``type=`` filter returns only assets of that type."""
        create_tool = infra_tools["infra_asset_create"]
        list_tool = infra_tools["infra_asset_list"]

        await create_tool.ainvoke({
            "project_id": project_id, "type": "server", "name": "s1"
        })
        await create_tool.ainvoke({
            "project_id": project_id, "type": "server", "name": "s2"
        })
        await create_tool.ainvoke({
            "project_id": project_id, "type": "datacenter", "name": "d1"
        })

        result = await list_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
        })
        assert "(2 rows)" in result
        assert "| s1 |" in result
        assert "| s2 |" in result
        # d1 must NOT appear because of the type filter.
        assert "| d1 |" not in result

    @pytest.mark.asyncio
    async def test_list_filter_by_parent(
        self, infra_tools, seed_projects, project_id
    ):
        """``parent_asset_id=`` filter returns only the children
        of that parent (not the parent itself, not orphans).
        """
        create_tool = infra_tools["infra_asset_create"]
        list_tool = infra_tools["infra_asset_list"]

        parent_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "k8s_cluster",
            "name": "cluster-a",
        })
        parent_id = json.loads(
            TestAssetCreate._extract_json_object(parent_result)
        )["id"]

        await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "child-1",
            "parent_asset_id": parent_id,
        })
        await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "child-2",
            "parent_asset_id": parent_id,
        })
        # Orphan — must NOT appear.
        await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "orphan",
        })

        result = await list_tool.ainvoke({
            "project_id": project_id,
            "parent_asset_id": parent_id,
        })
        assert "(2 rows)" in result
        assert "| child-1 |" in result
        assert "| child-2 |" in result
        # Parent and orphan must not appear.
        assert "| cluster-a |" not in result
        assert "| orphan |" not in result

    @pytest.mark.asyncio
    async def test_list_pagination(
        self, infra_tools, seed_projects, project_id
    ):
        """``limit`` and ``offset`` produce distinct pages."""
        create_tool = infra_tools["infra_asset_create"]
        list_tool = infra_tools["infra_asset_list"]

        for i in range(5):
            await create_tool.ainvoke({
                "project_id": project_id,
                "type": "server",
                "name": f"s{i}",
            })

        page1 = await list_tool.ainvoke({
            "project_id": project_id,
            "limit": 2,
            "offset": 0,
        })
        page2 = await list_tool.ainvoke({
            "project_id": project_id,
            "limit": 2,
            "offset": 2,
        })
        page3 = await list_tool.ainvoke({
            "project_id": project_id,
            "limit": 2,
            "offset": 4,
        })

        assert "(2 rows)" in page1
        assert "(2 rows)" in page2
        assert "(1 rows)" in page3


# =============================================================================
# Group 5: infra_asset_search
# =============================================================================


class TestAssetSearch:
    """Tests for ``infra_asset_search``."""

    @pytest.mark.asyncio
    async def test_search_substring_on_name(
        self, infra_tools, seed_projects, project_id
    ):
        """Search does a case-insensitive substring match on
        ``name``.
        """
        create_tool = infra_tools["infra_asset_create"]
        search_tool = infra_tools["infra_asset_search"]

        for name in ("web-prod-01", "web-staging-01", "db-prod-01"):
            await create_tool.ainvoke({
                "project_id": project_id,
                "type": "server",
                "name": name,
            })

        result = await search_tool.ainvoke({
            "project_id": project_id,
            "query": "prod",
        })
        # Two web/db-prod-* assets match; web-staging does not.
        assert "(2 rows)" in result
        assert "web-prod-01" in result
        assert "db-prod-01" in result
        assert "web-staging-01" not in result

    @pytest.mark.asyncio
    async def test_search_with_type_filter(
        self, infra_tools, seed_projects, project_id
    ):
        """``type=`` filter narrows the search results."""
        create_tool = infra_tools["infra_asset_create"]
        search_tool = infra_tools["infra_asset_search"]

        await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "prod-srv",
        })
        await create_tool.ainvoke({
            "project_id": project_id,
            "type": "datacenter",
            "name": "prod-dc",
        })

        result = await search_tool.ainvoke({
            "project_id": project_id,
            "query": "prod",
            "type": "server",
        })
        assert "(1 rows)" in result
        assert "prod-srv" in result
        # The datacenter must be excluded by the type filter.
        assert "prod-dc" not in result

    @pytest.mark.asyncio
    async def test_search_no_matches_returns_message(
        self, infra_tools, seed_projects, project_id
    ):
        """Search that matches nothing returns the "No infra
        assets match" message.
        """
        create_tool = infra_tools["infra_asset_create"]
        search_tool = infra_tools["infra_asset_search"]

        await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "lonely",
        })

        result = await search_tool.ainvoke({
            "project_id": project_id,
            "query": "definitely-not-present",
        })
        assert "No infra assets match" in result


# =============================================================================
# Group 6: infra_asset_update
# =============================================================================


class TestAssetUpdate:
    """Tests for ``infra_asset_update``."""

    @pytest.mark.asyncio
    async def test_update_attributes_and_name(
        self, infra_tools, seed_projects, project_id
    ):
        """Updating ``name`` and ``attributes`` returns the
        updated asset JSON.
        """
        create_tool = infra_tools["infra_asset_create"]
        update_tool = infra_tools["infra_asset_update"]

        create_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "old-name",
            "attributes": {"cpu_cores": 4},
        })
        asset_id = json.loads(
            TestAssetCreate._extract_json_object(create_result)
        )["id"]

        result = await update_tool.ainvoke({
            "project_id": project_id,
            "asset_id": asset_id,
            "name": "new-name",
            "attributes": {"cpu_cores": 8, "memory_gb": 32},
        })
        assert "Updated infra asset" in result
        data = json.loads(TestAssetCreate._extract_json_object(result))
        assert data["name"] == "new-name"
        assert data["attributes"] == {"cpu_cores": 8, "memory_gb": 32}
        # The ``updated_by`` audit field is the current instance id.
        assert data["updated_by"] == "test-instance"

    @pytest.mark.asyncio
    async def test_update_parent_asset_id(
        self, infra_tools, seed_projects, project_id
    ):
        """Updating ``parent_asset_id`` re-parents the asset."""
        create_tool = infra_tools["infra_asset_create"]
        update_tool = infra_tools["infra_asset_update"]

        # Create a parent and a child.
        parent_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "k8s_cluster",
            "name": "p",
        })
        new_parent_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "k8s_cluster",
            "name": "p2",
        })
        child_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "c",
            "parent_asset_id": json.loads(
                TestAssetCreate._extract_json_object(parent_result)
            )["id"],
        })
        child_id = json.loads(
            TestAssetCreate._extract_json_object(child_result)
        )["id"]
        new_parent_id = json.loads(
            TestAssetCreate._extract_json_object(new_parent_result)
        )["id"]

        result = await update_tool.ainvoke({
            "project_id": project_id,
            "asset_id": child_id,
            "parent_asset_id": new_parent_id,
        })
        data = json.loads(TestAssetCreate._extract_json_object(result))
        assert data["parent_asset_id"] == new_parent_id

    @pytest.mark.asyncio
    async def test_update_relationships(
        self, infra_tools, seed_projects, project_id
    ):
        """Updating ``relationships`` replaces the dict."""
        create_tool = infra_tools["infra_asset_create"]
        update_tool = infra_tools["infra_asset_update"]

        create_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "load_balancer",
            "name": "lb",
            "relationships": {"backends": ["s1"]},
        })
        asset_id = json.loads(
            TestAssetCreate._extract_json_object(create_result)
        )["id"]

        result = await update_tool.ainvoke({
            "project_id": project_id,
            "asset_id": asset_id,
            "relationships": {"backends": ["s1", "s2", "s3"]},
        })
        data = json.loads(TestAssetCreate._extract_json_object(result))
        assert data["relationships"] == {"backends": ["s1", "s2", "s3"]}

    @pytest.mark.asyncio
    async def test_update_no_fields_returns_error(
        self, infra_tools, seed_projects, project_id
    ):
        """Calling update with no updatable fields returns an
        ``ERROR:`` string and does NOT touch the row.
        """
        create_tool = infra_tools["infra_asset_create"]
        update_tool = infra_tools["infra_asset_update"]

        create_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "untouched",
        })
        asset_id = json.loads(
            TestAssetCreate._extract_json_object(create_result)
        )["id"]

        result = await update_tool.ainvoke({
            "project_id": project_id,
            "asset_id": asset_id,
        })
        assert "ERROR" in result
        assert "No update fields provided" in result


# =============================================================================
# Group 7: infra_asset_delete
# =============================================================================


class TestAssetDelete:
    """Tests for ``infra_asset_delete``."""

    @pytest.mark.asyncio
    async def test_delete_existing_asset_succeeds(
        self, infra_tools, seed_projects, project_id
    ):
        """Deleting an existing asset returns a confirmation
        string and the row is gone from the repository.
        """
        create_tool = infra_tools["infra_asset_create"]
        delete_tool = infra_tools["infra_asset_delete"]
        get_tool = infra_tools["infra_asset_get"]

        create_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "doomed",
        })
        asset_id = json.loads(
            TestAssetCreate._extract_json_object(create_result)
        )["id"]

        result = await delete_tool.ainvoke({
            "project_id": project_id,
            "asset_id": asset_id,
        })
        assert "Deleted infra asset" in result
        assert asset_id in result

        # Confirm the row is gone.
        post_get = await get_tool.ainvoke({
            "project_id": project_id,
            "asset_id": asset_id,
        })
        assert "ERROR" in post_get
        assert "found" in post_get.lower()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_asset(
        self, infra_tools, seed_projects
    ):
        """Deleting an unknown asset id returns a "no asset ...
        to delete" message (not a hard error).
        """
        delete_tool = infra_tools["infra_asset_delete"]
        result = await delete_tool.ainvoke({
            "project_id": "test-project",
            "asset_id": "ghost-id",
        })
        assert "ghost-id" in result
        assert "to delete" in result

    @pytest.mark.asyncio
    async def test_delete_twice_idempotent(
        self, infra_tools, seed_projects, project_id
    ):
        """A second delete on the same id is a no-op message,
        not an error. The first delete succeeded; the second
        reports the asset is no longer there.
        """
        create_tool = infra_tools["infra_asset_create"]
        delete_tool = infra_tools["infra_asset_delete"]

        create_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "twice-deleted",
        })
        asset_id = json.loads(
            TestAssetCreate._extract_json_object(create_result)
        )["id"]

        first = await delete_tool.ainvoke({
            "project_id": project_id,
            "asset_id": asset_id,
        })
        second = await delete_tool.ainvoke({
            "project_id": project_id,
            "asset_id": asset_id,
        })

        assert "Deleted" in first
        # The second call must NOT raise; it returns the
        # "no asset ... to delete" message.
        assert "to delete" in second
        assert "ERROR" not in second


# =============================================================================
# Group 8: Type registry — infra_type_register / infra_type_list
# =============================================================================


class TestTypeRegister:
    """Tests for ``infra_type_register`` (global, no project_id)."""

    @pytest.mark.asyncio
    async def test_register_new_type_returns_json(
        self, infra_tools
    ):
        """Registering a new type returns the type row JSON.

        C3 fix: the parameter is now called ``schema_def`` (not
        ``schema_json``) to avoid shadowing Pydantic's
        ``BaseModel.schema_json`` method.
        """
        register_tool = infra_tools["infra_type_register"]
        result = await register_tool.ainvoke({
            "name": "firewall",
            "description": "A network firewall appliance",
            "schema_def": {
                "type": "object",
                "properties": {"ports": {"type": "array"}},
            },
        })
        assert "Registered infra type" in result
        assert "firewall" in result
        data = json.loads(TestAssetCreate._extract_json_object(result))
        assert data["name"] == "firewall"
        assert data["description"] == "A network firewall appliance"
        # The repository stores the schema under the column name
        # ``schema_json`` (the locked DB-side name), so the JSON
        # returned to the agent uses that key.
        assert "ports" in data["schema_json"]["properties"]

    @pytest.mark.asyncio
    async def test_register_upserts_existing_type(
        self, infra_tools
    ):
        """Registering the same name a second time is an
        upsert — the description and schema are overwritten.

        C3 fix: uses ``schema_def`` instead of ``schema_json``.
        """
        register_tool = infra_tools["infra_type_register"]

        await register_tool.ainvoke({
            "name": "router",
            "description": "original",
            "schema_def": {"type": "object"},
        })
        result = await register_tool.ainvoke({
            "name": "router",
            "description": "updated",
            "schema_def": {"type": "object", "properties": {"asn": {}}},
        })
        data = json.loads(TestAssetCreate._extract_json_object(result))
        assert data["description"] == "updated"
        assert "asn" in data["schema_json"]["properties"]


class TestTypeList:
    """Tests for ``infra_type_list`` (global, no project_id)."""

    @pytest.mark.asyncio
    async def test_type_list_empty(self, infra_tools):
        """With no types registered, list returns the
        "No infra asset types registered" message.
        """
        list_tool = infra_tools["infra_type_list"]
        result = await list_tool.ainvoke({})
        assert "No infra asset types registered" in result

    @pytest.mark.asyncio
    async def test_type_list_after_bootstrap(
        self, infra_tools, bootstrap_types
    ):
        """After ``bootstrap_default_types`` seeds the registry,
        ``infra_type_list`` shows the 9 built-in types in a
        markdown table ordered by name.
        """
        list_tool = infra_tools["infra_type_list"]
        result = await list_tool.ainvoke({})
        assert "| name | description | updated_at |" in result
        # Each of the 9 seeded types should appear as a row.
        for type_name in (
            "datacenter",
            "server",
            "rack",
            "k8s_cluster",
            "k8s_node",
            "network",
            "load_balancer",
            "database",
            "storage",
        ):
            assert f"| {type_name} |" in result
        # Count is announced.
        assert "(9):" in result

    @pytest.mark.asyncio
    async def test_type_list_after_register(
        self, infra_tools
    ):
        """Registering a custom type then listing shows it."""
        register_tool = infra_tools["infra_type_register"]
        list_tool = infra_tools["infra_type_list"]

        await register_tool.ainvoke({
            "name": "custom_type",
            "description": "Custom infra asset type",
        })

        result = await list_tool.ainvoke({})
        assert "custom_type" in result


# =============================================================================
# Group 9: infra_history_get
# =============================================================================


class TestHistoryGet:
    """Tests for ``infra_history_get`` — every asset mutation
    writes a history row."""

    @pytest.mark.asyncio
    async def test_history_records_create(
        self, infra_tools, seed_projects, project_id
    ):
        """Creating an asset writes a ``created`` history entry."""
        create_tool = infra_tools["infra_asset_create"]
        history_tool = infra_tools["infra_history_get"]

        create_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "hist-create",
        })
        asset_id = json.loads(
            TestAssetCreate._extract_json_object(create_result)
        )["id"]

        result = await history_tool.ainvoke({
            "project_id": project_id,
            "asset_id": asset_id,
        })
        assert "Change history" in result
        assert "(1 entries)" in result
        assert "| created |" in result
        # The changed_by column should be the calling instance.
        assert "| test-instance |" in result

    @pytest.mark.asyncio
    async def test_history_records_update(
        self, infra_tools, seed_projects, project_id
    ):
        """Updating an asset writes an ``updated`` history entry
        alongside the original ``created`` entry.
        """
        create_tool = infra_tools["infra_asset_create"]
        update_tool = infra_tools["infra_asset_update"]
        history_tool = infra_tools["infra_history_get"]

        create_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "hist-update",
        })
        asset_id = json.loads(
            TestAssetCreate._extract_json_object(create_result)
        )["id"]

        await update_tool.ainvoke({
            "project_id": project_id,
            "asset_id": asset_id,
            "name": "hist-update-v2",
        })

        result = await history_tool.ainvoke({
            "project_id": project_id,
            "asset_id": asset_id,
        })
        assert "(2 entries)" in result
        # Both change types are present (the order is newest-first
        # so ``updated`` appears before ``created`` in the table).
        assert "| updated |" in result
        assert "| created |" in result

    @pytest.mark.asyncio
    async def test_history_records_delete(
        self, infra_tools, seed_projects, project_id
    ):
        """Deleting an asset writes a ``deleted`` history entry —
        the audit trail is preserved even after the asset is gone.
        """
        create_tool = infra_tools["infra_asset_create"]
        delete_tool = infra_tools["infra_asset_delete"]
        history_tool = infra_tools["infra_history_get"]

        create_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "hist-delete",
        })
        asset_id = json.loads(
            TestAssetCreate._extract_json_object(create_result)
        )["id"]

        await delete_tool.ainvoke({
            "project_id": project_id,
            "asset_id": asset_id,
        })

        result = await history_tool.ainvoke({
            "project_id": project_id,
            "asset_id": asset_id,
        })
        assert "| deleted |" in result
        assert "| created |" in result

    @pytest.mark.asyncio
    async def test_history_unknown_asset_returns_empty_message(
        self, infra_tools
    ):
        """An unknown asset id returns the
        "No history found" message.
        """
        history_tool = infra_tools["infra_history_get"]
        result = await history_tool.ainvoke({
            "project_id": "test-project",
            "asset_id": "never-existed",
        })
        assert "No history found" in result


# =============================================================================
# Group 10: Error handling
# =============================================================================


class TestErrorHandling:
    """Tool-layer error paths: invalid project_id, unknown type,
    missing asset for get / update / delete."""

    @pytest.mark.asyncio
    async def test_create_with_invalid_project_id_returns_error(
        self, infra_tools
    ):
        """Creating an asset under a project that was not seeded
        violates the ``projects.project_id`` FK and the tool
        returns an ``ERROR:`` string (does NOT raise).

        C4 fix: the repository now differentiates UNIQUE
        (duplicate) from FK (invalid reference) violations.
        The FK violation message explicitly mentions
        ``"Invalid reference"`` and the offending
        ``project_id`` so the agent can correlate the cause
        to its action.
        """
        create_tool = infra_tools["infra_asset_create"]
        result = await create_tool.ainvoke({
            "project_id": "nonexistent-project",
            "type": "server",
            "name": "orphan-01",
        })
        assert "ERROR" in result
        # C4: the FK violation message is differentiated from
        # the duplicate-UNIQUE message.
        assert "Invalid reference" in result
        # The project id is reflected in the error so the agent
        # can correlate which call failed.
        assert "nonexistent-project" in result

    @pytest.mark.asyncio
    async def test_create_with_unknown_type_is_allowed(
        self, infra_tools, seed_projects, project_id
    ):
        """The infra tool layer does NOT validate the ``type``
        against the type registry (the spec is explicit about
        this — type validation is the caller's responsibility,
        not the tool's). An arbitrary type string is therefore
        accepted and the asset is created.

        This test pins down that intentional behavior so a
        future change that adds tool-layer validation is a
        conscious decision, not an accident.
        """
        create_tool = infra_tools["infra_asset_create"]
        result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "made_up_type",
            "name": "anything",
        })
        # The asset was created — no ERROR prefix.
        assert "ERROR" not in result
        assert "Created infra asset" in result
        data = json.loads(TestAssetCreate._extract_json_object(result))
        assert data["type"] == "made_up_type"

    @pytest.mark.asyncio
    async def test_update_nonexistent_asset_returns_error(
        self, infra_tools, seed_projects, project_id
    ):
        """Updating an unknown asset id returns an ``ERROR:``
        string and does NOT raise.
        """
        update_tool = infra_tools["infra_asset_update"]
        result = await update_tool.ainvoke({
            "project_id": project_id,
            "asset_id": "ghost-asset",
            "name": "x",
        })
        assert "ERROR" in result
        assert "ghost-asset" in result
        assert "found" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_asset_no_raise(
        self, infra_tools, seed_projects, project_id
    ):
        """Deleting an unknown asset id returns a "no asset ...
        to delete" message and does NOT raise.
        """
        delete_tool = infra_tools["infra_asset_delete"]
        result = await delete_tool.ainvoke({
            "project_id": project_id,
            "asset_id": "ghost-asset",
        })
        # Returns the soft "no asset ... to delete" string,
        # not a hard error.
        assert "ghost-asset" in result
        assert "to delete" in result
        assert "ERROR" not in result

    @pytest.mark.asyncio
    async def test_get_nonexistent_asset_not_found_message(
        self, infra_tools, seed_projects, project_id
    ):
        """Getting an unknown asset id returns the
        ``ERROR: No infra asset found ...`` message.
        """
        get_tool = infra_tools["infra_asset_get"]
        result = await get_tool.ainvoke({
            "project_id": project_id,
            "asset_id": "no-such-asset",
        })
        assert "ERROR" in result
        assert "no-such-asset" in result
        assert "found" in result.lower()


# =============================================================================
# Group 11: Project isolation (C2 fix) — update / delete / history
# =============================================================================


class TestProjectIsolation:
    """C2 fix: project isolation on mutation and history tools.

    The repository's ``update_asset`` / ``delete_asset`` / ``get_history``
    now take a ``project_id`` argument and refuse to operate on
    assets that belong to a different project. The tool layer
    forwards ``project_id`` for every call, so an agent that
    only knows its own project_id cannot probe / mutate / read
    history for assets belonging to a different project.
    """

    @pytest.mark.asyncio
    async def test_update_cross_project_returns_not_found(
        self, infra_tools, seed_projects, project_id, other_project_id
    ):
        """Updating an asset that belongs to another project is
        indistinguishable from updating a non-existent asset.

        Security property: a misbehaving agent that supplies
        ``other_project_id`` while holding a valid ``asset_id``
        cannot mutate the row, and gets a not-found error
        rather than evidence that the asset exists in
        ``other_project_id``.
        """
        create_tool = infra_tools["infra_asset_create"]
        update_tool = infra_tools["infra_asset_update"]

        # Create the asset under project A.
        create_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "iso-update-target",
        })
        asset_id = json.loads(
            TestAssetCreate._extract_json_object(create_result)
        )["id"]

        # Attempt to update it under project B.
        result = await update_tool.ainvoke({
            "project_id": other_project_id,
            "asset_id": asset_id,
            "name": "hijacked",
        })
        assert "ERROR" in result
        assert "found" in result.lower()
        # The "hijacked" name must NOT have been applied — the
        # row in the correct project must still be untouched.
        get_tool = infra_tools["infra_asset_get"]
        post_get = await get_tool.ainvoke({
            "project_id": project_id,
            "asset_id": asset_id,
        })
        data = json.loads(post_get)
        assert data["name"] == "iso-update-target"

    @pytest.mark.asyncio
    async def test_delete_cross_project_returns_no_op(
        self, infra_tools, seed_projects, project_id, other_project_id
    ):
        """Deleting an asset that belongs to another project is a
        no-op ("no asset ... to delete" message), not a real
        delete. The asset still exists in its owning project.
        """
        create_tool = infra_tools["infra_asset_create"]
        delete_tool = infra_tools["infra_asset_delete"]
        get_tool = infra_tools["infra_asset_get"]

        create_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "iso-delete-target",
        })
        asset_id = json.loads(
            TestAssetCreate._extract_json_object(create_result)
        )["id"]

        result = await delete_tool.ainvoke({
            "project_id": other_project_id,
            "asset_id": asset_id,
        })
        # No-op message, not an error string.
        assert "to delete" in result
        assert "ERROR" not in result

        # The asset must still exist in its owning project.
        post_get = await get_tool.ainvoke({
            "project_id": project_id,
            "asset_id": asset_id,
        })
        data = json.loads(post_get)
        assert data["name"] == "iso-delete-target"

    @pytest.mark.asyncio
    async def test_history_cross_project_returns_no_history(
        self, infra_tools, seed_projects, project_id, other_project_id
    ):
        """Querying history for an asset that belongs to another
        project returns the "No history found" message rather
        than leaking the audit trail.
        """
        create_tool = infra_tools["infra_asset_create"]
        history_tool = infra_tools["infra_history_get"]

        create_result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "iso-history-target",
        })
        asset_id = json.loads(
            TestAssetCreate._extract_json_object(create_result)
        )["id"]

        # History from the correct project — should work.
        own_result = await history_tool.ainvoke({
            "project_id": project_id,
            "asset_id": asset_id,
        })
        assert "Change history" in own_result
        assert "(1 entries)" in own_result

        # History from a different project — must NOT leak.
        cross_result = await history_tool.ainvoke({
            "project_id": other_project_id,
            "asset_id": asset_id,
        })
        assert "No history found" in cross_result


# =============================================================================
# Group 13: Exception handler C1 fix — ``type`` parameter shadowing builtin
# =============================================================================


class TestExceptionHandlerC1:
    """Regression coverage for C1.

    The tools ``infra_asset_create``, ``infra_asset_list``, and
    ``infra_asset_search`` all accept a ``type`` parameter. Inside
    their generic ``except Exception`` handler, the original code
    called ``type(exc).__name__`` to report the class name of the
    failure. Because ``type`` was a local parameter, this
    shadowed the Python builtin — and at runtime
    ``type(exc)`` tried to call the parameter's value as a
    function, raising ``TypeError: 'str' object is not callable``
    BEFORE the error string could be produced. The fix replaces
    ``type(exc).__name__`` with ``exc.__class__.__name__`` in
    all three handlers.

    These tests monkeypatch the underlying repository method to
    raise a plain ``RuntimeError`` and verify the tool returns a
    clean ``ERROR: ...`` string containing the runtime exception's
    class name — and does NOT itself crash with ``TypeError``.

    A separate sub-test (parametrized over the three tools)
    pins the behavior: any future change that re-introduces
    ``type(exc)`` will fail the test with a clear traceback from
    the inner ``TypeError`` rather than a confusing assertion
    failure.
    """

    @pytest.mark.asyncio
    async def test_create_exception_handler_returns_clean_error(
        self, infra_tools, seed_projects, project_id, infra_repository, monkeypatch
    ):
        """``infra_asset_create``'s generic except handler must not
        crash with ``TypeError`` when ``type`` is a parameter.

        C1 regression: the handler used to call ``type(exc).__name__``
        which raised ``TypeError: 'str' object is not callable``
        because the local ``type`` parameter shadowed the builtin.
        """
        def _raise(*_a, **_kw):
            raise RuntimeError("boom from create_asset")
        monkeypatch.setattr(infra_repository, "create_asset", _raise)

        create_tool = infra_tools["infra_asset_create"]
        result = await create_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
            "name": "c1-target",
            "attributes": {},
        })

        assert "ERROR" in result
        assert "RuntimeError" in result
        # Defensive: the C1 symptom would surface as a TypeError
        # from inside the except handler. We assert its absence.
        assert "TypeError" not in result
        # And the handler should NOT swallow the failure silently —
        # the message should mention the create operation.
        assert "create" in result.lower()

    @pytest.mark.asyncio
    async def test_list_exception_handler_returns_clean_error(
        self, infra_tools, seed_projects, project_id, infra_repository, monkeypatch
    ):
        """``infra_asset_list``'s generic except handler must not
        crash with ``TypeError`` when ``type`` is a parameter.

        Same shadowing bug as ``infra_asset_create``. The list
        tool's ``type`` filter is optional, but the parameter
        itself still binds in the function's local scope.
        """
        def _raise(*_a, **_kw):
            raise RuntimeError("boom from list_assets")
        monkeypatch.setattr(infra_repository, "list_assets", _raise)

        list_tool = infra_tools["infra_asset_list"]
        # Pass ``type=`` explicitly so the bug is reachable
        # (otherwise the parameter binds to its default ``None``
        # and the test is still valid, but explicit is clearer).
        result = await list_tool.ainvoke({
            "project_id": project_id,
            "type": "server",
        })

        assert "ERROR" in result
        assert "RuntimeError" in result
        assert "TypeError" not in result
        assert "list" in result.lower()

    @pytest.mark.asyncio
    async def test_search_exception_handler_returns_clean_error(
        self, infra_tools, seed_projects, project_id, infra_repository, monkeypatch
    ):
        """``infra_asset_search``'s generic except handler must not
        crash with ``TypeError`` when ``type`` is a parameter.
        """
        def _raise(*_a, **_kw):
            raise RuntimeError("boom from search_assets")
        monkeypatch.setattr(infra_repository, "search_assets", _raise)

        search_tool = infra_tools["infra_asset_search"]
        result = await search_tool.ainvoke({
            "project_id": project_id,
            "query": "anything",
            "type": "server",
        })

        assert "ERROR" in result
        assert "RuntimeError" in result
        assert "TypeError" not in result
        assert "search" in result.lower()


# =============================================================================
# Group 12: DevOps agent access — meta.json + resolve_tool_filter
# =============================================================================


class TestDevOpsAgentAccess:
    """The DevOps agent must have access to the ``infra`` tool
    category. The source of truth is ``agents/devops/meta.json``,
    which feeds ``resolve_tool_filter``."""

    def test_devops_meta_includes_infra(self):
        """``agents/devops/meta.json`` lists ``"infra"`` in
        ``tools.allow``. This is the agent-side configuration
        the loader reads at startup.
        """
        assert DEVOPS_META_PATH.exists(), (
            f"DevOps meta.json not found at {DEVOPS_META_PATH}"
        )
        meta = json.loads(DEVOPS_META_PATH.read_text())
        allow = meta.get("tools", {}).get("allow", [])
        assert "infra" in allow, (
            f"DevOps agent must have 'infra' in tools.allow; got {allow}"
        )

    def test_resolve_tool_filter_expands_infra_to_all_9_tools(self):
        """``resolve_tool_filter`` with ``allow=["infra"]`` and a
        tool_categories map that includes ``"infra"`` returns the
        9 infra tool names. This is the same expansion path the
        loader takes at startup.
        """
        from daemon.tools.instance import resolve_tool_filter

        categories = {
            "bash": ["bash"],
            "infra": list(INFRA_TOOL_NAMES),
        }
        result = resolve_tool_filter(
            allow=["infra"],
            deny=None,
            tool_categories=categories,
        )
        assert result == set(INFRA_TOOL_NAMES)

    def test_resolve_tool_filter_excludes_infra_when_absent(self):
        """``allow=["bash"]`` does NOT include any infra_* tool."""
        from daemon.tools.instance import resolve_tool_filter

        categories = {
            "bash": ["bash"],
            "infra": list(INFRA_TOOL_NAMES),
        }
        result = resolve_tool_filter(
            allow=["bash"],
            deny=None,
            tool_categories=categories,
        )
        assert "bash" in result
        assert not (result & INFRA_TOOL_NAMES)

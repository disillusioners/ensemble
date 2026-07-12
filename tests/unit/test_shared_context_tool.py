"""Tests for the ``shared_context_metadata`` LangChain tool.

Mirrors the pattern used in ``tests/unit/tools/test_context_tools.py``:
the factory is called with a ``MagicMock`` manager (so the tool body
sees the real ``shared_context_metadata_repo`` and
``_instance_repository`` attributes) and we drive the tool via
``ainvoke`` to exercise the full async path. ``asyncio.to_thread`` is
not mocked — the real ``set_many`` / ``delete_many`` / etc. on the
repository are simple sync methods that complete immediately.

The tool resolves ``context_key`` from the caller's tree-root id via
``manager._instance_repository.get_tree_root_id(...)``, with a
fallback to ``current_instance_id`` when the lookup misses. The tests
cover the resolution, the three mutating operations, and the
no-args read snapshot.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from daemon.tools.shared_context_tools import create_shared_context_tools


# ─── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def manager():
    """A mock ``InstanceManager`` with the two attributes the tool touches.

    ``shared_context_metadata_repo`` and ``_instance_repository`` are
    private/public names that match what the real InstanceManager
    exposes — see ``daemon/manager.py``. Each is a MagicMock so the
    tool's body records every call for assertion.
    """
    mgr = MagicMock()
    mgr.shared_context_metadata_repo = MagicMock()
    mgr._instance_repository = MagicMock()
    return mgr


@pytest.fixture
def tool(manager):
    """The single ``shared_context_metadata`` tool from the factory."""
    tools = create_shared_context_tools(manager, "inst-current")
    assert len(tools) == 1
    return tools[0]


# ─── Factory shape ────────────────────────────────────────────────────────────


class TestFactoryShape:
    def test_factory_returns_single_tool(self):
        tools = create_shared_context_tools(MagicMock(), "inst-1")
        assert len(tools) == 1
        assert tools[0].name == "shared_context_metadata"

    def test_tool_has_correct_category(self, tool):
        assert getattr(tool, "_tool_category", None) == "shared_context"


# ─── WRITE paths ───────────────────────────────────────────────────────────────


class TestSetKV:
    """``set_kv=...`` upserts the dict via ``set_many``."""

    @pytest.mark.asyncio
    async def test_setting_kv_pairs(self, tool, manager):
        """Passing ``set_kv`` calls ``repo.set_many`` with the resolved context_key."""
        manager._instance_repository.get_tree_root_id.return_value = "root-1"

        result = await tool.ainvoke({"set_kv": {"topic": "auth", "priority": 2}})

        # set_many was called with the resolved context_key + the dict.
        manager.shared_context_metadata_repo.set_many.assert_called_once_with(
            "root-1",
            {"topic": "auth", "priority": 2},
        )
        # The tool returns the post-op snapshot as JSON.
        snapshot = json.loads(result)
        assert isinstance(snapshot, dict)


# ─── DELETE paths ─────────────────────────────────────────────────────────────


class TestDeleteKeys:
    """``delete_keys=[...]`` removes the listed keys via ``delete_many``."""

    @pytest.mark.asyncio
    async def test_deleting_kv_pairs(self, tool, manager):
        """Passing ``delete_keys`` calls ``repo.delete_many`` with the resolved key."""
        manager._instance_repository.get_tree_root_id.return_value = "root-1"

        await tool.ainvoke({"delete_keys": ["old_decision", "stale_flag"]})

        manager.shared_context_metadata_repo.delete_many.assert_called_once_with(
            "root-1",
            ["old_decision", "stale_flag"],
        )
        # set_many must NOT be touched when only delete_keys is supplied.
        manager.shared_context_metadata_repo.set_many.assert_not_called()


class TestClearAll:
    """``clear_all=True`` wipes every row via ``delete_all``."""

    @pytest.mark.asyncio
    async def test_clear_all(self, tool, manager):
        """``clear_all=True`` calls ``repo.delete_all`` with the resolved key."""
        manager._instance_repository.get_tree_root_id.return_value = "root-1"

        await tool.ainvoke({"clear_all": True})

        manager.shared_context_metadata_repo.delete_all.assert_called_once_with(
            "root-1",
        )
        # The clear_all branch must not also call delete_many / set_many.
        manager.shared_context_metadata_repo.delete_many.assert_not_called()
        manager.shared_context_metadata_repo.set_many.assert_not_called()


# ─── READ path ────────────────────────────────────────────────────────────────


class TestReadSnapshot:
    """Calling with no arguments is a read-only snapshot."""

    @pytest.mark.asyncio
    async def test_reading_current_state(self, tool, manager):
        """No args → ``repo.get_all_as_dict`` is called and result returned as JSON."""
        manager._instance_repository.get_tree_root_id.return_value = "root-1"
        manager.shared_context_metadata_repo.get_all_as_dict.return_value = {
            "topic": "auth",
            "lang": "en",
        }

        result = await tool.ainvoke({})

        manager.shared_context_metadata_repo.get_all_as_dict.assert_called_once_with(
            "root-1",
        )
        # No mutating calls happen on a read.
        manager.shared_context_metadata_repo.set_many.assert_not_called()
        manager.shared_context_metadata_repo.delete_many.assert_not_called()
        manager.shared_context_metadata_repo.delete_all.assert_not_called()

        snapshot = json.loads(result)
        assert snapshot == {"topic": "auth", "lang": "en"}


# ─── Context-key resolution ────────────────────────────────────────────────────


class TestContextKeyResolution:
    """The tool resolves ``context_key`` from the caller's tree-root id."""

    @pytest.mark.asyncio
    async def test_context_key_resolution_from_instance_id(self, tool, manager):
        """The tool asks the instance repo for the tree-root id of ``current_instance_id``."""
        manager._instance_repository.get_tree_root_id.return_value = "tree-root-42"

        await tool.ainvoke({"set_kv": {"k": "v"}})

        # Resolver called with the current instance id captured at factory time.
        manager._instance_repository.get_tree_root_id.assert_called_once_with(
            "inst-current",
        )
        # And the repo was hit with that resolved id.
        manager.shared_context_metadata_repo.set_many.assert_called_once_with(
            "tree-root-42",
            {"k": "v"},
        )

    @pytest.mark.asyncio
    async def test_context_key_fallback_when_root_id_empty(self, tool, manager):
        """When ``get_tree_root_id`` returns falsy, ``current_instance_id`` is used."""
        manager._instance_repository.get_tree_root_id.return_value = None

        await tool.ainvoke({"set_kv": {"k": "v"}})

        # Still attempted the lookup, but fell back to the current id.
        manager._instance_repository.get_tree_root_id.assert_called_once_with(
            "inst-current",
        )
        manager.shared_context_metadata_repo.set_many.assert_called_once_with(
            "inst-current",
            {"k": "v"},
        )
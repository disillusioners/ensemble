"""Tests for the SSE integration layer of the todo tools.

Six tools (``todo_create``, ``todo_update``, ``todo_list``, ``todo_clear``,
``todo_add_edge``, ``todo_remove_edge``) emit a ``todo_update`` SSE event
on every successful mutation. The events are delivered through
``LiveEventHub.stream_todo_update(instance_id, todos)`` which uses an
``asyncio.Queue``-based fanout (no DB persistence — see
``LiveEventHub._stream_to_connections``).

Every payload item follows the **frozen 7-key schema** that the
frontend's Angular signals consume:

    {id, index, text, status, comment, next_ids, subtasks}

``id`` is the stable node ID (``n-`` prefixed), ``index`` is the
backward-compat insertion-order position, and ``next_ids`` carries the
adjacency list for the DAG.

Coverage lanes:

  1. **Emission on mutation** — create/update/clear/add_edge/remove_edge
     each invoke ``stream_todo_update`` exactly once on success.
  2. **Resilience** — SSE failure must NOT break the tool's user-facing
     contract (tool still returns its success string).
  3. **Optional hub** — when ``live_event_hub=None``, tools work without
     crashing and never try to emit.
  4. **Payload** — the ``todos`` argument equals the manager's stored
     state (snapshot semantics, full list each time).

Pattern: ``AsyncMock`` for ``live_event_hub`` per
``tests/test_chart_tools.py`` style; ``MagicMock`` manager with a real
``TodoManager`` attached at ``_todo_manager``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.todo_manager import TodoManager


# =============================================================================
# Helpers
# =============================================================================


def _make_manager_with_hub() -> MagicMock:
    """Build a mock ``InstanceManager`` with a real ``TodoManager``.

    The tools only need ``manager._todo_manager``; everything else on the
    mock is unused by the SSE integration tests.
    """
    manager = MagicMock()
    manager._todo_manager = TodoManager()
    return manager


def _build_tools(hub: AsyncMock | None = None, manager: MagicMock | None = None):
    """Build the 6 todo tools with the supplied hub (or no hub).

    ``manager`` is shared by both the caller and the tools so that tests
    can pre-populate state via ``manager._todo_manager.create(...)`` and
    then verify the same store after tool invocation. When omitted, a
    fresh manager is allocated (suitable for tests that only care about
    the SSE side-effect).

    Returns the tools in canonical order::

        [todo_create, todo_update, todo_list, todo_clear,
         todo_add_edge, todo_remove_edge]
    """
    from daemon.tools.todo_tools import create_todo_tools

    if manager is None:
        manager = _make_manager_with_hub()
    return create_todo_tools(
        manager=manager,
        current_instance_id="sse-test-instance",
        live_event_hub=hub,
    )


# =============================================================================
# Emission on create
# =============================================================================


class TestTodoCreateSSE:
    """``todo_create`` must emit one ``stream_todo_update`` per call."""

    async def test_sse_emit_on_create(self):
        """After ``todo_create``, ``stream_todo_update`` is awaited exactly once."""
        hub = AsyncMock()
        tools = _build_tools(hub=hub)
        create_tool = tools[0]

        await create_tool.coroutine(items=["Task 1", "Task 2"])

        hub.stream_todo_update.assert_awaited_once()
        # The hub is awaited with (instance_id, todos) — verify both args.
        call_args = hub.stream_todo_update.call_args
        assert call_args.args[0] == "sse-test-instance"
        todos_arg = call_args.args[1]
        assert len(todos_arg) == 2
        assert [t["text"] for t in todos_arg] == ["Task 1", "Task 2"]
        assert all(t["status"] == "pending" for t in todos_arg)


# =============================================================================
# Emission on update
# =============================================================================


class TestTodoUpdateSSE:
    """``todo_update`` must emit one ``stream_todo_update`` per successful update."""

    async def test_sse_emit_on_update(self):
        """Updating an item triggers an SSE emit with the new full list."""
        hub = AsyncMock()
        manager = _make_manager_with_hub()
        manager._todo_manager.create("sse-test-instance", ["alpha", "beta"])
        tools = _build_tools(hub=hub, manager=manager)
        update_tool = tools[1]

        await update_tool.coroutine(index=0, status="done")

        hub.stream_todo_update.assert_awaited_once()
        todos_arg = hub.stream_todo_update.call_args.args[1]
        assert todos_arg[0]["status"] == "done"
        assert todos_arg[1]["status"] == "pending"

    async def test_sse_not_emitted_on_invalid_update(self):
        """Invalid index/status → manager returns ``None`` → no SSE emit.

        The tool returns an ERROR string instead, and the SSE contract is
        that we only emit on successful mutation. The frontend never sees
        a phantom update.
        """
        hub = AsyncMock()
        manager = _make_manager_with_hub()
        manager._todo_manager.create("sse-test-instance", ["only"])
        tools = _build_tools(hub=hub, manager=manager)
        update_tool = tools[1]

        result = await update_tool.coroutine(index=99, status="done")

        assert result.startswith("ERROR:")
        hub.stream_todo_update.assert_not_awaited()


# =============================================================================
# Emission on clear
# =============================================================================


class TestTodoClearSSE:
    """``todo_clear`` must emit one ``stream_todo_update`` (with empty list)."""

    async def test_sse_emit_on_clear(self):
        """Clearing emits a ``todo_update`` with ``todos=[]``.

        The frontend listens for the empty payload to wipe its checklist.
        We verify the payload is ``[]`` specifically (not the prior list).
        """
        hub = AsyncMock()
        manager = _make_manager_with_hub()
        manager._todo_manager.create("sse-test-instance", ["X", "Y", "Z"])
        tools = _build_tools(hub=hub, manager=manager)
        clear_tool = tools[3]

        await clear_tool.coroutine()

        hub.stream_todo_update.assert_awaited_once()
        call_args = hub.stream_todo_update.call_args
        assert call_args.args[0] == "sse-test-instance"
        assert call_args.args[1] == []


# =============================================================================
# Resilience — SSE failure must NOT break tools
# =============================================================================


class TestSSEFailureResilience:
    """If the SSE hub raises, the tool must still complete successfully.

    The contract is documented in ``todo_tools._emit_update``: SSE is
    best-effort and never propagates exceptions into the tool's caller.
    A flaky hub must not turn a successful state mutation into a visible
    ERROR.
    """

    async def test_sse_failure_on_create_doesnt_break_tool(self):
        """Hub raises on create → tool returns the success string anyway."""
        hub = AsyncMock()
        hub.stream_todo_update.side_effect = RuntimeError("SSE pipe broken")

        manager = _make_manager_with_hub()
        tools = _build_tools(hub=hub, manager=manager)
        create_tool = tools[0]

        result = await create_tool.coroutine(items=["Survive failure"])

        # Tool still succeeded — state was mutated, output is the formatted list.
        assert "Survive failure" in result
        assert not result.startswith("ERROR:")
        # Underlying state reflects the mutation (proves the operation ran).
        assert manager._todo_manager.get_all("sse-test-instance")[0]["text"] == (
            "Survive failure"
        )

    async def test_sse_failure_on_update_doesnt_break_tool(self):
        """Hub raises on update → tool returns success and state is mutated."""
        hub = AsyncMock()
        hub.stream_todo_update.side_effect = RuntimeError("SSE down")

        manager = _make_manager_with_hub()
        manager._todo_manager.create("sse-test-instance", ["X"])
        tools = _build_tools(hub=hub, manager=manager)
        update_tool = tools[1]

        result = await update_tool.coroutine(index=0, status="done")

        assert not result.startswith("ERROR:")
        assert "Updated index=0" in result
        # State was actually updated despite SSE failure
        assert (
            manager._todo_manager.get_all("sse-test-instance")[0]["status"] == "done"
        )

    async def test_sse_failure_on_clear_doesnt_break_tool(self):
        """Hub raises on clear → tool returns success and state is cleared."""
        hub = AsyncMock()
        hub.stream_todo_update.side_effect = RuntimeError("SSE down")

        manager = _make_manager_with_hub()
        manager._todo_manager.create("sse-test-instance", ["X", "Y"])
        tools = _build_tools(hub=hub, manager=manager)
        clear_tool = tools[3]

        result = await clear_tool.coroutine()

        assert "cleared" in result.lower()
        # State was cleared
        assert manager._todo_manager.get_all("sse-test-instance") == []


# =============================================================================
# Optional hub — tools work without an SSE hub
# =============================================================================


class TestNoSSEHubConfigured:
    """When ``live_event_hub=None`` the tools run without any SSE attempt."""

    async def test_sse_not_called_when_no_hub(self):
        """``live_event_hub=None`` → tools execute and never try to emit."""
        # No hub passed — should not raise and should not produce any SSE call.
        tools = _build_tools(hub=None)
        create_tool = tools[0]
        update_tool = tools[1]
        clear_tool = tools[3]

        # Each mutation: runs cleanly without a hub.
        create_result = await create_tool.coroutine(items=["A", "B"])
        assert not create_result.startswith("ERROR:")

        update_result = await update_tool.coroutine(index=0, status="done")
        assert not update_result.startswith("ERROR:")

        clear_result = await clear_tool.coroutine()
        assert "cleared" in clear_result.lower()

    async def test_todo_list_does_not_emit_sse_at_all(self):
        """Read-only ``todo_list`` must never invoke the SSE hub.

        Even with a hub configured, the read path doesn't mutate state,
        so no event is warranted — the frontend already has the data.
        """
        hub = AsyncMock()
        manager = _make_manager_with_hub()
        manager._todo_manager.create("sse-test-instance", ["A"])
        tools = _build_tools(hub=hub, manager=manager)
        list_tool = tools[2]

        await list_tool.coroutine()

        hub.stream_todo_update.assert_not_awaited()


# =============================================================================
# Payload shape
# =============================================================================


class TestSSEPayloadStructure:
    """The ``todos`` argument sent to the hub mirrors the manager's stored state."""

    async def test_sse_payload_after_create_contains_serializable_dicts(self):
        """Each ``todo`` in the payload is a plain 7-key dict.

        The frontend JSON-serializes the payload directly — anything not
        a primitive would break its parser. The frozen Phase 1 schema
        carries exactly these keys per node::

            {id, index, text, status, comment, next_ids, subtasks}

        ``comment`` is always present (default empty string) and
        ``next_ids`` is always present (default empty list) so the FE
        doesn't have to handle a missing-key case.
        """
        hub = AsyncMock()
        manager = _make_manager_with_hub()
        tools = _build_tools(hub=hub)
        create_tool = tools[0]

        await create_tool.coroutine(items=["Only one"])

        todos_arg = hub.stream_todo_update.call_args.args[1]
        assert len(todos_arg) == 1
        item = todos_arg[0]
        assert set(item.keys()) == {
            "id",
            "index",
            "text",
            "status",
            "comment",
            "next_ids",
            "subtasks",
        }
        assert item["index"] == 0
        assert item["text"] == "Only one"
        assert item["status"] == "pending"
        assert item["comment"] == ""
        # ``id`` is the stable, ``n-``-prefixed node identifier; only the
        # schema is checked here — value uniqueness is enforced elsewhere.
        assert isinstance(item["id"], str) and item["id"].startswith("n-")
        # ``next_ids`` defaults to ``[]`` for a single-node graph and is
        # always a list (never ``None``).
        assert item["next_ids"] == []
        assert isinstance(item["next_ids"], list)
        # ``subtasks`` defaults to ``[]`` for a node with no checklist.
        assert item["subtasks"] == []
        assert isinstance(item["subtasks"], list)

    async def test_sse_payload_after_partial_progress_reflects_current_state(self):
        """After marking some items done, the payload reflects the new statuses.

        The contract is "full list every emission", which is what the
        frontend needs to fully re-render without bookkeeping.
        """
        hub = AsyncMock()
        manager = _make_manager_with_hub()
        manager._todo_manager.create("sse-test-instance", ["A", "B", "C"])
        manager._todo_manager.update("sse-test-instance", 1, "in_progress")
        tools = _build_tools(hub=hub, manager=manager)
        create_tool = tools[0]

        # Re-create to trigger another emit (mid-flow scenario).
        await create_tool.coroutine(items=["A", "B", "C"])

        todos_arg = hub.stream_todo_update.call_args.args[1]
        # The newly-created list is all-pending (create replaces, not merges).
        assert [t["status"] for t in todos_arg] == ["pending", "pending", "pending"]

    async def test_sse_payload_after_update_contains_seven_keys(self):
        """After ``todo_update``, the SSE payload still carries 7-key dicts.

        The 7-key schema is the cross-phase contract — the FE
        reconstructs graph state from the SSE stream alone and never
        needs to hit the API. A successful update must therefore emit
        the same shape as ``todo_create`` (same seven keys), not a
        trimmed-down variant.
        """
        hub = AsyncMock()
        manager = _make_manager_with_hub()
        manager._todo_manager.create("sse-test-instance", ["A", "B"])
        tools = _build_tools(hub=hub, manager=manager)
        update_tool = tools[1]

        await update_tool.coroutine(index=0, status="done")

        hub.stream_todo_update.assert_awaited_once()
        todos_arg = hub.stream_todo_update.call_args.args[1]
        # Full list, not a partial patch — every emission replaces the
        # client-side graph snapshot.
        assert len(todos_arg) == 2
        for item in todos_arg:
            assert set(item.keys()) == {
                "id",
                "index",
                "text",
                "status",
                "comment",
                "next_ids",
                "subtasks",
            }
            assert isinstance(item["id"], str) and item["id"].startswith("n-")
            assert isinstance(item["next_ids"], list)
        # The update changed statuses; payload reflects the mutation.
        assert todos_arg[0]["status"] == "done"
        assert todos_arg[1]["status"] == "pending"
        # IDs are unique across nodes (one of the FE's dedup invariants).
        assert todos_arg[0]["id"] != todos_arg[1]["id"]

    async def test_sse_payload_after_add_edge_emits_with_updated_next_ids(self):
        """``todo_add_edge`` emits an SSE event whose ``next_ids`` reflect the new edge.

        Node IDs are looked up from the manager (not positional
        indexes) — ``todo_add_edge`` is keyed by stable ``n-``-prefixed
        IDs. The emission contains the full 7-key node list (not a
        diff), because the FE re-renders from the snapshot.
        """
        hub = AsyncMock()
        manager = _make_manager_with_hub()
        # Linear chain: A → B → C — adding A → C bypasses B and
        # preserves DAG-ness (no cycle).
        initial = manager._todo_manager.create(
            "sse-test-instance", ["A", "B", "C"]
        )
        a_id = initial[0]["id"]
        b_id = initial[1]["id"]
        c_id = initial[2]["id"]
        # Sanity check: A's auto-chain goes to B, not C.
        assert initial[0]["next_ids"] == [b_id]

        tools = _build_tools(hub=hub, manager=manager)
        add_edge_tool = tools[4]

        await add_edge_tool.coroutine(from_id=a_id, to_id=c_id)

        hub.stream_todo_update.assert_awaited_once()
        todos_arg = hub.stream_todo_update.call_args.args[1]
        # Snapshot is the full node list, 7 keys each.
        assert len(todos_arg) == 3
        for item in todos_arg:
            assert set(item.keys()) == {
                "id",
                "index",
                "text",
                "status",
                "comment",
                "next_ids",
                "subtasks",
            }
        # A's next_ids now contains both B (original) and C (new edge).
        a_node = next(n for n in todos_arg if n["id"] == a_id)
        assert c_id in a_node["next_ids"]
        assert b_id in a_node["next_ids"]
        # B and C are otherwise unchanged.
        b_node = next(n for n in todos_arg if n["id"] == b_id)
        assert b_node["next_ids"] == [c_id]
        c_node = next(n for n in todos_arg if n["id"] == c_id)
        assert c_node["next_ids"] == []

    async def test_sse_payload_after_remove_edge_emits_with_updated_next_ids(self):
        """``todo_remove_edge`` emits an SSE event whose ``next_ids`` reflect the removal.

        The first emission in the auto-built chain (A → B) is removed,
        so A's ``next_ids`` becomes empty in the payload. As with
        ``add_edge``, the full snapshot is re-sent (not a delta) so the
        FE can re-render without bookkeeping.
        """
        hub = AsyncMock()
        manager = _make_manager_with_hub()
        initial = manager._todo_manager.create(
            "sse-test-instance", ["A", "B", "C"]
        )
        a_id = initial[0]["id"]
        b_id = initial[1]["id"]
        c_id = initial[2]["id"]
        # Sanity check: A's auto-chain goes to B.
        assert b_id in initial[0]["next_ids"]

        tools = _build_tools(hub=hub, manager=manager)
        remove_edge_tool = tools[5]

        await remove_edge_tool.coroutine(from_id=a_id, to_id=b_id)

        hub.stream_todo_update.assert_awaited_once()
        todos_arg = hub.stream_todo_update.call_args.args[1]
        # Snapshot is the full node list, 7 keys each.
        assert len(todos_arg) == 3
        for item in todos_arg:
            assert set(item.keys()) == {
                "id",
                "index",
                "text",
                "status",
                "comment",
                "next_ids",
                "subtasks",
            }
        # The removed edge is gone — A no longer points at B.
        a_node = next(n for n in todos_arg if n["id"] == a_id)
        assert b_id not in a_node["next_ids"]
        assert a_node["next_ids"] == []
        # B and C are otherwise unchanged (B still points to C).
        b_node = next(n for n in todos_arg if n["id"] == b_id)
        assert b_node["next_ids"] == [c_id]


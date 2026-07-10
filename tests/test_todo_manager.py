"""Unit tests for ``daemon.services.todo_manager.TodoManager``.

The TodoManager keeps ephemeral, in-memory todo state per ``instance_id``
behind a ``threading.Lock``. These tests exercise the public surface
(create / update / get_all / clear) and the documented edge cases.

Coverage lanes:

  1. **Create** — list creation, replacement, empty list.
  2. **Update** — valid statuses, invalid status (None), out-of-bounds
     indices (negative + too large).
  3. **Read** — empty for non-existent instance, dict shape verification.
  4. **Clear** — remove existing, no-op for missing instance.
  5. **Isolation** — different instance_ids have independent state.

No DB, no asyncio, no SSE: these are pure sync unit tests of one class.
"""

from __future__ import annotations

import pytest

from daemon.services.todo_manager import TodoGraphManager
from daemon.services.todo_manager import TodoManager


# =============================================================================
# Create
# =============================================================================


class TestTodoManagerCreate:
    """``TodoManager.create(instance_id, items)`` — replace list with all-pending."""

    def test_create_list_all_items_start_pending(self):
        """Create returns dicts, every item status == 'pending'."""
        mgr = TodoManager()
        result = mgr.create("inst-1", ["Task A", "Task B", "Task C"])

        assert isinstance(result, list)
        assert len(result) == 3
        for item in result:
            assert item["status"] == "pending"
            assert item["text"] in ("Task A", "Task B", "Task C")

    def test_create_assigns_sequential_indices(self):
        """Indices follow list position (0-based, contiguous)."""
        mgr = TodoManager()
        result = mgr.create("inst-1", ["alpha", "beta", "gamma"])

        assert [item["index"] for item in result] == [0, 1, 2]
        assert result[0]["text"] == "alpha"
        assert result[1]["text"] == "beta"
        assert result[2]["text"] == "gamma"

    def test_create_replaces_existing(self):
        """Second create on same instance_id replaces first wholesale.

        The 'replace' semantics matter: callers should not expect
        previous items to merge with the new list.
        """
        mgr = TodoManager()
        mgr.create("inst-1", ["old-1", "old-2", "old-3"])
        result = mgr.create("inst-1", ["new-1"])

        assert len(result) == 1
        assert result[0]["text"] == "new-1"
        assert result[0]["index"] == 0
        # Stored state matches
        assert len(mgr.get_all("inst-1")) == 1

    def test_create_empty_list(self):
        """Creating with ``[]`` stores an empty list and returns ``[]``."""
        mgr = TodoManager()
        result = mgr.create("inst-1", [])

        assert result == []
        # Stored state is also empty
        assert mgr.get_all("inst-1") == []

    def test_create_returned_dicts_are_independent_of_state(self):
        """Returned dicts are snapshots — mutating them must not corrupt state.

        The implementation uses ``_to_dict(item)`` to serialize; we verify
        the surface copy is decoupled by mutating one returned dict and
        reading the underlying state via ``get_all``.
        """
        mgr = TodoManager()
        result = mgr.create("inst-1", ["Task A"])
        result[0]["status"] = "done"

        # State must still report pending — dicts are copies, not live refs.
        assert mgr.get_all("inst-1")[0]["status"] == "pending"


# =============================================================================
# Update
# =============================================================================


class TestTodoManagerUpdate:
    """``TodoManager.update(instance_id, index, status)`` — mutate one item."""

    def test_update_to_each_valid_status(self):
        """All three valid statuses are accepted."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A", "B"])

        for status in ("pending", "in_progress", "done"):
            result = mgr.update_by_index("inst-1", 0, status)
            assert result is not None
            assert result["todos"][0]["status"] == status

    def test_update_invalid_status_returns_none(self):
        """``update`` rejects unknown statuses by returning ``None``.

        Status inputs are normalized (case-insensitive; ``completed`` → ``done``,
        ``started`` → ``in_progress``, etc.) before validation, so this test only
        exercises values that are neither canonical nor aliased. The tool layer
        translates ``None`` into a user-facing error string.
        """
        mgr = TodoManager()
        mgr.create("inst-1", ["A"])

        # Bogus status values: not canonical, no alias matches.
        assert mgr.update("inst-1", 0, "bogus_status") is None
        assert mgr.update("inst-1", 0, "") is None
        assert mgr.update("inst-1", 0, "!!!") is None

        # State is unchanged
        assert mgr.get_all("inst-1")[0]["status"] == "pending"

    def test_update_negative_index_returns_none(self):
        """Negative index is out of range and rejected with ``None``."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A", "B"])

        assert mgr.update("inst-1", -1, "done") is None
        # State unchanged
        assert mgr.get_all("inst-1")[1]["status"] == "pending"

    def test_update_index_too_large_returns_none(self):
        """index >= len(items) is rejected with ``None``."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A", "B"])

        assert mgr.update("inst-1", 2, "done") is None
        assert mgr.update("inst-1", 100, "done") is None

    def test_update_returns_full_list_snapshot_and_reminder(self):
        """``update`` returns ``{"todos": [...], "reminder": str}``.

        The full ordered list is in ``todos`` so the SSE payload matches the
        previous single-list return shape. ``reminder`` is a formatted string
        pointing at the next pending item (or all-completed).
        """
        mgr = TodoManager()
        mgr.create("inst-1", ["A", "B", "C"])

        result = mgr.update_by_index("inst-1", 1, "in_progress")

        assert result is not None
        assert set(result.keys()) == {"todos", "reminder"}
        todos = result["todos"]
        assert len(todos) == 3
        assert todos[0]["status"] == "pending"
        assert todos[1]["status"] == "in_progress"
        assert todos[2]["status"] == "pending"
        # Next pending reminder points at A (the first remaining pending item).
        assert "Next:" in result["reminder"]
        assert "A" in result["reminder"]

    def test_update_on_nonexistent_instance_returns_none(self):
        """Updating an instance that has no list returns ``None``."""
        mgr = TodoManager()
        assert mgr.update("ghost-instance", 0, "done") is None

    def test_update_to_done_with_no_comment_returns_default_reminder(self):
        """When marking done and the item has no comment, reminder has no prefix.

        Default reminder is ``"\\n\\n⏭️ Next: ..."`` or ``"\\n\\nAll items completed! ✅"``.
        No ``"User commented:"`` prefix should appear.
        """
        mgr = TodoManager()
        mgr.create("inst-1", ["A", "B"])
        result = mgr.update_by_index("inst-1", 0, "done")

        assert result is not None
        assert "User commented:" not in result["reminder"]
        assert "Next:" in result["reminder"]
        assert "B" in result["reminder"]

    def test_update_to_done_with_comment_prefixes_reminder(self):
        """When marking done and the item has a non-empty comment, the reminder
        is prefixed with ``"User commented: {comment}\\n..."`` followed by the
        original next-pending reminder.
        """
        mgr = TodoManager()
        mgr.create("inst-1", ["A", "B"])
        mgr.set_comment_by_index("inst-1", 0, "Looks good!")
        result = mgr.update_by_index("inst-1", 0, "done")

        assert result is not None
        assert "User commented:\n---\nLooks good!\n---\n" in result["reminder"]
        # The base next-pending reminder still follows.
        assert "Next:" in result["reminder"]
        assert "B" in result["reminder"]

    def test_update_to_done_with_comment_and_no_remaining_pending(self):
        """When the last item is done with a comment, the reminder uses the
        all-completed suffix (``"All items completed!"``) after the comment.
        """
        mgr = TodoManager()
        mgr.create("inst-1", ["Only"])
        mgr.set_comment_by_index("inst-1", 0, "Approved")
        result = mgr.update_by_index("inst-1", 0, "done")

        assert result is not None
        assert "User commented:\n---\nApproved\n---\n" in result["reminder"]
        assert "All items completed!" in result["reminder"]
        # No next-pending pointer.
        assert "Next:" not in result["reminder"]

    def test_update_to_non_done_status_ignores_comment(self):
        """A non-``done`` status never triggers the comment prefix.

        The comment is a side-channel for *completed* items — the
        ``in_progress`` and ``pending`` transitions never surface the
        comment in the reminder.
        """
        mgr = TodoManager()
        mgr.create("inst-1", ["A", "B"])
        mgr.set_comment_by_index("inst-1", 0, "Hidden feedback")
        result = mgr.update_by_index("inst-1", 0, "in_progress")

        assert result is not None
        assert "User commented:" not in result["reminder"]
        # DAG-aware waiting reminder (B is blocked because A is in_progress,
        # not done) — replaces the legacy flat-list "Next:" pointer.
        assert "blocked" in result["reminder"]

    def test_update_with_empty_comment_skips_user_commented_prefix(self):
        """An empty comment never produces the ``User commented:`` prefix."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A", "B"])
        mgr.set_comment_by_index("inst-1", 0, "")
        result = mgr.update_by_index("inst-1", 0, "done")

        assert result is not None
        assert "User commented:" not in result["reminder"]
        assert "Next:" in result["reminder"]


# =============================================================================
# Set Comment
# =============================================================================


class TestTodoManagerSetComment:
    """``TodoManager.set_comment(instance_id, index, comment)`` — annotate item."""

    def test_set_comment_valid_index_returns_updated_item(self):
        """Setting a comment returns the updated item dict."""
        mgr = TodoManager()
        mgr.create("inst-1", ["Task A", "Task B"])

        updated = mgr.set_comment_by_index("inst-1", 1, "Please rephrase this")

        assert updated["index"] == 1
        assert updated["text"] == "Task B"
        assert updated["comment"] == "Please rephrase this"
        assert updated["status"] == "pending"

    def test_set_comment_persists_into_state(self):
        """After ``set_comment``, ``get_all`` reflects the new comment."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A", "B", "C"])
        mgr.set_comment_by_index("inst-1", 2, "follow-up note")

        items = mgr.get_all("inst-1")
        assert items[0]["comment"] == ""
        assert items[1]["comment"] == ""
        assert items[2]["comment"] == "follow-up note"

    def test_set_comment_overwrites_existing(self):
        """Calling ``set_comment`` twice replaces the prior comment."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A"])
        mgr.set_comment_by_index("inst-1", 0, "first")
        mgr.set_comment_by_index("inst-1", 0, "second")

        assert mgr.get_all("inst-1")[0]["comment"] == "second"

    def test_set_comment_empty_string_clears_comment(self):
        """Empty string clears any prior comment."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A"])
        mgr.set_comment_by_index("inst-1", 0, "first")
        mgr.set_comment_by_index("inst-1", 0, "")

        assert mgr.get_all("inst-1")[0]["comment"] == ""

    def test_set_comment_negative_index_raises_value_error(self):
        """Negative index raises ``ValueError`` (matches error-handling style)."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A", "B"])

        with pytest.raises(ValueError):
            mgr.set_comment("inst-1", -1, "x")

    def test_set_comment_index_too_large_raises_value_error(self):
        """index >= len(items) raises ``ValueError``."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A"])

        with pytest.raises(ValueError):
            mgr.set_comment("inst-1", 5, "x")
        with pytest.raises(ValueError):
            mgr.set_comment("inst-1", 1, "x")

    def test_set_comment_on_nonexistent_instance_raises_value_error(self):
        """Instance without a todo list raises ``ValueError``."""
        mgr = TodoManager()

        with pytest.raises(ValueError):
            mgr.set_comment("ghost-instance", 0, "x")

    def test_set_comment_exceeds_max_length_raises_value_error(self):
        """A comment longer than ``MAX_COMMENT_LENGTH`` raises ``ValueError``.

        Defense-in-depth length guard: the HTTP layer returns 400 before
        reaching here under normal flow, but any non-HTTP caller (tools,
        scripts, future internal jobs) cannot bypass the limit.
        """
        from daemon.services.todo_manager import MAX_COMMENT_LENGTH

        mgr = TodoManager()
        mgr.create("inst-1", ["A"])

        over_limit = "a" * (MAX_COMMENT_LENGTH + 1)
        with pytest.raises(ValueError, match="exceeds maximum length"):
            mgr.set_comment("inst-1", 0, over_limit)
        # State untouched
        assert mgr.get_all("inst-1")[0]["comment"] == ""

    def test_set_comment_at_max_length_succeeds(self):
        """A comment exactly at ``MAX_COMMENT_LENGTH`` chars is accepted.

        Off-by-one boundary check — the boundary value must succeed;
        only strict greater-than must raise.
        """
        from daemon.services.todo_manager import MAX_COMMENT_LENGTH

        mgr = TodoManager()
        mgr.create("inst-1", ["A"])

        at_limit = "a" * MAX_COMMENT_LENGTH
        result = mgr.set_comment_by_index("inst-1", 0, at_limit)

        assert result["comment"] == at_limit
        assert mgr.get_all("inst-1")[0]["comment"] == at_limit

    def test_set_comment_does_not_change_status_or_text(self):
        """Comment is a side-channel — it must not mutate ``text`` or ``status``."""
        mgr = TodoManager()
        mgr.create("inst-1", ["Original text"])
        mgr.update_by_index("inst-1", 0, "in_progress")
        mgr.set_comment_by_index("inst-1", 0, "side note")

        item = mgr.get_all("inst-1")[0]
        assert item["text"] == "Original text"
        assert item["status"] == "in_progress"
        assert item["comment"] == "side note"


# =============================================================================
# Read
# =============================================================================


class TestTodoManagerGetAll:
    """``TodoManager.get_all(instance_id)`` — read-only state inspection."""

    def test_get_all_empty_for_nonexistent_instance(self):
        """``get_all`` on an unknown instance returns ``[]`` (not an error)."""
        mgr = TodoManager()
        assert mgr.get_all("never-created") == []

    def test_get_all_returns_list_of_dicts(self):
        """Returned items are dicts with the documented key set."""
        mgr = TodoManager()
        mgr.create("inst-1", ["Only item"])

        result = mgr.get_all("inst-1")

        assert isinstance(result, list)
        assert len(result) == 1
        item = result[0]
        assert set(item.keys()) == {"id", "index", "text", "status", "comment", "next_ids"}
        assert item["index"] == 0
        assert item["text"] == "Only item"
        assert item["status"] == "pending"
        assert item["comment"] == ""
        assert item["next_ids"] == []

    def test_get_all_reflects_updates(self):
        """``get_all`` after ``update`` returns the latest status."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A", "B"])
        mgr.update_by_index("inst-1", 1, "done")

        result = mgr.get_all("inst-1")
        assert result[0]["status"] == "pending"
        assert result[1]["status"] == "done"

    def test_get_all_returns_independent_copy(self):
        """Mutating the result of ``get_all`` does not corrupt internal state."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A"])

        snapshot = mgr.get_all("inst-1")
        snapshot[0]["status"] = "done"

        # Re-read must still show 'pending' — the implementation copies via
        # ``_to_dict`` per item.
        assert mgr.get_all("inst-1")[0]["status"] == "pending"


# =============================================================================
# Clear
# =============================================================================


class TestTodoManagerClear:
    """``TodoManager.clear(instance_id)`` — drop the list entirely."""

    def test_clear_removes_all_items(self):
        """After clear, ``get_all`` returns ``[]``."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A", "B", "C"])
        assert len(mgr.get_all("inst-1")) == 3

        mgr.clear("inst-1")

        assert mgr.get_all("inst-1") == []

    def test_clear_nonexistent_instance_does_not_raise(self):
        """Clearing an unknown instance is a no-op (no KeyError)."""
        mgr = TodoManager()
        # Should not raise
        mgr.clear("ghost-instance")
        assert mgr.get_all("ghost-instance") == []

    def test_clear_then_create_replaces_freshly(self):
        """After clear, a new ``create`` starts fresh (no leftover state)."""
        mgr = TodoManager()
        mgr.create("inst-1", ["old-A", "old-B"])
        mgr.clear("inst-1")

        result = mgr.create("inst-1", ["new-A"])
        assert len(result) == 1
        assert result[0]["text"] == "new-A"
        assert result[0]["status"] == "pending"


# =============================================================================
# Instance Isolation
# =============================================================================


class TestTodoManagerInstanceIsolation:
    """Different ``instance_id``s must have fully independent state."""

    def test_two_instances_have_independent_lists(self):
        """Mutating instance A leaves instance B untouched."""
        mgr = TodoManager()
        mgr.create("inst-A", ["A1", "A2"])
        mgr.create("inst-B", ["B1"])

        # Mutate A
        mgr.update_by_index("inst-A", 0, "done")

        a = mgr.get_all("inst-A")
        b = mgr.get_all("inst-B")

        assert a[0]["status"] == "done"
        assert a[1]["status"] == "pending"
        assert b[0]["status"] == "pending"
        assert b[0]["text"] == "B1"

    def test_clear_one_instance_does_not_affect_others(self):
        """``clear`` is scoped to one instance_id only."""
        mgr = TodoManager()
        mgr.create("inst-A", ["A1"])
        mgr.create("inst-B", ["B1"])

        mgr.clear("inst-A")

        assert mgr.get_all("inst-A") == []
        assert len(mgr.get_all("inst-B")) == 1


# =============================================================================
# DAG Graph Manager — structural creation
# =============================================================================


class TestTodoGraphManagerCreateGraph:
    """``TodoGraphManager.create_graph(instance_id, nodes, edges)`` — explicit graph build."""

    def test_create_graph_with_valid_nodes_and_edges(self):
        """A 2-node chain is stored with the edge wired into ``next_ids``.

        Verifies that the explicit ``edges`` list is the canonical
        source: even if ``next_ids`` is not pre-populated on the node
        spec, the declared edge materializes in storage.
        """
        mgr = TodoGraphManager()
        result = mgr.create_graph(
            "inst-1",
            [{"id": "step-a", "text": "A"}, {"id": "step-b", "text": "B"}],
            [{"from": "step-a", "to": "step-b"}],
        )

        assert len(result) == 2
        by_id = {item["id"]: item for item in result}
        assert by_id["step-a"]["next_ids"] == ["step-b"]
        assert by_id["step-b"]["next_ids"] == []

    def test_create_graph_with_cycle_raises_value_error(self):
        """A 3-node cycle A→B→C→A is rejected with ``ValueError``."""
        mgr = TodoGraphManager()
        with pytest.raises(ValueError):
            mgr.create_graph(
                "inst-1",
                [
                    {"id": "a", "text": "A"},
                    {"id": "b", "text": "B"},
                    {"id": "c", "text": "C"},
                ],
                [
                    {"from": "a", "to": "b"},
                    {"from": "b", "to": "c"},
                    {"from": "c", "to": "a"},
                ],
            )

    def test_create_graph_with_self_loop_raises_value_error(self):
        """An edge ``a→a`` is a self-loop cycle and must be rejected."""
        mgr = TodoGraphManager()
        with pytest.raises(ValueError):
            mgr.create_graph(
                "inst-1",
                [{"id": "a", "text": "A"}],
                [{"from": "a", "to": "a"}],
            )

    def test_create_graph_with_disconnected_components(self):
        """Two disjoint pairs ``A→B`` and ``C→D`` both persist independently."""
        mgr = TodoGraphManager()
        result = mgr.create_graph(
            "inst-1",
            [
                {"id": "a", "text": "A"},
                {"id": "b", "text": "B"},
                {"id": "c", "text": "C"},
                {"id": "d", "text": "D"},
            ],
            [{"from": "a", "to": "b"}, {"from": "c", "to": "d"}],
        )

        assert len(result) == 4
        by_id = {item["id"]: item for item in result}
        assert by_id["a"]["next_ids"] == ["b"]
        assert by_id["b"]["next_ids"] == []
        assert by_id["c"]["next_ids"] == ["d"]
        assert by_id["d"]["next_ids"] == []

    def test_create_graph_with_diamond_pattern(self):
        """A diamond A→B→D, A→C→D has 4 edges and D is the target of two.

        ``get_graph`` derives the edge list from ``next_ids`` adjacency,
        so a fan-in target like ``D`` must show up as ``to`` in exactly
        two edges.
        """
        mgr = TodoGraphManager()
        mgr.create_graph(
            "inst-1",
            [
                {"id": "a", "text": "A"},
                {"id": "b", "text": "B"},
                {"id": "c", "text": "C"},
                {"id": "d", "text": "D"},
            ],
            [
                {"from": "a", "to": "b"},
                {"from": "a", "to": "c"},
                {"from": "b", "to": "d"},
                {"from": "c", "to": "d"},
            ],
        )

        graph = mgr.get_graph("inst-1")
        assert len(graph["edges"]) == 4
        targets = [edge["to"] for edge in graph["edges"]]
        assert targets.count("d") == 2
        sources = [edge["from"] for edge in graph["edges"]]
        assert sources.count("a") == 2

    def test_create_graph_rejects_all_numeric_node_id(self):
        """An all-numeric node id collides with the index-based shim path.

        The numeric form is reserved for ``set_comment_by_index`` /
        ``update_by_index``; user-supplied ids must be non-numeric.
        """
        mgr = TodoGraphManager()
        with pytest.raises(ValueError, match=r"numeric|all-numeric|non-numeric"):
            mgr.create_graph("inst-1", [{"id": "123", "text": "X"}], [])

    def test_create_graph_enforces_max_nodes(self):
        """``len(nodes) > MAX_NODES`` (200) raises ``ValueError`` before storage."""
        mgr = TodoGraphManager()
        too_many = [{"id": f"step-{i}", "text": f"T{i}"} for i in range(201)]
        with pytest.raises(ValueError, match=r"200|maximum"):
            mgr.create_graph("inst-1", too_many, [])


# =============================================================================
# DAG Graph Manager — incremental node mutation
# =============================================================================


class TestTodoGraphManagerAddNode:
    """``TodoGraphManager.add_node(instance_id, text, next_ids=None)`` — append a node."""

    def test_add_node_to_existing_graph(self):
        """A node appended to an existing graph gets auto-id and is stored.

        The returned dict carries the ``next_ids`` we supplied; the new
        node is visible in subsequent ``get_all`` snapshots.
        """
        mgr = TodoGraphManager()
        mgr.create_graph(
            "inst-1",
            [{"id": "step-a", "text": "A"}, {"id": "step-b", "text": "B"}],
            [{"from": "step-a", "to": "step-b"}],
        )
        new_node = mgr.add_node("inst-1", "New node", next_ids=["step-a"])

        assert new_node["text"] == "New node"
        assert new_node["next_ids"] == ["step-a"]
        # Visible in the snapshot
        all_nodes = mgr.get_all("inst-1")
        assert len(all_nodes) == 3
        assert any(item["text"] == "New node" for item in all_nodes)

    def test_add_node_enforces_max_nodes(self):
        """Adding a 201st node to a 200-node graph raises ``ValueError``."""
        mgr = TodoGraphManager()
        nodes = [{"id": f"step-{i}", "text": f"T{i}"} for i in range(200)]
        mgr.create_graph("inst-1", nodes, [])

        with pytest.raises(ValueError, match=r"max|200"):
            mgr.add_node("inst-1", "Over the cap")


# =============================================================================
# DAG Graph Manager — node removal
# =============================================================================


class TestTodoGraphManagerRemoveNode:
    """``TodoGraphManager.remove_node(instance_id, node_id)`` — drop + cleanup."""

    def test_remove_node_removes_node_and_cleans_inbound_edges(self):
        """Removing the middle of a 3-chain drops that node and its inbound edge.

        We verify both the node disappearance and the edge-cleanup
        via ``get_graph`` — no edge should point TO the removed node.
        """
        mgr = TodoGraphManager()
        mgr.create_graph(
            "inst-1",
            [
                {"id": "step-a", "text": "A"},
                {"id": "step-b", "text": "B"},
                {"id": "step-c", "text": "C"},
            ],
            [{"from": "step-a", "to": "step-b"}, {"from": "step-b", "to": "step-c"}],
        )

        mgr.remove_node("inst-1", "step-b")
        graph = mgr.get_graph("inst-1")

        # Original A→B edge is gone
        assert {"from": "step-a", "to": "step-b"} not in graph["edges"]
        # No remaining edge targets step-b
        assert not any(edge["to"] == "step-b" for edge in graph["edges"])
        # step-b itself is gone
        assert not any(node["id"] == "step-b" for node in graph["nodes"])

    def test_remove_node_nonexistent_returns_none(self):
        """Removing a node that isn't in the graph returns ``None`` (no-op)."""
        mgr = TodoGraphManager()
        mgr.create_graph("inst-1", [{"id": "a", "text": "A"}], [])

        assert mgr.remove_node("inst-1", "missing-id") is None


# =============================================================================
# DAG Graph Manager — edge mutation
# =============================================================================


class TestTodoGraphManagerAddEdge:
    """``TodoGraphManager.add_edge(instance_id, from_id, to_id)`` — wire two nodes."""

    def test_add_edge_between_existing_nodes(self):
        """Adding A→B on an existing pair returns the updated graph dict.

        The returned dict has ``{"nodes": [...], "edges": [{...}, ...]}``
        shape and includes the new edge exactly once.
        """
        mgr = TodoGraphManager()
        mgr.create_graph(
            "inst-1",
            [{"id": "step-a", "text": "A"}, {"id": "step-b", "text": "B"}],
            [],
        )

        result = mgr.add_edge("inst-1", "step-a", "step-b")

        assert result is not None
        assert "edges" in result
        assert {"from": "step-a", "to": "step-b"} in result["edges"]

    def test_add_edge_creating_cycle_returns_none(self):
        """Adding B→A on a graph that already has A→B would form a 2-cycle.

        The method must reject the mutation (``None``) and leave the
        graph unchanged (verified via a follow-up ``get_graph``).
        """
        mgr = TodoGraphManager()
        mgr.create_graph(
            "inst-1",
            [{"id": "step-a", "text": "A"}, {"id": "step-b", "text": "B"}],
            [{"from": "step-a", "to": "step-b"}],
        )

        result = mgr.add_edge("inst-1", "step-b", "step-a")

        assert result is None
        graph = mgr.get_graph("inst-1")
        assert {"from": "step-b", "to": "step-a"} not in graph["edges"]

    def test_add_edge_with_nonexistent_nodes_returns_none(self):
        """Edge with an unknown from-node is a no-op miss (``None``)."""
        mgr = TodoGraphManager()
        mgr.create_graph("inst-1", [{"id": "a", "text": "A"}], [])

        assert mgr.add_edge("inst-1", "ghost", "a") is None


# =============================================================================
# DAG Graph Manager — edge removal
# =============================================================================


class TestTodoGraphManagerRemoveEdge:
    """``TodoGraphManager.remove_edge(instance_id, from_id, to_id)`` — drop one edge."""

    def test_remove_edge_between_connected_nodes(self):
        """Removing A→B from a 2-node graph leaves zero edges."""
        mgr = TodoGraphManager()
        mgr.create_graph(
            "inst-1",
            [{"id": "step-a", "text": "A"}, {"id": "step-b", "text": "B"}],
            [{"from": "step-a", "to": "step-b"}],
        )

        result = mgr.remove_edge("inst-1", "step-a", "step-b")

        assert result is not None
        assert result["edges"] == []

    def test_remove_edge_nonexistent_returns_none(self):
        """Removing an edge that doesn't exist returns ``None`` (no-op miss)."""
        mgr = TodoGraphManager()
        mgr.create_graph(
            "inst-1",
            [{"id": "step-a", "text": "A"}, {"id": "step-b", "text": "B"}],
            [],
        )

        assert mgr.remove_edge("inst-1", "step-a", "step-b") is None


# =============================================================================
# DAG Graph Manager — internal helpers
# =============================================================================


class TestTodoGraphManagerHasCycle:
    """``TodoGraphManager._has_cycle(nodes)`` — Kahn's-algorithm cycle probe."""

    def test_has_cycle_on_linear_chain_returns_false(self):
        """A 2-node A→B chain is acyclic."""
        mgr = TodoGraphManager()
        mgr.create_graph(
            "inst-1",
            [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
            [{"from": "a", "to": "b"}],
        )
        nodes = mgr._instance_graphs["inst-1"]

        assert mgr._has_cycle(nodes) is False

    def test_has_cycle_on_actual_cycle_returns_true(self):
        """Mutating a valid DAG into a 2-cycle must be detected.

        We use the public API to build a legal chain, then directly
        mutate ``next_ids`` to close the loop (``b→a``). This bypasses
        ``add_edge``'s own cycle guard (which is exactly what we want
        to exercise for the helper).
        """
        mgr = TodoGraphManager()
        mgr.create_graph(
            "inst-1",
            [{"id": "step-a", "text": "A"}, {"id": "step-b", "text": "B"}],
            [{"from": "step-a", "to": "step-b"}],
        )
        # Close the loop: b→a turns the chain into a 2-cycle.
        nodes = mgr._instance_graphs["inst-1"]
        nodes["step-b"].next_ids.append("step-a")

        assert mgr._has_cycle(nodes) is True

    def test_has_cycle_on_empty_graph_returns_false(self):
        """Empty node map is vacuously acyclic."""
        mgr = TodoGraphManager()

        assert mgr._has_cycle({}) is False

    def test_has_cycle_on_single_node_returns_false(self):
        """A lone node with no edges is acyclic."""
        mgr = TodoGraphManager()
        mgr.create_graph("inst-1", [{"id": "a", "text": "A"}], [])
        nodes = mgr._instance_graphs["inst-1"]

        assert mgr._has_cycle(nodes) is False


# =============================================================================
# DAG Graph Manager — reminder formatting
# =============================================================================


class TestTodoGraphManagerComputeReminder:
    """``TodoGraphManager._compute_reminder`` — graph-aware reminder strings.

    The reminder format encodes two layers:
      1. A **base reminder** describing graph state (ready nodes, all
         blocked, or all done).
      2. An optional **comment-fence prefix** that wraps any non-empty
         user comment in ``"User commented:\\n---\\n...\\n---\\n"``
         markers — preserving the prompt-injection guard from the
         legacy ``TodoManager``.
    """

    def test_compute_reminder_shows_ready_nodes(self):
        """Marking A done on A→B→C unblocks B as the next ready node."""
        mgr = TodoGraphManager()
        mgr.create_graph(
            "inst-1",
            [
                {"id": "step-a", "text": "A"},
                {"id": "step-b", "text": "B"},
                {"id": "step-c", "text": "C"},
            ],
            [{"from": "step-a", "to": "step-b"}, {"from": "step-b", "to": "step-c"}],
        )

        result = mgr.update_by_index("inst-1", 0, "done")

        assert "⏭️ Next:" in result["reminder"]
        assert "B" in result["reminder"]

    def test_compute_reminder_shows_blocked_nodes(self):
        """Marking A in_progress on A→B leaves B blocked (waiting)."""
        mgr = TodoGraphManager()
        mgr.create_graph(
            "inst-1",
            [{"id": "step-a", "text": "A"}, {"id": "step-b", "text": "B"}],
            [{"from": "step-a", "to": "step-b"}],
        )

        result = mgr.update_by_index("inst-1", 0, "in_progress")

        assert "⏳ Waiting:" in result["reminder"]
        assert "blocked" in result["reminder"]

    def test_compute_reminder_all_done(self):
        """Completing the only pending node produces the celebratory suffix."""
        mgr = TodoGraphManager()
        mgr.create_graph("inst-1", [{"id": "a", "text": "A"}], [])

        result = mgr.update_by_index("inst-1", 0, "done")

        assert "All items completed" in result["reminder"]
        assert "✅" in result["reminder"]

    def test_compute_reminder_with_comment_on_done_node(self):
        """A non-empty comment on a done node is fenced with the comment markers.

        The fence is the security-critical pattern that protects the
        LLM from prompt injection via the comment channel.
        """
        mgr = TodoGraphManager()
        mgr.create_graph("inst-1", [{"id": "a", "text": "A"}], [])
        mgr.set_comment_by_index("inst-1", 0, "user note")

        result = mgr.update_by_index("inst-1", 0, "done")

        assert result["reminder"].startswith("User commented:\n---\nuser note\n---\n")

    def test_compute_reminder_with_empty_comment_on_done_node(self):
        """An empty comment on a done node does not produce the fence prefix."""
        mgr = TodoGraphManager()
        mgr.create_graph("inst-1", [{"id": "a", "text": "A"}], [])
        mgr.set_comment_by_index("inst-1", 0, "")

        result = mgr.update_by_index("inst-1", 0, "done")

        assert "User commented:" not in result["reminder"]

    def test_compute_reminder_on_non_done_status_no_fence(self):
        """An in_progress status surfaces the comment is NEVER fenced.

        Comment fences apply only to ``done`` transitions — this is
        the security invariant.
        """
        mgr = TodoGraphManager()
        mgr.create_graph("inst-1", [{"id": "a", "text": "A"}], [])
        mgr.set_comment_by_index("inst-1", 0, "feedback")

        result = mgr.update_by_index("inst-1", 0, "in_progress")

        assert "User commented:" not in result["reminder"]

    def test_compute_reminder_comment_fence_with_all_done(self):
        """Fence + all-done suffix coexist on a complete single-node graph.

        The fence wraps the comment; the ``All items completed`` suffix
        follows as the base reminder.
        """
        mgr = TodoGraphManager()
        mgr.create_graph("inst-1", [{"id": "a", "text": "A"}], [])
        mgr.set_comment_by_index("inst-1", 0, "looks good")

        result = mgr.update_by_index("inst-1", 0, "done")

        assert "User commented:\n---\n" in result["reminder"]
        assert "All items completed" in result["reminder"]

    def test_compute_reminder_comment_fence_with_branching_graph(self):
        """Fence precedes a branching ``Next:`` reminder after marking the root.

        Diamond A→B, A→C, B→D, C→D. Marking A done with a comment
        unblocks B and C as ready successors; D remains blocked.
        """
        mgr = TodoGraphManager()
        mgr.create_graph(
            "inst-1",
            [
                {"id": "a", "text": "A"},
                {"id": "b", "text": "B"},
                {"id": "c", "text": "C"},
                {"id": "d", "text": "D"},
            ],
            [
                {"from": "a", "to": "b"},
                {"from": "a", "to": "c"},
                {"from": "b", "to": "d"},
                {"from": "c", "to": "d"},
            ],
        )
        mgr.set_comment_by_index("inst-1", 0, "first done")

        result = mgr.update_by_index("inst-1", 0, "done")

        assert result["reminder"].startswith(
            "User commented:\n---\nfirst done\n---\n"
        )
        assert "⏭️ Next:" in result["reminder"]


# =============================================================================
# DAG Graph Manager — backward-compat (flat-list) shims
# =============================================================================


class TestTodoManagerBackwardCompat:
    """The flat-list path (``create``, ``update_by_index``, ``set_comment_by_index``).

    These methods stay working post-refactor so legacy callers do not
    need to rewrite their ``create([...])`` + ``update(0, ...)``
    invocations.
    """

    def test_create_flat_list_still_works(self):
        """``create(["A", "B", "C"])`` builds a 3-node linear chain."""
        mgr = TodoGraphManager()
        result = mgr.create("inst-1", ["A", "B", "C"])

        assert len(result) == 3
        for i, item in enumerate(result):
            assert item["index"] == i
            assert item["status"] == "pending"

    def test_update_by_index_works_after_create(self):
        """``update_by_index`` resolves insertion-order index to node_id.

        After marking index 0 (``A``) done, the linear chain reports
        ``B`` as the next pending.
        """
        mgr = TodoGraphManager()
        mgr.create("inst-1", ["A", "B"])

        result = mgr.update_by_index("inst-1", 0, "done")

        assert result["todos"][0]["status"] == "done"
        assert "Next:" in result["reminder"]
        assert "B" in result["reminder"]

    def test_set_comment_by_index_works_after_create(self):
        """``set_comment_by_index`` annotates the indexed node and persists.

        The returned dict carries the new comment and ``get_all``
        reflects it on a follow-up read.
        """
        mgr = TodoGraphManager()
        mgr.create("inst-1", ["A"])

        result = mgr.set_comment_by_index("inst-1", 0, "note")

        assert result["comment"] == "note"
        assert mgr.get_all("inst-1")[0]["comment"] == "note"


# =============================================================================
# DAG Graph Manager — schema and structural assertions
# =============================================================================


class TestTodoGraphManagerStructure:
    """Schema-level assertions: payload shape, size caps, graph view."""

    def test_to_dict_has_six_keys(self):
        """``_to_dict`` emits exactly the six-key frozen schema.

        This guards the contract downstream phases (tools, API, UI)
        rely on — any change requires cross-phase coordination.
        """
        mgr = TodoGraphManager()
        mgr.create("inst-1", ["X"])
        nodes = mgr._instance_graphs["inst-1"]
        result = TodoGraphManager._to_dict(list(nodes.values())[0])

        assert set(result.keys()) == {"id", "index", "text", "status", "comment", "next_ids"}
        assert isinstance(result["id"], str) and result["id"].startswith("n-")
        assert result["index"] == 0
        assert result["text"] == "X"
        assert result["status"] == "pending"
        assert result["comment"] == ""
        assert result["next_ids"] == []

    def test_max_nodes_enforced_on_create(self):
        """Flat-list ``create`` caps at ``MAX_NODES`` (200)."""
        mgr = TodoGraphManager()
        with pytest.raises(ValueError, match=r"200|maximum"):
            mgr.create("inst-1", [f"Task {i}" for i in range(201)])

    def test_get_graph_returns_nodes_and_edges(self):
        """``get_graph`` returns the ``{"nodes": [...], "edges": [...]}`` shape.

        ``get_graph`` is the canonical introspection view — used by
        the frontend for graph visualization and by tools that need
        the full adjacency.
        """
        mgr = TodoGraphManager()
        mgr.create_graph(
            "inst-1",
            [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
            [{"from": "a", "to": "b"}],
        )

        graph = mgr.get_graph("inst-1")

        assert set(graph.keys()) == {"nodes", "edges"}
        assert len(graph["nodes"]) == 2
        assert len(graph["edges"]) == 1
        assert {"from": "a", "to": "b"} in graph["edges"]

    def test_get_graph_on_empty_graph(self):
        """``get_graph`` on an unknown instance returns the empty shape.

        Symmetric with ``get_all`` returning ``[]`` — no errors for
        instances that have never been touched.
        """
        mgr = TodoGraphManager()

        assert mgr.get_graph("never-created") == {"nodes": [], "edges": []}

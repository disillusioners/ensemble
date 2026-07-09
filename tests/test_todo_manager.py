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
            result = mgr.update("inst-1", 0, status)
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

        result = mgr.update("inst-1", 1, "in_progress")

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
        result = mgr.update("inst-1", 0, "done")

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
        mgr.set_comment("inst-1", 0, "Looks good!")
        result = mgr.update("inst-1", 0, "done")

        assert result is not None
        assert "User commented: Looks good!" in result["reminder"]
        # The base next-pending reminder still follows.
        assert "Next:" in result["reminder"]
        assert "B" in result["reminder"]

    def test_update_to_done_with_comment_and_no_remaining_pending(self):
        """When the last item is done with a comment, the reminder uses the
        all-completed suffix (``"All items completed!"``) after the comment.
        """
        mgr = TodoManager()
        mgr.create("inst-1", ["Only"])
        mgr.set_comment("inst-1", 0, "Approved")
        result = mgr.update("inst-1", 0, "done")

        assert result is not None
        assert "User commented: Approved" in result["reminder"]
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
        mgr.set_comment("inst-1", 0, "Hidden feedback")
        result = mgr.update("inst-1", 0, "in_progress")

        assert result is not None
        assert "User commented:" not in result["reminder"]
        assert "Next:" in result["reminder"]

    def test_update_with_empty_comment_skips_user_commented_prefix(self):
        """An empty comment never produces the ``User commented:`` prefix."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A", "B"])
        mgr.set_comment("inst-1", 0, "")
        result = mgr.update("inst-1", 0, "done")

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

        updated = mgr.set_comment("inst-1", 1, "Please rephrase this")

        assert updated["index"] == 1
        assert updated["text"] == "Task B"
        assert updated["comment"] == "Please rephrase this"
        assert updated["status"] == "pending"

    def test_set_comment_persists_into_state(self):
        """After ``set_comment``, ``get_all`` reflects the new comment."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A", "B", "C"])
        mgr.set_comment("inst-1", 2, "follow-up note")

        items = mgr.get_all("inst-1")
        assert items[0]["comment"] == ""
        assert items[1]["comment"] == ""
        assert items[2]["comment"] == "follow-up note"

    def test_set_comment_overwrites_existing(self):
        """Calling ``set_comment`` twice replaces the prior comment."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A"])
        mgr.set_comment("inst-1", 0, "first")
        mgr.set_comment("inst-1", 0, "second")

        assert mgr.get_all("inst-1")[0]["comment"] == "second"

    def test_set_comment_empty_string_clears_comment(self):
        """Empty string clears any prior comment."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A"])
        mgr.set_comment("inst-1", 0, "first")
        mgr.set_comment("inst-1", 0, "")

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

    def test_set_comment_does_not_change_status_or_text(self):
        """Comment is a side-channel — it must not mutate ``text`` or ``status``."""
        mgr = TodoManager()
        mgr.create("inst-1", ["Original text"])
        mgr.update("inst-1", 0, "in_progress")
        mgr.set_comment("inst-1", 0, "side note")

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
        assert set(item.keys()) == {"index", "text", "status", "comment"}
        assert item["index"] == 0
        assert item["text"] == "Only item"
        assert item["status"] == "pending"
        assert item["comment"] == ""

    def test_get_all_reflects_updates(self):
        """``get_all`` after ``update`` returns the latest status."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A", "B"])
        mgr.update("inst-1", 1, "done")

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
        mgr.update("inst-A", 0, "done")

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

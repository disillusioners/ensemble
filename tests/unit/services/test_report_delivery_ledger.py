"""Unit tests for ``parent_history_has_internal_report`` (F-2 task 2.4).

Feature: durability-f1-f2 / F-2 / Phase 2 (2026-10-04).

The ``parent_history_has_internal_report`` helper is the
parent-history-side PREFIX ledger check that, together with
the queue-side ``MessageQueueRepository.find_wake_already_delivered_evidence``
(task 2.3), forms the F-2 operative cross-path dedup
(per decisions.md §14a W-1 LOCKED to fallback (ii)).

The helper reads the parent's ``get_instance_messages`` serialized
dicts and returns ``True`` if any message has
``dict.get("source","").startswith(f"internal_report:{child_id}")``.
The pre-migration parent's absence of the ``source`` field
degrades gracefully to "not yet reported" (no false-positive
skip — ``dict.get("source","")`` returns ``""`` for messages
without the field, and ``"".startswith(prefix)`` is ``False``).

Harness mirrors ``tests/unit/test_report_delivery_recovery_service.py``:
F9 parity (file-backed SQLite is NOT used here — the helper
operates on a checkpointer mock, not on the DB).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest


# A tiny async-friendly mock checkpointer. The real
# ``get_instance_messages`` is patched at the persistence
# layer in each test (so the function reads from the patched
# data without touching the real checkpointer / DB).


class _MockCheckpointer:
    """Mock checkpointer for the helper test path.

    The real ``get_instance_messages`` calls
    ``checkpointer.raw_saver.aget(config)`` (or
    ``checkpointer.aget(config)`` if not a ``CheckpointerAdapter``).
    The mock's ``raw_saver.aget`` returns ``None`` which short-
    circuits the real persistence read; the patched
    ``get_instance_messages`` in the test body returns the
    fixture data directly.
    """

    class _MockRawSaver:
        async def aget(self, config: Any) -> None:
            return None

    raw_saver = _MockRawSaver()

    async def aget(self, config: Any) -> None:
        """``saver = checkpointer`` (when not a ``CheckpointerAdapter``);
        the mock's ``aget`` returns ``None``."""
        return None


# ── PREFIX-match coverage matrix (S22 ledger-side) ───────────────────────────


class TestParentHistoryHasInternalReportPrefixMatch:
    """S22 ledger-side: the parent-history-side PREFIX check
    covers the SAME prefix as the queue-side check
    (``internal_report:{child_id}:%``)."""

    @pytest.mark.asyncio
    async def test_returns_true_when_assistant_message_has_source_prefix(
        self,
    ) -> None:
        from daemon.services.report_delivery_ledger import (
            parent_history_has_internal_report,
        )
        import daemon.persistence as persistence_mod

        parent = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"

        async def _fake_messages(checkpointer, instance_id, manager=None):
            assert instance_id == parent
            return [
                {
                    "role": "user",
                    "content": "hello",
                    "message_id": f"user-{uuid.uuid4().hex[:8]}",
                },
                {
                    "role": "assistant",
                    "content": "delivered report",
                    "message_id": f"assistant-{uuid.uuid4().hex[:8]}",
                    "source": f"internal_report:{child_id}:anchor-msg",
                },
            ]

        original = persistence_mod.get_instance_messages
        persistence_mod.get_instance_messages = _fake_messages
        try:
            result = await parent_history_has_internal_report(
                _MockCheckpointer(), parent, child_id
            )
        finally:
            persistence_mod.get_instance_messages = original
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_with_anchor_message_id_in_source(
        self,
    ) -> None:
        """PREFIX-match catches ANY anchor — the design review
        (B3 per decisions.md §12a) verified that the anchor
        id is stable-but-different across delivery paths and
        the PREFIX-match is the only correct cross-path
        check."""
        from daemon.services.report_delivery_ledger import (
            parent_history_has_internal_report,
        )
        import daemon.persistence as persistence_mod

        parent = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        # Multiple anchor ids — any of them triggers the PREFIX.
        anchor_ids = [
            f"anchor-{uuid.uuid4().hex[:8]}" for _ in range(3)
        ]

        async def _fake_messages(checkpointer, instance_id, manager=None):
            return [
                {
                    "role": "assistant",
                    "content": f"report anchor {i}",
                    "message_id": f"assistant-{uuid.uuid4().hex[:8]}",
                    "source": f"internal_report:{child_id}:{anchor_ids[i]}",
                }
                for i in range(3)
            ]

        original = persistence_mod.get_instance_messages
        persistence_mod.get_instance_messages = _fake_messages
        try:
            result = await parent_history_has_internal_report(
                _MockCheckpointer(), parent, child_id
            )
        finally:
            persistence_mod.get_instance_messages = original
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_match(
        self,
    ) -> None:
        """No ``internal_report:{child_id}:%`` match in the
        parent's checkpoint → returns False."""
        from daemon.services.report_delivery_ledger import (
            parent_history_has_internal_report,
        )
        import daemon.persistence as persistence_mod

        parent = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        other_child = f"other-{uuid.uuid4().hex[:8]}"

        async def _fake_messages(checkpointer, instance_id, manager=None):
            return [
                {
                    "role": "assistant",
                    "content": "report for a different child",
                    "message_id": f"assistant-{uuid.uuid4().hex[:8]}",
                    "source": f"internal_report:{other_child}:some-anchor",
                },
            ]

        original = persistence_mod.get_instance_messages
        persistence_mod.get_instance_messages = _fake_messages
        try:
            result = await parent_history_has_internal_report(
                _MockCheckpointer(), parent, child_id
            )
        finally:
            persistence_mod.get_instance_messages = original
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_for_pre_migration_parent_without_source(
        self,
    ) -> None:
        """A parent pre-dating the ``additional_kwargs.source``
        surfacing has messages with NO ``source`` field.
        ``dict.get(\"source\",\"\")`` returns ``""`` —
        ``\"\".startswith(prefix)`` is ``False`` — graceful
        degradation per the docstring."""
        from daemon.services.report_delivery_ledger import (
            parent_history_has_internal_report,
        )
        import daemon.persistence as persistence_mod

        parent = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"

        async def _fake_messages(checkpointer, instance_id, manager=None):
            return [
                {
                    "role": "user",
                    "content": "hello",
                    "message_id": f"user-{uuid.uuid4().hex[:8]}",
                },
                {
                    "role": "assistant",
                    "content": "pre-migration report (no source field)",
                    "message_id": f"assistant-{uuid.uuid4().hex[:8]}",
                    # NO "source" key — pre-migration parent.
                },
            ]

        original = persistence_mod.get_instance_messages
        persistence_mod.get_instance_messages = _fake_messages
        try:
            result = await parent_history_has_internal_report(
                _MockCheckpointer(), parent, child_id
            )
        finally:
            persistence_mod.get_instance_messages = original
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_checkpointer_is_none(
        self,
    ) -> None:
        """``checkpointer=None`` ⇒ returns False (no evidence
        available; the per-row pass logs WARNING and
        ``continue``s)."""
        from daemon.services.report_delivery_ledger import (
            parent_history_has_internal_report,
        )
        result = await parent_history_has_internal_report(
            None, "parent-x", "child-y"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_messages_empty(
        self,
    ) -> None:
        """An empty parent history (no messages at all) ⇒
        returns False."""
        from daemon.services.report_delivery_ledger import (
            parent_history_has_internal_report,
        )
        import daemon.persistence as persistence_mod

        async def _fake_messages(checkpointer, instance_id, manager=None):
            return []

        original = persistence_mod.get_instance_messages
        persistence_mod.get_instance_messages = _fake_messages
        try:
            result = await parent_history_has_internal_report(
                _MockCheckpointer(),
                f"parent-{uuid.uuid4().hex[:8]}",
                f"child-{uuid.uuid4().hex[:8]}",
            )
        finally:
            persistence_mod.get_instance_messages = original
        assert result is False

    @pytest.mark.asyncio
    async def test_user_messages_with_source_prefix_dont_match(
        self,
    ) -> None:
        """A user-role message with the PREFIX source (a
        malformed data shape — the source is supposed to
        be only on assistant messages) should NOT match
        the ledger. The implementation uses
        ``msg.get("role") == "assistant"`` as a guard so
        only assistant messages are scanned."""
        from daemon.services.report_delivery_ledger import (
            parent_history_has_internal_report,
        )
        import daemon.persistence as persistence_mod

        parent = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"

        async def _fake_messages(checkpointer, instance_id, manager=None):
            return [
                {
                    "role": "user",  # NOT assistant — should be ignored
                    "content": "user message with prefix source",
                    "message_id": f"user-{uuid.uuid4().hex[:8]}",
                    "source": f"internal_report:{child_id}:user-anchor",
                },
            ]

        original = persistence_mod.get_instance_messages
        persistence_mod.get_instance_messages = _fake_messages
        try:
            result = await parent_history_has_internal_report(
                _MockCheckpointer(), parent, child_id
            )
        finally:
            persistence_mod.get_instance_messages = original
        # The user-role message is NOT scanned; returns False.
        assert result is False


# ── PREFIX key shape ─────────────────────────────────────────────────────────


class TestSourceKeyShape:
    """The PREFIX-match key shape (verified)."""

    def test_source_key_includes_colon_suffix(self) -> None:
        from daemon.services.report_delivery_ledger import _source_key

        prefix = _source_key("child-abc-123")
        # The PREFIX ends with a colon so the message_id portion
        # is NOT part of the match (anchor-blindness avoidance).
        assert prefix == "internal_report:child-abc-123:"
        # The PREFIX is a strict prefix of any
        # ``internal_report:{child}:{anchor}`` shape.
        assert (
            f"internal_report:child-abc-123:anchor-msg".startswith(
                prefix
            )
        )
        # A DIFFERENT child's prefix does NOT match.
        assert not (
            "internal_report:child-xyz-789:anchor-msg".startswith(
                prefix
            )
        )

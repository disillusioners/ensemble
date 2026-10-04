"""Unit tests for ``parent_history_has_internal_report`` (F-2 task 2.4).

Feature: durability-f1-f2 / F-2 / Phase 2 (2026-10-04).

The ``parent_history_has_internal_report`` helper is the
parent-history-side PREFIX ledger check that, together with
the queue-side ``MessageQueueRepository.find_wake_already_delivered_evidence``
(task 2.3), forms the F-2 operative cross-path dedup
(per decisions.md §14a W-1 LOCKED to fallback (ii)).

REAL EVIDENCE SHAPE (verification iteration 2, blocker 2): the
parent-history delivery evidence is the report-frame
``HumanMessage`` stamped at ``daemon/graph.py:8528-8534`` —
``additional_kwargs["source"] = f"internal_report:{child_id}"``
(NO trailing colon) — which serializes with ``role == "user"``
(``daemon/utils.py:109`` role_map maps ``human → user``). The
queue-side mint shape (``daemon/manager.py``, all six
``MessageQueue`` write sites) is
``internal_report:{child_id}:{message_id}`` (colon-delimited).
The matcher accepts BOTH via the boundary rule and rejects
child-boundary violations (``internal_report:{child}2``,
``internal_report:{other}``). There is NO role guard — the
evidence is user-role, so an assistant-only scan was blind by
construction.

The pre-migration parent's absence of the ``source`` field
degrades gracefully to "not yet reported" (no false-positive
skip — the matcher treats a missing/non-str ``source`` as no
match).

Harness mirrors ``tests/unit/test_report_delivery_recovery_service.py``:
F9 parity (file-backed SQLite is NOT used here — the helper
operates on a checkpointer mock, not on the DB).
"""

from __future__ import annotations

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


# ── Reality-shaped evidence matrix (S22 ledger-side, iteration 2) ────────────


class TestParentHistoryHasInternalReportPrefixMatch:
    """S22 ledger-side: the parent-history-side PREFIX check
    matches the REAL evidence shapes — the no-colon HumanMessage
    stamp (``graph.py:8528``, user-role) AND the colon-delimited
    queue-side mint — and rejects child-boundary violations."""

    @pytest.mark.asyncio
    async def test_real_stamp_shape_no_colon_user_role_matches(
        self,
    ) -> None:
        """THE blocker-2 regression pin: the REAL parent-history
        evidence is a user-role (HumanMessage) serialized dict with
        a NO-COLON source (``internal_report:{child_id}`` — the
        stamp at ``graph.py:8528-8534``). The ledger MUST match it.
        The prior implementation (assistant-only role guard +
        colon-terminated prefix) was blind to this shape by
        construction."""
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
                    # The report-frame HumanMessage shape:
                    # role serializes to "user", source has NO
                    # trailing colon.
                    "role": "user",
                    "content": "[Child terminal report] ...",
                    "message_id": f"report-{uuid.uuid4().hex[:8]}",
                    "source": f"internal_report:{child_id}",
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
    async def test_queue_side_colon_form_also_matches(
        self,
    ) -> None:
        """The colon-delimited queue-side mint shape
        (``internal_report:{child_id}:{anchor}`` — written by all
        six ``MessageQueue`` sites in ``daemon/manager.py``) also
        matches: both forms exist historically, so the boundary
        rule accepts exact-no-colon OR colon-delimited."""
        from daemon.services.report_delivery_ledger import (
            parent_history_has_internal_report,
        )
        import daemon.persistence as persistence_mod

        parent = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        anchor = f"anchor-{uuid.uuid4().hex[:8]}"

        async def _fake_messages(checkpointer, instance_id, manager=None):
            return [
                {
                    "role": "user",
                    "content": "queued report mirror",
                    "message_id": f"report-{uuid.uuid4().hex[:8]}",
                    "source": f"internal_report:{child_id}:{anchor}",
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
    async def test_scan_is_role_agnostic(self) -> None:
        """The role guard is GONE (decisions.md §14a literal spec:
        startswith, no role guard). An assistant-role message
        carrying the child's source ALSO matches — the scan keys
        on the source shape, not the role. (Inverted from the
        prior iteration's fiction: that test asserted an
        assistant-only guard against a fabricated fixture
        shape.)"""
        from daemon.services.report_delivery_ledger import (
            parent_history_has_internal_report,
        )
        import daemon.persistence as persistence_mod

        parent = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"

        async def _fake_messages(checkpointer, instance_id, manager=None):
            return [
                {
                    "role": "assistant",
                    "content": "assistant message with the source",
                    "message_id": f"assistant-{uuid.uuid4().hex[:8]}",
                    "source": f"internal_report:{child_id}",
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
    async def test_child_boundary_violation_does_not_match(
        self,
    ) -> None:
        """Child-boundary safety: ``internal_report:{child}2`` is a
        DIFFERENT child whose id merely extends this one — MUST NOT
        match, in either the exact or the colon-delimited form."""
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
                    "content": "sibling child exact",
                    "message_id": f"r1-{uuid.uuid4().hex[:8]}",
                    "source": f"internal_report:{child_id}2",
                },
                {
                    "role": "user",
                    "content": "sibling child colon form",
                    "message_id": f"r2-{uuid.uuid4().hex[:8]}",
                    "source": f"internal_report:{child_id}2:anchor-x",
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
    async def test_other_child_does_not_match(self) -> None:
        """A DIFFERENT child's report (either shape) MUST NOT
        match."""
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
                    "role": "user",
                    "content": "other child no-colon",
                    "message_id": f"r1-{uuid.uuid4().hex[:8]}",
                    "source": f"internal_report:{other_child}",
                },
                {
                    "role": "user",
                    "content": "other child colon form",
                    "message_id": f"r2-{uuid.uuid4().hex[:8]}",
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
        surfacing has messages with NO ``source`` key. The matcher
        reads ``msg.get("source")`` → ``None`` → non-str → no
        match — graceful degradation per the docstring."""
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


# ── Boundary matcher shape ───────────────────────────────────────────────────


class TestChildReportSourceMatcherShape:
    """The boundary-safe matcher (``_is_child_report_source``) —
    the full accept/reject matrix, including the degenerate
    non-str inputs the ``dict.get`` degradation can produce."""

    def test_accepts_exact_no_colon_form(self) -> None:
        from daemon.services.report_delivery_ledger import (
            _is_child_report_source,
        )

        # The parent-history HumanMessage stamp shape.
        assert _is_child_report_source(
            "internal_report:child-abc-123", "child-abc-123"
        )

    def test_accepts_colon_delimited_form(self) -> None:
        from daemon.services.report_delivery_ledger import (
            _is_child_report_source,
        )

        # The queue-side mint shape (anchor suffix).
        assert _is_child_report_source(
            "internal_report:child-abc-123:anchor-msg",
            "child-abc-123",
        )

    def test_rejects_child_boundary_extension(self) -> None:
        from daemon.services.report_delivery_ledger import (
            _is_child_report_source,
        )

        # A different child whose id EXTENDS this one.
        assert not _is_child_report_source(
            "internal_report:child-abc-1232", "child-abc-123"
        )
        assert not _is_child_report_source(
            "internal_report:child-abc-1232:anchor", "child-abc-123"
        )

    def test_rejects_other_child(self) -> None:
        from daemon.services.report_delivery_ledger import (
            _is_child_report_source,
        )

        assert not _is_child_report_source(
            "internal_report:child-xyz-789", "child-abc-123"
        )
        assert not _is_child_report_source(
            "internal_report:child-xyz-789:anchor-msg",
            "child-abc-123",
        )

    def test_rejects_degenerate_sources(self) -> None:
        from daemon.services.report_delivery_ledger import (
            _is_child_report_source,
        )

        # Pre-migration degradation (msg.get → None) and junk.
        assert not _is_child_report_source(None, "child-abc-123")
        assert not _is_child_report_source("", "child-abc-123")
        assert not _is_child_report_source(123, "child-abc-123")
        assert not _is_child_report_source(
            "internal_report:", "child-abc-123"
        )

    def test_source_prefix_is_no_colon(self) -> None:
        from daemon.services.report_delivery_ledger import (
            _source_prefix,
        )

        prefix = _source_prefix("child-abc-123")
        # The prefix is the NO-COLON child-boundary stem; the
        # matcher appends the colon only for the delimited form.
        assert prefix == "internal_report:child-abc-123"
        assert not prefix.endswith(":")

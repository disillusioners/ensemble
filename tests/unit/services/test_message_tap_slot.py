"""Unit tests for ``MessageTapSlot`` (Phase 1 C2).

Phase 1 C2 of the langgraph-checkpoint-perf plan. The slot fires at
the 4 approved tap sites (decisions.md D1):

* ``"user_message_entry"``           — entry path at ``_build_graph_input``
* ``"agent_node_return"``           — F2 single-return in ``agent_node``
* ``"compaction_aupdate_reactive"`` — after ``aupdate_state`` in graph.py
* ``"compaction_aupdate_messaging"`` — after ``aupdate_state`` in instance_messaging.py

Coverage
--------
* ``_extract_ids`` — dedup by ``message.id``, skip ``RemoveMessage``
  markers (D17 fold-in from Critical 8).
* ``tap_node_return`` — happy path: dedups IDs, calls
  ``upsert_batch`` via ``asyncio.to_thread`` bridge, returns rowcount.
* Failure path non-fatal — any exception in the repo (or in
  ``asyncio.to_thread``) is swallowed + WARNING-logged + returns 0.
* Empty / id-less persisted lists — no-op (returns 0, no SQL).
* Source-label round-trip — the slot's ``source`` is preserved
  through to ``log_message_tap``.

Mock repo (no SQLAlchemy, no engine) — these tests run without the
``daemon.persistence`` / ``daemon.graph`` machinery.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.services.message_tap import (
    SOURCE_AGENT_NODE_RETURN,
    SOURCE_COMPACTION_MESSAGING,
    SOURCE_COMPACTION_REACTIVE,
    SOURCE_USER_MESSAGE_ENTRY,
    MessageTapSlot,
)


# ────────────────────────────────────────────────────────────────────────
# Fake BaseMessage + mock repo helpers
# ────────────────────────────────────────────────────────────────────────


def _msg(msg_id: str | None, type_: str = "human") -> Any:
    """Build a duck-typed BaseMessage stand-in for tests."""
    m = MagicMock()
    m.id = msg_id
    m.type = type_
    return m


def _remove_marker(msg_id: str) -> Any:
    """A LangChain ``RemoveMessage(id='x', type='remove')`` marker."""
    return _msg(msg_id, type_="remove")


def _repo_double(
    rowcount: int = 0,
    *,
    raise_exc: Exception | None = None,
    capture_items: bool = False,
) -> tuple[MagicMock, list]:
    """Build a mock repo + the captured-items list.

    Returns ``(mock_repo, captured_items)``. If ``capture_items`` is
    True the mock records every ``upsert_batch(thread_id, items)``
    call into ``captured_items`` (a list of ``(thread_id, items)``
    tuples) — handy for asserting what the slot sends downstream.
    """
    captured_items: list = []
    repo = MagicMock()

    if raise_exc is not None:
        repo.upsert_batch.side_effect = raise_exc
    else:

        def _upsert(thread_id: str, items: list) -> int:
            if capture_items:
                captured_items.append((thread_id, items))
            return rowcount

        repo.upsert_batch.side_effect = _upsert

    return repo, captured_items


# ────────────────────────────────────────────────────────────────────────
# _extract_ids
# ────────────────────────────────────────────────────────────────────────


class TestExtractIds:
    """D17: filter ``RemoveMessage`` markers + dedup by ``message.id``."""

    def test_filters_remove_message_markers(self):
        """``type=='remove'`` markers are excluded from the upsert."""
        persisted = [
            _msg("h-1", type_="human"),
            _remove_marker("ghost"),
            _msg("ai-1", type_="ai"),
        ]
        ids = MessageTapSlot._extract_ids(persisted)
        assert ids == ["h-1", "ai-1"], (
            f"RemoveMessage marker must NOT appear in the upsert "
            f"items; got {ids}"
        )

    def test_dedups_duplicate_ids(self):
        """Repeated message ids collapse to one upsert entry."""
        persisted = [
            _msg("h-1"),
            _msg("h-1"),
            _msg("ai-1"),
            _msg("ai-1"),
            _msg("ai-1"),
        ]
        ids = MessageTapSlot._extract_ids(persisted)
        assert ids == ["h-1", "ai-1"]

    def test_skips_id_less_messages(self):
        """A message with ``id=None`` is silently skipped (truthy check)."""
        persisted = [_msg(None), _msg("h-1"), _msg(""), _msg("ai-1")]
        ids = MessageTapSlot._extract_ids(persisted)
        assert ids == ["h-1", "ai-1"]

    def test_empty_list_returns_empty(self):
        assert MessageTapSlot._extract_ids([]) == []

    def test_all_remove_markers_returns_empty(self):
        """A persisted list containing ONLY RemoveMessages is a no-op."""
        persisted = [_remove_marker("a"), _remove_marker("b")]
        assert MessageTapSlot._extract_ids(persisted) == []

    def test_preserves_first_appearance_order(self):
        """First-appearance order is preserved (matches the PK contract)."""
        persisted = [_msg("c"), _msg("a"), _msg("b"), _msg("a")]
        assert MessageTapSlot._extract_ids(persisted) == ["c", "a", "b"]


# ────────────────────────────────────────────────────────────────────────
# tap_node_return — happy path
# ────────────────────────────────────────────────────────────────────────


class TestTapNodeReturnHappyPath:
    """The tap bridges to the SYNC repo via ``asyncio.to_thread``."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_rowcount(self):
        """3 unique ids ⇒ repo called with 3 items ⇒ rowcount returned."""
        repo, captured = _repo_double(rowcount=3, capture_items=True)
        slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)
        persisted = [_msg("h-1"), _msg("ai-1"), _msg("h-2")]
        count = await slot.tap_node_return(persisted, "t-1")
        assert count == 3
        # Exactly ONE ``upsert_batch`` call (batched, not per-message).
        assert len(captured) == 1
        thread_id, items = captured[0]
        assert thread_id == "t-1"
        # 3 items, each shaped ``(message_id, iso_timestamp, None)``.
        assert len(items) == 3
        for (mid, ts, seq) in items:
            assert mid in {"h-1", "ai-1", "h-2"}
            assert isinstance(ts, str)  # ISO-8601 from datetime.now(...)
            assert seq is None

    @pytest.mark.asyncio
    async def test_empty_persisted_list_is_noop(self):
        """An empty persisted list ⇒ no upsert, returns 0."""
        repo, captured = _repo_double(rowcount=0, capture_items=True)
        slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)
        count = await slot.tap_node_return([], "t-1")
        assert count == 0
        assert captured == []  # NO upsert_batch call

    @pytest.mark.asyncio
    async def test_all_remove_markers_is_noop(self):
        """Persisted list with only RemoveMessages ⇒ no upsert, returns 0."""
        repo, captured = _repo_double(rowcount=0, capture_items=True)
        slot = MessageTapSlot(repo, SOURCE_COMPACTION_REACTIVE)
        persisted = [_remove_marker("a"), _remove_marker("b")]
        count = await slot.tap_node_return(persisted, "t-1")
        assert count == 0
        assert captured == []

    @pytest.mark.asyncio
    async def test_idempotent_noop_returns_repo_rowcount(self):
        """A RE-tap on already-recorded ids returns the repo's rowcount.

        The repo enforces the constraint; if the second call's
        rowcount is 0 (the constraint rejected), the slot
        faithfully returns 0 — proving first-appearance wins at
        the repo layer.
        """
        repo, captured = _repo_double(rowcount=0, capture_items=True)
        slot = MessageTapSlot(repo, SOURCE_USER_MESSAGE_ENTRY)
        count = await slot.tap_node_return([_msg("h-1")], "t-1")
        assert count == 0
        assert len(captured) == 1


# ────────────────────────────────────────────────────────────────────────
# Failure path — non-fatal, never raises
# ────────────────────────────────────────────────────────────────────────


class TestTapNodeReturnFailurePath:
    """Critical 4 — a failed upsert NEVER breaks the graph turn."""

    @pytest.mark.asyncio
    async def test_repo_raises_returns_zero_and_logs_warning(self, caplog):
        """A repo exception is caught, logged at WARNING, returns 0."""
        repo, _captured = _repo_double(raise_exc=RuntimeError("boom"))
        slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)
        with caplog.at_level(logging.WARNING, logger="daemon.services.message_tap"):
            count = await slot.tap_node_return([_msg("h-1")], "t-1")
        assert count == 0
        # WARNING log line carries the source label + a thread prefix.
        assert any(
            "[MessageTap]" in rec.message
            and "source=agent_node_return" in rec.message
            and "error=RuntimeError" in rec.message
            and "boom" in rec.message
            for rec in caplog.records
        ), f"Missing WARNING log: {[r.message for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_repo_raises_never_propagates(self):
        """A repo exception does NOT propagate out of ``tap_node_return``."""
        repo, _captured = _repo_double(
            raise_exc=ValueError("synthetic failure")
        )
        slot = MessageTapSlot(repo, SOURCE_COMPACTION_MESSAGING)
        # No exception escapes — the test passes iff the await resolves.
        count = await slot.tap_node_return([_msg("h-1")], "t-1")
        assert count == 0

    @pytest.mark.asyncio
    async def test_unexpected_repo_type_returns_zero(self, caplog):
        """A repo that doesn't match the protocol is also non-fatal."""
        # Build a repo double that returns a non-int (edge case).
        repo = MagicMock()
        repo.upsert_batch.side_effect = TypeError("wrong arg shape")
        slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)
        with caplog.at_level(logging.WARNING):
            count = await slot.tap_node_return([_msg("h-1")], "t-1")
        assert count == 0


# ────────────────────────────────────────────────────────────────────────
# log_message_tap — observability
# ────────────────────────────────────────────────────────────────────────


class TestTapNodeReturnObservability:
    """The slot emits ``[MessageTap]`` lines via ``log_message_tap``."""

    @pytest.mark.asyncio
    async def test_log_line_emitted_on_happy_path(self, caplog):
        """[MessageTap] source=<label> thread=<8-char-prefix> count=<N>."""
        repo, _captured = _repo_double(rowcount=2, capture_items=True)
        slot = MessageTapSlot(repo, SOURCE_COMPACTION_REACTIVE)
        persisted = [_msg("h-1"), _msg("ai-1")]
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            count = await slot.tap_node_return(persisted, "thread-abcdef1234567890")
        assert count == 2
        # The plan mandates the thread_id be sliced to 8 chars.
        # "thread-abcdef1234567890" -> first 8 chars -> "thread-a".
        assert any(
            "[MessageTap]" in rec.message
            and "source=compaction_aupdate_reactive" in rec.message
            and "thread=thread-a" in rec.message
            and "count=2" in rec.message
            for rec in caplog.records
        ), f"Missing [MessageTap] line: {[r.message for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_no_log_on_empty_persisted(self, caplog):
        """Empty persisted list ⇒ no upsert, no log line."""
        repo, _captured = _repo_double(rowcount=0, capture_items=True)
        slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            await slot.tap_node_return([], "t-1")
        # The checkpoint_perf logger emitted nothing for an empty tap.
        perf_logs = [r for r in caplog.records if r.name == "daemon.checkpoint_perf"]
        assert perf_logs == []

    @pytest.mark.asyncio
    async def test_no_log_on_remove_only(self, caplog):
        """All-RemoveMessage persisted list ⇒ no log line, no upsert."""
        repo, _captured = _repo_double(rowcount=0, capture_items=True)
        slot = MessageTapSlot(repo, SOURCE_USER_MESSAGE_ENTRY)
        persisted = [_remove_marker("a"), _remove_marker("b")]
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            count = await slot.tap_node_return(persisted, "t-1")
        assert count == 0
        perf_logs = [r for r in caplog.records if r.name == "daemon.checkpoint_perf"]
        assert perf_logs == []

    def test_source_property_returns_label(self):
        """The slot exposes ``source`` for diagnostics."""
        repo, _ = _repo_double()
        slot = MessageTapSlot(repo, SOURCE_COMPACTION_MESSAGING)
        assert slot.source == "compaction_aupdate_messaging"

    def test_all_four_source_constants_distinct(self):
        """The 4 source-label constants are all distinct (D1 contract)."""
        labels = {
            SOURCE_USER_MESSAGE_ENTRY,
            SOURCE_AGENT_NODE_RETURN,
            SOURCE_COMPACTION_REACTIVE,
            SOURCE_COMPACTION_MESSAGING,
        }
        assert len(labels) == 4


# ────────────────────────────────────────────────────────────────────────
# asyncio.to_thread bridge
# ────────────────────────────────────────────────────────────────────────


class TestAsyncBridge:
    """The slot runs the SYNC repo on the default executor."""

    @pytest.mark.asyncio
    async def test_repo_runs_on_thread_executor(self):
        """``upsert_batch`` is invoked from a thread OTHER than the loop's.

        This proves the ``asyncio.to_thread`` bridge is wired. The
        check is conservative — the loop has exactly one thread
        (the main thread); the executor pool is multi-threaded so
        the captured ``threading.current_thread()`` name is distinct.
        """
        import threading

        captured_thread_name: list[str] = []

        def _upsert(thread_id, items):
            captured_thread_name.append(threading.current_thread().name)
            return 1

        repo = MagicMock()
        repo.upsert_batch.side_effect = _upsert

        slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)
        # main thread is "MainTest" (pytest) or "asyncio.tests" etc.
        main_thread_name = threading.current_thread().name
        await slot.tap_node_return([_msg("h-1")], "t-1")
        assert len(captured_thread_name) == 1
        # The captured thread name must NOT be the test's main thread.
        assert captured_thread_name[0] != main_thread_name, (
            f"upsert_batch ran on the test's main thread "
            f"({main_thread_name}) — asyncio.to_thread bridge not active"
        )


# ────────────────────────────────────────────────────────────────────────
# asyncio.to_thread cancellation handling
# ────────────────────────────────────────────────────────────────────────


class TestCancellationHandling:
    """``CancelledError`` propagates UP (it's not a regular exception).

    The plan's failure-path guarantee is for non-fatal errors (DB
    down, schema drift, etc.). ``CancelledError`` is the asyncio
    cancel signal — it MUST propagate so the graph turn can be
    cancelled cleanly. The slot catches ``Exception`` (the non-fatal
    base class), NOT ``BaseException``, so CancelledError reaches
    the outer await without being swallowed.
    """

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        """asyncio.CancelledError MUST propagate out of the tap."""
        repo = MagicMock()
        repo.upsert_batch.side_effect = asyncio.CancelledError()
        slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)
        with pytest.raises(asyncio.CancelledError):
            await slot.tap_node_return([_msg("h-1")], "t-1")
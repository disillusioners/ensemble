"""Unit tests for the user message injection queue (Phase 1 / W1, S1, Phase 3).

Covers:
    * InstanceManager.set_injection / get_injection / clear_injection /
      get_injection_count
    * Append-list semantics (Phase 3): multiple messages queue up
    * Idempotent clear
    * Centralized _cleanup_instance_state helper
    * TTL sweeper _cleanup_stale_injections (queues, not single slots)

These tests construct a minimal ``InstanceManager`` stand-in object that
exposes only the methods/attributes the slot helpers touch. The goal is to
exercise the slot mechanics without spinning up the real ``InstanceManager``
(which requires a database, MCP pool, repositories, etc.).

The real InstanceManager class is exercised end-to-end in test_manager.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _make_manager_with_pending_dict():
    """Build a minimal stand-in for InstanceManager exposing only the slot surface.

    We bind the manager module's helper methods (set_injection, etc.)
    onto the stand-in so we can test the slot semantics without
    constructing a real ``InstanceManager`` (which requires a database,
    MCP pool, repositories, etc.).
    """
    from daemon import manager as manager_module

    class _ManagerStub:
        """Minimal stand-in for InstanceManager — only exposes slot surface."""

        # Type-annotated slots so the test file LSP-checks cleanly without
        # constructing the real InstanceManager. The runtime values are
        # bound at __init__ via ``setattr`` (the daemon module is imported
        # at function-call time, not class-definition time, so we cannot
        # put the real ``InstanceManager.set_injection`` as a class-level
        # method binding).
        set_injection: Any
        get_injection: Any
        get_injection_count: Any
        clear_injection: Any
        _cleanup_instance_state: Any
        _cleanup_stale_injections: Any
        # Class-level constant read by _cleanup_stale_injections default.
        # Use the real value (1h) so tests can exercise the default
        # behavior without a TTL override.
        _INJECTION_TTL_SECONDS = (
            manager_module.InstanceManager._INJECTION_TTL_SECONDS
        )

        def __init__(self):
            self._pending_injections: dict[str, list[dict[str, str]]] = {}
            self._graph_tasks: dict = {}
            self._gii_throttle: dict = {}
            self._loop_breaker_state: dict = {}
            self._deferred_question_pause: set[str] = set()
            self._question_pause_requested: dict = {}
            self._question_manager = MagicMock()
            self._question_manager.clear_question_pack = MagicMock()
            self.release_context_usage_cache = MagicMock()
            self.clear_question_pause_requested = MagicMock()
            # Bind the real helpers as instance methods.
            self.set_injection = manager_module.InstanceManager.set_injection.__get__(self)
            self.get_injection = manager_module.InstanceManager.get_injection.__get__(self)
            self.get_injection_count = manager_module.InstanceManager.get_injection_count.__get__(self)
            self.clear_injection = manager_module.InstanceManager.clear_injection.__get__(self)
            self._cleanup_instance_state = manager_module.InstanceManager._cleanup_instance_state.__get__(self)
            self._cleanup_stale_injections = manager_module.InstanceManager._cleanup_stale_injections.__get__(self)

    return _ManagerStub()


# ---------------------------------------------------------------------------
# Slot mechanics (Phase 3 append-list semantics)
# ---------------------------------------------------------------------------


class TestSlotMechanics:
    """set / get / clear / count semantics for the RAM injection queue."""

    def test_set_then_get_returns_content(self):
        mgr = _make_manager_with_pending_dict()
        stored = mgr.set_injection("iid-1", "hello")

        assert stored["content"] == "hello"
        assert "timestamp" in stored

        fetched = mgr.get_injection("iid-1")
        assert fetched is not None
        # Phase 3: get returns a LIST (FIFO queue), oldest first.
        assert isinstance(fetched, list)
        assert len(fetched) == 1
        assert fetched[0]["content"] == "hello"

    def test_set_twice_appends_to_queue(self):
        """Phase 3: two set_injection calls APPEND; both messages survive."""
        mgr = _make_manager_with_pending_dict()
        first = mgr.set_injection("iid-1", "first")
        second = mgr.set_injection("iid-1", "second")

        fetched = mgr.get_injection("iid-1")
        assert fetched is not None
        assert len(fetched) == 2
        # FIFO order — oldest first
        assert fetched[0]["content"] == "first"
        assert fetched[1]["content"] == "second"
        # Returned entry is the newly appended one
        assert second["content"] == "second"

    def test_set_three_times_preserves_fifo_order(self):
        """Three messages queue up in insertion order."""
        mgr = _make_manager_with_pending_dict()
        mgr.set_injection("iid-1", "msg-A")
        mgr.set_injection("iid-1", "msg-B")
        mgr.set_injection("iid-1", "msg-C")

        fetched = mgr.get_injection("iid-1")
        assert fetched is not None
        assert [e["content"] for e in fetched] == ["msg-A", "msg-B", "msg-C"]

    def test_get_after_clear_returns_none(self):
        mgr = _make_manager_with_pending_dict()
        mgr.set_injection("iid-1", "hello")
        mgr.clear_injection("iid-1")

        assert mgr.get_injection("iid-1") is None

    def test_clear_when_empty_is_safe(self):
        mgr = _make_manager_with_pending_dict()
        # No injection exists; clear must not raise
        assert mgr.clear_injection("iid-empty") is None

    def test_clear_returns_full_queue(self):
        """Phase 3: clear returns the entire list, not just one entry."""
        mgr = _make_manager_with_pending_dict()
        mgr.set_injection("iid-1", "first")
        mgr.set_injection("iid-1", "second")

        cleared = mgr.clear_injection("iid-1")
        assert cleared is not None
        assert isinstance(cleared, list)
        assert len(cleared) == 2
        assert cleared[0]["content"] == "first"
        assert cleared[1]["content"] == "second"
        # And the slot is empty afterwards
        assert mgr.get_injection("iid-1") is None

    def test_get_does_not_clear(self):
        """Peek must be non-destructive — consumption is a separate step."""
        mgr = _make_manager_with_pending_dict()
        mgr.set_injection("iid-1", "peek-only")
        mgr.set_injection("iid-1", "more-peek")

        # Multiple gets should all return the same list
        first = mgr.get_injection("iid-1")
        second = mgr.get_injection("iid-1")
        third = mgr.get_injection("iid-1")

        assert first is not None
        assert second is not None
        assert third is not None
        assert len(first) == 2
        assert [e["content"] for e in first] == ["peek-only", "more-peek"]
        # And it's still there
        assert mgr.get_injection("iid-1") is not None

    def test_get_injection_count_returns_queue_depth(self):
        """Phase 3: get_injection_count returns the number of pending entries."""
        mgr = _make_manager_with_pending_dict()
        assert mgr.get_injection_count("iid-1") == 0

        mgr.set_injection("iid-1", "msg-1")
        assert mgr.get_injection_count("iid-1") == 1

        mgr.set_injection("iid-1", "msg-2")
        mgr.set_injection("iid-1", "msg-3")
        assert mgr.get_injection_count("iid-1") == 3

        # Clear resets the count
        mgr.clear_injection("iid-1")
        assert mgr.get_injection_count("iid-1") == 0

    def test_get_injection_count_zero_for_unknown_instance(self):
        """get_injection_count on an instance with no queue returns 0, not None."""
        mgr = _make_manager_with_pending_dict()
        assert mgr.get_injection_count("iid-unknown") == 0

    def test_set_injection_timestamp_is_iso_utc(self):
        mgr = _make_manager_with_pending_dict()
        stored = mgr.set_injection("iid-1", "ts-test")

        ts = stored["timestamp"]
        # Must round-trip via fromisoformat (with possible trailing 'Z'
        # normalization) — the implementation uses timezone.utc.isoformat()
        # which produces '+00:00' suffix.
        normalized = ts.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        assert parsed.tzinfo is not None

    def test_independent_instances_do_not_collide(self):
        mgr = _make_manager_with_pending_dict()
        mgr.set_injection("iid-A", "for-A")
        mgr.set_injection("iid-B", "for-B")

        assert [e["content"] for e in mgr.get_injection("iid-A")] == ["for-A"]
        assert [e["content"] for e in mgr.get_injection("iid-B")] == ["for-B"]

        # Clearing A must not affect B
        mgr.clear_injection("iid-A")
        assert mgr.get_injection("iid-A") is None
        assert mgr.get_injection("iid-B") is not None

    def test_get_returns_defensive_copy(self):
        """get_injection returns a copy so the caller can't mutate internal state."""
        mgr = _make_manager_with_pending_dict()
        mgr.set_injection("iid-1", "orig")

        queue = mgr.get_injection("iid-1")
        assert queue is not None
        # Mutate the returned list and re-get — original should be intact
        queue.append({"content": "tampered", "timestamp": "now"})
        queue.append({"content": "tampered2", "timestamp": "now"})

        fresh = mgr.get_injection("iid-1")
        assert fresh is not None
        assert len(fresh) == 1
        assert fresh[0]["content"] == "orig"


# ---------------------------------------------------------------------------
# Centralized cleanup helper
# ---------------------------------------------------------------------------


class TestCleanupInstanceState:
    """W1: _cleanup_instance_state must clear all three dicts in one call."""

    def test_clears_all_three_dicts(self):
        mgr = _make_manager_with_pending_dict()
        # Populate all three dicts — use a list with two entries to
        # verify cleanup drops the entire queue, not just one entry.
        mgr._pending_injections["iid-1"] = [
            {"content": "x", "timestamp": "now"},
            {"content": "y", "timestamp": "now"},
        ]
        mgr._graph_tasks["iid-1"] = MagicMock(name="fake_task")

        result = mgr._cleanup_instance_state("iid-1")

        assert mgr._pending_injections == {}
        assert "iid-1" not in mgr._graph_tasks
        mgr.release_context_usage_cache.assert_called_once_with("iid-1")

        # Returned dict must carry the cleared queue so callers (pause-cascade)
        # can forward them to SSE without re-querying the manager.
        assert result["cleared_injection"] == [
            {"content": "x", "timestamp": "now"},
            {"content": "y", "timestamp": "now"},
        ]
        assert result["context_usage_cleared"] is True
        assert result["graph_task"] is not None

    def test_clears_when_only_injection_present(self):
        mgr = _make_manager_with_pending_dict()
        mgr._pending_injections["iid-1"] = [
            {"content": "y", "timestamp": "now"},
            {"content": "z", "timestamp": "now"},
        ]

        result = mgr._cleanup_instance_state("iid-1")

        assert mgr._pending_injections == {}
        assert result["cleared_injection"] == [
            {"content": "y", "timestamp": "now"},
            {"content": "z", "timestamp": "now"},
        ]
        assert result["graph_task"] is None

    def test_clears_when_no_state_present(self):
        """No entries exist — must not raise, return None cleared values."""
        mgr = _make_manager_with_pending_dict()

        result = mgr._cleanup_instance_state("iid-empty")

        assert result["cleared_injection"] is None
        assert result["graph_task"] is None
        assert result["context_usage_cleared"] is True


# ---------------------------------------------------------------------------
# TTL sweeper
# ---------------------------------------------------------------------------


class TestTTLSweeper:
    """S1: _cleanup_stale_injections drops queues older than the TTL."""

    def test_drops_queues_older_than_ttl(self):
        """Oldest entry's timestamp drives the staleness decision."""
        mgr = _make_manager_with_pending_dict()
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        # Queue with an OLD head and a FRESH tail — the OLD head wins.
        mgr._pending_injections["old"] = [
            {"content": "stale", "timestamp": old_ts},
            {"content": "fresh-tail", "timestamp": datetime.now(timezone.utc).isoformat()},
        ]
        mgr._pending_injections["fresh"] = [
            {"content": "fresh", "timestamp": datetime.now(timezone.utc).isoformat()},
        ]

        removed = mgr._cleanup_stale_injections(ttl_seconds=3600)

        assert removed == 1
        assert "old" not in mgr._pending_injections
        assert "fresh" in mgr._pending_injections

    def test_keeps_recent_queues(self):
        mgr = _make_manager_with_pending_dict()
        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        mgr._pending_injections["recent"] = [
            {"content": "ok", "timestamp": recent_ts},
            {"content": "ok-2", "timestamp": recent_ts},
        ]

        removed = mgr._cleanup_stale_injections(ttl_seconds=3600)

        assert removed == 0
        assert "recent" in mgr._pending_injections

    def test_unparseable_timestamp_treated_as_stale(self):
        mgr = _make_manager_with_pending_dict()
        mgr._pending_injections["broken"] = [
            {"content": "?", "timestamp": "not-a-date"},
        ]

        removed = mgr._cleanup_stale_injections(ttl_seconds=3600)

        assert removed == 1
        assert "broken" not in mgr._pending_injections

    def test_missing_timestamp_treated_as_stale(self):
        mgr = _make_manager_with_pending_dict()
        mgr._pending_injections["nots"] = [{"content": "?"}]  # no timestamp key

        removed = mgr._cleanup_stale_injections(ttl_seconds=3600)

        assert removed == 1
        assert "nots" not in mgr._pending_injections

    def test_empty_queue_dropped(self):
        """Phase 3: empty queues must be dropped (no orphans)."""
        mgr = _make_manager_with_pending_dict()
        mgr._pending_injections["empty"] = []

        removed = mgr._cleanup_stale_injections(ttl_seconds=3600)

        # Empty queue is treated as stale so it can't accumulate.
        assert removed == 1
        assert "empty" not in mgr._pending_injections

    def test_default_ttl_is_one_hour(self):
        """Manager default must be 1h (matches phase-1 plan)."""
        mgr = _make_manager_with_pending_dict()
        # 30 minutes old — must NOT be swept at default TTL
        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        mgr._pending_injections["recent"] = [
            {"content": "ok", "timestamp": recent_ts},
        ]
        # 90 minutes old — MUST be swept at default TTL
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
        mgr._pending_injections["old"] = [
            {"content": "stale", "timestamp": old_ts},
        ]

        removed = mgr._cleanup_stale_injections()  # default

        assert removed == 1
        assert "recent" in mgr._pending_injections
        assert "old" not in mgr._pending_injections

    def test_zero_or_negative_ttl_is_noop(self):
        """Defensive guard: ttl <= 0 must NOT silently wipe everything."""
        mgr = _make_manager_with_pending_dict()
        old_ts = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        mgr._pending_injections["ancient"] = [
            {"content": "old", "timestamp": old_ts},
        ]

        assert mgr._cleanup_stale_injections(ttl_seconds=0) == 0
        assert mgr._cleanup_stale_injections(ttl_seconds=-1) == 0
        # Entries still present — we don't accidentally wipe on misconfig
        assert "ancient" in mgr._pending_injections

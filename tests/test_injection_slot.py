"""Unit tests for the user message injection slot (Phase 1 / W1, S1).

Covers:
    * InstanceManager.set_injection / get_injection / clear_injection
    * Single-slot replace semantics
    * Idempotent clear
    * Centralized _cleanup_instance_state helper
    * TTL sweeper _cleanup_stale_injections

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
            self._pending_injections: dict[str, dict[str, str]] = {}
            self._graph_tasks: dict = {}
            self.release_context_usage_cache = MagicMock()
            # Bind the real helpers as instance methods.
            self.set_injection = manager_module.InstanceManager.set_injection.__get__(self)
            self.get_injection = manager_module.InstanceManager.get_injection.__get__(self)
            self.clear_injection = manager_module.InstanceManager.clear_injection.__get__(self)
            self._cleanup_instance_state = manager_module.InstanceManager._cleanup_instance_state.__get__(self)
            self._cleanup_stale_injections = manager_module.InstanceManager._cleanup_stale_injections.__get__(self)

    return _ManagerStub()


# ---------------------------------------------------------------------------
# Slot mechanics
# ---------------------------------------------------------------------------


class TestSlotMechanics:
    """set / get / clear semantics for the RAM injection slot."""

    def test_set_then_get_returns_content(self):
        mgr = _make_manager_with_pending_dict()
        stored = mgr.set_injection("iid-1", "hello")

        assert stored["content"] == "hello"
        assert "timestamp" in stored

        fetched = mgr.get_injection("iid-1")
        assert fetched is not None
        assert fetched["content"] == "hello"

    def test_set_twice_replaces_first(self):
        mgr = _make_manager_with_pending_dict()
        mgr.set_injection("iid-1", "first")
        mgr.set_injection("iid-1", "second")

        fetched = mgr.get_injection("iid-1")
        assert fetched["content"] == "second"

    def test_get_after_clear_returns_none(self):
        mgr = _make_manager_with_pending_dict()
        mgr.set_injection("iid-1", "hello")
        mgr.clear_injection("iid-1")

        assert mgr.get_injection("iid-1") is None

    def test_clear_when_empty_is_safe(self):
        mgr = _make_manager_with_pending_dict()
        # No injection exists; clear must not raise
        assert mgr.clear_injection("iid-empty") is None

    def test_clear_returns_cleared_entry(self):
        mgr = _make_manager_with_pending_dict()
        mgr.set_injection("iid-1", "captured")

        cleared = mgr.clear_injection("iid-1")
        assert cleared is not None
        assert cleared["content"] == "captured"
        # And the slot is empty afterwards
        assert mgr.get_injection("iid-1") is None

    def test_get_does_not_clear(self):
        """Peek must be non-destructive — consumption is a separate step."""
        mgr = _make_manager_with_pending_dict()
        mgr.set_injection("iid-1", "peek-only")

        # Multiple gets should all return the same entry
        first = mgr.get_injection("iid-1")
        second = mgr.get_injection("iid-1")
        third = mgr.get_injection("iid-1")

        assert first is not None
        assert second is not None
        assert third is not None
        assert first["content"] == "peek-only"
        # And it's still there
        assert mgr.get_injection("iid-1") is not None

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

        assert mgr.get_injection("iid-A")["content"] == "for-A"
        assert mgr.get_injection("iid-B")["content"] == "for-B"

        # Clearing A must not affect B
        mgr.clear_injection("iid-A")
        assert mgr.get_injection("iid-A") is None
        assert mgr.get_injection("iid-B") is not None


# ---------------------------------------------------------------------------
# Centralized cleanup helper
# ---------------------------------------------------------------------------


class TestCleanupInstanceState:
    """W1: _cleanup_instance_state must clear all three dicts in one call."""

    def test_clears_all_three_dicts(self):
        mgr = _make_manager_with_pending_dict()
        # Populate all three dicts
        mgr._pending_injections["iid-1"] = {"content": "x", "timestamp": "now"}
        mgr._graph_tasks["iid-1"] = MagicMock(name="fake_task")

        result = mgr._cleanup_instance_state("iid-1")

        assert mgr._pending_injections == {}
        assert "iid-1" not in mgr._graph_tasks
        mgr.release_context_usage_cache.assert_called_once_with("iid-1")

        # Returned dict must carry the cleared items so callers (pause-cascade)
        # can forward them to SSE without re-querying the manager.
        assert result["cleared_injection"] == {"content": "x", "timestamp": "now"}
        assert result["context_usage_cleared"] is True
        assert result["graph_task"] is not None

    def test_clears_when_only_injection_present(self):
        mgr = _make_manager_with_pending_dict()
        mgr._pending_injections["iid-1"] = {"content": "y", "timestamp": "now"}

        result = mgr._cleanup_instance_state("iid-1")

        assert mgr._pending_injections == {}
        assert result["cleared_injection"] == {"content": "y", "timestamp": "now"}
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
    """S1: _cleanup_stale_injections drops entries older than the TTL."""

    def test_drops_entries_older_than_ttl(self):
        mgr = _make_manager_with_pending_dict()
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        mgr._pending_injections["old"] = {"content": "stale", "timestamp": old_ts}
        mgr._pending_injections["fresh"] = {"content": "fresh", "timestamp": datetime.now(timezone.utc).isoformat()}

        removed = mgr._cleanup_stale_injections(ttl_seconds=3600)

        assert removed == 1
        assert "old" not in mgr._pending_injections
        assert "fresh" in mgr._pending_injections

    def test_keeps_recent_entries(self):
        mgr = _make_manager_with_pending_dict()
        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        mgr._pending_injections["recent"] = {"content": "ok", "timestamp": recent_ts}

        removed = mgr._cleanup_stale_injections(ttl_seconds=3600)

        assert removed == 0
        assert "recent" in mgr._pending_injections

    def test_unparseable_timestamp_treated_as_stale(self):
        mgr = _make_manager_with_pending_dict()
        mgr._pending_injections["broken"] = {"content": "?", "timestamp": "not-a-date"}

        removed = mgr._cleanup_stale_injections(ttl_seconds=3600)

        assert removed == 1
        assert "broken" not in mgr._pending_injections

    def test_missing_timestamp_treated_as_stale(self):
        mgr = _make_manager_with_pending_dict()
        mgr._pending_injections["nots"] = {"content": "?"}  # no timestamp key

        removed = mgr._cleanup_stale_injections(ttl_seconds=3600)

        assert removed == 1
        assert "nots" not in mgr._pending_injections

    def test_default_ttl_is_one_hour(self):
        """Manager default must be 1h (matches phase-1 plan)."""
        mgr = _make_manager_with_pending_dict()
        # 30 minutes old — must NOT be swept at default TTL
        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        mgr._pending_injections["recent"] = {"content": "ok", "timestamp": recent_ts}
        # 90 minutes old — MUST be swept at default TTL
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
        mgr._pending_injections["old"] = {"content": "stale", "timestamp": old_ts}

        removed = mgr._cleanup_stale_injections()  # default

        assert removed == 1
        assert "recent" in mgr._pending_injections
        assert "old" not in mgr._pending_injections

    def test_zero_or_negative_ttl_is_noop(self):
        """Defensive guard: ttl <= 0 must NOT silently wipe everything."""
        mgr = _make_manager_with_pending_dict()
        old_ts = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        mgr._pending_injections["ancient"] = {"content": "old", "timestamp": old_ts}

        assert mgr._cleanup_stale_injections(ttl_seconds=0) == 0
        assert mgr._cleanup_stale_injections(ttl_seconds=-1) == 0
        # Entries still present — we don't accidentally wipe on misconfig
        assert "ancient" in mgr._pending_injections
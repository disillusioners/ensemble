"""W3 cleanup-path review tests for the user message injection slot.

These tests verify that each lifecycle cleanup path properly clears the
RAM injection slot for an instance and returns the cleared content under
its documented contract. We follow the ``_ManagerStub`` method-binding
pattern from ``test_injection_slot.py`` to exercise the slot mechanics
without spinning up the full ``InstanceManager`` (which requires a
database, MCP pool, repositories, etc.).

Coverage:

    * ``test_terminate_clears_injection`` — ``terminate_instance`` invokes
      ``clear_injection``; the slot is wiped and the popped entry is
      returned for the post-commit ``injection_cleared`` SSE emit.
    * ``test_clear_all_clears_injection`` — ``clear_all_instances`` does
      a bulk ``_pending_injections.clear()`` (admin/reset path: any
      in-flight injection not yet checkpoint-persisted is discarded).
    * ``test_project_delete_clears_injection`` — ``delete_project`` calls
      the centralized ``_cleanup_instance_state`` helper per instance;
      the returned dict surfaces the cleared entry under
      ``cleared_injection`` for the SSE cascade.

Note: the full ``terminate_instance`` / ``clear_all_instances`` /
``delete_project`` paths require the full lifecycle service stack (DB
sessions, async events, request registry, etc.). Each test exercises the
exact in-memory cleanup call that the named path makes, which is what
determines whether the RAM slot is wiped — the same test boundary used
by ``TestCleanupInstanceState`` in ``test_injection_slot.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


def _make_manager_with_pending_dict():
    """Stand-in for InstanceManager exposing only the slot surface."""
    from daemon import manager as manager_module

    class _ManagerStub:
        """Minimal stand-in for InstanceManager — only the slot surface."""

        set_injection: Any
        get_injection: Any
        clear_injection: Any
        _cleanup_instance_state: Any

        def __init__(self):
            self._pending_injections: dict[str, dict[str, str]] = {}
            self._graph_tasks: dict = {}
            self.release_context_usage_cache = MagicMock()
            # Bind the real helpers as instance methods.
            self.set_injection = (
                manager_module.InstanceManager.set_injection.__get__(self)
            )
            self.get_injection = (
                manager_module.InstanceManager.get_injection.__get__(self)
            )
            self.clear_injection = (
                manager_module.InstanceManager.clear_injection.__get__(self)
            )
            self._cleanup_instance_state = (
                manager_module.InstanceManager._cleanup_instance_state.__get__(self)
            )

    return _ManagerStub()


def test_terminate_clears_injection():
    """terminate_instance path: clear_injection wipes slot and returns entry.

    ``InstanceLifecycleService.terminate_instance`` calls
    ``self._manager.clear_injection(instance_id)`` and captures the
    returned dict for the post-commit ``injection_cleared`` SSE emit.
    """
    mgr = _make_manager_with_pending_dict()
    mgr.set_injection("iid-1", "terminate-msg")

    cleared = mgr.clear_injection("iid-1")  # what terminate invokes

    # Slot is wiped
    assert mgr.get_injection("iid-1") is None
    # Return contract: popped entry, suitable for SSE forwarding
    assert cleared is not None
    assert cleared["content"] == "terminate-msg"
    assert "timestamp" in cleared
    # Idempotent — second clear is safe
    assert mgr.clear_injection("iid-1") is None


def test_clear_all_clears_injection():
    """clear_all_instances path: bulk _pending_injections.clear() wipes slot.

    ``InstanceLifecycleService.clear_all_instances`` bulk-clears the
    slot via ``self._manager._pending_injections.clear()`` because no
    SSE consumer survives a full reset. The return value is the int
    count from ``_instance_repository.delete_all()`` — not a slot
    concern, so this test asserts only the in-memory wipe.
    """
    mgr = _make_manager_with_pending_dict()
    mgr.set_injection("iid-1", "first")
    mgr.set_injection("iid-2", "second")
    # Sanity: both are pending before the bulk clear
    assert len(mgr._pending_injections) == 2

    mgr._pending_injections.clear()  # what clear_all_instances invokes

    assert mgr.get_injection("iid-1") is None
    assert mgr.get_injection("iid-2") is None
    assert mgr._pending_injections == {}


def test_project_delete_clears_injection():
    """delete_project path: _cleanup_instance_state clears slot and surfaces entry.

    ``routers/projects.py::delete_project`` loops over project instances
    and calls ``manager._cleanup_instance_state(instance_id)`` per
    instance. The helper's return dict carries ``cleared_injection`` so
    the route can forward the cleared entry to SSE without re-querying
    the manager.
    """
    mgr = _make_manager_with_pending_dict()
    mgr.set_injection("iid-1", "delete-msg")

    result = mgr._cleanup_instance_state("iid-1")  # what delete_project invokes

    # Slot is wiped
    assert mgr.get_injection("iid-1") is None
    # Return contract: helper surfaces the cleared entry
    assert result["cleared_injection"] is not None
    assert result["cleared_injection"]["content"] == "delete-msg"
    assert result["context_usage_cleared"] is True
    assert result["graph_task"] is None
    mgr.release_context_usage_cache.assert_called_once_with("iid-1")

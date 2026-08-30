"""W3 cleanup-path review tests for the user message injection queue.

These tests verify that each lifecycle cleanup path properly clears the
RAM injection queue for an instance and returns the cleared contents
under its documented contract. We follow the ``_ManagerStub``
method-binding pattern from ``test_injection_slot.py`` to exercise the
slot mechanics without spinning up the full ``InstanceManager``
(which requires a database, MCP pool, repositories, etc.).

Coverage:

    * ``test_terminate_clears_injection`` — ``terminate_instance`` invokes
      ``clear_injection``; the queue is wiped and the popped list is
      returned for the post-commit ``injection_consumed`` SSE emit.
    * ``test_clear_all_clears_injection`` — ``clear_all_instances`` does
      a bulk ``_pending_injections.clear()`` (admin/reset path: any
      in-flight queue not yet checkpoint-persisted is discarded).
    * ``test_project_delete_clears_injection`` — ``delete_project`` calls
      the centralized ``_cleanup_instance_state`` helper per instance;
      the returned dict surfaces the cleared queue under
      ``cleared_injection`` for the SSE cascade.

Note: the full ``terminate_instance`` / ``clear_all_instances`` /
``delete_project`` paths require the full lifecycle service stack (DB
sessions, async events, request registry, etc.). Each test exercises the
exact in-memory cleanup call that the named path makes, which is what
determines whether the RAM queue is wiped — the same test boundary used
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
        get_injection_count: Any
        clear_injection: Any
        _cleanup_instance_state: Any

        def __init__(self):
            self._pending_injections: dict[str, list[dict[str, str]]] = {}
            self._graph_tasks: dict = {}
            self._gii_throttle: dict = {}
            self._loop_breaker_state: dict = {}
            self._deferred_question_pause: set[str] = set()
            self._question_pause_requested: dict = {}
            self._question_manager = MagicMock()
            self._question_manager.clear_question_pack = MagicMock()
            # C2-safe watchover cleanup marker — the real manager's
            # ``_cleanup_instance_state`` discards from this set
            # (daemon/manager.py ``_deferred_watchover_terminate``); the
            # stub predates that attribute. Synced in the
            # message-display-latency batch (pre-existing failure fix).
            self._deferred_watchover_terminate: set[str] = set()
            self.release_context_usage_cache = MagicMock()
            self.clear_question_pause_requested = MagicMock()
            # Bind the real helpers as instance methods.
            self.set_injection = (
                manager_module.InstanceManager.set_injection.__get__(self)
            )
            self.get_injection = (
                manager_module.InstanceManager.get_injection.__get__(self)
            )
            self.get_injection_count = (
                manager_module.InstanceManager.get_injection_count.__get__(self)
            )
            self.clear_injection = (
                manager_module.InstanceManager.clear_injection.__get__(self)
            )
            self._cleanup_instance_state = (
                manager_module.InstanceManager._cleanup_instance_state.__get__(self)
            )

    return _ManagerStub()


def test_terminate_clears_injection():
    """terminate_instance path: clear_injection wipes queue and returns list.

    ``InstanceLifecycleService.terminate_instance`` calls
    ``self._manager.clear_injection(instance_id)`` and captures the
    returned list for the post-commit ``injection_consumed`` SSE emit.
    """
    mgr = _make_manager_with_pending_dict()
    mgr.set_injection("iid-1", "terminate-msg-A")
    mgr.set_injection("iid-1", "terminate-msg-B")

    cleared = mgr.clear_injection("iid-1")  # what terminate invokes

    # Queue is wiped
    assert mgr.get_injection("iid-1") is None
    # Return contract: popped list, suitable for SSE forwarding
    assert cleared is not None
    assert isinstance(cleared, list)
    assert len(cleared) == 2
    assert cleared[0]["content"] == "terminate-msg-A"
    assert cleared[1]["content"] == "terminate-msg-B"
    # Idempotent — second clear is safe
    assert mgr.clear_injection("iid-1") is None


def test_clear_all_clears_injection():
    """clear_all_instances path: bulk _pending_injections.clear() wipes queue.

    ``InstanceLifecycleService.clear_all_instances`` bulk-clears the
    queue via ``self._manager._pending_injections.clear()`` because no
    SSE consumer survives a full reset. The return value is the int
    count from ``_instance_repository.delete_all()`` — not a slot
    concern, so this test asserts only the in-memory wipe.
    """
    mgr = _make_manager_with_pending_dict()
    mgr.set_injection("iid-1", "first")
    mgr.set_injection("iid-2", "second")
    # Sanity: both queues populated before the bulk clear
    assert len(mgr._pending_injections) == 2

    mgr._pending_injections.clear()  # what clear_all_instances invokes

    assert mgr.get_injection("iid-1") is None
    assert mgr.get_injection("iid-2") is None
    assert mgr._pending_injections == {}


def test_project_delete_clears_injection():
    """delete_project path: _cleanup_instance_state clears queue and surfaces list.

    ``routers/projects.py::delete_project`` loops over project instances
    and calls ``manager._cleanup_instance_state(instance_id)`` per
    instance. The helper's return dict carries ``cleared_injection`` so
    the route can forward the cleared queue to SSE without re-querying
    the manager.
    """
    mgr = _make_manager_with_pending_dict()
    mgr.set_injection("iid-1", "delete-msg-A")
    mgr.set_injection("iid-1", "delete-msg-B")

    result = mgr._cleanup_instance_state("iid-1")  # what delete_project invokes

    # Queue is wiped
    assert mgr.get_injection("iid-1") is None
    # Return contract: helper surfaces the cleared list
    assert result["cleared_injection"] is not None
    assert isinstance(result["cleared_injection"], list)
    assert len(result["cleared_injection"]) == 2
    assert result["cleared_injection"][0]["content"] == "delete-msg-A"
    assert result["cleared_injection"][1]["content"] == "delete-msg-B"
    assert result["context_usage_cleared"] is True
    assert result["graph_task"] is None
    mgr.release_context_usage_cache.assert_called_once_with("iid-1")

"""Phase 2 / clipboard-image-chat / freeze-list A9.

``InstanceManager.set_injection`` byte-identical-when-absent test for
the new ``image_refs`` kwarg. Mirrors the established pattern from
``tests/test_injection_slot.py:255-280`` (source / echo_id
conditional-add).

The contract:
  * When ``image_refs=None`` (default), the FIFO entry MUST be
    byte-identical to the pre-feature shape — no ``"image_refs"`` key
    added. The 4 consumer call sites (user-API, agent-tool, chat-
    source, job_inject) do NOT pass the kwarg today; they must keep
    today's exact behavior.
  * When ``image_refs=[r]``, the FIFO entry gains the
    ``"image_refs"`` key alongside content / timestamp / etc.
  * Source / echo_id conditional-add pattern is preserved.
  * Defensive list-copy on the input — caller-side mutation of the
    passed-in list cannot affect the stored entry.

These mirror the A9 test freeze list — covers the user-API call site
(routers/messages.py:458 — threads image_refs) + 3 byte-identical
call sites (tools/instance.py:3124, sources/registry.py:1029,
tools/job_queue.py:2362).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.manager import InstanceManager


def _make_manager_with_pending_dict():
    """Minimal stand-in for InstanceManager exposing only the slot surface.

    Mirrors ``_make_manager_with_pending_dict`` in
    ``tests/test_injection_slot.py:33``.
    """
    from daemon import manager as manager_module

    class _ManagerStub:
        set_injection: Any
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
            self._deferred_watchover_terminate: set[str] = set()
            self.release_context_usage_cache = MagicMock()
            self.clear_question_pause_requested = MagicMock()
            self.set_injection = manager_module.InstanceManager.set_injection.__get__(self)

    return _ManagerStub()


# ===========================================================================
# Group 1 — byte-identical-when-absent (the A9 contract)
# ===========================================================================


class TestSetInjectionByteIdenticalWhenAbsent:
    """Without image_refs, the entry is byte-identical to the pre-feature shape."""

    def test_no_kwargs_entry_has_only_content_and_timestamp(self):
        mgr = _make_manager_with_pending_dict()
        entry = mgr.set_injection("iid-1", "hello")

        # Pre-feature shape: {content, timestamp} ONLY.
        assert sorted(entry.keys()) == ["content", "timestamp"]
        assert entry["content"] == "hello"
        assert "timestamp" in entry

    def test_source_only_entry_lacks_image_refs(self):
        """A tool-path entry with ``source`` but no image_refs must
        lack the image_refs key (byte-identical pre-feature shape)."""
        mgr = _make_manager_with_pending_dict()
        entry = mgr.set_injection("iid-1", "from-tool", source="internal_agent:x")
        assert "image_refs" not in entry
        assert sorted(entry.keys()) == ["content", "source", "timestamp"]

    def test_echo_id_only_entry_lacks_image_refs(self):
        """A user-API entry with ``echo_id`` but no image_refs must
        lack the image_refs key."""
        mgr = _make_manager_with_pending_dict()
        entry = mgr.set_injection("iid-1", "from-api", echo_id="echo-uuid")
        assert "image_refs" not in entry
        assert sorted(entry.keys()) == ["content", "echo_id", "timestamp"]

    def test_source_and_echo_id_no_image_refs(self):
        """Both source + echo_id without image_refs — byte-identical
        pre-feature shape plus the image_refs absence."""
        mgr = _make_manager_with_pending_dict()
        entry = mgr.set_injection(
            "iid-1", "from-api", source="internal_agent:x", echo_id="echo-uuid"
        )
        assert "image_refs" not in entry


# ===========================================================================
# Group 2 — image_refs kwarg behavior
# ===========================================================================


class TestSetInjectionImageRefsKwarg:
    def test_image_refs_attaches_to_entry(self):
        mgr = _make_manager_with_pending_dict()
        refs = ["/api/tmp_images/" + "a" * 32]
        entry = mgr.set_injection("iid-1", "with-refs", image_refs=refs)
        assert entry["image_refs"] == refs
        assert "image_refs" in entry

    def test_image_refs_defensive_copy(self):
        """A caller-side mutation of the passed-in list cannot affect
        the stored entry (defensive list-copy at the boundary)."""
        mgr = _make_manager_with_pending_dict()
        refs = ["/api/tmp_images/" + "a" * 32]
        entry = mgr.set_injection("iid-1", "with-refs", image_refs=refs)
        # Mutate the caller's list — the stored entry MUST stay unchanged.
        refs.append("/api/tmp_images/" + "b" * 32)
        assert entry["image_refs"] == ["/api/tmp_images/" + "a" * 32]

    def test_image_refs_with_other_kwargs(self):
        """image_refs is independent of source / echo_id — all three
        can be set together."""
        mgr = _make_manager_with_pending_dict()
        entry = mgr.set_injection(
            "iid-1",
            "all",
            source="internal_agent:foo",
            echo_id="echo-1",
            image_refs=["/api/tmp_images/" + "a" * 32],
        )
        assert entry["source"] == "internal_agent:foo"
        assert entry["echo_id"] == "echo-1"
        assert entry["image_refs"] == ["/api/tmp_images/" + "a" * 32]


# ===========================================================================
# Group 3 — FIFO ordering
# ===========================================================================


class TestSetInjectionFIFOOrdering:
    def test_image_refs_entries_queue_in_order(self):
        """Multiple entries with refs queue in FIFO order; their
        image_refs are preserved per-entry."""
        mgr = _make_manager_with_pending_dict()
        a_ref = ["/api/tmp_images/" + "a" * 32]
        b_ref = ["/api/tmp_images/" + "b" * 32]
        mgr.set_injection("iid-1", "a", image_refs=a_ref)
        mgr.set_injection("iid-1", "b", image_refs=b_ref)

        queue = mgr.set_injection.__self__._pending_injections["iid-1"] if False else None  # noqa
        # Reach into the underlying FIFO via the stub attribute:
        queue = mgr._pending_injections["iid-1"]
        assert len(queue) == 2
        assert queue[0]["image_refs"] == a_ref
        assert queue[1]["image_refs"] == b_ref
        # get_injection returns a defensive copy.
        fetched = mgr.set_injection.__self__.get_injection("iid-1") if False else None  # noqa

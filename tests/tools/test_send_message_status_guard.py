"""Targeted tests for ``send_message`` status guard in ``daemon/tools/instance.py``.

Branch: fix/revive-stale-job-lookup — Fix 5.

The old guard ``if instance_info.get("terminated")`` was always false because
the instance_info dict (from ``manager.get_instance_info()``) does not contain
a ``"terminated"`` key — it carries the live ``"status"`` field instead. The
fix replaces that with an explicit status check against TERMINATED and ERROR.

A dead (terminated or errored) instance must be rejected by ``send_message`` so
the caller does not enqueue work that will never be processed. Live instances
(idle, running) must pass through the guard.

These tests invoke the real ``send_message`` closure by:
  1. Calling ``create_instance_tools`` with all heavy factory helpers patched
     out (mirrors the pattern in ``tests/unit/tools/test_knowledge_tools.py``).
  2. Extracting the ``send_message`` tool from the returned list.
  3. Invoking ``tool.coroutine(instance_id, message)`` to call the underlying
     async function.

Note: ``send_message`` does not raise; it RETURNS a tool-response string
starting with ``"ERROR:"`` for rejected instances. The LLM sees this string
in the tool result and can act on it. Verifying the return value (not the
exception) is the correct contract.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _patch_heavy_helpers():
    """Return a stack of ``unittest.mock.patch`` context managers that disable
    the heavy ``create_instance_tools`` factory helpers (RAG, knowledge, MCP,
    project, job, mother, OpenCode, DB, infra, context) so only the
    instance-management tools (spawn/send/terminate/list/get) are built.
    """
    from unittest.mock import patch

    return [
        patch("daemon.tools.instance.is_rag_enabled", return_value=False),
        patch("daemon.tools.instance.create_rag_tools", return_value=[]),
        patch("daemon.tools.instance.create_knowledge_tools", return_value=[]),
        patch("daemon.tools.instance.create_inner_soul_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_access_memory_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_project_tools", return_value=[]),
        patch("daemon.tools.instance.create_job_tools_if_available", return_value=[]),
        patch("daemon.tools.instance.create_help_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_critical_notes_tools", return_value=[]),
        patch("daemon.tools.instance.create_project_history_tools", return_value=[]),
        patch("daemon.tools.instance.create_opencode_tools", return_value=[]),
        patch("daemon.tools.instance.create_db_tools", return_value=[]),
        patch("daemon.tools.instance.create_infra_tools", return_value=[]),
        patch("daemon.tools.instance.create_context_tools", return_value=[]),
        patch("daemon.tools.instance._load_mcp_tools", return_value=[]),
        patch("daemon.tools.instance.scan_tools_for_full_docs"),
        patch("daemon.tools.instance._apply_tool_filter", side_effect=lambda tools, *a, **kw: tools),
    ]


def _make_manager(*, status: str) -> MagicMock:
    """Build a mock manager wired for ``send_message`` with a given status.

    The manager exposes:
      * ``get_instance`` (async) — succeeds so ``_resolve_instance_id`` passes.
      * ``get_instance_info`` — returns ``{"status": status}`` (the contract
        the production code reads).
      * ``get_queue_stats`` (async) — returns empty counts.
      * ``enqueue_message`` (async) — succeeds (used only for live status).
    """
    manager = MagicMock()

    # _resolve_instance_id calls get_instance (async) and find_near_instance.
    async def _get_instance(instance_id):
        return MagicMock(instance_id=instance_id)

    manager.get_instance = _get_instance
    manager.find_near_instance = MagicMock(return_value=[])  # no fuzzy matches

    # The fix reads status from get_instance_info.
    manager.get_instance_info = MagicMock(return_value={"status": status})

    # Live-instance path: no in-flight messages, enqueue succeeds.
    manager.get_queue_stats = AsyncMock(
        return_value={"pending_count": 0, "processing_count": 0}
    )
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-abc-123")
    )
    # Real code path also touches _instance_repository for the waiting_for
    # increment (only when target.parent_id == current_instance_id). To keep
    # the live test deterministic, return None so the increment is skipped.
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)
    manager.engine = MagicMock()
    manager.write_guard = MagicMock()
    # Live-hub and correlation-manager hooks the production code touches.
    manager._live_hub = MagicMock()
    return manager


def _get_send_message_tool(manager: MagicMock):
    """Build the instance tools and return the ``send_message`` tool object.

    The tool object exposes a ``.coroutine`` attribute that is the actual
    async function decorated by ``@tool``. Invoking it directly bypasses
    Pydantic schema validation (we already know our inputs are valid).
    """
    from daemon.tools.instance import create_instance_tools

    patches = _patch_heavy_helpers()
    for p in patches:
        p.start()
    try:
        tools = create_instance_tools(manager, "parent-instance", "developer")
    finally:
        for p in reversed(patches):
            p.stop()

    # Find the send_message tool by name.
    for t in tools:
        if getattr(t, "name", None) == "send_message":
            return t
    raise RuntimeError(
        "send_message tool not found in create_instance_tools output; "
        f"got {[getattr(t, 'name', None) for t in tools]}"
    )


# =============================================================================
# Tests
# =============================================================================


class TestSendMessageStatusGuard:
    """Regression tests for Fix 5: ``send_message`` rejects terminated/errored instances."""

    async def test_send_message_rejects_terminated_instance(self):
        """A terminated instance must be rejected with an ERROR string.

        The function returns a tool-response string (does not raise). The
        caller (the LLM) sees the ERROR string and can stop or retry.
        """
        manager = _make_manager(status="terminated")
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine(
            "dead-instance-001", "hello"
        )

        # The function returns (does not raise). Verify the rejection.
        assert isinstance(result, str), f"Expected str, got {type(result)}"
        assert "ERROR" in result, f"Expected ERROR string, got: {result}"
        assert "dead-instance-001" in result, (
            f"Error should include the instance_id for the LLM; got: {result}"
        )
        assert "terminated" in result.lower(), (
            f"Error should explain the rejection reason; got: {result}"
        )

        # The post-guard path was NOT taken: enqueue_message was never called.
        manager.enqueue_message.assert_not_called()

    async def test_send_message_rejects_errored_instance(self):
        """An errored instance must be rejected with an ERROR string.

        The fix checks BOTH TERMINATED and ERROR — both are dead-instance
        states and the caller should not enqueue work to them.
        """
        manager = _make_manager(status="error")
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine(
            "errored-instance-002", "hello"
        )

        assert isinstance(result, str)
        assert "ERROR" in result
        assert "errored-instance-002" in result
        assert "error" in result.lower()

        # No enqueue attempted.
        manager.enqueue_message.assert_not_called()

    async def test_send_message_accepts_idle_instance(self):
        """An idle instance is live and must pass the status guard.

        The guard must not be over-restrictive — only TERMINATED/ERROR are
        rejected. IDLE is a normal pre-message state.
        """
        manager = _make_manager(status="idle")
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine(
            "live-instance-idle", "hello from parent"
        )

        # The function does not return an error string for live instances.
        # The exact success response varies (it includes the message_id);
        # what matters is that the status guard did not reject it.
        assert not (isinstance(result, str) and result.startswith("ERROR")), (
            f"Idle instance should not be rejected; got: {result!r}"
        )

        # The post-guard path WAS taken: enqueue_message was called.
        manager.enqueue_message.assert_awaited_once()

    async def test_send_message_accepts_running_instance(self):
        """A running instance is live and must pass the status guard.

        RUNNING is the most common state during a parent's lifetime; the
        guard must allow it through.
        """
        manager = _make_manager(status="running")
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine(
            "live-instance-running", "hi"
        )

        assert not (isinstance(result, str) and result.startswith("ERROR")), (
            f"Running instance should not be rejected; got: {result!r}"
        )
        manager.enqueue_message.assert_awaited_once()

    async def test_send_message_rejects_when_terminated_check_runs_first(self):
        """Sanity: the guard checks status BEFORE the in-progress check.

        A terminated instance with a stale in-progress message should be
        rejected on the status check, not the in-progress check. This
        proves the status guard is positioned correctly in the flow.
        """
        manager = _make_manager(status="terminated")
        # Even if get_queue_stats would return a busy queue, the status
        # guard must trigger first.
        manager.get_queue_stats = AsyncMock(
            return_value={"pending_count": 1, "processing_count": 0}
        )
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine(
            "terminated-with-stale-queue", "x"
        )

        # Status guard fires first → terminated error string.
        assert "ERROR" in result
        assert "terminated" in result.lower()
        # Not the in-progress error.
        assert "in progress" not in result.lower()
        # No enqueue attempted.
        manager.enqueue_message.assert_not_called()

    async def test_send_message_does_not_use_deprecated_terminated_key(self):
        """Regression guard: the fix replaced ``instance_info.get("terminated")``
        with the ``status`` check. This test asserts the new behavior is in
        place by checking the LIVE-STATUS branch is correct.

        If someone reverts to ``info.get("terminated")``, the instance
        with ``status="terminated"`` (and no "terminated" key) would be
        incorrectly accepted. We verify the rejection works as expected.
        """
        # The status="terminated" → no "terminated" key in the dict.
        manager = _make_manager(status="terminated")
        # Explicitly remove the key to mirror the production dict shape.
        manager.get_instance_info = MagicMock(
            return_value={"status": "terminated"}
        )
        # Confirm: no "terminated" key.
        info = manager.get_instance_info("any-id")
        assert "terminated" not in info, (
            "Test invariant: instance_info dict has no 'terminated' key, "
            "only 'status'"
        )

        send_message = _get_send_message_tool(manager)
        result = await send_message.coroutine("any-id", "hi")

        # The new status-based guard caught it; the old "terminated"-key
        # guard would have missed it.
        assert "ERROR" in result and "terminated" in result.lower(), (
            f"Status-based guard must reject; got: {result!r}"
        )

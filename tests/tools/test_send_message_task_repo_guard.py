"""Targeted tests for ``send_message`` ``_task_repo is None`` error guard in
``daemon/tools/instance.py``.

Branch: feature/cleanup-old-architecture — Phase 8 C1 fix (commit 3d929c8c).

Background
----------
Before the C1 fix, the ``send_message`` tool only logged a warning when
``manager._task_repo`` was ``None`` and silently continued. The bus watcher
registration was then skipped, which could allow a parent instance to mark
itself complete while the child was still running — a silent premature
parent-completion bug class.

The C1 fix converts that silent log-and-continue path into an explicit
ERROR return so the agent caller observes the failure and can decide
whether to retry. The DependencyBus is the SOLE completion authority
post-Phase 8, so there is no alternative path to fall back on.

These tests mirror the pattern in
``tests/tools/test_send_message_status_guard.py``: build the real
``send_message`` closure with heavy factory helpers patched out, then
invoke ``tool.coroutine(instance_id, message)`` directly and assert on
the returned tool-response string.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _patch_heavy_helpers():
    """Patch the heavy ``create_instance_tools`` factory helpers so only the
    instance-management tools are built (RAG, knowledge, MCP, project, job,
    mother, OpenCode, DB, infra, context all disabled).
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


def _make_manager(*, status: str = "idle", task_repo=None) -> MagicMock:
    """Build a mock manager wired for ``send_message`` with the C1-guard
    preconditioning.

    Args:
        status: The status the manager reports for the target instance.
            Defaults to ``"idle"`` so the status-guard path does not fire
            first.
        task_repo: Value to set ``manager._task_repo`` to. ``None`` triggers
            the C1 error guard. The default ``MagicMock()`` represents the
            healthy, fully-wired manager.
    """
    manager = MagicMock()

    # _resolve_instance_id calls get_instance (async) and find_near_instance.
    async def _get_instance(instance_id):
        return MagicMock(instance_id=instance_id)

    manager.get_instance = _get_instance
    manager.find_near_instance = MagicMock(return_value=[])  # no fuzzy matches

    # The status-guard reads status from get_instance_info.
    manager.get_instance_info = MagicMock(return_value={"status": status})

    # Live-instance path: no in-flight messages, enqueue succeeds.
    manager.get_queue_stats = AsyncMock(
        return_value={"pending_count": 0, "processing_count": 0}
    )
    # ``send_message`` dispatches via ``enqueue_message`` (NOT
    # ``enqueue_message_job``). Production ``send_message`` at
    # daemon/tools/instance.py:715 calls ``await manager.enqueue_message(...)``.
    # Awaiting a plain ``MagicMock`` raises
    # ``TypeError: object MagicMock can't be used in 'await' expression``,
    # so this attribute must be an ``AsyncMock``.
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-abc-123")
    )
    # ``enqueue_message_job`` is the public/external path (POST /messages,
    # chat adapters, scheduler) and is NOT called by ``send_message``.
    # Kept as a MagicMock so any straggling read doesn't accidentally
    # invoke the real implementation, but NOT asserted against.
    manager.enqueue_message_job = MagicMock(
        return_value=MagicMock(message_id="msg-abc-123")
    )

    # Production code touches these for the post-enqueue path.
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)
    manager.engine = MagicMock()
    manager.write_guard = MagicMock()
    manager._live_hub = MagicMock()

    # C1 fix verification: explicitly control the _task_repo attribute.
    # Use ``__setattr__`` to bypass MagicMock's auto-attr so the production
    # ``getattr(manager, "_task_repo", None)`` sees the exact value we set
    # (including the literal None that triggers the guard).
    object.__setattr__(manager, "_task_repo", task_repo)
    return manager


def _get_send_message_tool(manager: MagicMock):
    """Build the instance tools and return the ``send_message`` tool object."""
    from daemon.tools.instance import create_instance_tools

    patches = _patch_heavy_helpers()
    for p in patches:
        p.start()
    try:
        tools = create_instance_tools(manager, "parent-instance", "developer")
    finally:
        for p in reversed(patches):
            p.stop()

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


class TestSendMessageTaskRepoGuard:
    """Regression tests for Phase 8 C1 fix: ``send_message`` must return an
    explicit ERROR when ``manager._task_repo`` is ``None``, not silently
    succeed.
    """

    async def test_send_message_returns_error_when_task_repo_is_none(self):
        """C1 fix core: ``manager._task_repo = None`` ⇒ ERROR string returned.

        Before the fix the function logged a warning and continued, leaving
        the parent without a bus watcher. After the fix it returns an
        explicit ERROR so the agent caller can observe the failure.
        """
        manager = _make_manager(status="idle", task_repo=None)
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine(
            "any-target-id", "hello from parent"
        )

        # Returns a string (does not raise).
        assert isinstance(result, str), f"Expected str, got {type(result)}"
        # Starts with the ERROR sentinel the rest of the tool code uses.
        assert result.startswith("ERROR"), (
            f"C1 guard must return ERROR string; got: {result!r}"
        )
        # Body mentions the missing dependency so the LLM can act.
        assert "_task_repo" in result, (
            f"Error should name the missing _task_repo; got: {result!r}"
        )
        assert "dependency_bus" in result or "Parent-child" in result, (
            f"Error should explain coordination is unavailable; got: {result!r}"
        )

    async def test_send_message_error_mentions_unavailable_coordination(self):
        """The ERROR string should explicitly say parent-child coordination
        is unavailable so the LLM understands it is not a transient error.
        """
        manager = _make_manager(status="running", task_repo=None)
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine("child-1", "go")

        assert isinstance(result, str)
        assert result.startswith("ERROR")
        assert "unavailable" in result.lower() or "missing" in result.lower(), (
            f"Error should state coordination unavailable; got: {result!r}"
        )

    async def test_send_message_does_not_call_bus_watch_when_task_repo_is_none(self):
        """The C1 guard fires BEFORE the bus watch branch, so a missing
        ``_task_repo`` must NOT result in a ``bus.watch`` call.

        We can't easily stub ``_bus.watch`` from the manager (it's a module
        singleton), so we assert indirectly: the function returns the ERROR
        string, which is the only path that skips the watch call.
        """
        manager = _make_manager(status="idle", task_repo=None)
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine("any-id", "msg")

        # If the guard had not fired, the function would have returned the
        # success string starting with "Message queued and sent to ...".
        assert isinstance(result, str)
        assert result.startswith("ERROR")
        assert "Message queued" not in result, (
            f"C1 guard must not produce a success message; got: {result!r}"
        )

    async def test_send_message_succeeds_when_task_repo_is_present(self):
        """Regression guard: the happy path (task_repo wired up) must still
        work. A missing guard would not break this, but a wrong guard
        (e.g., a stray ``is None`` on a non-optional dependency) could
        regress the success path.
        """
        # task_repo default is a MagicMock — get_by_message will return a
        # Mock-like object. The tool only uses it for ``child_task.id``, so
        # we need a Mock that returns a valid id when accessed.
        child_task = MagicMock()
        child_task.id = 42
        task_repo = MagicMock()
        task_repo.get_by_message = MagicMock(return_value=child_task)
        manager = _make_manager(status="idle", task_repo=task_repo)
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine("any-id", "msg")

        # Live path: not an ERROR. It either succeeds or hits a later
        # (non-task-repo) error path; the important thing is the C1 guard
        # did NOT fire. We accept any non-ERROR result.
        assert isinstance(result, str)
        assert not result.startswith("ERROR"), (
            f"Wired task_repo must not trigger C1 guard; got: {result!r}"
        )

    async def test_send_message_guard_runs_after_status_check(self):
        """Order check: the status guard must fire BEFORE the task_repo
        guard. A terminated target with a missing ``_task_repo`` should be
        rejected on the status check, not the C1 guard — proving the
        guards are positioned correctly.
        """
        manager = _make_manager(status="terminated", task_repo=None)
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine("dead-id", "hello")

        # Status guard fires first → terminated error, NOT the task_repo error.
        assert isinstance(result, str)
        assert result.startswith("ERROR")
        assert "terminated" in result.lower(), (
            f"Status guard should fire first; got: {result!r}"
        )
        assert "_task_repo" not in result, (
            f"Task-repo guard should not have fired; got: {result!r}"
        )

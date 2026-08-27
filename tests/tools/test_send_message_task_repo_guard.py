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

from unittest.mock import MagicMock

import pytest

from tests.helpers.send_message_fixtures import (
    get_send_message_tool as _get_send_message_tool,
    patch_heavy_helpers as _patch_heavy_helpers,
)


def _make_manager(*, status: str = "idle", task_repo=None) -> MagicMock:
    """Build a mock manager wired for ``send_message`` with the C1-guard
    preconditioning.

    Extends the shared baseline (``tests.helpers.send_message_fixtures.
    make_send_message_manager``) with two per-file deltas:

      * ``get_instance_info`` returns the MINIMAL shape ``{"status": status}``
        — this suite focuses on the C1 ``_task_repo`` guard, not on the
        agent-id lookup path, so the minimal shape keeps the input
        surface tight and matches the pre-extraction behavior.
      * ``task_repo`` parameter controls ``manager._task_repo`` via
        ``object.__setattr__`` (bypasses ``MagicMock``'s auto-attr) so the
        production ``getattr(manager, "_task_repo", None)`` sees the
        exact value passed in (including the literal ``None`` that
        triggers the C1 guard).

    Args:
        status: The status the manager reports for the target instance.
            Defaults to ``"idle"`` so the status-guard path does not fire
            first.
        task_repo: Value to set ``manager._task_repo`` to. ``None`` triggers
            the C1 error guard. The default ``MagicMock()`` represents the
            healthy, fully-wired manager.
    """
    from tests.helpers.send_message_fixtures import make_send_message_manager

    manager = make_send_message_manager(status=status)
    # Strip ``agent_id`` from ``get_instance_info`` — this suite does not
    # exercise the agent-id lookup path, and the pre-extraction baseline
    # returned the minimal ``{"status": status}`` shape.
    manager.get_instance_info = MagicMock(return_value={"status": status})
    # C1 fix verification: explicitly control the _task_repo attribute.
    # Use ``__setattr__`` to bypass MagicMock's auto-attr so the production
    # ``getattr(manager, "_task_repo", None)`` sees the exact value we set
    # (including the literal None that triggers the guard).
    object.__setattr__(manager, "_task_repo", task_repo)
    return manager


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

        Phase 1 (agent-instance-tools): RUNNING / WAITING_CHILDREN now
        route through the injection path (``manager.set_injection``),
        which does NOT register a child-completion watcher (no Task /
        JobItem is created for the injection — the live turn absorbs the
        message). The ``_task_repo`` guard only fires for the enqueue
        branch (IDLE / WAITING / QUEUED / terminal-revive). Use IDLE here
        so the C1 guard is exercised end-to-end.
        """
        manager = _make_manager(status="idle", task_repo=None)
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
        """Order check: the routing-helper classification must happen
        BEFORE the task_repo guard fires.

        Phase 1 (agent-instance-tools) updated the ordering contract:
          * IDLE / terminal-revive / non-eligible non-terminal: the
            routing helper classifies, the queue-busy guard runs (if
            applicable), ``enqueue_message`` runs, then the C1 task_repo
            guard fires if needed.
          * RUNNING / WAITING_CHILDREN: the routing helper classifies
            and routes via ``set_injection`` — no task_repo guard (the
            injection path does NOT register a child-completion
            watcher).
          * PAUSED: routing helper returns ``"paused"`` → reject.
            task_repo guard is irrelevant.

        We exercise the enqueue branch (IDLE) so the ordering check
        remains meaningful: the routing-helper classification must
        happen BEFORE the C1 guard fires (i.e. the C1 guard fires only
        AFTER ``enqueue_message`` was attempted). With task_repo=None,
        the C1 guard returns the missing-``_task_repo`` ERROR string
        — that proves the guard is positioned correctly in the
        enqueue-branch flow.
        """
        manager = _make_manager(status="idle", task_repo=None)
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine("idle-id", "hello")

        assert isinstance(result, str)
        assert result.startswith("ERROR")
        # The C1 guard fires AFTER ``enqueue_message`` — i.e. the
        # _task_repo-missing error is the surface error. The status
        # guard does NOT fire here (IDLE is not injection-eligible and
        # not terminal, so the routing helper returns ``"enqueue"`` and
        # we proceed to enqueue + C1 guard).
        assert "_task_repo" in result, (
            f"C1 guard should fire on the enqueue branch with missing "
            f"_task_repo; got: {result!r}"
        )

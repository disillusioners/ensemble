"""Regression tests for the ``child_still_running_defer`` bus-emit skip.

Incident (RCA-confirmed, 02fb2e01): the ``child_still_running_defer``
branch in :meth:`ChildReportsService._dispatch_post_commit_side_effects`
emitted the SSE ``waiting_children`` notification but skipped the
``bus.emit_terminal`` call. Consequence chain:

* The parent's PENDING watcher on this child's task never fired.
* ``JobItem`` was never finalized → ``dependency_watchers`` row
  ``13670c9c`` stayed PENDING forever.
* Leader's completion-gate wedged in ``waiting_children``.

The branch is intentionally a defer (children still running → do NOT
emit a ``child_completed`` lifecycle event, do NOT mark the instance
COMPLETED). What the defer MUST also do is release the parent's
watcher so the parent's pending count drops. The corrective
(parent, child)-keyed bus emit (added by commit ``16553972`` for the
``regular_child_completed`` outcome) is exactly the primitive the
defer needs.

The fix: the defer branch now fires ``_emit_terminal_via_bus`` (task-
keyed) AND ``_emit_terminal_for_child_instance_via_bus`` (corrective
parent+child pair). The task-keyed emit fires watchers on the current
task; the corrective emit fires watchers by (parent, child) instance
pair (multi-turn safe). ``transition_state``'s guarded
``WHERE state = 'PENDING'`` Core UPDATE enforces exactly-once, so a
later ``regular_child_completed`` turn whose corrective emit lands on
the same watcher is a safe no-op — the defer fires the watcher once,
the later completion fires the lifecycle event once.

What the defer STILL does NOT do (legitimate preservation):

* No ``Instance.status = COMPLETED`` write.
* No ``MessageQueue`` ``internal_report:`` row creation.
* No ``Task`` for the parent to process.
* No ``CompletionRegistry.complete(...)`` call.
* No ``child_completed`` SSE/lifecycle broadcast (the parent only
  learns via the bus watcher firing).

These tests follow the same mock-based pattern as
``tests/unit/test_lifecycle_hook_completion.py``: build the service
via ``__new__`` and patch the bus helpers. The repro test asserts
that the bus helpers are awaited when the defer fires (red-green: the
helpers are NOT awaited on base ``b4dbfda2``; they ARE awaited with
the fix). The guard test asserts the legitimate defer preserves the
``waiting_children`` SSE shape and does not run the lifecycle-hook /
report-broadcast paths reserved for ``regular_child_completed``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.child_reports import (
    ChildReportsService,
    _ChildCompletionDbResult,
)


# ─── Fixtures & helpers ────────────────────────────────────────────────


def _make_service(mock_manager) -> ChildReportsService:
    """Build a bare ``ChildReportsService`` with patched dependencies.

    Mirrors ``tests/unit/test_lifecycle_hook_completion.py``'s pattern:
    avoid running ``__init__`` (which expects a real ``InstanceManager``)
    by going through ``__new__`` and wiring the few attributes that
    ``_dispatch_post_commit_side_effects`` reads.
    """
    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = mock_manager
    # Provide no events service so the lifecycle-event try/except is a no-op.
    service._events_service = None
    # Stub the post-commit helpers. The repro tests assert on these
    # AsyncMocks — pre-fix they are NOT awaited, post-fix they ARE.
    service._emit_terminal_via_bus = AsyncMock()
    service._emit_terminal_for_child_instance_via_bus = AsyncMock()
    service._trigger_title_generation = MagicMock()
    return service


def _make_mock_manager(*, instance_repo: MagicMock | None = None) -> MagicMock:
    """Build a manager mock with the minimal surface the dispatch path reads."""
    manager = MagicMock()
    manager._instance_repository = instance_repo
    manager._live_hub = None
    manager._worker_pool = None
    manager._task_repo = None
    return manager


def _make_db_result(
    outcome: str,
    *,
    instance_id: str = "child-001",
    agent_id: str = "wanderer",
    parent_id: str | None = "parent-001",
    child_agent_id: str | None = "wanderer",
) -> _ChildCompletionDbResult:
    """Construct a ``_ChildCompletionDbResult`` with sensible defaults."""
    return _ChildCompletionDbResult(
        outcome=outcome,
        instance_id=instance_id,
        agent_id=agent_id,
        parent_id=parent_id,
        child_agent_id=child_agent_id,
    )


# ─── Red-green repro: defer must fire the bus terminal hook ────────────


class TestDeferFiresBusTerminal:
    """Repro of incident 02fb2e01: the defer branch emitted SSE ONLY
    and skipped ``bus.emit_terminal``. The fix fires both bus helpers
    so the parent's PENDING watcher is released.
    """

    @pytest.mark.asyncio
    async def test_defer_fires_corrective_bus_emit(self):
        """The corrective (parent, child)-keyed emit MUST fire when the
        defer outcome is dispatched.

        Pre-fix on base ``b4dbfda2``: ``_emit_terminal_for_child_instance_via_bus``
        is NOT awaited → parent's PENDING watcher ``13670c9c`` stays
        PENDING forever (incident symptom).

        Post-fix: ``_emit_terminal_for_child_instance_via_bus`` IS awaited
        with ``(parent_instance_id=parent_id, child_instance_id=instance_id,
        status="completed")`` → corrective emit fires the watcher
        regardless of which task id was the terminal one (multi-turn
        safe).
        """
        manager = _make_mock_manager()
        service = _make_service(manager)

        result = _make_db_result(
            "child_still_running_defer",
            instance_id="wanderer-001",
            parent_id="leader-001",
        )

        await service._dispatch_post_commit_side_effects(
            result, last_content="report body", completed_message_id="msg-current"
        )

        service._emit_terminal_for_child_instance_via_bus.assert_awaited_once()
        kwargs = service._emit_terminal_for_child_instance_via_bus.await_args.kwargs
        assert kwargs["parent_instance_id"] == "leader-001", (
            f"corrective emit must target the parent; got {kwargs}"
        )
        assert kwargs["child_instance_id"] == "wanderer-001", (
            f"corrective emit must identify the child; got {kwargs}"
        )
        assert kwargs["status"] == "completed", (
            f"corrective emit status must be 'completed'; got {kwargs}"
        )

    @pytest.mark.asyncio
    async def test_defer_fires_task_keyed_bus_emit(self):
        """The task-keyed emit also fires when ``completed_message_id``
        is provided AND a ``_task_repo`` can resolve it.

        The task-keyed emit is the one that fires watchers on the
        CURRENT task id — the single-turn case where the parent's
        watcher happens to be keyed on this exact task. Combined with
        the corrective emit, this closes both single-turn and
        multi-turn watcher paths.

        Pre-fix on base ``b4dbfda2``: ``_emit_terminal_via_bus`` is NOT
        awaited → ``bus.emit_terminal(task_id=...)`` is never called.
        """
        # ``_task_repo.get_by_message`` is called via ``asyncio.to_thread``
        # (a sync worker-thread call), so the stub MUST be sync (the
        # ``to_thread`` wrapper does NOT await coroutines).
        def _fake_get_by_message(message_id):
            t = MagicMock()
            t.id = 25935
            return t

        manager = _make_mock_manager()
        manager._task_repo = MagicMock()
        manager._task_repo.get_by_message = _fake_get_by_message

        service = _make_service(manager)

        result = _make_db_result(
            "child_still_running_defer",
            instance_id="child-of-wanderer",
            parent_id="wanderer",
        )

        await service._dispatch_post_commit_side_effects(
            result, last_content="body", completed_message_id="msg-current"
        )

        service._emit_terminal_via_bus.assert_awaited_once()
        kwargs = service._emit_terminal_via_bus.await_args.kwargs
        assert kwargs["task_id"] == 25935, (
            f"task-keyed emit must fire on the current task id; got {kwargs}"
        )
        assert kwargs["status"] == "completed"


# ─── Guard tests: legitimate defer is preserved ────────────────────────


class TestDeferPreservesLegitimateDeferral:
    """Guard: the defer's SSE shape is preserved, no premature
    finalization is performed. The defer still does NOT:
      * update Instance.status
      * call CompletionRegistry.complete
      * enqueue a completion_report MessageQueue / Task for the parent
      * publish a child_completed lifecycle event
      * run the lifecycle-hook dispatch (which is gated on
        regular_child_completed — see TestOutcomeGating in
        tests/unit/test_lifecycle_hook_completion.py)
    """

    @pytest.mark.asyncio
    async def test_defer_still_emits_waiting_children_sse(self):
        """Pre-existing SSE behavior preserved: when outcome is
        ``child_still_running_defer`` and ``_live_hub`` is wired,
        ``stream_status_change(instance_id, "waiting_children", ...)``
        is awaited exactly once.

        The fix MUST NOT drop this call — the UI relies on it to
        reflect the wait state.
        """
        manager = _make_mock_manager()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_status_change = AsyncMock()

        service = _make_service(manager)

        result = _make_db_result("child_still_running_defer")

        await service._dispatch_post_commit_side_effects(
            result, last_content="body", completed_message_id="msg-current"
        )

        manager._live_hub.stream_status_change.assert_awaited_once()
        args = manager._live_hub.stream_status_change.await_args.args
        assert args[0] == "child-001"
        assert args[1] == "waiting_children"

    @pytest.mark.asyncio
    async def test_defer_does_not_call_completion_registry(self):
        """CompletionRegistry.complete MUST NOT fire on defer — the
        instance is not done yet. Guard against regression where the
        fix accidentally promotes the defer to a completion.

        ``get_completion_registry`` is imported inline at the call
        site (``from .completion_registry import get_completion_registry``)
        so we patch the source module directly.
        """
        # Patch the source module so any ``from .completion_registry
        # import get_completion_registry`` lookup at the call site
        # returns our registry mock.
        registry_mock = MagicMock(complete=MagicMock())
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "daemon.services.completion_registry.get_completion_registry",
                lambda: registry_mock,
            )

            manager = _make_mock_manager()
            service = _make_service(manager)

            result = _make_db_result("child_still_running_defer")
            await service._dispatch_post_commit_side_effects(
                result, last_content="body", completed_message_id="msg-current"
            )

        registry_mock.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_defer_does_not_publish_lifecycle_event(self):
        """``_publish_instance_lifecycle_event`` MUST NOT fire on defer
        — the instance has not reached a terminal state. The parent's
        child_completed notification must wait for the eventual
        ``regular_child_completed`` turn, not fire here.
        """
        events = MagicMock()
        events._publish_instance_lifecycle_event = AsyncMock()

        manager = _make_mock_manager()
        service = _make_service(manager)
        service._events_service = events

        result = _make_db_result("child_still_running_defer")

        await service._dispatch_post_commit_side_effects(
            result, last_content="body", completed_message_id="msg-current"
        )

        events._publish_instance_lifecycle_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_defer_does_not_run_lifecycle_hooks(self):
        """The lifecycle-hook dispatch is gated on
        ``regular_child_completed`` (``TestOutcomeGating`` in
        ``tests/unit/test_lifecycle_hook_completion.py``). The defer
        MUST NOT trigger the hook — registered hooks fire only on
        actual completion, never on a wait-state defer.
        """
        # We do NOT need to actually register a hook — the guard is
        # that the dispatch path's hook call site (``dispatch_lifecycle_hooks``)
        # is unreachable for this outcome. We assert by patching
        # ``get_registry`` and confirming the registered hooks list is
        # never read.
        registry = MagicMock()
        registry.get_version.return_value = MagicMock(
            lifecycle_hooks={"on_complete": ["some_hook"]}
        )

        manager = _make_mock_manager()
        service = _make_service(manager)

        from unittest.mock import patch

        result = _make_db_result("child_still_running_defer")
        with patch("daemon.services.child_reports.get_registry", return_value=registry):
            await service._dispatch_post_commit_side_effects(
                result, last_content="body", completed_message_id="msg-current"
            )

        # If the dispatch had reached the hook site, the registry's
        # ``get_version`` (or ``get_resolved``) would have been called
        # to resolve the agent's lifecycle_hooks config. Defer must
        # short-circuit BEFORE that lookup.
        registry.get_version.assert_not_called()
        registry.get_resolved.assert_not_called()

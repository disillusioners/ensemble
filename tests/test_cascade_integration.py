"""W2 Integration tests: CM-active bypass eliminates Race #3 (SELECT COUNT TOCTOU).

Phase 3 (Cascade Unification) unifies three divergent cascade decision
sites (Site 1A in ``child_reports._update_parent_on_child_complete``,
Site 1B in the same module, Site 2 in ``error_reporting._send_error_report``)
into a single authoritative CorrelationManager delegation.

The critical invariant — and the only thing these tests verify — is:

* When CM is **active** (``get_correlation_manager()`` returns a real CM
  instance), the inline cascade MUST short-circuit before any
  ``SELECT COUNT(*) FROM message_queue`` query. The CM's in-memory
  pending set is the single source of truth, and its per-parent
  ``asyncio.Lock`` eliminates the legacy TOCTOU window (Race #3).
* When CM is **None** (graceful degradation), the legacy inline cascade
  runs unchanged: it does the ``SELECT COUNT(*)`` and the inline
  status transition. This is the path exercised by every test that
  does not wire a CM fixture (regression coverage).

These are the mandatory tests (W2) before production deploy.

Mapping to plan §Verification Strategy
---------------------------------------

| # | Test                                            | Plan item |
|---|-------------------------------------------------|-----------|
| 1 | CM active → no SELECT COUNT, no inline status   | 2, 5      |
| 2 | CM None  → legacy path runs                     | 2, 5      |
| 3 | CM active → no inline cascade (error path)      | 2, 5      |
| 4 | CM None  → legacy error cascade runs            | 2, 5      |

See ``.agents/shared/planning/correlation-manager/phase3-cascade-unification.md``
for the full plan.

Run with::

    pytest tests/test_cascade_integration.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.services.correlation_manager import (
    CorrelationManager,
    set_correlation_manager,
)


# =============================================================================
# Shared helpers
# =============================================================================


def _make_parent(
    status: str = "running",
    waiting_for: int = 0,
    parent_id: str = "parent-W2",
) -> MagicMock:
    """Build a mock parent Instance with the attributes the cascade reads."""
    parent = MagicMock()
    parent.instance_id = parent_id
    parent.parent_id = None
    parent.status = status
    parent.waiting_for = waiting_for
    parent.children = "[]"
    parent.instance_metadata = {}
    parent.last_activity_at = None
    parent.updated_at = None
    parent.version = 1
    return parent


def _make_child(parent_id: str = "parent-W2") -> MagicMock:
    """Build a mock child Instance referencing the given parent."""
    child = MagicMock()
    child.instance_id = "child-W2"
    child.parent_id = parent_id
    child.status = "completed"
    child.instance_metadata = {}
    child.children = "[]"
    child.waiting_for = 0
    child.last_activity_at = None
    child.version = 1
    return child


def _setup_cascade_session(parent: MagicMock) -> MagicMock:
    """Build a mock session that simulates the atomic UPDATE returning
    ``new_waiting=0`` and the post-expiry parent re-read.

    The session is a stand-in for the SQLAlchemy session inside
    ``ChildReportsService._update_parent_on_child_complete``:

    * ``session.get(Instance, ...)`` → ``parent`` (twice — initial and
      post-expiry re-read)
    * ``session.execute(text("UPDATE instances SET waiting_for = ... RETURNING waiting_for"))``
      → row with new value 0
    * ``session.exec(select(func.count()).select_from(MessageQueue)...)``
      → ``scalar_one() == 0`` (no pending messages — the only branch
      where the inline status transition would actually run)
    * ``session.expire(parent)`` → no-op
    """
    session = MagicMock()
    # First session.get → parent (initial lookup).
    # Second session.get → parent (re-read after session.expire).
    # The mock just returns the same object for both — the test sets
    # waiting_for manually before the cascade call.
    session.get = MagicMock(return_value=parent)
    # SQL UPDATE … RETURNING waiting_for — return the new (decremented) value.
    update_result = MagicMock()
    update_result.first = MagicMock(return_value=(0,))
    session.execute = MagicMock(return_value=update_result)
    # Default: the count_pending query returns 0 (no pending messages).
    # The CM-bypass tests inspect session.exec.call_count BEFORE this
    # result would be consumed, but we still wire it up so the legacy
    # path (CM=None) returns cleanly.
    pending_result = MagicMock()
    pending_result.scalar_one = MagicMock(return_value=0)
    session.exec = MagicMock(return_value=pending_result)
    session.expire = MagicMock()
    return session


def _make_mock_manager() -> MagicMock:
    """Build a minimal ``MagicMock`` for the ``InstanceManager`` facade.

    The cascade only touches a few manager attributes. Setting
    ``is_write_paused = False`` is required because ``WriteGuardSession``
    reads it on enter and ``MagicMock``'s auto-generated truthy value
    would raise 503-style errors in any path that opens a session.
    """
    manager = MagicMock()
    manager._live_hub = None
    manager._checkpointer = None
    manager.config = MagicMock()
    manager.config.llm = MagicMock()
    # WritePauseGuard gotcha — see AGENTS.md / task spec.
    manager.is_write_paused = False
    manager.write_guard = MagicMock()
    manager.write_guard.is_write_paused = False
    manager.engine = MagicMock()
    return manager


# =============================================================================
# Site 1A — ChildReportsService._update_parent_on_child_complete
# =============================================================================


class TestSite1ACmActiveBypass:
    """CM is active → the bypass returns BEFORE any SELECT COUNT(*) query.

    This is the Race #3 fix: the in-memory CM pending set is the single
    source of truth. The legacy ``SELECT COUNT(*) FROM message_queue``
    TOCTOU window is structurally eliminated.
    """

    @pytest.mark.asyncio
    async def test_cm_active_skips_select_count_and_inline_status(self):
        """W2 mandatory: with CM active, the cascade short-circuits
        BEFORE ``session.exec(select(func.count())...)``.

        Verified by inspecting ``session.exec.call_args_list`` — the
        count-pending query must NOT appear in it. The CM's
        ``resolve_response`` (called via the ``notify_corr_resolve`` hook
        at lines 480-504 of ``child_reports.py``) is the only path that
        observes completion; it does not touch the DB.
        """
        from daemon.services.child_reports import ChildReportsService
        from daemon.repositories.instance.models import InstanceStatus

        # Wire a real CM (any CM instance — the bypass is a singleton
        # None-check, not a behaviour of the CM itself). We also patch
        # ``notify_corr_resolve`` to an AsyncMock so the CM hook doesn't
        # try to talk to DBs we haven't set up — the bypass is the
        # thing under test, not the CM.
        cm = MagicMock(spec=CorrelationManager, name="cm-active")
        set_correlation_manager(cm)
        try:
            parent = _make_parent(
                status=InstanceStatus.RUNNING.value,
                waiting_for=0,  # post-decrement
            )
            child = _make_child()
            session = _setup_cascade_session(parent)

            mock_manager = _make_mock_manager()
            service = ChildReportsService(manager=mock_manager)

            # Capture the AsyncMock so we can assert against it after
            # the patch context exits (cm_mod.notify_corr_resolve
            # refers to the original function, not our patch).
            mock_hook = AsyncMock(name="notify_corr_resolve")
            with patch(
                "daemon.services.correlation_manager.notify_corr_resolve",
                new=mock_hook,
            ):
                transitioned, completed_parent_id, completed_parent_parent_id = (
                    await service._update_parent_on_child_complete(
                        session, child, completed_message_id="msg-W2-active"
                    )
                )

            # (a) Bypass return signature — no transition, no completed parent.
            assert transitioned is False
            assert completed_parent_id is None
            assert completed_parent_parent_id is None

            # (b) Race #3 fix: NO ``session.exec`` call. The
            # ``SELECT COUNT(*) FROM message_queue`` is the only
            # ``session.exec`` call in ``_update_parent_on_child_complete``
            # (the atomic UPDATE uses ``session.execute(text(...))``).
            # The CM bypass returns before line 567 of child_reports.py.
            assert session.exec.call_count == 0, (
                f"CM-active bypass MUST skip SELECT COUNT(*) — "
                f"session.exec was called {session.exec.call_count} time(s). "
                f"This re-opens the Race #3 TOCTOU window. "
                f"Calls observed: "
                f"{[repr(c.args[0]) for c in session.exec.call_args_list]}"
            )

            # (c) Inline status transition: parent.status MUST stay as RUNNING.
            # The bypass returns before line 581 (`parent.status = COMPLETED`).
            assert parent.status == InstanceStatus.RUNNING.value, (
                f"CM-active bypass must NOT mutate parent.status inline — "
                f"expected RUNNING, got {parent.status!r}"
            )

            # (d) The CM hook was still called (it is the authoritative
            # source of truth, not a bypass). It is the dispatch path,
            # not the inline cascade, that records completion.
            assert mock_hook.await_count == 1, (
                f"notify_corr_resolve must be called exactly once when "
                f"completed_message_id is set; got {mock_hook.await_count}"
            )
        finally:
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_cm_active_returns_early_before_cascade_block(self):
        """Sanity / regression: the bypass return is the FIRST thing the
        function does after the CM check. Verify the cascade guard
        ``if (waiting_for == 0 and status != COMPLETED and status != ERROR)``
        is also reached and matches the bypass path.
        """
        from daemon.services.child_reports import ChildReportsService
        from daemon.repositories.instance.models import InstanceStatus

        cm = MagicMock(spec=CorrelationManager, name="cm-active")
        set_correlation_manager(cm)
        try:
            parent = _make_parent(status="running", waiting_for=0)
            child = _make_child()
            session = _setup_cascade_session(parent)
            mock_manager = _make_mock_manager()
            service = ChildReportsService(manager=mock_manager)

            with patch(
                "daemon.services.correlation_manager.notify_corr_resolve",
                new=AsyncMock(),
            ):
                result = await service._update_parent_on_child_complete(
                    session, child, completed_message_id="msg-W2-early"
                )

            # The return must be the exact sentinel tuple (False, None, None)
            # that the bypass emits at line 564 of child_reports.py. If this
            # tuple ever changes, the caller's contract at line 942
            # (`parent_transitioned_to_running, completed_parent_id, ...`)
            # also needs to change.
            assert result == (False, None, None), (
                f"CM-active bypass must return (False, None, None); got {result!r}. "
                f"This is the sentinel tuple that signals 'CM owns completion' "
                f"to the caller in _process_child_completion_and_notify_parent."
            )
        finally:
            set_correlation_manager(None)


class TestSite1ACmDisabledLegacy:
    """CM is None → the original inline cascade runs (graceful degradation).

    This is the regression coverage: every test that does not wire a CM
    fixture exercises this path, so it must keep working unchanged.
    """

    @pytest.mark.asyncio
    async def test_cm_none_runs_select_count_and_inline_status(self):
        """W2 mandatory: with CM=None, the legacy inline cascade runs
        end-to-end: SELECT COUNT(*) → no pending messages → status set
        to COMPLETED → return tuple carries the completed parent.
        """
        from daemon.services.child_reports import ChildReportsService
        from daemon.repositories.instance.models import InstanceStatus

        # Make sure no CM is registered (graceful degradation).
        set_correlation_manager(None)

        parent = _make_parent(
            status=InstanceStatus.RUNNING.value,
            waiting_for=0,  # post-decrement
        )
        child = _make_child()
        session = _setup_cascade_session(parent)
        mock_manager = _make_mock_manager()
        service = ChildReportsService(manager=mock_manager)

        # Pass completed_message_id=None so the notify_corr_resolve hook
        # is skipped (it would no-op when CM is None, but skipping the
        # call keeps the test focused on the legacy path).
        transitioned, completed_parent_id, completed_parent_parent_id = (
            await service._update_parent_on_child_complete(
                session, child, completed_message_id=None
            )
        )

        # (a) Legacy return signature: no RUNNING transition, but the
        # parent IS reported as completed.
        assert transitioned is False
        assert completed_parent_id == parent.instance_id
        assert completed_parent_parent_id is None  # root in this fixture

        # (b) SELECT COUNT(*) WAS executed — this is the path Phase 3
        # is trying to eliminate when CM is active. With CM=None the
        # legacy TOCTOU window is the (acceptable) graceful-degradation
        # cost. The count-pending query is the only ``session.exec``
        # call in the cascade block (the atomic UPDATE uses
        # ``session.execute(text(...))``), so we can assert
        # ``session.exec.call_count == 1`` directly.
        assert session.exec.call_count == 1, (
            f"CM=None legacy path MUST run SELECT COUNT(*) — "
            f"expected 1 session.exec call, got {session.exec.call_count}. "
            f"Either the bypass leaked into the legacy path, or the "
            f"session.exec mock is not configured correctly."
        )

        # (c) Inline status transition to COMPLETED.
        assert parent.status == InstanceStatus.COMPLETED.value, (
            f"CM=None legacy path must set parent.status = COMPLETED; "
            f"got {parent.status!r}"
        )

    @pytest.mark.asyncio
    async def test_cm_none_pending_messages_keeps_waiting_children(self):
        """Negative control: CM=None, parent has pending messages → parent
        transitions to WAITING_CHILDREN (not COMPLETED). The
        ``SELECT COUNT(*)`` returns a non-zero scalar in this scenario.
        """
        from daemon.services.child_reports import ChildReportsService
        from daemon.repositories.instance.models import InstanceStatus

        set_correlation_manager(None)

        parent = _make_parent(status="running", waiting_for=0)
        child = _make_child()
        session = _setup_cascade_session(parent)
        # Pending messages present — parent must NOT complete.
        pending_result = MagicMock()
        pending_result.scalar_one = MagicMock(return_value=2)
        session.exec = MagicMock(return_value=pending_result)
        mock_manager = _make_mock_manager()
        service = ChildReportsService(manager=mock_manager)

        transitioned, completed_parent_id, _ = (
            await service._update_parent_on_child_complete(
                session, child, completed_message_id=None
            )
        )

        # With pending messages, parent transitions to WAITING_CHILDREN
        # (line 596-607 of child_reports.py). transitioned_to_running is
        # True for WAITING_CHILDREN in this codebase's contract.
        assert transitioned is True
        assert completed_parent_id is None
        assert parent.status == InstanceStatus.WAITING_CHILDREN.value


# =============================================================================
# Site 2 — ErrorReportingService._send_error_report
# =============================================================================


class TestSite2CmActiveBypass:
    """CM is active → the bypass skips the inline error cascade.

    Mirrors Site 1A but in the error-reporting path. The bypass log
    statement at line 326-330 is the visible marker; the structural
    invariant is the same: NO ``SELECT COUNT(*)`` and NO inline
    ``parent.status = COMPLETED`` transition.
    """

    @pytest.mark.asyncio
    async def test_cm_active_skips_inline_cascade_in_error_path(self):
        """W2 mandatory: with CM active, the error cascade short-circuits
        before ``session.exec(select(func.count())...)``.

        The function does NOT return early when CM is active (unlike
        Site 1A which does), but the inline cascade block (lines
        331-391 of ``error_reporting.py``) IS skipped via the
        ``if cm is not None`` branch. The session.exec with the
        count-pending query therefore MUST NOT appear.
        """
        from daemon.services.error_reporting import ErrorReportingService
        from daemon.repositories.instance.models import InstanceStatus
        from daemon.repositories.message_queue.models import MessageStatus

        cm = MagicMock(spec=CorrelationManager, name="cm-active-site2")
        set_correlation_manager(cm)
        try:
            # Construct an ErrorReportingService with mocks.
            mock_manager = _make_mock_manager()
            # The service calls _instance_repository.get / _queue_repository.list
            # through asyncio.to_thread. Make those return quickly.
            child_meta = MagicMock()
            child_meta.parent_id = "parent-site2"
            child_meta.agent_name = "tester"
            child_meta.agent_dir = "/tmp/tester"
            mock_manager._instance_repository.get = MagicMock(return_value=child_meta)
            mock_manager._queue_repository.list = MagicMock(return_value=[])
            mock_manager._queue_repository.enqueue = MagicMock(
                return_value=MagicMock(message_id="err-msg-W2")
            )

            events_service = MagicMock()
            events_service._publish_instance_lifecycle_event = AsyncMock()

            service = ErrorReportingService(
                manager=mock_manager, events_service=events_service
            )

            # Build a mock session that the WriteGuardSession yields.
            parent = _make_parent(
                parent_id="parent-site2",
                status=InstanceStatus.RUNNING.value,
                waiting_for=0,
            )
            child = MagicMock()
            child.instance_id = "child-site2"
            child.parent_id = "parent-site2"
            child.status = "running"
            child.agent_id = "tester"
            child.instance_metadata = {}
            child.waiting_for = 0

            session = MagicMock()
            session.get = MagicMock(side_effect=lambda cls, iid: {
                "child-site2": child,
                "parent-site2": parent,
            }.get(iid))
            # UPDATE waiting_for … RETURNING
            update_result = MagicMock()
            update_result.first = MagicMock(return_value=(0,))
            session.execute = MagicMock(return_value=update_result)
            # session.exec — must be inspectable for the count query.
            session.exec = MagicMock()
            session.expire = MagicMock()
            session.commit = MagicMock()
            session.add = MagicMock()

            wgs = MagicMock()
            wgs.__enter__ = MagicMock(return_value=session)
            wgs.__exit__ = MagicMock(return_value=False)

            with patch(
                "daemon.services.correlation_manager.notify_corr_resolve",
                new=AsyncMock(),
            ):
                with patch(
                    "daemon.services.error_reporting.WriteGuardSession",
                    return_value=wgs,
                ):
                    with patch(
                        "daemon.services.error_reporting.Session",
                        return_value=MagicMock(),
                    ):
                        await service._send_error_report(
                            instance_id="child-site2",
                            error="test error",
                            error_type="execution_error",
                            message_id="msg-site2",
                        )

            # (a) The count-pending SELECT COUNT(*) MUST NOT have been
            # issued. The bypass skips the inline cascade block entirely.
            assert session.exec.call_count == 0, (
                f"CM-active error cascade MUST skip SELECT COUNT(*) — "
                f"got {session.exec.call_count} session.exec call(s). "
                f"Calls observed: "
                f"{[repr(c.args[0]) for c in session.exec.call_args_list]}"
            )

            # (b) parent.status MUST NOT be set to COMPLETED.
            assert parent.status == InstanceStatus.RUNNING.value, (
                f"CM-active error cascade must NOT mutate parent.status "
                f"inline — got {parent.status!r}"
            )

            # (c) The lifecycle event for the PARENT must NOT be published
            # inline. (A lifecycle event for the child is allowed — it is
            # not the bypassed path.)
            # The events service is only used for the parent completion
            # event in this function, so call_count should be 0.
            assert events_service._publish_instance_lifecycle_event.await_count == 0, (
                f"CM-active error cascade must NOT publish parent "
                f"lifecycle event inline; got "
                f"{events_service._publish_instance_lifecycle_event.await_count} call(s)"
            )
        finally:
            set_correlation_manager(None)


class TestSite2CmDisabledLegacy:
    """CM is None → the original error cascade runs (graceful degradation)."""

    @pytest.mark.asyncio
    async def test_cm_none_runs_select_count_and_completes_parent(self):
        """W2 mandatory: with CM=None, the legacy error cascade runs
        end-to-end: SELECT COUNT(*) → no pending messages → parent.status
        = COMPLETED → lifecycle event published.
        """
        from daemon.services.error_reporting import ErrorReportingService
        from daemon.repositories.instance.models import InstanceStatus

        set_correlation_manager(None)

        mock_manager = _make_mock_manager()
        child_meta = MagicMock()
        child_meta.parent_id = "parent-site2-none"
        child_meta.agent_name = "tester"
        child_meta.agent_dir = "/tmp/tester"
        mock_manager._instance_repository.get = MagicMock(return_value=child_meta)
        mock_manager._queue_repository.list = MagicMock(return_value=[])
        mock_manager._queue_repository.enqueue = MagicMock(
            return_value=MagicMock(message_id="err-msg-W2-none")
        )

        events_service = MagicMock()
        events_service._publish_instance_lifecycle_event = AsyncMock()

        service = ErrorReportingService(
            manager=mock_manager, events_service=events_service
        )

        parent = _make_parent(
            parent_id="parent-site2-none",
            status=InstanceStatus.RUNNING.value,
            waiting_for=0,
        )
        child = MagicMock()
        child.instance_id = "child-site2-none"
        child.parent_id = "parent-site2-none"
        child.status = "running"
        child.agent_id = "tester"
        child.instance_metadata = {}
        child.waiting_for = 0

        session = MagicMock()
        session.get = MagicMock(side_effect=lambda cls, iid: {
            "child-site2-none": child,
            "parent-site2-none": parent,
        }.get(iid))
        update_result = MagicMock()
        update_result.first = MagicMock(return_value=(0,))
        session.execute = MagicMock(return_value=update_result)
        # session.exec with the count-pending query returns 0.
        pending_result = MagicMock()
        pending_result.scalar_one = MagicMock(return_value=0)
        session.exec = MagicMock(return_value=pending_result)
        session.expire = MagicMock()
        session.commit = MagicMock()
        session.add = MagicMock()

        wgs = MagicMock()
        wgs.__enter__ = MagicMock(return_value=session)
        wgs.__exit__ = MagicMock(return_value=False)

        with patch(
            "daemon.services.error_reporting.WriteGuardSession",
            return_value=wgs,
        ):
            with patch(
                "daemon.services.error_reporting.Session",
                return_value=MagicMock(),
            ):
                await service._send_error_report(
                    instance_id="child-site2-none",
                    error="test error",
                    error_type="execution_error",
                    message_id="msg-site2-none",
                )

        # (a) SELECT COUNT(*) WAS executed.
        assert session.exec.call_count == 1, (
            f"CM=None legacy error cascade MUST run SELECT COUNT(*) — "
            f"expected 1 session.exec call, got {session.exec.call_count}"
        )

        # (b) Inline status transition to COMPLETED.
        assert parent.status == InstanceStatus.COMPLETED.value, (
            f"CM=None legacy error cascade must set parent.status = "
            f"COMPLETED; got {parent.status!r}"
        )

        # (c) The parent lifecycle event WAS published.
        assert events_service._publish_instance_lifecycle_event.await_count == 1


# =============================================================================
# Cross-site invariant: CM hook is called in BOTH paths (active + None)
# =============================================================================


class TestNotifyCorrResolveHookIsUniversal:
    """The ``notify_corr_resolve`` hook is the authoritative dispatch,
    not the inline cascade. It is called whenever there is a
    ``completed_message_id`` AND a CM, regardless of which path the
    function takes — but the inline cascade is the only one that does
    the work when CM is None.

    This is a sanity check on the hook's contract.
    """

    @pytest.mark.asyncio
    async def test_cm_active_hook_called_but_inline_cascade_skipped(self):
        """CM active → notify_corr_resolve called once, inline cascade
        skipped (verified by zero SELECT COUNT(*) calls)."""
        from daemon.services.child_reports import ChildReportsService
        from daemon.repositories.instance.models import InstanceStatus

        cm = MagicMock(spec=CorrelationManager, name="cm-hook-test")
        set_correlation_manager(cm)
        try:
            parent = _make_parent(status="running", waiting_for=0)
            child = _make_child()
            session = _setup_cascade_session(parent)
            mock_manager = _make_mock_manager()
            service = ChildReportsService(manager=mock_manager)

            mock_hook = AsyncMock(name="notify_corr_resolve")
            with patch(
                "daemon.services.correlation_manager.notify_corr_resolve",
                new=mock_hook,
            ):
                await service._update_parent_on_child_complete(
                    session, child, completed_message_id="msg-hook-test"
                )

            # The hook fires once (the authoritative dispatch).
            assert mock_hook.await_count == 1, (
                f"notify_corr_resolve must be called exactly once when "
                f"completed_message_id is set; got {mock_hook.await_count}"
            )
            # ...but the inline cascade did not touch the DB.
            assert session.exec.call_count == 0, (
                f"CM-active bypass must skip the inline cascade; got "
                f"{session.exec.call_count} session.exec call(s)"
            )
        finally:
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_no_message_id_skips_hook_but_legacy_cascade_runs(self):
        """Edge case: completed_message_id=None → notify_corr_resolve
        is NOT called (the hook is keyed on (child_id, message_id) and
        has nothing to resolve), but the legacy inline cascade still
        runs when CM is None.

        This proves the hook is optional and the legacy path is
        independent — important for the graceful-degradation invariant.
        """
        from daemon.services.child_reports import ChildReportsService
        from daemon.repositories.instance.models import InstanceStatus

        set_correlation_manager(None)

        parent = _make_parent(status="running", waiting_for=0)
        child = _make_child()
        session = _setup_cascade_session(parent)
        mock_manager = _make_mock_manager()
        service = ChildReportsService(manager=mock_manager)

        mock_hook = AsyncMock(name="notify_corr_resolve")
        with patch(
            "daemon.services.correlation_manager.notify_corr_resolve",
            new=mock_hook,
        ):
            await service._update_parent_on_child_complete(
                session, child, completed_message_id=None
            )

        # Hook was NOT called (no message_id → CM has nothing to resolve).
        assert mock_hook.await_count == 0
        # Legacy inline cascade still ran (1 session.exec call).
        assert session.exec.call_count == 1
        assert parent.status == InstanceStatus.COMPLETED.value

"""B2 RESOLVED (2026-09-11) — Resume = wake or LOUD refusal.

The previous ``silent=True + no handle`` resume path returned
``{status: 'silent_resume'}`` for any parked parent — a SILENT
NO-OP that the FE presented as success while the parent stayed
parked indefinitely. The 84563a03 incident (turn done 10:59:15,
no terminal report, parent parked ~4.5h) and the cascade-resume
silent-noop evidence (route_outcome=internal_child_noop silent=True,
15:01-15:04+07) are both direct consequences of this silent park.

B2 invariant: ``resume_processing_job(silent=True)`` on a
WAITING_CHILDREN parent ALWAYS produces one of:

  * ``status: 'wake_enqueued'`` — a real wake turn was enqueued
    via the durable ``enqueue_message`` primitive (writes
    MessageQueue + Task rows, flips WC→RUNNING, notifies the
    worker pool).
  * ``status: 'wake_failed'`` with structured ``error`` + ``refusal_kind`` —
    loud refusal. Surfaced to the FE so the operator can reason
    about the wedge.

The legitimate ``internal_child_noop`` contract (silent resume of
a non-WC parent where the parent owns the actual work) is
preserved.

Census stays at 23/1/0 — B2 uses the existing
``InstanceManager.enqueue_message`` primitive; no new admission
state writer / JobItem creator / work_id mint site.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Helpers — minimal manager fixture mirroring test_resume_child_notification.py
# ---------------------------------------------------------------------------


class _MockAsyncMessageResult:
    """Return value for ``InstanceManager.enqueue_message``."""

    def __init__(
        self, message_id: str = "msg-b2", job_id: str | None = "job-b2"
    ) -> None:
        self.message_id = message_id
        self.job_id = job_id


def _build_manager(
    *, instance_status: str | None = None, enqueue_side_effect: Exception | None = None
):
    """Build a minimal InstanceManager with mocked dependencies for B2.

    Returns (manager, lifecycle_service). The lifecycle service's
    ``get_instance_info`` returns a dict with the given status (None
    means the lifecycle service raises KeyError — instance row
    missing, the legitimate silent cascade path).
    """
    manager = MagicMock()
    manager._task_repo = MagicMock()
    manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
        return_value=None
    )
    manager._task_repo.find_suspended_turn_for_answer = MagicMock(
        return_value=None
    )

    if enqueue_side_effect is not None:
        manager.enqueue_message = AsyncMock(side_effect=enqueue_side_effect)
    else:
        manager.enqueue_message = AsyncMock(
            return_value=_MockAsyncMessageResult()
        )

    # Lifecycle service stub — get_instance_info returns a dict
    # with the desired status, or raises KeyError if instance_status is
    # explicitly None.
    lifecycle_service = MagicMock()
    if instance_status is None:
        lifecycle_service.get_instance_info = MagicMock(
            side_effect=KeyError("instance not found")
        )
    else:
        lifecycle_service.get_instance_info = MagicMock(
            return_value={"status": instance_status, "instance_id": "any"}
        )
    manager._lifecycle_service = lifecycle_service

    # Other attributes used by the resume router.
    manager._report_injection_repo = MagicMock()
    manager._report_injection_repo.find_deferred_for_parent = MagicMock(
        return_value=[]
    )
    manager._worker_pool = MagicMock()
    manager._is_parent_terminal = AsyncMock(return_value=True)
    manager._revive_terminal_instance = AsyncMock(return_value=True)

    return manager, lifecycle_service


def _install_resume_method(manager) -> MagicMock:
    """Bind the real ``resume_processing_job`` method to the mock manager.

    The resume method reads from ``self._task_repo`` and
    ``self._lifecycle_service`` and calls ``self.enqueue_message`` —
    all mocked. The real method's B2 logic is what we're testing.

    Implementation note: ``MagicMock`` doesn't allow attribute
    assignment in some configurations, so we use ``__class__``
    rewriting — set the manager's ``__class__`` to a fresh subclass
    that mixes in the real method, OR — simpler — use
    ``types.MethodType`` to bind the unbound function to the
    instance. The latter is more portable.
    """
    from daemon.manager import InstanceManager
    import types

    unbound = InstanceManager.resume_processing_job
    bound = types.MethodType(unbound, manager)
    manager.resume_processing_job = bound
    return manager


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestSilentResumeWCWakesOrRefuses:
    """B2 (2026-09-11): silent resume on a WC parent MUST wake or
    loudly refuse — never return ``silent_resume``.
    """

    @pytest.mark.asyncio
    async def test_wc_parent_silent_resume_enqueues_wake(self):
        """WC parent + silent=True → ``enqueue_message`` is called
        and the result returns ``status: 'wake_enqueued'``.

        This is the fix for the 84563a03 incident — a parked parent
        that previously got a silent no-op now gets a real wake
        turn.
        """
        manager, _ = _build_manager(instance_status="waiting_children")
        _install_resume_method(manager)

        result = await manager.resume_processing_job(
            "wc-parent-123", message="resume", silent=True
        )

        # B2 invariant: enqueue_message was called (the wake).
        manager.enqueue_message.assert_awaited_once()
        kwargs = manager.enqueue_message.await_args.kwargs
        assert kwargs["instance_id"] == "wc-parent-123"
        assert kwargs["source"] == "system:resume_wake"
        # B2 invariant: structured wake_enqueued return — NEVER
        # the legacy ``silent_resume``.
        assert result is not None
        assert result["status"] == "wake_enqueued"
        assert result["instance_id"] == "wc-parent-123"
        assert result["job_id"] == "job-b2"
        assert result["message_id"] == "msg-b2"

    @pytest.mark.asyncio
    async def test_wc_parent_silent_resume_loud_refusal_on_enqueue_error(self):
        """WC parent + silent=True + enqueue_message raises → LOUD
        refusal with structured ``error`` + ``refusal_kind`` — never
        a silent no-op.
        """
        manager, _ = _build_manager(
            instance_status="waiting_children",
            enqueue_side_effect=RuntimeError("DB write failed: parent_row_missing"),
        )
        _install_resume_method(manager)

        result = await manager.resume_processing_job(
            "wc-parent-456", message="resume", silent=True
        )

        # B2 invariant: enqueue was attempted (it raised).
        manager.enqueue_message.assert_awaited_once()
        # B2 invariant: LOUD refusal — the FE MUST see this,
        # not a silent success.
        assert result is not None
        assert result["status"] == "wake_failed"
        assert result["refusal_kind"] == "wc_wake_failed"
        assert "WAITING_CHILDREN" in result["error"]
        assert "DB write failed" in result["error"]
        assert result["job_id"] is None
        assert result["message_id"] is None

    @pytest.mark.asyncio
    async def test_non_wc_parent_silent_resume_preserves_noop(self):
        """Non-WC parent + silent=True → preserves the legitimate
        ``internal_child_noop`` contract (§9.3) — no enqueue,
        ``silent_resume`` return.

        The parent's own owner does the work; the child does not
        need a new message. This is the legitimate silent cascade
        case.
        """
        manager, _ = _build_manager(instance_status="idle")
        _install_resume_method(manager)

        result = await manager.resume_processing_job(
            "idle-parent-789", message="resume", silent=True
        )

        # B2 invariant for non-WC: NO enqueue, silent_resume return.
        manager.enqueue_message.assert_not_called()
        assert result is not None
        assert result["status"] == "silent_resume"

    @pytest.mark.asyncio
    async def test_running_parent_silent_resume_preserves_noop(self):
        """RUNNING parent + silent=True → preserves ``silent_resume``.

        RUNNING parents are not parked — there's nothing to wake.
        The legitimate no-op is preserved.
        """
        manager, _ = _build_manager(instance_status="running")
        _install_resume_method(manager)

        result = await manager.resume_processing_job(
            "running-parent-abc", message="resume", silent=True
        )

        manager.enqueue_message.assert_not_called()
        assert result["status"] == "silent_resume"

    @pytest.mark.asyncio
    async def test_paused_parent_silent_resume_preserves_noop(self):
        """PAUSED parent + silent=True → preserves ``silent_resume``.

        PAUSED parents are handled by the cascade-resume path
        (which sets PAUSED→RUNNING). The silent no-op is preserved
        — the parent will be woken by the cascade, not by the
        silent enqueue.
        """
        manager, _ = _build_manager(instance_status="paused")
        _install_resume_method(manager)

        result = await manager.resume_processing_job(
            "paused-parent-def", message="resume", silent=True
        )

        manager.enqueue_message.assert_not_called()
        assert result["status"] == "silent_resume"

    @pytest.mark.asyncio
    async def test_completed_parent_silent_resume_preserves_noop(self):
        """Terminal parent (COMPLETED) + silent=True → preserves
        ``silent_resume``. Terminal instances are handled by the
        revival paths, not by B2's wake path."""
        manager, _ = _build_manager(instance_status="completed")
        _install_resume_method(manager)

        result = await manager.resume_processing_job(
            "completed-parent-ghi", message="resume", silent=True
        )

        manager.enqueue_message.assert_not_called()
        assert result["status"] == "silent_resume"

    @pytest.mark.asyncio
    async def test_missing_instance_silent_resume_preserves_noop(self):
        """Instance row missing (lifecycle service raises KeyError)
        + silent=True → preserves ``silent_resume``.

        The lifecycle service stub raises KeyError; B2 must
        tolerate this and fall through to the legitimate silent
        path.
        """
        manager, _ = _build_manager(instance_status=None)
        _install_resume_method(manager)

        result = await manager.resume_processing_job(
            "missing-parent-jkl", message="resume", silent=True
        )

        manager.enqueue_message.assert_not_called()
        assert result["status"] == "silent_resume"


class TestSilentResumeNonSilentPreservesBehavior:
    """B2 invariant: silent=False behavior is UNCHANGED.

    Non-silent resumes (the user-facing path) still return
    ``None`` for ``invalid_or_missing_handle`` (the explicit
    routing-error contract — §9.4 removed the fallback fabrication).
    """

    @pytest.mark.asyncio
    async def test_wc_parent_non_silent_returns_none(self):
        """WC parent + silent=False → ``None`` (invalid_or_missing_handle).

        The user-facing path requires an explicit handle or paused
        turn; the WC silent-wake path only applies to ``silent=True``.
        This preserves the §9.4 contract.
        """
        manager, _ = _build_manager(instance_status="waiting_children")
        _install_resume_method(manager)

        result = await manager.resume_processing_job(
            "wc-parent-non-silent", message="please resume", silent=False
        )

        # No enqueue — the user-facing path requires an explicit handle.
        manager.enqueue_message.assert_not_called()
        # Returns None (invalid_or_missing_handle) — same as pre-B2.
        assert result is None

    @pytest.mark.asyncio
    async def test_idle_parent_non_silent_returns_none(self):
        """Non-WC + silent=False → ``None``."""
        manager, _ = _build_manager(instance_status="idle")
        _install_resume_method(manager)

        result = await manager.resume_processing_job(
            "idle-parent-non-silent", message="please resume", silent=False
        )

        manager.enqueue_message.assert_not_called()
        assert result is None


class TestB2StaticInvariants:
    """B2 (2026-09-11): the silent_resume no-op path is REPLACED for
    WC parents. The legacy path is preserved for non-WC parents
    (the §9.3 internal_child_noop contract).
    """

    def test_wc_branch_enqueues_message(self):
        """The WC branch in resume_processing_job must call
        ``enqueue_message`` (the wake primitive). Source-grep pins
        the contract.
        """
        from daemon import manager as manager_mod
        import inspect

        src = inspect.getsource(manager_mod.InstanceManager.resume_processing_job)
        assert "b2_wc_wake" in src, (
            "B2 added the b2_wc_wake route_outcome — it must be "
            "present in resume_processing_job."
        )
        assert "wake_enqueued" in src, (
            "B2 returns wake_enqueued status for the WC wake branch."
        )
        assert "wake_failed" in src, (
            "B2 returns wake_failed for the WC loud-refusal branch."
        )
        assert "system:resume_wake" in src, (
            "B2 wake messages carry the system:resume_wake source "
            "provenance."
        )

    def test_non_wc_branch_preserves_silent_resume(self):
        """The non-WC silent resume still returns ``silent_resume``
        — the §9.3 contract is preserved."""
        from daemon import manager as manager_mod
        import inspect

        src = inspect.getsource(manager_mod.InstanceManager.resume_processing_job)
        assert "silent_resume" in src, (
            "B2 preserves the silent_resume contract for non-WC "
            "parents (the §9.3 internal_child_noop case)."
        )
        assert "internal_child_noop" in src, (
            "B2 keeps the structured log line for the "
            "internal_child_noop case."
        )

    def test_no_new_admission_state_writer(self):
        """B2 uses the existing ``InstanceManager.enqueue_message``
        primitive (an existing 1-of-1 JobItem creator — the public
        facade). No new admission state writer site was added."""
        from daemon import manager as manager_mod
        import inspect

        src = inspect.getsource(manager_mod.InstanceManager.resume_processing_job)
        # The wake enqueue MUST go through ``self.enqueue_message``,
        # not a direct repo write (which would be a new writer site).
        assert "self.enqueue_message(" in src, (
            "B2 wake MUST go through InstanceManager.enqueue_message "
            "— the existing JobItem-creator facade."
        )
        # No direct repo calls (would bypass the facade and add a
        # new writer).
        assert "self._task_repo.create" not in src
        assert "self._queue_repository.create" not in src

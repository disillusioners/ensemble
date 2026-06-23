"""Tests for the root-instance carve-out guard in ChildReportsService.

The carve-out guard (daemon/services/child_reports.py lines 1017-1052)
prevents a root instance from being stuck in WAITING_CHILDREN when its
own message queue has stale/duplicate messages (pending_count > 0) BUT
the instance's MESSAGE job is already in a terminal state (completed,
failed, cancelled, dead_letter). Without this guard, the task-claim race
that produced the stale messages would leave the instance permanently
stuck with no code path to clear it.

These tests exercise the carve-out against a real in-memory SQLite
engine (StaticPool for cross-thread safety) with minimal manager mocks,
following the pattern in tests/test_deadlock_fix.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Model imports — required so SQLModel.metadata sees the tables when
# create_all() runs on the test engine.
from daemon.repositories.event.models import Event, EventKind  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import JobItem, JobStatus
from daemon.repositories.message_queue.models import (  # noqa: F401
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.task.models import Task  # noqa: F401
from daemon.services.child_reports import ChildReportsService
from daemon.services.completion_registry import (
    CompletionResult,
    get_completion_registry,
)
from daemon.services.dependency_bus import set_dependency_bus
from daemon.write_pause_guard import WritePauseGuard


# ─────────────────────────────────────────────────────────────────────────────
# Engine + service helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine (StaticPool for cross-thread safety)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(autouse=True)
def _reset_dependency_bus():
    """Ensure no DependencyBus singleton leaks between tests.

    The legacy ``waiting_for`` fallback path is required for the carve-out
    test (bus is None → falls through to ``instance.waiting_for`` read).
    """
    set_dependency_bus(None)
    yield
    set_dependency_bus(None)


def _build_child_reports_service(engine: Engine) -> ChildReportsService:
    """Build a real ``ChildReportsService`` with a mock manager that
    exposes only the attributes ``_process_child_completion_db_sync`` needs.

    Mirrors the helper in tests/test_deadlock_fix.py — uses ``__new__`` to
    skip ``__init__`` and bind attributes manually.
    """
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._checkpointer = None
    manager._live_hub = None  # SSE no-op (guarded on truthiness)
    manager._queue_repository = MagicMock()
    manager._instance_repository = MagicMock()

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None  # lifecycle event publish is guarded
    return service


def _seed_root_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    waiting_for: int = 0,
) -> str:
    """Insert a root Instance row (parent_id=None)."""
    iid = instance_id or f"root-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        inst = Instance(
            instance_id=iid,
            agent_id="leader",
            agent_name="leader",
            agent_dir="/tmp/leader",
            parent_id=None,
            status=status,
            waiting_for=waiting_for,
            version=1,
            instance_metadata={},
            children="[]",
        )
        session.add(inst)
        session.commit()
    return iid


def _seed_message(
    engine: Engine,
    *,
    instance_id: str,
    message_id: str | None = None,
    status: str = MessageStatus.READY.value,
) -> str:
    """Insert a MessageQueue row for the given instance."""
    mid = message_id or f"msg-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        msg = MessageQueue(
            message_id=mid,
            instance_id=instance_id,
            content="stale duplicate message",
            type=MessageType.HUMAN.value,
            status=status,
        )
        session.add(msg)
        session.commit()
    return mid


def _seed_message_job(
    engine: Engine,
    *,
    instance_id: str,
    status: str = JobStatus.COMPLETED.value,
) -> str:
    """Insert a JobItem with job_type='message' for the given instance."""
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        job = JobItem(
            job_id=job_id,
            agent_id="leader",
            agent_dir="/tmp/leader",
            message="test message",
            source="api",
            job_type="message",
            status=status,
            instance_id=instance_id,
        )
        session.add(job)
        session.commit()
    return job_id


def _seed_soft_deleted_message_job(
    engine: Engine,
    *,
    instance_id: str,
    status: str = JobStatus.COMPLETED.value,
) -> str:
    """Insert a JobItem with job_type='message' AND a non-null ``deleted_at``.

    Soft-deleted jobs are kept for audit but excluded from active-job
    queries. The carve-out guard MUST treat them as if they were absent
    (only ``deleted_at IS NULL`` jobs count as active).
    """
    job_id = f"job-deleted-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        job = JobItem(
            job_id=job_id,
            agent_id="leader",
            agent_dir="/tmp/leader",
            message="test message",
            source="api",
            job_type="message",
            status=status,
            instance_id=instance_id,
            deleted_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(job)
        session.commit()
    return job_id


def _build_child_reports_service_async(engine: Engine) -> ChildReportsService:
    """Build a ``ChildReportsService`` suitable for exercising the full
    async caller ``_process_child_completion_and_notify_parent``.

    The async caller reaches into the manager for:
      * ``engine`` + ``write_guard`` — for the WriteGuardSession
      * ``_instance_repository.get`` — real SQLModelInstanceRepository
        (the async caller awaits ``asyncio.to_thread(self._instance_repository.get, ...)``)
      * ``_checkpointer`` — None skips the assistant-message fetch
      * ``_live_hub`` — None guards the SSE branch out
      * ``_queue_repository`` — only used by title generation (mocked)

    Mirrors the pattern in ``tests/test_deadlock_fix.py::_build_child_reports_service``
    so the carve-out dispatch path can run end-to-end.
    """
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._instance_repository = SQLModelInstanceRepository(engine)
    manager._checkpointer = None
    manager._live_hub = None  # SSE no-op (guarded on truthiness)
    manager._queue_repository = MagicMock()

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
    return service


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestRootCarveOutTerminalJob:
    """Carve-out guard: root instance with stale pending messages AND a
    terminal MESSAGE job should NOT be set to WAITING_CHILDREN."""

    def test_carve_out_skips_waiting_children_when_message_job_terminal(
        self, engine: Engine
    ):
        """A root instance with pending_count > 0 in its own queue but
        whose MESSAGE job is already ``completed`` should hit the
        carve-out guard: return ``root_skipped_terminal_job`` and leave
        the instance status untouched (still ``running``).
        """
        # Arrange
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_message(engine, instance_id=root_id, status=MessageStatus.READY.value)
        _seed_message_job(engine, instance_id=root_id, status=JobStatus.COMPLETED.value)
        completed_message_id = "msg-already-completed-other"

        # Act
        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id=completed_message_id,
            last_content="some assistant text",
        )

        # Assert: carve-out guard fired
        assert result.outcome == "root_skipped_terminal_job"
        assert result.instance_id == root_id
        assert result.parent_id is None

        # Instance status MUST remain unchanged
        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst is not None
            assert inst.status == InstanceStatus.RUNNING.value, (
                f"Expected status=running (carve-out should not commit), "
                f"got status={inst.status}"
            )

    def test_carve_out_triggers_for_failed_message_job(self, engine: Engine):
        """The carve-out applies to ANY terminal MESSAGE job status, not
        just ``completed``. A ``failed`` MESSAGE job should also trigger it.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_message(engine, instance_id=root_id, status=MessageStatus.READY.value)
        _seed_message_job(engine, instance_id=root_id, status=JobStatus.FAILED.value)

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        assert result.outcome == "root_skipped_terminal_job"
        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.RUNNING.value

    def test_carve_out_triggers_for_cancelled_message_job(self, engine: Engine):
        """A ``cancelled`` MESSAGE job is also terminal — the carve-out
        should still fire.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_message(engine, instance_id=root_id, status=MessageStatus.READY.value)
        _seed_message_job(
            engine, instance_id=root_id, status=JobStatus.CANCELLED.value
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        assert result.outcome == "root_skipped_terminal_job"
        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.RUNNING.value

    def test_carve_out_triggers_for_dead_letter_message_job(self, engine: Engine):
        """A ``dead_letter`` MESSAGE job is also terminal — the carve-out
        should still fire. ``dead_letter`` is the final state for jobs that
        exhausted all retries and were moved to the DLQ; treating it as
        non-terminal would leave root instances stuck in WAITING_CHILDREN
        forever (no consumer will ever re-process a DLQ'd job).
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_message(engine, instance_id=root_id, status=MessageStatus.READY.value)
        _seed_message_job(
            engine, instance_id=root_id, status=JobStatus.DEAD_LETTER.value
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        assert result.outcome == "root_skipped_terminal_job"
        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.RUNNING.value


class TestRootPendingMessagesNormalPath:
    """Companion tests: when the MESSAGE job is NOT terminal, the carve-out
    MUST NOT fire and the normal ``root_waiting_children`` path should run.
    These guard against the carve-out over-firing and breaking the normal
    pending-messages branch."""

    def test_normal_path_sets_waiting_children_when_job_processing(
        self, engine: Engine
    ):
        """A root instance with pending_count > 0 AND a non-terminal
        (processing) MESSAGE job should proceed to set WAITING_CHILDREN
        and return ``root_waiting_children`` — the carve-out must not
        over-fire.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_message(engine, instance_id=root_id, status=MessageStatus.READY.value)
        # Non-terminal job — carve-out must NOT fire
        _seed_message_job(
            engine, instance_id=root_id, status=JobStatus.PROCESSING.value
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        assert result.outcome == "root_waiting_children"
        assert result.instance_id == root_id

        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.WAITING_CHILDREN.value


# ─────────────────────────────────────────────────────────────────────────────
# F5 — Multi-message-job case: terminal job coexisting with active job
# ─────────────────────────────────────────────────────────────────────────────


class TestRootCarveOutMultiMessageJob:
    """F5 scenario: MESSAGE jobs are NOT 1:1 per instance — every user
    message enqueues a new ``JobItem(job_type='message')``. A root that has
    BOTH a completed (terminal) old job AND a processing (active) new job
    has genuine pending work to do.

    The guard ``_has_no_active_message_job`` must look at ACTIVE jobs
    (PENDING/PROCESSING, ``deleted_at IS NULL``) — NOT terminal jobs — so
    the presence of a stale completed job does not falsely trigger the
    carve-out and strand a real, in-flight workflow.

    Before the F5 fix the guard would see "a terminal job exists → skip"
    and incorrectly drop the WAITING_CHILDREN write.
    """

    def test_active_job_coexists_with_completed_old_job(
        self, engine: Engine
    ):
        """One COMPLETED old job + one PROCESSING new job + pending message
        → guard does NOT fire, ``root_waiting_children`` is written.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_message(engine, instance_id=root_id, status=MessageStatus.READY.value)
        # F5 setup: one terminal (old, completed) AND one active (new, processing)
        _seed_message_job(
            engine, instance_id=root_id, status=JobStatus.COMPLETED.value
        )
        _seed_message_job(
            engine, instance_id=root_id, status=JobStatus.PROCESSING.value
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        assert result.outcome == "root_waiting_children", (
            f"F5 REGRESSION: expected 'root_waiting_children' (an active "
            f"PROCESSING job is real work), got '{result.outcome}'. The "
            f"guard must check ACTIVE jobs (PENDING/PROCESSING), not all "
            f"jobs — otherwise a completed old job would falsely trip the "
            f"carve-out and strand an in-flight workflow."
        )
        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.WAITING_CHILDREN.value


# ─────────────────────────────────────────────────────────────────────────────
# F8 — Soft-deleted terminal job: deleted_at IS NOT NULL jobs are NOT active
# ─────────────────────────────────────────────────────────────────────────────


class TestRootCarveOutSoftDeletedJob:
    """Soft-deleted (``deleted_at IS NOT NULL``) MESSAGE jobs are kept
    in the table for audit / replay but MUST be excluded from the
    ``_has_no_active_message_job`` query — they cannot be picked up by a
    worker, so they don't count as "active work in flight".

    A root with ONLY a soft-deleted terminal job and a pending message
    has no real worker that will drain the message. The guard MUST fire
    and the status MUST stay ``running`` to avoid a permanent
    WAITING_CHILDREN strand.
    """

    def test_soft_deleted_terminal_job_treated_as_absent(
        self, engine: Engine
    ):
        """A soft-deleted COMPLETED MESSAGE job + pending message + no
        active job → guard fires, status stays ``running``.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_message(engine, instance_id=root_id, status=MessageStatus.READY.value)
        # Soft-deleted terminal job — must NOT count as active
        _seed_soft_deleted_message_job(
            engine, instance_id=root_id, status=JobStatus.COMPLETED.value
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        assert result.outcome == "root_skipped_terminal_job", (
            f"Soft-deleted jobs must be invisible to the active-job query. "
            f"Got outcome='{result.outcome}'."
        )
        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.RUNNING.value, (
                "Carve-out must preserve the original status (no commit)."
            )

    def test_soft_deleted_processing_job_also_treated_as_absent(
        self, engine: Engine
    ):
        """A soft-deleted PROCESSING MESSAGE job also does not count as
        active — even though the status is non-terminal, ``deleted_at``
        means the worker will not pick it up.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_message(engine, instance_id=root_id, status=MessageStatus.READY.value)
        _seed_soft_deleted_message_job(
            engine, instance_id=root_id, status=JobStatus.PROCESSING.value
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        assert result.outcome == "root_skipped_terminal_job"
        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.RUNNING.value


# ─────────────────────────────────────────────────────────────────────────────
# F8 — Zero-job edge case: no MESSAGE job rows at all
# ─────────────────────────────────────────────────────────────────────────────


class TestRootCarveOutNoJobs:
    """Edge case: a root with pending messages but ZERO MESSAGE jobs of
    any kind (terminal, active, or otherwise). The pending messages are
    pure noise — no worker is going to drain them. The carve-out must
    fire so the instance is not permanently stranded.
    """

    def test_zero_jobs_with_pending_messages_fires_carve_out(
        self, engine: Engine
    ):
        """No JobItem rows for the instance + one pending READY message
        → guard fires, status preserved.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_message(engine, instance_id=root_id, status=MessageStatus.READY.value)
        # Intentionally NO _seed_message_job call — zero jobs in the table.

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        assert result.outcome == "root_skipped_terminal_job", (
            f"Zero-job case must trip the carve-out. Got outcome="
            f"'{result.outcome}'."
        )
        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.RUNNING.value


# ─────────────────────────────────────────────────────────────────────────────
# F6 — CompletionRegistry IS signaled when the carve-out fires
# ─────────────────────────────────────────────────────────────────────────────


class TestRootCarveOutCompletionRegistrySignaled:
    """F6 scenario: when the carve-out fires (``root_skipped_terminal_job``),
    the async dispatch path MUST still call ``CompletionRegistry.complete``
    so any ``invoke_agent_and_wait()`` callers do not hang waiting for an
    event that will never come.

    The status write is intentionally skipped (the whole point of the
    carve-out), but the completion signal is a separate concern — the
    instance HAS finished whatever work it was doing; the carve-out just
    declines to mark it as WAITING_CHILDREN because the pending work is
    stale.
    """

    @pytest.mark.asyncio
    async def test_completion_registry_signaled_when_carve_out_fires(
        self, engine: Engine
    ):
        """End-to-end: carve-out fires → ``CompletionRegistry.complete()``
        is called with the correct ``instance_id`` and the carved-out
        ``last_content`` (no hang for waiters).
        """
        service = _build_child_reports_service_async(engine)
        root_id = _seed_root_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_message(engine, instance_id=root_id, status=MessageStatus.READY.value)
        # Force the carve-out: terminal job only, no active worker.
        _seed_message_job(
            engine, instance_id=root_id, status=JobStatus.COMPLETED.value
        )

        # Build a real registry, capture the singleton, and assert
        # ``complete()`` is called. The async dispatch path imports
        # ``get_completion_registry`` lazily inside the
        # ``if outcome == 'root_skipped_terminal_job':`` block — so
        # we must patch the symbol on its own module, not on
        # child_reports (which would shadow the lazy import).
        #
        # The carve-out path always calls ``complete()`` even when the
        # status write is intentionally skipped — the instance has
        # finished its work; we just decline to mark it
        # WAITING_CHILDREN. The exact content payload depends on the
        # checkpointer (the test sets ``_checkpointer=None`` so the
        # async caller falls back to the empty-content sentinel
        # ``"[No response content]"``); we only assert the call shape
        # here, not the content.
        registry = get_completion_registry()
        registry.register(root_id)
        # Pre-clear any buffered/result state from a prior test in the
        # singleton (the autouse ``_reset_completion_registry_singleton``
        # fixture below handles this between tests, but be explicit for
        # safety).
        registry._buffered.pop(root_id, None)
        registry._results.pop(root_id, None)
        registry._events[root_id].clear()

        with patch(
            "daemon.services.completion_registry.get_completion_registry",
            return_value=registry,
            create=True,
        ) as mock_get_registry:
            await service._process_child_completion_and_notify_parent(
                instance_id=root_id,
                completed_message_id="msg-different-id",
            )

            # Registry was queried at the carve-out dispatch site
            mock_get_registry.assert_called()
            # And ``complete()`` was invoked exactly once for this
            # instance — so any ``invoke_agent_and_wait()`` callers do
            # not hang waiting for an event that will never come.
            assert root_id in registry._results, (
                "F6 REGRESSION: CompletionRegistry was NOT signaled for "
                "the carved-out instance — invoke_agent_and_wait() callers "
                "would hang forever."
            )
            result: CompletionResult = registry._results[root_id]
            assert result.is_error is False
            # Content is the empty sentinel because this test runs
            # without a checkpointer — the important invariant is that
            # ``complete()`` was called (above), not the content value.


@pytest.fixture(autouse=True)
def _reset_completion_registry_singleton():
    """Reset the global ``CompletionRegistry`` singleton between tests so
    state from one F6 test does not leak into the next.
    """
    import daemon.services.completion_registry as cr_module

    cr_module._completion_registry = None
    yield
    cr_module._completion_registry = None

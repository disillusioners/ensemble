"""Unit tests for F13, F14, F15 observer hardening (Phase 3 of defer-seam bugfix).

These tests pin the three observer-hardening fixes that close remaining
wrong-finalize paths in the ``JobFeedbackObserver``:

  * **F13 — exact job resolution**: ``get_active_by_instance`` and the
    observer's ``_get_processing_job_for_instance`` resolve by exact
    ``job_id`` when provided. Prevents finalizing the WRONG sibling
    when two ACTIVE JobItems exist for the same instance.
  * **F14 — bus gate sees pending tasks**: the premature-finalization
    gate also counts non-bus-registered PENDING ``task`` rows for the
    instance. Prevents premature finalization when a child Task's
    ``bus.watch`` failed before the lifecycle event fired.
  * **F15 — deferred finalize TOCTOU guard**: the 5s
    ``_deferred_finalize_check`` captures ``expected_job_id`` at
    scheduling time and verifies the same job_id is still active
    after the sleep. Prevents finalizing a freshly-created JobItem
    that appeared during the sleep window.

The tests use a mix of:

  * **Real SQLite engine** (via the ``engine`` fixture from
    ``tests/job_queue/conftest.py``) — required for F13's repository-
    level helper test and F14's DB-backed pending-task query.
  * **Mocked collaborators** (``AsyncMock`` / ``MagicMock``) — keeps
    the observer unit-testable: the bus gate, finalize chain, and
    deferred scheduling are all stubbed.

Backward-compatibility invariants pinned by these tests:

  * F13: when ``job_id is None``, the legacy freshest-by-
    ``created_at`` ordering is preserved.
  * F14: when no PENDING tasks exist for the instance, the gate
    passes cleanly (no false-positive deferrals).
  * F15: when ``expected_job_id is None`` (post-D13 MESSAGE path),
    the TOCTOU guard short-circuits and the legacy behavior runs.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from daemon.repositories.job_queue import JobRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.repositories.instance.models import InstanceStatus
from daemon.services.dependency_bus import set_dependency_bus
from daemon.services.job_feedback_observer import (
    JobFeedbackObserver,
    _FinalizeJobResult,
    _ProcessingJobContext,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _insert_instance(
    engine,
    instance_id: str,
    project_id: str = "test-project",
    status: str = "running",
    agent_id: str = "developer",
) -> None:
    """Insert an Instance row directly via SQL."""
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status, :project_id,
                     :created_at, :updated_at, 1)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": agent_id,
                "agent_dir": f"agents/{agent_id}",
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
            },
        )


def _insert_job_item(
    engine,
    *,
    job_id: str,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str | None = None,
    admission_state: str = AdmissionState.QUEUED.value,
    job_metadata: dict | None = None,
    created_at: datetime | None = None,
) -> None:
    """Insert a JobItem directly via SQL with explicit created_at."""
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    metadata_json = json.dumps(job_metadata or {})
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source,
                     project_id, queue_id, priority, admission_state,
                     created_at, instance_id, job_type, retry_count,
                     metadata)
                VALUES
                    (:job_id, :agent_id, :agent_dir, :message, :source,
                     :project_id, :queue_id, :priority, :admission_state,
                     :created_at, :instance_id, :job_type, :retry_count,
                     :metadata)
                """
            ),
            {
                "job_id": job_id,
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "message": "hi",
                "source": "api",
                "project_id": project_id,
                "queue_id": queue_id,
                "priority": 0,
                "admission_state": admission_state,
                "created_at": now,
                "instance_id": instance_id,
                "job_type": "task",
                "retry_count": 0,
                "metadata": metadata_json,
            },
        )


def _create_pending_task(
    engine,
    *,
    instance_id: str,
    message_id: str | None = None,
) -> int:
    """Insert a PENDING ``task`` row directly via SQL and return its id."""
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                     :retry_count, :created_at, :cancel_requested,
                     :retry_scheduled, :work_id, :is_deferred)
                """
            ),
            {
                "task_type": TaskType.PROCESS_MESSAGE.value,
                "instance_id": instance_id,
                "message_id": message_id,
                "status": TaskStatus.PENDING.value,
                "retry_count": 0,
                "created_at": now,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": str(uuid.uuid4()),
                "is_deferred": False,
            },
        )
        return result.lastrowid


def _make_observer_with_engine(engine) -> tuple[JobFeedbackObserver, MagicMock]:
    """Build a ``JobFeedbackObserver`` whose ``_instance_manager.engine``
    points at the real test engine.

    The observer's other dependencies (event_bus, job_queue_service,
    job_repo, lock_repo, project_repo) are ``MagicMock`` — only the
    engine-bearing attribute is needed for the F14 sync helper that
    opens its own ``SQLModelSession``.
    """
    observer = JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=MagicMock(),
        job_repo=MagicMock(spec=JobRepository),
        lock_repo=MagicMock(spec=LockRepository),
        project_repo=MagicMock(),
        instance_manager=MagicMock(),
    )
    # The F14 sync helper reads ``self._instance_manager.engine`` —
    # wire it to the real test engine so the COUNT runs against the
    # same DB the test seeded.
    observer._instance_manager.engine = engine
    return observer, observer._instance_manager


# ─── F13 Tests ────────────────────────────────────────────────────────────────


class TestF13ExactJobResolution:
    """F13: ``get_active_by_instance`` resolves by exact ``job_id`` when provided."""

    def test_get_active_by_instance_with_job_id_resolves_exact(self, engine):
        """When ``job_id`` is provided, ``get_active_by_instance``
        returns the matching row, even if it is NOT the freshest.
        """
        repository = JobRepository(engine)
        instance_id = "inst-f13-1"

        # Two ACTIVE JobItems for one instance — older + newer.
        _insert_instance(engine, instance_id)
        older_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        newer_time = datetime(2026, 1, 1, 12, 1, 0, tzinfo=timezone.utc)
        _insert_job_item(
            engine,
            job_id="job-older",
            instance_id=instance_id,
            admission_state=AdmissionState.ACTIVE.value,
            created_at=older_time,
        )
        _insert_job_item(
            engine,
            job_id="job-newer",
            instance_id=instance_id,
            admission_state=AdmissionState.ACTIVE.value,
            created_at=newer_time,
        )

        # Resolve by exact ID — older.
        result_older = repository.get_active_by_instance(
            instance_id, job_id="job-older"
        )
        assert result_older is not None
        assert result_older.job_id == "job-older"

        # Resolve by exact ID — newer.
        result_newer = repository.get_active_by_instance(
            instance_id, job_id="job-newer"
        )
        assert result_newer is not None
        assert result_newer.job_id == "job-newer"

    def test_get_active_by_instance_without_job_id_uses_freshest(
        self, engine
    ):
        """When ``job_id is None`` (legacy path), the freshest-by-
        ``created_at`` ordering is preserved for backward
        compatibility.
        """
        repository = JobRepository(engine)
        instance_id = "inst-f13-2"

        _insert_instance(engine, instance_id)
        older_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        newer_time = datetime(2026, 1, 1, 12, 1, 0, tzinfo=timezone.utc)
        _insert_job_item(
            engine,
            job_id="job-older",
            instance_id=instance_id,
            admission_state=AdmissionState.ACTIVE.value,
            created_at=older_time,
        )
        _insert_job_item(
            engine,
            job_id="job-newer",
            instance_id=instance_id,
            admission_state=AdmissionState.ACTIVE.value,
            created_at=newer_time,
        )

        # Legacy path: no job_id → freshest-by-created_at (job-newer).
        result = repository.get_active_by_instance(instance_id)
        assert result is not None
        assert result.job_id == "job-newer"

    def test_get_active_by_instance_with_unknown_job_id_returns_none(
        self, engine
    ):
        """When ``job_id`` is provided but does NOT match any row,
        ``get_active_by_instance`` returns ``None`` (the exact-ID
        filter rejects the row). Prevents finalizing a phantom job.
        """
        repository = JobRepository(engine)
        instance_id = "inst-f13-3"

        _insert_instance(engine, instance_id)
        _insert_job_item(
            engine,
            job_id="job-real",
            instance_id=instance_id,
            admission_state=AdmissionState.ACTIVE.value,
        )

        result = repository.get_active_by_instance(
            instance_id, job_id="job-phantom"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_get_processing_job_for_instance_threads_job_id(
        self, engine
    ):
        """The observer's ``_get_processing_job_for_instance`` threads
        the optional ``job_id`` through to ``get_active_by_instance``
        so the wrong-sibling bug is closed end-to-end.
        """
        repository = JobRepository(engine)
        observer, _ = _make_observer_with_engine(engine)
        # Wire the real repo for the defense-in-depth re-query path.
        observer._job_repo = repository
        # ``get_job_by_instance`` returns the freshest row (job-newer).
        observer._job_queue_service.get_job_by_instance = AsyncMock(
            return_value=MagicMock(
                spec=JobItem,
                job_id="job-newer",
                admission_state=AdmissionState.DONE.value,
                instance_id="inst-f13-4",
            )
        )
        # The defense-in-depth re-query path runs only when the
        # freshest row is non-PROCESSING (defense against stale rows).
        # Insert two ACTIVE rows + flip the freshest to DONE so the
        # re-query path executes.
        instance_id = "inst-f13-4"
        _insert_instance(engine, instance_id)
        older_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        newer_time = datetime(2026, 1, 1, 12, 1, 0, tzinfo=timezone.utc)
        # job-older is the surviving ACTIVE JobItem (the one we want).
        _insert_job_item(
            engine,
            job_id="job-older",
            instance_id=instance_id,
            admission_state=AdmissionState.ACTIVE.value,
            created_at=older_time,
        )
        # job-newer is the freshest but already DONE (terminated in DB).
        _insert_job_item(
            engine,
            job_id="job-newer",
            instance_id=instance_id,
            admission_state=AdmissionState.DONE.value,
            created_at=newer_time,
        )

        # F13 path: caller passes job_id="job-older" (the live ACTIVE
        # row). The helper should resolve by exact ID and return the
        # matching context — NOT the freshest DONE row.
        ctx = await observer._get_processing_job_for_instance(
            instance_id, job_id="job-older"
        )
        assert ctx is not None
        assert ctx.job_id == "job-older"
        assert ctx.instance_id == instance_id


# ─── F14 Tests ────────────────────────────────────────────────────────────────


class TestF14BusGateSeesPendingTasks:
    """F14: the premature-finalization gate defers when the ``task`` table
    has PENDING rows for the instance (children not registered in the bus).
    """

    def test_sync_helper_counts_pending_tasks(self, engine):
        """``_count_pending_tasks_for_instance_sync`` returns the
        number of PENDING ``task`` rows for ``instance_id``.
        """
        observer, _ = _make_observer_with_engine(engine)
        instance_id = "inst-f14-1"

        # Two PENDING tasks + one RUNNING task.
        _insert_instance(engine, instance_id)
        _create_pending_task(engine, instance_id=instance_id)
        _create_pending_task(engine, instance_id=instance_id)

        # Insert a RUNNING task — should NOT count.
        running_task_id = _create_pending_task(
            engine, instance_id="inst-other"
        )
        # Promote that task to RUNNING via raw SQL.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET status = :status WHERE id = :id"
                ),
                {"status": TaskStatus.RUNNING.value, "id": running_task_id},
            )

        # Two PENDING tasks for instance_id.
        count = observer._count_pending_tasks_for_instance_sync(
            instance_id
        )
        assert count == 2

    def test_sync_helper_returns_zero_when_no_pending_tasks(
        self, engine
    ):
        """When no PENDING ``task`` rows exist for the instance, the
        helper returns 0 (no false-positive deferrals).
        """
        observer, _ = _make_observer_with_engine(engine)
        instance_id = "inst-f14-2"
        _insert_instance(engine, instance_id)

        count = observer._count_pending_tasks_for_instance_sync(
            instance_id
        )
        assert count == 0

    def test_sync_helper_handles_missing_instance_manager_engine(
        self, engine
    ):
        """When ``_instance_manager.engine`` is ``None`` (test mock
        that did not wire an engine), the helper returns 0 — the
        F14 gate cannot run, but the in-session gate below is the
        authoritative check.
        """
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=MagicMock(),
            job_repo=MagicMock(spec=JobRepository),
            lock_repo=MagicMock(spec=LockRepository),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        # ``MagicMock().engine`` is a MagicMock (truthy by default);
        # explicit None forces the FAIL-OPEN path.
        observer._instance_manager.engine = None

        count = observer._count_pending_tasks_for_instance_sync(
            "inst-f14-3"
        )
        assert count == 0

    def test_sync_helper_fail_open_on_db_error(self, engine):
        """When the DB query raises, the helper returns 0
        (FAIL-OPEN). The in-session inline query below is the
        authoritative safety net — exceptions there propagate to
        the W3 fail-safe path in ``_finalize_job``.
        """
        observer, _ = _make_observer_with_engine(engine)
        # Patch ``SQLModelSession`` to raise — simulates a transient
        # DB failure.
        with patch(
            "sqlmodel.Session",
            side_effect=RuntimeError("simulated DB outage"),
        ):
            count = observer._count_pending_tasks_for_instance_sync(
                "inst-f14-4"
            )
        assert count == 0


# ─── F15 Tests ────────────────────────────────────────────────────────────────


class TestF15DeferredFinalizeToctouGuard:
    """F15: the 5s ``_deferred_finalize_check`` captures ``expected_job_id``
    at scheduling time and verifies the same job_id is still active after
    the sleep. A new JobItem created during the sleep window aborts the
    finalize for the old job.
    """

    @pytest.mark.asyncio
    async def test_new_job_during_sleep_skips_old_finalize(self):
        """When ``expected_job_id`` is set but the post-sleep re-query
        returns a context with a DIFFERENT ``job_id`` (a new JobItem
        was created during the sleep window), the deferred check
        returns WITHOUT driving ``_finalize_job``.
        """
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=MagicMock(),
            job_repo=MagicMock(spec=JobRepository),
            lock_repo=MagicMock(spec=LockRepository),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )

        # Wire the bus to return 0 pending (so the gate is cleared
        # and we reach the re-query + TOCTOU guard).
        bus_mock = MagicMock()
        bus_mock.count_pending_for_target = AsyncMock(return_value=0)
        bus_mock.had_parent_error = MagicMock(return_value=False)
        set_dependency_bus(bus_mock)
        try:
            # Spy on ``_finalize_job`` — it must NOT be awaited.
            finalize_spy = AsyncMock(return_value=None)
            observer._finalize_job = finalize_spy

            # The post-sleep re-query returns a NEW job_id (a
            # ``job_continue``/``watch_job`` created a fresh JobItem
            # on this instance during the sleep window).
            new_ctx = _ProcessingJobContext(
                instance_id="inst-f15-1", job_id="job-NEW"
            )
            observer._get_processing_job_for_instance = AsyncMock(
                return_value=new_ctx
            )

            await observer._deferred_finalize_check(
                "inst-f15-1",
                delay=0.01,
                expected_job_id="job-OLD",
            )

            # The bus was consulted (cleared the gate).
            bus_mock.count_pending_for_target.assert_awaited_once_with(
                "inst-f15-1"
            )
            # The helper was called with the exact ``expected_job_id``
            # threaded through.
            observer._get_processing_job_for_instance.assert_awaited_once_with(
                "inst-f15-1", "job-OLD"
            )
            # Finalize was NOT driven — the F15 TOCTOU guard caught
            # the mismatch and skipped.
            finalize_spy.assert_not_called()
        finally:
            set_dependency_bus(None)

    @pytest.mark.asyncio
    async def test_same_job_after_sleep_drives_finalize(self):
        """When ``expected_job_id`` matches the post-sleep active
        ``job_id``, the deferred check drives ``_finalize_job`` (no
        new JobItem was created during the sleep window — the legacy
        happy path is preserved).
        """
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=MagicMock(),
            job_repo=MagicMock(spec=JobRepository),
            lock_repo=MagicMock(spec=LockRepository),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )

        bus_mock = MagicMock()
        bus_mock.count_pending_for_target = AsyncMock(return_value=0)
        bus_mock.had_parent_error = MagicMock(return_value=False)
        set_dependency_bus(bus_mock)
        try:
            finalize_spy = AsyncMock(return_value=None)
            observer._finalize_job = finalize_spy

            # Post-sleep re-query returns the SAME job_id.
            ctx = _ProcessingJobContext(
                instance_id="inst-f15-2", job_id="job-stable"
            )
            observer._get_processing_job_for_instance = AsyncMock(
                return_value=ctx
            )

            # Instance status is RUNNING so the pre-check passes.
            with patch.object(
                observer,
                "_read_instance_status_sync",
                return_value=InstanceStatus.RUNNING.value,
            ):
                await observer._deferred_finalize_check(
                    "inst-f15-2",
                    delay=0.01,
                    expected_job_id="job-stable",
                )

            # Finalize WAS driven — same job_id, no TOCTOU mismatch.
            finalize_spy.assert_awaited_once()
        finally:
            set_dependency_bus(None)

    @pytest.mark.asyncio
    async def test_legacy_path_when_expected_job_id_is_none(self):
        """When ``expected_job_id is None`` (post-D13 MESSAGE path —
        no JobItem at scheduling time), the TOCTOU guard short-
        circuits and the deferred check proceeds with the legacy
        freshest-by-``created_at`` lookup.

        The re-query is called with ``job_id=None`` (no exact-ID
        filter) and the guard does not block finalize when the
        post-sleep active ``job_id`` differs.
        """
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=MagicMock(),
            job_repo=MagicMock(spec=JobRepository),
            lock_repo=MagicMock(spec=LockRepository),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )

        bus_mock = MagicMock()
        bus_mock.count_pending_for_target = AsyncMock(return_value=0)
        bus_mock.had_parent_error = MagicMock(return_value=False)
        set_dependency_bus(bus_mock)
        try:
            finalize_spy = AsyncMock(return_value=None)
            observer._finalize_job = finalize_spy

            # Post-sleep re-query returns a job_id (the legacy
            # freshest row) — but the legacy path does not enforce
            # the exact-ID match.
            ctx = _ProcessingJobContext(
                instance_id="inst-f15-3", job_id="job-freshest"
            )
            observer._get_processing_job_for_instance = AsyncMock(
                return_value=ctx
            )

            with patch.object(
                observer,
                "_read_instance_status_sync",
                return_value=InstanceStatus.RUNNING.value,
            ):
                await observer._deferred_finalize_check(
                    "inst-f15-3",
                    delay=0.01,
                    expected_job_id=None,
                )

            # The helper was called with ``job_id=None`` — the legacy
            # freshest-by-created_at path.
            observer._get_processing_job_for_instance.assert_awaited_once_with(
                "inst-f15-3", None
            )
            # Finalize WAS driven (no guard enforcement on the legacy
            # path).
            finalize_spy.assert_awaited_once()
        finally:
            set_dependency_bus(None)

    @pytest.mark.asyncio
    async def test_no_processing_context_skips_finalize(self):
        """When the post-sleep re-query returns ``None`` (no JobItem
        exists for the instance — the lookup raised), the deferred
        check returns silently. The TOCTOU guard is irrelevant
        here because there is no job_id to compare against.
        """
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=MagicMock(),
            job_repo=MagicMock(spec=JobRepository),
            lock_repo=MagicMock(spec=LockRepository),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )

        bus_mock = MagicMock()
        bus_mock.count_pending_for_target = AsyncMock(return_value=0)
        bus_mock.had_parent_error = MagicMock(return_value=False)
        set_dependency_bus(bus_mock)
        try:
            finalize_spy = AsyncMock(return_value=None)
            observer._finalize_job = finalize_spy
            observer._get_processing_job_for_instance = AsyncMock(
                return_value=None
            )

            await observer._deferred_finalize_check(
                "inst-f15-4",
                delay=0.01,
                expected_job_id="job-OLD",
            )

            # Finalize was NOT driven — lookup returned None.
            finalize_spy.assert_not_called()
        finally:
            set_dependency_bus(None)
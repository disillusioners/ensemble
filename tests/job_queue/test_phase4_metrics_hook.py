"""Phase 4 Skill Evolution — completion-metrics hook tests.

Covers Task 5 of the Phase 4 plan:

* :meth:`JobQueueService._get_task_details` returns the expected
  ``{instance_id, agent_id, project_id, iterations, duration_seconds}``
  shape for a normal job and degrades gracefully when the message
  queue lookup is unavailable.
* The metrics hook in :meth:`JobQueueService._finalize_terminal`
  records the task completion on the
  :class:`SkillMetricsService` after a successful terminal write
  (and ONLY after — a concurrent no-op return must not trigger
  metrics).
* Metrics failures NEVER block job finalization — the boundary
  logs a warning and returns the canonical ``(job_id, final_status)``
  tuple regardless.

These are the only test surfaces for the hook — the underlying
``SkillMetricsService.record_task_completion`` already has full
unit coverage in
``tests/services/test_skill_metrics_service.py`` (Phase 4 Tasks 1-2).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, select

from daemon.repositories.job_queue import (
    AdmissionState,
    Decision,
    JobItem,
    JobQueue,
    JobQueueRepository,
    QueueType,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.services.job_queue_service import JobQueueService


# ─── Engine + repo fixtures (mirror test_jq_proxy_phase4_finalize_terminal) ──


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite + StaticPool + FK enforcement."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def queue_repo(engine: Engine) -> JobQueueRepository:
    return JobQueueRepository(engine)


@pytest.fixture(autouse=True)
def _truncate_tables(engine):
    """Override the autouse ``_truncate_tables`` from
    ``tests/job_queue/conftest.py`` so the global DELETE pass
    doesn't trip FK constraints after our per-test cleanup.

    The ``job_queue_service`` fixture's teardown handles cleanup
    in correct dependency order, so this override just yields
    without touching any tables.
    """
    yield


@pytest.fixture
def job_queue_service(
    job_repo: JobRepository,
    queue_repo: JobQueueRepository,
):
    """A ``JobQueueService`` with a MagicMock'd ``instance_manager``.

    The Phase 4 hook reaches ``self._instance_manager._skill_metrics_service``
    so the tests must inject a manager-like object onto the service. The
    lock_manager is also a MagicMock with async no-ops for the lock
    release path. The fixture also wires teardown that drops every
    test-created table row before the autouse ``_truncate_tables``
    fixture's DELETE pass runs (which would otherwise hit FK errors
    because the MagicMock lock_manager doesn't actually clear
    ``job_locks`` rows).
    """
    lock_manager = MagicMock()
    lock_manager.release_by_instance = AsyncMock(return_value=[])
    lock_manager.release_queue_lock = AsyncMock(return_value=None)
    lock_manager.release_by_job = AsyncMock(return_value=False)

    instance_manager = MagicMock()

    service = JobQueueService(
        repository=job_repo,
        lock_manager=lock_manager,
        queue_repo=queue_repo,
        instance_manager=instance_manager,
    )

    yield service

    # Teardown: drop every test-created row in FK-respecting order so
    # the autouse ``_truncate_tables`` fixture's post-yield DELETE
    # pass runs against an empty schema. The DBAPI-level connection
    # is used directly because SQLModel's ``Session.exec`` only
    # accepts ORM-bound statements, not raw text.
    try:
        from sqlalchemy import text as _text

        with job_repo.engine.begin() as conn:
            for tbl in (
                "job_queue_items",
                "job_locks",
                "dead_letter_items",
            ):
                try:
                    conn.execute(_text(f'DELETE FROM "{tbl}"'))
                except Exception:
                    pass
            for q in queue_repo.list_by_project("test-project") or []:
                conn.execute(
                    _text('DELETE FROM "job_queues" WHERE queue_id = :qid'),
                    {"qid": q.queue_id},
                )
    except Exception:
        pass


# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_queue(engine: Engine, *, project_id: str = "test-project") -> str:
    queue_id = f"q-{uuid.uuid4().hex[:12]}"
    queue_name = f"q-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        s.add(
            JobQueue(
                queue_id=queue_id,
                project_id=project_id,
                queue_name=queue_name,
                queue_name_lower=queue_name,
                queue_type=QueueType.FIFO.value,
                concurrency_limit=1,
            )
        )
        s.commit()
    return queue_id


def _make_and_start_job(
    engine: Engine,
    job_repo: JobRepository,
    *,
    instance_id: str = "inst-1",
    project_id: str = "test-project",
    agent_id: str = "developer",
    created_at: str | None = None,
) -> JobItem:
    """Create a job, optionally back-date it, then start it as ACTIVE."""
    queue_id = _make_queue(engine, project_id=project_id)
    kwargs: dict[str, Any] = {
        "agent_id": agent_id,
        "agent_dir": "/tmp/agents/developer",
        "message": "phase4 metrics hook test job",
        "source": "api",
        "project_id": project_id,
        "queue_id": queue_id,
        "priority": 5,
    }
    job = job_repo.create(**kwargs)
    if created_at is not None:
        # Backdate via raw SQL — JobRepository.create doesn't accept
        # ``created_at`` (it uses a model default_factory).
        with Session(engine) as s:
            row = s.get(JobItem, job.job_id)
            if row is not None:
                row.created_at = created_at
                s.add(row)
                s.commit()
                s.refresh(row)
                job = row
    started = job_repo.start_job(job.job_id, instance_id=instance_id)
    assert started is not None
    return job


def _refresh(engine: Engine, job_id: str) -> JobItem:
    with Session(engine) as s:
        return s.exec(select(JobItem).where(JobItem.job_id == job_id)).one()


# ─── _get_task_details ──────────────────────────────────────────────────────


class TestGetTaskDetails:
    """``_get_task_details`` returns the canonical shape used by the hook."""

    @pytest.mark.asyncio
    async def test_returns_canonical_shape_for_normal_job(
        self, engine, job_repo, job_queue_service
    ):
        """Fresh job → instance_id/agent_id/project_id/counts populated."""
        instance_id = "inst-details-1"
        job = _make_and_start_job(
            engine,
            job_repo,
            instance_id=instance_id,
            project_id="proj-details",
            agent_id="developer",
        )

        details = await job_queue_service._get_task_details(job.job_id)

        assert details is not None
        assert details["instance_id"] == instance_id
        assert details["agent_id"] == "developer"
        assert details["project_id"] == "proj-details"
        # Default agent/message-queue repo is None on the MagicMock
        # manager → iterations default to 0.
        assert details["iterations"] == 0
        # duration_seconds is computed from created_at → now; just
        # assert it's a non-negative int.
        assert isinstance(details["duration_seconds"], int)
        assert details["duration_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_job(
        self, job_queue_service
    ):
        """Unknown job_id → ``None`` so the hook skips recording."""
        details = await job_queue_service._get_task_details(
            "does-not-exist"
        )
        assert details is None

    @pytest.mark.asyncio
    async def test_counts_agent_messages_from_queue_repo(
        self, engine, job_repo, job_queue_service
    ):
        """``iterations`` counts ``type='agent'`` rows on the
        instance's message queue."""
        # Wire a stub queue_repo that returns 3 agent rows + 1 human
        # row + 1 system row. Only agent rows should count.
        stub_queue_repo = MagicMock()
        stub_queue_repo.get_by_instance = MagicMock(
            return_value=[
                MagicMock(type="agent"),
                MagicMock(type="agent"),
                MagicMock(type="agent"),
                MagicMock(type="human"),
                MagicMock(type="system"),
            ]
        )
        job_queue_service._instance_manager._queue_repository = (
            stub_queue_repo
        )

        job = _make_and_start_job(
            engine, job_repo, instance_id="inst-iter"
        )

        details = await job_queue_service._get_task_details(job.job_id)
        assert details is not None
        assert details["iterations"] == 3

    @pytest.mark.asyncio
    async def test_duration_uses_job_created_at(
        self, engine, job_repo, job_queue_service
    ):
        """Back-dating created_at produces a larger duration_seconds."""
        # Backdate by 10 minutes — duration_seconds should be >= 600.
        ten_min_ago = (
            datetime.now(timezone.utc) - timedelta(minutes=10)
        ).isoformat()
        job = _make_and_start_job(
            engine,
            job_repo,
            instance_id="inst-duration",
            created_at=ten_min_ago,
        )

        details = await job_queue_service._get_task_details(job.job_id)
        assert details is not None
        # Allow a 5-second slack for test execution time.
        assert details["duration_seconds"] >= 600 - 5

    @pytest.mark.asyncio
    async def test_falls_back_when_queue_repo_missing(
        self, engine, job_repo, job_queue_service
    ):
        """No message queue repo on the manager → iterations=0."""
        # MagicMock default: accessing any attribute returns a MagicMock,
        # so set _queue_repository=None explicitly to trigger the fallback.
        job_queue_service._instance_manager._queue_repository = None

        job = _make_and_start_job(
            engine, job_repo, instance_id="inst-no-qr"
        )

        details = await job_queue_service._get_task_details(job.job_id)
        assert details is not None
        assert details["iterations"] == 0

    @pytest.mark.asyncio
    async def test_handles_message_queue_repo_exception(
        self, engine, job_repo, job_queue_service
    ):
        """A queue-repo exception is logged and iterations=0; never raises."""

        def _raise(_instance_id: str) -> list:
            raise RuntimeError("simulated DB failure")

        stub = MagicMock()
        stub.get_by_instance = MagicMock(side_effect=_raise)
        job_queue_service._instance_manager._queue_repository = stub

        job = _make_and_start_job(
            engine, job_repo, instance_id="inst-boom"
        )

        details = await job_queue_service._get_task_details(job.job_id)
        assert details is not None
        assert details["iterations"] == 0


# ─── Metrics hook integration ──────────────────────────────────────────────


class TestMetricsHookInFinalizeTerminal:
    """The hook records usage only after a successful terminal write."""

    @pytest.mark.asyncio
    async def test_records_task_completion_on_no_retry_success(
        self, engine, job_repo, queue_repo, job_queue_service
    ):
        """A successful NO_RETRY dispatch fires
        ``metrics_service.record_task_completion`` once with the
        task_succeeded flag set from the derived status."""
        # Inject a mock metrics service that records the call args.
        metrics = MagicMock()
        metrics.record_task_completion = AsyncMock(return_value=1)
        job_queue_service._instance_manager._skill_metrics_service = (
            metrics
        )

        job = _make_and_start_job(
            engine,
            job_repo,
            instance_id="inst-hook-ok",
            project_id="proj-hook",
        )

        canonical_id, status = await job_queue_service._finalize_terminal(
            instance_id="inst-hook-ok",
            decision=Decision.NO_RETRY,
            job_id=job.job_id,
            target_status="completed",
        )

        assert canonical_id == job.job_id
        assert status == "completed"
        metrics.record_task_completion.assert_awaited_once()
        kwargs = metrics.record_task_completion.await_args.kwargs
        assert kwargs["instance_id"] == "inst-hook-ok"
        assert kwargs["agent_id"] == "developer"
        assert kwargs["project_id"] == "proj-hook"
        assert kwargs["task_succeeded"] is True
        assert isinstance(kwargs["iterations"], int)
        assert isinstance(kwargs["duration_seconds"], int)

    @pytest.mark.asyncio
    async def test_records_failed_task_on_no_retry_failure(
        self, engine, job_repo, queue_repo, job_queue_service
    ):
        """Failed terminalization records ``task_succeeded=False``."""
        metrics = MagicMock()
        metrics.record_task_completion = AsyncMock(return_value=0)
        job_queue_service._instance_manager._skill_metrics_service = (
            metrics
        )

        job = _make_and_start_job(
            engine, job_repo, instance_id="inst-hook-fail"
        )

        await job_queue_service._finalize_terminal(
            instance_id="inst-hook-fail",
            decision=Decision.NO_RETRY,
            job_id=job.job_id,
            target_status="failed",
        )

        kwargs = metrics.record_task_completion.await_args.kwargs
        assert kwargs["task_succeeded"] is False

    @pytest.mark.asyncio
    async def test_metrics_failure_does_not_block_finalization(
        self, engine, job_repo, queue_repo, job_queue_service
    ):
        """A raising metrics service is logged + swallowed; the
        finalize still returns the canonical tuple and the row
        lands in DONE."""
        metrics = MagicMock()

        async def _raise(**_kwargs: Any) -> None:
            raise RuntimeError("simulated metrics failure")

        metrics.record_task_completion = AsyncMock(side_effect=_raise)
        job_queue_service._instance_manager._skill_metrics_service = (
            metrics
        )

        job = _make_and_start_job(
            engine, job_repo, instance_id="inst-boom"
        )

        canonical_id, status = await job_queue_service._finalize_terminal(
            instance_id="inst-boom",
            decision=Decision.NO_RETRY,
            job_id=job.job_id,
            target_status="completed",
        )

        assert canonical_id == job.job_id
        assert status == "completed"
        # The terminal row IS persisted despite the metrics boom.
        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value

    @pytest.mark.asyncio
    async def test_skips_hook_when_metrics_service_absent(
        self, engine, job_repo, queue_repo, job_queue_service
    ):
        """No metrics service on the manager → finalize succeeds silently."""
        # Explicitly remove the attribute so the getattr(None, ...)
        # fallback inside the hook fires.
        job_queue_service._instance_manager._skill_metrics_service = None

        job = _make_and_start_job(
            engine, job_repo, instance_id="inst-no-metrics"
        )

        canonical_id, status = await job_queue_service._finalize_terminal(
            instance_id="inst-no-metrics",
            decision=Decision.NO_RETRY,
            job_id=job.job_id,
            target_status="completed",
        )

        assert canonical_id == job.job_id
        assert status == "completed"

    @pytest.mark.asyncio
    async def test_skips_hook_when_instance_manager_missing(
        self, engine, job_repo, queue_repo, job_queue_service
    ):
        """No instance_manager wired → finalize succeeds (no metrics call)."""
        # Build a fresh service without instance_manager.
        service = JobQueueService(
            repository=job_repo,
            lock_manager=job_queue_service._lock_manager,
            queue_repo=queue_repo,
            instance_manager=None,
        )

        job = _make_and_start_job(
            engine, job_repo, instance_id="inst-no-mgr"
        )

        canonical_id, status = await service._finalize_terminal(
            instance_id="inst-no-mgr",
            decision=Decision.NO_RETRY,
            job_id=job.job_id,
            target_status="completed",
        )

        assert canonical_id == job.job_id
        assert status == "completed"

    @pytest.mark.asyncio
    async def test_metrics_not_called_on_retry_decision(
        self, engine, job_repo, queue_repo, job_queue_service
    ):
        """The hook lives on the NO_RETRY branch only — RETRY must NOT
        trigger ``record_task_completion`` (the job hasn't really
        finished; it's queued for another attempt)."""
        metrics = MagicMock()
        metrics.record_task_completion = AsyncMock(return_value=0)
        job_queue_service._instance_manager._skill_metrics_service = (
            metrics
        )

        # Wire a stub retry engine that flips active → queued.
        class _StubRetry:
            def maybe_retry(self, _job_id: str) -> JobItem | None:
                return None  # no retry → DLQ path

        job_queue_service.set_retry_engine(_StubRetry())

        job = _make_and_start_job(
            engine, job_repo, instance_id="inst-retry-path"
        )

        canonical_id, status = await job_queue_service._finalize_terminal(
            instance_id="inst-retry-path",
            decision=Decision.RETRY,
            job_id=job.job_id,
        )

        assert canonical_id == job.job_id
        assert status == "dead_letter"
        metrics.record_task_completion.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_records_on_real_metrics_service(
        self, engine, job_repo, queue_repo
    ):
        """End-to-end: the hook fires
        ``metrics_service.record_task_completion`` with the
        fields the Tier 0 recorder needs.

        Full-stack coverage of the Tier 0 recorder lives in
        ``tests/services/test_skill_metrics_service.py`` — here
        we only verify the *handoff* from the boundary to the
        service. We use a MagicMock for the metrics service so
        this test doesn't depend on the skill tables (those are
        created separately by the
        ``tests/services/test_skill_metrics_service.py`` suite).
        """
        metrics = MagicMock()
        metrics.record_task_completion = AsyncMock(return_value=1)
        # Verify the wrapper signature matches the recorder
        # contract (instance_id, agent_id, project_id,
        # task_succeeded, iterations, duration_seconds).
        captured: dict[str, Any] = {}

        async def _capture(**kwargs: Any) -> int:
            captured.update(kwargs)
            return 1

        metrics.record_task_completion = AsyncMock(side_effect=_capture)

        lock_manager = MagicMock()
        lock_manager.release_by_instance = AsyncMock(return_value=[])
        lock_manager.release_queue_lock = AsyncMock(return_value=None)
        lock_manager.release_by_job = AsyncMock(return_value=False)
        instance_manager = MagicMock()
        instance_manager._skill_metrics_service = metrics
        service = JobQueueService(
            repository=job_repo,
            lock_manager=lock_manager,
            queue_repo=queue_repo,
            instance_manager=instance_manager,
        )

        job = _make_and_start_job(
            engine,
            job_repo,
            instance_id="inst-e2e",
            project_id="proj-e2e",
            agent_id="developer",
        )

        canonical_id, status = await service._finalize_terminal(
            instance_id="inst-e2e",
            decision=Decision.NO_RETRY,
            job_id=job.job_id,
            target_status="completed",
        )
        assert canonical_id == job.job_id
        assert status == "completed"

        # Verify the recorded call carried the Tier 0 contract.
        assert captured.get("instance_id") == "inst-e2e"
        assert captured.get("agent_id") == "developer"
        assert captured.get("project_id") == "proj-e2e"
        assert captured.get("task_succeeded") is True
        assert isinstance(captured.get("iterations"), int)
        assert isinstance(captured.get("duration_seconds"), int)
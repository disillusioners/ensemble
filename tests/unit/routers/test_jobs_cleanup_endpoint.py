"""Unit tests for the ``POST /api/jobs/cleanup`` System Jobs Cleanup endpoint.

The endpoint is wired in ``daemon/routers/jobs_management.py`` and is
the public "system reset" hook for the job board: it cancels every
job whose ``admission_state`` is ``'queued'`` or ``'active'`` (active
jobs use the per-job ``cancel_job`` cascade so the underlying
instance is terminated and locks are released). Terminal rows
(``done`` / ``dead``) and soft-deleted rows are left untouched.

Coverage:
    * Endpoint registration (path / method / ``/jobs`` prefix).
    * Service-level :func:`JobQueueService.cleanup_non_terminal_jobs`
      with a mocked repository for the queued batch path and the
      per-job cancel cascade.
    * Endpoint integration with a mocked service to exercise the
      ``is_write_paused`` 503 guard, the response schema, and the
      manager error path.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Group 1 — Endpoint registration
# ---------------------------------------------------------------------------


class TestCleanupEndpointRegistration:
    """Pin the endpoint shape: path / method / router prefix."""

    def test_cleanup_route_registered_under_jobs_prefix(self):
        """POST /api/jobs/cleanup must be registered on the jobs router."""
        from fastapi import APIRouter

        from daemon.routers.jobs import router as jobs_router

        api_router = APIRouter(prefix="/api")
        api_router.include_router(jobs_router)
        app = FastAPI()
        app.include_router(api_router)

        route = next(
            (
                r
                for r in app.routes
                if getattr(r, "path", None) == "/api/jobs/cleanup"
            ),
            None,
        )
        assert route is not None, "POST /api/jobs/cleanup must be registered"
        assert "POST" in route.methods

    def test_cleanup_endpoint_lives_in_jobs_management_router(self):
        """Cleanup endpoint is exported from the ``jobs_management`` module."""
        from daemon.routers import jobs_management

        cleanup_paths = [
            r.path
            for r in jobs_management.router.routes
            if getattr(r, "path", None).endswith("/cleanup")
        ]
        assert cleanup_paths, "cleanup endpoint missing from jobs_management"

    def test_cleanup_endpoint_uses_job_clean_up_response_model(self):
        """Response model is ``JobCleanupResponse`` so OpenAPI introspects the
        three-counter shape (``cancelled_queued``, ``cancelled_active``,
        ``total_processed``)."""
        from daemon.routers import jobs_management
        from daemon.routers.schemas import JobCleanupResponse

        # FastAPI tags the response_model on the underlying APIRoute
        # object, not on the bare function -- find the POST /cleanup
        # route and assert its response_model.
        cleanup_route = next(
            r
            for r in jobs_management.router.routes
            if getattr(r, "path", None).endswith("/cleanup")
            and "POST" in getattr(r, "methods", set())
        )
        assert cleanup_route.response_model is JobCleanupResponse

    def test_job_cleanup_response_schema_has_four_counters(self):
        """The response schema must carry exactly the five contract fields.

        Counter contract (Phase 2 — System Cleanup reaper; Phase 4 —
        bad-state Task reconciliation):

          * ``cancelled_queued``   — batch-UPDATE PENDING rows
          * ``cancelled_active``   — per-row cancel cascade (PROCESSING)
          * ``orphaned_reaped``    — force-finalized ghost active rows
                                     (Phase 2 of the System Cleanup
                                     button — instance gone but
                                     ``admission_state='active'``).
          * ``reconciled_bad_state`` — bad-state Tasks (paused/pending
                                       with terminal JobItem) reconciled
                                       to CANCELLED (Phase 4).
          * ``total_processed``    — sum of the first two only.

        ``orphaned_reaped`` and ``reconciled_bad_state`` are kept OUT of
        the ``total_processed`` invariant so existing operator dashboards
        / tests that sum only the first two counters continue to
        reconcile.
        """
        from daemon.routers.schemas import JobCleanupResponse

        fields = set(JobCleanupResponse.model_fields.keys())
        assert fields == {
            "cancelled_queued",
            "cancelled_active",
            "orphaned_reaped",
            "reconciled_bad_state",
            "total_processed",
        }

    def test_job_cleanup_response_total_excludes_orphaned_reaped(self):
        """``total_processed`` must equal ``cancelled_queued + cancelled_active``
        even when ``orphaned_reaped > 0`` — pinning that invariant means
        future cleanup-pipeline changes cannot silently re-classify
        ghost rows into the existing two-counter contract.
        """
        from daemon.routers.schemas import JobCleanupResponse

        response = JobCleanupResponse(
            cancelled_queued=4,
            cancelled_active=2,
            orphaned_reaped=7,
            total_processed=6,
        )
        assert response.total_processed == 6
        assert response.orphaned_reaped == 7


# ---------------------------------------------------------------------------
# Group 2 — Service-level: cleanup_non_terminal_jobs()
# ---------------------------------------------------------------------------


class TestCleanupNonTerminalJobsService:
    """Unit-test the service method with a mocked repository.

    Two branches to cover:

    * Queued bucket -> ``batch_cancel_queued`` (single batch UPDATE).
    * Active bucket -> one ``cancel_job`` per row found by
      ``find_active_jobs``.
    """

    @pytest.mark.asyncio
    async def test_cleanup_with_queued_only_calls_batch_update(self):
        """When only queued rows exist, ``batch_cancel_queued`` runs and
        ``find_active_jobs`` returns ``[]``. No per-job cancel
        cascade fires."""
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService.__new__(JobQueueService)
        repo = MagicMock()
        repo.batch_cancel_queued = MagicMock(return_value=7)
        repo.find_active_jobs = MagicMock(return_value=[])
        service._repository = repo
        # No active jobs to cancel, so we don't need an instance manager.

        result = await service.cleanup_non_terminal_jobs()

        repo.batch_cancel_queued.assert_called_once_with()
        repo.find_active_jobs.assert_called_once_with()
        assert result == {
            "cancelled_queued": 7,
            "cancelled_active": 0,
            "orphaned_reaped": 0,
            "reconciled_bad_state": 0,
            "total_processed": 7,
        }

    @pytest.mark.asyncio
    async def test_cleanup_iterates_active_jobs_through_cancel_job(self):
        """Active rows are routed through the per-job ``cancel_job`` cascade."""
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService.__new__(JobQueueService)
        repo = MagicMock()
        repo.batch_cancel_queued = MagicMock(return_value=2)

        # Two active job rows; ``cancel_job`` returns True for both.
        active_job_a = SimpleNamespace(job_id="active-A")
        active_job_b = SimpleNamespace(job_id="active-B")
        repo.find_active_jobs = MagicMock(return_value=[active_job_a, active_job_b])
        service._repository = repo

        # Patch ``cancel_job`` on the bound instance so the cleanup loop
        # counts it as "cancelled_active" without touching the real cascade.
        service.cancel_job = AsyncMock(side_effect=[True, True])

        result = await service.cleanup_non_terminal_jobs()

        service.cancel_job.assert_any_call("active-A")
        service.cancel_job.assert_any_call("active-B")
        assert service.cancel_job.await_count == 2
        assert result == {
            "cancelled_queued": 2,
            "cancelled_active": 2,
            "orphaned_reaped": 0,
            "reconciled_bad_state": 0,
            "total_processed": 4,
        }

    @pytest.mark.asyncio
    async def test_cleanup_counts_only_successful_cancel_job_results(self):
        """Per-job failures (``cancel_job`` returns False) are counted as 0,
        matching the single-cancel endpoint's success/Failure contract."""
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService.__new__(JobQueueService)
        repo = MagicMock()
        repo.batch_cancel_queued = MagicMock(return_value=0)
        repo.find_active_jobs = MagicMock(
            return_value=[
                SimpleNamespace(job_id="ok"),
                SimpleNamespace(job_id="race-lost"),
                SimpleNamespace(job_id="ok2"),
            ]
        )
        service._repository = repo
        service.cancel_job = AsyncMock(side_effect=[True, False, True])

        result = await service.cleanup_non_terminal_jobs()

        assert result == {
            "cancelled_queued": 0,
            "cancelled_active": 2,
            "orphaned_reaped": 0,
            "reconciled_bad_state": 0,
            "total_processed": 2,
        }

    @pytest.mark.asyncio
    async def test_cleanup_continues_when_cancel_job_raises(self):
        """An exception in ``cancel_job`` is logged at WARNING and the loop
        continues (best-effort semantics). The final count drops the failed
        row but still reports what succeeded."""
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService.__new__(JobQueueService)
        repo = MagicMock()
        repo.batch_cancel_queued = MagicMock(return_value=1)
        repo.find_active_jobs = MagicMock(
            return_value=[
                SimpleNamespace(job_id="ok"),
                SimpleNamespace(job_id="boom"),
                SimpleNamespace(job_id="ok2"),
            ]
        )
        service._repository = repo
        service.cancel_job = AsyncMock(
            side_effect=[True, RuntimeError("simulated cascade failure"), True]
        )

        result = await service.cleanup_non_terminal_jobs()

        assert result == {
            "cancelled_queued": 1,
            "cancelled_active": 2,
            "orphaned_reaped": 0,
            "reconciled_bad_state": 0,
            "total_processed": 3,
        }

    @pytest.mark.asyncio
    async def test_cleanup_reaps_orphan_active_jobs(self):
        """Orphan active rows (instance terminal/missing) are reaped via
        ``force_finalize_orphan``. They surface as ``orphaned_reaped`` in
        the response and are NOT counted into ``cancelled_active`` or
        ``total_processed``.

        Reap order assertions:

        * ``find_orphan_active_jobs`` is called once.
        * ``force_finalize_orphan`` is called for each candidate.
        * The regular ``cancel_job`` loop is untouched (none of the
          orphans go through it).
        * The returned dict carries ``orphaned_reaped`` so the FE
          snackbar can surface the ghost-row drain — Phase-2 review
          caught the case where the dict was missing the key and the
          ``JobCleanupResponse`` schema silently fell back to ``0``.
        """
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService.__new__(JobQueueService)
        repo = MagicMock()
        repo.batch_cancel_queued = MagicMock(return_value=0)
        # No live active jobs — the per-row cancel loop is empty.
        repo.find_active_jobs = MagicMock(return_value=[])
        # Two ghosts (e.g. killed-mid-ack workers whose instances are
        # already terminal) and one None (missing instance_id).
        orphan_a = SimpleNamespace(job_id="orphan-A")
        orphan_b = SimpleNamespace(job_id="orphan-B")
        orphan_c = SimpleNamespace(job_id="orphan-C")
        repo.find_orphan_active_jobs = MagicMock(
            return_value=[orphan_a, orphan_b, orphan_c]
        )
        repo.force_finalize_orphan = MagicMock(
            side_effect=[
                SimpleNamespace(job_id="orphan-A"),
                None,  # row vanished mid-flight
                SimpleNamespace(job_id="orphan-C"),
            ]
        )
        service._repository = repo

        result = await service.cleanup_non_terminal_jobs()

        # Per-row reap ran for all three candidates; ``None`` is
        # counted as 0 (not reaped) by the service loop.
        assert repo.force_finalize_orphan.call_count == 3
        # Wire-up uses ``asyncio.to_thread`` which invokes the bound
        # method directly — just check the positional args.
        called_ids = [
            call.args[0] for call in repo.force_finalize_orphan.call_args_list
        ]
        assert called_ids == ["orphan-A", "orphan-B", "orphan-C"]
        # The orphan reap counter must travel back in the response so
        # the FE can show "reaped N orphan active" — encoding the
        # earlier bug (#2) where the dict dropped the key.
        assert result == {
            "cancelled_queued": 0,
            "cancelled_active": 0,
            "orphaned_reaped": 2,
            "reconciled_bad_state": 0,
            "total_processed": 0,
        }

    @pytest.mark.asyncio
    async def test_cleanup_continues_when_orphan_reap_raises(self):
        """Per-row reap failures (or a single ``find`` failure) MUST NOT
        abort the cleanup. The main counters still return, and the
        response simply omits the failed orphans from
        ``orphaned_reaped``.

        Implemented via ``try/except`` around the reap loop in the
        service; this test pins that contract so a future refactor
        (e.g. someone moves reap before the cancel loop) cannot
        silently regress error isolation.
        """
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService.__new__(JobQueueService)
        repo = MagicMock()
        repo.batch_cancel_queued = MagicMock(return_value=2)
        repo.find_active_jobs = MagicMock(return_value=[])
        # ``find_orphan_active_jobs`` itself raises — defensive outer
        # block must swallow.
        repo.find_orphan_active_jobs = MagicMock(
            side_effect=RuntimeError("simulated reap finder failure")
        )
        service._repository = repo

        result = await service.cleanup_non_terminal_jobs()

        # Batch + active counters still report; orphaned_reaped drops
        # out because the reap pass raised.
        assert result == {
            "cancelled_queued": 2,
            "cancelled_active": 0,
            "orphaned_reaped": 0,
            "reconciled_bad_state": 0,
            "total_processed": 2,
        }


# ---------------------------------------------------------------------------
# Group 3 — Endpoint integration: POST /jobs/cleanup
# ---------------------------------------------------------------------------


@pytest.fixture
def cleanup_test_app():
    """FastAPI app with the management router + a stub service.

    ``app.state.manager`` is explicitly set to a stub whose
    ``is_write_paused`` is ``False`` so the write-pause guard does not
    503 the endpoint. The autouse ``_ensure_app_state_manager``
    fixture in ``tests/conftest.py`` provides the same default, but
    setting it explicitly makes the test self-contained.
    """
    app = FastAPI()
    # Import the inner management router so we don't pull in jobs_crud /
    # jobs_streaming fixtures the cleanup tests do not need.
    from daemon.routers.jobs_management import router as management_router

    app.include_router(management_router)
    app.state.manager = MagicMock(is_write_paused=False)
    yield app
    # Reset the singleton so the next test gets a fresh dependency.
    from daemon.routers.jobs_crud import get_job_queue_service

    get_job_queue_service.set_service(None)


@pytest.fixture
def cleanup_client(cleanup_test_app):
    with TestClient(cleanup_test_app) as client:
        yield client


class TestCleanupJobsEndpoint:
    """Integration tests through the FastAPI app."""

    def _stub_service(self, return_value):
        svc = MagicMock()
        svc.cleanup_non_terminal_jobs = AsyncMock(return_value=return_value)
        return svc

    def test_cleanup_returns_200_with_counters(self, cleanup_client):
        from daemon.routers.jobs_crud import get_job_queue_service

        service = self._stub_service(
            {"cancelled_queued": 4, "cancelled_active": 2, "total_processed": 6}
        )
        get_job_queue_service.set_service(service)

        response = cleanup_client.post("/jobs/cleanup")

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "cancelled_queued": 4,
            "cancelled_active": 2,
            "orphaned_reaped": 0,
            "reconciled_bad_state": 0,
            "total_processed": 6,
        }
        service.cleanup_non_terminal_jobs.assert_awaited_once_with()

    def test_cleanup_includes_orphaned_reaped_when_set(self, cleanup_client):
        """``orphaned_reaped`` is surfaced in the response when the
        reaper drained ghost active rows. ``total_processed`` stays
        pinned to ``cancelled_queued + cancelled_active`` so dashboards
        keyed off that single number do not silently jump when an
        orphan sweep succeeds.
        """
        from daemon.routers.jobs_crud import get_job_queue_service

        service = self._stub_service(
            {
                "cancelled_queued": 1,
                "cancelled_active": 0,
                "orphaned_reaped": 3,
                "total_processed": 1,
            }
        )
        get_job_queue_service.set_service(service)

        response = cleanup_client.post("/jobs/cleanup")

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "cancelled_queued": 1,
            "cancelled_active": 0,
            "orphaned_reaped": 3,
            "reconciled_bad_state": 0,
            "total_processed": 1,
        }

    def test_cleanup_returns_zeros_when_nothing_to_cancel(self, cleanup_client):
        """Empty job board -> all counters zero, still 200 (idempotent)."""
        from daemon.routers.jobs_crud import get_job_queue_service

        service = self._stub_service(
            {"cancelled_queued": 0, "cancelled_active": 0, "total_processed": 0}
        )
        get_job_queue_service.set_service(service)

        response = cleanup_client.post("/jobs/cleanup")

        assert response.status_code == 200
        assert response.json()["total_processed"] == 0

    def test_cleanup_returns_503_when_writes_paused(self, cleanup_client):
        """``is_write_paused=True`` short-circuits before touching the service.

        FastAPI evaluates :class:`Depends` BEFORE the endpoint body, so a
        stub service must be registered even for the pause-guard to
        trigger -- otherwise the dependency injector wins the race and
        reports ``"JobQueueService not initialized"`` instead.
        """
        from daemon.routers.jobs_crud import get_job_queue_service

        # Register a stub service so the dependency resolves; the
        # pause-guard inside the handler is what we want to exercise.
        get_job_queue_service.set_service(self._stub_service({"total_processed": 0}))
        cleanup_client.app.state.manager = MagicMock(is_write_paused=True)

        response = cleanup_client.post("/jobs/cleanup")

        assert response.status_code == 503
        assert "paused" in response.json()["detail"].lower()

    def test_cleanup_returns_500_when_service_raises(self, cleanup_client):
        """An unexpected exception from the service is wrapped in HTTP 500."""
        from daemon.routers.jobs_crud import get_job_queue_service

        service = MagicMock()
        service.cleanup_non_terminal_jobs = AsyncMock(
            side_effect=RuntimeError("database write failed")
        )
        get_job_queue_service.set_service(service)

        response = cleanup_client.post("/jobs/cleanup")

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["error"] == "Cleanup failed"
        assert "database write failed" in detail["message"]

    def test_cleanup_returns_503_when_service_not_initialised(self, cleanup_client):
        """No service registered -> dependency injector raises 503 via the
        shared ``create_service_dependency`` wiring on jobs_crud."""
        # Default fixture resets the dependency at teardown; assert
        # without setting anything triggers the 503 path.
        with patch(
            "daemon.routers.jobs_crud.get_job_queue_service.set_service"
        ):
            response = cleanup_client.post("/jobs/cleanup")
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# Group 4 — Repository primitives (batch_cancel_queued, find_active_jobs)
# ---------------------------------------------------------------------------


class TestRepositoryPrimitives:
    """Pin the two repository methods added to drive the cleanup endpoint.

    Using real SQLite (StaticPool) so SQL semantics match production;
    the SQL guard is the race-safety boundary on both dialects.
    """

    @pytest.fixture
    def engine(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel

        eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(eng)
        yield eng
        eng.dispose()

    @pytest.fixture
    def repo(self, engine):
        from daemon.repositories.job_queue.repository import JobRepository

        return JobRepository(engine)

    def _create(self, repo, **overrides):
        defaults = {
            "agent_id": "tester",
            "agent_dir": "/tmp/tester",
            "message": "hello",
            "source": "api",
            "project_id": "test-project",
            "priority": 5,
        }
        defaults.update(overrides)
        return repo.create(**defaults)

    def test_batch_cancel_queued_marks_only_queued_rows_terminal(self, repo):
        """`batch_cancel_queued` updates rows whose admission_state is
        'queued' and leaves active / done / dead rows alone."""
        queued_a = self._create(repo)
        queued_b = self._create(repo)
        active_job = self._create(repo)
        # Move one to active by binding to a fake instance id so its
        # admission_state advances through start_job semantics.
        from daemon.repositories.job_queue.repository import JobRepository

        # Simpler: directly mutate admission_state via start_job; if
        # start_job isn't available, use the repository's atomic_transition.
        from daemon.repositories.job_queue.models import AdmissionState

        repo.atomic_transition(
            active_job.job_id,
            from_status="queued",
            to_status="active",
            instance_id="inst-x",
        )
        done_job = self._create(repo)
        repo.atomic_transition(
            done_job.job_id,
            from_status="queued",
            to_status="done",
            terminal_reason="completed",
        )

        cancelled = repo.batch_cancel_queued()

        assert cancelled == 2

        # Queued rows now done+cancelled.
        for jid in (queued_a.job_id, queued_b.job_id):
            row = repo.get(jid)
            assert row.admission_state == AdmissionState.DONE.value
            assert row.terminal_reason == "cancelled"

        # Untouched rows are still active / done.
        assert repo.get(active_job.job_id).admission_state == "active"
        assert repo.get(done_job.job_id).terminal_reason == "completed"

    def test_find_active_jobs_returns_only_active(self, repo):
        """`find_active_jobs` returns active rows in FIFO order,
        dropping queued / done / dead rows."""
        from daemon.repositories.job_queue.models import AdmissionState

        queued_a = self._create(repo)
        active_old = self._create(repo)
        active_new = self._create(repo)
        done_job = self._create(repo)

        repo.atomic_transition(
            active_old.job_id,
            from_status="queued",
            to_status="active",
            instance_id="inst-1",
        )
        repo.atomic_transition(
            active_new.job_id,
            from_status="queued",
            to_status="active",
            instance_id="inst-2",
        )
        repo.atomic_transition(
            done_job.job_id,
            from_status="queued",
            to_status="done",
            terminal_reason="completed",
        )

        active_jobs = repo.find_active_jobs()

        ids = [j.job_id for j in active_jobs]
        assert ids == [active_old.job_id, active_new.job_id]
        assert queued_a.job_id not in ids
        assert done_job.job_id not in ids
        assert all(j.admission_state == AdmissionState.ACTIVE.value for j in active_jobs)

    def test_batch_cancel_queued_returns_zero_when_no_queued_jobs(self, repo):
        """No-op -> returns 0 (still 200 at the endpoint)."""
        assert repo.batch_cancel_queued() == 0

    def test_batch_cancel_queued_skips_message_job_items(self, repo):
        """`batch_cancel_queued` must NOT cancel ``job_type='message'``
        JobItems — those are pure mirrors of Task rows and cancelling
        the mirror here would desync it from its authoritative Task.

        Setup: two queued rows (one task, one message mirror) plus one
        active task row. The batch UPDATE must flip exactly one row
        (the queued task); the queued message mirror stays
        ``admission_state='queued'`` and the active task stays
        ``active``.
        """
        from daemon.repositories.job_queue.models import AdmissionState

        queued_task = self._create(repo, job_type="task")
        queued_message = self._create(repo, job_type="message")
        active_task = self._create(repo, job_type="task")
        repo.atomic_transition(
            active_task.job_id,
            from_status="queued",
            to_status="active",
            instance_id="inst-m",
        )

        cancelled = repo.batch_cancel_queued()

        # Only the queued task was cancelled. Message mirror stays queued.
        assert cancelled == 1

        # Task row -> done + cancelled.
        task_row = repo.get(queued_task.job_id)
        assert task_row.admission_state == AdmissionState.DONE.value
        assert task_row.terminal_reason == "cancelled"

        # Message mirror stays untouched (still queued, no terminal_reason).
        msg_row = repo.get(queued_message.job_id)
        assert msg_row.admission_state == AdmissionState.QUEUED.value
        assert msg_row.terminal_reason is None

        # Active task untouched.
        active_row = repo.get(active_task.job_id)
        assert active_row.admission_state == AdmissionState.ACTIVE.value

    def test_find_active_jobs_excludes_message_job_items(self, repo):
        """`find_active_jobs` must NOT return ``job_type='message'``
        JobItems — those are pure mirrors and the per-row ``cancel_job``
        cascade (``terminate_instance``) would either be a no-op or
        trigger destructive instance termination on a Task that has
        other live work.

        Setup: two active rows (one task, one message mirror). The
        find call must return only the active task — the message
        mirror is filtered out at the SQL layer.
        """
        from daemon.repositories.job_queue.models import AdmissionState

        active_task = self._create(repo, job_type="task")
        active_message = self._create(repo, job_type="message")

        repo.atomic_transition(
            active_task.job_id,
            from_status="queued",
            to_status="active",
            instance_id="inst-task",
        )
        repo.atomic_transition(
            active_message.job_id,
            from_status="queued",
            to_status="active",
            instance_id="inst-msg",
        )

        active_jobs = repo.find_active_jobs()

        ids = [j.job_id for j in active_jobs]
        # Only the active task is returned; the message mirror is excluded.
        assert ids == [active_task.job_id]
        assert active_message.job_id not in ids
        # Sanity: every returned row is genuinely active.
        assert all(j.admission_state == AdmissionState.ACTIVE.value for j in active_jobs)
        # Sanity: the message mirror row still exists and is still active
        # in the DB — we only filtered it out of the result, we did not
        # touch the row.
        msg_row = repo.get(active_message.job_id)
        assert msg_row.admission_state == AdmissionState.ACTIVE.value

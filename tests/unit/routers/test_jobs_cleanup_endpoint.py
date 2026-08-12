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

    def test_job_cleanup_response_schema_has_six_counters(self):
        """The response schema must carry exactly the six contract fields.

        Counter contract (Phase 2 — System Cleanup reaper; Phase 4 —
        bad-state Task reconciliation; Phase 5 — instance-level reaper):

          * ``cancelled_queued``     — batch-UPDATE PENDING rows
          * ``cancelled_active``     — per-row cancel cascade (PROCESSING)
          * ``orphaned_reaped``      — force-finalized ghost active rows
                                       (Phase 2 of the System Cleanup
                                       button — instance gone but
                                       ``admission_state='active'``).
          * ``reconciled_bad_state`` — bad-state Tasks (paused/pending
                                       with terminal JobItem) reconciled
                                       to CANCELLED (Phase 4).
          * ``terminated_instances`` — non-terminal instances with no
                                       live work transitioned to
                                       TERMINATED by the Bucket 5
                                       instance-level reaper.
          * ``total_processed``      — sum of the first two only.

        ``orphaned_reaped``, ``reconciled_bad_state`` and
        ``terminated_instances`` are kept OUT of the ``total_processed``
        invariant so existing operator dashboards / tests that sum
        only the first two counters continue to reconcile.
        """
        from daemon.routers.schemas import JobCleanupResponse

        fields = set(JobCleanupResponse.model_fields.keys())
        assert fields == {
            "cancelled_queued",
            "cancelled_active",
            "orphaned_reaped",
            "reconciled_bad_state",
            "terminated_instances",
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
            "terminated_instances": 0,
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
            "terminated_instances": 0,
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
            "terminated_instances": 0,
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
            "terminated_instances": 0,
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
            "terminated_instances": 0,
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
            "terminated_instances": 0,
            "total_processed": 2,
        }

    # ----------------------------------------------------------
    # Phase 5 — Bucket 5: instance-level reaper
    # ----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_cleanup_terminates_zombie_instances(self):
        """Non-terminal instances with no live work are terminated via
        :meth:`InstanceManager.terminate_instance`.

        C2 (2026-08-12) — Bucket 5 was previously driving a raw
        ``transition_status_if`` UPDATE that skipped 15+ cleanup
        steps. The fix is to call ``terminate_instance`` directly,
        which cascades to children, releases ``job_locks``, deletes
        Tasks, closes MCP connections, emits lifecycle events, etc.
        ``terminate_instance`` is idempotent so a TOCTOU race vs.
        another terminal-write path is safe.

        C3 (2026-08-12) — ``_has_live_work`` re-checks the instance
        immediately before termination. The mocks below set both
        JobItem and Task probes to empty so the re-check returns
        ``False`` and the termination proceeds for every zombie.

        Setup:

        * No queued / active jobs to cancel (queued batch returns 0,
          ``find_active_jobs`` is empty).
        * ``find_zombie_instances`` returns two ids.
        * ``manager.terminate_instance`` is an ``AsyncMock`` returning
          ``None`` (success) so the ``await`` resolves to a no-op.

        Asserts:

        * ``result["terminated_instances"] == 2`` — both zombies reaped.
        * ``result["total_processed"] == 0`` — Bucket 5 is excluded from
          the invariant (same treatment as ``orphaned_reaped`` and
          ``reconciled_bad_state``).
        * ``terminate_instance`` was awaited once per zombie id.
        """
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService.__new__(JobQueueService)
        repo = MagicMock()
        repo.batch_cancel_queued = MagicMock(return_value=0)
        repo.find_active_jobs = MagicMock(return_value=[])
        # C3: live-Work re-check uses ``find_jobs_by_instance``. A bare
        # MagicMock would return a truthy MagicMock by default, so we
        # pin the probe to ``[]`` so the re-check returns ``False``
        # and the termination proceeds.
        repo.find_jobs_by_instance = MagicMock(return_value=[])
        service._repository = repo

        # Wire up a stubbed ``_instance_manager`` so the Bucket 5
        # reaper sees a real instance repo to call.
        instance_repo = MagicMock()
        instance_repo.find_zombie_instances = MagicMock(
            return_value=["zombie-A", "zombie-B"]
        )
        manager = MagicMock()
        manager._instance_repository = instance_repo
        # C2: ``terminate_instance`` is async — must be AsyncMock or
        # ``await`` will raise TypeError. Returning ``None`` mirrors
        # the production code path where ``terminate_instance``
        # returns ``True`` (we don't read the return here — the loop
        # counts every awaited call).
        manager.terminate_instance = AsyncMock(return_value=None)
        # C3: Task re-check uses ``_task_repo``. Pin the probe so a
        # bare MagicMock truthy default does not skip every zombie.
        # Bug-1 fix (2026-08-12): the reaper's ``_has_live_work``
        # method now uses a single ``has_instance_busy`` query
        # (PENDING + RUNNING + PAUSED) — the prior 2-probe
        # ``has_inflight_task`` + ``get_by_instance`` pattern is
        # gone. Mock only ``has_instance_busy``; the
        # ``get_by_instance`` attribute is no longer consulted by
        # the production code but is kept on the MagicMock so any
        # straggling call site (regressions, future code that
        # forgets to migrate) does not accidentally AttributeError.
        manager._task_repo = MagicMock()
        manager._task_repo.has_instance_busy = MagicMock(return_value=False)
        manager._task_repo.get_by_instance = MagicMock(return_value=[])
        service._instance_manager = manager

        result = await service.cleanup_non_terminal_jobs()

        # Both zombies reaped.
        assert result["terminated_instances"] == 2
        # Bucket 5 is excluded from ``total_processed``.
        assert result["total_processed"] == 0
        # The repo method was queried for the zombie set and the full
        # terminate_instance cascade was invoked for each id.
        instance_repo.find_zombie_instances.assert_called_once_with()
        assert manager.terminate_instance.await_count == 2
        awaited_ids = [
            call.args[0]
            for call in manager.terminate_instance.await_args_list
        ]
        assert awaited_ids == ["zombie-A", "zombie-B"]

    @pytest.mark.asyncio
    async def test_cleanup_skips_zombies_whose_transition_loses_race(self):
        """C3 (2026-08-12) re-frame: a zombie that has *acquired live
        work between the scan and the termination* must be skipped, not
        terminated.

        The pre-C2 implementation tested the ``transition_status_if``
        returning ``None`` (race-loss) case. With the C2 fix,
        ``terminate_instance`` is idempotent on already-terminal
        instances and would short-circuit cheaply instead of being
        "skipped". The C3 ``_has_live_work`` re-check is the new skip
        path: if a concurrent dispatch created a new JobItem or Task
        for an instance between the scan and the termination, that
        instance is no longer a zombie and must NOT be terminated.

        Setup:

        * Three zombies returned by the scan.
        * ``_has_live_work`` returns ``True`` only for the middle id
          (``"loser"``) — the one a concurrent dispatcher snuck a
          new JobItem onto. Returns ``False`` for the other two.

        Asserts:

        * ``result["terminated_instances"] == 2`` — the live-work
          instance was skipped, the other two were terminated.
        * ``terminate_instance`` was awaited exactly twice (for the
          two non-live-work zombies) — the live-work instance never
          reached the cascade.
        """
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService.__new__(JobQueueService)
        repo = MagicMock()
        repo.batch_cancel_queued = MagicMock(return_value=0)
        repo.find_active_jobs = MagicMock(return_value=[])
        service._repository = repo

        instance_repo = MagicMock()
        instance_repo.find_zombie_instances = MagicMock(
            return_value=["winner", "loser", "winner2"]
        )
        manager = MagicMock()
        manager._instance_repository = instance_repo
        manager.terminate_instance = AsyncMock(return_value=None)
        manager._task_repo = MagicMock()
        # Bug-1 fix (2026-08-12): the reaper now uses
        # ``has_instance_busy`` (PENDING + RUNNING + PAUSED) — a
        # single canonical query — instead of the prior 2-probe
        # ``has_inflight_task`` + ``get_by_instance`` pattern. The
        # ``_has_live_work`` method below is pinned to override the
        # production single-query implementation, so the
        # ``has_instance_busy`` mock attribute on the manager is
        # NOT consulted by this test — but we set it for parity
        # with the rest of the test class.
        manager._task_repo.has_instance_busy = MagicMock(return_value=False)
        manager._task_repo.get_by_instance = MagicMock(return_value=[])
        service._instance_manager = manager

        # C3 re-check: ``_has_live_work`` returns ``True`` only for the
        # middle id — simulating a concurrent dispatch that landed a
        # new live JobItem / Task on that instance between the scan
        # and the per-row termination.
        live_work_map = {"loser": True, "winner": False, "winner2": False}
        service._has_live_work = lambda zid: live_work_map.get(zid, False)

        result = await service.cleanup_non_terminal_jobs()

        # The ``loser`` row dropped out of the count (TOCTOU re-check
        # caught it).
        assert result["terminated_instances"] == 2
        # ``terminate_instance`` was awaited exactly twice — for
        # ``winner`` and ``winner2``. ``loser`` never reached the
        # cascade because the C3 re-check returned ``True``.
        assert manager.terminate_instance.await_count == 2
        awaited_ids = [
            call.args[0]
            for call in manager.terminate_instance.await_args_list
        ]
        assert awaited_ids == ["winner", "winner2"]

    @pytest.mark.asyncio
    async def test_cleanup_continues_when_terminate_zombie_raises(self):
        """An exception raised by :meth:`InstanceManager.terminate_instance`
        for a single zombie must NOT abort the loop. The next zombie
        still runs and the final count reflects only the successful
        terminations.

        C2 (2026-08-12) — re-framed from
        ``transition_status_if``-raises to
        ``terminate_instance``-raises. The exception site is now the
        full cascade rather than the raw UPDATE — the loop's
        best-effort ``try/except`` still catches the exception and
        ``continue``s to the next zombie.

        Setup:

        * Two zombies returned by the scan.
        * ``manager.terminate_instance`` (AsyncMock) raises for
          ``zombie-A`` and returns ``None`` for ``zombie-B``.
        * ``_has_live_work`` is pinned to ``False`` so both reach the
          cascade (skipping the C3 path is out of scope here).
        """
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService.__new__(JobQueueService)
        repo = MagicMock()
        repo.batch_cancel_queued = MagicMock(return_value=0)
        repo.find_active_jobs = MagicMock(return_value=[])
        service._repository = repo

        instance_repo = MagicMock()
        instance_repo.find_zombie_instances = MagicMock(
            return_value=["zombie-A", "zombie-B"]
        )
        manager = MagicMock()
        manager._instance_repository = instance_repo
        # C2: ``terminate_instance`` raises for ``zombie-A`` and
        # succeeds for ``zombie-B``. ``side_effect`` consumes the
        # list positionally across the two awaits.
        manager.terminate_instance = AsyncMock(
            side_effect=[
                RuntimeError("simulated terminate cascade failure"),
                None,
            ]
        )
        # C3: pin the reaper's ``_has_live_work`` Task probe so the
        # re-check returns ``False`` (so we exercise the
        # raise-vs-succeed branch instead of the skip branch —
        # which has its own test). Bug-1 fix (2026-08-12): the
        # probe is now ``has_instance_busy`` (PENDING + RUNNING +
        # PAUSED) — a single canonical query — instead of the
        # prior 2-probe ``has_inflight_task`` + ``get_by_instance``
        # pattern. ``get_by_instance`` is no longer consulted by
        # the production code but is kept on the mock for parity.
        manager._task_repo = MagicMock()
        manager._task_repo.has_instance_busy = MagicMock(return_value=False)
        manager._task_repo.get_by_instance = MagicMock(return_value=[])
        service._instance_manager = manager
        # Pin C3 re-check to False so both reach the cascade.
        service._has_live_work = lambda zid: False

        result = await service.cleanup_non_terminal_jobs()

        # ``zombie-A`` raised and was skipped; ``zombie-B`` succeeded.
        assert result["terminated_instances"] == 1
        # Both zombies reached the cascade — the failure did not abort
        # the loop.
        assert manager.terminate_instance.await_count == 2

    @pytest.mark.asyncio
    async def test_cleanup_swallows_find_zombie_instances_failure(self):
        """Outer-try: a failure in ``find_zombie_instances`` itself
        must be swallowed — the bucket must never break the main
        cleanup counters.

        Companion to :meth:`test_cleanup_continues_when_terminate_zombie_raises`,
        which covers the inner-per-zombie exception path. Together
        they pin both try/except layers.
        """
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService.__new__(JobQueueService)
        repo = MagicMock()
        repo.batch_cancel_queued = MagicMock(return_value=0)
        repo.find_active_jobs = MagicMock(return_value=[])
        service._repository = repo

        instance_repo = MagicMock()
        instance_repo.find_zombie_instances = MagicMock(
            side_effect=RuntimeError("simulated zombie scan failure")
        )
        manager = MagicMock()
        manager._instance_repository = instance_repo
        service._instance_manager = manager

        result = await service.cleanup_non_terminal_jobs()

        # Outer-try swallowed the find failure; counter is 0.
        assert result["terminated_instances"] == 0

    @pytest.mark.asyncio
    async def test_cleanup_without_instance_manager_does_not_crash(self):
        """When ``_instance_manager`` is ``None`` (e.g. during very
        early daemon startup, or in a unit test that never wires an
        instance manager), Bucket 5 must be a no-op. The main
        counters still report so the endpoint is not broken.
        """
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService.__new__(JobQueueService)
        repo = MagicMock()
        repo.batch_cancel_queued = MagicMock(return_value=1)
        repo.find_active_jobs = MagicMock(return_value=[])
        service._repository = repo
        # ``_instance_manager`` left unset — the bucket's outer
        # ``if self._instance_manager`` guard skips it.
        # (use a sentinel: explicitly None)
        service._instance_manager = None

        result = await service.cleanup_non_terminal_jobs()

        assert result["cancelled_queued"] == 1
        assert result["terminated_instances"] == 0
        assert result["total_processed"] == 1


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
            "terminated_instances": 0,
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
            "terminated_instances": 0,
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

    def test_cleanup_includes_terminated_instances_when_set(
        self, cleanup_client
    ):
        """``terminated_instances`` is surfaced in the response when
        the Bucket 5 instance reaper flipped non-terminal instances
        to ``TERMINATED``. ``total_processed`` stays pinned to
        ``cancelled_queued + cancelled_active`` so dashboards keyed
        off that single number do not silently jump when the
        instance reaper succeeds.
        """
        from daemon.routers.jobs_crud import get_job_queue_service

        service = self._stub_service(
            {
                "cancelled_queued": 1,
                "cancelled_active": 0,
                "orphaned_reaped": 0,
                "reconciled_bad_state": 0,
                "terminated_instances": 2,
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
            "orphaned_reaped": 0,
            "reconciled_bad_state": 0,
            "terminated_instances": 2,
            "total_processed": 1,
        }


# ---------------------------------------------------------------------------
# Group 3b — Schema invariants (Phase 5: terminated_instances)
# ---------------------------------------------------------------------------


class TestJobCleanupResponseInvariant:
    """Pin the ``validate_total_processed`` invariant in the presence
    of the new ``terminated_instances`` counter.

    The bucket is excluded from ``total_processed`` (same as
    ``orphaned_reaped`` and ``reconciled_bad_state``), so a payload
    with ``terminated_instances=5`` and ``total_processed=3`` must
    validate as long as ``cancelled_queued + cancelled_active == 3``.
    """

    def test_terminated_instances_excluded_from_total(self):
        from daemon.routers.schemas import JobCleanupResponse

        response = JobCleanupResponse(
            cancelled_queued=2,
            cancelled_active=1,
            orphaned_reaped=0,
            reconciled_bad_state=0,
            terminated_instances=5,
            total_processed=3,
        )
        # Invariant still holds: 2 + 1 == 3.
        assert response.total_processed == 3
        assert response.terminated_instances == 5

    def test_terminated_instances_invariant_fails_when_total_mismatches(self):
        """The ``validate_total_processed`` check must STILL fail when
        the caller accidentally sums ``terminated_instances`` into the
        total. Pinning this catches future refactors that try to
        re-include the new counter.
        """
        from pydantic import ValidationError

        from daemon.routers.schemas import JobCleanupResponse

        with pytest.raises(ValidationError):
            JobCleanupResponse(
                cancelled_queued=2,
                cancelled_active=1,
                terminated_instances=5,
                # Wrong: caller double-counted terminated_instances.
                total_processed=8,
            )


# ---------------------------------------------------------------------------
# Group 3c — Preflight endpoint: /jobs/cleanup/preflight
# ---------------------------------------------------------------------------


class TestCleanupPreflightEndpoint:
    """Pin the read-only preflight that surfaces both the bad-state
    Task count and the zombie-instance count so the frontend can
    render the red-glow + tooltip on the System Cleanup button.
    """

    @pytest.fixture
    def preflight_app(self):
        """FastAPI app with the management router. Each test sets
        its own ``app.state.manager`` so the test body controls
        the dependency that the endpoint resolves."""
        app = FastAPI()
        from daemon.routers.jobs_management import router as management_router

        app.include_router(management_router)
        yield app

    def test_preflight_returns_zero_counts_when_manager_lacks_repos(
        self, preflight_app
    ):
        """When the manager exposes neither ``_task_repo`` nor
        ``_instance_repository`` (e.g. very early boot), the
        preflight returns zero for both counters rather than 500.
        """
        manager = MagicMock(spec=[])  # no ``_task_repo`` / ``_instance_repository``
        preflight_app.state.manager = manager
        with TestClient(preflight_app) as client:
            response = client.get("/jobs/cleanup/preflight")

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "bad_state_count": 0,
            "zombie_instance_count": 0,
        }

    def test_preflight_returns_both_counts_when_repos_available(
        self, preflight_app
    ):
        """When both ``_task_repo`` and ``_instance_repository`` are
        available on the manager, the preflight invokes
        ``count_bad_state_tasks`` and ``count_zombie_instances``
        and surfaces both counts in the response.
        """
        task_repo = MagicMock()
        task_repo.count_bad_state_tasks = MagicMock(return_value=7)
        instance_repo = MagicMock()
        instance_repo.count_zombie_instances = MagicMock(return_value=3)

        manager = MagicMock()
        manager._task_repo = task_repo
        manager._instance_repository = instance_repo
        preflight_app.state.manager = manager

        with TestClient(preflight_app) as client:
            response = client.get("/jobs/cleanup/preflight")

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "bad_state_count": 7,
            "zombie_instance_count": 3,
        }
        # Both repos were queried — the endpoint does not short-circuit
        # after the first one.
        task_repo.count_bad_state_tasks.assert_called_once_with()
        instance_repo.count_zombie_instances.assert_called_once_with()

    def test_preflight_zombie_count_independent_of_task_repo(
        self, preflight_app
    ):
        """The ``zombie_instance_count`` path must run even when
        ``_task_repo`` is missing (or raises). Pinning the per-counter
        isolation so a task-repo outage cannot blind the operator to
        zombie instances.
        """
        instance_repo = MagicMock()
        instance_repo.count_zombie_instances = MagicMock(return_value=4)

        manager = MagicMock()
        # No ``_task_repo`` attribute.
        del manager._task_repo
        manager._instance_repository = instance_repo
        preflight_app.state.manager = manager

        with TestClient(preflight_app) as client:
            response = client.get("/jobs/cleanup/preflight")

        assert response.status_code == 200
        body = response.json()
        # Task count falls back to 0; zombie count is real.
        assert body["bad_state_count"] == 0
        assert body["zombie_instance_count"] == 4


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


# ---------------------------------------------------------------------------
# Group 4b — Repository primitives (find_zombie_instances,
#                              count_zombie_instances) — Phase 5
# ---------------------------------------------------------------------------


class TestInstanceRepositoryZombieScan:
    """Pin the two Phase 5 repository methods that drive Bucket 5 of
    the System Cleanup pipeline.

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
        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )

        return SQLModelInstanceRepository(engine)

    @pytest.fixture
    def task_repo(self, engine):
        from daemon.repositories.task.repository import TaskRepository

        return TaskRepository(engine)

    @pytest.fixture
    def job_repo(self, engine):
        from daemon.repositories.job_queue.repository import JobRepository

        return JobRepository(engine)

    def _create_instance(
        self, repo, instance_id: str = "inst-1", status: str = "running",
        project_id: str = "test-project",
    ):
        """Create an instance row directly via the SQLModel session.

        Bypasses the higher-level ``create`` path so tests can pin the
        ``status`` field without orchestrating a full lifecycle.
        """
        from sqlmodel import Session as SQLModelSession

        from daemon.repositories.instance.models import Instance

        with SQLModelSession(repo.engine) as session:
            inst = Instance(
                instance_id=instance_id,
                project_id=project_id,
                agent_id="tester",
                agent_dir="/tmp/tester",
                agent_name="Tester",
                status=status,
            )
            session.add(inst)
            session.commit()

    def _create_task(
        self, task_repo, instance_id: str, status: str = "running",
    ):
        from daemon.repositories.task.models import Task, TaskStatus

        # Map the strings to the TaskStatus enum so the test uses the
        # same surface as the production code path.
        status_enum = TaskStatus(status)
        task = Task(
            instance_id=instance_id,
            agent_id="tester",
            agent_dir="/tmp/tester",
            status=status_enum.value,
        )
        from sqlmodel import Session as SQLModelSession

        with SQLModelSession(task_repo.engine) as session:
            session.add(task)
            session.commit()

    def _create_job(
        self, job_repo, instance_id: str,
        admission_state: str = "active",
    ):
        from sqlmodel import Session as SQLModelSession

        from daemon.repositories.job_queue.models import JobItem

        with SQLModelSession(job_repo.engine) as session:
            job = JobItem(
                agent_id="tester",
                agent_dir="/tmp/tester",
                message="hello",
                source="api",
                project_id="test-project",
                priority=5,
                instance_id=instance_id,
                admission_state=admission_state,
            )
            session.add(job)
            session.commit()

    def test_find_zombie_instances_returns_empty_when_none(
        self, repo, task_repo, job_repo
    ):
        """No instances -> empty list. Sanity baseline."""
        assert repo.find_zombie_instances() == []

    def test_find_zombie_instances_excludes_terminal_statuses(
        self, repo, task_repo, job_repo
    ):
        """Already-terminal instances (any of completed / error /
        terminated / failed) MUST NOT be in the result. The
        reaper only acts on the non-terminal set."""
        for terminal in ("completed", "error", "terminated", "failed"):
            self._create_instance(
                repo,
                instance_id=f"inst-{terminal}",
                status=terminal,
            )
        # No live JobItem or Task is needed for terminal instances —
        # the status filter alone is sufficient.

        result = repo.find_zombie_instances()

        assert result == []

    def test_find_zombie_instances_excludes_instances_with_active_jobitem(
        self, repo, task_repo, job_repo
    ):
        """An instance with a live (queued/active) JobItem is NOT a
        zombie — the JobItem is doing real work."""
        self._create_instance(
            repo, instance_id="inst-busy", status="running"
        )
        self._create_job(job_repo, instance_id="inst-busy",
                         admission_state="active")

        result = repo.find_zombie_instances()

        assert result == []

    def test_find_zombie_instances_excludes_instances_with_live_task(
        self, repo, task_repo, job_repo
    ):
        """An instance with a live (pending/running/paused) Task is
        NOT a zombie — the Task is doing real work."""
        self._create_instance(
            repo, instance_id="inst-task-busy", status="running"
        )
        self._create_task(task_repo, instance_id="inst-task-busy",
                          status="running")

        result = repo.find_zombie_instances()

        assert result == []

    def test_find_zombie_instances_returns_only_true_zombies(
        self, repo, task_repo, job_repo
    ):
        """A true zombie is: non-terminal status, no live JobItem,
        no live Task. Returned instances must match the SQL predicate
        exactly."""
        # True zombies — should be returned.
        self._create_instance(
            repo, instance_id="zombie-A", status="running"
        )
        self._create_instance(
            repo, instance_id="zombie-B", status="paused"
        )
        # Live instance — must NOT be returned.
        self._create_instance(
            repo, instance_id="alive", status="running"
        )
        self._create_job(job_repo, instance_id="alive",
                         admission_state="queued")
        # Terminal instance — must NOT be returned.
        self._create_instance(
            repo, instance_id="done", status="completed"
        )

        result = repo.find_zombie_instances()

        assert sorted(result) == ["zombie-A", "zombie-B"]

    def test_count_zombie_instances_matches_find(
        self, repo, task_repo, job_repo
    ):
        """``count_zombie_instances`` must agree with
        ``len(find_zombie_instances())`` for the same DB state."""
        self._create_instance(
            repo, instance_id="z1", status="running"
        )
        self._create_instance(
            repo, instance_id="z2", status="paused"
        )
        self._create_instance(
            repo, instance_id="terminal", status="completed"
        )

        find_result = repo.find_zombie_instances()
        count_result = repo.count_zombie_instances()

        assert len(find_result) == count_result == 2

    def test_find_zombie_instances_with_null_jobitem_instance_id(
        self, repo, task_repo, job_repo
    ):
        """C1 (2026-08-12) regression test — NULL ``instance_id`` on
        ``job_queue_items`` MUST NOT poison the zombie scan.

        Before the ``NOT EXISTS`` fix, the anti-join was expressed
        as ``i.instance_id NOT IN (SELECT DISTINCT jqi.instance_id
        ...)``. SQL three-valued logic evaluates ``x NOT IN (..., NULL)``
        to ``UNKNOWN`` for every ``x``, so a single ``job_queue_items``
        row with ``instance_id IS NULL`` silently caused the entire
        scan to return ``[]`` — Bucket 5 matched nothing in production.

        After the fix (``NOT EXISTS (SELECT 1 ... WHERE jqi.instance_id
        = i.instance_id ...)``), the outer row is excluded only by the
        EXISTS-correlated equality match; NULL rows on the inner side
        simply don't match any outer instance_id and the scan returns
        the true zombies.

        Setup:

        * A true zombie instance with no live work.
        * A ``job_queue_items`` row with ``instance_id=None`` (this is
          the row whose NULL value was poisoning the scan pre-fix).

        Asserts:

        * ``find_zombie_instances`` returns the true zombie — proving
          the NULL row did NOT poison the scan.

        Also verified against ``count_zombie_instances`` so the
        count variant (same NOT EXISTS template) is covered.
        """
        # True zombie — should be found even with a NULL-instance_id
        # JobItem co-existing in the table.
        self._create_instance(
            repo, instance_id="zombie-real", status="running"
        )
        # A JobItem with ``instance_id=None`` — must not poison the
        # scan. ``_create_job`` accepts the ``instance_id`` kwarg,
        # which forwards straight to the JobItem column.
        self._create_job(job_repo, instance_id=None,
                         admission_state="queued")

        result = repo.find_zombie_instances()

        assert result == ["zombie-real"]
        # Count variant is driven by the same SQL template via
        # ``_build_zombie_scan_sql(count_only=True)`` — must agree.
        assert repo.count_zombie_instances() == 1

    def test_find_zombie_instances_skips_parents_with_live_children(
        self, repo, task_repo, job_repo
    ):
        """W1 (2026-08-12) — a non-terminal parent with a non-terminal
        child instance MUST NOT be returned: terminating the parent
        would orphan the still-executing child.

        The third ``NOT EXISTS`` predicate in
        :meth:`SQLModelInstanceRepository._build_zombie_scan_sql`
        walks ``instances child`` for ``child.parent_id = i.instance_id``
        and excludes any parent that has at least one non-terminal
        child row.

        Setup:

        * A parent with a live (running) child — must NOT appear in
          the result (parent-of-live is excluded by the W1 guard).
        * The live child itself — IS a non-terminal instance with
          no live JobItem/Task of its own, so it IS a zombie
          (correct — the scan evaluates each instance independently
          against its own children; a child is not excluded merely
          because its parent is alive).
        * A parent whose only child is terminal — IS a zombie (the
          W1 guard only excludes parents with non-terminal children).
        * A childless parent — IS a zombie (no child rows means the
          NOT EXISTS is naturally true).
        """
        # Parent with a live child — must NOT be returned (W1).
        self._create_instance(
            repo, instance_id="parent-of-live", status="running"
        )
        self._create_instance(
            repo, instance_id="child-of-live", status="running"
        )
        # Tie parent_id — direct on the row, since the instance model
        # carries ``parent_id`` natively (see Instance.parent_id).
        from sqlmodel import Session as SQLModelSession

        from daemon.repositories.instance.models import Instance

        with SQLModelSession(repo.engine) as session:
            child = session.get(Instance, "child-of-live")
            child.parent_id = "parent-of-live"
            session.add(child)
            session.commit()

        # Parent whose only child is terminal — IS a zombie.
        self._create_instance(
            repo, instance_id="parent-terminal-child",
            status="waiting_children",
        )
        self._create_instance(
            repo, instance_id="child-terminal", status="completed"
        )
        with SQLModelSession(repo.engine) as session:
            child = session.get(Instance, "child-terminal")
            child.parent_id = "parent-terminal-child"
            session.add(child)
            session.commit()

        # Childless parent — IS a zombie.
        self._create_instance(
            repo, instance_id="parent-childless", status="paused"
        )

        result = repo.find_zombie_instances()

        # The W1 guard excludes ``parent-of-live`` (its child is live).
        # The other three rows are all valid zombies: ``child-of-live``
        # is a non-terminal instance with no live work of its own,
        # ``parent-terminal-child`` has only a terminal child (W1
        # does not exclude it), and ``parent-childless`` has no
        # children at all.
        assert sorted(result) == [
            "child-of-live",
            "parent-childless",
            "parent-terminal-child",
        ]

"""Job Queue Management API endpoints."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from daemon.services.job_queue_service import JobQueueService
from daemon.services.dead_letter_service import DeadLetterService
from daemon.services.work_status import (
    _derive_legacy_status,
    is_terminal as _is_terminal_canonical,
)
from daemon.repositories.job_queue.models import AdmissionState
from .schemas import (
    JobResponse,
    JobNotFoundResponse,
    JobCleanupResponse,
    CleanupPreflightResponse,
    DeferHolderForceCompleteResponse,
    DeferHolderResendResponse,
)
from .jobs_crud import (
    get_job_queue_service,
    get_dead_letter_svc,
    TERMINAL_STATUSES,
    _job_to_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state."""
    return request.app.state.manager


async def _resolve_job_status(service: JobQueueService, job_id: str) -> str | None:
    """Return the canonical status of ``job_id`` via the resolver, or ``None``.

    Phase 1 (Job as Queue Proxy): the canonical ``status`` for a job
    is sourced from the resolver (which reads the joined Instance)
    rather than the JobItem mirror. This helper centralises that
    read so the management endpoints' terminal-state gates operate
    on instance-authoritative state.

    Returns ``None`` if the resolver is not wired (older test doubles
    that never call ``set_work_resolver``) — callers treat that as
    "resolver not available, fall back to JobItem mirror" and check
    the legacy status vocabulary directly (inline string literals).
    This preserves the pre-Phase-1 behaviour for partial wirings and
    for tests that mock ``get_work`` to return ``None``.
    """
    try:
        work_record = await service.get_work(job_id)
    except Exception as exc:  # noqa: BLE001 — defensive broad catch
        logger.warning(
            "_resolve_job_status: resolver call failed for %s: %s", job_id, exc
        )
        return None
    if work_record is None:
        return None
    return work_record.status


# ==================== Management Endpoints ====================


@router.delete(
    "/{job_id}",
    responses={
        200: {"description": "Job cancelled or soft-deleted successfully"},
        400: {"description": "Job already deleted"},
        404: {"model": JobNotFoundResponse, "description": "Job not found"},
    },
)
async def delete_job(
    job_id: str,
    request: Request,
    service: JobQueueService = Depends(get_job_queue_service),
):
    """Delete (cancel or soft-delete) a job.

    - If job is PENDING or PROCESSING → cancel (existing behavior)
    - If job is in terminal state (completed, failed, cancelled, dead_letter) → soft delete
    - If job is already deleted → return 400

    Returns:
        200 if cancelled/soft-deleted successfully
        400 if job is already deleted
        404 if job not found
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    job = await service.get_job(job_id)

    if job is None:
        # Virtual-job write facade (Part B, revive-fix follow-up
        # 2026-07-01): a Task-backed work_id has no JobItem row, so
        # ``get_job`` misses and the legacy path 404'd (the UI cancel
        # button hit this on a message-turn task). Fall through the
        # resolver: if the work_id resolves to a Task, cancel it
        # directly; only a genuine miss raises 404.
        work = await service.get_work(job_id)
        if work is not None and work.kind != "job":
            from daemon.services.work_status import is_terminal
            if is_terminal(work.status):
                # Already terminal — nothing to cancel (tasks don't
                # soft-delete like JobItems). Report success; the FE
                # just needs the card gone.
                resp = work.to_dict()
                resp["message"] = "Work already in terminal state"
                return resp
            cancelled = await service.cancel_task_by_work_id(job_id)
            if not cancelled:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "Failed to cancel work",
                        "message": "Task is not in a cancellable state",
                        "work_id": job_id,
                    },
                )
            updated_work = await service.get_work(job_id)
            resp = (updated_work or work).to_dict()
            resp["message"] = "Work cancelled successfully"
            return resp
        raise HTTPException(
            status_code=404,
            detail=JobNotFoundResponse(
                error="Job not found",
                job_id=job_id
            ).model_dump()
        )

    # Check if already deleted
    if job.deleted_at is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Job already deleted",
                "message": "This job has already been soft-deleted",
                "job_id": job_id,
            }
        )

    # Phase 1 (Job as Queue Proxy): terminal check uses the
    # instance-authoritative status surfaced by the resolver (see
    # ``work_resolver._job_to_record``). Fall back to the
    # JobItem mirror when the resolver isn't wired (legacy / partial-wiring
    # test doubles) so the endpoint stays correct in those paths.
    # Phase 4: ``admission_state`` is the sole authority — the
    # ``status`` column is frozen at INSERT default and no longer
    # reflects lifecycle state.
    canonical_status = await _resolve_job_status(service, job_id)
    is_terminal = (
        _is_terminal_canonical(canonical_status)
        if canonical_status is not None
        else job.admission_state in {AdmissionState.DONE.value, AdmissionState.DEAD.value}
    )

    # Handle based on status
    if is_terminal:
        # Terminal state → soft delete
        updated_job = await service.soft_delete_job(job_id)
        if updated_job is None:
            raise HTTPException(
                status_code=500,
                detail={"error": "Failed to soft-delete job"}
            )
        # Resolver-aware response: re-resolve so the soft-deleted
        # row's execution state is sourced from the (now terminal)
        # Instance rather than the JobItem mirror.
        updated_work = await service.get_work(job_id)
        return _job_to_response(
            updated_job,
            work_record=updated_work,
            message="Job soft-deleted successfully"
        )
    else:
        # PENDING or PROCESSING → cancel
        success = await service.cancel_job(job_id)
        if not success:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "Failed to cancel job",
                    "message": f"Could not cancel job in admission_state: {job.admission_state}",
                }
            )
        updated_job = await service.get_job(job_id)
        updated_work = await service.get_work(job_id)
        return _job_to_response(
            updated_job,
            work_record=updated_work,
            message="Job cancelled successfully"
        )


@router.post(
    "/{job_id}/cancel",
    responses={
        200: {"description": "Job cancelled successfully"},
        400: {"description": "Job cannot be cancelled"},
        404: {"model": JobNotFoundResponse, "description": "Job not found"},
    },
)
async def cancel_job_endpoint(
    job_id: str,
    request: Request,
    service: JobQueueService = Depends(get_job_queue_service),
):
    """Cancel a pending or processing job.

    Explicit cancel endpoint for API consumers who want clear cancel semantics.

    Returns:
        200 if cancelled successfully
        400 if job is already in a terminal state or deleted
        404 if job not found
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    job = await service.get_job(job_id)

    if job is None:
        # Virtual-job write facade (Part B, revive-fix follow-up
        # 2026-07-01): a Task-backed work_id has no JobItem row —
        # fall through the resolver and cancel the Task directly so
        # the explicit cancel endpoint matches the DELETE semantics.
        work = await service.get_work(job_id)
        if work is not None and work.kind != "job":
            from daemon.services.work_status import is_terminal
            if is_terminal(work.status):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "Work cannot be cancelled",
                        "message": f"Work is already in terminal state: {work.status}",
                        "current_status": work.status,
                    },
                )
            cancelled = await service.cancel_task_by_work_id(job_id)
            if not cancelled:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "Failed to cancel work",
                        "message": "Task is not in a cancellable state",
                        "work_id": job_id,
                    },
                )
            updated_work = await service.get_work(job_id)
            resp = (updated_work or work).to_dict()
            resp["message"] = "Work cancelled successfully"
            return resp
        raise HTTPException(
            status_code=404,
            detail=JobNotFoundResponse(
                error="Job not found",
                job_id=job_id
            ).model_dump()
        )

    # Check if already deleted
    if job.deleted_at is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Job cannot be cancelled",
                "message": "This job has already been soft-deleted",
                "job_id": job_id,
            }
        )

    # Phase 1 (Job as Queue Proxy): terminal check uses the
    # instance-authoritative status surfaced by the resolver. Fall
    # back to the JobItem mirror when the resolver isn't wired so
    # the legacy path stays correct.
    # Phase 4: ``admission_state`` is the sole authority — the
    # ``status`` column is frozen at INSERT default.
    canonical_status = await _resolve_job_status(service, job_id)
    is_terminal = (
        _is_terminal_canonical(canonical_status)
        if canonical_status is not None
        else job.admission_state in {AdmissionState.DONE.value, AdmissionState.DEAD.value}
    )

    # Check if job is in a cancellable state
    if is_terminal:
        # Use the JobItem mirror value as the user-visible
        # ``current_status`` in the error detail. Once Phase 4
        # stops writing the mirror, this field falls back to the
        # canonical vocabulary ("completed" / "failed" / …) which
        # matches the mirror 1:1 for the terminal set.
        user_status = canonical_status or job.admission_state
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Job cannot be cancelled",
                "message": f"Job is already in terminal state: {user_status}",
                "current_status": user_status,
            }
        )

    # Cancel the job
    success = await service.cancel_job(job_id)

    if not success:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Failed to cancel job",
                "message": f"Could not cancel job in admission_state: {job.admission_state}",
            }
        )

    updated_job = await service.get_job(job_id)
    updated_work = await service.get_work(job_id)
    return _job_to_response(
        updated_job,
        work_record=updated_work,
        message="Job cancelled successfully"
    )


@router.post(
    "/{job_id}/restore",
    responses={
        200: {"description": "Job restored successfully"},
        400: {"description": "Job cannot be restored (not deleted or terminal)"},
        404: {"model": JobNotFoundResponse, "description": "Job not found"},
    },
)
async def restore_job_endpoint(
    job_id: str,
    request: Request,
    service: JobQueueService = Depends(get_job_queue_service),
):
    """Restore a soft-deleted job.

    Returns:
        200 with restored job
        400 if job is not deleted or is in terminal state
        404 if job not found
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    job = await service.get_job(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail=JobNotFoundResponse(
                error="Job not found",
                job_id=job_id
            ).model_dump()
        )

    # Check if job was deleted
    if job.deleted_at is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Job cannot be restored",
                "message": "This job has not been soft-deleted",
                "job_id": job_id,
            }
        )

    # Phase 1 (Job as Queue Proxy): terminal check uses the
    # instance-authoritative status surfaced by the resolver. Fall
    # back to the JobItem mirror when the resolver isn't wired.
    # Phase 4: ``admission_state`` is the sole authority.
    canonical_status = await _resolve_job_status(service, job_id)
    is_terminal = (
        _is_terminal_canonical(canonical_status)
        if canonical_status is not None
        else job.admission_state in {AdmissionState.DONE.value, AdmissionState.DEAD.value}
    )

    # Check if job is in a terminal state (restore not allowed for terminal jobs)
    if is_terminal:
        user_status = canonical_status or job.admission_state
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Job cannot be restored",
                "message": f"Cannot restore a job in terminal state: {user_status}. Retry the job instead.",
                "current_status": user_status,
            }
        )

    # Restore the job
    restored_job = await service.restore_job(job_id)

    if restored_job is None:
        raise HTTPException(
            status_code=500,
            detail={"error": "Failed to restore job"}
        )

    restored_work = await service.get_work(job_id)
    return _job_to_response(
        restored_job,
        work_record=restored_work,
        message="Job restored successfully"
    )


@router.post(
    "/{job_id}/retry",
    responses={
        200: {"description": "Job requeued for retry"},
        400: {"description": "Job cannot be retried"},
        404: {"model": JobNotFoundResponse, "description": "Job not found"},
        422: {"description": "DEAD_LETTER entry not found for job"},
    },
)
async def retry_job(
    job_id: str,
    request: Request,
    service: JobQueueService = Depends(get_job_queue_service),
    dlq_service: DeadLetterService = Depends(get_dead_letter_svc),
):
    """Retry a job by re-queuing it for processing.

    - FAILED jobs: Creates a NEW job with the same parameters (leaves original as FAILED)
    - DEAD_LETTER jobs: Resets the existing job to PENDING via DLQ replay

    Returns:
        200 with job details if retry successful
        400 if job is in neither FAILED nor DEAD_LETTER state
        404 if job not found
        422 if DEAD_LETTER entry not found for DEAD_LETTER job
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    job = await service.get_job(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail=JobNotFoundResponse(
                error="Job not found",
                job_id=job_id
            ).model_dump()
        )

    # Phase 1 (Job as Queue Proxy): the canonical status (sourced
    # from the joined Instance via the resolver) decides which
    # retry path runs. The JobItem mirror is the fallback when the
    # resolver isn't wired — both paths use the same vocabulary
    # (canonical and JobItem share the 6 non-dead_letter terminal
    # labels 1:1 today; ``dead_letter`` is JobItem-only).
    canonical_status = await _resolve_job_status(service, job_id)
    # Map resolver-canonical status to the legacy status string the
    # downstream branches still branch on. ``dead_letter`` is
    # JobItem-only (it has no Instance equivalent) so when the
    # resolver reports it, the JobItem mirror must agree — fall
    # back to ``job.admission_state`` which carries the authoritative
    # ``dead_letter`` value (the resolver surfaces the same value).
    #
    # F16 fix: route the fallback through ``_derive_legacy_status``
    # so a ``done`` job with ``terminal_reason='failed'`` reports
    # ``"failed"`` instead of the lossy map's ``"completed"``.
    #
    # M3 (mission-class, 2026-09-03) — pass ``job_type`` so the
    # per-kind dispatch in ``_derive_legacy_status`` applies: mirror
    # rows surface ``"settled"`` instead of ``"completed"`` (the
    # transport-receipt terminal). Task rows keep ``"completed"``
    # (the row IS its own mission). The downstream branches gate on
    # ``"dead_letter"`` / ``"completed"`` / ``"failed"`` /
    # ``"cancelled"`` — ``"settled"`` is a NEW terminal value that
    # does NOT match any branch and falls through to the default
    # terminal-state handling (the M3 mirror row is already terminal
    # by construction; no further action required).
    status_for_branches = canonical_status or _derive_legacy_status(
        job.admission_state,
        getattr(job, "terminal_reason", None),
        getattr(job, "job_type", None),
    )

    # Handle DEAD_LETTER jobs - replay from DLQ
    if status_for_branches == "dead_letter":
        # Find the DLQ entry for this job
        dlq_item = dlq_service.get_dlq_by_job_id(job_id)

        if dlq_item is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "DEAD_LETTER entry not found",
                    "message": f"Job {job_id} is in DEAD_LETTER state but no DLQ entry exists",
                    "job_id": job_id,
                }
            )

        # Replay from DLQ - resets job to PENDING and deletes DLQ entry atomically
        try:
            updated_job = dlq_service.replay_from_dlq(dlq_item.dlq_id)
        except Exception as e:
            logger.error(f"Failed to replay job {job_id} from DLQ: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "Failed to replay job",
                    "message": str(e),
                }
            )

        # Get position in queue
        position = None
        if updated_job.project_id:
            try:
                position = await service._get_queue_position(updated_job.job_id, updated_job.project_id)
            except Exception:
                pass

        updated_work = await service.get_work(job_id)
        return _job_to_response(
            updated_job,
            work_record=updated_work,
            position=position,
            message="Job replayed from DEAD_LETTER queue",
            dlq_reason=dlq_item.reason,
            retry_count=dlq_item.retry_count,
            moved_to_dlq_at=dlq_item.moved_to_dlq_at,
        )

    # Handle FAILED jobs - create new job with same parameters
    if status_for_branches != "failed":
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Job cannot be retried",
                "message": f"Only FAILED or DEAD_LETTER jobs can be retried. Current status: {status_for_branches}",
                "current_status": status_for_branches,
            }
        )

    # Retry the job - creates a new job with same parameters
    new_job = await service.retry_job(job_id)

    if new_job is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Failed to retry job",
                "message": "Could not create retry job",
            }
        )

    # Get position if job is pending
    position = None
    if new_job.admission_state == AdmissionState.QUEUED.value and new_job.project_id:
        try:
            position = await service._get_queue_position(new_job.job_id, new_job.project_id)
        except Exception:
            pass

    new_work = await service.get_work(new_job.job_id)
    return _job_to_response(
        new_job,
        work_record=new_work,
        position=position,
        message="Job queued for retry"
    )


@router.get(
    "/cleanup/preflight",
    response_model=CleanupPreflightResponse,
    responses={
        200: {"description": "System-wide bad-state + zombie instance counts"},
        503: {"description": "Service not initialized"},
    },
)
async def cleanup_preflight(request: Request):
    """Return system-wide bad-state Task + zombie instance counts (read-only).

    "Bad state" = a Task in ``paused``/``pending`` whose linked JobItem
    is already terminal (``done``/``dead``).

    "Zombie instance" = an ``instances`` row whose ``status`` is not in
    the terminal set (``completed``/``error``/``terminated``/``failed``)
    and which has no live JobItem (``admission_state`` in
    ``queued``/``active``) and no live Task (``status`` in
    ``pending``/``running``/``paused``). These instances survived the
    prior cleanup passes (queued batch + per-row cancel + orphan reaper
    + bad-state reconciliation) but their own status never advanced to
    a terminal value, so a Bucket 5 instance reaper is needed to
    actually terminate them.

    **WS4 mission lens (2026-09-06):** the zombie predicate no longer
    lets an instance's OWN queued defer-lane rows shield it (the
    stalled-holder self-shield exemption), so idle/stalled holders are
    reap-eligible. The response now carries the full live-vs-reap
    split: ``zombie_instance_count`` (WILL be reaped),
    ``live_instance_count`` / ``live_instance_ids`` (non-terminal
    instances cleanup will NOT touch — listed as "will remain"),
    and ``defer_blocked_count`` (pending defer-lane jobs). The defer
    count is deliberately SEPARATE from ``bad_state_count`` and is
    NOT a cleanup input: deferred messages are not cancelled by
    cleanup (constitution-era mirror protection) — the operator
    should use the defer warning's holder actions.

    All counts come from pure COUNT/SELECT queries with no writes, so
    the preflight is intentionally NOT guarded by ``is_write_paused``
    (W1 fix): the preflight MUST surface stale rows even during a
    write pause (database migration), which is precisely when
    bad-state / zombie items accumulate most because the cleanup
    endpoint itself cannot run.

    Used by the frontend to render the red-glow + tooltip on the System
    Cleanup button.
    """
    manager = _get_manager(request)
    task_repo = getattr(manager, "_task_repo", None)
    instance_repo = getattr(manager, "_instance_repository", None)

    bad_state_count = 0
    if task_repo is not None:
        bad_state_count = await asyncio.to_thread(
            task_repo.count_bad_state_tasks
        )

    zombie_instance_count = 0
    live_instance_count = 0
    live_instance_ids: list[str] = []
    if instance_repo is not None:
        zombie_instance_count = await asyncio.to_thread(
            instance_repo.count_zombie_instances
        )
        # WS4 live-vs-reap split: non-terminal instances that are NOT
        # reap-eligible will remain after cleanup — list them (bounded)
        # so the dialog can state the split explicitly.
        non_terminal_ids = await asyncio.to_thread(
            instance_repo.find_non_terminal_instance_ids
        )
        reap_ids = set(await asyncio.to_thread(
            instance_repo.find_zombie_instances
        ))
        live_ids = [iid for iid in non_terminal_ids if iid not in reap_ids]
        live_instance_count = len(live_ids)
        live_instance_ids = live_ids[:20]

    # WS4: pending defer-lane jobs, surfaced SEPARATELY from bad_state
    # (do not conflate — cleanup does not cancel them; the mirror
    # protection stays). Read-only import of the resolver's canonical
    # count SQL so the two surfaces cannot drift.
    defer_blocked_count = 0
    job_service = getattr(manager, "_job_queue_service", None)
    job_repo = getattr(job_service, "_repository", None)
    if job_repo is not None and getattr(job_repo, "engine", None) is not None:
        from daemon.services.defer_block_resolver import _DEFER_PENDING_COUNT_SQL

        def _count_defer_pending() -> int:
            with job_repo.engine.connect() as conn:
                return int(conn.execute(_DEFER_PENDING_COUNT_SQL).scalar_one())

        try:
            defer_blocked_count = await asyncio.to_thread(_count_defer_pending)
        except Exception as exc:  # noqa: BLE001 — best-effort read
            logger.warning(
                "cleanup_preflight: defer pending count failed: %s", exc
            )

    return CleanupPreflightResponse(
        bad_state_count=bad_state_count,
        zombie_instance_count=zombie_instance_count,
        live_instance_count=live_instance_count,
        live_instance_ids=live_instance_ids,
        defer_blocked_count=defer_blocked_count,
    )


@router.post(
    "/cleanup",
    response_model=JobCleanupResponse,
    responses={
        200: {"description": "Cleanup completed (counts of cancelled jobs)"},
        500: {"description": "Cleanup failed"},
        503: {"description": "Service not initialized or writes are paused"},
    },
)
async def cleanup_jobs(
    request: Request,
    service: JobQueueService = Depends(get_job_queue_service),
):
    """Cancel ALL non-terminal jobs ("system reset" for the job board).

    Splits the work into five buckets so each side uses the right
    cancellation tool:

    * **queued (PENDING)** -- batch UPDATE: ``admission_state='queued'
      -> 'done'`` with ``terminal_reason='cancelled'``. Single SQL
      statement; no lock to release, no instance to terminate.
      Constitution-era mirror protection STAYS: ``job_type='message'``
      rows are NOT cancelled here (cancelling a mirror would desync
      the JobItem from its authoritative Task).
    * **active (PROCESSING)** -- iterate and call :meth:`cancel_job` per
      row. Each call cascades to the existing instance (release lock ->
      :meth:`InstanceManager.terminate_instance` ->
      ``_finalize_terminal(Decision.NO_RETRY)`` -> ``notify_watchers``).
      Same semantics as a user-initiated single cancel -- reuse keeps
      the cleanup byte-for-byte identical to that path.
    * **orphan active** -- rows whose underlying ``instances`` row is
      already terminal (or missing) and whose ``job_locks`` row is
      gone. These jobs slipped through the natural finalize path
      (e.g. observer feedback dropped because the worker process was
      killed mid-ack) and were inflating the active-job counter
      forever. The reaper force-finalizes them with
      ``terminal_reason='cancelled'`` so a single System Cleanup click
      also drains the ghost rows. Reported as ``orphaned_reaped``
      (separate from the two main counters) so existing callers that
      only read ``total_processed`` see no behavioural change.
    * **bad-state Tasks** -- Tasks stuck in ``paused``/``pending``
      whose linked JobItem is already terminal (``done``/``dead``).
      These are reconciled to ``CANCELLED`` via
      :meth:`TaskRepository.batch_reconcile_bad_state_tasks`. Reported
      as ``reconciled_bad_state`` (excluded from ``total_processed``,
      same as ``orphaned_reaped``) because they are Task rows, not
      JobItem rows.
    * **instance reaper (Bucket 5)** -- non-terminal ``instances``
      rows with no live work. WS4 MISSION LENS (2026-09-06): an
      instance's OWN queued defer-lane rows no longer shield it (the
      stalled-holder self-shield exemption), so idle/stalled holders
      (the WS2 ``stalled`` mirrors-only class) are reap-eligible.
      LIVE missions are NEVER bulk-terminated: a running/paused Task,
      an ACTIVE JobItem on any lane, a queued non-defer job, or
      non-terminal children all still protect the instance. Each
      eligible instance is terminated via the full
      :meth:`InstanceManager.terminate_instance` cascade. Reported as
      ``terminated_instances`` (excluded from ``total_processed``,
      same as the other reaper counters) because it operates on the
      ``instances`` table, not ``job_queue_items``.

    **BY-DESIGN structural blindness (do not "fix" here):** deferred
    messages are NOT cancelled by this endpoint. The queued defer-lane
    mirrors survive by the constitution-era mirror protection
    (``batch_cancel_queued`` excludes ``job_type='message'``), and a
    mirror cancelled without its authoritative Task would desync the
    union. The defer-specific unstick lives in the holder-targeted
    actions (``POST /api/jobs/defer-holders/{id}/force-complete`` and
    ``.../resend-foreground``) and the WS3 defer watchdog. The
    preflight/dialog copy says this explicitly so an operator never
    expects the nuclear press to clear the defer lane.

    Already-terminal jobs (``admission_state IN ('done', 'dead')``) and
    already-terminal instances (``status IN ('completed', 'error',
    'terminated', 'failed')``) are left untouched.

    Returns:
        200 with :class:`JobCleanupResponse`:

        .. code-block:: json

            {"cancelled_queued": N, "cancelled_active": M,
             "orphaned_reaped": G, "reconciled_bad_state": T,
             "terminated_instances": Z, "total_processed": N + M}

    Raises:
        503: When writes are paused for migration.
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(
            status_code=503,
            detail="Writes are paused for database migration",
        )
    try:
        result = await service.cleanup_non_terminal_jobs()
    except Exception as exc:  # noqa: BLE001 -- defensive broad catch
        logger.error(
            "cleanup_jobs: cleanup_non_terminal_jobs failed: %s",
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Cleanup failed",
                "message": str(exc),
            },
        ) from exc
    return JobCleanupResponse(**result)


@router.post(
    "/defer-holders/{instance_id}/force-complete",
    response_model=DeferHolderForceCompleteResponse,
    responses={
        200: {"description": "Holder terminated (or refused by the guard — see body)"},
        404: {"description": "Holder instance not found"},
        503: {"description": "Service not initialized or writes are paused"},
    },
)
async def force_complete_defer_holder(
    instance_id: str,
    request: Request,
    service: JobQueueService = Depends(get_job_queue_service),
):
    """Force-complete a STALLED defer-gate holder (terminate it).

    WS4 holder action (2026-09-06) — the PRIMARY unstick path for the
    WS2 ``stalled`` class: a holder whose gate-busy state is
    EXCLUSIVELY its own settled message mirrors has no live work, so
    terminating the holder releases the defer gate.

    SAFETY: the mirrors-only state is RE-DERIVED at execution time via
    the canonical probe (``has_active_non_deferred_work(None,
    requester_instance_id=<holder>)``) — the FE-reported kind is never
    trusted. The probe is fail-CLOSED; a refusal surfaces as
    ``terminated=false, probe_busy=true`` (200, not an error — the
    action was evaluated and declined by the guard).

    Census discipline: no new admission-state write site — the
    termination reuses the existing ``terminate_instance`` cascade.
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(
            status_code=503, detail="Writes are paused for database migration"
        )
    try:
        result = await service.force_complete_defer_holder(instance_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — defensive broad catch
        logger.error(
            "force_complete_defer_holder(%s) failed: %s",
            instance_id[:8], exc, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "Force-complete failed", "message": str(exc)},
        ) from exc
    terminated = bool(result.get("terminated"))
    return DeferHolderForceCompleteResponse(
        instance_id=instance_id,
        terminated=terminated,
        probe_busy=bool(result.get("probe_busy")),
        message=(
            f"Stalled holder {instance_id[:8]}… force-completed."
            if terminated
            else (
                f"Refused: {instance_id[:8]}… has live non-defer work — "
                "force-complete is only safe for mirrors-only (stalled) holders."
            )
        ),
    )


@router.post(
    "/defer-holders/{instance_id}/resend-foreground",
    response_model=DeferHolderResendResponse,
    responses={
        200: {"description": "Deferred job(s) cancelled and re-sent foreground"},
        400: {"description": "No queued defer-lane jobs on the holder's books"},
        404: {"description": "Holder instance not found"},
        503: {"description": "Service not initialized or writes are paused"},
    },
)
async def resend_deferred_foreground(
    instance_id: str,
    request: Request,
    service: JobQueueService = Depends(get_job_queue_service),
):
    """Re-send a holder's queued defer-lane messages as FOREGROUND jobs.

    WS4 holder action (2026-09-06) — recovers deferred messages the
    holder is pinning: each queued defer-lane JobItem on the holder's
    own books is cancelled via the existing ``cancel_job`` path and its
    message content re-enqueued through the public
    ``enqueue_message_job`` front primitive (the same call
    ``POST /api/instances/{id}/messages`` makes). The re-enqueue is a
    NEW foreground message job — NOT a mirror mutation — and revives a
    terminal holder, so this action works even after a force-complete
    or cleanup pass.

    Census discipline: cancel + create are both registered writers;
    this path adds no new admission-state write site.
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(
            status_code=503, detail="Writes are paused for database migration"
        )
    try:
        result = await service.resend_deferred_foreground(instance_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — defensive broad catch
        logger.error(
            "resend_deferred_foreground(%s) failed: %s",
            instance_id[:8], exc, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "Re-send failed", "message": str(exc)},
        ) from exc
    if result.get("found_defer_jobs", 0) == 0:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Nothing to re-send",
                "message": (
                    f"Instance {instance_id[:8]}… has no queued defer-lane "
                    "jobs on its books"
                ),
            },
        )
    cancelled = result.get("cancelled_defer_jobs", 0)
    return DeferHolderResendResponse(
        instance_id=instance_id,
        found_defer_jobs=result.get("found_defer_jobs", 0),
        cancelled_defer_jobs=cancelled,
        resend_results=result.get("resend_results", []),
        skipped_empty_content=result.get("skipped_empty_content", 0),
        message=(
            f"Cancelled {cancelled} deferred job(s) and re-sent the "
            "message content as foreground."
        ),
    )


__all__ = ["router"]

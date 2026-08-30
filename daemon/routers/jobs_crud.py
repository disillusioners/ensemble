"""Job Queue CRUD API endpoints."""

import logging
from datetime import timezone as _tz
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from daemon.services.job_queue_service import JobQueueService, normalize_statuses
from daemon.services.dead_letter_service import DeadLetterService
from daemon.services.project_normalizer import normalize_project_id
from daemon.services.work_status import _derive_legacy_status
from daemon.repositories.job_queue.models import (
    AdmissionState,
    _VALID_LEGACY_STATUSES,
)
from daemon.constants import DEFAULT_JOB_LIST_LIMIT, MAX_JOB_LIST_LIMIT
from daemon.constants import (
    RESERVED_SOURCE_PREFIXES,
    is_reserved_source,
)
from daemon.utils import create_service_dependency, validate_agent_id
from .schemas import (
    JobCreateRequest,
    JobResponse,
    JobListResponse,
    JobValidationError,
    JobNotFoundResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Service dependencies
get_job_queue_service = create_service_dependency(JobQueueService)
get_dead_letter_svc = create_service_dependency(DeadLetterService)

# Terminal statuses for job lifecycle (legacy vocabulary — Phase 7b
# removed the JobStatus enum; these are the inline string values the
# API still emits / accepts for backward compatibility).
TERMINAL_STATUSES = frozenset({
    "completed",
    "failed",
    "cancelled",
    "dead_letter",
})


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state."""
    return request.app.state.manager


def _job_to_response(
    job,
    work_record: Any | None = None,
    position: int | None = None,
    message: str | None = None,
    dlq_reason: str | None = None,
    retry_count: int | None = None,
    moved_to_dlq_at: str | None = None,
) -> JobResponse:
    """Convert JobItem (and optional WorkRecord) to JobResponse.

    Phase 1 (Job as Queue Proxy): execution state (``status``,
    ``result_summary``, ``error_message``) is sourced from the
    resolver-supplied ``WorkRecord`` when one is provided. The
    resolver is instance-authoritative — ``status`` is read off the
    joined ``Instance`` row, not the JobItem mirror column. When
    ``work_record`` is ``None`` (legacy fallback path or batched
    callers that haven't fetched a record yet) execution state falls
    back to the JobItem columns so the response still has values to
    serialise.

    The JobItem-direct reads are kept for fields the WorkRecord
    doesn't carry — these are queue-payload (``priority``,
    ``agent_dir``, ``queue_id``, ``source``, ``job_metadata``,
    ``idempotency_key``, ``deleted_at``, ``message``) and the
    ``cancelled_at`` execution field (derivable from
    ``Instance.status == TERMINATED``, which Phase 4 will surface).
    Execution timing (``started_at`` / ``completed_at``) is sourced
    from the WorkRecord, which reads ``Instance.last_activity_at``
    (with ``Instance.created_at`` as fallback) for ``started_at``
    and ``Instance.updated_at`` for ``completed_at`` on terminal
    instances — the ``Instance`` table has carried these columns
    since the original schema, and they are the authoritative
    execution timestamps under the Job-as-Queue-Proxy model.

    Args:
        job: The ``JobItem`` row to project.
        work_record: Optional :class:`WorkRecord` from
            ``WorkResolverService.resolve_work`` — provides
            instance-authoritative execution state. ``None`` falls
            back to the JobItem mirror columns (legacy behaviour).
        position: Optional queue position (only meaningful for
            PENDING jobs; supplied by the caller).
        message: Optional status message override (defaults to
            ``job.message`` when omitted).
        dlq_reason: Optional DLQ reason (populated for DEAD_LETTER
            jobs by the caller from the matching DeadLetterItem).
        retry_count: Optional retry count (same source as
            ``dlq_reason``).
        moved_to_dlq_at: Optional DLQ timestamp (same source as
            ``dlq_reason``).
    """
    if work_record is not None:
        # Resolver-aware path (Phase 1). Execution state comes from
        # the WorkRecord, which sources ``status`` from the joined
        # Instance and ``result_summary`` / ``error_message`` from the
        # JobItem mirror columns (the Instance doesn't model these
        # yet; see the Phase 1 transition note in
        # ``work_resolver._job_to_record``).
        status = work_record.status
        instance_id = work_record.instance_id
        result_summary = work_record.result_summary
        error_message = work_record.error
        # Timing: the WorkRecord carries ``started_at`` / ``completed_at``
        # already sourced from the Instance columns
        # (``last_activity_at`` → ``started_at``,
        # ``updated_at`` → ``completed_at``) with the JobItem mirror
        # as a fallback. Prefer the WorkRecord value so the API
        # response stays in sync with the resolver's view.
        started_at = work_record.started_at
        completed_at = work_record.completed_at
        # ``created_at`` is a tz-aware datetime on WorkRecord; the
        # JobResponse schema expects an ISO-8601 string. Normalise
        # via the resolver's helper-equivalent inline format so the
        # wire output always carries the ``+00:00`` offset.
        created_at = work_record.created_at
        if created_at is not None:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=_tz.utc)
            created_at = created_at.isoformat()
    else:
        # Legacy fallback path. Execution state comes straight off
        # the JobItem mirror columns — used by older tests / partial
        # wirings where the resolver isn't reachable. The Phase 1
        # exit criterion is "execution-state reads resolve through
        # the instance/work layer"; this branch is the documented
        # exception for callers that haven't migrated yet.
        #
        # F16 fix: route through ``_derive_legacy_status`` so a
        # ``done`` job with ``terminal_reason='failed'`` /
        # ``'cancelled'`` / ``'aborted'`` no longer mis-reports as
        # ``"completed"`` (the lossy raw-map behaviour). Mirrors the
        # F3 fix in ``WorkResolverService._job_to_record``.
        status = _derive_legacy_status(
            job.admission_state,
            getattr(job, "terminal_reason", None),
        )
        instance_id = job.instance_id
        # Phase 5: JobItem mirror columns (``result_summary``,
        # ``error_message``, ``started_at``, ``completed_at``) were
        # removed from the SQLModel in Phase B; the legacy fallback
        # returns ``None`` for them. Use the resolver/``work_record``
        # path above (lines 116-117) for Instance-sourced values.
        result_summary = None
        error_message = None
        # Legacy timing: read directly from the JobItem mirror columns.
        started_at = None
        completed_at = None
        created_at = job.created_at

    return JobResponse(
        job_id=job.job_id,
        status=status,
        admission_state=job.admission_state,
        priority=job.priority,
        agent_id=job.agent_id,
        agent_dir=job.agent_dir,
        project_id=job.project_id,
        queue_id=job.queue_id,
        instance_id=instance_id,
        created_at=created_at,
        # Phase 1 (Job as Queue Proxy): timing fields are sourced
        # from the Instance via the resolver (``last_activity_at``
        # → ``started_at``, ``updated_at`` → ``completed_at``), with
        # the JobItem mirror columns as a defensive fallback for
        # callers on the legacy branch. The previous Phase 1
        # implementation hardcoded ``None`` for both fields based on
        # an incorrect "Instance has no timing columns" claim — the
        # Instance table has had ``last_activity_at`` /
        # ``created_at`` / ``updated_at`` / ``paused_at`` since the
        # original schema, and they are the authoritative execution
        # timestamps under the new model.
        started_at=started_at,
        completed_at=completed_at,
        result_summary=result_summary,
        error_message=error_message,
        source=job.source,
        job_metadata=job.job_metadata,
        # Phase 5: ``cancelled_at`` column was dropped from JobItem.
        # Cancellation timestamp is now derivable from
        # ``Instance.status == TERMINATED`` + ``Instance.terminated_at``.
        cancelled_at=None,
        idempotency_key=job.idempotency_key,
        position=position,
        message=message or job.message,
        dlq_reason=dlq_reason,
        retry_count=retry_count,
        moved_to_dlq_at=moved_to_dlq_at,
        deleted_at=job.deleted_at,
        # Phase 7c: terminal_reason discriminator. Read directly
        # from the JobItem — it's a queue-side field, not part of
        # the resolver's work-record view-model. Pre-7c rows have
        # ``None`` here (the column didn't exist); the resolver's
        # ``done → completed`` lossy fallback handles those rows
        # correctly. ``status`` above already carries the
        # canonicalised value (``"cancelled"`` for an aborted job,
        # etc.) — ``terminal_reason`` is the discriminator that
        # distinguishes ``cancelled`` from ``completed`` at the
        # source. ``getattr`` with ``None`` default keeps this
        # backward-compatible with mocks / older test fixtures that
        # construct ``JobItem`` rows without the column.
        terminal_reason=getattr(job, "terminal_reason", None),
    )


# ==================== CRUD Endpoints ====================


@router.post(
    "",
    response_model=JobResponse,
    responses={
        201: {"description": "Job created"},
        200: {"description": "Existing job returned (idempotent)"},
        422: {"model": JobValidationError, "description": "Validation error"},
    },
)
async def create_job(
    request: Request,
    body: JobCreateRequest,
    service: JobQueueService = Depends(get_job_queue_service),
):
    """Submit a new job for processing.
    
    Jobs are queued and processed by the JobProcessor. The job starts
    as PENDING and transitions to PROCESSING when picked up by the processor.
    
    With idempotency_key: if a job with the same key exists and is non-terminal,
    returns HTTP 200 with the existing job instead of creating a duplicate.
    
    Returns:
        201 with job details (new job created)
        200 with job details (existing non-terminal job returned)
        422 if validation errors
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    # Validate: queue_id requires project_id
    if body.queue_id and not body.project_id:
        raise HTTPException(
            status_code=422,
            detail={"error": "Validation Error", "message": "project_id is required when queue_id is specified"}
        )

    # Validate and resolve agent input
    try:
        resolved_agent_id, agent_path = validate_agent_id(body.agent_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid agent", "message": str(e)}
        )

    # Stability-backlog item 7 / F2 pre-close: user-supplied ``source``
    # at the HTTP boundary must not be able to forge an internal
    # dispatch origin. Internal callers (cascade-resume, watchover,
    # agent-to-agent ``internal_agent:``, completion-report drain
    # ``internal_report:``, error-report drain
    # ``internal_error_report:``, invoke_agent_and_wait
    # ``internal_invoke_and_wait:``, ``system:*`` infrastructure
    # notices) stamp these directly via ``manager.enqueue_message`` /
    # ``service.enqueue`` — they bypass this HTTP body path and are
    # unaffected. The 422 response shape matches the existing
    # ``ValidationError`` envelope (line 285-293) so the frontend
    # treats it like any other Pydantic validation failure. Empty /
    # None ``source`` falls back to the Pydantic default ``"api"`` so
    # the legacy contract is preserved.
    if is_reserved_source(body.source):
        raise HTTPException(
            status_code=422,
            detail=JobValidationError(
                error="Validation Error",
                details=[
                    {
                        "field": "source",
                        "message": (
                            f"Source '{body.source}' is reserved for "
                            "internal callers and cannot be supplied "
                            f"via HTTP. Reserved origins: "
                            f"{sorted(RESERVED_SOURCE_PREFIXES)}."
                        ),
                    }
                ],
            ).model_dump(),
        )

    # Normalize project_id for defense-in-depth consistency
    normalized_project_id = normalize_project_id(body.project_id)

    # Enqueue the job (service.enqueue handles idempotency check internally)
    try:
        # agent_tag intentionally omitted: HTTP API does not expose version_tag; jobs use base agent resolution
        job = await service.enqueue(
            agent_id=resolved_agent_id,
            message=body.message,
            source=body.source,
            project_id=normalized_project_id,
            priority=body.priority,
            metadata=body.metadata,
            queue_id=body.queue_id,
            idempotency_key=body.idempotency_key,
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=JobValidationError(
                error="Validation Error",
                details=[{"field": str(err["loc"][0]) if err["loc"] else "unknown", "message": err["msg"]} 
                        for err in e.errors()]
            ).model_dump()
        )
    except Exception as e:
        logger.error(f"Failed to enqueue job: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "Job submission failed", "message": "An internal error occurred while submitting the job"}
        )
    
    # Check if this was an idempotent return (job existed before this request)
    # This is detected by checking if the returned job has the same idempotency_key
    # and was already non-pending when returned
    is_idempotent_return = False
    if body.idempotency_key and job.idempotency_key == body.idempotency_key:
        if job.admission_state != AdmissionState.QUEUED.value:
            is_idempotent_return = True
    
    # Job is always PENDING at creation - return position if project_id provided
    position = None
    if job.project_id:
        try:
            position = await service._get_queue_position(job.job_id, job.project_id)
        except Exception:
            pass  # Best effort - position is optional

    # Phase 1 (Job as Queue Proxy): resolve the freshly-created job
    # through the resolver so ``status`` is sourced from the unified
    # ``WorkRecord`` rather than the JobItem mirror. At creation time
    # ``job.instance_id`` is ``None`` (job hasn't been dequeued yet),
    # so the resolver returns a ``WorkRecord`` with canonical
    # ``status="pending"`` and ``instance_id=None`` — which is
    # exactly what the JobResponse wants to surface.
    work_record = await service.get_work(job.job_id)

    response = _job_to_response(
        job,
        work_record=work_record,
        position=position,
        message="Job queued for processing",
    )
    
    # Return 200 for idempotent returns, 201 for new jobs
    return JSONResponse(
        status_code=200 if is_idempotent_return else 201,
        content=response.model_dump()
    )


@router.get(
    "/{job_id}",
    responses={
        200: {"description": "Job details"},
        404: {"model": JobNotFoundResponse, "description": "Job not found"},
    },
)
async def get_job(
    job_id: str,
    service: JobQueueService = Depends(get_job_queue_service),
    dlq_service: DeadLetterService = Depends(get_dead_letter_svc),
) -> JobResponse:
    """Get job status and details by ID.

    Returns:
        200 with job details
        404 if job doesn't exist
    """
    job = await service.get_job(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail=JobNotFoundResponse(
                error="Job not found",
                job_id=job_id
            ).model_dump()
        )

    # Get position if job is pending
    position = None
    if job.admission_state == AdmissionState.QUEUED.value and job.project_id:
        try:
            position = await service._get_queue_position(job.job_id, job.project_id)
        except Exception:
            pass  # Best effort

    # Get DLQ info if job is in dead_letter state
    dlq_reason = None
    retry_count = None
    moved_to_dlq_at = None
    if job.admission_state == AdmissionState.DEAD.value:
        dlq_item = dlq_service.get_dlq_by_job_id(job_id)
        if dlq_item:
            dlq_reason = dlq_item.reason
            retry_count = dlq_item.retry_count
            moved_to_dlq_at = dlq_item.moved_to_dlq_at

    # Phase 1 (Job as Queue Proxy): source execution state from the
    # resolver rather than the JobItem mirror. The WorkRecord's
    # ``status`` is read off the joined Instance (or "pending" when
    # the job hasn't been dequeued yet) — see
    # ``work_resolver._job_to_record``.
    work_record = await service.get_work(job_id)

    return _job_to_response(
        job,
        work_record=work_record,
        position=position,
        dlq_reason=dlq_reason,
        retry_count=retry_count,
        moved_to_dlq_at=moved_to_dlq_at,
    )


@router.get(
    "",
    response_model=JobListResponse,
)
async def list_jobs(
    status: str | None = None,
    project_id: str | None = None,
    queue_id: str | None = None,
    limit: int = DEFAULT_JOB_LIST_LIMIT,
    include_deleted: bool = False,
    service: JobQueueService = Depends(get_job_queue_service),
    dlq_service: DeadLetterService = Depends(get_dead_letter_svc),
) -> JobListResponse:
    """List jobs with optional filters.
    
    Query params:
        - status: Filter by status(es), comma-separated (pending, processing, completed, failed, cancelled, dead_letter)
        - project_id: Filter by project ID
        - queue_id: Filter by queue ID
        - limit: Maximum number of jobs to return (default: 50)
        - include_deleted: Include soft-deleted jobs (default: False)
    
    Returns:
        200 with list of jobs and total count
    """
    # Parse and validate statuses if provided
    statuses = None
    if status:
        # Parse, deduplicate, and normalize
        status_list = list(dict.fromkeys(
            s.strip().lower() for s in status.split(',') if s.strip()
        ))
        if len(status_list) > 20:
            raise HTTPException(
                status_code=400,
                detail={"error": "Too many status filters", "message": "Maximum 20 status values allowed"}
            )
        # Resolve natural-language aliases (e.g. "running" -> "processing") before validation
        status_list = normalize_statuses(status_list)
        invalid_statuses = [s for s in status_list if s not in _VALID_LEGACY_STATUSES]
        if invalid_statuses:
            raise HTTPException(
                status_code=400,
                detail={"error": "Invalid status", "message": f"Invalid status: {', '.join(invalid_statuses)}. Valid values: pending, processing, completed, failed, cancelled, dead_letter"}
            )
        statuses = status_list
    
    # Clamp limit
    limit = max(1, min(limit, MAX_JOB_LIST_LIMIT))
    
    # Validate: queue_id requires project_id
    if queue_id and not project_id:
        raise HTTPException(
            status_code=422,
            detail={"error": "Validation Error", "message": "project_id is required when queue_id is specified"}
        )
    
    # Validate queue belongs to project (IDOR protection)
    if queue_id and project_id:
        from daemon.routers.queues import get_mgmt_service
        try:
            mgmt = get_mgmt_service()
            queue = await mgmt.get_queue(project_id, queue_id)
            if queue is None:
                raise HTTPException(
                    status_code=404,
                    detail={"error": "Queue not found", "message": f"Queue {queue_id} not found for project {project_id}"}
                )
        except ValueError:
            raise HTTPException(
                status_code=404,
                detail={"error": "Queue not found", "message": f"Queue {queue_id} not found for project {project_id}"}
            )
    
    # List jobs
    jobs = await service.list_jobs(
        statuses=statuses,
        project_id=project_id,
        limit=limit,
        queue_id=queue_id,
        include_deleted=include_deleted,
    )

    # Phase 1 (Job as Queue Proxy): batch-resolve every JobItem
    # through the resolver so the response rows carry
    # instance-authoritative execution state (``status``, timing,
    # ``result_summary``, ``error_message``). The previous
    # implementation did a per-row ``asyncio.gather`` of
    # ``service.get_work`` — a classic N+1 (50 jobs → 50 resolver
    # calls → 50 ``SELECT … FROM instances`` round-trips). The
    # batched path issues ONE ``WorkResolverService.list_work`` call
    # which already does the per-page Instance lookup in a single
    # ``SELECT … WHERE instance_id IN (...)`` (see
    # ``work_resolver.list_work`` / ``_batch_instances``).
    #
    # Filter alignment: ``list_jobs`` accepts source-status values
    # (e.g. ``"processing"``); ``list_work`` accepts the canonical
    # vocabulary (``"processing"`` happens to be the canonical form
    # so we pass it through unchanged). When the caller passes a
    # multi-status filter (``"completed,failed"``) we join them into
    # a comma-separated canonical string — ``list_work`` does the
    # per-token canonical-to-source mapping internally.
    work_records_by_id: dict[str, Any] = {}
    if jobs:
        status_filter = ",".join(statuses) if statuses else None
        records = await service.list_work(
            project_id=project_id,
            status=status_filter,
            kind="job",
        )
        for record in records:
            # Defensive: only ``kind="job"`` rows belong on the
            # ``list_jobs`` response. ``list_work(kind="job")``
            # already filters server-side but a stale Test double or
            # future kind filter change should not leak Task rows
            # onto the JobResponse wire.
            if record.kind != "job":
                continue
            work_records_by_id[record.work_id] = record

    # Convert to response format
    job_responses = []
    for job in jobs:
        # Get position if pending
        position = None
        if job.admission_state == AdmissionState.QUEUED.value and job.project_id:
            try:
                position = await service._get_queue_position(job.job_id, job.project_id)
            except Exception:
                pass

        # Get DLQ info if job is in dead_letter state
        dlq_reason = None
        retry_count = None
        moved_to_dlq_at = None
        if job.admission_state == AdmissionState.DEAD.value:
            dlq_item = dlq_service.get_dlq_by_job_id(job.job_id)
            if dlq_item:
                dlq_reason = dlq_item.reason
                retry_count = dlq_item.retry_count
                moved_to_dlq_at = dlq_item.moved_to_dlq_at

        job_responses.append(_job_to_response(
            job,
            work_record=work_records_by_id.get(job.job_id),
            position=position,
            dlq_reason=dlq_reason,
            retry_count=retry_count,
            moved_to_dlq_at=moved_to_dlq_at,
        ))

    return JobListResponse(
        jobs=job_responses,
        total=len(job_responses),  # Note: for accurate total, would need a count method
    )


__all__ = ["router", "_job_to_response", "TERMINAL_STATUSES"]

"""Schedule API endpoints."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from daemon.constants import DEFAULT_SCHEDULE_EXECUTIONS_LIMIT, MAX_SCHEDULE_EXECUTION_LIMIT
from daemon.models import (
    ErrorCodes,
    ErrorResponse,
    ScheduleCancelRestResponse,
    ScheduleCreate,
    ScheduleCreateRestResponse,
    ScheduleDetailRestResponse,
    ScheduleExecutionInfo,
    ScheduleExecutionListResponse,
    ScheduleInfo,
    ScheduleListResponse,
    ScheduleTriggerResponse,
    ScheduleUpdate,
    SourceActionResponse,
    SourceStatus,
)
from daemon.services.scheduling_service import ScheduleCreatePayload
from daemon.utils import parse_utc_datetime, validate_instance_mode

logger = logging.getLogger(__name__)

# Create router with /api/schedules prefix
router = APIRouter(prefix="/schedules", tags=["schedules"])


def _get_manager(request: Request) -> "InstanceManager":
    """Get the InstanceManager from app state."""
    return request.app.state.manager


def _schedule_create_to_payload(req: ScheduleCreate) -> ScheduleCreatePayload:
    """REST ScheduleCreate → canonical ScheduleCreatePayload (ADR-013 F1 rewrite).

    Builds a FULLY-TYPED payload — NOT ``req.model_dump()`` — because the
    REST surface field names differ from the canonical ones:
    ``source_id``/``name`` → ``label``, ``agent_id`` → ``agent``,
    ``local_time``/``run_at`` → ``when`` (one per recurrence);
    ``timezone``/``cron_expression``/``weekday``/``priority``/
    ``project_id``/``instance_mode`` pass through. ``enabled``/
    ``autostart`` are NOT in the canonical payload (phase-2 hardcodes
    enabled=True, autostart=True — lifecycle flows through
    update/pause/cancel, never create).

    Label reconciliation (plan Task 1.3 vs Task 1.7, binding approver
    correction (c)): Task 1.3's "REST CREATE requires source_id == label"
    contradicts this frozen mapping ``label = req.name or req.source_id``.
    The FROZEN MAPPING FUNCTION wins: the canonical label is the display
    ``name`` when provided, else ``source_id``. Uniqueness is enforced
    ONCE, downstream, on the resolved label (the service raises
    ``ValueError("label already exists")`` → handler maps to 409); no
    separate ``source_id == label`` equality rule and no second
    uniqueness check are added here (Task 1.3's single-source-of-truth
    intent is preserved).
    """
    label = req.name or req.source_id
    if req.recurrence == "once":
        when = req.run_at.isoformat() if req.run_at else (req.local_time or "")
    else:  # daily / weekly / cron
        when = req.local_time or ""
    return ScheduleCreatePayload(
        label=label,
        agent=req.agent_id,
        message=req.message,
        when=when,
        timezone=req.timezone,
        recurrence=req.recurrence,
        weekday=req.weekday,
        cron_expression=req.cron_expression,
        project_id=req.project_id,
        priority=req.priority,
        instance_mode=req.instance_mode,
    )


# ==================== Endpoints ====================


# GET /schedules - List only scheduler sources
@router.get("", response_model=ScheduleListResponse)
async def list_schedules(request: Request, include_cancelled: bool = False):
    """List all configured scheduler sources.
    
    This endpoint filters sources to only return those with source_type='scheduler'.
    Returns schedules in the format expected by the frontend.

    Cancelled schedules are HIDDEN by default (ADR-005); pass
    ``?include_cancelled=true`` to include them. (GET
    /schedules/{schedule_id} always returns cancelled rows — the
    "did I actually cancel X?" path.)
    """
    manager = _get_manager(request)
    all_sources = await asyncio.to_thread(manager._source_repository.list_source_configs)
    schedules = []
    for src in all_sources:
        if src.source_type == "scheduler":
            # Default-exclude cancelled rows (architecture §3.4 / ADR-005).
            if not include_cancelled and src.status == SourceStatus.cancelled.value:
                continue
            # Calculate next_run_at from adapter if available
            next_run_at = None
            adapter = manager.source_registry.get(src.source_id) if manager.source_registry else None
            if adapter and hasattr(adapter, '_get_next_trigger_time'):
                try:
                    next_run_at = adapter._get_next_trigger_time()
                except Exception as e:
                    logger.debug(f"Failed to get next_run_at: {e}")

            # Get last_run_at from latest execution record
            last_run_at = None
            try:
                latest_execution = manager._source_repository.get_latest_execution(src.source_id)
                if latest_execution:
                    last_run_at = parse_utc_datetime(latest_execution.triggered_at)
            except Exception as e:
                logger.debug(f"Failed to get last_run_at: {e}")

            schedules.append(ScheduleInfo(
                id=src.source_id,
                name=src.name,
                config=src.config,
                status=SourceStatus(src.status),
                created_at=parse_utc_datetime(src.created_at),
                updated_at=parse_utc_datetime(src.updated_at),
                last_run_at=last_run_at,
                next_run_at=next_run_at,
            ))
    return ScheduleListResponse(schedules=schedules)


# POST /schedules - Create a new schedule
@router.post("", response_model=ScheduleCreateRestResponse, status_code=201)
async def create_schedule(req: ScheduleCreate, request: Request):
    """Create a new scheduled task.

    The body mirrors the scheduling tool category's `task_schedule`
    semantics. User-stated `local_time` is ALWAYS interpreted in the
    supplied `timezone` (or the default chain: explicit → user timezone
    setting → ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE → host-local → UTC
    with a loud warning). The response echoes BOTH local and UTC for
    `next_run_at_*` so external callers never have to re-derive timezone.

    Schedules are created enabled+autostart; lifecycle via update/pause/
    cancel (POST /schedules/{id}/stop|start, DELETE /schedules/{id}).
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    # 400 on instance_mode invalid (mirror the update route's validation).
    validate_instance_mode(instance_mode=req.instance_mode, config={})

    # REST ↔ canonical mapping (phase-3 §Task 1.2/1.7 + ADR-013 F1):
    # builds a fully-typed ScheduleCreatePayload; NOT req.model_dump()
    # (REST name/agent_id/local_time+run_at vs canonical label/agent/when).
    payload = _schedule_create_to_payload(req)

    # The service raises ValueError on invalid input (duplicate label,
    # unknown agent, bad recurrence/when/timezone). Duplicate label maps
    # to 409 Conflict (per plan §Task 1.3 uniqueness is enforced ONCE on
    # the resolved label); every other ValueError is client input error
    # → 400 (service docstring: callers map to 4xx).
    #
    # Landed service call: create_schedule is ASYNC (awaited directly —
    # correction (b); NOT wrapped in asyncio.to_thread, which never runs
    # a coroutine function) and requires keyword-only caller ids for
    # audit logging. REST has no calling instance/agent — the marker
    # below keeps the audit line truthful about its origin.
    try:
        created = await manager.scheduling_service.create_schedule(
            payload,
            caller_instance_id="rest-api",
            caller_agent_id="",
        )
    except ValueError as e:
        if "label" in str(e).lower() and "exists" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "SCHEDULE_LABEL_CONFLICT",
                    "message": str(e),
                },
            )
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=str(e),
            ).model_dump(),
        )

    # Flat ScheduleCreateRestResponse (no `detail=` envelope).
    return ScheduleCreateRestResponse(
        id=created.source_id,
        source_id=created.source_id,
        label=created.label,
        status=created.status,
        next_run_at_local=created.next_run_at_local,
        next_run_at_utc=created.next_run_at_utc,
        tz_warning=created.tz_warning,
    )


# GET /schedules/{schedule_id} - Single fetch (returns cancelled rows per architecture §3.4)
@router.get("/{schedule_id}", response_model=ScheduleDetailRestResponse)
async def get_schedule_by_id(schedule_id: str, request: Request):
    """Fetch a single schedule by source_id (or label — the service resolves either).

    Ungated (house posture for GETs; may surface rows mid-migration).
    Returns cancelled rows too: this is the "did I actually cancel X?"
    path (architecture §3.4); `cancelled_at` is populated when
    status == "cancelled". Both `next_run_at_local` and `next_run_at_utc`
    are computed by the shared service (null together when no upcoming
    run). Accepts id or label; unknown refs and non-scheduler rows are
    both 404.
    """
    manager = _get_manager(request)

    # Landed service call: get_schedule is ASYNC (awaited directly — correction (b)).
    detail = await manager.scheduling_service.get_schedule(schedule_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Schedule not found: {schedule_id}",
            ).model_dump(),
        )
    return ScheduleDetailRestResponse(
        id=detail.source_id,
        source_id=detail.source_id,
        label=detail.label,
        status=detail.status,
        recurrence=detail.recurrence,
        local_time=detail.local_time,
        timezone=detail.timezone,
        cron_expression=detail.cron_expression,
        weekday=detail.weekday,
        next_run_at_local=detail.next_run_at_local,
        next_run_at_utc=detail.next_run_at_utc,
        last_run_at=detail.last_run_at,
        agent_id=detail.agent,
        project_id=detail.project_id,
        instance_mode=detail.instance_mode,
        cancelled_at=detail.cancelled_at,
        tz_warning=detail.tz_warning,
    )


# DELETE /schedules/{schedule_id} - Cancel a schedule (terminal; history preserved)
@router.delete("/{schedule_id}", response_model=ScheduleCancelRestResponse)
async def cancel_schedule(schedule_id: str, request: Request):
    """Cancel a schedule.

    Terminal — the schedule is hidden from default list output, never
    fires post-restart, and history (schedule_executions) is PRESERVED.
    This route NEVER purges the row (that is the /api/sources delete
    path); the shared service performs the atomic cancel
    (status → "cancelled") and evicts the live adapter. Pause/resume
    remain the non-terminal alternative (POST /schedules/{id}/stop|start).
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    # Landed service call: cancel_schedule is ASYNC (awaited directly —
    # correction (b)). Service ValueError space: unknown reference → 404,
    # already-cancelled → 409 (cancel is terminal).
    try:
        cancelled = await manager.scheduling_service.cancel_schedule(schedule_id)
    except ValueError as e:
        if "already cancelled" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "SCHEDULE_ALREADY_CANCELLED",
                    "message": str(e),
                },
            )
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=str(e),
            ).model_dump(),
        )

    return ScheduleCancelRestResponse(
        id=cancelled.source_id,
        source_id=cancelled.source_id,
        status=cancelled.status,
        cancelled_at=cancelled.cancelled_at,
        last_execution_id=cancelled.last_execution_id,  # architecture §5.3 echo
        message=f"Schedule {schedule_id} cancelled (history retained)",
    )


# PUT /schedules/{schedule_id} - Update a schedule
@router.put("/{schedule_id}", response_model=ScheduleInfo)
async def update_schedule(schedule_id: str, schedule_update: ScheduleUpdate, request: Request):
    """Update a schedule configuration."""
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    # Check source exists and is a scheduler
    existing = await asyncio.to_thread(manager._source_repository.get_source_config, schedule_id)
    if not existing:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Schedule not found: {schedule_id}"
            ).model_dump()
        )
    
    if existing.source_type != "scheduler":
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Source {schedule_id} is not a scheduler (type: {existing.source_type})"
            ).model_dump()
        )

    # Round-5 fix: cancel is TERMINAL (ADR-005/007). PUT /schedules/{id}
    # must not mutate a cancelled row — cancel_schedule evicts the adapter
    # (seam-2 invariant), but the DB row + name + config still linger.
    # Without it the route silently re-writes the persisted config (so the
    # row becomes re-cancellable / re-boot-startable), violating the
    # "never fires again, not after a daemon restart" tools_note contract.
    # Mirrors the landed POST /start 409 (commit 2e7c5288, schedules.py:516)
    # — same error code, same HTTP status — and the DELETE cancel-already-
    # cancelled mapping at :284. Gate sits AFTER the get-or-404 (unknown
    # ids still 404 SOURCE_NOT_FOUND) and source_type check (non-scheduler
    # rows still 400 INVALID_REQUEST).
    if existing.status == SourceStatus.cancelled.value:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCHEDULE_ALREADY_CANCELLED",
                "message": f"Schedule {schedule_id} is cancelled and cannot be updated (cancel is terminal)",
            },
        )

    # Merge updates
    updated_name = schedule_update.name if schedule_update.name is not None else existing.name
    updated_config = schedule_update.config if schedule_update.config is not None else existing.config
    
    # Handle partial config update (merge with existing config)
    if schedule_update.config is not None and existing.config:
        # Merge partial config with existing config
        merged_config = {**existing.config, **schedule_update.config}
        updated_config = merged_config
    
    # Validate and process instance_mode
    instance_mode_config = validate_instance_mode(
        instance_mode=schedule_update.instance_mode,
        config=updated_config
    )
    updated_config["instance_mode"] = instance_mode_config["instance_mode"]
    
    # If instance_mode is reuse_instance, enforce max_concurrent = 1
    if updated_config.get("instance_mode") == "reuse_instance":
        current_max = updated_config.get("max_concurrent")
        if current_max is not None and current_max != 1:
            logger.info(f"Adjusting max_concurrent from {current_max} to 1 for reuse_instance mode")
            updated_config["max_concurrent"] = 1
    
    # Update source config using repository
    updated = await asyncio.to_thread(
        manager._source_repository.update_source_config,
        source_id=schedule_id,
        source_type=existing.source_type,
        name=updated_name,
        config=updated_config,
        credentials=existing.credentials,
        enabled=existing.enabled,
    )
    
    # Calculate next_run_at from adapter if available
    next_run_at = None
    adapter = manager.source_registry.get(updated.source_id)
    if adapter and hasattr(adapter, '_get_next_trigger_time'):
        try:
            next_run_at = adapter._get_next_trigger_time()
        except Exception as e:
            logger.debug(f"Failed to get next_run_at: {e}")

    # Get last_run_at from latest execution record
    last_run_at = None
    try:
        latest_execution = manager._source_repository.get_latest_execution(schedule_id)
        if latest_execution:
            last_run_at = parse_utc_datetime(latest_execution.triggered_at)
    except Exception as e:
        logger.debug(f"Failed to get last_run_at: {e}")

    return ScheduleInfo(
        id=updated.source_id,
        name=updated.name,
        config=updated.config,
        status=SourceStatus(updated.status),
        created_at=parse_utc_datetime(updated.created_at),
        updated_at=parse_utc_datetime(updated.updated_at),
        last_run_at=last_run_at,
        next_run_at=next_run_at,
    )


# POST /schedules/{schedule_id}/trigger - Manually trigger a schedule
@router.post("/{schedule_id}/trigger", response_model=ScheduleTriggerResponse)
async def trigger_schedule(schedule_id: str, request: Request):
    """Manually trigger a scheduled job.
    
    Triggers the schedule immediately, regardless of its configured schedule.
    """
    from daemon.sources.base import SourceConfig

    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    # Check source exists and is a scheduler
    source = await asyncio.to_thread(manager._source_repository.get_source_config, schedule_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Schedule not found: {schedule_id}"
            ).model_dump()
        )

    if source.source_type != "scheduler":
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Source {schedule_id} is not a scheduler (type: {source.source_type})"
            ).model_dump()
        )

    # Round-5 fix: cancel is TERMINAL (ADR-005/007). POST /trigger must
    # not fire on a cancelled row — cancel_schedule evicts the adapter
    # (seam-2 invariant), so the registry.get() below would return None
    # and the route would 503 (adapter-not-running) instead of the
    # semantically correct 409. Make the gate explicit so callers
    # (retry middleware, web-UI "run now" button hidden-state-survival
    # check) treat trigger-of-cancelled identically to start-of-cancelled
    # and cancel-of-cancelled. Mirrors the landed POST /start 409 (commit
    # 2e7c5288, schedules.py:516) — same error code, same HTTP status.
    # Gate sits AFTER get-or-404 (unknown ids still 404) and AFTER
    # source_type check (non-scheduler rows still 400).
    if source.status == SourceStatus.cancelled.value:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCHEDULE_ALREADY_CANCELLED",
                "message": f"Schedule {schedule_id} is cancelled and cannot be triggered (cancel is terminal)",
            },
        )

    # Check if registry has the source
    if not manager.source_registry:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message="Source registry not available"
            ).model_dump()
        )
    
    adapter = manager.source_registry.get(schedule_id)
    if not adapter:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Schedule adapter not running: {schedule_id}"
            ).model_dump()
        )
    
    # Trigger the schedule
    try:
        execution_id = await adapter.manual_trigger()
        # Note: Execution is recorded by the scheduler's execution_callback,
        # not here, to avoid duplicate records
        
        return ScheduleTriggerResponse(
            execution_id=execution_id,
            schedule_id=schedule_id,
            message="Schedule triggered successfully"
        )
    except Exception as e:
        logger.error(f"Failed to trigger schedule {schedule_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to trigger schedule: {str(e)}"
            ).model_dump()
        )


# POST /schedules/{schedule_id}/start - Start a scheduler
@router.post("/{schedule_id}/start", response_model=SourceActionResponse)
async def start_schedule(schedule_id: str, request: Request):
    """Start a scheduler source."""
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    # Check source exists
    source = await asyncio.to_thread(manager._source_repository.get_source_config, schedule_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Schedule not found: {schedule_id}"
            ).model_dump()
        )
    
    # Verify it's a scheduler source
    if source.source_type != "scheduler":
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Source {schedule_id} is not a scheduler (type: {source.source_type})"
            ).model_dump()
        )

    # Round-5 fix: cancel is TERMINAL (ADR-005/007). POST /start must not
    # resurrect a cancelled row — cancel_schedule evicts the adapter
    # (seam-2 invariant), so without this gate the rebuild branch below
    # always fires: _create_adapter_from_config (ignores status) → register
    # → start_adapter persists STARTING→RUNNING, erasing the terminal
    # 'cancelled' status in the DB (the row becomes boot-startable again,
    # violating the "never fires again, not after a daemon restart" tools_note
    # contract). Mirrors DELETE /schedules/{id} cancel-already-cancelled
    # mapping at :284 — same error code, same HTTP status, so callers
    # (web-UI "start" button hidden-state-survival check, retry middleware)
    # treat start-of-cancelled identically to double-cancel. Gate sits
    # AFTER the get-or-404 (unknown ids still 404) and source_type check
    # (non-scheduler rows still 400 INVALID_REQUEST).
    if source.status == SourceStatus.cancelled.value:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCHEDULE_ALREADY_CANCELLED",
                "message": f"Schedule {schedule_id} is cancelled and cannot be started (cancel is terminal)",
            },
        )

    # Start the scheduler adapter
    try:
        # Round-2 fix: scheduler adapters are constructed ONLY at boot
        # (registry.start_all at daemon/sources/registry.py:208-215). The
        # /sources/{id}/start route rejects schedulers (sources.py:364/:465),
        # so there is no other register site. After stop_adapter evicts
        # (seam-2 invariant, registry.py:578), the registry has no adapter
        # for this id. We must rebuild from the fresh DB row before calling
        # start_adapter — otherwise start_adapter returns False, the route
        # falls through to ``status = adapter.status if adapter else None``,
        # SourceActionResponse(status=None, ...) is rejected by pydantic
        # (source.py:181-183), and EVERY same-session pause→resume returns
        # HTTP 500. The same rebuild branch heals resume-after-restart for
        # schedulers that were persisted as 'stopped' at boot (boot-skip at
        # registry.py:201-203) — pre-existing breakage, fixed here for free.
        if manager.source_registry:
            existing_adapter = manager.source_registry.get(schedule_id)
            if existing_adapter is None:
                # Re-load the persisted config (already validated above;
                # re-use the `source` row we fetched at :259).
                adapter = await manager.source_registry._create_adapter_from_config(source)
                if adapter is None:
                    raise HTTPException(
                        status_code=500,
                        detail=ErrorResponse(
                            code=ErrorCodes.INTERNAL_ERROR,
                            message=f"Failed to build scheduler adapter for: {schedule_id} (unsupported type)"
                        ).model_dump()
                    )
                manager.source_registry.register(adapter)

        started = await manager.source_registry.start_adapter(schedule_id)
        if not started:
            # Surface the registry-level failure instead of constructing
            # SourceActionResponse(status=None, ...) which pydantic would
            # reject — that was the round-2 500-on-resume regression.
            raise HTTPException(
                status_code=500,
                detail=ErrorResponse(
                    code=ErrorCodes.INTERNAL_ERROR,
                    message=f"Failed to start scheduler adapter: {schedule_id}"
                ).model_dump()
            )
        adapter = manager.source_registry.get(schedule_id)
        status = adapter.status if adapter else SourceStatus.running
        return SourceActionResponse(
            source_id=schedule_id,
            status=status,
            message=f"Scheduler {schedule_id} started successfully"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start scheduler {schedule_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to start scheduler: {str(e)}"
            ).model_dump()
        )


# POST /schedules/{schedule_id}/stop - Stop a scheduler
@router.post("/{schedule_id}/stop", response_model=SourceActionResponse)
async def stop_schedule(schedule_id: str, request: Request):
    """Stop a scheduler source."""
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    # Check source exists
    source = await asyncio.to_thread(manager._source_repository.get_source_config, schedule_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Schedule not found: {schedule_id}"
            ).model_dump()
        )
    
    # Verify it's a scheduler source
    if source.source_type != "scheduler":
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Source {schedule_id} is not a scheduler (type: {source.source_type})"
            ).model_dump()
        )

    # Round-5 fix: cancel is TERMINAL (ADR-005/007). POST /stop must
    # not re-run on a cancelled row — cancel_schedule evicts the adapter
    # (seam-2 invariant), so stop_adapter would be a registry-level
    # no-op (or "unknown source" error) on a terminal row. Make the gate
    # explicit so callers (retry middleware, web-UI pause/stop button)
    # treat stop-of-cancelled identically to start-of-cancelled. Mirrors
    # the landed POST /start 409 (commit 2e7c5288, schedules.py:516) —
    # same error code, same HTTP status. Gate sits AFTER get-or-404
    # (unknown ids still 404) and AFTER source_type check (non-scheduler
    # rows still 400).
    if source.status == SourceStatus.cancelled.value:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCHEDULE_ALREADY_CANCELLED",
                "message": f"Schedule {schedule_id} is cancelled and cannot be stopped (cancel is terminal)",
            },
        )

    # Stop the scheduler adapter
    try:
        await manager.source_registry.stop_adapter(schedule_id)
        return SourceActionResponse(
            source_id=schedule_id,
            status=SourceStatus.stopped,
            message=f"Scheduler {schedule_id} stopped successfully"
        )
    except Exception as e:
        logger.error(f"Failed to stop scheduler {schedule_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to stop scheduler: {str(e)}"
            ).model_dump()
        )


# GET /schedules/{schedule_id}/executions - Get execution history
@router.get("/{schedule_id}/executions", response_model=ScheduleExecutionListResponse)
async def get_schedule_executions(
    schedule_id: str,
    request: Request,
    limit: int = DEFAULT_SCHEDULE_EXECUTIONS_LIMIT,  # 100
    offset: int = 0
):
    """Get execution history for a scheduled job.
    
    Args:
        schedule_id: The schedule to get executions for.
        limit: Maximum number of executions to return (default: 100).
        offset: Number of executions to skip (default: 0).
    """
    manager = _get_manager(request)
    
    # Check source exists and is a scheduler
    source = await asyncio.to_thread(manager._source_repository.get_source_config, schedule_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Schedule not found: {schedule_id}"
            ).model_dump()
        )
    
    if source.source_type != "scheduler":
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Source {schedule_id} is not a scheduler (type: {source.source_type})"
            ).model_dump()
        )
    
    # Input validation
    limit = max(1, min(limit, MAX_SCHEDULE_EXECUTION_LIMIT))  # Clamp to 1-MAX_SCHEDULE_EXECUTION_LIMIT
    offset = max(0, offset)  # Ensure non-negative
    
    # Get executions from repository
    executions_data = await asyncio.to_thread(
        manager._source_repository.list_schedule_executions,
        schedule_id=schedule_id,
        limit=limit,
        offset=offset
    )
    
    # Get total count (approximate - using len for now)
    # For accurate total, we'd need a count method in the repository
    total = len(executions_data)
    
    executions = []
    for exec_data in executions_data:
        executions.append(ScheduleExecutionInfo(
            execution_id=exec_data.execution_id,
            schedule_id=exec_data.schedule_id,
            triggered_at=parse_utc_datetime(exec_data.triggered_at),
            instance_id=exec_data.instance_id,
            status=exec_data.status,
            error_message=exec_data.error_message,
            completed_at=parse_utc_datetime(exec_data.completed_at),
        ))
    
    return ScheduleExecutionListResponse(
        executions=executions,
        total=total
    )

"""Schedule API endpoints."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from daemon.constants import DEFAULT_SCHEDULE_EXECUTIONS_LIMIT, MAX_SCHEDULE_EXECUTION_LIMIT
from daemon.models import (
    ErrorCodes,
    ErrorResponse,
    ScheduleExecutionInfo,
    ScheduleExecutionListResponse,
    ScheduleInfo,
    ScheduleListResponse,
    ScheduleTriggerResponse,
    ScheduleUpdate,
    SourceActionResponse,
    SourceStatus,
)
from daemon.utils import parse_utc_datetime, validate_instance_mode

logger = logging.getLogger(__name__)

# Create router with /api/schedules prefix
router = APIRouter(prefix="/schedules", tags=["schedules"])


def _get_manager(request: Request) -> "InstanceManager":
    """Get the InstanceManager from app state."""
    return request.app.state.manager


# ==================== Endpoints ====================


# GET /schedules - List only scheduler sources
@router.get("", response_model=ScheduleListResponse)
async def list_schedules(request: Request):
    """List all configured scheduler sources.
    
    This endpoint filters sources to only return those with source_type='scheduler'.
    Returns schedules in the format expected by the frontend.
    """
    manager = _get_manager(request)
    all_sources = await asyncio.to_thread(manager._source_repository.list_source_configs)
    schedules = []
    for src in all_sources:
        if src.source_type == "scheduler":
            # Calculate next_run_at from adapter if available
            next_run_at = None
            adapter = manager.source_registry.get(src.source_id) if manager.source_registry else None
            if adapter and hasattr(adapter, '_get_next_trigger_time'):
                try:
                    next_run_at = adapter._get_next_trigger_time()
                except Exception:
                    pass

            # Get last_run_at from latest execution record
            last_run_at = None
            try:
                latest_execution = manager._source_repository.get_latest_execution(src.source_id)
                if latest_execution:
                    last_run_at = parse_utc_datetime(latest_execution.triggered_at)
            except Exception:
                pass

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


# PUT /schedules/{schedule_id} - Update a schedule
@router.put("/{schedule_id}", response_model=ScheduleInfo)
async def update_schedule(schedule_id: str, schedule_update: ScheduleUpdate, request: Request):
    """Update a schedule configuration."""
    manager = _get_manager(request)
    
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
        except Exception:
            pass

    # Get last_run_at from latest execution record
    last_run_at = None
    try:
        latest_execution = manager._source_repository.get_latest_execution(schedule_id)
        if latest_execution:
            last_run_at = parse_utc_datetime(latest_execution.triggered_at)
    except Exception:
        pass

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
    
    # Start the scheduler adapter
    try:
        await manager.source_registry.start_adapter(schedule_id)
        adapter = manager.source_registry.get(schedule_id)
        status = adapter.status if adapter else None
        return SourceActionResponse(
            source_id=schedule_id,
            status=status,
            message=f"Scheduler {schedule_id} started successfully"
        )
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

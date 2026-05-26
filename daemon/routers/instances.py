"""Instance management API endpoints."""

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from daemon.constants import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from daemon.models import (
    ErrorCodes,
    ErrorResponse,
    InstanceCreate,
    InstanceInfo,
    InstanceListResponse,
    InstanceStatus,
    ResumeRequest,
)
from daemon.utils import parse_utc_datetime

logger = logging.getLogger(__name__)

# Create router with /instances prefix
router = APIRouter(prefix="/instances", tags=["instances"])


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state.
    
    Args:
        request: FastAPI request object.
        
    Returns:
        InstanceManager instance.
    """
    return request.app.state.manager


# 1. POST /instances - Spawn instance
@router.post("", response_model=InstanceInfo, status_code=201)
async def create_instance(
    instance_create: InstanceCreate,
    request: Request,
) -> InstanceInfo:
    """Spawn a new instance."""
    manager = _get_manager(request)
    
    # Generate instance_id upfront so MCP preload can use it
    instance_id = instance_create.instance_id or str(uuid.uuid4())
    
    try:
        # Prefer agent_id over agent_dir
        instance_id = await manager.spawn_instance_with_mcp(
            agent_id=instance_create.agent_id,
            instance_id=instance_id,
            project_id=instance_create.project_id,
        )
    except ValueError as e:
        error_msg = str(e)
        if "Max instances limit" in error_msg:
            raise HTTPException(
                status_code=429,
                detail=ErrorResponse(
                    code=ErrorCodes.MAX_INSTANCES_EXCEEDED,
                    message=error_msg
                ).model_dump()
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    code=ErrorCodes.INVALID_REQUEST,
                    message=error_msg
                ).model_dump()
            )

    # Get instance info from database
    instance_meta = manager.get_instance_info(instance_id)
    return InstanceInfo(
        instance_id=instance_meta["instance_id"],
        agent_id=instance_meta["agent_id"],
        agent_dir=instance_meta["agent_dir"],
        status=InstanceStatus(instance_meta["status"]),
        parent_id=instance_meta.get("parent_id"),
        children=instance_meta.get("children", []),
        mcp_tool_names=instance_meta.get("metadata", {}).get("mcp_tool_names"),
        created_at=parse_utc_datetime(instance_meta["created_at"]),
        updated_at=parse_utc_datetime(instance_meta.get("updated_at")),
        project_id=instance_meta.get("project_id"),
    )


# 2. GET /instances - List instances
@router.get("", response_model=InstanceListResponse)
async def list_instances(
    request: Request,
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
    project_id: str | None = Query(None, description="Filter instances by project ID"),
    exclude_kb: bool = Query(True, description="Exclude KB-related instances (experiencer, kb-importer)"),
) -> InstanceListResponse:
    """List instances with pagination.
    
    Args:
        request: FastAPI request object.
        limit: Maximum number of instances to return (default: 20, max: 100).
        offset: Number of instances to skip (default: 0, min: 0).
        project_id: Filter instances by project ID (optional).
        exclude_kb: Exclude KB-related instances (experiencer, kb-importer) when True (default: True).
    """
    manager = _get_manager(request)
    
    # Input validation
    limit = max(1, min(limit, MAX_PAGE_LIMIT))  # Clamp to 1-MAX_PAGE_LIMIT
    offset = max(0, offset)  # Ensure non-negative
    
    instances_data, total = manager.list_instances(
        limit=limit, offset=offset, project_id=project_id, exclude_kb=exclude_kb
    )
    instances = []
    for inst in instances_data:
        instances.append(InstanceInfo(
            instance_id=inst["instance_id"],
            agent_id=inst["agent_id"],
            agent_dir=inst["agent_dir"],
            status=InstanceStatus(inst["status"]),
            parent_id=inst.get("parent_id"),
            children=inst.get("children", []),
            title=inst.get("title"),
            mcp_tool_names=inst.get("metadata", {}).get("mcp_tool_names"),
            created_at=parse_utc_datetime(inst["created_at"]),
            updated_at=parse_utc_datetime(inst.get("updated_at")),
            project_id=inst.get("project_id"),
        ))
    
    has_more = (offset + limit) < total
    
    return InstanceListResponse(
        instances=instances,
        total=total,
        limit=limit,
        offset=offset,
        has_more=has_more
    )


# 3. GET /instances/{instance_id} - Get instance info
@router.get("/{instance_id}", response_model=InstanceInfo)
async def get_instance(
    instance_id: str,
    request: Request,
) -> InstanceInfo:
    """Get instance information."""
    manager = _get_manager(request)
    
    try:
        instance_meta = manager.get_instance_info(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    return InstanceInfo(
        instance_id=instance_meta["instance_id"],
        agent_id=instance_meta["agent_id"],
        agent_dir=instance_meta["agent_dir"],
        status=InstanceStatus(instance_meta["status"]),
        parent_id=instance_meta.get("parent_id"),
        children=instance_meta.get("children", []),
        title=instance_meta.get("title"),
        mcp_tool_names=instance_meta.get("metadata", {}).get("mcp_tool_names"),
        created_at=parse_utc_datetime(instance_meta["created_at"]),
        updated_at=parse_utc_datetime(instance_meta.get("updated_at")),
        project_id=instance_meta.get("project_id"),
    )


# 4. DELETE /instances/{instance_id} - Terminate instance
@router.delete("/{instance_id}")
async def terminate_instance(
    instance_id: str,
    request: Request,
) -> dict:
    """Terminate an instance."""
    manager = _get_manager(request)
    
    # Check instance exists
    try:
        await manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    await manager.terminate_instance(instance_id)
    
    return {"terminated": True}


# 5. POST /instances/{instance_id}/pause - Pause instance
@router.post("/{instance_id}/pause")
async def pause_instance(
    instance_id: str,
    request: Request,
) -> dict:
    """Pause an instance and cascade to children."""
    manager = _get_manager(request)

    # Check instance exists
    try:
        await manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    result = await manager.pause_instance_cascade(instance_id)
    return {
        "paused": True,
        "paused_ids": result["paused_ids"],
        "skipped_ids": result["skipped_ids"],
    }


# 6. POST /instances/{instance_id}/resume - Resume instance
@router.post("/{instance_id}/resume")
async def resume_instance(
    instance_id: str,
    request: Request,
    body: ResumeRequest | None = None,
) -> dict:
    """Resume a paused instance and cascade to children.
    
    Optionally sends a message through the normal pipeline after resuming.
    """
    manager = _get_manager(request)

    # Check instance exists
    try:
        await manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    # Cascade resume (sets PAUSED→RUNNING for target + children)
    result = await manager.resume_instance_cascade(instance_id)

    # Cancel any old PROCESSING MESSAGE jobs that are zombie (asyncio task cancelled
    # but DB status still PROCESSING). This prevents the new job from being blocked
    # by the concurrency gate in message_job_handler.py.
    zombie_jobs = await asyncio.to_thread(
        manager._job_queue_service._repository.find_processing_message_jobs_by_instance,
        instance_id
    )
    for old_job in zombie_jobs:
        # Cancel the job in JobQueue
        try:
            await manager._job_queue_service.cancel_job(old_job.job_id)
            logger.info(
                f"resume_instance: cancelled zombie MESSAGE job {old_job.job_id[:8]}... "
                f"for instance {instance_id[:8]}..."
            )
        except Exception as e:
            logger.warning(
                f"resume_instance: failed to cancel zombie job {old_job.job_id[:8]}...: {e}"
            )

        # Cancel corresponding MessageQueue entry (only if still in non-terminal state)
        old_message_id = old_job.job_metadata.get("message_id") if old_job.job_metadata else None
        if old_message_id:
            try:
                # Check if message already completed to avoid race condition
                existing = manager._queue_repository.get(old_message_id)
                if existing and existing.status not in ("completed", "failed"):
                    await asyncio.to_thread(
                        manager._queue_repository.fail,
                        old_message_id,
                        "Cancelled due to resume"
                    )
                    logger.info(
                        f"resume_instance: cancelled zombie MessageQueue entry {old_message_id[:8]}... "
                        f"for instance {instance_id[:8]}..."
                    )
                elif existing:
                    logger.debug(
                        f"resume_instance: skipping MessageQueue entry {old_message_id[:8]}... "
                        f"(status={existing.status}) for instance {instance_id[:8]}..."
                    )
            except Exception as e:
                logger.warning(
                    f"resume_instance: failed to cancel MessageQueue entry {old_message_id[:8]}...: {e}"
                )

    if zombie_jobs:
        logger.info(
            f"resume_instance: cancelled {len(zombie_jobs)} zombie job(s) for instance {instance_id[:8]}..."
        )

    msg_result = None
    try:
        # Only enqueue if this instance was actually resumed
        if instance_id in result["resumed_ids"]:
            message_text = (body.message.strip() if body and body.message else None) or "resume"
            msg_result = await manager.enqueue_message_via_jq(
                instance_id=instance_id,
                message=message_text,
                source="api",
            )
    except Exception:
        # Rollback: re-pause all resumed instances
        for rid in result.get("resumed_ids", []):
            try:
                # Direct repo update for rollback — avoids re-calling cascade which would log/notify
                manager._instance_repository.update(rid, status=InstanceStatus.PAUSED.value)
            except Exception:
                pass
        raise

    return {
        "resumed": True,
        "resumed_ids": result["resumed_ids"],
        "skipped_ids": result["skipped_ids"],
        "message_id": msg_result.message_id if msg_result else None,
    }


# 7. POST /instances/{instance_id}/stop - Deprecated: use POST /pause instead
@router.post("/{instance_id}/stop", deprecated=True)
async def stop_instance_deprecated(
    instance_id: str,
    request: Request,
) -> dict:
    """Deprecated: Use POST /pause instead."""
    return await pause_instance(instance_id, request)


# 7. GET /instances/{instance_id}/messages - Get message history
@router.get("/{instance_id}/messages")
async def get_messages(
    instance_id: str,
    request: Request,
) -> list[dict]:
    """Get message history for an instance."""
    manager = _get_manager(request)
    
    # Check instance exists
    try:
        await manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    # Get message history from LangGraph checkpoints
    return await manager.get_messages(instance_id)

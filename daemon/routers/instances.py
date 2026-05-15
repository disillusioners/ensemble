"""Instance management API endpoints."""

import logging
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
    
    try:
        # Prefer agent_id over agent_dir
        instance_id = manager.spawn_instance(
            agent_id=instance_create.agent_id,
            instance_id=instance_create.instance_id,
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
) -> InstanceListResponse:
    """List instances with pagination.
    
    Args:
        request: FastAPI request object.
        limit: Maximum number of instances to return (default: 20, max: 100).
        offset: Number of instances to skip (default: 0, min: 0).
        project_id: Filter instances by project ID (optional).
    """
    manager = _get_manager(request)
    
    # Input validation
    limit = max(1, min(limit, MAX_PAGE_LIMIT))  # Clamp to 1-MAX_PAGE_LIMIT
    offset = max(0, offset)  # Ensure non-negative
    
    instances_data, total = manager.list_instances(
        limit=limit, offset=offset, project_id=project_id
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
        manager.get_instance(instance_id)
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


# 5. POST /instances/{instance_id}/stop - Stop instance
@router.post("/{instance_id}/stop")
async def stop_instance(
    instance_id: str,
    request: Request,
) -> dict:
    """Stop an instance and cascade to children."""
    manager = _get_manager(request)
    
    # Check instance exists
    try:
        manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    result = await manager.stop_instance_cascade(instance_id)
    return {
        "stopped": True,
        "stopped_ids": result["stopped_ids"],
        "skipped_ids": result["skipped_ids"],
    }


# 6. GET /instances/{instance_id}/messages - Get message history
@router.get("/{instance_id}/messages")
async def get_messages(
    instance_id: str,
    request: Request,
) -> list[dict]:
    """Get message history for an instance."""
    manager = _get_manager(request)
    
    # Check instance exists
    try:
        manager.get_instance(instance_id)
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

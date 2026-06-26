"""Instance Mapping API endpoints."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from daemon.models import (
    InstanceMappingCreate,
    InstanceMappingInfo,
    InstanceMappingListResponse,
    DeleteResponse,
    ErrorResponse,
    ErrorCodes,
)
from daemon.utils import parse_utc_datetime, validate_agent_id

logger = logging.getLogger(__name__)

# Create router for mapping endpoints under /sources prefix
router = APIRouter(prefix="/sources", tags=["mappings"])


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state."""
    return request.app.state.manager


# GET /sources/{source_id}/mappings - List mappings for a source
@router.get("/{source_id}/mappings", response_model=InstanceMappingListResponse)
async def list_mappings(source_id: str, request: Request):
    """List all instance mappings for a source."""
    manager = _get_manager(request)
    
    # Check source exists
    source = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    mappings_data = await asyncio.to_thread(manager._source_repository.list_instance_mappings, source_id)
    mappings = []
    for m in mappings_data:
        mappings.append(InstanceMappingInfo(
            mapping_id=m.mapping_id,
            source_id=m.source_id,
            external_user_id=m.external_user_id,
            agent_instance_id=m.agent_instance_id,
            agent_id=m.agent_id,
            agent_dir=m.agent_dir,
            metadata=m.mapping_metadata,
            last_message_at=parse_utc_datetime(m.last_message_at),
            created_at=parse_utc_datetime(m.created_at),
        ))
    return InstanceMappingListResponse(mappings=mappings)


# POST /sources/{source_id}/mappings - Create or update a mapping
@router.post("/{source_id}/mappings", response_model=InstanceMappingInfo, status_code=201)
async def create_mapping(source_id: str, mapping_create: InstanceMappingCreate, request: Request):
    """Create an instance mapping for an external user."""
    import uuid
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    # Validate agent_id
    resolved_agent_id, agent_path = validate_agent_id(mapping_create.agent_id)
    
    # Check source exists
    source = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    # Check if mapping already exists
    existing = await asyncio.to_thread(manager._source_repository.get_instance_mapping, source_id, mapping_create.external_user_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                code=ErrorCodes.MAPPING_ALREADY_EXISTS,
                message=f"Mapping already exists for user {mapping_create.external_user_id}"
            ).model_dump()
        )
    
    # Generate IDs (use standard UUID format for consistency)
    mapping_id = f"{source_id}:{mapping_create.external_user_id}"
    # Let manager auto-generate a valid UUID instance_id
    instance_id = None
    
    # Spawn the agent instance
    try:
        # Unpack the (instance_id, validated_model_override) tuple — the
        # router only needs the instance_id; the validated override is
        # consumed by tool-layer callers that emit a fallback notice.
        instance_id, _validated_model_override = manager.spawn_instance(
            agent_id=resolved_agent_id,
            instance_id=instance_id,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to spawn instance: {str(e)}"
            ).model_dump()
        )
    
    # Save the mapping with rollback on failure
    try:
        await asyncio.to_thread(
            manager._source_repository.create_instance_mapping,
            source_id=source_id,
            external_user_id=mapping_create.external_user_id,
            agent_instance_id=instance_id,
            agent_id=resolved_agent_id,
            agent_dir=str(agent_path),
            metadata=mapping_create.metadata,
            mapping_id=mapping_id,
        )
    except Exception as e:
        # Rollback: terminate the orphaned instance
        try:
            await manager.terminate_instance(instance_id)
        except Exception:
            pass  # Best effort cleanup
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to save mapping: {str(e)}"
            ).model_dump()
        )
    
    # Get the saved mapping
    saved = await asyncio.to_thread(manager._source_repository.get_instance_mapping, source_id, mapping_create.external_user_id)
    return InstanceMappingInfo(
        mapping_id=saved.mapping_id,
        source_id=saved.source_id,
        external_user_id=saved.external_user_id,
        agent_instance_id=saved.agent_instance_id,
        agent_id=saved.agent_id,
        agent_dir=saved.agent_dir,
        metadata=saved.mapping_metadata,
        last_message_at=parse_utc_datetime(saved.last_message_at),
        created_at=parse_utc_datetime(saved.created_at),
    )


# DELETE /sources/{source_id}/mappings/{mapping_id} - Delete a mapping
@router.delete("/{source_id}/mappings/{mapping_id}", response_model=DeleteResponse)
async def delete_mapping(source_id: str, mapping_id: str, request: Request):
    """Delete an instance mapping."""
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    result = await asyncio.to_thread(manager._source_repository.delete_instance_mapping, mapping_id)
    if not result.get("deleted"):
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.MAPPING_NOT_FOUND,
                message=f"Mapping not found: {mapping_id}"
            ).model_dump()
        )
    
    return DeleteResponse(deleted=True, message=f"Mapping {mapping_id} deleted")

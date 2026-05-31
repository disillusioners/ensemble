"""Source Management API endpoints."""

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from daemon.models import (
    DeleteResponse,
    ErrorCodes,
    ErrorResponse,
    SourceActionResponse,
    SourceCreate,
    SourceInfo,
    SourceListResponse,
    SourceStatus,
    SourceTestRequest,
    SourceTestResponse,
    SourceType,
    SourceUpdate,
)
from daemon.constants import MAX_CREDENTIALS_SIZE
from daemon.utils import parse_utc_datetime, validate_instance_mode

logger = logging.getLogger(__name__)

# Create router with /sources prefix
router = APIRouter(prefix="/sources", tags=["sources"])


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state."""
    return request.app.state.manager


def _get_credential_manager(request: Request) -> Any:
    """Get the CredentialManager from app state."""
    return request.app.state.credential_manager


async def _reject_scheduler_lifecycle(source_id: str, manager: Any) -> None:
    """Raise error if source is a scheduler type.
    
    Scheduler sources manage their own lifecycle automatically and cannot be
    controlled via API. This helper checks if a source is a scheduler and
    raises an HTTPException if so.
    
    Args:
        source_id: The source ID to check.
        manager: The InstanceManager instance.
        
    Raises:
        HTTPException: If the source is a scheduler type.
    """
    source = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if source and source.source_type == "scheduler":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED",
                "message": "Scheduler sources manage their own lifecycle and cannot be controlled via API."
            }
        )


def _source_to_info(source) -> SourceInfo:
    """Convert source config to SourceInfo response model."""
    return SourceInfo(
        source_id=source.source_id,
        source_type=SourceType(source.source_type),
        name=source.name,
        config=source.config,
        enabled=source.enabled,
        status=SourceStatus(source.status),
        error_message=source.error_message,
        created_at=parse_utc_datetime(source.created_at),
        updated_at=parse_utc_datetime(source.updated_at),
        has_credentials=bool(source.credentials),
    )


# ==================== Endpoints ====================


@router.get("", response_model=SourceListResponse)
async def list_sources(request: Request):
    """List all configured message sources."""
    manager = _get_manager(request)
    sources_data = await asyncio.to_thread(manager._source_repository.list_source_configs)
    sources = [_source_to_info(src) for src in sources_data]
    return SourceListResponse(sources=sources)


@router.post("", response_model=SourceInfo, status_code=201)
async def create_source(source_create: SourceCreate, request: Request):
    """Create a new message source."""
    manager = _get_manager(request)
    credential_manager = _get_credential_manager(request)
    
    # Check if source already exists
    existing = await asyncio.to_thread(manager._source_repository.get_source_config, source_create.source_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_ALREADY_EXISTS,
                message=f"Source already exists: {source_create.source_id}"
            ).model_dump()
        )
    
    # Validate source type is supported
    supported_types = {"telegram", "webhook", "whatsapp", "discord", "scheduler", "slack"}
    if source_create.source_type.value not in supported_types:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_TYPE_NOT_SUPPORTED,
                message=f"Source type not supported: {source_create.source_type}. Supported: {supported_types}"
            ).model_dump()
        )
    
    # For scheduler sources, validate instance_mode in config
    instance_mode = source_create.config.get("instance_mode")
    validated = validate_instance_mode(
        instance_mode=instance_mode,
        config=source_create.config
    )
    final_config = {**source_create.config, **validated}
    
    # If instance_mode is reuse_instance, enforce max_concurrent = 1
    if final_config.get("instance_mode") == "reuse_instance":
        current_max = final_config.get("max_concurrent")
        if current_max is not None and current_max != 1:
            logger.info(f"Adjusting max_concurrent from {current_max} to 1 for reuse_instance mode")
            final_config["max_concurrent"] = 1
    
    # Validate and encrypt credentials
    credentials_json = None
    if source_create.credentials:
        # Validate credentials size
        cred_str = json.dumps(source_create.credentials)
        if len(cred_str) > MAX_CREDENTIALS_SIZE:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    code=ErrorCodes.INVALID_REQUEST,
                    message=f"Credentials too large (max {MAX_CREDENTIALS_SIZE} bytes)"
                ).model_dump()
            )
        credentials_json = credential_manager.encrypt(source_create.credentials)
    
    # Create source config using repository
    source = await asyncio.to_thread(
        manager._source_repository.create_source_config,
        source_type=source_create.source_type.value,
        name=source_create.name,
        config=final_config,
        credentials=credentials_json,
        enabled=source_create.enabled,
        source_id=source_create.source_id,
    )
    
    # Auto-start enabled sources (start adapter immediately for running daemon)
    if source.enabled:
        try:
            await manager.source_registry.start_adapter(source.source_id)
        except Exception as e:
            logger.warning(f"Failed to auto-start source {source.source_id}: {e}")
    
    return _source_to_info(source)


@router.post("/test", response_model=SourceTestResponse)
async def test_source(test_request: SourceTestRequest):
    """Test a source configuration without saving it.
    
    Validates credentials by attempting to connect to the external service.
    """
    from daemon.sources.base import SourceConfig
    
    # Create a temporary config for testing
    temp_config = SourceConfig(
        source_id="test",
        source_type=test_request.source_type.value,
        name="Test",
        config=test_request.config,
        credentials=test_request.credentials,
        enabled=True,
    )
    
    # Get the appropriate adapter class
    if test_request.source_type == SourceType.telegram:
        from daemon.sources.adapters.telegram import TelegramAdapter
        success, message = await TelegramAdapter.test_connection(temp_config)
    elif test_request.source_type == SourceType.webhook:
        # Webhook doesn't require external connection test
        success, message = True, "Webhook sources don't require connection testing"
    elif test_request.source_type == SourceType.whatsapp:
        # WhatsApp not implemented yet
        success, message = False, "WhatsApp adapter not yet implemented"
    elif test_request.source_type == SourceType.discord:
        # Discord not implemented yet
        success, message = False, "Discord adapter not yet implemented"
    elif test_request.source_type == SourceType.scheduler:
        # Scheduler doesn't require external connection test
        success, message = True, "Scheduler sources don't require connection testing"
    elif test_request.source_type == SourceType.slack:
        from daemon.sources.adapters.slack import SlackAdapter
        success, message = await SlackAdapter.test_connection(temp_config)
    else:
        success, message = False, f"Unknown source type: {test_request.source_type}"
    
    return SourceTestResponse(success=success, message=message)


@router.get("/{source_id}", response_model=SourceInfo)
async def get_source(source_id: str, request: Request):
    """Get a specific message source."""
    manager = _get_manager(request)
    
    source = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    return _source_to_info(source)


@router.put("/{source_id}", response_model=SourceInfo)
async def update_source(source_id: str, source_update: SourceUpdate, request: Request):
    """Update a message source configuration."""
    manager = _get_manager(request)
    credential_manager = _get_credential_manager(request)
    
    existing = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if not existing:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    # Scheduler sources cannot be enabled/disabled
    if existing.source_type == "scheduler" and source_update.enabled is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED",
                "message": "Scheduler sources manage their own lifecycle and cannot be controlled via API."
            }
        )
    
    # Merge updates
    updated_name = source_update.name if source_update.name is not None else existing.name
    updated_config = source_update.config if source_update.config is not None else existing.config
    updated_enabled = source_update.enabled if source_update.enabled is not None else existing.enabled
    
    # Handle credentials separately (dict from request vs encrypted string from DB)
    credentials_json = None
    if source_update.credentials is not None:
        # New credentials provided - validate and encrypt
        if source_update.credentials:  # Non-empty dict
            cred_str = json.dumps(source_update.credentials)
            if len(cred_str) > MAX_CREDENTIALS_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=ErrorResponse(
                        code=ErrorCodes.INVALID_REQUEST,
                        message=f"Credentials too large (max {MAX_CREDENTIALS_SIZE} bytes)"
                    ).model_dump()
                )
            credentials_json = credential_manager.encrypt(source_update.credentials)
        # else: empty dict means clear credentials (credentials_json stays None)
    else:
        # Keep existing encrypted credentials
        credentials_json = existing.credentials
    
    # Update source config using repository
    updated = await asyncio.to_thread(
        manager._source_repository.update_source_config,
        source_id=source_id,
        source_type=existing.source_type,
        name=updated_name,
        config=updated_config,
        credentials=credentials_json,
        enabled=updated_enabled,
    )
    
    return _source_to_info(updated)


@router.delete("/{source_id}", response_model=DeleteResponse)
async def delete_source(source_id: str, request: Request):
    """Delete a message source."""
    manager = _get_manager(request)
    
    # Get source to check type first
    existing = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if not existing:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    # Stop and unregister adapter if running
    try:
        adapter = manager.source_registry.get(source_id)
        if adapter:
            await manager.source_registry.stop_adapter(source_id)
            manager.source_registry.unregister(source_id)
            logger.info(f"Stopped and unregistered adapter: {source_id}")
    except Exception as e:
        logger.warning(f"Failed to stop adapter during delete {source_id}: {e}")
    
    # Delete from database
    await asyncio.to_thread(manager._source_repository.delete_source_config, source_id)
    
    return DeleteResponse(deleted=True, message=f"Source {source_id} deleted")


@router.post("/{source_id}/start", response_model=SourceActionResponse)
async def start_source(source_id: str, request: Request):
    """Start a message source adapter."""
    from daemon.sources.base import SourceConfig
    
    manager = _get_manager(request)
    credential_manager = _get_credential_manager(request)
    
    source = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    # Reject lifecycle operations for scheduler sources
    await _reject_scheduler_lifecycle(source_id, manager)
    
    if not source.enabled:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Source {source_id} is disabled. Enable it first."
            ).model_dump()
        )
    
    # Check if registry has the source
    if manager.source_registry:
        try:
            # Check if adapter is already registered
            existing_adapter = manager.source_registry.get(source_id)
            
            if existing_adapter is None:
                # Create adapter from config
                source_type = source.source_type
                credentials = source.credentials
                
                # Decrypt credentials if encrypted
                if credentials and isinstance(credentials, str):
                    credentials = credential_manager.decrypt(credentials)
                
                config = SourceConfig(
                    source_id=source.source_id,
                    source_type=source_type,
                    name=source.name,
                    config=source.config or {},
                    credentials=credentials,
                    enabled=source.enabled,
                )
                
                # Create the appropriate adapter
                if source_type == "telegram":
                    from daemon.sources.adapters.telegram import TelegramAdapter
                    # Create callback wrapper that includes source_id
                    async def on_message(msg):
                        await manager.source_registry._handle_message(source_id, msg)
                    adapter = TelegramAdapter(config, on_message)
                elif source_type == "slack":
                    from daemon.sources.adapters.slack import SlackAdapter
                    async def on_message(msg):
                        await manager.source_registry._handle_message(source_id, msg)
                    adapter = SlackAdapter(config, on_message)
                    adapter._source_repo = manager._source_repository
                else:
                    raise ValueError(f"Source type '{source_type}' adapter not yet implemented")
                
                # Register the adapter
                manager.source_registry.register(adapter)
            
            # Start the adapter
            await manager.source_registry.start_adapter(source_id)
            await asyncio.to_thread(manager._source_repository.update_source_status, source_id, "running")
            return SourceActionResponse(
                source_id=source_id,
                status=SourceStatus.running,
                message=f"Source {source_id} started successfully"
            )
        except Exception as e:
            await asyncio.to_thread(manager._source_repository.update_source_status, source_id, "error", str(e))
            return SourceActionResponse(
                source_id=source_id,
                status=SourceStatus.error,
                message=f"Failed to start source: {str(e)}"
            )
    
    return SourceActionResponse(
        source_id=source_id,
        status=SourceStatus.stopped,
        message="Source registry not available"
    )


@router.post("/{source_id}/stop", response_model=SourceActionResponse)
async def stop_source(source_id: str, request: Request):
    """Stop a message source adapter."""
    manager = _get_manager(request)
    
    source = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    # Reject lifecycle operations for scheduler sources
    await _reject_scheduler_lifecycle(source_id, manager)
    
    # Check if registry has the source
    if manager.source_registry:
        try:
            await manager.source_registry.stop_adapter(source_id)
            await asyncio.to_thread(manager._source_repository.update_source_status, source_id, "stopped")
            return SourceActionResponse(
                source_id=source_id,
                status=SourceStatus.stopped,
                message=f"Source {source_id} stopped successfully"
            )
        except Exception as e:
            await asyncio.to_thread(manager._source_repository.update_source_status, source_id, "error", str(e))
            return SourceActionResponse(
                source_id=source_id,
                status=SourceStatus.error,
                message=f"Failed to stop source: {str(e)}"
            )
    
    await asyncio.to_thread(manager._source_repository.update_source_status, source_id, "stopped")
    return SourceActionResponse(
        source_id=source_id,
        status=SourceStatus.stopped,
        message=f"Source {source_id} marked as stopped"
    )

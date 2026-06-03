"""Webhook Receiver API endpoints."""

import asyncio
import logging
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from daemon.models import ErrorResponse, ErrorCodes

logger = logging.getLogger(__name__)

# Create router with /webhooks prefix
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state."""
    return request.app.state.manager


# POST /webhooks/{source_id} - Receive webhook from external source
@router.post("/{source_id}")
async def receive_webhook(source_id: str, request: Request) -> dict:
    """Receive a webhook from an external message source.
    
    Args:
        source_id: The source identifier.
        request: FastAPI request object.
        
    Returns:
        Dictionary with received status.
        
    Raises:
        HTTPException: If source not found, invalid, or processing fails.
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    # Check source exists (use asyncio.to_thread like other routers)
    source = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    # Check source type is webhook-compatible
    if source.source_type not in ("webhook", "telegram"):
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Source type {source.source_type} does not support webhooks"
            ).model_dump()
        )
    
    # Verify webhook secret if configured
    source_config = source.config or {}
    configured_secret = source_config.get("webhook_secret")

    if configured_secret:
        provided_secret = request.headers.get("X-Webhook-Secret")
        if not provided_secret or not secrets.compare_digest(provided_secret, configured_secret):
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    code=ErrorCodes.INVALID_REQUEST,
                    message="Invalid or missing webhook secret"
                ).model_dump()
            )

    # Get the adapter from registry
    if not manager.source_registry:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message="Source registry not available"
            ).model_dump()
        )
    
    adapter = manager.source_registry.get(source_id)
    if not adapter:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Source adapter not running: {source_id}"
            ).model_dump()
        )
    
    # Check if adapter supports webhooks
    if not hasattr(adapter, 'handle_webhook'):
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message="Source adapter does not support webhooks"
            ).model_dump()
        )
    
    # Parse the webhook payload
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Invalid JSON payload: {str(e)}"
            ).model_dump()
        )
    
    # Get headers
    headers = dict(request.headers)
    
    # Forward to adapter
    try:
        await adapter.handle_webhook(payload, headers)
        return {"received": True, "source_id": source_id}
    except Exception as e:
        # Log but don't expose internal errors
        logger.error(f"Webhook processing error for {source_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message="Webhook processing failed"
            ).model_dump()
        )

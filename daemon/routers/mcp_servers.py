"""MCP Server Management API endpoints."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from daemon.models import (
    McpServerCreate,
    McpServerInfo,
    McpServerListResponse,
    McpServerUpdate,
    McpServerDeleteResponse,
    ErrorResponse,
    ErrorCodes,
)
from daemon.mcp.config import validate_mcp_server_config
from daemon.utils import parse_utc_datetime

logger = logging.getLogger(__name__)

# Create router with /mcp-servers prefix
router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state."""
    return request.app.state.manager


def _mcp_server_to_info(mcp_server) -> McpServerInfo:
    """Convert McpServer model to McpServerInfo response model."""
    return McpServerInfo(
        id=mcp_server.id,
        name=mcp_server.name,
        description=mcp_server.description,
        config=mcp_server.config,
        is_active=mcp_server.is_active,
        created_at=parse_utc_datetime(mcp_server.created_at),
        updated_at=parse_utc_datetime(mcp_server.updated_at) if mcp_server.updated_at else None,
    )


# ==================== Endpoints ====================


@router.get("", response_model=McpServerListResponse)
async def list_mcp_servers(request: Request):
    """List all MCP servers."""
    manager = _get_manager(request)
    mcp_servers_data = await asyncio.to_thread(
        manager._mcp_server_repository.list_mcp_servers
    )
    mcp_servers = [_mcp_server_to_info(srv) for srv in mcp_servers_data]
    return McpServerListResponse(mcp_servers=mcp_servers)


@router.post("", response_model=McpServerInfo, status_code=201)
async def create_mcp_server(mcp_server_create: McpServerCreate, request: Request):
    """Create a new MCP server."""
    manager = _get_manager(request)

    # Validate MCP server config
    try:
        validate_mcp_server_config(mcp_server_create.config)
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=str(e)
            ).model_dump()
        )

    # Check if server with same name already exists
    existing = await asyncio.to_thread(
        manager._mcp_server_repository.get_mcp_server_by_name,
        mcp_server_create.name
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                code=ErrorCodes.MCP_SERVER_ALREADY_EXISTS,  # Reuse existing error code
                message=f"MCP server with name already exists: {mcp_server_create.name}"
            ).model_dump()
        )

    # Create MCP server
    mcp_server = await asyncio.to_thread(
        manager._mcp_server_repository.create_mcp_server,
        name=mcp_server_create.name,
        description=mcp_server_create.description,
        config=mcp_server_create.config,
        is_active=mcp_server_create.is_active,
    )

    return _mcp_server_to_info(mcp_server)


@router.get("/{server_id}", response_model=McpServerInfo)
async def get_mcp_server(server_id: str, request: Request):
    """Get a specific MCP server."""
    manager = _get_manager(request)

    mcp_server = await asyncio.to_thread(
        manager._mcp_server_repository.get_mcp_server,
        server_id
    )
    if not mcp_server:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.MCP_SERVER_NOT_FOUND,  # Reuse existing error code
                message=f"MCP server not found: {server_id}"
            ).model_dump()
        )

    return _mcp_server_to_info(mcp_server)


@router.put("/{server_id}", response_model=McpServerInfo)
async def update_mcp_server(
    server_id: str,
    mcp_server_update: McpServerUpdate,
    request: Request
):
    """Update an MCP server configuration."""
    manager = _get_manager(request)

    # Check if server exists
    existing = await asyncio.to_thread(
        manager._mcp_server_repository.get_mcp_server,
        server_id
    )
    if not existing:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.MCP_SERVER_NOT_FOUND,
                message=f"MCP server not found: {server_id}"
            ).model_dump()
        )

    # Validate MCP server config if provided
    if mcp_server_update.config is not None:
        try:
            validate_mcp_server_config(mcp_server_update.config)
        except ValueError as e:
            raise HTTPException(
                status_code=422,
                detail=ErrorResponse(
                    code=ErrorCodes.INVALID_REQUEST,
                    message=str(e)
                ).model_dump()
            )

    # If updating name, check for conflicts
    if mcp_server_update.name is not None and mcp_server_update.name != existing.name:
        name_conflict = await asyncio.to_thread(
            manager._mcp_server_repository.get_mcp_server_by_name,
            mcp_server_update.name
        )
        if name_conflict:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    code=ErrorCodes.MCP_SERVER_ALREADY_EXISTS,
                    message=f"MCP server with name already exists: {mcp_server_update.name}"
                ).model_dump()
            )

    # Update MCP server
    updated = await asyncio.to_thread(
        manager._mcp_server_repository.update_mcp_server,
        server_id,
        name=mcp_server_update.name,
        description=mcp_server_update.description,
        config=mcp_server_update.config,
        is_active=mcp_server_update.is_active,
    )

    return _mcp_server_to_info(updated)


@router.delete("/{server_id}", response_model=McpServerDeleteResponse)
async def delete_mcp_server(server_id: str, request: Request):
    """Delete an MCP server."""
    manager = _get_manager(request)

    # Check if server exists
    existing = await asyncio.to_thread(
        manager._mcp_server_repository.get_mcp_server,
        server_id
    )
    if not existing:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.MCP_SERVER_NOT_FOUND,
                message=f"MCP server not found: {server_id}"
            ).model_dump()
        )

    # Delete MCP server
    result = await asyncio.to_thread(
        manager._mcp_server_repository.delete_mcp_server,
        server_id
    )

    return McpServerDeleteResponse(deleted=result["deleted"], id=server_id)

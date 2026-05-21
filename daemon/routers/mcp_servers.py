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
    ConfigSchemaField,
    BuiltinServerTemplate,
    BuiltinTemplateListResponse,
    BuiltinServerConfigure,
    McpServerTestConnectionRequest,
    McpServerTestConnectionResponse,
)
from daemon.mcp.config import validate_mcp_server_config, McpConfigValidationError
from daemon.mcp import get_mcp_connection_manager
from daemon.mcp.builtin_servers import get_registry
from daemon.mcp.builtin_servers.validation import validate_config_values, McpConfigValidationError as BuiltinConfigValidationError
from daemon.utils import parse_utc_datetime

logger = logging.getLogger(__name__)

# Create router with /mcp-servers prefix
router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state."""
    return request.app.state.manager


def _mcp_server_to_info(mcp_server) -> McpServerInfo:
    """Convert McpServer model to McpServerInfo response model."""
    # Parse config_schema from DB (stored as list[dict]) to list[ConfigSchemaField]
    config_schema: list[ConfigSchemaField] | None = None
    if mcp_server.config_schema:
        config_schema = [ConfigSchemaField(**field) for field in mcp_server.config_schema]

    # For built-in servers, parse config to get initial_values for form pre-fill
    initial_values: dict | None = None
    if mcp_server.is_builtin:
        registry = get_registry()
        definition = registry.get_by_name(mcp_server.name)
        if definition:
            initial_values = definition.parse_config(mcp_server.config)

    return McpServerInfo(
        id=mcp_server.id,
        name=mcp_server.name,
        description=mcp_server.description,
        config=mcp_server.config,
        is_active=mcp_server.is_active,
        is_builtin=mcp_server.is_builtin,
        config_schema=config_schema,
        config_schema_version=mcp_server.config_schema_version or "0",
        initial_values=initial_values,
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


@router.post("/test-connection", response_model=McpServerTestConnectionResponse)
async def test_mcp_server_connection(test_request: McpServerTestConnectionRequest):
    """
    Test MCP server connectivity before saving.

    Creates a temporary connection to the specified MCP server,
    verifies it responds correctly, and immediately cleans up.
    This does NOT save anything to the database.
    """
    conn_mgr = get_mcp_connection_manager()
    timeout = 15.0

    try:
        # Create a temporary session (validation + timeout handled inside create_test_session)
        session, streams_cm = await conn_mgr.create_test_session(
            test_request.config,
            timeout=timeout,
        )

        # Session created successfully — now try to list tools
        try:
            tools = await session.list_tools()
            tools_count = len(tools) if tools else 0

            # Success message
            if tools_count == 0:
                message = "Connection successful — server responded with no tools"
            elif tools_count == 1:
                message = "Connection successful — server responded with 1 tool"
            else:
                message = f"Connection successful — server responded with {tools_count} tools"

            return McpServerTestConnectionResponse(
                success=True,
                message=message,
                tools_count=tools_count,
            )
        finally:
            # Always clean up the session
            try:
                await session.stop()
            except Exception as e:
                logger.debug("session stop error: %s", e)
            try:
                await streams_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.debug("streams cleanup error: %s", e)

    except asyncio.TimeoutError:
        return McpServerTestConnectionResponse(
            success=False,
            message=f"Connection timed out after {timeout} seconds",
        )
    except ConnectionRefusedError:
        return McpServerTestConnectionResponse(
            success=False,
            message="Connection failed: connection refused",
        )
    except McpConfigValidationError as e:
        return McpServerTestConnectionResponse(
            success=False,
            message=f"Invalid configuration: {e}",
        )
    except OSError as e:
        if "ECONNREFUSED" in str(e):
            return McpServerTestConnectionResponse(
                success=False,
                message="Connection failed: connection refused",
            )
        elif "ENOENT" in str(e) or "No such file" in str(e):
            logger.warning("Connection failed: command not found — %s", e)
            return McpServerTestConnectionResponse(
                success=False,
                message="Connection failed: the specified command was not found",
            )
        else:
            logger.warning("Connection failed: %s", e)
            return McpServerTestConnectionResponse(
                success=False,
                message="Connection failed: an unexpected error occurred",
            )
    except Exception as e:
        # Log full exception for debugging, return sanitized message to user
        logger.exception("MCP connection test failed")
        return McpServerTestConnectionResponse(
            success=False,
            message="Connection failed: an unexpected error occurred",
        )


@router.post("", response_model=McpServerInfo, status_code=201)
async def create_mcp_server(mcp_server_create: McpServerCreate, request: Request):
    """Create a new MCP server."""
    manager = _get_manager(request)

    # Validate MCP server config
    try:
        validate_mcp_server_config(mcp_server_create.config)
    except McpConfigValidationError as e:
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


@router.get("/builtin-templates", response_model=BuiltinTemplateListResponse)
async def list_builtin_templates():
    """List all available built-in server templates."""
    registry = get_registry()
    templates = []
    for definition in registry.get_all():
        templates.append(BuiltinServerTemplate(
            name=definition.name,
            display_name=definition.display_name,
            description=definition.description,
            config_schema=[ConfigSchemaField(**field) for field in definition.get_config_schema()]
        ))
    return BuiltinTemplateListResponse(templates=templates)


@router.post("/configure-builtin", response_model=McpServerInfo, status_code=201)
async def configure_builtin_server(request: Request, config_request: BuiltinServerConfigure):
    """Configure a built-in MCP server from a template."""
    manager = _get_manager(request)
    registry = get_registry()

    # Look up the template definition
    definition = registry.get_by_name(config_request.template_name)
    if not definition:
        raise HTTPException(
            status_code=404,
            detail=f"Built-in server template '{config_request.template_name}' not found"
        )

    # Validate config values
    try:
        validate_config_values(definition.get_config_schema(), config_request.values)
    except BuiltinConfigValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message="Configuration validation failed",
                details={"errors": e.errors}
            ).model_dump()
        )

    # Build the final config from user values
    generated_config = definition.build_config(config_request.values)
    schema_as_dicts = definition.get_config_schema()

    # Check if server already exists
    existing = await asyncio.to_thread(
        manager._mcp_server_repository.get_mcp_server_by_name,
        definition.name
    )

    if existing:
        if not existing.is_builtin:
            raise HTTPException(
                status_code=409,
                detail=f"A user-created MCP server with name '{definition.name}' already exists"
            )
        # Update existing built-in server
        updated = await asyncio.to_thread(
            manager._mcp_server_repository.update_mcp_server,
            existing.id,
            config=generated_config
        )
        return _mcp_server_to_info(updated)
    else:
        # Create new built-in server (handle race condition)
        try:
            created = await asyncio.to_thread(
                manager._mcp_server_repository.create_mcp_server,
                name=definition.name,
                description=definition.description,
                config=generated_config,
                is_builtin=True,
                config_schema=schema_as_dicts,
                config_schema_version=definition.schema_version,
            )
            return _mcp_server_to_info(created)
        except Exception as e:
            # Handle race condition: concurrent create attempt
            if "unique" in str(e).lower() or "UNIQUE constraint" in str(e):
                raise HTTPException(
                    status_code=409,
                    detail=f"A server with name '{definition.name}' was created concurrently"
                )
            raise


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

    # Built-in servers: only allow is_active updates
    if existing.is_builtin:
        if mcp_server_update.name is not None or mcp_server_update.description is not None:
            raise HTTPException(
                status_code=403,
                detail=ErrorResponse(
                    code=ErrorCodes.BUILTIN_SERVER_PROTECTED,
                    message="Cannot modify name or description of a built-in MCP server"
                ).model_dump()
            )
        if mcp_server_update.config is not None:
            raise HTTPException(
                status_code=403,
                detail=ErrorResponse(
                    code=ErrorCodes.BUILTIN_SERVER_PROTECTED,
                    message="Cannot modify config of a built-in MCP server. Use /configure-builtin or /reset-builtin endpoints instead."
                ).model_dump()
            )

    # Validate MCP server config if provided
    if mcp_server_update.config is not None:
        try:
            validate_mcp_server_config(mcp_server_update.config)
        except McpConfigValidationError as e:
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

    # Built-in servers are protected and cannot be deleted
    if existing.is_builtin:
        raise HTTPException(
            status_code=403,
            detail=ErrorResponse(
                code=ErrorCodes.BUILTIN_SERVER_PROTECTED,
                message="Cannot delete a built-in MCP server"
            ).model_dump()
        )

    # Delete MCP server
    result = await asyncio.to_thread(
        manager._mcp_server_repository.delete_mcp_server,
        server_id
    )

    return McpServerDeleteResponse(deleted=result["deleted"], id=server_id)


@router.post("/{server_id}/reset-builtin", response_model=McpServerInfo)
async def reset_builtin_server(server_id: str, request: Request):
    """Reset a built-in MCP server to its default configuration."""
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

    if not existing.is_builtin:
        raise HTTPException(
            status_code=403,
            detail=ErrorResponse(
                code=ErrorCodes.BUILTIN_SERVER_PROTECTED,
                message="Server is not a built-in server"
            ).model_dump()
        )

    # Look up the definition in the registry
    registry = get_registry()
    definition = registry.get_by_name(existing.name)
    if not definition:
        raise HTTPException(
            status_code=404,
            detail=f"Built-in server definition '{existing.name}' not found in registry"
        )

    # Reset to default config (empty values = defaults only)
    defaults_config = definition.build_config({})

    # Update the server
    updated = await asyncio.to_thread(
        manager._mcp_server_repository.update_mcp_server,
        server_id,
        config=defaults_config
    )
    return _mcp_server_to_info(updated)

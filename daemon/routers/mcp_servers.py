"""MCP Server Management API endpoints."""

import asyncio
import copy
import logging
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from mcp.shared.exceptions import McpError

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


def _invalidate_mcp_schema_cache(manager: Any, server_name: str) -> None:
    """Invalidate the MCP service's schema cache for ``server_name``.

    Called from CRUD endpoints so a server create/update/delete forces
    a re-discovery of tool schemas on the next instance preload. No-op
    if the manager has no MCP service attached (e.g. legacy test
    fixtures that mock the manager without a service).
    """
    mcp_service = getattr(manager, "_mcp_service", None)
    if mcp_service is not None and hasattr(mcp_service, "invalidate_schema_cache"):
        mcp_service.invalidate_schema_cache(server_name)


def redact_secrets(config: dict) -> dict:
    """Return a deep copy of ``config`` with secrets redacted.

    Two surfaces are scrubbed:

    1. ``env`` sub-dict (if present and dict-like): keys whose name
       contains any of ``KEY``, ``TOKEN``, ``SECRET``, or ``PASSWORD``
       (case-insensitive substring match) have their values replaced
       with ``"[REDACTED]"``. Non-sensitive env keys such as
       ``OPENSPACE_MODEL`` and ``OPENSPACE_MCP_TRANSPORT`` are
       preserved intact.

    2. ``url`` top-level value (if present and a string): any userinfo
       (``user:pass@``) is stripped as a defense-in-depth measure in
       case a builtin server's ``build_config`` ever lets one slip
       through. Builtin servers (e.g. OpenSpace) already reject
       userinfo at build time — this layer exists so a malformed
       legacy/stored config still doesn't leak credentials over the
       API.

    The original input dict is not mutated.

    Args:
        config: MCP server config dict (typically loaded from the DB).

    Returns:
        A new dict with sensitive ``env`` values replaced by
        ``"[REDACTED]"`` and any URL userinfo stripped.
    """
    redacted = copy.deepcopy(config)
    env = redacted.get("env")
    if isinstance(env, dict):
        for env_key in list(env.keys()):
            if not isinstance(env_key, str):
                continue
            upper_key = env_key.upper()
            if any(marker in upper_key for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                env[env_key] = "[REDACTED]"

    # Defense-in-depth: strip userinfo from ``config["url"]`` if present.
    # Builtin build_config() should reject this upstream; this guards
    # against legacy/stored configs that pre-date the validation.
    url = redacted.get("url")
    if isinstance(url, str) and url:
        parsed_url = urlparse(url)
        if "@" in parsed_url.netloc:
            host_port = parsed_url.netloc.split("@", 1)[-1]
            redacted["url"] = parsed_url._replace(netloc=host_port).geturl()

    return redacted


def _mcp_server_to_info(mcp_server) -> McpServerInfo:
    """Convert McpServer model to McpServerInfo response model."""
    # Parse config_schema from DB (stored as list[dict]) to list[ConfigSchemaField]
    config_schema: list[ConfigSchemaField] | None = None
    if mcp_server.config_schema:
        config_schema = [ConfigSchemaField(**field) for field in mcp_server.config_schema]

    # Preserve the original (unredacted) DB config for form pre-fill so that
    # parse_config() can recover real values before we redact secrets for
    # the response payload.
    original_config = mcp_server.config or {}

    # For built-in servers, parse config to get initial_values for form pre-fill.
    # Note: parse_config only recovers schema-defined fields. Injected env vars
    # (credentials, OPENSPACE_MCP_TRANSPORT) live in the stored env dict but are
    # not part of the schema, so they will NOT appear in initial_values on
    # round-trip — this is intentional, those values are sourced from the
    # runtime environment rather than user input.
    initial_values: dict | None = None
    if mcp_server.is_builtin:
        registry = get_registry()
        definition = registry.get_by_name(mcp_server.name)
        if definition:
            initial_values = definition.parse_config(original_config)

    # Redact env secrets before exposing config to the client. This applies to
    # every API response path (list / get / create / update / configure-builtin
    # / reset-builtin) so credentials never leak over HTTP.
    safe_config = redact_secrets(original_config)

    return McpServerInfo(
        id=mcp_server.id,
        name=mcp_server.name,
        description=mcp_server.description,
        config=safe_config,
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
            tools_count = len(tools.tools) if tools and tools.tools else 0

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
    except McpError as e:
        # MCP protocol errors (e.g., "Session terminated", "Invalid request")
        # Extract error message from e.error.message if available
        error_message = "Unknown MCP error"
        if hasattr(e, 'error') and e.error is not None:
            if hasattr(e.error, 'message') and e.error.message:
                error_message = e.error.message
            else:
                error_message = str(e.error)
        else:
            error_message = str(e)
        logger.warning("MCP server error during test connection: %s", error_message)
        return McpServerTestConnectionResponse(
            success=False,
            message=f"Server error: {error_message}",
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
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

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

    # New server → its schema isn't cached yet, but invalidate to
    # be safe in case of a re-used name and to drop any pool-side
    # tool discovery cache that mentions the same name.
    _invalidate_mcp_schema_cache(manager, mcp_server.name)

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
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
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

    # Build the final config from user values. ``build_config()`` may
    # raise ``McpConfigValidationError`` (e.g. OpenSpace rejects a
    # ``ftp://`` URL or embedded userinfo) — translate that into a 422
    # rather than letting it surface as an opaque 500. ``BuiltinConfigValidationError``
    # (from ``validate_config_values`` above) is also caught here as a
    # safety net for any future builtin whose build_config re-validates.
    try:
        generated_config = definition.build_config(config_request.values)
    except BuiltinConfigValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message="Configuration validation failed",
                details={"errors": e.errors}
            ).model_dump()
        )
    except McpConfigValidationError as e:
        # McpConfigValidationError is a ValueError subclass — catch it
        # explicitly so it doesn't bubble up as a 500. Some builtins
        # raise it from build_config() for env-driven checks (e.g.
        # ENS_OPENSPACE_REMOTE_URL scheme / userinfo).
        raise HTTPException(
            status_code=422,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=str(e)
            ).model_dump()
        )
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
        # Config changed → drop the schema cache entry.
        _invalidate_mcp_schema_cache(manager, updated.name)
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
            # First-time build of this built-in server — make sure
            # the schema cache doesn't carry a stale empty entry.
            _invalidate_mcp_schema_cache(manager, created.name)
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
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

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

    # Invalidate the schema cache for the server's name. When the
    # name changed, drop BOTH the old and the new entries.
    _invalidate_mcp_schema_cache(manager, updated.name)
    if existing.name != updated.name:
        _invalidate_mcp_schema_cache(manager, existing.name)

    return _mcp_server_to_info(updated)


@router.delete("/{server_id}", response_model=McpServerDeleteResponse)
async def delete_mcp_server(server_id: str, request: Request):
    """Delete an MCP server."""
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

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

    # Drop the deleted server's schema cache entry so the next
    # preload doesn't try to look it up.
    _invalidate_mcp_schema_cache(manager, existing.name)

    return McpServerDeleteResponse(deleted=result["deleted"], id=server_id)


@router.post("/{server_id}/reset-builtin", response_model=McpServerInfo)
async def reset_builtin_server(server_id: str, request: Request):
    """Reset a built-in MCP server to its default configuration."""
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

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
    # Defaults changed → drop the schema cache entry.
    _invalidate_mcp_schema_cache(manager, updated.name)
    return _mcp_server_to_info(updated)

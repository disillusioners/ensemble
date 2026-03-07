import warnings

# Suppress langchain Pydantic V1 compatibility warning on Python 3.14+
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible with Python 3.14",
    category=UserWarning,
)

import time
import logging
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse
from typing import AsyncGenerator
from pathlib import Path
import os
import secrets

# Configure logging for daemon modules
# This ensures our logs are visible when running via uvicorn
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)

logger = logging.getLogger(__name__)

from .models import (
    SessionCreate,
    SessionInfo,
    MessageCreate,
    MessageResponse,
    ErrorResponse,
    ErrorCodes,
    SessionStatus,
    HealthResponse,
    SessionListResponse,
    AgentInfo,
    AgentListResponse,
    AgentCreate,
    # Source models
    SourceCreate,
    SourceUpdate,
    SourceInfo,
    SourceListResponse,
    SourceActionResponse,
    SourceStatus,
    SourceType,
    SourceTestRequest,
    SourceTestResponse,
    # Mapping models
    SessionMappingCreate,
    SessionMappingInfo,
    SessionMappingListResponse,
    DeleteResponse,
)
from .manager import SessionManager
from .config import Config, load_config
from .events import event_to_sse
from .sources.credentials import CredentialManager


def validate_agent_dir(agent_dir: str) -> Path:
    """Validate agent_dir is within allowed base directory.
    
    Args:
        agent_dir: Relative path to agent directory (e.g., "./agents/my-agent")
        
    Returns:
        Resolved absolute Path if valid.
        
    Raises:
        HTTPException: If path is invalid or outside allowed directory.
    """
    base_dir = Path(__file__).parent.parent  # project root
    agents_base = (base_dir / "agents").resolve()
    
    # Handle relative paths like "./agents/my-agent"
    if agent_dir.startswith("./"):
        agent_path = (base_dir / agent_dir).resolve()
    else:
        agent_path = (agents_base / agent_dir).resolve()
    
    # Check path is within agents directory
    try:
        agent_path.relative_to(agents_base)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Invalid agent_dir: must be within agents directory"
            ).model_dump()
        )

    # Check for symlink attack
    if agent_path.is_symlink():
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message="Symlinks not allowed in agent paths"
            ).model_dump()
        )

    # Check agent exists
    if not agent_path.exists():
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Agent directory not found: {agent_dir}"
            ).model_dump()
        )
    
    return agent_path


# Determine the base path
BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

# Max size in bytes for credentials JSON
MAX_CREDENTIALS_SIZE = 4096

# Global state
manager: SessionManager = None
start_time: float = None
credential_manager = CredentialManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager, start_time
    config = load_config()
    manager = SessionManager(config)
    await manager.initialize()  # Initialize async checkpointer within async context
    # Set the main event loop for thread-safe broadcasting
    manager.broadcaster.set_main_loop(asyncio.get_running_loop())
    start_time = time.time()
    
    # Start message sources (loads adapters from DB and starts them)
    await manager.start_sources()
    
    yield
    
    # Stop message sources on shutdown
    await manager.stop_sources()


app = FastAPI(
    title="Ensemble Daemon",
    version="0.1.0",
    lifespan=lifespan
)

# Add CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            code=ErrorCodes.INTERNAL_ERROR,
            message=str(exc)
        ).model_dump()
    )


# 1. GET /health - Health check
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(
        status="healthy",
        uptime_seconds=time.time() - start_time,
        version="0.1.0"
    )


# 1.5. GET /agents - List available agents
@app.get("/agents", response_model=AgentListResponse)
async def list_agents():
    """List all available agents by scanning the agents directory."""
    import json
    
    agents_dir = BASE_DIR / "agents"
    agents = []
    
    if agents_dir.exists():
        for agent_path in sorted(agents_dir.iterdir()):
            # Skip hidden directories (starting with .) and non-directories
            if not agent_path.is_dir() or agent_path.name.startswith("."):
                continue
            
            # Skip special internal directories that aren't agents
            if agent_path.name in ("_trash", "_baby_template"):
                continue
            
            meta_path = agent_path / "meta.json"
            if meta_path.exists():
                try:
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                    
                    agents.append(AgentInfo(
                        id=meta.get("id", agent_path.name),
                        name=meta.get("name", agent_path.name.title()),
                        description=meta.get("description", ""),
                        icon=meta.get("icon", "🤖"),
                        color=meta.get("color", "accent-blue"),
                        version=meta.get("version"),
                        agent_dir=f"./agents/{agent_path.name}",
                        system=meta.get("system", False),
                    ))
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"Failed to load meta.json for {agent_path.name}: {e}")
    
    return AgentListResponse(agents=agents)


# 1.6. POST /agents - Create new agent
@app.post("/agents", response_model=AgentInfo, status_code=201)
async def create_agent(agent_create: AgentCreate):
    """Create a new agent from template."""
    import json
    import shutil
    
    agents_dir = BASE_DIR / "agents"
    template_dir = BASE_DIR / "agents" / "_baby_template"
    new_agent_dir = agents_dir / agent_create.id
    
    # Validate ID
    if not agent_create.id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message="Agent ID must contain only alphanumeric characters, hyphens, and underscores"
            ).model_dump()
        )
    
    # Check if agent already exists
    if new_agent_dir.exists():
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Agent with ID '{agent_create.id}' already exists"
            ).model_dump()
        )
    
    # Check template exists
    if not template_dir.exists():
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message="Agent template not found"
            ).model_dump()
        )
    
    try:
        # Create agent directory
        new_agent_dir.mkdir(parents=True, exist_ok=True)
        
        # Copy template files (exclude history and memories directories)
        for item in template_dir.iterdir():
            if item.name in ("history", "memories"):
                continue
            if item.is_file():
                shutil.copy2(item, new_agent_dir / item.name)
        
        # Create meta.json
        meta = {
            "id": agent_create.id,
            "name": agent_create.name,
            "description": agent_create.description,
            "icon": agent_create.icon,
            "color": agent_create.color,
            "version": "1.0.0"
        }
        
        with open(new_agent_dir / "meta.json", "w") as f:
            json.dump(meta, f, indent=2)
        
        # Create empty directories
        (new_agent_dir / "history").mkdir(exist_ok=True)
        (new_agent_dir / "memories").mkdir(exist_ok=True)
        
        return AgentInfo(
            id=agent_create.id,
            name=agent_create.name,
            description=agent_create.description,
            icon=agent_create.icon,
            color=agent_create.color,
            version="1.0.0",
            agent_dir=f"./agents/{agent_create.id}",
        )
    except Exception as e:
        # Cleanup on failure
        if new_agent_dir.exists():
            shutil.rmtree(new_agent_dir)
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to create agent: {str(e)}"
            ).model_dump()
        )


# 1.7. DELETE /agents/{agent_id} - Move agent to trash
@app.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str):
    """Move an agent to trash (soft delete)."""
    import shutil
    from datetime import datetime
    
    agents_dir = BASE_DIR / "agents"
    agent_dir = agents_dir / agent_id
    trash_dir = agents_dir / "_trash"
    
    # Check agent exists
    if not agent_dir.exists():
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Agent not found: {agent_id}"
            ).model_dump()
        )
    
    # Don't allow deleting internal directories
    if agent_id.startswith("_"):
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message="Cannot delete internal agents"
            ).model_dump()
        )
    
    try:
        # Create trash directory if needed
        trash_dir.mkdir(exist_ok=True)
        
        # Generate unique name with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        trashed_name = f"{agent_id}_{timestamp}"
        trashed_path = trash_dir / trashed_name
        
        # If target already exists, add suffix
        suffix = 1
        while trashed_path.exists():
            trashed_path = trash_dir / f"{trashed_name}_{suffix}"
            suffix += 1
        
        # Move agent to trash
        shutil.move(str(agent_dir), str(trashed_path))
        
        return {"deleted": True, "agent_id": agent_id, "trashed_as": trashed_path.name}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to delete agent: {str(e)}"
            ).model_dump()
        )


# 2. POST /sessions - Spawn session
@app.post("/sessions", response_model=SessionInfo, status_code=201)
async def create_session(session_create: SessionCreate):
    """Spawn a new session."""
    try:
        session_id = manager.spawn_session(
            agent_dir=session_create.agent_dir,
            session_id=session_create.session_id,
        )
    except ValueError as e:
        error_msg = str(e)
        if "Max sessions limit" in error_msg:
            raise HTTPException(
                status_code=429,
                detail=ErrorResponse(
                    code=ErrorCodes.MAX_SESSIONS_EXCEEDED,
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

    # Get session info from database
    session_meta = manager.get_session_info(session_id)
    return SessionInfo(
        session_id=session_meta["session_id"],
        agent_dir=session_meta["agent_dir"],
        status=SessionStatus(session_meta["status"]),
        parent_id=session_meta.get("parent_id"),
        children=session_meta.get("children", []),
        created_at=datetime.fromisoformat(session_meta["created_at"]).replace(tzinfo=timezone.utc) if isinstance(session_meta["created_at"], str) else session_meta["created_at"],
        updated_at=datetime.fromisoformat(session_meta["updated_at"]).replace(tzinfo=timezone.utc) if session_meta.get("updated_at") and isinstance(session_meta["updated_at"], str) else session_meta.get("updated_at"),
    )


# 3. GET /sessions - List sessions
@app.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    limit: int = 100,
    offset: int = 0
):
    """List sessions with pagination.
    
    Args:
        limit: Maximum number of sessions to return (default: 100, max: 100).
        offset: Number of sessions to skip (default: 0, min: 0).
    """
    # Input validation
    limit = max(1, min(limit, 100))  # Clamp to 1-100
    offset = max(0, offset)  # Ensure non-negative
    
    sessions_data, total = manager.list_sessions(limit=limit, offset=offset)
    sessions = []
    for sess in sessions_data:
        sessions.append(SessionInfo(
            session_id=sess["session_id"],
            agent_dir=sess["agent_dir"],
            status=SessionStatus(sess["status"]),
            parent_id=sess.get("parent_id"),
            children=sess.get("children", []),
            title=sess.get("title"),
            created_at=datetime.fromisoformat(sess["created_at"]).replace(tzinfo=timezone.utc) if isinstance(sess["created_at"], str) else sess["created_at"],
            updated_at=datetime.fromisoformat(sess["updated_at"]).replace(tzinfo=timezone.utc) if sess.get("updated_at") and isinstance(sess["updated_at"], str) else sess.get("updated_at"),
        ))
    
    has_more = (offset + limit) < total
    
    return SessionListResponse(
        sessions=sessions,
        total=total,
        limit=limit,
        offset=offset,
        has_more=has_more
    )


# 4. GET /sessions/{session_id} - Get session info
@app.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str):
    """Get session information."""
    try:
        session_meta = manager.get_session_info(session_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SESSION_NOT_FOUND,
                message=f"Session not found: {session_id}"
            ).model_dump()
        )

    return SessionInfo(
        session_id=session_meta["session_id"],
        agent_dir=session_meta["agent_dir"],
        status=SessionStatus(session_meta["status"]),
        parent_id=session_meta.get("parent_id"),
        children=session_meta.get("children", []),
        title=session_meta.get("title"),
        created_at=datetime.fromisoformat(session_meta["created_at"]) if isinstance(session_meta["created_at"], str) else session_meta["created_at"],
        updated_at=datetime.fromisoformat(session_meta["updated_at"]) if session_meta.get("updated_at") and isinstance(session_meta["updated_at"], str) else session_meta.get("updated_at"),
    )


# 5. DELETE /sessions/{session_id} - Terminate session
@app.delete("/sessions/{session_id}")
async def terminate_session(session_id: str):
    """Terminate a session."""
    # Check session exists
    try:
        manager.get_session(session_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SESSION_NOT_FOUND,
                message=f"Session not found: {session_id}"
            ).model_dump()
        )

    manager.terminate_session(session_id)
    
    return {"terminated": True}


# 6. POST /sessions/{session_id}/messages - Send message
@app.post("/sessions/{session_id}/messages", response_model=MessageResponse)
async def send_message(session_id: str, message: MessageCreate):
    """Send a message to a session (async via queue)."""
    # Check session exists
    try:
        manager.get_session(session_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SESSION_NOT_FOUND,
                message=f"Session not found: {session_id}"
            ).model_dump()
        )

    # Enqueue the message (non-blocking)
    try:
        result = await manager.enqueue_message(
            session_id=session_id,
            message=message.content,
            source="api"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to enqueue message: {str(e)}"
            ).model_dump()
        )

    # Create response with queued status
    now = datetime.now(timezone.utc)

    return MessageResponse(
        message_id=result.message_id,
        role="assistant",
        content="",  # Response will come async
        thinking=None,
        thinking_extracted=None,
        tool_calls=None,
        created_at=now,
    )


# 7. GET /sessions/{session_id}/messages/{message_id} - Get message status
@app.get("/sessions/{session_id}/messages/{message_id}")
async def get_message_status(session_id: str, message_id: str):
    """Get the status of a queued message."""
    # Check session exists
    try:
        manager.get_session(session_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SESSION_NOT_FOUND,
                message=f"Session not found: {session_id}"
            ).model_dump()
        )
    
    # Get queue stats
    stats = manager.get_queue_stats(session_id)
    
    return {
        "message_id": message_id,
        "session_id": session_id,
        "queue_stats": {
            "pending_count": stats.pending_count,
            "processing_count": stats.processing_count,
            "oldest_message_age_seconds": stats.oldest_message_age_seconds,
        }
    }


# 8. GET /sessions/{session_id}/messages - Get message history
@app.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    """Get message history for a session."""
    # Check session exists
    try:
        manager.get_session(session_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SESSION_NOT_FOUND,
                message=f"Session not found: {session_id}"
            ).model_dump()
        )

    # Get message history from LangGraph checkpoints
    return await manager.get_messages(session_id)


# 9. GET /sessions/{session_id}/events - SSE stream
@app.get("/sessions/{session_id}/events")
async def stream_events(session_id: str, request: Request):
    """SSE stream for session events."""
    # Check session exists
    try:
        manager.get_session(session_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SESSION_NOT_FOUND,
                message=f"Session not found: {session_id}"
            ).model_dump()
        )

    broadcaster = manager.broadcaster

    async def event_generator() -> AsyncGenerator[dict, None]:
        """Generate SSE events for the session."""
        import asyncio
        import json
        
        try:
            # Send initial connection event
            yield {"event": "connected", "data": json.dumps({"session_id": session_id})}

            # Handle reconnection - get Last-Event-ID header
            last_event_id = request.headers.get("Last-Event-ID")
            if last_event_id:
                try:
                    last_id = int(last_event_id)
                    # Send missed events for reconnection
                    missed_events = broadcaster.get_events_since(session_id, last_id)
                    for event in missed_events:
                        yield event_to_sse(event)
                    logger.debug(f"Replayed {len(missed_events)} events for session {session_id}")
                except ValueError:
                    logger.warning(f"Invalid Last-Event-ID header: {last_event_id}")

            # Clear any stale events from previous connection
            broadcaster.clear_queue(session_id)
            
            # Get the event queue for this session
            queue = await broadcaster.get_queue(session_id)

            while True:
                # Check for client disconnect
                if await request.is_disconnected():
                    logger.debug(f"Client disconnected from session {session_id}")
                    break

                try:
                    # Wait for events with timeout
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield event_to_sse(event)
                except asyncio.TimeoutError:
                    # Send keepalive to prevent connection timeout
                    yield {"event": "keepalive", "data": "{}"}
                except Exception as e:
                    # Log the error but continue the stream for transient errors
                    logger.error(f"Error retrieving event for session {session_id}: {e}")
                    # Only break on fatal errors, not transient ones
                    if isinstance(e, (asyncio.CancelledError, asyncio.InvalidStateError)):
                        yield {
                            "event": "error", 
                            "data": json.dumps({"error": "Stream error", "details": str(e)})
                        }
                        break
                    # For other errors, send an error event but continue streaming
                    yield {
                        "event": "error", 
                        "data": json.dumps({"error": "Transient error", "details": str(e), "recoverable": True})
                    }
        except Exception as e:
            # Catch-all for generator errors
            logger.exception(f"Fatal error in event generator for session {session_id}")
            yield {
                "event": "error",
                "data": json.dumps({"error": "Fatal stream error", "details": str(e)})
            }

    return EventSourceResponse(event_generator())


# ==================== Source Management Endpoints ====================


# GET /sources - List all sources
@app.get("/sources", response_model=SourceListResponse)
async def list_sources():
    """List all configured message sources."""
    from .sources.persistence import list_source_configs
    
    sources_data = list_source_configs(manager.conn)
    sources = []
    for src in sources_data:
        sources.append(SourceInfo(
            source_id=src["source_id"],
            source_type=SourceType(src["source_type"]),
            name=src["name"],
            config=src["config"],
            enabled=src["enabled"],
            status=SourceStatus(src.get("status", "stopped")),
            error_message=src.get("error_message"),
            created_at=datetime.fromisoformat(src["created_at"]).replace(tzinfo=timezone.utc) if isinstance(src["created_at"], str) else src["created_at"],
            updated_at=datetime.fromisoformat(src["updated_at"]).replace(tzinfo=timezone.utc) if src.get("updated_at") and isinstance(src["updated_at"], str) else src.get("updated_at"),
        ))
    return SourceListResponse(sources=sources)


# POST /sources - Create new source
@app.post("/sources", response_model=SourceInfo, status_code=201)
async def create_source(source_create: SourceCreate):
    """Create a new message source."""
    from .sources.persistence import save_source_config, get_source_config
    
    # Check if source already exists
    existing = get_source_config(manager.conn, source_create.source_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_ALREADY_EXISTS,
                message=f"Source already exists: {source_create.source_id}"
            ).model_dump()
        )
    
    # Validate source type is supported
    supported_types = {"telegram", "webhook", "whatsapp", "discord"}
    if source_create.source_type.value not in supported_types:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_TYPE_NOT_SUPPORTED,
                message=f"Source type not supported: {source_create.source_type}. Supported: {supported_types}"
            ).model_dump()
        )
    
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
    save_source_config(
        conn=manager.conn,
        source_id=source_create.source_id,
        source_type=source_create.source_type.value,
        name=source_create.name,
        config=source_create.config,
        credentials=credentials_json,
        enabled=source_create.enabled,
    )
    
    # Get the saved config
    saved = get_source_config(manager.conn, source_create.source_id)
    return SourceInfo(
        source_id=saved["source_id"],
        source_type=SourceType(saved["source_type"]),
        name=saved["name"],
        config=saved["config"],
        enabled=saved["enabled"],
        status=SourceStatus(saved.get("status", "stopped")),
        error_message=saved.get("error_message"),
        created_at=datetime.fromisoformat(saved["created_at"]).replace(tzinfo=timezone.utc),
        updated_at=datetime.fromisoformat(saved["updated_at"]).replace(tzinfo=timezone.utc) if saved.get("updated_at") else None,
    )


# POST /sources/test - Test source configuration
@app.post("/sources/test", response_model=SourceTestResponse)
async def test_source(test_request: SourceTestRequest):
    """Test a source configuration without saving it.
    
    Validates credentials by attempting to connect to the external service.
    """
    from .sources.base import SourceConfig
    
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
        from .sources.adapters.telegram import TelegramAdapter
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
    else:
        success, message = False, f"Unknown source type: {test_request.source_type}"
    
    return SourceTestResponse(success=success, message=message)


# GET /sources/{source_id} - Get source info
@app.get("/sources/{source_id}", response_model=SourceInfo)
async def get_source(source_id: str):
    """Get a specific message source."""
    from .sources.persistence import get_source_config
    
    source = get_source_config(manager.conn, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    return SourceInfo(
        source_id=source["source_id"],
        source_type=SourceType(source["source_type"]),
        name=source["name"],
        config=source["config"],
        enabled=source["enabled"],
        status=SourceStatus(source.get("status", "stopped")),
        error_message=source.get("error_message"),
        created_at=datetime.fromisoformat(source["created_at"]).replace(tzinfo=timezone.utc),
        updated_at=datetime.fromisoformat(source["updated_at"]).replace(tzinfo=timezone.utc) if source.get("updated_at") else None,
    )


# PUT /sources/{source_id} - Update source
@app.put("/sources/{source_id}", response_model=SourceInfo)
async def update_source(source_id: str, source_update: SourceUpdate):
    """Update a message source configuration."""
    from .sources.persistence import get_source_config, save_source_config
    
    existing = get_source_config(manager.conn, source_id)
    if not existing:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    # Merge updates
    updated_name = source_update.name if source_update.name is not None else existing["name"]
    updated_config = source_update.config if source_update.config is not None else existing["config"]
    updated_credentials = source_update.credentials if source_update.credentials is not None else existing.get("credentials", {})
    updated_enabled = source_update.enabled if source_update.enabled is not None else existing["enabled"]
    
    # Validate and encrypt credentials
    credentials_json = None
    if updated_credentials:
        cred_str = json.dumps(updated_credentials)
        if len(cred_str) > MAX_CREDENTIALS_SIZE:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    code=ErrorCodes.INVALID_REQUEST,
                    message=f"Credentials too large (max {MAX_CREDENTIALS_SIZE} bytes)"
                ).model_dump()
            )
        credentials_json = credential_manager.encrypt(updated_credentials)
    
    # Save updated config
    save_source_config(
        conn=manager.conn,
        source_id=source_id,
        source_type=existing["source_type"],
        name=updated_name,
        config=updated_config,
        credentials=credentials_json,
        enabled=updated_enabled,
    )
    
    # Get the updated config
    updated = get_source_config(manager.conn, source_id)
    return SourceInfo(
        source_id=updated["source_id"],
        source_type=SourceType(updated["source_type"]),
        name=updated["name"],
        config=updated["config"],
        enabled=updated["enabled"],
        status=SourceStatus(updated.get("status", "stopped")),
        error_message=updated.get("error_message"),
        created_at=datetime.fromisoformat(updated["created_at"]).replace(tzinfo=timezone.utc),
        updated_at=datetime.fromisoformat(updated["updated_at"]).replace(tzinfo=timezone.utc),
    )


# DELETE /sources/{source_id} - Delete source
@app.delete("/sources/{source_id}", response_model=DeleteResponse)
async def delete_source(source_id: str):
    """Delete a message source."""
    from .sources.persistence import delete_source_config
    
    deleted = delete_source_config(manager.conn, source_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    return DeleteResponse(deleted=True, message=f"Source {source_id} deleted")


# POST /sources/{source_id}/start - Start a source
@app.post("/sources/{source_id}/start", response_model=SourceActionResponse)
async def start_source(source_id: str):
    """Start a message source adapter."""
    from .sources.persistence import get_source_config, update_source_status
    from .sources.base import SourceConfig
    
    source = get_source_config(manager.conn, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    if not source["enabled"]:
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
                source_type = source["source_type"]
                credentials = source.get("credentials", {})
                
                # Decrypt credentials if encrypted
                if credentials and isinstance(credentials, str):
                    credentials = credential_manager.decrypt(credentials)
                
                config = SourceConfig(
                    source_id=source["source_id"],
                    source_type=source_type,
                    name=source["name"],
                    config=source.get("config", {}),
                    credentials=credentials,
                    enabled=source["enabled"],
                )
                
                # Create the appropriate adapter
                if source_type == "telegram":
                    from .sources.adapters.telegram import TelegramAdapter
                    # Create callback wrapper that includes source_id
                    async def on_message(msg):
                        await manager.source_registry._handle_message(source_id, msg)
                    adapter = TelegramAdapter(config, on_message)
                else:
                    raise ValueError(f"Source type '{source_type}' adapter not yet implemented")
                
                # Register the adapter
                manager.source_registry.register(adapter)
            
            # Start the adapter
            await manager.source_registry.start_adapter(source_id)
            update_source_status(manager.conn, source_id, "running")
            return SourceActionResponse(
                source_id=source_id,
                status=SourceStatus.running,
                message=f"Source {source_id} started successfully"
            )
        except Exception as e:
            update_source_status(manager.conn, source_id, "error", str(e))
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


# POST /sources/{source_id}/stop - Stop a source
@app.post("/sources/{source_id}/stop", response_model=SourceActionResponse)
async def stop_source(source_id: str):
    """Stop a message source adapter."""
    from .sources.persistence import get_source_config, update_source_status
    
    source = get_source_config(manager.conn, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    # Check if registry has the source
    if manager.source_registry:
        try:
            await manager.source_registry.stop_adapter(source_id)
            update_source_status(manager.conn, source_id, "stopped")
            return SourceActionResponse(
                source_id=source_id,
                status=SourceStatus.stopped,
                message=f"Source {source_id} stopped successfully"
            )
        except Exception as e:
            update_source_status(manager.conn, source_id, "error", str(e))
            return SourceActionResponse(
                source_id=source_id,
                status=SourceStatus.error,
                message=f"Failed to stop source: {str(e)}"
            )
    
    update_source_status(manager.conn, source_id, "stopped")
    return SourceActionResponse(
        source_id=source_id,
        status=SourceStatus.stopped,
        message=f"Source {source_id} marked as stopped"
    )


# ==================== Session Mapping Endpoints ====================


# GET /sources/{source_id}/mappings - List mappings for a source
@app.get("/sources/{source_id}/mappings", response_model=SessionMappingListResponse)
async def list_mappings(source_id: str):
    """List all session mappings for a source."""
    from .sources.persistence import get_source_config, list_session_mappings
    
    # Check source exists
    source = get_source_config(manager.conn, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    mappings_data = list_session_mappings(manager.conn, source_id)
    mappings = []
    for m in mappings_data:
        mappings.append(SessionMappingInfo(
            mapping_id=m["mapping_id"],
            source_id=m["source_id"],
            external_user_id=m["external_user_id"],
            agent_session_id=m["agent_session_id"],
            agent_dir=m["agent_dir"],
            metadata=m.get("metadata"),
            last_message_at=datetime.fromisoformat(m["last_message_at"]).replace(tzinfo=timezone.utc) if m.get("last_message_at") and isinstance(m["last_message_at"], str) else m.get("last_message_at"),
            created_at=datetime.fromisoformat(m["created_at"]).replace(tzinfo=timezone.utc) if isinstance(m["created_at"], str) else m["created_at"],
        ))
    return SessionMappingListResponse(mappings=mappings)


# POST /sources/{source_id}/mappings - Create or update a mapping
@app.post("/sources/{source_id}/mappings", response_model=SessionMappingInfo, status_code=201)
async def create_mapping(source_id: str, mapping_create: SessionMappingCreate):
    """Create a session mapping for an external user."""
    from .sources.persistence import get_source_config, get_session_mapping, save_session_mapping
    import uuid
    
    # Validate agent_dir is within allowed directory
    validate_agent_dir(mapping_create.agent_dir)
    
    # Check source exists
    source = get_source_config(manager.conn, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    # Check if mapping already exists
    existing = get_session_mapping(manager.conn, source_id, mapping_create.external_user_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                code=ErrorCodes.MAPPING_ALREADY_EXISTS,
                message=f"Mapping already exists for user {mapping_create.external_user_id}"
            ).model_dump()
        )
    
    # Generate IDs
    mapping_id = f"{source_id}:{mapping_create.external_user_id}"
    session_id = f"ext-{uuid.uuid4().hex[:12]}"
    
    # Spawn the agent session
    try:
        manager.spawn_session(
            agent_dir=mapping_create.agent_dir,
            session_id=session_id,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to spawn session: {str(e)}"
            ).model_dump()
        )
    
    # Save the mapping with rollback on failure
    try:
        save_session_mapping(
            conn=manager.conn,
            mapping_id=mapping_id,
            source_id=source_id,
            external_user_id=mapping_create.external_user_id,
            agent_session_id=session_id,
            agent_dir=mapping_create.agent_dir,
            metadata=mapping_create.metadata,
        )
    except Exception as e:
        # Rollback: terminate the orphaned session
        try:
            manager.terminate_session(session_id)
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
    saved = get_session_mapping(manager.conn, source_id, mapping_create.external_user_id)
    return SessionMappingInfo(
        mapping_id=saved["mapping_id"],
        source_id=saved["source_id"],
        external_user_id=saved["external_user_id"],
        agent_session_id=saved["agent_session_id"],
        agent_dir=saved["agent_dir"],
        metadata=saved.get("metadata"),
        last_message_at=datetime.fromisoformat(saved["last_message_at"]).replace(tzinfo=timezone.utc) if saved.get("last_message_at") and isinstance(saved["last_message_at"], str) else saved.get("last_message_at"),
        created_at=datetime.fromisoformat(saved["created_at"]).replace(tzinfo=timezone.utc),
    )


# DELETE /sources/{source_id}/mappings/{mapping_id} - Delete a mapping
@app.delete("/sources/{source_id}/mappings/{mapping_id}", response_model=DeleteResponse)
async def delete_mapping(source_id: str, mapping_id: str):
    """Delete a session mapping."""
    from .sources.persistence import delete_session_mapping, get_session_mapping
    
    # URL decode the mapping_id if needed
    # mapping_id format is "source_id:external_user_id"
    
    deleted = delete_session_mapping(manager.conn, mapping_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.MAPPING_NOT_FOUND,
                message=f"Mapping not found: {mapping_id}"
            ).model_dump()
        )
    
    return DeleteResponse(deleted=True, message=f"Mapping {mapping_id} deleted")


# ==================== Webhook Receiver Endpoint ====================


# POST /webhooks/{source_id} - Receive webhook from external source
@app.post("/webhooks/{source_id}")
async def receive_webhook(source_id: str, request: Request):
    """Receive a webhook from an external message source."""
    from .sources.persistence import get_source_config
    
    # Check source exists
    source = get_source_config(manager.conn, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    # Check source type is webhook-compatible
    if source["source_type"] not in ("webhook", "telegram"):
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Source type {source['source_type']} does not support webhooks"
            ).model_dump()
        )
    
    # Verify webhook secret if configured
    source_config = source.get("config", {}) if isinstance(source.get("config"), dict) else json.loads(source.get("config", "{}"))
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
                message=f"Source adapter does not support webhooks"
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
                message=f"Webhook processing failed"
            ).model_dump()
        )


# Static file serving for production UI (served at root)
@app.get("/")
async def serve_ui():
    """Serve the frontend UI."""
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse(
        status_code=404,
        content={"error": "UI not built. Run 'npm run build' in frontend directory."}
    )


@app.get("/{path:path}")
async def serve_ui_assets(path: str):
    """Serve frontend assets and SPA routing."""
    # Skip API routes
    if path.startswith('api') or path.startswith('ws'):
        return JSONResponse(
            status_code=404,
            content={"error": "Not found"}
        )
    
    asset_path = FRONTEND_DIST / path
    if asset_path.exists():
        return FileResponse(str(asset_path))
    # For SPA routing, serve index.html
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse(
        status_code=404,
        content={"error": "Asset not found"}
    )

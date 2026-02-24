import warnings

# Suppress langchain Pydantic V1 compatibility warning on Python 3.14+
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible with Python 3.14",
    category=UserWarning,
)

import time
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse
from typing import AsyncGenerator
from pathlib import Path
import os

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
)
from .manager import SessionManager
from .config import Config, load_config

# Determine the base path
BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

# Global state
manager: SessionManager = None
start_time: float = None

# Event storage for SSE
_session_events: dict[str, list[dict]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager, start_time
    config = load_config()
    manager = SessionManager(config)
    start_time = time.time()
    yield


app = FastAPI(
    title="Auto-Code Daemon",
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
        created_at=datetime.fromisoformat(session_meta["created_at"]) if isinstance(session_meta["created_at"], str) else session_meta["created_at"],
        updated_at=datetime.fromisoformat(session_meta["updated_at"]) if session_meta.get("updated_at") and isinstance(session_meta["updated_at"], str) else session_meta.get("updated_at"),
    )


# 3. GET /sessions - List sessions
@app.get("/sessions", response_model=SessionListResponse)
async def list_sessions():
    """List all sessions."""
    sessions_data = manager.list_sessions()
    sessions = []
    for sess in sessions_data:
        sessions.append(SessionInfo(
            session_id=sess["session_id"],
            agent_dir=sess["agent_dir"],
            status=SessionStatus(sess["status"]),
            parent_id=sess.get("parent_id"),
            children=sess.get("children", []),
            created_at=datetime.fromisoformat(sess["created_at"]) if isinstance(sess["created_at"], str) else sess["created_at"],
            updated_at=datetime.fromisoformat(sess["updated_at"]) if sess.get("updated_at") and isinstance(sess["updated_at"], str) else sess.get("updated_at"),
        ))
    return SessionListResponse(sessions=sessions)


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
    
    # Clean up stored events for this session
    if session_id in _session_events:
        del _session_events[session_id]
    
    return {"terminated": True}


# 6. POST /sessions/{session_id}/messages - Send message
@app.post("/sessions/{session_id}/messages", response_model=MessageResponse)
async def send_message(session_id: str, message: MessageCreate):
    """Send a message to a session."""
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

    try:
        result = manager.send_message(session_id, message.content)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.LLM_ERROR,
                message=f"Error processing message: {str(e)}"
            ).model_dump()
        )

    # Create response message
    now = datetime.now()
    message_id = f"msg-{int(now.timestamp() * 1000)}"

    # Store event for SSE
    if session_id not in _session_events:
        _session_events[session_id] = []
    _session_events[session_id].append({
        "type": "message",
        "message_id": message_id,
        "role": "assistant",
        "content": result.content,
        "thinking": result.thinking,
        "tool_calls": result.tool_calls,
        "created_at": now.isoformat(),
    })

    return MessageResponse(
        message_id=message_id,
        role="assistant",
        content=result.content,
        thinking=result.thinking,
        tool_calls=result.tool_calls,
        created_at=now,
    )


# 7. GET /sessions/{session_id}/messages - Get message history
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

    # Return stored events for this session (or empty list if not implemented)
    messages = _session_events.get(session_id, [])
    return messages


# 8. GET /sessions/{session_id}/events - SSE stream
@app.get("/sessions/{session_id}/events")
async def stream_events(session_id: str):
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

    async def event_generator() -> AsyncGenerator[dict, None]:
        """Generate SSE events for the session."""
        # Send initial connection event
        yield {"event": "connected", "data": "Session connected"}

        # For now, just yield any stored events
        # In a real implementation, this would use a queue/async iterator
        # to stream events as they happen
        if session_id in _session_events:
            for event in _session_events[session_id]:
                yield {"event": "message", "data": event}

    return EventSourceResponse(event_generator())


# Static file serving for production UI
@app.get("/ui")
async def serve_ui():
    """Serve the frontend UI."""
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse(
        status_code=404,
        content={"error": "UI not built. Run 'npm run build' in frontend directory."}
    )


@app.get("/ui/{path:path}")
async def serve_ui_assets(path: str):
    """Serve frontend assets."""
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

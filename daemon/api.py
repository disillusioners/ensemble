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
)
from .manager import SessionManager
from .config import Config, load_config
from .events import event_to_sse

# Determine the base path
BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

# Global state
manager: SessionManager = None
start_time: float = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager, start_time
    config = load_config()
    manager = SessionManager(config)
    # Set the main event loop for thread-safe broadcasting
    manager.broadcaster.set_main_loop(asyncio.get_running_loop())
    start_time = time.time()
    yield


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
            created_at=datetime.fromisoformat(sess["created_at"]).replace(tzinfo=timezone.utc) if isinstance(sess["created_at"], str) else sess["created_at"],
            updated_at=datetime.fromisoformat(sess["updated_at"]).replace(tzinfo=timezone.utc) if sess.get("updated_at") and isinstance(sess["updated_at"], str) else sess.get("updated_at"),
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

    # Return stored events for this session (or empty list if not implemented)
    # TODO: Get message history from LangGraph checkpoints
    return []


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

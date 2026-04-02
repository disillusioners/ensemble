"""Session management tools for multi-agent orchestration."""

import asyncio
import logging
from typing import TYPE_CHECKING, Annotated

from langchain_core.tools import tool, BaseTool
from pydantic import BaseModel, Field, model_validator

from .bash import bash
from .filesystem import list_directory, read_file, glob_files, write_file, grep_files, edit_file
from .time import time
from .inner_soul import create_inner_soul_tool
from .access_memory import create_access_memory_tool
from .agent_mother import create_mother_tools
from .project import create_project_tools
from .help import create_help_tool

if TYPE_CHECKING:
    from ..manager import SessionManager


class SpawnSessionInput(BaseModel):
    """Input model for spawn_session tool."""
    
    agent_id: Annotated[str, Field(
        description="Agent ID (e.g., 'coder', 'leader')"
    )]
    
    project_id: Annotated[str | None, Field(
        default=None,
        description="Optional project ID for context injection. Pass None if no project context is needed."
    )] = None
    
    @model_validator(mode='after')
    def validate_params(self):
        """Require agent_id."""
        if not self.agent_id:
            raise ValueError('agent_id is required')
        return self


def create_session_tools(manager: "SessionManager", current_session_id: str, agent_id: str = ""):
    """Create tools with injected manager reference.
    
    Args:
        manager: The SessionManager instance to use for operations
        current_session_id: The ID of the current session (used as parent for spawned sessions)
        agent_id: The agent identifier (e.g., "coder").
    
    Returns:
        List of tool functions
    """
    
    logger = logging.getLogger(__name__)
    
    def _handle_process_result(task: asyncio.Task, session_id: str) -> None:
        """Callback to log errors from background queue processing."""
        try:
            exc = task.exception()
            if exc:
                logger.error(
                    f"Background queue processing failed for session "
                    f"{session_id[:8]}: {exc}",
                    exc_info=exc
                )
        except asyncio.CancelledError:
            logger.debug(f"Queue processing cancelled for session {session_id[:8]}")
    
    @tool(args_schema=SpawnSessionInput)
    def spawn_session(agent_id: Annotated[str, Field(description="Agent ID (e.g., 'coder', 'leader')")], project_id: Annotated[str | None, Field(default=None, description="Optional project ID for context injection.")] = None) -> str:
        """Spawn a new agent session and return its session_id.
        
        IMPORTANT: After spawning, you MUST use send_message(session_id, message) 
        to communicate with the new session. The spawned session will not do anything
        until you send it a message.
        
        Args:
            agent_id: Agent ID to spawn (e.g., 'coder', 'leader').
            project_id: Optional project ID for context injection.
        
        Returns:
            The session_id of the newly spawned session. Use this with send_message().
        """
        new_session_id = manager.spawn_session(
            agent_id=agent_id,
            session_id=None,
            parent_id=current_session_id,
            project_id=project_id,
        )
        return (
            f"Successfully spawned session: {new_session_id}\n"
            f"To communicate with this session, use: send_message(session_id=\"{new_session_id}\", message=\"your message here\")"
        )
    
    @tool
    async def send_message(session_id: str, message: str) -> str:
        """Send a message to another session's input queue. Use tool_help("send_message") for details."""
        # Enqueue the message (fast ~1-5ms DB write)
        message_id = manager.queue.enqueue(
            session_id=session_id,
            content=message,
            source=f"agent:{current_session_id}"
        )
        
        # Fire-and-forget processing (non-blocking)
        # Safe because: _process_queue has concurrency guard, messages are persisted,
        # and watchdog handles failures
        task = asyncio.create_task(manager._process_queue(session_id))
        task.add_done_callback(
            lambda t: _handle_process_result(t, session_id)
        )
        
        return message_id
    
    send_message._full_doc_ = """Send a message to another session's input queue.

The message is queued and processed asynchronously. The target
session will process the message and send a completion report
back if it's a child session.

Args:
    session_id: The ID of the target session to send the message to
    message: The message content to send

Returns:
    The message_id for tracking (queue is async, response comes later)
"""
    
    @tool
    def terminate_session(session_id: str) -> bool:
        """Terminate a session. Use with caution. Use tool_help("terminate_session") for details."""
        return manager.terminate_session(session_id)
    
    terminate_session._full_doc_ = """Terminate a session. Use with caution.

Args:
    session_id: The ID of the session to terminate

Returns:
    True if termination was successful, False otherwise
"""
    
    @tool
    def list_sessions() -> list[dict]:
        """List all active sessions (most recent first). Use tool_help("list_sessions") for details."""
        return manager.list_sessions(limit=20)
    
    list_sessions._full_doc_ = """List all active sessions (most recent first).

Returns:
    List of session info dictionaries (up to 20 most recent)
"""
    
    @tool
    def get_session_info(session_id: str) -> dict:
        """Get information about a specific session. Use tool_help("get_session_info") for details."""
        return manager.get_session_info(session_id)
    
    get_session_info._full_doc_ = """Get information about a specific session.

Args:
    session_id: The ID of the session to get info for

Returns:
    Session info dictionary
"""
    
    # Create inner_soul tool for self-modification
    inner_soul = create_inner_soul_tool(manager, agent_id, current_session_id)
    
    # Create access_memory tool for reading memory files
    access_memory = create_access_memory_tool(agent_id)
    
    # Create project management tools (with session context for creator tracking)
    project_tools = create_project_tools(manager.project_store, current_session_id, agent_id)
    
    # Base tools (available in all sessions)
    tools = [
        bash,
        list_directory,
        read_file,
        write_file,
        glob_files,
        grep_files,
        edit_file,
        time,
        # Session management tools
        spawn_session,
        send_message,
        terminate_session,
        list_sessions,
        get_session_info,
        # Self-modification tool
        inner_soul,
        # Memory access tool
        access_memory,
    ]
    
    # Add project management tools (available in all sessions)
    tools.extend(project_tools)
    
    # Add mother tools if this is the _mother agent
    if agent_id == "_mother":
        mother_tools = create_mother_tools(manager, current_session_id)
        tools.extend(mother_tools)
    
    # Add help tool (must be last so it knows about all other tools)
    help_tool = create_help_tool(tools)
    tools.append(help_tool)
    
    return tools

"""Session management tools for multi-agent orchestration."""

from pathlib import Path
from langchain_core.tools import tool
from typing import TYPE_CHECKING

from .bash import bash
from .filesystem import list_directory, read_file, glob_files
from .time import time
from .inner_soul import create_inner_soul_tool
from .agent_mother import create_mother_tools
from .project import create_project_tools
from .help import create_help_tool

if TYPE_CHECKING:
    from ..manager import SessionManager


def create_session_tools(manager: "SessionManager", current_session_id: str, agent_dir: str = ""):
    """Create tools with injected manager reference.
    
    Args:
        manager: The SessionManager instance to use for operations
        current_session_id: The ID of the current session (used as parent for spawned sessions)
        agent_dir: The path to the agent directory for self-modification tools
    
    Returns:
        List of tool functions
    """
    
    @tool
    def spawn_session(agent_dir: str) -> str:
        """Spawn a new agent session."""
        return manager.spawn_session(
            agent_dir=agent_dir,
            session_id=None,
            parent_id=current_session_id
        )
    
    @tool
    def send_message(session_id: str, message: str) -> str:
        """Send a message to another session's input queue. Use tool_help("send_message") for details."""
        import asyncio
        
        # Enqueue the message
        message_id = manager.queue.enqueue(
            session_id=session_id,
            content=message,
            source=f"agent:{current_session_id}"
        )
        
        # Trigger async processing of the target session's queue
        # We need to schedule this since we're in a sync tool context
        try:
            loop = asyncio.get_running_loop()
            # We're in an async context (LangGraph), schedule the processing
            asyncio.create_task(manager._process_queue(session_id))
        except RuntimeError:
            # No running loop, try to run in a new thread
            import threading
            import concurrent.futures
            
            def run_processing():
                import asyncio
                new_loop = asyncio.new_event_loop()
                try:
                    new_loop.run_until_complete(manager._process_queue(session_id))
                finally:
                    new_loop.close()
            
            thread = threading.Thread(target=run_processing, daemon=True)
            thread.start()
        
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
        """List all active sessions. Use tool_help("list_sessions") for details."""
        return manager.list_sessions()
    
    list_sessions._full_doc_ = """List all active sessions.

Returns:
    List of session info dictionaries
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
    inner_soul = create_inner_soul_tool(manager, agent_dir, current_session_id)
    
    # Create project management tools (with session context for creator tracking)
    project_tools = create_project_tools(manager.project_store, current_session_id, agent_dir)
    
    # Base tools (available in all sessions)
    tools = [
        bash,
        list_directory,
        read_file,
        glob_files,
        time,
        # Session management tools
        spawn_session,
        send_message,
        terminate_session,
        list_sessions,
        get_session_info,
        # Self-modification tool
        inner_soul,
    ]
    
    # Add project management tools (available in all sessions)
    tools.extend(project_tools)
    
    # Add mother tools if this is the _mother agent
    if agent_dir and Path(agent_dir).name == "_mother":
        mother_tools = create_mother_tools(manager, current_session_id)
        tools.extend(mother_tools)
    
    # Add help tool (must be last so it knows about all other tools)
    help_tool = create_help_tool(tools)
    tools.append(help_tool)
    
    return tools

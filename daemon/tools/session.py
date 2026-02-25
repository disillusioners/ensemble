"""Session management tools for multi-agent orchestration."""

from langchain_core.tools import tool
from typing import TYPE_CHECKING

from .bash import bash
from .filesystem import list_directory, read_file, glob_files
from .time import time
from .inner_soul import create_inner_soul_tool

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
    def spawn_session(agent_dir: str, session_id: str | None = None) -> str:
        """Spawn a new agent session.
        
        Args:
            agent_dir: The path to agent directory (e.g., 'agents/coder')
            session_id: Optional session ID (auto-generated if omitted)
        
        Returns:
            The session_id of the newly created session
        """
        return manager.spawn_session(
            agent_dir=agent_dir,
            session_id=session_id,
            parent_id=current_session_id
        )
    
    @tool
    def send_message(session_id: str, message: str) -> str:
        """Send a message to another session and get the response.
        
        Args:
            session_id: The ID of the target session to send the message to
            message: The message content to send
        
        Returns:
            The response content from the session
        """
        return manager.send_message(session_id, message)
    
    @tool
    def terminate_session(session_id: str) -> bool:
        """Terminate a session. Use with caution.
        
        Args:
            session_id: The ID of the session to terminate
        
        Returns:
            True if termination was successful, False otherwise
        """
        return manager.terminate_session(session_id)
    
    @tool
    def list_sessions() -> list[dict]:
        """List all active sessions.
        
        Returns:
            List of session info dictionaries
        """
        return manager.list_sessions()
    
    @tool
    def get_session_info(session_id: str) -> dict:
        """Get information about a specific session.
        
        Args:
            session_id: The ID of the session to get info for
        
        Returns:
            Session info dictionary
        """
        return manager.get_session_info(session_id)
    
    # Create inner_soul tool for self-modification
    inner_soul = create_inner_soul_tool(manager, agent_dir, current_session_id)
    
    return [
        # Static tools (available in all sessions)
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

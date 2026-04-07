"""Instance management tools for multi-agent orchestration."""

import logging
from typing import TYPE_CHECKING, Annotated

from langchain_core.tools import tool, BaseTool
from pydantic import BaseModel, Field, model_validator

from .bash import bash
from .filesystem import list_directory, read_file, glob_files, write_file, grep_files, edit_file
from .time import time
from .done import done
from .inner_soul import create_inner_soul_tool
from .access_memory import create_access_memory_tool
from .agent_mother import create_mother_tools
from .project import create_project_tools
from .help import create_help_tool

if TYPE_CHECKING:
    from ..manager import InstanceManager


class SpawnInstanceInput(BaseModel):
    """Input model for spawn_instance tool."""
    
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


def create_instance_tools(manager: "InstanceManager", current_instance_id: str, agent_id: str = ""):
    """Create tools with injected manager reference.
    
    Args:
        manager: The InstanceManager instance to use for operations
        current_instance_id: The ID of the current instance (used as parent for spawned instances)
        agent_id: The agent identifier (e.g., "coder").
    
    Returns:
        List of tool functions
    """
    
    logger = logging.getLogger(__name__)
    
    @tool(args_schema=SpawnInstanceInput)
    def spawn_instance(agent_id: Annotated[str, Field(description="Agent ID (e.g., 'coder', 'leader')")], project_id: Annotated[str | None, Field(default=None, description="Optional project ID for context injection.")] = None) -> str:
        """Spawn a new agent instance and return its instance_id.
        
        IMPORTANT: After spawning, you MUST use send_message(instance_id, message) 
        to communicate with the new instance. The spawned instance will not do anything
        until you send it a message.
        
        Args:
            agent_id: Agent ID to spawn (e.g., 'coder', 'leader').
            project_id: Optional project ID for context injection.
        
        Returns:
            The instance_id of the newly spawned instance. Use this with send_message().
        """
        new_instance_id = manager.spawn_instance(
            agent_id=agent_id,
            instance_id=None,
            parent_id=current_instance_id,
            project_id=project_id,
        )
        return (
            f"Successfully spawned instance: {new_instance_id}\n"
            f"To communicate with this instance, use: send_message(instance_id=\"{new_instance_id}\", message=\"your message here\")"
        )
    
    @tool
    async def send_message(instance_id: str, message: str) -> str:
        """Send a message to another instance's input queue. Use tool_help("send_message") for details."""
        # Validate instance exists first (with fuzzy matching for typos)
        try:
            manager.get_instance(instance_id)
        except KeyError:
            # Try to find a near match
            near_match = manager.find_near_instance(instance_id, max_distance=2)
            if near_match:
                return (
                    f"ERROR: instance not found, are you intent to use following '{near_match}'?\n"
                    f"If yes, please retry with the corrected instance_id."
                )
            else:
                return (
                    f"ERROR: '{instance_id}' not found, please re-plan, spawn new instance for your task"
                )
        
        # Enqueue the message (fast ~1-5ms DB write)
        message_id = manager.queue.enqueue(
            instance_id=instance_id,
            content=message,
            source=f"agent:{current_instance_id}"
        )
        
        # Trigger queue processing via persistent consumer (non-blocking)
        manager._signal_consumer(instance_id)
        
        return f"Message queued and sent to {instance_id}. Please wait — the system will deliver the completion report when ready."
    
    send_message._full_doc_ = """Send a message to another instance's input queue.

The message is queued and processed asynchronously. The target
instance will process the message and send a completion report
back if it's a child instance.

Args:
    instance_id: The ID of the target instance to send the message to
    message: The message content to send

Returns:
    The message_id for tracking (queue is async, response comes later)
"""
    
    @tool
    def terminate_instance(instance_id: str) -> bool:
        """Terminate an instance. Use with caution. Use tool_help("terminate_instance") for details."""
        return manager.terminate_instance(instance_id)
    
    terminate_instance._full_doc_ = """Terminate an instance. Use with caution.

Args:
    instance_id: The ID of the instance to terminate

Returns:
    True if termination was successful, False otherwise
"""
    
    @tool
    def list_instances() -> list[dict]:
        """List all active instances. Use tool_help("list_instances") for details."""
        instances, _ = manager.list_instances(limit=20)
        return instances
    
    list_instances._full_doc_ = """List the 20 most recent active instances.

Returns:
    List of instance info dictionaries
"""
    
    @tool
    def get_instance_info(instance_id: str) -> dict:
        """Get information about a specific instance. Use tool_help("get_instance_info") for details."""
        return manager.get_instance_info(instance_id)
    
    get_instance_info._full_doc_ = """Get information about a specific instance.

Args:
    instance_id: The ID of the instance to get info for

Returns:
    Instance info dictionary
"""
    
    # Create inner_soul tool for self-modification
    inner_soul = create_inner_soul_tool(manager, agent_id, current_instance_id)
    
    # Create access_memory tool for reading memory files
    access_memory = create_access_memory_tool(agent_id)
    
    # Create project management tools (with instance context for creator tracking)
    project_tools = create_project_tools(manager.project_store, current_instance_id, agent_id)
    
    # Base tools (available in all instances)
    tools = [
        bash,
        list_directory,
        read_file,
        write_file,
        glob_files,
        grep_files,
        edit_file,
        time,
        done,
        # Instance management tools
        spawn_instance,
        send_message,
        terminate_instance,
        list_instances,
        get_instance_info,
        # Self-modification tool
        inner_soul,
        # Memory access tool
        access_memory,
    ]
    
    # Add project management tools (available in all instances)
    tools.extend(project_tools)
    
    # Add mother tools if this is the _mother agent
    if agent_id == "_mother":
        mother_tools = create_mother_tools(manager, current_instance_id)
        tools.extend(mother_tools)
    
    # Add help tool (must be last so it knows about all other tools)
    help_tool = create_help_tool(tools)
    tools.append(help_tool)
    
    return tools

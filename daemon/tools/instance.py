"""Instance management tools for multi-agent orchestration."""

import asyncio
import logging
from functools import partial
from typing import TYPE_CHECKING, Annotated, Any, Callable

from langchain_core.tools import tool, BaseTool
from pydantic import BaseModel, Field, model_validator

from .bash import bash
from .filesystem import (
    list_directory,
    read_file,
    glob_files,
    write_file,
    grep_files,
    edit_file,
)
from .time import time
from .inner_soul import create_inner_soul_tool
from .access_memory import create_access_memory_tool
from .agent_mother import create_mother_tools
from .project import create_project_tools
from .help import create_help_tool

if TYPE_CHECKING:
    from ..manager import InstanceManager


def _get_instance_project_id(manager: "InstanceManager", instance_id: str) -> str | None:
    """Get the project_id from a parent instance's metadata.
    
    Args:
        manager: The InstanceManager instance
        instance_id: The current instance ID
    
    Returns:
        The project_id if found, None otherwise.
    """
    try:
        instance_meta = manager._instance_repository.get(instance_id)
        if instance_meta and instance_meta.instance_metadata:
            return instance_meta.instance_metadata.get("project_id")
    except Exception:
        pass
    return None


def _get_project_workdir(manager: "InstanceManager", instance_id: str) -> str | None:
    """Get the default workdir from the instance's project main_directory.
    
    Args:
        manager: The InstanceManager instance
        instance_id: The current instance ID
        
    Returns:
        The project's main_directory if found, None otherwise.
    """
    try:
        instance_meta = manager._instance_repository.get(instance_id)
        if instance_meta and instance_meta.instance_metadata:
            project_id = instance_meta.instance_metadata.get("project_id")
            if project_id:
                project = manager._project_repository.get(project_id)
                if project and project.main_directory:
                    return project.main_directory
    except Exception:
        pass
    return None


def _is_null_workdir(value: str | None) -> bool:
    """Check if workdir value should be treated as null/empty.
    
    Handles various null representations: None, "", "null", "none", "None", etc.
    """
    if value is None:
        return True
    return str(value).strip().lower() in ("", "null", "none")


def _make_workdir_aware(
    tool,  # Can be a function or StructuredTool
    get_default_workdir: Callable[[], str | None]
):
    """Wrap a tool to auto-populate workdir from project directory.
    
    Args:
        tool: The tool to wrap (function or StructuredTool)
        get_default_workdir: Callable that returns the default workdir
        
    Returns:
        Wrapped tool with auto workdir support
    """
    from functools import wraps
    from langchain_core.tools import StructuredTool
    
    # Check if it's a StructuredTool
    if isinstance(tool, StructuredTool):
        # Get the underlying function - @tool uses 'coroutine', from_function uses 'func'
        original_func = getattr(tool, 'coroutine', None) or getattr(tool, 'func', None)
        if original_func is None:
            # Fallback - tool doesn't have a callable func, return as-is
            return tool
        
        # Check if async
        is_async = asyncio.iscoroutinefunction(original_func)
        
        if is_async:
            @wraps(original_func)
            async def wrapped_func(*args, **kwargs):
                # Auto-fill workdir if not provided or null/empty
                if _is_null_workdir(kwargs.get('workdir')):
                    kwargs['workdir'] = get_default_workdir()
                return await original_func(*args, **kwargs)
        else:
            @wraps(original_func)
            def wrapped_func(*args, **kwargs):
                # Auto-fill workdir if not provided or null/empty
                if _is_null_workdir(kwargs.get('workdir')):
                    kwargs['workdir'] = get_default_workdir()
                return original_func(*args, **kwargs)
        
        # Create a new StructuredTool with the wrapped function
        # Use coroutine for async tools, func for sync
        if asyncio.iscoroutinefunction(wrapped_func):
            return tool.__class__.from_function(
                func=wrapped_func,
                name=tool.name,
                description=tool.description,
                coroutine=wrapped_func,
            )
        else:
            return tool.__class__.from_function(
                func=wrapped_func,
                name=tool.name,
                description=tool.description,
            )
    else:
        # It's a plain function - wrap it directly
        func = tool
        
        # Check if async
        is_async = asyncio.iscoroutinefunction(func)
        
        if is_async:
            @wraps(func)
            async def wrapped_func(*args, **kwargs):
                if _is_null_workdir(kwargs.get('workdir')):
                    kwargs['workdir'] = get_default_workdir()
                return await func(*args, **kwargs)
        else:
            @wraps(func)
            def wrapped_func(*args, **kwargs):
                if _is_null_workdir(kwargs.get('workdir')):
                    kwargs['workdir'] = get_default_workdir()
                return func(*args, **kwargs)
        
        return wrapped_func


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
    
    # Create a closure to get the current instance's project workdir
    def get_current_workdir() -> str | None:
        return _get_project_workdir(manager, current_instance_id)
    
    @tool(args_schema=SpawnInstanceInput)
    def spawn_instance(agent_id: Annotated[str, Field(description="Agent ID (e.g., 'coder', 'leader')")], project_id: Annotated[str | None, Field(default=None, description="Optional project ID for context injection. Pass None or 'null' if no project context is needed.")] = None) -> str:
        """Spawn a new agent instance and return its instance_id.
        
        IMPORTANT: After spawning, you MUST use send_message(instance_id, message) 
        to communicate with the new instance. The spawned instance will not do anything
        until you send it a message.
        
        Args:
            agent_id: Agent ID to spawn (e.g., 'coder', 'leader').
            project_id: Optional project ID for context injection. Use None or 'null' if no project context is needed.
        
        Returns:
            The instance_id of the newly spawned instance. Use this with send_message().
        """
        try:
            # Auto-inherit project_id from parent if not explicitly provided
            if project_id is None:
                project_id = _get_instance_project_id(manager, current_instance_id)
            
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
        except ValueError as e:
            # Return text guidance instead of raising - agent can self-correct
            error_msg = str(e)
            if "Agent not found" in error_msg:
                return (
                    f"ERROR: {error_msg}\n"
                    f"Available agents can be found using: list_agents()"
                )
            elif "not found" in error_msg.lower() and "project" in error_msg.lower():
                return (
                    f"ERROR: {error_msg}\n"
                    f"HINT: If you don't need a project context, pass project_id=None or project_id='null'"
                )
            elif "Max instances" in error_msg:
                return (
                    f"ERROR: {error_msg}\n"
                    f"HINT: Wait for existing instances to complete, or terminate unused instances with terminate_instance()"
                )
            elif "Max children" in error_msg:
                return (
                    f"ERROR: {error_msg}\n"
                    f"HINT: The parent instance has too many child instances. Consider a different approach."
                )
            else:
                return f"ERROR: {error_msg}"
        except Exception as e:
            return f"ERROR: Failed to spawn instance: {str(e)}"
    
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
        
        # Enqueue the message via worker pool (creates MessageQueue + Task atomically)
        result = await manager.enqueue_message(
            instance_id=instance_id,
            message=message,
            source=f"agent:{current_instance_id}"
        )
        message_id = result.message_id
        
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
    
    # Create workdir-aware wrappers for filesystem tools
    # These auto-populate workdir from project's main_directory when not provided
    bash_aware = _make_workdir_aware(bash, get_current_workdir)
    list_directory_aware = _make_workdir_aware(list_directory, get_current_workdir)
    read_file_aware = _make_workdir_aware(read_file, get_current_workdir)
    write_file_aware = _make_workdir_aware(write_file, get_current_workdir)
    glob_files_aware = _make_workdir_aware(glob_files, get_current_workdir)
    grep_files_aware = _make_workdir_aware(grep_files, get_current_workdir)
    edit_file_aware = _make_workdir_aware(edit_file, get_current_workdir)
    
    # Base tools (available in all instances) - with auto workdir support
    tools = [
        bash_aware,
        list_directory_aware,
        read_file_aware,
        write_file_aware,
        glob_files_aware,
        grep_files_aware,
        edit_file_aware,
        time,
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

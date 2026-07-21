"""Instance management tools for multi-agent orchestration."""

import asyncio
import json
import logging
from functools import partial
from typing import TYPE_CHECKING, Annotated, Any, Callable

from langchain_core.tools import tool, BaseTool
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Instance Management"
CATEGORY_DOC = """\
Spawn, communicate with, and manage agent instances.

**instance_name**: Optional short name for the instance to identify it in reports. Use concise, descriptive names. Examples: `create-feature-a`, `fix-bug-b`, `refactor-auth`.
"""


_FIRE_AND_FORGET_NOTE = """\
[SYSTEM REMINDER — Fire-and-Forget Workflow]

`get_instance_info` returns instance METADATA (status, config, project) — it does NOT return the instance's report/result. The system will deliver the instance's final report to you automatically when it finishes. Instances never get stuck silently: you are guaranteed to receive the result.

DO NOT poll `get_instance_info` or `list_instances` to wait for a delegated task, and DO NOT hold your turn open with `sleep`/`bash` waiting. Both waste resources and tokens, and holding your turn open does NOT speed up delivery.

Correct workflow:
```mermaid
flowchart LR
    A[Delegate task via send_message] --> B[END YOUR TURN — stop calling tools]
    B --> C{System resumes your turn<br/>with the report automatically}
    C -->|Report arrives| D[Continue work]
    C -->|Need to cancel| E[terminate_instance]
```

After delegating, END YOUR TURN (produce your final response / stop calling tools). The system will resume your turn automatically the moment the report arrives.
"""


# Innate-skill → required tool categories mapping.
#
# When an agent declares an innate skill, the matching tool categories are
# automatically granted (merged into the agent's allow list) so the skill is
# actually usable. This avoids requiring every agent to repeat
# "external_opencode" in its `tools.allow` list just because it has
# `innate_skills: ["opencode"]`.
#
# Add new entries here when introducing a new innate skill that requires
# dedicated tool categories. The skill prompt is loaded separately by the
# loader; this map only governs tool access.
INNATE_SKILL_TOOL_CATEGORIES: dict[str, list[str]] = {
    "opencode": ["external_opencode"],
    "chart": ["chart"],
    "todo": ["todo"],
    "dynamic-skill": ["dynamic-skill"],
    "skill-evolution": ["skill-evolution"],
}


def expand_allow_for_innate_skills(
    allow: list[str] | None,
    innate_skills: list[str] | None,
) -> list[str] | None:
    """Append tool categories implied by innate skills to an allow list.

    If the agent has no explicit allow list (None), it already has access to
    every tool, so no expansion is needed. Otherwise, any categories mapped
    from innate skills in :data:`INNATE_SKILL_TOOL_CATEGORIES` are appended
    (de-duplicated) to the allow list.

    Args:
        allow: The agent's configured `tools.allow` list (or None).
        innate_skills: The agent's `innate_skills` list (or None).

    Returns:
        The allow list with innate-skill categories merged in, or the
        original value if no expansion was needed.
    """
    if not innate_skills or allow is None:
        return allow

    extra: list[str] = []
    for skill in innate_skills:
        for category in INNATE_SKILL_TOOL_CATEGORIES.get(skill, []):
            if category not in allow and category not in extra:
                extra.append(category)

    if not extra:
        return allow
    return [*allow, *extra]

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
from .job_queue import create_job_tools
from .help import create_help_tool
from .knowledge_tools import create_knowledge_tools
from .chart_tools import create_chart_tools
from .image_tools import create_image_tools
from .todo_tools import create_todo_tools
from .question_tools import create_question_tools
from .skill_tools import create_skill_tools
from .skill_evolution_tools import create_skill_evolution_tools
from .external_opencode import create_opencode_tools
from .rag_tools import create_rag_tools
from .critical_notes import create_critical_notes_tools
from .project_history import create_project_history_tools
from .context_tools import create_context_tools
from .shared_context_tools import create_shared_context_tools
from .db_tools import create_db_tools
from .infra import create_infra_tools
from .system import create_system_tools
from .language_tools import create_language_tools
from .proc_tools import create_proc_tools
from ._tool_registry import list_tools_by_category, scan_tools_for_full_docs, register_tool_category
from daemon.services.project_normalizer import normalize_project_id
from daemon.utils import DEFAULT_FUZZY_MATCH_DISTANCE
from daemon.constants import DEFAULT_PAGE_LIMIT
from daemon.rag.config import is_rag_enabled


def _load_mcp_tools(manager: Any, instance_id: str) -> list[Any]:
    """Load MCP tools from preloaded cache.

    Returns:
        List of LangChain tools from MCP servers. Empty list if
        not preloaded or on error.
    """
    try:
        if hasattr(manager, '_mcp_service') and manager._mcp_service:
            return manager._mcp_service.get_mcp_tools(instance_id)
    except Exception as e:
        logger.warning(f"Failed to load MCP tools: {e}")
    return []


def resolve_tool_filter(
    allow: list[str] | None, 
    deny: list[str] | None,
    tool_categories: dict[str, list[str]] | None = None,
    all_tool_names: set[str] | None = None,
) -> set[str] | None:
    """Resolve tool filter allow/deny lists into a final set of allowed tool names.
    
    Logic:
    - If both allow and deny are None/empty → return None (all tools allowed)
    - If allow is set → start with allowed items, expand categories
    - Apply deny → remove denied items (deny wins conflicts)
    - Return the final set of allowed tool names
    
    Args:
        allow: List of category names and/or individual tool names to allow
        deny: List of category names and/or individual tool names to deny
        tool_categories: Optional dict mapping category names to tool name lists.
            If None, uses the dynamic tool registry via list_tools_by_category().
        all_tool_names: Optional set of all available tool names. Used for dynamic
            category expansion of MCP tools (tools starting with "mcp_" with at least
            2 underscores in the name).
    
    Returns:
        Set of allowed tool names, or None if all tools should be allowed
    """
    # Both empty → all tools allowed
    allow_empty = allow is None or len(allow) == 0
    deny_empty = deny is None or len(deny) == 0
    
    if allow_empty and deny_empty:
        return None
    
    # Use provided categories or fetch from registry
    if tool_categories is None:
        tool_categories = dict(list_tools_by_category())
    else:
        # Make a copy so we can modify it for MCP expansion
        tool_categories = dict(tool_categories)
    
    # Expand MCP category dynamically if all_tool_names is provided
    if all_tool_names is not None and "mcp" in tool_categories:
        # Check if MCP category is empty (not yet expanded)
        if len(tool_categories.get("mcp", [])) == 0:
            # Find all MCP tools: names starting with "mcp_" and contain at least 2 underscores
            mcp_tools = {
                name for name in all_tool_names
                if name.startswith("mcp_") and "_" in name[4:]
            }
            tool_categories["mcp"] = list(mcp_tools)
    if allow is None or len(allow) == 0:
        # No allow list means everything is potentially allowed
        # Start with all tools from all categories
        allowed_tools: set[str] = set()
        for category_tools in tool_categories.values():
            allowed_tools.update(category_tools)
    else:
        # Expand allow list (categories → individual tools)
        allowed_tools = set()
        for item in allow:
            if item in tool_categories:
                allowed_tools.update(tool_categories[item])
            else:
                allowed_tools.add(item)
    
    # Apply deny list (deny wins)
    if deny:
        denied_tools: set[str] = set()
        for item in deny:
            if item in tool_categories:
                denied_tools.update(tool_categories[item])
            else:
                denied_tools.add(item)
        allowed_tools -= denied_tools
    
    return allowed_tools


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
        if instance_meta and instance_meta.project_id:
            return instance_meta.project_id
    except Exception:
        pass
    return None


def _check_team_membership(caller_agent_id: str, requested_agent_id: str) -> str | None:
    """Verify the caller agent is allowed to spawn the requested agent.

    Reads the caller's ``meta.json`` ``team_members`` list and checks that the
    requested agent_id (resolved to its canonical id) is present. Returns
    ``None`` when the spawn is permitted, or an error message describing the
    rejection when it is not.

    Both the caller's list entries AND the requested ``agent_id`` are
    canonicalized via :func:`registry.resolve_pure_id` so renamed agents
    continue to match their ``team_members`` entries correctly.

    Secure default: ``team_members`` missing OR empty → deny everything.

    Args:
        caller_agent_id: The agent_id of the instance invoking
            ``spawn_instance`` (the parent instance's agent).
        requested_agent_id: The agent_id the caller wants to spawn.

    Returns:
        ``None`` when the spawn is authorized, otherwise a human-readable
        error string suitable for the tool's existing error path.
    """
    # Import here to avoid circular import (registry imports utils indirectly).
    from ..registry import get_registry

    registry = get_registry()

    # Canonicalize the REQUESTED id first — unknown agent → reject (will be
    # reported as "not allowed" rather than "not found" since this is a
    # permissions check). The downstream lifecycle service still raises a
    # "not found" ValueError for unresolvable ids, which is the right
    # primary signal for callers; the membership check is purely an
    # authorization filter on top.
    requested_canonical = registry.resolve_pure_id(requested_agent_id)
    if requested_canonical is None:
        return (
            f"Agent '{caller_agent_id}' is not allowed to spawn "
            f"'{requested_agent_id}'. Requested agent does not exist. "
            "Allowed team members: []"
        )

    # Look up the caller's metadata.
    caller_meta = registry.get_resolved(caller_agent_id)
    if caller_meta is None:
        # Caller agent_id is unknown — this is a wiring/misconfiguration
        # bug, but we fail closed (deny). The downstream lifecycle service
        # will raise a "not found" ValueError for the caller as well.
        return (
            f"Agent '{caller_agent_id}' is not allowed to spawn "
            f"'{requested_canonical}'. Caller agent not found. "
            "Allowed team members: []"
        )

    # Use the caller's canonical id from the registry as the basis for
    # team_members matching.
    caller_canonical = caller_meta.id
    raw_members = caller_meta.team_members or []

    # Canonicalize each member so a renamed team member still matches
    # the requested agent_id consistently.
    allowed_canonical: set[str] = set()
    for member in raw_members:
        canonical = registry.resolve_pure_id(member)
        if canonical is not None:
            allowed_canonical.add(canonical)

    if requested_canonical not in allowed_canonical:
        allowed_display = sorted(allowed_canonical) if allowed_canonical else []
        return (
            f"Agent '{caller_canonical}' is not allowed to spawn "
            f"'{requested_canonical}'. Allowed team members: {allowed_display}"
        )

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
        if instance_meta and instance_meta.project_id:
            project = manager._project_repository.get(instance_meta.project_id)
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


async def _resolve_instance_id(
    manager: "InstanceManager",
    instance_id: str | None,
) -> str:
    """Resolve instance_id with fuzzy matching fallback.

    First tries exact match. On KeyError, attempts fuzzy matching with
    max_distance=DEFAULT_FUZZY_MATCH_DISTANCE to find all near matches. Raises ValueError with
    helpful error message including suggestions if available.

    Args:
        manager: The InstanceManager instance.
        instance_id: The instance ID to resolve.

    Returns:
        The instance_id if found exactly.

    Raises:
        ValueError: If instance not found, with suggestion if near match(es) exist.
    """
    # Input validation
    if not instance_id:
        raise ValueError("ERROR: instance_id cannot be empty")

    try:
        # First try exact match - this is the fast path
        await manager.get_instance(instance_id)
        return instance_id
    except KeyError:
        # Exact match failed - try fuzzy matching
        near_matches = manager.find_near_instance(instance_id, max_distance=DEFAULT_FUZZY_MATCH_DISTANCE)
        if near_matches:
            if len(near_matches) == 1:
                raise ValueError(
                    f"ERROR: instance '{instance_id}' not found. "
                    f"Did you mean '{near_matches[0]}'? Please retry with the corrected instance_id."
                )
            else:
                # Multiple matches — list all candidates
                candidates = "', '".join(near_matches)
                raise ValueError(
                    f"ERROR: instance '{instance_id}' not found. Multiple similar instances found: "
                    f"'{candidates}'. Please retry with the correct instance_id."
                )
        else:
            raise ValueError(
                f"ERROR: instance '{instance_id}' not found and no similar instance found. "
                f"Please check the instance ID or spawn a new instance for your task."
            )


# Custom attributes set on tool functions/StructuredTools by the
# registration decorators in ``daemon.tools._tool_registry`` (``_tool_category``
# for the category itself, ``_tool_category_first_party`` as the provenance
# marker that gate category overrides in scan_tools_for_full_docs, and
# ``_full_doc_`` for full documentation). The workdir/instance-id wrappers
# rebuild the StructuredTool via ``from_function`` which does NOT propagate
# these — losing them makes the downstream metadata scan infer the wrong
# category (e.g. ``list_directory`` ends up under ``"list"`` instead of
# ``"filesystem"``), which silently breaks the ``tools.allow`` category
# filter. See instance.py:_make_workdir_aware.
_TOOL_INHERITED_ATTRS = (
    "_tool_category",
    "_tool_category_first_party",
    "_full_doc_",
)


def _rebuild_structured_tool(tool, wrapped_func) -> "StructuredTool":
    """Rebuild a StructuredTool preserving metadata attributes.

    ``StructuredTool.from_function`` only copies ``name`` / ``description`` /
    ``args_schema``; any custom attributes set by ``@register_tool_category``
    or ``_full_doc_`` registrations are dropped. This copy-step restores
    them so that ``scan_tools_for_full_docs`` sees the correct category
    instead of falling back to ``tool_name.split('_')[0]``.

    Args:
        tool: The original StructuredTool (source of attributes).
        wrapped_func: The wrapped callable to use as the new tool's func.

    Returns:
        A new StructuredTool sharing the original's name/description/schema
        plus any inherited metadata attributes.
    """
    from langchain_core.tools import StructuredTool

    if asyncio.iscoroutinefunction(wrapped_func):
        new_tool = tool.__class__.from_function(
            func=wrapped_func,
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            coroutine=wrapped_func,
        )
    else:
        new_tool = tool.__class__.from_function(
            func=wrapped_func,
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
        )
    # Propagate custom metadata attributes lost by from_function().
    # pydantic v2 StructuredTool accepts underscore-prefixed private attrs
    # silently on vanilla instances, but a frozen/read-only subclass
    # (or a future langchain_core version) could raise; catch broadly and
    # log so the silent dependency on the warmup registry is observable
    # rather than a hidden bug.
    for attr in _TOOL_INHERITED_ATTRS:
        if hasattr(tool, attr):
            value = getattr(tool, attr)
            try:
                setattr(new_tool, attr, value)
            except Exception as exc:  # noqa: BLE001 — broad on purpose
                logger.warning(
                    "Failed to propagate %s onto rebuilt StructuredTool "
                    "%r: %s. Category filter will fall back to the warmup "
                    "registry entry if present.",
                    attr, getattr(tool, "name", "<unknown>"), exc,
                )
    return new_tool


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

        # Rebuild StructuredTool via shared helper so _tool_category / _full_doc_
        # metadata is preserved (lossy in StructuredTool.from_function).
        return _rebuild_structured_tool(tool, wrapped_func)
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

        # functools.wraps already copies __dict__ for plain functions; no
        # extra propagation needed.
        return wrapped_func


def _make_instance_id_aware(
    tool,  # Can be a function or StructuredTool
    get_default_instance_id: Callable[[], str | None]
):
    """Wrap a tool to auto-inject instance_id from a closure.

    Mirrors ``_make_workdir_aware`` but keeps instance ownership out of
    LLM-controlled arguments.

    Args:
        tool: The tool to wrap (function or StructuredTool)
        get_default_instance_id: Callable that returns the default instance ID

    Returns:
        Wrapped tool with auto instance-id support
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
                if kwargs.get('instance_id') is None:
                    kwargs['instance_id'] = get_default_instance_id()
                return await original_func(*args, **kwargs)
        else:
            @wraps(original_func)
            def wrapped_func(*args, **kwargs):
                if kwargs.get('instance_id') is None:
                    kwargs['instance_id'] = get_default_instance_id()
                return original_func(*args, **kwargs)

        # Rebuild StructuredTool via shared helper so _tool_category / _full_doc_
        # metadata is preserved (lossy in StructuredTool.from_function).
        return _rebuild_structured_tool(tool, wrapped_func)
    else:
        # It's a plain function - wrap it directly
        func = tool

        # Check if async
        is_async = asyncio.iscoroutinefunction(func)

        if is_async:
            @wraps(func)
            async def wrapped_func(*args, **kwargs):
                if kwargs.get('instance_id') is None:
                    kwargs['instance_id'] = get_default_instance_id()
                return await func(*args, **kwargs)
        else:
            @wraps(func)
            def wrapped_func(*args, **kwargs):
                if kwargs.get('instance_id') is None:
                    kwargs['instance_id'] = get_default_instance_id()
                return func(*args, **kwargs)

        return wrapped_func


def create_job_tools_if_available(manager, current_instance_id: str, agent_id: str) -> list:
    """Create job tools if job services are available on the manager."""
    job_service = getattr(manager, '_job_queue_service', None)
    if job_service is None:
        return []
    queue_mgmt_service = getattr(manager, '_job_queue_mgmt_service', None)
    dead_letter_service = getattr(manager, '_dead_letter_service', None)
    if queue_mgmt_service is None or dead_letter_service is None:
        return []

    # Get watcher_repo from manager (may be None)
    watcher_repo = getattr(manager, '_watcher_repo', None)

    return create_job_tools(
        job_service=job_service,
        queue_mgmt_service=queue_mgmt_service,
        dead_letter_service=dead_letter_service,
        current_instance_id=current_instance_id,
        agent_id=agent_id,
        watcher_repo=watcher_repo,
        manager=manager,
    )


class SpawnInstanceInput(BaseModel):
    """Input model for spawn_instance tool."""

    agent_id: Annotated[str, Field(
        description="Agent ID (e.g., 'developer', 'leader')"
    )]

    project_id: Annotated[str | None, Field(
        default=None,
        description="Optional project ID for context injection. Pass None or 'null' if no project context is needed."
    )] = None

    instance_name: Annotated[str | None, Field(
        default=None,
        description="Optional short name for the instance (e.g., 'create-feature-a', 'fix-bug-b'). Used in completion reports."
    )] = None

    model: Annotated[str | None, Field(
        default=None,
        description=(
            "Optional LLM model to use for this instance. If provided AND in the "
            "allowed models list (config.llm.allowed_models), it overrides the "
            "default model with HIGHEST priority — overriding meta.json's "
            "llm_model and the env OPENAI_MODEL. If the list is non-empty and "
            "this model is NOT in it, the override is silently ignored and the "
            "default model is used."
        ),
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
        agent_id: The agent identifier (e.g., "developer").
    
    Returns:
        List of tool functions
    """
    
    logger = logging.getLogger(__name__)
    
    # Create a closure to get the current instance's project workdir
    def get_current_workdir() -> str | None:
        return _get_project_workdir(manager, current_instance_id)

    def get_current_instance_id() -> str | None:
        return current_instance_id

    # Capture the caller's agent_id from the outer ``create_instance_tools``
    # scope. The ``spawn_instance`` tool's parameter is named ``agent_id``
    # too (it shadows the outer var), so we pin the caller's id here under
    # a distinct name for the team_members authorization check.
    caller_agent_id: str = agent_id or ""

    @register_tool_category("instance")
    @tool(args_schema=SpawnInstanceInput)
    async def spawn_instance(agent_id: Annotated[str, Field(description="Agent ID (e.g., 'developer', 'leader')")], project_id: Annotated[str | None, Field(default=None, description="Optional project ID for context injection. Pass None or 'null' if no project context is needed.")] = None, instance_name: Annotated[str | None, Field(default=None, description="Optional short name for the instance (e.g., 'create-feature-a', 'fix-bug-b').")] = None, model: Annotated[str | None, Field(default=None, description="Optional LLM model override for this instance (highest priority — overrides meta.json and env). If provided but not in config.llm.allowed_models, silently falls back to default.")] = None) -> str:
        """Spawn a new agent instance and return its instance_id.

        IMPORTANT: After spawning, you MUST use send_message(instance_id, message)
        to communicate with the new instance. The spawned instance will not do anything
        until you send it a message.

        Args:
            agent_id: Agent ID to spawn (e.g., 'developer', 'leader').
            project_id: Optional project ID for context injection. Use None or 'null' if no project context is needed.
            instance_name: Optional short name for the instance (e.g., 'create-feature-a', 'fix-bug-b').
            model: Optional LLM model override. If provided and in the allowed models
                list (config.llm.allowed_models), it overrides the default model with
                the HIGHEST priority — above meta.json's llm_model and the env OPENAI_MODEL.
                If the list is non-empty and the model is not in it, the override is
                silently ignored and the default model is used.

        Returns:
            The instance_id of the newly spawned instance. Use this with send_message().
        """
        # ─── Authorization gate (BEFORE any DB transaction) ───────────────
        # The caller agent (the instance invoking this tool) is the closure
        # variable ``caller_agent_id``. Check that the requested
        # ``agent_id`` is in the caller's ``meta.json`` ``team_members`` list
        # before doing any work. Deny-by-default: missing/empty team_members
        # rejects all spawns. Both sides are canonicalized via the registry
        # to handle any future renames consistently.
        if not caller_agent_id:
            return (
                "ERROR: spawn_instance invoked without a caller agent_id. "
                "This is a wiring/configuration bug — the instance tools were "
                "created without an agent_id. Spawn is denied."
            )
        if not agent_id:
            return (
                "ERROR: agent_id is required to spawn_instance. "
                "Pass a non-empty agent_id (e.g. 'developer', 'leader')."
            )
        membership_error = _check_team_membership(caller_agent_id, agent_id)
        if membership_error is not None:
            return f"ERROR: {membership_error}"

        try:
            # Auto-inherit project_id from parent if not explicitly provided
            if project_id is None:
                project_id = _get_instance_project_id(manager, current_instance_id)
                project_id = normalize_project_id(project_id)

            new_instance_id, validated_model_override = manager.spawn_instance(
                agent_id=agent_id,
                instance_id=None,
                parent_id=current_instance_id,
                project_id=project_id,
                instance_name=instance_name,
                model=model,
            )
            # Surface a silent-fallback notice (Fix 2 / security review):
            # if the caller supplied a model that's not in
            # ``config.llm.allowed_models``, the spawn service silently
            # fell back to the default model. The calling agent needs to
            # know its requested model was rejected (cost, latency, and
            # capability differ across models). We use the SAME validated
            # value returned by ``spawn_instance`` (no second
            # ``_resolve_model_override`` call) — closes the TOCTOU window
            # where a mid-flight ``allowed_models`` mutation could yield
            # a different notice than the model actually applied. The
            # notice is ``None`` when no caller model was supplied, or
            # when the model is in the allow-list — preserving the
            # success-only path.
            fallback_notice = manager._lifecycle_service._format_model_fallback_notice(
                model, validated_model_override
            )
            return (
                f"Successfully spawned instance: {new_instance_id}\n"
                f"To communicate with this instance, use: send_message(instance_id=\"{new_instance_id}\", message=\"your message here\")"
                f"{fallback_notice or ''}"
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
    
    @register_tool_category("instance")
    @tool
    async def send_message(
        instance_id: str,
        message: str,
        load_skill: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Optional skill name to load on the recipient instance "
                    "(e.g., 'unit-test'). When provided, a <meta>{\"load_skill\": "
                    "\"<name>\"}</meta> tag is appended to the message so the "
                    "skill is injected into the recipient's context for clean 1:1 "
                    "attribution. Omit or pass None for backward-compatible "
                    "behavior."
                ),
            ),
        ] = None,
    ) -> str:
        """Send a message to another instance's input queue. Use tool_help("send_message") for details.

        Args:
            instance_id: The ID of the target instance.
            message: The message content to send.
            load_skill: Optional skill name (e.g. 'unit-test'). When provided, a
                ``<meta>{"load_skill": "<name>"}</meta>`` tag is appended to the
                message so the skill is injected into the recipient's context for
                clean 1:1 attribution. Omit or pass None for backward-compatible
                behavior (no meta-tag appended).
        """
        # ── load_skill sugar: append <meta> tag before enqueue ─────────────
        # This is purely syntactic sugar. The existing meta-tag parser
        # (daemon/services/skill_meta_parser.py) and injection pipeline
        # (daemon/services/instance_messaging.py) consume the tag. We do NOT
        # touch those modules — we only generate the tag string here.
        if load_skill is not None and str(load_skill).strip():
            _payload = json.dumps({"load_skill": str(load_skill).strip()})
            message = message + f"\n<meta>{_payload}</meta>"

        # Validate instance exists with fuzzy matching for typos
        try:
            await _resolve_instance_id(manager, instance_id)
        except ValueError as e:
            return str(e)

        # Check if instance is terminated or errored
        # NOTE: `to_dict()` returns the live status field, NOT a `terminated`
        # boolean (the old `instance_info.get("terminated")` guard was always
        # false because that key doesn't exist, so dead instances were never
        # rejected). Status is stored as the enum's string value.
        from ..repositories.instance.models import InstanceStatus
        instance_info = manager.get_instance_info(instance_id)
        if instance_info.get("status") in (
            InstanceStatus.TERMINATED.value,
            InstanceStatus.ERROR.value,
        ):
            return (
                f"ERROR: Instance '{instance_id}' is terminated/errored "
                f"(status={instance_info.get('status')}). Cannot send message."
            )

        # Check if there's already a message in progress (pending or processing)
        stats = await manager.get_queue_stats(instance_id)
        if stats["pending_count"] > 0 or stats["processing_count"] > 0:
            return (
                f"ERROR: Instance '{instance_id}' already has a message in progress. "
                f"Pending: {stats['pending_count']}, Processing: {stats['processing_count']}. "
                "Please wait for the current message to complete before sending another."
            )

        # Enqueue the message via worker pool (creates MessageQueue + Task atomically).
        # send_message is ALWAYS agent-to-agent (internal orchestration) and
        # therefore MUST NOT create a JobItem mirror — only external entry
        # points (POST /messages, chat adapters, scheduler) create JobItems.
        # The 06f500af-class bugs were caused by letting internal traffic
        # mint JobItems.
        result = await manager.enqueue_message(
            instance_id=instance_id,
            message=message,
            source=f"internal_agent:{current_instance_id}"
        )
        message_id = result.message_id

        # Resolve the freshly-created child task id. The DependencyBus
        # keys watchers on the child task id, so we look up the task
        # the ``enqueue_message`` call just wrote.
        child_task = None
        _task_repo = getattr(manager, "_task_repo", None)
        if _task_repo is not None:
            child_task = await asyncio.to_thread(
                _task_repo.get_by_message, message_id
            )
        else:
            logger.warning(
                "manager._task_repo is missing — cannot resolve child task id"
            )
            return ("ERROR: manager._task_repo is missing; cannot register "
                    "dependency_bus watcher. Parent-child coordination unavailable.")

        # Register watcher when sender is the parent of the target instance.
        from sqlmodel import Session
        from ..repositories.instance.models import Instance
        from ..write_pause_guard import WriteGuardSession
        with WriteGuardSession(Session(manager.engine), manager.write_guard) as session:
            target_instance = session.get(Instance, instance_id)
            if target_instance and target_instance.parent_id == current_instance_id:
                # ─── Bus is the SOLE completion authority ───
                # The DependencyBus is the sole parent→child correlation
                # authority. The legacy SQL increment + parent-revive
                # UPDATE were removed with the
                # ``USE_LEGACY_WAITING_FOR_CASCADE`` flag in Phase 3, and
                # ``CorrelationManager`` (the prior sole authority) was
                # removed in Phase 5 — there is no alternative path to
                # fall back on.
                #
                # The bus path is the unconditional behavior of
                # ``send_message``: call ``bus.watch(...)`` to register a
                # ``FollowUp`` keyed on the child task id. The bus stores
                # a PENDING row in ``dependency_watchers`` and fires the
                # follow-up on terminal event via ``emit_terminal`` (called
                # from ``child_reports`` / ``error_reporting``).
                if child_task is not None:
                    # ─── Bus path: register a PENDING watcher ────────────
                    from daemon.services.dependency_bus import (
                        FollowUp,
                        get_dependency_bus,
                    )
                    _bus = get_dependency_bus()
                    if _bus is None:
                        # Bus singleton missing is a wiring failure
                        # (the bus is mandatory).
                        logger.warning(
                            "Bus singleton is None — bus wiring "
                            "failure (no fallback)"
                        )
                    else:
                        try:
                            _follow_up = FollowUp(
                                target_instance_id=current_instance_id,
                                message=(
                                    f"[dependency_bus] child {instance_id} "
                                    f"completed for message {message_id}"
                                ),
                                source=f"internal_agent:{current_instance_id}",
                                metadata={
                                    "kind": "child_complete",
                                    "child_id": instance_id,
                                    "parent_id": current_instance_id,
                                    "message_id": message_id,
                                },
                            )
                            await _bus.watch(
                                source_task_id=str(child_task.id),
                                follow_up=_follow_up,
                            )
                            logger.debug(
                                f"bus.watch registered: child_task="
                                f"{str(child_task.id)[:8]}..., "
                                f"parent={current_instance_id[:8]}..., "
                                f"child={instance_id[:8]}..., "
                                f"message={message_id[:8]}...",
                                extra={"completion_delivery_path": "bus"},
                            )
                        except Exception as hook_err:
                            logger.warning(
                                f"bus hook: watch failed "
                                f"(parent={current_instance_id[:8]}, "
                                f"child={instance_id[:8]}, "
                                f"task={str(child_task.id)[:8]}): {hook_err}"
                            )
                            # Surface the failure so the agent sees an
                            # ERROR string and the caller can decide
                            # whether to retry.
                            return (
                                f"ERROR: Failed to register message "
                                f"correlation (dependency_bus): {hook_err}"
                            )

        return (
            f"Message queued and sent to {instance_id}. Do NOT poll or "
            f"sleep waiting for the result — END YOUR TURN now (stop "
            f"calling tools, produce your final response). The system "
            f"will deliver the completion report to you automatically "
            f"the moment the child finishes, as a new message that "
            f"resumes your turn. Polling or holding your turn open will "
            f"NOT make the report arrive faster."
        )
    
    send_message._full_doc_ = """Send a message to another instance's input queue.

The message is queued and processed asynchronously. The target
instance will process the message and send a completion report
back if it's a child instance.

Args:
    instance_id: The ID of the target instance to send the message to
    message: The message content to send
    load_skill: Optional skill name (e.g. 'unit-test'). When provided,
        a ``<meta>{"load_skill": "<name>"}</meta>`` tag is appended to the
        message so the skill is injected into the recipient's context for
        clean 1:1 attribution. Omit or pass None for backward-compatible
        behavior (no meta-tag appended).

Returns:
    The message_id for tracking (queue is async, response comes later)
"""
    
    @register_tool_category("instance")
    @tool
    async def terminate_instance(instance_id: str) -> dict:
        """Terminate an instance. Use with caution. Use tool_help("terminate_instance") for details."""
        # Validate instance exists with fuzzy matching for typos
        try:
            await _resolve_instance_id(manager, instance_id)
        except ValueError as e:
            return {"error": str(e), "terminated": False}
        result = await manager.terminate_instance(instance_id)
        return {"terminated": result}
    
    terminate_instance._full_doc_ = """Terminate an instance. Use with caution.

Args:
    instance_id: The ID of the instance to terminate

Returns:
    dict with "terminated" key: {"terminated": True} on success, {"error": ..., "terminated": False} on error
"""
    
    @register_tool_category("instance")
    @tool
    def list_instances() -> list[dict]:
        """List all active instances. Use tool_help("list_instances") for details."""
        instances, _ = manager.list_instances(limit=DEFAULT_PAGE_LIMIT)
        return instances

    list_instances._full_doc_ = f"""List the {DEFAULT_PAGE_LIMIT} most recent active instances.

Returns:
    List of instance info dictionaries
"""
    
    @register_tool_category("instance")
    @tool
    async def get_instance_info(instance_id: str) -> dict:
        """Get information about a specific instance. Use tool_help("get_instance_info") for details."""
        # Validate instance exists with fuzzy matching for typos
        try:
            await _resolve_instance_id(manager, instance_id)
        except ValueError as e:
            return {"error": str(e)}
        result = manager.get_instance_info(instance_id)
        if isinstance(result, dict) and "error" not in result:
            result["_system_note"] = _FIRE_AND_FORGET_NOTE
        return result

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
    # and job queue management service for system queue provisioning
    queue_mgmt_service = getattr(manager, '_job_queue_mgmt_service', None)
    project_tools = create_project_tools(
        manager.project_store,
        current_instance_id,
        agent_id,
        job_queue_mgmt_service=queue_mgmt_service,
    )
    
    # Create workdir-aware wrappers for filesystem tools
    # These auto-populate workdir from project's main_directory when not provided
    bash_aware = _make_instance_id_aware(
        _make_workdir_aware(bash, get_current_workdir),
        get_current_instance_id,
    )
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

    # ── Background process tools (proc_*, always available — same base
    # layer as bash). Created without workdir auto-injection because
    # proc_run accepts an explicit ``workdir`` arg; relying on the
    # caller (or proc_run internals) keeps the tool surface uniform
    # with bash for cases where the agent intentionally overrides
    # the project directory.
    proc_tool_list = create_proc_tools(current_instance_id)
    tools.extend(proc_tool_list)

    # Critical notes tools (project-scoped notes management)
    cn_tools = create_critical_notes_tools(
        manager.project_store, current_instance_id, agent_id
    )
    tools.extend(cn_tools)

    # Project history tools (chronological project event tracking)
    history_tools = create_project_history_tools(
        manager.project_store, current_instance_id, agent_id
    )
    tools.extend(history_tools)
    
    # Create job tools if job service is available
    job_tools = create_job_tools_if_available(manager, current_instance_id, agent_id)
    tools.extend(job_tools)
    
    # Add mother tools if this is the _mother agent
    if agent_id == "_mother":
        mother_tools = create_mother_tools(manager, current_instance_id)
        tools.extend(mother_tools)

    # Create and add RAG tools (only when RAG is configured)
    if is_rag_enabled():
        rag_tool_list = create_rag_tools(manager, current_instance_id)
        tools.extend(rag_tool_list)

        knowledge_tool_list = create_knowledge_tools(manager, current_instance_id)
        tools.extend(knowledge_tool_list)

    # ── OpenCode tools (external system integration, always available) ──
    # NOTE: NOT inside the is_rag_enabled() block — these are always available.
    opencode_tool_list = create_opencode_tools(manager, current_instance_id)
    tools.extend(opencode_tool_list)

    # ── Chart tools (delegates to Charter agent for Mermaid diagrams, always available) ──
    # NOTE: NOT inside the is_rag_enabled() block — these are always available,
    # matching the OpenCode pattern above. The chart category is auto-granted
    # to agents with innate_skills:["chart"] via INNATE_SKILL_TOOL_CATEGORIES.
    chart_tool_list = create_chart_tools(manager, current_instance_id)
    tools.extend(chart_tool_list)

    # ── Image tools (delegates to image-reader agent for vision analysis, always available) ──
    # Image tools (always available, like chart tools)
    image_tools = create_image_tools(manager, current_instance_id)
    tools.extend(image_tools)

    # ── Todo tools (per-instance todo list with SSE emission, always available) ──
    # NOTE: NOT inside the is_rag_enabled() block — these are always available,
    # matching the chart/opencode pattern. The todo category is auto-granted to
    # agents with innate_skills:["todo"] via INNATE_SKILL_TOOL_CATEGORIES.
    # Pass manager._live_hub defensively via getattr so the factory still works
    # in tests/environments where the hub is not wired up.
    todo_tool_list = create_todo_tools(
        manager,
        current_instance_id,
        getattr(manager, "_live_hub", None),
    )
    tools.extend(todo_tool_list)

    # ── Question tools (per-instance user-pause-and-answer flow, always available) ──
    # Mirrors the todo wiring above. The single ``question`` tool stores a
    # QuestionPack, emits a ``question_pack`` SSE event, sets the pause flag,
    # and the conditional post-tools edge in ``daemon.graph`` routes the
    # graph to ``question_pause_node`` (which sets the deferred-pause
    # marker — the actual ``pause_instance_cascade`` runs from the post-
    # graph completion path; see C2 fix). The user answers via the Phase 2
    # answer API (``POST /api/instances/{id}/answer``).
    question_tool_list = create_question_tools(
        manager,
        current_instance_id,
        getattr(manager, "_live_hub", None),
    )
    tools.extend(question_tool_list)

    # ── Dynamic Skill tools (per-instance dynamic-skill surface, always available) ──
    # Mirrors the todo/chart pattern above. These tools are auto-granted to
    # agents with innate_skills:["dynamic-skill"] via INNATE_SKILL_TOOL_CATEGORIES.
    # All 6 tools soft-fail when their underlying service is not yet wired
    # to the manager, so importing this module is always safe.
    skill_tool_list = create_skill_tools(manager, current_instance_id)
    tools.extend(skill_tool_list)

    # ── Skill Evolution tools (for the skill-keeper agent; Phase 2 stubs) ──
    # Auto-granted to agents with innate_skills:["skill-evolution"]. These wrap
    # SkillEvolutionService methods (Phase 5) and currently return soft-fail
    # stub messages when the service is absent.
    skill_evo_tools = create_skill_evolution_tools(manager, current_instance_id)
    tools.extend(skill_evo_tools)

    # ── Database tools (external DB connection management, always available) ──
    # C3: Pass shared repository and pool_manager from the manager — these are
    # singletons at the InstanceManager level, not created here. This prevents
    # pool proliferation (N instances × M pools would be wasteful and cause
    # connection-count exhaustion against the upstream Postgres).
    db_tool_list = create_db_tools(
        manager,
        current_instance_id,
        repository=manager.db_connection_repository,
        pool_manager=manager.db_pool_manager,
    )
    tools.extend(db_tool_list)

    # ── Infrastructure tools (asset / type-registry / history) ──
    # Pass the shared infra repository from the manager — this is a
    # singleton at the InstanceManager level, not created here. The
    # repository's audit columns (created_by / updated_by / deleted_by)
    # are auto-populated with current_instance_id inside the factory.
    infra_tool_list = create_infra_tools(
        manager,
        current_instance_id,
        repository=manager.infra_repository,
    )
    tools.extend(infra_tool_list)

    # ── Context tools (list/read shared context directory) ──
    # Always available — internal agents need this to inspect accumulated context
    # without exposing the on-disk path. The hosted MCP server exposes the
    # equivalent tools for external agent systems.
    context_tool_list = create_context_tools(manager, current_instance_id)
    tools.extend(context_tool_list)

    # ── Shared context metadata tools (KV upsert/delete/clear) ──
    # Always available — internal agents store and read lightweight metadata
    # (e.g., "last_seen", "topic", "user_locale") keyed by the context_key
    # partition. Auto-resolves context_key from the caller via closure.
    shared_context_tool_list = create_shared_context_tools(manager, current_instance_id)
    tools.extend(shared_context_tool_list)

    # ── System tools (read-only env / config / health snapshots) ──
    # Always available — internal agents use these for fast triage of
    # runtime state (which DB backend, which config section is loaded,
    # what env vars are in scope) without exposing the on-disk paths.
    # Secrets are masked by default; agents must opt into ``nomask=True``
    # to see raw values.
    system_tool_list = create_system_tools(manager, current_instance_id)
    tools.extend(system_tool_list)

    # ── MCP tools: load BEFORE creating help tool so we have the names ──
    # IMPORTANT: MCP tools MUST be loaded BEFORE help tool creation
    # because create_help_tool needs MCP tool names for category expansion.
    mcp_tools = _load_mcp_tools(manager, current_instance_id)
    mcp_tool_names: list[str] = []
    if mcp_tools:
        # Extract MCP tool names for help tool and system prompt
        mcp_tool_names = [
            getattr(t, 'name', None) or getattr(getattr(t, 'func', None), '__name__', None)
            for t in mcp_tools
        ]
        mcp_tool_names = [n for n in mcp_tool_names if n]  # Filter None
        logger.info(f"Loaded {len(mcp_tools)} MCP tools for instance {current_instance_id[:8]}: {mcp_tool_names[:5]}...")

    # Add MCP tools BEFORE creating help tool so mcp_tool_names are available
    if mcp_tools:
        tools.extend(mcp_tools)

    # ── Language tools (language preference check, always available) ──
    # Phase 2 wiring: register language_tools module. MUST run BEFORE
    # scan_tools_for_full_docs(tools) so the help tool sees the language
    # category for documentation expansion.
    language_tool_list = create_language_tools()
    tools.extend(language_tool_list)

    # Create help tool - needs mcp_tool_names for MCP category expansion
    help_tool = create_help_tool(tools, agent_id, mcp_tool_names)
    tools.append(help_tool)

    # Scan ALL tools (including MCP + help) to populate _tool_metadata
    # MUST run after all tools are added to the list
    scan_tools_for_full_docs(tools)
    
    # Apply tool filtering based on agent's tools config
    tools = _apply_tool_filter(tools, agent_id, mcp_tool_names)
    
    return tools


def _apply_tool_filter(tools: list[Any], agent_id: str, mcp_tool_names: list[str] | None = None) -> list[Any]:
    """Apply tool filtering based on agent's tools configuration.
    
    Args:
        tools: List of all tools (before filtering)
        agent_id: The agent identifier to look up tools config
        mcp_tool_names: Optional list of MCP tool names for category expansion.
        
    Returns:
        Filtered list of tools based on agent's tools config.
        Returns all tools if no config or config is empty.
    """
    # Import registry locally to avoid circular imports
    from ..registry import get_registry

    # Get agent metadata
    registry = get_registry()
    agent_meta = registry.get_resolved(agent_id)

    if agent_meta is None or agent_meta.tools is None:
        # No tools config → all tools allowed (backward compatible)
        return tools

    # Collect all tool names for MCP category expansion
    all_tool_names: set[str] = set()
    
    # Add MCP tool names first (they may not be in the tools list yet)
    if mcp_tool_names:
        all_tool_names.update(mcp_tool_names)
    
    # Add tool names from the tools list
    for tool in tools:
        tool_name = getattr(tool, 'name', None)
        if tool_name is None:
            # Fallback: try to get from func
            func = getattr(tool, 'func', None) or getattr(tool, 'coroutine', None)
            if func:
                tool_name = getattr(func, '__name__', None)
        if tool_name:
            all_tool_names.add(tool_name)

    # Resolve the filter with MCP-aware category expansion.
    # Innate skills (e.g. "opencode") implicitly grant the tool categories
    # they require, so the agent does not have to repeat them in `tools.allow`.
    effective_allow = expand_allow_for_innate_skills(
        agent_meta.tools.allow,
        agent_meta.innate_skills,
    )
    allowed_tools = resolve_tool_filter(
        allow=effective_allow,
        deny=agent_meta.tools.deny,
        all_tool_names=all_tool_names,
    )

    # If None returned, all tools are allowed
    if allowed_tools is None:
        return tools

    # Filter tools by name
    filtered_tools = []
    for tool in tools:
        tool_name = getattr(tool, 'name', None)
        if tool_name is None:
            # Fallback: try to get from func
            func = getattr(tool, 'func', None) or getattr(tool, 'coroutine', None)
            if func:
                tool_name = getattr(func, '__name__', None)

        if tool_name is None:
            logger.warning(f"Tool has no name attribute — skipping filter for: {type(tool)}")
            continue

        if tool_name and tool_name in allowed_tools:
            filtered_tools.append(tool)

    if len(filtered_tools) < len(tools):
        logger.debug(f"Filtered tools for {agent_id}: {len(tools)} → {len(filtered_tools)} "
                     f"(removed: {set(t.name for t in tools if hasattr(t, 'name')) - allowed_tools})")

    return filtered_tools

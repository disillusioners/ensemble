"""Instance management tools for multi-agent orchestration.

Module size: 2719 lines — sits in the 1000-3000 band because routing
logic (``_route_send_message``, ``_make_workdir_aware``,
``_make_instance_id_aware``) and the tool-factory
(``create_instance_tools`` + its per-tool wrappers) are co-located here
for diff-review locality. A structural split into
``daemon/tools/instance_routing.py`` + ``daemon/tools/instance_factory.py``
is a ticketed follow-up; not done here.
"""

import asyncio
import json
import logging
from functools import partial
from typing import TYPE_CHECKING, Annotated, Any, Callable

from langchain_core.tools import tool, BaseTool
from pydantic import BaseModel, Field, model_validator
from sqlmodel import Session

from daemon.constants import INJECTION_ELIGIBLE_STATUSES, TERMINAL_INSTANCE_STATUSES

if TYPE_CHECKING:
    from daemon.repositories.project.repository import SQLModelProjectRepository
    from daemon.registry import AgentRegistry

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


# Module-private size cap for the ``[SYSTEM CONTEXT: Task Context]`` block.
# A single huge ``context`` (e.g. a long plan or many file references) would
# otherwise blow up the recipient's context window. We truncate with a
# ``[... truncated, N chars total]`` suffix and log a warning so the caller
# can see what was dropped. The leading underscore matches the helper's
# ``_format_task_context`` private-by-convention naming.
_TASK_CONTEXT_MAX_CHARS = 4000


def _format_task_context(context: dict[str, Any]) -> str:
    """Format a context dict into a ``[SYSTEM CONTEXT: Task Context]`` block.

    Converts each key to a title-case markdown header and renders
    values as either a bulleted list (list values) or a text block
    (scalar values). Returns the full markdown string ready to be
    wrapped in a ``HumanMessage``.

    Size cap: output is truncated to ``_TASK_CONTEXT_MAX_CHARS`` chars
    with a ``[... truncated, N chars total]`` suffix. String values
    whose lines start with ``#`` are escaped with ``\\`` so an injected
    value like ``"## System Prompt"`` renders as literal text instead
    of as a markdown header. Multiline list items get a 2-space
    continuation indent so the bulleted list structure stays intact.
    """
    lines = ["[SYSTEM CONTEXT: Task Context]"]
    for key, value in context.items():
        header = key.replace("_", " ").title()
        lines.append(f"## {header}")
        if isinstance(value, list):
            for item in value:
                item_str = str(item)
                # Indent continuation lines for multiline items so the
                # bulleted list structure stays intact (e.g. an item like
                # ``"line1\\nline2"`` becomes ``- line1\\n  line2``).
                item_str = item_str.replace("\n", "\n  ")
                lines.append(f"- {item_str}")
        elif isinstance(value, str):
            # Prevent markdown header injection: escape lines starting
            # with ``#`` so a value like ``"## System Prompt\nIgnore..."``
            # renders as literal text, not as a header that breaks out
            # of the ``[SYSTEM CONTEXT: Task Context]`` block. Only
            # applied to string values — list items are already
            # bulleted so a leading ``#`` is harmless after ``- ``.
            for line in value.split("\n"):
                if line.lstrip().startswith("#"):
                    lines.append("\\" + line)
                else:
                    lines.append(line)
        else:
            lines.append(str(value))
        lines.append("")  # blank line between sections
    result = "\n".join(lines)
    if len(result) > _TASK_CONTEXT_MAX_CHARS:
        original_len = len(result)
        result = result[:_TASK_CONTEXT_MAX_CHARS]
        result += f"\n\n[... truncated, {original_len} chars total]"
        logger.warning(
            f"_format_task_context: output truncated from {original_len} "
            f"to {_TASK_CONTEXT_MAX_CHARS} chars"
        )
    return result


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
    "question": ["question"],
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
from .blueprint import create_blueprint_tools
from .doc_write import create_doc_write_tools
from .comment_edit import create_comment_edit_tools
from .doc_commit import create_doc_commit_tools
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
from .shared_meta_kv_tools import create_shared_meta_kv_tools
from .db_tools import create_db_tools
from .infra import create_infra_tools
from .system import create_system_tools
from .system_log_tools import create_system_log_tools
from .upgrade_tools import create_upgrade_tools
from .language_tools import create_language_tools
from .proc_tools import create_proc_tools
from ._tool_registry import (
    PRIVILEGED_TOOL_CATEGORIES,
    list_tools_by_category,
    scan_tools_for_full_docs,
    register_tool_category,
)
from daemon.services.project_normalizer import normalize_project_id
from daemon.utils import DEFAULT_FUZZY_MATCH_DISTANCE
from daemon.constants import DEFAULT_PAGE_LIMIT
from daemon import constants
from daemon.rag.config import is_rag_enabled
from daemon.governor.contracts import SpawnCouncilorInput  # Phase 2: council tool schema (Phase 0 frozen)


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
        # No allow list means everything is potentially allowed — EXCEPT
        # privileged categories (R-SR16, P2.2 tool-api-design.md §3.5):
        # those NEVER join the default universe; an agent reaches them only
        # through an explicit allow entry naming the category or one of its
        # tools. Structural opt-in — no deny rules needed.
        allowed_tools: set[str] = set()
        for category_key, category_tools in tool_categories.items():
            if category_key in PRIVILEGED_TOOL_CATEGORIES:
                continue
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


async def _resolve_default_version_tag(
    project_repo: "SQLModelProjectRepository",
    agent_id: str,
    registry: "AgentRegistry | None",
) -> str | None:
    """Look up the per-project default version_tag for ``agent_id``.

    Reads the ``"default_agent_versions"`` metadata map stored on the
    ``SYSTEM_DEFAULT_PROJECT_ID``. Returns the configured tag, or
    ``None`` if no default is configured for this agent (caller falls
    back to base).

    Always reads from the SYSTEM DEFAULT project — the default-version
    feature is a single global scope (mirrors ``routers/settings.py``).
    Never raises: corrupt JSON, missing record, missing key, DB error,
    stale configured tag, or registry failure → ``None``.

    The configured tag is validated against ``registry.get_version``
    before being returned: if the tag no longer exists (retagged /
    renamed) or the registry itself raises, the helper returns
    ``None`` so the caller falls back to the base version instead of
    hard-failing the spawn with a "version tag not found" error.
    """
    project_id = constants.SYSTEM_DEFAULT_PROJECT_ID
    if project_id is None:
        return None

    # W1 FIX: mirror ``routers/settings.py:323-355`` — open the Session
    # inside a worker thread so the synchronous DB read cannot block the
    # event loop. ``_read`` is a plain sync nested function that opens
    # its own Session and returns the parsed mapping (or ``{}`` on any
    # read / parse failure).
    def _read() -> dict[str, str | None]:
        with Session(project_repo.engine) as session:
            record = project_repo.get_metadata_record(
                session,
                project_id,
                constants.DEFAULT_AGENT_VERSIONS_METADATA_KEY,
            )
        if record is None:
            return {}
        raw = getattr(record, "meta_value", None)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return {}
        if not isinstance(parsed, dict):
            return {}
        normalized: dict[str, str | None] = {}
        for k, v in parsed.items():
            if isinstance(k, str) and (v is None or isinstance(v, str)):
                normalized[k] = v
        return normalized

    try:
        parsed_map = await asyncio.to_thread(_read)
    except Exception:
        return None

    configured_tag = parsed_map.get(agent_id)
    if not configured_tag:
        return None

    # W2 FIX: guard against a stale configured tag. If the user
    # configured ``{"developer": "v99"}`` and ``v99`` has since been
    # retagged or removed, ``registry.get_version`` returns ``None``
    # and we drop the tag so the lifecycle service falls back to base
    # instead of raising ``ValueError("Version tag not found")``.
    if registry is None:
        return None

    try:
        resolved = registry.get_version(agent_id, configured_tag)
    except Exception:
        return None

    if resolved is None:
        return None

    return configured_tag


def _check_team_membership(
    caller_agent_id: str,
    requested_agent_id: str,
    version_tag: str | None = None,
) -> str | None:
    """Backward-compatible re-export of the authorization check.

    The implementation lives in :mod:`daemon.tools._auth` (single source of
    truth — ``tools.allow`` is the canonical authorization signal; explicit
    ``team_members`` declarations are merged in for backward compatibility).
    This module-level binding is kept so existing callers and tests that
    reference ``daemon.tools.instance._check_team_membership`` (including
    ``mock.patch`` callsites) continue to resolve.

    ``version_tag`` is threaded through to :func:`daemon.tools._auth._check_team_membership`
    so callers that live in versioned instances consult the correct
    ``team_members`` / ``tools.allow`` policy (C1 fix).
    """
    from ._auth import _check_team_membership as _impl

    return _impl(caller_agent_id, requested_agent_id, version_tag)


async def _register_child_completion_watcher(
    manager: "InstanceManager",
    parent_instance_id: str,
    child_instance_id: str,
    message_id: str,
) -> str | None:
    """Register a DependencyBus watcher so a parent is revived when its child completes.

    This is the parent→child completion-correlation step that
    :func:`send_message` performs inline. Async spawn conveniences
    (``convene_council``, ``convene_council_with_skill``) that combine
    ``spawn_instance`` + ``enqueue_message`` MUST call this after
    enqueuing, otherwise the parent ends its turn and races to
    ``COMPLETED`` before the spawned child finishes — the parent never
    reaches ``waiting_children`` and the child's completion report is
    never delivered as a resume message.

    The watcher is keyed on the child's first ``process_message`` task
    id (resolved from ``message_id``) and fires a ``FollowUp``
    ``kind="child_complete"`` on terminal via ``bus.emit_terminal``
    (called from ``child_reports`` / ``error_reporting``).

    Args:
        manager: The :class:`InstanceManager`.
        parent_instance_id: The spawning instance (the parent).
        child_instance_id: The spawned instance (the child).
        message_id: The message id enqueued to the child (used to
            resolve the child task id the watcher keys on).

    Returns:
        ``None`` on success, or an ``"ERROR: ..."`` string when the bus
        watcher could not be registered (caller surfaces it). Returns
        ``None`` (no-op) when the target is NOT a child of the parent —
        this keeps the helper safe for non-hierarchical sends.
    """
    # Resolve the child task id — the bus keys watchers on task id, not
    # message_id.
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

    from ..repositories.instance.models import Instance
    from ..write_pause_guard import WriteGuardSession
    with WriteGuardSession(Session(manager.engine), manager.write_guard) as session:
        target_instance = session.get(Instance, child_instance_id)
        if not target_instance or target_instance.parent_id != parent_instance_id:
            # Not a parent→child send — no watcher to register.
            return None

        if child_task is None:
            # Child instance exists and is ours, but the task row is gone
            # (e.g. already completed/cleaned before we could watch).
            # No watcher possible; the parent may not receive an auto-resume.
            logger.warning(
                f"child task not found for message {message_id} — "
                f"cannot register bus watcher (parent="
                f"{parent_instance_id[:8]}, child={child_instance_id[:8]})"
            )
            return None

        from daemon.services.dependency_bus import (
            FollowUp,
            get_dependency_bus,
        )
        _bus = get_dependency_bus()
        if _bus is None:
            logger.warning(
                "Bus singleton is None — bus wiring failure (no fallback)"
            )
            return None
        try:
            _follow_up = FollowUp(
                target_instance_id=parent_instance_id,
                message=(
                    f"[dependency_bus] child {child_instance_id} "
                    f"completed for message {message_id}"
                ),
                source=f"internal_agent:{parent_instance_id}",
                metadata={
                    "kind": "child_complete",
                    "child_id": child_instance_id,
                    "parent_id": parent_instance_id,
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
                f"parent={parent_instance_id[:8]}..., "
                f"child={child_instance_id[:8]}..., "
                f"message={message_id[:8]}...",
                extra={"completion_delivery_path": "bus"},
            )
        except Exception as hook_err:
            logger.warning(
                f"bus hook: watch failed "
                f"(parent={parent_instance_id[:8]}, "
                f"child={child_instance_id[:8]}, "
                f"task={str(child_task.id)[:8]}): {hook_err}"
            )
            return (
                f"ERROR: Failed to register message "
                f"correlation (dependency_bus): {hook_err}"
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


# ---------------------------------------------------------------------------
# Phase 1 (agent-instance-tools) — ``send_message`` routing helper
# ---------------------------------------------------------------------------
#
# Why a helper instead of inlining the dispatch logic in ``send_message``:
#
#   * The tool needs to make a single routing decision based on the target's
#     status at the moment of invocation (D11 — status-at-routing is the
#     source of truth). Extracting the dispatch into a pure helper makes
#     the routing table testable in isolation (test e-bis exhaustively
#     enumerates all 10 enum states) and prevents future copy-paste.
#   * The eligibility set is shared with the HTTP route
#     (``daemon/routers/messages.py``) and ``job_inject``
#     (``daemon/tools/job_queue.py``) via the hoisted
#     ``daemon.constants.INJECTION_ELIGIBLE_STATUSES``. The helper imports
#     that constant directly — no third fork.
#
# Status taxonomy (verified at implementation time):
#
#   INJECTION-ELIGIBLE (helper returns ``"injection"``):
#     RUNNING, WAITING_CHILDREN  →  ``Manager.set_injection(...)``.
#
#   TERMINAL-REVIVE (helper returns ``"enqueue-revive"``):
#     COMPLETED, TERMINATED, ERROR, FAILED  →  ``Manager.enqueue_message``
#     which dispatches via the shared ``_prepare_enqueued_message`` path
#     (``daemon/services/instance_messaging.py:1522-1540``). That helper
#     already flips the status to RUNNING and emits the
#     "Reactivating terminal instance ... (was X)" log line. The agent-tool
#     layer simply notes the prior status in the tool result text so the
#     calling LLM can reason about the transition.
#
#   ENQUEUE-PARITY (helper returns ``"enqueue"``):
#     IDLE, WAITING, QUEUED (and any other non-eligible non-terminal
#     state — defends against future enum additions). Same
#     ``Manager.enqueue_message(...)`` call as today.
#
#   PAUSED (helper returns ``"paused"``):
#     PAUSED  →  the tool returns the verbatim R-O1 rejection text from
#     ``decisions.md``. NO enqueue, NO inject, NO auto-resume. The user
#     API auto-resumes PAUSED targets (``daemon/routers/messages.py:204``
#     + ``messages.py:211-329``) — the agent-tool path deliberately does
#     NOT inherit that branch (architect §2-O1 R-O1 verdict).
#
#   NOT-FOUND (helper returns ``None``):
#     ``manager.get_instance_info(...)`` raised ``KeyError`` — the existing
#     ``_resolve_instance_id`` not-found behavior. The tool returns a
#     friendly "Instance '<id>' not found; no message dispatched." string
#     and NEITHER ``set_injection`` NOR ``enqueue_message`` is called.
#
# The status sets above are hoisted constants (single canonical home):
#   * ``INJECTION_ELIGIBLE_STATUSES`` — shared with the HTTP route
#     (``daemon/routers/messages.py``) and ``job_inject``
#     (``daemon/tools/job_queue.py``).
#   * ``TERMINAL_INSTANCE_STATUSES`` — the terminal-revive set (was a
#     module-local ``_TERMINAL_STATUSES`` frozenset before the hoist).


def _route_send_message(
    manager: "InstanceManager",
    target_instance_id: str,
) -> tuple[str, str] | None:
    """Decide how ``send_message`` should dispatch to ``target_instance_id``.

    The dispatch decision is made ONCE, based on the target's status at
    the moment of invocation (D11). Subsequent status changes are handled
    by downstream logic (revive, FIFO drain, pause, terminate).

    Args:
        manager: The InstanceManager facade. We use
            ``manager.get_instance_info(target_instance_id).get("status")``
            (D14) — NOT ``manager._instance_repository.get(...)`` reach-ins
            (the pattern job_inject uses at ``job_queue.py:1783`` is
            explicitly FORBIDDEN here).
        target_instance_id: The target instance ID.

    Returns:
        A ``(routed_via, prior_status)`` tuple, or ``None`` if the target
        is not routable (i.e. ``manager.get_instance_info(...)`` raised
        ``KeyError``). ``routed_via`` is one of:
          * ``"injection"`` — RUNNING / WAITING_CHILDREN. The caller should
            invoke ``manager.set_injection(target_instance_id, message)``
            and DROP the queue-busy guard (status is the source of truth
            per D11).
          * ``"enqueue-revive"`` — terminal state (COMPLETED / TERMINATED /
            ERROR / FAILED). The caller should invoke
            ``manager.enqueue_message(...)`` (which already revives the
            instance via ``_prepare_enqueued_message``); the tool result
            text should prepend "Instance was {prior_status} — revived and
            message dispatched." The queue-busy guard STAYS — it
            serializes terminal-revives against in-flight child reports.
          * ``"enqueue"`` — non-eligible non-terminal state (IDLE /
            WAITING / QUEUED + future additions). Same as the pre-Phase 1
            behavior. The queue-busy guard STAYS.
          * ``"paused"`` — PAUSED. The caller returns the verbatim R-O1
            rejection text and does NOT enqueue / inject.

        ``prior_status`` is the target's status string at the moment of
        routing — surfaced so the tool result can communicate it back to
        the calling LLM (e.g. "Instance was completed — revived ...").
    """
    try:
        info = manager.get_instance_info(target_instance_id)
    except KeyError:
        # Delta-fix #1: not-found / typo'd instance_id. Mirror the
        # existing ``_resolve_instance_id`` not-found behavior — the
        # tool layer returns a friendly error and NEITHER ``set_injection``
        # NOR ``enqueue_message`` is called.
        return None

    prior_status = info.get("status")
    # Defensive: if the dict is missing ``status`` (e.g. an instance row
    # in an inconsistent state), treat it as not-found so the caller
    # reports a clean error instead of silently falling through to a
    # branch that does not match the dict's actual shape.
    if not isinstance(prior_status, str) or not prior_status:
        return None

    # PAUSED — explicit pre-check before the eligibility/terminal tests
    # so the rejection text can use the verbatim R-O1 wording. (Architect
    # §2-O1 R-O1 verdict: REJECT, not auto-resume.)
    if prior_status == "paused":
        return ("paused", prior_status)

    # Injection branch — RUNNING / WAITING_CHILDREN.
    if prior_status in INJECTION_ELIGIBLE_STATUSES:
        return ("injection", prior_status)

    # Terminal-revive branch — all four terminal states flow through the
    # existing ``_prepare_enqueued_message`` revive path
    # (``daemon/services/instance_messaging.py:1522-1540``). Tool result
    # text uses ``prior_status`` for the "Instance was X — revived ..."
    # prefix.
    if prior_status in TERMINAL_INSTANCE_STATUSES:
        return ("enqueue-revive", prior_status)

    # Enqueue-parity branch — IDLE / WAITING / QUEUED (and any future
    # non-eligible non-terminal additions). Same ``enqueue_message`` path
    # as the pre-Phase 1 behavior.
    return ("enqueue", prior_status)


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
        # Exact match failed. Log authoritatively — this failure is otherwise
        # silent: ``_resolve_instance_id`` returns a ValueError that the tool
        # wrappers surface to the LLM as a ``ToolMessage`` string, NOT as a
        # log line. Inc 2026-08-03 (tester-stuck-waiting-children) had ZERO
        # log lines for the "instance not found" dispatch failure that
        # produced the ghost; the wedge was only discoverable by decoding
        # checkpoint_blobs. A WARNING here surfaces a just-spawned-but-
        # unresolvable instance in real time (the typical ghost-creation
        # signature) so operators don't need checkpoint forensics.
        logger.warning(
            "instance resolution failed (KeyError from get_instance): "
            "instance_id=%s — not in cache and not in DB; "
            "check for a failed spawn / cold-load None-read "
            "(ghost-child risk for spawning parents)",
            instance_id,
            extra={"unresolved_instance_id": instance_id},
        )
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


# ---------------------------------------------------------------------------
# Phase 2 (agent-instance-tools) — ``subtree_messages`` helpers + constants
# ---------------------------------------------------------------------------
#
# The subtree query is authorization-scoped to the caller's own subtree via
# a SINGLE chokepoint: ``_validate_subtree_target``. The helper calls the
# leader-approved facade method ``manager.get_tree_ids_permanent(...)`` —
# the tool layer MUST NOT reach into ``manager._instance_repository``
# directly (D14 / R-D14). ``get_tree_ids_permanent`` itself walks the
# permanent ``parent_id`` lineage (NOT the transient ``instance_hierarchy``
# working set), Python-side BFS, depth-capped at 256. See
# ``daemon/repositories/instance/repository.py:428-492``.

# Canonical role names exposed to the LLM-facing API — see
# ``daemon/utils.py:96``. The LangChain class names ``"human"`` /
# ``"ai"`` are NOT accepted by ``subtree_messages`` ``filters.role`` —
# they fail every filter call. Tests pin all four canonical names.

_SUBTREE_CANONICAL_ROLES = frozenset({"user", "assistant", "tool", "system"})

# Per-instance content truncation cap (full-mode messages).
_SUBTREE_CONTENT_MAX_CHARS = 200
# ToolMessage args redaction cap.
_SUBTREE_TOOL_ARGS_MAX_CHARS = 100
# Summary-mode content preview cap.
_SUBTREE_SUMMARY_CONTENT_MAX_CHARS = 80
# Hard ceiling on total output bytes; tail-truncated with a warning.
_SUBTREE_OUTPUT_CEILING_CHARS = 8000


def _validate_subtree_target(
    manager: "InstanceManager",
    caller_instance_id: str,
    target_instance_id: str | None,
) -> tuple[bool, list[str]]:
    """Validate ``target_instance_id`` is in the caller's subtree.

    Calls ``manager.get_tree_ids_permanent(caller_instance_id)`` — the
    leader-approved facade seam that delegates to
    ``InstanceRepository.get_tree_ids_permanent`` (Python-side BFS over
    ``parent_id``, depth-capped 256). The tool layer MUST NOT call
    ``manager._instance_repository`` directly (D14); this helper IS the
    only authorization chokepoint.

    Args:
        manager: The InstanceManager facade.
        caller_instance_id: The instance invoking the tool (its
            ``parent_id`` lineage defines the subtree).
        target_instance_id: The subtree root. ``None`` resolves to the
            caller's OWN subtree (no root-walk — per §7 #13). An empty
            string is rejected as malformed.

    Returns:
        ``(allowed, queried_subtree_ids)``:
          * ``allowed=True`` — the target is in the caller's subtree; the
            second element is the QUERIED subtree — i.e. the target's own
            subtree (target + every descendant) when ``target ≠ caller``,
            or the caller's subtree when ``target is caller / None``.
            Iterating the queried subtree is what satisfies the
            ``target=grandchild → grandchild's messages only`` case in
            phase2-plan.md §Test Plan (a).
          * ``allowed=False`` — the target is missing, malformed, or
            outside the caller's subtree; the second element is the
            caller's subtree (empty when the caller itself is missing).
    """
    if not caller_instance_id:
        return False, []

    if target_instance_id is not None and not str(target_instance_id).strip():
        # Empty string treated as malformed — same as not-found, but
        # with an explicit guard so the tool layer can produce a clean
        # error message instead of letting an empty string match.
        return False, manager.get_tree_ids_permanent(caller_instance_id)

    caller_subtree = manager.get_tree_ids_permanent(caller_instance_id)
    if not caller_subtree:
        # Caller itself is missing from the permanent record — caller
        # has no observable subtree.
        return False, []

    # ``target_instance_id=None`` → caller's own subtree (no root-walk).
    resolved_target = target_instance_id or caller_instance_id
    if resolved_target not in caller_subtree:
        return False, caller_subtree

    # Authz passed. The queried set is the TARGET's own subtree, not
    # the caller's whole tree — this is what makes
    # ``subtree_messages(target=grandchild_id)`` return only grandchild's
    # messages instead of every descendant in the caller's tree.
    # Both calls go through the facade (D14 / R-D14). When target IS the
    # caller, ``caller_subtree`` already IS the target's subtree — we
    # reuse it instead of calling the facade a second time.
    if resolved_target == caller_instance_id:
        return True, caller_subtree
    return True, manager.get_tree_ids_permanent(resolved_target)


def _filter_subtree_messages(
    msgs: list[dict],
    *,
    is_descendant: bool,
) -> list[dict]:
    """Apply the D12 synthetic-message filter to a per-instance message list.

    Phase 2 §3b (D12):
      * When ``is_descendant=True`` (any descendant of the caller), drop
        every ``is_synthetic=True`` message AND every real
        ``role=="system"`` message — synthetic context tokens must never
        leak to a parent, and a descendant's real system prompt is
        persona-privileged and not shareable.
      * When ``is_descendant=False`` (the caller itself), keep
        ``role=="system"`` messages authored by the caller — the
        caller's own system prompt is part of its context. The
        synthetic-context/system entries are still dropped (they are
        never meant to be quoted back at the caller).
      * The ``role`` value MUST be one of the canonical lowercase
        names (``user``/``assistant``/``tool``/``system``) per
        ``daemon/utils.py:96``. ``"human"`` and ``"ai"`` will not match
        any filter but will pass-through for non-filtered reads.

    Args:
        msgs: The list of serialized message dicts from
            ``manager.get_messages(iid)``.
        is_descendant: True iff the source instance is a descendant of
            the caller (target != caller).

    Returns:
        The filtered message list. Original order preserved.
    """
    out: list[dict] = []
    for m in msgs:
        # Synthetic markers — both the dict key AND the message_id
        # prefix (per persistence.py:437, 669). The prefix check is
        # belt-and-suspenders; the dict key is the authoritative one.
        if m.get("is_synthetic") is True:
            continue
        mid = m.get("message_id") or ""
        if (
            isinstance(mid, str)
            and (mid.startswith("synthetic-system-") or mid.startswith("synthetic-context-"))
        ):
            continue

        if is_descendant:
            role = m.get("role")
            if role == "system":
                # Descendant's real system prompt is persona-privileged.
                continue

        out.append(m)
    return out


def _summarize_tool_message(m: dict) -> str:
    """Build the ToolMessage redaction preview used in full mode.

    Per Phase 2 §4: ToolMessage → ``name + first 100 chars of args``.
    The tool-output ``content`` field is intentionally omitted — it is
    the raw tool response and would otherwise dominate the token budget.
    """
    name = m.get("name") or m.get("tool_name") or "tool"
    args = m.get("args") or m.get("arguments") or ""
    if not isinstance(args, str):
        args = str(args)
    args_snippet = args[:_SUBTREE_TOOL_ARGS_MAX_CHARS]
    if len(args) > _SUBTREE_TOOL_ARGS_MAX_CHARS:
        args_snippet = args_snippet + "…"
    return f"[{name}] {args_snippet}"


def _render_subtree_message(
    m: dict,
    *,
    summary: bool,
) -> str:
    """Render one message for the ``subtree_messages`` output string.

    Args:
        m: Serialized message dict from ``manager.get_messages(iid)``.
        summary: When True, emit metadata-only (instance_id, agent_id,
            role, created_at, tool_call_names, content preview).

    Returns:
        A single line (or short block) representing the message.
    """
    role = m.get("role") or "unknown"
    created_at = m.get("created_at") or ""

    if summary:
        content = m.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        preview = content[:_SUBTREE_SUMMARY_CONTENT_MAX_CHARS]
        if len(content) > _SUBTREE_SUMMARY_CONTENT_MAX_CHARS:
            preview = preview + "…"
        tool_call_names = [
            (tc.get("name") or "tool")
            for tc in (m.get("tool_calls") or [])
            if isinstance(tc, dict)
        ]
        parts = [f"[{role}]"]
        if created_at:
            parts.append(f"({created_at})")
        if tool_call_names:
            parts.append("tools=" + ",".join(tool_call_names))
        if preview:
            parts.append(preview)
        return " ".join(parts)

    # Full-content mode.
    # Defense-in-depth: a ToolMessage MUST be redacted regardless of
    # how the role was stamped on the dict. The canonical path stamps
    # ``role == "tool"`` (daemon/utils.py:96 maps ``msg.type`` ``"tool"``
    # to ``"tool"``), but a non-canonical / missing role MUST still
    # trigger redaction when tool markers are present — otherwise a
    # raw tool output could leak into the token budget.
    if (
        role == "tool"
        or m.get("type") == "tool"
        or m.get("tool_call_id") is not None
        or m.get("_call_id") is not None
    ):
        return _summarize_tool_message(m)

    content = m.get("content") or ""
    if not isinstance(content, str):
        content = str(content)
    snippet = content[:_SUBTREE_CONTENT_MAX_CHARS]
    if len(content) > _SUBTREE_CONTENT_MAX_CHARS:
        snippet = snippet + "…"
    line = f"[{role}] {snippet}"
    tool_call_names = [
        (tc.get("name") or "tool")
        for tc in (m.get("tool_calls") or [])
        if isinstance(tc, dict)
    ]
    if tool_call_names:
        line += f" (tools: {', '.join(tool_call_names)})"
    return line


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


def create_job_tools_if_available(manager, current_instance_id: str, agent_id: str, agent_tag: str | None = None) -> list:
    """Create job tools if job services are available on the manager.

    Args:
        manager: InstanceManager holding job-queue services.
        current_instance_id: Current instance ID.
        agent_id: Caller's agent_id.
        agent_tag: F2 — caller's version tag (e.g. ``"v2"``). Forwarded to
            ``create_job_tools`` so the agent-facing ``job_create`` tool can
            thread it into ``enqueue(agent_tag=...)``. Defaults to ``None``
            (base resolution) when the caller is not versioned.
    """
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
        agent_tag=agent_tag,
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


def create_instance_tools(manager: "InstanceManager", current_instance_id: str, agent_id: str = "", version_tag: str | None = None):
    """Create tools with injected manager reference.
    
    Args:
        manager: The InstanceManager instance to use for operations
        current_instance_id: The ID of the current instance (used as parent for spawned instances)
        agent_id: The agent identifier (e.g., "developer").
        version_tag: Optional agent version tag (e.g., ``"v2"``) used to resolve
            the versioned tool filter (``tools.allow`` / ``tools.deny``). When
            ``None``, falls back to the base resolved agent meta. Threaded from
            ``spawn_instance`` / ``_restore_instance`` so versioned agents see
            the correct allow/deny list (C1 fix — base/v1 was being applied to
            v2 instances).
    
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

    # Defensively capture the caller's version tag before defining closures.
    # This snapshot is a regression guard against future closure code that
    # might reassign ``version_tag`` locally; the distinct name remains bound
    # to the caller's tag regardless of any such closure-local reassignment.
    caller_version_tag: str | None = version_tag

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
        membership_error = _check_team_membership(caller_agent_id, agent_id, caller_version_tag)
        if membership_error is not None:
            return f"ERROR: {membership_error}"

        # ── S7 TOCTOU note (comment-only fix) ──────────────────────────────
        # The auth check above reads ``caller_agent_id``'s ``team_members``
        # from the live registry. After this check passes, the only await
        # before spawn is ``_resolve_default_version_tag`` (a synchronous
        # DB read in a worker thread) and the parent-instance project-id
        # fetch. Theoretically a mid-flight registry reload could change
        # the caller's team_members — granting a previously-denied spawn
        # access during this narrow window.
        #
        # Accepted risk: registry reloads are rare admin-level operations;
        # the window is bounded by two short awaits (typically <100ms total);
        # and every spawn path re-checks auth at spawn time inside
        # ``_check_team_membership`` so admin changes apply on the very
        # next spawn. Threading a snapshot through manager/lifecycle adds
        # plumbing + regression risk for a marginal defensive gain; we
        # accept the narrow window instead. See ``spawn_councilor`` for the
        # same rationale.

        try:
            # Auto-inherit project_id from parent if not explicitly provided
            if project_id is None:
                project_id = _get_instance_project_id(manager, current_instance_id)
                project_id = normalize_project_id(project_id)

            # Resolve per-project default version (mirrors the frontend
            # client-side resolution). The spawn_instance TOOL does not
            # expose version_tag (per UX decision); instead it always
            # uses the user-configured default, falling back to base.
            # W1: helper is async (DB read runs in a worker thread);
            # W2: registry validates the configured tag so a stale
            # default (e.g. ``"v99"`` retagged/renamed) falls back to
            # base instead of hard-failing the spawn.
            from ..registry import get_registry
            registry = get_registry()
            version_tag = await _resolve_default_version_tag(
                manager._project_repository, agent_id, registry
            )

            new_instance_id, validated_model_override = manager.spawn_instance(
                agent_id=agent_id,
                instance_id=None,
                parent_id=current_instance_id,
                project_id=project_id,
                instance_name=instance_name,
                model=model,
                version_tag=version_tag,
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

    # ─── Phase 2: council tools ──────────────────────────────────────────
    # spawn_councilor / clear_councilor_errors are defined as closures INSIDE
    # create_instance_tools() (C5 fix). They capture manager, caller_agent_id,
    # and current_instance_id from the outer scope. Per the Phase 2 plan:
    #   - C3: _check_team_membership returns str|None — check return value.
    #   - C4: resolve_to_id returns str|None — check for None.
    #   - W6: pass canonical_model to manager.spawn_instance() (not raw).
    #   - W7: normalize model to canonical form via case-insensitive lookup.
    #   - C2: use manager.config.llm.allowed_models (not manager._config).
    # These tools are bound only to the governor's tools.allow=["council",...]
    # via the "council" category in CATEGORY_MODULES.
    @register_tool_category("council")
    @tool(args_schema=SpawnCouncilorInput)
    async def spawn_councilor(
        councilor_agent_id: Annotated[str, Field(description="REQUIRED. Agent to spawn as councilor. Must be in governor's team_members.")],
        model: Annotated[str, Field(description="REQUIRED. LLM model. Must be in <allowed_models>. RAISES on invalid — no fallback.")],
        initial_message: Annotated[str, Field(description="REQUIRED. The request/message to forward to this councilor. Used in the returned instructions.")],
        instance_name: Annotated[str | None, Field(default=None, description="Optional short name for the instance (e.g., 'councilor-gpt4o').")] = None,
    ) -> str:
        """Spawn a councilor instance with a REQUIRED, validated model.

        Unlike ``spawn_instance``: REQUIRES both ``councilor_agent_id`` and
        ``model`` parameters, RAISES on invalid model (no silent fallback),
        and RAISES on invalid agent_id (no fallback to the membership gate).
        The model is normalized to the canonical name from
        ``config.llm.allowed_models`` before spawn (W7) so different
        capitalizations of the same model do not produce duplicate councilors.

        Like ``spawn_instance``: ``version_tag`` is NOT exposed — the tool
        resolves the per-project default version internally via
        ``_resolve_default_version_tag``. This mirrors the frontend UX
        (the user never picks the councilor's version tag) and prevents
        a v2 governor from accidentally spawning a v1 councilor with a
        broader tool set than intended. Stale / missing defaults fall
        back to base; a stale configured tag also falls back to base.

        Returns:
            A string containing the new ``instance_id``, the canonical model
            name used, and instructions to forward the request via
            ``send_message``.
        """
        # W1 FIX: Restrict to governor agent only
        if caller_agent_id != "governor":
            raise ValueError("council tools are restricted to the governor agent")

        # ─── STEP 1: Validate councilor_agent_id (C4: resolve_to_id returns None, never raises) ───
        # Lazy import to avoid circular import (registry imports utils indirectly).
        from ..registry import get_registry

        registry = get_registry()
        resolved_agent_id = registry.resolve_to_id(councilor_agent_id)
        if resolved_agent_id is None:  # C4 FIX: check for None, not exception
            raise ValueError(
                f"councilor_agent_id '{councilor_agent_id}' is not a valid agent in the registry."
            )

        # ─── STEP 2: Validate team membership (C3: _check_team_membership returns str|None, never raises) ───
        # ── S7 TOCTOU note (comment-only fix) ──────────────────────────────
        # The auth check below reads ``caller_agent_id``'s ``team_members``
        # from the live registry. After this check passes, the next await
        # is ``_resolve_default_version_tag`` (a synchronous DB read in a
        # worker thread). Theoretically a mid-flight registry reload could
        # change the caller's team_members — granting a previously-denied
        # agent spawn access during this narrow window.
        #
        # Accepted risk: registry reloads are rare admin-level operations;
        # the window is bounded by a single DB read (typically <50ms); and
        # every spawn path re-checks auth at spawn time inside
        # ``_check_team_membership`` so admin changes apply on the very
        # next spawn. Threading a snapshot through manager/lifecycle adds
        # plumbing + regression risk for a marginal defensive gain; we
        # accept the narrow window instead.
        err = _check_team_membership(caller_agent_id, resolved_agent_id, caller_version_tag)
        if err is not None:  # C3 FIX: check return value, not rely on exception
            raise ValueError(err)

        # ─── STEP 3: Validate model STRICTLY (raise, do not fallback) ───
        # Lifecycle's _resolve_model_override silently returns None when the
        # model is not in allowed_models. spawn_councilor inverts this — a
        # None result is an error, not a silent fallback.
        # C2 FIX: read manager.config.llm.allowed_models (NOT manager._config).
        # Define `allowed` at outer scope so the W7 canonical-name normalization
        # below can use it on the success path too.
        allowed = getattr(manager.config.llm, "allowed_models", None) or []
        lifecycle = manager._lifecycle_service
        validated_model = lifecycle._resolve_model_override(model)
        if validated_model is None:
            if not allowed:
                # Unrestricted — _resolve_model_override returned None only when
                # the input was None/empty/whitespace, which Pydantic min_length=1
                # should have rejected. If we reach here, something is off.
                raise ValueError(
                    f"Model '{model}' was rejected despite no allowed_models "
                    f"restriction. Unexpected — report to user."
                )
            raise ValueError(
                f"Model '{model}' is NOT in allowed_models. Valid models: {allowed}. "
                f"No fallback — correct the model and retry."
            )

        # ─── STEP 3b: W7 — Normalize to canonical model name ───
        # _resolve_model_override returns the caller's spelling (the candidate),
        # not the canonical form from allowed_models. Normalize so 'gpt-4o'
        # and 'GPT-4O' collapse to the same canonical entry (W7) — preventing
        # duplicate councilors with different capitalizations of the same model.
        canonical_model = next(
            (m for m in allowed if m.lower() == validated_model.lower()),
            validated_model,  # fallback to caller spelling if unrestricted
        )

        # ─── STEP 3c: W3 — Resolve per-project default version tag ───
        # The frontend never exposes ``version_tag`` for councilor spawns
        # (mirroring ``spawn_instance``). Internally we resolve the
        # user-configured default; missing / stale / corrupt → ``None`` →
        # base agent. A v2 governor therefore cannot accidentally spawn a
        # v1 councilor with a wider tool set.
        version_tag = await _resolve_default_version_tag(
            manager._project_repository, resolved_agent_id, registry
        )

        # ─── STEP 4: Delegate to lifecycle (W6: pass canonical, not raw) ───
        # Passing canonical_model (not the raw caller-supplied model) closes
        # the TOCTOU window: lifecycle's spawn_instance would otherwise re-run
        # _resolve_model_override on the raw value, which could disagree with
        # the validation already performed above under a mid-flight
        # allowed_models mutation.
        # Inherit project_id from the caller (governor) so councilors are
        # visible in the same project-scoped instance list.
        councilor_project_id = _get_instance_project_id(manager, current_instance_id)
        new_instance_id, _returned_model = manager.spawn_instance(
            agent_id=resolved_agent_id,
            instance_id=None,
            parent_id=current_instance_id,
            project_id=councilor_project_id,
            instance_name=instance_name,
            model=canonical_model,
            version_tag=version_tag,
        )

        # ─── STEP 5: Return success ───
        return (
            f"Successfully spawned councilor instance: {new_instance_id}\n"
            f"Agent: {resolved_agent_id} | Model: {canonical_model}\n"
            f"To send the request, use: "
            f"send_message(instance_id=\"{new_instance_id}\", message=\"{initial_message}\")"
        )

    @register_tool_category("council")
    @tool
    async def clear_councilor_errors() -> str:
        """Clear the sticky parent-error flag so the governor can finalize as COMPLETED.

        The dependency bus marks the parent as ERROR if ANY child (councilor)
        fails. This flag is STICKY — once set, the parent terminal status is
        forced to ERROR even if synthesis succeeded. Call this tool AFTER
        successful synthesis to clear the flag and allow COMPLETED finalization.

        Do NOT call if synthesis failed (all councilors errored) — let ERROR
        propagate.

        Returns:
            A short status string describing the outcome (cleared / warning).
        """
        # W1 FIX: Restrict to governor agent only
        if caller_agent_id != "governor":
            raise ValueError("council tools are restricted to the governor agent")

        # Lazy import to avoid module-load circularity.
        from daemon.services.dependency_bus import get_dependency_bus

        bus = get_dependency_bus()
        if bus is None:
            return (
                "Warning: No dependency bus available — cannot clear parent-error flag."
            )

        try:
            bus.clear_parent_error(current_instance_id)
            return (
                f"Cleared parent-error flag for instance {current_instance_id[:8]}..."
            )
        except Exception as e:
            return f"Warning: Failed to clear parent-error flag: {e}"

    @register_tool_category("council")
    @tool
    async def convene_council(
        councilor_agent_id: str,
        request: str,
        models: list[str] | None = None,
        max_councilors: int | None = None,
        instance_name: str | None = None,
    ) -> dict:
        """Convene a council of agents with different LLM models to solve a problem.

        Non-blocking: returns immediately with an async hint. The governor's
        completion report arrives as a new message to the caller.
        """
        from ..registry import get_registry

        registry = get_registry()
        canonical = registry.resolve_to_id(councilor_agent_id)
        if not canonical:
            raise ValueError(f"Unknown agent_id: {councilor_agent_id!r}")

        # convene_council requires "governor" in the caller's team_members.
        # Add "governor" to meta.json team_members for any agent that should
        # be able to convene councils.
        membership_error = _check_team_membership(caller_agent_id, "governor", caller_version_tag)
        if membership_error is not None:
            raise ValueError(membership_error)

        # ── F6 FIX: Resolve per-project default version_tag for the governor ──
        # Mirrors the ``spawn_councilor`` W3 contract: ``version_tag`` is
        # intentionally NOT exposed (frontend never picks the governor's tag).
        # Without this resolution, ``manager.spawn_instance`` would receive
        # ``version_tag=None`` and ``lifecycle`` would fall back to base —
        # meaning a configured ``governor[v2]`` is never spawned via
        # ``convene_council``. Stale / missing / corrupt defaults fall back
        # to ``None`` → base, exactly like ``spawn_councilor``.
        gov_version_tag = await _resolve_default_version_tag(
            manager._project_repository, "governor", registry
        )

        # ── Councilor team-membership guard (defense-in-depth) ───────────
        # ``convene_council`` validates the councilor only against the agent
        # registry above, but the governor later spawns councilors via
        # ``spawn_councilor`` which enforces ``_check_team_membership(
        # "governor", councilor_id)``. Without this symmetric check here, a
        # councilor that is valid in the registry but NOT in the governor's
        # ``team_members`` passes the convene layer and fails only deep
        # inside the governor — where the LLM may mishandle the runtime
        # rejection (see the ask_questions-pause incident). Fail fast at the
        # caller instead. Symmetric to ``spawn_councilor`` 's STEP 2 check.
        councilor_membership_error = _check_team_membership(
            "governor", canonical, gov_version_tag
        )
        if councilor_membership_error is not None:
            raise ValueError(councilor_membership_error)

        # No W1 identity guard: any caller authorized by team_members may convene.
        # Inherit project_id from the caller so the governor and its councilors
        # are visible in the same project-scoped instance list (mirrors the
        # spawn_instance tool's auto-inherit at the top of this file).
        gov_project_id = _get_instance_project_id(manager, current_instance_id)
        gov_instance_id, _ = manager.spawn_instance(
            agent_id="governor",
            parent_id=current_instance_id,
            project_id=gov_project_id,
            instance_name=instance_name,
            version_tag=gov_version_tag,
        )

        message_text = (
            f'Convene a council using councilor_agent_id="{canonical}".\n'
            f"Request: {request}\n"
            f"Models: {', '.join(models) if models else 'all available'}\n"
            f"Max councilors: "
            f"{max_councilors if max_councilors is not None else 'governor decides'}"
        )

        convene_result = await manager.enqueue_message(
            instance_id=gov_instance_id,
            message=message_text,
            source=f"internal_agent:{current_instance_id}",
        )

        # Register a DependencyBus watcher so the caller (parent) is
        # revived — put into ``waiting_children`` then reactivated with
        # the governor's completion report — when the spawned governor
        # finishes. Without this the parent races to COMPLETED before the
        # governor even starts. Mirrors ``send_message`` 's
        # parent→child watcher; see ``_register_child_completion_watcher``.
        watcher_error = await _register_child_completion_watcher(
            manager, current_instance_id, gov_instance_id, convene_result.message_id
        )
        if watcher_error is not None:
            return watcher_error

        return {
            "status": "convened",
            "governor_instance_id": gov_instance_id,
            "hint": (
                "Council convened. The governor will process your request and "
                "deliver a result. Watch for the completion report."
            ),
        }

    @register_tool_category("council")
    @tool
    async def convene_council_with_skill(
        councilor_agent_id: str,
        request: str,
        councilor_skill: str,
        models: list[str] | None = None,
        max_councilors: int | None = None,
        instance_name: str | None = None,
    ) -> dict:
        """Convene a council of agents and inject a skill into each councilor.

        Skill-passthrough variant of :func:`convene_council`. The
        ``councilor_skill`` is forwarded to the governor's request message so
        each spawned councilor can be primed with the named skill before
        tackling the request.

        Non-blocking: returns immediately with an async hint. The governor's
        completion report arrives as a new message to the caller.
        """
        if councilor_skill is None:
            raise ValueError(
                "councilor_skill is required for convene_council_with_skill"
            )
        councilor_skill = councilor_skill.strip()
        if not councilor_skill:
            raise ValueError(
                "councilor_skill is required for convene_council_with_skill"
            )
        # Reject newlines to prevent governor prompt injection
        if "\n" in councilor_skill or "\r" in councilor_skill:
            raise ValueError("councilor_skill must not contain newlines")

        from ..registry import get_registry

        registry = get_registry()
        canonical = registry.resolve_to_id(councilor_agent_id)
        if not canonical:
            raise ValueError(f"Unknown agent_id: {councilor_agent_id!r}")

        # convene_council_with_skill requires "governor" in the caller's team_members.
        membership_error = _check_team_membership(caller_agent_id, "governor", caller_version_tag)
        if membership_error is not None:
            raise ValueError(membership_error)

        # ── Optional skill existence check (defensive hardening) ────────
        # Surface a WARNING when the requested skill is missing from both the
        # ``skills`` and ``skill_bank`` tables so misnamed skills are caught
        # before spawning a full governor + councilor chain. The lookup
        # proceeds either way — consistent with the existing ``load_skill``
        # tool, which also no-ops on misses. All access is defensive: any
        # missing repo / lookup exception degrades to "not found".
        skill_repo = getattr(manager, "_skill_repo", None)
        skill_bank_repo = getattr(manager, "_skill_bank_repo", None)
        project_id: str | None = None
        instance_repo = getattr(manager, "_instance_repository", None)
        if current_instance_id and instance_repo is not None:
            try:
                inst_meta = instance_repo.get(current_instance_id)
                project_id = getattr(inst_meta, "project_id", None)
            except Exception:
                project_id = None

        skill_found = False
        if skill_repo is not None:
            try:
                hit = await asyncio.to_thread(
                    skill_repo.get_by_name, project_id, councilor_skill, 0
                )
                if hit is not None:
                    skill_found = True
            except Exception:
                pass
        if not skill_found and skill_bank_repo is not None:
            try:
                hit = await asyncio.to_thread(
                    skill_bank_repo.get_by_name_any_agent, councilor_skill
                )
                if hit is not None:
                    skill_found = True
            except Exception:
                pass
        if not skill_found:
            logger.warning(
                f"WARNING: Skill '{councilor_skill}' not found in skills or "
                f"skill_bank tables — councilors will run without skill injection"
            )

        # ── F6 FIX: Resolve per-project default version_tag for the governor ──
        # Mirrors the ``spawn_councilor`` W3 contract (and the
        # ``convene_council`` F6 fix above): ``version_tag`` is intentionally
        # NOT exposed to the LLM, and without this resolution the lifecycle
        # would receive ``version_tag=None`` and silently fall back to base —
        # a configured ``governor[v2]`` would never be selected via
        # ``convene_council_with_skill``. Stale / missing / corrupt defaults
        # fall back to ``None`` → base, exactly like ``spawn_councilor``.
        gov_version_tag = await _resolve_default_version_tag(
            manager._project_repository, "governor", registry
        )

        # ── Councilor team-membership guard (defense-in-depth) ───────────
        # Same guard as ``convene_council`` above and symmetric to
        # ``spawn_councilor`` 's STEP 2 check: the councilor must be in the
        # governor's ``team_members`` (not merely a valid registry id), else
        # ``spawn_councilor`` rejects it deep inside the governor run where
        # the LLM may mishandle the runtime rejection. Fail fast at the
        # caller before spawning the governor.
        councilor_membership_error = _check_team_membership(
            "governor", canonical, gov_version_tag
        )
        if councilor_membership_error is not None:
            raise ValueError(councilor_membership_error)

        # No W1 identity guard: any caller authorized by team_members may convene.
        # Inherit project_id from the caller so the governor and its councilors
        # are visible in the same project-scoped instance list (mirrors the
        # spawn_instance tool's auto-inherit at the top of this file).
        gov_project_id = _get_instance_project_id(manager, current_instance_id)
        gov_instance_id, _ = manager.spawn_instance(
            agent_id="governor",
            parent_id=current_instance_id,
            project_id=gov_project_id,
            instance_name=instance_name,
            version_tag=gov_version_tag,
        )

        message_text = (
            f'Convene a council using councilor_agent_id="{canonical}".\n'
            f"Councilor skill: {councilor_skill}\n"
            f"Request: {request}\n"
            f"Models: {', '.join(models) if models else 'all available'}\n"
            f"Max councilors: "
            f"{max_councilors if max_councilors is not None else 'governor decides'}"
        )

        convene_result = await manager.enqueue_message(
            instance_id=gov_instance_id,
            message=message_text,
            source=f"internal_agent:{current_instance_id}",
        )

        # Register a DependencyBus watcher so the caller (parent) is
        # revived — put into ``waiting_children`` then reactivated with
        # the governor's completion report — when the spawned governor
        # finishes. Without this the parent races to COMPLETED before the
        # governor even starts. Mirrors ``send_message`` 's
        # parent→child watcher; see ``_register_child_completion_watcher``.
        watcher_error = await _register_child_completion_watcher(
            manager, current_instance_id, gov_instance_id, convene_result.message_id
        )
        if watcher_error is not None:
            return watcher_error

        return {
            "status": "convened",
            "governor_instance_id": gov_instance_id,
            "councilor_skill": councilor_skill,
            "hint": (
                "Council convened with skill injection. The governor will "
                "process your request and deliver a result. Watch for the "
                "completion report."
            ),
        }

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
        context: Annotated[
            dict[str, Any] | None,
            Field(
                default=None,
                description=(
                    "Optional structured context to inject before the task "
                    "message. Keys are free-form (suggested: 'files', 'notes', "
                    "'plan_ref', 'conventions'). Values may be lists or strings. "
                    "When provided and non-empty, formatted into a "
                    "[SYSTEM CONTEXT: Task Context] block and injected as a "
                    "separate HumanMessage BEFORE the task message. Omit or pass "
                    "None for backward-compatible behavior."
                ),
            ),
        ] = None,
    ) -> str:
        """Send a message to another instance.

        Phase 1 (agent-instance-tools): the tool now routes the
        message through the same delivery machinery as the user-facing
        HTTP API based on the target's status at the moment of
        invocation:

          * ``RUNNING`` / ``WAITING_CHILDREN`` → INJECTION
            (``manager.set_injection(...)``). The message lands in the
            target's live turn on the next ``agent_node`` pass. Tool
            pairing safety is preserved by the existing
            ``_ensure_tool_result_pairing`` guard at
            ``daemon/graph.py:2893`` — no new guard site is added.
            EXCEPTION: a send bearing ``load_skill`` or a non-empty
            ``context`` routes via ENQUEUE even for these statuses —
            both parameters are enqueue-pipeline-only (the ``<meta>``
            tag parser and the ``metadata`` channel live in
            ``enqueue_message``'s pipeline) and would be lost or land
            as raw tag text on the injection branch.
          * ``COMPLETED`` / ``TERMINATED`` / ``ERROR`` / ``FAILED`` →
            REVIVE + ENQUEUE. All four terminal states flow through the
            shared ``_prepare_enqueued_message`` path
            (``daemon/services/instance_messaging.py:1522-1540``); the
            tool result pre-pends ``"Instance was {prior_status} —
            revived and message dispatched."``
          * ``IDLE`` / ``WAITING`` / ``QUEUED`` (and any other
            non-eligible non-terminal state) → ENQUEUE parity with the
            pre-Phase 1 behavior.
          * ``PAUSED`` → REJECT. The agent-tool path deliberately does
            NOT auto-resume (architect §2-O1 R-O1 verdict). The
            ``messages`` HTTP route auto-resumes PAUSED targets — that
            asymmetry is intentional (human authority resumes; agent
            sends wait). No ``resume_instance`` reference (no such
            tool exists; only ``Manager.pause_instance_cascade`` /
            ``Manager.resume_instance_cascade`` are operator/lifecycle
            methods).

        Empty / whitespace-only content is rejected before routing
        (Task 2c, §7 #7) — a blank message injected into a live turn
        wastes an LLM turn. The trim-check mirrors S4 at
        ``daemon/routers/messages.py:181-188``.

        Ordering semantics (W5, architect §2-O4 R-O4):
            Delivery is FIFO but may interleave with concurrent senders — do not assume order between injection and enqueue. Injections land before child reports in the same wake-up turn.

        Stranding-race exposure (R-O2 verbatim, leader decision b):
            In-flight injected messages share the same pause / clear /
            crash loss profile as the user messages API. The tool
            result for the injection branch surfaces this caveat
            verbatim — see the post-dispatch text returned for that
            branch.

        Args:
            instance_id: The ID of the target instance.
            message: The message content to send. Must be non-empty
                after stripping whitespace.
            load_skill: Optional skill name (e.g. 'unit-test'). When
                provided, a ``<meta>{"load_skill": "<name>"}</meta>``
                tag is appended to the message so the skill is
                injected into the recipient's context for clean 1:1
                attribution. Omit or pass None for backward-compatible
                behavior (no meta-tag appended). Sends bearing
                ``load_skill`` always dispatch via the enqueue
                pipeline (the only path with a meta-tag parser) —
                even when the target is injection-eligible.
            context: Optional structured context dict to inject before
                the task message. Keys are free-form (suggested:
                'files', 'notes', 'plan_ref', 'conventions'). Values
                may be lists or strings. When provided and non-empty,
                formatted into a ``[SYSTEM CONTEXT: Task Context]``
                block and injected as a separate HumanMessage BEFORE
                the task message. Omit or pass None for
                backward-compatible behavior (no context injected).
                Sends bearing a non-empty ``context`` always dispatch
                via the enqueue pipeline (the only path with a
                metadata channel for the context block).

        Returns:
            A human-readable status string. The shape varies by
            routing branch:
              * ``"Message content is empty; nothing to send."``
                (trim-check reject — no dispatch).
              * ``"Instance '<id>' not found; no message dispatched."``
                (not-found — no dispatch).
              * ``"Instance '<id>' is PAUSED. …"`` (PAUSED reject —
                no dispatch; full text in ``_full_doc_``).
              * ``"Message injected into {prior_status} target. …"``
                (injection; includes the R-O2 W3 stranding caveat).
              * ``"Instance was {prior_status} — revived and message
                dispatched. Message queued and sent to <id>. …"``
                (terminal-revive).
              * ``"Message queued and sent to <id>. …"`` (enqueue
                parity — IDLE / WAITING / QUEUED / future additions).

        Example outputs::

            # trim-check reject:
            "Message content is empty; nothing to send."

            # injection (RUNNING / WAITING_CHILDREN):
            "Message injected into running target. The next agent_node
            cycle will deliver it to the live turn.

            Note: if the target is paused or the daemon restarts before
            delivery, an in-flight injected message may be dropped
            (pause-loss parity with the user messages API)."

            # terminal revive (COMPLETED / TERMINATED / ERROR / FAILED):
            "Instance was completed — revived and message dispatched.
            Message queued and sent to <id>. The completion report …"

            # PAUSED reject (no dispatch):
            "Instance '<id>' is PAUSED. Paused instances cannot receive messages; delivery is rejected to respect the pause (operator/lifecycle intent). Wait for it to be resumed via the API/UI, or proceed with other work."
        """
        # ── Trim-check (Task 2c, §7 #7) ─────────────────────────────────────
        # Mirrors S4 at ``daemon/routers/messages.py:181-188``. Applies to
        # ALL paths (injection, terminal-revive, enqueue-parity) so a
        # caller typo never produces a wasted LLM turn. Returns BEFORE
        # routing so the W3 stranding note / PAUSED text / revival log
        # are NOT mixed with a trim-check rejection.
        if not message or not message.strip():
            return "Message content is empty; nothing to send."

        # ── load_skill sugar: append <meta> tag before dispatch ────────────
        # This is purely syntactic sugar. The ONLY consumer of the tag is
        # the ENQUEUE pipeline: ``extract_load_skill``
        # (daemon/services/instance_messaging.py:2234) parses it inside
        # the enqueued-dispatch machinery. The Phase 1 injection branch
        # (``manager.set_injection`` → graph drain) builds a plain
        # HumanMessage with NO meta-tag parsing — a tag landing there
        # would appear as raw garbage text in the target's live turn and
        # the skill would never load. Sends bearing ``load_skill``
        # therefore override injection routing and take the enqueue path
        # (see the enqueue-only parameter override at the routing call
        # site below). We do NOT touch the parser module — we only
        # generate the tag string here.
        load_skill_requested = (
            load_skill is not None and str(load_skill).strip() != ""
        )
        if load_skill_requested:
            _payload = json.dumps({"load_skill": str(load_skill).strip()})
            message = message + f"\n<meta>{_payload}</meta>"

        # Format task context into a string for metadata threading.
        # The actual HumanMessage injection happens in
        # `_process_message_with_tracking` via the messaging pipeline.
        # We store it in message_metadata so it survives the async
        # dispatch (tool → enqueue → DB → task_processor → pipeline).
        # NOTE: this channel is ENQUEUE-ONLY — ``set_injection``
        # (manager.py) stores ``{content, timestamp}`` with no metadata
        # field, so a context-bearing send MUST NOT take the injection
        # branch (the context would be silently dropped). See the
        # enqueue-only parameter override at the routing call site.
        task_context_text: str | None = None
        if context is not None:
            # Reject non-dict ``context`` with a clear error string instead
            # of silently dropping it. The old guard
            # ``isinstance(context, dict) and context`` was False for a
            # non-dict value, so ``task_context_text`` stayed ``None`` and
            # the caller got no feedback that their context was lost.
            # This matches the existing ``ERROR: ...`` pattern used by
            # the status guard below.
            if not isinstance(context, dict):
                return (
                    f"ERROR: The 'context' parameter must be a dict, "
                    f"got {type(context).__name__}. Omit it or pass a "
                    f"dict with keys like 'files', 'notes', 'plan_ref'."
                )
            if context:
                task_context_text = _format_task_context(context)

        # Validate instance exists with fuzzy matching for typos
        try:
            await _resolve_instance_id(manager, instance_id)
        except ValueError as e:
            return str(e)

        # CR-2: team-membership authorization gate. Without this, any
        # instance (including project-manager, which has narrow team
        # membership) can message ANY other instance — bypassing the
        # same gate that ``spawn_instance`` enforces. The check runs
        # AFTER the existence check (we need a real instance to
        # resolve its agent_id) and BEFORE the status check (a
        # terminated target doesn't deserve a more specific error
        # than "not allowed"). The same closure variables as
        # ``spawn_instance`` (caller_agent_id, caller_version_tag) are
        # used, so PM→leader and other declared team-member messages
        # continue to flow.
        if not caller_agent_id:
            return (
                "ERROR: send_message invoked without a caller agent_id. "
                "This is a wiring/configuration bug — the instance tools "
                "were created without an agent_id. Send is denied."
            )
        # Look up the target's agent_id (canonical source for the
        # membership check). ``agent_id`` may be missing on the dict
        # when the instance row is incomplete; default to "" so the
        # check fails closed.
        # Split-cache race defense: ``_resolve_instance_id`` (async)
        # succeeded above, but ``get_instance_info`` (sync, in-memory
        # cache hit) can still raise ``KeyError`` when the lifecycle
        # store evicts the row between the two lookups. Mirror the
        # routing helper's not-found branch (instance.py:703-710) and
        # return the SAME friendly text the not-found branch already
        # uses at instance.py:1978 — delta-fix #1 contract on this
        # path (NEITHER ``set_injection`` NOR ``enqueue_message``
        # called). Without this guard a raw ``KeyError`` leaks to the
        # calling agent (tester CR-2 race probe).
        try:
            target_info = manager.get_instance_info(instance_id)
        except KeyError:
            return f"Instance '{instance_id}' not found; no message dispatched."
        target_agent_id = target_info.get("agent_id", "") or ""
        if target_agent_id:
            membership_error = _check_team_membership(
                caller_agent_id, target_agent_id, caller_version_tag
            )
            if membership_error is not None:
                return f"ERROR: {membership_error}"

        # ── Phase 1 routing decision (Tasks 2 + 3) ─────────────────────────
        # The routing helper is the single source of truth for the
        # dispatch decision. It uses ``manager.get_instance_info(iid)``
        # (D14 — NO ``_instance_repository`` reach-in) and returns
        # ``None`` for not-found (mirrors the existing
        # ``_resolve_instance_id`` not-found behavior, delta-fix #1).
        route_result = _route_send_message(manager, instance_id)
        if route_result is None:
            # Delta-fix #1: not-found / typo'd instance_id. Friendly
            # error, NO ``set_injection`` / ``enqueue_message`` called.
            return f"Instance '{instance_id}' not found; no message dispatched."

        routed_via, prior_status = route_result

        # ── Enqueue-only parameter override (review 377b0a8f, fix 1 + 2) ──
        # ``load_skill`` and ``context`` only work on the ENQUEUE pipeline:
        #   * ``load_skill``: the ``<meta>`` tag parser
        #     (``extract_load_skill``, instance_messaging.py:2234) runs in
        #     the enqueue pipeline only. On the injection branch the tag
        #     would land as raw garbage text in the target's live turn AND
        #     the skill would never load.
        #   * ``context``: ``task_context`` rides
        #     ``enqueue_message(metadata=...)``; ``set_injection``
        #     (manager.py) stores ``{content, timestamp}`` only — no
        #     metadata channel — so the context would be silently dropped.
        # A send bearing EITHER parameter therefore routes via ENQUEUE
        # even when the target is injection-eligible (RUNNING /
        # WAITING_CHILDREN). This restores exact pre-Phase-1 behavior for
        # these two cases (queue-busy guard included, as before); plain
        # sends without either parameter keep the new injection routing.
        # The routing helper's return contract is unchanged — the
        # override lives here, at the call site, deliberately AFTER the
        # helper so the helper stays a pure status → route mapping.
        if routed_via == "injection" and (
            load_skill_requested or task_context_text is not None
        ):
            routed_via = "enqueue"

        # ── PAUSED reject (Task 5, R-O1 verbatim) ──────────────────────────
        # Architect §2-O1 verdict: REJECT (do NOT auto-resume). The user
        # API auto-resumes PAUSED targets — the agent-tool path
        # deliberately does NOT inherit that branch (human authority
        # resumes; agent sends wait). The text MUST match decisions.md
        # R-O1 verbatim; test c asserts it.
        if routed_via == "paused":
            return (
                f"Instance '{instance_id}' is PAUSED. Paused instances "
                f"cannot receive messages; delivery is rejected to "
                f"respect the pause (operator/lifecycle intent). Wait "
                f"for it to be resumed via the API/UI, or proceed with "
                f"other work."
            )

        # ── Injection branch (Task 3, R-O3 + R-O4) ─────────────────────────
        # RUNNING / WAITING_CHILDREN → ``manager.set_injection(...)``.
        # Tool-pairing safety is preserved by the existing
        # ``_ensure_tool_result_pairing`` guard at
        # ``daemon/graph.py:2893`` — NO new guard site is added (D3).
        # The queue-busy guard is DROPPED for this branch (D11 / R-O3 —
        # status is the source of truth; a status change after the
        # routing decision is handled by downstream logic).
        if routed_via == "injection":
            manager.set_injection(instance_id, message)
            # Task 3b: provenance INFO logging at the call site. v1
            # mitigation for injection anonymity (R-LEADER deferred the
            # ``set_injection(..., source=None)`` + drain-stamps
            # ``additional_kwargs["source"]`` work to §8 follow-ups).
            # The structured fields are populated via ``extra=`` so the
            # log line is greppable as ``event="agent_send_message"``.
            logger.info(
                "agent_send_message routed via injection",
                extra={
                    "event": "agent_send_message",
                    "caller_iid": current_instance_id,
                    "target_iid": instance_id,
                    "routed_via": "injection",
                    "prior_status": prior_status,
                    "content_len": len(message),
                    "source": f"internal_agent:{current_instance_id}",
                },
            )
            # R-O2 (leader decision b) — the injection-path success
            # result MUST include the W3 stranding sentence verbatim
            # (or an equivalent covering the same three facts:
            # pause-loss parity, daemon-restart loss, in-flight
            # delivery caveat). This composes with the PAUSED-reject
            # text in R-O1 — both texts MUST ship together; an
            # implementer cannot ship one without the other (test c +
            # test f).
            return (
                f"Message injected into {prior_status} target. "
                f"The next agent_node cycle will deliver it to the "
                f"live turn.\n\n"
                f"Note: if the target is paused or the daemon "
                f"restarts before delivery, an in-flight injected "
                f"message may be dropped (pause-loss parity with the "
                f"user messages API)."
            )

        # ── Enqueue branch (terminal-revive OR non-eligible non-terminal)
        # Tasks 3 + 4: terminal-revive flows through the existing
        # ``_prepare_enqueued_message`` path — no explicit
        # ERROR/TERMINATED rejection at the tool layer (D2). The tool
        # result pre-pends the prior-status text for the revive branch
        # so the calling agent can reason about the transition.
        # The queue-busy guard STAYS for both sub-branches (D11 /
        # R-O3) — it serializes terminal-revives against in-flight
        # child reports.
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
            source=f"internal_agent:{current_instance_id}",
            metadata={"task_context": task_context_text} if task_context_text else None,
        )
        message_id = result.message_id

        # Task 3b: provenance INFO logging at the call site (mirrors the
        # injection branch above; see the comment there for context).
        logger.info(
            "agent_send_message routed via enqueue",
            extra={
                "event": "agent_send_message",
                "caller_iid": current_instance_id,
                "target_iid": instance_id,
                "routed_via": routed_via,  # "enqueue" or "enqueue-revive"
                "prior_status": prior_status,
                "content_len": len(message),
                "source": f"internal_agent:{current_instance_id}",
            },
        )

        # Register a DependencyBus watcher so the parent is revived when
        # the child completes. Shared with ``convene_council`` /
        # ``convene_council_with_skill`` (any async spawn+enqueue that
        # should keep the parent in ``waiting_children`` until the child
        # reports back). The helper is a no-op when the target is NOT a
        # child of the sender.
        watcher_error = await _register_child_completion_watcher(
            manager, current_instance_id, instance_id, message_id
        )
        if watcher_error is not None:
            return watcher_error

        # Terminal-revive prefix (Task 4). All four terminal states
        # flow through the same ``enqueue_message`` path; only the
        # prefix text differs.
        prefix = (
            f"Instance was {prior_status} — revived and message dispatched. "
            if routed_via == "enqueue-revive"
            else ""
        )

        return (
            prefix
            + f"Message queued and sent to {instance_id}. The completion report "
            f"will be delivered to you automatically as a new message that "
            f"resumes your turn the moment the child finishes — do not poll or "
            f"sleep waiting for it. You may continue other work (spawn more "
            f"children, send more messages, etc.) in the meantime; when you "
            f"have nothing left to do, end your turn and the report will arrive "
            f"on its own."
        )

    send_message._full_doc_ = """Send a message to another instance.

Phase 1 (agent-instance-tools) routes the message through the same
delivery machinery as the user-facing HTTP API, based on the target's
status at the moment of invocation:

  * ``RUNNING`` / ``WAITING_CHILDREN`` → INJECTION via
    ``Manager.set_injection(...)``. The message lands in the target's
    live turn on the next ``agent_node`` pass. Tool-pairing safety is
    preserved by the existing ``_ensure_tool_result_pairing`` guard at
    ``daemon/graph.py:2893`` — no new guard site is added.
    EXCEPTION: a send bearing ``load_skill`` or a non-empty ``context``
    routes via ENQUEUE even for these statuses — both parameters are
    enqueue-pipeline-only (the ``<meta>`` tag parser and the
    ``metadata`` channel live in ``enqueue_message``'s pipeline) and
    would be lost or land as raw tag text on the injection branch.

  * ``COMPLETED`` / ``TERMINATED`` / ``ERROR`` / ``FAILED`` → REVIVE +
    ENQUEUE via the shared ``_prepare_enqueued_message`` path
    (``daemon/services/instance_messaging.py:1522-1540``). The tool
    result pre-pends ``"Instance was {prior_status} — revived and
    message dispatched."``

  * ``IDLE`` / ``WAITING`` / ``QUEUED`` (and any other non-eligible
    non-terminal state) → ENQUEUE parity with the pre-Phase 1
    behavior.

  * ``PAUSED`` → REJECT. The agent-tool path deliberately does NOT
    auto-resume (architect §2-O1 R-O1 verdict). The ``messages`` HTTP
    route auto-resumes PAUSED targets — that asymmetry is intentional
    (human authority resumes; agent sends wait). No ``resume_instance``
    reference (no such tool exists; only ``Manager.pause_instance_cascade``
    / ``Manager.resume_instance_cascade`` are operator/lifecycle
    methods).

Empty / whitespace-only content is rejected before routing (Task 2c,
§7 #7) — a blank message injected into a live turn wastes an LLM
turn. The trim-check mirrors S4 at
``daemon/routers/messages.py:181-188``.

Ordering semantics (W5, architect §2-O4 R-O4):
    Delivery is FIFO but may interleave with concurrent senders — do not assume order between injection and enqueue. Injections land before child reports in the same wake-up turn.

Stranding-race exposure (R-O2 verbatim, leader decision b):
    In-flight injected messages share the same pause / clear /
    crash loss profile as the user messages API. The tool result for
    the injection branch surfaces this caveat verbatim — see the
    post-dispatch text returned for that branch.

Args:
    instance_id: The ID of the target instance.
    message: The message content to send. Must be non-empty after
        stripping whitespace.
    load_skill: Optional skill name (e.g. 'unit-test'). When provided,
        a ``<meta>{"load_skill": "<name>"}</meta>`` tag is appended to
        the message so the skill is injected into the recipient's
        context for clean 1:1 attribution. Omit or pass None for
        backward-compatible behavior (no meta-tag appended). Sends
        bearing ``load_skill`` always dispatch via the enqueue
        pipeline (the only path with a meta-tag parser) — even when
        the target is injection-eligible.
    context: Optional structured context dict to inject before the
        task message. Keys are free-form (suggested: 'files',
        'notes', 'plan_ref', 'conventions'). Values may be lists or
        strings. When provided and non-empty, formatted into a
        ``[SYSTEM CONTEXT: Task Context]`` block and injected as a
        separate HumanMessage BEFORE the task message. Omit or pass
        None for backward-compatible behavior (no context injected).
        Sends bearing a non-empty ``context`` always dispatch via the
        enqueue pipeline (the only path with a metadata channel for
        the context block).

Returns:
    A human-readable status string. The shape varies by routing
    branch:
      * ``"Message content is empty; nothing to send."``
        (trim-check reject — no dispatch).
      * ``"Instance '<id>' not found; no message dispatched."``
        (not-found — no dispatch).
      * ``"Instance '<id>' is PAUSED. …"`` (PAUSED reject —
        no dispatch; full text below).
      * ``"Message injected into {prior_status} target. …"``
        (injection; includes the R-O2 W3 stranding caveat).
      * ``"Instance was {prior_status} — revived and message
        dispatched. Message queued and sent to <id>. …"``
        (terminal-revive).
      * ``"Message queued and sent to <id>. …"`` (enqueue parity).

Example outputs::

    # trim-check reject:
    "Message content is empty; nothing to send."

    # injection (RUNNING / WAITING_CHILDREN):
    "Message injected into running target. The next agent_node cycle
    will deliver it to the live turn.

    Note: if the target is paused or the daemon restarts before
    delivery, an in-flight injected message may be dropped
    (pause-loss parity with the user messages API)."

    # terminal revive (COMPLETED / TERMINATED / ERROR / FAILED):
    "Instance was completed — revived and message dispatched. Message
    queued and sent to <id>. The completion report will be delivered
    to you automatically as a new message that resumes your turn the
    moment the child finishes — do not poll or sleep waiting for it.
    You may continue other work (spawn more children, send more
    messages, etc.) in the meantime; when you have nothing left to
    do, end your turn and the report will arrive on its own."

    # PAUSED reject (no dispatch):
    "Instance '<id>' is PAUSED. Paused instances cannot receive messages; delivery is rejected to respect the pause (operator/lifecycle intent). Wait for it to be resumed via the API/UI, or proceed with other work."
"""

    # ──────────────────────────────────────────────────────────────────
    # Phase 2 (agent-instance-tools) — ``subtree_messages`` read-only
    # subtree query. Registered in the ``instance`` category so
    # ``tools.allow: ["subtree_messages"]`` (narrow opt-in) OR
    # ``tools.allow: ["instance"]`` (whole-category) both grant access.
    # The default args mirror ``job_messages`` (job_queue.py:1447-1503)
    # for consistency across the tool surface.
    #
    # Authorization: ``_validate_subtree_target`` is the SINGLE
    # chokepoint — it calls ``manager.get_tree_ids_permanent(...)`` (the
    # leader-approved facade seam) and returns a ``(allowed, subtree_ids)``
    # tuple. The tool layer MUST NOT reach into
    # ``manager._instance_repository`` directly (D14).
    #
    # Reads: ``await manager.get_messages(iid)`` per subtree instance —
    # the canonical saver-based read (NOT the older graph-state path).
    # Per-instance errors are caught and warned; the whole query never
    # fails on a single bad instance.
    #
    # D12 (synthetic exclusion) is applied at RETRIEVAL time (not in the
    # formatter) so synthetic token costs never reach the agent.
    # ──────────────────────────────────────────────────────────────────
    @register_tool_category("instance")
    @tool
    async def subtree_messages(
        target_instance_id: str | None = None,
        filters: dict | None = None,
        limit: int = 50,
        offset: int = 0,
        max_instances: int = 20,
        cap_first_N_per_instance: int = 0,
        summary: bool = False,
    ) -> str:
        """Query messages across the caller's subtree. Read-only. Use tool_help("subtree_messages") for details."""
        # ── 1. Validate args ────────────────────────────────────────
        if limit < 0:
            return (
                "ERROR: subtree_messages: limit must be >= 0 "
                f"(got {limit})."
            )
        if offset < 0:
            return (
                "ERROR: subtree_messages: offset must be >= 0 "
                f"(got {offset})."
            )
        if max_instances <= 0:
            return (
                "ERROR: subtree_messages: max_instances must be > 0 "
                f"(got {max_instances})."
            )
        if cap_first_N_per_instance < 0:
            return (
                "ERROR: subtree_messages: cap_first_N_per_instance "
                f"must be >= 0 (got {cap_first_N_per_instance})."
            )

        filters = filters or {}
        if not isinstance(filters, dict):
            return (
                "ERROR: subtree_messages: filters must be a dict "
                f"(got {type(filters).__name__})."
            )

        # Canonical role filter — reject ``"human"``/``"ai"`` and other
        # non-canonical names per Phase 2 §7 #4. ``"system"`` is
        # allowed at the filter level but is heavily pruned at retrieval
        # time by the D12 helper (real descendant system messages are
        # dropped; caller's own system messages are kept).
        role_filter = filters.get("role")
        if role_filter is not None and role_filter != "":
            if role_filter not in _SUBTREE_CANONICAL_ROLES:
                return (
                    "ERROR: subtree_messages: filters.role must be one "
                    "of "
                    f"{sorted(_SUBTREE_CANONICAL_ROLES)} "
                    f"(canonical lowercase per daemon/utils.py:96), "
                    f"got {role_filter!r}. The pre-serialization names "
                    "'human' and 'ai' are NOT accepted."
                )

        child_filter = filters.get("child_instance_id")
        if child_filter is not None and not isinstance(child_filter, str):
            return (
                "ERROR: subtree_messages: filters.child_instance_id "
                f"must be a string, got {type(child_filter).__name__}."
            )

        status_filter = filters.get("status")
        if status_filter is not None and not isinstance(status_filter, str):
            return (
                "ERROR: subtree_messages: filters.status must be a "
                f"string, got {type(status_filter).__name__}."
            )

        # Combined-filter semantics: child_instance_id + target
        # together is an error UNLESS equal (the target-as-its-own-
        # descendant edge case where target == child_instance_id).
        if (
            child_filter is not None
            and target_instance_id is not None
            and child_filter != target_instance_id
        ):
            return (
                "ERROR: subtree_messages: filters.child_instance_id "
                f"({child_filter!r}) and target_instance_id "
                f"({target_instance_id!r}) must be equal when both are "
                "set."
            )

        # ── 2. Resolve subtree (single authz chokepoint) ─────────────
        allowed, subtree_ids = _validate_subtree_target(
            manager, current_instance_id, target_instance_id
        )
        if not allowed:
            return (
                "ERROR: subtree_messages: target_instance_id "
                f"{target_instance_id!r} is not in the caller's "
                "subtree, or the caller has no observable lineage. "
                "The tool can only read messages from instances the "
                "caller spawned (caller + descendants)."
            )

        # Resolve the canonical target: None → caller.
        resolved_target = target_instance_id or current_instance_id

        # ── 3. Cap enforcement: first N by instance_id sort ─────────
        # Sort the subtree by instance_id so the subset returned is
        # stable across calls (otherwise dict/set iteration order would
        # differ between Python sessions).
        sorted_subtree = sorted(subtree_ids)
        truncated_by_cap = len(sorted_subtree) > max_instances
        working_set = sorted_subtree[:max_instances]

        # ── 4. Status filter: gather via Semaphore(5) gather ────────
        status_map: dict[str, str | None] = {}
        if status_filter is not None:
            status_sem = asyncio.Semaphore(5)

            async def _fetch_status(iid: str) -> tuple[str, str | None]:
                async with status_sem:
                    # ``await asyncio.sleep(0)`` yields control to the
                    # event loop so the ``Semaphore(5)`` actually
                    # interleaves concurrent status fetches — without
                    # it, a purely sync body acquires+releases in the
                    # same tick and the semaphore is decorative.
                    # Behavior-neutral: does not change correctness or
                    # output; only makes the concurrency limit real.
                    await asyncio.sleep(0)
                    try:
                        info = manager.get_instance_info(iid)
                    except Exception as e:  # KeyError + defsive
                        logger.warning(
                            "subtree_messages: get_instance_info(%s) "
                            "failed: %s: %s",
                            iid, type(e).__name__, e,
                        )
                        return iid, None
                    if not isinstance(info, dict):
                        return iid, None
                    return iid, info.get("status")

            status_results = await asyncio.gather(
                *[_fetch_status(iid) for iid in working_set]
            )
            status_map = dict(status_results)

            working_set = [
                iid for iid in working_set
                if status_map.get(iid) == status_filter
            ]

        # ── 5. Per-instance read loop (get_messages once per iid) ───
        collected: list[tuple[str, dict]] = []  # (instance_id, msg)
        for iid in working_set:
            # child_instance_id filter BEFORE the read so filtered-out
            # instances are NEVER read. The EXACTLY-one-read-per-
            # remaining-instance property holds for the post-filter
            # working set, not the pre-filter one — see the docstring
            # "Read path" section below for the full invariant.
            if child_filter is not None and child_filter != "":
                if iid != child_filter:
                    continue

            is_descendant = iid != current_instance_id
            try:
                msgs = await manager.get_messages(iid)
            except Exception as e:
                logger.warning(
                    "subtree_messages: get_messages(%s) failed: %s: %s",
                    iid, type(e).__name__, e,
                )
                continue
            if not isinstance(msgs, list):
                msgs = []

            # D12 exclusion at retrieval time (per §3b).
            msgs = _filter_subtree_messages(msgs, is_descendant=is_descendant)

            # Role filter applied AFTER D12 so callers that filter
            # for "system" on their OWN subtree still see something
            # (the caller's system prompt is kept by D12).
            if role_filter is not None and role_filter != "":
                msgs = [m for m in msgs if m.get("role") == role_filter]

            # Per-instance breadth cap (cap_first_N_per_instance > 0):
            # take the first N per instance BEFORE global pagination
            # (matches job_messages behavior at job_queue.py).
            if cap_first_N_per_instance > 0 and len(msgs) > cap_first_N_per_instance:
                msgs = msgs[:cap_first_N_per_instance]

            for m in msgs:
                collected.append((iid, m))

        # ── 6. Global pagination across merged collection ───────────
        # Compaction is destructive — pre-compaction messages are
        # replaced by ``RemoveMessage`` sentinels + a SystemMessage
        # summary (daemon/compaction.py:1036-1070). Offsets returned
        # today may not correspond to the same messages tomorrow.
        # This is documented behavior, NOT a bug; agents MUST re-query
        # after a compaction if they need stable pagination.
        total_collected = len(collected)
        end = offset + limit if limit > 0 else None
        if end is None:
            window = collected[offset:]
        else:
            window = collected[offset:end]

        has_more = (offset + len(window)) < total_collected
        if offset > 0 and not window:
            warning_lines = [
                f"WARNING: offset={offset} is past the end of the "
                f"current collection ({total_collected} messages). "
                "This is often caused by a compaction event between "
                "queries — re-query with offset=0 to see the current "
                "state."
            ]
        elif has_more:
            warning_lines = []
        else:
            warning_lines = []

        if truncated_by_cap:
            warning_lines.append(
                f"WARNING: subtree has {len(sorted_subtree)} "
                f"instances; only the first {max_instances} (sorted "
                "by instance_id) were queried. Increase max_instances "
                "(<= 100) to inspect the rest, or split the query "
                "across multiple targets."
            )

        # ── 7. Render per-instance blocks (job_messages style) ──────
        # Group by instance_id for human readability. Sort by
        # instance_id for stable output.
        per_instance: dict[str, list[dict]] = {}
        for iid, m in window:
            per_instance.setdefault(iid, []).append(m)

        block_lines: list[str] = []
        block_lines.append(
            f"subtree_messages(target={resolved_target!r}, "
            f"limit={limit}, offset={offset}, "
            f"max_instances={max_instances}, "
            f"cap_first_N_per_instance={cap_first_N_per_instance}, "
            f"summary={summary}, "
            f"filters={filters!r})"
        )
        block_lines.append(
            f"subtree_size={len(sorted_subtree)} "
            f"(returned {len(working_set)} after caps/filter)"
        )
        block_lines.append(
            f"messages={len(window)} of {total_collected} collected"
        )

        for iid in sorted(per_instance.keys()):
            block_lines.append("")
            block_lines.append(f"=== instance_id: {iid} ===")
            for m in per_instance[iid]:
                block_lines.append(_render_subtree_message(m, summary=summary))

        output = "\n".join(block_lines)

        # ── 8. Output ceiling with tail-truncate + warning ──────────
        if len(output) > _SUBTREE_OUTPUT_CEILING_CHARS:
            truncated_at = _SUBTREE_OUTPUT_CEILING_CHARS
            output = (
                output[:truncated_at]
                + "\n\n[output truncated at "
                f"{_SUBTREE_OUTPUT_CEILING_CHARS} chars; "
                "reduce limit, lower max_instances, or set "
                "summary=True to fit.]"
            )
            warning_lines.append(
                "Output exceeded the "
                f"{_SUBTREE_OUTPUT_CEILING_CHARS}-char ceiling "
                "and was tail-truncated."
            )

        if warning_lines:
            output = output + "\n\n" + "\n".join(warning_lines)

        return output

    subtree_messages._full_doc_ = """Query messages across the caller's subtree (read-only).

Phase 2 (agent-instance-tools): a parent agent can introspect the
conversation history of every instance it spawned (children,
grandchildren, great-grandchildren, …), strictly scoped to its own
subtree. Useful for post-mortem review of a delegated plan, audit of a
multi-step workflow, or surface-rendering the context a descendant was
operating under.

Authorization
-------------

The tool can ONLY read messages from instances in the CALLER'S subtree.
Subtree is defined by a Python-side BFS over ``instances.parent_id``
(permanent lineage — survives completion, error, terminate, revive;
``daemon/repositories/instance/repository.py:428-492``, depth-capped
256). The ``instance_hierarchy`` working set is NOT consulted — it is
transient and gets drained at terminate / child_reports / error paths.

  * ``target_instance_id=None`` → caller's OWN subtree (no root-walk).
  * ``target_instance_id=`` some descendant → that descendant's subtree.
  * ``target_instance_id`` outside the caller's subtree → permission
    error.

This is the AUTHORIZATION — there is no separate per-instance ACL.
Cross-subtree queries are rejected at retrieval time. The tool layer
calls the leader-approved facade ``Manager.get_tree_ids_permanent(...)``
— it MUST NOT reach into ``manager._instance_repository`` directly (D14).

Synthetic-message exclusion (D12, §3b)
---------------------------------------

When the resolved target ≠ caller (i.e. reading a descendant's
messages), the tool DROPS at retrieval time:

  * every ``is_synthetic=True`` message (the per-turn context rebuild
    emitted by ``GET /messages``);
  * every ``message_id`` prefixed ``synthetic-system-`` /
    ``synthetic-context-`` (``daemon/persistence.py:437, 669``); and
  * every REAL ``role="system"`` message authored by the descendant
    (descendant system prompts are persona-privileged and not
    shareable).

The filter happens at retrieval time — not in the formatter — so
synthetic token costs never reach the agent. When the resolved target
== caller, the caller's own system messages are KEPT (they are part of
the caller's own context).

Read path
---------

Each subtree instance is read ONCE via ``await manager.get_messages(iid)``
(the canonical saver-based read; ``daemon/routers/instances.py:1422-1489``
and ``daemon/tools/job_queue.py:1470`` ride the same path). The thread
config is built inside ``get_instance_messages``
(``daemon/persistence.py:309``). The older graph-state path is
explicitly NOT used here — that API does not exist; this tool reads via
the saver/checkpoint machinery only (regression guard against the
broken API reappearing).

When ``filters.child_instance_id`` is set, the membership check runs
BEFORE ``get_messages`` so filtered-out instances are never read —
the EXACTLY-one-read-per-remaining-instance property holds for the
post-filter working set, not the pre-filter one.

Per-instance errors are caught and warned; the whole query never fails
because one descendant has a missing / corrupt checkpoint.

Status filter
-------------

When ``filters.status`` is set, the tool calls
``manager.get_instance_info(iid)`` per working-set instance under
``asyncio.Semaphore(5)``. The N× call fan-out is acceptable for v1
(no bulk ``get_many_by_ids()`` exists in the facade today).

Pagination
----------

  * ``limit`` (default 50) — GLOBAL cap across the merged collection
    (NOT per-instance), matching ``job_messages``
    (``daemon/tools/job_queue.py:1447-1503``).
  * ``offset`` (default 0) — GLOBAL offset across the merged collection.
  * ``cap_first_N_per_instance`` (default 0) — when > 0, take only the
    first N messages from each instance BEFORE global pagination. Useful
    for breadth-first sampling.

KNOWN LIMITATION — compaction offset instability:

  Context compaction (``daemon/compaction.py:1036-1070``) is
  destructive: pre-compaction messages are replaced by ``RemoveMessage``
  sentinels + a SystemMessage summary. ``offset/limit`` returned today
  may not correspond to the same messages tomorrow. Agents MUST re-query
  with ``offset=0`` after observing unexpected pagination behavior.

Token safety
------------

  * Per-message content (full mode) is truncated to 200 chars + ellipsis.
  * ``ToolMessage`` is redacted to ``[tool_name] <first 100 chars of args>``
    — the raw tool-output ``content`` is OMITTED (it would otherwise
    dominate the token budget).
  * Per-instance cap: ``max_instances=20`` (default). When the subtree
    exceeds the cap, the first 20 instances by ``instance_id`` sort are
    returned + a warning.
  * Output ceiling: ~8000 chars. Tail-truncated with a warning; reduce
    ``limit``, lower ``max_instances``, or set ``summary=True`` to fit.
  * ``summary=True`` mode (default False) emits ONLY
    ``[role] (created_at) tools=… <first 80 chars of content>`` per
    message — drops tool-output content and reduces output budget
    ~80% versus full mode.

Args:
    target_instance_id: Root of subtree to query. ``None`` = caller's
        own subtree (no root-walk).
    filters: ``{"role": str, "child_instance_id": str, "status": str}``.
        All three keys are optional; combined with AND semantics. Roles
        MUST be the canonical lowercase ``"user" | "assistant" |
        "tool" | "system"`` (``daemon/utils.py:96``) — ``"human"`` and
        ``"ai"`` are NOT accepted.
    limit: Global message cap across the merged collection
        (default 50).
    offset: Global offset across the merged collection (default 0).
    max_instances: Total instance cap (default 20). Subtree beyond the
        cap is silently truncated (sorted by instance_id); a warning
        surfaces in the output.
    cap_first_N_per_instance: When > 0, take only the first N messages
        per instance before global pagination (default 0 = no
        per-instance cap).
    summary: When True, emit metadata-only mode (default False).

Returns:
    A human-readable string of per-instance blocks (matches the
    ``job_messages`` output style). Warnings are appended at the end
    (compaction-instability notice, cap truncation, output-ceiling
    truncation). On permission error (target outside subtree, missing
    caller lineage), returns an ``ERROR: subtree_messages: ...`` line —
    no partial output, no leak.

Example outputs::

    subtree_messages(target=None, limit=10, summary=True)

    subtree_messages(target='i-grandchild-7', filters={"role": "assistant"})

    subtree_messages(target=None, filters={"child_instance_id": "i-child-3"})
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
    
    # Create inner_soul tool for self-modification.
    # Thread version_tag so v2+ agents self-modify the versioned agent
    # subtree (C1 fix — base/v1 was being written by v2 instances).
    inner_soul = create_inner_soul_tool(manager, agent_id, current_instance_id, version_tag=version_tag)

    # Create access_memory tool for reading memory files.
    # Thread version_tag so v2+ agents read the versioned memories/
    # subtree (C1 fix — base/v1 was being read for v2 instances).
    access_memory = create_access_memory_tool(agent_id, version_tag=version_tag)
    
    # Create project management tools (with instance context for creator tracking)
    # and job queue management service for system queue provisioning
    queue_mgmt_service = getattr(manager, '_job_queue_mgmt_service', None)
    project_tools = create_project_tools(
        manager.project_store,
        current_instance_id,
        agent_id,
        job_queue_mgmt_service=queue_mgmt_service,
    )

    # Plane sync tool — registered in the "plane_sync" category so the
    # worker agent (with tools.allow: ["plane_sync"]) can invoke
    # ``plane_sync_project`` directly. PM dispatches sync tasks to
    # worker. Wraps PlaneSyncService behind a 30s per-project cooldown.
    from .plane_sync import create_plane_sync_tools
    plane_sync_tools = create_plane_sync_tools(manager.project_store)
    
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
        spawn_councilor,          # Phase 2: council category — governor-only
        clear_councilor_errors,   # Phase 2: council category — governor-only
        convene_council,          # Council category — team-membership authorized
        convene_council_with_skill,  # Council category — team-membership authorized (skill-injection variant)
        send_message,
        terminate_instance,
        list_instances,
        get_instance_info,
        subtree_messages,           # Phase 2: read-only subtree query (opt-in)
        # Self-modification tool
        inner_soul,
        # Memory access tool
        access_memory,
    ]
    
    # Add project management tools (available in all instances)
    tools.extend(project_tools)

    # Add Plane sync tool (plane_sync category auto-attached; the
    # worker agent picks it up via ``tools.allow: ["plane_sync"]``).
    tools.extend(plane_sync_tools)

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
    # C8: thread ``manager`` so the ``project_history_add`` hook can
    # feed the Blueprint pending-queue for ``feature``/``milestone``
    # entries. The factory itself still works with manager=None — this
    # only enables the Blueprint sidecar when a manager is reachable.
    history_tools = create_project_history_tools(
        manager.project_store, current_instance_id, agent_id,
        manager=manager,
    )
    tools.extend(history_tools)
    
    # Create job tools if job service is available
    # F2: forward ``version_tag`` as ``agent_tag`` so the agent-facing
    # ``job_create`` tool resolves jobs to the correct versioned ``agent_dir``.
    job_tools = create_job_tools_if_available(manager, current_instance_id, agent_id, agent_tag=version_tag)
    tools.extend(job_tools)
    
    # Add mother tools if this is the _mother agent
    if agent_id == "_mother":
        mother_tools = create_mother_tools(manager, current_instance_id)
        tools.extend(mother_tools)

    # Create and add RAG tools (only when RAG is configured)
    if is_rag_enabled():
        rag_tool_list = create_rag_tools(manager, current_instance_id)
        tools.extend(rag_tool_list)

        # Forward agent_id so the explore() tool can resolve Explorer's
        # ``caller_model_overrides`` for the calling agent (e.g. swap
        # Explorer from its default "quick" model to the system default
        # when the caller is "coder").
        knowledge_tool_list = create_knowledge_tools(manager, current_instance_id, agent_id=agent_id)
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
    # Mirrors the todo wiring above. The single ``ask_questions`` tool stores a
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
    shared_meta_kv_tool_list = create_shared_meta_kv_tools(manager, current_instance_id)
    tools.extend(shared_meta_kv_tool_list)

    # ── Blueprint tools (project blueprint search/get/list, restricted create/update) ──
    # Always available — read tools are unrestricted; write tools (create/update)
    # are gated by runtime agent_id check (_is_writer_authorized).
    blueprint_tool_list = create_blueprint_tools(manager, current_instance_id, agent_id)
    tools.extend(blueprint_tool_list)

    # ── Doc maintenance tools (doc-maintainer agent's restricted write surface) ──
    # The doc-maintainer agent is the ONLY agent that should be invoking
    # doc_write / comment_edit (they are absent from all other agents'
    # tools.allow). commit_docs_validated is restricted to the blueprinter.
    doc_write_tool_list = create_doc_write_tools(manager, current_instance_id, agent_id)
    tools.extend(doc_write_tool_list)
    comment_edit_tool_list = create_comment_edit_tools(manager, current_instance_id, agent_id)
    tools.extend(comment_edit_tool_list)
    doc_commit_tool_list = create_doc_commit_tools(manager, current_instance_id, agent_id)
    tools.extend(doc_commit_tool_list)

    # ── System tools (read-only env / config / health snapshots) ──
    # Always available — internal agents use these for fast triage of
    # runtime state (which DB backend, which config section is loaded,
    # what env vars are in scope) without exposing the on-disk paths.
    # Secrets are masked by default; agents must opt into ``nomask=True``
    # to see raw values.
    system_tool_list = create_system_tools(manager, current_instance_id)
    tools.extend(system_tool_list)

    system_log_tool_list = create_system_log_tools(manager, current_instance_id, agent_id)
    tools.extend(system_log_tool_list)

    # ── System Upgrade tools (release/upgrade observability + actor pair; P2.2 A+B) ──
    # §8 checklist item 5 — the CRITICAL list-append: decorator-only =
    # never constructed = silently invisible (precedents: job_tools above,
    # question_tool_list). The category is PRIVILEGED (R-SR16): the tool
    # filter never default-grants it — a privileged category reached ONLY
    # via an explicit tools.allow entry naming "system_upgrade" (ari's
    # entry landed with Dispatch B).
    upgrade_tool_list = create_upgrade_tools(
        manager, current_instance_id, agent_id, agent_tag=version_tag
    )
    tools.extend(upgrade_tool_list)

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

    # Create help tool - needs mcp_tool_names for MCP category expansion.
    # Forward version_tag so help docs reflect the version's tools.allow/deny
    # (Batch 3 fix: create_help_tool was version-blind).
    help_tool = create_help_tool(tools, agent_id, mcp_tool_names, version_tag=version_tag)
    tools.append(help_tool)

    # Scan ALL tools (including MCP + help) to populate _tool_metadata
    # MUST run after all tools are added to the list
    scan_tools_for_full_docs(tools)
    
    # Apply tool filtering based on agent's tools config
    tools = _apply_tool_filter(tools, agent_id, mcp_tool_names, version_tag=version_tag)
    
    return tools


def _strip_privileged_category_tools(tools: list[Any]) -> list[Any]:
    """Remove privileged-category tools from a default-allow (unfiltered) list.

    R-SR16 (P2.2 tool-api-design.md §3.5, architect-resolved 2026-08-22):
    categories in ``PRIVILEGED_TOOL_CATEGORIES`` (today: ``system_upgrade``)
    are opt-in-only — an agent reaches them ONLY through an explicit
    ``tools.allow`` entry naming the category or one of its tools. The
    default-allow paths below (no tools config at all, or an empty
    allow+deny pair — e.g. ``watcher``) would otherwise default-grant them.

    Defense-in-depth with the ``resolve_tool_filter`` empty-allow branch:
    that one covers the empty-allow + non-empty-deny universe construction;
    this one covers the two return-all paths.
    """
    return [
        t for t in tools
        if getattr(t, "_tool_category", None) not in PRIVILEGED_TOOL_CATEGORIES
    ]


def _apply_tool_filter(tools: list[Any], agent_id: str, mcp_tool_names: list[str] | None = None, version_tag: str | None = None) -> list[Any]:
    """Apply tool filtering based on agent's tools configuration.
    
    Args:
        tools: List of all tools (before filtering)
        agent_id: The agent identifier to look up tools config
        mcp_tool_names: Optional list of MCP tool names for category expansion.
        version_tag: Optional version tag to resolve tools config from the
            versioned registry (e.g., ``"v2"``). Falls back to base resolved
            meta if the tagged version is not found.
        
    Returns:
        Filtered list of tools based on agent's tools config.
        Returns all tools if no config or config is empty.
    """
    # Import registry locally to avoid circular imports
    from ..registry import get_registry

    # Get agent metadata — prefer versioned meta when a version_tag is provided,
    # fall back to base resolved meta to preserve backward compatibility
    # (fix: reviewerv2 instances were getting base v1 tools.allow).
    registry = get_registry()
    agent_meta = registry.get_version(agent_id, version_tag)
    if agent_meta is None:
        agent_meta = registry.get_resolved(agent_id)

    if agent_meta is None or agent_meta.tools is None:
        # No tools config → all tools allowed (backward compatible) —
        # except privileged categories (R-SR16): opt-in-only, never
        # default-granted.
        return _strip_privileged_category_tools(tools)

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

    # If None returned, all tools are allowed — except privileged
    # categories (R-SR16): opt-in-only, never default-granted.
    if allowed_tools is None:
        return _strip_privileged_category_tools(tools)

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

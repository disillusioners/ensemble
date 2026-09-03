"""Tool registry for storing full documentation.

This module provides a registry for tool documentation that allows
tools to have short docstrings (for LLM context efficiency) while
still providing detailed documentation via the tool_help() function.
"""

from importlib import import_module
from typing import Callable, Any
import logging

_logger = logging.getLogger(__name__)

# Global registry: tool_name -> full documentation string
_full_docs: dict[str, str] = {}

# Tool metadata registry: tool_name -> {category, short_doc, full_doc}
_tool_metadata: dict[str, dict[str, Any]] = {}

# These tools are created by per-instance factories rather than registered during
# module import. They are nevertheless valid names in agent allow/deny config,
# so startup validation must know them before an instance is built.
DYNAMIC_TOOL_NAMES: frozenset[str] = frozenset({
    "rag_insert_text",
    "rag_insert_texts",
    "rag_query",
    "rag_query_data",
    "rag_search_labels",
    "rag_get_graph",
    "rag_create_entity",
    "rag_get_entity",
    "rag_create_relation",
    "rag_update_entity",
    "rag_merge_entities",
    "rag_delete_entity",
    "rag_delete_relation",
    "rag_delete_docs",
    "rag_list_docs",
    "rag_track_status",
    # db_* tools are created by create_db_tools() per-instance factory, not
    # registered at import time; see daemon/tools/db_tools.py.
    "db_conn_add",
    "db_conn_delete",
    "db_conn_list",
    "db_conn_test",
    "db_postgres_dml_select",
    "tool_help",
    "explore",
    "experience",
    "ens_system_log_list",
    "ens_system_log_read",
    "ens_system_log_search",
    "ens_system_log_tail",
    "commit_docs_validated",
    "doc_write",
    "comment_edit",
    "skill_search",
    "shared_meta_kv",
    # P0 job visibility tools — created by create_job_tools() factory
    "job_messages",
    "job_tree",
    "job_progress",
    "job_inject",
    # Mission tools (M2 of mission-class, 2026-09-02) — created by
    # ``create_mission_tools()`` factory in ``daemon/tools/missions.py``.
    # These are READ-ONLY readers of the mission projection; census
    # untouched.
    "get_mission",
    "await_mission",
    "list_missions",
    # system_upgrade tools — created by create_upgrade_tools() factory
    # (daemon/tools/upgrade_tools.py, P2.2 Dispatch A read pair + Dispatch B
    # actor pair).
    "release_info",
    "upgrade_status",
    "system_restart",
    "system_upgrade",
})


# Tool name prefixes that are dynamically discovered at runtime (e.g. via MCP
# servers) and therefore cannot be statically registered. Validation must skip
# these — the tool names are only known after the MCP server responds.
DYNAMIC_TOOL_PREFIXES: frozenset[str] = frozenset({
    "plane_",
})


# Categories that are OPT-IN-ONLY regardless of the empty-allow default.
#
# R-SR16 (P2.2 tool-api-design.md §3.5, architect-resolved 2026-08-22):
# ``resolve_tool_filter`` treats an absent/empty allow list as "everything
# is potentially allowed" — which would default-grant these categories to
# every agent created without an explicit allow-list (watcher is empty-allow
# today). Privileged categories NEVER join that default universe: an agent
# reaches them ONLY through an explicit ``tools.allow`` entry naming the
# category or one of its tools. Enforced structurally in
# ``daemon.tools.instance`` (the ``resolve_tool_filter`` empty-allow branch
# and the default-allow paths of ``_apply_tool_filter``) — no deny rules
# needed. Today: ``system_upgrade`` (restart/upgrade authority).
PRIVILEGED_TOOL_CATEGORIES: frozenset[str] = frozenset({
    "system_upgrade",
})


def register_tool_category(category: str):
    """Decorator to mark a tool with its category.
    
    Usage:
        @register_tool_category("filesystem")
        @tool
        def read_file(...):
            ...
    
    Args:
        category: The category key (e.g., "filesystem", "instance").
    
    Returns:
        Decorator function that sets _tool_category on the tool.
    """
    def decorator(func):
        func._tool_category = category
        # First-party provenance marker. scan_tools_for_full_docs only
        # honors a category OVERRIDE on rescan (existing entry) when this
        # flag is present, so attacker-influenced tool lists (e.g. MCP
        # tools whose ``name`` is server-controlled and may collide with
        # first-party tool names) cannot silently re-categorize an
        # already-registered entry via a spoofed ``_tool_category`` attr.
        func._tool_category_first_party = True
        return func
    return decorator


def register_tool(
    tool_name: str,
    category: str = "general",
    short_doc: str = "",
    full_doc: str = "",
) -> Callable:
    """Decorator to register a tool with its documentation.
    
    Usage:
        @register_tool("project_create", category="project")
        @tool
        def project_create(...):
            ...
            project_create._full_doc_ = "..."
    
    Args:
        tool_name: Unique tool identifier.
        category: Tool category for grouping (e.g., "project", "instance").
        short_doc: Brief one-line description.
        full_doc: Detailed documentation.
    
    Returns:
        Decorator function.
    """
    def decorator(func: Callable) -> Callable:
        _tool_metadata[tool_name] = {
            "category": category,
            "short_doc": short_doc,
            "full_doc": full_doc,
        }
        if full_doc:
            _full_docs[tool_name] = full_doc
        return func
    return decorator


def register_full_doc(tool_name: str, full_doc: str) -> None:
    """Register full documentation for a tool.
    
    Called automatically when a tool function has a _full_doc_ attribute.
    
    Args:
        tool_name: The tool identifier.
        full_doc: Full documentation string.
    """
    _full_docs[tool_name] = full_doc


def get_full_doc(tool_name: str) -> str | None:
    """Retrieve full documentation for a tool.
    
    Args:
        tool_name: The tool identifier.
    
    Returns:
        Full documentation string, or None if not found.
    """
    return _full_docs.get(tool_name)


def get_tool_metadata(tool_name: str) -> dict[str, Any] | None:
    """Get metadata for a specific tool.
    
    Args:
        tool_name: The tool identifier.
    
    Returns:
        Dictionary with category, short_doc, full_doc, or None.
    """
    return _tool_metadata.get(tool_name)


def list_tools() -> dict[str, dict[str, Any]]:
    """List all registered tools with their metadata.
    
    Returns:
        Dictionary mapping tool names to their metadata.
    """
    return dict(_tool_metadata)


def list_tools_by_category() -> dict[str, list[str]]:
    """List tools grouped by category.
    
    Returns:
        Dictionary mapping category names to lists of tool names.
    """
    categories: dict[str, list[str]] = {}
    for tool_name, meta in _tool_metadata.items():
        cat = meta.get("category", "general")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(tool_name)
    return categories


def _scan_category_module_sources() -> tuple[set[str], bool]:
    """AST-scan every CATEGORY_MODULES source file for ``@tool``-decorated names.

    Shared by ``discover_all_tool_names()`` (which then merges with the static
    ``KNOWN_TOOL_NAMES`` fallback) and ``discover_source_only_tool_names()``
    (which raises on zero-source — the frozen-binary case).

    Returns:
        (tool_names, any_source_read) — ``any_source_read`` is True iff at least
        one category-module source file was actually read from disk. The AST
        walker descends into every nested scope so factory-internal ``@tool``
        decorations are caught (they never register at import time).
    """
    import ast
    from pathlib import Path

    tool_names: set[str] = set()
    # Tracks whether ANY source file was actually read on this call. When the
    # whole daemon/ tree is bytecode-only (frozen binary), this stays False and
    # the caller decides what to do (fallback vs raise).
    any_source_read = False

    for category_key, module_path_str in CATEGORY_MODULES.items():
        paths = module_path_str if isinstance(module_path_str, list) else [module_path_str]
        for mod_path in paths:
            # Resolve module path to file path
            # e.g. "daemon.tools.project" -> daemon/tools/project.py
            parts = mod_path.split(".")
            # Skip the "daemon" prefix to find the package root
            # Try relative path from this file's location
            try:
                # Walk up from this file (daemon/tools/_tool_registry.py) to find the file
                # daemon/tools/_tool_registry.py -> parent is daemon/tools/
                base_dir = Path(__file__).parent  # daemon/tools/
                # mod_path like "daemon.tools.project" -> parts ["daemon", "tools", "project"]
                # We need the relative path from the daemon package root
                # daemon/tools/ corresponds to "daemon.tools"
                file_path = base_dir
                for part in parts[2:]:  # skip "daemon", "tools"
                    file_path = file_path / part
                file_path = file_path.with_suffix(".py")

                if not file_path.exists():
                    continue

                any_source_read = True
                source = file_path.read_text()
                tree = ast.parse(source)

                # Walk the ENTIRE tree (not just top-level) to catch tools
                # defined inside factory functions
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    # Check if any decorator is @tool (bare or called: @tool or @tool(...))
                    has_tool_decorator = False
                    for dec in node.decorator_list:
                        dec_name = ""
                        if isinstance(dec, ast.Name):
                            dec_name = dec.id
                        elif isinstance(dec, ast.Attribute):
                            dec_name = dec.attr
                        elif isinstance(dec, ast.Call):
                            if isinstance(dec.func, ast.Name):
                                dec_name = dec.func.id
                            elif isinstance(dec.func, ast.Attribute):
                                dec_name = dec.func.attr
                        if dec_name == "tool":
                            has_tool_decorator = True
                            break

                    if has_tool_decorator:
                        tool_names.add(node.name)

            except (OSError, SyntaxError, Exception):
                continue

    return tool_names, any_source_read


def discover_source_only_tool_names() -> set[str]:
    """Pure source discovery — NO merge with ``KNOWN_TOOL_NAMES``, NO frozen fallback.

    This function is the regen source of truth for the ``KNOWN_TOOL_NAMES``
    static fallback and the canonical basis for bidirectional drift detection
    between source and the static universe. It MUST return only what the
    on-disk ``daemon/tools/`` source contains; it never augments with the
    static list.

    Frozen-binary contract: when ZERO ``CATEGORY_MODULES`` source files are
    readable (typical PyInstaller frozen build where ``daemon/`` ships as
    bytecode only), this function raises ``RuntimeError`` rather than
    silently returning the static universe. Drift detection in a frozen
    environment is not meaningful — callers that need the frozen-safe
    merged result should use ``discover_all_tool_names()`` instead.

    Returns:
        Set of ``@tool``-decorated function names discovered by AST-scanning
        every CATEGORY_MODULES source file on disk.

    Raises:
        RuntimeError: if ZERO ``CATEGORY_MODULES`` source files are readable
            (frozen binary, or path resolution failed for every category).
    """
    tool_names, any_source_read = _scan_category_module_sources()
    if not any_source_read:
        raise RuntimeError(
            "discover_source_only_tool_names(): no CATEGORY_MODULES source files "
            "readable (frozen binary?) — source-only discovery is unavailable; "
            "use discover_all_tool_names() for the frozen-safe universe"
        )
    return tool_names


def discover_all_tool_names() -> set[str]:
    """Statically discover all @tool-decorated function names across CATEGORY_MODULES.

    Many tools are created inside factory functions (e.g. ``create_project_tools``)
    and use ``@tool`` inside the factory body, so they never register in
    ``_tool_metadata`` at import time. This function AST-scans the source of every
    category module to find ``@tool``-decorated functions at ANY nesting depth.

    Frozen-binary safe: in PyInstaller-frozen builds the ``daemon/`` package ships
    as bytecode only and ``file_path.exists()`` is False for every category
    module. In that case the static fallback universe ``KNOWN_TOOL_NAMES`` is
    returned (and a single debug log is emitted). When some source files are
    readable but others are not, the result merges source-discovered names with
    ``KNOWN_TOOL_NAMES`` so source stays canonical where present and the static
    list covers the rest.

    For pure source-of-truth discovery (drift detection, regenerating
    ``KNOWN_TOOL_NAMES``), use ``discover_source_only_tool_names()`` instead.

    Returns:
        Set of tool names discoverable from static source analysis, augmented
        with the static ``KNOWN_TOOL_NAMES`` fallback.
    """
    tool_names, any_source_read = _scan_category_module_sources()

    if not any_source_read:
        # No CATEGORY_MODULES source files readable — typically a PyInstaller
        # frozen binary where daemon/ is bytecode-only. Fall back to the static
        # universe so factory-created tool names remain visible to the validator
        # and we don't emit false-positive "unknown tool" warnings. Single
        # debug log; not per-file.
        _logger.debug(
            "discover_all_tool_names: no CATEGORY_MODULES source files readable "
            "(frozen binary?); falling back to KNOWN_TOOL_NAMES (%d entries)",
            len(KNOWN_TOOL_NAMES),
        )
        return set(KNOWN_TOOL_NAMES)

    # Partial-source path: source is canonical where present; KNOWN_TOOL_NAMES
    # covers the rest. Merging is defensive — if a maintainer adds a @tool and
    # forgets to regenerate KNOWN_TOOL_NAMES, the source scan still catches it.
    tool_names |= KNOWN_TOOL_NAMES
    return tool_names


def clear_registry() -> None:
    """Clear all registered tools. Useful for testing."""
    _full_docs.clear()
    _tool_metadata.clear()


def scan_tools_for_full_docs(tools: list) -> None:
    """Scan a list of tools and register any with _full_doc_ attributes.
    
    This should be called after tools are created to pick up any
    _full_doc_ attributes that were set on tool functions.
    
    Args:
        tools: List of tool functions (from @tool decorator).
    """
    for tool_func in tools:
        tool_name = getattr(tool_func, 'name', None) or getattr(tool_func, '__name__', str(tool_func))
        
        # Check for _full_doc_ attribute
        if hasattr(tool_func, '_full_doc_'):
            register_full_doc(tool_name, tool_func._full_doc_)
        
        # Extract short doc from description or docstring
        short_doc = ""
        if hasattr(tool_func, 'description'):
            short_doc = tool_func.description.split('\n')[0]
        elif hasattr(tool_func, '__doc__') and tool_func.__doc__:
            short_doc = tool_func.__doc__.split('\n')[0]
        
        # Infer category from _tool_category attribute or name fallback
        category = getattr(tool_func, '_tool_category', None)
        if category is None:
            category = tool_name.split('_')[0] if '_' in tool_name else 'general'
        
        # Register metadata
        if tool_name not in _tool_metadata:
            _tool_metadata[tool_name] = {
                "category": category,
                "short_doc": short_doc,
                "full_doc": _full_docs.get(tool_name, ""),
            }
        else:
            # Update existing metadata.
            #
            # short_doc/full_doc always reflect the latest scan (different
            # closures may carry refreshed docstrings). category is sticky:
            # it is only updated when the scanning tool carries the
            # first-party provenance marker ``_tool_category_first_party``
            # (set only by @register_tool_category). This blocks an
            # attacker-influenced tool list (e.g. MCP tools with
            # server-controlled names) from spoofing ``_tool_category``
            # to re-categorize an already-registered first-party tool,
            # which would silently break ``tools.allow`` category
            # resolution. The workdir/instance_id wrappers in
            # daemon.tools.instance rebuild StructuredTool via
            # from_function which strips ``_tool_category``; the wrapper
            # helper re-applies it AND the provenance marker.
            _tool_metadata[tool_name]["short_doc"] = short_doc
            if getattr(tool_func, '_tool_category_first_party', False):
                _tool_metadata[tool_name]["category"] = category
            if tool_name in _full_docs:
                _tool_metadata[tool_name]["full_doc"] = _full_docs[tool_name]


# Category module mapping: category_key -> full module path(s)
CATEGORY_MODULES: dict[str, str | list[str]] = {
    "bash": "daemon.tools.bash",
    "critical_notes": "daemon.tools.critical_notes",
    "project_history": "daemon.tools.project_history",
    "filesystem": "daemon.tools.filesystem",
    "time": "daemon.tools.time",
    "instance": "daemon.tools.instance",
    "self": ["daemon.tools.inner_soul", "daemon.tools.access_memory"],
    "project": "daemon.tools.project",
    "job": "daemon.tools.job_queue",
    # Mission tools (M2 of mission-class, 2026-09-02) — additive
    # READ-ONLY surface for the mission read-model projection. No
    # writers; census stays at 23.
    "mission": "daemon.tools.missions",
    "help": "daemon.tools.help",
    "mother": "daemon.tools.agent_mother",
    "knowledge": "daemon.tools.knowledge_tools",
    "chart": "daemon.tools.chart_tools",
    "image": "daemon.tools.image_tools",
    "todo": "daemon.tools.todo_tools",
    "question": "daemon.tools.question_tools",
    "rag": "daemon.tools.rag_tools",
    "mcp": "daemon.tools.mcp_tools",
    "external_opencode": "daemon.tools.external_opencode",
    "context": "daemon.tools.context_tools",
    "shared_meta_kv": "daemon.tools.shared_meta_kv_tools",
    "db": "daemon.tools.db_tools",
    "infra": "daemon.tools.infra",
    "system": "daemon.tools.system",
    "skill-evolution": "daemon.tools.skill_evolution_tools",
    "dynamic-skill": "daemon.tools.skill_tools",
    "language": "daemon.tools.language_tools",
    "proc": "daemon.tools.proc_tools",
    "council": "daemon.tools.instance",  # Phase 2: spawn_councilor + clear_councilor_errors
    "blueprint": "daemon.tools.blueprint",
    "system-log": "daemon.tools.system_log_tools",
    "plane": "daemon.tools.plane_tools",
    "plane_sync": "daemon.tools.plane_sync",
    "system_upgrade": "daemon.tools.upgrade_tools",
}


# Static fallback universe of @tool-decorated function names across CATEGORY_MODULES.
#
# discover_all_tool_names() AST-scans source files on disk to find @tool-decorated
# functions at any nesting depth. In PyInstaller frozen binaries (e.g. ensemble-prod)
# daemon/ ships as bytecode only — file_path.exists() returns False for every
# category module and the discovery function silently returns an empty set. The
# result: every factory-created tool name (project_*, todo_view, terminate_instance,
# ...) vanishes from the validator universe and produces false-positive "unknown
# tool" warnings on agent boot (prod incident 2026-08-20 20:00:35, 32 warnings for
# agent project-manager).
#
# This constant is the frozen-binary fallback. discover_all_tool_names() falls back
# to set(KNOWN_TOOL_NAMES) when ZERO source files are readable, and merges with
# source-discovered names otherwise (source is canonical where present; the static
# list covers the rest).
#
# Maintenance: when you add a new @tool-decorated function to a CATEGORY_MODULES
# module, regenerate this set by running:
#
#     uv run python -c "from daemon.tools._tool_registry import discover_source_only_tool_names; print(sorted(discover_source_only_tool_names()))"
#
# The output is the unambiguous source-only universe (no merge with this
# constant, no frozen fallback). In a frozen-binary environment this command
# fails loudly with RuntimeError instead of producing a false-OK output —
# drift detection only makes sense from source. Bidirectional drift between
# source and KNOWN_TOOL_NAMES is caught by the test
# tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift.
#
# Paste the printed (sorted) names into the frozenset below. Keep this
# constant adjacent to CATEGORY_MODULES so a maintainer adding a new tool module
# is looking at exactly this area.
KNOWN_TOOL_NAMES: frozenset[str] = frozenset({
    "access_memory",
    "agent_create",
    "agent_delete",
    "agent_list",
    "agent_modify",
    "agent_read",
    "ask_questions",
    "await_mission",
    "bash",
    "blueprint_acknowledge_pending",
    "blueprint_claim_pending",
    "blueprint_create",
    "blueprint_disable",
    "blueprint_get",
    "blueprint_get_pending_count",
    "blueprint_list",
    "blueprint_release_lease",
    "blueprint_search",
    "blueprint_update",
    "clear_councilor_errors",
    "convene_council",
    "convene_council_with_skill",
    "db_conn_add",
    "db_conn_delete",
    "db_conn_list",
    "db_conn_test",
    "db_postgres_dml_select",
    "dlq_list",
    "dlq_replay",
    "edit_file",
    "ens_system_log_list",
    "ens_system_log_read",
    "ens_system_log_search",
    "ens_system_log_tail",
    "experience",
    "explain_image",
    "explore",
    "external_opencode_abort_session",
    "external_opencode_answer_question",
    "external_opencode_get_status",
    "external_opencode_init_session",
    "external_opencode_resume_session",
    "external_opencode_send_message",
    "external_opencode_wait_any",
    "external_opencode_wait_for_result",
    "generate_chart",
    "get_instance_info",
    "get_mission",
    "glob_files",
    "grep_files",
    "infra_asset_create",
    "infra_asset_delete",
    "infra_asset_get",
    "infra_asset_list",
    "infra_asset_search",
    "infra_asset_update",
    "infra_history_get",
    "infra_type_list",
    "infra_type_register",
    "inner_soul",
    "job_cancel",
    "job_continue",
    "job_create",
    "job_delete",
    "job_get",
    "job_inject",
    "job_list",
    "job_messages",
    "job_progress",
    "job_restore",
    "job_retry",
    "job_tree",
    "language_skip_check",
    "list_context",
    "list_directory",
    "list_instances",
    "list_missions",
    "list_watched_jobs",
    "plane_sync_project",
    "proc_list",
    "proc_logs",
    "proc_run",
    "proc_status",
    "proc_stop",
    "project_add_directory",
    "project_add_shortname",
    "project_add_tag",
    "project_cn_add",
    "project_cn_list",
    "project_cn_remove",
    "project_create",
    "project_delete",
    "project_delete_metadata",
    "project_get",
    "project_get_by_directory",
    "project_get_by_instance",
    "project_history_add",
    "project_history_delete",
    "project_history_list",
    "project_history_search",
    "project_link",
    "project_list",
    "project_remove_directory",
    "project_remove_shortname",
    "project_remove_tag",
    "project_search",
    "project_set_metadata",
    "project_set_shortnames",
    "project_set_status",
    "project_set_tags",
    "project_unlink",
    "project_update",
    "queue_create",
    "queue_list",
    "queue_update",
    "rag_create_entity",
    "rag_create_relation",
    "rag_delete_docs",
    "rag_delete_entity",
    "rag_delete_relation",
    "rag_get_entity",
    "rag_get_graph",
    "rag_insert_text",
    "rag_insert_texts",
    "rag_list_docs",
    "rag_merge_entities",
    "rag_query",
    "rag_query_data",
    "rag_search_labels",
    "rag_track_status",
    "rag_update_entity",
    "read_context",
    "read_file",
    "release_info",
    "send_message",
    "shared_meta_kv",
    "skill_analyze",
    "skill_create",
    "skill_evolve",
    "skill_execute_capture",
    "skill_feedback",
    "skill_fix",
    "skill_get_metrics",
    "skill_list",
    "skill_resolve_ab",
    "skill_search",
    "skill_view",
    "spawn_councilor",
    "spawn_instance",
    "subtree_messages",
    "subtree_status",
    "system_config",
    "system_env",
    "system_health",
    "system_restart",
    "system_upgrade",
    "terminate_instance",
    "time",
    "todo_clear",
    "todo_graph_add_edge",
    "todo_graph_add_subtask",
    "todo_graph_create",
    "todo_graph_remove_edge",
    "todo_graph_remove_subtask",
    "todo_graph_update",
    "todo_graph_update_subtask",
    "todo_list_create",
    "todo_list_update",
    "todo_view",
    "tool_help",
    "unwatch_job",
    "upgrade_status",
    "watch_job",
    "watch_jobs",
    "write_file",})


def get_tool_categories(allowed_tools: set[str] | None = None) -> dict[str, list[str]]:
    """Get tools grouped by their category, using CATEGORY_NAME as keys.
    
    Args:
        allowed_tools: Optional set of tool names to filter by.
    
    Returns:
        Dictionary mapping CATEGORY_NAME to list of tool names.
    """
    categories: dict[str, list[str]] = {}
    
    for tool_name, meta in _tool_metadata.items():
        # Filter by allowed_tools if provided
        if allowed_tools is not None and tool_name not in allowed_tools:
            continue
        
        category_key = meta.get("category", "general")
        
        # Look up CATEGORY_NAME from the module
        if category_key in CATEGORY_MODULES:
            try:
                module_paths = CATEGORY_MODULES[category_key]
                # Handle both str and list[str] values
                first_module = module_paths[0] if isinstance(module_paths, list) else module_paths
                module = import_module(first_module)
                category_name = getattr(module, "CATEGORY_NAME", category_key)
            except ImportError:
                category_name = category_key
        else:
            category_name = category_key
        
        if category_name not in categories:
            categories[category_name] = []
        categories[category_name].append(tool_name)
    
    return categories


def get_category_doc(category_key: str) -> tuple[str, str] | None:
    """Get CATEGORY_NAME and CATEGORY_DOC for a category.
    
    Args:
        category_key: The category key (e.g., "bash", "filesystem").
    
    Returns:
        Tuple of (CATEGORY_NAME, CATEGORY_DOC), or None if category doesn't exist.
    """
    if category_key not in CATEGORY_MODULES:
        return None
    
    try:
        module_paths = CATEGORY_MODULES[category_key]
        # Handle both str and list[str] values
        if isinstance(module_paths, list):
            category_name = None
            category_docs: list[str] = []
            for path in module_paths:
                module = import_module(path)
                if category_name is None:
                    category_name = getattr(module, "CATEGORY_NAME", category_key)
                doc = getattr(module, "CATEGORY_DOC", "")
                if doc:
                    category_docs.append(doc)
            return (category_name or category_key, "\n\n".join(category_docs))
        else:
            module = import_module(module_paths)
            return (
                getattr(module, "CATEGORY_NAME", category_key),
                getattr(module, "CATEGORY_DOC", ""),
            )
    except ImportError:
        return None

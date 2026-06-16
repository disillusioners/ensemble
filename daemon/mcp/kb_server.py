"""MCP server exposing Knowledge Base tools for external agent systems.

Provides ensemble_kb_explore and ensemble_kb_experience tools via
SSE and StreamableHTTP transports.
"""

import asyncio
import difflib
import json
import logging
import uuid
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from daemon.rag.config import is_rag_enabled
from daemon.services.context_tools import list_context_files, read_context_file
from daemon.tools.knowledge_tools import (
    _check_rag_errored_via_checkpoint,
    _check_rag_queried_via_checkpoint,
    _check_read_file_called_via_checkpoint,
    _enqueue_experience_job,
    _enqueue_kb_update_job,
)
from daemon.utils import invoke_agent_and_wait

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

# Module-level manager reference (set during app lifespan)
_manager: "InstanceManager | None" = None

# System parent ID for MCP-spawned agent instances
_MCP_SYSTEM_PARENT_ID = "mcp-kb-server"

# Valid explore modes
_VALID_MODES = ("local", "global", "hybrid", "naive")

# Module-level references (created once by create_kb_mcp_server)
_mcp_server: FastMCP | None = None
_http_app = None  # Eagerly created streamable_http_app


def set_kb_mcp_manager(manager: "InstanceManager") -> None:
    """Set the InstanceManager reference. Called during app lifespan startup."""
    global _manager
    _manager = manager


async def _resolve_project(
    project_id: str | None = None,
    project_name: str | None = None,
    project_path: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve a project identifier to a project_id UUID.

    When multiple identifiers provided, priority: project_id > project_name > project_path.

    Returns (project_id, error_message) — one will be None.
    """
    if _manager is None:
        return None, "Server not initialized: manager not set"

    # C2: Empty string validation
    if project_id is not None and not project_id.strip():
        return None, "Error: project_id must not be empty."
    if project_name is not None and not project_name.strip():
        return None, "Error: project_name must not be empty."
    if project_path is not None and not project_path.strip():
        return None, "Error: project_path must not be empty."

    if not project_id and not project_name and not project_path:
        # Nothing provided
        repo = _manager._project_repository
        all_projects = await asyncio.to_thread(repo.list_projects, limit=20)
        if not all_projects:
            return None, "No project identifier provided and no projects exist in the system."

        available_hint = ", ".join(
            f"{p.name}" + (f" ({', '.join(p.shortnames)})" if p.shortnames else "")
            for p in all_projects[:10]
        )
        return None, f"No project identifier provided. Please provide project_id, project_name, or project_path. Available projects: {available_hint}"

    repo = _manager._project_repository

    # Priority 1: project_id
    if project_id is not None:
        # Try exact match first (no need for full list)
        project = await asyncio.to_thread(repo.get, project_id)
        if project is not None:
            return project.project_id, None

        # Need fuzzy matching — fetch once
        all_projects = await asyncio.to_thread(repo.list_projects, limit=200)
        best_match = None
        best_ratio = 0.0
        for p in all_projects:
            ratio = difflib.SequenceMatcher(None, project_id.lower(), p.project_id.lower()).ratio()
            if ratio >= 0.6 and ratio > best_ratio:
                best_ratio = ratio
                best_match = p

        available_hint = ", ".join(
            f"{p.name}" + (f" ({', '.join(p.shortnames)})" if p.shortnames else "")
            for p in all_projects[:10]
        )

        if best_ratio >= 0.85:
            return best_match.project_id, None

        if best_ratio >= 0.7:
            shortnames = f" ({', '.join(best_match.shortnames)})" if best_match.shortnames else ""
            return None, f"Project ID '{project_id}' not found. Did you mean '{best_match.name}'{shortnames}?"

        return None, f"Project ID '{project_id}' not found. Available projects: {available_hint}"

    # Priority 2: project_name
    if project_name is not None:
        # Try exact match first (no need for full list)
        project = await asyncio.to_thread(repo.get_by_name, project_name)
        if project is not None:
            return project.project_id, None

        project = await asyncio.to_thread(repo.get_by_shortname, project_name)
        if project is not None:
            return project.project_id, None

        # Need fuzzy matching — fetch once
        all_projects = await asyncio.to_thread(repo.list_projects, limit=200)

        # W1: Short name guard
        if len(project_name) < 3:
            available_hint = ", ".join(
                f"{p.name}" + (f" ({', '.join(p.shortnames)})" if p.shortnames else "")
                for p in all_projects[:10]
            )
            return None, f"Project '{project_name}' not found. Available projects: {available_hint}"

        candidates = []
        for p in all_projects:
            name_ratio = difflib.SequenceMatcher(None, project_name.lower(), p.name.lower()).ratio()
            sn_ratios = [difflib.SequenceMatcher(None, project_name.lower(), sn.lower()).ratio() for sn in (p.shortnames or [])]
            best_sn_ratio = max(sn_ratios) if sn_ratios else 0.0
            best_for_project = max(name_ratio, best_sn_ratio)
            if best_for_project >= 0.6:
                candidates.append((p, best_for_project))

        candidates.sort(key=lambda x: x[1], reverse=True)

        available_hint = ", ".join(
            f"{p.name}" + (f" ({', '.join(p.shortnames)})" if p.shortnames else "")
            for p in all_projects[:10]
        )

        if candidates and candidates[0][1] >= 0.8:
            return candidates[0][0].project_id, None

        if candidates and candidates[0][1] >= 0.6:
            best = candidates[0][0]
            shortnames = f" ({', '.join(best.shortnames)})" if best.shortnames else ""
            return None, f"Project '{project_name}' not found. Did you mean '{best.name}'{shortnames}?"

        return None, f"Project '{project_name}' not found. Available projects: {available_hint}"

    # Priority 3: project_path
    if project_path is not None:
        # Try exact match first
        projects = await asyncio.to_thread(repo.get_by_directory, project_path)
        if projects:
            return projects[0].project_id, None

        # Need fuzzy matching — fetch once
        all_projects = await asyncio.to_thread(repo.list_projects, limit=200)
        candidates = []
        for p in all_projects:
            if p.main_directory:
                ratio = difflib.SequenceMatcher(None, project_path.lower(), p.main_directory.lower()).ratio()
                if ratio >= 0.5:
                    candidates.append((p, ratio))

        candidates.sort(key=lambda x: x[1], reverse=True)

        available_hint = ", ".join(
            f"{p.name}" + (f" ({', '.join(p.shortnames)})" if p.shortnames else "")
            for p in all_projects[:10]
        )

        if candidates and candidates[0][1] >= 0.7:
            return candidates[0][0].project_id, None

        # W2: threshold raised to 0.65
        if candidates and candidates[0][1] >= 0.65:
            best = candidates[0][0]
            shortnames = f" ({', '.join(best.shortnames)})" if best.shortnames else ""
            return None, f"Project path '{project_path}' not found. Did you mean '{best.name}'{shortnames}?"

        return None, f"Project path '{project_path}' not found. Available projects: {available_hint}"

    # Should never reach here due to earlier check
    return None, "No project identifier provided."


def create_kb_mcp_server() -> FastMCP:
    """Create and return the FastMCP server for KB tools.
    
    Called once during app startup. Eagerly initializes the StreamableHTTP
    session manager by calling streamable_http_app().
    """
    global _mcp_server, _http_app
    
    mcp = FastMCP(
        name="ensemble-kb",
        instructions=(
            "Knowledge Base tools for the agents-ensemble system. "
            "Use ensemble_kb_explore to search the knowledge base, "
            "and ensemble_kb_experience to record new knowledge."
        ),
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool()
    async def ensemble_kb_explore(
        query: str,
        project_id: str | None = None,
        project_name: str | None = None,
        project_path: str | None = None,
        mode: str = "hybrid",
    ) -> str:
        """Search the agents-ensemble knowledge base.

        Args:
            query: The question or topic to search for.
            project_id: Optional project ID to search within.
            project_name: Optional project name to search within.
            project_path: Optional project path to search within.
            mode: Search mode - "local", "global", "hybrid", or "naive". Defaults to "hybrid".

        Returns:
            Search results from the knowledge base.
        """
        if _manager is None:
            return "Error: KB MCP server not initialized. Please try again later."

        # Resolve project
        resolved_id, error = await _resolve_project(project_id, project_name, project_path)
        if error:
            return f"Error: {error}"
        project_id = resolved_id

        if mode not in _VALID_MODES:
            return f"Error: Invalid mode '{mode}'. Must be one of: {', '.join(_VALID_MODES)}."

        if not is_rag_enabled():
            return "Error: Knowledge base (RAG) is not enabled. Configure RAG to use this tool."

        try:
            # IMPORTANT: Construct message to match internal tool format
            message = f"Query (mode={mode}): {query}\nProject: {project_id}"

            # Spawn explorer agent and wait for result
            result, child_instance_id = await invoke_agent_and_wait(
                manager=_manager,
                agent_id="explorer",
                message=message,
                project_id=project_id,
                parent_id=_MCP_SYSTEM_PARENT_ID,
                instance_name=f"mcp-explore-{project_id[:8]}-{uuid.uuid4().hex[:6]}",
                timeout=300.0,
                return_instance_id=True,
            )

            if result is None:
                return "Explorer agent timed out or failed. Try a simpler query."

            # Deterministic KB-gap detection: did the explorer query RAG
            # successfully AND still have to read project files? Three
            # independent guards combine to mirror the old
            # ``## Need Update KB:`` logic:
            #
            # 1. ``rag_queried`` — explorer must have at least attempted
            #    RAG (skipped-RAG + read_file is not a KB-gap signal).
            # 2. ``not rag_errored`` — if RAG timed out / 504'd / refused
            #    connection, the KB might already contain the information;
            #    we just couldn't reach it. Mirrors the original "RAG
            #    error → no KB update" rule.
            # 3. ``read_file_called`` — the actual KB-gap signal.
            #
            # Pure system check, no agent-emitted heading involved.
            rag_queried = False
            rag_errored = False
            read_file_called = False
            if child_instance_id and hasattr(_manager, "_checkpointer") and _manager._checkpointer:
                rag_queried = await _check_rag_queried_via_checkpoint(
                    _manager._checkpointer, child_instance_id
                )
                rag_errored = await _check_rag_errored_via_checkpoint(
                    _manager._checkpointer, child_instance_id
                )
                read_file_called = await _check_read_file_called_via_checkpoint(
                    _manager._checkpointer, child_instance_id
                )

            if read_file_called and rag_queried and not rag_errored:
                try:
                    # Fire-and-forget: enqueue kb-importer job if explorer found new knowledge
                    asyncio.ensure_future(_enqueue_kb_update_job(
                        manager=_manager,
                        query=query,
                        explorer_response=result,
                        project_id=project_id,
                        source_instance_id=_MCP_SYSTEM_PARENT_ID,
                    ))
                except RuntimeError as e:
                    # Defensive: ensure_future can raise RuntimeError if event loop is closing
                    logger.warning("Failed to schedule kb-importer job (no event loop): %s", e)
                except Exception as e:
                    logger.warning("Failed to schedule kb-importer job: %s", e)

            return result

        except Exception as e:
            logger.error("ensemble_kb_explore failed: %s", e, exc_info=True)
            return "Error: An internal error occurred while exploring the knowledge base."

    @mcp.tool()
    async def ensemble_kb_experience(
        text: str,
        project_id: str | None = None,
        project_name: str | None = None,
        project_path: str | None = None,
    ) -> str:
        """Record new knowledge into the agents-ensemble knowledge base.

        Args:
            text: The knowledge text to record (facts, findings, patterns, etc.).
            project_id: Optional project ID to record knowledge under.
            project_name: Optional project name to record knowledge under.
            project_path: Optional project path to record knowledge under.

        Returns:
            Confirmation message.
        """
        if _manager is None:
            return "Error: KB MCP server not initialized. Please try again later."

        # Resolve project
        resolved_id, error = await _resolve_project(project_id, project_name, project_path)
        if error:
            return f"Error: {error}"
        project_id = resolved_id

        if not is_rag_enabled():
            return "Error: Knowledge base (RAG) is not enabled. Configure RAG to use this tool."

        try:
            # Fire-and-forget: enqueue experiencer job via JobQueueService
            # This does NOT use invoke_agent_and_wait() — avoids semaphore consumption
            asyncio.ensure_future(_enqueue_experience_job(
                manager=_manager,
                text=text,
                project_id=project_id,
                source_instance_id=_MCP_SYSTEM_PARENT_ID,
            ))
            return "Knowledge recording started."

        except RuntimeError as e:
            # Defensive: ensure_future can raise RuntimeError if event loop is closing
            logger.warning("Failed to schedule experiencer job (no event loop): %s", e)
            return "Error: Failed to schedule knowledge recording. Please try again."
        except Exception as e:
            logger.error("ensemble_kb_experience failed: %s", e, exc_info=True)
            return "Error: An internal error occurred while recording knowledge."

    @mcp.tool()
    async def ensemble_kb_list_projects(
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> str:
        """List all projects in the ensemble system.

        Args:
            limit: Maximum number of projects to return (default 50)
            offset: Number of projects to skip (default 0)
            status: Optional status filter (e.g. "active")

        Returns:
            JSON array of projects with id, name, shortnames, main_directory, status, tags
        """
        if _manager is None:
            return "Error: Server not initialized"

        try:
            projects = await asyncio.to_thread(
                _manager._project_repository.list_projects,
                status=status,
                limit=limit,
                offset=offset,
            )

            result = []
            for p in projects:
                result.append({
                    "id": p.project_id,
                    "name": p.name,
                    "shortnames": p.shortnames or [],
                    "main_directory": p.main_directory,
                    "status": p.status,
                    "tags": p.tags or [],
                })

            return json.dumps(result, indent=2)

        except Exception as e:
            logger.error("ensemble_kb_list_projects failed: %s", e, exc_info=True)
            return "Error: Failed to list projects."

    @mcp.tool()
    async def ensemble_kb_search_projects(
        query: str,
        limit: int = 20,
    ) -> str:
        """Search projects by name, description, or shortnames.

        Args:
            query: Search query string
            limit: Maximum number of results (default 20)

        Returns:
            JSON array of matching projects with id, name, shortnames, main_directory, status, tags
        """
        if _manager is None:
            return "Error: Server not initialized"

        try:
            projects = await asyncio.to_thread(
                _manager._project_repository.search,
                query=query,
                limit=limit,
            )

            result = []
            for p in projects:
                result.append({
                    "id": p.project_id,
                    "name": p.name,
                    "shortnames": p.shortnames or [],
                    "main_directory": p.main_directory,
                    "status": p.status,
                    "tags": p.tags or [],
                })

            return json.dumps(result, indent=2)

        except Exception as e:
            logger.error("ensemble_kb_search_projects failed: %s", e, exc_info=True)
            return "Error: Failed to search projects."

    @mcp.tool()
    async def ensemble_context_list(context_key: str, query: str = "") -> str:
        """List .md files in the shared context directory for a context_key.

        Args:
            context_key: The CONTEXT_KEY (tree-root instance id) of the session
                whose context you want to inspect.
            query: Optional case-insensitive filter. When non-empty, only files
                whose filename, slug, concise_preview, or full content contains
                the query are returned. When empty (default), all files are
                returned. Use this to narrow down a long list to the files
                relevant to your task.

        Returns:
            JSON string: list of {filename, slug, size_bytes, modified_at,
            concise_preview}. Returns "[]" if no files exist or the filter
            matches nothing.
        """
        if not context_key or not context_key.strip():
            return "Error: context_key is required."
        try:
            files = await asyncio.to_thread(list_context_files, context_key, query)
            return json.dumps(files, indent=2)
        except Exception as e:
            logger.error("ensemble_context_list failed: %s", e, exc_info=True)
            return "Error: Failed to list context files."

    @mcp.tool()
    async def ensemble_context_read(context_key: str, filename: str) -> str:
        """Read a specific context file.

        Args:
            context_key: The CONTEXT_KEY. Required.
            filename: Bare filename returned by ensemble_context_list.

        Returns:
            File contents or an error string.
        """
        if not context_key or not context_key.strip():
            return "Error: context_key is required."
        if not filename or not filename.strip():
            return "Error: filename is required."
        try:
            content = await asyncio.to_thread(read_context_file, context_key, filename)
        except Exception as e:
            logger.error("ensemble_context_read failed: %s", e, exc_info=True)
            return "Error: Failed to read context file."
        if content is None:
            return (
                f"Error: Could not read '{filename}' from context_key='{context_key}'. "
                "The file may not exist, the filename may be invalid, or it failed a security check."
            )
        return content

    # Eagerly initialize StreamableHTTP session manager
    # (prevents RuntimeError when get_kb_mcp_session_manager() is called later)
    _http_app = mcp.streamable_http_app()

    _mcp_server = mcp
    return mcp


def get_kb_mcp_sse_app(mount_path: str = "/api/mcp/kb/sse"):
    """Get Starlette sub-app for SSE transport."""
    if _mcp_server is None:
        raise RuntimeError("KB MCP server not created. Call create_kb_mcp_server() first.")
    return _mcp_server.sse_app(mount_path)


def get_kb_mcp_http_app():
    """Get Starlette sub-app for StreamableHTTP transport (already created eagerly)."""
    if _http_app is None:
        raise RuntimeError("KB MCP server not created. Call create_kb_mcp_server() first.")
    return _http_app


def get_kb_mcp_session_manager():
    """Get the StreamableHTTP session manager (initialized during create_kb_mcp_server)."""
    if _mcp_server is None:
        raise RuntimeError("KB MCP server not created. Call create_kb_mcp_server() first.")
    return _mcp_server.session_manager

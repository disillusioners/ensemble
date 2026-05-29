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
from daemon.tools.knowledge_tools import (
    _enqueue_experience_job,
    _enqueue_kb_update_job,
    _parse_should_update_kb,
    _SHOULD_UPDATE_KB_PATTERN,
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

    Returns (project_id, error_message) — one will be None.
    """
    if _manager is None:
        return None, "Server not initialized: manager not set"

    repo = _manager._project_repository

    # 1. Try project_id (exact match first, then fuzzy UUID)
    if project_id is not None:
        # Exact match
        project = await asyncio.to_thread(repo.get, project_id)
        if project is not None:
            return project_id, None

        # Fuzzy UUID match
        all_projects = await asyncio.to_thread(repo.list_projects, limit=200)
        best_match = None
        best_ratio = 0.0
        for p in all_projects:
            ratio = difflib.SequenceMatcher(None, project_id.lower(), p.project_id.lower()).ratio()
            if ratio >= 0.6 and ratio > best_ratio:
                best_ratio = ratio
                best_match = p

        if best_ratio >= 0.85:
            return best_match.project_id, None

        # No close UUID match
        if best_ratio >= 0.7:
            return None, f"Project ID '{project_id}' not found. Did you mean '{best_match.name}' ({best_match.project_id})?"

        names = [f"{p.name} ({p.project_id})" for p in all_projects[:10]]
        return None, f"Project ID '{project_id}' not found. Available projects: {', '.join(names)}"

    # 2. Try project_name (exact match on name and shortnames, then fuzzy)
    if project_name is not None:
        # Exact name match
        project = await asyncio.to_thread(repo.get_by_name, project_name)
        if project is not None:
            return project.project_id, None

        # Exact shortname match
        project = await asyncio.to_thread(repo.get_by_shortname, project_name)
        if project is not None:
            return project.project_id, None

        # Fuzzy name match against name + shortnames
        all_projects = await asyncio.to_thread(repo.list_projects, limit=200)
        candidates = []
        for p in all_projects:
            # Compare against name
            name_ratio = difflib.SequenceMatcher(None, project_name.lower(), p.name.lower()).ratio()
            # Compare against each shortname
            sn_ratios = [difflib.SequenceMatcher(None, project_name.lower(), sn.lower()).ratio() for sn in (p.shortnames or [])]
            best_sn_ratio = max(sn_ratios) if sn_ratios else 0.0
            best_for_project = max(name_ratio, best_sn_ratio)
            if best_for_project >= 0.6:
                candidates.append((p, best_for_project))

        candidates.sort(key=lambda x: x[1], reverse=True)

        if candidates and candidates[0][1] >= 0.8:
            return candidates[0][0].project_id, None

        if candidates and candidates[0][1] >= 0.6:
            best = candidates[0][0]
            shortnames = f" ({', '.join(best.shortnames)})" if best.shortnames else ""
            return None, f"Project '{project_name}' not found. Did you mean '{best.name}'{shortnames} ({best.project_id})?"

        names = [f"{p.name}" + (f" ({', '.join(p.shortnames)})" if p.shortnames else "") for p in all_projects[:10]]
        return None, f"Project '{project_name}' not found. Available projects: {', '.join(names)}"

    # 3. Try project_path
    if project_path is not None:
        # Exact path match
        projects = await asyncio.to_thread(repo.get_by_directory, project_path)
        if projects:
            return projects[0].project_id, None

        # Fuzzy path match
        all_projects = await asyncio.to_thread(repo.list_projects, limit=200)
        candidates = []
        for p in all_projects:
            if p.main_directory:
                ratio = difflib.SequenceMatcher(None, project_path.lower(), p.main_directory.lower()).ratio()
                if ratio >= 0.5:
                    candidates.append((p, ratio))

        candidates.sort(key=lambda x: x[1], reverse=True)

        if candidates and candidates[0][1] >= 0.7:
            return candidates[0][0].project_id, None

        if candidates and candidates[0][1] >= 0.5:
            best = candidates[0][0]
            return None, f"Project path '{project_path}' not found. Did you mean '{best.main_directory}' ({best.name}, {best.project_id})?"

        paths = [f"{p.main_directory} ({p.name})" for p in all_projects if p.main_directory][:10]
        return None, f"Project path '{project_path}' not found. Known paths: {', '.join(paths)}"

    # 4. Nothing provided
    all_projects = await asyncio.to_thread(repo.list_projects, limit=20)
    if not all_projects:
        return None, "No project identifier provided and no projects exist in the system."

    names = [f"{p.name}" + (f" ({', '.join(p.shortnames)})" if p.shortnames else "") for p in all_projects[:10]]
    return None, f"No project identifier provided. Please provide project_id, project_name, or project_path. Available projects: {', '.join(names)}"


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
            result = await invoke_agent_and_wait(
                manager=_manager,
                agent_id="explorer",
                message=message,
                project_id=project_id,
                parent_id=_MCP_SYSTEM_PARENT_ID,
                instance_name=f"mcp-explore-{project_id[:8]}-{uuid.uuid4().hex[:6]}",
                timeout=300.0,
            )

            if result is None:
                return "Explorer agent timed out or failed. Try a simpler query."

            # Post-processing — parse KB update flag from explorer response
            should_update_kb = _parse_should_update_kb(result)

            if should_update_kb:
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

            # Strip the "## Need Update KB: ..." heading from the response
            result = _SHOULD_UPDATE_KB_PATTERN.sub("", result).strip()

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
            return f"Error listing projects: {e}"

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
            return f"Error searching projects: {e}"

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

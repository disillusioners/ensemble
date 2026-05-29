"""MCP server exposing Knowledge Base tools for external agent systems.

Provides ensemble_kb_explore and ensemble_kb_experience tools via
SSE and StreamableHTTP transports.
"""

import asyncio
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
        project_id: str,
        mode: str = "hybrid",
    ) -> str:
        """Search the agents-ensemble knowledge base.

        Args:
            query: The question or topic to search for.
            project_id: Required. The project ID to search within.
            mode: Search mode - "local", "global", "hybrid", or "naive". Defaults to "hybrid".

        Returns:
            Search results from the knowledge base.
        """
        if _manager is None:
            return "Error: KB MCP server not initialized. Please try again later."

        if not project_id:
            return "Error: project_id is required. Provide the project ID to search within."

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
            logger.error(f"ensemble_kb_explore failed: {e}", exc_info=True)
            return "Error: An internal error occurred while exploring the knowledge base."

    @mcp.tool()
    async def ensemble_kb_experience(
        text: str,
        project_id: str,
    ) -> str:
        """Record new knowledge into the agents-ensemble knowledge base.

        Args:
            text: The knowledge text to record (facts, findings, patterns, etc.).
            project_id: Required. The project ID to record knowledge under.

        Returns:
            Confirmation message.
        """
        if _manager is None:
            return "Error: KB MCP server not initialized. Please try again later."

        if not project_id:
            return "Error: project_id is required. Provide the project ID to record knowledge under."

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
            logger.error(f"ensemble_kb_experience failed: {e}", exc_info=True)
            return "Error: An internal error occurred while recording knowledge."

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

"""Knowledge management tools for exploring and recording project knowledge."""

import logging
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from ._tool_registry import register_tool_category
from daemon.rag.config import is_rag_enabled
from daemon.utils import invoke_agent_and_wait

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Knowledge"
CATEGORY_DOC = """Knowledge management tools for exploring and recording project knowledge.

explore() queries the project knowledge base using the Explorer agent.
experience() records new knowledge using the Experiencer agent.
"""


def create_knowledge_tools(manager: "InstanceManager", current_instance_id: str) -> list:
    """Create knowledge management tools with injected manager reference.

    Args:
        manager: The InstanceManager instance to use for operations.
        current_instance_id: The ID of the current instance (used as parent for spawned instances).

    Returns:
        List of tool functions: [explore, experience]
    """

    def _get_project_id() -> str | None:
        """Auto-inject project_id from instance context."""
        try:
            instance = manager.get_instance(current_instance_id)
            if instance and instance.instance_metadata:
                return instance.instance_metadata.get("project_id")
        except Exception:
            pass
        return None

    @register_tool_category("knowledge")
    @tool
    async def explore(
        query: str,
        mode: str = "hybrid",
        project_id: str | None = None,
    ) -> str:
        """Explore project knowledge using the Explorer agent.

        Sends a query to the Explorer agent, which searches the RAG knowledge base
        and optionally browses project files to find relevant information.

        Args:
            query: The question or topic to explore.
            mode: Query mode - "local", "global", "hybrid", or "naive". Defaults to "hybrid".
            project_id: Optional project ID. Auto-detected from context if not provided.

        Returns:
            The explorer agent's response with relevant knowledge.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."

        pid = project_id or _get_project_id()

        explorer_message = f"Query (mode={mode}): {query}"
        if pid:
            explorer_message += f"\nProject: {pid}"

        try:
            result = await invoke_agent_and_wait(
                manager=manager,
                agent_id="explorer",
                message=explorer_message,
                project_id=pid,
                parent_id=current_instance_id,
                instance_name=f"explore-{query[:30]}",
                timeout=300.0,
            )
        except Exception as e:
            return f"Explorer agent failed: {e}"

        if result is None:
            return "Explorer agent timed out or failed. Try a simpler query."
        return result

    @register_tool_category("knowledge")
    @tool
    async def experience(
        text: str,
        project_id: str | None = None,
    ) -> str:
        """Record new knowledge using the Experiencer agent.

        Analyzes the text, extracts entities and relationships,
        and inserts them into the RAG knowledge base. Runs in background.

        Args:
            text: The knowledge text to record (facts, findings, patterns, etc.)
            project_id: Optional project ID. Auto-detected from context if not provided.

        Returns:
            Confirmation that knowledge recording has started.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."

        pid = project_id or _get_project_id()

        experiencer_message = f"Process and record the following knowledge:\n\n{text}"
        if pid:
            experiencer_message += f"\nProject: {pid}"

        # Fire-and-forget: spawn instance and enqueue message
        instance_id = None
        try:
            instance_id = manager.spawn_instance(
                agent_id="experiencer",
                parent_id=current_instance_id,
                project_id=pid,
                instance_name=f"experience-{text[:30]}",
                invoked_as_tool=True,
            )
            await manager.enqueue_message(
                instance_id=instance_id,
                message=experiencer_message,
                source=f"experience:{current_instance_id}",
            )
        except Exception as e:
            if instance_id:
                try:
                    manager.terminate_instance(instance_id)
                except Exception:
                    pass
            return f"Error: Failed to start knowledge recording: {e}"

        return f"Knowledge recording started. Instance: {instance_id[:8]}..."

    return [explore, experience]

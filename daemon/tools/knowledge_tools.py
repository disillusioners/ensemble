"""Knowledge management tools for exploring and recording project knowledge."""

import asyncio
import hashlib
import logging
import re
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

# Pattern to match ## Need Update KB: true|false heading (with optional bold/italic)
_SHOULD_UPDATE_KB_PATTERN = re.compile(
    r"^##\s+Need\s+Update\s+KB:\s*\*{0,2}(true|false)\*{0,2}\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def _parse_should_update_kb(response: str) -> bool:
    """Parse the Explorer response for the should_update_kb flag.

    Args:
        response: The Explorer agent's response text.

    Returns:
        True if the response indicates knowledge should be updated, False otherwise.
    """
    # Search for ## Need Update KB: true|false pattern directly
    match = _SHOULD_UPDATE_KB_PATTERN.search(response)
    if match:
        return match.group(1).lower() == "true"
    return False


def _generate_idempotency_key(query: str, project_id: str) -> str:
    """Generate a deterministic idempotency key for experiencer jobs."""
    content = f"explorer-kb-update:{project_id}:{query.lower().strip()}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]


async def _enqueue_experiencer_job(
    manager: "InstanceManager",
    query: str,
    explorer_response: str,
    project_id: str,
    source_instance_id: str,
) -> None:
    """Fire-and-forget: create a job for the experiencer agent to update KB.

    This function is designed to never raise — all errors are logged and swallowed.
    The caller (explore tool) should not be affected by KB update failures.
    """
    try:
        job_service = getattr(manager, "_job_queue_service", None)
        if job_service is None:
            logger.warning("JobQueueService not available, skipping experiencer job")
            return

        # Resolve system_parallel_queue for this project
        queue = await asyncio.to_thread(
            job_service._queue_repo.get_by_name, project_id, "system_parallel_queue"
        )
        if queue is None:
            # Fall back to system_fifo_queue if parallel doesn't exist
            queue = await asyncio.to_thread(
                job_service._queue_repo.get_by_name, project_id, "system_fifo_queue"
            )
            if queue is None:
                logger.warning(
                    "No system queue found for project %s, skipping experiencer job",
                    project_id,
                )
                return
            logger.debug("No parallel queue for %s, using FIFO queue", project_id)

        # Build message for experiencer with full context
        experiencer_message = (
            "Process new knowledge discovered during exploration.\n\n"
            f"Original Query: {query}\n\n"
            f"Explorer Findings:\n{explorer_response}\n\n"
            f"Project: {project_id}"
        )

        # Create the job
        await job_service.enqueue(
            agent_id="experiencer",
            message=experiencer_message,
            source=f"explore:{source_instance_id}",
            project_id=project_id,
            priority=5,
            queue_id=queue.queue_id,
            idempotency_key=_generate_idempotency_key(query, project_id),
            metadata={
                "triggered_by": "explorer",
                "original_query": query,
            },
        )
        logger.debug(
            "Enqueued experiencer job for project %s on queue %s",
            project_id, queue.queue_id,
        )

    except Exception as e:
        # Fire-and-forget: don't fail the explore response if job creation fails
        logger.warning("Failed to enqueue experiencer job: %s", e)


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
            # Use _instance_repository directly - get_instance() returns CompiledStateGraph, not metadata
            instance_meta = manager._instance_repository.get(current_instance_id)
            if instance_meta and instance_meta.instance_metadata:
                return instance_meta.instance_metadata.get("project_id")
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

        # Parse response for should_update_kb flag (use original result, before stripping)
        should_update_kb = _parse_should_update_kb(result)

        # Fire-and-forget: create job for experiencer if knowledge update needed
        # Pass original result so experiencer has full context including the flag heading
        if should_update_kb:
            if not pid:
                logger.warning(
                    "Cannot enqueue experiencer job: project_id not available. "
                    "Ensure the agent instance has a project context set."
                )
            else:
                try:
                    asyncio.ensure_future(_enqueue_experiencer_job(
                        manager=manager,
                        query=query,
                        explorer_response=result,  # Pass original result with heading
                        project_id=pid,
                        source_instance_id=current_instance_id,
                    ))
                except RuntimeError as e:
                    # No running event loop - log warning but don't fail explore
                    logger.warning(
                        "Failed to schedule experiencer job (no event loop): %s", e
                    )
                except Exception as e:
                    logger.warning("Failed to schedule experiencer job: %s", e)

        # Strip the Need Update KB heading from the response before returning to caller
        result = _SHOULD_UPDATE_KB_PATTERN.sub("", result).strip()

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

"""Knowledge management tools for exploring and recording project knowledge."""

import asyncio
import hashlib
import logging
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from ._tool_registry import register_tool_category
from daemon.rag.config import is_rag_enabled
from daemon.services.context_injection import get_shared_context
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

# Pattern to match ## Did you query RAG: yes|no heading (with optional bold/italic)
_RAG_QUERIED_PATTERN = re.compile(
    r"^##\s+Did\s+you\s+query\s+RAG:\s*\*{0,2}(yes|no)\*{0,2}\s*$",
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


def _parse_rag_queried(response: str) -> bool:
    """Parse ## Did you query RAG: yes/no from Explorer response."""
    match = _RAG_QUERIED_PATTERN.search(response)
    if match:
        return match.group(1).lower() == "yes"
    return False


# RAG tool names whose invocation indicates the explorer queried the KB.
# Used for deterministic RAG detection via checkpoint inspection.
RAG_TOOL_NAMES = frozenset({"rag_query_data", "rag_get_graph"})


async def _check_rag_queried_via_checkpoint(
    checkpointer,
    instance_id: str,
) -> bool:
    """Check if RAG tools were actually called by inspecting checkpoint messages.

    Queries the LangGraph checkpointer for the agent's message history
    and looks for rag_query_data or rag_get_graph tool calls. This is the
    deterministic, source-of-truth signal for whether RAG was queried —
    it does not depend on the LLM self-reporting a flag.

    Args:
        checkpointer: AsyncSqliteSaver instance from the manager.
        instance_id: The child agent's instance ID (used as thread_id).

    Returns:
        True if any RAG tool was called, False otherwise (including on any
        error — graceful degradation, never raises).
    """
    try:
        config = {"configurable": {"thread_id": instance_id}}
        state = await checkpointer.aget(config)
        if not state:
            logger.debug("Checkpoint inspection: no state found for %s", instance_id[:8])
            return False

        messages = state.get("channel_values", {}).get("messages", [])
        scanned = 0
        for msg in messages:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                    if name in RAG_TOOL_NAMES:
                        logger.info(
                            "Checkpoint inspection: RAG tool '%s' found (scanned %d messages)",
                            name, scanned + 1,
                        )
                        return True
            scanned += 1

        logger.info("Checkpoint inspection: no RAG tools found (scanned %d messages)", scanned)
        return False
    except Exception:
        logger.debug("Failed to check RAG tool calls from checkpoint", exc_info=True)
        return False


def _generate_idempotency_key(query: str, project_id: str) -> str:
    """Generate a deterministic idempotency key for kb-importer jobs."""
    content = f"explorer-kb-update:{project_id}:{query.lower().strip()}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def _generate_experience_idempotency_key(text: str, project_id: str) -> str:
    """Generate a deterministic idempotency key for experiencer jobs."""
    content = f"experience:{project_id}:{text.lower().strip()}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]


async def _enqueue_kb_update_job(
    manager: "InstanceManager",
    query: str,
    explorer_response: str,
    project_id: str,
    source_instance_id: str,
) -> None:
    """Fire-and-forget: create a job for the kb-importer agent to update KB.

    This function is designed to never raise — all errors are logged and swallowed.
    The caller (explore tool) should not be affected by KB update failures.
    """
    try:
        job_service = getattr(manager, "_job_queue_service", None)
        if job_service is None:
            logger.warning("JobQueueService not available, skipping kb-importer job")
            return

        # Resolve system_kb_fifo_queue for KB import jobs
        queue = await asyncio.to_thread(
            job_service._queue_repo.get_by_name, project_id, "system_kb_fifo_queue"
        )
        if queue is None:
            # Fall back to system_fifo_queue if KB queue doesn't exist
            queue = await asyncio.to_thread(
                job_service._queue_repo.get_by_name, project_id, "system_fifo_queue"
            )
            if queue is None:
                logger.warning(
                    "No system queue found for project %s, skipping kb-importer job",
                    project_id,
                )
                return
            logger.debug("No KB FIFO queue for %s, using system FIFO queue", project_id)

        # Build message for kb-importer with full context
        kb_importer_message = (
            "Process new knowledge discovered during exploration.\n\n"
            f"Original Query: {query}\n\n"
            f"Explorer Findings:\n{explorer_response}\n\n"
            f"Project: {project_id}"
        )

        # Create the job
        await job_service.enqueue(
            agent_id="kb-importer",
            message=kb_importer_message,
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
            "Enqueued kb-importer job for project %s on queue %s",
            project_id, queue.queue_id,
        )

    except Exception as e:
        # Fire-and-forget: don't fail the explore response if job creation fails
        logger.warning("Failed to enqueue kb-importer job: %s", e)


async def _enqueue_experience_job(
    manager: "InstanceManager",
    text: str,
    project_id: str,
    source_instance_id: str,
) -> None:
    """Fire-and-forget: create a job for the experiencer agent to record knowledge.

    This function is designed to never raise — all errors are logged and swallowed.
    The caller (experience tool) should not be affected by job creation failures.
    """
    try:
        job_service = getattr(manager, "_job_queue_service", None)
        if job_service is None:
            logger.warning("JobQueueService not available, skipping experiencer job")
            return

        # Resolve system_kb_fifo_queue for experience jobs
        queue = await asyncio.to_thread(
            job_service._queue_repo.get_by_name, project_id, "system_kb_fifo_queue"
        )
        if queue is None:
            # Fall back to system_fifo_queue if KB queue doesn't exist
            queue = await asyncio.to_thread(
                job_service._queue_repo.get_by_name, project_id, "system_fifo_queue"
            )
            if queue is None:
                logger.warning(
                    "No system queue found for project %s, skipping experiencer job",
                    project_id,
                )
                return
            logger.debug("No KB FIFO queue for %s, using system FIFO queue", project_id)

        # Build message for experiencer
        experiencer_message = (
            "Process and record the following knowledge:\n\n"
            f"{text}\n\n"
            f"Project: {project_id}"
        )

        # Create the job
        await job_service.enqueue(
            agent_id="experiencer",
            message=experiencer_message,
            source=f"experience:{source_instance_id}",
            project_id=project_id,
            priority=5,
            queue_id=queue.queue_id,
            idempotency_key=_generate_experience_idempotency_key(text, project_id),
            metadata={
                "triggered_by": "experience_tool",
                "text_preview": text[:100],
            },
        )
        logger.debug(
            "Enqueued experiencer job for project %s on queue %s",
            project_id, queue.queue_id,
        )

    except Exception as e:
        # Fire-and-forget: don't fail the experience response if job creation fails
        logger.warning("Failed to enqueue experiencer job: %s", e)


def _extract_concise_section(content: str) -> str | None:
    """Extract the ## Concise section from content."""
    match = re.search(r'^##\s*Concise:\s*\n(.*?)(?=\n##\s|\Z)', content, re.MULTILINE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def _is_duplicate_concise(new_concise: str, context_dir: Path, threshold: float = 0.8) -> bool:
    """Check if a similar concise section already exists."""
    try:
        new_tokens = set(new_concise.lower().split())
        if len(new_tokens) < 5:  # too short to compare
            return False
        for md_file in context_dir.glob("*.md"):
            existing = md_file.read_text(encoding="utf-8", errors="replace")
            existing_concise = _extract_concise_section(existing)
            if not existing_concise:
                continue
            existing_tokens = set(existing_concise.lower().split())
            if not existing_tokens:
                continue
            overlap = len(new_tokens & existing_tokens) / min(len(new_tokens), len(existing_tokens))
            if overlap >= threshold:
                return True
    except Exception:
        return False
    return False


def _save_explorer_result(
    query: str,
    result: str,
    context_key: str,
    project_name: str | None = None,
    mode: str = "hybrid",
) -> None:
    """Auto-save explorer result to shared context directory. Fire-and-forget."""
    try:
        now = datetime.now()
        slug = re.sub(r'[^a-z0-9]+', '-', query.lower()).strip('-')[:80]
        if not slug:
            slug = "query"
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        dir_path = Path(tempfile.gettempdir()) / "ensemble" / "context" / context_key
        file_path = dir_path / f"{slug}_{timestamp}.md"

        dir_path.mkdir(parents=True, exist_ok=True)

        iso_ts = now.isoformat()

        content = (
            f"# Explorer Result: {query}\n"
            f"**Time**: {iso_ts}\n"
            f"**Project**: {project_name or 'unknown'}\n"
            f"**Mode**: {mode}\n\n"
            f"{result}"
        )

        # Dedup: skip if a file with very similar ## Concise section exists
        concise = _extract_concise_section(result)
        if concise and _is_duplicate_concise(concise, dir_path):
            logger.debug("Skipping save: concise section too similar to existing file")
            return

        file_path.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.debug("Failed to save explorer result to shared context: %s", e)


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
            if instance_meta and instance_meta.project_id:
                return instance_meta.project_id
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

        # Derive context_key the same way auto-save does, and pass the dir path
        context_key = None
        try:
            context_key = manager._instance_repository.get_tree_root_id(current_instance_id)
            if not context_key:
                context_key = current_instance_id
        except Exception:
            context_key = current_instance_id

        logger.info("[Explorer] Context auto-injection: context_key=%s", context_key)

        if context_key:
            context_dir_path = Path(tempfile.gettempdir()) / "ensemble" / "context" / context_key

            logger.debug("[Explorer] Context dir path: %s", context_dir_path)
            logger.debug("[Explorer] Context dir exists: %s", context_dir_path.exists())

            # Auto-inject relevant context files via reusable service
            # Run on thread pool to avoid blocking the async event loop with sync I/O
            try:
                injection = await asyncio.to_thread(get_shared_context, context_key, query)
                logger.info("[Explorer] get_shared_context returned: %s", type(injection).__name__)
                if injection:
                    explorer_message += f"\n\n{injection}"
                    logger.debug(
                        "Context auto-injection: matched files for query '%s', injection length: %d",
                        query[:50], len(injection),
                    )
                else:
                    logger.info("[Explorer] Context auto-injection: no injection (returned None or empty)")
            except Exception as e:
                logger.info("[Explorer] Context auto-injection failed (exception): %s", e)

        # Invoke explorer agent — always returns (content, child_instance_id) tuple
        # No try/except wrapper: errors propagate to the registry path below
        # so we can still inspect the checkpoint before bailing.
        result, child_instance_id = await invoke_agent_and_wait(
            manager=manager,
            agent_id="explorer",
            message=explorer_message,
            project_id=pid,
            parent_id=current_instance_id,
            instance_name=f"explore-{query[:30]}",
            timeout=300.0,
            return_instance_id=True,
        )

        # Handle error results — but check checkpoint BEFORE returning
        # (child may have called RAG tools before failing)
        is_error = result is None or (isinstance(result, str) and result.startswith("Error:"))
        if result is None:
            result = "Explorer agent timed out or failed. Try a simpler query."

        # Deterministic RAG detection via checkpoint (runs for BOTH success and error paths)
        rag_queried_checkpoint = False
        if child_instance_id and hasattr(manager, "_checkpointer") and manager._checkpointer:
            rag_queried_checkpoint = await _check_rag_queried_via_checkpoint(
                manager._checkpointer, child_instance_id
            )

        # [Phase 1 only] Keep heading-based detection for log comparison
        rag_queried_heading = _parse_rag_queried(result) if isinstance(result, str) else False

        if rag_queried_checkpoint != rag_queried_heading:
            logger.info(
                "RAG detection mismatch: checkpoint=%s, heading=%s, instance=%s",
                rag_queried_checkpoint,
                rag_queried_heading,
                child_instance_id[:8] if child_instance_id else "N/A",
            )

        # Use checkpoint result as source of truth
        rag_queried = rag_queried_checkpoint

        # Return early if error (AFTER checkpoint inspection)
        if is_error:
            return result

        # Parse response for should_update_kb flag (use original result, before stripping)
        should_update_kb = _parse_should_update_kb(result)

        # Fire-and-forget: create job for kb-importer if knowledge update needed
        # Pass original result so kb-importer has full context including the flag heading
        if should_update_kb:
            if not pid:
                logger.warning(
                    "Cannot enqueue kb-importer job: project_id not available. "
                    "Ensure the agent instance has a project context set."
                )
            else:
                try:
                    asyncio.ensure_future(_enqueue_kb_update_job(
                        manager=manager,
                        query=query,
                        explorer_response=result,  # Pass original result with heading
                        project_id=pid,
                        source_instance_id=current_instance_id,
                    ))
                except RuntimeError as e:
                    # No running event loop - log warning but don't fail explore
                    logger.warning(
                        "Failed to schedule kb-importer job (no event loop): %s", e
                    )
                except Exception as e:
                    logger.warning("Failed to schedule kb-importer job: %s", e)

        # Strip the Need Update KB and RAG Queried headings from the response before returning
        result = _SHOULD_UPDATE_KB_PATTERN.sub("", result)
        result = _RAG_QUERIED_PATTERN.sub("", result).strip()

        # Auto-save explorer result to shared context directory (fire-and-forget)
        if rag_queried:
            try:
                root_id = manager._instance_repository.get_tree_root_id(current_instance_id)
                context_key = root_id or current_instance_id or "default"
                project_name = None
                if pid and hasattr(manager, '_project_repository'):
                    try:
                        proj = manager._project_repository.get(pid)
                        project_name = proj.name if proj else None
                    except Exception:
                        pass
                _save_explorer_result(
                    query=query,
                    result=result,
                    context_key=context_key,
                    project_name=project_name,
                    mode=mode,
                )
            except Exception as e:
                logger.debug("Failed to save explorer result to shared context: %s", e)

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

        if not pid:
            return "Error: project_id not available. Ensure the agent instance has a project context set."

        # Fire-and-forget: enqueue job for experiencer agent
        try:
            asyncio.ensure_future(_enqueue_experience_job(
                manager=manager,
                text=text,
                project_id=pid,
                source_instance_id=current_instance_id,
            ))
        except RuntimeError as e:
            # No running event loop - log warning but don't fail experience
            logger.warning("Failed to schedule experiencer job (no event loop): %s", e)
        except Exception as e:
            logger.warning("Failed to schedule experiencer job: %s", e)

        return "Knowledge recording started."

    return [explore, experience]

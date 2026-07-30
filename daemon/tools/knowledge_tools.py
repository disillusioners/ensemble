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
from daemon.persistence import CheckpointerAdapter
from daemon.rag.config import is_rag_enabled
from daemon.services.context_injection import get_shared_context
from daemon.utils import invoke_agent_and_wait

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Knowledge"
CATEGORY_DOC = """Knowledge management tools for exploring and recording project knowledge.

explore() queries the project knowledge base using the Explorer agent.
experience() records new knowledge using the kb-writer agent.
"""

# RAG tool names whose invocation indicates the explorer queried the KB.
# Used for deterministic RAG detection via checkpoint inspection.
RAG_TOOL_NAMES = frozenset({"rag_query_data", "rag_get_graph"})

# File-browse tool name whose invocation indicates the explorer had to look
# beyond RAG — used as the deterministic, system-driven signal that the KB
# lacks the information the caller needs (i.e. "Need Update KB").
KB_GAP_TOOL_NAME = "read_file"


async def _scan_checkpoint_for_tool_match(
    checkpointer,
    instance_id: str,
    matches,
    log_label: str,
) -> bool:
    """Check if a matching tool was called by inspecting checkpoint messages.

    Shared scan logic for deterministic tool-call detection. Used by both
    RAG detection (matches any of ``RAG_TOOL_NAMES``) and KB-gap detection
    (matches the single ``read_file`` tool name).

    Args:
        checkpointer: AsyncSqliteSaver instance from the manager.
        instance_id: The child agent's instance ID (used as thread_id).
        matches: Either a single tool name (str) or a collection of names
            (any container supporting ``in``). The matched name is
            reported in the log line.
        log_label: Short label used in log lines (e.g. "RAG", "read_file").

    Returns:
        True if a matching tool was called, False otherwise (including on
        any error — graceful degradation, never raises).
    """
    try:
        config = {"configurable": {"thread_id": instance_id}}
        # Unwrap CheckpointerAdapter to its raw saver. Production's
        # manager._checkpointer is a CheckpointerAdapter (introduced in
        # commit 8c76247 for PostgreSQL support) which exposes the raw
        # saver via .raw_saver — the adapter itself has no aget. Tests
        # pass plain mocks (or real savers) directly, so fall through.
        # Pattern matches daemon/persistence.py:280.
        if isinstance(checkpointer, CheckpointerAdapter):
            saver = checkpointer.raw_saver
        else:
            saver = checkpointer
        state = await saver.aget(config)
        if not state:
            logger.debug("Checkpoint inspection: no state found for %s", instance_id[:8])
            return False

        messages = state.get("channel_values", {}).get("messages", [])
        scanned = 0
        for msg in messages:
            scanned += 1
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                    if name in matches:
                        logger.info(
                            "Checkpoint inspection: %s tool '%s' found (scanned %d messages)",
                            log_label, name, scanned,
                        )
                        return True

        logger.info(
            "Checkpoint inspection: no %s tool found (scanned %d messages)",
            log_label, scanned,
        )
        return False
    except Exception:
        logger.warning(
            "Failed to check %s tool calls from checkpoint", log_label, exc_info=True
        )
        return False


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
        error — graceful degradation, never raises). Note: this returns True
        whether the RAG call succeeded OR errored — it only confirms RAG was
        attempted. Use :func:`_check_rag_errored_via_checkpoint` to
        distinguish the two.
    """
    return await _scan_checkpoint_for_tool_match(
        checkpointer, instance_id, RAG_TOOL_NAMES, "RAG"
    )


# Substrings in RAG tool responses that indicate an error. The RAG tools
# in ``daemon.tools.rag_tools`` return ``f"RAG error: {e}"`` for any
# exception (timeouts, connection failures, 504s, etc.). A leading
# ``"Error: "`` also indicates a pre-call validation failure.
_RAG_ERROR_INDICATORS = ("RAG error", "Error: ")


def _message_content_to_text(content) -> str:
    """Coerce a ToolMessage's content (str | list | dict) into a searchable string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                # Common shape: {"type": "text", "text": "..."} or plain dict
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    parts.append(text)
                else:
                    parts.append(str(part))
            else:
                parts.append(str(part))
        return " ".join(parts)
    if isinstance(content, dict):
        text = content.get("text") or content.get("content")
        if isinstance(text, str):
            return text
    return str(content)


async def _check_rag_errored_via_checkpoint(
    checkpointer,
    instance_id: str,
) -> bool:
    """Check if any RAG tool call returned an error response.

    Scans the checkpoint for ``ToolMessage`` objects (or test mocks with
    ``name`` + ``content`` attributes) that correspond to RAG tool calls,
    and returns True if any of their content contains a known error
    indicator (``"RAG error"`` or leading ``"Error: "``).

    Used to gate the "Need Update KB" enqueue: if RAG errored, we cannot
    assess whether the KB genuinely lacks the requested information —
    the KB may already contain it; we just couldn't reach it. Skipping
    the enqueue mirrors the original explorer rule "Set
    ``## Need Update KB: false`` when RAG returned an error" without
    depending on the LLM self-reporting a flag.

    Args:
        checkpointer: AsyncSqliteSaver instance from the manager.
        instance_id: The child agent's instance ID (used as thread_id).

    Returns:
        True if any RAG tool returned an error response, False otherwise
        (including on any error during inspection — graceful degradation).
    """
    try:
        config = {"configurable": {"thread_id": instance_id}}
        # Unwrap CheckpointerAdapter (matches the other helpers' pattern).
        if isinstance(checkpointer, CheckpointerAdapter):
            saver = checkpointer.raw_saver
        else:
            saver = checkpointer
        state = await saver.aget(config)
        if not state:
            return False

        messages = state.get("channel_values", {}).get("messages", [])
        for msg in messages:
            # ToolMessage carries ``name`` (the tool that produced it) and
            # ``content`` (the tool's response). Real ToolMessage objects
            # expose both as attributes; mocks do too as long as the test
            # sets them. Skip AI-side messages (which carry ``tool_calls``)
            # and any non-tool messages.
            #
            # ``getattr`` may return a MagicMock for non-real messages
            # (test fixtures); explicitly type-check to avoid surprise
            # matches against the frozenset of str tool names.
            tool_name = getattr(msg, "name", None)
            if not isinstance(tool_name, str) or tool_name not in RAG_TOOL_NAMES:
                continue
            if not hasattr(msg, "content"):
                continue
            text = _message_content_to_text(msg.content)
            for indicator in _RAG_ERROR_INDICATORS:
                if indicator in text:
                    logger.info(
                        "Checkpoint inspection: RAG tool '%s' returned an error: %s",
                        tool_name, text[:200],
                    )
                    return True
        return False
    except Exception:
        logger.warning(
            "Failed to check RAG tool errors from checkpoint", exc_info=True
        )
        return False


async def _check_read_file_called_via_checkpoint(
    checkpointer,
    instance_id: str,
) -> bool:
    """Check if the read_file tool was called by inspecting checkpoint messages.

    Deterministic, source-of-truth signal for the ``Need Update KB`` flag:
    if the explorer had to read a project file, the KB likely did not have
    the answer and a kb-importer job should be enqueued to fill the gap.
    Replaces the previous agent-driven ``## Need Update KB:`` heading parse.

    Args:
        checkpointer: AsyncSqliteSaver instance from the manager.
        instance_id: The child agent's instance ID (used as thread_id).

    Returns:
        True if the read_file tool was called, False otherwise (including on
        any error — graceful degradation, never raises).
    """
    return await _scan_checkpoint_for_tool_match(
        checkpointer, instance_id, KB_GAP_TOOL_NAME, "read_file"
    )


def _generate_idempotency_key(query: str, project_id: str) -> str:
    """Generate a deterministic idempotency key for kb-importer jobs."""
    content = f"explorer-kb-update:{project_id}:{query.lower().strip()}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def _generate_experience_idempotency_key(text: str, project_id: str) -> str:
    """Generate a deterministic idempotency key for kb-writer jobs."""
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
        # agent_tag intentionally omitted: kb-importer is an internal system agent, not versioned
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
        logger.info(
            "Triggered kb-importer job for project %s on queue %s (rag_queried=True, read_file_called=True, rag_errored=False)",
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
    """Fire-and-forget: create a job for the kb-writer agent to record knowledge.

    This function is designed to never raise — all errors are logged and swallowed.
    The caller (experience tool) should not be affected by job creation failures.
    """
    try:
        job_service = getattr(manager, "_job_queue_service", None)
        if job_service is None:
            logger.warning("JobQueueService not available, skipping kb-writer job")
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
                    "No system queue found for project %s, skipping kb-writer job",
                    project_id,
                )
                return
            logger.debug("No KB FIFO queue for %s, using system FIFO queue", project_id)

        # Build message for kb-writer
        kb_writer_message = (
            "Process and record the following knowledge:\n\n"
            f"{text}\n\n"
            f"Project: {project_id}"
        )

        # Create the job
        # agent_tag intentionally omitted: kb-writer is an internal system agent, not versioned
        await job_service.enqueue(
            agent_id="kb-writer",
            message=kb_writer_message,
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
            "Enqueued kb-writer job for project %s on queue %s",
            project_id, queue.queue_id,
        )

    except Exception as e:
        # Fire-and-forget: don't fail the experience response if job creation fails
        logger.warning("Failed to enqueue kb-writer job: %s", e)


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


def _is_duplicate_experience(new_text: str, context_dir: Path, threshold: float = 0.8) -> bool:
    """Check if a similar experience text already exists in the context dir.

    Uses containment-based token overlap: |intersection| / min(|A|, |B|).
    Containment (rather than Jaccard) is used because the stored file
    includes a markdown header (``# Experience Recorded``, ``**Time**:``,
    ``**Project**:``, etc.) that inflates the union denominator without
    contributing to the intersection — under Jaccard, the ratio drops
    below threshold even for near-identical content. Containment
    measures "how much of the new text is already present" which is
    what we actually care about for dedup.

    Skips empty/short texts to avoid false positives on near-empty inputs.
    """
    try:
        new_tokens = set(new_text.lower().split())
        if len(new_tokens) < 5:
            return False
        for md_file in context_dir.glob("*_experience.md"):
            try:
                existing = md_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            existing_tokens = set(existing.lower().split())
            if not existing_tokens:
                continue
            overlap = len(new_tokens & existing_tokens) / min(len(new_tokens), len(existing_tokens))
            if overlap >= threshold:
                return True
    except Exception:
        return False
    return False


def _save_experience_result(
    text: str,
    context_key: str,
    project_name: str | None = None,
) -> None:
    """Auto-save experience text to shared context directory. Fire-and-forget.

    Mirrors the structure of ``_save_explorer_result`` but for the experience
    tool. Skips near-duplicates (containment overlap >= 0.8) against existing
    ``*_experience.md`` files in the same context dir to avoid redundant
    saves when the same knowledge is recorded repeatedly.

    Filename includes a ``%Y%m%d_%H%M%S`` timestamp (matching
    ``_save_explorer_result``) so successive saves never overwrite each
    other and there is no TOCTOU race between filename derivation and
    write.

    Never raises — all errors are logged at DEBUG and swallowed.
    """
    try:
        # Slug: first ~60 chars, slugified. Mirrors _save_explorer_result
        # style but truncates earlier since text bodies tend to be longer
        # than explorer queries.
        slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:60]
        if not slug:
            slug = "experience"
        now = datetime.now()
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        dir_path = Path(tempfile.gettempdir()) / "ensemble" / "context" / context_key
        file_path = dir_path / f"{slug}_{timestamp}_experience.md"

        dir_path.mkdir(parents=True, exist_ok=True)

        iso_ts = now.isoformat()

        # Dedup: skip if a file with very similar content already exists
        if _is_duplicate_experience(text, dir_path):
            logger.debug("Skipping save: experience too similar to existing file")
            return

        content = (
            f"# Experience Recorded\n"
            f"**Time**: {iso_ts}\n"
            f"**Project**: {project_name or 'unknown'}\n\n"
            f"{text}"
        )

        file_path.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.debug("Failed to save experience result to shared context: %s", e)


def create_knowledge_tools(manager: "InstanceManager", current_instance_id: str, agent_id: str = "") -> list:
    """Create knowledge management tools with injected manager reference.

    Args:
        manager: The InstanceManager instance to use for operations.
        current_instance_id: The ID of the current instance (used as parent for spawned instances).
        agent_id: The ``agent_id`` of the calling instance. Captured in the
            closure so the ``explore()`` tool can resolve Explorer's
            ``caller_model_overrides`` and switch the spawned instance's
            model based on who is calling. Defaults to ``""`` for backward
            compatibility — when empty, no override is applied and the
            spawned explorer uses its default ``llm_model``.

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

            # Resolve project metadata once so we can forward project_id,
            # project_name, and the project's critical notes into the context
            # injection. The MCP RAG hint surfaces all three so an external
            # agent can scope tool calls and respect pinned warnings.
            project_name = None
            critical_notes: list[dict] = []
            if pid and hasattr(manager, "_project_repository"):
                try:
                    proj = manager._project_repository.get(pid)
                    if proj is not None:
                        project_name = getattr(proj, "name", None)
                except Exception as e:
                    logger.debug("[Explorer] Failed to resolve project name: %s", e)
                try:
                    notes = manager._project_repository.list_critical_notes(pid)
                    critical_notes = [n.to_dict() for n in notes]
                except Exception as e:
                    logger.debug("[Explorer] Failed to load critical notes: %s", e)

            # Auto-inject relevant context files via reusable service
            # Run on thread pool to avoid blocking the async event loop with sync I/O
            try:
                injection = await asyncio.to_thread(
                    get_shared_context,
                    context_key,
                    query,
                    project_id=pid,
                    project_name=project_name,
                    critical_notes=critical_notes or None,
                )
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

        # Determine if the calling agent needs a model override.
        # Explorer's meta.json may declare ``caller_model_overrides``: a map
        # of caller agent_id -> model name (a string forces that model; ``None``
        # falls back to the system default model from ``config.llm.model``).
        # A missing key means no override — explorer uses its default
        # ``llm_model`` (typically "quick").
        #
        # Registry lookup follows the project's critical-note pattern:
        # ``get_version()`` first (versioned meta), falling back to
        # ``get_resolved()`` (base meta). When the agent_id is missing
        # (legacy callers), we skip the override entirely.
        model_override: str | None = None
        if agent_id:
            try:
                # Use the module-level registry singleton (same pattern as
                # ``daemon.tools.instance._apply_tool_filter``). Lazy
                # import avoids circular dependency on the registry module
                # at tool-factory load time.
                from daemon.registry import get_registry

                registry = get_registry()
                explorer_meta = (
                    registry.get_version("explorer", None)
                    or registry.get_resolved("explorer")
                )
                if explorer_meta is not None:
                    overrides = getattr(explorer_meta, "caller_model_overrides", None) or {}
                    # Use ``in`` rather than ``.get()`` so we can distinguish
                    # "no override configured for this caller" (missing key)
                    # from "explicit override configured" (key present, value
                    # either a string or ``None``). Only the explicit case
                    # yields a model override.
                    if agent_id in overrides:
                        explicit = overrides[agent_id]
                        if explicit is None:
                            # ``null`` in meta.json → "use the system default
                            # model". Resolve the actual default model name
                            # from the manager's config so the downstream
                            # ``spawn_instance`` override layer sees a real
                            # model string (not a sentinel). This is the
                            # difference between "explorer keeps its quick
                            # model" and "explorer upgrades to the system
                            # default". Uses ``getattr`` defensively so tests
                            # with bare MagicMock managers still work.
                            config = getattr(manager, "config", None)
                            llm_cfg = getattr(config, "llm", None) if config is not None else None
                            default_model = getattr(llm_cfg, "model", None) if llm_cfg is not None else None
                            if default_model:
                                model_override = default_model
                            else:
                                # No config available — pass through None,
                                # which downstream treats as "no override, use
                                # explorer default". This is the safer
                                # fallback than guessing a model name.
                                logger.warning(
                                    "caller_model_overrides[%s] is null but config.llm.model is falsy; "
                                    "falling back to default (no override applied)",
                                    agent_id,
                                )
                                model_override = None
                        else:
                            model_override = explicit
            except Exception as e:
                logger.warning(
                    "[Explorer] Failed to resolve caller_model_overrides for %s: %s",
                    agent_id, e,
                )

        # Invoke explorer agent — always returns (content, child_instance_id) tuple
        # No try/except wrapper: errors propagate to the registry path below
        # so we can still inspect the checkpoint before bailing.
        # ``model_override`` is forwarded unconditionally so the
        # ``invoke_agent_and_wait`` boundary sees the value (None / string)
        # directly. The boundary layer treats ``None`` as "no override" →
        # the spawn layer keeps the explorer's default ``llm_model``.
        result, child_instance_id = await invoke_agent_and_wait(
            manager=manager,
            agent_id="explorer",
            message=explorer_message,
            project_id=pid,
            parent_id=current_instance_id,
            instance_name=f"explore-{query[:30]}",
            timeout=300.0,
            return_instance_id=True,
            model=model_override,
        )

        # Handle error results — but check checkpoint BEFORE returning
        # (child may have called RAG tools before failing)
        is_error = result is None or (isinstance(result, str) and result.startswith("Error:"))
        if result is None:
            result = "Explorer agent timed out or failed. Try a simpler query."

        # Deterministic tool-call detection via checkpoint (runs for BOTH success
        # and error paths). rag_queried drives auto-save; read_file_called is
        # the new system-driven "Need Update KB" signal (replaces the agent
        # self-reported ## Need Update KB: heading).
        rag_queried = False
        rag_errored = False
        read_file_called = False
        if child_instance_id and hasattr(manager, "_checkpointer") and manager._checkpointer:
            rag_queried = await _check_rag_queried_via_checkpoint(
                manager._checkpointer, child_instance_id
            )
            rag_errored = await _check_rag_errored_via_checkpoint(
                manager._checkpointer, child_instance_id
            )
            read_file_called = await _check_read_file_called_via_checkpoint(
                manager._checkpointer, child_instance_id
            )

        # Return early if error (AFTER checkpoint inspection)
        if is_error:
            return result

        # Fire-and-forget: create kb-importer job when the explorer queried
        # RAG successfully, RAG did not return an error, AND the explorer
        # still had to read project files. The conjunction encodes three
        # independent guards:
        #
        # 1. ``rag_queried`` prevents enqueuing a spurious update when the
        #    agent skipped RAG entirely and went straight to files — we
        #    have no evidence the KB is the problem.
        # 2. ``not rag_errored`` preserves the original "RAG error → no KB
        #    update" rule. If RAG timed out / 504'd / connection-refused,
        #    the KB might already contain the information; we just
        #    couldn't reach it. Without this guard, a transient RAG outage
        #    plus a routine file fallback would pollute the KB.
        # 3. ``read_file_called`` is the actual KB-gap signal — the agent
        #    had to fall back to filesystem to find something RAG didn't
        #    provide.
        #
        # Heuristic: ``read_file_called`` is a coarse proxy for "the
        # explorer fell back to filesystem because RAG was insufficient."
        # It may over-trigger if a future explorer uses ``read_file`` for
        # non-fallback reasons (confirmation, citation, etc.). A tighter
        # signal would be ``read_file`` after a RAG miss.
        if read_file_called and rag_queried and not rag_errored:
            if not pid:
                logger.warning(
                    "Cannot enqueue kb-importer job: project_id not available. "
                    "Ensure the agent instance has a project context set."
                )
            else:
                logger.info(
                    "KB gap detected — enqueueing kb-importer job "
                    "(rag_queried=True, read_file_called=True, rag_errored=False)"
                )
                try:
                    asyncio.ensure_future(_enqueue_kb_update_job(
                        manager=manager,
                        query=query,
                        explorer_response=result,
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
        else:
            logger.info(
                "Skipping kb-importer job (rag_queried=%s, read_file_called=%s, rag_errored=%s)",
                rag_queried, read_file_called, rag_errored,
            )

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
        """Record new knowledge using the kb-writer agent.

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

        # Derive context_key + project_name for the shared-context file save.
        # Mirrors the explore() pattern at lines ~684-693.
        try:
            root_id = manager._instance_repository.get_tree_root_id(current_instance_id)
            context_key = root_id or current_instance_id or "default"
        except Exception:
            context_key = current_instance_id or "default"
        project_name = None
        if pid and hasattr(manager, "_project_repository"):
            try:
                proj = manager._project_repository.get(pid)
                project_name = proj.name if proj else None
            except Exception:
                pass

        # Fire-and-forget: persist experience text to the shared context
        # directory. Runs in a worker thread to avoid blocking the event
        # loop on sync filesystem I/O — same pattern as explore()'s
        # get_shared_context call at line ~566.
        try:
            asyncio.ensure_future(asyncio.to_thread(
                _save_experience_result,
                text,
                context_key,
                project_name,
            ))
        except RuntimeError as e:
            # No running event loop - log warning but don't fail experience
            logger.debug("Failed to schedule experience save (no event loop): %s", e)
        except Exception as e:
            logger.debug("Failed to schedule experience save: %s", e)

        # Fire-and-forget: enqueue job for kb-writer agent
        try:
            asyncio.ensure_future(_enqueue_experience_job(
                manager=manager,
                text=text,
                project_id=pid,
                source_instance_id=current_instance_id,
            ))
        except RuntimeError as e:
            # No running event loop - log warning but don't fail experience
            logger.warning("Failed to schedule kb-writer job (no event loop): %s", e)
        except Exception as e:
            logger.warning("Failed to schedule kb-writer job: %s", e)

        return "Knowledge recording started."

    return [explore, experience]

"""Title generation service for generating instance titles asynchronously."""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ..graph import ThinkingChatOpenAI, clean_llm_config
from ..utils import parse_think_tags

if TYPE_CHECKING:
    from ..config import Config

_default_logger = logging.getLogger(__name__)


class TitleGenerationService:
    """Service for generating instance titles asynchronously and broadcasting updates.
    
    This runs as a fire-and-forget task to avoid delaying events.
    Errors are logged but not retried - title generation is best-effort.
    """

    def __init__(
        self,
        manager: "InstanceManager",
        logger: Any = None,
    ):
        """Initialize the title generation service.
        
        Args:
            manager: The InstanceManager facade.
            logger: Optional logger to use (defaults to module logger).
                Pass manager's logger to enable test mocking.
        """
        self._manager = manager
        self._logger = logger if logger is not None else _default_logger
        # In-flight dedup: prevents concurrent LLM calls for the same instance.
        # Title generation is triggered fire-and-forget from multiple paths
        # (enqueue + completion), so two coroutines can race. The DB idempotency
        # check below reads metadata BEFORE the LLM call but writes only AFTER,
        # leaving a TOCTOU window. This set provides an in-memory guard that
        # covers the gap. Cleaned up via try/finally on every exit path.
        self._generating_instances: set[str] = set()

    @property
    def _config(self) -> "Config":
        """Access config through manager for test mockability."""
        return self._manager.config

    async def _generate_and_broadcast_title(
        self, instance_id: str, message_content: str
    ) -> None:
        """Generate instance title asynchronously and broadcast the update.

        This runs as a fire-and-forget task to avoid delaying the 'completed' event.
        Errors are logged but not retried - title generation is best-effort.

        Args:
            instance_id: The instance to generate title for
            message_content: The message content to base the title on
        """
        # Skip if empty message
        if not message_content or not message_content.strip():
            return

        # In-flight dedup: prevent concurrent LLM calls for the same instance.
        # Fire-and-forget dispatch means two paths (enqueue + completion) can race.
        if instance_id in self._generating_instances:
            self._logger.debug(f"Title generation already in-flight for instance {instance_id[:8]}..., skipping")
            return
        self._generating_instances.add(instance_id)

        try:
            # Check if title already exists
            # Use manager's repository reference so tests can mock it
            meta = await asyncio.to_thread(self._manager._instance_repository.get, instance_id)
            if meta and meta.instance_metadata and meta.instance_metadata.get("title"):
                # Title already exists, skip
                self._logger.debug(f"Title already exists for instance {instance_id}, skipping generation")
                return

            from langchain_core.messages import HumanMessage, SystemMessage

            # Create LLM client for title generation
            # Use dedicated title model (falls back to main model if not configured)
            # Filter model_vision from config to avoid noisy LangChain warnings.
            # ``base_url_backup`` is threaded through so the config surface
            # stays uniform; it is NOT consumed here (no FailoverController
            # is attached to secondary LLM clients in v1 — ``clean_llm_config``
            # strips it before construction). Failover wiring for secondary
            # sites is future work; the primary retry path lives in
            # ``build_instance_llms`` (daemon/graph.py) and is fully wired.
            llm_config = {
                "base_url": self._config.llm.base_url,
                "base_url_backup": self._config.llm.base_url_backup,
                "api_key": self._config.llm.api_key,
                "model": self._config.llm.model_title,
                "temperature": 0.3,  # Lower temperature for more focused titles
                "default_headers": {"x-proxy-app": "ensemble"},
            }
            # Remove model_vision if present (title generation doesn't need vision)
            llm_config = clean_llm_config(llm_config)

            llm = ThinkingChatOpenAI(**llm_config)

            # The completion safety-net path (daemon/services/child_reports.py:
            # _trigger_title_generation) fires when Path 1 (first message) failed,
            # and at that point the instance's tail is usually dominated by git
            # activity (commit messages, merge output, push logs) — the LLM would
            # otherwise obediently title the instance "Merge branch feature/x"
            # or "Commit changes". The instruction below steers the LLM to
            # extract the underlying user goal from any non-git prose (original
            # request, file names, error messages) when git activity is present.
            title_prompt = f"""Generate a short, descriptive title (3-6 words max) that captures the user's underlying goal or task — not the surface content of the message.

Important: if the message contains raw tool output from version-control commands (commit hashes, merge logs, push output, or diff lines), de-emphasize that output — it is tool noise, not the user's goal. Ignore it as a title subject and focus on the substantive ask from any non-tool prose (original request, file names referenced, error messages, etc.), even when the ask is about git itself. If the entire message is git activity with no other content, produce a short title describing the high-level operation (e.g., "Code Merge" or "Release Prep").

User message:
{message_content[:500]}

Title:"""

            try:
                # One-shot with 30s timeout - title generation is not critical
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        llm.invoke,
                        [SystemMessage(content="You are a helpful assistant that generates concise instance titles."),
                         HumanMessage(content=title_prompt)]
                    ),
                    timeout=30.0
                )
                # Handle both string and list content types
                content = response.content
                if isinstance(content, list):
                    # Extract text from list of content blocks
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict):
                            text_parts.append(block.get("text", ""))
                        else:
                            text_parts.append(str(block))
                    title = " ".join(text_parts).strip()
                else:
                    title = str(content).strip() if content else ""

                # Strip <think> blocks — reasoning models (DeepSeek, GLM, QwQ)
                # may emit their reasoning as <think>...</think> inside the
                # response content. The title should be the visible text only.
                # If the response is thinking-only, the empty-title check
                # below gracefully skips storing a title.
                if title:
                    title, _thinking = parse_think_tags(title)
                    title = title.strip()

                # Validate and truncate title
                if not title:
                    return

                # Truncate to reasonable length (100 chars max)
                if len(title) > 100:
                    title = title[:97] + "..."

                # Store title in instance metadata (use manager's repository so tests can mock it)
                await asyncio.to_thread(self._manager._instance_repository.update_title, instance_id, title)
                self._logger.info(f"Generated title for instance {instance_id}: {title}")
                # Title updates don't need explicit broadcast - frontend can refresh from instance metadata

            except asyncio.TimeoutError:
                self._logger.warning(f"Timeout generating title for instance {instance_id[:8]}...")
            except Exception as e:
                self._logger.warning(f"Failed to generate title for instance {instance_id}: {e}")
        finally:
            # Use discard() not remove(): if somehow missing this is a no-op.
            # Runs on ALL exit paths: success, title-exists early-return,
            # empty-title early-return, timeout, exception.
            self._generating_instances.discard(instance_id)

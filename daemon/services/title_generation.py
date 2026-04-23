"""Title generation service for generating instance titles asynchronously."""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

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
        # Filter model_vision from config to avoid noisy LangChain warnings
        llm_config = {
            "base_url": self._config.llm.base_url,
            "api_key": self._config.llm.api_key,
            "model": self._config.llm.model_title,
            "temperature": 0.3,  # Lower temperature for more focused titles
        }
        # Remove model_vision if present (title generation doesn't need vision)
        llm_config = {k: v for k, v in llm_config.items() if k != "model_vision"}
        
        # Import here to use the same pattern as graph.py
        from ..graph import ThinkingChatOpenAI
        llm = ThinkingChatOpenAI(**llm_config)
        
        title_prompt = f"""Generate a short, descriptive title (3-6 words max) for this user message. The title should summarize what the user is asking about or trying to accomplish.

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

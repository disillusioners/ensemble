"""
Instance mapping and deduplication for message sources.

Provides utilities for mapping external user identities to agent instances,
validating input, and preventing duplicate message processing.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING

from .base import IncomingMessage
from ..registry import get_registry

if TYPE_CHECKING:
    from .manager import InstanceManager
    from ..repositories.source.repository import SQLModelSourceRepository

logger = logging.getLogger(__name__)

# Maximum length for external user IDs
MAX_USER_ID_LENGTH = 256

# Valid source types
SOURCE_TYPE_TELEGRAM = "telegram"
SOURCE_TYPE_WEBHOOK = "webhook"
SOURCE_TYPE_SLACK = "slack"
SOURCE_TYPE_DISCORD = "discord"
VALID_SOURCE_TYPES = {
    SOURCE_TYPE_TELEGRAM,
    SOURCE_TYPE_WEBHOOK,
    SOURCE_TYPE_SLACK,
    SOURCE_TYPE_DISCORD,
}

# Slack composite ID pattern: workspace_id:channel_type:channel_id[:thread_ts]
# workspace_id: starts with T, W, or B followed by alphanumeric
# channel_type: U (user/DM), C (channel), G (private channel)
# channel_id: starts with appropriate prefix
# thread_ts: timestamp with dot (e.g., 1234567890.123456)
SLACK_ID_PATTERN = re.compile(r'^[A-Z0-9]+:[UWC][A-Z0-9]+(:[0-9.]+)?$')

# Discord ID pattern. The single source of truth for canonical external
# user IDs lives in `daemon.sources.adapters.discord.constants`. We
# re-import the literal regex here so the mapper doesn't depend on the
# adapter package (which in turn imports discord.py — a heavy dep that
# we don't want to drag into lightweight mapping validation paths).
# Layout:
#   DM:      dm:{user_id}
#   Channel: {guild_id}:{channel_id}
#   Thread:  {guild_id}:{parent_channel_id}:{thread_id}
# Discord snowflakes are 17-19 digit integers.
DISCORD_ID_PATTERN = (
    r"^(dm:\d{17,19}|\d{17,19}:\d{17,19}(:\d{17,19})?)$"
)


class ValidationError(Exception):
    """Raised when input validation fails."""
    pass


def validate_external_user_id(source_type: str, user_id: str) -> str:
    """Validate and normalize external user ID based on source type.
    
    Args:
        source_type: The type of source ("telegram" or "webhook").
        user_id: The external user ID to validate.
        
    Returns:
        The validated and normalized user ID.
        
    Raises:
        ValidationError: If the user_id is invalid for the given source_type.
    """
    # Check length
    if len(user_id) > MAX_USER_ID_LENGTH:
        raise ValidationError(
            f"User ID exceeds maximum length of {MAX_USER_ID_LENGTH}: {len(user_id)} chars"
        )
    
    if not user_id:
        raise ValidationError("User ID cannot be empty")
    
    # Validate based on source type
    if source_type == SOURCE_TYPE_TELEGRAM:
        # Telegram IDs must be valid integers
        try:
            # Check it's a valid integer (can be negative for groups)
            int(user_id)
            return user_id
        except ValueError:
            raise ValidationError(
                f"Invalid Telegram ID '{user_id}': must be a valid integer"
            )
    
    elif source_type == SOURCE_TYPE_WEBHOOK:
        # Webhook IDs must be alphanumeric (can include hyphens and underscores)
        if not re.match(r'^[a-zA-Z0-9_-]+$', user_id):
            raise ValidationError(
                f"Invalid webhook ID '{user_id}': must be alphanumeric "
                "(can include hyphens and underscores)"
            )
        return user_id

    elif source_type == SOURCE_TYPE_SLACK:
        # Slack composite ID: {workspace}:{channel_type}{id}[:{thread_ts}]
        # Examples:
        # - DM: T123456:U123456
        # - Channel: T123456:C123456
        # - Thread: T123456:C123456:1234567890.123456
        if not SLACK_ID_PATTERN.match(user_id):
            raise ValidationError(
                f"Invalid Slack ID '{user_id}': expected format "
                "{workspace}:{channel_type}{id}[:{thread_ts}] "
                "(e.g., T123456:U123456 or T123456:C123456:1234567890.123456)"
            )
        return user_id

    elif source_type == SOURCE_TYPE_DISCORD:
        # Discord external_user_id formats:
        #   DM:      dm:{user_id}
        #   Channel: {guild_id}:{channel_id}
        #   Thread:  {guild_id}:{parent_channel_id}:{thread_id}
        if not re.match(DISCORD_ID_PATTERN, user_id):
            raise ValidationError(
                f"Invalid Discord ID '{user_id}': expected format "
                "dm:{user_id} or {guild}:{channel}[:{thread}] "
                "(snowflakes are 17-19 digit integers)"
            )
        return user_id

    else:
        # Unknown source type - log warning and allow it (forward compatibility)
        logger.warning(f"Unknown source_type '{source_type}', allowing any user_id")
        return user_id


class InstanceMapper:
    """Maps external user identities to agent instances.

    This class handles:
    - Looking up existing instance mappings
    - Creating new instances when needed
    - Deduplicating incoming messages
    - Tracking last message activity
    - Detecting and recovering from stale mappings whose target instance
      was removed out-of-band (DB reset, manual cleanup, restore, etc.)
    """
    
    def __init__(self, source_repo: "SQLModelSourceRepository", manager: "InstanceManager"):
        """Initialize the instance mapper.
        
        Args:
            source_repo: SQLModelSourceRepository instance for database operations.
            manager: InstanceManager instance for spawning new instances.
        """
        self.source_repo = source_repo
        self.manager = manager
    
    def _mapping_instance_exists(self, mapping: dict) -> bool:
        """Check whether the instance referenced by a mapping still exists.

        A mapping can outlive its target instance if the instance row was
        removed out-of-band (DB restore, manual cleanup, schema reset, etc.).
        Returning a dead instance_id causes the downstream worker to fail with
        ``Instance not found`` and the user message is lost.

        Fast path: in-memory ``manager.instances`` dict (covers the common
        warm-process case with zero DB cost).
        Slow path: ``instance_repository.get`` DB lookup (cold start, restart,
        or instance only persisted to DB).

        Args:
            mapping: Mapping dict from :meth:`get_mapping`.

        Returns:
            True if the instance is live (in memory) or present in the DB.
        """
        instance_id = mapping.get("agent_instance_id")
        if not instance_id:
            return False

        # Fast path: instance is loaded in this process.
        in_memory = getattr(self.manager, "instances", None)
        if isinstance(in_memory, dict) and instance_id in in_memory:
            return True

        # Slow path: check the instance repository.
        instance_repo = getattr(self.manager, "_instance_repository", None)
        if instance_repo is None:
            # No way to verify — assume it exists to preserve prior behavior.
            return True

        try:
            return instance_repo.get(instance_id) is not None
        except Exception as e:
            # If the verification query itself fails, log and fall back to
            # trusting the mapping (matches the legacy behavior so a transient
            # DB hiccup doesn't accidentally cascade into a forced re-spawn).
            logger.warning(
                "Failed to verify instance %s for mapping %s: %s",
                instance_id[:8] if instance_id else "<empty>",
                mapping.get("mapping_id", "<unknown>"),
                e,
            )
            return True

    def get_mapping(
        self,
        source_id: str,
        external_user_id: str
    ) -> dict | None:
        """Get instance mapping for a source and external user.
        
        Args:
            source_id: The source identifier.
            external_user_id: The external user ID.
            
        Returns:
            Mapping dictionary if exists, None otherwise.
        """
        mapping = self.source_repo.get_instance_mapping(source_id, external_user_id)
        if mapping:
            return {
                "mapping_id": mapping.mapping_id,
                "source_id": mapping.source_id,
                "external_user_id": mapping.external_user_id,
                "agent_instance_id": mapping.agent_instance_id,
                "agent_id": mapping.agent_id,
                "agent_dir": mapping.agent_dir,
                "metadata": mapping.mapping_metadata,
                "last_message_at": mapping.last_message_at,
                "created_at": mapping.created_at,
            }
        return None
    
    def update_last_message(
        self, 
        source_id: str, 
        external_user_id: str
    ) -> None:
        """Update the last_message_at timestamp for a mapping.
        
        Args:
            source_id: The source identifier.
            external_user_id: The external user ID.
        """
        self.source_repo.update_mapping_last_message(source_id, external_user_id)
    
    def is_duplicate(
        self, 
        source_id: str, 
        external_message_id: str
    ) -> bool:
        """Check if a message has already been processed.
        
        This is an atomic operation that marks the message as processed
        if it's new, or returns True if it already exists.
        
        Args:
            source_id: The source identifier.
            external_message_id: The external message ID to check.
            
        Returns:
            True if duplicate (already processed), False if new.
        """
        return self.source_repo.check_and_mark_processed(
            source_id,
            external_message_id
        )
    
    async def get_or_create_instance(
        self,
        source_id: str,
        external_user_id: str,
        agent_id: str,
        force_new: bool = False,
        extra_mapping_metadata: dict | None = None,
        source_type: str | None = None,
    ) -> str:
        """Get existing instance or create a new one.

        Looks up the mapping for the source and external user. If found,
        returns the existing agent_instance_id. If not found, spawns a new
        instance and creates the mapping.

        Args:
            source_id: The source identifier.
            external_user_id: The external user ID.
            agent_id: The agent identifier.
            force_new: If True, delete any existing mapping and create a fresh instance.
            extra_mapping_metadata: Additional metadata to store with the mapping.
            source_type: Optional chat platform type (e.g. "discord", "slack",
                "telegram"). Threaded into the spawned instance's metadata so
                the platform-context appender can inject formatting rules into
                the root instance's system prompt.

        Returns:
            The agent_instance_id (UUID string).

        Raises:
            Exception: If instance creation fails.
        """
        # Resolve agent_id to canonical form
        registry = get_registry()
        resolved_id = registry.resolve_to_id(agent_id)
        effective_agent_id = resolved_id if resolved_id else agent_id
        
        # Get agent_dir from registry
        agent_meta = registry.get(effective_agent_id)
        if agent_meta is None:
            raise ValueError(f"Agent not found: {effective_agent_id}")
        effective_agent_dir = str(agent_meta.path)
        
        # Check for existing mapping
        mapping = self.get_mapping(source_id, external_user_id)
        
        if mapping is not None:
            if force_new:
                # Delete existing mapping to force new instance creation
                logger.info(
                    f"force_new=True: Deleting existing mapping: source_id={source_id}, "
                    f"external_user_id={external_user_id}, "
                    f"old_agent_instance_id={mapping['agent_instance_id']}"
                )
                self.source_repo.delete_instance_mapping(mapping["mapping_id"])
            else:
                # Verify the mapped instance still exists. Mappings can outlive
                # their target instance (DB reset, manual cleanup, out-of-band
                # deletion). Returning a dead id would make the worker fail
                # with ``Instance not found`` and drop the user message.
                if not self._mapping_instance_exists(mapping):
                    logger.warning(
                        f"Stale instance mapping detected: source_id={source_id}, "
                        f"external_user_id={external_user_id}, "
                        f"dead_agent_instance_id={mapping['agent_instance_id']}. "
                        f"Deleting orphan mapping and creating a fresh instance."
                    )
                    self.source_repo.delete_instance_mapping(mapping["mapping_id"])
                    # Fall through to create-new path below.
                else:
                    logger.debug(
                        f"Found existing instance: source_id={source_id}, "
                        f"external_user_id={external_user_id}, "
                        f"agent_instance_id={mapping['agent_instance_id']}"
                    )
                    return mapping["agent_instance_id"]
        
        # No valid mapping exists - create new instance
        logger.info(
            f"Creating new instance: source_id={source_id}, "
            f"external_user_id={external_user_id}, agent_id={effective_agent_id}"
        )
        
        try:
            # Spawn new instance via InstanceManager
            instance_id = str(uuid.uuid4())
            agent_instance_id = await self.manager.spawn_instance_with_mcp(
                instance_id=instance_id,
                agent_id=effective_agent_id,
                source_type=source_type,
            )
            
            # Create mapping
            mapping_id = str(uuid.uuid4())
            metadata = {
                "source_id": source_id,
                "external_user_id": external_user_id,
            }
            
            # Merge extra mapping metadata if provided (e.g., for Slack channel_id, thread_ts)
            if extra_mapping_metadata:
                metadata.update(extra_mapping_metadata)
            
            self.source_repo.create_instance_mapping(
                source_id=source_id,
                external_user_id=external_user_id,
                agent_instance_id=agent_instance_id,
                agent_id=effective_agent_id,
                agent_dir=effective_agent_dir,
                metadata=metadata,
                mapping_id=mapping_id,
            )
            
            logger.info(
                f"Created instance mapping: mapping_id={mapping_id}, "
                f"agent_instance_id={agent_instance_id}"
            )
            
            return agent_instance_id
            
        except Exception as e:
            logger.error(
                f"Failed to create instance: source_id={source_id}, "
                f"external_user_id={external_user_id}, error={e}"
            )
            raise
    
    async def handle_incoming_message(
        self,
        msg: IncomingMessage,
        default_agent_id: str,
    ) -> tuple[str, str]:
        """Process an incoming message: validate, check duplicate, get/create instance.
        
        Args:
            msg: The incoming message to handle.
            default_agent_id: Default agent identifier for new instances.
            
        Returns:
            Tuple of (agent_instance_id, source_id).
            
        Raises:
            ValidationError: If message validation fails.
            ValueError: If message is a duplicate.
        """
        # Get source type from metadata or derive from source_id
        source_type = msg.metadata.get("source_type", "webhook") if msg.metadata else "webhook"
        
        # Validate external user ID
        try:
            validated_user_id = validate_external_user_id(source_type, msg.external_user_id)
        except ValidationError as e:
            logger.warning(f"Invalid user ID: {e}")
            raise
        
        # Check for duplicate message (using metadata.message_id if available)
        external_message_id = msg.metadata.get("message_id") if msg.metadata else None
        
        if external_message_id:
            if self.is_duplicate(msg.source_id, external_message_id):
                logger.debug(
                    f"Duplicate message ignored: source_id={msg.source_id}, "
                    f"external_message_id={external_message_id}"
                )
                raise ValueError(
                    f"Duplicate message: {external_message_id}"
                )
        
        # Determine agent identifier (use metadata if specified, otherwise default)
        agent_id = msg.metadata.get("agent_id") if msg.metadata else None
        
        if agent_id is None:
            agent_id = default_agent_id
        
        # Get or create instance
        try:
            agent_instance_id = await self.get_or_create_instance(
                source_id=msg.source_id,
                external_user_id=validated_user_id,
                agent_id=agent_id
            )
        except Exception as e:
            logger.error(f"Failed to get/create instance: {e}")
            raise
        
        # Update last message timestamp
        self.update_last_message(msg.source_id, validated_user_id)
        
        logger.debug(
            f"Handled incoming message: source_id={msg.source_id}, "
            f"external_user_id={validated_user_id}, "
            f"agent_instance_id={agent_instance_id}"
        )
        
        return agent_instance_id, msg.source_id

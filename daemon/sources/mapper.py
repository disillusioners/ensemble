"""
Session mapping and deduplication for message sources.

Provides utilities for mapping external user identities to agent sessions,
validating input, and preventing duplicate message processing.
"""

import logging
import re
import uuid
from typing import TYPE_CHECKING

from .base import IncomingMessage

if TYPE_CHECKING:
    from .manager import SessionManager
    from ..repositories.source.repository import SQLModelSourceRepository

logger = logging.getLogger(__name__)

# Maximum length for external user IDs
MAX_USER_ID_LENGTH = 256

# Valid source types
SOURCE_TYPE_TELEGRAM = "telegram"
SOURCE_TYPE_WEBHOOK = "webhook"
VALID_SOURCE_TYPES = {SOURCE_TYPE_TELEGRAM, SOURCE_TYPE_WEBHOOK}


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
    
    else:
        # Unknown source type - log warning and allow it (forward compatibility)
        logger.warning(f"Unknown source_type '{source_type}', allowing any user_id")
        return user_id


class SessionMapper:
    """Maps external user identities to agent sessions.
    
    This class handles:
    - Looking up existing session mappings
    - Creating new sessions when needed
    - Deduplicating incoming messages
    - Tracking last message activity
    """
    
    def __init__(self, source_repo: "SQLModelSourceRepository", manager: "SessionManager"):
        """Initialize the session mapper.
        
        Args:
            source_repo: SQLModelSourceRepository instance for database operations.
            manager: SessionManager instance for spawning new sessions.
        """
        self.source_repo = source_repo
        self.manager = manager
    
    def get_mapping(
        self, 
        source_id: str, 
        external_user_id: str
    ) -> dict | None:
        """Get session mapping for a source and external user.
        
        Args:
            source_id: The source identifier.
            external_user_id: The external user ID.
            
        Returns:
            Mapping dictionary if exists, None otherwise.
        """
        mapping = self.source_repo.get_session_mapping(source_id, external_user_id)
        if mapping:
            return {
                "mapping_id": mapping.mapping_id,
                "source_id": mapping.source_id,
                "external_user_id": mapping.external_user_id,
                "agent_session_id": mapping.agent_session_id,
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
    
    async def get_or_create_session(
        self,
        source_id: str,
        external_user_id: str,
        agent_dir: str,
        force_new: bool = False,
    ) -> str:
        """Get existing session or create a new one.
        
        Looks up the mapping for the source and external user. If found,
        returns the existing agent_session_id. If not found, spawns a new
        session and creates the mapping.
        
        Args:
            source_id: The source identifier.
            external_user_id: The external user ID.
            agent_dir: The agent directory path for new sessions.
            force_new: If True, delete any existing mapping and create a fresh session.
            
        Returns:
            The agent_session_id (UUID string).
            
        Raises:
            Exception: If session creation fails.
        """
        # Check for existing mapping
        mapping = self.get_mapping(source_id, external_user_id)
        
        if mapping is not None:
            if force_new:
                # Delete existing mapping to force new session creation
                logger.info(
                    f"force_new=True: Deleting existing mapping: source_id={source_id}, "
                    f"external_user_id={external_user_id}, "
                    f"old_agent_session_id={mapping['agent_session_id']}"
                )
                self.source_repo.delete_session_mapping(mapping["mapping_id"])
            else:
                logger.debug(
                    f"Found existing session: source_id={source_id}, "
                    f"external_user_id={external_user_id}, "
                    f"agent_session_id={mapping['agent_session_id']}"
                )
                return mapping["agent_session_id"]
        
        # No mapping exists - create new session
        logger.info(
            f"Creating new session: source_id={source_id}, "
            f"external_user_id={external_user_id}, agent_dir={agent_dir}"
        )
        
        try:
            # Spawn new session via SessionManager
            agent_session_id = self.manager.spawn_session(agent_dir)
            
            # Create mapping
            mapping_id = str(uuid.uuid4())
            metadata = {
                "source_id": source_id,
                "external_user_id": external_user_id,
            }
            
            self.source_repo.create_session_mapping(
                source_id=source_id,
                external_user_id=external_user_id,
                agent_session_id=agent_session_id,
                agent_dir=agent_dir,
                metadata=metadata,
                mapping_id=mapping_id,
            )
            
            logger.info(
                f"Created session mapping: mapping_id={mapping_id}, "
                f"agent_session_id={agent_session_id}"
            )
            
            return agent_session_id
            
        except Exception as e:
            logger.error(
                f"Failed to create session: source_id={source_id}, "
                f"external_user_id={external_user_id}, error={e}"
            )
            raise
    
    async def handle_incoming_message(
        self,
        msg: IncomingMessage,
        default_agent_dir: str
    ) -> tuple[str, str]:
        """Process an incoming message: validate, check duplicate, get/create session.
        
        Args:
            msg: The incoming message to handle.
            default_agent_dir: Default agent directory for new sessions.
            
        Returns:
            Tuple of (agent_session_id, source_id).
            
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
        
        # Determine agent directory (use metadata if specified, otherwise default)
        agent_dir = msg.metadata.get("agent_dir") if msg.metadata else None
        if not agent_dir:
            agent_dir = default_agent_dir
        
        # Get or create session
        try:
            agent_session_id = await self.get_or_create_session(
                source_id=msg.source_id,
                external_user_id=validated_user_id,
                agent_dir=agent_dir
            )
        except Exception as e:
            logger.error(f"Failed to get/create session: {e}")
            raise
        
        # Update last message timestamp
        self.update_last_message(msg.source_id, validated_user_id)
        
        logger.debug(
            f"Handled incoming message: source_id={msg.source_id}, "
            f"external_user_id={validated_user_id}, "
            f"agent_session_id={agent_session_id}"
        )
        
        return agent_session_id, msg.source_id

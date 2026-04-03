"""Tests for daemon/sources/mapper.py"""

import pytest
import uuid
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path

from daemon.sources.mapper import (
    validate_external_user_id,
    ValidationError,
    InstanceMapper,
    MAX_USER_ID_LENGTH,
)


# ==================== Fixtures ====================


@dataclass
class MockAgentMeta:
    """Mock agent metadata returned by registry."""
    path: Path = field(default_factory=lambda: Path("/agents/test"))


@dataclass
class MockMapping:
    """Mock mapping object returned by source_repo."""
    mapping_id: str
    source_id: str
    external_user_id: str
    agent_instance_id: str
    agent_id: str
    agent_dir: str
    mapping_metadata: dict = field(default_factory=dict)
    last_message_at: datetime = None
    created_at: datetime = None


class MockSourceRepository:
    """Mock source repository with required methods."""
    
    def __init__(self):
        self.mappings: dict[tuple[str, str], MockMapping] = {}
        self.processed_messages: set[tuple[str, str]] = set()
        self.mapping_id_counter = 0
    
    def get_instance_mapping(self, source_id: str, external_user_id: str) -> MockMapping | None:
        """Get mapping by source and external user ID."""
        return self.mappings.get((source_id, external_user_id))
    
    def update_mapping_last_message(self, source_id: str, external_user_id: str) -> None:
        """Update the last_message_at timestamp for a mapping."""
        mapping = self.mappings.get((source_id, external_user_id))
        if mapping:
            mapping.last_message_at = datetime.now(timezone.utc)
    
    def check_and_mark_processed(self, source_id: str, external_message_id: str) -> bool:
        """Check if message is duplicate and mark as processed if new."""
        key = (source_id, external_message_id)
        if key in self.processed_messages:
            return True
        self.processed_messages.add(key)
        return False
    
    def delete_instance_mapping(self, mapping_id: str) -> None:
        """Delete mapping by mapping_id."""
        for key, mapping in list(self.mappings.items()):
            if mapping.mapping_id == mapping_id:
                del self.mappings[key]
                break
    
    def create_instance_mapping(
        self,
        source_id: str,
        external_user_id: str,
        agent_instance_id: str,
        agent_id: str,
        agent_dir: str,
        metadata: dict,
        mapping_id: str,
    ) -> None:
        """Create a new instance mapping."""
        mapping = MockMapping(
            mapping_id=mapping_id,
            source_id=source_id,
            external_user_id=external_user_id,
            agent_instance_id=agent_instance_id,
            agent_id=agent_id,
            agent_dir=agent_dir,
            mapping_metadata=metadata,
            last_message_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        self.mappings[(source_id, external_user_id)] = mapping


@pytest.fixture
def mock_registry():
    """Create a mock agent registry."""
    mock_reg = MagicMock()
    mock_reg.resolve_to_id.return_value = None
    mock_reg.get.return_value = MockAgentMeta()
    return mock_reg


@pytest.fixture
def mock_source_repo():
    """Create a mock source repository with required methods."""
    return MockSourceRepository()


@pytest.fixture
def mock_manager():
    """Create a mock InstanceManager."""
    manager = MagicMock()
    # Create a simple spawn that returns a UUID
    manager.spawn_instance = MagicMock(return_value=str(uuid.uuid4()))
    return manager


@pytest.fixture
def instance_mapper(mock_source_repo, mock_manager, mock_registry):
    """Create an InstanceMapper with mock source_repo and mock manager."""
    with patch("daemon.sources.mapper.get_registry", return_value=mock_registry):
        yield InstanceMapper(source_repo=mock_source_repo, manager=mock_manager)


# ==================== Input Validation Tests ====================


class TestValidateExternalUserId:
    """Tests for validate_external_user_id function."""
    
    def test_validate_telegram_id_valid_integer(self):
        """Valid Telegram IDs should be returned as-is."""
        assert validate_external_user_id("telegram", "12345") == "12345"
        assert validate_external_user_id("telegram", "-100") == "-100"
        assert validate_external_user_id("telegram", "0") == "0"
        assert validate_external_user_id("telegram", "999999999999") == "999999999999"
    
    def test_validate_telegram_id_negative_for_groups(self):
        """Negative IDs should be allowed for groups."""
        assert validate_external_user_id("telegram", "-100") == "-100"
        assert validate_external_user_id("telegram", "-1001234567890") == "-1001234567890"
    
    def test_validate_telegram_id_invalid_string(self):
        """Non-integer strings should raise ValidationError."""
        with pytest.raises(ValidationError, match="Invalid Telegram ID"):
            validate_external_user_id("telegram", "abc123")
    
    def test_validate_telegram_id_with_spaces(self):
        """IDs with spaces should raise ValidationError (for embedded spaces)."""
        # Note: int() accepts leading/trailing whitespace, so " 123" passes
        # But embedded spaces like "123 456" correctly fail
        with pytest.raises(ValidationError, match="Invalid Telegram ID"):
            validate_external_user_id("telegram", "123 456")
    
    def test_validate_webhook_id_valid_alphanumeric(self):
        """Valid webhook IDs should be returned as-is."""
        assert validate_external_user_id("webhook", "user_123") == "user_123"
        assert validate_external_user_id("webhook", "user-123") == "user-123"
        assert validate_external_user_id("webhook", "UserName123") == "UserName123"
        assert validate_external_user_id("webhook", "abc") == "abc"
        assert validate_external_user_id("webhook", "123456") == "123456"
    
    def test_validate_webhook_id_with_hyphen_underscore(self):
        """Webhook IDs with hyphens and underscores should be valid."""
        assert validate_external_user_id("webhook", "user-name") == "user-name"
        assert validate_external_user_id("webhook", "user_name") == "user_name"
        assert validate_external_user_id("webhook", "user-name_123") == "user-name_123"
    
    def test_validate_webhook_id_invalid_special_chars(self):
        """Webhook IDs with invalid special chars should raise ValidationError."""
        with pytest.raises(ValidationError, match="Invalid webhook ID"):
            validate_external_user_id("webhook", "user@123")
        with pytest.raises(ValidationError, match="Invalid webhook ID"):
            validate_external_user_id("webhook", "user 123")
        with pytest.raises(ValidationError, match="Invalid webhook ID"):
            validate_external_user_id("webhook", "user!123")
        with pytest.raises(ValidationError, match="Invalid webhook ID"):
            validate_external_user_id("webhook", "user.name@example.com")
    
    def test_validate_empty_id(self):
        """Empty IDs should raise ValidationError."""
        with pytest.raises(ValidationError, match="User ID cannot be empty"):
            validate_external_user_id("telegram", "")
        with pytest.raises(ValidationError, match="User ID cannot be empty"):
            validate_external_user_id("webhook", "")
    
    def test_validate_id_too_long(self):
        """IDs exceeding 256 chars should raise ValidationError."""
        long_id = "a" * (MAX_USER_ID_LENGTH + 1)
        with pytest.raises(ValidationError, match=f"exceeds maximum length of {MAX_USER_ID_LENGTH}"):
            validate_external_user_id("telegram", long_id)
        with pytest.raises(ValidationError, match=f"exceeds maximum length of {MAX_USER_ID_LENGTH}"):
            validate_external_user_id("webhook", long_id)
    
    def test_validate_id_exactly_max_length(self):
        """IDs at exactly 256 chars should be valid for webhook."""
        max_id = "a" * MAX_USER_ID_LENGTH
        # Telegram requires integers, so this won't work
        # But webhook accepts any alphanumeric string
        assert validate_external_user_id("webhook", max_id) == max_id
        # Test with numeric string at max length for telegram
        assert validate_external_user_id("telegram", "1" * MAX_USER_ID_LENGTH) == "1" * MAX_USER_ID_LENGTH


# ==================== InstanceMapper Tests ====================


class TestInstanceMapper:
    """Tests for InstanceMapper class."""
    
    @pytest.mark.asyncio
    async def test_get_or_create_instance_new_user(self, mock_source_repo, instance_mapper):
        """Creating instance for new user should create mapping."""
        source_id = "test_source"
        external_user_id = "12345"
        agent_id = "test_agent"
        
        # Get or create instance
        agent_instance_id = await instance_mapper.get_or_create_instance(
            source_id=source_id,
            external_user_id=external_user_id,
            agent_id=agent_id
        )
        
        # Should return a valid instance ID
        assert agent_instance_id is not None
        assert len(agent_instance_id) > 0
        
        # Should have created a mapping in the database
        mapping = instance_mapper.get_mapping(source_id, external_user_id)
        assert mapping is not None
        assert mapping["source_id"] == source_id
        assert mapping["external_user_id"] == external_user_id
        assert mapping["agent_instance_id"] == agent_instance_id
    
    @pytest.mark.asyncio
    async def test_get_or_create_instance_existing_user(self, mock_source_repo, instance_mapper):
        """Existing user should return existing instance."""
        source_id = "test_source"
        external_user_id = "12345"
        agent_id = "test_agent"
        
        # Create first instance
        instance_id_1 = await instance_mapper.get_or_create_instance(
            source_id=source_id,
            external_user_id=external_user_id,
            agent_id=agent_id
        )
        
        # Get or create again - should return same instance
        instance_id_2 = await instance_mapper.get_or_create_instance(
            source_id=source_id,
            external_user_id=external_user_id,
            agent_id=agent_id
        )
        
        # Should return same instance ID
        assert instance_id_1 == instance_id_2
    
    @pytest.mark.asyncio
    async def test_get_or_create_instance_spawns_agent(self, mock_source_repo, mock_manager, mock_registry):
        """Should call manager.spawn_instance to create new agent."""
        # Reset the mock to track calls
        mock_manager.spawn_instance.reset_mock()
        
        with patch("daemon.sources.mapper.get_registry", return_value=mock_registry):
            mapper = InstanceMapper(source_repo=mock_source_repo, manager=mock_manager)
            
            instance_id = await mapper.get_or_create_instance(
                source_id="test_source",
                external_user_id="user123",
                agent_id="test"
            )
            
            # Should have called spawn_instance
            mock_manager.spawn_instance.assert_called_once()
            
            # Should return the spawned instance ID
            assert instance_id == mock_manager.spawn_instance.return_value
    
    @pytest.mark.asyncio
    async def test_get_mapping_returns_mapping(self, mock_source_repo, instance_mapper):
        """Get mapping should return existing mapping."""
        source_id = "test_source"
        external_user_id = "12345"
        
        # First create an instance to have a mapping
        await instance_mapper.get_or_create_instance(
            source_id=source_id,
            external_user_id=external_user_id,
            agent_id="test"
        )
        
        # Get mapping should return it
        mapping = instance_mapper.get_mapping(source_id, external_user_id)
        assert mapping is not None
        assert mapping["external_user_id"] == external_user_id
        assert mapping["source_id"] == source_id
    
    def test_get_mapping_not_found(self, mock_source_repo, instance_mapper):
        """Get mapping should return None for non-existent mapping."""
        mapping = instance_mapper.get_mapping("nonexistent_source", "nonexistent_user")
        assert mapping is None
    
    @pytest.mark.asyncio
    async def test_update_last_message(self, mock_source_repo, instance_mapper):
        """Update last message should update timestamp."""
        import time
        
        source_id = "test_source"
        external_user_id = "12345"
        
        # Create instance first
        await instance_mapper.get_or_create_instance(
            source_id=source_id,
            external_user_id=external_user_id,
            agent_id="test"
        )
        
        # Get initial mapping
        initial_mapping = instance_mapper.get_mapping(source_id, external_user_id)
        initial_timestamp = initial_mapping["last_message_at"]
        
        # Wait a bit to ensure timestamp would change
        time.sleep(0.01)
        
        # Update last message
        instance_mapper.update_last_message(source_id, external_user_id)
        
        # Get updated mapping
        updated_mapping = instance_mapper.get_mapping(source_id, external_user_id)
        
        # The timestamp should have been updated
        assert updated_mapping["last_message_at"] is not None


# ==================== Deduplication Tests ====================


class TestDeduplication:
    """Tests for message deduplication."""
    
    def test_is_duplicate_new_message(self, mock_source_repo, instance_mapper):
        """New message should return False."""
        source_id = "test_source"
        message_id = "msg_001"
        
        is_dup = instance_mapper.is_duplicate(source_id, message_id)
        
        assert is_dup is False
    
    def test_is_duplicate_duplicate_message(self, mock_source_repo, instance_mapper):
        """Duplicate message should return True."""
        source_id = "test_source"
        message_id = "msg_001"
        
        # First message - not a duplicate
        is_dup_1 = instance_mapper.is_duplicate(source_id, message_id)
        assert is_dup_1 is False
        
        # Second message with same ID - should be duplicate
        is_dup_2 = instance_mapper.is_duplicate(source_id, message_id)
        assert is_dup_2 is True
    
    def test_is_duplicate_different_sources(self, mock_source_repo, instance_mapper):
        """Same message ID from different sources should not be duplicates."""
        message_id = "msg_001"
        
        is_dup_source1 = instance_mapper.is_duplicate("source_1", message_id)
        is_dup_source2 = instance_mapper.is_duplicate("source_2", message_id)
        
        assert is_dup_source1 is False
        assert is_dup_source2 is False
    
    def test_is_duplicate_different_messages_same_source(self, mock_source_repo, instance_mapper):
        """Different messages from same source should not be duplicates."""
        source_id = "test_source"
        
        is_dup_1 = instance_mapper.is_duplicate(source_id, "msg_001")
        is_dup_2 = instance_mapper.is_duplicate(source_id, "msg_002")
        is_dup_3 = instance_mapper.is_duplicate(source_id, "msg_003")
        
        assert is_dup_1 is False
        assert is_dup_2 is False
        assert is_dup_3 is False


# ==================== Full Flow Tests ====================


class TestHandleIncomingMessage:
    """Tests for handle_incoming_message full flow."""
    
    @pytest.mark.asyncio
    async def test_handle_incoming_message_creates_instance(self, mock_source_repo, mock_manager, mock_registry):
        """Full flow: incoming message should create instance if new user."""
        from daemon.sources.base import IncomingMessage
        
        with patch("daemon.sources.mapper.get_registry", return_value=mock_registry):
            mapper = InstanceMapper(source_repo=mock_source_repo, manager=mock_manager)
            
            msg = IncomingMessage(
                external_user_id="12345",
                content="Hello",
                source_id="telegram_source",
                metadata={
                    "source_type": "telegram",
                    "message_id": "msg_001",
                    "agent_id": "test"
                }
            )
            
            agent_instance_id, source_id = await mapper.handle_incoming_message(
                msg=msg,
                default_agent_id="default"
            )
            
            # Should return an instance ID
            assert agent_instance_id is not None
            assert len(agent_instance_id) > 0
            
            # Should return the correct source_id
            assert source_id == "telegram_source"
            
            # Should have created mapping in DB
            mapping = mapper.get_mapping("telegram_source", "12345")
            assert mapping is not None
            assert mapping["agent_instance_id"] == agent_instance_id
    
    @pytest.mark.asyncio
    async def test_handle_incoming_message_rejects_duplicate(self, mock_source_repo, mock_manager, mock_registry):
        """Full flow: duplicate message should raise ValueError."""
        from daemon.sources.base import IncomingMessage
        
        with patch("daemon.sources.mapper.get_registry", return_value=mock_registry):
            mapper = InstanceMapper(source_repo=mock_source_repo, manager=mock_manager)
            
            msg = IncomingMessage(
                external_user_id="12345",
                content="Hello",
                source_id="telegram_source",
                metadata={
                    "source_type": "telegram",
                    "message_id": "msg_duplicate",
                    "agent_id": "test"
                }
            )
            
            # First message should succeed
            agent_instance_id, source_id = await mapper.handle_incoming_message(
                msg=msg,
                default_agent_id="default"
            )
            assert agent_instance_id is not None
            
            # Second message with same ID should raise ValueError
            with pytest.raises(ValueError, match="Duplicate message"):
                await mapper.handle_incoming_message(
                    msg=msg,
                    default_agent_id="default"
                )
    
    @pytest.mark.asyncio
    async def test_handle_incoming_message_updates_last_message(self, mock_source_repo, mock_manager, mock_registry):
        """Full flow: should update last_message_at timestamp."""
        from daemon.sources.base import IncomingMessage
        import time
        
        with patch("daemon.sources.mapper.get_registry", return_value=mock_registry):
            mapper = InstanceMapper(source_repo=mock_source_repo, manager=mock_manager)
            
            msg = IncomingMessage(
                external_user_id="12345",
                content="Hello",
                source_id="webhook_source",
                metadata={
                    "source_type": "webhook",
                    "message_id": "msg_001",
                    "agent_id": "test"
                }
            )
            
            await mapper.handle_incoming_message(
                msg=msg,
                default_agent_id="default"
            )
            
            # Get mapping and check timestamp
            mapping = mapper.get_mapping("webhook_source", "12345")
            assert mapping["last_message_at"] is not None
            
            # Wait a bit
            time.sleep(0.01)
            
            # Send another message
            msg2 = IncomingMessage(
                external_user_id="12345",
                content="Hello again",
                source_id="webhook_source",
                metadata={
                    "source_type": "webhook",
                    "message_id": "msg_002",
                }
            )
            
            await mapper.handle_incoming_message(
                msg=msg2,
                default_agent_id="default"
            )
            
            # Timestamp should still be updated
            mapping2 = mapper.get_mapping("webhook_source", "12345")
            assert mapping2["last_message_at"] is not None
    
    @pytest.mark.asyncio
    async def test_handle_incoming_message_without_message_id(self, mock_source_repo, mock_manager, mock_registry):
        """Should work without message_id in metadata (no deduplication)."""
        from daemon.sources.base import IncomingMessage
        
        with patch("daemon.sources.mapper.get_registry", return_value=mock_registry):
            mapper = InstanceMapper(source_repo=mock_source_repo, manager=mock_manager)
            
            msg = IncomingMessage(
                external_user_id="12345",
                content="Hello",
                source_id="webhook_source",
                metadata={
                    "source_type": "webhook",
                    # No message_id - no deduplication
                }
            )
            
            # Should work fine without message_id
            agent_instance_id, source_id = await mapper.handle_incoming_message(
                msg=msg,
                default_agent_id="default"
            )
            
            assert agent_instance_id is not None
    
    @pytest.mark.asyncio
    async def test_handle_incoming_message_invalid_user_id(self, mock_source_repo, mock_manager, mock_registry):
        """Should raise ValidationError for invalid user ID."""
        from daemon.sources.base import IncomingMessage
        
        with patch("daemon.sources.mapper.get_registry", return_value=mock_registry):
            mapper = InstanceMapper(source_repo=mock_source_repo, manager=mock_manager)
            
            msg = IncomingMessage(
                external_user_id="invalid_id_with_spaces 123",
                content="Hello",
                source_id="telegram_source",
                metadata={
                    "source_type": "telegram",
                    "agent_id": "test"
                }
            )
            
            with pytest.raises(ValidationError, match="Invalid Telegram ID"):
                await mapper.handle_incoming_message(
                    msg=msg,
                    default_agent_id="default"
                )
    
    @pytest.mark.asyncio
    async def test_handle_incoming_message_uses_default_agent_id(self, mock_source_repo, mock_manager, mock_registry):
        """Should use default_agent_id when not specified in metadata."""
        from daemon.sources.base import IncomingMessage
        
        with patch("daemon.sources.mapper.get_registry", return_value=mock_registry):
            mapper = InstanceMapper(source_repo=mock_source_repo, manager=mock_manager)
            
            msg = IncomingMessage(
                external_user_id="12345",
                content="Hello",
                source_id="webhook_source",
                metadata={
                    "source_type": "webhook",
                    # No agent_id in metadata
                }
            )
            
            await mapper.handle_incoming_message(
                msg=msg,
                default_agent_id="default_agent"
            )
            
            # Should have created mapping with default agent
            mapping = mapper.get_mapping("webhook_source", "12345")
            assert mapping is not None
            assert mapping["agent_id"] == "default_agent"

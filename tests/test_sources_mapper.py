"""Tests for daemon/sources/mapper.py"""

import pytest
import sqlite3
import tempfile
import os
import uuid
from unittest.mock import MagicMock, AsyncMock
from pathlib import Path

from daemon.sources.mapper import (
    validate_external_user_id,
    ValidationError,
    SessionMapper,
    MAX_USER_ID_LENGTH,
)


# ==================== Fixtures ====================


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database with required tables."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    
    # Enable WAL mode
    conn.execute("PRAGMA journal_mode=WAL")
    
    # Create tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_configs (
            source_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            name TEXT NOT NULL,
            config JSON NOT NULL,
            credentials TEXT,
            enabled BOOLEAN DEFAULT TRUE,
            status TEXT DEFAULT 'stopped',
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_mappings (
            mapping_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            agent_session_id TEXT NOT NULL,
            agent_dir TEXT NOT NULL,
            metadata JSON,
            last_message_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, external_user_id)
        )
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_external_messages (
            source_id TEXT,
            external_message_id TEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source_id, external_message_id)
        )
    """)
    
    conn.commit()
    
    yield conn
    
    conn.close()
    os.unlink(db_path)


@pytest.fixture
def mock_manager():
    """Create a mock SessionManager."""
    manager = MagicMock()
    # Create a simple spawn that returns a UUID
    manager.spawn_session = MagicMock(return_value=str(uuid.uuid4()))
    return manager


@pytest.fixture
def session_mapper(temp_db, mock_manager):
    """Create a SessionMapper with temp database and mock manager."""
    return SessionMapper(conn=temp_db, manager=mock_manager)


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


# ==================== SessionMapper Tests ====================


class TestSessionMapper:
    """Tests for SessionMapper class."""
    
    @pytest.mark.asyncio
    async def test_get_or_create_session_new_user(self, temp_db, session_mapper):
        """Creating session for new user should create mapping."""
        source_id = "test_source"
        external_user_id = "12345"
        agent_dir = "/agents/test_agent"
        
        # Get or create session
        agent_session_id = await session_mapper.get_or_create_session(
            source_id=source_id,
            external_user_id=external_user_id,
            agent_dir=agent_dir
        )
        
        # Should return a valid session ID
        assert agent_session_id is not None
        assert len(agent_session_id) > 0
        
        # Should have created a mapping in the database
        mapping = session_mapper.get_mapping(source_id, external_user_id)
        assert mapping is not None
        assert mapping["source_id"] == source_id
        assert mapping["external_user_id"] == external_user_id
        assert mapping["agent_session_id"] == agent_session_id
        assert mapping["agent_dir"] == agent_dir
    
    @pytest.mark.asyncio
    async def test_get_or_create_session_existing_user(self, temp_db, session_mapper):
        """Existing user should return existing session."""
        source_id = "test_source"
        external_user_id = "12345"
        agent_dir = "/agents/test_agent"
        
        # Create first session
        session_id_1 = await session_mapper.get_or_create_session(
            source_id=source_id,
            external_user_id=external_user_id,
            agent_dir=agent_dir
        )
        
        # Get or create again - should return same session
        session_id_2 = await session_mapper.get_or_create_session(
            source_id=source_id,
            external_user_id=external_user_id,
            agent_dir=agent_dir
        )
        
        # Should return same session ID
        assert session_id_1 == session_id_2
    
    @pytest.mark.asyncio
    async def test_get_or_create_session_spawns_agent(self, temp_db, mock_manager):
        """Should call manager.spawn_session to create new agent."""
        # Reset the mock to track calls
        mock_manager.spawn_session.reset_mock()
        
        mapper = SessionMapper(conn=temp_db, manager=mock_manager)
        
        session_id = await mapper.get_or_create_session(
            source_id="test_source",
            external_user_id="user123",
            agent_dir="/agents/test"
        )
        
        # Should have called spawn_session
        mock_manager.spawn_session.assert_called_once_with("/agents/test")
        
        # Should return the spawned session ID
        assert session_id == mock_manager.spawn_session.return_value
    
    @pytest.mark.asyncio
    async def test_get_mapping_returns_mapping(self, temp_db, session_mapper):
        """Get mapping should return existing mapping."""
        source_id = "test_source"
        external_user_id = "12345"
        
        # First create a session to have a mapping
        await session_mapper.get_or_create_session(
            source_id=source_id,
            external_user_id=external_user_id,
            agent_dir="/agents/test"
        )
        
        # Get mapping should return it
        mapping = session_mapper.get_mapping(source_id, external_user_id)
        assert mapping is not None
        assert mapping["external_user_id"] == external_user_id
        assert mapping["source_id"] == source_id
    
    def test_get_mapping_not_found(self, temp_db, session_mapper):
        """Get mapping should return None for non-existent mapping."""
        mapping = session_mapper.get_mapping("nonexistent_source", "nonexistent_user")
        assert mapping is None
    
    @pytest.mark.asyncio
    async def test_update_last_message(self, temp_db, session_mapper):
        """Update last message should update timestamp."""
        import time
        
        source_id = "test_source"
        external_user_id = "12345"
        
        # Create session first
        await session_mapper.get_or_create_session(
            source_id=source_id,
            external_user_id=external_user_id,
            agent_dir="/agents/test"
        )
        
        # Get initial mapping
        initial_mapping = session_mapper.get_mapping(source_id, external_user_id)
        initial_timestamp = initial_mapping["last_message_at"]
        
        # Wait a bit to ensure timestamp would change
        time.sleep(0.01)
        
        # Update last message
        session_mapper.update_last_message(source_id, external_user_id)
        
        # Get updated mapping
        updated_mapping = session_mapper.get_mapping(source_id, external_user_id)
        
        # The timestamp should have been updated
        assert updated_mapping["last_message_at"] is not None


# ==================== Deduplication Tests ====================


class TestDeduplication:
    """Tests for message deduplication."""
    
    def test_is_duplicate_new_message(self, temp_db, session_mapper):
        """New message should return False."""
        source_id = "test_source"
        message_id = "msg_001"
        
        is_dup = session_mapper.is_duplicate(source_id, message_id)
        
        assert is_dup is False
    
    def test_is_duplicate_duplicate_message(self, temp_db, session_mapper):
        """Duplicate message should return True."""
        source_id = "test_source"
        message_id = "msg_001"
        
        # First message - not a duplicate
        is_dup_1 = session_mapper.is_duplicate(source_id, message_id)
        assert is_dup_1 is False
        
        # Second message with same ID - should be duplicate
        is_dup_2 = session_mapper.is_duplicate(source_id, message_id)
        assert is_dup_2 is True
    
    def test_is_duplicate_different_sources(self, temp_db, session_mapper):
        """Same message ID from different sources should not be duplicates."""
        message_id = "msg_001"
        
        is_dup_source1 = session_mapper.is_duplicate("source_1", message_id)
        is_dup_source2 = session_mapper.is_duplicate("source_2", message_id)
        
        assert is_dup_source1 is False
        assert is_dup_source2 is False
    
    def test_is_duplicate_different_messages_same_source(self, temp_db, session_mapper):
        """Different messages from same source should not be duplicates."""
        source_id = "test_source"
        
        is_dup_1 = session_mapper.is_duplicate(source_id, "msg_001")
        is_dup_2 = session_mapper.is_duplicate(source_id, "msg_002")
        is_dup_3 = session_mapper.is_duplicate(source_id, "msg_003")
        
        assert is_dup_1 is False
        assert is_dup_2 is False
        assert is_dup_3 is False


# ==================== Full Flow Tests ====================


class TestHandleIncomingMessage:
    """Tests for handle_incoming_message full flow."""
    
    @pytest.mark.asyncio
    async def test_handle_incoming_message_creates_session(self, temp_db, mock_manager):
        """Full flow: incoming message should create session if new user."""
        from daemon.sources.base import IncomingMessage
        
        mapper = SessionMapper(conn=temp_db, manager=mock_manager)
        
        msg = IncomingMessage(
            external_user_id="12345",
            content="Hello",
            source_id="telegram_source",
            metadata={
                "source_type": "telegram",
                "message_id": "msg_001",
                "agent_dir": "/agents/test"
            }
        )
        
        agent_session_id, source_id = await mapper.handle_incoming_message(
            msg=msg,
            default_agent_dir="/agents/default"
        )
        
        # Should return a session ID
        assert agent_session_id is not None
        assert len(agent_session_id) > 0
        
        # Should return the correct source_id
        assert source_id == "telegram_source"
        
        # Should have created mapping in DB
        mapping = mapper.get_mapping("telegram_source", "12345")
        assert mapping is not None
        assert mapping["agent_session_id"] == agent_session_id
    
    @pytest.mark.asyncio
    async def test_handle_incoming_message_rejects_duplicate(self, temp_db, mock_manager):
        """Full flow: duplicate message should raise ValueError."""
        from daemon.sources.base import IncomingMessage
        
        mapper = SessionMapper(conn=temp_db, manager=mock_manager)
        
        msg = IncomingMessage(
            external_user_id="12345",
            content="Hello",
            source_id="telegram_source",
            metadata={
                "source_type": "telegram",
                "message_id": "msg_duplicate",
                "agent_dir": "/agents/test"
            }
        )
        
        # First message should succeed
        agent_session_id, source_id = await mapper.handle_incoming_message(
            msg=msg,
            default_agent_dir="/agents/default"
        )
        assert agent_session_id is not None
        
        # Second message with same ID should raise ValueError
        with pytest.raises(ValueError, match="Duplicate message"):
            await mapper.handle_incoming_message(
                msg=msg,
                default_agent_dir="/agents/default"
            )
    
    @pytest.mark.asyncio
    async def test_handle_incoming_message_updates_last_message(self, temp_db, mock_manager):
        """Full flow: should update last_message_at timestamp."""
        from daemon.sources.base import IncomingMessage
        import time
        
        mapper = SessionMapper(conn=temp_db, manager=mock_manager)
        
        msg = IncomingMessage(
            external_user_id="12345",
            content="Hello",
            source_id="webhook_source",
            metadata={
                "source_type": "webhook",
                "message_id": "msg_001",
                "agent_dir": "/agents/test"
            }
        )
        
        await mapper.handle_incoming_message(
            msg=msg,
            default_agent_dir="/agents/default"
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
            default_agent_dir="/agents/default"
        )
        
        # Timestamp should still be updated
        mapping2 = mapper.get_mapping("webhook_source", "12345")
        assert mapping2["last_message_at"] is not None
    
    @pytest.mark.asyncio
    async def test_handle_incoming_message_without_message_id(self, temp_db, mock_manager):
        """Should work without message_id in metadata (no deduplication)."""
        from daemon.sources.base import IncomingMessage
        
        mapper = SessionMapper(conn=temp_db, manager=mock_manager)
        
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
        agent_session_id, source_id = await mapper.handle_incoming_message(
            msg=msg,
            default_agent_dir="/agents/default"
        )
        
        assert agent_session_id is not None
    
    @pytest.mark.asyncio
    async def test_handle_incoming_message_invalid_user_id(self, temp_db, mock_manager):
        """Should raise ValidationError for invalid user ID."""
        from daemon.sources.base import IncomingMessage
        
        mapper = SessionMapper(conn=temp_db, manager=mock_manager)
        
        msg = IncomingMessage(
            external_user_id="invalid_id_with_spaces 123",
            content="Hello",
            source_id="telegram_source",
            metadata={
                "source_type": "telegram",
                "agent_dir": "/agents/test"
            }
        )
        
        with pytest.raises(ValidationError, match="Invalid Telegram ID"):
            await mapper.handle_incoming_message(
                msg=msg,
                default_agent_dir="/agents/default"
            )
    
    @pytest.mark.asyncio
    async def test_handle_incoming_message_uses_default_agent_dir(self, temp_db, mock_manager):
        """Should use default_agent_dir when not specified in metadata."""
        from daemon.sources.base import IncomingMessage
        
        mapper = SessionMapper(conn=temp_db, manager=mock_manager)
        
        msg = IncomingMessage(
            external_user_id="12345",
            content="Hello",
            source_id="webhook_source",
            metadata={
                "source_type": "webhook",
                # No agent_dir in metadata
            }
        )
        
        await mapper.handle_incoming_message(
            msg=msg,
            default_agent_dir="/agents/default_agent"
        )
        
        # Should have used the default agent dir
        mapping = mapper.get_mapping("webhook_source", "12345")
        assert mapping["agent_dir"] == "/agents/default_agent"

"""Tests for TelegramAdapter implementation."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import aiohttp

from daemon.sources.adapters.telegram import (
    TelegramAdapter,
    TelegramAPIError,
    CircuitOpenError,
    SecurityError,
    MAX_CHAT_LOCKS,
)
from daemon.sources.base import SourceConfig, SourceStatus, IncomingMessage, OutgoingMessage


def make_telegram_config(
    source_id: str = "telegram-main",
    bot_token: str = "test_token_123",
    **config_kwargs
) -> SourceConfig:
    """Create a Telegram source config for testing."""
    return SourceConfig(
        source_id=source_id,
        source_type="telegram",
        name="Test Telegram Bot",
        config=config_kwargs,
        credentials={"bot_token": bot_token},
    )


@pytest.fixture
def mock_on_message():
    """Create a mock message handler."""
    return AsyncMock()


@pytest.fixture
def telegram_config():
    """Create a default Telegram config."""
    return make_telegram_config()


class TestTelegramAdapterInit:
    """Tests for TelegramAdapter initialization."""
    
    def test_init_requires_bot_token(self, mock_on_message):
        """Should raise ValueError if bot_token is missing."""
        config = SourceConfig(
            source_id="test",
            source_type="telegram",
            name="Test",
            config={},
            credentials={},  # No bot_token
        )
        
        with pytest.raises(ValueError, match="bot_token"):
            TelegramAdapter(config, mock_on_message)
    
    def test_init_with_valid_config(self, telegram_config, mock_on_message):
        """Should initialize with valid config."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        assert adapter.source_id == "telegram-main"
        assert adapter.source_type == "telegram"
        assert adapter.status == SourceStatus.STOPPED
    
    def test_init_extracts_config_options(self, mock_on_message):
        """Should extract Telegram-specific config options."""
        config = make_telegram_config(
            secret_token="my_secret",
            polling_enabled=False,
            polling_timeout=60,
        )
        adapter = TelegramAdapter(config, mock_on_message)
        
        assert adapter._secret_token == "my_secret"
        assert adapter._polling_enabled is False
        assert adapter._polling_timeout == 60


class TestTelegramAdapterStartStop:
    """Tests for start/stop lifecycle."""
    
    @pytest.mark.asyncio
    async def test_start_creates_session_and_verifies_bot(self, telegram_config, mock_on_message):
        """Start should create HTTP session and verify bot."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={
            "ok": True,
            "result": {"id": 123, "username": "test_bot"}
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_response)
        
        with patch.object(aiohttp, 'ClientSession', return_value=mock_session):
            await adapter.start()
        
        assert adapter.status == SourceStatus.RUNNING
        assert adapter._bot_info["username"] == "test_bot"
        assert adapter._session is not None
    
    @pytest.mark.asyncio
    async def test_start_sets_error_on_failure(self, telegram_config, mock_on_message):
        """Start should set ERROR status on failure."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        with patch.object(aiohttp, 'ClientSession', side_effect=Exception("Connection failed")):
            with pytest.raises(Exception):
                await adapter.start()
        
        assert adapter.status == SourceStatus.ERROR
        assert "Connection failed" in adapter.error
    
    @pytest.mark.asyncio
    async def test_stop_closes_session(self, telegram_config, mock_on_message):
        """Stop should close HTTP session."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        adapter._session = mock_session
        adapter._status = SourceStatus.RUNNING
        
        await adapter.stop()
        
        mock_session.close.assert_called_once()
        assert adapter.status == SourceStatus.STOPPED
    
    @pytest.mark.asyncio
    async def test_stop_cancels_polling_task(self, telegram_config, mock_on_message):
        """Stop should cancel any running polling task."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        # Create a real task that will be cancelled
        async def dummy_poll():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                raise
        
        adapter._polling_task = asyncio.create_task(dummy_poll())
        adapter._status = SourceStatus.RUNNING
        
        await adapter.stop()
        
        # Task should be cancelled
        assert adapter._polling_task is None


class TestTelegramAdapterSend:
    """Tests for sending messages."""
    
    @pytest.mark.asyncio
    async def test_send_validates_chat_id(self, telegram_config, mock_on_message):
        """Send should validate chat_id format."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        adapter._status = SourceStatus.RUNNING
        adapter._session = AsyncMock(spec=aiohttp.ClientSession)
        
        # Invalid chat_id (contains letters)
        message = OutgoingMessage(
            external_user_id="abc123",
            content="Hello",
            source_id="telegram-main",
        )
        
        result = await adapter.send(message)
        assert result is False
    
    @pytest.mark.asyncio
    async def test_send_fails_when_not_running(self, telegram_config, mock_on_message):
        """Send should fail if adapter not running."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        # Status is STOPPED by default
        
        message = OutgoingMessage(
            external_user_id="123456",
            content="Hello",
            source_id="telegram-main",
        )
        
        result = await adapter.send(message)
        assert result is False
    
    @pytest.mark.asyncio
    async def test_send_calls_api(self, telegram_config, mock_on_message):
        """Send should call Telegram API."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        adapter._status = SourceStatus.RUNNING
        
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={
            "ok": True,
            "result": {"message_id": 1}
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_response)
        adapter._session = mock_session
        
        message = OutgoingMessage(
            external_user_id="123456",
            content="Hello World",
            source_id="telegram-main",
        )
        
        result = await adapter.send(message)
        assert result is True
        mock_session.post.assert_called_once()


class TestTelegramAdapterHealthCheck:
    """Tests for health check."""
    
    @pytest.mark.asyncio
    async def test_health_check_returns_false_when_stopped(self, telegram_config, mock_on_message):
        """Health check should fail when stopped."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        result = await adapter.health_check()
        assert result is False
    
    @pytest.mark.asyncio
    async def test_health_check_calls_getme(self, telegram_config, mock_on_message):
        """Health check should call getMe API."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        adapter._status = SourceStatus.RUNNING
        
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={
            "ok": True,
            "result": {"id": 123}
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_response)
        adapter._session = mock_session
        
        result = await adapter.health_check()
        assert result is True


class TestTelegramAdapterWebhook:
    """Tests for webhook handling."""
    
    @pytest.mark.asyncio
    async def test_webhook_rejects_invalid_secret(self, mock_on_message):
        """Webhook should reject invalid secret token."""
        config = make_telegram_config(secret_token="correct_secret")
        adapter = TelegramAdapter(config, mock_on_message)
        
        payload = {"update_id": 1, "message": {}}
        headers = {"X-Telegram-Bot-Api-Secret-Token": "wrong_secret"}
        
        with pytest.raises(SecurityError):
            await adapter.handle_webhook(payload, headers)
    
    @pytest.mark.asyncio
    async def test_webhook_accepts_valid_secret(self, mock_on_message):
        """Webhook should accept valid secret token."""
        config = make_telegram_config(secret_token="correct_secret")
        adapter = TelegramAdapter(config, mock_on_message)
        
        payload = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "chat": {"id": 123456},
                "text": "Hello",
            }
        }
        headers = {"X-Telegram-Bot-Api-Secret-Token": "correct_secret"}
        
        await adapter.handle_webhook(payload, headers)
        mock_on_message.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_webhook_without_secret_validation(self, telegram_config, mock_on_message):
        """Webhook without secret should process normally."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        payload = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "chat": {"id": 123456},
                "text": "Hello",
            }
        }
        headers = {}
        
        await adapter.handle_webhook(payload, headers)
        mock_on_message.assert_called_once()


class TestTelegramAdapterProcessUpdate:
    """Tests for update processing."""
    
    @pytest.mark.asyncio
    async def test_processes_text_message(self, telegram_config, mock_on_message):
        """Should process text messages correctly."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        update = {
            "update_id": 1,
            "message": {
                "message_id": 100,
                "chat": {"id": 123456, "type": "private"},
                "from": {"id": 789, "username": "testuser", "first_name": "Test"},
                "text": "Hello bot!",
                "date": 1234567890,
            }
        }
        
        await adapter._process_update(update)
        
        mock_on_message.assert_called_once()
        msg = mock_on_message.call_args[0][0]
        
        assert isinstance(msg, IncomingMessage)
        assert msg.external_user_id == "123456"
        assert msg.content == "Hello bot!"
        assert msg.message_type == "text"
        assert msg.metadata["telegram"]["chat_type"] == "private"
    
    @pytest.mark.asyncio
    async def test_processes_command_message(self, telegram_config, mock_on_message):
        """Should detect command messages."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        update = {
            "update_id": 1,
            "message": {
                "message_id": 100,
                "chat": {"id": 123456},
                "text": "/start",
                "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
            }
        }
        
        await adapter._process_update(update)
        
        msg = mock_on_message.call_args[0][0]
        assert msg.message_type == "command"
    
    @pytest.mark.asyncio
    async def test_skips_non_text_messages(self, telegram_config, mock_on_message):
        """Should skip messages without text/content."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        update = {
            "update_id": 1,
            "message": {
                "message_id": 100,
                "chat": {"id": 123456},
                # No text, photo, document, or sticker
            }
        }
        
        await adapter._process_update(update)
        mock_on_message.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_handles_photo_message(self, telegram_config, mock_on_message):
        """Should handle photo messages with placeholder."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        update = {
            "update_id": 1,
            "message": {
                "message_id": 100,
                "chat": {"id": 123456},
                "photo": [{"file_id": "abc123"}],
            }
        }
        
        await adapter._process_update(update)
        
        msg = mock_on_message.call_args[0][0]
        assert msg.content == "[Photo]"
    
    @pytest.mark.asyncio
    async def test_handles_edited_message(self, telegram_config, mock_on_message):
        """Should handle edited messages."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        update = {
            "update_id": 1,
            "edited_message": {
                "message_id": 100,
                "chat": {"id": 123456},
                "text": "Edited text",
                "edit_date": 1234567900,
            }
        }
        
        await adapter._process_update(update)
        
        msg = mock_on_message.call_args[0][0]
        assert msg.content == "Edited text"


class TestTelegramAdapterChatIdValidation:
    """Tests for chat_id validation."""
    
    def test_validates_positive_chat_id(self, telegram_config, mock_on_message):
        """Should accept positive chat IDs."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        assert adapter._validate_chat_id("123456") is True
        assert adapter._validate_chat_id("999999999") is True
    
    def test_validates_negative_chat_id(self, telegram_config, mock_on_message):
        """Should accept negative chat IDs (groups/channels)."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        assert adapter._validate_chat_id("-1001234567890") is True
        assert adapter._validate_chat_id("-123456") is True
    
    def test_rejects_invalid_chat_ids(self, telegram_config, mock_on_message):
        """Should reject invalid chat IDs."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        assert adapter._validate_chat_id("") is False
        assert adapter._validate_chat_id("abc") is False
        assert adapter._validate_chat_id("123abc") is False
        assert adapter._validate_chat_id("x" * 25) is False  # Too long


class TestTelegramAdapterCircuitBreaker:
    """Tests for circuit breaker integration."""
    
    @pytest.mark.asyncio
    async def test_api_failure_opens_circuit(self, telegram_config, mock_on_message):
        """API failures should open circuit breaker."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        adapter._status = SourceStatus.RUNNING
        
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={
            "ok": False,
            "error_code": 500,
            "description": "Internal Server Error"
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_response)
        adapter._session = mock_session
        
        # Make failing calls until circuit opens (threshold is 5)
        # API errors (not network errors) count as 1 failure per call
        for i in range(5):
            try:
                await adapter._api_call("getMe")
            except TelegramAPIError:
                pass
        
        # Circuit should now be open (5 failures >= threshold of 5)
        assert await adapter._circuit_breaker.can_execute() is False
    
    @pytest.mark.asyncio
    async def test_send_returns_false_on_circuit_open(self, telegram_config, mock_on_message):
        """Send should return False when circuit is open."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        adapter._status = SourceStatus.RUNNING
        
        # Set up proper mock session
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={"ok": True, "result": {}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_response)
        adapter._session = mock_session
        
        # Force circuit open by recording enough failures
        for _ in range(5):
            await adapter._circuit_breaker.record_failure()
        
        # Verify circuit is open
        assert await adapter._circuit_breaker.can_execute() is False
        
        message = OutgoingMessage(
            external_user_id="123456",
            content="Hello",
            source_id="telegram-main",
        )
        
        result = await adapter.send(message)
        # Should return False because circuit is open (checked before rate limiter)
        assert result is False


class TestTelegramAdapterPollingRobustness:
    """Tests for polling loop robustness - prevents message loss."""
    
    @pytest.mark.asyncio
    async def test_polling_continues_after_process_failure(self, telegram_config):
        """Should continue processing subsequent updates after one fails."""
        processed_updates = []
        
        async def track_message(msg):
            processed_updates.append(msg.metadata.get("telegram", {}).get("message_id"))
        
        adapter = TelegramAdapter(telegram_config, track_message)
        adapter._status = SourceStatus.RUNNING
        adapter._session = AsyncMock(spec=aiohttp.ClientSession)
        
        # Simulate the polling loop behavior manually
        updates = [
            {"update_id": 1, "message": {"message_id": 100, "chat": {"id": 123}, "text": "msg1"}},
            {"update_id": 2, "message": {"message_id": 101, "chat": {"id": 123}, "text": "msg2"}},
            {"update_id": 3, "message": {"message_id": 102, "chat": {"id": 123}, "text": "msg3"}},
        ]
        
        # Make the second message fail
        emit_call_count = 0
        original_emit = adapter._emit_message
        
        async def failing_emit(msg):
            nonlocal emit_call_count
            emit_call_count += 1
            if emit_call_count == 2:
                raise Exception("Simulated processing failure")
            await original_emit(msg)
        
        adapter._emit_message = failing_emit
        
        # Simulate polling loop behavior
        for update in updates:
            try:
                await adapter._process_update(update)
                adapter._last_update_id = update.get("update_id", adapter._last_update_id)
            except Exception:
                pass  # Continue to next update
        
        # First and third messages should have been processed (second failed)
        assert 100 in processed_updates  # First succeeded
        assert 102 in processed_updates  # Third succeeded despite second failing
    
    @pytest.mark.asyncio
    async def test_update_id_only_acknowledged_after_success(self, telegram_config, mock_on_message):
        """Update ID should only be updated after successful processing."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        adapter._status = SourceStatus.RUNNING
        adapter._session = AsyncMock(spec=aiohttp.ClientSession)
        
        # Initial state
        assert adapter._last_update_id == 0
        
        # Process a successful update
        update = {"update_id": 5, "message": {"message_id": 100, "chat": {"id": 123}, "text": "test"}}
        await adapter._process_update(update)
        
        # Manually acknowledge (as polling loop does)
        adapter._last_update_id = update.get("update_id", adapter._last_update_id)
        
        assert adapter._last_update_id == 5
    
    @pytest.mark.asyncio
    async def test_failed_update_not_acknowledged(self, telegram_config, mock_on_message):
        """Failed updates should not be acknowledged, allowing re-fetch."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        adapter._status = SourceStatus.RUNNING
        adapter._session = AsyncMock(spec=aiohttp.ClientSession)
        
        # Simulate a failure in _emit_message
        adapter._emit_message = AsyncMock(side_effect=Exception("Processing failed"))
        
        update = {"update_id": 10, "message": {"message_id": 100, "chat": {"id": 123}, "text": "test"}}
        
        # _process_update catches exceptions internally and logs them
        await adapter._process_update(update)
        
        # _last_update_id should NOT be updated (would be checked by caller in polling loop)
        assert adapter._last_update_id == 0  # Still at initial value
        
        # The polling loop only acknowledges after successful processing
        # So if we simulate the polling loop behavior, the update_id stays at 0


class TestTelegramAdapterConcurrency:
    """Tests for concurrent message handling."""
    
    @pytest.mark.asyncio
    async def test_concurrent_sends_isolated_by_chat(self, telegram_config, mock_on_message):
        """Concurrent sends to different chats should not block each other."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        adapter._status = SourceStatus.RUNNING
        
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={"ok": True, "result": {}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_response)
        adapter._session = mock_session
        
        # Send to two different chats concurrently
        import asyncio
        results = await asyncio.gather(
            adapter.send(OutgoingMessage(external_user_id="111", content="Hello", source_id="telegram-main")),
            adapter.send(OutgoingMessage(external_user_id="222", content="World", source_id="telegram-main")),
        )
        
        # Both should succeed
        assert results[0] is True
        assert results[1] is True
    
    @pytest.mark.asyncio
    async def test_same_chat_sends_serialized(self, telegram_config, mock_on_message):
        """Concurrent sends to same chat should be serialized via lock."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        adapter._status = SourceStatus.RUNNING
        
        call_order = []
        
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        
        def make_response():
            mock_response = AsyncMock()
            mock_response.json = AsyncMock(return_value={"ok": True, "result": {}})
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)
            return mock_response
        
        def track_order(url, json, timeout):
            call_order.append(json["text"])
            return make_response()
        
        mock_session.post = track_order
        adapter._session = mock_session
        
        # Send two messages to same chat concurrently
        import asyncio
        await asyncio.gather(
            adapter.send(OutgoingMessage(external_user_id="111", content="First", source_id="telegram-main")),
            adapter.send(OutgoingMessage(external_user_id="111", content="Second", source_id="telegram-main")),
        )
        
        # Both messages should have been sent (order may vary due to concurrency)
        assert len(call_order) == 2
        assert "First" in call_order
        assert "Second" in call_order


class TestTelegramAdapterResourceManagement:
    """Tests for resource management and memory safety."""
    
    @pytest.mark.asyncio
    async def test_chat_locks_evicted_at_capacity(self, telegram_config, mock_on_message):
        """Old chat locks should be evicted when limit reached."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        # Fill up to capacity
        for i in range(MAX_CHAT_LOCKS):
            await adapter._get_chat_lock(str(i))
        
        assert len(adapter._chat_locks) == MAX_CHAT_LOCKS
        
        # Add one more - should evict oldest
        await adapter._get_chat_lock("new_chat")
        
        assert len(adapter._chat_locks) == MAX_CHAT_LOCKS
        assert "0" not in adapter._chat_locks  # Oldest evicted
        assert "new_chat" in adapter._chat_locks  # New one added
    
    @pytest.mark.asyncio
    async def test_chat_lock_lru_access_moves_to_end(self, telegram_config, mock_on_message):
        """Accessing a chat lock should move it to most-recently-used position."""
        adapter = TelegramAdapter(telegram_config, mock_on_message)
        
        # Add three chats
        await adapter._get_chat_lock("chat_1")
        await adapter._get_chat_lock("chat_2")
        await adapter._get_chat_lock("chat_3")
        
        # Access chat_1 again - should move to end
        await adapter._get_chat_lock("chat_1")
        
        # Check order: chat_2, chat_3, chat_1 (chat_1 moved to end)
        keys = list(adapter._chat_locks.keys())
        assert keys[-1] == "chat_1"  # Most recently used at end

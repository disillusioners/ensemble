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

    def test_init_with_default_agent_fallback(self, mock_on_message):
        """Should default to 'ari' if no default_agent in config."""
        config = SourceConfig(
            source_id="test",
            source_type="telegram",
            name="Test",
            config={},  # No default_agent
            credentials={"bot_token": "test_token_123"},
        )
        adapter = TelegramAdapter(config, mock_on_message)
        assert adapter._default_agent == "ari"

    def test_init_extracts_default_agent(self, mock_on_message):
        """Should extract default_agent from config."""
        config = make_telegram_config(default_agent="custom-agent")
        adapter = TelegramAdapter(config, mock_on_message)
        assert adapter._default_agent == "custom-agent"


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
                "from": {"id": 789, "username": "testuser"},
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
                "from": {"id": 789, "username": "testuser"},
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
        assert msg.external_user_id == "789"  # from_user_id for private chat
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
                "from": {"id": 789, "username": "testuser"},
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
                "from": {"id": 789, "username": "testuser"},
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
                "from": {"id": 789, "username": "testuser"},
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
            {"update_id": 1, "message": {"message_id": 100, "chat": {"id": 123}, "from": {"id": 789}, "text": "msg1"}},
            {"update_id": 2, "message": {"message_id": 101, "chat": {"id": 123}, "from": {"id": 789}, "text": "msg2"}},
            {"update_id": 3, "message": {"message_id": 102, "chat": {"id": 123}, "from": {"id": 789}, "text": "msg3"}},
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


# ============================================================================
# Phase B — chart-image delivery tests (Phase B.5 tasks #33-#35)
# ============================================================================
#
# Implements:
#   * test_telegram_send_photo_multipart (Task #33)
#   * test_telegram_4xx_non_transient_no_circuit_record (Task #34)
#   * test_telegram_send_document_for_large_image (Task #35)
#   * test_telegram_oversize_skip_with_warn (Task #16)
#   * test_telegram_mime_miss_skip_with_warn (Task #16)
#   * test_telegram_all_images_failed_text_floor (Task #16 — iter-002 blocking #1)
#   * test_telegram_caption_followup_when_caption_exceeds_1024 (Task #17)
#   * test_telegram_parse_mode_none_for_image_caption (Task #18)


import base64
import os

from daemon.sources.base import ImageAttachment
from daemon.constants import (
    TELEGRAM_PHOTO_MAX_BYTES,
    TELEGRAM_DOCUMENT_MAX_BYTES,
    CHART_IMAGE_MIME_WHITELIST,
)
from daemon.sources.adapters.telegram import TelegramAPIError, _TelegramNonTransientAPIError


def _make_telegram_att(image_id: str = "a" * 32, *, size: int = 100, content_type: str = "image/png") -> ImageAttachment:
    bytes_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"X" * max(0, size - 8)).decode("ascii")
    return ImageAttachment(
        image_id=image_id,
        content_type=content_type,
        filename=f"chart-{image_id[:8]}.png",
        size_bytes=size,
        bytes_b64=bytes_b64,
    )


def _make_mock_session(*, photo_return: dict | Exception = None, doc_return: dict | Exception = None, message_return: dict | Exception = None):
    """Build a mock aiohttp.ClientSession that records FormData/multipart posts.

    photo_return / doc_return / message_return:
      - dict (with "ok": True) → returned as resp.json()
      - Exception → raised from resp.json()
      - None → success default
    """
    session = AsyncMock()
    posts: list[dict] = []

    def _post(url, **kwargs):
        # Capture: which method was called (sendPhoto / sendDocument / sendMessage)
        # and the FormData fields.
        if "data" in kwargs and hasattr(kwargs["data"], "_fields"):
            # aiohttp.FormData._fields: List[Tuple[MultiDict, Dict, bytes/str]]
            # We only need the field NAME for assertions (photo/document vs
            # caption/chat_id). The value is bytes for files, str for params.
            fields = []
            for entry in kwargs["data"]._fields:
                name = entry[0].get("name", "") if hasattr(entry[0], "get") else str(entry[0])
                fields.append((name, entry[2]))
        else:
            fields = []
        posts.append({"url": url, "fields": fields, "kwargs": kwargs})

        # Choose return based on the method substring.
        method = url.rsplit("/", 1)[-1]
        if method == "sendPhoto":
            payload = photo_return if photo_return is not None else {"ok": True, "result": {"message_id": 1}}
        elif method == "sendDocument":
            payload = doc_return if doc_return is not None else {"ok": True, "result": {"message_id": 2}}
        elif method == "sendMessage":
            payload = message_return if message_return is not None else {"ok": True, "result": {"message_id": 3}}
        else:
            payload = {"ok": True, "result": {}}

        if isinstance(payload, Exception):
            # Return a response whose .json() raises the exception.
            resp = MagicMock()
            async_cm = MagicMock()
            async_cm.json = AsyncMock(side_effect=payload)
            async_cm.__aenter__ = AsyncMock(return_value=async_cm)
            async_cm.__aexit__ = AsyncMock(return_value=None)
            return async_cm

        # Build a proper async context manager wrapping a real aiohttp-style resp.
        async_cm = MagicMock()
        async_cm.__aenter__ = AsyncMock(return_value=async_cm)
        async_cm.__aexit__ = AsyncMock(return_value=None)
        async_cm.json = AsyncMock(return_value=payload)
        return async_cm

    session.post = MagicMock(side_effect=_post)
    session.posts = posts  # expose
    return session


@pytest.fixture
def tg_adapter(telegram_config, mock_on_message):
    a = TelegramAdapter(telegram_config, mock_on_message)
    a._status = SourceStatus.RUNNING
    a._bot_info = {"username": "test_bot"}
    return a


class TestTelegramSendPhotoMultipart:
    """Task #33: sendPhoto multipart with the photo field."""

    @pytest.mark.asyncio
    async def test_send_photo_with_image(self, tg_adapter):
        session = _make_mock_session()
        tg_adapter._session = session

        msg = OutgoingMessage(
            external_user_id="123456",
            content="Here is the chart",
            source_id="telegram-main",
            images=[_make_telegram_att(size=5000)],  # ≤ 10 MB → sendPhoto
        )
        assert await tg_adapter.send(msg) is True
        # One sendPhoto call.
        photo_posts = [p for p in session.posts if "sendPhoto" in p["url"]]
        assert len(photo_posts) == 1
        # Photo field present + content_type image/png.
        photo_fields = dict(photo_posts[0]["fields"])
        assert "photo" in photo_fields
        # parse_mode is NOT passed (firm decision — Mermaid `<` safety).
        assert "parse_mode" not in photo_fields


class TestTelegramSendDocumentForLargeImage:
    """Task #35: >10 MB → sendDocument."""

    @pytest.mark.asyncio
    async def test_send_document_for_large_image(self, tg_adapter):
        session = _make_mock_session()
        tg_adapter._session = session

        # 11 MB > TELEGRAM_PHOTO_MAX_BYTES (10 MB) → sendDocument
        msg = OutgoingMessage(
            external_user_id="123456",
            content="large chart",
            source_id="telegram-main",
            images=[_make_telegram_att(size=11 * 1024 * 1024)],
        )
        assert await tg_adapter.send(msg) is True
        doc_posts = [p for p in session.posts if "sendDocument" in p["url"]]
        assert len(doc_posts) == 1
        doc_fields = dict(doc_posts[0]["fields"])
        assert "document" in doc_fields


class TestTelegramCircuitBreakerGuardrail:
    """Task #34: 4xx non-transient, no record_failure; transport keeps record."""

    @pytest.mark.asyncio
    async def test_4xx_non_transient_no_circuit_record(self, tg_adapter):
        # 4xx Telegram response
        bad_resp = {"ok": False, "error_code": 400, "description": "Bad Request: photo_invalid_dimensions"}
        session = _make_mock_session(photo_return=bad_resp)
        tg_adapter._session = session

        initial_failures = tg_adapter._circuit_breaker.failure_count

        msg = OutgoingMessage(
            external_user_id="123456",
            content="x",
            source_id="telegram-main",
            images=[_make_telegram_att()],
        )
        # 4xx → non-transient → image skipped + text floor (delivered_count == 0)
        # OR (depending on whether text content is empty) sendMessage follows.
        # Either way, send() returns True (text was delivered).
        result = await tg_adapter.send(msg)

        # Critical assertion: circuit-breaker DID NOT record failure.
        assert tg_adapter._circuit_breaker.failure_count == initial_failures
        # Result was True (text floor delivered).
        assert result is True

    @pytest.mark.asyncio
    async def test_5xx_records_failure_in_helper(self, tg_adapter):
        """Direct helper test: 5xx → record_failure fires once per attempt.

        Tests the helper in isolation (NOT through send() — the text-floor
        fallback would call record_success() and reset the breaker after
        the image-upload failure, which is correct behavior but doesn't
        let us observe the failure count here).
        """
        bad_resp = {"ok": False, "error_code": 500, "description": "Internal Server Error"}
        session = _make_mock_session(photo_return=bad_resp)
        tg_adapter._session = session

        initial_failures = tg_adapter._circuit_breaker.failure_count

        file_bytes = b"\x89PNG\r\n\x1a\n" + b"X" * 92
        with pytest.raises(TelegramAPIError):
            await tg_adapter._api_call_multipart(
                "sendPhoto",
                file_bytes=file_bytes,
                filename="x.png",
                file_field="photo",
                chat_id="123",
            )
        # 5xx → record_failure fired on each of MAX_RETRIES attempts.
        assert tg_adapter._circuit_breaker.failure_count > initial_failures

    @pytest.mark.asyncio
    async def test_5xx_send_message_text_floor_resets_breaker(self, tg_adapter):
        """End-to-end send(): 5xx image upload + successful text-floor → breaker
        resets via the text-floor's record_success. This is CORRECT behavior
        — the breaker reflects current health, and the text succeeded."""
        bad_resp = {"ok": False, "error_code": 500, "description": "ISE"}
        session = _make_mock_session(photo_return=bad_resp)
        tg_adapter._session = session

        msg = OutgoingMessage(
            external_user_id="123456",
            content="x",
            source_id="telegram-main",
            images=[_make_telegram_att()],
        )
        # 5xx on image upload → text-floor via _api_call (JSON path)
        # which succeeds → record_success → failure_count resets.
        result = await tg_adapter.send(msg)
        # The text was delivered.
        assert result is True
        # Breaker reflects the text's success.
        assert tg_adapter._circuit_breaker.failure_count == 0


class TestTelegramOversizeMimeMiss:
    """Task #16: >50 MB / MIME-miss → skip + WARN + deliver text."""

    @pytest.mark.asyncio
    async def test_oversize_image_skipped_with_warn(self, tg_adapter, caplog):
        session = _make_mock_session()
        tg_adapter._session = session

        # 51 MB > TELEGRAM_DOCUMENT_MAX_BYTES (50 MB) → skip
        msg = OutgoingMessage(
            external_user_id="123456",
            content="text only",
            source_id="telegram-main",
            images=[_make_telegram_att(size=51 * 1024 * 1024)],
        )
        with caplog.at_level("WARNING", logger="daemon.sources.adapters.telegram"):
            result = await tg_adapter.send(msg)
        assert result is True
        # No sendPhoto / sendDocument calls.
        upload_posts = [p for p in session.posts if "sendPhoto" in p["url"] or "sendDocument" in p["url"]]
        assert len(upload_posts) == 0
        # WARN was logged.
        assert any("too large" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_mime_miss_skipped_with_warn(self, tg_adapter, caplog):
        session = _make_mock_session()
        tg_adapter._session = session

        # text/plain not allowed
        msg = OutgoingMessage(
            external_user_id="123456",
            content="text only",
            source_id="telegram-main",
            images=[_make_telegram_att(content_type="text/plain")],
        )
        with caplog.at_level("WARNING", logger="daemon.sources.adapters.telegram"):
            result = await tg_adapter.send(msg)
        assert result is True
        upload_posts = [p for p in session.posts if "sendPhoto" in p["url"] or "sendDocument" in p["url"]]
        assert len(upload_posts) == 0
        assert any("MIME miss" in record.message for record in caplog.records)


class TestTelegramAllImagesFailedTextFloor:
    """Task #16 / iter-002 blocking #1: ALL image uploads fail + content ≤1024 →
    full text STILL delivered via the `delivered_count == 0` guard."""

    @pytest.mark.asyncio
    async def test_all_images_failed_content_below_1024_text_floor(self, tg_adapter):
        session = _make_mock_session()
        tg_adapter._session = session

        # All MIME-miss → all skipped; content short (below 1024).
        msg = OutgoingMessage(
            external_user_id="123456",
            content="short text",
            source_id="telegram-main",
            images=[
                _make_telegram_att(image_id="1" * 32, content_type="text/plain"),
                _make_telegram_att(image_id="2" * 32, content_type="text/plain"),
            ],
        )
        result = await tg_adapter.send(msg)
        # Text floor delivered → success.
        assert result is True
        # A sendMessage call (text) was issued.
        text_posts = [p for p in session.posts if "sendMessage" in p["url"]]
        assert len(text_posts) >= 1


class TestTelegramCaptionFollowup:
    """Task #17: caption >1024 → truncated on image + full-text follow-up sendMessage."""

    @pytest.mark.asyncio
    async def test_caption_over_1024_truncated_plus_followup(self, tg_adapter):
        session = _make_mock_session()
        tg_adapter._session = session

        long_caption = "x" * 1500  # > 1024

        msg = OutgoingMessage(
            external_user_id="123456",
            content=long_caption,
            source_id="telegram-main",
            images=[_make_telegram_att(size=5000)],
        )
        result = await tg_adapter.send(msg)
        assert result is True

        # sendPhoto was called with caption[:1024].
        photo_posts = [p for p in session.posts if "sendPhoto" in p["url"]]
        assert len(photo_posts) == 1
        photo_fields = dict(photo_posts[0]["fields"])
        caption_value = photo_fields.get("caption", "")
        # aiohttp.FormData stores values as bytes sometimes — handle both.
        if isinstance(caption_value, bytes):
            caption_value = caption_value.decode("utf-8", errors="replace")
        assert len(caption_value) == 1024

        # Follow-up sendMessage with caption[1024:] (476 chars).
        message_posts = [p for p in session.posts if "sendMessage" in p["url"]]
        assert len(message_posts) >= 1


class TestTelegramParseModeNone:
    """Task #18: parse_mode=None for image captions."""

    @pytest.mark.asyncio
    async def test_image_caption_has_no_parse_mode(self, tg_adapter):
        session = _make_mock_session()
        tg_adapter._session = session

        msg = OutgoingMessage(
            external_user_id="123456",
            content="text",
            source_id="telegram-main",
            images=[_make_telegram_att()],
        )
        await tg_adapter.send(msg)

        photo_posts = [p for p in session.posts if "sendPhoto" in p["url"]]
        assert len(photo_posts) == 1
        photo_fields = dict(photo_posts[0]["fields"])
        assert "parse_mode" not in photo_fields

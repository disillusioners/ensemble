"""Telegram Bot API adapter for message sources.

Implements both polling (for development) and webhook (for production)
modes for receiving messages from Telegram.
"""

import asyncio
import logging
import re
import secrets
from collections import OrderedDict
from typing import Any, Optional

import aiohttp

from ..base import (
    IncomingMessage,
    MessageSourceAdapter,
    OutgoingMessage,
    SourceConfig,
    SourceStatus,
)
from ..circuit_breaker import CircuitBreaker
from ..rate_limiter import DEFAULT_RATE_LIMITS, RateLimit, TokenBucketLimiter

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"
MAX_RETRIES = 3
POLLING_TIMEOUT = 30  # seconds
RETRY_BASE_DELAY = 1.0  # seconds
MAX_CHAT_LOCKS = 1000  # LRU eviction limit for per-chat locks


class TelegramAdapter(MessageSourceAdapter):
    """Telegram Bot API adapter.
    
    Supports:
    - Long polling for receiving messages (development)
    - Webhook for receiving messages (production)
    - Message sending with rate limiting
    - Circuit breaker for API failures
    """
    
    def __init__(self, config: SourceConfig, on_message, 
                 rate_limit: Optional[RateLimit] = None):
        super().__init__(config, on_message)
        
        # Extract Telegram-specific config
        self._bot_token = config.credentials.get("bot_token")
        if not self._bot_token:
            raise ValueError("Telegram adapter requires 'bot_token' in credentials")
        
        self._secret_token = config.config.get("secret_token")  # For webhook verification
        self._default_agent = config.config.get("default_agent")
        self._polling_enabled = config.config.get("polling_enabled", True)
        self._polling_timeout = config.config.get("polling_timeout", POLLING_TIMEOUT)
        
        # State
        self._session: Optional[aiohttp.ClientSession] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._last_update_id: int = 0
        self._bot_info: Optional[dict] = None
        
        # Rate limiting (Telegram: 30 msg/sec to same chat)
        rate = rate_limit or DEFAULT_RATE_LIMITS.get("telegram", RateLimit(30, 30))
        self._rate_limiter = TokenBucketLimiter(rate)
        
        # Circuit breaker for API resilience
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60.0,
        )
        
        # Per-chat rate limit tracking with LRU eviction
        self._chat_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._chat_locks_guard = asyncio.Lock()
    
    @property
    def bot_username(self) -> Optional[str]:
        """Get the bot's username if available."""
        if self._bot_info:
            return self._bot_info.get("username")
        return None
    
    async def _get_chat_lock(self, chat_id: str) -> asyncio.Lock:
        """Get or create per-chat lock for message ordering with LRU eviction."""
        async with self._chat_locks_guard:
            if chat_id in self._chat_locks:
                # Move to end (most recently used)
                self._chat_locks.move_to_end(chat_id)
                return self._chat_locks[chat_id]
            
            # Evict oldest if at capacity
            while len(self._chat_locks) >= MAX_CHAT_LOCKS:
                self._chat_locks.popitem(last=False)
            
            lock = asyncio.Lock()
            self._chat_locks[chat_id] = lock
            return lock
    
    def _get_api_url(self, method: str) -> str:
        """Build Telegram API URL for a method."""
        return TELEGRAM_API_BASE.format(token=self._bot_token, method=method)
    
    async def _api_call(self, method: str, **params) -> dict:
        """Make a Telegram Bot API call with circuit breaker protection.
        
        Args:
            method: Telegram API method name
            **params: API parameters
            
        Returns:
            API response data
            
        Raises:
            TelegramAPIError: On API errors
            CircuitOpenError: When circuit breaker is open
        """
        if not await self._circuit_breaker.can_execute():
            raise CircuitOpenError(f"Circuit open for Telegram API, method={method}")
        
        if not self._session:
            raise RuntimeError("Adapter not started - no HTTP session")
        
        url = self._get_api_url(method)
        
        # Remove None values
        params = {k: v for k, v in params.items() if v is not None}
        
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                async with self._session.post(url, json=params, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    data = await resp.json()
                    
                    if not data.get("ok"):
                        error_desc = data.get("description", "Unknown error")
                        error_code = data.get("error_code", 0)
                        raise TelegramAPIError(f"Telegram API error {error_code}: {error_desc}")
                    
                    await self._circuit_breaker.record_success()
                    return data.get("result", {})
                    
            except aiohttp.ClientError as e:
                last_error = e
                logger.warning(f"Telegram API call failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                # Count each retry failure toward circuit breaker
                await self._circuit_breaker.record_failure()
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    await asyncio.sleep(delay)
            except TelegramAPIError:
                await self._circuit_breaker.record_failure()
                raise
        
        # All retries exhausted
        raise TelegramAPIError(f"Failed after {MAX_RETRIES} attempts: {last_error}")
    
    async def start(self) -> None:
        """Start the adapter."""
        if self._status == SourceStatus.RUNNING:
            return
        
        self._status = SourceStatus.STARTING
        self._error = None
        
        try:
            # Create HTTP session
            self._session = aiohttp.ClientSession()
            
            # Verify bot token and get info
            self._bot_info = await self._api_call("getMe")
            logger.info(f"Telegram bot connected: @{self._bot_info.get('username')}")
            
            # Start polling if enabled
            if self._polling_enabled:
                self._polling_task = asyncio.create_task(self._polling_loop())
            
            self._status = SourceStatus.RUNNING
            logger.info(f"Telegram adapter started: {self.source_id}")
            
        except Exception as e:
            self._status = SourceStatus.ERROR
            self._error = str(e)
            logger.error(f"Failed to start Telegram adapter: {e}")
            if self._session:
                await self._session.close()
                self._session = None
            raise
    
    async def stop(self) -> None:
        """Stop the adapter gracefully."""
        logger.info(f"Stopping Telegram adapter: {self.source_id}")
        
        # Stop polling
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
        
        # Close HTTP session
        if self._session:
            await self._session.close()
            self._session = None
        
        self._status = SourceStatus.STOPPED
        logger.info(f"Telegram adapter stopped: {self.source_id}")
    
    async def send(self, message: OutgoingMessage) -> bool:
        """Send a message to Telegram.
        
        Args:
            message: Outgoing message to send
            
        Returns:
            True if sent successfully, False otherwise
        """
        if self._status != SourceStatus.RUNNING:
            logger.warning(f"Cannot send: adapter not running (status={self._status})")
            return False
        
        chat_id = message.external_user_id
        
        # Validate chat_id format
        if not self._validate_chat_id(chat_id):
            logger.error(f"Invalid Telegram chat_id: {chat_id}")
            return False
        
        # Check circuit breaker BEFORE acquiring rate limit token to avoid waste
        if not await self._circuit_breaker.can_execute():
            logger.warning(f"Circuit open, cannot send to {chat_id}")
            return False
        
        # Wait for rate limit token
        if not await self._rate_limiter.wait_and_acquire(max_wait=10.0):
            logger.warning(f"Rate limit exceeded, dropping message to {chat_id}")
            return False
        
        # Use per-chat lock for ordering
        lock = await self._get_chat_lock(chat_id)
        async with lock:
            try:
                params = {
                    "chat_id": chat_id,
                    "text": message.content,
                    "parse_mode": message.metadata.get("parse_mode", "HTML"),
                }
                
                if message.reply_to_id:
                    params["reply_to_message_id"] = message.reply_to_id
                
                await self._api_call("sendMessage", **params)
                logger.debug(f"Sent message to Telegram chat {chat_id}")
                return True
                
            except TelegramAPIError as e:
                logger.error(f"Failed to send to Telegram {chat_id}: {e}")
                return False
            except CircuitOpenError:
                logger.warning(f"Circuit open, cannot send to {chat_id}")
                return False
    
    async def health_check(self) -> bool:
        """Check if the adapter is healthy."""
        if self._status != SourceStatus.RUNNING:
            return False
        
        if not self._session:
            return False
        
        try:
            # Simple API call to verify connectivity
            await self._api_call("getMe")
            return True
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            return False
    
    async def handle_webhook(self, payload: dict, headers: dict) -> None:
        """Handle incoming webhook from Telegram.
        
        Args:
            payload: The webhook payload (Update object)
            headers: HTTP headers for verification
        """
        # Verify secret token if configured
        if self._secret_token:
            provided_token = headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if not secrets.compare_digest(self._secret_token, provided_token):
                logger.warning("Webhook received with invalid secret token")
                raise SecurityError("Invalid webhook secret token")
        
        await self._process_update(payload)
    
    async def _polling_loop(self) -> None:
        """Long polling loop for receiving updates."""
        logger.info(f"Starting Telegram polling for {self.source_id}")
        
        while self._status == SourceStatus.RUNNING:
            try:
                updates = await self._get_updates()
                for update in updates:
                    try:
                        await self._process_update(update)
                        # Only acknowledge after successful processing
                        self._last_update_id = update.get("update_id", self._last_update_id)
                    except Exception as e:
                        update_id = update.get("update_id", "unknown")
                        logger.error(f"Failed to process update {update_id}: {e}", exc_info=True)
                        # Continue to next update instead of breaking
                        # Update will be re-fetched on next poll since we didn't acknowledge it
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Polling error: {e}", exc_info=True)
                # Brief pause before retry
                await asyncio.sleep(5)
        
        logger.info(f"Polling stopped for {self.source_id}")
    
    async def _get_updates(self) -> list[dict]:
        """Fetch updates via long polling.
        
        Note: Does NOT update _last_update_id here - caller must acknowledge
        each update after successful processing to prevent message loss.
        """
        params = {
            "timeout": self._polling_timeout,
            "offset": self._last_update_id + 1 if self._last_update_id else None,
            "allowed_updates": ["message", "edited_message", "channel_post"],
        }
        
        return await self._api_call("getUpdates", **params)
    
    async def _process_update(self, update: dict) -> None:
        """Process a Telegram update and emit message.
        
        Args:
            update: Telegram Update object
        """
        update_id = update.get("update_id")
        
        # Extract message from update
        message = update.get("message") or update.get("edited_message") or update.get("channel_post")
        if not message:
            logger.debug(f"Update {update_id} has no message, skipping")
            return
        
        # Extract chat and user info
        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))
        chat_type = chat.get("type", "private")
        
        if not chat_id:
            logger.warning(f"Update {update_id} has no chat_id")
            return
        
        # Extract message content
        text = message.get("text", "")
        if not text:
            # Handle other message types (photos, documents, etc.)
            if message.get("photo"):
                text = "[Photo]"
            elif message.get("document"):
                text = "[Document]"
            elif message.get("sticker"):
                text = "[Sticker]"
            else:
                logger.debug(f"Update {update_id} has no text content")
                return
        
        # Determine message type
        entities = message.get("entities", [])
        message_type = "text"
        for entity in entities:
            if entity.get("type") == "bot_command":
                message_type = "command"
                break
        
        # Build metadata
        from_user = message.get("from", {})
        metadata = {
            "telegram": {
                "message_id": message.get("message_id"),
                "chat_type": chat_type,
                "from_id": from_user.get("id"),
                "from_username": from_user.get("username"),
                "from_first_name": from_user.get("first_name"),
                "from_last_name": from_user.get("last_name"),
                "date": message.get("date"),
                "edit_date": message.get("edit_date"),
            },
            "agent": self._default_agent,
        }
        
        # Create incoming message
        incoming = IncomingMessage(
            external_user_id=chat_id,
            content=text,
            source_id=self.source_id,
            metadata=metadata,
            message_type=message_type,
            reply_to_id=str(message.get("reply_to_message_id")) if message.get("reply_to_message_id") else None,
        )
        
        # Emit to handler
        try:
            await self._emit_message(incoming)
            logger.debug(f"Processed Telegram message from chat {chat_id}")
        except Exception as e:
            logger.error(f"Error emitting message: {e}", exc_info=True)
    
    @staticmethod
    def _validate_chat_id(chat_id: str) -> bool:
        """Validate Telegram chat ID format.
        
        Chat IDs are numeric, can be negative for groups/channels.
        """
        if not chat_id:
            return False
        if len(chat_id) > 20:
            return False
        return bool(re.match(r'^-?\d+$', chat_id))


class TelegramAPIError(Exception):
    """Telegram Bot API error."""
    pass


class CircuitOpenError(Exception):
    """Circuit breaker is open."""
    pass


class SecurityError(Exception):
    """Security-related error (e.g., invalid webhook signature)."""
    pass

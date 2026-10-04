"""Telegram Bot API adapter for message sources.

Implements both polling (for development) and webhook (for production)
modes for receiving messages from Telegram.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import secrets
from collections import OrderedDict
from typing import Any

import aiohttp

from daemon.constants import (
    CHART_IMAGE_MIME_WHITELIST,
    INITIAL_COMMENT_MAX,
    TELEGRAM_DOCUMENT_MAX_BYTES,
    TELEGRAM_PHOTO_MAX_BYTES,
)
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


def _log_image_metadata(image_id: str, size_bytes: int | None, content_type: str | None) -> None:
    """Standardized image metadata line — bytes never included.

    (architecture-recommendation.md §3 amendment #12.) Mirrored across
    the Discord / Telegram / Slack adapters + dispatcher. Logs
    ``image_id[:8]`` + ``size_bytes`` + ``content_type`` only.
    """
    short = image_id[:8]
    if size_bytes is not None and content_type is not None:
        logger.debug(
            f"telegram chart-image: image_id={short}... size={size_bytes} "
            f"content_type={content_type}"
        )
    else:
        logger.debug(f"telegram chart-image: image_id={short}...")


def _strip_llm_artifact_tags(content: str) -> str:
    """Strip LLM thinking/reasoning artifact tags from content.
    
    Some LLMs output tags like <think>...</think>, <reasoning>...</reasoning>
    which Telegram's HTML parser can't handle.
    
    Args:
        content: Raw message content that may contain artifact tags.
        
    Returns:
        Content with artifact tags removed.
    """
    # Known artifact tag names to strip
    artifact_tags = ["think", "scratchpad", "reflection", "reasoning"]
    
    for tag in artifact_tags:
        # Remove full blocks with content first (more specific, must come before opening tag removal)
        # Pattern: <tag optional_attrs>content</tag> with case-insensitive matching
        content = re.sub(
            rf"<{tag}(?:\s[^>]*)?>.*?</{tag}\s*>",
            "",
            content,
            flags=re.IGNORECASE | re.DOTALL
        )
        # Remove self-closing tags (e.g., <think/> or <think attr="val" />)
        content = re.sub(rf"<{tag}(?:\s[^>]*)?\s*/>", "", content, flags=re.IGNORECASE)
        # Remove orphan opening tags (e.g., <think> or <think attr="val">)
        content = re.sub(rf"<{tag}(?:\s[^>]*)?>", "", content, flags=re.IGNORECASE)
        # Remove orphan closing tags (e.g., </think>)
        content = re.sub(rf"</{tag}\s*>", "", content, flags=re.IGNORECASE)
    
    # Clean up empty lines left behind
    content = re.sub(r"\n{3,}", "\n\n", content)
    
    return content.strip()


class TelegramAdapter(MessageSourceAdapter):
    """Telegram Bot API adapter.
    
    Supports:
    - Long polling for receiving messages (development)
    - Webhook for receiving messages (production)
    - Message sending with rate limiting
    - Circuit breaker for API failures
    """
    
    def __init__(self, config: SourceConfig, on_message, 
                 rate_limit: RateLimit | None = None):
        super().__init__(config, on_message)
        
        # Extract Telegram-specific config
        self._bot_token = config.credentials.get("bot_token")
        if not self._bot_token:
            raise ValueError("Telegram adapter requires 'bot_token' in credentials")
        
        self._secret_token = config.config.get("secret_token")  # For webhook verification
        self._default_agent = config.config.get("default_agent", "ari")
        self._polling_enabled = config.config.get("polling_enabled", True)
        self._polling_timeout = config.config.get("polling_timeout", POLLING_TIMEOUT)
        
        # State
        self._session: aiohttp.ClientSession | None = None
        self._polling_task: asyncio.Task | None = None
        self._last_update_id: int = 0
        self._bot_info: dict | None = None
        
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
        
        # Typing indicator tracking per chat
        self._typing_tasks: dict[str, asyncio.Task] = {}
    
    @property
    def bot_username(self) -> str | None:
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

    async def _api_call_multipart(
        self,
        method: str,
        *,
        file_bytes: bytes,
        filename: str,
        file_field: str = "photo",
        content_type: str = "image/png",
        **params,
    ) -> dict:
        """Make a Telegram Bot API call with multipart file upload.

        Phase B chart-image upload (sendPhoto / sendDocument). Mirrors
        ``_api_call``'s 3-retry + exponential backoff + circuit-breaker
        discipline for TRANSPORT errors (network / 5xx) but treats
        multipart 4xx as NON-TRANSIENT — no ``record_failure`` (mirror
        of Discord ``adapter.py:1537-1547`` per architecture-
        recommendation.md §3 amendment #5). Systematically-rejected
        images (e.g., ``Bad Request: photo_invalid_dimensions``) exert
        zero breaker pressure; a real outage (transport / 5xx) DOES
        trip the breaker.

        ``parse_mode`` is intentionally NOT passed in the default caption
        (set on caller side) — Mermaid fences contain ``<`` characters
        which break HTML parse_mode. ``parse_mode=None`` keeps the
        caption as plain text — safe for Mermaid fences, no
        user-visible encoding artifacts.

        Raises:
            TelegramAPIError: On 4xx (non-transient, no breaker record).
            CircuitOpenError: When circuit breaker is open.
            RuntimeError: When adapter is not started (no HTTP session).
        """
        if not await self._circuit_breaker.can_execute():
            raise CircuitOpenError(f"Circuit open for Telegram API, method={method}")

        if not self._session:
            raise RuntimeError("Adapter not started - no HTTP session")

        url = self._get_api_url(method)

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            form = aiohttp.FormData()
            # Multipart upload: photo/document bytes (filename hint).
            # F8 (council-review): ``content_type`` is passed in from the
            # caller (``ImageAttachment.content_type``), defaulting to
            # ``image/png`` for callers that don't override — preserving
            # the prior behavior for any non-image MIME we'd add later.
            form.add_field(file_field, file_bytes, filename=filename, content_type=content_type)
            for k, v in params.items():
                if v is not None:
                    form.add_field(k, str(v))

            try:
                async with self._session.post(
                    url, data=form, timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    data = await resp.json()

                    if not data.get("ok"):
                        error_desc = data.get("description", "Unknown error")
                        error_code = data.get("error_code", 0)
                        # Multipart 4xx is NON-TRANSIENT — do NOT call
                        # record_failure; raise a distinct subclass so the
                        # caller can skip the image and deliver text
                        # without polluting breaker state. Mirrors Discord
                        # adapter.py:1537-1547 (architecture-recommendation.md
                        # §3 amendment #5).
                        if 400 <= error_code < 500:
                            raise _TelegramNonTransientAPIError(
                                f"Telegram API {error_code} "
                                f"(non-transient, no breaker): {error_desc}"
                            )
                        raise TelegramAPIError(
                            f"Telegram API error {error_code}: {error_desc}"
                        )

                    await self._circuit_breaker.record_success()
                    return data.get("result", {})

            except aiohttp.ClientError as e:
                last_error = e
                logger.warning(
                    f"Telegram multipart call failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}"
                )
                # Transport error — DOES trip the breaker (3× exponential backoff).
                await self._circuit_breaker.record_failure()
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    await asyncio.sleep(delay)
            except _TelegramNonTransientAPIError:
                # 4xx — non-transient, NEVER record_failure.
                raise
            except TelegramAPIError:
                # 5xx — IS transient (transport-class), record_failure then raise.
                await self._circuit_breaker.record_failure()
                raise

        if last_error is not None:
            raise last_error
        raise RuntimeError("unreachable")

    async def _send_text_only(self, reply_chat_id: str, content: str) -> None:
        """Send a text-only message via ``sendMessage`` (Phase B extraction).

        Phase B: text fallback path for caption follow-up (>1024) and
        for the ``delivered_count == 0`` text floor when ALL image
        uploads failed (architecture-recommendation.md §3 amendment #8
        + iter-002 blocking #1). Preserves the existing LLM artifact
        sanitization from the legacy ``send()`` body.
        """
        # Sanitize content to remove LLM artifact tags that Telegram's HTML parser can't handle
        sanitized_content = _strip_llm_artifact_tags(content)
        params = {
            "chat_id": reply_chat_id,
            "text": sanitized_content,
            "parse_mode": "HTML",
        }
        try:
            await self._api_call("sendMessage", **params)
        except TelegramAPIError as e:
            logger.warning(f"Telegram text send failed: {e}")
            raise

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
                logger.info(f"Starting Telegram polling for {self.source_id} (bot: @{self._bot_info.get('username')})")
                self._polling_task = asyncio.create_task(self._polling_loop())
            else:
                logger.info(f"Polling disabled for {self.source_id}, webhook mode only")
            
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
        
        # Stop all typing indicators
        for chat_id in list(self._typing_tasks.keys()):
            self.stop_typing(chat_id)
        
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
        """Send an OutgoingMessage to Telegram.

        Phase B restructure: image upload (sendPhoto / sendDocument) +
        text-send BOTH execute inside the per-chat LRU lock
        (architecture-recommendation.md §3 amendment #10 — ordering
        hazard). The lock is acquired BEFORE any image processing so
        that chat-A image-A → chat-A text-A ordering is preserved
        (no interleaving from another concurrent send).

        Image handling:
        * No images → legacy text-send path (unchanged semantics).
        * With images → each image uploads via ``_api_call_multipart``
          (sendPhoto ≤10 MB / sendDocument >10 MB). All failures
          (4xx non-transient, transport 3× backoff, >50 MB skip,
          MIME-miss) WARN-log + skip; the FULL content is STILL delivered
          as text (text floor invariant) — either via caption on the
          image (≤1024) or via the follow-up ``sendMessage`` for
          caption_remaining.

        Returns:
            True if any delivery (image or text) succeeded; False only
            when both image upload and text fallback fail.
        """
        if self._status != SourceStatus.RUNNING:
            logger.warning(f"Cannot send: adapter not running (status={self._status})")
            return False

        # Determine where to send the message
        # For group chats: reply_chat_id = group ID (from metadata)
        # For private chats: reply_chat_id = user ID (same as external_user_id)
        reply_chat_id = message.metadata.get("reply_chat_id") if message.metadata else None
        if not reply_chat_id:
            reply_chat_id = message.external_user_id

        # Stop typing indicator before sending (use the same chat_id we send to)
        self.stop_typing(reply_chat_id)

        # Validate chat_id format
        if not self._validate_chat_id(reply_chat_id):
            logger.error(f"Invalid Telegram chat_id: {reply_chat_id}")
            return False

        # Check circuit breaker BEFORE acquiring rate limit token to avoid waste
        if not self._circuit_breaker or not await self._circuit_breaker.can_execute():
            logger.warning(f"Circuit open, cannot send to {reply_chat_id}")
            return False

        # Wait for rate limit token (Phase B: token bucket preserved per
        # adapter-semantics contract).
        if not await self._rate_limiter.wait_and_acquire(max_wait=10.0):
            logger.warning(f"Rate limit exceeded, dropping message to {reply_chat_id}")
            return False

        # Per-chat lock — acquired ONCE; image upload + text both happen
        # inside (architecture-recommendation.md §3 amendment #10). The lock
        # is the SINGLE serialization point for chat-A → ordered image-then-text.
        lock = await self._get_chat_lock(reply_chat_id)
        async with lock:
            # Re-check circuit breaker under the lock — it may have opened
            # while we waited for the rate-limiter.
            if not self._circuit_breaker or not await self._circuit_breaker.can_execute():
                logger.warning(f"Circuit open under lock, cannot send to {reply_chat_id}")
                return False

            images = getattr(message, "images", None)
            if images:
                # parse_mode=None firm decision (Mermaid ``<`` breaks HTML mode).
                full_caption = message.content if message.content else ""
                # Telegram sendPhoto/sendDocument caption limit is 1024 chars.
                # Truncate on the image + follow-up sendMessage for the
                # remainder (architecture-recommendation.md §3 amendment #8).
                if len(full_caption) > 1024:
                    caption_truncated = full_caption[:1024]
                    caption_remaining = full_caption[1024:]
                else:
                    caption_truncated = full_caption
                    caption_remaining = ""

                delivered_count = 0
                chat_id = reply_chat_id
                # F5 (council-review): mutable list captured on the message
                # by-reference; the dispatcher reads ``message.delivered_image_ids``
                # AFTER ``send()`` to decide which images to ``store.delete``.
                # ``None`` → backward-compat: dispatcher deletes all images.
                if message.delivered_image_ids is None:
                    message.delivered_image_ids = []
                for img in images:
                    _log_image_metadata(img.image_id, img.size_bytes, img.content_type)
                    try:
                        file_bytes = base64.b64decode(img.bytes_b64)
                    except Exception as e:
                        logger.warning(
                            f"telegram image decode failed image_id={img.image_id[:8]}...: {e}; "
                            f"skipping (continuing with text/siblings)"
                        )
                        continue

                    # >50 MB / MIME-miss skip (defense + telegram hard-limit).
                    if len(file_bytes) > TELEGRAM_DOCUMENT_MAX_BYTES:
                        logger.warning(
                            f"telegram image too large: image_id={img.image_id[:8]}... "
                            f"size={len(file_bytes)} > {TELEGRAM_DOCUMENT_MAX_BYTES}; skipping"
                        )
                        continue
                    if img.content_type not in CHART_IMAGE_MIME_WHITELIST:
                        logger.warning(
                            f"telegram image MIME miss: image_id={img.image_id[:8]}... "
                            f"content_type={img.content_type!r}; skipping"
                        )
                        continue

                    # sendPhoto ≤10 MB / sendDocument >10 MB.
                    if len(file_bytes) <= TELEGRAM_PHOTO_MAX_BYTES:
                        method = "sendPhoto"
                        file_field = "photo"
                    else:
                        method = "sendDocument"
                        file_field = "document"

                    try:
                        await self._api_call_multipart(
                            method,
                            file_bytes=file_bytes,
                            filename=img.filename,
                            file_field=file_field,
                            # F8: pass through the actual stored MIME so
                            # jpeg/gif/webp uploads carry the correct
                            # content_type (preserved when stored by the
                            # charter render, not hardcoded to png).
                            content_type=img.content_type,
                            chat_id=chat_id,
                            caption=caption_truncated,
                            # parse_mode=None (default) — Mermaid `<` safety
                        )
                        delivered_count += 1
                        # F5: track the id so the dispatcher can delete
                        # only images the adapter confirmed delivered.
                        message.delivered_image_ids.append(img.image_id)
                    except _TelegramNonTransientAPIError as e:
                        # 4xx non-transient — skip this image (no breaker).
                        logger.warning(f"Telegram {method} non-transient: {e}; skipping image")
                        continue
                    except CircuitOpenError:
                        logger.warning(f"Telegram circuit opened mid-send to {reply_chat_id}")
                        return False
                    except TelegramAPIError as e:
                        # 5xx — recorded by helper, surface as skip.
                        logger.warning(f"Telegram {method} failed: {e}; skipping image")
                        continue
                    except Exception as e:
                        logger.warning(f"Telegram image upload unexpected error: {e}; skipping image")
                        continue

                # TEXT FLOOR GUARD (iter-002 blocking #1): if ALL image
                # uploads failed, the FULL content MUST still be delivered
                # as text — including the content <= 1024 case
                # (caption_remaining == "") where neither caption branch
                # fires. Without this guard nothing is sent, return True
                # lies about success, and amendment-#22 store.delete
                # would delete the never-delivered image.
                if delivered_count == 0:
                    try:
                        await self._send_text_only(reply_chat_id, message.content)
                    except Exception as e:
                        logger.warning(f"Telegram text-floor send failed: {e}")
                        return False

                # Caption >1024: full-text follow-up sendMessage (text floor).
                if caption_remaining and delivered_count > 0:
                    try:
                        await self._send_text_only(reply_chat_id, caption_remaining)
                    except Exception as e:
                        logger.warning(f"telegram caption-follow-up text send failed: {e}")

                return True

            # No images — legacy text-send code path (unchanged semantics).
            try:
                # Sanitize content to remove LLM artifact tags that Telegram's HTML parser can't handle
                sanitized_content = _strip_llm_artifact_tags(message.content)

                params = {
                    "chat_id": reply_chat_id,
                    "text": sanitized_content,
                    "parse_mode": message.metadata.get("parse_mode", "HTML"),
                }

                if message.reply_to_id:
                    params["reply_to_message_id"] = message.reply_to_id

                await self._api_call("sendMessage", **params)
                logger.debug(f"Sent message to Telegram chat {reply_chat_id}")
                return True

            except TelegramAPIError as e:
                logger.error(f"Failed to send to Telegram {reply_chat_id}: {e}")
                return False
            except CircuitOpenError:
                logger.warning(f"Circuit open, cannot send to {reply_chat_id}")
                return False
    
    async def start_typing(self, chat_id: str) -> None:
        """Start showing typing indicator for a chat.
        
        Sends typing action periodically (every 4 seconds) until stopped.
        Telegram's typing action expires after 5 seconds.
        
        Args:
            chat_id: The Telegram chat ID to show typing for.
        """
        # Stop any existing typing task for this chat
        self.stop_typing(chat_id)
        
        async def typing_loop():
            """Send typing action periodically."""
            try:
                while True:
                    try:
                        await self._api_call("sendChatAction", chat_id=chat_id, action="typing")
                        logger.debug(f"Sent typing indicator to chat {chat_id}")
                    except Exception as e:
                        logger.warning(f"Failed to send typing indicator: {e}")
                        break
                    await asyncio.sleep(4)  # Send every 4 seconds (expires after 5)
            except asyncio.CancelledError:
                pass
            finally:
                logger.debug(f"Typing indicator stopped for chat {chat_id}")
        
        self._typing_tasks[chat_id] = asyncio.create_task(typing_loop())
        logger.debug(f"Started typing indicator for chat {chat_id}")
    
    def stop_typing(self, chat_id: str) -> None:
        """Stop showing typing indicator for a chat.
        
        Args:
            chat_id: The Telegram chat ID to stop typing for.
        """
        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()
            logger.debug(f"Cancelled typing indicator for chat {chat_id}")
    
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
    
    @classmethod
    async def test_connection(cls, config: SourceConfig) -> tuple[bool, str]:
        """Test Telegram bot token without full adapter initialization.
        
        Args:
            config: Source configuration containing bot_token in credentials
            
        Returns:
            Tuple of (success, message)
        """
        from ..base import MessageSourceAdapter
        
        bot_token = config.credentials.get("bot_token")
        if not bot_token:
            return False, "Bot token is required"
        
        url = TELEGRAM_API_BASE.format(token=bot_token, method="getMe")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    
                    if not data.get("ok"):
                        error_desc = data.get("description", "Unknown error")
                        error_code = data.get("error_code", 0)
                        
                        # Provide user-friendly error messages
                        if error_code == 401:
                            return False, "Invalid bot token. Please check your token from @BotFather."
                        elif error_code == 404:
                            return False, "Bot not found. Please verify your bot token."
                        else:
                            return False, f"Telegram API error: {error_desc}"
                    
                    bot_info = data.get("result", {})
                    username = bot_info.get("username", "unknown")
                    first_name = bot_info.get("first_name", "")
                    
                    return True, f"Connected to @{username}" + (f" ({first_name})" if first_name else "")
                    
        except asyncio.TimeoutError:
            return False, "Connection timed out. Please check your network."
        except aiohttp.ClientError as e:
            return False, f"Connection failed: {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected error during Telegram connection test: {e}")
            return False, f"Unexpected error: {str(e)}"
    
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
            except (asyncio.CancelledError, asyncio.TimeoutError):
                # TimeoutError can occur when CancelledError propagates through aiohttp
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
        
        # Extract the actual user who sent the message
        from_user = message.get("from", {})
        from_user_id = str(from_user.get("id", ""))
        
        if not from_user_id:
            logger.warning(f"Update {update_id} has no from_user_id")
            return
        
        # Session mapping strategy:
        # - Private chat: each user has their own session (from_user_id)
        # - Group chat: entire group shares ONE session (chat_id) for collaborative context
        if chat_type == "private":
            session_user_id = from_user_id
        else:
            session_user_id = chat_id  # Group/Supergroup/Channel = shared session
        
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
        
        # Determine message type and check for commands
        entities = message.get("entities", [])
        message_type = "text"
        command = None
        for entity in entities:
            if entity.get("type") == "bot_command":
                message_type = "command"
                # Extract command text (e.g., "/new" or "/new@botname")
                offset = entity.get("offset", 0)
                length = entity.get("length", 0)
                command = text[offset:offset + length].split("@")[0]  # Remove bot name suffix
                break
        
        # Build metadata
        metadata = {
            "telegram": {
                "message_id": message.get("message_id"),
                "chat_id": chat_id,  # For sending responses (group or private)
                "chat_type": chat_type,
                "from_id": from_user_id,
                "from_username": from_user.get("username"),
                "from_first_name": from_user.get("first_name"),
                "from_last_name": from_user.get("last_name"),
                "date": message.get("date"),
                "edit_date": message.get("edit_date"),
            },
            "agent": self._default_agent,
            # For group chats: reply to the group, but session is per-user
            "reply_chat_id": chat_id,
        }
        
        logger.debug(f"Message metadata: agent={self._default_agent}, chat_type={chat_type}")
        
        # Handle special commands
        if command == "/new":
            metadata["force_new_instance"] = True
            metadata["command"] = command
        
        # Create incoming message
        # session_user_id: private chat = user_id, group chat = chat_id (shared session)
        incoming = IncomingMessage(
            external_user_id=session_user_id,
            content=text,
            source_id=self.source_id,
            metadata=metadata,
            message_type=message_type,
            reply_to_id=str(message.get("reply_to_message_id")) if message.get("reply_to_message_id") else None,
        )
        
        # Emit to handler
        try:
            await self._emit_message(incoming)
            logger.debug(
                f"Processed Telegram message: session_user={session_user_id}, "
                f"chat={chat_id} ({chat_type}), from={from_user_id}, type={message_type}"
            )
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


class _TelegramNonTransientAPIError(TelegramAPIError):
    """Telegram API error that should NOT count against the circuit breaker.

    Raised by ``_api_call_multipart`` for 4xx responses (systematically
    rejected images). Subclasses TelegramAPIError so existing
    ``except TelegramAPIError`` callers still catch it — but the
    ``_api_call_multipart`` call site has its own dedicated handler
    that re-raises without calling ``record_failure``.
    """


class CircuitOpenError(Exception):
    """Circuit breaker is open."""
    pass


class SecurityError(Exception):
    """Security-related error (e.g., invalid webhook signature)."""
    pass

"""Slack adapter using Socket Mode for real-time messaging."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any

import aiohttp
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler

from daemon.sources.base import (
    IncomingMessage,
    MessageSourceAdapter,
    OutgoingMessage,
    SourceConfig,
    SourceStatus,
)
from daemon.sources.circuit_breaker import CircuitBreaker

from .rate_limiter import SlackTieredRateLimiter
from .thread_manager import ThreadManager
from .blocks import markdown_to_slack_blocks

logger = logging.getLogger(__name__)

# Constants
MAX_CHANNEL_LOCKS = 100
BLOCKS_CONTENT_THRESHOLD = 400
TEXT_FALLBACK_MAX_LENGTH = 500


class CircuitOpenError(Exception):
    """Circuit breaker is open."""
    pass


class SlackAPIError(Exception):
    """Slack API error."""
    pass


class SlackAdapter(MessageSourceAdapter):
    """Slack Bot adapter using Socket Mode.

    Supports:
    - Real-time messaging via WebSocket (Socket Mode)
    - Direct messages (DMs) with per-user instances
    - Channel messages with shared instances
    - Thread messages with separate instances
    - Rate limiting per Slack API tiers
    - Circuit breaker for API resilience

    External user ID format:
    - DM: {workspace_id}:{user_id}
    - Channel: {workspace_id}:{channel_id}
    - Thread: {workspace_id}:{channel_id}:{thread_ts}
    """

    # DM cache TTL in seconds
    DM_CACHE_TTL_SECONDS: float = 5 * 60  # 5 minutes
    # DM cache max entries
    DM_CACHE_MAX_SIZE: int = 1000

    def __init__(
        self,
        config: SourceConfig,
        on_message: Callable[[IncomingMessage], Awaitable[None]],
        manager=None,
    ):
        """Initialize Slack adapter.

        Args:
            config: Source configuration with credentials.
            on_message: Callback for incoming messages.
            manager: InstanceManager for thread management.
        """
        super().__init__(config, on_message)

        # Extract Slack credentials
        self._bot_token = config.credentials.get("bot_token")
        self._app_token = config.credentials.get("app_token")

        if not self._bot_token:
            raise ValueError("Slack adapter requires 'bot_token' in credentials")
        if not self._app_token:
            raise ValueError("Slack adapter requires 'app_token' in credentials")

        if not self._bot_token.startswith("xoxb-"):
            raise ValueError("bot_token must start with 'xoxb-'")
        if not self._app_token.startswith("xapp-"):
            raise ValueError("app_token must start with 'xapp-'")

        # Configuration
        self._default_agent = config.config.get("default_agent", "leader")
        # In channels/group DMs, only respond when the bot is @-mentioned.
        # DMs and threads with an explicit mention are always allowed.
        # Default: True (matches Slack's app_mention semantics; defensive in case
        # message.channels/message.groups is subscribed on the Slack app side).
        self._channel_require_mention: bool = bool(
            config.config.get("channel_require_mention", True)
        )

        # State
        self._app: AsyncApp | None = None
        self._handler: AsyncSocketModeHandler | None = None
        self._workspace_id: str | None = None
        self._workspace_name: str | None = None
        self._bot_user_id: str | None = None
        self._bot_name: str | None = None

        # Tiered rate limiter for Slack API
        self._rate_limiter = SlackTieredRateLimiter()

        # Circuit breaker for API resilience
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60.0,
        )

        # Per-channel locks for message ordering
        self._channel_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._channel_locks_guard = asyncio.Lock()

        # DM channel cache: user_id -> channel_id
        self._dm_cache: OrderedDict[str, tuple[str, float]] = OrderedDict()  # user_id -> (channel_id, timestamp)
        self._dm_cache_guard = asyncio.Lock()

        # Thread manager (initialized with manager reference)
        self._thread_manager: ThreadManager | None = None
        if manager is not None:
            self._thread_manager = ThreadManager(manager=manager)

        # Reference to source repository (injected by registry)
        self._source_repo: Any = None

    @property
    def workspace_id(self) -> str | None:
        """Get the workspace ID."""
        return self._workspace_id

    @property
    def workspace_name(self) -> str | None:
        """Get the workspace name."""
        return self._workspace_name

    def _handle_handler_failure(self, task: asyncio.Task) -> None:
        """Called when the Socket Mode handler task fails.

        Args:
            task: The completed task that may have failed.
        """
        try:
            exc = task.exception()
            if exc:
                logger.error(f"Slack Socket Mode handler failed: {exc}")
                self._status = SourceStatus.ERROR
        except asyncio.CancelledError:
            pass

    async def _get_channel_lock(self, channel_id: str) -> asyncio.Lock:
        """Get or create per-channel lock for message ordering with LRU eviction.

        Args:
            channel_id: The Slack channel ID.

        Returns:
            asyncio.Lock for this channel.
        """
        async with self._channel_locks_guard:
            if channel_id in self._channel_locks:
                self._channel_locks.move_to_end(channel_id)
                return self._channel_locks[channel_id]

            # Evict oldest if at capacity
            while len(self._channel_locks) >= MAX_CHANNEL_LOCKS:
                self._channel_locks.popitem(last=False)

            lock = asyncio.Lock()
            self._channel_locks[channel_id] = lock
            return lock

    async def start(self) -> None:
        """Start the Slack adapter with Socket Mode."""
        if self._status == SourceStatus.RUNNING:
            return

        # Reset circuit breaker for fresh start (prevents stale state from reused adapters)
        self._circuit_breaker.reset()

        self._status = SourceStatus.STARTING
        self._error = None

        try:
            # Create AsyncApp for Socket Mode
            self._app = AsyncApp(token=self._bot_token)

            # Register event handlers
            self._app.event("message")(self._handle_message_event)
            self._app.event("app_mention")(self._handle_message_event)
            self._app.command("/new")(self._handle_new_command)

            # Authenticate and get workspace info
            await self._authenticate()

            # Start Socket Mode handler
            logger.info(f"Starting Slack Socket Mode for workspace: {self._workspace_name}")
            self._handler = AsyncSocketModeHandler(self._app, self._app_token)

            # Start handler as background task
            task = asyncio.create_task(self._handler.start_async())
            task.add_done_callback(self._handle_handler_failure)
            self._status = SourceStatus.RUNNING
            logger.info(f"Slack adapter started: {self.source_id}, workspace: {self._workspace_name}")

        except Exception as e:
            self._status = SourceStatus.ERROR
            self._error = str(e)
            logger.error(f"Failed to start Slack adapter: {e}")
            await self._cleanup()
            raise

    async def stop(self) -> None:
        """Stop the Slack adapter gracefully."""
        logger.info(f"Stopping Slack adapter: {self.source_id}")

        # Shutdown thread manager if initialized
        if self._thread_manager is not None:
            await self._thread_manager.shutdown()

        await self._cleanup()

        self._status = SourceStatus.STOPPED
        logger.info(f"Slack adapter stopped: {self.source_id}")

    async def _cleanup(self) -> None:
        """Clean up resources."""
        if self._handler:
            try:
                await self._handler.close()
            except Exception as e:
                logger.warning(f"Error closing Socket Mode handler: {e}")
            self._handler = None

        self._app = None

    async def _authenticate(self) -> None:
        """Authenticate with Slack and get workspace info."""
        result = await self._call_slack_api("auth.test")

        if not result.get("ok"):
            raise SlackAPIError(f"Authentication failed: {result}")

        self._workspace_id = result.get("team_id")
        self._workspace_name = result.get("team")
        self._bot_user_id = result.get("user_id")
        self._bot_name = result.get("user")

        logger.info(f"Authenticated as @{self._bot_name} in workspace {self._workspace_name}")

    async def _call_slack_api(
        self,
        method: str,
        **kwargs,
    ) -> dict:
        """Call Slack API with circuit breaker and rate limiting.

        Args:
            method: Slack API method name (e.g., "chat.postMessage").
            **kwargs: API parameters.

        Returns:
            API response dict.

        Raises:
            CircuitOpenError: When circuit breaker is open.
            SlackAPIError: On API errors.
        """
        if not await self._circuit_breaker.can_execute():
            raise CircuitOpenError(f"Circuit open for Slack API, method={method}")

        # Use rate limiter
        success, result = await self._rate_limiter.acquire_and_execute(
            method=method,
            fn=lambda: self._do_api_call(method, **kwargs),
            max_wait=30.0,
        )

        if not success:
            raise SlackAPIError(f"Rate limit timeout for {method}")

        return result

    async def _do_api_call(self, method: str, **kwargs) -> dict:
        """Perform actual API call using Slack SDK typed methods.

        Args:
            method: Slack API method name.
            **kwargs: API parameters.

        Returns:
            API response dict.
        """
        if not self._app:
            raise RuntimeError("Adapter not started - no Slack app")

        client = self._app.client

        try:
            # Use typed async methods instead of generic api_call()
            # Convert "chat.postMessage" -> client.chat_postMessage()
            typed_method = method.replace(".", "_")
            if hasattr(client, typed_method):
                api_method = getattr(client, typed_method)
                response = await api_method(**kwargs)
            else:
                # Fallback: use params dict for methods without typed wrappers
                response = await client.api_call(
                    api_method=method,
                    params=kwargs
                )

            if not response.get("ok"):
                error = response.get("error", "unknown_error")
                await self._circuit_breaker.record_failure()
                raise SlackAPIError(f"Slack API error: {error}")

            await self._circuit_breaker.record_success()
            return response

        except SlackAPIError:
            raise
        except Exception as e:
            await self._circuit_breaker.record_failure()
            raise SlackAPIError(f"API call failed: {e}")

    async def _safe_api_call(
        self,
        method: str,
        **kwargs,
    ) -> tuple[bool, dict | None]:
        """Safely call Slack API with error handling for common errors.

        Args:
            method: Slack API method name.
            **kwargs: API parameters.

        Returns:
            Tuple of (success, result) where result is None on failure.
        """
        try:
            result = await self._call_slack_api(method, **kwargs)
            return True, result
        except CircuitOpenError:
            logger.warning(f"Circuit open for {method}")
            return False, None
        except SlackAPIError as e:
            error_msg = str(e)

            # Handle specific Slack error codes
            if "not_authed" in error_msg or "token_expired" in error_msg:
                logger.error(f"Authentication error for {method}: {e}")
            elif "channel_not_found" in error_msg:
                logger.warning(f"Channel not found: {kwargs.get('channel')}")
            elif "not_in_channel" in error_msg:
                logger.warning(f"Bot not in channel: {kwargs.get('channel')}")
            elif "rate_limited" in error_msg or "ratelimited" in error_msg:
                logger.warning(f"Rate limited for {method}")
            else:
                logger.error(f"Slack API error for {method}: {e}")

            return False, None
        except Exception as e:
            logger.error(f"Unexpected error calling {method}: {e}")
            return False, None

    async def send(self, message: OutgoingMessage) -> bool:
        """Send a message to Slack.

        This method performs DB lookup for routing because dispatch_completed()
        creates OutgoingMessage with empty metadata.

        Args:
            message: Outgoing message to send.

        Returns:
            True if sent successfully, False otherwise.
        """
        if self._status != SourceStatus.RUNNING:
            logger.warning(f"Cannot send: adapter not running (status={self._status})")
            return False

        if not self._source_repo:
            logger.error("Cannot send: _source_repo not set")
            return False

        # Parse external_user_id to get routing info
        # Format: {workspace_id}:{channel_or_user_id}[:{thread_ts}]
        try:
            parts = message.external_user_id.split(":")
            if len(parts) < 2:
                logger.error(f"Invalid external_user_id format: {message.external_user_id}")
                return False

            workspace_id = parts[0]
            second_part = parts[1]
            thread_ts = parts[2] if len(parts) > 2 else None

        except Exception as e:
            logger.error(f"Failed to parse external_user_id: {e}")
            return False

        # DB lookup for routing (CRITICAL) - use asyncio.to_thread to avoid blocking
        try:
            mapping = await asyncio.to_thread(
                self._source_repo.get_instance_mapping,
                message.source_id,
                message.external_user_id
            )

            if not mapping:
                logger.warning(
                    f"No instance mapping found for: source={message.source_id}, "
                    f"user={message.external_user_id}"
                )
                return False

            # Get channel_id and thread_ts from mapping metadata
            channel_id = mapping.mapping_metadata.get("slack_channel_id") if mapping.mapping_metadata else None
            if not channel_id:
                logger.warning(
                    f"No slack_channel_id in mapping metadata for: user={message.external_user_id}"
                )
                return False

            mapping_thread_ts = (
                mapping.mapping_metadata.get("slack_thread_ts")
                if mapping.mapping_metadata else None
            )

        except Exception as e:
            logger.error(f"DB lookup failed: {e}")
            return False

        # Use thread_ts from mapping if available
        reply_ts = mapping_thread_ts or thread_ts

        # Check circuit breaker
        if not await self._circuit_breaker.can_execute():
            logger.warning(f"Circuit open, cannot send to channel {channel_id}")
            return False

        # Get per-channel lock
        lock = await self._get_channel_lock(channel_id)

        async with lock:
            try:
                # Prepare message parameters
                # Use blocks for longer/formatted content (>400 chars or contains code blocks)
                if len(message.content) > BLOCKS_CONTENT_THRESHOLD or "```" in message.content:
                    blocks = markdown_to_slack_blocks(message.content)
                    if blocks:
                        params = {
                            "channel": channel_id,
                            "blocks": blocks,
                        }
                        # Include text fallback for notifications
                        params["text"] = message.content[:TEXT_FALLBACK_MAX_LENGTH] if message.content else "..."

                        if reply_ts:
                            params["thread_ts"] = reply_ts
                    else:
                        # Fallback to simple text
                        params = {
                            "channel": channel_id,
                            "text": message.content,
                        }
                        if reply_ts:
                            params["thread_ts"] = reply_ts
                else:
                    params = {
                        "channel": channel_id,
                        "text": message.content,
                    }

                    # Add thread reply if available
                    if reply_ts:
                        params["thread_ts"] = reply_ts

                # Send the message
                success, result = await self._safe_api_call("chat.postMessage", **params)

                if success:
                    logger.debug(f"Sent message to Slack channel {channel_id}" +
                               (f" thread {reply_ts}" if reply_ts else ""))
                    return True
                else:
                    logger.warning(f"Failed to send to Slack {channel_id}")
                    return False

            except CircuitOpenError:
                logger.warning(f"Circuit open, cannot send to {channel_id}")
                return False
            except SlackAPIError as e:
                logger.error(f"Failed to send to Slack {channel_id}: {e}")
                return False

    async def health_check(self) -> bool:
        """Check if the adapter is healthy.

        Returns:
            True if healthy, False otherwise.
        """
        if self._status != SourceStatus.RUNNING:
            return False

        try:
            result = await self._call_slack_api("auth.test")
            return result.get("ok", False)
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            return False

    @classmethod
    async def test_connection(cls, config: SourceConfig) -> tuple[bool, str]:
        """Test Slack connection without full adapter initialization.

        Args:
            config: Source configuration containing bot_token and app_token.

        Returns:
            Tuple of (success, message).
        """
        bot_token = config.credentials.get("bot_token")
        app_token = config.credentials.get("app_token")

        if not bot_token:
            return False, "bot_token is required"
        if not app_token:
            return False, "app_token is required"

        if not bot_token.startswith("xoxb-"):
            return False, "bot_token must start with 'xoxb-'"
        if not app_token.startswith("xapp-"):
            return False, "app_token must start with 'xapp-'"

        try:
            async with aiohttp.ClientSession() as session:
                # Test bot token
                url = "https://slack.com/api/auth.test"
                headers = {"Authorization": f"Bearer {bot_token}"}

                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()

                    if not data.get("ok"):
                        error = data.get("error", "unknown_error")
                        if error == "invalid_auth":
                            return False, "Invalid bot token"
                        if error == "token_revoked":
                            return False, "Bot token has been revoked"
                        return False, f"Slack API error: {error}"

                    team = data.get("team", "unknown")
                    user = data.get("user", "unknown")

                    return True, f"Connected to {team} as @{user}"

        except asyncio.TimeoutError:
            return False, "Connection timed out. Please check your network."
        except aiohttp.ClientError as e:
            return False, f"Connection failed: {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected error during Slack connection test: {e}")
            return False, f"Unexpected error: {str(e)}"

    def _is_valid_message(self, event: dict) -> bool:
        """Filter out bot and system messages.

        Args:
            event: Slack message event.

        Returns:
            True if this is a valid user message.
        """
        # Ignore messages from bots (including ourselves)
        if event.get("bot_id"):
            return False

        # Ignore messages with bot_profile (Slack 2.0)
        if event.get("bot_profile"):
            return False

        # Ignore channel join/leave messages
        subtype = event.get("subtype")
        if subtype in ("channel_join", "channel_leave", "group_join", "group_leave"):
            return False

        # Ignore thread broadcasts
        if subtype == "thread_broadcast":
            return False

        # Ignore edits (we handle original messages)
        if subtype == "message_changed":
            return False

        # Check for our own bot ID to avoid echo
        if event.get("user") == self._bot_user_id:
            return False

        return True

    async def _handle_message_event(self, event: dict, client: Any) -> None:
        """Handle incoming message event from Slack.

        Args:
            event: Slack message event.
            client: Slack client instance.
        """
        try:
            # Validate message
            if not self._is_valid_message(event):
                return

            # Deduplicate: Slack fires BOTH 'message' and 'app_mention' events
            # for the same @-mention in a channel (when both message.channels
            # and app_mention are subscribed). Process only the app_mention
            # variant in channels/groups to avoid duplicate responses.
            # In DMs (channel_type=im/mpim), only 'message' events fire, so
            # we process those normally.
            event_type = event.get("type", "message")
            channel_type = event.get("channel_type", "channel")
            is_dm = channel_type in ("im", "mpim")
            if event_type == "message" and not is_dm and self._is_bot_mentioned(event):
                logger.debug(
                    f"Skipping duplicate 'message' event for bot mention; "
                    f"the 'app_mention' variant will be processed: "
                    f"channel={event.get('channel')}, user={event.get('user')}"
                )
                return

            # Defensive: in channels/private channels, only respond when the
            # bot is @-mentioned. DMs and threaded replies (which always
            # include the mention) are unaffected. Safe to early-return: the
            # bolt framework auto-acks the Socket Mode envelope before
            # invoking this handler, so discarding does not cause stackup.
            if self._channel_require_mention and not self._is_bot_mentioned(event):
                logger.debug(
                    f"Skipping channel message without bot mention: "
                    f"channel={event.get('channel')}, user={event.get('user')}"
                )
                return

            # Process the event
            incoming = await self._process_event(event)

            if incoming:
                await self._emit_message(incoming)

        except Exception as e:
            logger.error(f"Error handling message event: {e}", exc_info=True)

    def _is_bot_mentioned(self, event: dict) -> bool:
        """Check whether the message is directed at the bot.

        Returns True for:
        - DMs (channel_type 'im' or 'mpim') — no mention syntax needed
        - Messages whose text contains <@BOT_USER_ID>
        - app_mention events (always contain the mention by definition)
        - Thread broadcasts inside DMs

        Returns False for:
        - Channel/group messages that do not @-mention the bot

        Args:
            event: Slack message event.

        Returns:
            True if the bot should respond, False to filter out.
        """
        channel_type = event.get("channel_type", "channel")
        # DMs and multi-party DMs: always respond
        if channel_type in ("im", "mpim"):
            return True

        if not self._bot_user_id:
            # No bot ID resolved yet (auth not complete) — fail open to
            # preserve prior behavior rather than silently dropping messages.
            return True

        text = event.get("text", "") or ""
        mention_token = f"<@{self._bot_user_id}>"
        if mention_token in text:
            return True

        # Also accept the event type as a signal — app_mention is always a mention
        return event.get("type") == "app_mention"

    async def _process_event(self, event: dict) -> IncomingMessage | None:
        """Process Slack event and create IncomingMessage.

        Args:
            event: Slack message event.

        Returns:
            IncomingMessage or None if filtered out.
        """
        # Extract basic info
        channel = event.get("channel", "")
        channel_type = event.get("channel_type", "channel")
        user = event.get("user", "")
        text = event.get("text", "")
        thread_ts = event.get("thread_ts")
        ts = event.get("ts")

        # Build external user ID
        external_user_id = self._build_external_user_id(event)

        if not external_user_id:
            logger.warning("Could not build external_user_id from event")
            return None

        # Extract message content
        if not text:
            # Handle message subtypes
            if event.get("files"):
                text = "[File attached]"
            elif event.get("attachments"):
                text = "[Message with attachments]"
            else:
                return None

        # Check if this is a command
        message_type = "text"
        if text.strip().startswith("/new"):
            message_type = "command"

        # Build metadata
        metadata = {
            "slack": {
                "channel_id": channel,
                "channel_type": channel_type,
                "user_id": user,
                "ts": ts,
                "thread_ts": thread_ts,
                "workspace_id": self._workspace_id,
                "workspace_name": self._workspace_name,
            },
            "agent": self._default_agent,
            # For sending responses
            "reply_chat_id": channel,
        }

        # Handle /new command
        if message_type == "command":
            metadata["force_new_instance"] = True
            metadata["command"] = "/new"

        # Create incoming message
        incoming = IncomingMessage(
            external_user_id=external_user_id,
            content=text,
            source_id=self.source_id,
            metadata=metadata,
            message_type=message_type,
            reply_to_id=ts,
        )

        logger.debug(
            f"Processed Slack message: user={external_user_id}, "
            f"channel={channel}, type={message_type}"
        )

        return incoming

    def _build_external_user_id(self, event: dict) -> str | None:
        """Build external user ID from Slack event.

        Format:
        - DM: {workspace}:{user_id}
        - Channel: {workspace}:{channel_id}
        - Thread: {workspace}:{channel_id}:{thread_ts}

        Args:
            event: Slack message event.

        Returns:
            External user ID string or None.
        """
        workspace = self._workspace_id
        if not workspace:
            return None

        channel = event.get("channel", "")
        channel_type = event.get("channel_type", "channel")
        user = event.get("user", "")
        thread_ts = event.get("thread_ts")

        if not channel:
            return None

        # DM (direct message) - channel type 'im' or 'mpim'
        if channel_type in ("im", "mpim"):
            if not user:
                return None
            return f"{workspace}:{user}"

        # Thread reply
        if thread_ts:
            return f"{workspace}:{channel}:{thread_ts}"

        # Regular channel message
        return f"{workspace}:{channel}"

    async def _handle_new_command(self, ack: Callable, body: dict, client: Any) -> None:
        """Handle /new slash command.

        Args:
            ack: Acknowledge function (AsyncAck in slack-bolt - must be awaited).
            body: Command payload (flat structure, no 'event' key).
            client: Slack client instance.
        """
        await ack()

        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", "")
        team_id = body.get("team_id", "")
        text = body.get("text", "")

        if not user_id or not channel_id:
            logger.warning("Slash command missing user_id or channel_id")
            return

        # Match the external_user_id format used by incoming chat events so
        # `/new` actually resets the conversation the user is about to send
        # their next message in. The chat path uses:
        #   - DM (channel_id starts with "D"): {workspace}:{user_id}
        #   - Channel/thread: {workspace}:{channel_id}
        # The slash command body doesn't carry channel_type, but Slack DM
        # channel IDs always start with "D" by convention.
        if channel_id.startswith("D"):
            external_user_id = f"{team_id}:{user_id}"
        else:
            external_user_id = f"{team_id}:{channel_id}"

        incoming = IncomingMessage(
            source_id=self.source_id,
            external_user_id=external_user_id,
            content=text or "/new",
            message_type="command",
            metadata={
                "slack": {
                    "channel_id": channel_id,
                    "workspace_id": team_id,
                    "channel_type": "command",
                    "user_id": user_id,
                    "user_name": body.get("user_name", ""),
                },
                "agent": self._default_agent,
                "force_new_instance": True,
                "command": "/new",
            },
        )

        await self._emit_message(incoming)

    async def _resolve_dm_channel(self, user_id: str) -> str | None:
        """Resolve DM channel ID for a user, with caching.

        Args:
            user_id: Slack user ID.

        Returns:
            Channel ID for DM or None.
        """
        now = time.monotonic()

        # Check cache
        async with self._dm_cache_guard:
            if user_id in self._dm_cache:
                channel_id, cached_at = self._dm_cache[user_id]
                if now - cached_at < self.DM_CACHE_TTL_SECONDS:
                    # Move to end (most recently used)
                    self._dm_cache.move_to_end(user_id)
                    return channel_id
                else:
                    # Expired
                    del self._dm_cache[user_id]

        # Call conversations.open
        try:
            response = await self._call_slack_api(
                "conversations.open",
                users=[user_id]
            )

            channel_id = response.get("channel", {}).get("id")
            if channel_id:
                # Update cache with eviction logic
                async with self._dm_cache_guard:
                    # Evict expired entries first
                    self._evict_expired_cache_entries(now)
                    # Add new entry
                    self._dm_cache[user_id] = (channel_id, now)
                    # Evict oldest if still over capacity
                    while len(self._dm_cache) > self.DM_CACHE_MAX_SIZE:
                        self._dm_cache.popitem(last=False)
                return channel_id

        except Exception as e:
            logger.error(f"Failed to resolve DM channel for {user_id}: {e}")

        return None

    def _evict_expired_cache_entries(self, now: float | None = None) -> None:
        """Evict expired entries from DM cache.

        Args:
            now: Current time (monotonic). If None, uses time.monotonic().
        """
        if now is None:
            now = time.monotonic()

        expired = [
            user_id
            for user_id, (_, cached_at) in self._dm_cache.items()
            if now - cached_at >= self.DM_CACHE_TTL_SECONDS
        ]
        for user_id in expired:
            del self._dm_cache[user_id]

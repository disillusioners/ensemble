"""Discord source adapter for the ensemble multi-agent daemon.

Implements the full ``MessageSourceAdapter`` ABC for Discord using
``discord.py`` for the Gateway / WebSocket transport and REST. Mirrors the
multi-file shape of ``daemon/sources/adapters/slack/`` and reuses patterns
from ``daemon/sources/adapters/telegram.py`` where appropriate.

External user ID scheme (canonical):

* DM:      ``dm:{user_id}``
* Channel: ``{guild_id}:{channel_id}``
* Thread:  ``{guild_id}:{parent_channel_id}:{thread_id}``

The adapter:

* Validates ``bot_token`` and the canonical config schema.
* Manages a ``discord.Client`` lifecycle with an ``asyncio.Event``-gated
  ``start()`` that blocks until ``on_ready`` fires (or 30s timeout).
* FAIL-CLOSED on missing ``MESSAGE_CONTENT`` intent (clear error log).
* Normalizes ``on_message`` events to ``IncomingMessage`` with cleaned
  text, attachment placeholders, nested ``{"discord": {...}, "agent":
  ...}`` metadata, and an ``external_user_id`` following the canonical
  scheme.
* Sends outbound ``OutgoingMessage`` to Discord via a DB-backed mapping
  lookup (Slack pattern), splitting content longer than 2000 chars at
  paragraph / line / sentence / word / hard-cut boundaries (FR-22).
* Wraps sends with circuit breaker (5 failures → open for 60s) and a
  per-channel ``OrderedDict`` LRU lock (capped at 1000).
* Tracks threads per guild with TTL + LRU via
  :class:`DiscordThreadManager`, including archive-state awareness that
  falls back to the parent channel on send.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, ClassVar

import aiohttp

from daemon.sources.base import (
    IncomingMessage,
    MessageSourceAdapter,
    OutgoingMessage,
    SourceConfig,
    SourceStatus,
)
from daemon.sources.circuit_breaker import CircuitBreaker

from .constants import (
    DEFAULT_MAX_CONCURRENT_SENDS,
    DISCORD_API_BASE,
    DISCORD_ID_PATTERN,
    DISCORD_LATENCY_THRESHOLD_MS,
    DISCORD_MAX_MESSAGE_LENGTH,
    DISCORD_TOKEN_PATTERN,
    EVICTION_INTERVAL_SECONDS,
    GATEWAY_READY_TIMEOUT_SECONDS,
    HEALTH_CHECK_GRACE_SECONDS,
    MAX_CHANNEL_LOCKS,
    TEST_CONNECTION_TIMEOUT_SECONDS,
)
from .formatting import (
    _build_attachment_placeholder,
    _clean_discord_text,
    _strip_llm_artifact_tags,
)
from .resilience import DiscordSendSemaphore
from .thread_manager import DiscordThreadManager

logger = logging.getLogger(__name__)


class DiscordAPIError(Exception):
    """Raised when the Discord REST API rejects a request."""


# Module-level compiled regex for ID parsing. Anchored and case-sensitive.
_DISCORD_ID_RE = re.compile(DISCORD_ID_PATTERN)

# Module-level compiled regex for token format validation. Anchored so
# the entire string must match.
_DISCORD_TOKEN_RE = re.compile(DISCORD_TOKEN_PATTERN)


def _is_valid_discord_token_format(token: str) -> bool:
    """Check whether ``token`` matches Discord's bot-token shape.

    Used by :meth:`DiscordAdapter.test_connection` to short-circuit
    obviously-invalid tokens (missing dots, wrong segments) without
    making an API call. Returns True iff the token has the canonical
    3-segment dot-separated shape.
    """
    if not token:
        return False
    return bool(_DISCORD_TOKEN_RE.match(token))


class DiscordAdapter(MessageSourceAdapter):
    """Discord Gateway + REST adapter.

    Patterns reused (with provenance):

    * Slack ``SlackAdapter._get_channel_lock`` — OrderedDict LRU.
    * Telegram ``TelegramAdapter`` — config extraction, ABC
      conformance, ``OrderedDict`` cap of 1000.
    * Slack ``SlackAdapter.test_connection`` — classmethod with direct
      aiohttp ``GET`` and ``Authorization`` header.
    """

    # Per-spec: canonical key constants. Re-exports of the canonical
    # config keys are exposed as class attributes for tests/clarity.
    KEY_AGENT: ClassVar[str] = "agent"
    KEY_REQUIRE_MENTION: ClassVar[str] = "require_mention"
    KEY_ALLOWED_GUILD_IDS: ClassVar[str] = "allowed_guild_ids"
    KEY_ALLOWED_CHANNELS: ClassVar[str] = "allowed_channels"
    KEY_ALLOWED_BOT_IDS: ClassVar[str] = "allowed_bot_ids"
    KEY_CHANNEL_MENTION_CONFIG: ClassVar[str] = "channel_mention_config"
    KEY_IGNORE_BOT_MESSAGES: ClassVar[str] = "ignore_bot_messages"
    KEY_STRIP_LLM_ARTIFACT_TAGS: ClassVar[str] = "strip_llm_artifact_tags"
    KEY_INTENTS: ClassVar[str] = "intents"
    KEY_EVICTION_INTERVAL: ClassVar[str] = "eviction_interval_seconds"
    KEY_MAX_CONCURRENT_SENDS: ClassVar[str] = "max_concurrent_sends"

    # Mention override values for `channel_mention_config`.
    MENTION_ALWAYS_ACTIVE: ClassVar[str] = "always_active"
    MENTION_REQUIRE_MENTION: ClassVar[str] = "require_mention"
    MENTION_DISABLED: ClassVar[str] = "disabled"

    def __init__(
        self,
        config: SourceConfig,
        on_message: Callable[[IncomingMessage], Awaitable[None]],
        manager: Any = None,
    ) -> None:
        super().__init__(config, on_message)

        # --- Credentials -------------------------------------------------
        self._bot_token: str | None = config.credentials.get("bot_token")
        if not self._bot_token:
            raise ValueError(
                "Discord adapter requires 'bot_token' in credentials"
            )
        # Redacted copy used by tests and logs (NFR-10).
        self._bot_token_redacted = self._redact_token(self._bot_token)

        # --- Config ------------------------------------------------------
        cfg = config.config or {}

        # canonical key: "agent" (NOT "default_agent" — leader decision)
        self._default_agent: str = cfg.get(self.KEY_AGENT, "ari")
        self._require_mention: bool = bool(
            cfg.get(self.KEY_REQUIRE_MENTION, True)
        )
        self._allowed_guild_ids: list[int] = self._coerce_id_list(
            cfg.get(self.KEY_ALLOWED_GUILD_IDS)
        )
        self._allowed_channels: list[int] = self._coerce_id_list(
            cfg.get(self.KEY_ALLOWED_CHANNELS)
        )
        self._allowed_bot_ids: list[int] = self._coerce_id_list(
            cfg.get(self.KEY_ALLOWED_BOT_IDS)
        )
        # Per-channel override of mention requirement. Keys are channel
        # snowflakes (str or int). Values are one of MENTION_* constants.
        raw_mention_cfg = cfg.get(self.KEY_CHANNEL_MENTION_CONFIG, {}) or {}
        self._channel_mention_config: dict[str, str] = {
            str(k): str(v) for k, v in raw_mention_cfg.items()
        }
        self._ignore_bot_messages: bool = bool(
            cfg.get(self.KEY_IGNORE_BOT_MESSAGES, True)
        )
        self._strip_llm_artifact_tags_enabled: bool = bool(
            cfg.get(self.KEY_STRIP_LLM_ARTIFACT_TAGS, True)
        )

        # Intents config — dict format matching discord.py ``Intents``.
        # Stored as-is and resolved against the imported ``Intents`` class
        # inside ``start()`` so import side effects are deferred.
        self._intents_config: dict[str, bool] = (
            dict(cfg.get(self.KEY_INTENTS, {}))
            if isinstance(cfg.get(self.KEY_INTENTS), dict)
            else {}
        )
        if not self._intents_config:
            # Default: enable the intents we actually need to read
            # message content and handle DMs.
            self._intents_config = {
                "guilds": True,
                "guild_messages": True,
                "message_content": True,
                "dm_messages": True,
            }

        self._eviction_interval_seconds: int = int(
            cfg.get(self.KEY_EVICTION_INTERVAL, EVICTION_INTERVAL_SECONDS)
        )
        self._max_concurrent_sends: int = int(
            cfg.get(self.KEY_MAX_CONCURRENT_SENDS, DEFAULT_MAX_CONCURRENT_SENDS)
        )

        # --- State -------------------------------------------------------
        self._client: Any = None
        self._client_task: asyncio.Task | None = None
        self._ready_event: asyncio.Event = asyncio.Event()
        # FIX 5: Set when ``_client_task`` terminates with an exception
        # (e.g. ``PrivilegedIntentsRequired``). Raced against ``_ready_event``
        # so that a real startup failure surfaces immediately instead of
        # being swallowed by the 30s ready timeout.
        self._gateway_error: asyncio.Event = asyncio.Event()
        self._gateway_error_detail: BaseException | None = None
        self._bot_user_id: str | None = None
        self._bot_user_name: str | None = None
        self._ttl_task: asyncio.Task | None = None
        self._stop_lock = asyncio.Lock()
        # FIX 4: serialize concurrent ``start()`` invocations so a
        # concurrent ``stop()`` can't observe ``start()`` mid-init and
        # leak an orphaned Gateway task. See start()/stop() race in the
        # plan.
        self._start_lock = asyncio.Lock()
        self._stopped = False
        # Monotonic deadline (set in ``start()`` after RUNNING) that
        # suppresses transient ``is_ready()=False`` / unstable-latency
        # failures during the post-``on_ready`` Gateway stabilization
        # window. Prevents the supervisor's immediate post-start()
        # health check from crash-looping the adapter. See
        # ``health_check()``.
        self._health_check_grace_until: float = 0.0

        # --- Resilience --------------------------------------------------
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=5, recovery_timeout=60.0
        )
        self._send_semaphore = DiscordSendSemaphore(
            max_concurrent_sends=self._max_concurrent_sends
        )

        # Per-channel ordering locks. Slack pattern: cap of 100 (here 1000
        # to match Telegram), LRU via move_to_end/popitem.
        self._channel_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._channel_locks_guard = asyncio.Lock()

        # Thread manager (initialized only if InstanceManager is provided).
        self._thread_manager: DiscordThreadManager | None = None
        if manager is not None:
            self._thread_manager = DiscordThreadManager(manager=manager)

        # Source repository injected by registry.
        self._source_repo: Any = None

    # ------------------------------------------------------------------
    # Properties / helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _redact_token(token: str) -> str:
        """Return a redacted form of the bot token safe for logs (NFR-10)."""
        if not token:
            return ""
        if len(token) <= 8:
            return "***"
        return f"{token[:4]}***{token[-4:]}"

    @staticmethod
    def _coerce_id_list(value: Any) -> list[int]:
        """Coerce a config value (None, list of str/int, comma string) to a list of ints.

        Empty / None returns []. Strings like "123,456" are split on
        commas. Int values inside the list are kept as-is; string values
        are parsed via ``int(...)``. Invalid entries raise ``ValueError``
        with the offending value.
        """
        if value is None:
            return []
        if isinstance(value, str):
            value = [v.strip() for v in value.split(",") if v.strip()]
        if not isinstance(value, (list, tuple)):
            raise ValueError(
                f"Expected list for Discord ID list, got {type(value).__name__}"
            )
        out: list[int] = []
        for v in value:
            try:
                out.append(int(v))
            except (TypeError, ValueError) as e:
                raise ValueError(f"Invalid Discord ID {v!r}: {e}") from e
        return out

    @property
    def bot_user_id(self) -> str | None:
        return self._bot_user_id

    @property
    def default_agent(self) -> str:
        return self._default_agent

    @property
    def eviction_interval_seconds(self) -> int:
        return self._eviction_interval_seconds

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect to the Discord Gateway and wait for ``on_ready``.

        Order matters:

        1. Resolve intents from config. FAIL-CLOSED on missing
           ``MESSAGE_CONTENT`` (which the bot needs to read message text).
        2. Reset circuit breaker (avoids stale state across restart).
        3. Build ``discord.Client`` and register ``on_ready`` /
           ``on_message``.
        4. Spawn ``client.start(token)`` in a background task.
        5. Race ``on_ready`` against the client's task — if the client
           task fails fast (e.g. ``PrivilegedIntentsRequired``) we
           surface the real error immediately instead of waiting for
           the 30s ready timeout. If neither fires, we cancel the
           client task to avoid leaking a half-connected Gateway.

        Raises:
            RuntimeError: On ``MESSAGE_CONTENT`` not granted, on Gateway
                failure, or on Gateway timeout.
        """
        if self._status == SourceStatus.RUNNING:
            return
        if self._status == SourceStatus.STARTING:
            # Defensive — do not double-start.
            return

        # FIX 4: serialize concurrent ``start()`` invocations against
        # ``stop()`` so we never leak an orphaned Gateway task between
        # the ``_stopped=False`` reset and the ``_client_task`` creation.
        async with self._start_lock:
            # Re-check status under the lock; another concurrent start()
            # may have completed while we were waiting.
            if self._status == SourceStatus.RUNNING:
                return
            if self._status == SourceStatus.STARTING:
                return

            self._circuit_breaker.reset()
            self._status = SourceStatus.STARTING
            self._error = None
            self._ready_event.clear()
            # FIX 5: clear any prior error state for a fresh attempt.
            self._gateway_error.clear()
            self._gateway_error_detail = None
            self._stopped = False

            # Local imports so that ``import discord`` is deferred until a
            # Discord source is actually configured (keeps base imports fast
            # and avoids forcing discord.py into non-Discord test runs).
            import discord
            from discord import Intents as _Intents

            # Build Intents from config dict.
            intents = _Intents.none()
            for name, enabled in self._intents_config.items():
                if not hasattr(intents, name):
                    logger.warning(
                        f"Unknown intent '{name}' in Discord config; skipping"
                    )
                    continue
                setattr(intents, name, bool(enabled))

            # FAIL CLOSED: MESSAGE_CONTENT is required to read message text.
            if not intents.message_content:
                self._status = SourceStatus.ERROR
                self._error = (
                    "MESSAGE_CONTENT intent is required to read message text. "
                    "Enable it in the Discord developer portal AND set "
                    "'intents.message_content: true' in this source's config."
                )
                logger.error(self._error)
                raise RuntimeError(self._error)

            client = discord.Client(intents=intents)
            self._client = client

            async def _on_ready() -> None:
                # Capture bot identity and signal readiness.
                user = client.user
                if user is not None:
                    self._bot_user_id = str(user.id)
                    self._bot_user_name = user.name
                logger.info(
                    f"Discord Gateway ready: source={self.source_id}, "
                    f"user=@{self._bot_user_name} (id={self._bot_user_id})"
                )
                self._ready_event.set()

            async def _on_message(message: Any) -> None:
                # Stub registered in Phase 1; real handler in Phase 2.
                await self._handle_message(message)

            client.event(_on_ready)
            client.event(_on_message)

            # Spawn the Gateway task.
            self._client_task = asyncio.create_task(
                client.start(self._bot_token),
                name=f"discord-gateway-{self.source_id}",
            )

            # FIX 5: hook a done_callback so we surface the *real* error
            # (e.g. PrivilegedIntentsRequired, LoginFailure) immediately
            # rather than after the 30s ready timeout.
            def _on_client_task_done(task: asyncio.Task) -> None:
                if task.cancelled():
                    return
                exc = task.exception()
                if exc is not None:
                    self._gateway_error_detail = exc
                    self._gateway_error.set()

            self._client_task.add_done_callback(_on_client_task_done)

            # Race on_ready vs gateway_error so a fast-failing client
            # surfaces its real error rather than the generic timeout.
            ready_task: asyncio.Task | None = asyncio.create_task(
                self._ready_event.wait(),
                name=f"discord-ready-wait-{self.source_id}",
            )
            error_task: asyncio.Task | None = asyncio.create_task(
                self._gateway_error.wait(),
                name=f"discord-error-wait-{self.source_id}",
            )
            try:
                done, pending = await asyncio.wait(
                    {ready_task, error_task},
                    timeout=GATEWAY_READY_TIMEOUT_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except BaseException:
                # Cancel and await the racers and client task on any
                # exception (including cancellation from the caller).
                for task in (ready_task, error_task):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(
                    *(task for task in (ready_task, error_task) if task is not None),
                    return_exceptions=True,
                )
                if self._client_task is not None:
                    self._client_task.cancel()
                    await asyncio.gather(
                        self._client_task, return_exceptions=True
                    )
                raise

            # The winning side gets cleaned up; the loser is cancelled and
            # awaited so it cannot remain pending after start() exits.
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            if self._gateway_error.is_set():
                # The client task failed fast — surface the real error.
                exc = self._gateway_error_detail
                self._status = SourceStatus.ERROR
                self._error = f"Discord Gateway failed: {exc}"
                logger.error(self._error)
                if self._client_task is not None:
                    try:
                        await asyncio.gather(
                            self._client_task, return_exceptions=True
                        )
                    except Exception:  # noqa: BLE001
                        pass
                self._client_task = None
                self._client = None
                raise RuntimeError(self._error) from exc

            if not self._ready_event.is_set():
                # True timeout — neither ready nor an immediate error fired.
                self._status = SourceStatus.ERROR
                self._error = "Discord Gateway connection timed out"
                logger.error(
                    f"Discord Gateway timeout: source={self.source_id}, "
                    f"timeout={GATEWAY_READY_TIMEOUT_SECONDS}s"
                )
                if self._client_task is not None:
                    self._client_task.cancel()
                    try:
                        await asyncio.gather(
                            self._client_task, return_exceptions=True
                        )
                    except Exception:  # noqa: BLE001
                        pass
                self._client_task = None
                self._client = None
                raise RuntimeError(self._error)

            self._status = SourceStatus.RUNNING
            # Open the startup grace window so the supervisor's immediate
            # post-start() health check tolerates transient
            # ``is_ready()=False`` / unstable latency while the Gateway
            # stabilizes. Without this, Discord's gateway can transiently
            # report unhealthy for a few hundred ms after ``on_ready``,
            # crash-looping the adapter. See ``health_check()``.
            self._health_check_grace_until = (
                time.monotonic() + HEALTH_CHECK_GRACE_SECONDS
            )

            # Start the TTL eviction loop AFTER the Gateway is confirmed.
            self._ttl_task = asyncio.create_task(
                self._periodic_eviction_loop(),
                name=f"discord-ttl-evict-{self.source_id}",
            )

            logger.info(
                f"Discord adapter started: source={self.source_id}, "
                f"agent={self._default_agent}"
            )

    async def stop(self) -> None:
        """Stop the adapter gracefully and idempotently.

        7-step idempotent shutdown (matches plan):

        1. Guard with ``_stop_lock`` to serialize concurrent stop calls.
        2. Set ``_stopped=True`` so background tasks exit promptly.
        3. Cancel TTL eviction task and await it.
        4. Shut down thread manager (try/except each instance).
        5. Close discord.py client (closes WebSocket).
        6. Await the client task (may already be done).
        7. Release channel locks, mark STOPPED.
        """
        async with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True

            logger.info(f"Stopping Discord adapter: {self.source_id}")

            # 3. Cancel TTL eviction loop.
            if self._ttl_task is not None:
                self._ttl_task.cancel()
                try:
                    await self._ttl_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"TTL task ended with error: {e}")
                self._ttl_task = None

            # 4. Shut down thread manager (best-effort).
            if self._thread_manager is not None:
                try:
                    await self._thread_manager.shutdown()
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"DiscordThreadManager.shutdown() raised: {e}"
                    )

            # 5. Close the discord.py client.
            if self._client is not None:
                try:
                    await self._client.close()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Error closing Discord client: {e}")

            # 6. Await the client task.
            if self._client_task is not None:
                try:
                    await asyncio.gather(
                        self._client_task, return_exceptions=True
                    )
                except Exception:  # noqa: BLE001
                    pass
                self._client_task = None

            self._client = None
            # ``_ready_event`` is reusable for the next start.
            self._ready_event.clear()

            # 7. Release channel locks (drop them; the locks will be GC'd).
            async with self._channel_locks_guard:
                self._channel_locks.clear()

            self._status = SourceStatus.STOPPED
            logger.info(f"Discord adapter stopped: {self.source_id}")

    async def health_check(self) -> bool:
        """Return True iff the Gateway is connected and latency is healthy.

        During the startup grace period (``HEALTH_CHECK_GRACE_SECONDS``
        after ``start()`` returns), returns True as long as the client
        task is alive, tolerating transient ``is_ready()=False`` or
        unstable latency while the Gateway stabilizes. After the grace
        window expires, the original strict checks apply unchanged.
        """
        if self._status != SourceStatus.RUNNING:
            return False
        client = self._client
        if client is None:
            return False

        in_grace = time.monotonic() < self._health_check_grace_until

        try:
            # During the startup grace window, only verify the client
            # task is alive. ``is_ready()`` and ``latency`` can be
            # transiently False/None for a few hundred ms after
            # ``on_ready`` while the Gateway stabilizes — failing the
            # health check on that would crash-loop the adapter because
            # the supervisor runs health_check() immediately after
            # start() with no grace period. A done client task, however,
            # IS a real failure (e.g. uncaught exception in
            # ``_client_task``).
            if in_grace:
                if self._client_task is not None and self._client_task.done():
                    return False
                return True

            # Strict checks after grace period.
            if not client.is_ready():
                return False
            latency = client.latency
            # latency is in seconds; threshold is 5000ms.
            if latency is None:
                # Defensive — discord.py normally returns a float.
                return True
            if latency * 1000.0 >= DISCORD_LATENCY_THRESHOLD_MS:
                logger.warning(
                    f"Discord Gateway latency high: "
                    f"{latency * 1000:.0f}ms >= "
                    f"{DISCORD_LATENCY_THRESHOLD_MS}ms"
                )
                return False
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Discord health check failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Periodic eviction loop
    # ------------------------------------------------------------------

    async def _periodic_eviction_loop(self) -> None:
        """Periodic TTL eviction of expired thread instances.

        Exits cleanly on ``CancelledError`` (no error log on shutdown).
        Logs other exceptions at warning and continues — eviction is
        a maintenance task, not a critical path.
        """
        try:
            while True:
                await asyncio.sleep(self._eviction_interval_seconds)
                if self._thread_manager is not None:
                    try:
                        await self._thread_manager.evict_expired()
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            f"Periodic Discord thread eviction failed: {e}"
                        )
        except asyncio.CancelledError:
            # Expected during stop(). Silent exit.
            return

    # ------------------------------------------------------------------
    # Per-channel ordering locks (Slack pattern)
    # ------------------------------------------------------------------

    async def _get_channel_lock(self, channel_id: str) -> asyncio.Lock:
        """Return a per-channel asyncio.Lock, evicting held locks safely.

        Eviction strategy:

        * If at capacity, walk from the oldest end and pop the first
          entry whose lock is NOT currently held.
        * If every entry is held, this is a degenerate case (1000+
          concurrent in-flight channels) — fall back to replacing the
          oldest entry to avoid deadlock. This still preserves per-key
          locking semantics (each entry has its own lock).
        """
        async with self._channel_locks_guard:
            if channel_id in self._channel_locks:
                self._channel_locks.move_to_end(channel_id)
                return self._channel_locks[channel_id]

            # Evict up to capacity: skip held locks.
            while len(self._channel_locks) >= MAX_CHANNEL_LOCKS:
                evicted = False
                for key in list(self._channel_locks.keys()):
                    lock = self._channel_locks[key]
                    if not lock.locked():
                        self._channel_locks.pop(key, None)
                        evicted = True
                        break
                if not evicted:
                    # All locks held — degrade gracefully by removing
                    # the oldest entry (worst case: one channel loses
                    # its lock identity briefly).
                    self._channel_locks.popitem(last=False)
                    break

            lock = asyncio.Lock()
            self._channel_locks[channel_id] = lock
            return lock

    # ------------------------------------------------------------------
    # Inbound: Discord message -> IncomingMessage
    # ------------------------------------------------------------------

    async def _handle_message(self, message: Any) -> None:
        """discord.py on_message entry point."""
        try:
            # Ignore messages authored by us.
            if self._client is not None and message.author == self._client.user:
                return

            # Earliest gate: guild / channel / bot filters.
            if not self._should_process_message(message):
                return

            # Mention gating (guild channels only).
            if not self._is_bot_mentioned(message):
                logger.debug(
                    f"Skipping channel message without bot mention: "
                    f"channel={getattr(message.channel, 'id', None)}, "
                    f"user={getattr(message.author, 'id', None)}"
                )
                return

            incoming = self._normalize_incoming(message)
            if incoming is None:
                return

            # If this is a thread, register it with the thread manager
            # for TTL/archive tracking. Skip if manager isn't initialized
            # (no manager was provided at construction).
            if (
                self._thread_manager is not None
                and incoming.metadata.get("discord", {}).get("thread_id")
            ):
                d = incoming.metadata["discord"]
                try:
                    await self._thread_manager.register_thread(
                        guild_id=str(d["guild_id"]) if d.get("guild_id") else "0",
                        channel_id=str(d["parent_channel_id"] or d["channel_id"]),
                        thread_id=str(d["thread_id"]),
                        instance_id=None,  # Instance is managed upstream
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"Failed to register Discord thread: {e}"
                    )

            await self._emit_message(incoming)
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"Error handling Discord message: {e}", exc_info=True
            )

    def _should_process_message(self, message: Any) -> bool:
        """Earliest gate: guild / channel / bot filters.

        Order: bot author -> guild allow -> channel allow. DMs always
        pass guild/channel filters (no guild context).
        """
        author = getattr(message, "author", None)
        is_bot = bool(getattr(author, "bot", False)) if author else False
        if is_bot:
            if self._ignore_bot_messages:
                # ``allowed_bot_ids`` override the default skip.
                if self._allowed_bot_ids:
                    if int(getattr(author, "id", 0)) not in self._allowed_bot_ids:
                        return False
                else:
                    return False

        # DMs have no guild; channel resolution falls back to the DM
        # channel itself for allow-list checks.
        channel = getattr(message, "channel", None)
        guild = getattr(message, "guild", None)

        if guild is not None:
            guild_id = int(getattr(guild, "id", 0) or 0)
            if self._allowed_guild_ids and guild_id not in self._allowed_guild_ids:
                logger.debug(
                    f"Skipping message from non-allowlisted guild: "
                    f"guild={guild_id}"
                )
                return False

            # Channel allow-list. For threads, check the parent channel.
            target_channel_id = self._resolve_filter_channel_id(message, channel)
            if (
                self._allowed_channels
                and target_channel_id not in self._allowed_channels
            ):
                logger.debug(
                    f"Skipping message from non-allowlisted channel: "
                    f"channel={target_channel_id}"
                )
                return False

        # Otherwise (DM or no filters configured), pass.
        return True

    @staticmethod
    def _resolve_filter_channel_id(message: Any, channel: Any) -> int:
        """Return the parent channel id for threads, else the channel id.

        discord.py exposes ``channel.parent_id`` on Thread channels.
        """
        parent_id = getattr(channel, "parent_id", None)
        if parent_id is not None:
            return int(parent_id)
        return int(getattr(channel, "id", 0) or 0)

    def _is_bot_mentioned(self, message: Any) -> bool:
        """Return True if the bot should respond to this message.

        * DMs: always True.
        * Guild channels with ``require_mention``: require either an
          explicit mention token in the content or an entry in
          ``message.mentions``.
        * ``channel_mention_config`` overrides per-channel: ``always_active``
          forces True, ``disabled`` forces False, ``require_mention``
          defers to the global setting.
        """
        # DMs (no guild) never require mention.
        if getattr(message, "guild", None) is None:
            return True

        # SECURITY: Bot user id not yet resolved — FAIL CLOSED for all guild
        # messages, regardless of channel overrides or global settings.  The
        # Gateway can dispatch on_message before on_ready in edge cases.
        if not self._bot_user_id:
            logger.warning(
                "Discord mention check: bot identity not yet resolved; "
                "dropping guild message to avoid unverified processing"
            )
            return False

        channel = getattr(message, "channel", None)
        channel_id = str(getattr(channel, "id", "")) if channel else ""
        override = self._channel_mention_config.get(channel_id)
        if override == self.MENTION_ALWAYS_ACTIVE:
            return True
        if override == self.MENTION_DISABLED:
            return False
        # If override == "require_mention" or not set, fall through to
        # the global setting.

        if not self._require_mention:
            return True

        # Check ``message.mentions`` (the SDK-resolved list).
        mentions = getattr(message, "mentions", None) or []
        bot_user_id_int = (
            int(self._bot_user_id) if self._bot_user_id.isdigit() else None
        )
        if bot_user_id_int is not None:
            for u in mentions:
                if int(getattr(u, "id", 0) or 0) == bot_user_id_int:
                    return True

        # Fall back to scanning content for the literal mention token.
        content = getattr(message, "content", "") or ""
        token_plain = f"<@{self._bot_user_id}>"
        token_nick = f"<@!{self._bot_user_id}>"
        if token_plain in content or token_nick in content:
            return True

        return False

    def _build_external_user_id(self, message: Any) -> str | None:
        """Construct the canonical external_user_id from a Discord message."""
        author = getattr(message, "author", None)
        if author is None:
            return None
        user_id = str(getattr(author, "id", "") or "")
        if not user_id:
            return None

        channel = getattr(message, "channel", None)
        guild = getattr(message, "guild", None)

        # DMs: guild is None.
        if guild is None:
            return f"dm:{user_id}"

        guild_id = str(getattr(guild, "id", "") or "")
        parent_id = getattr(channel, "parent_id", None)
        channel_id = str(getattr(channel, "id", "") or "")

        # Thread reply: discord.py threads expose ``parent_id``.
        if parent_id is not None:
            return f"{guild_id}:{str(parent_id)}:{channel_id}"

        # Regular channel.
        if guild_id and channel_id:
            return f"{guild_id}:{channel_id}"
        return None

    def _normalize_incoming(self, message: Any) -> IncomingMessage | None:
        """Discord ``Message`` -> ``IncomingMessage``.

        Handles attachments (FR-17) and reply references (FR-20).
        Returns None if the resulting content is empty AND there are no
        attachments (no useful payload).
        """
        external_user_id = self._build_external_user_id(message)
        if external_user_id is None:
            logger.warning("Could not build external_user_id from message")
            return None

        # Build metadata first (we need channel/guild ids for empty-content
        # branch).
        author = message.author
        channel = message.channel
        guild = getattr(message, "guild", None)
        attachments = getattr(message, "attachments", None) or []

        is_dm = guild is None
        thread_id = getattr(channel, "id", None) if getattr(channel, "parent_id", None) is not None else None
        parent_channel_id = getattr(channel, "parent_id", None)

        discord_meta: dict[str, Any] = {
            "guild_id": str(getattr(guild, "id", "")) if guild else None,
            "guild_name": getattr(guild, "name", None) if guild else None,
            "channel_id": str(getattr(channel, "id", "")) if channel else None,
            "channel_name": getattr(channel, "name", None) if channel else None,
            "channel_type": "dm" if is_dm else "thread" if thread_id else "text",
            "thread_id": str(thread_id) if thread_id else None,
            "thread_name": getattr(channel, "name", None) if thread_id else None,
            "parent_channel_id": str(parent_channel_id) if parent_channel_id else None,
            "user_id": str(getattr(author, "id", "")) if author else None,
            "user_name": getattr(author, "name", None) if author else None,
            "user_display_name": getattr(author, "display_name", None) if author else None,
            "message_id": str(getattr(message, "id", "")) or None,
            "is_dm": is_dm,
        }

        # --- Content extraction ---
        raw_content = getattr(message, "content", "") or ""
        cleaned_content = _clean_discord_text(raw_content) if raw_content else ""

        # FIX 6: Only populate ``image_urls`` for actual image attachments.
        # Previously, every attachment URL was included regardless of MIME
        # type, leaking PDF / text / video URLs into the agent's image
        # context. Discord attachments expose ``content_type``; we filter
        # to anything starting with ``image/``.
        image_urls: list[str] = [
            a.url
            for a in attachments
            if getattr(a, "url", None)
            and getattr(a, "content_type", "")
            and a.content_type.startswith("image/")
        ]

        # The placeholder check uses ALL attachments (image or otherwise)
        # so a non-image attachment still produces a meaningful
        # ``[File attachment: ...]`` placeholder when text is empty. Only
        # ``image_urls`` (the structured list) is filtered by content type.
        if not cleaned_content and attachments:
            # Empty text + attachments -> placeholder.
            content = _build_attachment_placeholder(attachments)
        elif cleaned_content and attachments:
            # Both text and attachments -> keep text, populate images.
            content = cleaned_content
        elif cleaned_content:
            content = cleaned_content
        else:
            # Nothing useful (no text, no attachments).
            return None

        # Strip LLM artifact tags if enabled.
        if self._strip_llm_artifact_tags_enabled:
            content = _strip_llm_artifact_tags(content)

        # Reply-to (FR-20 inbound).
        # NOTE: discord.py exposes reply metadata as `message.reference`
        # (a MessageReference object), NOT `message.message_reference`.
        reply_to_id: str | None = None
        msg_ref = getattr(message, "reference", None)
        if msg_ref is not None:
            ref_id = getattr(msg_ref, "message_id", None)
            if ref_id is not None:
                try:
                    reply_to_id = str(int(ref_id))
                except (TypeError, ValueError):
                    reply_to_id = None

        # Detect /new command.
        message_type = "text"
        metadata: dict[str, Any] = {
            "discord": discord_meta,
            "agent": self._default_agent,
        }
        if content.strip().startswith("/new"):
            message_type = "command"
            metadata["force_new_instance"] = True
            metadata["command"] = "/new"

        return IncomingMessage(
            external_user_id=external_user_id,
            content=content,
            source_id=self.source_id,
            images=image_urls or None,
            metadata=metadata,
            message_type=message_type,
            reply_to_id=reply_to_id,
        )

    # ------------------------------------------------------------------
    # Outbound: OutgoingMessage -> Discord
    # ------------------------------------------------------------------

    def _split_message(
        self, content: str, max_length: int = DISCORD_MAX_MESSAGE_LENGTH
    ) -> list[str]:
        """Split content into chunks each <= max_length.

        5-tier priority boundary chain (FR-22):

        1. Paragraph boundary (``\\n\\n``)
        2. Line boundary (``\\n``)
        3. Sentence boundary (``. `` / ``! `` / ``? ``)
        4. Word boundary (space)
        5. Hard cut at ``max_length``

        Applied to each oversized chunk independently after the first
        split. Returns ``[content]`` if it fits in a single chunk.
        """
        if not content:
            return [""]
        if len(content) <= max_length:
            return [content]

        chunks: list[str] = []
        remaining = content
        while len(remaining) > max_length:
            window = remaining[:max_length]
            split_at = -1
            # 1. Paragraph
            idx = window.rfind("\n\n")
            if idx > 0:
                split_at = idx
            else:
                # 2. Line
                idx = window.rfind("\n")
                if idx > 0:
                    split_at = idx
                else:
                    # 3. Sentence (longest of the three terminal punct+space)
                    best = -1
                    for punct in (". ", "! ", "? "):
                        idx = window.rfind(punct)
                        if idx > best:
                            best = idx
                    if best > 0:
                        split_at = best + 1  # include the space
                    else:
                        # 4. Word
                        idx = window.rfind(" ")
                        if idx > 0:
                            split_at = idx

            if split_at <= 0:
                # 5. Hard cut
                split_at = max_length

            chunk = remaining[:split_at].rstrip()
            remaining = remaining[split_at:].lstrip("\n")
            if not chunk:
                # Defensive: avoid infinite loop if everything was stripped.
                chunk = remaining[:max_length]
                remaining = remaining[max_length:]
            chunks.append(chunk)

        if remaining:
            chunks.append(remaining)
        return chunks

    def _parse_external_user_id(self, external_user_id: str) -> dict[str, str | None]:
        """Parse a canonical external_user_id into its components.

        Returns a dict with keys: ``mode`` (``"dm"`` / ``"channel"`` /
        ``"thread"``), ``user_id``, ``guild_id``, ``channel_id``,
        ``parent_channel_id``, ``thread_id``.
        """
        if not external_user_id or not _DISCORD_ID_RE.match(external_user_id):
            raise ValueError(
                f"Invalid Discord external_user_id: {external_user_id!r}"
            )
        if external_user_id.startswith("dm:"):
            return {
                "mode": "dm",
                "user_id": external_user_id[3:],
                "guild_id": None,
                "channel_id": None,
                "parent_channel_id": None,
                "thread_id": None,
            }
        parts = external_user_id.split(":")
        if len(parts) == 2:
            return {
                "mode": "channel",
                "user_id": None,
                "guild_id": parts[0],
                "channel_id": parts[1],
                "parent_channel_id": parts[1],
                "thread_id": None,
            }
        return {
            "mode": "thread",
            "user_id": None,
            "guild_id": parts[0],
            "channel_id": parts[2],
            "parent_channel_id": parts[1],
            "thread_id": parts[2],
        }

    async def _resolve_send_target(
        self, external_user_id: str
    ) -> dict[str, Any] | None:
        """Resolve the canonical target for outbound send.

        Returns a dict with ``mode``, ``channel_id``, ``guild_id``, etc.,
        backed by the DB mapping when one exists. Returns None if no
        mapping is available (caller logs + returns False).

        Always passes a default mapping for DM routing so the bot can
        reply to DMs even before any inbound message has been processed
        (in practice the first inbound message creates the mapping, but
        this is robust to operator-initiated sends).
        """
        parsed = self._parse_external_user_id(external_user_id)

        if parsed["mode"] == "dm":
            return {
                "mode": "dm",
                "user_id": parsed["user_id"],
                "channel_id": None,
                "guild_id": None,
                "thread_id": None,
            }

        # Channel / thread — look up the DB mapping for the canonical
        # external_user_id.
        if self._source_repo is None:
            logger.error("Discord send: _source_repo not injected")
            return None

        try:
            mapping = await asyncio.to_thread(
                self._source_repo.get_instance_mapping,
                self.source_id,
                external_user_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"Discord send: DB lookup failed for {external_user_id}: {e}"
            )
            return None

        if mapping is None:
            logger.warning(
                f"Discord send: no mapping for {external_user_id}"
            )
            return None

        meta = getattr(mapping, "mapping_metadata", None) or {}
        discord_meta = meta.get("discord") if isinstance(meta, dict) else None
        if not isinstance(discord_meta, dict):
            logger.warning(
                f"Discord send: mapping missing discord metadata for {external_user_id}"
            )
            return None

        channel_id = discord_meta.get("channel_id")
        guild_id = discord_meta.get("guild_id")
        thread_id = discord_meta.get("thread_id")
        parent_channel_id = discord_meta.get("parent_channel_id")
        if not channel_id:
            logger.warning(
                f"Discord send: mapping missing channel_id for {external_user_id}"
            )
            return None

        return {
            "mode": parsed["mode"],
            "user_id": None,
            "guild_id": guild_id,
            "channel_id": str(channel_id),
            "parent_channel_id": str(parent_channel_id) if parent_channel_id else None,
            "thread_id": str(thread_id) if thread_id else None,
        }

    async def _route_outgoing(self, parsed: dict[str, Any]) -> Any:
        """Resolve parsed routing into a discord.py send target.

        Returns the discord.py object whose ``send(content, ...)`` method
        will deliver the message. For DMs, returns the DM channel
        (created on demand). For channels and threads, returns the
        channel/thread object fetched from ``client.get_channel``.
        """
        client = self._client
        if client is None:
            raise RuntimeError("Discord client not initialized")

        if parsed["mode"] == "dm":
            user_id = int(parsed["user_id"])
            user = await client.fetch_user(user_id)
            if user is None:
                raise RuntimeError(
                    f"Could not fetch Discord user {user_id}"
                )
            return await user.create_dm()

        # Channel or thread.
        thread_id = parsed.get("thread_id")
        if thread_id:
            # Check archive status; if archived, fall back to parent channel.
            if self._thread_manager is not None:
                guild_id = parsed.get("guild_id")
                if guild_id:
                    thread = await self._thread_manager.get_thread(
                        str(guild_id), str(thread_id)
                    )
                    if thread is not None and thread.is_archived:
                        # FIX 1: route to the parent channel — ``channel_id`` in
                        # the DB mapping is the thread's own ID, NOT the parent.
                        parent = parsed.get("parent_channel_id")
                        if parent:
                            logger.warning(
                                f"Discord send: thread {thread_id} is archived; "
                                f"routing to parent channel {parent}"
                            )
                            return await client.fetch_channel(int(parent))
                        # No parent stored in mapping — fall through to
                        # channel_id (best effort, may still 404).
                        logger.warning(
                            f"Discord send: thread {thread_id} is archived "
                            f"but mapping has no parent_channel_id; "
                            f"falling back to channel_id={parsed['channel_id']}"
                        )

        if thread_id:
            chan = client.get_channel(int(thread_id))
            if chan is None:
                # Try fetch as fallback.
                chan = await client.fetch_channel(int(thread_id))
            return chan

        # Regular channel.
        chan = client.get_channel(int(parsed["channel_id"]))
        if chan is None:
            chan = await client.fetch_channel(int(parsed["channel_id"]))
        return chan

    async def _send_single_chunk(
        self,
        target: Any,
        content: str,
        *,
        reference: Any | None,
        allowed_mentions: Any,
    ) -> bool:
        """Send a single chunk to a resolved Discord target.

        Returns True on success. Records circuit-breaker success/failure
        and translates discord.py's rate-limit handling into the agreed
        429-exclusion rule (no failure count for SDK-backoff signals).
        """
        try:
            await target.send(
                content,
                reference=reference,
                allowed_mentions=allowed_mentions,
            )
            await self._circuit_breaker.record_success()
            return True
        except Exception as e:  # noqa: BLE001
            # FIX 3: Classify precisely. Only transport/5xx/timeout errors
            # should count as circuit failures. Permanent 4xx client errors
            # (NotFound=404, Forbidden=403) indicate the request itself is
            # wrong, not a transport failure — they must NOT open the
            # circuit. 429 is handled internally by discord.py; explicit
            # guard for belt-and-suspenders.
            is_transient = True
            try:
                import discord as _discord_mod
                if isinstance(e, _discord_mod.HTTPException):
                    status = getattr(e, "status", None)
                    if status == 429:
                        is_transient = False
                    elif status is not None and 400 <= status < 500:
                        is_transient = False
                elif isinstance(e, asyncio.TimeoutError):
                    is_transient = True
            except ImportError:
                # discord not installed — treat as transient (existing
                # behavior); better to err on the side of opening the
                # circuit than to mask a real outage.
                pass

            if is_transient:
                await self._circuit_breaker.record_failure()
            logger.error(
                f"Discord send failed: {type(e).__name__}: {e}"
            )
            return False

    async def send(self, message: OutgoingMessage) -> bool:
        """Send an OutgoingMessage to Discord.

        Flow: circuit check -> resolve target (DB lookup) -> split ->
        acquire channel lock -> semaphore -> per-chunk send -> record
        success/failure. Multi-chunk sends are sequential.
        """
        if self._status != SourceStatus.RUNNING:
            logger.warning(
                f"Cannot send: adapter not running (status={self._status})"
            )
            return False

        if not await self._circuit_breaker.can_execute():
            logger.warning("Circuit open, cannot send to Discord")
            return False

        # Strip LLM artifact tags from outbound content if enabled.
        content = message.content
        if self._strip_llm_artifact_tags_enabled:
            content = _strip_llm_artifact_tags(content)
        if not content:
            logger.warning("Discord send: empty content after stripping")
            return False

        try:
            target_info = await self._resolve_send_target(
                message.external_user_id
            )
        except ValueError as e:
            logger.error(f"Discord send: {e}")
            return False
        if target_info is None:
            return False

        # Build MessageReference once if reply_to_id is set. Channel id
        # is required by discord.py 2.x MessageReference; we use the
        # resolved channel_id.
        reference: Any | None = None
        if message.reply_to_id:
            try:
                from discord import MessageReference as _MR

                ref_channel_id = target_info.get("channel_id") or target_info.get("thread_id")
                if ref_channel_id is None and target_info.get("mode") == "dm":
                    # DM replies don't need a channel_id; pass user_id if possible.
                    ref_channel_id = 0
                reference = _MR(
                    message_id=int(message.reply_to_id),
                    channel_id=int(ref_channel_id),
                    fail_if_not_exists=False,
                )
            except (TypeError, ValueError) as e:
                logger.warning(
                    f"Discord send: invalid reply_to_id "
                    f"{message.reply_to_id!r}: {e}"
                )
                reference = None

        # Resolve the discord.py target and lock key.
        try:
            target = await self._route_outgoing(target_info)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Discord send: route resolution failed: {e}")
            return False

        lock_key = (
            str(target_info.get("thread_id") or target_info.get("channel_id") or target_info.get("user_id"))
        )
        lock = await self._get_channel_lock(lock_key)

        allowed_mentions: Any | None = None
        try:
            from discord import AllowedMentions as _AM

            # Disable everyone/here/role mass-mentions by default; allow
            # the replied-to user (handled by reference) and explicit user
            # mentions in content.
            allowed_mentions = _AM(everyone=False, roles=False, users=True)
        except ImportError:  # pragma: no cover
            allowed_mentions = None

        chunks = self._split_message(content)
        async with lock:
            async with self._send_semaphore:
                sent_count = 0
                for chunk in chunks:
                    ok = await self._send_single_chunk(
                        target,
                        chunk,
                        reference=reference,
                        allowed_mentions=allowed_mentions,
                    )
                    if not ok:
                        logger.warning(
                            f"Discord send: chunk {sent_count + 1}/{len(chunks)} "
                            f"failed for external_user_id={message.external_user_id}"
                        )
                        return False
                    sent_count += 1
        return True

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------

    @classmethod
    async def test_connection(cls, config: SourceConfig) -> tuple[bool, str]:
        """Pre-flight validate a bot token via the Discord ``/users/@me`` REST endpoint.

        Mirrors ``SlackAdapter.test_connection`` (slack/adapter.py:527-578):
        build an aiohttp session, send an authenticated GET, map the
        response into a (bool, message) pair.

        FIX 9: Pre-flight the token FORMAT before making an API call.
        Tokens that don't match the canonical 3-segment shape are
        rejected up front so operators get a clear error instead of a
        generic 401 from Discord.
        """
        bot_token = config.credentials.get("bot_token") if config.credentials else None
        if not bot_token:
            return False, "bot_token is required"
        if not isinstance(bot_token, str):
            return False, "bot_token must be a string"
        if not _is_valid_discord_token_format(bot_token):
            return False, (
                "bot_token has invalid format. Discord bot tokens consist of "
                "3 dot-separated segments (e.g., "
                "'MTIzNDU2Nzg5.GAbCdE.xxx...')."
            )

        url = f"{DISCORD_API_BASE}/users/@me"
        headers = {"Authorization": f"Bot {bot_token}"}

        try:
            timeout = aiohttp.ClientTimeout(total=TEST_CONNECTION_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        username = data.get("username") or "unknown"
                        user_id = data.get("id") or "unknown"
                        return True, f"Connected as {username} (id={user_id})"
                    if resp.status == 401:
                        return False, "Invalid bot token"
                    if 400 <= resp.status < 500:
                        return False, f"Discord API error: {resp.status}"
                    # 5xx etc.
                    return False, f"Discord API error: {resp.status}"
        except asyncio.TimeoutError:
            return False, (
                f"Connection timed out after {TEST_CONNECTION_TIMEOUT_SECONDS}s"
            )
        except aiohttp.ClientError as e:
            return False, f"Connection failed: {e}"
        except Exception as e:  # noqa: BLE001
            logger.error(f"Unexpected error during Discord connection test: {e}")
            return False, f"Unexpected error: {e}"

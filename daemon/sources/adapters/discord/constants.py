"""Constants for the Discord source adapter.

Centralizes module-level constants used across the Discord adapter package.
Patterns are kept consistent with `daemon/sources/adapters/telegram.py` and
`daemon/sources/adapters/slack/adapter.py`.
"""

from __future__ import annotations

# LRU eviction limit for per-channel ordering locks. Matches Telegram's
# `MAX_CHAT_LOCKS=1000`. See telegram.py:34.
MAX_CHANNEL_LOCKS = 1000

# Interval (seconds) between periodic thread eviction passes. Matches the
# 1-hour cadence used by the Slack adapter's TTL eviction loop.
EVICTION_INTERVAL_SECONDS = 3600

# Gateway confirmation timeout (seconds). `start()` blocks until the
# discord.py `on_ready` event fires; if it does not fire within this window
# the client task is cancelled and `start()` raises RuntimeError.
GATEWAY_READY_TIMEOUT_SECONDS = 30.0

# Discord snowflake IDs are 17-19 digit integers. The external user ID
# scheme is:
#   DM:      dm:{user_id}
#   Channel: {guild_id}:{channel_id}
#   Thread:  {guild_id}:{parent_channel_id}:{thread_id}
# This regex is the single source of truth used by both `mapper.py` and
# adapter parsing; keep them in sync.
DISCORD_ID_PATTERN = r"^(dm:\d{17,19}|\d{17,19}:\d{17,19}(:\d{17,19})?)$"

# Discord hard message-length cap. Outbound messages longer than this are
# split via the priority-ordered boundary chain in
# `DiscordAdapter._split_message()`.
DISCORD_MAX_MESSAGE_LENGTH = 2000

# Gateway latency threshold (ms) above which `health_check()` returns False.
# Discord's API docs treat >5s heartbeat latency as effectively disconnected.
DISCORD_LATENCY_THRESHOLD_MS = 5000.0

# Default cap for the local outbound send concurrency semaphore. discord.py
# handles 429s and dynamic route buckets internally; this only prevents the
# adapter from overwhelming discord.py's HTTP handler.
DEFAULT_MAX_CONCURRENT_SENDS = 5

# Discord REST base URL — used by `test_connection()` for the pre-flight
# `users/@me` request.
DISCORD_API_BASE = "https://discord.com/api/v10"

# Time budget for the test_connection() pre-flight request.
TEST_CONNECTION_TIMEOUT_SECONDS = 15.0

# FIX 9: Discord bot-token format for ``test_connection()`` pre-flight.
# Real Discord bot tokens are 3 dot-separated segments:
#   - segment 1: base64-encoded bot user ID (24+ chars, [A-Za-z0-9_])
#   - segment 2: timestamp + hmac (6+ chars, [A-Za-z0-9_])
#   - segment 3: HMAC signature ([A-Za-z0-9_-])
# Tokens that do not match this shape are invalid by construction; we
# short-circuit ``test_connection`` instead of wasting an API call.
DISCORD_TOKEN_PATTERN = r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_-]+$"

# Requirements: Discord Source Adapter

Date: 2026-08-11T21:04:05Z
Author: planner[v2] via requirements-analysis worker
Status: Draft
Source Request: "Create a detailed requirements decomposition for a new Discord source adapter in the ensemble multi-agent daemon. Discord is whitelisted as a source type but has no adapter yet. We need to add one following the established patterns."

## Stakeholders

- **Requester:** Planner (dispatcher)
- **Affected users:** Discord bot operators, end users communicating with ensemble agents via Discord (DMs, server channels, threads)
- **Affected systems:** `daemon/sources/` adapter subsystem, source registry (`daemon/sources/registry.py`), ensemble daemon message pipeline (IncomingMessage → agent → OutgoingMessage), resilience utilities (`rate_limiter.py`, `circuit_breaker.py`)

---

## Functional Requirements

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-1 | Adapter shall connect to Discord via the Gateway (WebSocket) and maintain a persistent connection | Discord uses a WebSocket Gateway for real-time events; this is the only supported connection model | Must | Lifecycle |
| FR-2 | Adapter shall receive MESSAGE_CREATE events and emit IncomingMessage to the ensemble pipeline | Core purpose of any source adapter | Must | Message Receiving |
| FR-3 | Adapter shall send OutgoingMessage content to Discord channels, DMs, and threads | Round-trip reply capability required for agent-to-user communication | Must | Message Sending |
| FR-4 | Adapter shall start and stop cleanly via `start()` and `stop()` lifecycle methods | Conforms to MessageSourceAdapter ABC contract | Must | Lifecycle |
| FR-5 | Adapter shall require an explicit mention of the bot to activate in server channels | Avoids responding to every message in busy servers; mirrors Slack's `channel_require_mention` | Must | Mention Activation |
| FR-6 | Adapter shall always respond in DMs without requiring a mention | DMs are inherently directed; mention gating would block all private communication | Must | DM Support |
| FR-7 | Adapter shall track and reply within Discord threads | Threads are a primary conversation structure; replies must land in the correct thread context | Must | Thread Support |
| FR-8 | Adapter shall normalize Discord messages to IncomingMessage format | Unifies all source adapters to a common internal contract | Must | Normalization |
| FR-9 | Adapter shall convert ensemble plaintext to Discord-compatible formatting on send | Prevents garbled output (raw markdown leaks, unrendered mentions) | Must | Formatting |
| FR-10 | Adapter shall implement Gateway reconnection with identify/resume sequencing | Discord Gateway drops connections; without reconnection the adapter goes permanently deaf | Must | Lifecycle |
| FR-11 | Adapter shall pass `health_check()` returning `True` only when Gateway is connected and listening | Operators need a reliable liveness signal | Must | Lifecycle |
| FR-12 | Adapter shall implement `test_connection()` classmethod validating the bot token | Allows configuration validation without full adapter startup | Should | Lifecycle |
| FR-13 | Adapter shall strip LLM artifact tags (`<think>`, `<reasoning>`, `<scratchpad>`, `<reflection>`) from outgoing content | Matches Telegram adapter convention; prevents leaking internal LLM reasoning to Discord users | Should | Formatting |
| FR-14 | Adapter shall default agent routing to "ari" when no agent is specified in config | Matches existing adapters (Telegram, Slack) convention | Must | Routing |
| FR-15 | Adapter shall support optional guild/server restriction — ignore messages from non-allowlisted guilds | Multi-server bots need scoping to prevent unauthorized use | Should | Security |
| FR-16 | Adapter shall handle Discord mentions in incoming messages (translate `<@user_id>` to human-readable or strip) | Raw mention tags are noise in agent context; normalized text improves agent comprehension | Should | Normalization |
| FR-17 | Adapter shall handle image attachments on incoming messages | IncomingMessage supports `images: list[str]`; Discord users may send images | Should | Message Receiving |
| FR-18 | Adapter shall support `reload(new_config)` to update configuration without full restart | Enables live configuration changes; matches ABC optional override | Could | Lifecycle |
| FR-19 | Adapter shall support configurable per-channel mention requirements (some channels always-on, some mention-only, some off) | Mirrors Slack's granular `channel_require_mention` per-channel config | Could | Mention Activation |
| FR-20 | Adapter shall support reply-to message linking via Discord's message reference API | OutgoingMessage has `reply_to_id`; Discord supports reply threading via `message_reference` | Should | Message Sending |
| FR-21 | Adapter shall enforce an allowlist of bot user IDs and ignore messages from other bots | Prevents bot-to-bot infinite loops and irrelevant noise | Should | Security |
| FR-22 | Adapter shall implement `_split_message(content, max_length=2000) -> list[str]` to split outgoing content exceeding Discord's 2000-character limit into multiple messages, chunked at paragraph/line boundaries and sent sequentially | Discord rejects messages >2000 chars; a dedicated splitter preserves readability by avoiding mid-word breaks and respects the hard limit per send | Must | Formatting |

### Theme: Lifecycle

**FR-1:** Connect to Discord via WebSocket Gateway
- **Rationale:** Discord's only real-time event delivery mechanism is the Gateway WebSocket. Unlike Telegram's HTTP polling, the adapter must establish and maintain a WebSocket connection, complete the IDENTIFY handshake with the bot token, and subscribe to relevant Gateway events (GUILD_MESSAGES, DIRECT_MESSAGES).
- **Priority:** Must
- **Notes:** The Gateway requires heartbeat/ack sequencing. Must handle READY, RESUMED, and INVALID_SESSION events.

**FR-4:** `start()` / `stop()` lifecycle methods
- **Rationale:** The `MessageSourceAdapter` ABC (base.py:53-130) declares these as abstract. `start()` must initiate the Gateway connection and event loop. `stop()` must cleanly close the WebSocket and cancel any background tasks. The adapter must set `SourceStatus` transitions: STOPPED → STARTING → RUNNING → STOPPED/ERROR.
- **Priority:** Must
- **Notes:** Must be idempotent — calling `stop()` on an already-stopped adapter must not raise.

**FR-10:** Gateway reconnection with resume sequencing
- **Rationale:** Discord Gateway connections drop routinely (server restarts, network blips, rate limits on IDENTIFY). The adapter must detect disconnections and reconnect using either RESUME (if session is resumable and resume_gateway_url + sequence number available) or a fresh IDENTIFY. Must respect Discord's backoff requirements for reconnection and identify rate limits.
- **Priority:** Must
- **Notes:** Must store the last sequence number (`s`) and session ID to support RESUME. Must handle opcode 7 (Reconnect) and opcode 9 (Invalid Session).

**FR-11:** `health_check()`
- **Rationale:** Returns `True` only when the Gateway connection is active, the heartbeat loop is running, and the adapter has received a heartbeat ACK within the expected interval. Returns `False` (or `False` with error populated) during disconnection, reconnection attempts, or after stop.
- **Priority:** Must

**FR-12:** `test_connection()` classmethod
- **Rationale:** Validates the bot token by calling Discord's `GET /users/@me` endpoint (REST API, not Gateway). Returns `(True, "OK")` on valid token, `(False, "Invalid token")` or `(False, "Rate limited")` on failure.
- **Priority:** Should
- **Notes:** Optional ABC override; provide for configuration validation UX.

**FR-18:** `reload(new_config)`
- **Rationale:** Optional ABC override. May re-apply guild restrictions, mention config, or agent routing without tearing down the Gateway connection. Configuration changes requiring reconnection (e.g., bot token change) must trigger a full restart cycle.
- **Priority:** Could

### Theme: Message Receiving

**FR-2:** Receive MESSAGE_CREATE and emit IncomingMessage
- **Rationale:** The adapter listens for MESSAGE_CREATE Gateway events. For each qualifying message (passes mention gate, not from a bot, not from ignored guild), construct an `IncomingMessage` and call `_emit_message(msg)` (the base class helper that invokes `_on_message`).
- **Priority:** Must
- **Notes:** Must ignore the bot's own messages to prevent echo loops. Must filter MESSAGE_CREATE by intents (GUILD_MESSAGES for server channels, DIRECT_MESSAGES for DMs).

**FR-17:** Image attachments
- **Rationale:** Discord messages may contain attachments (images). If present, extract attachment URLs into `IncomingMessage.images`. If the message has no attachments, set `images=None`.
- **Priority:** Should

### Theme: Message Sending

**FR-3:** Send OutgoingMessage to Discord
- **Rationale:** Implements `send(message: OutgoingMessage) -> bool`. Decodes the `external_user_id` to determine the target (DM, channel, or thread). Sends content via the appropriate Discord REST API or Gateway send path. Returns `True` on success, `False` on failure (rate limited, network error, invalid target).
- **Priority:** Must

**FR-20:** Reply-to linking
- **Rationale:** If `OutgoingMessage.reply_to_id` is set, use Discord's `message_reference` field to create a reply. If the original message no longer exists, gracefully fall back to a non-reply send.
- **Priority:** Should

### Theme: Mention Activation

**FR-5:** Mention-required in server channels
- **Rationale:** In server (guild) channels, the adapter must only activate when the bot is explicitly mentioned (`<@bot_user_id>` or `<@!bot_user_id>` in message content) or replied to. Messages without a mention are ignored. This mirrors Slack's `channel_require_mention` and prevents the bot from responding to all server traffic.
- **Priority:** Must
- **Notes:** Discord MESSAGE_CREATE includes a `mentions` array; check for bot's user ID in it.

**FR-6:** DMs always active
- **Rationale:** DM channels are inherently private and directed; requiring a mention in a DM is counterintuitive and would block all private communication.
- **Priority:** Must

**FR-19:** Per-channel mention configuration
- **Rationale:** Some channels may want the bot active on all messages (e.g., a dedicated bot channel). Config should support per-channel override: `always_active` (respond to all), `require_mention` (default), `disabled` (ignore entirely).
- **Priority:** Could
- **Notes:** If unimplemented in v1, default all server channels to `require_mention`.

### Theme: Thread Support

**FR-7:** Thread tracking and reply
- **Rationale:** Discord threads are sub-channels with their own message streams. When a user mentions the bot in a thread, the reply must be sent back to that thread (not the parent channel). The adapter must extract `channel_id` from the MESSAGE_CREATE event and use it as the send target. Threads created mid-conversation must be handled (thread ID becomes the target for subsequent sends).
- **Priority:** Must
- **Notes:** Discord's `channel_id` in a MESSAGE_CREATE event is the thread's ID when the message is in a thread. No special parsing needed — the same send path works for channels and threads.

### Theme: Normalization

**FR-8:** Incoming message normalization
- **Rationale:** Convert Discord MESSAGE_CREATE to IncomingMessage dataclass (base.py:19-29). Mapping:
  - `external_user_id`: Derived from channel context using the canonical scheme: DM is `dm:{user_id}`, server channel is `{guild_id}:{channel_id}`, thread is `{guild_id}:{parent_channel_id}:{thread_id}` (see External User ID Scheme section below).
  - `content`: Discord message content after mention/format normalization (FR-16)
  - `source_id`: The adapter's source_id from SourceConfig
  - `images`: List of attachment URLs (FR-17) or None
  - `metadata`: Nested dict with all Discord-specific fields under a `discord` sub-key plus a top-level `agent` (the default routing agent). Example shape:
    ```python
    metadata = {
        "discord": {
            "channel_id": "...",
            "guild_id": "...",
            "thread_id": "...",
            "user_id": "...",
            "message_id": "...",
            "username": "...",
            "is_dm": False,
            "referenced_message_id": None,
            "mention_type": "mention",  # "mention" | "reply" | "dm" | "always_active"
        },
        "agent": self._default_agent,
    }
    ```
    All Discord-specific keys live under `metadata["discord"][...]` — never as flat top-level keys.
  - `message_type`: "text" (default)
  - `reply_to_id`: Discord message ID of referenced message (if reply), encoded in the external user ID scheme
- **Priority:** Must

**FR-16:** Mention normalization
- **Rationale:** Discord mentions appear as `<@user_id>` or `<@!user_id>` (nickname mention). For agent readability, strip the bot's own mention (already used for activation). For other mentions, either strip to username or leave as raw mention token — decide based on whether agents need the mention context.
- **Priority:** Should
- **Notes:** Channel mentions (`<#channel_id>`) and role mentions (`<@&role_id>`) should also be normalized or stripped.

### Theme: Formatting

**FR-9:** Outgoing format conversion
- **Rationale:** Discord renders markdown natively (bold `**`, italic `*`, code blocks, etc.). Ensemble agents output plaintext or markdown. For outgoing messages, pass through markdown content directly (Discord is markdown-compatible). Ensure no ensemble-internal formatting leaks.
- **Priority:** Must
- **Notes:** No conversion needed if ensemble uses standard markdown — Discord renders it natively. The main concern is ensuring agent output doesn't contain non-Discord-compatible markup.

**FR-13:** Strip LLM artifact tags
- **Rationale:** Agents may emit `<think>`, `<reasoning>`, `<scratchpad>`, `<reflection>` tags in their output. These are internal and must never reach Discord users. Implement `_strip_llm_artifact_tags()` matching the Telegram adapter's implementation.
- **Priority:** Should

**FR-22:** Message length splitting via `_split_message(content, max_length=2000) -> list[str]`
- **Rationale:** Discord enforces a strict 2000-character limit on message content and rejects oversize messages at the API. Agent responses frequently exceed this. The adapter MUST expose a `_split_message(content, max_length=2000) -> list[str]` helper that returns a list of message chunks, each `<= max_length` characters. `send()` calls `_split_message` on outgoing content and dispatches the chunks as separate sequential Discord messages via the REST API.
- **Priority:** Must
- **Behavior:**
  - If `len(content) <= max_length`, returns `[content]` (single chunk, no splitting).
  - Otherwise, splits at natural boundaries in this order of preference:
    1. Paragraph break (double newline `\n\n`).
    2. Single newline (`\n`).
    3. Sentence boundary (`. `, `! `, `? ` followed by a space and capital letter).
    4. Word boundary (whitespace).
    5. Hard cut at `max_length` (last resort, only when no boundary exists in the trailing window).
  - Each emitted chunk is `<= max_length` characters; the helper MUST guarantee this invariant.
  - The split is deterministic for a given input.
- **Notes:** No truncation-with-indicator; the splitter produces complete chunks that are each individually valid Discord messages. The 2000-character cap is the default but the helper is parameterized so the constant can be tested in isolation with smaller values.

### Theme: Routing

**FR-14:** Default agent "ari"
- **Rationale:** Both existing adapters (Telegram, Slack) default to "ari" as the target agent when no agent is specified. Discord must follow this convention for consistency.
- **Priority:** Must

### Theme: Security

**FR-15:** Guild restriction
- **Rationale:** Config may include `allowed_guild_ids: list[str]`. Messages from guilds not in this list are silently ignored. If the list is empty or absent, the bot responds in all guilds it is a member of.
- **Priority:** Should

**FR-21:** Bot message filtering
- **Rationale:** Ignore MESSAGE_CREATE events where `author.bot == true` (other bots) to prevent echo loops and noise. The adapter's own messages are already filtered by user ID matching.
- **Priority:** Should

---

## Non-Functional Requirements

| ID | Category | Requirement | Metric | Target | Measurement |
|----|----------|-------------|--------|--------|-------------|
| NFR-1 | Reliability | Gateway reconnection after network drop | Time to reconnect | < 30s including backoff | Integration test: kill WebSocket, measure reconnect latency |
| NFR-2 | Reliability | Gateway heartbeat compliance | Heartbeat interval adherence | Heartbeat sent within Discord-specified interval (typically 41.25s) | Unit test: verify heartbeat sent before ACK timeout |
| NFR-3 | Performance | Message receive-to-emit latency | Time from MESSAGE_CREATE to `_emit_message` call | < 100ms (excluding network transit) | Integration test: inject mock event, measure emit latency |
| NFR-4 | Performance | Message send latency | Time from `send()` call to Discord API acceptance | < 500ms (excluding Discord API latency) | Integration test: mock Discord API, measure send latency |
| NFR-5 | Resilience | Rate limiting (Discord global + per-route buckets) | No 429 responses after adapter-side limiting kicks in | 0 unhandled 429s after rate limiter warmup | Integration test: burst send, verify adapter queues/throttles |
| NFR-6 | Resilience | Circuit breaker on repeated Discord API failures | Circuit opens after N consecutive failures | Open after 5 failures, half-open after 60s (matches existing adapters) | Unit test: simulate failures, verify circuit opens/closes |
| NFR-7 | Reliability | Message ordering preservation per channel/thread/DM | Messages delivered to ensemble in order received | Strict ordering within same channel_id | Integration test: inject sequential events, verify order in emit log |
| NFR-8 | Reliability | `health_check()` accuracy | False negatives (reporting unhealthy when connected) | 0 false negatives during steady-state operation | Integration test: connected adapter, verify health_check returns True |
| NFR-9 | Scalability | Concurrent send channel locks | Maximum concurrent send targets tracked with LRU eviction | Per-channel ordering locks with LRU eviction, `MAX_CHANNEL_LOCKS=1000`, matching Telegram's `MAX_CHAT_LOCKS` precedent | Unit test: create 1000 lock entries, verify no overflow; load test beyond 1000 to verify LRU eviction |
| NFR-10 | Security | Bot token never logged or emitted in error messages | Token presence in logs/error strings | 0 occurrences | Automated grep over adapter source + integration test error paths |
| NFR-11 | Maintainability | Adapter follows existing single-file or multi-file pattern consistent with Telegram/Slack | Structural similarity to existing adapters | Reviewer confirms pattern conformance | Code review against Telegram (single file) and Slack (5-file module) |
| NFR-12 | Resource | Background tasks cleaned up on stop() | Lingering asyncio tasks after stop() | 0 lingering tasks | Integration test: stop adapter, scan event loop for adapter-owned tasks |
| NFR-13 | Reliability | Graceful handling of Discord API maintenance/outage | Adapter degrades gracefully (sets ERROR status) without crashing | No unhandled exception crashes the daemon | Integration test: simulate API timeout, verify ERROR status and no crash |
| NFR-14 | Performance | Startup time (Gateway connection + READY event) | Time from `start()` to RUNNING status | < 10s under normal conditions | Integration test: measure start() to RUNNING transition |

---

## Constraints

| ID | Type | Description | Source | Impact |
|----|------|-------------|--------|--------|
| C-1 | Technical | Discord Gateway (WebSocket) is the only real-time event delivery mechanism; no long-polling equivalent | Discord API | Adapter must manage WebSocket lifecycle, heartbeat, and reconnection — cannot reuse Telegram's HTTP polling pattern |
| C-2 | Technical | Discord enforces rate limits: global (50 req/s) and per-route buckets (varies by endpoint) | Discord API | Adapter must implement a dual-bucket rate limiter distinct from Telegram's TokenBucketLimiter and Slack's tiered limiter |
| C-3 | Technical | Message content limit: 2000 characters | Discord API | Adapter must split or truncate outgoing messages (FR-22) |
| C-4 | Technical | Privileged Gateway Intents required: MESSAGE_CONTENT intent (privileged) must be enabled in Discord Developer Portal and requested in IDENTIFY payload | Discord API | Bot token must have MESSAGE_CONTENT intent enabled; without it, message content is empty in server channels (works in DMs without privileged intent) |
| C-5 | Technical | Must implement `MessageSourceAdapter` ABC (base.py:53-130): constructor signature, `start()`, `stop()`, `send()`, `health_check()`, optional `test_connection()`, `reload()` | Ensemble architecture | Adapter API surface is fixed by the base class; no deviation allowed |
| C-6 | Technical | Must register via `elif source_type == "discord":` block in `registry.py:339-426` `_create_adapter_from_config()` | Ensemble architecture | Registration is a code change in registry.py — no factory/plugin mechanism exists |
| C-7 | Technical | discord.py library (if used) provides Gateway, rate limiting, and reconnection built-in — OR raw WebSocket implementation if discord.py is too heavy | Library choice | If discord.py is used, much of FR-1/FR-10/C-1/C-2 is handled by the library. If raw WebSocket, all Gateway logic is custom. Trade-off: dependency weight vs implementation effort |
| C-8 | Technical | External user ID scheme MUST use the canonical formats: `dm:{user_id}` (DM), `{guild_id}:{channel_id}` (server channel), `{guild_id}:{parent_channel_id}:{thread_id}` (thread), all matching the `DISCORD_ID_PATTERN` regex `^(dm:\d{17,19}|\d{17,19}:\d{17,19}(:\d{17,19})?)$` | Ensemble architecture | ID scheme must be deterministic, parseable, collision-free across guilds/channels/threads, and validated by the regex |
| C-9 | Business | Default agent routing to "ari" must match existing adapters | Ensemble convention | Routing default is fixed; configurable per source |
| C-10 | Technical | Postgres is the primary DB; source adapter persistence (if any) must support dual SQLite/Postgres | Ensemble DB convention | Adapter should avoid direct DB writes; use existing repository patterns |
| C-11 | Technical | Configuration model must fit within SourceConfig.config dict and SourceConfig.credentials dict (base.py:42-51) | Ensemble architecture | No custom config class; all Discord-specific config is dict-based |
| C-12 | Technical | SourceStatus enum is fixed: STOPPED, STARTING, RUNNING, ERROR | Ensemble architecture | No custom status values; adapter must use only these four |
| C-13 | Technical | Must use resilience utilities from `daemon/sources/rate_limiter.py` and `daemon/sources/circuit_breaker.py`, OR provide custom implementations in the adapter directory (as Slack does with `SlackTieredRateLimiter`) | Ensemble architecture | Discord's rate limiting model differs enough from Telegram/Slack that a custom limiter is likely needed, following the Slack pattern |
| C-14 | Technical | CircuitBreaker configuration should match existing adapters: threshold=5, reset_timeout=60s | Ensemble convention | Resilience behavior is consistent across adapters |
| C-15 | Technical | `_emit_message(msg)` is the base class helper that calls `_on_message`; adapter must use this, not call `_on_message` directly | Ensemble architecture | Emit path is fixed |

---

## Acceptance Criteria

### FR-1: Gateway Connection

**AC-1.1** (happy path)
- **Given:** A valid Discord bot token and network connectivity
- **When:** `start()` is called
- **Then:** Adapter establishes WebSocket connection to Discord Gateway, completes IDENTIFY handshake, receives READY event, transitions status from STARTING → RUNNING
- **Test type:** integration (mock Gateway server) / e2e (real Discord bot)

**AC-1.2** (edge case: invalid token)
- **Given:** An invalid or expired Discord bot token
- **When:** `start()` is called
- **Then:** Adapter receives authentication error from Gateway, transitions to ERROR status, populates error property, does NOT crash the daemon
- **Test type:** integration (mock Gateway returning op 9 invalid session / HTTP 401 on token validation)

**AC-1.3** (error case: network unreachable)
- **Given:** No network connectivity to Discord Gateway
- **When:** `start()` is called
- **Then:** Adapter retries connection with backoff, remains in STARTING or transitions to ERROR after max retries, does NOT crash
- **Test type:** integration (block WebSocket endpoint)

### FR-2: Message Reception

**AC-2.1** (happy path: server channel message with mention)
- **Given:** Adapter is RUNNING, bot is mentioned in a server channel message
- **When:** MESSAGE_CREATE event arrives
- **Then:** `_emit_message()` is called with an IncomingMessage containing the message content (mention stripped), source_id set, and `metadata["discord"]` containing `guild_id`, `channel_id`, `message_id`, and `user_id` (nested under `discord` key, not flat at the top level)
- **Test type:** integration (inject mock MESSAGE_CREATE event)

**AC-2.2** (edge case: DM message)
- **Given:** Adapter is RUNNING, user sends a DM to the bot
- **When:** MESSAGE_CREATE event arrives from a DM channel
- **Then:** `_emit_message()` is called with IncomingMessage, `metadata["discord"]["is_dm"] == True`, no mention required
- **Test type:** integration

**AC-2.3** (edge case: thread message)
- **Given:** Adapter is RUNNING, user mentions bot in a thread
- **When:** MESSAGE_CREATE event arrives from a thread channel
- **Then:** `_emit_message()` is called with IncomingMessage, `metadata["discord"]["thread_id"]` is populated, and `external_user_id` encodes thread context (canonical scheme `{guild_id}:{parent_channel_id}:{thread_id}`) for reply routing
- **Test type:** integration

**AC-2.4** (error case: message from another bot)
- **Given:** Adapter is RUNNING
- **When:** MESSAGE_CREATE event arrives with `author.bot=true`
- **Then:** Message is silently ignored; `_emit_message()` is NOT called
- **Test type:** integration

### FR-21: Bot Message Filtering and Allowlist

**AC-21.1** (allowlisted bot message)
- **Given:** Config has `allowed_bot_ids=["123"]`, and a MESSAGE_CREATE event arrives from bot ID 123
- **When:** The message is processed
- **Then:** The message bypasses the default bot-skip filter and is eligible for normal processing
- **Test type:** integration

**AC-21.2** (non-allowlisted bot message)
- **Given:** Config has `allowed_bot_ids=["123"]`, and a MESSAGE_CREATE event arrives from bot ID 456
- **When:** The message is processed
- **Then:** The message is silently ignored; `_emit_message()` is NOT called
- **Test type:** integration

### FR-3: Message Sending

**AC-3.1** (happy path: send to server channel)
- **Given:** Adapter is RUNNING, OutgoingMessage with a channel-scoped external_user_id
- **When:** `send(message)` is called
- **Then:** Message content is sent to the Discord channel via REST API or Gateway, method returns `True`
- **Test type:** integration (mock Discord API)

**AC-3.2** (happy path: send to DM)
- **Given:** Adapter is RUNNING, OutgoingMessage with a DM-scoped external_user_id
- **When:** `send(message)` is called
- **Then:** Message is sent to the DM channel, returns `True`
- **Test type:** integration

**AC-3.3** (happy path: send to thread)
- **Given:** Adapter is RUNNING, OutgoingMessage with a thread-scoped external_user_id
- **When:** `send(message)` is called
- **Then:** Message is sent to the thread channel, returns `True`
- **Test type:** integration

**AC-3.4** (error case: rate limited)
- **Given:** Adapter is RUNNING, Discord API returns 429
- **When:** `send(message)` is called
- **Then:** Adapter respects `retry_after` header, queues or retries message, eventually returns `True` (if retried within timeout) or `False` (if timeout exceeded)
- **Test type:** integration (mock 429 response)

**AC-3.5** (error case: channel not found / deleted)
- **Given:** Adapter is RUNNING, target channel has been deleted
- **When:** `send(message)` is called
- **Then:** Discord API returns 404/403, adapter returns `False`, populates error, does NOT crash
- **Test type:** integration

### FR-4: Lifecycle (start/stop)

**AC-4.1** (happy path: clean start and stop)
- **Given:** Valid configuration
- **When:** `start()` then `stop()` are called
- **Then:** Gateway connects, status transitions STOPPED→STARTING→RUNNING→STOPPED, all background tasks (heartbeat, event listener) are cancelled, no lingering asyncio tasks
- **Test type:** integration

**AC-4.2** (edge case: stop when already stopped)
- **Given:** Adapter is in STOPPED status
- **When:** `stop()` is called
- **Then:** No exception raised; no-op
- **Test type:** unit

**AC-4.3** (edge case: start when already running)
- **Given:** Adapter is in RUNNING status
- **When:** `start()` is called again
- **Then:** No duplicate connection; either no-op or raises a clear error (define behavior in implementation)
- **Test type:** unit

### FR-5: Mention Activation in Servers

**AC-5.1** (happy path: mentioned, activates)
- **Given:** Adapter is RUNNING in a server channel
- **When:** Message arrives with bot mentioned in `mentions` array
- **Then:** Message is processed, IncomingMessage emitted
- **Test type:** integration

**AC-5.2** (negative case: not mentioned, ignored)
- **Given:** Adapter is RUNNING in a server channel with require_mention
- **When:** Message arrives WITHOUT bot in `mentions` array
- **Then:** Message is silently ignored; `_emit_message()` NOT called
- **Test type:** integration

**AC-5.3** (edge case: reply to bot counts as activation)
- **Given:** Adapter is RUNNING in a server channel
- **When:** Message arrives as a reply to the bot's previous message (referenced message belongs to bot)
- **Then:** Message is processed (reply acts as implicit mention), IncomingMessage emitted
- **Test type:** integration

### FR-6: DM Support (No Mention Required)

**AC-6.1** (happy path: DM without mention)
- **Given:** Adapter is RUNNING
- **When:** DM message arrives without any mention
- **Then:** Message is processed, IncomingMessage emitted
- **Test type:** integration

### FR-7: Thread Support

**AC-7.1** (happy path: reply in thread)
- **Given:** Bot was mentioned in a thread, IncomingMessage was emitted
- **When:** Agent responds via `send()` with thread-scoped external_user_id
- **Then:** Response lands in the correct thread, not the parent channel
- **Test type:** integration

**AC-7.2** (edge case: thread created during conversation)
- **Given:** Bot is in a conversation in a server channel
- **When:** User creates a thread from the bot's message and mentions bot there
- **Then:** New thread context is tracked, subsequent replies go to the thread
- **Test type:** integration

### FR-8: Incoming Message Normalization

**AC-8.1** (happy path: full normalization)
- **Given:** MESSAGE_CREATE event with content, author, channel_id, guild_id, message_id
- **When:** Adapter processes the event
- **Then:** IncomingMessage is constructed with all fields populated per the mapping table in FR-8, content has bot mention stripped, and all Discord-specific context lives under `metadata["discord"]` (nested format) with `agent` set at the top level of metadata
- **Test type:** unit

**AC-8.2** (edge case: empty content with attachment only)
- **Given:** MESSAGE_CREATE event with empty content but image attachment
- **When:** Adapter processes the event
- **Then:** IncomingMessage.content is empty string, `images` contains the attachment URL, message is still emitted (or optionally filtered if empty-only messages should be ignored)
- **Test type:** unit

### FR-9: Outgoing Format Conversion

**AC-9.1** (happy path: markdown passthrough)
- **Given:** OutgoingMessage with markdown content (bold, code blocks, lists)
- **When:** `send()` is called
- **Then:** Discord renders the markdown correctly (no double-escaping, no broken syntax)
- **Test type:** integration (send to mock Discord, inspect API payload)

### FR-10: Gateway Reconnection

**AC-10.1** (happy path: resume after transient drop)
- **Given:** Adapter is RUNNING with an active session
- **When:** WebSocket connection drops (network blip)
- **Then:** Adapter detects disconnection, reconnects within 30s, resumes session using stored session_id and sequence number, receives RESUMED event, no messages lost (if resume succeeds)
- **Test type:** integration (simulate WebSocket close mid-operation)

**AC-10.2** (edge case: session invalid, full re-IDENTIFY)
- **Given:** Adapter is RUNNING
- **When:** Gateway sends opcode 9 (Invalid Session)
- **Then:** Adapter performs a full re-IDENTIFY (not RESUME), re-establishes all state
- **Test type:** integration

**AC-10.3** (edge case: opcode 7 reconnect requested)
- **Given:** Adapter is RUNNING
- **When:** Gateway sends opcode 7 (Reconnect)
- **Then:** Adapter gracefully disconnects and reconnects (resume preferred)
- **Test type:** integration

### FR-11: Health Check

**AC-11.1** (healthy)
- **Given:** Adapter is RUNNING, Gateway connected, heartbeat active
- **When:** `health_check()` is called
- **Then:** Returns `True`
- **Test type:** integration

**AC-11.2** (unhealthy: disconnected)
- **Given:** Adapter Gateway connection has dropped, not yet reconnected
- **When:** `health_check()` is called
- **Then:** Returns `False`, error property describes the disconnection
- **Test type:** integration

### FR-12: Test Connection

**AC-12.1** (valid token)
- **Given:** Valid bot token
- **When:** `test_connection(config)` is called
- **Then:** Returns `(True, "OK")` or similar success message
- **Test type:** integration (mock Discord REST `/users/@me`)

**AC-12.2** (invalid token)
- **Given:** Invalid bot token
- **When:** `test_connection(config)` is called
- **Then:** Returns `(False, "Invalid token")` or similar descriptive error
- **Test type:** integration

### FR-13: LLM Artifact Tag Stripping

**AC-13.1** (tags stripped)
- **Given:** OutgoingMessage content contains `<think>internal reasoning</think>`
- **When:** `send()` is called
- **Then:** Discord receives content WITHOUT the `<think>` tags and their content
- **Test type:** unit

### FR-14: Default Agent Routing

**AC-14.1** (default routing)
- **Given:** SourceConfig.config does not specify an agent
- **When:** Adapter is created and started
- **Then:** Incoming messages are routed to agent "ari"
- **Test type:** integration

### FR-15: Guild Restriction

**AC-15.1** (allowed guild)
- **Given:** Config has `allowed_guild_ids=["123"]`, message arrives from guild 123
- **When:** MESSAGE_CREATE is processed
- **Then:** Message is processed normally
- **Test type:** integration

**AC-15.2** (disallowed guild)
- **Given:** Config has `allowed_guild_ids=["123"]`, message arrives from guild 456
- **When:** MESSAGE_CREATE is processed
- **Then:** Message is silently ignored
- **Test type:** integration

**AC-15.3** (allowed channel)
- **Given:** Config has `allowed_channels=["789"]`, message arrives from channel 789
- **When:** MESSAGE_CREATE is processed
- **Then:** Message is processed normally
- **Test type:** integration

**AC-15.4** (disallowed channel)
- **Given:** Config has `allowed_channels=["789"]`, message arrives from channel 456
- **When:** MESSAGE_CREATE is processed
- **Then:** Message is silently ignored
- **Test type:** integration

### FR-22: Message Length Handling

**AC-22.1** (split long message)
- **Given:** OutgoingMessage content is 3000 characters
- **When:** `send()` is called
- **Then:** `_split_message(content, max_length=2000)` returns a list of chunks each `<= 2000` characters; `send()` dispatches them as multiple sequential messages to Discord, all under the 2000-char limit, split at a paragraph/line/sentence boundary (not mid-word)
- **Test type:** integration

**AC-22.2** (exact boundary)
- **Given:** OutgoingMessage content is exactly 2000 characters
- **When:** `_split_message(content, max_length=2000)` is called
- **Then:** Returns a single-element list `[content]`; `send()` dispatches one message without splitting or truncation
- **Test type:** unit

**AC-22.3** (no natural boundary)
- **Given:** OutgoingMessage content is 3000 characters with no `\n`, `\n\n`, or sentence boundaries (e.g., a single very long token)
- **When:** `_split_message(content, max_length=2000)` is called
- **Then:** Returns at least two chunks, each `<= 2000` characters; the last chunk may be `< 2000`. Splits occur at word boundaries where possible; the last-resort hard cut is only used when no whitespace boundary fits in the trailing window
- **Test type:** unit

**AC-22.4** (chunk invariant)
- **Given:** A set of random strings of varying lengths (0–10 000 chars)
- **When:** `_split_message(content, max_length=2000)` is called
- **Then:** Every returned chunk is `<= 2000` characters, and the concatenation of all chunks equals the original content (no characters dropped or duplicated)
- **Test type:** unit (property-based)

### FR-23–FR-25: External User ID Scheme

**AC-23.1** (DM ID round-trip)
- **Given:** A DM message from user 999 (DMs have no guild)
- **When:** IncomingMessage is constructed
- **Then:** `external_user_id` follows the canonical DM scheme `dm:{user_id}` (e.g., `dm:999888777666555`), and when used in OutgoingMessage, `send()` targets the correct DM channel
- **Test type:** unit

**AC-23.2** (Server Channel ID round-trip)
- **Given:** A server channel message in channel 555 of guild 111
- **When:** IncomingMessage is constructed
- **Then:** `external_user_id` follows the canonical channel scheme `{guild_id}:{channel_id}` (e.g., `111222:333444`), and `send()` targets channel 555
- **Test type:** unit

**AC-23.3** (Thread ID round-trip)
- **Given:** A thread message in thread 777 of channel 555 in guild 111
- **When:** IncomingMessage is constructed
- **Then:** `external_user_id` follows the canonical thread scheme `{guild_id}:{parent_channel_id}:{thread_id}` (e.g., `111222:333444:555666`), and `send()` targets thread 777
- **Test type:** unit

**AC-23.4** (regex validation)
- **Given:** A `send()` call with an `external_user_id` that does not match `DISCORD_ID_PATTERN`
- **When:** The adapter parses the ID
- **Then:** Parser raises a clear `ValueError` and `send()` returns `False` without calling the Discord API
- **Test type:** unit

**AC-23.5** (intent config dict format)
- **Given:** SourceConfig with `intents: {"guilds": true, "guild_messages": true, "message_content": true, "dm_messages": true}` (dict form)
- **When:** The adapter constructs the discord.py `Intents` object
- **Then:** Each intent key is mapped to the corresponding discord.py Intents flag, and any list-form intents (e.g., `["guilds"]`) are rejected at config-load time with a clear validation error
- **Test type:** unit

---

## Configuration Model Requirements

### Required Credentials

| Key | Type | Description |
|-----|------|-------------|
| `bot_token` | str | Discord bot token (from Discord Developer Portal → Bot → Token). Required. Must be kept secret; never logged. |

### Required Config Keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `agent` | str | `"ari"` | Default agent to route incoming messages to |
| `intents` | dict[str, bool] | `{"guilds": true, "guild_messages": true, "message_content": true, "dm_messages": true}` | Discord Gateway intents to request as a boolean dict matching discord.py's `Intents` API. Each key is an intent name; the value indicates whether it is enabled. `message_content` is a privileged intent and must be enabled in the Discord Developer Portal. The adapter MUST use the dict form (e.g. `{"message_content": True}`), not a list of strings. |

### Optional Config Keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `allowed_guild_ids` | list[str] | `[]` (all guilds) | Restrict bot to specific guild/server IDs. Empty = respond in all guilds. |
| `allowed_channels` | list[str] or None | `None` (all channels) | If set, only process messages from channels in this list. Empty or unset = allow all channels. |
| `allowed_bot_ids` | list[str] or None | `None` (skip all bots) | If set, messages from bots with IDs in this list bypass the default bot-skip filter. Empty or unset = skip all bot messages. |
| `require_mention` | bool | `True` | Whether bot requires explicit mention in server channels to activate. Overridden by per-channel config. |
| `channel_mention_config` | dict[str, str] | `{}` | Per-channel mention mode: keys are channel IDs, values are `"always_active"`, `"require_mention"`, or `"disabled"`. |
| `ignore_bot_messages` | bool | `True` | Whether to ignore messages from other bots (author.bot == true). |
| `strip_llm_artifact_tags` | bool | `True` | Whether to strip `<think>`, `<reasoning>`, `<scratchpad>`, `<reflection>` tags from outgoing messages. |
| `max_message_length` | int | `2000` | Discord message character limit for splitting/truncation. |

### Example SourceConfig

```json
{
  "source_id": "discord-prod",
  "source_type": "discord",
  "name": "Production Discord Bot",
  "config": {
    "agent": "ari",
    "intents": {"guilds": true, "guild_messages": true, "message_content": true, "dm_messages": true},
    "allowed_guild_ids": ["111222333444555666"],
    "allowed_channels": ["222333444555666777"],
    "allowed_bot_ids": ["333444555666777888"],
    "require_mention": true
  },
  "credentials": {
    "bot_token": "<secret>"
  },
  "enabled": true
}
```

---

## External User ID Scheme Requirements

The adapter must encode Discord channel context into a single `external_user_id` string so that `send()` can route replies to the correct target. The scheme is canonical and namespace-typed (the DM prefix `dm:` distinguishes DMs from numeric-only channel/thread IDs). This differs from Slack's `DM:{ws}:{user}` style — Discord IDs use compact numeric-only forms for channel/thread (no `{workspace}` namespace because the guild ID is the namespace), and use a `dm:` prefix for DMs to avoid ambiguity with guild channels.

### Canonical ID Formats

| Entity | Format | Example |
|--------|--------|---------|
| DM Channel | `dm:{user_id}` | `dm:999888777666555` |
| Server Channel | `{guild_id}:{channel_id}` | `111222:333444` |
| Thread | `{guild_id}:{parent_channel_id}:{thread_id}` | `111222:333444:555666` |

### Validation Regex (DISCORD_ID_PATTERN)

External user IDs MUST match the canonical regex:

```
^(dm:\d{17,19}|\d{17,19}:\d{17,19}(:\d{17,19})?)$
```

- `dm:\d{17,19}` — DM (Snowflake user ID, 17–19 digits)
- `\d{17,19}:\d{17,19}` — Server channel (guild + channel)
- `\d{17,19}:\d{17,19}:\d{17,19}` — Thread (guild + parent channel + thread)

The parser MUST reject any ID that does not match this pattern. Use the regex as the single source of truth for validation.

### Requirements

| ID | Requirement | Rationale | Priority |
|----|-------------|-----------|----------|
| ID-1 | External user IDs must be deterministic and reproducible from MESSAGE_CREATE event data | Same message always produces the same ID; enables deduplication and reply routing | Must |
| ID-2 | External user IDs must be unambiguously parseable by `send()` to extract the target channel/thread/DM | `send()` must know where to deliver; parsing must not collide across entity types | Must |
| ID-3 | External user IDs must not collide across guilds or adapters | Two channels with the same ID in different guilds must be distinguishable | Must |
| ID-4 | The author's Discord user ID should be stored in metadata (under `discord.user_id`) for user identification | external_user_id is the conversation target (channel/thread), not the user; author is contextual | Should |
| ID-5 | Thread IDs must include parent channel ID for context | Enables navigation back to the parent channel if needed | Should |
| ID-6 | External user IDs MUST match `DISCORD_ID_PATTERN` regex above; parser rejects any non-conforming value | Defense-in-depth: prevents injection of arbitrary strings as routing keys | Must |

### Metadata Fields

`IncomingMessage.metadata` for Discord MUST use a **nested** structure. All Discord-specific keys live under `metadata["discord"][...]`; routing/control keys (e.g. `agent`) live at the top level. Flat keys like `discord_guild_id` are forbidden.

Example:

```python
metadata = {
    "discord": {
        "guild_id": "111222",            # str | None (None for DMs)
        "channel_id": "333444",          # str (or thread ID for thread messages)
        "thread_id": None,               # str | None
        "message_id": "555666",          # str (Discord message ID, for reply reference)
        "user_id": "777888",             # str (author's Discord user ID)
        "username": "alice",             # str (author's username)
        "is_dm": False,                  # bool
        "referenced_message_id": None,   # str | None (message ID of referenced message, if reply)
        "mention_type": "mention",       # "mention" | "reply" | "dm" | "always_active"
    },
    "agent": "ari",                      # top-level: default routing agent
}
```

Field reference:

| Path | Type | Description |
|------|------|-------------|
| `metadata["discord"]["guild_id"]` | `str \| None` | Guild ID (None for DMs) |
| `metadata["discord"]["channel_id"]` | `str` | Channel ID (or thread ID for thread messages) |
| `metadata["discord"]["thread_id"]` | `str \| None` | Thread ID if message is in a thread |
| `metadata["discord"]["message_id"]` | `str` | Discord message ID (for reply reference) |
| `metadata["discord"]["user_id"]` | `str` | Author's Discord user ID |
| `metadata["discord"]["username"]` | `str` | Author's username |
| `metadata["discord"]["is_dm"]` | `bool` | Whether message is from a DM |
| `metadata["discord"]["referenced_message_id"]` | `str \| None` | Message ID of referenced message (if reply) |
| `metadata["discord"]["mention_type"]` | `str \| None` | How the bot was activated: `"mention"`, `"reply"`, `"dm"`, `"always_active"` |
| `metadata["agent"]` | `str` | Default agent to route the message to (e.g. `"ari"`) |

---

## Resolved Decisions (Leader Call)

The following open questions were resolved by the Leader before the requirements analysis closed. These decisions are no longer open and MUST be treated as binding requirements.

| # | Topic | Decision | Status | Impact |
|---|-------|----------|--------|--------|
| RD-1 | Guild sharding (auto-shard when guild count exceeds 2500) | **Deferred to v2.** v1 ships with a single Gateway client. Sharding is added only when measured guild count crosses the session-start threshold. | RESOLVED | Removes the need for shard-aware ownership, session-start coordination, and per-shard event ownership in v1. Documented in "Out of Scope" below. |
| RD-2 | `MESSAGE_CONTENT` privileged intent — behavior when intent is not granted | **FAIL-CLOSED.** If `message_content` is configured but not granted in the Developer Portal, `start()` MUST log a clear error describing the missing privileged intent and raise. The adapter MUST NOT silently start with empty content; users must explicitly opt out by removing the intent from config (which is then a deployment choice, not a silent default). | RESOLVED | Removes the "metadata-only operation" open question. The adapter fails fast with actionable diagnostics instead of producing an apparently-healthy but non-functional source. See NFR-13 (graceful degradation) for the distinction between this and runtime API failures. |
| RD-3 | Archived thread send policy | **Route to parent channel.** When a thread is archived (Discord auto-archive after inactivity) and the adapter receives an `OutgoingMessage` targeting that thread, `send()` MUST rewrite the target to the parent channel and dispatch there. The thread context is preserved in `metadata["discord"]["thread_id"]` for the agent's awareness, but the message lands in the parent channel. Reopening archived threads is NOT attempted automatically. | RESOLVED | Removes the "reopen / redirect / fail" open question. Implementation lives in the thread-manager / send path; the original thread ID is preserved in metadata for traceability. |
| RD-4 | `MAX_CHANNEL_LOCKS` constant for per-channel ordering locks | **1000.** The adapter MUST use `MAX_CHANNEL_LOCKS=1000` with LRU eviction, matching Telegram's `MAX_CHAT_LOCKS` precedent. The constant is a top-level adapter-level constant, configurable via `config["max_channel_locks"]` (optional override). | RESOLVED | Removes the Slack-vs-Telegram precedent ambiguity (Slack uses 100, Telegram uses 1000). Discord inherits Telegram's larger value because guilds commonly span thousands of channels. See NFR-9. |

## Gaps & Ambiguities

The following items remain open and need caller input before implementation proceeds. They are NOT blocking for v1 design but should be resolved before the adapter ships to production.

| # | Gap / Ambiguity | Question for Caller | Severity |
|---|-----------------|---------------------|----------|
| 1 | Library choice: discord.py (full-featured, handles Gateway/rate-limiting/reconnection) vs raw WebSocket implementation (lighter, more control, more code) | Should we use discord.py (or a lightweight variant like discordpy-stubs) or implement the Gateway protocol directly? discord.py would drastically reduce implementation effort (FR-1, FR-10, C-1, C-2 handled by library) but adds a significant dependency. | High |
| 2 | Emoji/reaction handling: Should the bot send reactions to messages (e.g., 🤔 while thinking, ✅ when done)? | Are reactions in scope for v1? They are not part of the OutgoingMessage model currently. | Medium |
| 3 | Embed support: Discord supports rich embeds (title, description, fields, color, images). Should OutgoingMessage support sending embeds, or only text? | Is embed support needed? If so, how should the ensemble model represent embeds in OutgoingMessage? Currently OutgoingMessage only has `content: str`. | Medium |
| 4 | Component/interaction support: Discord supports buttons, select menus, and modals via message components. Should these be in scope? | Are interactive components needed for v1? Likely out of scope, but confirmation needed. | Low |
| 5 | Voice channel support: Discord supports voice. The adapter is text-only, but should the presence/status be configurable? | Is bot status/presence (online, idle, dnd, custom status) configurable? | Low |
| 6 | Multi-message handling: If a user sends multiple messages quickly, should they be batched/coalesced into one IncomingMessage? | Should rapid sequential messages be merged (like Telegram sometimes does) or treated as separate? | Medium |
| 7 | Forum channel support: Discord has forum channels with posts (which are threads). Are these handled identically to threads? | Should forum posts be treated as threads for the purposes of this adapter? | Low |
| 8 | Error notification to user: When the adapter encounters an error (rate limit, send failure), should it notify the user in Discord? | Should send failures produce a user-visible error message in Discord (e.g., "Sorry, I couldn't send that")? | Medium |
| 9 | File/image sending: OutgoingMessage currently only supports `content: str`. Should the adapter support sending images/files back to Discord? | Is outbound image/file support needed? If so, the OutgoingMessage model needs extension or adapter-specific handling. | Medium |
| 10 | Rate limiter strategy: Discord's rate limit headers include bucket info, global flag, and retry_after. Should the adapter implement the Discord-specific per-route bucket tracking (more accurate, more code) or a simpler global token bucket approximation (less accurate, less code)? | Which rate limiting strategy is preferred for v1? Discord's headers are the authoritative source, but a simpler limiter may suffice for initial deployment. | Medium |
| 11 | Message edit/delete events: Discord sends MESSAGE_UPDATE and MESSAGE_DELETE events. Should the adapter handle edits (re-process) or ignore them? | Should message edits trigger re-processing? If ignored, should they be logged? | Low |
| 12 | Presence/typing indicators: Should the bot show "typing" while the agent is processing (long-running LLM calls)? | Should the adapter send typing indicators during agent processing? This improves UX but requires a periodic heartbeat to Discord's typing endpoint. | Medium |

## Assumptions

| # | Assumption | Reason | Risk if Wrong |
|---|------------|--------|---------------|
| 1 | discord.py library will be used for Gateway management, rate limiting, and reconnection | discord.py is the de facto Python Discord library, handles all Gateway complexity, and reduces implementation effort by 60-80%. Raw WebSocket would require reimplementing heartbeat, resume, rate limiting, and event dispatch. | If discord.py is rejected as too heavy, the adapter must implement all Gateway logic from scratch, significantly increasing scope and risk. FR-1, FR-10, C-1, C-2 would require custom implementations. |
| 2 | The MESSAGE_CONTENT privileged intent will be enabled in the Discord Developer Portal | Without MESSAGE_CONTENT intent, server channel messages arrive with empty content. DMs work without it but server channels (the primary use case) do not. | If not enabled, the bot cannot read message content in servers, rendering it non-functional for the primary use case. Must be documented as a setup prerequisite. |
| 3 | Single-file adapter structure (like Telegram) is preferred over multi-file (like Slack) unless complexity demands otherwise | Discord adapter complexity is between Telegram (single file, HTTP polling) and Slack (5 files, WebSocket + tiered rate limiter + thread manager + blocks). A single file is manageable if discord.py abstracts Gateway complexity. | If the adapter grows complex (custom rate limiter, formatter, thread manager), a multi-file structure may be needed mid-implementation. |
| 4 | The adapter does NOT need to persist conversation state (thread mappings, channel locks) to the database | Existing adapters (Telegram, Slack) keep lock state in memory (OrderedDict). Source adapters are stateless across restarts by design — the ensemble session hierarchy handles conversation continuity. | If DB persistence is needed, the adapter must use existing repository patterns (C-10) and dual SQLite/Postgres support, adding scope. |
| 5 | Outgoing messages use plain markdown that Discord renders natively — no format conversion needed beyond LLM tag stripping | Discord supports standard markdown (bold, italic, code blocks, lists). Ensemble agents output markdown or plaintext. No transformation layer needed unless agent output contains non-standard markup. | If agents output non-Discord-compatible markup (e.g., custom XML, HTML), a conversion layer is needed. |
| 6 | External user IDs use the canonical scheme (`dm:{user_id}` for DMs; `{guild_id}:{channel_id}` for channels; `{guild_id}:{parent_channel_id}:{thread_id}` for threads), with the `dm:` prefix distinguishing DMs from numeric-only channel/thread IDs. The Discord guild ID serves as the namespace for server channels/threads, so no separate source-type prefix is needed. | Discord IDs are Snowflakes (17–19 digit integers); guild ID is the natural namespace, while the `dm:` prefix prevents a DM channel ID from being confused with a guild channel ID. Routing layer consumes the ID as-is via the `DISCORD_ID_PATTERN` regex. | If the ensemble's routing layer expects prefixed IDs matching Slack's format, the parser may need a normalization step. This must be verified against the existing external_user_id consumer. |
| 7 | The bot ignores its own messages via user ID check (not via a flag from the Gateway) | Discord MESSAGE_CREATE includes `author.id`. The adapter knows its own bot user ID from the READY event. Checking `author.id == self.bot_user_id` filters self-messages. | If the bot user ID is not reliably known at READY time, an alternative filter is needed. Discord.py handles this automatically. |
| 8 | Health check heartbeat interval is governed by Discord (typically ~41.25s); the adapter should not override it | Discord sends `heartbeat_interval` in the HELLO event. The adapter must use this value, not a hardcoded one. | If a hardcoded interval is used, Discord may disconnect for heartbeat non-compliance. |
| 9 | The adapter will use the same CircuitBreaker configuration as existing adapters (threshold=5, reset_timeout=60s) | Consistency across adapters (Telegram, Slack both use these values). Discord API failures are similar in nature. | If Discord's failure characteristics differ (e.g., longer recovery times), the circuit breaker params may need tuning. |

## Out of Scope (Deferred)

- **Voice channel support** — text-only adapter; voice is a fundamentally different subsystem requiring audio processing
- **Slash commands / application commands** — Discord slash commands require separate registration and interaction handling; defer to a future enhancement once basic message flow is proven
- **Message components (buttons, select menus, modals)** — interactive UI components require OutgoingMessage model extensions and interaction event handling; out of scope for v1
- **Rich embeds (outbound)** — OutgoingMessage only supports `content: str`; embed support requires model extension; defer to v2
- **Forum channel specialization** — forum posts are structurally threads; treat as threads for v1 without special handling
- **Scheduled messages / delayed sends** — no scheduling capability in the adapter; ensemble job queue handles scheduling if needed
- **Webhook-only mode** — some Discord bots use webhooks for sending; the adapter uses bot token + REST/Gateway for full bidirectional communication
- **Multi-token / sharding** — Discord requires sharding for bots in >2500 guilds; defer until scale demands it
- **Outbound image/file sending** — OutgoingMessage model only supports text content; file sending requires model extension or adapter-specific path; defer
- **Per-user rate limiting** — adapter-level rate limiting targets Discord API limits (global/per-route), not per-user throttling; user-level throttling is the ensemble's responsibility
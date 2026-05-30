# Phase 1: Core Adapter + Integration Points

## Objective

Create the SlackAdapter package with Socket Mode connection, wire it into all 7 integration points (enum, imports, registry, routes, mapper, rate limits), and achieve basic message send/receive with DM support.

## Coupling

- **Depends on**: None
- **Coupling type**: — (root phase)
- **Shared files with other phases**: `daemon/sources/adapters/slack/` (all files), `daemon/sources/mapper.py`, `daemon/models/source.py`
- **Shared APIs/interfaces**: MessageSourceAdapter ABC, SourceConfig, IncomingMessage, OutgoingMessage
- **Why this coupling**: Phase 1 defines the adapter structure and integration points that Phase 2 builds routing on top of.

## Context

### Reference Implementation Analysis (Telegram)

The Telegram adapter (`daemon/sources/adapters/telegram.py`, 626 lines) provides the reference pattern:

```
TelegramAdapter(MessageSourceAdapter):
  __init__()          — extracts config, creates rate limiter + circuit breaker
  start()             — creates aiohttp session, calls getMe, starts polling
  stop()              — cancels tasks, closes session
  send()              — rate limit → circuit breaker → per-chat lock → API call
  health_check()      — simple API call
  test_connection()   — static, validates bot token
  handle_webhook()    — optional, verifies secret + processes update
  _polling_loop()     — long polling background task
  _process_update()   — extracts message data, builds IncomingMessage, calls _emit_message()
```

### Key Differences: Telegram vs Slack

| Aspect | Telegram | Slack |
|--------|----------|-------|
| Connection | Long polling or webhooks | Socket Mode (WebSocket) |
| Library | aiohttp (raw HTTP) | slack-bolt async framework |
| DM routing | chat_id = user_id | Need conversations.open() to get DM channel_id |
| Groups | chat_type field in message | channel_type field in event |
| Threads | No native threads | thread_ts field for threaded messages |
| Typing | sendChatAction API | No typing API — use reactions (✅ while processing) |
| File handling | Photo/Document objects | File URLs with auth headers |
| Rate limiting | 30 msg/sec per chat | Tiered: Tier 1=1/min, Tier 2=5/min, Tier 3=50/min, Tier 4=100+/min |
| Bot commands | /command entities | Slash commands (requires App configuration) |

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Add `slack` to SourceType enum** | Add `slack = "slack"` to the enum | `daemon/models/source.py` |
| 2 | **Add `slack` to VALID_SOURCE_TYPES** | Add `"slack"` to the set and add Slack composite ID validation (regex: `^[A-Z0-9]+:[UWC][A-Z0-9]+(:[0-9.]+)?$`) | `daemon/sources/mapper.py` |
| 3 | **Add `slack` to supported_types** | Add `"slack"` to the set in sources router | `daemon/routers/sources.py` |
| 4 | **Create adapter package structure** | Create `daemon/sources/adapters/slack/` with `__init__.py`, `adapter.py`, `rate_limiter.py` | `daemon/sources/adapters/slack/` |
| 5 | **Implement SlackRateLimiter** | Per-tier token buckets matching Slack's documented rate limits | `daemon/sources/adapters/slack/rate_limiter.py` |
| 6 | **Implement core SlackAdapter** | Socket Mode connection via slack-bolt, message normalization, basic send | `daemon/sources/adapters/slack/adapter.py` |
| 7 | **Wire adapter into adapters/__init__.py** | Add import for SlackAdapter | `daemon/sources/adapters/__init__.py` |
| 8 | **Wire adapter into registry._create_adapter_from_config()** | Add `elif source_type == "slack":` branch | `daemon/sources/registry.py` |
| 9 | **Add test_connection for Slack** | Validate bot token + app config via auth.test API | `daemon/routers/sources.py` (test_source endpoint) |
| 10 | **Add Slack to DEFAULT_RATE_LIMITS** | Add base rate limit for Slack adapter-level throttling | `daemon/sources/rate_limiter.py` |

## Key Files

### Files to Create
- `daemon/sources/adapters/slack/__init__.py` — Package init, re-exports SlackAdapter
- `daemon/sources/adapters/slack/adapter.py` — Main SlackAdapter class (~400-500 lines)
- `daemon/sources/adapters/slack/rate_limiter.py` — Per-tier Slack rate limiter (~80 lines)

### Files to Modify
- `daemon/models/source.py` — Add `slack = "slack"` to SourceType enum (1 line)
- `daemon/sources/mapper.py` — Add `"slack"` to VALID_SOURCE_TYPES, add composite ID validation regex (10 lines)
- `daemon/sources/adapters/__init__.py` — Add SlackAdapter import (2 lines)
- `daemon/sources/registry.py` — Add elif branch for Slack adapter creation (~20 lines)
- `daemon/routers/sources.py` — Add `"slack"` to supported_types, add test_connection branch (~10 lines)
- `daemon/sources/rate_limiter.py` — Add Slack to DEFAULT_RATE_LIMITS (1 line)

## Detailed Implementation Guidance

### Task 4: Adapter Package Structure

```
daemon/sources/adapters/slack/
├── __init__.py          # from .adapter import SlackAdapter
├── adapter.py           # SlackAdapter class
└── rate_limiter.py      # SlackTieredRateLimiter
```

**Note**: Thread management will be added in Phase 2 as `thread_manager.py`. Keep Phase 1 focused on basic Socket Mode connectivity.

### Task 5: SlackRateLimiter

Slack has 4 API method tiers with different rate limits:

```python
# daemon/sources/adapters/slack/rate_limiter.py

SLACK_RATE_TIERS = {
    # Tier 1: ~1 request per minute (e.g., conversations.create)
    "tier_1": RateLimit(messages_per_second=1/60, burst_size=1),
    # Tier 2: ~5 requests per minute (e.g., conversations.open, conversations.members)
    "tier_2": RateLimit(messages_per_second=5/60, burst_size=5),
    # Tier 3: ~50 requests per minute (e.g., chat.postEphemeral, reactions.add)
    "tier_3": RateLimit(messages_per_second=50/60, burst_size=50),
    # Tier 4: ~100+ requests per minute (e.g., chat.postMessage, chat.update)
    "tier_4": RateLimit(messages_per_second=100/60, burst_size=100),
}

# Map API methods to tiers
METHOD_TIERS = {
    "chat.postMessage": "tier_4",
    "chat.update": "tier_4",
    "chat.delete": "tier_4",
    "conversations.open": "tier_2",
    "conversations.info": "tier_3",
    "conversations.members": "tier_3",
    "reactions.add": "tier_3",
    "files.info": "tier_3",
    "auth.test": "tier_3",
    "users.info": "tier_3",
}
```

### Task 6: Core SlackAdapter

```python
# daemon/sources/adapters/slack/adapter.py

class SlackAdapter(MessageSourceAdapter):
    """Slack Socket Mode adapter.
    
    Uses slack-bolt async framework for persistent WebSocket connection.
    Supports DMs, channels, and (Phase 2) threads.
    
    CRITICAL ROUTING ARCHITECTURE:
    Unlike Telegram where external_user_id IS the chat_id, Slack's
    composite external_user_id (e.g. "TWS:U1") is NOT a routable channel ID.
    The dispatcher constructs OutgoingMessage(metadata={}) — always empty.
    Therefore, send() MUST look up routing info from the DB.
    """
    
    def __init__(self, config: SourceConfig, on_message):
        super().__init__(config, on_message)
        
        # Extract Slack-specific config
        self._bot_token = config.credentials.get("bot_token")      # xoxb-...
        self._app_token = config.credentials.get("app_token")      # xapp-...
        self._default_agent = config.config.get("default_agent")
        
        # State
        self._app: AsyncApp | None = None          # slack-bolt AsyncApp
        self._handler: AsyncSocketModeHandler | None = None
        self._bot_user_id: str | None = None
        self._workspace_id: str | None = None
        
        # Rate limiting (adapter-level for send operations)
        rate = DEFAULT_RATE_LIMITS.get("slack", RateLimit(100/60, 100))
        self._rate_limiter = TokenBucketLimiter(rate)
        
        # Per-tier rate limiter for Slack API calls
        self._slack_rate_limiter = SlackTieredRateLimiter()
        
        # Circuit breaker
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60.0,
        )
        
        # Per-channel send locks for message ordering
        self._channel_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._channel_locks_guard = asyncio.Lock()
        
        # DM channel cache (user_id -> dm_channel_id)
        self._dm_cache: dict[str, str] = {}
        
        # Source repository reference — needed for DB lookup in send()
        # Set by registry._create_adapter_from_config() after construction
        self._source_repo = None
```

#### Key Methods

**start()** — Initialize slack-bolt AsyncApp and SocketModeHandler:

```python
async def start(self) -> None:
    self._status = SourceStatus.STARTING
    self._error = None
    
    # Validate required credentials
    if not self._bot_token:
        raise ValueError("Slack adapter requires 'bot_token' (xoxb-...) in credentials")
    if not self._app_token:
        raise ValueError("Slack adapter requires 'app_token' (xapp-...) in credentials")
    
    # Initialize slack-bolt AsyncApp
    self._app = AsyncApp(token=self._bot_token)
    
    # Register event handlers
    @self._app.event("message")
    async def handle_message(body, say):
        await self._process_event(body)
    
    @self._app.event("message_changed") 
    async def handle_message_changed(body, say):
        await self._process_event(body, is_edit=True)
    
    # Get bot identity
    auth_result = await self._slack_rate_limiter.acquire_and_execute(
        "auth.test",
        lambda: self._app.client.auth_test()
    )
    self._bot_user_id = auth_result["user_id"]
    self._workspace_id = auth_result["team_id"]
    
    # Start Socket Mode handler
    self._handler = AsyncSocketModeHandler(
        app=self._app,
        app_token=self._app_token,
    )
    # Run in background task (non-blocking)
    asyncio.create_task(self._handler.start_async())
    
    self._status = SourceStatus.RUNNING
```

**stop()** — Clean shutdown:

```python
async def stop(self) -> None:
    if self._handler:
        await self._handler.close_async()
        self._handler = None
    
    self._app = None
    self._status = SourceStatus.STOPPED
```

**send() — DB Lookup Routing Strategy (CRITICAL)**

This is the most important method in the entire adapter. Unlike Telegram where `external_user_id` IS the `chat_id`, Slack's composite `external_user_id` (e.g. `TWS:U1`) is NOT a routable channel ID. The dispatcher constructs `OutgoingMessage(metadata={})` — always empty metadata. Therefore `send()` MUST look up routing info from the DB.

```python
async def send(self, message: OutgoingMessage) -> bool:
    """Send a response back to Slack.
    
    ROUTING STRATEGY: DB lookup from mapping_metadata.
    
    The ResponseDispatcher constructs OutgoingMessage with metadata={}
    (always empty), so we CANNOT rely on metadata for routing. Instead,
    we look up the instance mapping in the DB using source_id + external_user_id
    to retrieve slack_channel_id and slack_thread_ts from mapping_metadata.
    
    This also handles the /new confirmation path, where registry._handle_message()
    creates a bare OutgoingMessage with no metadata.
    """
    if self._status != SourceStatus.RUNNING:
        return False
    
    if not await self._circuit_breaker.can_execute():
        return False
    
    # === DB LOOKUP: Resolve routing from mapping_metadata ===
    channel_id = None
    thread_ts = None
    
    if self._source_repo:
        try:
            mapping = await asyncio.to_thread(
                self._source_repo.get_instance_mapping,
                self.source_id,
                message.external_user_id,
            )
            if mapping and mapping.mapping_metadata:
                meta = mapping.mapping_metadata
                if isinstance(meta, str):
                    import json
                    meta = json.loads(meta)
                channel_id = meta.get("slack_channel_id")
                thread_ts = meta.get("slack_thread_ts")
        except Exception as e:
            logger.error(f"DB lookup failed for routing: {e}")
    
    if not channel_id:
        logger.error(
            f"No slack_channel_id found in mapping for "
            f"source_id={self.source_id}, user={message.external_user_id}. "
            f"Cannot route response."
        )
        return False
    
    # Per-channel lock for message ordering
    lock = await self._get_channel_lock(channel_id)
    async with lock:
        try:
            await self._slack_rate_limiter.acquire_and_execute(
                "chat.postMessage",
                lambda: self._app.client.chat_postMessage(
                    channel=channel_id,
                    text=message.content,
                    thread_ts=thread_ts,  # None = post in channel, value = reply in thread
                    mrkdwn=True,
                )
            )
            await self._circuit_breaker.record_success()
            return True
        except Exception as e:
            await self._circuit_breaker.record_failure()
            logger.error(f"Failed to send Slack message to {channel_id}: {e}")
            return False
```

**Why this works for ALL send() callers:**
1. **Dispatcher path**: `dispatch_completed()` creates `OutgoingMessage(metadata={})`. The DB lookup resolves `slack_channel_id` from the mapping that was created when the user first messaged.
2. **/new confirmation**: `registry._handle_message()` creates a bare `OutgoingMessage` at line ~675. The mapping already exists (it was just created), so the DB lookup succeeds.
3. **Progressive delivery**: `dispatch_message()` also creates `OutgoingMessage(metadata={})`. Same DB lookup works.

**_process_event()** — Normalize Slack event to IncomingMessage:

```python
async def _process_event(self, body: dict, is_edit: bool = False) -> None:
    event = body.get("event", {})
    
    # Skip bot's own messages
    if event.get("bot_id") or event.get("user") == self._bot_user_id:
        return
    
    # Extract message data
    user_id = event.get("user", "")
    channel_id = event.get("channel", "")
    channel_type = event.get("channel_type", "")  # "channel", "group", "im", "mpim"
    text = event.get("text", "")
    thread_ts = event.get("thread_ts")  # Present if in a thread
    ts = event.get("ts", "")  # Message timestamp (unique ID)
    
    if not user_id or not channel_id:
        return
    
    # Skip empty messages (may be file-only, handled in Phase 2)
    if not text and not event.get("files"):
        return
    
    # Determine session mapping:
    # DM (channel_type="im"): each user gets their own instance
    # Channel/Group: entire channel shares one instance
    if channel_type == "im":
        session_user_id = user_id
    else:
        session_user_id = channel_id  # Shared session for channels
    
    # Build metadata (all Slack-specific data goes here)
    metadata = {
        "source_type": "slack",
        "message_id": ts,  # For deduplication
        "slack": {
            "channel_id": channel_id,
            "channel_type": channel_type,
            "user_id": user_id,
            "user_name": event.get("user_profile", {}).get("display_name", ""),
            "thread_ts": thread_ts,
            "ts": ts,
            "is_edit": is_edit,
            "workspace_id": self._workspace_id,
        },
        "agent": self._default_agent,
        # These fields are stored in mapping_metadata via extra_mapping_metadata
        # They are NOT used for routing in send() — send() does a DB lookup instead.
        "slack_channel_id": channel_id,
        "slack_thread_ts": thread_ts,
        "reply_chat_id": channel_id,
    }
    
    incoming = IncomingMessage(
        external_user_id=session_user_id,
        content=text or "[File]",
        source_id=self.source_id,
        metadata=metadata,
        message_type="command" if text.startswith("/") else "text",
    )
    
    await self._emit_message(incoming)
```

### Task 7-8: Wiring

**adapters/__init__.py**:
```python
from .slack import SlackAdapter
# Add to __all__
```

**registry.py** (add after telegram elif block at line ~273):
```python
elif source_type == "slack":
    from .adapters.slack import SlackAdapter
    adapter = SlackAdapter(config, on_message)
    # CRITICAL: Inject source_repo for DB lookup routing in send()
    adapter._source_repo = self._source_repo
    logger.info(f"SlackAdapter created: default_agent={adapter._default_agent}")
    return adapter
```

> **Why `_source_repo` injection?** The adapter's `send()` method needs DB access to look up `slack_channel_id` from `mapping_metadata`. The `SourceRegistry` already holds `_source_repo`. Injecting it after construction keeps the adapter's `__init__` signature compatible with the standard `MessageSourceAdapter(config, on_message)` pattern.

**sources.py router** (add test branch ~line 196):
```python
elif test_request.source_type == SourceType.slack:
    from daemon.sources.adapters.slack.adapter import SlackAdapter
    success, message = await SlackAdapter.test_connection(temp_config)
```

### Task 2: Mapper Validation

```python
# In mapper.py, add to VALID_SOURCE_TYPES:
SOURCE_TYPE_SLACK = "slack"
VALID_SOURCE_TYPES = {SOURCE_TYPE_TELEGRAM, SOURCE_TYPE_WEBHOOK, SOURCE_TYPE_SLACK}

# Add Slack user ID validation in validate_external_user_id():
elif source_type == SOURCE_TYPE_SLACK:
    # Slack composite external_user_id format: {workspace}:{user_or_channel}[:{thread_ts}]
    # workspace starts with T, user IDs start with U or W, channel IDs start with C
    # Examples: T12345:U67890, T12345:C67890, T12345:C67890:1234567890.123456
    if not re.match(r'^[A-Z0-9]+:[UWC][A-Z0-9]+(:[0-9.]+)?$', user_id):
        raise ValidationError(
            f"Invalid Slack composite ID '{user_id}': "
            f"expected format WORKSPACE:ID or WORKSPACE:ID:THREAD_TS"
        )
    return user_id
```

## Constraints

- **Must use slack-bolt async** — Do NOT implement raw WebSocket handling. slack-bolt provides Socket Mode, event parsing, and reconnection.
- **Must not change base interfaces** — IncomingMessage/OutgoingMessage/SourceConfig remain unchanged. All Slack data goes in `metadata`.
- **Must work with both processing paths** — WorkerPool (`enqueue_message`) and JobQueue (`enqueue_message_via_jq`). The registry's `_handle_message()` uses `enqueue_message()` (WorkerPool path), which calls `dispatch_completed()` for responses.
- **Socket Mode (NOT webhooks)** — No changes to webhooks.py needed.
- **Dependency**: `slack-bolt>=1.18.0` and `slack-sdk>=3.21.0` must be added to requirements.

## Deliverables

- [ ] `slack` added to SourceType enum
- [ ] `"slack"` added to VALID_SOURCE_TYPES in mapper.py with user ID validation
- [ ] `"slack"` added to supported_types in sources router
- [ ] `daemon/sources/adapters/slack/` package created with 3 files
- [ ] SlackAdapter connects via Socket Mode and receives DM messages
- [ ] SlackAdapter sends responses back to correct Slack channel
- [ ] Registry creates SlackAdapter from config
- [ ] Test connection endpoint validates Slack bot token
- [ ] Circuit breaker + rate limiting active on send path
- [ ] Health check verifies Socket Mode connection alive

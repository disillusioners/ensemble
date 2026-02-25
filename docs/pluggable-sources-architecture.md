# Pluggable Message Sources Architecture

## Overview

Add plugable message sources (Telegram, webhooks, etc.) to the agent backend with **minimal core changes**. Uses an adapter pattern where external sources normalize messages and feed them into the existing queue, while a `ResponseDispatcher` routes responses back.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                       EXTERNAL LAYER                            │
│   Telegram  │  WhatsApp  │  Webhook  │  Discord  │  ...        │
│   Adapter   │  Adapter   │  Adapter  │  Adapter  │             │
└──────┬─────────────┬───────────┬───────────┬───────────────────┘
       │             │           │           │
       ▼             ▼           ▼           ▼
┌─────────────────────────────────────────────────────────────────┐
│                     SOURCE MANAGER (NEW)                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │SourceRegistry│  │SessionMapper │  │ ResponseDispatcher   │  │
│  │- register()  │  │- map()       │  │- Listens to events   │  │
│  │- get()       │  │- create()    │  │- Routes to adapters  │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                  CORE ENGINE (REQUIRES CHANGES)                  │
│  ┌────────────────┐   ┌────────────────┐   ┌────────────────┐  │
│  │InputMessageQueue│   │SessionManager  │   │EventBroadcaster│  │
│  │  (unchanged)   │   │ (+add source)  │   │ +subscribe_all │  │
│  └────────────────┘   └────────────────┘   └────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## ⚠️ Critical Issues (MUST FIX BEFORE IMPLEMENTATION)

### 🔴 CRITICAL: EventBroadcaster Lacks Subscription Mechanism

**Problem**: The plan assumes `ResponseDispatcher` can "listen" to `EventBroadcaster`, but the current implementation has **no subscription mechanism**. The `broadcast()` method only pushes to a single session's queue.

**Required Fix**: Add `subscribe_all()` method to EventBroadcaster:

```python
# daemon/events.py - EventBroadcaster
class EventBroadcaster:
    def __init__(self):
        # ... existing ...
        self._global_subscribers: list[asyncio.Queue] = []  # NEW
    
    async def subscribe_all(self) -> asyncio.Queue:
        """Subscribe to ALL events across all sessions."""
        q = asyncio.Queue(maxsize=1000)
        self._global_subscribers.append(q)
        return q
    
    async def broadcast(self, event: Event) -> None:
        # ... existing session queue logic ...
        
        # NEW: Also push to global subscribers
        for q in self._global_subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Global subscriber queue full, dropping event")
```

---

### 🔴 CRITICAL: Completed Event Missing `source` Field

**Problem**: The `completed` event doesn't include the `source` field needed by ResponseDispatcher to route responses.

**Required Fix** in `daemon/manager.py`:

```python
await self.broadcaster.broadcast(Event(
    type="completed",
    session_id=session_id,
    message_id=msg.message_id,
    data={
        "content": result.content,
        "thinking": result.thinking,
        "tool_calls": result.tool_calls,
        "source": msg.source,  # NEW - required for routing!
    }
))
```

---

### 🔴 CRITICAL: No Duplicate Message Handling

**Problem**: Telegram and other platforms can deliver the same message multiple times (network retries, webhook redelivery). No deduplication = duplicate responses to users.

**Required Fix**: Add deduplication table:

```sql
-- Track processed external messages
CREATE TABLE processed_external_messages (
    source_id TEXT,
    external_message_id TEXT,  -- Telegram message_id
    processed_at TIMESTAMP,
    PRIMARY KEY (source_id, external_message_id)
);
```

---

### 🔴 CRITICAL: "1-2 Line Change" is Misleading

**Reality**: The integration requires:

1. **EventBroadcaster modification** - Add global subscription support
2. **SessionManager modification** - Add source to completed events
3. **New tables** - source_configs, session_mappings, processed_external_messages
4. **New module** - Entire `daemon/sources/` directory (~500+ lines)
5. **API endpoints** - ~100-150 lines, not 50
6. **Startup sequence** - Initialize sources on boot

---

## 🟠 HIGH Priority Issues

### SQLite Write Contention

**Problem**: All components share ONE SQLite connection. With multiple adapters, write contention becomes a bottleneck at ~10-20 concurrent sessions.

**Mitigations**:
1. Increase SQLite busy timeout
2. Use WAL mode: `PRAGMA journal_mode=WAL`
3. Plan for PostgreSQL migration if scaling

### Adapter Crash Isolation

**Problem**: Adapters run in the same process. One crash can take down the entire system.

**Required Fix**: Add supervisor pattern:

```python
class SourceRegistry:
    async def _run_adapter_safe(self, adapter: MessageSourceAdapter):
        while True:
            try:
                await adapter.start()
            except Exception as e:
                logger.error(f"Adapter {adapter.source_id} crashed: {e}")
                adapter._status = SourceStatus.ERROR
                await asyncio.sleep(self._backoff)  # Exponential backoff
                await adapter.start()
```

### Message Ordering

**Problem**: Rapid messages (A, B, C) may be delivered out of order if responses fail/retry.

**Required Fix**: Add per-user send locks:

```python
class ResponseDispatcher:
    def __init__(self):
        self._send_locks: dict[str, asyncio.Lock] = {}  # per external_user_id
```

### Telegram Polling vs Webhook

**Problem**: Plan mentions polling, but webhooks are better for production (real-time, no latency).

**Required Fix**: Adapter interface should support both:

```python
class MessageSourceAdapter(ABC):
    async def handle_webhook(self, payload: dict) -> None:
        """Override for webhook-based sources."""
        raise NotImplementedError("Not a webhook source")
```

---

## Database Schema

### New Tables

```sql
-- Source configurations (bots, webhooks)
CREATE TABLE source_configs (
    source_id TEXT PRIMARY KEY,        -- e.g., "telegram-main"
    source_type TEXT NOT NULL,         -- "telegram", "webhook", "whatsapp"
    name TEXT NOT NULL,                -- Display name "Customer Support Bot"
    config JSON NOT NULL,              -- Type-specific config
    credentials JSON,                  -- Encrypted API tokens/secrets
    enabled BOOLEAN DEFAULT TRUE,
    status TEXT DEFAULT 'stopped',     -- 'stopped', 'starting', 'running', 'error'
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Session mappings: external_user -> agent_session
CREATE TABLE session_mappings (
    mapping_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,           -- Which source this belongs to
    external_user_id TEXT NOT NULL,    -- Telegram chat_id, webhook client_id
    agent_session_id TEXT NOT NULL,    -- The agent session handling this user
    agent_dir TEXT NOT NULL,           -- Which agent config to use
    metadata JSON,                     -- User info, preferences
    last_message_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, external_user_id),
    FOREIGN KEY (source_id) REFERENCES source_configs(source_id)
);

CREATE INDEX idx_session_mappings_source ON session_mappings(source_id);
CREATE INDEX idx_session_mappings_session ON session_mappings(agent_session_id);

-- Deduplication: track processed external messages
CREATE TABLE processed_external_messages (
    source_id TEXT,
    external_message_id TEXT,       -- Telegram message_id, webhook event_id
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_id, external_message_id)
);

-- Optional: TTL cleanup via periodic job
-- DELETE FROM processed_external_messages WHERE processed_at < datetime('now', '-1 day')
```

---

## Core Interfaces

### `daemon/sources/base.py`

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable
from enum import Enum


class SourceStatus(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"


@dataclass
class IncomingMessage:
    """Normalized incoming message from any source."""
    external_user_id: str       # Telegram chat_id, webhook client_id
    content: str                # Message text/content
    source_id: str              # Which source adapter this came from
    metadata: dict = field(default_factory=dict)
    message_type: str = "text"  # "text", "image", "command"
    reply_to_id: Optional[str] = None


@dataclass
class OutgoingMessage:
    """Normalized outgoing message to any source."""
    external_user_id: str
    content: str
    source_id: str
    metadata: dict = field(default_factory=dict)
    message_type: str = "text"
    reply_to_id: Optional[str] = None


@dataclass
class SourceConfig:
    """Configuration for a message source."""
    source_id: str
    source_type: str
    name: str
    config: dict
    credentials: dict
    enabled: bool = True


class MessageSourceAdapter(ABC):
    """Abstract base class for all message source adapters.
    
    Each adapter handles:
    - Connecting to external service
    - Receiving and normalizing messages
    - Sending responses back
    - Lifecycle management
    """
    
    def __init__(self, config: SourceConfig,
                 on_message: Callable[[IncomingMessage], Awaitable[None]]):
        self.config = config
        self._on_message = on_message
        self._status = SourceStatus.STOPPED
        self._error: Optional[str] = None
    
    @property
    def source_id(self) -> str:
        return self.config.source_id
    
    @property
    def source_type(self) -> str:
        return self.config.source_type
    
    @property
    def status(self) -> SourceStatus:
        return self._status
    
    @abstractmethod
    async def start(self) -> None:
        """Start the adapter (connect, begin listening)."""
        ...
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop the adapter gracefully."""
        ...
    
    @abstractmethod
    async def send(self, message: OutgoingMessage) -> bool:
        """Send message to external service. Returns success."""
        ...
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check if adapter is healthy and connected."""
        ...
    
    async def reload(self, new_config: SourceConfig) -> None:
        """Reload configuration (restart if needed)."""
        if self.config != new_config:
            await self.stop()
            self.config = new_config
            await self.start()
    
    async def _emit_message(self, msg: IncomingMessage) -> None:
        """Internal: call the message handler."""
        await self._on_message(msg)
```

---

## Message Flow

### Incoming (External User → Agent)

```
1. Telegram User sends message
2. TelegramAdapter receives via polling/webhook
3. Adapter creates IncomingMessage and calls _emit_message()
4. SourceManager._handle_incoming() receives message
5. SessionMapper.get_or_create_session() finds or creates agent session
6. InputMessageQueue.enqueue(session_id, content, source="telegram:chat_id")
7. LangGraph processes message (unchanged)
8. EventBroadcaster broadcasts "completed" event
```

### Outgoing (Agent → External User)

```
1. LangGraph completes with response
2. EventBroadcaster.broadcast("completed", session_id, source)
3. ResponseDispatcher.on_event() receives event
4. Parse source field: "telegram:chat_id" -> source_type, external_user_id
5. Look up adapter in SourceRegistry
6. Call adapter.send(OutgoingMessage)
7. Adapter sends via external API (Telegram sendMessage)
```

---

## File Structure

```
daemon/
├── api.py                      # + /sources/* endpoints (~100 lines)
├── manager.py                  # + source field in event, source system init
├── queue.py                    # UNCHANGED
├── events.py                   # + subscribe_all() method (~15 lines)
├── graph.py                    # UNCHANGED
├── persistence.py              # + new tables (~40 lines)
│
├── sources/                    # NEW MODULE (~500+ lines total)
│   ├── __init__.py             # Exports
│   ├── base.py                 # Interfaces (IncomingMessage, Adapter ABC)
│   ├── registry.py             # SourceRegistry with supervisor pattern
│   ├── mapper.py               # SessionMapper + deduplication
│   ├── dispatcher.py           # ResponseDispatcher with per-user locks
│   ├── persistence.py          # DB operations for sources
│   │
│   └── adapters/               # Concrete adapters
│       ├── __init__.py
│       ├── telegram.py         # TelegramAdapter (polling + webhook)
│       └── webhook.py          # WebhookAdapter (future)
│
└── models.py                   # + SourceInfo, SourceCreateRequest
```

---

## API Endpoints

### Source Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/sources` | List all configured sources |
| POST | `/sources` | Create new source |
| GET | `/sources/{source_id}` | Get source config and status |
| PATCH | `/sources/{source_id}` | Update source config |
| DELETE | `/sources/{source_id}` | Stop and delete source |
| POST | `/sources/{source_id}/start` | Start a stopped source |
| POST | `/sources/{source_id}/stop` | Stop a running source |

### Session Mappings

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/sources/{source_id}/mappings` | List session mappings |
| POST | `/sources/{source_id}/mappings` | Create/update mapping |
| DELETE | `/sources/{source_id}/mappings/{mapping_id}` | Delete mapping |

### Webhook Receiver

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/webhooks/{source_id}` | Handle incoming webhook |

---

## Core Changes Required

### 1. EventBroadcaster - Add Global Subscription (daemon/events.py)

```python
class EventBroadcaster:
    def __init__(self):
        # ... existing ...
        self._global_subscribers: list[asyncio.Queue] = []
    
    async def subscribe_all(self) -> asyncio.Queue:
        """Subscribe to ALL events across all sessions."""
        q = asyncio.Queue(maxsize=1000)
        self._global_subscribers.append(q)
        return q
    
    async def broadcast(self, event: Event) -> None:
        # ... existing session queue logic ...
        
        # Push to global subscribers
        for q in self._global_subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Global subscriber queue full, dropping event")
```

### 2. SessionManager - Add Source to Completed Event (daemon/manager.py)

```python
# In _process_queue(), when broadcasting completed event:
await self.broadcaster.broadcast(Event(
    type="completed",
    session_id=session_id,
    message_id=msg.message_id,
    data={
        "content": result.content,
        "thinking": result.thinking,
        "tool_calls": result.tool_calls,
        "source": msg.source,  # NEW - required for ResponseDispatcher routing
    }
))
```

### 3. SessionManager - Initialize Source System (daemon/manager.py)

```python
from .sources.dispatcher import ResponseDispatcher
from .sources.registry import SourceRegistry

class SessionManager:
    def __init__(self, config: Config):
        # ... existing initialization ...
        
        # NEW: Source management system
        self.source_registry = SourceRegistry(conn=self.conn, manager=self)
        self.source_dispatcher = ResponseDispatcher(
            broadcaster=self.broadcaster,
            registry=self.source_registry
        )
        
    async def start(self):
        """Start session manager and source system."""
        # ... existing startup ...
        
        # Start sources and dispatcher
        await self.source_registry.start_all()
        self.source_dispatcher.start()
```

### 4. Persistence - Add New Tables (daemon/persistence.py)

```python
def init_db(conn: sqlite3.Connection):
    # ... existing tables ...
    
    # Source configurations
    conn.execute("""CREATE TABLE IF NOT EXISTS source_configs (...)""")
    
    # Session mappings
    conn.execute("""CREATE TABLE IF NOT EXISTS session_mappings (...)""")
    
    # Deduplication
    conn.execute("""CREATE TABLE IF NOT EXISTS processed_external_messages (...)""")
```

---

## Implementation Phases

### Phase 0.5: Critical Core Fixes (1 day) ⚠️ MUST DO FIRST
- [ ] Add `subscribe_all()` method to `EventBroadcaster` (daemon/events.py)
- [ ] Add `source` field to completed event in `SessionManager` (daemon/manager.py)
- [ ] Add new tables to persistence (source_configs, session_mappings, processed_external_messages)
- [ ] Write tests for event subscription/routing
- [ ] Verify core changes don't break existing functionality

### Phase 1: Foundation (1-2 days)
- [ ] Create `daemon/sources/__init__.py`
- [ ] Create `daemon/sources/base.py` with interfaces
- [ ] Create `daemon/sources/persistence.py` with DB operations
- [ ] Create `daemon/sources/mapper.py` for session mapping
- [ ] Add deduplication logic in mapper

### Phase 2: Core Components (1-2 days)
- [ ] Create `daemon/sources/registry.py` - SourceRegistry with supervisor pattern
- [ ] Create `daemon/sources/dispatcher.py` - ResponseDispatcher with per-user locks
- [ ] Integrate registry and dispatcher into SessionManager
- [ ] Test event routing end-to-end

### Phase 3: Telegram Adapter (2-3 days)
- [ ] Create `daemon/sources/adapters/__init__.py`
- [ ] Create `daemon/sources/adapters/telegram.py`
- [ ] Implement polling-based message receiving (initial)
- [ ] Implement message sending via Bot API
- [ ] Handle Telegram-specific message types (text, commands)
- [ ] Add error handling with exponential backoff
- [ ] Add webhook support (for production)

### Phase 4: API Endpoints (1-2 days)
- [ ] Add source CRUD endpoints to `daemon/api.py`
- [ ] Add mapping endpoints
- [ ] Add webhook receiver endpoint
- [ ] Add request/response models to `daemon/models.py`
- [ ] Add API tests

### Phase 5: Frontend Integration (2-3 days)
- [ ] Source configuration UI
- [ ] Source list/status display
- [ ] Mapping management UI
- [ ] Agent selection for sources

### Phase 6: Additional Adapters (Future)
- [ ] Webhook adapter
- [ ] WhatsApp adapter
- [ ] Discord adapter

---

## Design Decisions

### 1. Adapter Pattern (vs Event Bus)

| Aspect | Adapter Pattern | Event Bus |
|--------|----------------|-----------|
| Complexity | Lower | Higher |
| Dependencies | None (no Redis/NATS) | Requires message broker |
| Latency | Lower (direct calls) | Higher |
| Scalability | Single process | Distributed |

**Choice**: Adapter Pattern - simpler, no external dependencies, fits current single-process architecture.

### 2. Response Routing (Event Listener vs Callback)

| Approach | Pros | Cons |
|----------|------|------|
| Event listener | Decoupled | Requires EventBroadcaster modification |
| Callback | Direct | Modifies core processing logic |

**Choice**: Event listener - requires adding `subscribe_all()` to EventBroadcaster, but keeps processing logic clean.

### 3. Session Creation (Lazy vs Eager)

| Approach | Pros | Cons |
|----------|------|------|
| Lazy (on-demand) | Auto-scaling, no pre-config | First message slower |
| Eager (pre-configured) | Predictable | Manual setup required |

**Choice**: Lazy - sessions created when first message arrives from new user.

### 4. Source Field Convention

Using existing `source` field in queue as `"source_type:external_user_id"`:
- No schema changes needed
- Easy to parse: `source.split(":", 1)`
- Works with existing queue code

### 5. Deployment (Same Process vs Separate)

| Approach | Pros | Cons |
|----------|------|------|
| Same process | Simpler, shared DB | Single point of failure |
| Separate | Isolation, independent scaling | Complex orchestration |

**Choice**: Same process - start simple, can extract to microservices later.

### 6. Telegram: Polling vs Webhook

| Approach | Pros | Cons |
|----------|------|------|
| Long Polling | No public endpoint needed | Latency up to 30s, constant connections |
| Webhook | Real-time, efficient | Requires public HTTPS endpoint |

**Choice**: Start with polling for development, add webhook support for production. Adapter interface supports both.

---

## Configuration Example

```yaml
# config.yaml
sources:
  telegram:
    enabled: true
    bot_token: ${TELEGRAM_BOT_TOKEN}
    default_agent: coder
    
  webhook:
    enabled: true
    secret: ${WEBHOOK_SECRET}
    default_agent: leader
```

---

## Security Considerations

1. **Credential Storage**: Encrypt sensitive credentials (API tokens) in database
2. **Webhook Verification**: Validate signatures on incoming webhooks
3. **Rate Limiting**: Per-source rate limits to prevent abuse
4. **Access Control**: User permissions for source configuration
5. **Credential Rotation**: Support rotating credentials without downtime (support old + new during transition)

---

## Failure Modes & Mitigations

| Failure | Impact | Mitigation |
|---------|--------|------------|
| Telegram API down | Lost inbound messages | Exponential backoff, dead letter queue for outbound |
| Adapter crash | Could crash entire process | Supervisor pattern with isolation |
| Database locked | Message processing delays | WAL mode, retry on lock, increase busy timeout |
| Queue backup | Messages dropped | Per-source rate limiting, alerting |
| Out-of-order responses | Confusing UX | Per-user send locks in dispatcher |

---

## Testing Strategy

1. **Unit Tests**: Each adapter, mapper, dispatcher
2. **Integration Tests**: Full message flow (incoming + outgoing)
3. **Mock External APIs**: Use VCR.py or recorded responses for Telegram API
4. **Load Tests**: Multiple concurrent sessions per source
5. **Failure Tests**: Simulate API outages, database locks, adapter crashes

---

## Future Considerations

- [ ] Message queuing per source for reliability
- [ ] Multi-tenant source isolation
- [ ] Source health monitoring and alerts
- [ ] Message templates for common responses
- [ ] Rich message support (images, files, buttons)
- [ ] Conversation context persistence across restarts
- [ ] PostgreSQL migration for scaling
- [ ] Extract adapters to separate processes for isolation

---

## Estimated Timeline

| Phase | Description | Effort | Dependencies |
|-------|-------------|--------|--------------|
| 0.5 | Critical Core Fixes | 1 day | None |
| 1 | Foundation | 1-2 days | Phase 0.5 |
| 2 | Core Components | 1-2 days | Phase 1 |
| 3 | Telegram Adapter | 2-3 days | Phase 2 |
| 4 | API Endpoints | 1-2 days | Phase 3 |
| 5 | Frontend Integration | 2-3 days | Phase 4 |

**Total: ~8-13 days** (backend only, excluding Phase 6 future adapters)

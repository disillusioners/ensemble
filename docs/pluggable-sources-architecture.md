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
│                  CORE ENGINE (MINIMAL CHANGE)                   │
│  ┌────────────────┐   ┌────────────────┐   ┌────────────────┐  │
│  │InputMessageQueue│   │SessionManager  │   │EventBroadcaster│  │
│  │  (unchanged)   │   │ (+1 hook line) │   │  (unchanged)   │  │
│  └────────────────┘   └────────────────┘   └────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
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
├── api.py                      # + /sources/* endpoints (~50 lines)
├── manager.py                  # + dispatcher init (1-2 lines)
├── queue.py                    # UNCHANGED
├── events.py                   # UNCHANGED
├── graph.py                    # UNCHANGED
├── persistence.py              # + new tables (~30 lines)
│
├── sources/                    # NEW MODULE
│   ├── __init__.py             # Exports
│   ├── base.py                 # Interfaces (IncomingMessage, Adapter ABC)
│   ├── registry.py             # SourceRegistry, SourceManager
│   ├── mapper.py               # SessionMapper
│   ├── dispatcher.py           # ResponseDispatcher
│   ├── persistence.py          # DB operations for sources
│   │
│   └── adapters/               # Concrete adapters
│       ├── __init__.py
│       ├── telegram.py         # TelegramAdapter
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

## Core Changes

Only **one minimal change** to the core engine:

```python
# daemon/manager.py - SessionManager.__init__()

from .sources.dispatcher import ResponseDispatcher

class SessionManager:
    def __init__(self, config: Config):
        # ... existing initialization ...
        
        # NEW: Response dispatcher for external sources
        self.source_dispatcher = ResponseDispatcher(
            broadcaster=self.broadcaster,
            queue=self.queue,
            conn=self.conn
        )
        self.source_dispatcher.start()
```

---

## Implementation Phases

### Phase 1: Foundation (1-2 days)
- [ ] Create `daemon/sources/__init__.py`
- [ ] Create `daemon/sources/base.py` with interfaces
- [ ] Create `daemon/sources/persistence.py` with DB schema
- [ ] Create `daemon/sources/mapper.py` for session mapping
- [ ] Add migration for new tables in `daemon/persistence.py`

### Phase 2: Core Components (1-2 days)
- [ ] Create `daemon/sources/registry.py` - SourceRegistry
- [ ] Create `daemon/sources/dispatcher.py` - ResponseDispatcher
- [ ] Add dispatcher hook in `SessionManager.__init__()`
- [ ] Test event routing

### Phase 3: Telegram Adapter (2-3 days)
- [ ] Create `daemon/sources/adapters/__init__.py`
- [ ] Create `daemon/sources/adapters/telegram.py`
- [ ] Implement polling-based message receiving
- [ ] Implement message sending via Bot API
- [ ] Handle Telegram-specific message types (text, commands)
- [ ] Error handling and reconnection logic

### Phase 4: API Endpoints (1 day)
- [ ] Add source CRUD endpoints to `daemon/api.py`
- [ ] Add mapping endpoints
- [ ] Add webhook receiver endpoint
- [ ] Add request/response models to `daemon/models.py`

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
| Event listener | Decoupled, no core changes | Slight indirection |
| Callback | Direct | Modifies core |

**Choice**: Event listener - keeps SessionManager untouched, subscribes to existing EventBroadcaster.

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

---

## Testing Strategy

1. **Unit Tests**: Each adapter, mapper, dispatcher
2. **Integration Tests**: Full message flow (incoming + outgoing)
3. **Mock External APIs**: Use recorded responses for Telegram API
4. **Load Tests**: Multiple concurrent sessions per source

---

## Future Considerations

- [ ] Message queuing per source for reliability
- [ ] Multi-tenant source isolation
- [ ] Source health monitoring and alerts
- [ ] Message templates for common responses
- [ ] Rich message support (images, files, buttons)
- [ ] Conversation context persistence across restarts

# daemon/sources/

## Responsibility
This module provides a **multi-source message abstraction layer** for receiving messages from and sending responses to external messaging platforms (Telegram, webhooks, WhatsApp). It implements a unified interface that normalizes incoming/outgoing messages and manages the lifecycle of various source adapters with resilience patterns (circuit breaker, rate limiting).

## Design Patterns

| Pattern | Implementation | Purpose |
|---------|----------------|---------|
| **Adapter** | `MessageSourceAdapter` (ABC) | Unified interface for different message sources |
| **Registry** | `SourceRegistry` | Lifecycle management for all registered adapters |
| **Circuit Breaker** | `CircuitBreaker` | Prevents cascading failures from external services |
| **Rate Limiter** | `TokenBucketLimiter` | Token bucket algorithm for API throttling |
| **Session Mapping** | `SessionMapper` | Maps external user identities to internal agent sessions |
| **Credential Manager** | `CredentialManager` | Fernet symmetric encryption for API tokens |
| **LRU Lock Pool** | `ResponseDispatcher` | Per-user ordering locks with LRU eviction |
| **Supervisor** | `_run_adapter_safe()` | Exponential backoff restart with health checks |

## Data & Control Flow

### Incoming Messages (Adapter → Session → Queue)
```
External Source (Telegram/Webhook/WhatsApp)
        ↓
[MessageSourceAdapter] - Normalizes message to IncomingMessage
        ↓
[SourceRegistry._handle_message] - Handles incoming message
        ↓
[SessionMapper] - Validates user, checks duplicates, maps to session
        ↓
[SessionManager.queue.enqueue] - Queues for agent processing
```

### Outgoing Messages (Agent → Dispatcher → Adapter)
```
Agent processing complete
        ↓
[EventBroadcaster] - Emits "completed" event
        ↓
[ResponseDispatcher._handle_event] - Listens for completed events
        ↓
[SourceRegistry.get] - Looks up adapter by source_id
        ↓
[MessageSourceAdapter.send] - Sends OutgoingMessage to external source
```

## Integration Points

| Component | Integration |
|-----------|-------------|
| **SessionManager** | Provides `queue.enqueue()` for message processing; receives messages via `_handle_message()` callback |
| **EventBroadcaster** | `ResponseDispatcher` subscribes to all events for routing responses |
| **SQLite DB** | `persistence.py` manages `source_configs`, `session_mappings`, `processed_external_messages` tables |
| **External Sources** | Implementations of `MessageSourceAdapter` (not in this module) |

## Key Files

- **`base.py`**: Core types (`IncomingMessage`, `OutgoingMessage`, `SourceConfig`) and abstract `MessageSourceAdapter` base class
- **`registry.py`**: Central registry for adapter lifecycle - registration, startup, shutdown, supervisor loop with exponential backoff
- **`dispatcher.py`**: Routes agent responses back to external sources using per-user locks with LRU eviction (max 10,000 locks)
- **`mapper.py`**: Session mapping logic - validates external user IDs, prevents duplicate messages, creates/retrieves agent sessions
- **`persistence.py`**: SQLite operations for source configs, session mappings, and deduplication (`processed_external_messages` table)
- **`credentials.py`**: Fernet encryption for API tokens; falls back to plaintext if `cryptography` unavailable
- **`rate_limiter.py`**: Token bucket implementation with async support; defaults: Telegram=30 msg/s, webhook=100 msg/s, WhatsApp=10 msg/s
- **`circuit_breaker.py`**: Three-state circuit breaker (CLOSED→OPEN→HALF_OPEN) with configurable failure threshold and recovery timeout
- **`cleanup.py`**: Periodic cleanup job (default 6h interval) - removes processed messages >24h old and inactive session mappings >30 days

## Database Schema

```sql
-- Source configurations
source_configs (source_id, source_type, name, config, credentials, enabled, status, error_message, created_at, updated_at)

-- Session mappings (external user → agent session)
session_mappings (mapping_id, source_id, external_user_id, agent_session_id, agent_dir, metadata, last_message_at, created_at)

-- Deduplication
processed_external_messages (source_id, external_message_id, processed_at)
```

## Key Classes

| Class | Purpose |
|-------|---------|
| `MessageSourceAdapter` | Abstract base for source adapters (Telegram, webhook, etc.) |
| `SourceRegistry` | Manages all adapters, handles start/stop/reload, supervisor loop |
| `ResponseDispatcher` | Routes completed agent responses back to sources |
| `SessionMapper` | Maps external users to agent sessions, handles deduplication |
| `CredentialManager` | Encrypts/decrypts API credentials |
| `TokenBucketLimiter` | Rate limiting per source |
| `CircuitBreaker` | Resilience pattern for external API calls |
| `SourceCleanup` | Periodic cleanup of old database records |

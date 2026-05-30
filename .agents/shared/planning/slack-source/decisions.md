# Architecture Decisions: Slack Source Integration

## ADR-001: Socket Mode over Webhooks

**Decision**: Use Slack Socket Mode (persistent WebSocket) instead of HTTP webhooks.

**Rationale**:
- No public HTTP endpoint required — works behind NAT/firewalls
- Simpler deployment — no need to configure SSL certificates or reverse proxy
- Real-time delivery — no webhook delivery delays
- Ensemble is a daemon process, not a public web server
- Telegram adapter already uses polling (similar pattern — no inbound HTTP needed)

**Consequences**:
- Requires `app_token` (xapp-) in addition to `bot_token` (xoxb-)
- Requires `slack-bolt` library dependency
- Socket Mode handler runs as a background asyncio task
- No changes needed to `daemon/routers/webhooks.py`

---

## ADR-002: Adapter as Package (Not Single File)

**Decision**: Implement as `daemon/sources/adapters/slack/` package with multiple files.

**Rationale**:
- Slack adapter is significantly more complex than Telegram (~500-600 lines adapter + rate limiter + thread manager + blocks)
- ThreadManager is a distinct concern warranting its own file
- SlackTieredRateLimiter has complex per-method tier logic
- Blocks formatter is optional and may grow

**Package Structure**:
```
daemon/sources/adapters/slack/
├── __init__.py          # Re-exports SlackAdapter
├── adapter.py           # Main SlackAdapter class (~500 lines)
├── rate_limiter.py      # SlackTieredRateLimiter (~80 lines)
├── thread_manager.py    # ThreadManager for thread TTL (~120 lines)
└── blocks.py            # Markdown-to-Slack-Blocks converter (~120 lines)
```

**Consequences**:
- Slightly more complex import path (`from .adapters.slack import SlackAdapter`)
- But follows the same pattern as other adapters when imported via `__init__.py`
- Individual concerns are testable in isolation

---

## ADR-003: Composite external_user_id for Instance Identity

**Decision**: Use composite format `{workspace_id}:{channel_or_user_id}[:{thread_ts}]` for the `external_user_id` field.

**Rationale**:
- Must be globally unique across workspaces (future multi-workspace support)
- Must distinguish DM vs channel vs thread for instance mapping
- Thread replies need separate instances from parent channel
- Format is parseable with `split(":", 2)` for max 3 parts

**Examples**:
| Context | external_user_id | Instance Behavior |
|---------|------------------|-------------------|
| DM from user U1 | `TWS:U1` | 1 user = 1 instance |
| Channel message | `TWS:C1` | Shared instance for channel |
| Thread in channel | `TWS:C1:1234.5678` | Separate instance per thread |

**Important**: external_user_id is for **identity and mapping** only. The actual Slack channel for response routing is retrieved via DB lookup in `send()` from `mapping_metadata`.

**Consequences**:
- Mapper sees these as opaque strings (after validation passes)
- Dispatcher routes based on metadata, not external_user_id
- Need to ensure the format doesn't exceed 256-char limit in mapper.py (workspace ~10 + channel ~10 + thread_ts ~20 = well within limits)

---

## ADR-004: Routing via DB Lookup in send(), Not Metadata

**Decision**: SlackAdapter's `send()` method performs a DB lookup using `source_id` + `external_user_id` to retrieve `slack_channel_id` and `slack_thread_ts` from `mapping_metadata`. It does NOT rely on `OutgoingMessage.metadata`.

**Rationale**:
- `ResponseDispatcher.dispatch_completed()` constructs `OutgoingMessage(metadata=metadata or {})` — the `metadata` parameter is always `None` or `{}`, never carrying Slack routing data
- Neither `task_processor.py` nor `message_job_handler.py` pass metadata through `dispatch_completed()`
- **Why Telegram works**: Telegram's `external_user_id` IS the numeric `chat_id` — directly routable. `send()` uses `message.external_user_id` directly.
- **Why Slack breaks**: Slack's composite `external_user_id` (`TWS:U1`) is NOT a routable Slack channel ID.
- DB lookup keeps changes contained to the Slack adapter — no dispatcher modifications needed
- Also fixes the `/new` confirmation path, where registry creates a bare `OutgoingMessage` with no metadata

**Implementation**:
1. `SourceRegistry._create_adapter_from_config()` injects `_source_repo` into SlackAdapter after construction
2. `_process_event()` stores `slack_channel_id` and `slack_thread_ts` in `msg.metadata`
3. `registry._handle_message()` extracts these and passes them as `extra_mapping_metadata` to the mapper
4. Mapper stores them in `mapping_metadata` JSON column
5. `send()` does `self._source_repo.get_instance_mapping(source_id, external_user_id)` → reads `mapping_metadata`

**Data Flow**:
```
Slack event → _process_event() → metadata{slack_channel_id, slack_thread_ts}
    ↓
registry._handle_message() → extra_mapping_metadata={slack_channel_id, ...}
    ↓
mapper.get_or_create_instance() → stores in DB mapping_metadata
    ↓
Agent processes → ResponseDispatcher → adapter.send()
    ↓
send() → DB lookup(source_id, external_user_id) → gets slack_channel_id
    ↓
Slack chat.postMessage(channel=slack_channel_id)
```

**Consequences**:
- No changes to base interfaces or dispatcher
- One extra DB read per response (acceptable — mappings are indexed, fast lookup)
- Adapter needs `_source_repo` reference (injected by registry)
- Routing data is persistent across adapter restarts (stored in DB)

---

## ADR-005: Thread Instances with 24h TTL

**Decision**: Thread-scoped instances have a 24-hour TTL with LRU eviction at 50 threads per workspace.

**Rationale**:
- Threads in Slack can be long-lived but are typically active for hours, not days
- 24h TTL balances memory usage with user experience (conversation context persists for a day)
- 50-thread cap prevents runaway instance creation in high-traffic channels
- LRU eviction ensures active threads survive, stale ones are cleaned up

**Lifecycle**:
1. First message in thread → ThreadManager creates ThreadInstance
2. Subsequent messages in same thread → reuse existing instance (if not expired)
3. 24h passes → next access triggers TTL check, instance evicted, agent instance terminated via `manager.terminate_instance()`
4. Workspace hits 50-thread cap → LRU eviction of oldest active thread, agent instance terminated

**Consequences**:
- ThreadManager tracks thread_ts → instance mapping in adapter memory
- ThreadManager needs `manager` reference for `terminate_instance()` on eviction
- SourceCleanup handles periodic cleanup of expired thread instances
- Evicted instances are terminated (not just forgotten) to free agent resources

---

## ADR-006: slack-bolt Async Framework

**Decision**: Use `slack-bolt` (async) as the Slack integration framework.

**Rationale**:
- Official Slack Python library — best maintained, best documented
- Handles Socket Mode WebSocket lifecycle, reconnection, and event parsing
- Provides decorator-based event handlers (@app.event("message"))
- Async-compatible with Ensemble's asyncio event loop
- Widely used in production (>2M downloads/month)

**Dependencies**:
- `slack-bolt>=1.18.0`
- `slack-sdk>=3.21.0` (transitive dependency of slack-bolt)

**Consequences**:
- Two new pip dependencies
- Socket Mode handler runs as asyncio task within Ensemble's event loop
- Event handlers are coroutine functions (async def)

---

## ADR-007: Minimal Base Interface Changes

**Decision**: Zero changes to IncomingMessage, OutgoingMessage, SourceConfig, or MessageSourceAdapter ABC.

**Rationale**:
- All Slack-specific data fits in the `metadata` dict field
- `images` field already supports list of strings
- `message_type` field already supports "text", "image", "command"
- `reply_to_id` field can store thread_ts for thread replies
- Keeping base interfaces unchanged means no risk to existing adapters

**One Exception**: `InstanceMapper.get_or_create_instance()` gains an optional `extra_mapping_metadata` parameter (defaults to None, backward-compatible).

**Consequences**:
- Zero breaking changes to existing code
- All Slack-specific logic is encapsulated in the adapter package
- Other adapters (Telegram, webhook) completely unaffected

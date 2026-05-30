# Phase 2: Instance Routing + Thread Lifecycle

## Objective

Implement the full routing architecture for Slack: DM resolution via conversations.open(), composite external_user_id format for identity, thread-based instance isolation with 24h TTL, channel shared-instance routing, and proper response dispatch back to Slack threads/channels.

## Coupling

- **Depends on**: Phase 1 (adapter package, enum, registry wiring)
- **Coupling type**: tight
- **Shared files with other phases**: `daemon/sources/adapters/slack/adapter.py`, `daemon/sources/mapper.py`
- **Shared APIs/interfaces**: InstanceMapper.get_or_create_instance(), IncomingMessage.metadata
- **Why this coupling**: Phase 2 builds on the adapter structure from Phase 1 and modifies mapper.py to support extra_mapping_metadata for Slack routing. The adapter's _process_event() method from Phase 1 needs enhancement for thread-aware routing.

## Context

### How Telegram Handles Session Mapping (Reference)

```python
# telegram.py _process_update():
# - Private chat: session_user_id = from_user_id (each user = own instance)
# - Group chat: session_user_id = chat_id (shared instance per group)
# - Metadata stores reply_chat_id = chat_id (for sending responses back to correct chat)
```

### Slack Routing Requirements

Slack is more complex than Telegram because:
1. **DMs require an API call** to resolve user_id → dm_channel_id (`conversations.open()`)
2. **Threads are first-class** — each thread_ts can have its own instance
3. **Response routing uses DB lookup** — `send()` reads `slack_channel_id` from `mapping_metadata` via DB lookup, not from `OutgoingMessage.metadata`
4. **Thread instances have TTL** — 24-hour auto-cleanup with LRU eviction

### Composite external_user_id Format

```
{workspace_id}:{channel_id}[:{thread_ts}]
```

Examples:
- DM: `T12345678:U12345678` (workspace:user_id)
- Channel: `T12345678:C12345678` (workspace:channel_id) 
- Thread: `T12345678:C12345678:1234567890.123456` (workspace:channel_id:thread_ts)

**Important**: external_user_id is for IDENTITY and MAPPING only. The actual Slack channel to send responses to is stored in `mapping_metadata.slack_channel_id`.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Implement DM resolution with caching** | Use conversations.open() to resolve user_id → dm_channel_id. Cache results in adapter memory with TTL. | `daemon/sources/adapters/slack/adapter.py` |
| 2 | **Implement composite external_user_id builder** | Build `{workspace}:{channel}[:{thread_ts}]` format for instance mapping identity | `daemon/sources/adapters/slack/adapter.py` |
| 3 | **Add extra_mapping_metadata to InstanceMapper** | Modify `get_or_create_instance()` to accept and store extra metadata (for slack_channel_id routing) | `daemon/sources/mapper.py` |
| 4 | **Wire registry call site for extra_mapping_metadata** | In `registry.py _handle_message()`, extract Slack metadata from `msg.metadata` and pass it as `extra_mapping_metadata=` to the mapper call | `daemon/sources/registry.py` |
| 5 | **Implement ThreadManager** | Track thread instances with TTL, LRU eviction, workspace cap (50 threads). Eviction calls `manager.terminate_instance()`. | `daemon/sources/adapters/slack/thread_manager.py` |
| 6 | **Rewrite _process_event for routing** | **Replaces** Phase 1's version entirely. Route DMs → user instance, channels → shared instance, threads → separate instance with TTL | `daemon/sources/adapters/slack/adapter.py` |
| 7 | **Enhance send() for thread-aware responses** | DB lookup already returns `slack_thread_ts` from mapping_metadata — verify it's stored correctly for threads | `daemon/sources/adapters/slack/adapter.py` |
| 8 | **Handle Slack file downloads** | Download file URLs using shared aiohttp session with bot token authorization headers, convert to base64 for IncomingMessage.images | `daemon/sources/adapters/slack/adapter.py` |
| 9 | **Add /new slash command support** | Handle /new command from Slack to reset conversation (same UX as Telegram) | `daemon/sources/adapters/slack/adapter.py` |

## Key Files

### Files to Create
- `daemon/sources/adapters/slack/thread_manager.py` — Thread instance lifecycle manager (~120 lines)

### Files to Modify
- `daemon/sources/adapters/slack/adapter.py` — Rewrite _process_event (replaces Phase 1 version), add DM resolution, thread awareness
- `daemon/sources/mapper.py` — Add `extra_mapping_metadata` parameter to `get_or_create_instance()`
- `daemon/sources/registry.py` — Wire `extra_mapping_metadata` in `_handle_message()` call site

## Detailed Implementation Guidance

### Task 1: DM Resolution

Slack DMs work differently from Telegram. In Slack, a DM is a "conversation" with its own channel_id (starting with `D`). To send a message to a user via DM, you need their DM channel_id, not their user_id.

```python
class SlackAdapter(MessageSourceAdapter):
    # ...
    
    async def _resolve_dm_channel(self, user_id: str) -> str | None:
        """Resolve user_id to DM channel_id using conversations.open().
        
        Results are cached to minimize API calls.
        """
        # Check cache
        if user_id in self._dm_cache:
            return self._dm_cache[user_id]
        
        try:
            result = await self._slack_rate_limiter.acquire_and_execute(
                "conversations.open",
                lambda: self._app.client.conversations_open(users=user_id)
            )
            channel = result.get("channel", {})
            dm_channel_id = channel.get("id")
            
            if dm_channel_id:
                self._dm_cache[user_id] = dm_channel_id
                return dm_channel_id
        except Exception as e:
            logger.error(f"Failed to resolve DM channel for {user_id}: {e}")
        
        return None
    
    async def _send_dm(self, user_id: str, text: str) -> bool:
        """Send a DM to a user by resolving their DM channel first."""
        dm_channel = await self._resolve_dm_channel(user_id)
        if not dm_channel:
            return False
        
        try:
            await self._slack_rate_limiter.acquire_and_execute(
                "chat.postMessage",
                lambda: self._app.client.chat_postMessage(
                    channel=dm_channel,
                    text=text,
                    mrkdwn=True,
                )
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send DM to {user_id}: {e}")
            return False
```

### Task 2: Composite external_user_id

```python
def _build_external_user_id(self, event: dict) -> str:
    """Build composite external_user_id for instance mapping.
    
    Format: {workspace_id}:{channel_or_user_id}[:{thread_ts}]
    
    This is for IDENTITY only — actual routing uses metadata.slack_channel_id.
    """
    channel_id = event.get("channel", "")
    channel_type = event.get("channel_type", "")
    user_id = event.get("user", "")
    thread_ts = event.get("thread_ts")
    workspace = self._workspace_id or "unknown"
    
    if channel_type == "im":
        # DM: use user_id for identity (1 user = 1 instance)
        base = f"{workspace}:{user_id}"
    else:
        # Channel/Group: use channel_id for identity (shared instance)
        base = f"{workspace}:{channel_id}"
    
    # Append thread_ts if threaded
    if thread_ts:
        return f"{base}:{thread_ts}"
    
    return base
```

### Task 3: Extra Mapping Metadata

The current `get_or_create_instance()` in mapper.py creates mappings with basic metadata. For Slack, we need to store `slack_channel_id` in the mapping's `mapping_metadata` so the dispatcher can route responses correctly.

```python
# mapper.py — modified get_or_create_instance()

async def get_or_create_instance(
    self,
    source_id: str,
    external_user_id: str,
    agent_id: str,
    force_new: bool = False,
    extra_mapping_metadata: dict | None = None,  # NEW PARAMETER
) -> str:
    # ... existing code ...
    
    # Create mapping
    metadata = {
        "source_id": source_id,
        "external_user_id": external_user_id,
    }
    
    # Merge extra metadata (for Slack: slack_channel_id, slack_thread_ts, etc.)
    if extra_mapping_metadata:
        metadata.update(extra_mapping_metadata)
    
    # ... rest unchanged ...
```

**Important**: This is a backward-compatible change. The parameter defaults to `None`, so all existing callers (Telegram, webhook) are unaffected.

### Task 4: Wire Registry Call Site for extra_mapping_metadata

The `extra_mapping_metadata` parameter added in Task 3 is useless unless the registry actually passes Slack routing data when calling the mapper. The call site is in `registry.py` `_handle_message()` at line ~660.

**Exact change needed in `daemon/sources/registry.py`**:

```python
# In _handle_message(), BEFORE the mapper.get_or_create_instance() call (~line 657-665):

# Existing code:
# agent_dir = msg.metadata.get("agent_dir") if msg.metadata else None
# ... (agent resolution logic) ...

# NEW: Build extra_mapping_metadata for Slack source types
extra_mapping_meta = None
if msg.metadata and msg.metadata.get("source_type") == "slack":
    extra_mapping_meta = {
        "slack_channel_id": msg.metadata.get("slack_channel_id"),
        "slack_thread_ts": msg.metadata.get("slack_thread_ts"),
    }
    # Filter out None values
    extra_mapping_meta = {k: v for k, v in extra_mapping_meta.items() if v is not None}

# Modified mapper call (add extra_mapping_metadata parameter):
instance_id = await mapper.get_or_create_instance(
    source_id=source_id,
    external_user_id=msg.external_user_id,
    agent_id=agent_dir,
    force_new=force_new,
    extra_mapping_metadata=extra_mapping_meta,  # NEW
)
```

**Why this is critical**: Without this wiring, the `slack_channel_id` and `slack_thread_ts` are never stored in `mapping_metadata`. The DB lookup in `send()` would find no routing info, and all responses would fail silently.

### Task 5: ThreadManager

```python
# daemon/sources/adapters/slack/thread_manager.py

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

THREAD_TTL_SECONDS = 24 * 60 * 60  # 24 hours
MAX_THREADS_PER_WORKSPACE = 50


@dataclass
class ThreadInstance:
    """Tracks a thread-scoped instance."""
    external_user_id: str      # Composite ID: workspace:channel:thread_ts
    agent_instance_id: str
    channel_id: str
    thread_ts: str
    created_at: float          # time.monotonic()
    last_active: float         # time.monotonic()


class ThreadManager:
    """Manages thread-scoped instances with TTL and LRU eviction.
    
    Each Slack thread can have its own agent instance, separate from the
    parent channel's shared instance. These thread instances have a 24-hour
    TTL and are evicted when the workspace cap is reached.
    """
    
    def __init__(self, max_threads: int = MAX_THREADS_PER_WORKSPACE,
                 ttl_seconds: float = THREAD_TTL_SECONDS,
                 manager=None):
        self._max_threads = max_threads
        self._ttl = ttl_seconds
        self._manager = manager  # InstanceManager — for terminate_instance() on eviction
        self._threads: OrderedDict[str, ThreadInstance] = OrderedDict()
        self._lock = asyncio.Lock()
    
    async def get(self, thread_ts: str) -> ThreadInstance | None:
        """Get thread instance if it exists and hasn't expired."""
        async with self._lock:
            instance = self._threads.get(thread_ts)
            if instance is None:
                return None
            
            # Check TTL
            if time.monotonic() - instance.created_at > self._ttl:
                del self._threads[thread_ts]
                return None
            
            # Update LRU
            self._threads.move_to_end(thread_ts)
            instance.last_active = time.monotonic()
            return instance
    
    async def put(self, thread_ts: str, instance: ThreadInstance) -> None:
        """Store a thread instance, evicting if necessary."""
        async with self._lock:
            # Evict expired threads first
            await self._evict_expired()
            
            # Evict LRU if at capacity
            while len(self._threads) >= self._max_threads:
                oldest_key, oldest = self._threads.popitem(last=False)
                logger.info(
                    f"Evicted thread instance: thread_ts={oldest_key}, "
                    f"instance={oldest.agent_instance_id[:8]}..."
                )
                # S2: Terminate the evicted agent instance to free resources
                # The manager reference is set by the adapter during initialization
                if self._manager:
                    try:
                        await self._manager.terminate_instance(oldest.agent_instance_id)
                        logger.info(f"Terminated evicted thread instance: {oldest.agent_instance_id[:8]}...")
                    except Exception as e:
                        logger.warning(f"Failed to terminate evicted instance {oldest.agent_instance_id[:8]}: {e}")
            
            self._threads[thread_ts] = instance
    
    async def _evict_expired(self) -> None:
        """Remove all expired thread instances and terminate their agent instances."""
        now = time.monotonic()
        expired = [
            (key, inst) for key, inst in self._threads.items()
            if now - inst.created_at > self._ttl
        ]
        for key, inst in expired:
            del self._threads[key]
            logger.debug(f"Evicted expired thread: {key}")
            # Terminate the expired agent instance
            if self._manager:
                try:
                    await self._manager.terminate_instance(inst.agent_instance_id)
                    logger.info(f"Terminated expired thread instance: {inst.agent_instance_id[:8]}...")
                except Exception as e:
                    logger.warning(f"Failed to terminate expired instance {inst.agent_instance_id[:8]}: {e}")
```

### Task 6: Rewrite _process_event (Replaces Phase 1 Version)

> **⚠️ IMPORTANT**: This method **replaces** the Phase 1 `_process_event()` entirely. Do NOT merge them. The Phase 1 version used simple `user_id`/`channel_id` as `external_user_id`. This version uses the composite format `{workspace}:{id}[:{thread_ts}]` and adds thread-aware routing. Overwrite Phase 1's version with this one.

```python
async def _process_event(self, body: dict, is_edit: bool = False) -> None:
    event = body.get("event", {})
    
    # Skip bot messages
    if event.get("bot_id") or event.get("user") == self._bot_user_id:
        return
    # Skip message_changed sub-events (edits don't need re-processing usually)
    if event.get("subtype") == "message_changed":
        return
    # Skip message_deleted
    if event.get("subtype") == "message_deleted":
        return
    
    user_id = event.get("user", "")
    channel_id = event.get("channel", "")
    channel_type = event.get("channel_type", "")
    text = event.get("text", "")
    thread_ts = event.get("thread_ts")
    ts = event.get("ts", "")
    
    if not user_id or not channel_id:
        return
    
    # Build composite external_user_id
    external_user_id = self._build_external_user_id(event)
    
    # Resolve DM channel_id if needed (for response routing)
    dm_channel_id = None
    if channel_type == "im":
        dm_channel_id = channel_id  # Already the DM channel
    
    # Handle file downloads
    images = None
    if event.get("files"):
        images = await self._download_files(event["files"])
    
    # Detect commands (slash commands come via separate events, but /new in text)
    command = None
    message_type = "text"
    if text.strip().lower() == "/new":
        message_type = "command"
        command = "/new"
    
    # Build metadata
    metadata = {
        "source_type": "slack",
        "message_id": ts,
        "slack": {
            "channel_id": channel_id,
            "channel_type": channel_type,
            "user_id": user_id,
            "thread_ts": thread_ts,
            "ts": ts,
            "workspace_id": self._workspace_id,
        },
        "agent": self._default_agent,
        # CRITICAL routing fields (used by send() and dispatcher)
        "slack_channel_id": channel_id,
        "slack_thread_ts": thread_ts,
        "reply_chat_id": channel_id,
    }
    
    if command == "/new":
        metadata["force_new_instance"] = True
        metadata["command"] = command
    
    incoming = IncomingMessage(
        external_user_id=external_user_id,
        content=text or "[File]",
        source_id=self.source_id,
        images=images,
        metadata=metadata,
        message_type=message_type,
    )
    
    await self._emit_message(incoming)
```

### Task 8: File Download Handling

Slack file URLs require authentication. Download using the **shared** aiohttp session (created in `start()`, stored as `self._session`) to avoid creating a new session per file:

```python
async def _download_files(self, files: list[dict]) -> list[str]:
    """Download Slack files and return as base64-encoded strings.
    
    Uses the adapter's shared aiohttp session (self._session) 
    instead of creating per-file sessions.
    """
    import base64
    
    if not self._session:
        logger.warning("No HTTP session available for file downloads")
        return []
    
    images = []
    headers = {"Authorization": f"Bearer {self._bot_token}"}
    
    for file_info in files:
        # Only handle image files
        if not file_info.get("mimetype", "").startswith("image/"):
            continue
        
        url = file_info.get("url_private_download")
        if not url:
            continue
        
        try:
            async with self._session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    b64 = base64.b64encode(content).decode("utf-8")
                    images.append(f"data:{file_info['mimetype']};base64,{b64}")
        except Exception as e:
            logger.warning(f"Failed to download Slack file: {e}")
    
    return images
```

### Task 9: /new Command

The `/new` command works the same as Telegram: metadata["force_new_instance"] = True. The registry's `_handle_message()` already handles this flag (lines 636-655). No special code needed beyond setting the flag in `_process_event()`.

However, **Slack slash commands** (registered in the App config) come through a different event type. For Phase 2, we support `/new` as a text message only. Slash command integration is deferred to Phase 3 (interactive features).

## Constraints

- **InstanceMapper change must be backward-compatible** — The `extra_mapping_metadata` parameter defaults to None.
- **Thread TTL uses monotonic time** — Not wall clock, to avoid issues with clock changes.
- **DM cache has no explicit TTL** — DM channel IDs are stable. Cache grows with active users. Consider adding LRU eviction if memory becomes an issue.
- **composite external_user_id must be parseable** — The format `{workspace}:{channel}[:{thread_ts}]` must be reliably splittable. Use `split(":", 2)` (max 3 parts).

## Deliverables

- [ ] DM resolution working (user_id → dm_channel_id via conversations.open())
- [ ] Composite external_user_id format implemented and tested
- [ ] InstanceMapper accepts extra_mapping_metadata (backward-compatible)
- [ ] ThreadManager tracks thread instances with 24h TTL and LRU eviction
- [ ] _process_event routes DMs → user instance, channels → shared instance
- [ ] Thread messages create separate instances
- [ ] send() correctly routes to channel/thread
- [ ] File images downloaded and forwarded as base64
- [ ] /new text command resets conversation

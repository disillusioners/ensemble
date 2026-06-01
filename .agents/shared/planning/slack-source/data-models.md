# Data Models for Slack Source Integration

## Overview

This document defines the data models used throughout the Slack source adapter. Models are organized by domain: messages, channels, users, workspaces, and internal state.

## Design Principles

1. **Reuse ensemble base models**: `IncomingMessage`, `OutgoingMessage`, `SourceConfig` — no modifications
2. **Slack-specific data goes in metadata dict**: Use `metadata["slack"]` namespace
3. **Internal models are dataclasses**: Lightweight, typed, no ORM
4. **Composite IDs**: All external_user_ids are composite: `{workspace_id}:{entity_id}[:{thread_ts}]`

## Core Models

### External User ID Formats

The `external_user_id` field in `IncomingMessage` uses composite formats:

| Context | Format | Example |
|---------|--------|---------|
| Direct Message (DM) | `{workspace}:{user_id}` | `T12345:U67890` |
| Channel message | `{workspace}:{channel_id}` | `T12345:C11111` |
| Thread reply | `{workspace}:{channel_id}:{thread_ts}` | `T12345:C11111:1234567890.123456` |

### Slack Metadata Schema

The `metadata["slack"]` dict contains all Slack-specific context:

```python
metadata = {
    "slack": {
        # Core fields (always present)
        "channel_id": str,           # Channel/DM ID (C..., D..., G...)
        "channel_type": str,         # "channel", "group", "im", "mpim"
        "channel_name": str | None,  # Channel name (if available)
        "user_id": str,              # Slack user ID (U...)
        "user_name": str | None,     # Display name
        "message_ts": str,           # Message timestamp (unique ID)
        "workspace_id": str,         # Team/workspace ID (T...)
        
        # Thread fields (when in thread)
        "thread_ts": str | None,     # Thread parent timestamp
        "parent_user_id": str | None,# User who started thread
        
        # Message content fields
        "bot_id": str | None,        # Bot ID (B...) if message from bot
        "subtype": str | None,       # Message subtype
        
        # File fields (when file shared)
        "file_id": str | None,       # File ID (F...)
        "file_name": str | None,     # Original filename
        "file_mimetype": str | None, # MIME type
        "file_size": int | None,     # File size in bytes
        
        # Interactive fields (Phase 2+)
        "action_id": str | None,     # Button/action identifier
        "action_type": str | None,   # "button", "select", etc.
        "action_value": str | None,  # Selected value
        "trigger_id": str | None,    # For opening modals
        
        # Event metadata
        "event_type": str,           # "message", "app_mention", "file_shared", etc.
    },
    "agent": str | None,            # Agent to route to
    "reply_chat_id": str,           # Where to send response
    "message_id": str,              # Deduplication ID (Slack ts or generated)
}
```

## Internal Data Models

### SlackMessage

```python
@dataclass
class SlackMessage:
    """Normalized Slack message."""
    ts: str                         # Message timestamp (unique ID)
    channel: str                    # Channel ID
    channel_type: str               # "channel", "group", "im", "mpim"
    user: str                       # User ID
    text: str                       # Message text (mrkdwn)
    thread_ts: str | None = None    # Thread parent ts
    bot_id: str | None = None       # Bot ID if from bot
    subtype: str | None = None      # Message subtype
    files: list[SlackFile] | None = None
    blocks: list[dict] | None = None
    reactions: list[dict] | None = None
    parent_user_id: str | None = None
    edited: dict | None = None      # {"user": "U...", "ts": "..."}
    
    @property
    def is_bot_message(self) -> bool:
        return self.bot_id is not None
    
    @property
    def is_thread_reply(self) -> bool:
        return self.thread_ts is not None and self.thread_ts != self.ts
    
    @property
    def is_thread_root(self) -> bool:
        return self.thread_ts is not None and self.thread_ts == self.ts
```

### SlackChannel

```python
@dataclass
class SlackChannel:
    """Slack channel/conversation."""
    id: str                         # Channel ID (C..., G..., D...)
    name: str | None = None         # Channel name
    type: str = "channel"           # "channel", "group", "im", "mpim"
    is_private: bool = False
    is_archived: bool = False
    is_general: bool = False
    creator: str | None = None      # User ID of creator
    num_members: int = 0
    topic: dict | None = None       # {"value": "...", "creator": "..."}
    purpose: dict | None = None     # {"value": "...", "creator": "..."}
    
    @property
    def is_dm(self) -> bool:
        return self.type == "im"
    
    @property
    def is_group_dm(self) -> bool:
        return self.type == "mpim"
```

### SlackUser

```python
@dataclass
class SlackUser:
    """Slack user profile."""
    id: str                         # User ID (U...)
    name: str | None = None         # Display name
    real_name: str | None = None    # Real name
    email: str | None = None        # Email (if available)
    is_bot: bool = False
    is_admin: bool = False
    is_owner: bool = False
    tz: str | None = None           # Timezone
    profile_image_url: str | None = None
    team_id: str | None = None      # Workspace ID
```

### SlackWorkspace

```python
@dataclass
class SlackWorkspace:
    """Slack workspace (team)."""
    id: str                         # Team ID (T...)
    name: str                       # Workspace name
    domain: str                     # Workspace domain (xxx.slack.com)
    icon_url: str | None = None
    email_domain: str | None = None
    
    @property
    def url(self) -> str:
        return f"https://{self.domain}.slack.com"
```

### SlackFile

```python
@dataclass
class SlackFile:
    """Slack file attachment."""
    id: str                         # File ID (F...)
    name: str                       # Filename
    title: str | None = None        # Display title
    mimetype: str | None = None     # MIME type
    filetype: str | None = None     # Slack file type
    size: int = 0                   # Size in bytes
    url_private: str | None = None  # Authenticated download URL
    url_private_download: str | None = None
    thumb_360: str | None = None    # Thumbnail URL
    created: int | None = None      # Unix timestamp
    
    @property
    def is_image(self) -> bool:
        return (self.mimetype or "").startswith("image/")
    
    @property
    def is_document(self) -> bool:
        doc_types = {"pdf", "doc", "docx", "txt", "csv", "xlsx", "json", "md"}
        return (self.filetype or "") in doc_types
```

### SlackUserGroup

```python
@dataclass
class SlackUserGroup:
    """Slack user group."""
    id: str                         # Usergroup ID (S...)
    name: str                       # Group name (e.g., "engineering")
    handle: str                     # Mention handle (e.g., "engineering")
    description: str | None = None
    users: list[str] = field(default_factory=list)  # User IDs
    channel_count: int = 0
    
    @property
    def mention(self) -> str:
        return f"<@{self.handle}>"
```

## Outgoing Message Metadata Schema

When `ResponseDispatcher` sends an `OutgoingMessage` to the Slack adapter, metadata controls behavior:

```python
outgoing_metadata = {
    # Required
    "channel_id": str,              # Target channel/DM
    
    # Optional — threading
    "thread_ts": str | None,        # Reply in thread
    "reply_broadcast": bool,        # Also show in main channel
    
    # Optional — formatting
    "parse_mode": str,              # "mrkdwn" (default) or "plain"
    "use_blocks": bool,             # Use Block Kit formatting
    
    # Optional — reactions
    "add_reaction": str | None,     # Emoji name to add to original message
    "original_ts": str | None,      # ts of message to react to
    
    # Optional — ephemeral
    "ephemeral": bool,              # Send as ephemeral (only visible to user)
    "ephemeral_user": str,          # User ID for ephemeral message
    
    # Optional — files
    "file_content": bytes,          # File content to upload
    "file_name": str,               # Filename for upload
    "file_title": str,              # Display title
}
```

## Rate Limit Tiers

```python
class SlackRateTier(Enum):
    """Slack API rate limit tiers."""
    TIER_1 = "tier1"   # ~1 call per minute per method
    TIER_2 = "tier2"   # ~20 calls per minute per method
    TIER_3 = "tier3"   # ~100 calls per minute per method
    TIER_4 = "tier4"   # ~100+ calls per minute per method
    SPECIAL = "special"  # Method-specific limits

RATE_LIMIT_CONFIG = {
    # chat.postMessage: ~1/sec per channel (Tier 2 with per-channel granularity)
    "chat.postMessage": {"tier": SlackRateTier.TIER_2, "calls": 1, "period": 1, "burst": 5},
    "chat.postEphemeral": {"tier": SlackRateTier.TIER_2, "calls": 4, "period": 60, "burst": 4},
    "chat.update": {"tier": SlackRateTier.TIER_2, "calls": 4, "period": 60, "burst": 4},
    "chat.delete": {"tier": SlackRateTier.TIER_2, "calls": 4, "period": 60, "burst": 4},
    
    # Tier 2 (20/min)
    "conversations.list": {"tier": SlackRateTier.TIER_2, "calls": 20, "period": 60, "burst": 5},
    "conversations.info": {"tier": SlackRateTier.TIER_2, "calls": 20, "period": 60, "burst": 10},
    "users.info": {"tier": SlackRateTier.TIER_2, "calls": 20, "period": 60, "burst": 10},
    "usergroups.list": {"tier": SlackRateTier.TIER_2, "calls": 20, "period": 60, "burst": 5},
    
    # Tier 3 (100/min)
    "reactions.add": {"tier": SlackRateTier.TIER_3, "calls": 100, "period": 60, "burst": 10},
    "reactions.remove": {"tier": SlackRateTier.TIER_3, "calls": 100, "period": 60, "burst": 10},
    "pins.add": {"tier": SlackRateTier.TIER_3, "calls": 100, "period": 60, "burst": 10},
    
    # Special
    "files.upload": {"tier": SlackRateTier.SPECIAL, "calls": 20, "period": 60, "burst": 3},
    "files.info": {"tier": SlackRateTier.TIER_2, "calls": 20, "period": 60, "burst": 10},
    "auth.test": {"tier": SlackRateTier.TIER_4, "calls": 200, "period": 60, "burst": 20},
    "views.open": {"tier": SlackRateTier.TIER_2, "calls": 20, "period": 60, "burst": 10},
    "views.publish": {"tier": SlackRateTier.TIER_2, "calls": 20, "period": 60, "burst": 10},
}
```

## Instance Mapping Storage

Instance mappings in the database use the composite `external_user_id`:

| source_id | external_user_id | agent_instance_id | agent_id |
|-----------|-----------------|-------------------|----------|
| `slack-acme` | `T12345:U67890` | `uuid-1` | `general-agent` |
| `slack-acme` | `T12345:C11111` | `uuid-2` | `support-bot` |
| `slack-acme` | `T12345:C11111:1234567890.123456` | `uuid-3` | `code-reviewer` |
| `slack-beta` | `T99999:U88888` | `uuid-4` | `general-agent` |

This naturally maps to the existing `instance_mappings` table — no schema changes needed.

### ⚠️ Critical: Identity vs. Routing Separation

**`external_user_id` is for instance IDENTITY, not for message ROUTING.**

| Concern | Field | Example | Purpose |
|---------|-------|---------|---------|
| **Instance identity** | `external_user_id` | `T12345:C11111` | Determines which agent instance handles the conversation |
| **Routing address** | `mapping_metadata.slack_channel_id` | `C11111` | Actual Slack channel ID to send responses to |
| **Thread routing** | `mapping_metadata.slack_thread_ts` | `1234567890.123456` | Thread timestamp for threaded replies |
| **DM resolution** | `mapping_metadata.slack_channel_id` | `D22222` | DM channel ID (resolved from U... user ID via `conversations.open`) |

### Mapping Metadata Schema

The `instance_mappings.mapping_metadata` JSON column stores routing info:

```json
{
  "source_id": "slack-acme",
  "external_user_id": "T12345:C11111",
  "slack_channel_id": "C11111",
  "slack_thread_ts": null,
  "slack_channel_type": "channel"
}
```

For DMs (where `external_user_id` contains a user ID, not a channel ID):

```json
{
  "source_id": "slack-acme",
  "external_user_id": "T12345:U67890",
  "slack_channel_id": "D22222",
  "slack_thread_ts": null,
  "slack_channel_type": "im"
}
```

### Why This Separation Is Needed

The dispatch pipeline (`ResponseDispatcher.dispatch_completed()`) creates `OutgoingMessage` with:
- `external_user_id` = composite ID from `source` string (e.g., `T12345:C11111`)
- `metadata` = **empty dict** — the pipeline does NOT pass through source metadata

For Telegram, `external_user_id` IS the chat_id (numeric), so `adapter.send()` can use it directly.

For Slack, `external_user_id` is a composite string that is NOT a valid Slack channel ID. The adapter must resolve the actual channel from the mapping metadata stored when the instance was created.

## Lifecycle Configuration

Instance lifecycle controls are configurable per source:

```json
{
  "config": {
    "default_agent": "general-agent",
    "workspace_id": "T12345",
    "thread_ttl": 86400,
    "max_instances": 50,
    "idle_threshold": 3600
  }
}
```

| Config Key | Default | Description |
|-----------|---------|-------------|
| `thread_ttl` | 86400 (24h) | Max lifetime (seconds) for thread-based instances |
| `max_instances` | 50 | Max concurrent instances per workspace |
| `idle_threshold` | 3600 (1h) | Time (seconds) before an instance is considered idle |

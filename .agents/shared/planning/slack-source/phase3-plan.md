# Phase 3: Testing + Polish + Documentation

## Objective

Create a comprehensive test suite for the Slack adapter, harden error handling for production use, add Slack Blocks support for rich message formatting, implement slash command event handling, and write setup/configuration documentation.

## Coupling

- **Depends on**: Phase 2 (full routing, thread management, DM resolution)
- **Coupling type**: loose
- **Shared files with other phases**: All `daemon/sources/adapters/slack/` files, `tests/`
- **Shared APIs/interfaces**: SlackAdapter public methods
- **Why this coupling**: Tests exercise the adapter's public API surface. They can be written independently once the interface is stable from Phase 2.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Unit test: SlackAdapter core** | Test start/stop/send/health_check with mocked slack-bolt | `tests/test_slack_adapter.py` |
| 2 | **Unit test: SlackTieredRateLimiter** | Test per-tier token buckets, method-to-tier mapping | `tests/test_slack_rate_limiter.py` |
| 3 | **Unit test: ThreadManager** | Test TTL, LRU eviction, workspace cap | `tests/test_slack_thread_manager.py` |
| 4 | **Integration test: registry wiring** | Test that SourceRegistry creates SlackAdapter from config | `tests/test_sources_registry.py` (extend existing) |
| 5 | **Integration test: mapper validation** | Test Slack user ID validation in validate_external_user_id() | `tests/test_sources_mapper.py` (extend existing) |
| 6 | **Integration test: end-to-end message flow** | Test incoming Slack event → instance creation → response dispatch | `tests/test_slack_e2e.py` |
| 7 | **Error handling hardening** | Handle Slack-specific errors (token expired, workspace disconnected, rate limit exceeded) | `daemon/sources/adapters/slack/adapter.py` |
| 8 | **Slack Blocks support (MVP)** | Parse agent responses for rich formatting (code blocks, lists, headers) and convert to Slack Blocks. **MVP quality** — production Block Kit conversion is a future improvement. | `daemon/sources/adapters/slack/blocks.py` |
| 9 | **Slash command handling** | Register app.command handler for /new and future commands | `daemon/sources/adapters/slack/adapter.py` |
| 10 | **Setup and configuration docs** | Document Slack App setup, Socket Mode config, OAuth scopes, credentials format | `docs/` |

## Key Files

### Files to Create
- `tests/test_slack_adapter.py` — Core adapter unit tests (~300 lines)
- `tests/test_slack_rate_limiter.py` — Rate limiter tests (~100 lines)
- `tests/test_slack_thread_manager.py` — Thread manager tests (~150 lines)
- `tests/test_slack_e2e.py` — End-to-end integration tests (~200 lines)
- `daemon/sources/adapters/slack/blocks.py` — Slack Blocks formatter (~120 lines)
- `docs/sources/slack-setup.md` — Setup documentation

### Files to Modify
- `daemon/sources/adapters/slack/adapter.py` — Error handling hardening, slash command support

## Detailed Implementation Guidance

### Task 1: Core Adapter Unit Tests

Follow the testing pattern from `tests/test_sources_registry.py`:

```python
# tests/test_slack_adapter.py

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from daemon.sources.adapters.slack import SlackAdapter
from daemon.sources.base import SourceConfig, IncomingMessage, OutgoingMessage

@pytest.fixture
def slack_config():
    return SourceConfig(
        source_id="slack-test",
        source_type="slack",
        name="Test Slack Bot",
        config={
            "default_agent": "coder",
            "polling_enabled": True,
        },
        credentials={
            "bot_token": "xoxb-test-token",
            "app_token": "xapp-test-token",
        },
    )

@pytest.fixture
def on_message():
    return AsyncMock()

@pytest.fixture
def adapter(slack_config, on_message):
    return SlackAdapter(slack_config, on_message)

class TestSlackAdapterInit:
    def test_requires_bot_token(self, on_message):
        config = SourceConfig(
            source_id="slack-test",
            source_type="slack",
            name="Test",
            config={},
            credentials={"app_token": "xapp-test"},
        )
        adapter = SlackAdapter(config, on_message)
        with pytest.raises(ValueError, match="bot_token"):
            asyncio.get_event_loop().run_until_complete(adapter.start())
    
    def test_requires_app_token(self, on_message):
        config = SourceConfig(
            source_id="slack-test",
            source_type="slack",
            name="Test",
            config={},
            credentials={"bot_token": "xoxb-test"},
        )
        adapter = SlackAdapter(config, on_message)
        with pytest.raises(ValueError, match="app_token"):
            asyncio.get_event_loop().run_until_complete(adapter.start())

class TestSlackAdapterStart:
    @pytest.mark.asyncio
    async def test_start_success(self, adapter):
        with patch("daemon.sources.adapters.slack.adapter.AsyncApp") as MockApp, \
             patch("daemon.sources.adapters.slack.adapter.AsyncSocketModeHandler") as MockHandler:
            
            mock_app_instance = MagicMock()
            mock_app_instance.client.auth_test = AsyncMock(return_value={
                "user_id": "UBOT123",
                "team_id": "TWORKSPACE1",
            })
            MockApp.return_value = mock_app_instance
            MockHandler.return_value.start_async = AsyncMock()
            
            await adapter.start()
            
            assert adapter._bot_user_id == "UBOT123"
            assert adapter._workspace_id == "TWORKSPACE1"

class TestSlackAdapterSend:
    @pytest.mark.asyncio
    async def test_send_with_db_lookup(self, adapter):
        """Test that send() resolves routing via DB lookup, not metadata."""
        adapter._status = SourceStatus.RUNNING
        adapter._circuit_breaker = MagicMock()
        adapter._circuit_breaker.can_execute = AsyncMock(return_value=True)
        adapter._circuit_breaker.record_success = AsyncMock()
        
        # Mock the DB lookup
        mock_mapping = MagicMock()
        mock_mapping.mapping_metadata = {
            "slack_channel_id": "CCHANNEL1",
            "slack_thread_ts": "1234.5678",
        }
        mock_repo = MagicMock()
        mock_repo.get_instance_mapping = MagicMock(return_value=mock_mapping)
        adapter._source_repo = mock_repo
        
        # Mock the Slack API
        adapter._app = MagicMock()
        adapter._app.client.chat_postMessage = AsyncMock(return_value={"ok": True})
        
        msg = OutgoingMessage(
            external_user_id="TWS:U1",
            content="Hello",
            source_id="slack-test",
            metadata={},  # Empty! This is what the dispatcher actually sends
        )
        result = await adapter.send(msg)
        assert result is True
        # Verify DB lookup was called with correct args
        mock_repo.get_instance_mapping.assert_called_once_with("slack-test", "TWS:U1")
        # Verify Slack API was called with the looked-up channel_id
        adapter._app.client.chat_postMessage.assert_called_once()
        call_kwargs = adapter._app.client.chat_postMessage.call_args[1]
        assert call_kwargs["channel"] == "CCHANNEL1"
        assert call_kwargs["thread_ts"] == "1234.5678"
    
    @pytest.mark.asyncio
    async def test_send_fails_when_no_mapping(self, adapter):
        """Test that send() returns False when DB has no mapping (user never messaged)."""
        adapter._status = SourceStatus.RUNNING
        adapter._circuit_breaker = MagicMock()
        adapter._circuit_breaker.can_execute = AsyncMock(return_value=True)
        
        # Mock DB returning no mapping
        mock_repo = MagicMock()
        mock_repo.get_instance_mapping = MagicMock(return_value=None)
        adapter._source_repo = mock_repo
        
        msg = OutgoingMessage(
            external_user_id="TWS:U1",
            content="Hello",
            source_id="slack-test",
            metadata={},
        )
        result = await adapter.send(msg)
        assert result is False
    
    @pytest.mark.asyncio
    async def test_send_falls_back_when_no_repo(self, adapter):
        """Test that send() handles missing _source_repo gracefully."""
        adapter._status = SourceStatus.RUNNING
        adapter._circuit_breaker = MagicMock()
        adapter._circuit_breaker.can_execute = AsyncMock(return_value=True)
        adapter._source_repo = None  # Not injected
        
        msg = OutgoingMessage(
            external_user_id="TWS:U1",
            content="Hello",
            source_id="slack-test",
            metadata={},
        )
        result = await adapter.send(msg)
        assert result is False

class TestSlackProcessEvent:
    @pytest.mark.asyncio
    async def test_dm_creates_user_session(self, adapter, on_message):
        adapter._status = SourceStatus.RUNNING
        adapter._bot_user_id = "UBOT123"
        adapter._workspace_id = "TWS1"
        
        body = {
            "event": {
                "type": "message",
                "user": "UUSER1",
                "channel": "DCHANNEL1",
                "channel_type": "im",
                "text": "Hello bot!",
                "ts": "1234567890.123456",
            }
        }
        
        await adapter._process_event(body)
        
        on_message.assert_called_once()
        msg = on_message.call_args[0][0]
        assert isinstance(msg, IncomingMessage)
        assert msg.external_user_id == "TWS1:UUSER1"  # workspace:user
        assert msg.metadata["slack_channel_id"] == "DCHANNEL1"
    
    @pytest.mark.asyncio
    async def test_channel_creates_shared_session(self, adapter, on_message):
        adapter._status = SourceStatus.RUNNING
        adapter._bot_user_id = "UBOT123"
        adapter._workspace_id = "TWS1"
        
        body = {
            "event": {
                "type": "message",
                "user": "UUSER1",
                "channel": "CCHANNEL1",
                "channel_type": "channel",
                "text": "Hello everyone!",
                "ts": "1234567890.123456",
            }
        }
        
        await adapter._process_event(body)
        
        msg = on_message.call_args[0][0]
        assert msg.external_user_id == "TWS1:CCHANNEL1"  # workspace:channel (shared)
    
    @pytest.mark.asyncio
    async def test_thread_appends_thread_ts(self, adapter, on_message):
        adapter._status = SourceStatus.RUNNING
        adapter._bot_user_id = "UBOT123"
        adapter._workspace_id = "TWS1"
        
        body = {
            "event": {
                "type": "message",
                "user": "UUSER1",
                "channel": "CCHANNEL1",
                "channel_type": "channel",
                "text": "Reply in thread",
                "ts": "1234567890.999999",
                "thread_ts": "1234567890.123456",
            }
        }
        
        await adapter._process_event(body)
        
        msg = on_message.call_args[0][0]
        assert msg.external_user_id == "TWS1:CCHANNEL1:1234567890.123456"
    
    @pytest.mark.asyncio
    async def test_skips_bot_messages(self, adapter, on_message):
        adapter._status = SourceStatus.RUNNING
        adapter._bot_user_id = "UBOT123"
        
        body = {
            "event": {
                "type": "message",
                "user": "UBOT123",  # Same as bot
                "channel": "DCHANNEL1",
                "text": "Bot echo",
                "ts": "1234567890.123456",
            }
        }
        
        await adapter._process_event(body)
        on_message.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_new_command_sets_force_new(self, adapter, on_message):
        adapter._status = SourceStatus.RUNNING
        adapter._bot_user_id = "UBOT123"
        adapter._workspace_id = "TWS1"
        
        body = {
            "event": {
                "type": "message",
                "user": "UUSER1",
                "channel": "DCHANNEL1",
                "channel_type": "im",
                "text": "/new",
                "ts": "1234567890.123456",
            }
        }
        
        await adapter._process_event(body)
        
        msg = on_message.call_args[0][0]
        assert msg.metadata.get("force_new_instance") is True
        assert msg.metadata.get("command") == "/new"
```

### Task 7: Error Handling

Key Slack-specific errors to handle:

```python
# In adapter.py, wrap API calls with Slack-specific error handling

class SlackAPIError(Exception):
    """Slack API error with structured info."""
    def __init__(self, error: str, response: dict | None = None):
        self.error = error
        self.response = response or {}
        super().__init__(f"Slack API error: {error}")

# Common Slack errors:
# - "not_authed" → bot token invalid/expired
# - "token_expired" → need to refresh (unlikely with bot tokens)
# - "rate_limited" → respect Retry-After header
# - "channel_not_found" → channel archived or bot removed
# - "not_in_channel" → bot not in channel
# - "cannot_dm_bot" → tried to DM the bot itself
# - "message_not_found" → trying to update/delete non-existent message

async def _safe_api_call(self, method: str, **kwargs) -> dict:
    """Execute Slack API call with error handling."""
    try:
        result = await self._slack_rate_limiter.acquire_and_execute(
            method,
            lambda: getattr(self._app.client, method.replace(".", "_"))(**kwargs)
        )
        return result
    except Exception as e:
        error_str = str(e).lower()
        
        if "not_authed" in error_str or "invalid_auth" in error_str:
            logger.critical(f"Slack auth failed — bot token may be invalid: {e}")
            await self._circuit_breaker.record_failure()
            raise SlackAPIError("authentication_failed", {"error": str(e)})
        
        elif "rate_limited" in error_str:
            retry_after = getattr(e, 'response', {}).get('headers', {}).get('Retry-After', 60)
            logger.warning(f"Slack rate limited, retry after {retry_after}s")
            await self._circuit_breaker.record_failure()
            raise SlackAPIError("rate_limited", {"retry_after": retry_after})
        
        elif "channel_not_found" in error_str:
            logger.warning(f"Slack channel not found: {kwargs.get('channel')}")
            # Don't record as circuit breaker failure — not a transient error
            raise SlackAPIError("channel_not_found")
        
        else:
            logger.error(f"Slack API error: {e}")
            await self._circuit_breaker.record_failure()
            raise SlackAPIError("unknown", {"error": str(e)})
```

### Task 8: Slack Blocks Support

Slack has its own rich text format (Block Kit). Agent responses in Markdown should be converted to Slack's mrkdwn or Block Kit format:

```python
# daemon/sources/adapters/slack/blocks.py

def markdown_to_slack_blocks(text: str) -> list[dict]:
    """Convert Markdown text to Slack Block Kit format.
    
    MVP QUALITY: Best-effort conversion for basic formatting only.
    Full production Block Kit conversion (tables, nested lists, 
    rich code blocks with syntax highlighting) is a future improvement.
    
    Slack's mrkdwn supports: *bold*, _italic_, ~strikethrough~, `code`, ```code blocks```
    It does NOT support: headers (#), tables, HTML
    """
    if not text:
        return []
    
    # Slack mrkdwn supports: *bold*, _italic_, ~strikethrough~, `code`, ```code blocks```
    # It does NOT support: headers (#), tables, HTML
    
    # Split into chunks (< 3000 chars per block — Slack limit)
    blocks = []
    remaining = text
    
    while remaining:
        chunk = remaining[:2900]
        if len(remaining) > 2900:
            # Try to split at newline
            last_nl = chunk.rfind("\n")
            if last_nl > 1000:
                chunk = remaining[:last_nl]
            remaining = remaining[len(chunk):]
        else:
            remaining = ""
        
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": chunk,
            }
        })
    
    return blocks
```

**Integration in send()**: Use blocks when content is long or has formatting:

```python
async def send(self, message: OutgoingMessage) -> bool:
    # ...
    content = message.content
    
    # Use blocks for longer/formatted content, text for simple messages
    if len(content) > 400 or "```" in content:
        blocks = markdown_to_slack_blocks(content)
        await self._safe_api_call("chat.postMessage",
            channel=channel_id,
            text=content[:200],  # Fallback text for notifications
            blocks=blocks,
            thread_ts=thread_ts,
        )
    else:
        await self._safe_api_call("chat.postMessage",
            channel=channel_id,
            text=content,
            mrkdwn=True,
            thread_ts=thread_ts,
        )
```

### Task 9: Slash Command Handling

Slack slash commands require App-level configuration and come through a different event type:

```python
# In adapter.py start():

@self._app.command("/new")
async def handle_new_command(ack, body, say):
    """Handle /new slash command to reset conversation."""
    await ack()  # Must acknowledge within 3 seconds
    
    user_id = body["user_id"]
    channel_id = body["channel_id"]
    
    # Build message as if user typed "/new"
    metadata = {
        "source_type": "slack",
        "message_id": f"cmd_{body['command_id']}_{body['trigger_id']}",
        "slack": {
            "channel_id": channel_id,
            "user_id": user_id,
            "workspace_id": self._workspace_id,
        },
        "agent": self._default_agent,
        "slack_channel_id": channel_id,
        "force_new_instance": True,
        "command": "/new",
    }
    
    external_user_id = f"{self._workspace_id}:{user_id}"
    
    incoming = IncomingMessage(
        external_user_id=external_user_id,
        content="/new",
        source_id=self.source_id,
        metadata=metadata,
        message_type="command",
    )
    
    await self._emit_message(incoming)
```

### Task 10: Setup Documentation

```markdown
# docs/sources/slack-setup.md

## Slack Source Setup Guide

### Prerequisites
- A Slack workspace where you have admin or app installation permissions
- Ensemble daemon running

### Step 1: Create Slack App
1. Go to https://api.slack.com/apps
2. Click "Create New App" → "From scratch"
3. Name your app (e.g., "Ensemble Bot")
4. Select your workspace

### Step 2: Configure OAuth Scopes
Navigate to "OAuth & Permissions" → "Bot Token Scopes" and add:
- `chat:write` — Send messages
- `channels:history` — Read channel messages
- `groups:history` — Read private channel messages
- `im:history` — Read DM messages
- `mpim:history` — Read multi-party DM messages
- `channels:read` — List channels
- `groups:read` — List private channels
- `im:read` — List DMs
- `files:read` — Read shared files
- `users:read` — Get user info
- `users:read.email` — Get user email (optional)
- `reactions:write` — Add reactions (optional)
- `commands` — Register slash commands (optional)

### Step 3: Enable Socket Mode
1. Go to "Socket Mode" in app settings
2. Enable Socket Mode
3. Copy the App-Level Token (starts with `xapp-`)
4. Ensure the token has `connections:write` scope

### Step 4: Install App to Workspace
1. Go to "Install App" 
2. Install to workspace
3. Copy the Bot User OAuth Token (starts with `xoxb-`)

### Step 5: Create Source in Ensemble
```bash
curl -X POST http://localhost:8079/sources \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "slack-main",
    "source_type": "slack",
    "name": "My Slack Bot",
    "config": {
      "default_agent": "coder"
    },
    "credentials": {
      "bot_token": "xoxb-your-bot-token",
      "app_token": "xapp-your-app-token"
    },
    "enabled": true
  }'
```

### Step 6: Verify Connection
```bash
curl -X POST http://localhost:8079/sources/test \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "slack",
    "credentials": {
      "bot_token": "xoxb-your-bot-token",
      "app_token": "xapp-your-app-token"
    }
  }'
```

### Optional: Slash Commands
1. Go to "Slash Commands" in app settings
2. Create `/new` command (Request URL can be any URL — Socket Mode doesn't use it)
3. Description: "Reset conversation"
```

## Constraints

- **Tests must mock slack-bolt** — Do NOT require a real Slack workspace for unit tests. Use `unittest.mock.AsyncMock` for the slack-bolt AsyncApp and client.
- **Tests must not depend on network** — All HTTP calls mocked.
- **Blocks conversion is best-effort** — Slack's mrkdwn doesn't support all Markdown features. Gracefully fall back to plain text.
- **Slash commands require App-level config** — Cannot be tested without Slack app config. Integration tests should mock the command event body.

## Deliverables

- [ ] test_slack_adapter.py with >90% coverage on adapter code
- [ ] test_slack_rate_limiter.py testing all 4 tiers
- [ ] test_slack_thread_manager.py testing TTL, LRU, cap
- [ ] Extended test_sources_registry.py with Slack adapter creation test
- [ ] Extended test_sources_mapper.py with Slack user ID validation test
- [ ] test_slack_e2e.py with full message flow test
- [ ] Slack-specific error handling (auth failure, rate limit, channel not found)
- [ ] Slack Blocks formatter for rich message output
- [ ] Slash command /new handling
- [ ] Setup documentation (docs/sources/slack-setup.md)

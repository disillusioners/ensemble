# SSE + Message Unification Plan

## Overview

This document outlines the plan to unify message handling between SSE (Server-Sent Events) and the list messages API, ensuring identical message formats and comprehensive SSE coverage for all message types.

## Problem Statement

### Current Issues

1. **Inconsistent message formats**: SSE events and `GET /messages` API return different message structures
2. **Missing messages in SSE**: User messages from child agents → parent agent are stored in DB but NOT sent via SSE
3. **Scattered SSE emission**: SSE events are emitted from multiple places in the codebase
4. **Final assistant message not broadcast**: `processing_completed` doesn't include the full assistant message content

### Current Message Flow

```
USER INPUT
├── API POST /instances/{id}/messages
│   → manager.enqueue_message()
│   → MessageQueue (DB) + Task (DB)
│   → message_received event (SSE)
│
└── Sources (Telegram, etc.)
    → SourceRegistry → _handle_message()
    → Same enqueue path

PROCESSING (Worker Pool)
├── Streaming SSE (in-memory):
│   → content_chunk, thinking, tool_call, tool_complete
│   → Emitted from manager._process_message_with_tracking()
│
├── Final result:
│   → processing_completed event (SSE + DB)
│   → Does NOT include full message content/thinking/tool_calls!
│
└── Assistant message stored:
    → LangGraph checkpoints (via AsyncSqliteSaver)
    → Only retrievable via GET /messages, NOT via SSE

CHILD → PARENT
├── Completion report → enqueued as user message in parent's queue
├── child_completed event emitted
└── Parent processes it like any user message
    → BUT: no SSE emitted for the actual message content!
```

## Architecture

### Pattern: MessageService Coordination Layer

We will NOT replace existing storage mechanisms (LangGraph checkpoints, message queues). Instead, we create a thin coordination layer that:

1. Accepts messages that are **already stored** by existing code
2. Emits the canonical SSE event with full message content
3. Can be adopted incrementally without replacing existing storage code

### Components

```
┌─────────────────────────────────────────────────────────────┐
│                      MessageService                          │
│  (Coordinates storage confirmation + SSE emission)           │
│                                                             │
│  - on_user_message_stored()    → message_received event     │
│  - on_assistant_message_completed() → message_completed    │
│  - on_child_completion_report() → message_received         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       EventBus                              │
│  (Existing - handles DB persistence + SSE delivery)         │
└─────────────────────────────────────────────────────────────┘
```

### New Event: `message_completed`

| Event | Purpose | Payload |
|-------|---------|---------|
| `processing_completed` | Task lifecycle (existing) | task_id, message_id, success |
| `message_completed` | Content event (NEW) | Full message: role, content, thinking, tool_calls |

## Implementation Plan

### Phase 1: Foundation (Week 1) - No Behavioral Changes

#### 1.1 Add UnifiedMessage Model

**File**: `daemon/models/messages.py` (new)

```python
"""Unified message models for consistent API + SSE formats."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ToolCallInfo(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = {}
    output: str | None = None


class UnifiedMessage(BaseModel):
    """Canonical message format for both GET /messages and SSE events."""
    
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    instance_id: str
    role: str  # user | assistant | system | tool
    content: str = ""
    thinking: str | None = None
    thinking_extracted: str | None = None
    tool_calls: list[ToolCallInfo] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str | None = None  # api, telegram:xxx, child:instance_id

    def to_sse_data(self) -> dict[str, Any]:
        """Format for SSE event data payload."""
        payload = {
            "message_id": self.message_id,
            "instance_id": self.instance_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
        }
        if self.thinking is not None:
            payload["thinking"] = self.thinking
        if self.thinking_extracted is not None:
            payload["thinking_extracted"] = self.thinking_extracted
        if self.tool_calls:
            payload["tool_calls"] = [tc.model_dump() for tc in self.tool_calls]
        if self.source:
            payload["source"] = self.source
        return payload

    def to_api_response(self) -> dict[str, Any]:
        """Format for GET /messages API response."""
        return {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
            "thinking": self.thinking,
            "thinking_extracted": self.thinking_extracted,
            "tool_calls": [tc.model_dump() for tc in self.tool_calls] if self.tool_calls else None,
            "created_at": self.created_at.isoformat(),
        }
```

#### 1.2 Add MESSAGE_COMPLETED to EventKind

**File**: `daemon/repositories/event/models.py`

```python
class EventKind(str, enum.Enum):
    MESSAGE_RECEIVED = "message_received"
    PROCESSING_STARTED = "processing_started"
    PROCESSING_COMPLETED = "processing_completed"
    PROCESSING_FAILED = "processing_failed"
    CHILD_COMPLETED = "child_completed"
    CHILD_FAILED = "child_failed"
    INSTANCE_COMPLETED = "instance_completed"
    ERROR = "error"
    # NEW
    MESSAGE_COMPLETED = "message_completed"
```

#### 1.3 Create MessageService

**File**: `daemon/services/message_service.py` (new)

```python
"""Unified message storage + SSE emission service.

Single entry point:
    store_message_and_emit(message) → stores + emits SSE automatically
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from daemon.models.messages import ToolCallInfo, UnifiedMessage

if TYPE_CHECKING:
    from daemon.services.event_bus import EventBus

logger = logging.getLogger(__name__)


class MessageService:
    """Coordinates message storage confirmation + SSE emission."""
    
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
    
    async def on_user_message_stored(
        self,
        instance_id: str,
        message_id: str,
        content: str,
        source: str = "api",
        **extra: Any,
    ) -> UnifiedMessage:
        """Emit message_received after user message is queued."""
        message = UnifiedMessage(
            message_id=message_id,
            instance_id=instance_id,
            role="user",
            content=content,
            source=source,
            created_at=datetime.now(timezone.utc),
        )
        
        await self._event_bus.create_message_received_event(
            instance_id=instance_id,
            message_id=message_id,
            content={
                "source": source,
                "content": content,
                **extra,
            },
        )
        
        return message
    
    async def on_assistant_message_completed(
        self,
        instance_id: str,
        original_message_id: str,
        content: str = "",
        thinking: str | None = None,
        thinking_extracted: str | None = None,
        tool_calls: list[ToolCallInfo] | None = None,
    ) -> UnifiedMessage:
        """Emit message_completed with full assistant response.
        
        This is THE key addition. Call this after LangGraph has stored
        the assistant message. It broadcasts the full message via SSE.
        """
        assistant_message_id = str(uuid.uuid4())
        
        message = UnifiedMessage(
            message_id=assistant_message_id,
            instance_id=instance_id,
            role="assistant",
            content=content,
            thinking=thinking,
            thinking_extracted=thinking_extracted,
            tool_calls=tool_calls,
            created_at=datetime.now(timezone.utc),
        )
        
        # Emit message_completed event with full payload
        await self._event_bus.create_event(
            instance_id=instance_id,
            kind="message_completed",
            message_id=original_message_id,
            data={
                "original_message_id": original_message_id,
                "message": message.to_sse_data(),
            },
        )
        
        # Also emit processing_completed with full content (backward compat)
        await self._event_bus.create_processing_completed_event(
            instance_id=instance_id,
            message_id=original_message_id,
            result={
                "task_id": None,
                "message_id": original_message_id,
                "success": True,
                "content": content,
                "thinking": thinking,
                "thinking_extracted": thinking_extracted,
                "tool_calls": [tc.model_dump() for tc in tool_calls] if tool_calls else None,
                "assistant_message_id": assistant_message_id,
            },
        )
        
        logger.info(
            f"Assistant message completed: {assistant_message_id} "
            f"for instance {instance_id[:8]}..."
        )
        return message
    
    async def on_child_completion_report(
        self,
        parent_instance_id: str,
        child_instance_id: str,
        report_content: str,
        message_id: str,
    ) -> UnifiedMessage:
        """Emit message for child agent's completion report to parent."""
        message = UnifiedMessage(
            message_id=message_id,
            instance_id=parent_instance_id,
            role="user",
            content=report_content,
            source=f"child:{child_instance_id}",
            created_at=datetime.now(timezone.utc),
        )
        
        await self._event_bus.create_message_received_event(
            instance_id=parent_instance_id,
            message_id=message_id,
            content={
                "source": f"child:{child_instance_id}",
                "content": report_content,
                "child_instance_id": child_instance_id,
            },
        )
        
        return message
```

#### 1.4 Add Tests for MessageService

**File**: `tests/unit/test_message_service.py` (new)

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from daemon.models.messages import ToolCallInfo, UnifiedMessage
from daemon.services.message_service import MessageService


@pytest.fixture
def mock_event_bus():
    bus = MagicMock()
    bus.create_message_received_event = AsyncMock()
    bus.create_processing_completed_event = AsyncMock()
    bus.create_event = AsyncMock()
    return bus


@pytest.fixture
def service(mock_event_bus):
    return MessageService(event_bus=mock_event_bus)


class TestUserMessageStored:
    @pytest.mark.asyncio
    async def test_emits_message_received(self, service, mock_event_bus):
        msg = await service.on_user_message_stored(
            instance_id="inst-1",
            message_id="msg-1",
            content="Hello",
            source="api",
        )
        
        assert isinstance(msg, UnifiedMessage)
        assert msg.role == "user"
        assert msg.content == "Hello"
        mock_event_bus.create_message_received_event.assert_called_once()


class TestAssistantMessageCompleted:
    @pytest.mark.asyncio
    async def test_emits_both_events(self, service, mock_event_bus):
        msg = await service.on_assistant_message_completed(
            instance_id="inst-1",
            original_message_id="user-msg-1",
            content="Response text",
            thinking="Let me think...",
        )
        
        assert isinstance(msg, UnifiedMessage)
        assert msg.role == "assistant"
        assert msg.content == "Response text"
        
        # message_completed event
        mock_event_bus.create_event.assert_called_once()
        
        # processing_completed event (backward compat)
        mock_event_bus.create_processing_completed_event.assert_called_once()


class TestUnifiedMessageFormats:
    def test_to_sse_data_omits_none_fields(self):
        msg = UnifiedMessage(
            message_id="m1",
            instance_id="i1",
            role="user",
            content="Hi",
        )
        
        data = msg.to_sse_data()
        assert "thinking" not in data
        assert "tool_calls" not in data
    
    def test_to_api_response_format(self):
        msg = UnifiedMessage(
            message_id="m1",
            instance_id="i1",
            role="assistant",
            content="Response",
        )
        
        resp = msg.to_api_response()
        assert resp["role"] == "assistant"
        assert resp["content"] == "Response"
```

### Phase 2: Integration (Week 2) - Add New Event Emission

#### 2.1 Wire MessageService into InstanceManager

**File**: `daemon/manager.py`

```python
from daemon.services.message_service import MessageService

class InstanceManager:
    def __init__(self, config: Config):
        # ... existing init ...
        self._message_service: MessageService | None = None
    
    async def initialize(self) -> None:
        # ... existing initialization ...
        
        # NEW: Create MessageService
        self._message_service = MessageService(event_bus=self._event_bus)
```

#### 2.2 Inject into TaskProcessor

**File**: `daemon/services/task_processor.py`

```python
class ProcessMessageProcessor:
    def __init__(
        self,
        # ... existing params ...
        message_service: MessageService | None = None,
    ):
        # ... existing init ...
        self._message_service = message_service
```

#### 2.3 Emit message_completed After Processing

**File**: `daemon/services/task_processor.py` - in `process()` method

After `_process_message_with_tracking()` completes successfully:

```python
async def process(self, task, cancellation_token=None):
    # ... existing code ...
    
    result = await self._manager._process_message_with_tracking(...)
    
    # NEW: Broadcast the full assistant message
    if self._message_service and result:
        tool_calls = None
        if getattr(result, 'tool_calls', None):
            tool_calls = [
                ToolCallInfo(
                    id=tc.get("id", str(uuid.uuid4())),
                    name=tc.get("name", ""),
                    arguments=tc.get("arguments", {}),
                    output=tc.get("output"),
                )
                for tc in result.tool_calls
            ]
        
        await self._message_service.on_assistant_message_completed(
            instance_id=task.instance_id,
            original_message_id=task.message_id,
            content=result.content or "",
            thinking=getattr(result, 'thinking', None),
            thinking_extracted=getattr(result, 'thinking_extracted', None),
            tool_calls=tool_calls,
        )
    
    # ... rest of existing completion logic ...
```

#### 2.4 Emit message for Child Completion Reports

**File**: `daemon/manager.py` - in `_process_child_completion_and_notify_parent()`

When creating the completion report message:

```python
async def _process_child_completion_and_notify_parent(
    self,
    instance_id: str,
    completed_message_id: str,
):
    # ... existing code ...
    
    # Create completion report content
    report_content = f"[Child {instance_id[:8]} completed: {summary}]"
    
    # NEW: Emit via MessageService for SSE
    if self._message_service:
        # Get the queued message ID from parent's queue
        queued_message_id = str(uuid.uuid4())  # or get from enqueue
        await self._message_service.on_child_completion_report(
            parent_instance_id=parent.instance_id,
            child_instance_id=instance_id,
            report_content=report_content,
            message_id=queued_message_id,
        )
```

### Phase 3: SSE Formatting (Week 3) - Update Frontend Contract

#### 3.1 Update format_sse_event

**File**: `daemon/api.py`

```python
def format_sse_event(event) -> dict:
    """Format event for SSE response."""
    # ... existing handling ...
    
    event_type = event.get("event_type", "unknown")
    
    # NEW: message_completed gets full message payload
    if event_type == "message_completed":
        data = event.get("data", {})
        return {
            "id": str(event.get("id", 0)),
            "event": "message_completed",
            "data": json.dumps({
                "instance_id": event.get("instance_id", ""),
                "original_message_id": data.get("original_message_id"),
                "message": data.get("message"),  # Full UnifiedMessage payload
            }),
        }
    
    # ... rest ...
```

#### 3.2 Frontend: Handle message_completed

**File**: `frontend/src/app/services/sse.service.ts`

```typescript
// Add message_completed event handler
eventSource.addEventListener('message_completed', (e: MessageEvent) => {
  this.ngZone.run(() => {
    try {
      const data = JSON.parse(e.data);
      if (!this.isValidInstanceEvent(data)) return;
      
      // Emit delta for ChatComponent
      if (data.message && data.original_message_id) {
        this.emitDelta({
          type: 'message_completed',
          instance_id: data.instance_id,
          message_id: data.original_message_id,
          message: data.message,  // Full UnifiedMessage payload
        });
      }
    } catch (err) {
      console.error('[SSE] Failed to parse message_completed:', err);
    }
  });
});
```

#### 3.3 Frontend: Handle message_completed Delta

**File**: `frontend/src/app/pages/chat/chat.component.ts`

```typescript
case 'message_completed':
  // Finalize message with canonical content from message_completed
  if (msgIndex >= 0) {
    // Replace accumulated state with canonical message
    updated[msgIndex] = {
      ...updated[msgIndex],
      role: delta.message?.role || 'assistant',
      content: delta.message?.content || updated[msgIndex].content,
      thinking: delta.message?.thinking,
      thinking_extracted: delta.message?.thinking_extracted,
      tool_calls: delta.message?.tool_calls || updated[msgIndex].tool_calls,
    };
  }
  break;
```

### Phase 4: Adoption (Week 4) - Replace Scattered Emission

#### 4.1 Replace message_received Emissions

Find all places that emit `message_received`:
- `daemon/manager.py` - user messages via API
- `daemon/sources/dispatcher.py` - messages from sources

Replace with:
```python
await self._message_service.on_user_message_stored(
    instance_id=instance_id,
    message_id=message_id,
    content=content,
    source=source,
)
```

#### 4.2 Replace child completion report emission

Find and update `_process_child_completion_and_notify_parent()`

#### 4.3 Update GET /messages to Use UnifiedMessage

**File**: `daemon/api.py` - in messages endpoint

```python
@router.get("/instances/{instance_id}/messages")
async def list_messages(instance_id: str):
    # ... existing code to get messages from LangGraph ...
    
    # Format using UnifiedMessage
    return [
        UnifiedMessage(**msg_dict).to_api_response()
        for msg_dict in messages
    ]
```

### Phase 5: Cleanup (Week 5) - Deprecation & Documentation

#### 5.1 Deprecation Warnings

Add deprecation warnings when emitting old-style events

#### 5.2 Update Documentation

- API docs for message_completed event
- Migration guide for frontend

## File Changes Summary

| File | Action | Purpose |
|------|--------|---------|
| `daemon/models/messages.py` | NEW | UnifiedMessage model |
| `daemon/repositories/event/models.py` | MODIFY | Add MESSAGE_COMPLETED |
| `daemon/services/message_service.py` | NEW | MessageService class |
| `daemon/manager.py` | MODIFY | Wire MessageService |
| `daemon/services/task_processor.py` | MODIFY | Emit message_completed |
| `daemon/api.py` | MODIFY | SSE formatting + GET /messages |
| `tests/unit/test_message_service.py` | NEW | Unit tests |
| `frontend/src/app/services/sse.service.ts` | MODIFY | Handle message_completed |
| `frontend/src/app/pages/chat/chat.component.ts` | MODIFY | Process message_completed delta |

## Testing Strategy

### Unit Tests
- `tests/unit/test_message_service.py` - MessageService isolation tests
- Verify event emission with correct payloads

### Integration Tests
- `tests/integration/test_sse_message_completed.py`
- E2E: message_completed appears in SSE stream
- E2E: SSE message matches GET /messages format

### Manual Testing
1. Send message via API → check message_received event
2. Process message → check content_chunk + message_completed events
3. Spawn child → check child_completed + message for parent

## Migration Safety

### Non-Breaking Changes
1. `message_completed` emitted **alongside** `processing_completed`
2. Old frontend code still works (ignores new fields)
3. New frontend code uses `message_completed` for canonical content

### Rollback Plan
If issues arise:
1. Disable MessageService emission in TaskProcessor
2. Revert to old event emission code
3. Fix and re-enable

## Open Questions

| Question | Status | Notes |
|----------|--------|-------|
| Result shape from `_process_message_with_tracking()` | Verify | Need to check actual field names |
| EventBus.create_event() signature | Verify | Check if kind accepts string or EventKind enum |
| Frontend change detection | Pending | May need `markForCheck()` after delta processing |

## Appendix: Event Flow After Implementation

```
USER MESSAGE (API)
┌─────────────────────────────────────────────┐
│ POST /instances/{id}/messages                │
│ ↓                                           │
│ manager.enqueue_message()                   │
│ ↓                                           │
│ MessageService.on_user_message_stored()      │
│ ↓                                           │
│ EventBus: message_received event            │
│ ↓                                           │
│ SSE → Frontend: message_received            │
└─────────────────────────────────────────────┘

ASSISTANT MESSAGE (Processing)
┌─────────────────────────────────────────────┐
│ TaskProcessor.process()                     │
│ ↓                                           │
│ manager._process_message_with_tracking()    │
│ ↓                                           │
│ LangGraph: store assistant message         │
│ ↓                                           │
│ Streaming: content_chunk, tool_call, etc.   │
│ ↓                                           │
│ MessageService.on_assistant_message_completed()
│ ↓                                           │
│ EventBus: message_completed + processing_completed
│ ↓                                           │
│ SSE → Frontend: message_completed          │
└─────────────────────────────────────────────┘

CHILD → PARENT MESSAGE
┌─────────────────────────────────────────────┐
│ manager._process_child_completion()         │
│ ↓                                           │
│ Create completion report                     │
│ ↓                                           │
│ MessageService.on_child_completion_report() │
│ ↓                                           │
│ EventBus: message_received                  │
│ ↓                                           │
│ SSE → Frontend: message_received           │
└─────────────────────────────────────────────┘
```

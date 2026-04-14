# SSE Rewrite: `message_id` → `id` Rename Checklist

> **⚠️ CRITICAL**: This rename MUST be done in the **same PR/commit** for both backend and frontend.
> Breaking changes will occur if only one side is updated.

## Overview

This checklist covers renaming `message_id` to `id` across the entire codebase. The `id` field aligns with LangGraph's `msg.id` which is the source of truth after the SSE rewrite.

---

## Phase 0: Pre-Rename Search Commands

Run these commands BEFORE making any changes to establish a baseline:

```bash
# 1. Count all message_id occurrences in Python files
grep -rn "message_id" daemon/ --include="*.py" | wc -l

# 2. Count all message_id occurrences in TypeScript files
grep -rn "message_id" frontend/src --include="*.ts" | wc -l

# 3. Count all message_id occurrences in test files
grep -rn "message_id" tests/ --include="*.py" | wc -l

# 4. List all files containing message_id
grep -rl "message_id" daemon/ frontend/src tests/ > /tmp/message_id_files.txt
cat /tmp/message_id_files.txt
```

---

## Phase 1: Backend Core Files

### 1.1 `daemon/persistence.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 23-37 | `def compute_message_id(...)` | **DELETE** | Function removed - use `msg.id` directly |
| 193 | `msg_id = compute_message_id(instance_id, role, content)` | Use `msg.id` or `_stable_message_id()` | New helper function needed |
| 202 | `"message_id": msg_id` | `"id": msg_id` | JSON key in response dict |

**Action**: 
1. Delete `compute_message_id()` function (lines 23-37)
2. Add `_stable_message_id()` helper for fallback when `msg.id` is None
3. Change `"message_id"` to `"id"` in the response dict (line 202)

### 1.2 `daemon/manager.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 21 | `compute_message_id,` | **DELETE** | Import removed |
| 151 | `message_id: str` | `id: str` | Parameter name in ActivityCallbackHandler |
| 156 | `message_id: The message ID...` | `id: The message ID...` | Docstring |
| 160 | `self.message_id = message_id` | `self.id = id` | Attribute assignment |
| 169 | `self.queue_repository.update_activity(self.message_id)` | `self.queue_repository.update_activity(self.id)` | Method call |
| 171 | `logger.warning(f"Failed to update activity for {self.message_id}"` | `logger.warning(f"Failed to update activity for {self.id}"` | Log message |
| 243 | `message_id: str` | `id: str` | AsyncMessageResult dataclass field |
| 907, 911, 915 | `message_id = str(uuid.uuid4())` | `id = str(uuid.uuid4())` | Local variable names |
| 919 | `message_id = compute_message_id(instance_id, "user", message)` | `id = str(uuid.uuid4())` | Use random UUID instead |
| 924, 939, 961, 970 | `message_id=message_id,` | `id=id,` | Keyword arguments |
| 983 | `logger.debug(f"Enqueued message {message_id}...` | `logger.debug(f"Enqueued message {id}...` | Log message |
| 986 | `message_id=message_id,` | `id=id,` | AsyncMessageResult constructor |
| 995 | `message_id: str,` | `id: str,` | Parameter name |
| 1009 | `message_id: The queue message ID.` | `id: The queue message ID.` | Docstring |
| 1026 | `message_id,` | `id,` | Variable reference |
| 1057 | `current_assistant_msg_id = compute_message_id(instance_id, "assistant", "")` | `current_assistant_msg_id = str(uuid.uuid4())` | Use random UUID |
| 1188, 1212, 1253 | `message_id=current_assistant_msg_id,` | `id=current_assistant_msg_id,` | Keyword arguments |
| 1272-1274 | `current_assistant_msg_id = compute_message_id(...)` | `current_assistant_msg_id = str(uuid.uuid4())` | Use random UUID |
| 1318, 1339, 1386, 1399 | `message_id=current_assistant_msg_id,` | `id=current_assistant_msg_id,` | Keyword arguments |
| 1372 | `logger.error(f"Streaming failed for message {message_id}..."` | `logger.error(f"Streaming failed for message {id}..."` | Log message |
| 1376 | `"message_id": message_id` | `"id": id` | JSON key in error dict |
| 1598 | `completed_message_id: str` | `completed_id: str` | Parameter name |
| 1605-1611 | `message_id` in docstring | `id` in docstring | Documentation |
| 1646 | `# Use message_id in source...` | `# Use id in source...` | Comment |
| 1650 | `f"report:{instance_id}:{completed_message_id}"` | `f"report:{instance_id}:{completed_id}"` | String interpolation |
| 1661 | `completed_message_id[:8]` | `completed_id[:8]` | Variable reference |
| 1672, 1680, 1687, 1690 | `completed_message_id` | `completed_id` | Variable names and docstrings |
| 1703-1727 | `report_message_id` | `report_id` | Variable names throughout |
| 1812, 1825, 1840, 1849 | `report_message_id` | `report_id` | Variable names and JSON keys |
| 1861 | `completed_message_id: str` | `completed_id: str` | Parameter name |
| 1875 | `completed_message_id` | `completed_id` | Docstring |
| 1898, 1905, 1906, 1920 | `completed_message_id` | `completed_id` | Variable references |
| 1936 | `message_id=report_message_id,` | `id=report_id,` | Keyword argument |
| 1955, 1969 | `message_id: str \| None = None` | `id: str \| None = None` | Parameter name |
| 1973 | `if message_id:` | `if id:` | Conditional |
| 2024, 2029, 2055 | `message_id` in dict keys | `id` in dict keys | JSON serialization |
| 2328-2338 | `message_id: str` | `id: str` | CancelRequest parameters |
| 2342, 2344, 2731-2736 | `message_id` | `id` | Variable names |

**Action**: Global replace `message_id` → `id` in this file, then verify no `compute_message_id` remains.

### 1.3 `daemon/services/message_service.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 14 | `from daemon.persistence import compute_message_id` | **DELETE** | Import removed |
| 32 | `message_id: str,` | `id: str,` | Parameter name in on_user_message_stored |
| 39 | `message_id=message_id,` | `id=id,` | UnifiedMessage constructor |
| 47-49 | `message_id=message_id,` | `id=id,` | create_message_received_event call |
| 70 | `assistant_message_id = compute_message_id(...)` | `assistant_message_id = str(uuid.uuid4())` | Use random UUID |
| 73 | `message_id=assistant_message_id,` | `id=assistant_message_id,` | UnifiedMessage constructor |
| 88, 98, 132, 159 | `message_id` | `id` | Variable names and parameters |
| 101 | `"assistant_message_id": assistant_message_id` | Keep as-is | This is a result field, not the primary id |

**Action**: Update parameter names and JSON keys from `message_id` to `id`.

### 1.4 `daemon/message_models.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 31 | `message_id: str \| None = None` | `id: str \| None = None` | SSEEventPayload field |
| 50 | `message_id: str \| None = None` | `id: str \| None = None` | SSEEventStatus field |
| 57 | `message_id: str = Field(...)` | `id: str = Field(...)` | UnifiedMessage field |
| 75 | `"message_id": self.message_id` | `"id": self.id` | to_dict() method |

**Action**: Rename field `message_id` to `id` in these models and update `to_dict()`.

### 1.5 `daemon/api.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 762 | `message_id=result.message_id,` | `id=result.id,` | MessageResponse construction |
| 772-773 | `messages/{message_id}` | `messages/{id}` | Route path parameter |
| 774 | `message_id: str` | `id: str` | Path parameter name |
| 792 | `"message_id": message_id,` | `"id": id,` | Response dict key |
| 971, 977 | `message_id = event.message_id` | `id = event.id` | Variable assignment |
| 988-990 | `if message_id:` / `envelope["message_id"] = message_id` | `if id:` / `envelope["id"] = id` | Conditional and assignment |
| 1021 | `envelope["message_id"] = message_id` | `envelope["id"] = id` | Assignment |

**Action**: Rename path parameter and response keys.

---

## Phase 2: Backend Repository/Service Files

### 2.1 `daemon/services/event_bus.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 259 | `"message_id": message_id` | `"id": id` | Event dict key |
| 424 | `event["message_id"] = message_id` | `event["id"] = id` | Event assignment |

### 2.2 `daemon/services/task_processor.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 136 | `"message_id": task.message_id` | `"id": task.id` | Result dict |
| 189 | `"message_id": task.message_id` | `"id": task.id` | Result dict |
| 209 | `"message_id": task.message_id` | `"id": task.id` | Result dict |
| 223, 234 | `"message_id": task.message_id` | `"id": task.id` | Result dict |

### 2.3 `daemon/repositories/event/models.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 40 | `message_id: Optional[str]` | `id: Optional[str]` | Field name (keep column name for migration) |
| 56 | `"message_id": self.message_id` | `"id": self.id` | to_dict() method |

### 2.4 `daemon/repositories/message_queue/models.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 45 | `message_id: str = Field(...)` | `id: str = Field(...)` | Primary key field |
| 76 | `"message_id": self.message_id` | `"id": self.id` | to_dict() method |

### 2.5 `daemon/repositories/task/models.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 47 | `message_id: Optional[str]` | `id: Optional[str]` | Field name |
| 90 | `"message_id": self.message_id` | `"id": self.id` | to_dict() method |

### 2.6 `daemon/repositories/message_queue/repository.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 499 | `"message_id": message_id,` | `"id": id,` | Response dict |
| 506 | `"message_id": message_id,` | `"id": id,` | Response dict |

### 2.7 `daemon/repositories/task/repository.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 472, 675 | `"message_id": parent.get("message_id")` | `"id": parent.get("id")` | Parent reference |
| 733 | `"message_id": message_id,` | `"id": id,` | Response dict |

### 2.8 `daemon/services/stale_task_recovery.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 375 | `"message_id": task.message_id` | `"id": task.id` | Result dict |

### 2.9 `daemon/sources/registry.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 596-598 | `msg.metadata.get("message_id")` | `msg.metadata.get("id")` | Metadata lookup |

### 2.10 `daemon/sources/mapper.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 297 | `msg.metadata.get("message_id")` | `msg.metadata.get("id")` | Metadata lookup |

### 2.11 `daemon/sources/adapters/telegram.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 518 | `"message_id": message.get("message_id")` | `"id": message.get("message_id")` | Map Telegram's message_id to our id |

### 2.12 `daemon/models.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 145 | `"message_id": "msg-456"` | `"id": "msg-456"` | JSON example in docstring |

---

## Phase 3: Frontend Files

### 3.1 `frontend/src/app/models/index.ts`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 25 | `message_id: string;` | `id: string;` | Message interface |
| 50 | `message_id: string;` | `id: string;` | MessageResponse interface |
| 112 | `message_id: string \| null;` | `id: string \| null;` | SSEEvent interface |
| 119 | `message_id?: string;` | `id?: string;` | SSEEventEnvelope interface |
| 136 | `message_id?: string;` | `id?: string;` | SSEStatus interface |
| 155 | `message_id: string;` | `id: string;` | CanonicalMessage interface |
| 169 | `message_id: string;` | `id: string;` | MessageDelta interface |

**Action**: Update all `message_id` references in interfaces to `id`.

### 3.2 `frontend/src/app/services/sse.service.ts`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 20 | `message_id: string;` | `id: string;` | latestError signal type |
| 99, 105, 113 | `envelope.message_id` | `envelope.id` | message_received handler |
| 130, 136, 145 | `data.message_id` | `data.id` | processing_started handler |
| 139 | `data.message_id` | `data.id` | statusUpdates map key |
| 145 | `message_id: data.message_id` | `id: data.id` | emitDelta call |
| 166, 175 | `envelope.message_id` | `envelope.id` | content_chunk handler |
| 198, 206 | `envelope.message_id` | `envelope.id` | thinking handler |
| 235, 243 | `envelope.message_id` | `envelope.id` | tool_call handler |
| 272, 280 | `envelope.message_id` | `envelope.id` | tool_complete handler |
| 310, 314-320 | `data.message_id` | `data.id` | error handler |
| 325 | `message_id: data.message_id` | `id: data.id` | emitDelta call |
| 344-345 | `data.message_id` | `data.id` | cancelled handler |
| 369 | `message_id: data.message_id` | `id: data.id` | title_updated handler |
| 395, 404, 406 | `envelope.message_id` | `envelope.id` | message_completed handler |
| 430, 436, 444 | `envelope.status?.message_id` | `envelope.status?.id` | Error event handler |
| 479, 485, 489-490 | `data.message_id` | `data.id` | handleCompletedEvent |

### 3.3 `frontend/src/app/pages/chat/chat.component.ts`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 99 | `m.message_id === delta.message_id` | `m.id === delta.id` | findIndex lookup |
| 107, 118, 123 | `message_id: delta.message_id` | `id: delta.id` | Placeholder creation |
| 132, 140, 155 | `delta.message_id` | `delta.id` | Log messages |
| 145, 165, 178, 201, 213, 235, 247 | `message_id: delta.message_id` | `id: delta.id` | Message creation |
| 287, 292 | `delta.message_id` | `delta.id` | Log messages |
| 304-305 | `delta.message_id`, `msg.message?.message_id` | `delta.id`, `msg.message?.id` | Log messages |
| 313, 332, 334 | `msg.message.message_id` | `msg.message.id` | Message creation |
| 512 | `m.message_id` | `m.id` | Map key |
| 516, 526 | `httpMsg.message_id`, `existingMsg.message_id` | `httpMsg.id`, `existingMsg.id` | Map lookup |
| 658, 677 | `message_id: userMessage.message_id` | `id: userMessage.id` | User message creation/error handling |

### 3.4 `frontend/src/app/components/chat-interface/chat-interface.component.ts`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 123 | `message.message_id` | `message.id` | trackByMessageId function |

---

## Phase 4: Test Files

### 4.1 `tests/integration/test_sse_streaming.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 57, 65 | `message_id="msg-123"` / `events[0].message_id` | `id="msg-123"` / `events[0].id` | Assertions |
| 78, 86 | `message_id="msg-789"` / `events[0].message_id` | `id="msg-789"` / `events[0].id` | Assertions |
| 95, 109 | `message_id=` | `id=` | Event attributes |
| 124, 130, 135 | `message_id="msg-1"` | `id="msg-1"` | Event attributes |
| 220, 261 | `message_id=` | `id=` | Event attributes |

### 4.2 `tests/unit/test_message_service.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 272 | `resp["message_id"]` | `resp["id"]` | Assertion |

### 4.3 `tests/test_persistence.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 270-271 | `messages[0]["message_id"]` | `messages[0]["id"]` | Assertions |

### 4.4 `tests/test_models.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 136, 163, 169 | `"message_id":` in test data | `"id":` | Test fixtures and assertions |

### 4.5 `tests/test_api.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 328 | `"message_id" in data` | `"id" in data` | Assertion |

### 4.6 `tests/mock_message_service.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 84, 97, 146 | `"message_id" in ...` | `"id" in ...` | Assertions |
| 243, 353, 431 | `call_args.kwargs["message_id"]` | `call_args.kwargs["id"]` | Assertions |
| 268, 550, 573, 576, 584, 595, 599 | `"message_id":` in test data | `"id":` | Test fixtures |

### 4.7 `tests/message_queue_redesign/conftest.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 44 | `"message_id": "test-message-456"` | `"id": "test-message-456"` | Test fixture |

### 4.8 `tests/message_queue_redesign/test_task_repository.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 20, 86, 90 | `task.message_id` | `task.id` | Assertions |

### 4.9 `tests/message_queue_redesign/test_task_retry_repository.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 62 | `"message_id":` | `"id":` | Test data |

### 4.10 `tests/message_queue_redesign/test_stale_task_recovery.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 35, 318 | `"message_id":` | `"id":` | Test data |

### 4.11 `tests/message_queue_redesign/test_stale_recovery_v2.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 174, 359, 496, 863 | `"message_id":` | `"id":` | Test data |

### 4.12 `tests/message_queue_redesign/test_timeout_retry_e2e.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 228 | `"message_id":` | `"id":` | Test data |

### 4.13 `tests/integration/test_message_queue_e2e.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 368 | `event.get('data', {}).get('message_id')` | `event.get('data', {}).get('id')` | Log message |

### 4.14 `tests/integration/test_completion_report.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 120 | `event.get('data', {}).get('message_id', ...)` | `event.get('data', {}).get('id', ...)` | Log message |

### 4.15 `tests/conftest.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 148 | `"message_id": "msg-456"` | `"id": "msg-456"` | Test fixture |

### 4.16 `tests/test_sources_registry.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 302, 334, 347, 373, 415, 456 | `metadata={"message_id": ...}` | `metadata={"id": ...}` | Test fixtures |

### 4.17 `tests/test_sources_mapper.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 413, 449, 483, 507 | `"message_id":` | `"id":` | Test data |

### 4.18 `tests/mock_test_runner.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 141 | `result.get('message_id')` | `result.get('id')` | Log message |

### 4.19 `tests/test_telegram_adapter.py`

| Line | Current | Change To | Notes |
|------|---------|-----------|-------|
| 200, 274, 293, 316, 343, 364, 381, 401 | `"message_id": 1` or `100` | **KEEP AS-IS** | Telegram API uses `message_id` natively |
| 516, 524-526, 565, 583 | `telegram.message_id` | **KEEP AS-IS** | Telegram API field |

---

## Phase 5: Verification Commands

Run these after making all changes to verify no references remain:

```bash
# 1. Verify no compute_message_id remains in daemon/
grep -rn "compute_message_id" daemon/ --include="*.py"
# Expected: No output

# 2. Verify no message_id string keys remain in persistence.py
grep -n '"message_id"' daemon/persistence.py
# Expected: No output

# 3. Verify no message_id string keys remain in message_models.py
grep -n '"message_id"' daemon/message_models.py
# Expected: No output

# 4. Verify no message_id in Event model to_dict
grep -n '"message_id"' daemon/repositories/event/models.py
# Expected: No output

# 5. Verify no message_id in MessageQueue model to_dict
grep -n '"message_id"' daemon/repositories/message_queue/models.py
# Expected: No output

# 6. Verify no message_id in Task model to_dict
grep -n '"message_id"' daemon/repositories/task/models.py
# Expected: No output

# 7. Verify no message_id in UnifiedMessage to_dict
grep -n '"message_id"' daemon/message_models.py
# Expected: No output

# 8. Verify frontend models have no message_id
grep -n "message_id" frontend/src/app/models/index.ts
# Expected: No output

# 9. Verify frontend services have no message_id
grep -n "message_id" frontend/src/app/services/sse.service.ts
# Expected: No output (except in comments if any)

# 10. Verify frontend chat component has no message_id
grep -n "\.message_id" frontend/src/app/pages/chat/chat.component.ts
# Expected: No output

# 11. Verify chat-interface component has no message_id
grep -n "\.message_id" frontend/src/app/components/chat-interface/chat-interface.component.ts
# Expected: No output

# 12. Full count - should be 0 or very low (only telegram message_id references)
grep -rn "message_id" daemon/ frontend/src tests/ --include="*.py" --include="*.ts" | grep -v "telegram" | grep -v "Telegram" | grep -v "\.telegram" | wc -l
# Expected: 0 or minimal (telegram API uses message_id natively)
```

---

## Phase 6: Run Tests

After verification, run the test suite:

```bash
# Backend tests
cd /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
pytest tests/unit/ -v -x --tb=short

# Integration tests (if mock LLM available)
pytest tests/integration/test_sse_streaming.py -v --tb=short

# Frontend tests
cd frontend && npm test -- --no-watch --browsers=ChromeHeadless
```

---

## Phase 7: Files That Should NOT Be Changed

The following are **Telegram API fields** and should **NOT** be renamed:

- `tests/test_telegram_adapter.py` - Uses Telegram's native `message_id` field
- `daemon/sources/adapters/telegram.py` - Telegram API response parsing

These map Telegram's `message_id` to our `id` field internally.

---

## Summary of Changes

### Backend Python Files (17 files)
1. `daemon/persistence.py` - Remove `compute_message_id`, change dict key
2. `daemon/manager.py` - Global rename, remove import
3. `daemon/services/message_service.py` - Rename params, remove import
4. `daemon/message_models.py` - Rename model fields
5. `daemon/api.py` - Rename path param and response keys
6. `daemon/services/event_bus.py` - Rename dict keys
7. `daemon/services/task_processor.py` - Rename dict keys
8. `daemon/repositories/event/models.py` - Rename field and dict key
9. `daemon/repositories/message_queue/models.py` - Rename field and dict key
10. `daemon/repositories/task/models.py` - Rename field and dict key
11. `daemon/repositories/message_queue/repository.py` - Rename dict keys
12. `daemon/repositories/task/repository.py` - Rename dict keys
13. `daemon/services/stale_task_recovery.py` - Rename dict keys
14. `daemon/sources/registry.py` - Rename metadata lookup
15. `daemon/sources/mapper.py` - Rename metadata lookup
16. `daemon/sources/adapters/telegram.py` - Map telegram.message_id → our id
17. `daemon/models.py` - Update docstring examples

### Frontend TypeScript Files (4 files)
1. `frontend/src/app/models/index.ts` - Update interface fields
2. `frontend/src/app/services/sse.service.ts` - Rename event properties
3. `frontend/src/app/pages/chat/chat.component.ts` - Rename property access
4. `frontend/src/app/components/chat-interface/chat-interface.component.ts` - Rename trackBy

### Test Files (16 files)
All test files referencing `message_id` in assertions or fixtures.

---

## Migration Note: Database Column Names

The database column names (`message_id`) in SQL tables may remain unchanged to avoid requiring a migration. Only the Python model fields and JSON serialization keys are renamed from `message_id` to `id`.

If database column names also need to change, add a migration step.

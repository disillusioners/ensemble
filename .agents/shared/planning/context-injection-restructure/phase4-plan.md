# Phase 4: GET /messages API Integration

## Objective
Surface context messages in the GET /messages API response as synthetic, identifiable messages. Ensure NO DB writes happen during read. Since context messages never enter checkpoint (per ADR-2), they must be re-assembled on-demand for the API response.

## Coupling
- **Depends on**: Phase 3 (loose — needs `assemble_context_messages()` to be callable)
- **Coupling type**: loose
- **Shared files with other phases**: `persistence.py` (shared with Phase 2)
- **Shared APIs/interfaces**: `get_instance_messages()` return shape — adds context messages
- **Why this coupling**: API layer reads from the same builder; can run parallel to Phase 5
- **Can parallel with**: Phase 5 (different files: persistence.py vs instance_lifecycle.py)

## Context
- Phase 3 completed: Context messages are assembled inside `agent_node` (local only)
- Context messages are NOT in checkpoint — they need to be re-built for API display
- Current GET /messages reconstructs system prompt via `_reconstruct_full_system_prompt()` which calls `_apply_post_cache_appends()`
- Known bug: `append_auto_load_skills` writes to DB during GET /messages poll

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update `get_instance_messages()` | After reading checkpoint messages, call `assemble_context_messages()` to build context messages ON-DEMAND. Insert them after the synthetic system message but before the most recent user message. | `daemon/persistence.py:254-416` |
| 2 | Mark context messages as synthetic | Set `is_synthetic=True` on all context messages in the serialized response. Ensures `child_reports.py` filters them (lines 523, 1007). | `daemon/persistence.py:394-406` |
| 3 | Add `context_kind` to serialized output | Extend `serialize_message()` to include `context_kind` from `additional_kwargs` when present. Frontend can use this to style context messages. | `daemon/utils.py:70-176` |
| 4 | Eliminate DB write during poll | When mode is `human_messages`, `_reconstruct_full_system_prompt()` returns PERSONA-only prompt (Phase 2 handles this). Context comes from `assemble_context_messages()` instead. Confirm no writes. | `daemon/persistence.py:419-516` |
| 5 | Update API response shape documentation | The response is `list[dict]`. Context messages appear as additional items with `is_synthetic=True`, `context_kind` set. Document in endpoint docstring. | `daemon/routers/instances.py:988-1009` |
| 6 | API contract test | Assert GET /messages returns: `[synthetic_system] + [synthetic_context_msgs...] + [real_user_ai_msgs...]`. Verify no DB writes. Verify `is_synthetic` and `context_kind` fields. | `tests/integration/test_api_messages.py` (new) |
| 7 | Performance test | Context build latency under 50ms for the API read path. If exceeds, add caching layer. | `tests/performance/test_context_api_latency.py` (new) |

## Key Files
- `daemon/persistence.py` — MODIFIED: `get_instance_messages()` inserts context messages on-demand
- `daemon/utils.py` — MODIFIED: `serialize_message()` adds `context_kind` field
- `daemon/routers/instances.py` — READ-ONLY: verify response shape
- `tests/integration/test_api_messages.py` — NEW
- `tests/performance/test_context_api_latency.py` — NEW

## Constraints
- NO database writes during GET /messages (read endpoint)
- Context messages rebuilt ON-DEMAND (they're not in checkpoint)
- Existing API consumers must not break (context messages are additive)
- `child_reports.py` filter (`is_synthetic`) must continue to exclude context messages

## Context Message Placement

For multi-turn conversations, context messages should appear **before the most recent user message only** (the current turn). Historical turns don't need context re-displayed since it was ephemeral.

```python
# In get_instance_messages():
# ... read checkpoint messages ...
result = serialize checkpoint messages
# Build context for current state (on-demand)
context_msgs = await assemble_context_messages(...)
# Serialize with is_synthetic=True
context_dicts = [serialize_with_synthetic(msg) for msg in context_msgs]
# Insert after synthetic system message, before last user message
_insert_context_before_last_user(result, context_dicts)
```

## Deliverables
- [ ] GET /messages returns synthetic context messages (rebuilt on-demand)
- [ ] `context_kind` field in serialized output
- [ ] No DB writes during GET /messages read
- [ ] `child_reports.py` filter still works
- [ ] API contract test passes
- [ ] Context build latency < 50ms

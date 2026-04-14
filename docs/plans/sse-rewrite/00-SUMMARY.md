# SSE System Rewrite — Master Summary

> **Note**: This project is **not in production**. No migration, no backward compatibility,
> no feature flags. All changes are applied directly. Old code is deleted, not deprecated.

---

## Goal

Rewrite SSE system so that messages delivered via SSE are **identical** to messages from the REST API, using LangGraph's native message IDs.

---

## Key Principles

1. **LangGraph's `msg.id` is the source of truth** — no more `compute_message_id()`
2. **SSE delivers checkpoint snapshots** after each node completes
3. **Frontend replaces entire message list** on each checkpoint event
4. **Correctness over real-time feedback** (project focuses on long-running tasks)
5. **Sequence numbers for ordering** — checkpoint events include monotonically incrementing sequence number to handle out-of-order delivery

---

## Architecture Summary

### Only 4 SSE Event Types

| Event | Payload | When |
|-------|---------|------|
| `connected` | `{instance_id}` | Client connects |
| `checkpoint` | `{instance_id, messages[], checkpoint_id}` | After each LangGraph node completes |
| `error` | `{error, details}` | Unrecoverable failure |
| `keepalive` | `{}` | Every 30s timeout |

### Unified Message Format

> **Note**: JSON API and frontend use `message_id` (semantically clear). This maps to LangGraph's internal `msg.id`.

```json
{
  "message_id": "msg-uuid-from-langgraph",
  "role": "assistant",
  "content": "Hello!",
  "thinking": null,
  "thinking_extracted": null,
  "tool_calls": null,
  "created_at": "2026-04-13T15:30:34.050055+00:00"
}
```

---

## Files Changed

### Backend — New Files
| File | Purpose |
|------|---------|
| `daemon/utils.py` | `serialize_message()`, `_stable_message_id()`, `parse_think_tags()` |

### Backend — Modified Files
| File | Action |
|------|--------|
| `daemon/services/event_bus.py` | Major rewrite — add `broadcast_checkpoint_event()`, remove 14 old event types |
| `daemon/manager.py` | Remove streaming, add checkpoints, delete MessageService |
| `daemon/api.py` | Rewrite SSE endpoint |
| `daemon/persistence.py` | Use LangGraph IDs, import from utils |
| `daemon/task_processor.py` | Remove EventBus lifecycle calls, MessageService |
| `daemon/message_models.py` | Delete SSE-specific models |

### Backend — Deleted Files
| File | Reason |
|------|--------|
| `daemon/services/message_service.py` | Pure SSE emission wrappers — all event types removed |

### Frontend — Modified Files
| File | Action |
|------|--------|
| `frontend/src/app/models/index.ts` | Delete SSE-specific types (keep `message_id`) |
| `frontend/src/app/services/sse.service.ts` | Full rewrite |
| `frontend/src/app/pages/chat/chat.component.ts` | Remove delta effects, simplify |

---

## Implementation Phases

| Phase | Name | Description |
|-------|------|-------------|
| [Phase -1](#) | extract_thinking() Utility | Extract thinking extraction to `daemon/utils.py` with full test coverage |
| [Phase 0](./phase-0-preparation.md) | Preparation | Extract `parse_think_tags` to `daemon/utils.py` |
| [Phase 0.5](./verification.md#step-35-langgraph-stream-format-verification) | **VERIFICATION** | Verify LangGraph stream format (MANDATORY before Phase 1) |
| [Phase 1](./phase-1-backend-core.md) | Backend Core | Add serialization helpers, rewrite persistence |
| [Phase 2](./phase-2-eventbus.md) | EventBus Rewrite | Add `broadcast_checkpoint_event()`, remove old methods |
| [Phase 3a](./phase-3-manager-migration.md) | Manager Migration — Core | Remove streaming from `_process_message_with_tracking`, add checkpoint emission with final-state safety net |
| [Phase 3b](./phase-3-manager-migration.md) | Manager Migration — Cleanup | Remove MessageService from task_processor, child completion, error report |
| [Phase 4](./phase-4-cleanup.md) | Cleanup | Delete MessageService, rewrite API endpoint |
| [Phase 5](./phase-5-frontend-models.md) | Frontend Models | Delete SSE-specific types (keep `message_id`) |
| [Phase 6](./phase-6-frontend-sse.md) | Frontend SSE Service | Full rewrite of SSE service |
| [Phase 7](./phase-7-frontend-chat.md) | Frontend Chat Component | Remove delta effects, simplify |
| [Phase 8](./phase-8-tests.md) | Tests & Polish | Write tests alongside each phase; final verification pass |

---

## Critical Notes

> **Note**: JSON API and frontend keep `message_id` (semantically clear). Only the internal LangGraph/persistence layer uses `msg.id`. No rename across the stack.

### Rollback Strategy

If Phase 3 breaks in production:

1. **Revert the feature branch** — since project is pre-production, rollback = `git revert` or branch reset
2. **If LangGraph stream format is wrong (Phase 0.5 failure)**: Reassess the approach entirely — the checkpoint-based architecture depends on correct format
3. **If only manager.py changes broke**: Revert to Phase 2 state (EventBus already updated), restore streaming code in manager
4. **If EventBus changes broke**: Revert to Phase 2 state, restore old event methods
5. **Feature flag path**: If feature flag `sse_v2: true` is added to config.yaml, toggle back to old behavior

> **No hot-fix path**: The architecture change is fundamental. Streaming events cannot be restored
> without restoring `broadcast_streaming_event()` and content buffering code.

### PR Boundaries

| PR | Phases | Must Ship Together |
|----|--------|-------------------|
| PR 0 | **extract_thinking() prerequisite** | Isolated PR — extract thinking extraction logic to `daemon/utils.py` with comprehensive tests covering all 5 provider paths |
| PR 1 | Phase 0 | Isolated PR (prerequisite) |
| PR 2 | Phase 0.5 | Isolated verification (no code changes) |
| PR 3 | **Phases 1 + 2 + 3a + 3b + 4** | **ALL backend changes** — must ship together |
| PR 4 | Phases 5 + 6 + 7 | All frontend changes |
| PR 5 | Phase 8 | Tests and final verification |

> **⚠️ Why Phases 1+2+3+4 must ship together**: Phase 2 removes `broadcast_streaming_event()` from
> EventBus, Phase 3 removes the call sites from manager. If merged separately, code breaks.

### Testing Philosophy

Write tests alongside each phase, not just at the end. Each phase should have passing tests before proceeding to the next.

### Accepted Regressions

| Regression | Rationale |
|------------|-----------|
| No real-time token streaming | Project focuses on long-running tasks |
| `created_at` is `None` during streaming | Timestamps only populated on REST API reload |
| `Last-Event-ID` reconnection dropped | Simplifies SSE endpoint |
| Large message list per checkpoint | Acceptable for current scale |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Circular import: `utils.py` ↔ `event_bus.py` | Use lazy imports inside functions |
| `msg.id` might be `None` | `_stable_message_id()` deterministic hash fallback |
| Multi-node update loses messages | Remove `break` — accumulate ALL nodes |
| `isStreaming` never reset | Set `false` on SSE `onerror`/`onclose` |
| `all_state_messages` grows unbounded | Reset `[]` at start of each `_process_message_with_tracking()` call |
| Missing sequence numbers | Add `checkpoint_sequence` to Phase 1 |
| Thinking extraction scattered | Extract to `extract_thinking()` in PR 0 |
| PR boundary incomplete | Include test files and task_processor.py call sites |
| Streaming integration tests become obsolete | Rewrite in Phase 8 |
| LangGraph version mismatch | Lock version in `pyproject.toml`. Future upgrades need separate plan. |

# Plan Overview: User Message Injection on Running Instance

## Objective
Enable users to inject messages into a running (or WAITING_CHILDREN) instance's LangGraph conversation in real-time, by storing the message in a RAM-only slot on InstanceManager that is consumed at the next agent-node LLM call — appended as a HumanMessage before `current_llm.invoke()`. This allows users to fine-tune, redirect, or remind the agent during active execution without pausing.

## Scope Assessment
**LARGE** — Multi-module feature spanning backend core (manager, graph, lifecycle, compaction), backend API (router, SSE), frontend (3 components + service), and E2E testing. ~18+ files modified across 4 logical phases. Estimated 1-2 days of focused development.

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Branch**: `feature/user-msg-injection` (already created from `latest`)
- **Database**: PostgreSQL (primary dev/test DB)
- **E2E tests**: `tests/e2e/test_e2e_workflows.py` with `@pytest.mark.integration` marker
- **Test guide**: `testing-guide.md` at project root (renamed from `ensure.md`)
- **Mermaid diagram**: Already created and approved by user
- **Architecture decisions**: See `decisions.md` for C1-C6 detailed rationale

## Architecture Summary

### Key Components Touched

| Component | File | Role |
|-----------|------|------|
| InstanceManager | `daemon/manager.py` | Add `_pending_injections` dict + helper methods + cleanup in all 5 paths + TTL sweeper |
| Agent Node | `daemon/graph.py` (lines 258-391) | Inject HumanMessage before `current_llm.invoke()`, return both messages for checkpoint persistence (C1, C2) |
| Build Instance Graph | `daemon/graph.py` (~400-561) | Thread injection_slot + live_hub via factory closure (C1) |
| Compaction | `daemon/graph.py` (641-684), `daemon/compaction.py`, `daemon/services/instance_messaging.py` (523) | Preserve injected messages in both reactive + proactive compaction paths (C3) |
| Instance Lifecycle | `daemon/services/instance_lifecycle.py` | Clear injection on pause + terminate + clear_all (W1) |
| Messages Router | `daemon/routers/messages.py` | State-aware routing: inject for RUNNING/WAITING_CHILDREN, unchanged for PAUSED (C4) |
| LiveEventHub | `daemon/services/live_event_hub.py` | Reuse `stream_message()` with event_type param for injection events (W5) |
| SseService | `frontend/src/app/services/sse.service.ts` | Handle injection_* SSE events |
| Chat Interface | `frontend/src/app/components/chat-interface/` | Show pending injected message |
| Message Input | `frontend/src/app/components/message-input/` | Allow send while RUNNING via `canInject` computed (C6) |

### Injection Flow

```
User sends message via POST /api/instances/{id}/messages
    │
    ├─ Instance RUNNING or WAITING_CHILDREN
    │   ├─ Validate content not empty (S4)
    │   ├─ InstanceManager.set_injection(instance_id, content)
    │   ├─ LiveEventHub.stream_message(instance_id, event_type="injection_pending", ...)
    │   └─ Return 202 Accepted
    │
    ├─ Instance PAUSED
    │   └─ Existing auto-resume behavior (resume_instance_cascade + resume_processing_job), return 200 (C4 — NO CHANGE)
    │
    └─ Instance IDLE or terminal
        └─ Normal enqueue_message path, return 200 (NO CHANGE)
```

```
Agent node execution (next LLM call):
    ├─ Check injection_slot(instance_id) via factory closure (C1)
    ├─ If message exists:
    │   ├─ Create HumanMessage(content=injection["content"], additional_kwargs={"injected_message": True})  (C2)
    │   ├─ Append to full_messages before current_llm.invoke()
    │   ├─ Clear slot via InstanceManager.clear_injection(instance_id)
    │   ├─ Emit SSE: stream_message(instance_id, event_type="injection_consumed", ...) via live_hub (C1, W5)
    │   └─ Return {'messages': [injected_msg, response]}  ← BOTH messages for checkpoint persistence (C2)
    └─ Return {'messages': [response]}  ← normal path
```

```
Pause triggered:
    ├─ InstanceManager.clear_injection(instance_id)
    └─ If slot had content: emit SSE stream_message(event_type="injection_cleared", ...)
```

```
Compaction (C3):
    ├─ Reactive (ContextLengthExceededError): re-append injected_msg to compacted messages before re-invoke
    └─ Proactive (compaction.py): skip messages with additional_kwargs["injected_message"] from summarization
```

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Backend Core | RAM slot via factory closure + agent node consumption with checkpoint persistence + compaction fixes + pause/terminate cleanup | None | — (root) | 4-5h |
| 2 | Backend API + SSE | State-aware send_message (PAUSED unchanged) + SSE via stream_message + query endpoint | Phase 1 | tight | 2-3h |
| 3 | Frontend | SSE handling + pending message UI + canInject computed for input | Phase 2 | loose | 3-4h |
| 4 | Testing | 6 E2E tests (incl. WAITING_CHILDREN) + testing-guide.md update + regression run | Phase 1-3 | tight | 2-3h |

### Coupling Assessment

| Phase Pair | Coupling | Reasoning |
|------------|----------|-----------|
| 1 → 2 | **tight** | Phase 2's send_message API directly calls Phase 1's `set_injection()` method and uses `live_hub` from the factory closure. Same backend codebase, shared InstanceManager + graph API. |
| 2 → 3 | **loose** | Frontend depends only on the SSE event contract (event types + payload shape via `stream_message`) and the query endpoint — both defined in Phase 2. No shared code files. Can pipeline. |
| 3 → 4 | **tight** | E2E test exercises the full stack. All phases must be complete before E2E validation. |

**Parallelism opportunities**: Phase 3 (frontend) can begin as soon as Phase 2's SSE event contract is defined (even before Phase 2 backend is fully complete), since frontend development can mock the SSE events.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Factory closure wiring complexity (C1)** | high | Follow the exact `compactor`/`graph_ref` pattern. Thread `injection_slot` (callable or lightweight object) + `live_hub` through `build_instance_graph()` → `create_agent_node()`. Test that the closure captures correctly. |
| **Checkpoint persistence of injected message (C2)** | high | Return `{'messages': [injected_msg, response]}` so `add_messages` reducer persists both. Use `additional_kwargs={"injected_message": True}` marker. Verify crash recovery preserves the message. |
| **Compaction losing injected messages (C3)** | high | Two fixes: (1) reactive path re-appends injected_msg after compaction, (2) proactive path in compaction.py skips messages with `injected_message` flag from summarization. Follow `language_check_reminder` pattern. |
| **Thread safety of `_pending_injections` dict** | med | Dict operations on single keys are atomic in CPython. If set/get/clear all run on the main event loop (via MainLoopBridge), no lock needed. Verify no cross-thread access from WorkerPool. |
| **Breaking PAUSED auto-resume (C4)** | high | DO NOT change PAUSED branch in send_message. Only add new branch for RUNNING/WAITING_CHILDREN. Existing PAUSED auto-resume (resume_instance_cascade + resume_processing_job) remains untouched. |
| **InstanceManager cleanup on all 5 paths (W1)** | med | Add cleanup to: `_release_cached_instance`, `pause_instance_cascade`, `terminate_instance`, `clear_all_instances`, project cascade delete. Consider centralized `_cleanup_instance_state()` helper. |
| **Orphaned injection slots (S1)** | low | TTL sweeper cleans slots >1h old. Add to existing periodic cleanup alongside `_cleanup_cached_instances`. |
| **Modifying isInstanceRunning breaks QUEUED Pause (C6)** | high | Do NOT modify `isInstanceRunning()`. Add separate `canInject` computed for RUNNING/WAITING_CHILDREN only. Pause button visibility unchanged. |
| **SSE event not received by frontend** | med | Reuse existing `stream_message()` with event_type param (W5). Add fallback: query endpoint to poll pending injection status. |
| **E2E test timing sensitivity** | med | Use prompts that generate long responses + tool calls (S9) to keep instance RUNNING. Poll with generous timeouts. |

## Success Criteria
- [ ] User can send a message to a RUNNING instance via API, and the message appears in the LLM conversation before the next LLM call
- [ ] User can send a message to a WAITING_CHILDREN instance, and it's consumed when parent resumes
- [ ] Injected HumanMessage persists to checkpoint (C2) — verified via GET /messages history and crash recovery
- [ ] Sending a 2nd message before the 1st is consumed replaces the 1st (RAM-only, single slot)
- [ ] Pause clears any pending injection slot
- [ ] PAUSED instances still auto-resume via existing send_message behavior (C4 — NO CHANGE to PAUSED)
- [ ] IDLE/terminal instances use normal enqueue_message path
- [ ] Both compaction paths preserve injected messages (C3)
- [ ] Factory closure threads injection_slot + live_hub correctly (C1)
- [ ] SSE events emitted via stream_message: `injection_pending`, `injection_consumed`, `injection_cleared` (W5)
- [ ] Frontend shows pending injected message with distinct visual style
- [ ] Frontend allows message input + send while RUNNING via `canInject` computed, alongside pause button (C6)
- [ ] `isInstanceRunning()` unchanged — QUEUED still shows Pause button (C6)
- [ ] Frontend clears pending message on `injection_consumed` or `injection_cleared` SSE event
- [ ] All 5 cleanup paths clear `_pending_injections` (W1)
- [ ] TTL sweeper cleans orphaned injection slots >1h old (S1)
- [ ] E2E test passes: injection into running instance (with long-response prompt, S9)
- [ ] E2E test passes: injection into WAITING_CHILDREN instance (W3)
- [ ] All existing tests pass (5 existing E2E + all unit tests, no regressions) (W7)
- [ ] New E2E tests documented in testing-guide.md (C5)

## Tracking
- Created: 2026-07-12
- Last Updated: 2026-07-12 (revised — C1-C6, W1/W3/W5/W6/W7, S1/S4/S9)
- Status: draft (revised)

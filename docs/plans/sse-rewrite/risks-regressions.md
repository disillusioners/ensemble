# Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Circular import: `utils.py` → `event_bus.py` → `utils.py` | **Step 0**: Move `parse_think_tags` + `_THINK_PATTERN` to `daemon/utils.py` before any other changes. Use lazy imports inside functions. |
| LangGraph `msg.id` might be `None` for some message types | `_stable_message_id()` fallback using deterministic hash of (role, content[:200], tool_call_id) — prevents duplicates on re-emission. |
| Thinking extraction has 5 provider-specific paths | Port all 5 paths to `serialize_message()`. |
| `tool_outputs` map needs ToolMessages that are excluded from output | Build map before filtering, pass to `serialize_message()`. |
| No rename needed: JSON API keeps `message_id` | `serialize_message()` maps LangGraph's `msg.id` → `message_id`. No frontend/backend changes needed. |
| No real-time feedback during LLM inference | Acceptable for long-running task focus. |
| Large message list on each checkpoint | Acceptable for now. Add diff mode later if needed. |
| `created_at` is `None` during SSE streaming | Accept regression. REST API populates after reload. |
| Multi-node update coalescing — `break` loses messages from subsequent nodes | **Remove `break`**. Accumulate messages from ALL nodes before emitting. |
| `isStreaming` signal never reset after stream ends | Set `isStreaming.set(false)` on SSE `onerror`/`onclose`. |
| `all_state_messages` grows unbounded across turns | **Reset `all_state_messages = []`** at the start of each `_process_message_with_tracking()` call. |
| `ResponseDispatcher` loses event stream (external sources silent) | Keep lightweight `completed` event via `_broadcast_to_global()` for dispatcher. |
| `broadcast_sync()` wrong for async contexts | Use `_broadcast_to_global()` directly from async code. |
| `_broadcast_to_global()` positional arg confusion | **Always use keyword args**: `data={...}`. Document as constraint. |
| `send_message()` inconsistency with new system | Document as SSE-invisible. `ainvoke()` bypasses SSE. |
| `create_child_failed_event()` call in `_send_error_report()` not in original plan | Add to removal list at `manager.py:2046`. |
| Empty checkpoint wipes frontend messages | Skip emission in `broadcast_checkpoint_event()` when `serialized` is empty. |
| `broadcast_checkpoint_event()` sends to dispatcher queue unnecessarily | Dispatcher filters them out (event_type="checkpoint" != "completed"). Acceptable overhead, or remove `_broadcast_to_global()` call if optimization needed. |
| `MessageService` "DB migration" phantom | **CORRECTED**: `MessageService` methods are SSE-only wrappers. No DB writes to migrate. Just delete call sites and file. |
| Child completion SSE gap: parent sees child report only after checkpoint | Accept as regression OR emit immediate `message_received` event on child completion for instant parent notification |
| `send_message()` SSE bypass: direct `graph.ainvoke()` with no streaming | Document as known limitation — affects agent-to-agent communication (`tools/instance.py:267`), not just API calls. SSE stream never updates when agent calls `send_message()` on watched instance. |
| Queue ordering under concurrency: out-of-order checkpoints cause UI flicker | **Mitigation**: Add sequence numbers to checkpoint events; frontend sorts by sequence on receipt |
| `broadcast_streaming_event` test files break | Update in same PR (test file rewrites included in Phase 8) |
| `task_processor.py` call sites break | Include in same PR as Phase 3b |
| Streaming integration tests become obsolete | Rewrite `tests/test_events.py`, `tests/integration/test_sse_streaming.py`, `tests/integration/test_streaming_errors.py`, `tests/integration/test_streaming_performance.py` in Phase 8 |
| LangGraph version mismatch | Lock LangGraph version in `pyproject.toml`. Future version upgrades require separate verification plan. |

---

# Accepted Regressions

The following behavior changes are intentional and accepted:

| Regression | Rationale |
|------------|-----------|
| No real-time token streaming during LLM inference | Project focuses on long-running tasks; correctness over real-time feedback |
| `created_at` is `None` during SSE streaming | Timestamps only populated when loading from REST API after completion |
| `Last-Event-ID` reconnection support dropped | Simplifies SSE endpoint; can be re-added with checkpoint sequence numbers |
| `send_message()` bypasses SSE entirely | Used for programmatic/API calls, not user-facing streaming |
| `send_message()` SSE bypass: agent-to-agent communication | Frontend should poll REST API when using `send_message()` directly on watched instance (`tools/instance.py:267`). SSE stream will not update. |
| No tool progress indication during streaming | User won't see which tool is active until it completes. Acceptable for long-running task focus. |
| Large message list sent on each checkpoint | Acceptable for current scale; diff mode can be added later |
| Some `EventKind` enum values become dead code | Doesn't break anything; can clean up later |
| Child completion SSE gap: parent sees child's report only after parent's next checkpoint | Parent's SSE stream doesn't instantly reflect child completion — delay until parent processes report via checkpoint |
| `enqueue_message()` DB writes become audit-only | SSE no longer reads from event table. Verify no external systems depend on `Event(kind=MESSAGE_RECEIVED)` for real-time features. |
| `_create_completion_events()` DB writes become audit-only | SSE endpoint no longer reads these events. Document as audit-only. |
| Feature flag complexity | **Rejected**: Project is not in production. Rollback = `git revert`. Phase 0.5 serves as the abort gate. |

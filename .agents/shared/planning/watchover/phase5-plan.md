# Phase 5: Edge Cases, Persistence & Hardening

## Objective

Harden the watchover feature against the edge cases and failure modes identified
across the requirements and architecture analysis: crash recovery (DB flag
restore), compaction-during-watchover context refresh, concurrent-instance
isolation, loop_breaker interaction (exclude denial evidence from loop detection), parallel
tool-call evaluation semantics, and a comprehensive test suite. This is the
convergence phase that makes the feature production-ready.

## Files to Create

| # | Path | Purpose |
|---|------|---------|
| C5.1 | `test/test_watchover_edge_cases.py` | Edge-case tests: crash recovery, compaction refresh, concurrent isolation, loop_breaker exclusion (deny evidence not detected as loop), parallel tool calls, deferred-marker sweep. |
| C5.2 | `test/test_watchover_integration.py` | End-to-end integration test: activate watchover → DevOps makes allowed tool call → DevOps makes denied tool call → 3rd denial → instance terminates. Full lifecycle. |

## Files to Modify

| # | Path | What Changes |
|---|------|--------------|
| M5.1 | `daemon/services/stale_task_recovery.py` | Add a sweep step for stale `_deferred_watchover_terminate` markers — mirrors the existing 5-step cancel→grace→force-cancel→retry sweep. If a deferred-terminate marker exists but the instance is still alive (marker never fired), trigger the termination cascade. |
| M5.2 | `daemon/graph.py` (`LoopDetector.scan`) | **Mark denial ToolMessages** with `additional_kwargs.watchover_denial=true` (done in Phase 2 T2.5), then teach `LoopDetector.scan` to EXCLUDE call/result pairs whose result has that marker from loop detection. The denial counter is **NOT reset** on loop repair — it persists (it lives in SessionState; repair only touches `messages`). Per AD-8 + architecture-recommendation.md Area 6. **Reuse callout:** `_loop_breaker_state` cleanup path is NOT where this lives; this is a LoopDetector.filter change. |
| M5.3 | `daemon/graph.py` (`create_watchover_check_node`) | Add `watchover_context` freshness check: compare a turn counter or timestamp to detect stale context; if stale, re-derive a lightweight context snapshot (or trigger re-compaction). Optional: configurable refresh interval (Open Question #2). |
| M5.4 | `daemon/services/watchover_service.py` (from Phase 3) | Add crash-recovery hook: on instance load (manager startup / `load_session_into_memory`), check `instance_metadata["watchover_enabled"]` and restore the in-memory flags. The graph router reads in-memory flags (via `is_watchover_enabled`), so the DB flag must be synced to memory on restart. |
| M5.5 | `daemon/graph.py` (SSE event emission) | Emit `watchover_denial` and `watchover_terminate` SSE events from the watchover node (on Deny and on 3-strike respectively). **Coordinate with Phase 4 (T4.4).** |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| T5.1 | **Crash recovery — DB flag restore.** In `watchover_service.py` (or manager load path), add a `_restore_watchover_state(instance_id)` that reads `instance_metadata["watchover_enabled"]` on instance load and ensures the in-memory state (read by `is_watchover_enabled()`) matches. Call this from the instance load path (`load_session_into_memory` or equivalent). **Reuses the OpenCode session manager load-on-demand pattern (critical note: opencode session manager resource guard fix).** | Phase 3 (T3.3 flag storage) | Unit test: set watchover in DB → restart manager → load instance → `is_watchover_enabled()` returns True → tool call is intercepted. |
| T5.2 | **Loop_breaker interaction (AD-8).** Teach `LoopDetector.scan` to EXCLUDE call/result pairs whose ToolMessage result has `additional_kwargs.watchover_denial=true` from loop detection. The denial counter is **NOT reset** by loop repair — it persists across repairs (lives in SessionState; repair only touches `messages`). This prevents a false loop-detection from erasing denial evidence or nudging the agent around the 3-strike policy. **Per architecture-recommendation.md Area 6 + AD-8.** | Phase 2 (T2.5 denial ToolMessage marking) | Unit test: (a) mark a denial ToolMessage with `additional_kwargs.watchover_denial=true` → LoopDetector.scan excludes it; (b) trigger loop repair mid-turn with `deny_count=2` → counter stays at 2 (NOT reset); (c) third denial after repair still terminates. |
| T5.3 | **Compaction-during-watchover context refresh.** In `create_watchover_check_node`, add a freshness check on `watchover_context`: store a turn counter or timestamp alongside the context in `instance_metadata`. If the context is older than a configurable threshold (default: refresh every turn), re-derive a lightweight context snapshot. **Open Question #2 — default to per-turn refresh; make configurable.** | Phase 3 (T3.4 context construction) | Unit test: activate watchover → run N turns → context is refreshed per the threshold; stale context does not cause incorrect verdicts. |
| T5.4 | **Concurrent-instance isolation.** Verify that watchover state is fully isolated per instance: `_deferred_watchover_terminate` is keyed by `instance_id`, `watchover_denial_count` is in per-instance LangGraph state, `instance_metadata` flags are per-instance. Add a test that runs two instances (one watched, one not) concurrently and verifies no cross-contamination. | Phase 1 (T1.5), Phase 2 (T2.5) | Integration test: two concurrent instances — watched instance's tool calls are intercepted; non-watched instance is unaffected; denial counts are separate. |
| T5.5 | **Parallel tool-call evaluation (deny-whole-batch, LD-1).** When the LLM emits multiple `tool_calls` in one AIMessage, the `watchover_check` node evaluates ALL calls in the batch. If ANY call is denied, deny the ENTIRE batch: inject one denial ToolMessage per denied call + a "deferred — batch contained denied call" ToolMessage for allowed-but-not-executed calls; route back to `agent`. If ALL calls are allowed, route to `tools` normally. No `watchover_finalize_denials` node, no message replacement, no post-tools router extension. Document the deny-whole-batch semantics in `agents/watcher/rule.md`. | Phase 2 (T2.4, T2.5) | Unit test: AIMessage with 3 tool calls, one denied → all 3 get ToolMessages (denied + deferred), route to agent, none execute; all 3 allowed → route to tools, all execute. |
| T5.6 | **SSE event emission.** Emit `watchover_denial` and `watchover_terminate` SSE events from the watchover node. On Deny: emit `{event: "watchover_denial", data: {instance_id, tool_call, reason, denial_count}}`. On 3-strike: emit `{event: "watchover_terminate", data: {instance_id, reason}}`. Use the existing SSE/notification infrastructure (`live_event_hub.py` / `notification_broadcaster.py`). **Coordinate with Phase 4 (T4.4).** | Phase 2 (T2.5, T2.6) | Integration test: activate watchover → trigger denial → `watchover_denial` SSE event received; trigger 3rd denial → `watchover_terminate` SSE event received. |
| T5.7 | **Stale deferred-marker sweep.** In `stale_task_recovery.py`, add a step that checks for `_deferred_watchover_terminate` markers on instances that are still alive (the marker was set but the cascade never ran — e.g. crash between marker-set and post-graph completion). If found, trigger `terminate_instance` cascade. **Reuses the existing 5-step stale-task sweep pattern.** | Phase 1 (T1.5 deferred marker) | Unit test: set deferred marker → simulate crash (skip post-graph completion) → run stale-task recovery → marker is detected → termination cascade runs. |
| T5.8 | Write `test/test_watchover_edge_cases.py` + `test/test_watchover_integration.py` covering all T5.1-T5.7 scenarios plus a full end-to-end lifecycle test. | T5.1-T5.7 | All tests pass; coverage includes crash recovery, loop_breaker interaction, context refresh, concurrent isolation, parallel tool calls, SSE events, stale-marker sweep. |

## Coupling

- **Tight with: Phase 1** — touches the `watchover_check` node (M5.2, M5.3) and the deferred marker (T5.7).
- **Tight with: Phase 2** — touches the denial counter (T5.2), the decision logic (T5.5), and the termination path (T5.6).
- **Loose with: Phase 3** — hardens the flag restore (T5.1) and context refresh (T5.3).
- **Loose with: Phase 4** — emits the SSE events that Phase 4 consumes (T5.6).

## Reuse Callouts

| Pattern | Source | Reused For |
|---------|--------|------------|
| Stale-task recovery 5-step sweep | `stale_task_recovery.py` | Stale deferred-terminate marker sweep (T5.7) |
| `LoopDetector.scan` filtering | `graph.py` (critical note: Loop Breaker 2026-07-17) | Exclude `watchover_denial=true` ToolMessages from loop detection (T5.2) |
| OpenCode session load-on-demand | `load_session_into_memory` (critical note: opencode session manager) | Crash-recovery flag restore (T5.1) |
| SSE fan-out | `live_event_hub.py`, `notification_broadcaster.py` | Watchover denial/terminate events (T5.6) |
| `ContextCompactor.compact_state()` | `compaction.py:380-781` | Context refresh during watchover (T5.3) |

## Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| P5-R1 | Crash recovery misses a code path — the in-memory flag is not restored on some instance-load paths, leaving watchover silently inactive after a restart. | High | T5.1: audit ALL instance-load paths (manager startup, `load_session_into_memory`, job recovery). Add a test for each. |
| P5-R2 | Context refresh on every turn is too expensive (compaction cost × every turn). | Medium | T5.3: make the refresh interval configurable. Default to per-turn but allow operators to set a higher interval (e.g. every 5 turns). Use a lightweight summary instead of full compaction for refreshes. |
| P5-R3 | The stale-marker sweep (T5.7) terminates an instance that the operator wanted to keep alive (false positive). | Medium | T5.7: only sweep markers that are older than a grace period (e.g. 60s) to avoid racing with the normal post-graph completion path. Log all sweep-triggered terminations. |
| P5-R4 | Test suite for watchover is flaky due to timing dependencies (deferred marker, SSE events, async LLM calls). | Low | T5.8: use deterministic mocks for LLM calls; use `asyncio` test fixtures; avoid real network calls in unit tests. |

## Exit Criterion

- Crash recovery: watchover state restored from DB after restart (T5.1 test passes).
- Loop_breaker: denial evidence excluded from loop detection; counter NOT reset by repair (T5.2 test passes).
- Context freshness: stale context is detected and refreshed (T5.3 test passes).
- Concurrent isolation: no cross-contamination between instances (T5.4 test passes).
- Parallel tool calls: documented semantics + test (T5.5).
- SSE events: denial and termination events emitted (T5.6 test passes).
- Stale-marker sweep: orphaned terminate markers are resolved (T5.7 test passes).
- Full integration test passes end-to-end (T5.8).
- Feature is production-ready for DevOps-first usage.

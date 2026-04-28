# Plan Tracking: Explorer/Experiencer RAG Upgrade

## Iteration 001 — 2026-04-26

**Verdict: APPROVED**

### Verification Results

**Council Session 1 — Claim Verification (5 claims):**
| Claim | Status | Details |
|-------|--------|---------|
| Explorer agent references exist | ✅ VERIFIED | All references to experience(), rag_insert_text, upsert found in 5 markdown files |
| explore() tool behavior described accurately | ✅ VERIFIED | invoke_agent_and_wait with 300s timeout, returns as-is, no flag parsing |
| JobQueueService access pattern | ✅ VERIFIED | `_job_queue_service` on InstanceManager, getattr pattern used in codebase |
| System queue names | ✅ VERIFIED | `system_parallel_queue` and `system_fifo_queue` confirmed |
| asyncio.ensure_future appropriateness | ⚠️ PARTIAL | Functional, but codebase prefers `create_task` + `add_done_callback` |

**Council Session 2 — Internal Consistency (5 checks):**
| Check | Status | Details |
|-------|--------|---------|
| Phase contradictions | ✅ PASS | Phases are complementary, no contradictions |
| rag_insert_text vs meta.json | ✅ PASS | Defense-in-depth, not contradictory |
| asyncio.ensure_future + async/await | ✅ PASS | Works correctly with internal awaits |
| asyncio.to_thread for sync I/O | ✅ PASS | Correct pattern for blocking DB operations |
| "Unchanged" vs new header | ⚠️ NOTE | Wording ambiguity only — header IS the feature, not a real issue |

### Notes (non-blocking)
- Phase 2 uses `asyncio.ensure_future()` but the codebase has a `MainLoopBridge.run_async_no_wait()` pattern with `add_done_callback` for exception logging. Consider using `asyncio.create_task()` with done callback instead.
- Phase 2 line 223 says "caller sees no difference" — should say "response content unchanged" since the new header is the feature itself.

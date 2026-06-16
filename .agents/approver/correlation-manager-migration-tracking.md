# CorrelationManager Migration — Approval Tracking

## Iteration 001 (2026-06-16)
**Verdict: REJECTED**

### Verification Performed
- 11 code location claims verified against codebase via council session — ALL CONFIRMED (line numbers accurate to the byte)
- 4 architectural concerns evaluated independently via council session

### Blocking Issues Found

#### Issue 1: Phase 4 breaks rebuild_from_db() — No post-removal rebuild strategy
- **Severity**: BLOCKING
- **Details**: Phase 1's `rebuild_from_db()` relies solely on `instances WHERE waiting_for > 0`. Phase 4 Part A stops writing to `waiting_for`, making the column read 0 for all new correlations. Part B drops the column entirely. The plan gives NO alternative rebuild strategy for either case.
- **Impact**: After Phase 4 Part A + restart, CM state is lost for any correlation created post-Part-A → parents stuck in PROCESSING forever.
- **Fix path**: Document an alternative rebuild query (e.g., find PROCESSING instances with children + cross-reference message_queue), OR keep `waiting_for` as a rebuild-only cache (write but never read for control flow), OR add a persistent correlation_state table.

#### Issue 2: Site 1B self-referential correlation key bug
- **Severity**: BLOCKING
- **Details**: Phase 3 proposes `cm.resolve_response(parent_id=instance_id, child_id=instance_id, message_id=...)` for the root instance fallback. The correlation key `f"{instance_id}:{message_id}"` would NEVER match any registered key, because `register_message_send` registers keys as `f"{child_id}:{message_id}"` under the SENDER's parent_id. The root never registers a self-correlation.
- **Impact**: Root instance completion logic silently fails — `resolve_response` returns False, status transition to COMPLETED is skipped, lifecycle event not published.
- **Root cause**: Plan conflates "does the instance have more work in its own queue?" (Site 1B's actual purpose — verified at child_reports.py:685-715) with "has the instance received all expected responses from children?" (CM's concern).
- **Fix path**: Site 1B should use `cm.is_complete(instance_id)` (pure read) for the "are children done?" check, and keep its existing message_queue pending-count logic separate. Do NOT call `resolve_response` for the root's own message lifecycle.

### Non-Blocking Observations
- Concern 3 (WorkerPool thread → asyncio.Lock marshaling): NON-BLOCKING — MainLoopBridge exists and is widely used; constraint N3 is documented
- Concern 4 (Callback within Lock blocks same-parent operations): NON-BLOCKING at stated volume (~1 msg/sec, 50 children/parent); optimization opportunity to move `_get_last_assistant_message_raw` outside Lock scope

### Strengths Noted
- All 11 cited code locations verified accurate
- 3 race conditions correctly identified
- Shadow mode → progressive cutover strategy is sound
- Per-parent asyncio.Lock design is correct for the concurrency model
- Direct callback vs EventBus decision well-justified (C2/C3 issues confirmed real)

---

## Iteration 002 (2026-06-16)
**Verdict: APPROVED**

### Fix Verification

#### A1 Fix: `waiting_for` kept as rebuild-only cache — RESOLVED
- Phase 4 Part A revised: stops READING `waiting_for` for decisions, KEEPS writing (increment/decrement)
- Phase 4 Part B (column drop): explicitly CANCELLED
- ADR-011 documents the decision with correct rationale (message_queue is direction-blind — no sender_id)
- `rebuild_from_db()` unchanged: still queries `waiting_for > 0` + message_queue for real UUIDs
- Key Design Decisions #1, #3, #5 consistently document the rebuild-cache strategy
- Planner chose the simplest viable fix from the three options I offered

#### A2 Fix: Root completion uses two independent conditions — RESOLVED
- Phase 3 Site 1B: explicitly states "Root completion is NOT a child response — do not call resolve_response"
- Uses `cm.is_complete(instance_id)` (read-only) for condition 1 + existing `SELECT COUNT(*)` for condition 2
- ADR-012 documents the semantic distinction (self-pending-work vs child-response correlation)
- Code example (phase3 lines 204-239) correctly shows two-condition check without resolve_response
- Constraints section (line 298-304) explicitly documents: Sites 1A/2 use resolve_response, Site 1B does NOT

### Minor Stale Text (Non-Blocking)
- Phase 4 verification strategy lines 202, 207-211 still reference "no longer written" and "Removal Phase migration tests" from the pre-revision version — cosmetic inconsistencies in the verification section only, not in decisions or implementation tasks

### Conclusion
Both blocking issues from iteration 001 are fully resolved with correct, well-documented fixes. The plan is architecturally sound and ready for implementation.

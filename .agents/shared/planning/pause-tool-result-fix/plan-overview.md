# Plan Overview: Incomplete-Pause / Tool-Result Race Fix

Date: 2026-07-31 (revised 2026-08-01)
Author: planner[v2] via plan-creation worker
Status: Ready for Review (revised)

## Objective

Close the deferred-pause race window so a paused instance stays paused when tool results (child completions, user messages, sibling messages) land while the pause cascade is in flight. Implement source-side Task-creation guards at the two sites that mint `Task` rows for potentially-paused instances.

**The two fix sites use DIFFERENT guard logic — the asymmetry is the central design decision of this plan:**

- **Phase 1 (`child_reports._process_child_completion_db_sync`)**: full **dual check** (marker OR DB==PAUSED). Skips the `PROCESS_REPORT` Task. The `ReportInjection` row is the durable fallback, drained via `claim_for_injection` on every LLM call (`daemon/graph.py:2566-2590`). No report is lost.

- **Phase 2 (`_prepare_enqueued_message`)**: **marker-only check** (DB==PAUSED does NOT skip). Skips the `PROCESS_MESSAGE` Task ONLY when the in-memory marker is set. When the marker is empty + DB==PAUSED, the Task IS created and the existing `claim_pending_task` SQL pause gate (`task/repository.py:646-671`) defers the claim until resume. This preserves the message-delivery contract for the post-cascade case (Path 1, 5). The in-window skip case is a **known limitation** with a documented follow-up (materialize the marker to DB).

**Why the asymmetry**: `ReportInjection` has a verified drain path on every LLM call (`graph.py:2566-2590`), so Phase 1 can rely on it as a fallback. `MessageQueue` rows in READY status are **not drained** by the resume cleanup (`manager.py:4937-4940` filters to `[PENDING, PROCESSING, RETRYING]` only) and are **not consulted** by the resume flow (root resume bypasses `enqueue_message` entirely; child resume creates a fresh Task with a fresh `message_id` UUID). Therefore Phase 2 must rely on the existing SQL gate for the post-cascade case and accept a known limitation for the in-window case.

## Scope

### In Scope

- **Phase 1**: Add marker + DB-status dual check at `daemon/services/child_reports.py:_process_child_completion_db_sync:1158`, before `report_task = Task(...)` at line 1893 (Path 2). The `MessageQueue` row (lines 1879-1889) and the `ReportInjection` row (lines 1927-1934) remain UNCHANGED. **Both** `asyncio.to_thread` and a new DB column are NOT needed — use the existing `session.get(Instance, instance.parent_id)` on the already-open `WriteGuardSession` (`child_reports.py:1193`). Add a code comment documenting the residual `ReportInjection` orphan risk for terminated parents (Medium likelihood) and reference the follow-up `reconcile_terminal_report_injections` cleanup.

- **Phase 2**: Add **marker-only** check at `daemon/services/instance_messaging.py:_prepare_enqueued_message:1070`, inside the existing `with WriteGuardSession(...) as session:` block (line 1185), before the `task = Task(...)` insert at line 1211 (Paths 1 and 5). The check has three branches:
  - **Marker set** (in-window race case): skip the Task. MessageQueue row is in READY status with no Task. This is the race-window case; message delivery is a known limitation (see "Open Questions" / Q5).
  - **Marker empty + DB==PAUSED** (post-cascade case): create the Task as today. The existing `claim_pending_task` SQL pause gate (`task/repository.py:646-671`) defers the claim until resume. Message is delivered.
  - **Marker empty + DB==RUNNING** (normal case): create the Task as today.
  The `MessageQueue` row (lines 1187-1199) is created unconditionally (durable input). Use the existing `session.get(Instance, instance_id)` on the same session (already used at line 1253 for the status-flip branch). NOT `await asyncio.to_thread(...)` (the function is a synchronous `def` at line 1070 — `asyncio.to_thread` would be a `SyntaxError`).

- Add structured `INFO` log lines on each skip event so operators can verify the guard is firing.

- Add code-comment documentation blocks above each new check explaining: the race window, the C2 torn-state bug context, the dual check (Phase 1) / marker-only check (Phase 2) reasoning, the durable fallback, the residual risks, and the cross-reference to the other phase.

- **Regression tests** (resume-flow integration tests, NOT just branch tests):
  - Phase 1: race-window skip test + resume-drain test (parent resumes, first LLM call drains the ReportInjection row) + **no-false-positive test exercising the actual resume flow** (pause via `question_pause_node`, wait for cascade, resume, assert `_resume_processing_background` drives unblocked).
  - Phase 2: race-window skip test + post-cascade pause-gate test (Task created, not claimed while DB==PAUSED) + **no-false-positive test exercising the actual resume flow** (root and child paths) + **controlled-delay race-window test** that monkeypatches the cascade with a delay to hold the race window open and asserts the guard fires.
- Run all tests against both SQLite and PostgreSQL (project critical note: PostgreSQL is the DEFAULT DB).

### Out of Scope

- **Open question #1** (mandatory report-injection drain on resume) — **deferred**. The live `ReportInjectionSlot` drain before each LLM call (`daemon/graph.py:2566-2590`) is sufficient; the first LLM call after resume drains the row. No explicit resume-init drain is needed.
- **Open question #3** (report-injection rows for never-resumed parents) — **deferred as a follow-up**. The fix site adds a code comment documenting the residual risk; the actual `reconcile_terminal_report_injections` cleanup (mirroring `reconcile_terminal_watches`) is a separate PR.
- **Open question #4** (sibling in a different tree) — **confirmed no fix needed**. The cascade pauses the entire tree via `get_tree_ids(root_id)` (`daemon/services/instance_lifecycle.py:3030-3112`), so siblings in the same tree share the cascade. Siblings in a different tree are unaffected by the parent's pause.
- **Materialize `_deferred_question_pause` to DB** (`instances.pause_pending BOOL`) — **deferred as a follow-up**. This is the multi-node concern AND the watertight fix for the Phase 2 in-window message-loss limitation. Will require `_ensure_postgres_columns()` for dual-driver compatibility. Tracking as a follow-up item; not in scope for this PR.
- **The `pause-resume-redesign` plan** (first-class job/task PAUSED state) — **orthogonal**. This fix is a surgical patch against the current architecture; it remains valid/needed even if `pause-resume-redesign` lands later, because the marker-based race guard is complementary to job/task PAUSED state. When the redesign lands, the in-memory marker check will be replaced with a DB-backed `instances.pause_pending` column check; the dual-check pattern remains valid.
- **Options A, B, C from the technical analysis** (pipeline pre-check, gate pre-check, `_process_message_with_tracking` backstop) — **rejected as primary fix, conditional acceptance**: they all read DB during the race window and see `RUNNING`, so they no-op precisely when needed. They DO catch the post-cascade case (DB==PAUSED), so they are not useless — but they are insufficient as a complete fix and redundant once Option D closes the source. The post-cascade case is already covered by the existing `claim_pending_task` SQL gate. Option B is additionally rejected for violating the gate's documented pure-Lock design.
- **Option E from the technical analysis** (synchronous DB flip in `question_pause_node`) — **explicitly rejected and re-rejected here**. It re-introduces the C2 torn-state bug class that the deferred-pause pattern was designed to prevent.
- **Approach 2 / Approach 5 from the technical analysis** (defense-in-depth backstop in `_process_message_with_tracking` / every-downstream-guard) — **rejected**. The DB check in those guards is no-op during the race window; adding them adds complexity without catching the bug.
- **The existing PAUSED skip guard at `child_reports.py:1995`** — **unchanged**. That guard skips the parent's `status → COMPLETED` transition when the parent is already PAUSED; it does NOT cover Task creation. Our new guard is additive and lives at line 1893 (Task creation), not at line 1995 (status transition).
- **The existing PAUSED-excluded IDLE→RUNNING flip at `instance_messaging.py:1240-1278`** — **unchanged**. That flip correctly handles the post-cascade case (DB=RUNNING) and is the right behavior for legitimate child-resume re-enqueue. Our new guard is additive and lives at line 1211 (Task creation), not at line 1240-1278 (status flip).

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Primary Fix: `child_reports` race guard (dual check) | Add marker + DB-status dual check at `child_reports._process_child_completion_db_sync:1158` (Path 2). The `ReportInjection` row is the durable fallback, drained on every LLM call. | 7 | loose (with Phase 2: different guard logic — see "Why the asymmetry") | pending |
| 2 | Secondary Fix: `_prepare_enqueued_message` race guard (marker-only) | Add marker-only check at `instance_messaging._prepare_enqueued_message:1070` (Paths 1 and 5). Three branches: marker set → skip; DB==PAUSED → create + SQL-gate defers; DB==RUNNING → create. | 7 | loose (with Phase 1: shared session.get pattern, different guard logic) | pending |

## Coupling Map

| | Phase 1 | Phase 2 |
|---|---|---|
| Phase 1 | — | loose (shared session.get pattern; **different guard logic** — see "Why the asymmetry" above) |
| Phase 2 | loose | — |

The two phases share the **same `session.get(Instance, ...)` pattern** on the existing `WriteGuardSession`, but use **different guard logic**. Phase 1 uses the full dual check (marker OR DB) because `ReportInjection` has a verified drain path. Phase 2 uses marker-only because `MessageQueue` does not have an equivalent drain path; the post-cascade case relies on the existing `claim_pending_task` SQL pause gate. They can be developed, reviewed, and landed independently. Phase 1 is the canonical case (Path 2) and the most important deliverable; Phase 2 follows.

**Cross-phase risks where coupling could break**: if Phase 1's `session.get(...)` pattern is implemented differently from Phase 2's (e.g., one uses `asyncio.to_thread` and the other uses `session.get`), pattern consistency suffers. Both phases MUST use the same synchronous `session.get(Instance, ...)` idiom on the existing `WriteGuardSession`. Both phases MUST use the same structured-log format.

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | The `asyncio.to_thread` DB read in the dual check introduces a new block or race on the event loop | N/A | N/A | **N/A — resolved by design.** Both fix sites use the existing `WriteGuardSession.session` (no `asyncio.to_thread`, no event-loop hop, no new locks). Phase 1 uses `session.get(Instance, instance.parent_id)` at `child_reports.py:1158` (the same function already has a `session` from line 1193). Phase 2 uses `session.get(Instance, instance_id)` at `instance_messaging.py:1070` (the same function already has a `session` from line 1185, and a `session.get(Instance, instance_id)` call at line 1253 for the existing status-flip branch). |
| 2 | **Legitimate resume path is blocked (false positive)** | **CRITICAL** | Low | The `_deferred_question_pause` marker is popped during the pause flow itself (`daemon/services/instance_messaging.py:958` via `pop_deferred_question_pause`). The resume cascade flips DB to RUNNING BEFORE `_resume_processing_background` (root) or `enqueue_message` (child) runs (`daemon/routers/instances.py:579-587`). Both checks see empty marker + RUNNING DB → admit normally. **The no-false-positive tests in Phase 1 Task 6 (and Phase 2 Task 7) are mandatory and must exercise the actual resume flow** (pause via `question_pause_node`, wait for cascade, resume, assert unblocked). |
| 3 | Path 4 (user-click-stop) has a residual in-flight race between the cascade's `_graph_tasks.pop`/`task.cancel` and the `_pause_cascade_db_sync` commit | Medium | Low | The marker is NOT set on the user-click-stop path (no `question_pause_node`). The DB check catches the post-cascade case. The narrow in-flight window (~ms) is acceptable residual risk; flagged for future tightening. **For Phase 2 specifically**: the marker-only logic means DB==PAUSED no longer skips at the source, so this micro-window is also caught by the existing `claim_pending_task` SQL pause gate if a Task was created in the micro-window. **Document this in the code comment.** |
| 4 | `_deferred_question_pause` is process-local; multi-node deployment would re-introduce the race | Low | Low (multi-node not planned) | Document the in-memory marker as a single-process assumption in the code comment block. Future work: materialize as `instances.pause_pending BOOL` column with `_ensure_postgres_columns()` for dual-driver support. This same materialization is also the watertight fix for Phase 2's in-window message-loss limitation. Note in the `Risks` section above. |
| 5 | PostgreSQL vs SQLite syntax divergence in the fix | Low | Low | The fix uses only `session.get(Instance, ...)` (existing pattern, dual-driver) and `instance.status == str` (no new SQL). Naturally dual-driver compatible. Tests MUST run on BOTH DBs (project critical note). |
| 6 | `MessageQueue` row is created but Task is skipped (Phase 2 marker-set case) — message appears orphaned | Medium | Low (narrow race window) | **Verified delivery path analysis**: the in-window case is a known limitation. Root resume (`manager.py:5077-5093`) bypasses `enqueue_message` entirely and delivers the **answer** (a fresh `message` parameter) to `_process_message_with_tracking`; the skipped MessageQueue row is a stale/audit row, not the delivery mechanism. Child resume (`manager.py:4895`) re-invokes `enqueue_message` with a fresh UUID. **The skipped READY MessageQueue row's content is NOT delivered to the instance on resume.** Document this in the code comment; log a `WARNING` on skip; the user's UI can re-send if needed (the race window is 10-100ms; user impact is rare). **Recommended follow-up**: materialize the marker to DB so `claim_pending_task` SQL gate can defer — same future work as Risk #4. |
| 7 | The `report_injection` row is created but never drained (e.g., parent never resumes / parent terminated) | Medium | Medium (parent termination) | **Phase 1 only.** The live agent-node drain (`daemon/repositories/report_injection/repository.py:162-260`) fires before EVERY LLM call, so any post-resume turn drains the row. **If the parent is TERMINATED while a PENDING ReportInjection row exists, the row stays forever** (no cleanup). The existing `reconcile_terminal_watches` pattern is the template for a follow-up `reconcile_terminal_report_injections` cleanup. Add a code comment at the fix site documenting the residual risk. **Likelihood upgraded from Low to Medium.** |
| 8 | The fix changes behavior in a way that breaks an existing test | Medium | Medium | Regression-test sweep before landing. Specifically audit: tests that assert a Task is created during `_process_child_completion_db_sync` (Path 2) or `_prepare_enqueued_message` (Path 1/5). The tests that need updating are the ones that simulate a parent in `_deferred_question_pause` — they should now assert "no Task created". |
| 9 | Interaction with the `pause-resume-redesign` plan (first-class job/task PAUSED) | Low | Low (not yet landed) | The marker-based guard is complementary to job/task PAUSED state. Both can coexist; the redesign will replace the in-memory marker with a DB-backed `instances.pause_pending` column. Document the interaction in the plan. |
| 10 | The fix adds a DB read per child completion / per enqueue_message | Low | Low | Per child completion, one extra `session.get(Instance, ...)` on the PK (~sub-ms, same transaction). Negligible. Same for `enqueue_message` (one extra `session.get` on the PK; the existing status-flip branch already does this at `instance_messaging.py:1253`). The DB read is gated by the marker check (fast path), so the slow path is only hit when the marker is empty. |
| 11 | The fix is silently bypassed by a future code path that creates a `Task` row for a paused instance without going through `child_reports` or `instance_messaging` | Low | Low (no other Task-creation sites today) | The technical analysis audited all 5 paths and confirmed these are the only Task-creation sites. Add a comment in both files cross-referencing each other so future maintainers see the pattern. |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | No spurious turn when child completes while parent in `_deferred_question_pause` | Phase 1 unit + integration test: trigger child completion during the race window, assert no PROCESS_REPORT Task row created, assert MessageQueue + ReportInjection rows ARE created | 0 spurious turns in 1000 simulated race-window events |
| 2 | No spurious turn when user message arrives while instance in `_deferred_question_pause` | Phase 2 unit test: call `enqueue_message` for instance in marker, assert no Task row created, assert MessageQueue row IS created | 0 spurious turns in 1000 simulated events |
| 3 | No spurious turn when sibling message arrives while sibling's tree in `_deferred_question_pause` | Phase 2 unit test: enqueue message to sibling during the race window (simulated via DB==PAUSED), assert no Task row created | 0 spurious turns in 1000 simulated events |
| 4 | **Legitimate resume is NOT blocked (no false positive)** | Phase 1 Task 6 + Phase 2 Task 7: simulate user-answering question → resume cascade → drive turn, assert Task is created and turn executes normally. Tests must exercise the **actual resume flow** (pause via `question_pause_node`, wait for cascade, resume, assert `_resume_processing_background` drives unblocked) | 100% of legitimate resumes complete the graph turn |
| 5 | No report is lost during the skip | Phase 1 Task 5 integration test: trigger Path 2 skip, then resume parent, assert report content is injected as HumanMessage via `claim_for_injection` on the parent's first post-resume LLM call | 100% of skipped reports delivered on resume |
| 6 | No message is lost during the post-cascade skip (Phase 2) | Phase 2 Task 5 integration test: simulate post-cascade case (DB==PAUSED, marker empty), trigger enqueue, assert Task IS created and not claimed until resume; after resume, assert message is delivered | 100% of post-cascade messages delivered on resume |
| 7 | All tests pass on both SQLite and PostgreSQL | Run test suite against both DBs (project critical note: PostgreSQL is the DEFAULT DB) | 0 failures, 0 errors on both DBs |
| 8 | The fix is dual-driver compatible (SQLite + PostgreSQL) | Code review: no SQLite-only or PG-only syntax; existing `session.get` pattern is dual-driver | Code review passes |
| 9 | Structured logging on skip | Manually verify: race-window event produces an `INFO` log line identifying the instance and the skip reason | Log line present in 100% of skip events |
| 10 | The fix does not introduce a new event-loop block | Profile: race-window event completes the `child_reports` / `instance_messaging` path within the same latency budget as the unfixed path | <5% latency increase |
| 11 | The fix is documented in code comments | Code review: comment block above each guard explains the race, the guard logic (dual vs marker-only), the durable fallback, the C2 bug context, the residual risks, and cross-references the other phase's fix | Comment block present, accurate, and cross-references both phases |
| 12 | The fix does not break existing tests | Run full test suite (excluding the new tests) — must pass on both DBs | 0 regressions |
| 13 | **Race-window tests verify the actual race (not just a flag)** | Phase 1 Task 7 + Phase 2 Task 6: a test that monkeypatches `pause_instance_cascade` (or the DB-sync dispatch) with a CONTROLLED DELAY so the race window is held open, then asserts: while the window is open (marker set, DB still RUNNING), a child completion / enqueue_message produces NO Task. This proves the guard fires during the real race window, not just when a flag is set. | 100% of race-window simulations: NO Task created while window is open |
| 14 | **Residual race window is documented** | Code comment at both fix sites documents the micro-window between marker-POP (during resume cleanup) and the DB PAUSED→RUNNING commit. State explicitly: marker-only Phase 2 logic means DB==PAUSED no longer skips, so this micro-window is handled by the existing `claim_pending_task` SQL gate. | Code comment present at both fix sites |

## Research Insights

The technical analysis at `.agents/shared/planning/pause-tool-result-fix/technical-analysis.md` (revised 2026-08-01) provides the full root-cause analysis, option evaluation, and recommendation. Key findings that shaped this plan:

- **The decisive factor**: every candidate check that reads DB status sees `RUNNING` during the race window, so it does not fix the bug. A correct fix MUST observe the in-memory `_deferred_question_pause` marker (set synchronously inside `question_pause_node` before the cascade's DB flip) or close the race window at its source. This is why the dual check uses the marker as primary and DB as fallback.

- **Option D (source fix at `child_reports._process_child_completion_db_sync:1158`)** is the recommended primary fix. It closes the race at its source: the spurious PROCESS_REPORT Task is never created, so the SQL gate is never asked to admit it. The `ReportInjection` row is the durable fallback, drained on the parent's first post-resume LLM call (`graph.py:2566-2590`).

- **Options A, C (downstream DB-based guards) are REJECTED as primary fix, CONDITIONALLY acceptable as defense-in-depth**: they all read DB during the race window. Adding them as primary fix is misleading — they look like defense-in-depth but actually no-op during the window they need to cover. They DO catch the post-cascade case, but the post-cascade case is already covered by the existing `claim_pending_task` SQL gate, so they add no defense-in-depth value. Keep rejected per the trade-off table. **Option B is rejected additionally** for violating the gate's documented pure-Lock design.

- **Option E (synchronous DB flip in `question_pause_node`) is REJECTED**: it re-introduces the C2 torn-state bug class that the deferred-pause pattern was designed to prevent. The deferred-pause pattern is the proven solution; re-adding synchronous DB writes inside the graph task undoes the C2 fix. This is re-asserted in the Out-of-Scope section above.

- **The Phase 1 vs Phase 2 asymmetry is the main conceptual change**:
  - Phase 1 uses the full dual check (marker OR DB) because `ReportInjection` has a verified drain path on every LLM call (`graph.py:2566-2590`).
  - Phase 2 uses marker-only because `MessageQueue` does not have an equivalent drain path. The post-cascade case relies on the existing `claim_pending_task` SQL gate. The in-window skip case is a known limitation with a recommended follow-up (materialize the marker to DB).
  - Both phases share the `session.get(Instance, ...)` pattern on the existing `WriteGuardSession` (no `asyncio.to_thread` — see BLOCKING Issue #1 in the original review, which is fixed here by using the existing `session`).

- **Verified delivery path analysis (BLOCKING Issue #2 from the original review)**: I traced the resume flow precisely:
  - Root resume (`manager.py:5077-5093`) bypasses `enqueue_message` entirely and calls `_process_message_with_tracking` with a fresh `message=answer_msg` parameter. The skipped MessageQueue row's content is NOT delivered; the row is a stale/audit record.
  - Child resume (`manager.py:4895`) calls `enqueue_message` with `message=message` (the resume message) and `source="cascade_resume"`, creating a fresh MessageQueue row with a fresh UUID. The old skipped row is never claimed.
  - The resume cleanup at `manager.py:4937-4940` filters to `[PENDING, PROCESSING, RETRYING]` only — READY is excluded.
  - **Verdict**: the in-window skip DOES lose the user message. This is a known limitation, accepted because the race window is narrow (~10-100ms) and the fix prevents the worse bug (spurious turn). Recommended follow-up: materialize the marker to DB so `claim_pending_task` can defer the Task instead of skipping it.

- **Path-by-path summary**: Path 1 (user message) and Path 5 (sibling message) are caught by the Phase 2 fix; Path 2 (child completion) is caught by the Phase 1 fix; Path 3 (legitimate resume) is unaffected because the marker is popped during the pause flow and the DB is flipped to RUNNING before resume runs; Path 4 (user-click-stop) is partially caught (post-cascade case via DB check in Phase 1, via the existing SQL gate in Phase 2; in-flight case is residual).

- **The in-memory marker is process-local**: `daemon/manager.py:714` declares `self._deferred_question_pause: set[str] = set()`. This is single-process by design; multi-node deployment is a future-scope follow-up. The technical analysis's Risk #1 documents this; our Risk #4 reaffirms it. The same materialization is the recommended follow-up for Phase 2's in-window limitation.

## Open Questions

- **Q1 (mandatory report-injection drain on resume)** — **RESOLVED as out-of-scope**. The live `ReportInjectionSlot` drain before each LLM call (`daemon/graph.py:2566-2590`) is sufficient. The first LLM call after resume drains the row. No explicit resume-init drain is needed; this would add complexity for no measurable gain.

- **Q2 (whether `enqueue_message` check should also gate the `MessageQueue` insert)** — **RESOLVED**. The check gates ONLY the `Task` insert. Justification: the `MessageQueue` row is the durable record of "user wanted to send this message" and must be preserved (it is the source of truth for message content + ordering). The `Task` is the dispatch primitive — skipping it is what avoids the spurious worker claim. On resume, the resume flow re-creates the Task (via `enqueue_message` from the child-resume path, or via `_resume_processing_background` from the root-resume path which bypasses `enqueue_message` entirely). The `MessageQueue` row's READY status is processed normally on the next claim **when the Task is created (post-cascade case)**. **In the in-window skip case, the MessageQueue row is NOT processed on resume** (see Q5).

- **Q3 (report-injection rows for never-resumed / terminated parents)** — **RESOLVED as a follow-up item**. The fix site adds a code comment documenting the residual risk; the actual `reconcile_terminal_report_injections` cleanup (mirroring `reconcile_terminal_watches`) is a separate PR. Likelihood: Medium.

- **Q4 (sibling in a different tree)** — **RESOLVED**. Cascade pauses the entire tree via `get_tree_ids(root_id)`, so siblings in the same tree share the cascade. Siblings in a different tree are unaffected by the parent's pause. No additional fix needed.

- **Q5 (NEW — Phase 2 in-window message-loss limitation)** — **RESOLVED as known limitation + follow-up**. The Phase 2 marker-only logic means a Task is skipped when the in-memory marker is set (in-window race case). The skipped READY MessageQueue row is NOT processed on resume. This is a known limitation accepted because: (a) the race window is narrow (~10-100ms); (b) the fix prevents the worse bug (spurious turn on a paused instance); (c) the recommended follow-up (materialize the marker to DB) is the watertight fix. The fix site must: log a `WARNING` on skip; document the limitation in the code comment; reference the follow-up item.

**No new questions raised by this plan that block implementation.**

## Follow-up Items (Tracked, Out of Scope)

1. **`reconcile_terminal_report_injections` cleanup** — mirror the existing `reconcile_terminal_watches` pattern to clean up PENDING `ReportInjection` rows for TERMINATED parents. Likelihood: Medium. Add a code comment at the Phase 1 fix site referencing this.

2. **Materialize `_deferred_question_pause` to DB** — add `instances.pause_pending BOOL` column (or equivalent), set/clear alongside the in-memory set, update `claim_pending_task` SQL gate to check it. This: (a) enables multi-node deployment; (b) is the watertight fix for the Phase 2 in-window message-loss limitation. Requires `_ensure_postgres_columns()` for dual-driver compatibility.

3. **Refresh `test-strategy.md` / `workflow.md`** — these are noted as having stale references in the project critical notes; the marker-based guard pattern should be documented for future reference.

## Tracking

- **Created**: 2026-07-31
- **Last Updated**: 2026-08-01 (revised after rigorous review)
- **Status**: Ready for Review (revised)
- **Source technical analysis**: `.agents/shared/planning/pause-tool-result-fix/technical-analysis.md`
- **Related plan (orthogonal)**: `.agents/shared/planning/pause-resume-redesign/`

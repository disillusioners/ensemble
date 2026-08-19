# Plan Overview: Pause/Resume Silently Drops Child Completion Reports (incident 6c631666)

Date: 2026-08-19 (v3.1, cycle-2 patched same day; v3 review-patched, v2 architect-amended, v1 original)
Author: planner[v2] via plan-creation worker
Status: v3.1 (cycle-2 patched — implementation-ready) — deep review cycle 2 verdict APPROVE_WITH_NOTES (implementation-ready); N1 mechanical patch + N2 doc note + reviewer residuals folded; architecture VERIFIED SOUND, specs converged
Branch: feature/pause-report-recovery @ 6bb99d5f
Evidence: two embedded explorer reports (HIGH confidence) + independent line-citation spot-verification + architecture review (architecture-recommendation.md) + deep review cycle 1 (2-model council, 5 criticals + promoted warnings folded in)

## Objective

A completed child turn ALWAYS eventually reports to its parent: pause/resume (including restart and crash) must never silently drop a completion report. Exactly-once delivery is preserved, with idempotency keyed on a **DB-persisted marker** (never instance-status checks or RAM state).

## Verified Root Cause Summary

All line citations independently re-verified on branch head 6bb99d5f; architect review cross-verified; v3 re-verified the review's own anchors (enum case at models.py:56-58, `_handle_cancel` second cancel point at pipeline.py:893, `ProcessingContext` shape at 102-139, `dependency_watchers` join keys at dependency_bus/models.py:100-180).

### Site 1 — pipeline silent PAUSED skip (Variant A, first half)

`daemon/services/message_processing_pipeline.py:472-482`
A defensive re-check ("instance is PAUSED → skip child completion entirely") was added for the question()-tool pause-overwrite bug (2026-07-21). When the pause TOCTOU window fires — pause cascade cancels the graph task at `instance_lifecycle.py:2124-2125` **before** `_pause_cascade_db_sync` commits PAUSED at `2164-2172`, and the WorkerPool has already claimed a PROCESS_REPORT task in that window — the pipeline's post-processing observes PAUSED and skips `_check_child_completion` with **no persisted marker and no deferred re-entry**: the log line is the only trace. The child's own message is already COMPLETED (Stage 4, pipeline.py:428) and its instance status already transitioned (Stage 5.5, pipeline.py:451-453), so the drop is silent and permanent.

### FM-11 (CRITICAL, architect finding) — a naive marker-at-skip-site does NOT close Site 1

Two architect workers independently found the same hole: Stages 4–6 run inside a try whose `except asyncio.CancelledError` (pipeline.py:486-487) returns via `_handle_cancel`. **`asyncio.CancelledError` is BaseException (Py3.13+), not Exception — a best-effort `except Exception` wrap never sees it.** The planned marker write sits downstream of `await _is_instance_paused(...)`; if the pause cascade's `graph_task.cancel()` fires at that await, the marker write never runs. Pipeline.py has **no in-flight transaction at Stage 6** (verified — no `engine.begin` in the file), so "write inside the existing transaction" is not implementable. This is the exact incident shape (6c631666): cancel mid-Stage-6 → no marker, no artifacts, silent drop.

Plan-v2 verification note: `_is_instance_paused` is **`async def`** (pipeline.py:709; `asyncio.to_thread` inside) — the await IS a genuine cancellation point; FM-11's primary window is real, not narrowed.

**Closure (adopted, v3 pattern precision per review W4/C2): Option A + Option C defense in depth** —
- **A (task 1.4)**: hoist the marker write into a `finally` block at the try/except indent, keyed on a cached pause-check result. **Precise pattern (W4): `asyncio.create_task(asyncio.shield(asyncio.to_thread(ensure_deferred, ...)))` — schedule and DETACH (do not hold the Task ref in a way that ties lifecycle; do NOT await it from the cancelled finally — a bare `await shield(...)` there would be re-cancelled); all error handling self-contained inside the dispatched coroutine.** Control flow (C2): the detached dispatch is scheduled in `finally` BEFORE the except arm re-raises; `except asyncio.CancelledError` (486-487) then routes through `_handle_cancel` (880-902), whose `await callbacks.on_cancel(exc)` at **:893 is a SECOND cancel point — it must not (and, with the detached pattern, cannot) kill the dispatched write task**. Best-effort `except Exception` remains only for in-coroutine DB errors (no Stage-6 transaction exists — forced).
- **C (task 2.4)**: the no-row backstop query — **designed from scratch in task 2.4 (v3; the v2 name `find_completed_children_missing_report` was a placeholder that does not exist in code)** — is a **PERMANENT periodic lane**, load-bearing for the primary incident site (catches cancel-at-await escapes, crash-mid-shield, and any future no-marker drop lane), not legacy cleanup.
- **B (pause-cascade transactional hook in instance_lifecycle.py:2164-2172)**: DEFERRED — pre-approved fallback if test 3.9 shows shield gaps.

### Site 2 — internal_child_noop zombie resume (Variant A, second half)

`daemon/manager.py:6190-6204` (decision tree 6118-6220)
When a parent is resumed via `resume_processing_job`: if no `awaiting_answer` suspension handle and no paused/cancellable turn is found, but `silent=True`, the router returns `{"status": "silent_resume"}` — the instance stays RUNNING with **no graph driver**: a zombie. For the incident scenario the child's turn had completed while paused (Site 1 drop), so no handle exists, no report row exists, and the router has no concept of "completed child work whose report was never created/delivered". Work is lost.

### Variant B — recoverable-skip guards (incident 1d5fd5d2)

Live check is INLINED at `daemon/services/child_reports.py:2106-2108` (`pending_count > 0 → should_send=False, skip_reason="pending_messages_exist"`); the helper at 665-695 is dead/test-only code (kept; one deprecation comment only — review S-d). Idempotency guard at `:1626-1641`. **Scope note (v3, review C5): the 1.6 guard fires only when the CHILD instance is PAUSED** — the canonical Site-1 shape (child COMPLETED, parent PAUSED) is handled by the **1.4 pipeline lane** (pipeline.py:472 checks `context.instance_id`, the child; the parent-pause artifact-skip is the 2419-2437 conditional). Each guard's covered shape is stated in task 1.6; test 3.2(d) pins the separation. The skip reasons are treated as terminal "no report owed" outcomes, but under pause they mean "delivery deferred, nothing remembers the deferral".

### Shared enablers (both variants)

- **FM-2 / RAM-only structures**: `_report_injection_pending` (manager.py:583; bumped child_reports.py:2915-2917; fast-path check graph.py:275-277 returns `[]` without DB check when the id is absent) and `_pending_injections` (manager.py:616-630). Both lost on restart; a PENDING report_injections row enqueued pre-restart is stranded.
- **FM-1 / type-blind cleanup**: `_schedule_explicit_handle_resume` (manager.py:6306-6527; PENDING-task branch 6414-6467 completes message + cancels task with no task_type check) can kill a live PROCESS_REPORT task racing resume. Tasks 2.3 ↔ 2.4 are co-dependent (same PR).
- **FM-5 / mirror gotcha**: reconcile_turn_mirror CASE WHEN (task/repository.py:844-863) — ELSE 'completed' default. Architect-verified: this design introduces NO new terminal_reason (`deferred_pause` is an outcome label, not a `message_queue.terminal_reason`) → MIRROR_SET guard downgraded to a regression assertion (task 2.5d).
- **Drop-site asymmetry**: Site 1 skips **before** `_process_child_completion_db_sync` runs (no injection row exists — and per FM-11, even writing one at the skip site is cancel-fragile); child_reports internal guards skip **after** row creation (PENDING row exists but nothing re-enters). The fix covers both shapes via marker (A) + permanent backstop (C).

## Assumptions (leader-decided; W2 documented-only)

- **FM-12 — "parents eventually resume."** A parent paused indefinitely leaves its PENDING/DEFERRED rows unrecovered: the sweep skips busy parents (PAUSED counts as busy), and the router fires only on resume. ACCEPTED and DOCUMENTED; a paused-parent sweep seam is FUTURE, not in scope. **Exception (v3 W1): terminal parents are NOT covered by this assumption — see the ORPHAN lane in task 2.4 (revive-and-deliver or explicit disposition, never silent).**
- **Recovery latency contract (W2)**: for the canonical shape (marker written, parent later resumed), recovery fires at resume (router — immediate); for backstop shapes (marker lost / no marker), recovery lands within **one sweep cycle** after the age guard (≤ interval + age bound; defaults → ~10-15 min worst case). The router step order is UNCHANGED (2.1 stays after step 2 — review confirmed no reorder). Observability note: add a `deferred_row_age_seconds_p99` metric (age at recovery claim) to the sweep's structured logs; p99 growth signals backstop reliance rising (FM-11 escape frequency).
- **Per-instance serialization S3** (`claim_pending_task` status='running' ONLY) blocks recovery behind a live parent turn — accepted as the busy-check TOCTOU mitigation.

## Design Decisions (answers to Q1–Q5, architect-confirmed, review-patched)

### Q1 — Marker home: `report_injections` + new state `DEFERRED` (no new table, two new columns)

**Decision: extend `report_injections`** — architect CONFIRMED across 5 axes. DEFERRED is invisible to every existing consumer by construction: hot-path drain `claim_for_injection` (repo:162-259), fallback `claim_for_task_delivery` (267-345), mirror SQL (task/repository.py:951, guarded `state='PENDING'`), and the INJECTED filter (child_reports.py:1547) all guard on states DEFERRED never occupies.

Schema (task 1.1; v3 patches C1/S-b):
- `DEFERRED` enum member on `ReportInjectionState`. **C1 CASE PARITY: the enum stores UPPERCASE string values (verified models.py:56-58: `"PENDING"`, `"INJECTED"`, `"TASK_DELIVERED"`) — the partial unique index predicate must use UPPERCASE literals `WHERE state IN ('PENDING','DEFERRED')`, and `DEFERRED_REASON_*` constant VALUES are UPPERCASE everywhere** (`PAUSE_TOCTOU`, `PENDING_MESSAGES`, `IDEMPOTENCY_SKIP`, `RESUME_ROUTER`) — schema DDL, repository, writer sites, tests, logs. A **case-lockstep contract** (task 1.7 docstring) declares: storage-layer literals and app-layer constants must never drift in case; any new state/reason value is added to both in the same change.
- State machine: PENDING is the single gateway — normal path creates PENDING atomically (message+task+injection); pause drop-sites create DEFERRED; recovery claims DEFERRED→PENDING via guarded UPDATE (one winner); terminals depart only from PENDING (INJECTED / TASK_DELIVERED). DEFERRED→INJECTED/TASK_DELIVERED illegal. `enqueue` and `ensure_deferred` are the ONLY row-creation paths (repo contract, task 1.2).
- `deferred_reason: str | None` — **TEXT column, NOT VARCHAR(32)** (review S-b: the reason vocabulary is open-ended; length caps add churn without value). Values uppercase per C1.
- **`report_message_id` becomes nullable=True** (architect Blocker 1; **C4: it is NOT NULL today** — models.py:144-147 — and both claim lanes index it (repository.py:222, 267-345), so task 2.2 carries an explicit `IS NULL` branch and a consumer grep-audit). Site-1 DEFERRED markers have no artifact yet; NULL is the honest pre-artifact shape. The NULL shape arises ONLY from marker-first writes (sweep/hot-path consumers must handle-or-exclude).
- **`recovery_attempted_at: str | None` timestamp + partial index** (FM-13, **W9: permanent reconciler semantics — NO one-time-only sweeps anywhere**): sweep stamps it at recovery claim; the retry lane re-scans `state='PENDING' AND recovery_attempted_at < now - retry` (covering both stamped-stale rows and, unified with the age-bounded PENDING lane, never-stamped stranded rows) — permanent FM-3 closure, not a migration one-shot.
- **Idempotency key pinned = `(parent_instance_id, child_instance_id, child_message_id)`** via partial unique index `WHERE state IN ('PENDING','DEFERRED')` (UPPERCASE literals — C1) — `report_message_id` is NOT the key (per-delivery-attempt). Index via `sqlite_where`/`postgresql_where` (precedent `idx_job_idempotency`, job_queue/models.py:292-298); **index NAME identical** in `_ensure_postgres_columns()` and the SQLite companion migration. **Acceptance (C1): a test must ASSERT `IntegrityError` actually raises on a duplicate non-terminal triple — not merely that the index exists.**
- New columns via `_ensure_postgres_columns()` + SQLite companion migration. **W3 pre-check: a `GROUP BY (parent,child,child_message_id) HAVING COUNT(*)>1` dedup/detect step runs BEFORE index creation** (existing duplicate rows would fail the PG index build). **W8 rollback runbook: on revert, DROP the partial unique index BEFORE reverting the column** (column reversion with the index present fails / orphans the index). Migration safety architect-verified: `state` is TEXT not a PG enum — rollback-safe.

### Q2 — Resume-router re-entry check

**Decision (unchanged position; W6 IntegrityError narrowing): new routing step between step 2 (manager.py:6156-6187) and step 3 (internal_child_noop, 6190). route_outcome: `deferred_report_recovery`. Step order NOT reordered (W2).**

Router check (DB-only, indexed): `find_deferred_for_parent(instance_id)` non-empty → per DEFERRED row (oldest first): atomically `transition_deferred_to_pending` (guarded UPDATE; rowcount=0 = another actor recovered → skip row), **THEN** partial-artifact reconciliation, **THEN** re-enter `_process_child_completion_and_notify_parent(child_instance_id, child_message_id)`. **Ordering binding (architect Verdict 2): the DEFERRED→PENDING transition MUST precede reconciliation** — the mirror SQL (task/repository.py:951) guards on `state='PENDING'`.

**IntegrityError handling (v3 W6 — narrowed)**: the catch belongs **at the INSERT site — `ensure_deferred` — log+skip** (duplicate concurrent marker write across the three actors; the child-keyed bus lock at child_reports.py:1453 does NOT serialize them; the triple index is the only cross-actor gate). The **tri-state (claimed / already_delivered / missing) remains at the NATURAL enqueue path** (`enqueue()` / claim lanes), unchanged. The router/sweep never see raw IntegrityError — `ensure_deferred` absorbs it and reports a no-op result. Terminal parent (COMPLETED/ERROR/TERMINATED/FAILED) → revival semantics first (instance_messaging.py:1486-1510 precedent — declared In-Scope, W1), then re-enter.

### Q3 — Variant B guards become recoverable

1. `pending_messages_exist` — **live inlined site child_reports.py:2106-2108** (665-695 dead/test-only; one deprecation comment only, S-d). Keep the skip (no inline re-queue — sibling completion is the natural re-entry); write `ensure_deferred(parent=inst_check.parent_id, child=instance_id, child_message_id, reason='PENDING_MESSAGES')` on the same session/transaction as the skip decision.
2. Idempotency guard (1626-1641): split. `status in (COMPLETED, ERROR)` → unchanged `idempotency_skip`, no marker. `status == PAUSED` → new outcome `deferred_pause` + `ensure_deferred(reason='IDEMPOTENCY_SKIP')`; side-effect-free return. **Scope note (C5): this guard covers the CHILD-PAUSED shape only; child-COMPLETED/parent-PAUSED (canonical Site-1) is 1.4's lane — test 3.2(d) pins the separation.**
3. Both guards log `deferred_reason` (uppercase) for observability.

### Q4 — Sweep: PERIODIC reconciler (leader decision), not boot-only

`ReportDeliveryRecoveryService` runs PERIODICALLY (StaleTaskRecovery precedent, manager.py:5072-5091) + once at boot, after `_ensure_postgres_columns` (binding order). Bounded + idempotent. **Five lanes (v3: no-row backstop designed-in W1/C3; no one-time lanes W9):**
1. DEFERRED rows.
2. **Permanent no-row backstop** — the designed query (task 2.4 spec: JOIN semantics, terminal-parent exclusion, FIRED-watcher exclusion, false-positive matrix, dual-driver index dependencies; the v2 placeholder name is replaced).
3. **Age-bounded PENDING lane (permanent, W9)** — stranded PENDING rows past the age guard, with or without `recovery_attempted_at` (unified with lane 4; replaces v2's one-time `sweep_legacy_pending`).
4. **`recovery_attempted_at` retry lane (permanent, W9/FM-13)** — `state='PENDING' AND (recovery_attempted_at IS NULL OR recovery_attempted_at < now - retry_minutes)` on rows past the age guard: closes the mid-sweep-crash gap and FM-3's missing corrective path, permanently.
5. **ORPHAN lane (W1)** — DEFERRED rows whose parent is TERMINAL (COMPLETED/ERROR/TERMINATED/FAILED): revive-and-deliver (instance_messaging.py:1486-1510 precedent) or an explicit final disposition (structured log + metric; never silent). Asserted by test 3.6 sub-case.
- Per row: skip if `has_instance_busy(parent_id)` (widened PENDING+RUNNING+PAUSED); TOCTOU re-check inside the claim transaction; `transition_deferred_to_pending` (rowcount=0 = already-recovered); `ensure_deferred` absorbs IntegrityError; re-enter completion under S3.
- Bounds: age guard 10 min; batch cap 100/run; remainder logged; endpoint action `recover_report_delivery` for on-demand runs.
- Crash-safety: per-row errors leave rows DEFERRED (retried next cycle); mid-sweep crash after transition → fresh PENDING caught by lane 3/4 next cycle.

### Q5 — Test strategy

Existing coverage (do not duplicate): test_pause_resume_root.py, test_resume_flow_redesign.py, test_pause_race_resume_flow.py (incl. test_resume_after_pause_admits_child_completion), test_cascade_pause_resume.py, test_pause_cascade_message_queue_orphan.py, test_completion_report.py, test_report_lane_phase2.py, test_pause_report_orphan_reconciliation_pg.py, test_child_completion_pending_task_guard.py, test_report_injection.py.

New tests — detailed in Phase 3: both sites + all three variants **+ 3.2(d) guard-scope separation**; **3.9 FM-11 shield-gap detection with crash-mid-shield fixture (W12)**; crash recovery (FM-2 RAM loss); double-delivery across **10 explicit actor pairings (S-a)** incl. transition ordering + IntegrityError; sweep safety incl. **terminal-parent ORPHAN sub-case (W1)**; PG migration **3 sub-cases (C4)**; **MANDATORY full e2e per `.agents/tester/rules/ensure.md` cited BY NAME** — changes touch all five gated modules: claim_pending_task, turn_transitions, reconcile_turn_mirror, job_processor, job_locks (W12). PostgreSQL-primary; no SQLite-only syntax.

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | DB-persisted delivery marker + drop-site fixes | DEFERRED state + 2 columns (case-parity contract) + nullable report_message_id; Site 1 FM-11-hardened marker (precise W4 pattern); variant B guards recoverable | 7 | tight with Phase 2 (marker contract) | pending |
| 2 | Resume-router re-entry + cleanup guard + periodic recovery sweep | Router `deferred_report_recovery` (narrowed IntegrityError, transition-before-reconciliation); FM-1 type guard; periodic sweep with 5 lanes incl. designed no-row backstop + ORPHAN lane | 6 | tight with Phase 1; 2.3↔2.4 same PR | pending |
| 3 | Tests + e2e (MANDATORY ensure.md + 3.9 shield-gap + crash-mid-shield fixture) | Unit both sites/variants/crash/double-delivery/FM-11 + full e2e | 9 | loose with 1–2 | pending |

Ordering: 1 → 2 → 3. **W11 SAFETY NOTE: the Phase-1-incomplete window (markers written, no reader until Phase 2) is SAFE by merge policy — merge-to-latest happens only after ALL phases pass; no intermediate state reaches production.** Phase 3 unit tests for Phase 1 content may run alongside Phase 2; the e2e gate runs last.

## Coupling Map

| | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| Phase 1 | — | tight (marker contract: enum, uppercase constants, 3 repo methods, schema) | loose |
| Phase 2 | tight | — (internally: 2.3↔2.4 land together, same PR) | loose |
| Phase 3 | loose | loose | — |

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | FM-11: cancellation prevents the Site 1 marker write | High | Medium | Option A precise pattern (create_task+shield+detach, self-handled errors — W4) + Option C permanent no-row backstop (designed query, task 2.4) + test 3.9 (incl. crash-mid-shield fixture, W12) verifies; Option B pre-approved fallback |
| 2 | Recovery re-entry duplicates a report | High | Medium | Idempotency key = obligation triple (partial unique index, UPPERCASE predicate — C1) with an IntegrityError-raises acceptance test; guarded DEFERRED→PENDING claim; claim-level guarded UPDATEs; S3 serialization; existing_report SELECT; **IntegrityError absorbed at `ensure_deferred` INSERT site (W6)**; Phase 3 pairing tests |
| 3 | Transition/reconciliation ordering violated → mirror silently skips | High | Low | Binding order in 2.1; Phase 3 ordering assertion |
| 4 | Pause TOCTOU window remains open | Medium | Medium | Deferral is sound ONLY with the FM-11 seam (A+C); every window drop is marker-recoverable or backstop-recoverable; revisit with field data (`deferred_row_age_seconds_p99` — W2) |
| 5 | New columns/index break existing PG DBs | High | Low | ADD COLUMN IF NOT EXISTS + **W3 pre-index dedup detect step** + **W8 rollback runbook (drop index BEFORE column revert)**; identical index names both DDL paths; state TEXT not PG enum; PG migration tests (3 sub-cases — C4) |
| 6 | Sweep touches a live instance | High | Medium | `has_instance_busy` (widened) + TOCTOU re-check + age guard + batch cap; periodic idempotent re-runs |
| 7 | **Shared 2.3↔2.4**: partial landing kills sweep-delivered tasks or leaves FM-3 escapes | High | Medium | Same-PR mandate in both task specs + PR checklist; CI runs both task groups together |
| 8 | DEFERRED semantics drift / case drift between storage and app layers | Medium | Low | Single uppercase constants module; **case-lockstep contract (task 1.7, C1)**; repo contract: `enqueue` + `ensure_deferred` only creation paths |
| 9 | Mirror gotcha: new state silently maps healthy | Medium | Low | No new terminal_reason by design; 2.5d regression assertion (grep: no new terminal_reason literals without MIRROR_SET) |
| 10 | Terminal-parent DEFERRED rows strand silently (W1) | Medium | Low | ORPHAN lane in 2.4 (revive-and-deliver or explicit disposition); test 3.6 sub-case asserts observable eventual disposition |
| 11 | Boot/per-sweep latency on large backlog | Medium | Low | Fire-and-forget boot run; batch cap; per-lane kill-switches; interval config-gated (default 300s — S-e) |
| 12 | Scope creep | Medium | Medium | Hard scope boundary; dead helper keeps one deprecation comment only; Option B deferred |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Variant A no longer drops | Unit+integration (3.1, 3.8) | 100% pass; exactly 1 report on parent |
| 2 | FM-11 closed (incl. crash-mid-shield) | Test 3.9 (a+b, crash-mid-shield fixture) | 100% pass — Option B decision gate |
| 3 | Variant B no longer drops; guard-scope separation holds | Unit (3.2 a-d) | 100% pass; exactly-once; 3.2(d) proves 1.4-lane not 1.6-lane |
| 4 | Crash-safe: restart between drop and resume | Integration (3.4) | 100% pass |
| 5 | Exactly-once across all 10 actor pairings + ordering + IntegrityError | Race tests (3.5, explicit matrix — S-a) | 0 duplicate deliveries |
| 6 | internal_child_noop preserved for genuine no-work | Router regression (3.3b) | outcome unchanged without DEFERRED row |
| 7 | Existing suite green + MANDATORY `.agents/tester/rules/ensure.md` e2e (all five gated modules) on PG | Full run (3.8) | 0 regressions |
| 8 | Sweep never touches live instances; idempotent; 5 lanes incl. ORPHAN | Sweep tests (3.6) | 0 live touches; re-run no-op; terminal-parent row gets observable disposition |
| 9 | Legacy prod zombies recoverable | Sweep tests (3.6) | recovered or busy-skipped, never duplicated |
| 10 | Schema migration + rollback safe on existing PG DB | PG migration tests, 3 sub-cases (C4) + W3/W8 procedures | re-run idempotent; dedup pre-check passes; rollback runbook order verified |
| 11 | IntegrityError genuinely raises on duplicate triple (C1) | Repo unit test | assertion on raised IntegrityError, not index existence |

## Scope

### In Scope
- Site 1 FM-11-hardened marker write (pipeline.py:472-482 + finally-hoist of that block only; precise W4 pattern)
- Variant B guard changes at the LIVE sites (child_reports.py:2106-2108, 1626-1641) with the C5 scope note
- `report_injections` schema: DEFERRED state (UPPERCASE) + `deferred_reason` TEXT + `recovery_attempted_at` + nullable `report_message_id` + partial unique index (UPPERCASE predicate, name-parity, W3 pre-check, W8 runbook)
- Resume router new step (manager.py, between 6187 and 6190), narrowed IntegrityError (W6), transition-before-reconciliation ordering
- FM-1 type-aware guard (same PR as sweep)
- Periodic ReportDeliveryRecoveryService — 5 lanes (DEFERRED; designed no-row backstop; permanent age-bounded PENDING; recovery_attempted_at retry; **terminal-parent ORPHAN**), bounded, idempotent, busy-guarded
- **Terminal-parent revival-and-deliver for DEFERRED rows (W1; instance_messaging.py:1486-1510 precedent)**
- Crash-recovery endpoint extension (`recover_report_delivery`)
- Tests 3.1–3.9 incl. 3.2(d), crash-mid-shield fixture, 10-pairing matrix, MANDATORY ensure.md e2e

### Out of Scope
- Closing the pause TOCTOU race window itself (Option B deferred — pre-approved fallback ONLY on 3.9(a) failure)
- Paused-parent sweep seam (FM-12, non-terminal parents) — documented assumption; future seam
- Periodic-scheduler redesign beyond StaleTaskRecovery precedent
- `reconcile_terminal_report_injections` general orphan cleanup
- Deleting the dead 665-695 helper (one deprecation comment only — S-d)
- Drive-by refactors; frontend/SSE; Plane sync; skill system; `_pending_injections` RAM queue persistence

## Research Insights

Shaping findings (all verified on 6bb99d5f; architect cross-verified; v3 re-verified review anchors):
- Resume router lives in manager.py — 6118-6220, noop at 6190-6204.
- Site 1 skips BEFORE `_process_child_completion_db_sync` → no row; FM-11 makes naive marker-at-skip cancel-fragile (CancelledError = BaseException; no Stage-6 transaction; `_is_instance_paused` async def at 709 → real cancel point).
- **`_handle_cancel` second cancel point: `await callbacks.on_cancel(exc)` at pipeline.py:893 (v3-verified) — the W4 detach pattern is what makes the dispatched write immune to it.**
- **`ProcessingContext` (pipeline.py:102-139) has NO parent_id member (v3-verified) — tasks 1.4/1.5 must fetch/carry parent_id with a root guard (W5).**
- Variant B live check INLINED at 2106-2108; 665-695 helper dead/test-only.
- **`report_message_id` is NOT NULL today (models.py:144-147); both claim lanes index it (repository.py:222, 267-345) — NULL-branch + grep-audit required (C4).**
- **`dependency_watchers` (dependency_bus/models.py:100-180): `source_task_id` = child's TASK id, `target_instance_id` = parent, `state` PENDING/FIRED/CANCELLED, all indexed (source+state, target+state) — the FIRED-exclusion join for the no-row backstop is driver-neutral WITHOUT JSON parsing (child's task ids from the tasks table); `watcher_metadata` JSONB holds child_id but is driver-dependent to query (C3 design).**
- Enum values UPPERCASE in storage (models.py:56-58; dependency_bus/models.py:69-71 same convention) — C1 case parity.
- graph.py:276-277 fast-path confirms FM-2 post-restart stranding.
- Exactly-once: claim guards + S3; child-keyed lock insufficient across recovery actors → triple index + IntegrityError absorption at INSERT site.
- Mirror: report_injections SQL guards on state='PENDING' (task/repository.py:951) → transition-before-reconciliation binding.
- Sweep precedents: StaleTaskRecovery (5072-5091), has_instance_busy (task/repository.py:448+; widened api.py:1020), TOCTOU re-check (job_queue_service.py:1320-1520), crash-recovery endpoint (api.py:1140-1400).
- Hard constraints encoded: PG-primary, `_ensure_postgres_columns`, JAFP, no CancellationReason pause member, Pause-First Then Quiesce where applicable, reconcile_turn_mirror authority, MVP growth rule.

## Open Questions

1. ~~Sweep cadence~~ — RESOLVED: PERIODIC (interval default 300s — S-e, tunable) + boot run; 10-min age guard kept.
2. ~~`count_pending_for_parent`~~ — RESOLVED: PENDING ∪ DEFERRED.
3. ~~FM-12~~ — RESOLVED: document assumption; terminal parents excepted via ORPHAN lane (W1).
4. ~~FM-13~~ — RESOLVED: `recovery_attempted_at` permanent reconciler (W9: no one-time lanes).
5. ~~`_is_instance_paused` signature~~ — RESOLVED: async def; FM-11 window real.
6. Option B trigger — open pending 3.9(a): shield insufficiency (incl. crash-mid-shield) activates the pre-approved pause-cascade hook.
7. ~~Partial unique index feasibility~~ — RESOLVED (precedent + W3 pre-check + C1 uppercase predicate + IntegrityError-raises test).
8. No-row backstop cost on very large DBs — the designed query (task 2.4) is index-assisted and batch-capped; if field data shows pressure, per-lane kill-switches + a one-time operational script remain the fallback.

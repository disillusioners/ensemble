# Execution Plan: Decouple Job / Task / Message / Correlation — Accelerated

| Field | Value |
|---|---|
| **Status** | DETAILED PLAN (post-review, v2 — incorporates reviewer round-2 fixes) |
| **Review reference** | `docs/plans/decouple-review.md` (2026-06-20) |
| **Original plan** | `docs/plans/decouple-job-task-message-correlation.md` (v2) |
| **Mode** | **Accelerated**: Phase D (Dependency Bus) deferred to release N+1. Release N ships the bug fix + single dispatcher. |
| **Scope** | Same as v2, minus Phase D deliverables |
| **Total effort** | **~4 weeks** (realistic, after review round-1 + round-2 adjustments) |
| **Critical path** | Phase A (8–10 days) → Phase B (2.5 days) → Phase C (2.5 weeks) |
| **Reviewer round-2** | 6 critical fixes (C1–C6) + 10 warnings (W1–W11) incorporated. See `docs/plans/decouple-review.md` §9. |

---

## 1. Objective

Reach the **single-dispatcher architecture** (WorkerPool execution, JobQueue scheduling, CorrelationManager authoritative completion) in one release, closing the premature-completion bug class, with **no shadow dwell periods** and **CI gates replacing production validation**. Dependency Bus (the final architectural polish) is deferred to a follow-up release, allowing the critical production bug fix + structural simplification to ship in **~4 weeks** instead of **~5.5–6 weeks**.

---

## 2. Scope Assessment

**MEDIUM+** — 3 phases, ~15–20 files touched, 3–4 new test packs, significant behavioral changes to hot paths (JobProcessor admission, child cascade).

**Key changes from v2:**
- Phase D (Dependency Bus) removed from this release
- Phase A scope expanded to all 18 files with `waiting_for` reads
- Phase A scope expanded with `rebuild_from_db()` crash-safety audit (reviewer C5)
- Phase A7 re-targeted to actual `FOR UPDATE` gate (lines 1230–1320), not docstrings (reviewer C1)
- Phase C-M4 re-scoped as documentation (no behavior change)
- Phase C-M6 expanded with threading-model decision + serialization test (reviewer C2)
- Column drop (D10) removed; `waiting_for` retained as dead column for this release
- Kill-switch test pack added (reviewer C4)
- Pause/terminate matrix test added as C-M5 precondition (reviewer C6)
- `cross_dispatcher_*` tests either created or criterion removed (reviewer C3)

---

## 3. Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|--------------|----------|-----------|
| **A** | Authority & visibility | Gate `waiting_for` behind flag across ALL 18 files; add `DEBUG_COMPLETION_INVARIANT` runtime check; CM is authoritative; audit `rebuild_from_db()` crash-safety | None | — | 8–10 days |
| **B** | Close the bug class | Route `watch_job`/`job_continue` through CM; all 3 repro variants structurally impossible | Phase A | loose | 2.5 days |
| **C** | Single dispatcher | Unify enqueue to WorkerPool-only; JobQueue is scheduling-only; gate collapsed to `asyncio.Lock` with threading model proven | Phase B | tight (within C) | 2.5 weeks |

**Total:** ~4 weeks (realistic, post-review round-1 + round-2 adjustment).

---

## 4. Coupling Assessment

| Phase pair | Coupling type | Justification | Can overlap? |
|---|---|---|---|
| A → B | loose | B adds `pending_jobs` to CM that A instrumented; no shared files touched simultaneously | **Yes** — B can start after A3–A7 (core gating) lands, while A8–A10 (docs/tests) finalize |
| B → C | independent | B touches `correlation_manager.py`, `job_feedback_observer.py`, `job_queue.py`; C touches `instance_messaging.py`, `job_processor.py`, `execution_gate.py` | **No practical overlap** — B is 2 days, C is 2.5 weeks |
| Within C | tight | C-M4, C-M5, C-M6 touch the same files (`instance_messaging.py`, `job_feedback_observer.py`, `message_processing_pipeline.py`, `execution_gate.py`) | **No** — inherently serial; one coder, sequential PRs or one large PR |

**Conclusion:** The chain is A → B → C with no exploitable parallelism. B can pipeline with A's tail (docs/tests), but the net savings is ~1 day.

---

## 5. Task Breakdown by Phase

---

## Phase A: Authority & visibility (8–10 days)

### Objective

ADR-011 is enforced in code. The premature-completion bug class is structurally impossible under `USE_LEGACY_WAITING_FOR_CASCADE=OFF`. Divergence between CM and `waiting_for` is observable at runtime. All 18 files with `waiting_for` control-flow reads are explicitly gated. CM crash-recovery contract is defined and tested. Kill-switch (flag ON) path is regression-tested.

### Coupling

- **Depends on**: None
- **Coupling type**: N/A (root phase)
- **Shared files with other phases**: `correlation_manager.py` (B adds `pending_jobs`; D8 in follow-up removes methods)
- **Shared APIs/interfaces**: `CorrelationManager.is_complete()`, `get_pending_count()` — stable across phases
- **Why this coupling**: B builds on CM authority that A establishes

### Context

- **Previous phase**: None
- **Key decisions**: `waiting_for` is deprecated as control-flow (ADR-011); retained as rebuild cache only
- **Known issues**: 13 of 18 files with `waiting_for` reads are NOT in v2's Phase A scope; this plan fixes that
- **Reviewer round-2 additions**: A0a (`rebuild_from_db` crash-safety), A7 re-targeted to `FOR UPDATE` gate, A8 is a hard decision (throw), A14 kill-switch test pack, A15 in-flight flag-flip test, A12 expanded with register-window proof + crash-recovery tests

### Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **A0** | Baseline audit | Run invariant check against current `main`; produce list of all `waiting_for` control-flow reads (55 sites); categorize: (1) mutation (increment/decrement), (2) cascade decision (`==0`), (3) deferral decision (`>0`), (4) rebuild-only (cache reads) | `tests/test_completion_authority_invariant.py` (new) |
| **A0a** | Audit `rebuild_from_db()` crash-safety **[reviewer C5]** | Read `correlation_manager.py:493–584`. The W2 fix clears `_pending = {}` before rebuild, but there is **no test for stale-entry cleanup after a crash mid-rebuild** (register arrives between `clear` and the loop). Define CM's crash-recovery contract: what happens if the daemon restarts while a parent has `waiting_for > 0` and in-flight children? Document: (a) the rebuild reads `waiting_for > 0` parents from DB, (b) queries pending messages per child, (c) reconstructs `_pending`. Add test for: restart with stale entries, restart with zero children but `waiting_for > 0` (orphan count), restart with concurrent register. If bugs found, fix them here — they are Phase A blockers. | `daemon/services/correlation_manager.py:493–584`, `tests/test_correlation_manager.py` |
| **A1** | Create authority doc | Document three authorities (CM, `waiting_for`, `SELECT COUNT(*)` fallback), invariant, every call site with rationale, ADR-011 reference | `docs/architecture/completion-authority.md` (new, ≤200 lines) |
| **A2** | Add config flags | `USE_LEGACY_WAITING_FOR_CASCADE` (default `False`), `DEBUG_COMPLETION_INVARIANT` (default `False` in prod, `True` in dev/CI). **[reviewer W9]** Document feature-flag interaction matrix in `docs/configuration/`: which combinations are valid, which are undefined (e.g. `USE_LEGACY_WAITING_FOR_CASCADE=ON` + `DEBUG_COMPLETION_INVARIANT=ON` is valid; `USE_LEGACY_WAITING_FOR_CASCADE=OFF` + CM not initialized is a hard error per A8). | `daemon/config.py`, `docs/configuration/` |
| **A3** | Instrument CM with invariant check | On every `resolve_response`/`register_message_send`, read current `waiting_for` from same session; log `CM_WAITING_FOR_DIVERGENCE` on mismatch. **[reviewer W7]** Define divergence triage decision tree: (1) <10 divergences/hour = investigate in next sprint, (2) 10–100/hour = page on-call, check for new `waiting_for` mutation sites, (3) >100/hour = flip kill switch immediately. **[reviewer W11]** Acceptable divergence rate is **<10/hour** (background noise from timing), not zero-tolerance. Zero is the goal; <10/hour is the operational threshold. | `daemon/services/correlation_manager.py` |
| **A4** | Gate `child_reports.py` cascade | Wrap `waiting_for` SQL decrement + `if waiting_for == 0` cascade in `if USE_LEGACY_WAITING_FOR_CASCADE:`; when OFF: call `notify_corr_resolve`, use `cm.get_pending_count()` for cascade decision, skip `WAITING_CHILDREN` status | `daemon/services/child_reports.py` (lines ~486–626, ~1300) |
| **A5** | Gate `instance.py` send_message | Wrap `waiting_for` SQL increment + M0 parent-revive `UPDATE` in flag; when OFF: only `notify_corr_register` | `daemon/tools/instance.py` |
| **A6** | Gate `instance_lifecycle.py` pause/resume | Wrap `waiting_for` reset in `pause_instance_cascade`/`resume_instance_cascade` in flag; when OFF: CM re-registers via `rebuild_from_db()` | `daemon/services/instance_lifecycle.py` |
| **A7** | Re-target and gate the `FOR UPDATE` row-lock gate **[reviewer C1 — CRITICAL]** | **The actual gate is at `job_feedback_observer.py:1230–1320` in `_finalize_job_db_sync`** — a `SELECT waiting_for FROM instances WHERE instance_id = :iid FOR UPDATE` (PostgreSQL) or non-locking SELECT (SQLite) that holds a row lock to prevent `send_message` from writing `waiting_for=1` between the read and the finalization UPDATE. This is NOT the `WriteGuardSession` docstring at lines 65/106/127/621. Wrap this entire `SELECT ... FOR UPDATE` block (lines 1230–1320) in `if USE_LEGACY_WAITING_FOR_CASCADE:`. When OFF: replace with `cm.is_complete(instance_id)` check. **CRITICAL precondition**: before removing the `FOR UPDATE` gate, verify that CM's `resolve_response` callback chain closes the register-before/increment-after window. The window is: `send_message` increments `waiting_for` BEFORE registering the CM correlation. Under flag OFF (A5), `waiting_for` is not incremented, so the window is structurally closed — but **add a test (A12) that proves the window is closed** before A7 removes the gate. | `daemon/services/job_feedback_observer.py:1230–1320` (the `FOR UPDATE` gate in `_finalize_job_db_sync`) |
| **A8** | `SELECT COUNT(*)` fallback: DECISION, not fork **[reviewer W1]** | In `child_reports.py:657` (the `cm is None` fallback path), **throw if CM is None when `USE_LEGACY_WAITING_FOR_CASCADE=OFF`**. This is a hard error, not a graceful degradation. The `SELECT COUNT(*)` TOCTOU path (Race #3) is the bug we're fixing; it must not be reachable under flag OFF. Document in the authority doc (A1) that graceful degradation with flag OFF is unsupported — CM must be initialized. | `daemon/services/child_reports.py:657–680` |
| **A9** | Audit remaining 13 files | Grep for `waiting_for` reads in the 13 files not in A4–A7; gate any control-flow reads; document rebuild-only reads | `api.py`, `manager.py`, `models/instance.py`, `opencode/state.py`, `repositories/instance/{models,repository}.py`, `repositories/task/repository.py`, `routers/instances.py`, `services/{error_reporting,job_processor,job_queue_service,message_job_handler,message_processing_pipeline}.py` |
| **A10** | Update architecture doc | Pointer to `completion-authority.md` from `message-processing-and-correlation.md` §5 | `docs/architecture/message-processing-and-correlation.md` |
| **A11** | Create invariant test pack | 10 tests: every `waiting_for` mutation site has matching CM call OR documented cache-only; every `waiting_for` control-flow read is gated by flag OR documented cache-only; every `pending_count` read consistent with `CM.is_complete()` | `tests/test_completion_authority_invariant.py` (new, ~10 tests, 2 min) |
| **A12** | Create shadow test pack + register-window proof | 20 tests: full M0 suite under flag OFF (Variants A and C pass); CM `is_complete` returns True iff `waiting_for == 0` AND `pending_count == 0` (50 random parent state fixtures); pause/resume with flag OFF preserves CM pending set; `waiting_for` consistent with CM at end of every test. **[reviewer C1 — adds]** **Critical test**: prove the register-before/increment-after window is closed under flag OFF — spawn child, verify CM registers before any finalize can fire, verify `_finalize_job_db_sync` defers correctly without the `FOR UPDATE` gate. **[reviewer C5 — adds]** **Crash-recovery tests**: restart with mid-flight parents, verify `rebuild_from_db()` reconstructs correct `_pending` state. | `tests/test_correlation_authority_shadow.py` (new, ~22 tests, 2 min) |
| **A13** | Update premature completion regression tests | Assert Variants A and C pass under `USE_LEGACY_WAITING_FOR_CASCADE=OFF` | `tests/postgres/test_premature_completion_regression.py`, `tests/postgres/test_premature_completion_edge_cases.py` |
| **A14** | Create kill-switch test pack **[reviewer C4 — CRITICAL]** | 15 tests: exercise the **full legacy path with `USE_LEGACY_WAITING_FOR_CASCADE=ON`**. The kill switch is the sole rollback for Phase A/B. If the legacy path regresses during refactoring (18 files touched), the kill switch won't save you because nothing tests it. Tests: (1) `waiting_for` increment/decrement under flag ON, (2) cascade decision via `waiting_for == 0` under flag ON, (3) `FOR UPDATE` gate active under flag ON, (4) `SELECT COUNT(*)` fallback active under flag ON, (5) M0 parent-revive under flag ON, (6) full spawn → child completion → parent cascade under flag ON. All 18 gated files must pass under BOTH flag states. | `tests/test_kill_switch_legacy_path.py` (new, ~15 tests, 2 min) |
| **A15** | In-flight-during-flag-flip test **[reviewer W10]** | PostgreSQL test: daemon restart with mid-flight parents (parent spawned children, children running), flip flag OFF, verify CM picks up from `rebuild_from_db()` without dropping in-flight correlations. | `tests/postgres/test_inflight_flag_flip.py` (new, ~5 tests, 5 min) |

### Acceptance criteria

- [ ] The premature-completion bug class is structurally impossible under `USE_LEGACY_WAITING_FOR_CASCADE=OFF`.
- [ ] Legacy path preserved as a kill switch **AND tested** (A14 kill-switch pack passes under flag ON).
- [ ] M0's band-aid — the `FOR UPDATE` row-lock gate at lines 1230–1320 — is gated and removed under flag OFF (A7).
- [ ] **A7 precondition met**: A12 test proves the register-before/increment-after window is closed under flag OFF BEFORE A7 removes the `FOR UPDATE` gate (reviewer C1).
- [ ] All 18 files with `waiting_for` reads are audited and gated (A0 baseline + A9 audit).
- [ ] `SELECT COUNT(*)` TOCTOU fallback throws if CM is None when flag OFF (A8 — hard error, not graceful degradation).
- [ ] A developer introducing a new completion source gets a CI failure (A11 invariant pack).
- [ ] `DEBUG_COMPLETION_INVARIANT` log lines appear in dev/CI on any divergence; absent means invariant holds.
- [ ] `rebuild_from_db()` correctly reconstructs CM pending set on resume (A0a audit + A12 crash-recovery tests).
- [ ] CM crash-recovery contract documented (A0a).
- [ ] In-flight-during-flag-flip test passes on PostgreSQL (A15).

### Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Audit (A0/A9) finds 10+ ungated sites requiring rework | Medium | High | A0 baseline is run *before* coding starts; A9 is budgeted as 2–3 days of the 8–10 day estimate |
| `rebuild_from_db()` has dict-merge/stale-state bugs (problem #4 from context) | Low | High | A0a audit is a Phase A precondition; if bugs found, fix before flag flip; A12 crash-recovery tests verify |
| Removing the `FOR UPDATE` gate (A7) reintroduces the premature-completion bug | Medium | Critical | **A12 register-window proof test must pass BEFORE A7**. The window is structurally closed by A5 (flag OFF skips `waiting_for` increment), but the test proves it empirically (reviewer C1) |
| `SELECT COUNT(*)` fallback (A8) is the Race #3 vector that persists | Medium | Medium | A8 makes flag OFF + CM None a **hard error** (throw), not a fallback (reviewer W1) |
| Kill switch regresses during 18-file refactoring | High | High | A14 kill-switch test pack exercises the full legacy path under flag ON (reviewer C4) |
| `rebuild_from_db()` clears `_pending` but a concurrent register lands between clear and rebuild | Low | Medium | A0a audit + A12 crash-recovery test for concurrent register during rebuild |

### Success criteria

- `tests/test_completion_authority_invariant.py` passes (10/10)
- `tests/test_correlation_authority_shadow.py` passes (22/22) including register-window proof and crash-recovery tests
- `tests/test_kill_switch_legacy_path.py` passes (15/15) — legacy path fully tested under flag ON (reviewer C4)
- `tests/postgres/test_premature_completion_regression.py` Variants A/C pass with flag OFF
- `tests/postgres/test_inflight_flag_flip.py` passes (5/5) — restart with mid-flight parents (reviewer W10)
- No `CM_WAITING_FOR_DIVERGENCE` logs in dev/CI with flag OFF (or <10/hour background noise — reviewer W11)

---

## Phase B: Close the bug class (2.5 days)

### Objective

The `watch_job`/`job_continue` path is routed through CM. All three repro variants from the 2026-06-20 investigation are structurally impossible. The bug class is closed.

### Coupling

- **Depends on**: Phase A (CM must be authoritative before adding `pending_jobs`)
- **Coupling type**: loose (B adds to CM that A instrumented)
- **Shared files with other phases**: `correlation_manager.py` (adds `pending_jobs`; D8 in follow-up removes methods)
- **Shared APIs/interfaces**: `CorrelationManager.is_complete()` extended to check both `pending` and `pending_jobs`
- **Why this coupling**: B extends CM's completion check; no behavioral change to existing CM usage

### Context

- **Previous phase completed**: Phase A gated `waiting_for` writes and made CM authoritative
- **Key decisions**: `watch_job`/`job_continue` are fire-and-forget (C2-PartB from v2 fix); they don't increment `waiting_for`
- **Known issues**: v2 plan had wrong file path (`daemon/tools/job.py` → actual `daemon/tools/job_queue.py`)

### Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **B1** | Add `pending_jobs` to CM | Add `pending_jobs: dict[parent_id, set[child_job_id]]` to `ParentCorrelation` storage; `is_complete(parent_id)` returns True only when both `pending` and `pending_jobs` are empty; `handle_correlation_complete` fires only when both reach zero | `daemon/services/correlation_manager.py` |
| **B2** | Add job correlation helpers | `notify_corr_register_job(parent_id, child_job_id)` and `notify_corr_resolve_job(parent_id, child_job_id)` — CM-API-level, not lifecycle | `daemon/services/correlation_manager.py` |
| **B3** | Route `watch_job` through CM | Wrap the `watch_job` follow-up call in `notify_corr_register_job(parent_id=current_instance_id, child_job_id=child_job.job_id)` | `daemon/tools/job_queue.py:610` (NOT `job.py` — v2 plan error) |
| **B4** | Route watched job terminal through CM | When existing terminal-event emission for a watched job (`processing → completed` / `processing → failed`) fires, add `notify_corr_resolve_job(parent_id=parent_instance_id, child_job_id=job.job_id)` call | `daemon/services/job_feedback_observer.py` |
| **B5** | Create watch_job integration test pack | 10 tests: Variant B regression test from M0; multiple `watch_job` calls from same parent (only fires when all resolved); watched job that fails → parent's terminal is `error`; watched job cancelled → parent's terminal is `cancelled` (document policy choice) | `tests/test_watch_job_integration.py` (new, ~10 tests, 2 min) |

### Acceptance criteria

- [ ] All three repro variants from the 2026-06-20 investigation are structurally impossible.
- [ ] `tests/test_premature_completion_regression.py` and `tests/test_premature_completion_edge_cases.py` are fully green.
- [ ] The premature-completion bug class is **closed**.

### Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| `watch_job` may not have a clear "this is the parent instance" context | Low | Medium | Audit call site; confirm `current_instance_id` is the parent. If unclear, file a follow-up rather than guess. |

### Success criteria

- `tests/test_watch_job_integration.py` passes (10/10)
- All three repro variants (A, B, C) from the investigation are regression-tested and pass

---

## Phase C: Single dispatcher (2.5 weeks)

### Objective

One enqueue function. JobQueue is scheduling only. WorkerPool is the only execution path. Execution Gate is ~40 lines (in-process `asyncio.Lock`).

### Coupling

- **Depends on**: Phase B (bug class closed before restructuring dispatch)
- **Coupling type**: tight (within C — C-M4, C-M5, C-M6 touch the same files)
- **Shared files with other phases**: None (C is the final phase of this release)
- **Shared APIs/interfaces**: `ExecutionGateService.run(instance_id, holder_id, holder_kind, work_fn)` — signature preserved, implementation changes
- **Why this coupling**: C-M4 → C-M5 → C-M6 is inherently serial; one coder, sequential work within the phase

### Context

- **Previous phase completed**: Phase B closed the premature-completion bug class via CM
- **Key decisions**: `enqueue_message_via_jq` is NOT a thin wrapper (C1 re-scoped); real unification is C-M5 (routing JobProcessor through observer)
- **Known issues**: v2 plan's C1 framing is misleading; C-M4 is a documentation/no-op step
- **Reviewer round-2 additions**: C4.5 (pause/terminate matrix precondition), C12a/C12b/C18 (threading-model decision + serialization tests), C13 expanded to update/delete old gate tests, W2 (performance SLO), C3 (cross_dispatcher criterion removed)

### Tasks

#### C-M4: Documentation step (re-scoped — 0.5 day)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **C1** | Document the alias (no behavior change) | `enqueue_message_via_jq` remains a separate function (writes `JobItem`, not `Task`). Add deprecation log line (`LOG_LEVEL >= INFO`) on every call. No wrapper. The unification happens at C-M5. | `daemon/services/instance_messaging.py:1486` |
| **C2** | Create path equivalence test pack | 10 tests: runs the same scenario 100 times through each entry point (HTTP, agent tool, child completion report, error report, source, scheduler) and asserts identical observable behavior for *cross-instance handoff only* (the one path that still uses JobQueue) | `tests/test_dispatcher_path_equivalence.py` (new, ~10 tests, 2 min) |
| **C3** | Create path invariants test | 1 test: greps entire `daemon/` tree for `enqueue_message_via_jq(`; asserts the only call sites are the documented ones (HTTP API, `job_queue.py` tool). Fails build on any new direct call. | `tests/test_dispatcher_path_invariants.py` (new, 1 test, 30s) |
| **C4** | Update architecture doc | Section 4 ("How a Message Flows") updated to reflect that JobQueue is now scheduling-only for cross-instance handoff; link to `docs/plans/unified-dispatcher.md` §5.2 | `docs/architecture/message-processing-and-correlation.md` |

#### C-M5: Route JobQueue admission through observer (1.5 weeks)

**[reviewer W2 — precondition]** Before C5, define a performance SLO for the observer hot-loop path: p99 admission latency ≤ 50ms under 100 concurrent admissions. The observer was designed for rare cross-instance handoff; making it the admission path for ALL HTTP message work changes its load profile fundamentally. If SLO is not met, implement per-instance admission throttling (same pattern as WorkerPool's `requeue_task_with_backoff`) before C11 flag flip.

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **C4.5** | Pause/terminate matrix test pack **[reviewer C6 — CRITICAL precondition]** | 20 tests: snapshot the pause/terminate discrimination matrix from BOTH `MessageJobHandler.handle` (JobQueue path) AND `ProcessMessageProcessor.process` (WorkerPool path). For each (starting state × cancel reason × expected terminal state × expected job/task status), assert the unified processor produces the correct result. **This is the highest-risk merge in the entire plan** (438 lines of pause/terminate logic). This test must be created and PASSING (against current code, both paths) BEFORE any C-M5 code changes. Run it again after C11 to verify the unified path matches. | `tests/test_pause_terminate_matrix.py` (new, ~20 tests, 3 min) |
| **C5** | Add `USE_LEGACY_JOBQUEUE_DISPATCH` flag | Default `False`. Exists for duration of C-M5 only; removed in C11. | `daemon/config.py` |
| **C6** | Extend `JobFeedbackObserver` for local admission | When `JobProcessor` admits a `JobItem` of `job_type='message'`: observer writes a `Task` row pointing at the same `message_id`; observer calls `worker_pool.notify_work()`; `JobItem` is marked `PROCESSING` (status only — execution is in the Task table). This is the only path that writes a `Task` row for message work. | `daemon/services/job_feedback_observer.py` |
| **C7** | Update `JobProcessor` dispatch | Under `USE_LEGACY_JOBQUEUE_DISPATCH=ON`: keep current behavior (calls `MessageJobHandler.handle` for local work). Under `OFF`: call observer (C6). | `daemon/services/job_processor.py:395, 672, 704` |
| **C8** | Demote `MessageJobHandler.handle` | Under flag OFF: `handle` is a no-op for the local path. File not deleted yet (Phase D-M8 in follow-up). Remains a thin adapter: delegates to observer for local work, handles cross-instance handoff for remote work. | `daemon/services/message_job_handler.py:107` |
| **C9** | Add `dispatch_path` structured log metric | `dispatch_path=jobqueue_local` for work admitted by `JobProcessor` through the observer; `dispatch_path=jobqueue_cross_node` for work bounced from another node; `dispatch_path=workerpool_direct` for work via `enqueue_message` without a `JobItem` row. | All relevant log lines |
| **C10** | Create unified dispatcher shadow test pack | 15 tests: with `USE_LEGACY_JOBQUEUE_DISPATCH=OFF`, asserts observer's path produces the same result for 50 randomized scenarios; `JobItem` rows for `job_type='message'` transition PROCESSING → COMPLETED with Task table as source of truth; cross-instance handoff unaffected (runs both flag states, cross-instance path must work in both) | `tests/test_unified_dispatcher_shadow.py` (new, ~15 tests, 5 min) |
| **C11** | Flip flag permanently, remove flag | After C4.5 (matrix test) AND C10 (shadow test) pass: `MessageJobHandler` becomes purely cross-instance handoff; `JobProcessor` no longer calls `MessageJobHandler.handle` for local work; `USE_LEGACY_JOBQUEUE_DISPATCH` flag is removed from `daemon/config.py`. **Re-run C4.5 after flip** to verify unified path matches both original paths. | `daemon/config.py`, `daemon/services/job_processor.py`, `daemon/services/message_job_handler.py` |

#### C-M6: Collapse gate to asyncio.Lock (1 week)

**[reviewer C2 — CRITICAL precondition]** The existing `concurrency_atomic_unit_test` (86 tests) tests CM-level locks and has **ZERO** tests exercising the ExecutionGate or its threading model. The existing ExecutionGate tests (`tests/unit/services/test_execution_gate.py`, `tests/test_resume_gate.py`) all test the **DB-backed lease** that C-M6 deletes. C17 would pass 86/86 even if the asyncio.Lock collapse has a broken threading model. Therefore C12a and C12b are mandatory BEFORE C12.

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **C12a** | Threading model decision document **[reviewer C2 — CRITICAL]** | Write a decision doc answering: (1) Which event loop owns the per-instance `asyncio.Lock`? (2) How do 4 WorkerPool threads contend via `MainLoopBridge`? (3) Is the `asyncio.Lock` acquired on the main loop or the worker thread? (4) What happens when two threads call `gate.run()` for the same instance — does the second block on the main loop, or does it get `LeaseContention`? (5) Is `asyncio.Lock` the right primitive, or do we need `threading.Lock` (since WorkerPool is threaded) or a hybrid? The answer determines C12's implementation. **This must be written and reviewed BEFORE C12.** | `docs/architecture/execution-gate-threading-model.md` (new) |
| **C12b** | 2-worker-thread serialization test **[reviewer C2 — CRITICAL]** | Test that proves the asyncio.Lock (or whatever primitive C12a decides on) correctly serializes 2 WorkerPool threads processing the same instance. Spawn 2 threads, both call `gate.run(instance_id, ...)`, verify only one runs `work_fn` at a time. Test under the OLD (DB-backed lease) implementation first to capture the contract, then under the NEW (asyncio.Lock) implementation. If the new implementation can't serialize 2 threads, the collapse is blocked. | `tests/test_gate_threading_serialization.py` (new, ~5 tests, 2 min) |
| **C12** | Replace DB-backed lease with `asyncio.Lock` | Implement per the C12a threading-model decision. Signature preserved: `run(instance_id, holder_id, holder_kind, work_fn)`. `holder_id`/`holder_kind` ignored (for backward compat with call sites). **Precondition: C12a decision doc written, C12b serialization test passes under old implementation.** | `daemon/services/execution_gate.py` (707 lines → ~40 lines) |
| **C13** | Delete dead code | `recover_stale_leases` startup call, `LeaseContention` exception, `LeaseLostError` exception, `_lease_heartbeat_loop` background task, heartbeat escalation logic, `LeaseHolderKind` enum, `instance_execution_leases` table migration (`20260614_000002_create_instance_execution_leases.sql`), `daemon/repositories/execution_lease/` directory. **Also update/delete existing execution_gate tests** (`tests/unit/services/test_execution_gate.py`, `tests/test_resume_gate.py`) that test the DB-backed lease — these tests import `ExecutionLeaseRepository`, `LeaseHolderKind`, `LeaseContention` which no longer exist after C13. | `daemon/services/execution_gate.py`, `daemon/repositories/execution_lease/`, migration file, `tests/unit/services/test_execution_gate.py`, `tests/test_resume_gate.py` |
| **C14** | Update `MessageProcessingPipeline` call site | Change the one surviving call site to `async with self._gate._lock_for(instance_id): await work_fn()` (or per C12a decision). | `daemon/services/message_processing_pipeline.py:398` |
| **C15** | Add module docstring | Verbatim from v1 M6-6e: documents that this gate serializes per instance within a single process; no cross-process coordination; multi-node deployment is a follow-up. | `daemon/services/execution_gate.py` |
| **C16** | Update architecture doc | Section 6 ("ExecutionGate in Depth") updated to reflect new implementation. | `docs/architecture/message-processing-and-correlation.md` |
| **C17** | Run concurrency/atomic unit test pack **[reviewer C2 — re-labeled]** | 86 tests from `PACKS.md` (`concurrency_atomic_unit_test`): these test CM-level locks, NOT the gate. They must still pass (no CM regression), but they are **NOT sufficient** to verify the gate collapse. C12b is the gate-specific gate test. | All 7 test files (86 tests total) |
| **C18** | **Run C12b serialization test under NEW implementation [reviewer C2]** | After C12–C13 land, re-run `tests/test_gate_threading_serialization.py` and verify it passes under the asyncio.Lock implementation. **This is the gate for C-M6 merge.** | `tests/test_gate_threading_serialization.py` (5 tests) |

### Acceptance criteria

- [ ] One enqueue function documented (C3 grep test enforces).
- [ ] JobQueue is scheduling layer, not execution layer.
- [ ] WorkerPool is the only execution path.
- [ ] ~660 lines removed from `execution_gate.py` + repos.
- [ ] `concurrency_atomic_unit_test` 86/86 pass (C17 — CM-level locks, no regression).
- [ ] **`test_gate_threading_serialization.py` 5/5 pass under asyncio.Lock** (C18 — reviewer C2, the actual gate-collapse gate test).
- [ ] **`test_pause_terminate_matrix.py` 20/20 pass** (C4.5 — reviewer C6, pause/terminate discrimination verified for unified path).
- [ ] Pause/terminate discrimination verified: unified processor matches both original paths (C4.5 re-run after C11).
- [ ] **[reviewer C3]** ~~`cross_dispatcher_*` race tests pass~~ — **REMOVED** (these tests do not exist in `tests/`). Replaced by C4.5 (pause/terminate matrix) and C10 (unified dispatcher shadow) which provide equivalent coverage.

> **[reviewer C3 — note]** The v2 plan and round-1 execution plan both referenced "`cross_dispatcher_*` race tests pass" as an acceptance criterion. No such tests exist in `tests/`. The bug doc `docs/bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md` exists but has no associated test file. This criterion is removed. Coverage for the cross-dispatcher race is now provided by C4.5 (pause/terminate matrix snapshot from both paths) and C10 (shadow equivalence between old and new dispatch paths).

### Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| C-M5 hot loop on the observer (cross-instance handoff was designed for rare events) | Medium | High | **[reviewer W2]** Performance SLO defined before C5 (p99 ≤ 50ms under 100 concurrent admissions). Profile first. Observer's polling is event-driven (dispatch bus notify), not interval-driven. If hot, add per-instance admission throttling before C11 flag flip. |
| C-M6 re-creates the original race if C-M5 is incomplete | Medium | High | C17 gate. Do not merge C-M6 until C-M5 (C11) is done. |
| **asyncio.Lock threading model for 4-thread WorkerPool is unclear or broken** | **Medium** | **High** | **[reviewer C2]** C12a threading-model decision doc written before C12. C12b serialization test proves 2 threads serialize correctly under old implementation (contract capture), then under new implementation (asyncio.Lock). C18 is the merge gate. |
| `MessageJobHandler`'s pause/terminate discrimination may differ from `ProcessMessageProcessor`'s | Medium | High | **[reviewer C6]** C4.5 pause/terminate matrix test pack (20 tests) created BEFORE any C-M5 code changes. Must pass against current code first, then re-run after C11. |
| Existing execution_gate tests break when DB-backed lease is deleted | High | Medium | C13 explicitly updates/deletes `tests/unit/services/test_execution_gate.py` and `tests/test_resume_gate.py` which import `ExecutionLeaseRepository`, `LeaseHolderKind`, `LeaseContention`. |

### Success criteria

- `tests/test_pause_terminate_matrix.py` passes (20/20) — **before AND after** C-M5 changes (reviewer C6)
- `tests/test_dispatcher_path_equivalence.py` passes (10/10)
- `tests/test_dispatcher_path_invariants.py` passes (1/1)
- `tests/test_unified_dispatcher_shadow.py` passes (15/15)
- `tests/test_gate_threading_serialization.py` passes (5/5) under asyncio.Lock — **the C-M6 merge gate** (reviewer C2)
- `concurrency_atomic_unit_test` 86/86 passes (CM-level locks, no regression)
- `execution_gate.py` is ~40 lines with `asyncio.Lock` implementation
- `instance_execution_leases` table and migration are deleted
- Observer hot-loop meets p99 ≤ 50ms SLO under 100 concurrent admissions (reviewer W2)

---

## 6. Critical Path

```
Phase A (8–10 days)
  │
  ├── A0 baseline audit (0.5 day)
  ├── A0a rebuild_from_db crash-safety audit (1 day) [reviewer C5]
  ├── A1–A3 core CM instrumentation + divergence triage (2 days)
  ├── A4–A6 gate 3 files (1.5 days)
  ├── A7 re-target FOR UPDATE gate [reviewer C1] (1 day)
  ├── A8 SELECT COUNT(*) → throw decision (0.5 day)
  ├── A9 audit remaining 13 files (2–3 days)
  ├── A10–A13 docs + shadow tests (2 days)
  ├── A14 kill-switch test pack (1 day) [reviewer C4]
  └── A15 in-flight flag-flip test (0.5 day) [reviewer W10]
  │
Phase B (2.5 days, can pipeline with A tail)
  │
Phase C (2.5 weeks)
  ├── C-M4 documentation (0.5 day)
  ├── C-M5 route admission (1.5 weeks)
  │   ├── C4.5 pause/terminate matrix test (1 day) [reviewer C6, PRECONDITION]
  │   ├── Performance SLO + throttle design (0.5 day) [reviewer W2]
  │   ├── C5–C9 observer extension (4 days)
  │   ├── C10–C11 shadow tests + flag flip (3 days)
  │   └── Re-run C4.5 after flip (0.5 day)
  └── C-M6 collapse gate (1 week)
      ├── C12a threading-model decision doc (1 day) [reviewer C2, PRECONDITION]
      ├── C12b serialization test under old impl (0.5 day) [reviewer C2]
      ├── C12–C15 implementation + docstring (2.5 days)
      ├── C13 update/delete old gate tests (0.5 day)
      ├── C16–C17 docs + concurrency tests (1 day)
      └── C18 serialization test under NEW impl — MERGE GATE [reviewer C2]
```

**Fastest path to new architecture:** A (core gating) → B (parallel with A tail) → C-M5 (real unification) → C-M6 (gate collapse). Total: **~4 weeks**.

---

## 7. Dependency Graph

```
A0 (baseline audit)
  │
  ├── A1 (authority doc)
  ├── A2 (config flags)
  ├── A3 (CM invariant)
  │
  ├── A4 (child_reports gate) ──┐
  ├── A5 (instance.py gate)     │
  ├── A6 (instance_lifecycle gate) │
  ├── A7 (job_feedback_observer gate) │
  ├── A8 (SELECT COUNT(*) gate)    │
  │                              │
  ├── A9 (audit remaining 13 files) │
  │                              │
  ├── A10 (architecture doc)     │
  ├── A11 (invariant test pack)  │
  ├── A12 (shadow test pack)     │
  └── A13 (premature completion tests) │
                                   │
                                   ▼
                              Phase A complete
                                   │
                                   ▼
                              Phase B (2 days)
                                   │
                                   ▼
                              Phase C (2.5 weeks)
```

**No parallelizable branches** — the chain is serial. B can overlap with A's tail (A10–A13), saving ~1 day.

---

## 8. Risk Mitigations

| Risk | Phase | Mitigation |
|------|-------|------------|
| Audit finds 10+ ungated `waiting_for` sites | A | A0 baseline *before* coding; A9 budgeted as 2–3 days |
| `rebuild_from_db()` has dict-merge/stale-entry bugs | A | **[reviewer C5]** A0a crash-safety audit is a Phase A precondition; A12 crash-recovery tests verify |
| Removing the `FOR UPDATE` gate reintroduces the bug | A | **[reviewer C1]** A12 register-window proof test must pass BEFORE A7. Window is structurally closed by A5 (flag OFF skips `waiting_for` increment) |
| `SELECT COUNT(*)` TOCTOU fallback persists | A | **[reviewer W1]** A8 makes flag OFF + CM None a hard error (throw), not a fallback |
| Kill switch regresses during 18-file refactoring | A | **[reviewer C4]** A14 kill-switch test pack exercises full legacy path under flag ON |
| `watch_job` parent context unclear | B | Audit call site; confirm `current_instance_id` is the parent |
| C-M5 hot loop on observer | C | **[reviewer W2]** Performance SLO (p99 ≤ 50ms) defined before C5; add throttling if needed |
| asyncio.Lock threading model unclear or broken | C | **[reviewer C2]** C12a decision doc before C12; C12b serialization test proves contract; C18 merge gate |
| Pause/terminate discrimination lost in merge | C | **[reviewer C6]** C4.5 matrix test (20 tests) before AND after C-M5 changes |
| Existing gate tests break when DB-lease deleted | C | C13 updates/deletes `test_execution_gate.py` and `test_resume_gate.py` |
| `concurrency_atomic_unit_test` 86/86 fails | C | C17 gate (CM-level locks); do not merge C-M6 until 86/86 passes |

---

## 9. Test Strategy

### Per-phase test packs

| Phase | New packs | Existing packs (must pass) | Notes |
|-------|-----------|----------------------------|-------|
| A | `completion_authority_invariant_test` (10), `correlation_authority_shadow_test` (22 incl. register-window proof + crash-recovery), `kill_switch_legacy_path_test` (15), `inflight_flag_flip_test` (5, postgres) | `premature_completion_regression_test` (3), `correlation_manager_unit_test` (40), `correlation_shadow_integration_test` (8) | A11/A12/A14/A15 are new; A13 updates existing postgres tests. **[reviewer W4]** All new packs registered in `PACKS.md` in their phase's PR. |
| B | `watch_job_integration_test` (10) | `premature_completion_regression_test` (3), `premature_completion_edge_cases` (postgres), `kill_switch_legacy_path_test` (15 from Phase A) | B5 is new. B must also pass under flag ON (kill switch). |
| C | `dispatcher_path_equivalence_test` (10), `dispatcher_path_invariants_test` (1), `unified_dispatcher_shadow_test` (15), `pause_terminate_matrix_test` (20), `gate_threading_serialization_test` (5) | `concurrency_atomic_unit_test` (86), `correlation_manager_unit_test` (40) | C4.5/C10/C12b/C17/C18 are gating tests. |

### Total new tests

- **98 unit/integration tests** across 9 new packs
- **~30 integration tests** (shadow-equivalence scenarios in C10)

### CI gating

- Every commit runs: `completion_authority_invariant_test`, `correlation_manager_unit_test`, `concurrency_atomic_unit_test`, `kill_switch_legacy_path_test`
- Phase A PR requires: A11, A12 (incl. register-window proof), A13, A14 (kill switch), A15 green
- Phase B PR requires: B5 green + all premature completion variants pass (both flag states)
- Phase C-M5 sub-PR requires: C4.5 (matrix test, before AND after), C10 green
- Phase C-M6 sub-PR requires: C12a (decision doc), C12b (serialization test, old impl), C17 (86/86), C18 (serialization test, new impl) green

---

## 10. Rollback Strategy

> **[reviewer W6 — CRITICAL documentation]** The kill switch (`USE_LEGACY_WAITING_FOR_CASCADE=ON`) is a **"lesser evil" rollback**, not a safe revert. It reverts to the **buggy M0 path** — the same path that has the premature-completion bug class. It is the fallback when the new path is *worse* than the old path, not when the new path is *broken*. Document this in `docs/configuration/` and in the release runbook. If the new path is broken in a way that doesn't involve premature completion (e.g. a deadlock), the kill switch won't help — you must revert the PR.

### Per-phase rollback **[reviewer W3 — per-subphase granularity]**

| Phase / Subphase | Rollback trigger | Action |
|------------------|------------------|--------|
| **A** | `CM_WAITING_FOR_DIVERGENCE` logs exceed threshold (>10/hour) | Flip `USE_LEGACY_WAITING_FOR_CASCADE=ON`. **Note [W6]:** this reverts to the M0 buggy path. |
| **B** | `watch_job`-driven regressions | Flip `USE_LEGACY_WAITING_FOR_CASCADE=ON` (re-enables legacy `_process_child_completion_and_notify_parent`). **Note [W6]:** lesser-evil rollback. |
| **C-M4** (documentation) | N/A — no behavior change | N/A — C-M4 is a documentation/deprecation-log step |
| **C-M5** (route admission) | Dispatch divergence, observer hot-loop exceeds SLO | Flip `USE_LEGACY_JOBQUEUE_DISPATCH=ON` (restores `MessageJobHandler.handle` local path). If flag already removed (C11), revert the C-M5 sub-PR. |
| **C-M6** (collapse gate) | Gate race, threading deadlock, serialization test fails | Revert the C-M6 sub-PR (restores DB-backed lease, `ExecutionLeaseRepository`, `LeaseContention`, `instance_execution_leases` table). This is a clean revert because C-M6 only touches `execution_gate.py` + repos. |

### Feature flag defaults at release

| Flag | Default | Purpose |
|------|---------|---------|
| `USE_LEGACY_WAITING_FOR_CASCADE` | **OFF** | Kill switch for premature-completion bug class |
| `DEBUG_COMPLETION_INVARIANT` | **ON** for 2 weeks post-release, then OFF | Observability for CM/`waiting_for` divergence |
| `USE_LEGACY_JOBQUEUE_DISPATCH` | Removed pre-release | C-M5 flag; not present at deploy |

---

## 11. Acceptance Criteria (per phase)

### Phase A

- [ ] Premature-completion bug class structurally impossible under flag OFF
- [ ] All 18 files with `waiting_for` reads audited and gated
- [ ] `rebuild_from_db()` crash-safety audited and contract documented (A0a) [reviewer C5]
- [ ] `SELECT COUNT(*)` TOCTOU fallback throws if CM is None when flag OFF (A8) [reviewer W1]
- [ ] A7 `FOR UPDATE` gate removed only after A12 register-window proof passes (A7) [reviewer C1]
- [ ] A11, A12 (incl. register-window + crash-recovery), A13, A14 (kill switch), A15 (in-flight) test packs pass
- [ ] `DEBUG_COMPLETION_INVARIANT` logs on any divergence in dev/CI (or <10/hour background noise) [reviewer W11]
- [ ] Kill switch (flag ON) fully tested (A14) [reviewer C4]

### Phase B

- [ ] All three repro variants (A, B, C) from 2026-06-20 investigation structurally impossible
- [ ] B5 test pack passes
- [ ] Premature-completion regression tests fully green (both flag states)

### Phase C

- [ ] One enqueue function documented (C3 grep test)
- [ ] JobQueue is scheduling-only; WorkerPool is only execution path
- [ ] `execution_gate.py` ~40 lines with `asyncio.Lock`
- [ ] `instance_execution_leases` table and migration deleted
- [ ] C4.5 (pause/terminate matrix, 20 tests) passes before AND after C-M5 [reviewer C6]
- [ ] C10 (unified dispatcher shadow, 15 tests) passes
- [ ] C12a (threading-model decision doc) written before C12 [reviewer C2]
- [ ] C12b (serialization test) passes under old impl (contract capture) [reviewer C2]
- [ ] C17 (`concurrency_atomic_unit_test` 86/86) passes
- [ ] C18 (serialization test under new asyncio.Lock impl) passes — C-M6 merge gate [reviewer C2]
- [ ] Observer hot-loop meets p99 ≤ 50ms SLO [reviewer W2]

---

## 12. Definition of Done (Release N)

This release is done when:

1. The premature-completion bug class is **structurally impossible** (closed at Phase B).
2. The codebase has **one dispatcher** (WorkerPool, closed at Phase C-M5) and **one scheduling layer** (JobQueue, scheduling vocabulary only at Phase C-M5).
3. The Execution Gate is **~40 lines**, not ~700 (closed at Phase C-M6).
4. Three documented repro variants from the 2026-06-20 investigation are regression-tested in `tests/postgres/test_premature_completion_regression.py` and registered in `PACKS.md`.
5. ADR-011 is enforced in code: `waiting_for` is no longer a control-flow value (closed at Phase A-M2).
6. All test packs in §9 are green on the release branch.
7. Docs match code: `docs/architecture/message-processing-and-correlation.md`, `docs/architecture/job-task-pause-resume.md`, and `docs/architecture.md` reflect the single-dispatcher architecture.
8. CHANGELOG entry added.
9. Release-tracking issue lists all feature flags and their expected values at deploy.

---

## 13. Follow-up (Release N+1) — Dependency Bus (Phase D deferred)

The Dependency Bus (`daemon/services/dependency_bus.py`, `dependency_watchers` table, drop `waiting_for`/`children`/`instance_hierarchy`) is deferred to a follow-up release. This release ships the **production bug fix** (Phase A+B) and the **structural simplification** (Phase C) in ~4 weeks. The architectural polish (replacing CM with a bus) can be done when there's no production pressure, with a proper in-flight migration and a two-step column drop (D10a shadow, D10b drop after 2+ weeks of clean bus operation).

---

## 14. File-level Change List (consolidated)

### Phase A (8–10 days)

- `docs/architecture/completion-authority.md` — new
- `daemon/config.py` — add `USE_LEGACY_WAITING_FOR_CASCADE`, `DEBUG_COMPLETION_INVARIANT` + flag interaction matrix [reviewer W9]
- `daemon/services/correlation_manager.py` — add `DEBUG_COMPLETION_INVARIANT` check + divergence triage decision tree [reviewer W7]
- `daemon/services/correlation_manager.py:493–584` — `rebuild_from_db()` crash-safety audit + fixes [reviewer C5]
- `daemon/services/child_reports.py` — gate `waiting_for` decrement + cascade decision + `SELECT COUNT(*)` fallback → throw [reviewer W1]
- `daemon/tools/instance.py` — gate M0's parent-revive and `waiting_for` increment
- `daemon/services/instance_lifecycle.py` — gate `waiting_for` reset in pause/resume
- `daemon/services/job_feedback_observer.py:1230–1320` — gate `FOR UPDATE` row-lock gate (NOT docstrings at 65/106/127/621) [reviewer C1]
- `api.py`, `manager.py`, `models/instance.py`, `opencode/state.py`, `repositories/instance/{models,repository}.py`, `repositories/task/repository.py`, `routers/instances.py`, `services/{error_reporting,job_processor,job_queue_service,message_job_handler,message_processing_pipeline}.py` — audit + gate remaining `waiting_for` reads
- `docs/architecture/message-processing-and-correlation.md` — pointer to authority doc
- `tests/test_completion_authority_invariant.py` — new (A11)
- `tests/test_correlation_authority_shadow.py` — new (A12, incl. register-window proof + crash-recovery)
- `tests/test_kill_switch_legacy_path.py` — new (A14) [reviewer C4]
- `tests/postgres/test_inflight_flag_flip.py` — new (A15) [reviewer W10]
- `tests/postgres/test_premature_completion_regression.py`, `tests/postgres/test_premature_completion_edge_cases.py` — update for flag

### Phase B (2.5 days)

- `daemon/services/correlation_manager.py` — `pending_jobs` dict; `is_complete` checks both; `notify_corr_register_job`, `notify_corr_resolve_job`
- `daemon/services/job_feedback_observer.py` — call `notify_corr_resolve_job` on terminal event
- `daemon/tools/job_queue.py` — call `notify_corr_register_job` on `watch_job` (NOT `job.py`)
- `tests/test_watch_job_integration.py` — new

### Phase C (2.5 weeks)

**C-M4 (documentation, 0.5 day):**
- `daemon/services/instance_messaging.py` — add deprecation log to `enqueue_message_via_jq`
- `daemon/config.py` — (no new flag for C-M4)
- `tests/test_dispatcher_path_equivalence.py` — new
- `tests/test_dispatcher_path_invariants.py` — new
- `docs/architecture/message-processing-and-correlation.md` — update §4

**C-M5 (1.5 weeks):**
- `daemon/config.py` — add (then remove) `USE_LEGACY_JOBQUEUE_DISPATCH`
- `daemon/services/job_feedback_observer.py` — add local-admission path
- `daemon/services/job_processor.py` — call observer on local admission
- `daemon/services/message_job_handler.py` — demote to cross-instance handoff only
- `tests/test_pause_terminate_matrix.py` — new (C4.5) [reviewer C6]
- `tests/test_unified_dispatcher_shadow.py` — new
- `docs/architecture/message-processing-and-correlation.md` — update

**C-M6 (1 week):**
- `docs/architecture/execution-gate-threading-model.md` — new (C12a) [reviewer C2]
- `tests/test_gate_threading_serialization.py` — new (C12b/C18) [reviewer C2]
- `daemon/services/execution_gate.py` — collapse to in-process `asyncio.Lock` (~40 lines) + module docstring
- `daemon/repositories/execution_lease/` — delete
- `20260614_000002_create_instance_execution_leases.sql` — drop
- `daemon/services/message_processing_pipeline.py` — update call site
- `tests/unit/services/test_execution_gate.py` — update/delete (imports deleted classes) [reviewer C2]
- `tests/test_resume_gate.py` — update/delete (imports deleted classes) [reviewer C2]
- `docs/architecture/message-processing-and-correlation.md` — update §6
- `concurrency_atomic_unit_test` (86 tests) — gate (C17, CM-level locks only)

---

## 15. Tracking

- **Created**: 2026-06-20
- **Last Updated**: 2026-06-20 (reviewer round-2 incorporated)
- **Status**: Ready for execution
- **Review feedback incorporated**:
  - Round 1: `docs/plans/decouple-review.md` §7 recommendations (expanded Phase A scope, fixed file paths, re-scoped C1, deferred Phase D)
  - Round 2: 6 critical fixes (C1–C6) + 10 warnings (W1–W11) from reviewer round-2. See `docs/plans/decouple-review.md` §9 for mapping.
- **Critical preconditions added in round 2**:
  - A0a: `rebuild_from_db()` crash-safety audit [C5]
  - A12 register-window proof test BEFORE A7 gate removal [C1]
  - A14 kill-switch test pack [C4]
  - C4.5 pause/terminate matrix test BEFORE C-M5 [C6]
  - C12a threading-model decision doc + C12b serialization test BEFORE C12 [C2]
  - C18 serialization test under new impl — C-M6 merge gate [C2]
- **Next step**: Initialize `plan-track` opencode session to monitor execution progress

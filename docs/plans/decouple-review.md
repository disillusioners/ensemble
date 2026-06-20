# Critical Review: Decouple Job / Task / Message / Correlation — v2 Plan

| Field | Value |
|---|---|
| **Reviewer** | Strategic Planner (ensemble) |
| **Date** | 2026-06-20 |
| **Document reviewed** | `docs/plans/decouple-job-task-message-correlation.md` (v2, 508 lines) |
| **Cross-referenced** | `docs/plans/unified-dispatcher.md`, `.agents/tester/RESULTS/2026-06-20-job-premature-completion-investigation.md`, `docs/architecture/message-processing-and-correlation.md`, actual source code |
| **Verdict** | **Approved for execution** (post round-2) — all 6 critical issues resolved. See §9. |

---

## 1. Assessment — What's sound

### 1.1 The destination architecture is correct

The target — one dispatcher (WorkerPool), one scheduling layer (JobQueue), one completion authority (Dependency Bus), a ~40-line in-process gate — is the right end state. The diagnosis of the dual-path drift is accurate and confirmed by the bug investigation logs. The investigation (`2026-06-20-job-premature-completion-investigation.md`) is first-rate: three repro variants (A: multi-wave spawn, B: `job_continue`/`watch_job`, C: concurrent root messages), with smoking-gun log evidence (job `edab333b` finalized 28 minutes before the parent stopped spawning children). The root cause — "CM tracks message resolutions keyed by `(child_id, message_id)`, not child-instance lifecycle" — is correctly identified as a *class* of bugs, not a single bug.

### 1.2 The phase sequencing is logically correct

The critical-dependency chain is right:
- **Phase A before B** — making CM authoritative before adding `pending_jobs` to it avoids a three-authority window. ✅ Correct.
- **Phase C-M5 before C-M6** — collapsing the gate before unifying dispatch *re-creates* the original cross-dispatcher race that motivated the gate. ✅ Correct, and the C17 gate (`concurrency_atomic_unit_test` 86/86) is a real, existing, passing test pack (verified in `PACKS.md`).
- **Phase D last** — the Dependency Bus must be the completion authority before dropping CM. ✅ Correct.

### 1.3 Feature flags as safety nets (not validation campaigns) is a reasonable bet

Replacing calendar dwell periods with `DEBUG_COMPLETION_INVARIANT` + CI shadow-equivalence tests is a legitimate risk-reduction strategy *if* the test packs are genuinely equivalent to production dwell. The invariant check (Phase A3) reading `waiting_for` on every CM operation and logging divergence is a strong, cheap observability tool.

### 1.4 Independent reversibility per PR is well-structured

The rollback table (§8) correctly identifies which flag or PR revert addresses which symptom. The "each phase is independently reversible" claim holds *structurally* (each PR touches disjoint files), though there's a subtlety — see §2.4.

---

## 2. Critical Issues — What's wrong or will fail

### 2.1 🔴 CRITICAL: C1 ("alias, don't fork") is materially understated — the two enqueue paths are NOT equivalent

**The plan says (C1):** `enqueue_message_via_jq` becomes a "thin wrapper" of `enqueue_message` with `metadata={"dispatch_path": "legacy_jq"}` tag. "No behavior change."

**The code says otherwise.** I read both methods (`instance_messaging.py:887` and `:1486`). They share only the `_prepare_enqueued_message` prelude, then **diverge completely**:

| Aspect | `enqueue_message` (WorkerPool path) | `enqueue_message_via_jq` (JobQueue path) |
|---|---|---|
| Dispatch row written | **`Task` row** (`create_task_row=True`) | **`JobItem` row** (`create_task_row=False`) via `_job_queue_service.enqueue(job_type="message", ...)` |
| Notification | `worker_pool.notify_work()` | None (JobProcessor async poll loop picks it up) |
| Queue/scheduling | None (FIFO Task claim) | Full JobQueue: `JobLockManager` per-queue lock, priority, project_id scoping |
| `instance_id` storage | Implicit (Task → instance) | Explicit `JobItem.instance_id` column |

**Impact:** You cannot make `enqueue_message_via_jq` a thin wrapper of `enqueue_message` without *either* (a) also writing a Task row (which changes JobQueue-path behavior and defeats the point of the JobQueue's scheduling layer), or (b) leaving the JobItem write in place (so it's not a thin wrapper — it's still a different dispatch row). C1 as written is a no-op that doesn't actually advance the unification. The real unification happens at **C-M5** (routing JobProcessor through the observer to write Task rows). C1's "thin wrapper" framing obscures that C-M4 is essentially documentation, and the equivalence test (C2) will pass trivially because nothing changed.

**This matters for the schedule:** Phase C's "~2 weeks" estimate likely allocates ~1 day to C-M4. C-M4 delivers ~nothing (the wrapper doesn't unify anything), but it makes it *look* like the path-equivalence problem is solved. The bulk of the real work (C-M5) then gets compressed.

### 2.2 🔴 CRITICAL: Phase A "~4 days" is optimistic for 55 control-flow reads across 18 files

The plan gates every `waiting_for` mutation/control-flow read behind `USE_LEGACY_WAITING_FOR_CASCADE`. The actual footprint:

- **55** `waiting_for` control-flow read sites (decrement/increment/`==0`/`>0`) across **18 files** (`api.py`, `manager.py`, `models/instance.py`, `opencode/state.py`, `repositories/instance/{models,repository}.py`, `repositories/task/repository.py`, `routers/instances.py`, `services/{child_reports,correlation_manager,error_reporting,instance_lifecycle,job_feedback_observer,job_processor,job_queue_service,message_job_handler,message_processing_pipeline}.py`, `tools/instance.py`).

Phase A explicitly lists only ~5 files (A4–A6). **13 of the 18 files are not mentioned in Phase A deliverables.** Every un-gated control-flow read of `waiting_for` is a silent regression vector when the flag is OFF — the exact "hidden divergence" risk the plan warns about (§9, row 1) but doesn't fully account for in the task list.

**The audit (A9 invariant pack) is supposed to catch these, but the audit is part of the deliverable, not a precondition.** If the audit finds 13 ungated sites after a 4-day sprint, you're looking at another 2–3 days to gate them + re-verify. Phase A is realistically **6–8 days**, not 4.

### 2.3 🔴 CRITICAL: `tests/test_premature_completion.py` does not exist

The plan references `tests/test_premature_completion.py` in **9 places** (acceptance criteria for A, B, C, D; DoD item #4; test pack table). It does not exist in the repo. The actual premature-completion tests are in:
- `tests/postgres/test_premature_completion_regression.py`
- `tests/postgres/test_premature_completion_edge_cases.py`

These are **PostgreSQL-specific** tests (the project's critical note: "PostgreSQL is the PRIMARY dev/test DB"). The plan doesn't acknowledge that the canonical regression tests require a running PostgreSQL instance, nor that they live in `tests/postgres/`. This is a blocking ambiguity: do all the "test_premature_completion.py passes" acceptance criteria mean the postgres variants? If so, CI gating on these requires PG infrastructure at every phase.

### 2.4 🟡 Phase B's file path is wrong (B3)

B3 says modify `daemon/tools/job.py`. There is no such file. `job_continue` and `watch_job` live in **`daemon/tools/job_queue.py`** (verified: `job_continue` at line 409, `watch_job` at line 610). This is a minor error but signals the plan was written against assumptions rather than code verification. It would surface immediately during execution, but it's symptomatic — see §5.

### 2.5 🟡 Phase D `drop_legacy_completion_columns` is NOT reversible as claimed

D10 claims the migration dropping `Instance.waiting_for`, `Instance.children`, `instance_hierarchy` is "reversible (drops columns, recreates as NULL, no data loss)." **Dropping a column destroys its data.** "Recreating as NULL" is not reversal — it's schema recreation with data loss. The `waiting_for` values that survive as the rebuild cache today would be gone. If the kill switch (`USE_LEGACY_WAITING_FOR_CASCADE=ON`) is ever needed *after* D10, the cache it depends on is empty.

The plan contradicts itself: §8 rollback table says "Dependency Bus divergence → revert Phase D PR" restores CM + `waiting_for`. But D10's migration is applied at deploy; reverting the PR does not un-apply a migration. **The migration is a one-way door, and the rollback path is broken if the migration has run.**

### 2.6 🟡 `rebuild_from_db()` mismatch (problem #4 from context) is not explicitly fixed

The context lists `rebuild_from_db` mismatch — "Dict merge bug, stale state, atomicity gap, missing cleanup. Parents can be permanently wedged after crashes" — as a current architecture problem. The v2 plan relies heavily on `rebuild_from_db()` (A6: "CM re-registers in-flight correlations on resume via existing `rebuild_from_db()`"; A9 tests "pause/resume with flag OFF preserves CM pending set"). But **no deliverable in any phase actually fixes `rebuild_from_db()`**. Phase A assumes it works; Phase D replaces it (the Dependency Bus is DB-backed and survives restart by construction). The gap is Phase A→D: if `rebuild_from_db()` has the mismatches described, Phase A's flag-OFF path inherits those bugs.

---

## 3. Missing Elements — What the plan doesn't address

### 3.1 🔴 No multi-worker / thread-safety analysis for the asyncio.Lock gate (C-M6)

The collapsed gate (C12) uses `asyncio.Lock` per instance. But WorkerPool runs **4 threads**, and `asyncio.Lock` is **single-event-loop**. The code shows `_locks_guard = threading.Lock()` guards dict creation, but the plan doesn't address: which event loop does the lock run on? The WorkerPool uses `MainLoopBridge` to call into the asyncio loop from worker threads. If two threads acquire the same `asyncio.Lock` for the same instance, the second blocks — but is that on the main loop or the worker? The plan treats the collapse as "trivial (~40 lines)" but the threading↔asyncio interop is the actual complexity. The existing DB-backed gate sidesteps this (leases are in the DB, shared across threads). The asyncio.Lock replacement needs an explicit concurrency model decision.

### 3.2 🔴 No handling of in-flight jobs during migration

If Phase D flips `USE_DEPENDENCY_BUS=ON` while jobs with `waiting_for > 0` are in flight, what happens to those correlation states? The plan has no migration of live CM `_pending` sets to Dependency Bus `dependency_watchers` rows. Phase D assumes a clean cutover, but a long-running parent instance (the exact scenario in the bug logs — 28+ minutes) could be mid-flight at deploy time.

### 3.3 🟡 No `CompletionRegistry` lifecycle fix (problem #3 from context)

The context identifies: "When CM is active, the callback chain only transitions the JOB, never the INSTANCE. Instance lifecycle SSE stream breaks, CompletionRegistry signaling fails → hung `invoke_agent_and_wait()` callers." The plan addresses JOB completion authority but **never explicitly fixes the INSTANCE lifecycle gap**. Phase D's Dependency Bus replaces the completion mechanism, which may implicitly fix it — but no deliverable targets it, no test asserts that `invoke_agent_and_wait()` callers are unblocked under the new architecture.

### 3.4 🟡 No `error_reporting.py` changes listed

`_send_error_report` writes a Task and touches `instance_hierarchy` (verified by grep). The unified-dispatcher plan (§7) explicitly lists error reporting as something that changes ("error becomes a `FollowUp` on the parent's previous Task"). The v2 plan's appendix (Phase D) doesn't mention `error_reporting.py`. This is a gap that would surface as a failing test or a dangling code path.

### 3.5 🟡 No staging/canary data-divergence detection plan

The plan replaces production shadow dwell with `DEBUG_COMPLETION_INVARIANT` logs for 2 weeks. But there's no defined *triage procedure*: when the log emits `CM_WAITING_FOR_DIVERGENCE`, what's the runbook? "Turn on the kill switch" is the only documented response, but that re-enables the buggy legacy path. There's no middle ground between "ignore the warning" and "full rollback to legacy."

---

## 4. Feasibility Analysis — Can this be done in ~3.5 weeks?

### Verdict: No. Realistic estimate is **5–6 weeks** of engineering (still better than v1's 5–6 weeks + 4 weeks dwell).

| Phase | Plan estimate | Realistic estimate | Delta driver |
|---|---|---|---|
| A (M1+M2) | 4 days | **6–8 days** | 55 call sites / 18 files (not 5); audit rework loop; `rebuild_from_db` verification |
| B (M3) | 1 day | **1.5–2 days** | Wrong file path fix; `watch_job` parent-context audit (B risk note) |
| C (M4+M5+M6) | 2 weeks | **2.5–3 weeks** | C-M4 delivers nothing (§2.1); real work compressed into C-M5; C-M6 threading model (§3.1); 438 lines of pause/terminate discrimination to merge |
| D (M7+M8) | 1.5 weeks | **2 weeks** | Bus persistence layer; in-flight migration (§3.2); irreversible column drop risk (§2.5) |
| **Total** | **~3.5 weeks** | **~5.5–6 weeks** | **+40–70%** |

### What's underestimated

1. **Phase C is the true critical path and the estimate is most wrong here.** The plan treats C-M4 (alias) as ~1 day of work, but it's ~1 day that produces no real unification. The actual unification (C-M5: routing JobProcessor through the observer) is a behavioral change to a **hot path** (`JobProcessor._process_loop` is the asyncio poll loop that drives all HTTP API message work). The plan's own risk note (C-M5 hot loop on observer) acknowledges this but budgets it as part of a generic "~2 weeks."

2. **The 438 lines of pause/terminate discrimination** in `MessageJobHandler` that must move to the unified processor (unified-dispatcher §5.5). This is the highest-risk merge in the entire plan — subtle behavioral differences between the two handlers' cancellation flows. The plan defers this to C-M5 but doesn't budget the contract-test work (snapshot the matrix before, verify after) that the unified-dispatcher plan explicitly calls out as a 3-day task.

3. **Test pack creation is front-loaded but under-timed.** Phase A alone creates 3 new test packs (~40 tests). Phase C creates 3 more (~26 tests). Phase D creates 1 (~30 tests). That's ~96 new tests across the plan. Writing + debugging concurrency tests for this codebase (where the bugs are timing-dependent TOCTOU races) is the dominant cost, and the "~2 min timeout" per pack hides the authoring time.

---

## 5. Dependency Analysis — Hidden coupling

### 5.1 Confirmed correct dependencies
- A → B: correct (CM authority before `pending_jobs`).
- C-M5 → C-M6: correct (unify before collapsing gate).
- D → all: correct (bus is the final authority).

### 5.2 Hidden coupling not in the plan

**(a) `message_processing_pipeline.py` is the shared chokepoint — and it's touched by C-M5 AND C-M6.** The unified pipeline (783 lines) is what both dispatch paths delegate to today. C-M5 changes how work *enters* the pipeline (via observer instead of `MessageJobHandler`). C-M6 changes how the pipeline *acquires* the gate (asyncio.Lock instead of DB lease). If C-M5 and C-M6 are done by different people in the same PR (as the "one PR" structure implies), they'll conflict on the same file. This is fine if done sequentially by one person, but it means Phase C is **inherently serial**, not parallelizable.

**(b) Phase A and Phase D both modify `correlation_manager.py`.** Phase A adds `DEBUG_COMPLETION_INVARIANT` to `resolve_response`/`register_message_send`. Phase D (D8) *removes* `register_message_send`/`resolve_response`. If Phase A and D overlap in time (different PRs on the same branch), there's rebase churn. Not blocking, but the "one branch, sequential merge" model is the only thing preventing this.

**(c) `child_reports.py:608` cascade has a live `SELECT COUNT(*)` fallback that Phase A does NOT gate.** The code at line 657–667 runs when `cm is None` (graceful degradation) — a `SELECT COUNT(*)` on `MessageQueue` for pending messages. This is the Race #3 TOCTOU vector (problem #7 from context). Phase A gates the `waiting_for` reads but **the `SELECT COUNT(*)` fallback is not gated** — it's inside the `cm is None` branch. If CM is wired up (flag OFF, CM active), this path is skipped. But the plan doesn't *guarantee* CM is always active; the graceful-degradation path with the `SELECT COUNT(*)` persists through Phase D. This is a latent bug that survives the entire plan.

### 5.3 Coupling assessment summary

| Phase pair | Coupling | Can parallelize? |
|---|---|---|
| A → B | loose (B adds to CM that A instrumented) | No — A must land first |
| B → C | independent (different subsystems) | **Yes** — but B is 1.5 days, not worth the coordination overhead |
| C-M4 → C-M5 → C-M6 | **tight** (same files, same PR) | No — inherently serial |
| C → D | loose (D builds on C's unified dispatcher) | No — D needs C's single-dispatcher invariant |

**Conclusion:** the plan is a serial chain with one parallelizable seam (B↔C-early-work) that's not worth exploiting. The "4 phases" structure is correct; the question is whether they can be compressed (see Execution Plan).

---

## 6. Risk Assessment — What's underweighted

| Risk | Plan's view | My view | Why |
|---|---|---|---|
| **Hidden `waiting_for` reads** | Medium/High, mitigated by A9 audit | **High** — 13 of 18 files not in Phase A scope | Audit finds the problem *after* the sprint; rework loop not budgeted |
| **C-M5 hot loop on observer** | Medium/Medium | **High/High** — JobProcessor is the HTTP API backbone | Observer was designed for rare cross-instance handoff; making it the admission path for ALL HTTP message work is a fundamental load profile change |
| **asyncio.Lock threading model (C-M6)** | Not addressed | **Medium/High** — 4-thread WorkerPool + asyncio.Lock interop | The DB-backed gate worked because leases are in shared DB; asyncio.Lock needs correct event-loop binding |
| **Irreversible column drop (D10)** | "Reversible" | **False** — column drop is data-destructive | Rollback path is broken post-migration |
| **In-flight job migration (Phase D cutover)** | Not addressed | **Medium/High** — 28-minute parent instances exist in prod | No migration of live CM state to bus |
| **`SELECT COUNT(*)` TOCTOU fallback (Race #3)** | "CM eliminates it" | **Persists** — graceful-degradation path not gated | Latent through entire plan |
| **CI replacing production dwell** | "Test packs are the gate" | **Partially true** — timing-dependent TOCTOU races are hard to test deterministically | Concurrency tests with controlled interleaving can miss the specific window that production load hits |

### Mitigations that are insufficient

1. **`DEBUG_COMPLETION_INVARIANT` for 2 weeks** is good *detection* but not *prevention*. It logs divergence; it doesn't prevent the premature completion. The kill switch (`USE_LEGACY_WAITING_FOR_CASCADE=ON`) reverts to the *buggy* legacy path — so "turning on the kill switch" trades one bug class for another. There is no safe middle state.

2. **D9 shadow-equivalence tests** replace v1's "1 sprint dual-running." But the 40 `correlation_manager_unit_test` fixtures test CM *behavior*, not the full system. They won't catch a bus-vs-CM divergence that only manifests under real graph execution (e.g., when a parent spawns children mid-resume, the bus's `watch()` timing relative to `graph.astream` checkpoint differs from CM's `register_message_send` timing).

---

## 7. Recommendations — What should change before execution

### 7.1 Fix the three critical issues before starting
1. **Re-scope Phase A to explicitly cover all 18 files** with `waiting_for` reads. Budget 6–8 days. Make the A9 invariant audit a *precondition* (run it first against current main to get the baseline of ungated sites), not a deliverable.
2. **Fix the file paths** (B3: `daemon/tools/job_queue.py`, not `job.py`). Fix the test paths (rename references from `test_premature_completion.py` to the actual `tests/postgres/` files, and confirm CI runs PG).
3. **Re-frame C1**: C-M4 is a documentation/no-op step. Real unification is C-M5. Don't budget C-M4 as if it delivers equivalence — budget C-M5 as the heavy lift.

### 7.2 Make the column drop a two-step migration
Split D10 into:
- **D10a (Phase D):** Add `dependency_watchers` table, run bus in shadow, but **do NOT drop columns**. Keep `waiting_for` as dead-but-present.
- **D10b (follow-up release):** After 2+ weeks of clean bus operation in production, drop the columns. This makes the column drop a genuinely separate, reversible decision.

This removes the "one-way door" risk (§2.5) and aligns with the project's documented constraint that migrations on PostgreSQL need `_ensure_postgres_columns()` hooks.

### 7.3 Add an explicit concurrency-model decision for C-M6
Before collapsing the gate, document: which event loop owns the `asyncio.Lock`? How do 4 WorkerPool threads contend? Is the `MainLoopBridge` the serialization point? Add a test that asserts 2 worker threads processing the same instance serializes correctly under the asyncio.Lock.

### 7.4 Add an in-flight migration handler for Phase D
Before flipping `USE_DEPENDENCY_BUS=ON`, write a one-time migration that snapshots active CM `_pending` sets and creates corresponding `dependency_watchers` rows. Alternatively (simpler): drain all in-flight jobs before flipping the flag (document a maintenance window).

### 7.5 Gate the `SELECT COUNT(*)` fallback
Phase A should also gate the `SELECT COUNT(*)` TOCTOU path in `child_reports.py:657` (the `cm is None` fallback). Either ensure CM is *always* active (no graceful degradation with the flag OFF), or document that the graceful-degradation path is unsupported and remove it.

### 7.6 Consider cutting scope to reach the new architecture faster
The user wants the new architecture **ASAP**. The biggest acceleration: **defer Phase D (Dependency Bus) to a follow-up release.** Phase A+B closes the premature-completion bug class (the active production bug). Phase C achieves the single-dispatcher architecture (the structural simplification). Phase D (Dependency Bus) is *architectural polish* — it replaces CM with a bus, but CM already works after Phase A+B. Shipping A+B+C as release N, and D as release N+1, cuts the critical-path time to **~3.5 weeks** (realistic) while delivering the bug fix + single dispatcher, then follows up with the bus when there's no production pressure.

See the companion **Execution Plan** (`decouple-execution-plan.md`) for this accelerated structure.

---

## 8. Summary scorecard

| Dimension | Score | Notes |
|---|---|---|
| Architecture target | ✅ Excellent | Correct end state |
| Root-cause diagnosis | ✅ Excellent | Investigation is first-rate |
| Phase sequencing | ✅ Correct | Dependencies are right |
| Deliverable specificity | 🟡 Good but with gaps | Wrong file paths; missing 13 files in Phase A |
| Effort estimate | 🔴 Optimistic | +40–70% understated |
| Risk identification | 🟡 Mixed | Some risks (hot loop) identified; others (threading, in-flight, irreversible drop) missed |
| Test strategy | 🟡 Good framework | But relies on a test file that doesn't exist; concurrency tests can't replace all dwell |
| Rollback strategy | 🟡 Partially broken | Column drop is irreversible; kill switch trades one bug for another |
| Overall | **Conditionally approved** | Address §2.1–2.3 and §7 before execution |

---

## 9. Reviewer Round-2 — Findings and Resolution

After the round-1 review (§1–§8) and the accelerated execution plan were produced, a second review pass identified **6 critical issues** and **10 warnings**. All have been incorporated into the execution plan (`decouple-execution-plan.md` v2). This section documents each finding and how it was addressed.

### 9.1 Critical fixes (C1–C6)

| ID | Finding | Verified against source? | Resolution in execution plan |
|---|---|---|---|
| **C1** | A7 targets the WRONG lines — would reintroduce the bug! | ✅ **Confirmed.** The actual `FOR UPDATE` gate is at `job_feedback_observer.py:1230–1320` (`_finalize_job_db_sync`), NOT the `WriteGuardSession` docstrings at lines 65/106/127/621–626. Removing the gate without CM providing equivalent protection reintroduces the premature-completion bug class. | **A7 re-targeted** to lines 1230–1320. Added **precondition**: A12 register-window proof test must pass BEFORE A7 removes the gate. The window is structurally closed by A5 (flag OFF skips `waiting_for` increment), but the test proves it empirically. |
| **C2** | C-M6 gate collapse has ZERO threading test coverage; C17 is mislabeled | ✅ **Confirmed.** The 86 `concurrency_atomic_unit_test` tests all test CM-level locks — **0 exercise the ExecutionGate**. The existing gate tests (`test_execution_gate.py`, `test_resume_gate.py`) test the DB-backed lease that C-M6 deletes (they import `ExecutionLeaseRepository`, `LeaseHolderKind`, `LeaseContention`). | Added **C12a** (threading-model decision doc), **C12b** (serialization test under old impl — contract capture), **C18** (serialization test under new asyncio.Lock impl — C-M6 merge gate). C17 re-labeled as CM-level only. C13 explicitly updates/deletes old gate tests. |
| **C3** | `cross_dispatcher_*` race tests do NOT exist | ✅ **Confirmed.** No `cross_dispatcher` test files in `tests/`. Only the bug doc exists (`docs/bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md`). | Acceptance criterion **removed**. Replaced by C4.5 (pause/terminate matrix snapshot from both paths) and C10 (shadow equivalence) which provide equivalent coverage. Documented as a note in Phase C acceptance criteria. |
| **C4** | No test verifies the kill switch (`USE_LEGACY_WAITING_FOR_CASCADE=ON`) | ✅ **Confirmed.** The kill switch is the sole Phase A/B rollback, but nothing exercises the full legacy path under flag ON. With 18 files touched, legacy path regression is very likely. | Added **A14** kill-switch test pack (15 tests): exercises full legacy path with flag ON — increment/decrement, cascade decision, `FOR UPDATE` gate, `SELECT COUNT(*)` fallback, M0 parent-revive, full spawn→completion cascade. All 18 files must pass under BOTH flag states. |
| **C5** | CM in-memory `_pending` dict has no crash-safety story for A→D window | ✅ **Confirmed.** `rebuild_from_db()` (lines 493–584) has the W2 fix (clears `_pending` before rebuild), but tests only check happy path — no stale-entry cleanup or concurrent-register-during-rebuild test. Between Phase A and the deferred Phase D, CM is the sole authority and it's in-memory. | Added **A0a** crash-safety audit: read `rebuild_from_db`, define CM crash-recovery contract, add tests for restart with stale entries / orphan counts / concurrent register. If bugs found, they are Phase A blockers. A12 expanded with crash-recovery tests. |
| **C6** | Pause/terminate discrimination matrix test is a risk NOTE, not a DELIVERABLE | ✅ **Confirmed.** The 438-line `MessageJobHandler` pause/terminate merge is the highest-risk merge in the plan. The plan's risk row says "Add pause/terminate matrix test" but there's no numbered task. | Added **C4.5** pause/terminate matrix test pack (20 tests) as a **C-M5 precondition**: snapshot the matrix from BOTH `MessageJobHandler.handle` and `ProcessMessageProcessor.process`, assert unified processor matches. Must pass against current code BEFORE C-M5 changes, then re-run after C11. |

### 9.2 Warnings addressed (W1–W11)

| ID | Warning | Resolution |
|---|---|---|
| **W1** | Make A8 a DECISION, not a fork | A8 is now a hard decision: **throw if CM is None when flag OFF**. The `SELECT COUNT(*)` TOCTOU path is unreachable under flag OFF. |
| **W2** | Define performance SLO for C-M5 observer hot-loop | Added SLO precondition: p99 admission latency ≤ 50ms under 100 concurrent admissions. If not met, implement throttling before C11. |
| **W3** | Add per-subphase rollback entries | Rollback table now has separate entries for C-M4, C-M5, C-M6. |
| **W4** | Register new test packs in PACKS.md | Test strategy section notes: "All new packs registered in `PACKS.md` in their phase's PR." |
| **W6** | Document kill switch is "lesser evil", not safe revert | Rollback section now has a critical note: kill switch reverts to the **buggy M0 path**. Documented in `docs/configuration/` and release runbook. |
| **W7** | Add triage decision tree for divergence logs | A3 now defines: <10/hour = investigate next sprint; 10–100/hour = page on-call; >100/hour = flip kill switch. |
| **W9** | Add feature-flag interaction matrix | A2 now includes documenting valid/undefined flag combinations in `docs/configuration/`. |
| **W10** | Add in-flight-during-flag-flip test | Added A15: PostgreSQL test for daemon restart with mid-flight parents. |
| **W11** | Define acceptable divergence rate threshold | A3 now defines: acceptable rate is **<10/hour** (background noise), not zero-tolerance. Zero is the goal; <10/hour is operational threshold. |

### 9.3 Confirmed correct (no changes needed)

The reviewer confirmed these aspects of the round-1 review and execution plan are correct:
- ✅ The accelerated path (defer Phase D) is **sound** — CM-as-authority is a stable end-state
- ✅ The dependency graph (A→B→C serial) is correct with no exploitable parallelism
- ✅ The round-1 effort estimates (+40–70%) were accurate
- ✅ All 7 round-1 factual claims verified against actual source code

### 9.4 Updated scorecard (post round-2)

| Dimension | Round-1 score | Round-2 score | Change |
|---|---|---|---|
| Architecture target | ✅ Excellent | ✅ Excellent | Confirmed |
| Root-cause diagnosis | ✅ Excellent | ✅ Excellent | Confirmed |
| Phase sequencing | ✅ Correct | ✅ Correct | Confirmed |
| Deliverable specificity | 🟡 Good but with gaps | ✅ Resolved | All 6 critical fixes incorporated |
| Effort estimate | 🔴 Optimistic (+40–70%) | 🟡 Revised (~4 weeks) | Adjusted from ~3.5 to ~4 weeks |
| Risk identification | 🟡 Mixed | ✅ Comprehensive | Threading, kill-switch, crash-safety, pause/terminate all addressed |
| Test strategy | 🟡 Good framework | ✅ Comprehensive | 98 tests across 9 packs; gate-specific tests added |
| Rollback strategy | 🟡 Partially broken | ✅ Fixed | Per-subphase; kill-switch documented as lesser-evil |
| Overall | **Conditionally approved** | **Approved for execution** | All blockers resolved |

# Architecture Recommendation: schedule-review-improve (Cycle 2)

Date: 2026-08-24
Author: Architect (controller) — synthesis of 4 dispatched design analysts
Analyst instances: architect-worker-queue-resilience (resilience-design), architect-worker-reconcile-dataflow (data-flow-design), architect-worker-source-resilience (resilience-design), architect-worker-sequencing (structural-design)
Plan under review: `.agents/shared/planning/schedule-review-improve/` @ branch `feature/schedule-review-improve` `46349698`
Status: **PLAN APPROVED WITH MANDATORY AMENDMENTS** — 4 blocking rewrites required before Phase-1 dispatch

---

## Executive Summary

The plan's tiering, phase structure, test-gate discipline, and 12 of 15 issue diagnoses are sound and should proceed. However, the analysis found **four plan elements that fail against the codebase as written**: (1) INV-2's success criterion and regression test assert a code path the architecture structurally forbids; (2) INV-5's sub-slice A′ introduces three new raw-UPDATE paths that violate the named-transition + post-commit-reconcile + dependency-bus conventions the rest of the plan depends on; (3) INV-13's premise is stale — its three "unmigrated" call-sites were already migrated to named transitions; (4) INV-1's deploy mitigation references an environment that does not exist. All four are fixable at the plan level (doc amendments + task rewrites), not at the architecture level. The corrected execution order is in §7.

### Verdict Table (per focus area)

| # | Focus Area | Verdict | One-line reason |
|---|-----------|---------|-----------------|
| 1 | Concurrency & atomicity (INV-2, INV-4) | **AMEND** | INV-2's lock-acquisition contract is internally inconsistent (test cannot pass); INV-4's counter scope and sleep primitive are both wrong |
| 2 | Reconciliation architecture (INV-5, INV-13) | **REJECT as-written / AMEND to redesign** | INV-5 fights `reconcile_turn_mirror` authority; INV-13's premise (unmigrated call-sites) is false |
| 3 | Failure isolation (INV-3, INV-8, INV-11) | **AMEND** | INV-3 fix has an ordering hazard (B2); INV-8 references a non-existent method; INV-11 needs clock injection, not threshold-lowering |
| 4 | Rollout safety for INV-1 | **AMEND** | Load-shadow replica does not exist; re-raise is observability-neutral without outer-handler tightening; add kill-switch |
| 5 | Phase sequencing | **AMEND** | Two hard edges dissolve (INV-6→INV-5, INV-5→INV-13); one missed semantic edge (D↔A′) needs a vocabulary freeze; hoist INV-15 quarantine to pre-flight |
| 6 | DB-time convention (INV-6) | **AGREE (core) / AMEND (details)** | DB-side `now()` ages are correct; plan misstates the write side (`now(timezone.utc)`, not `utcnow()`); document the session-TZ invariant |

---

## 1. Concurrency & Atomicity (INV-2, INV-4) — AMEND

### 1a. INV-2 — orphan-recovery bypass: the plan contradicts itself 🔴 BLOCKING

Evidence chain (worker: queue-resilience, re-verified by sequencing worker):

- **Success Criterion #3** (`phase1-plan.md:166`) and **test A9** (`phase1-plan.md:90`) require a `job_locks` row acquired via `start_job_atomic_with_lock` for message-job orphan recovery.
- **The code forbids this path**: the W1 skip at `job_processor.py:841-843` excludes `job_type == "message"` from the recovery loop entirely. Message orphans route through `JobRecoveryService.recover_on_startup → reset_active_to_queued` (`job_queue/repository.py:2169+`), which uses a *different* atomic pattern — DELETE-lock + UPDATE-state in one transaction — not `start_job_atomic_with_lock`.
- **The project convention forbids it twice over**: message JobItems are pure mirrors; the PG trigger skips them; no `message_job_*` caller ever acquires `job_locks` (verified airtight in Cycle-1 review, confirmed in `research-findings.md` cross-partition table).
- **The TASK-job re-spawn path (949-987) already holds its lock** from the original `start_job_atomic_with_lock`; only the no-instance-id branch (1006-1051) is lock-less, and it is documented as "shouldn't happen."

**Verdict on the leader's question** ("extend the atomic path or add compensating guards"): **neither — extend the *correct* atomic path's ownership.** The right architecture:

1. **Primary = monitor-only flag** (as the plan's D4 already selects): counters/log when the recovery path is reached, no production behavior change. *The plan's A8 benchmark must be re-scoped* — monitor-only changes no production behavior, so a p99-RT comparison against `latest` measures nothing; repurpose A8 as "counter volume observation over a synthetic message-job workload" (informing whether the fallback is needed at all).
2. **Fallback = W1-skip extension** delegating to `recover_on_startup` (the documented owner). Known trade-off (plan risk P1-4 already documents it): orphans created mid-session recover only at daemon boot.
3. **Test A9 must be rewritten** to assert: (a) monitor counters increment; (b) W1-skip fires for `job_type='message'` ACTIVE orphans; (c) `recover_on_startup → reset_active_to_queued` flips the row and releases the lock atomically. Success Criterion #3 must be reworded to match (currently it cannot be satisfied).

### 1b. INV-4 — SKIP contention: wrong scope, wrong primitive 🟡

- **Wrong scope**: the planned per-scan-cycle consecutive counter resets at iteration end (`job_processor.py:1052` `continue`); sustained cross-cycle hot-queue contention never accumulates. Move the counter to `JobProcessor` instance state (per-queue, rolling window).
- **Wrong primitive**: `asyncio.sleep` inside `_process_next_job` stalls **all queues** — the outer `_process_loop` is single-threaded (`job_processor.py:650`) and awaits `_process_next_job` once per iteration. Wakes are not lost (queued for the next `wait_for_job`), but every other queue waits out the hot queue's backoff.
- **Parameter interaction**: `system_parallel_queue` concurrency=5 means a 5-way race produces 4 SKIPs — threshold=3 trips the sleep on the first race, penalizing all queues.
- **Recommended design**: **per-queue lease / set-aside list** — when a queue exceeds the skip threshold, exclude it from the scan set for an exponentially growing window (with jitter) instead of sleeping inside the loop. Keep the `event=skip_backoff` log line; it remains the right cheap metric. Defaults (50ms base / 2s cap / jitter 0-250ms) are reasonable *for the set-aside window*; flag for post-deploy tuning as the plan already does.

---

## 2. Reconciliation Architecture (INV-5, INV-13) — REJECT as-written, AMEND to redesign

### 2a. INV-5 — the proposed fix fights `reconcile_turn_mirror` authority 🔴 BLOCKING

Evidence chain (worker: reconcile-dataflow):

- **A′1/A′3 bypass MIRROR_SET**: `ResumeTurn`/`AbortTurn`/`CompleteTurn` declare `MIRROR_SET = ALL_8_MIRRORS` (`turn_transitions.py:298, 351, 393`) — they are the only sanctioned writers whose mirror obligations `reconcile_turn_mirror(work_id)` tracks. The plan's new repo-level `reconcile_paused_task_on_resume` (raw UPDATE task → CANCELLED) and `sweep_stuck_paused_tasks_with_dead_jobitems` (bulk raw UPDATE) write terminal status **without** declaring or triggering any mirror reconciliation — 7 of 8 mirror tables go stale until some later reconcile pass.
- **A′2 violates the documented post-commit pattern**: calling reconcile *inside* the `_resume_cascade_db_sync` transaction re-introduces the SQLite file-lock reentrance that `task/repository.py:1854-1859` explicitly documents removing (see `complete_task`/`fail_task`/`cancel_task`, which all moved reconcile post-commit: `:1861`, `:1972`, `:3247`). The resume path *already* calls `reconcile_turn_mirror` post-commit at `instance_lifecycle.py:3870-3874` — A′2 is duplicative and riskier than the existing pattern.
- **A′3 bypasses dependency_bus**: the sweep cancels Tasks without `emit_terminal()`; `dependency_watchers` rows keyed on the swept Task stay PENDING (`repository.py:887-927` cancels watchers only via the reconciler) — recreating the exact idle-gate deadlock INV-5 is supposed to fix, one hop removed.
- **Dual-source-of-truth drift**: yes — exactly the risk the leader asked about. Two parallel writers to Task terminal state (raw UPDATEs vs named transitions) is the textbook drift setup.

**Recommended design (composes with, rather than fights, the machinery):**

1. Replace A′1/A′2 with: after the existing `ResumeTurn` loop in `_resume_cascade_db_sync`, **post-commit**, run `AbortTurn(reason=…)` (gated on `status='paused' AND NOT EXISTS live JobItem`) for stuck Tasks; `reconcile_turn_mirror(work_id)` then handles all 8 mirrors exactly as `cancel_task` already does (`:3242-3253`).
2. Replace A′3's bulk UPDATE with a `SELECT … FOR UPDATE SKIP LOCKED` candidate query over work_ids that **invokes the named transition** per row (bounded by the `limit=100` the plan already specifies). Same selectivity, convention-compliant writes.
3. A′5 (document, don't remove, the defensive NOT-EXISTS clauses) survives unchanged — but fix the **path typo**: the idle-gates live in `task/repository.py:2199, 2430` with the NOT-EXISTS defense at `:2519-2570`, **not** `instance_lifecycle.py:2474-2518` (those lines are the watchover crash-recovery block).

### 2b. INV-5 ↔ INV-7 semantic coupling (missed by the planner) 🔴 BLOCKING

- The sweep writes `terminal_reason='orphaned_no_task'`. **That value is NOT in `_STATUS_CANONICAL_MAP`** (`work_status.py:66-122`); `_derive_legacy_status` (`:256`) falls through to the lossy `done → completed` mapping. Net effect: the sweep marks an orphan CANCELLED but legacy APIs report it COMPLETED — a runtime regression on the very operator signal INV-5 exists to restore.
- The plan's own INV-7 test D3(a) (`phase2-plan.md:123`) lists `orphaned_no_task` as a "known value" — the plan assumes canonicality that the map doesn't have. Sub-slices A′ and D are marked "independent" in the coupling matrix (`phase2-plan.md:175-177`) but share this semantic contract; whichever lands second breaks the other.
- **Fix (D0 vocabulary freeze)**: land one micro-commit at Phase-2 wave start adding `"orphaned_no_task"` to `_STATUS_CANONICAL_MAP` (aligned to the message_queue CASE at `repository.py:851`, i.e. → `failed`), **or** have the redesigned sweep use `reason='failed'` (already canonical), which dissolves the coupling entirely. The second option is simpler — prefer it unless the plan specifically wants the distinct reason code.

### 2c. INV-13 — premise is stale; re-scope to verify-and-document 🔴 BLOCKING

- `cancel_task` (`repository.py:3102`), `complete_task` (`:1746`), and `fail_task` (`:1874`) are **already migrated** — thin wrappers over `AbortTurn`/`CompleteTurn` via `transition._write()` (`:1813-1827`, `:1930-1941`, `:3169-3175`; docstrings say "THIN WRAPPER"). D2's bounded migration set has nothing left to migrate; the ≤4-LOC ceiling and first-site hard-exit gate are moot.
- The D2 gate's stated justification — "INV-13 will reuse `reconcile_paused_task_on_resume` (A′1)" (`phase2-plan.md:150`) — is false, and after INV-5's redesign there is no A′1 to reuse. The gate dissolves.
- **Re-scope INV-13 to option (a) verify-and-document**: (1) add regression tests pinning the three sites as named-transition wrappers (especially `fail_task → AbortTurn(reason='failed')` — the correct discriminator; a separate `FailTurn` is unnecessary); (2) reword the success criterion from "3 call-sites migrated" to "3 call-sites verified + regression-pinned" so 15/15 stays honest; (3) keep Task 4.8's Cycle-3 handoff listing the *actual* deferred items (`_finalize_job_db_sync`, `_terminate_instance_db_sync`, `force_cancel_and_schedule_retry`, `_status_write_guard`). Do **not** pull those into this cycle — they are the migration-shaped work D2's hard-exit exists to stop, and they conflict with INV-12's same-file edits (`test_observer_race1.py:91-119` mirrors `_finalize_job_db_sync`'s signature).

---

## 3. Failure Isolation (INV-3, INV-8, INV-11) — AMEND

### 3a. INV-3 — reset semantics: correct idea, ordering hazard in the fix

- **Correct semantics confirmed**: binary reset (full `backoff = 2.0` only after a run that lasted ≥ `success_threshold`) — *not* graduated decay, which adds a tuning surface with no clear win for poll-loop adapters. `time.monotonic` is the right clock (immune to wall-clock jumps). 60s threshold must stay a configurable kwarg.
- **🔴 Ordering hazard in plan task B2**: B2 clears `_run_start_time = None` on stop/error transitions, but the 705-718 reset gate *reads* `_run_start_time` — after an error transition, the gate reads `None` and **can never fire**, pinning backoff at the 300s cap forever. Fix the task spec: (a) compute `run_duration = time.monotonic() - _run_start_time` at the *entry* of the error `except` block (~line 680) and store it; (b) gate both resets on `run_duration >= success_threshold`; (c) clear `_run_start_time` at exactly one site — supervisor exit (~line 720).
- **Secondary check**: verify `_run_start_time` is bound to an object that survives supervisor restart (if a fresh adapter instance is constructed per restart, the attribute resets — same hazard surface).

### 3b. INV-8 — one structural error, one missed invariant

- **Plan task C3 references `_set_state()`, which does not exist** in `circuit_breaker.py` (grep-verified; state assignments are inline at `:63, :91, :107`). Either extract `_set_state` as a refactor (scope creep) or place the invariant assertion at the three real sites.
- **Missed invariant (more important)**: `record_failure` clears `_probe_in_flight` unconditionally (`:103`) — a *non-probe* caller's failure during HALF_OPEN can falsely free the probe slot mid-probe. Add a "sibling-failure does not free the probe slot" test to C1.
- `__debug__:` is acceptable for the assertion (ensemble runs default Python, so it fires in prod; free under `-O`). Lower the 200-concurrency stress to **100** (symmetric with INV-10, less flaky on CI) and seed the failure order.

### 3c. INV-11 — validate without long fixtures: clock injection, not threshold-lowering

- **Canonical pattern**: inject the clock — monkeypatch `time.monotonic` at the `daemon.sources.registry` module namespace (or add a `_clock: Callable[[], float]` seam near `registry.py:625`). With synthetic time, test the exact boundaries deterministically: 59.9s → no reset; 60.0s → reset; 0.0s → no reset.
- **Threshold-lowering (B7's 2s) is only faithful under synthetic time** — the `>=` operator is value-independent, but the failure mode is time *control*, not the threshold value. Wall-clock 2s tests still rely on real `time.monotonic()` and remain flaky.
- **The 13-minute outage simulation (Phase-3 Task 3.4 sub-test 3) must be a clock-virtualized unit test** — 13 wall-clock minutes cannot fit in any 120-180s pack. Assert on state-machine decisions (`backoff >= initial × multiplier^k` after k fast-failing starts), not elapsed seconds.

### 3d. INV-11's deferred swallow (registry.py:366-405) — deferral OK, minimal control needed now

- Risk class: execution-lifecycle rows stuck in STARTED (completion write lost → operator sees no completion), and one-time schedulers re-firing if the `enabled=False` write is lost. Duplicated-message risk bounded by next successful write. Fire-and-forget `run_in_executor` (`:400`) — the future is never awaited; exceptions die silently.
- Deferral acceptable for scope discipline, **but** add the 4-6 line compensating control now: structured `logger.warning("source_registry.db_write_failed", extra={execution_id, status, error_class})` + a module-level failure counter, periodically logged. No behavior change; gives operators a grep-able signal.

---

## 4. Rollout Safety for INV-1 — AMEND

- **🔴 The mitigation environment does not exist**: `phase1-plan.md:151` gates deploy on "a 10-minute dry-run log on a load-shadow replica." The topology is dev=8079 / demo=7979 / live=9797 — no replica (grep across `docs/` and `.agents/` finds zero other references). **Substitute**: 10-minute log observation on **demo** post-deploy + stable queue depth + per-job_id error rate ≤ 2× historical baseline.
- **The re-raise is observability-neutral as specified**: the raised error lands in `_process_loop`'s outer handler (`job_processor.py:653-654`) which already logs-and-continues. Net effect of INV-1 = the log moves from line 1264 to line 654. Either (a) drop the re-raise and rely on the outer handler, or (b) tighten the outer handler (failure counter / DLQ marking) so the re-raise has observable semantics. Option (b) preferred — it makes INV-4's telemetry meaningful.
- **Kill-switch**: yes — add `JOB_PROCESSOR_RAISE_ON_ERROR` (default True). Cheap defense-in-depth for the deploy-window error flush; not strictly required (the outer handler swallows anyway) but it gives operators a one-env-var rollback for log volume.
- **Rate-limit key**: per-job_id (30s window) is correct for one bad job; a deploy-time flush of N failing job_ids produces N lines/window. Add a secondary **per-processor global cap** (~100/min) to bound the flush and to preserve INV-4's `skip_backoff` log lines from being visually drowned (the one real INV-1↔INV-4 interaction hazard — different code regions, so no masking within an iteration).
- **Scope note**: two sibling `except Exception:` blocks at `job_processor.py:535` and `:571` (log-only, no re-raise) — INV-1's grep acceptance (`zero matches`) will pass while leaving these. Out of cycle scope, but list them in the close-out notes for Cycle-3.

---

## 5. Phase Sequencing — AMEND (corrected graph in §7)

- **INV-1 → INV-2** (intra-phase): survives unchanged (D4 correct).
- **Phase-1(INV-1) → INV-4**: survives and *strengthens* — INV-4's redesigned per-queue state enlarges the `job_processor.py` diff; keep the A5 gate.
- **INV-6 → INV-5 critical path: dissolves.** The stated rationale ("the resume-time sweep reuses the same age predicate that INV-6 SQL-ifies", `phase2-plan.md:65`) was never true — A′3's own spec is an age-free NOT-EXISTS (`:133`), and the *redesigned* INV-5 needs no `_age_seconds_sql`. Keep only the soft same-file ordering inside the repo-worker (B before A′).
- **INV-5 → INV-13 gate: dissolves** — premise false (§2c). After redesign, both consume the same AbortTurn/post-commit-reconcile vocabulary; the requirement becomes "vocabulary frozen before both," not "INV-5 finishes first."
- **NEW edge the planner missed (D↔A′)**: see §2b — fix via the D0 vocabulary freeze (or `reason='failed'`), not via serialization.
- **Hoist Task 4.4 (INV-15 quarantine) to cycle pre-flight**: the pack statically false-PASSes (exit 0 with the inner `pytest.main` exit code swallowed at `mock_test_job_queue_api.py:1027`), so Phase-1/2/3 gate runs are currently running with a false-PASSING pack in the pipeline. Quarantine is doc-only — do it first. (Also fix phase1-plan's claim that it is *already* quarantined — the QUARANTINE.md row doesn't exist until Phase 4 creates it.)
- **Pack contention**: four parallel sub-slices all run `concurrency_atomic_unit_test` (13 files, 280s internal cap, timing-sensitive `gate_threading_serialization`). Safe (self-contained tmp fixtures) but contending — stagger runs via dispatcher-level queue.
- **D5 two-wave structure holds**: INV-10 early is safe (`rate_limiter.py` untouched by every fix); INV-9/INV-11 correctly wait for Phase-1 close. Only the test *contracts* move earlier (doc-level), not the schedule.
- **INV-12 removal is safe** vs Phase-2/3 tests: no test constructs `EventPublisherService` with `job_id`; `test_observer_race1.py` mirrors the *consumed* positional `job_id` of `_finalize_job_db_sync`, unaffected by the overload removal.

---

## 6. DB-Time Convention (INV-6) — AGREE core, AMEND details

- **DB-side `now()` ages everywhere is the correct pattern** for this codebase — confirmed consistent at both ends:
  - PG: the write side binds `datetime.now(timezone.utc)` (aware UTC); psycopg renders it into **session-local wall time** for the naive TIMESTAMP column; PG `now()` returns the same session-local wall time → `EXTRACT(EPOCH FROM (now() - col))` is frame-consistent. (Plan misstates the write side as `datetime.utcnow()` at `phase2-plan.md:98` — actual code `repository.py:1657` — cosmetic, but fix the plan text so reviewers aren't misled.)
  - SQLite: `julianday('now')` is UTC and `julianday(col)` interprets the stored ISO string as UTC — consistent with the writer.
- **Invariant is load-bearing**: correctness relies on the PG session TZ matching the daemon-local rendering frame (the same invariant `readiness.py:88-99` relies on). It holds *by accident, not by contract* today. Amendment: document the invariant at `_age_seconds_sql`, and (recommended) set `SET TIME ZONE 'UTC'` at connection time to make it contractual.
- **Precision caveats**: `julianday()` has millisecond (not microsecond) precision — sub-second thresholds unreliable (plan's defaults are minutes-scale; fine, but flag in the helper docstring). PG `EXTRACT` returns `Decimal` via psycopg — the plan's `float()` coercion is mandatory; B6's test matrix should assert non-Decimal returns.
- **UTC-strict app-side (option B) rejected**: it would require touching the write surface + every other naive-TIMESTAMP reader — larger diff, more risk, no benefit over the frame-consistent DB-side idiom.

---

## 7. Corrected Execution Order (minimal-diff from plan)

1. **Pre-flight (hoisted Task 4.4)**: QUARANTINE.md row + `mock_job_queue_test.sh` QUARANTINED marker. *(Doc-only; removes the false-PASSING gate signal immediately.)*
2. **Contract amendments (doc-level, before Phase-1 dispatch)**: rewrite A9 + Success Criterion #3 (§1a); re-scope A8/A10 (§1a); replace P1-1 load-shadow mitigation (§4); add D0 canonical-map decision — prefer `reason='failed'` (§2b); note Phase-3 contract rewrites (§8 item 5).
3. **Phase 1 Sub-Slice A**: INV-1 (outer-handler tightening + kill-switch + dual rate-cap) → INV-2 (monitor-only primary; rewritten A9).
4. **Phase 1 Sub-Slice B** ∥ step 3: INV-3 with the B2 ordering fix (run-duration at error-entry; single clear-site at supervisor exit).
5. **Phase 3 early wave** ∥ steps 3-4: INV-10.
6. **Phase 2 wave 1**: A (INV-4 per-queue lease redesign; gated on INV-1) ∥ B (INV-6) ∥ C (INV-8: real state sites, 100-concurrency, sibling-failure invariant) ∥ D (INV-7; D0 lands first if the map edit is chosen). Stagger `concurrency_atomic_unit_test` runs.
7. **Phase 2 wave 2**: A′ (INV-5 **redesigned**: AbortTurn + post-commit reconcile + NOT-EXISTS SKIP-LOCKED sweep) after B (soft same-file order only).
8. **Phase 3 late wave**: INV-9 + INV-11 (clock-injected contracts). May overlap steps 6-7.
9. **Phase 4**: INV-12 + INV-14 ∥ INV-13-**as-verification** (regression pin; gate dissolved).
10. **Final Release-gate sweep**: full non-integration packs in parallel + E2E one-by-one per `ensure.md`.

---

## 8. Required Amendments Checklist (for the Reviewer)

| # | Severity | Amendment | Plan location |
|---|----------|-----------|---------------|
| 1 | 🔴 | Rewrite test A9 + Success Criterion #3: assert `recover_on_startup → reset_active_to_queued` + W1-skip + monitor counters, **not** `job_locks` acquisition via `start_job_atomic_with_lock` | `phase1-plan.md:90, :166` |
| 2 | 🔴 | Redesign INV-5 A′1-A′4: AbortTurn named transition + post-commit `reconcile_turn_mirror` + `SELECT FOR UPDATE SKIP LOCKED` sweep invoking the transition; **no** in-transaction reconcile; **no** raw terminal UPDATEs | `phase2-plan.md:127-138` |
| 3 | 🔴 | Re-scope INV-13 (D2 amendment) to verify-and-document + regression pin; reword success criterion; keep real deferred items in Cycle-3 | `decisions.md §D2`, `phase4-plan.md` Slice C |
| 4 | 🔴 | Resolve the `orphaned_no_task` canonical-map gap: D0 micro-commit (add to `_STATUS_CANONICAL_MAP`, aligned to `failed`) **or** sweep uses `reason='failed'`; update D3(a) test list | `phase2-plan.md:123, :133`, `work_status.py:66-122` |
| 5 | 🔴 | Replace load-shadow-replica mitigation with demo-env observation + queue-depth + baseline-rate gate; decide outer-handler tightening vs re-raise; add `JOB_PROCESSOR_RAISE_ON_ERROR` + per-processor log cap | `phase1-plan.md:151`, P1-1 |
| 6 | 🟡 | INV-4: per-queue lease/set-aside (no `asyncio.sleep` in `_process_next_job`); counter in `JobProcessor` instance state (cross-cycle) | `phase2-plan.md` Sub-slice A |
| 7 | 🟡 | INV-3 B2: compute `run_duration` at error-entry; clear `_run_start_time` only at supervisor exit; verify attribute survives adapter restart | `phase1-plan.md` B2-B4 |
| 8 | 🟡 | INV-8 C3: target the three real state sites (`:63, :91, :107`) — `_set_state()` does not exist; add sibling-failure probe-slot test; stress at 100 | `phase2-plan.md:112-113` |
| 9 | 🟡 | INV-11 + B7: clock-injection (`time.monotonic` monkeypatch) as canonical fixture; 13-min sim = clock-virtualized unit test; boundary tests 59.9/60.0 | `phase1-plan.md:116-117`, `phase3-plan.md:110` |
| 10 | 🟡 | Hoist INV-15 quarantine (Task 4.4) to cycle pre-flight; fix phase1-plan's "already quarantined" claim | `phase4-plan.md:95-110` |
| 11 | 🟡 | Fix path typo: idle-gates live at `task/repository.py:2199, 2430` (NOT-EXISTS `:2519-2570`), not `instance_lifecycle.py:2474-2518` | `phase2-plan.md:135` |
| 12 | 🟢 | INV-6: fix `utcnow()` → `now(timezone.utc)` plan text; document session-TZ invariant at the helper; assert non-Decimal returns in B6; add `SET TIME ZONE 'UTC'` recommendation | `phase2-plan.md:98, B1` |
| 13 | 🟢 | INV-11 swallow: add structured WARNING + failure counter (4-6 LOC) now; keep design deferral | `registry.py:389`, `phase3-plan.md` Task 3.5 |
| 14 | 🟢 | Note sibling swallow sites `job_processor.py:535, :571` for Cycle-3; add A8 re-scope (counter-volume observation, not p99-RT) | `phase1-plan.md` |

---

## 9. Risks After Amendments

- 🟡 **INV-5 redesign diff size**: the named-transition sweep is a larger change than the plan's raw-UPDATE version — but it is the *correct* size; the raw-UPDATE version was smaller only by externalizing cost to mirror-table staleness and watcher deadlocks.
- 🟡 **Monitor-only INV-2 leaves the underlying bypass unfixed in production** (observation only; orphans recovered at boot via fallback if adopted). Accepted by D4; document as the known residual.
- 🟢 **Session-TZ invariant holds by accident** until `SET TIME ZONE 'UTC'` lands — the same exposure exists today, so INV-6 does not worsen it.
- 🟢 **PACKS.md:346 records FAIL** for the mock pack while the pack exits 0 — operator-visible inconsistency until pre-flight quarantine lands.

## 10. Unverified / Assumptions

- INV-4 per-queue lease exact re-entry mechanics (how a set-aside queue rejoins the scan after its window expires) — implementation-level detail for the phase-plan worker; direction is firm.
- Whether `_run_start_time` should live on the adapter or the supervisor record — depends on adapter-restart object identity (flagged in §3a for the implementing worker to verify).
- Empirical "Task stuck paused" row counts in production (INV-5's premise volume) — unchanged from the plan's own gap list.

## 11. Confidence

**High** on Focus 1, 3, 5, 6 (line-verified evidence chains, grep-confirmed by two independent workers each on the blocking items). **High** on Focus 2's rejection verdicts (triple-verified: data-flow worker discovered, sequencing worker grep-confirmed all three already-migrated sites). **Medium** on the INV-5 redesign's exact shape (direction firm — named transitions + post-commit reconcile — but the sweep's transactional details need the implementing worker's verification).

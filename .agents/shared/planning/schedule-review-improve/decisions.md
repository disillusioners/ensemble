# Architectural Decisions: Schedule Feature Review & Improvement (Cycle 2)

Date: 2026-08-24
Branch: `feature/schedule-review-improve` @ `46349698` (v0.11.0)
Author: planner[v2] via plan-creation worker

These decisions are the **authoritative** trade-off resolution for the 6 decision seeds in the dispatch. Phase-plan workers MUST honor them; deviations require an amendment to this file (not a phase-plan-side override).

---

## D1. Inventory Provenance — Re-derived Fresh

**Context**: The dispatch notes that the prior session's frozen inventory was unrecoverable ("branch recreate, never committed"). The current 15-issue inventory was produced by three parallel explorers against the same `latest` HEAD as the lost list; items 1-3-2/4/5 are bit-for-bit identical, items 6-15 trace back to the originally enumerated clusters.

**Decision**: Treat the 15-issue inventory as authoritative. Do NOT compare against the lost list (no canonical reference). Cross-checked confidence against the `critical-notes` system for recurrence patterns — confirmed.

**Rationale**:
- The `critical-notes` system (formerly `critical-experience`) is the project's persistent memory across session loss; it serves as the substitute for the lost inventory.
- Item-by-item spot-verification on `feature/schedule-review-improve @ 46349698` confirmed line refs hold against the current HEAD.
- A re-derived list with original tier mapping is the highest-confidence reconstruction available.

**Trade-offs**:
- ✅ Reliable against current HEAD
- ✅ Match against persistence layer
- ⚠️ Cannot diff against the lost inventory to confirm no item is missing — accept that risk.

**Counter-evidence trigger**: If the `critical-notes` system surfaces an item NOT in this inventory (e.g., during `SkillBank` re-scan or pre-cycle gate), append it with `decisions.md §D1-amendment-<n>` rather than expand scope unilaterally.

---

## D2. INV-13 Scope — In-Cycle but Bounded

> ⚠️ **AMENDMENT 2026-08-24**: superseded by **D7.3** — premise stale. INV-13 call-sites (`cancel_task` / `complete_task` / `fail_task`) are **already migrated** to `AbortTurn`/`CompleteTurn` via `transition._write()` (`repository.py:3102`, `:1746`, `:1874`; thin-wrapper docstrings). The bounded-migration set, ≤4-LOC ceiling, and first-site hard-exit gate are **MOOT** — nothing left to migrate. See D7.3 for the re-scoped verify-and-document + regression-pin contract. Real deferred items (`_finalize_job_db_sync`, `_terminate_instance_db_sync`, `force_cancel_and_schedule_retry`, `_status_write_guard`) stay Cycle-3.

**Context**: `daemon/services/turn_transitions.py:93-117` (`BeginTurn`) and `119-137` (`ClaimTurn`) are stubs with zero production callers. The full Phase-4b/4c migration calls for migrating `_finalize_job_db_sync`, `_terminate_instance_db_sync`, `cancel_task`, `complete_task`, `fail_task`, `force_cancel_and_schedule_retry` to use named transitions, plus permanent enablement of `_status_write_guard`. This is migration-shaped, not cleanup-shaped — exactly the kind of scope that historically turned "small cleanup" into cycles.

**Decision**: KEEP INV-13 in Phase 4, BUT with a **scope ceiling**:

1. **Bounded call-site set**: migrate `cancel_task`, `complete_task`, `fail_task` to `AbortTurn` / `CompleteTurn` only.
2. **LOC ceiling per call-site**: ≤ 4 LOC of net change per migrated site; anything beyond becomes a Cycle-3 follow-up.
3. **Defer the rest**: `_finalize_job_db_sync`, `_terminate_instance_db_sync`, `force_cancel_and_schedule_retry`, and the permanent `_status_write_guard` enablement go to a Cycle-3 plan.
4. **Sequence**: Phase 4 runs INV-13 only AFTER Phase 2 closes (INV-5 finalizes) — INV-13 in Phase 4 cannot start until INV-5's reconciliation contract stabilizes.
5. **Hard exit**: If during execution the LOC ceiling blows on the FIRST migrated call-site, INV-13 is moved entirely to Cycle-3, no questions asked.

**Rationale**:
- A migration-shaped item disguised as cleanup causes mid-cycle drift and re-opens INVs locked behind it.
- Bounded set + LOC ceiling preserves the spirit of the cleanup tier while preventing scope explosion.
- Hard-exit rule prevents sunk-cost rationalization.

**Alternatives considered**:
- **Defer entirely to Cycle 3**: Cleaner, but loses the small win of migrating the three core task-status setters; INV-13 contract already half-stabilized through Phase-4b deferred work.
- **Full in-cycle migration**: Rejected — matches the failure pattern this rule is meant to prevent.

**Trade-offs**:
- ✅ Three call-sites get migrated; clear progress
- ✅ Bounded risk
- ⚠️ Future cycle still has migration tail (Cycle 3 owns the rest)
- ⚠️ Per-site LOC judgment calls at phase-plan worker time

---

## D3. INV-15 — Quarantine Path (Recommended)

> ⚠️ **AMENDMENT 2026-08-24 — TIMING**: hoisted to **cycle pre-flight** per **D7.7(c)**. Decision content (path B: quarantine) **UNCHANGED**, but the `QUARANTINE.md` row creation MUST precede all phase gate runs — the pack currently statically false-PASSes (exit 0 with `pytest.main` exit code swallowed at `mock_test_job_queue_api.py:1027`). Pre-flight also fixes `phase1-plan`'s false "already quarantined" claim (the row does not exist until pre-flight creates it).

**Context**: `tests/mock_test_job_queue_api.py:1027` swallows `pytest.main` exit code; `test/packs/mock_job_queue_test.sh:16` raw-invokes python; 48/48 tests error in setup due to `JobLockManager` signature drift. Three paths exist:

(A) **Repair the harness** — scope-unbounded; requires recovering the signature-change commit, updating 48 fixtures + invocation; risks mid-cycle drift.
(B) **Quarantine the pack** — add to `.agents/tester/QUARANTINE.md` with rationale; pack returns to gate as skipped.
(C) **Delete the pack** — removes the test surface entirely.

**Decision**: Quarantine (path B). Add pack `mock_job_queue_test` to `.agents/tester/QUARANTINE.md` with this rationale:

> `mock_job_queue_test` pack quarantined 2026-08-24 (Cycle 2). 48/48 tests error in setup due to `JobLockManager` signature drift (commit lost to prior session branch recreate). `pytest.main` exit code swallowed at `tests/mock_test_job_queue_api.py:1027` plus raw python invocation at `test/packs/mock_job_queue_test.sh:16` produces a FALSE PASSING gate signal (effective coverage 0). Pack removed from gate pipeline via QUARANTINE until repair is scoped. See `decisions.md §D3` from `schedule-review-improve` Cycle 2.

**Rationale**:
- Repair scope is unbounded without the historical commit; low signal-to-effort ratio.
- Quarantine explicitly preserves operator awareness (the rationale is in the registry), prevents the false-passing signal, and removes the pack from any default gate runs.
- Path C (delete) loses history.

**Alternative**: Path A (repair) is open as a Cycle-3 follow-up if anyone recovers the signature change commit.

**Trade-offs**:
- ✅ Bounded effort in this cycle
- ✅ False PASSING signal removed from gate
- ⚠️ Mock coverage for legacy lock manager remains zero; Cycle-3 owner should re-evaluate.

---

## D4. Phase 1 Internal Ordering — INV-1 Before INV-2 (Sequential)

> ⚠️ **AMENDMENT 2026-08-24**: **A8 re-scoped** per **D7.4** — p99-RT benchmark → **counter-volume observation** (monitor-only changes no production behavior; p99-RT comparison against `latest` measures nothing). Ordering decision (INV-1 BEFORE INV-2) **UNCHANGED**. Risk #2 below is also re-shaped: monitor-only primary leaves the bypass unfixed in production (orphans recovered at boot via fallback if adopted) — see architect §9 residual risk.

**Context**: `job_processor.py` ACTIVE-loop block contains both the silent swallow (lines 1263-1271) and the orphan-recovery bypass (lines 945-987). Two natural orderings:
- **Forward**: INV-1 (silence-fix) → INV-2 (recovery route). After silencing errors, the recovery path's own errors become visible.
- **Reverse**: INV-2 (recovery route) → INV-1 (silence-fix). Risk: recovery errors get swallowed again before the swallow is fixed.

**Decision**: Forward (INV-1 BEFORE INV-2). Phase-plan worker MUST encode this ordering.

**Rationale**:
- INV-1 is the lower-risk fix (idiomatic change to error-handling only).
- INV-2 is the higher-risk structural fix (acquiring `job_locks` rows in a path that previously didn't).
- The Forward order is monotonic: each subsequent fix raises observability, never lowers it.
- The Reverse order would risk INV-2 fix being silently re-suppressed by INV-1's untouched swallow.

**Trade-offs**:
- ✅ First-fix safety net in place before risky migrations
- ⚠️ Slightly slower parallelization (cannot run in same worker)
- ✅ INV-2's test must explicitly assert error propagation (not just success path)

**Edge case**: If a phase-plan worker proposes parallel sub-dispatch for Phase 1 (INV-3 source-layer is orthogonal), INV-1+INV-2 stay sequential within the job-queue sub-slice while INV-3 runs as a parallel worker.

---

## D5. Phase 3 Test Coverage — Test-After Mix (Recommended)

**Context**: Three options for Phase 3 (INV-9, INV-10, INV-11):
- **(I) Pure Test-After**: Tests for each module written only AFTER Phase 1 + Phase 2 land. Higher-quality assertions, no rework.
- **(II) Pure Parallel**: Tests run alongside Phase 1 + Phase 2 on UNTOUCHED code paths. Faster wall-clock, but INV-9 / INV-11 partially depend on prior phase commits.
- **(III) Test-After Mix**: INV-10 runs parallel (independent); INV-9 and INV-11 run AFTER Phase 1 + Phase 2 land respectively.

**Decision**: Path (III) Test-After Mix.

- **INV-10 (rate-limiter stress)** — runs parallel from Phase 1 onward. The rate limiter code (`daemon/sources/rate_limiter.py:32-36, 84-91`) is not touched by INV-3 fix in a way that affects INV-10's behavioral assertions; tests can land earlier.
- **INV-9 (PENDING message orphan guard)** — runs AFTER Phase 1 closes. INV-9 tests the recovery path that INV-2 fixes; co-developing tests pre-fix risks re-writes.
- **INV-11 (adapter error paths)** — runs AFTER Phase 1 closes. INV-11 tests the backoff-reset path that INV-3 fixes; co-developing risks scenario mismatches.

**Rationale**:
- Maximizes wall-clock parallelization on the truly orthogonal slice (INV-10).
- Avoids test-implementation rework on the dependent slices (INV-9, INV-11).
- Skill-philosophy alignment: tests of a fix should know what the fix does.

**Trade-offs**:
- ✅ INV-10 parallel (one worker freed)
- ✅ INV-9 + INV-11 land with stable contracts
- ⚠️ Phase 3 starts in two waves (early + late)
- ⚠️ Phase 3 dependency on Phase 1 close-out for two of three items

**Trigger**: If Phase 1 unexpectedly extends past the 1-worker-day budget, INV-9 and INV-11 may start in test-scaffold mode (writing the test file structure with `TODO` bodies) — but finalization awaits Phase 1 closure.

---

## D6. INV-12 — Remove the Dead `job_id` Overloads (Preferred)

> ✅ **AMENDMENT 2026-08-24 — CONFIRMED SAFE**: per architect §5. No test constructs `EventPublisherService` with `job_id`; `test_observer_race1.py:91-119` mirrors the **consumed** positional `job_id` of `_finalize_job_db_sync`, unaffected by the overload removal. Phase-plan worker may proceed with the removal without additional test-coverage changes.

**Context**: `EventPublisherService` at `daemon/manager.py:970` carries an unused `job_id` parameter; F13 deferred from `defer-seam-bugfix` Phase 3 (2026-06-30) left it in place. The 9 sites in `daemon/services/job_feedback_observer.py` (643, 684, 703, 764, 907, 942, 1959, 2010, 2012) pass a value that nothing consumes in the direct event path.

**Decision**: REMOVE the overloads.

**Rationale**:
- Threading `job_id` through a publisher that's not read for `job_id` is feature-additive work without a consumer; YAGNI.
- Dead-code removal is the lower-risk path: each call-site is unambiguous (no behavioral decision needed).
- A future feature that needs `job_id`-aware events can re-introduce a properly threaded signature at that time, when consumers exist.

**Alternative considered**: Thread `job_id` properly. Rejected — no consumer; no spec; no test would meaningfully validate the threaded value until downstream consumes it.

**Trade-offs**:
- ✅ Lower-risk change
- ✅ Smaller diff
- ✅ Removes a known footgun (dead overloads that look meaningful)
- ⚠️ If a future feature wants `job_id` in events, it re-adds signatures — minor re-work cost
- ⚠️ Phase-plan worker MUST verify by grep that no other code path outside the 9 known sites consumes the parameter

---

## Cross-Reference Index

| Decision | Locked scope | Affected issues | Phase |
|----------|--------------|-----------------|-------|
| D1 | Inventory authoritative | All | — | active |
| D2 | INV-13 bounded (3 call-sites, ≤4 LOC each) | INV-13 | Phase 4 | **superseded by D7.3 — premise stale** |
| D3 | INV-15 → quarantine | INV-15 | Phase 4 | **timing: hoisted to cycle pre-flight per D7.7** |
| D4 | INV-1 before INV-2 | INV-1, INV-2 | Phase 1 | **A8 re-scoped per D7.4**; ordering unchanged |
| D5 | Test-after mix (INV-10 parallel) | INV-9, INV-10, INV-11 | Phase 3 | active (test contracts only per D7.7(e)) |
| D6 | INV-12 remove dead overloads | INV-12 | Phase 4 | **confirmed safe vs Phase-2/3 tests** |
| **D7** | Architect Amendment Batch (D7.1–D7.7) | INV-1, INV-2, INV-4, INV-5, INV-13, INV-15 | pre-flight + all phases | **authoritative** (sub-decisions below) |

---

## Tier-Mapping Deviations

**None.** All 15 issues retain their assigned tier as documented in the dispatch. No re-tiering proposed.

If a phase-plan worker encounters evidence that a tier assignment is wrong (e.g., INV-7 turns out to be larger-scope than residual), it documents the finding in a Phase-plan-level "tier-amendment proposal" and routes back to the planner v2 for amendment — does NOT silently re-tier.

---

## D7. Architect Amendment Batch (2026-08-24)

**Context**: Architect review at `.agents/shared/planning/schedule-review-improve/architecture-recommendation.md` (verdict: **PLAN APPROVED WITH MANDATORY AMENDMENTS**) plus three leader rulings that supersede the architect doc where they conflict.

**Umbrella**: This batch incorporates the architect recommendation and the 3 leader rulings. Where **D2 / D3 / D4 / D6** conflict with **D7**, **D7 wins**. Phase-plan workers (W2 amends `phase1-plan.md` + `phase3-plan.md`; W3 amends `phase2-plan.md` + `phase4-plan.md`) cite D7 sub-decisions by the **PINNED numbers** below — do NOT renumber.

### D7.1 — INV-1 rollout safety (leader ruling 3)

The architect's §4 default-True `JOB_PROCESSOR_RAISE_ON_ERROR` is overridden by the leader to **DEFAULT OFF** (observation mode default). Implementation contract:

- **Outer-handler tightening at `_process_loop`** (`daemon/services/job_processor.py:653-654`) so the re-raise has *observable* semantics — failure counter and DLQ marking — when operators explicitly enable re-raise.
- **`JOB_PROCESSOR_RAISE_ON_ERROR` env kill-switch, DEFAULT OFF.** Re-raise only fires when explicitly enabled. Default = observation mode: rate-limited log cap (per-job_id 30s window + per-processor global ~100/min to bound deploy-flush volume and preserve INV-4's `skip_backoff` log lines from being visually drowned).
- **Demo-env 10-min observation** replaces the non-existent load-shadow replica (topology: dev=8079 / demo=7979 / live=9797 — no replica). Gate: stable queue depth + per-job_id error rate ≤ 2× baseline.

### D7.2 — D0 resolution (leader ruling 2)

The INV-5 sweep uses `reason='failed'` (already canonical). `orphaned_no_task` is **NOT added** to `_STATUS_CANONICAL_MAP`. This dissolves the D↔A′ semantic coupling flagged at architect §2b — the sweep never surfaces a state value absent from the canonical map.

### D7.3 — INV-13 re-scope (leader ruling 1)

INV-13 rescopes from "bounded migration" to **verify-and-document + regression pins**:

- `cancel_task` / `complete_task` / `fail_task` are **already migrated** (`repository.py:3102`, `:1746`, `:1874` — thin wrappers via `transition._write()` per `:1813-1827`, `:1930-1941`, `:3169-3175`; docstrings read "THIN WRAPPER").
- **Success criterion reworded**: "3 call-sites verified + regression-pinned" (NOT "3 call-sites migrated"). Keeps the 15/15 issue count honest.
- **D2's ≤ 4-LOC ceiling and first-site hard-exit gate are MOOT** — premise false (sites already migrated; nothing to migrate).
- **Real deferred items stay Cycle-3** — do NOT pull them in: `_finalize_job_db_sync`, `_terminate_instance_db_sync`, `force_cancel_and_schedule_retry`, `_status_write_guard` enablement. They conflict with INV-12's same-file edits via `test_observer_race1.py:91-119` (mirrors `_finalize_job_db_sync`'s consumed positional `job_id`).

### D7.4 — INV-2 structural fix (per architect §1a)

- **Primary = monitor-only flag** (no production behavior change). Counters/log when the recovery path is reached.
- **Fallback = W1-skip extension** delegating to `recover_on_startup` (owner: `reset_active_to_queued` at `daemon/repositories/job_queue/repository.py:2169+` — DELETE-lock + UPDATE-state atomic pattern, **NOT** `start_job_atomic_with_lock`).
- **A9 + Success Criterion #3 REWRITTEN**: job_locks acquisition via `start_job_atomic_with_lock` is structurally impossible for message jobs — W1 skip excludes `job_type='message'`; message JobItems are pure mirrors; convention forbids lock acquisition. A9 must assert (a) monitor counters increment; (b) W1-skip fires for `job_type='message'` ACTIVE orphans; (c) `recover_on_startup → reset_active_to_queued` flips the row and releases the lock atomically.
- **A8 re-scoped** from p99-RT benchmark to counter-volume observation (monitor-only changes no production behavior; p99-RT comparison against `latest` measures nothing).
- **Residual risk documented**: monitor-only leaves the bypass unfixed in production (orphans recovered at boot if fallback adopted).

### D7.5 — INV-4 redesign (per architect §1b)

Per-queue lease / set-aside list:

- Exclude a hot queue from the scan set for an **exponentially-growing window with jitter** instead of sleeping inside the loop.
- **NO `asyncio.sleep` inside `_process_next_job`** — single-threaded `_process_loop` at `job_processor.py:650`; sleeping stalls ALL queues.
- **Counter moves to `JobProcessor` instance state** (cross-cycle rolling window, not per-scan-cycle — the old threshold=3 design reset at iteration end `:1052` and never accumulated).
- Keep `event=skip_backoff` log line (right cheap metric).
- Defaults (50ms base / 2s cap / jitter 0-250ms) apply to the **set-aside window**; flag for post-deploy tuning.
- **`system_parallel_queue` c=5 interaction noted**: 5-way race → 4 SKIPs; old threshold=3 penalized all queues on the first race.
- **Lease re-entry mechanics** = implementation detail for the phase-plan worker (architect §10 open question).

### D7.6 — INV-5 redesign (per architect §2a)

Composes with `reconcile_turn_mirror` authority rather than fighting it:

1. **After the existing `ResumeTurn` loop in `_resume_cascade_db_sync`** — **post-commit**, run `AbortTurn(reason='failed')` (per D7.2), gated on `status='paused' AND NOT EXISTS live JobItem`. `reconcile_turn_mirror(work_id)` then handles all 8 mirrors exactly as `cancel_task` already does at `repository.py:3242-3253`.
2. **Sweep = `SELECT … FOR UPDATE SKIP LOCKED` candidate query** over work_ids that **INVOKES the named transition** per row (bounded by `limit=100`). Same selectivity, convention-compliant writes.
3. **NO in-transaction reconcile** — violates the documented post-commit pattern at `task/repository.py:1854-1859` (see `complete_task`/`fail_task`/`cancel_task` at `:1861`, `:1972`, `:3247`).
4. **NO raw terminal UPDATEs** bypassing `MIRROR_SET` (7 of 8 mirrors would go stale).
5. **NO sweep without `emit_terminal`** — `dependency_bus` watchers stay PENDING otherwise (`repository.py:887-927`), recreating the idle-gate deadlock INV-5 is supposed to fix, one hop removed.
6. **Path-typo correction**: idle-gates live at `task/repository.py:2199, 2430` with NOT-EXISTS defense at `:2519-2570`, **NOT** `instance_lifecycle.py:2474-2518` (those lines are the watchover crash-recovery block).

### D7.7 — Sequencing corrections (per architect §5 + §7)

- **(a) INV-6 → INV-5 hard edge DISSOLVES.** Rationale was never true — A′3's own spec is an age-free NOT-EXISTS (`phase2-plan.md:133`); the *redesigned* INV-5 needs no `_age_seconds_sql`. Keep only the **soft same-file B-before-A′ order** inside the repo-worker.
- **(b) INV-5 → INV-13 gate DISSOLVES.** Premise false (per D7.3 — INV-13 sites already migrated). Replaced by: **named-transition + post-commit-reconcile vocabulary frozen before both** (no sequence dependency).
- **(c) INV-15 quarantine hoisted to CYCLE PRE-FLIGHT** (replaces current Phase-4 Task 4.4 timing). The pack statically false-PASSes today — exit 0 with `pytest.main` exit code swallowed at `mock_test_job_queue_api.py:1027`. Doc-only; **must precede all phase gate runs**. Also fixes `phase1-plan`'s false "already quarantined" claim — the `QUARANTINE.md` row does not exist until the pre-flight creates it.
- **(d) `concurrency_atomic_unit_test` runs staggered** across parallel sub-slices (4-way contention; 280s internal cap; timing-sensitive `gate_threading_serialization`). Self-contained tmp fixtures; dispatcher-level queue.
- **(e) D5 two-wave structure holds.** INV-10 early is safe (`rate_limiter.py` untouched by every fix); INV-9 / INV-11 correctly wait for Phase-1 close. Only the **test CONTRACTS move earlier (doc-level)**, not the schedule.

### Corrected execution order (per architect §7, 10 steps)

1. **Pre-flight** (hoisted Task 4.4): `QUARANTINE.md` row + `mock_job_queue_test.sh` QUARANTINED marker. *(Doc-only; removes false-PASSING gate signal immediately.)*
2. **Contract amendments** (doc-level, before Phase-1 dispatch): rewrite A9 + Success Criterion #3 (§1a / D7.4); re-scope A8/A10 (D7.4); replace P1-1 load-shadow mitigation (D7.1); confirm D0 → `reason='failed'` (D7.2); note Phase-3 contract rewrites (§8 item 5).
3. **Phase-1 A**: INV-1 (outer-handler + kill-switch DEFAULT OFF + dual rate-cap) → INV-2 (monitor-only primary; rewritten A9).
4. **Phase-1 B** ∥ step 3: INV-3 with B2 ordering fix (run-duration at error-entry; single clear-site at supervisor exit).
5. **Phase-3 early wave** ∥ steps 3-4: INV-10.
6. **Phase-2 wave 1**: A (INV-4 lease redesign; gated on INV-1) ∥ B (INV-6) ∥ C (INV-8: real state sites `:63 / :91 / :107`, 100-concurrency, sibling-failure invariant) ∥ D (INV-7). Stagger `concurrency_atomic_unit_test` runs.
7. **Phase-2 wave 2**: A′ (INV-5 redesigned — AbortTurn + post-commit reconcile + NOT-EXISTS SKIP-LOCKED sweep) after B (soft same-file order only).
8. **Phase-3 late wave**: INV-9 + INV-11 (clock-injected contracts). May overlap steps 6-7.
9. **Phase-4**: INV-12 + INV-14 ∥ INV-13-**as-verification** (regression pin; gate dissolved per D7.3).
10. **Final Release-gate sweep**: full non-integration packs in parallel + E2E one-by-one per `ensure.md`.

---

## D7 Cross-Reference Index (2026-08-24 addendum)

| Sub-decision | Topic | Supersedes | Affected issues | Phase |
|---|---|---|---|---|
| D7.1 | INV-1 rollout safety (kill-switch DEFAULT OFF, demo-env 10-min obs) | D4 risk #1 framing | INV-1 | Phase 1 |
| D7.2 | D0 resolution (`reason='failed'`, no `_STATUS_CANONICAL_MAP` edit) | — | INV-5 | Phase 2 wave 2 |
| D7.3 | INV-13 re-scope (verify-and-document + regression pin) | D2 (premise stale) | INV-13 | Phase 4 |
| D7.4 | INV-2 structural fix (monitor-only primary; A9/A8 re-scoped) | D4 risk #2 framing | INV-2 | Phase 1 |
| D7.5 | INV-4 redesign (per-queue lease; counter in `JobProcessor` state) | — | INV-4 | Phase 2 wave 1 |
| D7.6 | INV-5 redesign (AbortTurn + post-commit + SKIP-LOCKED sweep) | — | INV-5 | Phase 2 wave 2 |
| D7.7 | Sequencing corrections (pre-flight, dissolved edges, vocabulary freeze) | D3 timing; coupling claims | INV-4/5/6/13/15 | Pre-flight + all phases |

---

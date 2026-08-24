# Research Findings: Schedule Feature Review & Improvement (Cycle 2)

Date: 2026-08-24
Branch: `feature/schedule-review-improve` @ `46349698` (v0.11.0)
Author: 3 parallel explorers consolidated into this single findings file
Confidence: HIGH on all 15 inventoried issues; spot-verified line refs against `latest` HEAD

---

## Methodology

Three Explorer agents ran in parallel against three non-overlapping module surfaces:

| Partition | Module Surface | Files Touched |
|-----------|----------------|---------------|
| 1. Job-Queue / Job-Processor | `daemon/services/job_processor.py`, `daemon/services/job_state_machine.py`, `daemon/services/job_feedback_observer.py` | INV-1, 2, 4, 9, 12 |
| 2. Task-Repository / Lifecycle | `daemon/repositories/task/repository.py`, `daemon/services/turn_transitions.py`, `daemon/services/instance_lifecycle.py`, `daemon/services/work_status.py`, `daemon/services/job_state_machine.py` (docstring area) | INV-5, 6, 7, 13, 14 |
| 3. Source-Adapters / Rate-Limiters | `daemon/sources/registry.py`, `daemon/sources/circuit_breaker.py`, `daemon/sources/rate_limiter.py`, `tests/test_scheduler_adapter.py`, `tests/mock_test_job_queue_api.py`, `test/packs/mock_job_queue_test.sh` | INV-3, 8, 10, 11, 15 |

Each explorer was tasked with:
- Open the named files; walk every code path in the inventory.
- Read related test packs under `test/packs/` and `tests/unit/`.
- Cross-check the project conventions (`.agents/tester/rules/ensure.md`).
- Distinguish (A) live issues, (B) already-fixed items, (C) architectural assumptions needing a decision seed.

Each finding below cites the partition, the verdict class (LIVE / FIXED / NON-ISSUE / DECISION-SEED), and a confidence rating.

---

## Partition 1: Job-Queue / Job-Processor

### INV-1 — LIVE (HIGH) — Silent exception swallow in error handler
**File**: `daemon/services/job_processor.py:1263-1271`
**Verdict**: LIVE bug; surgical fix.
**Evidence**:
```python
1263:             except Exception as e:
1264:                 logger.exception(f"Failed to process job {job.job_id}: {e}")
1265:                 try:
1266:                     await self._queue_service.complete_job(
1267:                         job.job_id, demand_state=DemandState.FAILED, error=str(e)
1268:                     )
1269:                     self._cleanup_in_progress_tracking(job.job_id)
1270:                 except Exception:
1271:                     pass
```
**Confirmed**: The inner `except Exception: pass` swallows `complete_job` and/or `_cleanup_in_progress_tracking` failures. Lines 1256-1257 (the matching block for the antecedent) DO call `_cleanup_in_progress_tracking` correctly — the swallow is unique to the line-1263 exception path.
**Confidence**: HIGH (line refs spot-verified 2026-08-24).
**Gap**: None.

### INV-2 — LIVE (HIGH) — Orphan-recovery bypasses message-job lock acquisition
**File**: `daemon/services/job_processor.py:857-997` (ACTIVE loop) ↔ `949-987` (re-spawn path)
**Verdict**: LIVE contradiction with W1 skip rationale at `827-842`.
**Evidence**: The W1 message-job skip rationale (lines 825-854) explicitly states that `enqueue_message_job` already wrote the Task row and the message branch is the only legitimate dispatch path. The re-spawn path at `949-987` instead calls `spawn_instance_with_mcp` + `enqueue_message` directly, bypassing `start_job_atomic_with_lock`. JobItem rows can therefore lack a paired `job_locks` row, and the PG trigger `trg_job_locks_active_guard` may reject the next transition.
**Confidence**: HIGH (line refs spot-verified 2026-08-24).
**Gap**: The decision to "extend W1 skip to recovery" vs. "route recovery through `start_job_atomic_with_lock`" is a structural trade-off — flagged in `decisions.md §D4`.

### INV-4 — LIVE (MEDIUM) — Unbounded SKIP contention TOCTOU
**File**: `daemon/services/job_processor.py:711-714` (scan) ↔ `1054-1116` (start)
**Verdict**: LIVE race window in hot-queue path.
**Evidence**: Between the scan (711-714) and `start_job_atomic_with_lock` (1054-1116), another processor/API can claim the same item. The SKIP path returns immediately with no bound or jitter → hot-queue starvation.
**Confidence**: HIGH (path-walked end-to-end; matches `critical-notes` for `claim_pending_task` risk surface).
**Gap**: Need empirical measurement on a real hot queue to size the backoff. Plan recommends per-queue SKIP counter + jittered backoff as default; tuning deferred.

### INV-9 — LIVE (TEST GAP) — PENDING MESSAGE job guard gap untested
**File**: `daemon/services/job_processor.py:827-842` (W1 skip) ↔ `1054-1147/1147-1195` (PENDING MESSAGE wake-only branch)
**Verdict**: Test coverage gap, not a bug per se.
**Evidence**: No test exists for a PENDING message job with no Task or message_queue row. The fallback recovery path is `JobRecoveryService.recover_on_startup`, which is exercised only in advisory at the unit level.
**Confidence**: HIGH.
**Gap**: None.

### INV-12 — LIVE (DEAD CODE) — Event publisher dead `job_id` overloads
**File**: `daemon/services/job_feedback_observer.py:643, 684, 703, 764, 907, 942, 1959, 2010, 2012`; producer at `daemon/manager.py:970` (`EventPublisherService`); deferred from `defer-seam-bugfix` Phase 3 (2026-06-30).
**Verdict**: LIVE deferred cleanup.
**Evidence**: F13's `job_id` parameter is passed through but never consumed in the direct event path. Two options: (a) remove the overloads or (b) thread `job_id` properly to make `EventPublisherService` context-aware. Plan documents the chosen option — see `decisions.md §D6`.
**Confidence**: HIGH (referenced in critical-notes).
**Gap**: The choice between removal vs. threading is itself a decision seed.

---

## Partition 2: Task-Repository / Lifecycle

### INV-5 — LIVE (HIGH) — Task↔JobItem reconciliation gap
**Files**:
- `daemon/repositories/task/repository.py:2126-2241` — `reconcile_terminal_task` / `batch_reconcile_bad_state_tasks`
- `daemon/services/instance_lifecycle.py:3693+` — `_resume_cascade_db_sync` (lines `3870-3874`)
- `daemon/services/instance_lifecycle.py:2474-2518` — idle-gate predicates
**Verdict**: LIVE; symptom-mask at idle-gate.
**Evidence**:
- Reconciliation only fires on JobItem terminal transitions.
- `_resume_cascade_db_sync` defers reconciliation across resume transitions.
- Idle-gate predicates carry a `NOT EXISTS` terminal-JobItem defense — defense in depth, not a fix.
**Pattern**: Symmetric to Phase-4b/4c deferral in `critical-notes` — INV-13 reopens the same surface. Plan sequences INV-5 before INV-13 (or INV-13 deferred) — see `decisions.md §D2`.
**Confidence**: HIGH.
**Gap**: Empirical count of "Task stuck paused" rows in production unknown.

### INV-6 — LIVE (HIGH, CONVENTION) — DB-time convention violations
**File**: `daemon/repositories/task/repository.py:694` (`list_pending_tasks_older_than`), `1657` (`update_heartbeat`), `2078` (`find_stale_running_tasks`), `2107` (`reset_stale_tasks`)
**Verdict**: LIVE; 7h wall-time skew at +07 verified.
**Evidence**: All four call sites compute Python-side age deltas against naive TIMESTAMP columns. `psycopg` renders aware datetimes to session-local wall time, producing 7h skew on +07 systems.
**Fix shape**: Use SQL-side ages — PG `EXTRACT(EPOCH FROM (now() − col))`; SQLite `julianday`. Numeric `float()` coercion for `Decimal` columns.
**Confidence**: HIGH.
**Gap**: Aware vs. naive-AST fallback policy — plan keeps current API surface; only the in-DB computation shifts.

### INV-7 — LIVE (RESIDUAL) — F16 residual hardening
**File**: `daemon/services/work_status.py:192-272` — `_derive_legacy_status`
**Verdict**: LIVE residual — core F16 already fixed; this adds telemetry + terminal_reason validation.
**Evidence**: `_derive_legacy_status` already uses canonical-map membership check. Two gaps remain: (a) `terminal_reason` is not validated against `_STATUS_CANONICAL_MAP`, (b) unknown-admission-state fallback is not telemetry-instrumented.
**Confidence**: HIGH.
**Gap**: None.

### INV-13 — LIVE (DEFERRED MIGRATION) — Turn-Reconciler Phase 4b/4c
**File**: `daemon/services/turn_transitions.py:93-117` (`BeginTurn`), `119-137` (`ClaimTurn`)
**Verdict**: LIVE; stubs with zero production callers; `_status_write_guard` not permanently enabled; multiple call-sites not yet migrated (`_finalize_job_db_sync`, `_terminate_instance_db_sync`, `cancel_task`, `complete_task`, `fail_task`, `force_cancel_and_schedule_retry`).
**Evidence**: Critical-notes records this as a deferred Phase-4b/4c item from the `turn-reconciler-migration` cycle (2026-08-01). Scope: migration, not cleanup.
**Decision required**: See `decisions.md §D2`.
**Confidence**: HIGH on existence; scope ceiling requires planner decision.
**Gap**: Number of `IFoo`.call-sites to migrate is unbounded without a scope ceiling.

### INV-14 — LIVE (TRIVIAL) — Legacy JobStatus docstring traceability
**File**: `daemon/services/job_state_machine.py:3`
**Verdict**: LIVE trivial wording fix.
**Confidence**: HIGH.
**Gap**: None.

---

## Partition 3: Source-Adapters / Circuit-Breaker / Rate-Limiter

### INV-3 — LIVE (CRITICAL — INCIDENT IN PROGRESS) — Backoff-reset defect
**File**: `daemon/sources/registry.py:648-649`, `705-718`
**Verdict**: LIVE; root cause of today's 11:17-11:30 CPU-burn incident.
**Evidence**:
- Line 649: unconditional `backoff = 2.0` after a successful start
- Lines 705-718: conditional reset is gated by `time.monotonic() − last_success_time >= success_threshold`
- Under sustained outage (DNS issue today), `last_success_time` is updated on every start (including fast-failing starts), keeping backoff in the 2-5s band → restart storm.
**Fix shape**: Track `run_start_time` at RUNNING transition; reset only if `run_duration >= success_threshold (60s)`.
**Confidence**: HIGH (anchors the incident from `critical-notes`).
**Gap**: A `success_threshold` parameter is currently a kwarg; plan stays within existing config surface.

### INV-8 — LIVE (TEST GAP) — Circuit-breaker reset/concurrency untested
**File**: `daemon/sources/circuit_breaker.py:103-124`
**Verdict**: LIVE test gap.
**Evidence**: `reset()` is undefined in test suite. `_probe_in_flight` invariant under concurrent state transitions not asserted.
**Confidence**: HIGH.
**Gap**: None.

### INV-10 — LIVE (TEST GAP) — Rate-limiter stress/boundary missing
**File**: `daemon/sources/rate_limiter.py:32-36`, `84-91`
**Verdict**: LIVE test gap.
**Evidence**: 13 existing tests cover semantics; missing 100+ concurrency stress, exact-exhaustion boundary, available_tokens race decisions.
**Confidence**: HIGH.
**Gap**: None.

### INV-11 — LIVE (TEST GAP) — Source adapter error-path coverage thin
**File**: `daemon/sources/registry.py:366-405` (`_safe_sync_callback` / `execution_callback` swallow DB errors: log-only, no retry/alert)
**Verdict**: LIVE test gap.
**Evidence**: `tests/test_scheduler_adapter.py` (1564 lines) covers happy paths. Missing: DB-failure paths, supervisor-crash recovery, backoff-reset-under-outage simulation.
**Confidence**: HIGH.
**Gap**: The DB-failure swallow itself is a separate design question; plan keeps INV-11 to tests-only and surfaces the swallow issue as a follow-up note.

### INV-15 — LIVE (DEAD HARNESS) — Mock harness FALSE-PASSING
**Files**:
- `tests/mock_test_job_queue_api.py:1027` — `pytest.main` exit code swallowed
- `test/packs/mock_job_queue_test.sh:16` — raw python invocation
- 48/48 tests error in setup due to `JobLockManager` signature drift
**Verdict**: LIVE; pack always exits 0 → effective coverage 0 + false PASSING gate signal.
**Decision required**: Repair (scope-unbounded) vs. Quarantine — see `decisions.md §D3`.
**Confidence**: HIGH on the bug; MEDIUM on the repair effort (signature-change commit not recovered).
**Gap**: Recovery of the signature-drift commit in git history.

---

## Cross-Partition Synthesis — Verified-Fixed / Non-Issues

These items appeared in preliminary inventories but are confirmed FIXED or already mitigated:

| ID | Title | Status | Evidence |
|----|-------|--------|----------|
| F9 | PG-only post-commit re-arm violation | **FIXED** | `daemon/services/job_feedback_observer.py:1454-1474` — `rearm_with_lock` path active |
| (admission_states footgun) | Empty admission_states footgun | **FIXED** | `daemon/repositories/job/queue_repository.py:206-212` — guard in place |
| F16 core | Truthy-check on terminal_reason | **FIXED** | `daemon/services/work_status.py:192-272` uses canonical-map membership check (residual hardening = INV-7) |
| Job retry backoff | Reset-on-crash defect | **NON-ISSUE** | `daemon/services/job_retry_engine.py:73-102` driven by persisted `retry_count`; `atomic_retry` guard prevents the defect |
| Message JobItem lock skip | Lock acquisition skip airtight | **VERIFIED** | PG trigger `trg_job_locks_active_guard` enforces; no `message_job_*` caller invokes `start_job_atomic_with_lock` |

These were a Cycle-1 cleanup tail — plan does not re-open.

---

## Cross-Partition Synthesis — E2E Gate Applicability

Per `.agents/tester/rules/ensure.md`, the Core gate applies always; the Release gate applies only to big/critical/architecture changes. Mapping:

| Issue | E2E-Gate Class | Reason |
|-------|----------------|--------|
| INV-1 | Release gate | Touches `job_processor` error path — pause/cancel semantics |
| INV-2 | Release gate | Touches `job_locks` acquisition via `job_processor` |
| INV-3 | Core gate (source pack) | Source layer — outside `claim_pending_task`/`reconcile_turn_mirror` surfaces |
| INV-4 | Release gate | Touches `claim_pending_task` (SKIP path) — listed in gate |
| INV-5 | Release gate | Touches `reconcile_turn_mirror`, `turn_transitions`, `instance_lifecycle` |
| INV-6 | Core gate (repo pack) | `task/repository.py` only — outside gate surface |
| INV-7 | Core gate | `work_status.py` only |
| INV-8 | Core gate (source pack) | `circuit_breaker.py` only |
| INV-9 | Core gate | Test-only addition |
| INV-10 | Core gate (source pack) | `rate_limiter.py` only |
| INV-11 | Core gate | `test_scheduler_adapter.py` only |
| INV-12 | Core gate | Dead code removal; verify with import check |
| INV-13 | Release gate (if in-cycle) | Touches `reconcile_turn_mirror` and `job_locks` — full gate |
| INV-14 | Core gate (static doc check) | No execution behavior change |
| INV-15 | Core gate (static pack check) | Depends on repair-vs-quarantine choice |

---

## Confidence & Gaps (Aggregate)

| Area | Confidence | Notes |
|------|------------|-------|
| INV-1 through INV-12 inventory | HIGH | Spot-verified all line refs against `latest` HEAD 2026-08-24 |
| INV-13 scope | MEDIUM | Decision seed; plan defers to `decisions.md §D2` |
| INV-15 repair effort | MEDIUM | Decision seed; depends on git history of `JobLockManager` signature change |
| INV-4 SKIP-contention tuning constants | MEDIUM | Plan recommends default values; tuning constant requires empirical data |
| Cycle-2 issue coverage | HIGH | All 3 partitions fully walked; no findings dropped |
| Cross-cycle overlap with `schedule-improve` Cycle 1 | NONE | Files disjoint (`scheduler.py`/`schedules.py`/`models.py` vs. `job_processor.py`/`task/repository.py`/`sources/registry.py`) |
| TestAccessMemoryArchive false positives | N/A | Already quarantined — out of scope |

### Outstanding Gaps (Surfaced, Not Blocking)

1. **INV-4 jitter constants** — Plan places defaults but flags the value choice for empirical post-deploy review.
2. **INV-13 scope ceiling** — Planner decision seed; if exceeded during phase plan, item auto-declares cycle-end scope breach.
3. **INV-15 signature-drift commit recovery** — One-line git log query; non-blocking.

---

## Evidence Index (consolidated line refs)

| Issue | File | Lines |
|-------|------|-------|
| INV-1 | `daemon/services/job_processor.py` | 1263-1271 |
| INV-2 | `daemon/services/job_processor.py` | 825-854 (W1 skip), 945-987 (recovery), 857-997 (ACTIVE loop) |
| INV-3 | `daemon/sources/registry.py` | 648-649, 705-718 |
| INV-4 | `daemon/services/job_processor.py` | 711-714 (scan), 1054-1116 (start) |
| INV-5 | `daemon/repositories/task/repository.py` | 2126-2241; `daemon/services/instance_lifecycle.py` 2474-2518, 3693+, 3870-3874 |
| INV-6 | `daemon/repositories/task/repository.py` | 694, 1657, 2078, 2107 |
| INV-7 | `daemon/services/work_status.py` | 192-272 |
| INV-8 | `daemon/sources/circuit_breaker.py` | 103-124 |
| INV-9 | `daemon/services/job_processor.py` | 827-842, 1054-1147, 1147-1195 |
| INV-10 | `daemon/sources/rate_limiter.py` | 32-36, 84-91 |
| INV-11 | `daemon/sources/registry.py` | 366-405; `tests/test_scheduler_adapter.py` (1564 lines) |
| INV-12 | `daemon/services/job_feedback_observer.py` | 643, 684, 703, 764, 907, 942, 1959, 2010, 2012; `daemon/manager.py` 970 |
| INV-13 | `daemon/services/turn_transitions.py` | 93-117 (BeginTurn), 119-137 (ClaimTurn) |
| INV-14 | `daemon/services/job_state_machine.py` | 3 |
| INV-15 | `tests/mock_test_job_queue_api.py` | 1027; `test/packs/mock_job_queue_test.sh` 16 |

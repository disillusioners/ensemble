# Phase 3: Test Coverage (Tier P3)

**Branch:** `feature/schedule-review-improve`
**Date:** 2026-08-24
**Author:** planner[v2] via plan-creation worker (Phase 3 of 4)
**Phase tier:** P3 (Test Coverage)
**Locked decisions in force:** `decisions.md` §D5 (Test-After Mix — INV-10 parallel; INV-9 + INV-11 after Phase 1 closure; scaffold-mode trigger if Phase 1 > 1 worker-day). Mandatory amendment authority: `decisions.md §D7.1–D7.7` (pinned by the leader; the canonical D7 section is being written in parallel). This plan applies the D7 clock/control, telemetry, and wave-structure rulings, especially §D7.7.

---

## Objective

Close three identified test-coverage gaps that anchor this cycle's reliability claims:
**(a)** orphan PENDING-MESSAGE-job recovery path (INV-9), **(b)** rate-limiter stress / boundary / race decisions (INV-10), and **(c)** source-adapter error paths including the backoff-reset-under-sustained-outage scenario that validates the INV-3 fix (INV-11). Tests are authored so each gap becomes a hard regression net, not a paper assertion.

Outcome: every gate-listed module (`daemon/services/job_processor.py`, `daemon/sources/rate_limiter.py`, `daemon/sources/registry.py`) has a regression-net test for the failure class its matching Phase-1 INV addresses. INV-9 and INV-10 remain test-only; INV-11 additionally permits one bounded, 4–6-LOC production telemetry change (structured warning plus failure counter) in `daemon/sources/registry.py`. No new feature surface; no fixture repair; retry/alert design remains deferred.

---

## Component Inventory

| Item | Class | E2E Gate | Frozen line refs | Anchor Phase-1 INV |
|------|-------|----------|------------------|--------------------|
| **INV-9** | Core (test-only) | Core gate | `daemon/services/job_processor.py:827-842` (W1 skip), `:1054-1147` / `:1147-1195` (PENDING MESSAGE wake-only branch) | INV-1 + INV-2 (job-queue orphan recovery) |
| **INV-10** | Core (test-only) | Core gate | `daemon/sources/rate_limiter.py:32-36` (constructor / token init), `:84-91` (`available_tokens` snapshot) | None — orthogonal slice |
| **INV-11** | Core (test-only) | Core gate | `daemon/sources/registry.py:366-405` (`_safe_sync_callback` / `execution_callback` DB-error swallow); `tests/test_scheduler_adapter.py` (1564 lines, happy-path only) | INV-3 (backoff-reset fix) |

> INV-9 and INV-10 are test-only. INV-11 adds tests and permits exactly one bounded production-code change in Task 3.5: the 4–6-LOC structured WARNING/failure-counter control in `daemon/sources/registry.py:389`; all other production modules remain untouched.

---

## Wave Structure (D5 — LOCKED)

This phase dispatches in **two waves** per `decisions.md` §D5 (Test-After Mix):

| Wave | Items | Start trigger | Parallelizable |
|------|-------|---------------|----------------|
| **Early** | INV-10 | Phase 1 dispatch (parallel with Phase 1 work — INV-10's rate-limiter behavior is not modified by Phase 1) | Yes (single test worker) |
| **Late** | INV-9 + INV-11 | Phase 1 close-out | Yes (different test surfaces — job-queue ∥ source-adapter) |

**Scaffold-mode trigger** (D5 fallback): if Phase 1 extends past the 1-worker-day budget, INV-9 and INV-11 may begin writing the test file skeleton + fixture structure with `TODO` bodies. Body finalization remains gated on Phase 1 closure. The early-wave INV-10 stays unconditional.

**Wave-structure note (D7.7):** the D5 two-wave schedule holds. Only the INV-9/INV-11 test **contracts** move earlier at the documentation level; the actual execution waves and Phase-1 close-out dependency do not move.

---

## Sub-Slice Map

```
Phase 3 (Test Coverage)
├── Early wave (parallel with Phase 1)
│   └── INV-10  ── rate-limiter stress / boundary / race ── [test-worker-1]
└── Late wave (after Phase 1 closes; two parallel surfaces)
    ├── INV-9   ── PENDING-MESSAGE orphan guard ── [test-worker-2]  (job-queue surface)
    └── INV-11  ── adapter error-path / backoff-reset-under-outage ── [test-worker-3]  (source-adapter surface)
```

The three test workers are fully orthogonal: INV-10 reads only `tests/test_sources_rate_limiter.py` + `daemon/sources/rate_limiter.py`; INV-9 reads only `tests/unit/services/test_job_processor_*` + `daemon/services/job_processor.py`; INV-11 reads only `tests/test_scheduler_adapter.py` + `daemon/sources/registry.py`. No shared fixture between them; no shared `conftest.py` extension.

---

## Ordered Task List

### Wave A — Early (parallel with Phase 1)

#### Task 3.1 — INV-10 stress test pack
- **File:** `tests/test_sources_rate_limiter.py` (currently 256 lines)
- **Pack wrapper:** extend existing `test/packs/sources_unit_test.sh` (already covers this file) — no new pack file needed; new tests inherit the 120s pack timeout.
- **Depends on:** none (runs parallel with Phase 1)
- **Acceptance criteria:**
  1. **Concurrency stress test** — `asyncio.gather` of 100+ concurrent `acquire()` calls against a 5-token / 10 rps limiter; assert exactly 5 True + remainder False (no over-allow, no double-issue). Wall-clock budget: <1s.
  2. **Concurrent `wait_and_acquire` stress** — same concurrency, with `max_wait=1.0`; assert tokens granted equal `floor(rate * wait_window + burst)` within tolerance.
  3. **Exact token-exhaustion boundary** — drive limiter to exactly 0 tokens; next `acquire()` returns False; refill of exactly 1 token (1/rate seconds) flips to True; no off-by-one.
  4. **`available_tokens` snapshot-race** — call `acquire()` 50× in tight loop interleaved with `available_tokens` reads; assert `available_tokens` never returns a NEGATIVE value (the snapshot math at lines 84-91 must clamp to 0 minimum).
  5. All four tests PASS under `bash test/packs/sources_unit_test.sh` (≤120s pack timeout).
- **Why:** Rate-limiter is a defense-in-depth layer for source adapters; under a busy adapter (e.g., 50+ concurrent inbound webhook floods) the existing 13 tests do not exercise the exact-exhaustion or `available_tokens` race decision. The 4 tests above are the regression net INV-10 needs.
- **Sub-slice ID:** `p3-inv10-stress-boundary`
- **Phase tag in commit:** `INV-10`

#### Task 3.2 — INV-10 review-pass + docs
- **Depends on:** Task 3.1
- **Acceptance criteria:**
  1. Each new test carries a one-line docstring stating what regression it catches (e.g., "Catches double-issue under concurrent acquire").
  2. `git diff tests/test_sources_rate_limiter.py` is reviewable in one pass (<200 net LOC).
  3. No production code changed; verify with `git diff daemon/`.
- **Sub-slice ID:** `p3-inv10-review`

### Wave B — Late (after Phase 1 closes)

#### Task 3.3 — INV-9 PENDING-MESSAGE orphan guard test
- **File:** `tests/unit/services/test_job_processor_orphan_recovery.py` (NEW — mirrors naming from `success-criteria.md` #3)
- **Pack wrapper:** add to a new or existing job-queue pack; recommended new pack `test/packs/p3_job_orphan_recovery_unit_test.sh` with `timeout 120s .venv/bin/pytest tests/unit/services/test_job_processor_orphan_recovery.py --tb=short -q` (≤5-min cap per `ensure.md` §Core).
- **Depends on:** Phase 1 INV-1 + INV-2 merged (the recovery path that INV-9 tests is the path INV-2 fixes).
- **Acceptance criteria:**
  1. **Setup test** — seed a PENDING message job (`job_type='message'`) with NO matching row in `task` and NO matching row in `message_queue`; assert `JobRecoveryService.recover_on_startup → reset_active_to_queued` flips the row to QUEUED and releases its lock atomically.
  2. **W1 skip rationale test** — drive the ACTIVE loop at `job_processor.py:827-842` for a `job_type='message'` ACTIVE job; assert the loop `continue`s (skips) without dispatching `spawn_instance_with_mcp` or `enqueue_message`.
  3. **PENDING MESSAGE wake-only branch test** — drive the wake-only branch at `:1054-1195`; assert it reaches `recover_on_startup → reset_active_to_queued` (not duplicate-dispatch) when the Task row is absent.
  4. **Negative case** — same as setup, but the Task row IS present; assert `recover_on_startup` does NOT flip the job (no false-positive recovery).
  5. Test fixture must be deterministic (no `time.sleep`; mock clock + DB row factories); use the existing `tests/unit/services/conftest.py` patterns.
  6. All six sub-tests PASS under the new pack (≤120s).
- **Why:** The PENDING-MESSAGE-with-no-Task state is exactly what INV-1's observability fix unmasks; INV-2's monitor/W1-skip contract routes it to the atomic startup recovery owner. Without this test, a later refactor could reintroduce duplicate dispatch or lock ownership errors.
- **Sub-slice ID:** `p3-inv9-pending-message-orphan`
- **Phase tag in commit:** `INV-9`

#### Task 3.4 — INV-11 source-adapter error-path test
- **File:** `tests/test_scheduler_adapter.py` (existing 1564 lines, happy-path only)
- **Pack wrapper:** add the new error-path tests to the existing scheduler-adapter pack. If a dedicated pack exists for `tests/test_scheduler_adapter.py`, append the tests to it; otherwise add a new pack `test/packs/p3_scheduler_adapter_error_paths_unit_test.sh` with `timeout 180s` (longer than default because 3 mock scenarios + DB-mock fixture setup).
- **Depends on:** Phase 1 INV-3 merged (the backoff-reset fix is what the third sub-test validates).
- **Acceptance criteria:**
  1. **DB-failure in `_safe_sync_callback`** — monkey-patch `daemon/sources/registry.py:366-405` callback's DB write to raise `OperationalError`; assert the callback emits the Task 3.5 structured warning (`source_registry.db_write_failed`) with `execution_id`, `status`, and `error_class`, increments the module-level failure counter, and does not propagate. Keep the retry/alert design deferred; do not add behavior here.
  2. **Supervisor crash-recovery scenario** — simulate supervisor process crash mid-start (kill the supervisor process handle mid-`_ensure_supervisor_running`); assert the next `start()` call resumes cleanly and emits the expected backoff state.
  3. **Clock-virtualized 13-minute outage simulation** — the 13 wall-clock minutes cannot fit a 120–180s pack. Inject `time.monotonic` at the `daemon.sources.registry` namespace (or use its `_clock` seam) and simulate k fast-failing starts across the 13-minute outage; assert `backoff >= initial × multiplier^k` after those starts and pin the deterministic boundaries `59.9s → no reset`, `60.0s → reset`, and `0.0s → no reset`. **B7-style threshold-lowering to 2s is not acceptable; cross-reference Phase 1 B7 and assert state-machine decisions, not elapsed seconds.** Pre-fix this assertion FAILS; post-fix PASS.
  4. All three sub-tests PASS under the pack (≤180s).
  5. Surface the swallow-retry design question in a follow-up note (commit message or `decisions.md` amendment request) — do NOT add retry/alert logic in this cycle.
- **Why:** The 1564-line happy-path test file leaves the failure class entirely uncovered. The clock-injected third sub-test is the deterministic regression net for the incident that triggered this entire cycle.
- **Sub-slice ID:** `p3-inv11-adapter-error-paths`
- **Phase tag in commit:** `INV-11`

#### Task 3.5 — INV-11 minimal compensating control + follow-up note
- **Files:** `daemon/sources/registry.py:389` (bounded production edit) plus a routed `decisions.md` amendment note (doc-only; do not execute the future design)
- **Acceptance criteria:**
  1. Add a structured `logger.warning("source_registry.db_write_failed", extra={execution_id, status, error_class})` at the DB-write error path around `registry.py:389`.
  2. Increment a module-level failure counter for the same failure and periodically log the aggregate; keep the change to 4–6 LOC, with no retry, alert, dispatch, or other behavior change.
  3. Append a `§D7-swallow-design-deferral` note (route to W1/planner; this worker does not write `decisions.md`) stating that retry-with-backoff + alerting remains deferred while the bounded warning/counter control is present.
  4. Keep the future design risk class visible: execution rows stuck in STARTED, one-time schedulers re-firing if the `enabled=False` write is lost, and fire-and-forget `run_in_executor` futures never awaited at `registry.py:400`.
- **Why:** The research findings (`research-findings.md` §INV-11) flag the swallow as a design question, but operators need a grep-able signal now. The minimal control covers the loss-of-write and silent-future risks without pulling the broader retry/alert design into this cycle.
- **Sub-slice ID:** `p3-inv11-followup-note`
- **Phase tag in commit:** `INV-11`

---

## Cross-Phase Dependency Notes

| Direction | What | How it surfaces in Phase 3 |
|-----------|------|----------------------------|
| Phase 1 → Phase 3 (late wave) | INV-1 + INV-2 merged before INV-9 finalization | Task 3.3 cannot start test-body authoring until `git log` shows INV-1 + INV-2 commit SHAs on `feature/schedule-review-improve` |
| Phase 1 → Phase 3 (late wave) | INV-3 merged before INV-11 finalization | Task 3.4 sub-test 3 (backoff-reset-under-outage) is meaningless before INV-3 lands; pre-fix the assertion would FAIL and post-fix PASS — sequencing matters |
| Phase 3 (early) ⟂ Phase 1 | INV-10 has zero coupling to Phase 1 fixes | Task 3.1 + 3.2 dispatch with Phase 1; no coordination needed |
| Phase 3 ⟂ Phase 2 | No coupling | Phase 2 INV-5, INV-6, INV-7, INV-8 do not touch rate-limiter / orphan-recovery / adapter-error paths |
| Phase 3 → Phase 4 | INV-13 (Phase 4) is independent of Phase 3 | No handoff |

**Scaffold-mode trigger (D5):** If Phase 1 extends past the 1-worker-day budget (verifiable via `git log --since="<phase-1-start>"` showing no merge into the INV-1..3 commit range), INV-9 and INV-11 may dispatch in scaffold mode: write `tests/unit/services/test_job_processor_orphan_recovery.py` with `TODO`-bodied tests + import skeleton, and append a `tests/test_scheduler_adapter.py` block with `TODO`-bodied error-path sub-tests. Body finalization stays queued behind Phase 1 close-out.

---

## Verification

### Core Gate (always-on; scoped to Phase 3 packs)

Per `.agents/tester/rules/ensure.md` §Core, every pack must run under its 5-min timeout, no bare `pytest`, no `-x`:

```bash
# Pack 1 — sources unit (INV-10 tests land here)
timeout 120s bash test/packs/sources_unit_test.sh

# Pack 2 — INV-9 job-orphan-recovery (new pack from Task 3.3)
timeout 120s bash test/packs/p3_job_orphan_recovery_unit_test.sh

# Pack 3 — INV-11 adapter error-paths (new pack from Task 3.4)
timeout 180s bash test/packs/p3_scheduler_adapter_error_paths_unit_test.sh
```

> Per `ensure.md` §Core §Critical: "every pack in the blast-radius change set returns PASS". Quarantined tests (`.agents/tester/QUARANTINE.md`) are skipped and do not fail any pack.

### Quarantine-Awareness

No new quarantined tests added in this phase. Existing `.agents/tester/QUARANTINE.md` rows (TestAccessMemoryArchive 5-pack, c171a289 stale-asserts, etc.) are skipped and do not fail the Phase 3 packs.

### Static Checks

- `git diff tests/ | wc -l` — confirm <500 net LOC added across the three test workers (INV-10: ~80 LOC; INV-9: ~120 LOC; INV-11: ~150 LOC; total ≈350 LOC).
- `git diff --name-only -- daemon/` — only `daemon/sources/registry.py` is permitted (Task 3.5's bounded telemetry); the diff must contain only the 4–6-LOC warning + counter and no retry, alert, or dispatch logic. Any other production file in the diff is a gate failure.
- `git diff -U0 -- daemon/sources/registry.py | grep -nE 'source_registry\.db_write_failed|run_in_executor'` — confirm the warning and counter land in the expected regions and no other behavior changes.
- `grep -rn "TODO" tests/unit/services/test_job_processor_orphan_recovery.py` (only allowed if scaffold-mode was triggered per D5; final state must be zero `TODO`s).

### E2E Gate Class

**Core gate only** for all three items. Per `research-findings.md` §Cross-Partition Synthesis — E2E Gate Applicability: INV-9 = Core (test-only addition, observes `job_processor.py` recovery contract without changing it); INV-10 = Core (source pack, `rate_limiter.py` only); INV-11 = Core (`test_scheduler_adapter.py` plus the bounded `registry.py` telemetry). None of the three touch `claim_pending_task` / `turn_transitions` / `reconcile_turn_mirror` / `job_locks` (the Release-gate surface). **Release gate does NOT apply to Phase 3.**

### Phase 3 Exit Criteria

1. All three test workers' PRs merged to `feature/schedule-review-improve`.
2. `bash test/packs/sources_unit_test.sh` PASS (INV-10).
3. `bash test/packs/p3_job_orphan_recovery_unit_test.sh` PASS (INV-9).
4. `bash test/packs/p3_scheduler_adapter_error_paths_unit_test.sh` PASS (INV-11).
5. `git diff --name-only -- daemon/` contains **only** `daemon/sources/registry.py`, and the diff is bounded to the Task 3.5 warning + counter; no other production file is touched.
6. Task 3.5's routed `decisions.md §D7-swallow-design-deferral` note preserves the retry/alert deferral and the future-design risk class.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Phase 1 INV-2 changes the recovery-path surface enough that INV-9 test bodies need rework | Medium | Medium | Scaffold-mode fallback per D5; late-wave start guarantees the contract is stable when tests finalize |
| 2 | INV-11 sub-test 3 (sustained-outage simulation) previously required wall-clock time that exceeded the 180s pack budget | Medium | Low | **Re-spec:** the 13-minute outage must be a clock-virtualized unit test (monkeypatch `time.monotonic` or use the `_clock` seam). Assert state-machine decisions (`backoff >= initial × multiplier^k` after k fast-failing starts, plus the 59.9/60.0/0.0 boundaries), not elapsed seconds. B7-style threshold-lowering to 2s is not acceptable; cross-reference Phase 1 B7. |
| 3 | Rate-limiter 100-concurrent stress surfaces a deadlock or GIL contention not in the existing 13 tests | Low | Low | If observed, downgrade the concurrency to a level that exercises the race without exceeding the 120s pack timeout; document the limit |
| 4 | INV-9's new pack file naming conflicts with an existing pack | Low | Low | Use the explicit `p3_*` prefix to avoid collision with existing `*_unit_test.sh` names |
| 5 | Task 3.5's bounded telemetry widens the production diff and could regress if other retry/alert logic creeps in | Medium | Low | Static check pins the diff to exactly `daemon/sources/registry.py`; the 4–6-LOC edit must contain only the warning + counter, and the routed `decisions.md` note keeps retry/alert explicitly deferred |

---

## Open Questions

None. All questions are settled by `decisions.md` §D5 and the pinned §D7.1–D7.7 amendments. The Task 3.5 production change is a bounded 4–6-LOC warning + counter and remains within the amendment's authority; retry/alert design is intentionally deferred.

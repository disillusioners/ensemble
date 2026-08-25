# E2E Merge Gate — fix/reconciler-paused-race-job-cancel

**Date:** 2026-08-25
**Branch:** `fix/reconciler-paused-race-job-cancel` @ `5afe43cf` (8c388e25 guard fix + 259ea1aa failed_at amendment + 5afe43cf Site-3 test)
**Base:** `latest` @ `afd76b12` (clean fork, merge-base = latest tip)
**VERDICT: ✅ GATE PASS — MERGE-READY** (1 uncommitted test-code fix must ride the branch — see §5)

## Scope Decision

> Mandatory e2e gate per ensure.md critical note (change touches `reconcile_turn_mirror` + `job_locks`). Full 282-pack suite NOT run — scope = the five mandated systems (claim_pending_task, turn_transitions, reconcile_turn_mirror, job_processor, job_locks) via the established 5-pack mandatory gate + concurrency (Core R2/R3) + API seam + 1 NEW ad-hoc pack for the branch's 2 uncovered acceptance files. This matches the 2026-08-25 integrated-gate precedent. Release-Gate E2E (live daemon :8079 + real LLM) NOT triggered: repository/service-layer change, no architecture change; ensure.md reserves that tier for big/critical/release changes. 8 dispatches total (1 recon + 6 packs + 1 re-run inside pack worker), 0 direct executions.

## 1. Suite inventory (ensure.md mandatory systems → packs)

| System (ensure.md) | Pack | Result | Counts vs baseline | Runtime |
|---|---|---|---|---|
| claim_pending_task + job_locks | `claim_guard_locks_unit_test` | ✅ PASS | 168P/0F/0S — **exact** | 1.95s |
| turn_transitions + reconcile_turn_mirror (+ full-chain e2e) | `turn_transitions_reconciler_unit_test` | ✅ PASS (post-fix) | 48P/0F/1deselect — **exact** | 1.70s |
| job_processor (+ full job-queue suite) | `job_queue_unit_test` | ✅ PASS | 1530P/0F/38S — +1P/−1S environmental (see §4) | 38.3s |
| ensure.md Core R2/R3 (concurrency/deadlock/observer races) | `concurrency_atomic_unit_test` | ✅ PASS | 91P/0F/74S — **exact** | 10s |
| API seam (consumer control) | `api_unit_test` | ✅ PASS | 213P/0F/8S — **exact** | 12.6s |
| **NEW** acceptance suite | `reconciler_paused_race_unit_test` (ad-hoc, first run) | ✅ PASS | 8P/0F (5 paused-race + 3 observer-stamp) | 0.82s |

**Total: 2,058 passed / 0 failed / 121 skipped+1 deselected across 6 packs.** Skips are by-design conditional skips (concurrency pack 74S baseline-exact); the 1 deselect = QUARANTINE.md `test_state_machine` (2026-08-20), honored by pack script.

## 2. Original-symptom scenario (gate req #2) — VERDICT: INCIDENT SHAPE FIXED

**Incident:** job `f1bf796d` stamped `done`/`cancelled` + `job_locks` deleted during `resume_processing_job` race while instance stayed alive.

**What was driven** (recon-verified + pack-executed, real repository code on in-memory engines):
- `_seed_incident_shape`: Instance + Task(RUNNING) + JobItem(active) + JobLock + Message seeded; Task flipped to CANCELLED — the exact artifact `cancel_task` on the superseded task produces inside `resume_processing_job`; then `reconcile_turn_mirror(work_id)` post-commit fired with instance still live.
- Matrix at every live status — `paused`, `running`, `waiting_children` → asserts `admission_state='active'`, `terminal_reason IS NULL`, `failed_at IS NULL`, `job_locks` row survives (count=1, `updated_counts.job_locks==0`).
- Write-through preserved: instance `completed` → `done`/`cancelled`/`failed_at` stamped, locks released (natural-completion terminal write, in finalize-call shape).
- **Retryability gate proven, not assumed:** incident-shaped row → `atomic_retry` returns None; even forced `done`+`failed` with `failed_at=NULL` → still None (proves `failed_at IS NOT NULL` is THE retry gate — the amendment's core claim).
- Continuity coverage elsewhere: full-chain e2e (claim→process→pause[answer]→answer→complete, 8-mirror reconciliation) green inside turn_transitions pack; modified directed property scenario (pause-during-report → complete → DONE) green post-fix.

**Precisely what was NOT covered:**
- The literal manager-level `resume_processing_job` coroutine was not executed live — its race artifact (superseded-task CANCELLED + live instance) is seeded directly at the seam instead. No live-daemon/LLM run (Release-Gate tier, out of scope this gate).
- The paused→resume transition is not one continuous flow inside the acceptance file (static per-status matrix); continuity comes from the full-chain e2e + property scenario above.

**Conclusion:** the exact mechanism that produced the incident (terminal write + lock delete against a live instance's job in the race window) is guarded for all three live statuses and pinned by deterministic tests; the failure mode cannot recur through `reconcile_turn_mirror`.

## 3. Retryability regression (gate req #3) — PASS

Real-code chain: `test_failed_path_stamps_failed_at_and_row_is_retryable` drives the **real** `_finalize_job_db_sync` (failed branch) → `done`/`failed`/`failed_at NOT NULL` → **real** `JobRepository.atomic_retry` accepts (queued, retry_count=1, failed_at cleared). Completed-path negative control keeps NULL. Site-3 `finalize_active_to_done` failed branch stamps + retry-accepts independently. Full retry family green in job_queue pack: `test_job_retry_engine` (28, incl. atomic concurrency/max/status gates), `test_retry_engine` (30), DLQ family (`test_dead_letter_service` 45, `test_dlq_api` 21 incl. replay, replay_all, retry_orphan_normalization, retry_scheduler, retry_versioned_agent, deferred_finalize_check). Cancelled-keeps-NULL has no dedicated cancelled-path test (completed is the negative control) — minor, code-enforced, noted for author.

## 4. Non-pre-existing failures / triage

**Zero failures remain. Zero failures were pre-existing-or-unexplained.** No base attribution runs needed — every delta is reconciled:
1. `test_turn_state_machine.py::test_directed_pause_during_report_turn` — branch-caused **stale test** (see §5 + LESSONS). Deterministic (2 identical FAILs). The branch updated 4 sibling unit tests in `test_turn_reconciler.py` for the D13 guard widening but missed this property-file scenario. Not flaky → no quarantine; fixed in test code.
2. job_queue +1P/−1S: `test_ensure_dev_sh_still_works` runtime-skips when port 8079 is occupied; port was free this run so it executed and passed. Identical collected total (1568). Environmental, not code.
- Quarantined tests: only the 1 pack-script deselect (turn_state_machine, pre-existing QUARANTINE entry). The 5 TestAccessMemoryArchive + others in QUARANTINE.md live in packs outside this gate's scope and did not surface.
- Foreign uncommitted `daemon/graph.py` (+174): untouched by all workers; zero failures traced into it.

## 5. Quick fix applied (TEST CODE ONLY — **UNCOMMITTED, must ride the branch**)

`tests/property/test_turn_state_machine.py` (+23 lines: `_force_instance_status` helper mirroring existing `_force_task_status` raw-SQL pattern + step-5 drive instance→COMPLETED→re-reconcile→assert DONE). Slightly over the <20-line quick-fix guideline; accepted as a mirror-pattern extension in a single file, deterministically verified (48/0/1 exact baseline after). **Action for branch author: commit this file with the branch** (no-commit constraint prevented gate-side commit). Production code untouched.

## 6. Notes for branch author

- **D13 vs §7 directed-scenario spec tension** (report-only): the reconciler now holds JobItem `active` while instance is `running`/`paused` — intended per D13; the §7 spec text and the property scenario expectation (`done` while instance still RUNNING) were the stale side. Fix went to the test; if the spec doc codifies the old expectation, update the spec.
- Cancelled-path `failed_at` null-stamp lacks a dedicated test (completed negative control covers the branch condition only by code symmetry).
- `reconciler_paused_race_unit_test.sh` is untracked (created by gate recon, registered in PACKS.md) — commit it as gate test infra with the branch.

## 7. Constraint compliance

No git branch ops / commits / pushes; PROD untouched; port 8088 untouched; foreign `daemon/graph.py` preserved as-is all session.

## Documentation

- PACKS.md: campaign banner + ad-hoc pack registered (PENDING → PASS)
- LESSONS/2026-08-25-paused-race-gate-stale-property-test.md
- RESULTS/2026-08-25-reconciler-paused-race-job-cancel-e2e-gate.md (this file)

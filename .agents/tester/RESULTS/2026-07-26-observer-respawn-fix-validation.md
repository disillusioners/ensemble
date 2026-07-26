# Observer Re-Spawn Fix Validation (commit b6d4953f)
**Date:** 2026-07-26
**Worker Instance:** c5201e8a-4a30-4d0e-88c6-aceb7945e02e (observer-respawn-fix-validation)
**Skill:** `test-pack-execution`
**Branch:** `feature/queue-dispatch-option-b` @ `b6d4953f` — `fix: prevent observer re-spawn UniqueViolation for message jobs`
**Trigger:** User request — validate the observer re-spawn fix + re-run Release Gate e2e tests
**Environment:** DEV only (`./dev.sh` on port 8079)

---

## Summary

| Metric | Value |
|--------|-------|
| Part A — E2E Release Gate (regression) | ✅ **PASS (4/4)** |
| Part B — FIFO + Observer scenario (targeted fix verification) | ✅ **PASS** |
| Overall status | ✅ **READY** |
| Quick fixes | none (fix worked first try) |
| Files modified | none |

---

## Scope Decision

> Commit `b6d4953f` fixes the observer re-spawn UniqueViolation bug (surfaced in the 2026-07-26 FIFO validation run, documented in `LESSONS/2026-07-26-observer-respawn-existing-instance-dlq.md`). The fix changes the observer dispatch path for message jobs — a critical, cross-layer behavioral change. Full Release Gate e2e regression + the targeted FIFO+observer reproduction scenario are both warranted. No scope reduction.

---

# Part A: E2E Release Gate Regression (commit b6d4953f)

## Prerequisites Verified
- ✅ Daemon running on `localhost:8079` (worker started it via `./dev.sh`, HTTP 200 at `/docs`)
- ✅ SSL certs cleaned (`unset SSL_CERT_FILE SSL_CERT_DIR` before every test)
- ✅ Queue cleanup before each test — clean (0 pending jobs) every time
- ✅ Tests run **one by one** via `-k` filter (per ensure.md mandate)

## Per-Test Results

| Test | Result | Runtime | Exit Code | Notes |
|------|--------|---------|-----------|-------|
| `test_parent_child_workflow_happy_path` | ✅ PASS | 41s | 0 | 1 passed, 10 deselected |
| `test_pause_after_spawn_then_resume` | ✅ PASS | 38s | 0 | 1 passed, 10 deselected |
| `test_terminate_after_spawn_then_revive` | ✅ PASS | 41s | 0 | 1 passed, 10 deselected |
| `test_three_level_cascade_reports` | ✅ PASS | 97s | 0 | 1 passed, 10 deselected |

**Part A Overall: 4/4 PASS — no regressions from the observer fix.** Total ~218s sequential.

### Runtime trend across Option B runs

| Test | msg_id fix | FIFO fix (67eb16b1) | This run (b6d4953f) | Trend |
|------|-----------|---------------------|---------------------|-------|
| happy_path | 61s | 49s | 41s | ✅ stable/improving |
| pause+resume | 38s | 38s | 38s | ✅ stable |
| terminate+revive | 45s | 41s | 41s | ✅ stable |
| 3-level cascade | 121s | 118s | 97s | ✅ improving |

---

# Part B: FIFO Concurrency + Observer Scenario (the actual fix)

## Setup
- **FIFO queue:** `b2daeab1-...` ("fifo-serial-test"), `concurrency_limit=1`, `queue_type=fifo` (reused from previous run)
- **Instance A:** `7214bdb8-...` (developer) — Job A `b4fd9f60-...`, msg "Respond READY"
- **Instance B:** `6a37a176-...` (developer) — Job B `31ad7fc3-...`, msg "Respond DONE"
- Both freshly spawned. Neither is the tester/leader. Messages sent back-to-back at 17:42:34 UTC.

## Timeline (daemon-log evidence — definitive)

```
17:42:34  Job A b4fd9f60 started, acquiring queue lock ... concurrency_limit=1 → SUCCESS, admission_state=active
17:42:36  Job B 31ad7fc3 attempted → SKIP — lock NOT acquired (concurrency limit)   ← FIFO enforcement ✅
17:42:42  Job A's instance completed. Observer finalized b4fd9f60 (released 1 lock)
17:42:42  Observer re-claimed Job B: start_job ... SUCCESS job 31ad7fc3 started
17:42:42  Observer (message branch): woke worker pool for pre-existing Task on job 31ad7fc3...    ← B11 fix fired ✅
          / instance 6a37a176... (no spawn; instance already exists for this message job)
17:42:42  Worker claimed task 2777 (Job B's pre-existing Task). No spawn_instance_with_mcp.
17:42:47  Job B completed. Observer finalized 31ad7fc3 (released 1 lock) → admission_state=done
```

**The B11 fix fired correctly:** the observer detected the message job, skipped the spawn (instance already exists), and woke the worker pool for the pre-existing Task instead. No `spawn_instance_with_mcp`, no `enqueue_message`, no `complete_job(FAILED)`.

## Observer Error Grep Results (CRITICAL — the 5 patterns)

| Pattern | Expected | Found? | Count | Notes |
|---------|----------|--------|-------|-------|
| `failed to spawn instance` | NOT found | **no** | 0 | ✅ No spawn attempted |
| `duplicate key value violates unique constraint` | NOT found | **no** | 0 | ✅ |
| `UniqueViolation` | NOT found | **no** | 0 | ✅ |
| `released 0 lock` (our scenario jobs) | NOT found | **no** | 0 | ✅ Both jobs logged `released 1 lock(s)` |
| `SKIP — lock NOT acquired` | FOUND | **yes** | 1 | ✅ FIFO enforcement still works |

**All 4 "must NOT appear" patterns: zero matches.** The "SHOULD appear" FIFO pattern: present (1 match). The previous run's symptoms are **all eliminated**.

*Note:* `released 0 lock(s)` did appear in the buffer but only for unrelated e2e-test instances (title-generation `no_job` fallback path) — not for our scenario jobs.

## JobItem State Consistency (the new fix's key claim)
- **B's `admission_state` at start: `active`** ✅ (confirmed by log `17:42:42 started_job 31ad7fc3... admission_state=active`)
- ✅ **NOT stuck at `queued`** — the exact symptom from the previous run is gone
- The B12 defensive WARNING ("left start_job with admission_state != 'active'") did **NOT** fire — the atomic activation path worked
- Final state: `done`

## DLQ Check
- **Job B in DLQ? NO.** ✅ Job B (`31ad7fc3`) final state: `status=completed`, `admission_state=done`.
- The 1 pre-existing dead job in DLQ (`86d51f70`, from the earlier 09:31 UTC run — over 8 hours before our scenario) is unrelated.

## Message Delivery
- **Instance A response:** `SẴN SÀNG` (Vietnamese for "READY" — the developer agent's prompt is Vietnamese; semantically correct) ✅
- **Instance B response:** `DONE` ✅

## Part B Result: ✅ PASS

All 6 PASS criteria met:
1. ✅ While A held the slot, B was blocked (log: `SKIP — lock NOT acquired (concurrency limit)`)
2. ✅ "SKIP — lock NOT acquired" present for B
3. ✅ NONE of the 4 error patterns appeared for our scenario jobs
4. ✅ Job B NOT in DLQ (no spurious dead-letter)
5. ✅ Job B `admission_state=active` when it started (consistent; not stuck at `queued`)
6. ✅ Both messages delivered correctly

---

## ensure.md Validation Status

### Release Gate (Critical — release-gate)
- [x] **E2E: Normal parent→child workflow completes (happy path)** — ✅ PASS
- [x] **E2E: Pause after spawn, then resume works correctly** — ✅ PASS
- [x] **E2E: Terminate after spawn, then revive documented** — ✅ PASS
- [x] **E2E: 3-level cascade (leader→tester→staggered workers)** — ✅ PASS

**Release Gate E2E: 4/4 Critical requirements PASS ✅**

### Additional: Observer Re-Spawn Fix Verification
- [x] **NO UniqueViolation / NO spurious DLQ / NO stuck-`queued` when a queued message job eventually starts** — ✅ PASS (5-pattern log grep + DLQ check + JobItem state all confirm)

---

## Notes
- **Dev daemon log capture:** dev mode (`./dev.sh`) runs uvicorn with `--reload` and logs to **stdout** (no `.log` file). The worker captured daemon stdout via the background process buffer and grepped that (all 5 patterns verified). For future runs: use the process buffer, not `tail -f daemon.log`.
- **Trivially-fast prompts:** because "Respond READY/DONE" finish in ~6-8s, the two jobs didn't overlap long enough for the poll loop to catch a visible running/queued window. FIFO enforcement is nonetheless **proven by the daemon log** (`SKIP — lock NOT acquired`). For a future run wanting a visible overlap window, use a prompt with `bash sleep 15` so A holds the slot longer.
- **No production code modified.** The fix worked first try. Dev daemon left running on :8079 for any follow-up.

---

## Overall Status

- **E2E Release Gate:** ✅ **PASS (4/4)** — no regressions
- **Observer Re-Spawn Fix:** ✅ **PASS** — the UniqueViolation / spurious DLQ / stuck-`queued` symptoms are all eliminated
- **Quick Fixes Applied:** none required
- **Production code modified:** none
- **Testing Complete:** ✅ **READY** — commit `b6d4953f` validated and correct; observer re-spawn fix eliminates the previous run's anomaly with no e2e regressions
- **LESSONS update:** the observer bug entry (`LESSONS/2026-07-26-observer-respawn-existing-instance-dlq.md`) is now **RESOLVED** by this commit

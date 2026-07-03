# Plan Tracking: Job-as-the-Front-Primitive

## Iteration 001 (2026-07-03 04:33)

**Verdict: REJECTED**

### Blocking Issues

#### 1. BLOCKING ISSUE 2 Fix Is Incomplete — `_finalize_job_db_sync` Step 1 Not Modified

**Severity: BLOCKING**

The plan (Phase 1 Task 6b) claims that modifying ONLY `_get_processing_job_for_instance()` to match `queued` AND `active` JobItems is sufficient to fix the stuck `queued` JobItem leak. The plan explicitly states (Phase 1 line 50):

> "The finalize path (`_finalize_job_db_sync`) already transitions to `done`/`dead` via `admission_state`. This works regardless of the starting state."

**This is factually incorrect.** Verified against code:

- `_finalize_job_db_sync()` Step 1 UPDATE (`job_feedback_observer.py:2941`) has:
  `.where(JobItem.admission_state == AdmissionState.ACTIVE.value)`
- A `queued` JobItem hits `rowcount == 0` (line 2946)
- SELECT at line 2948 finds the row (it exists, state=queued, not gone)
- Raises `InvalidTransitionError` at line 2970
- Caught at `_finalize_job` line 1633 → logged at DEBUG → **silent return**
- **Steps 2+3 (instance status + lock release) NEVER execute for this path**
- The JobItem stays `queued` forever — the exact leak the plan claims to fix

**Required fix**: `_finalize_job_db_sync()` Step 1 WHERE clause (line 2941) MUST also be modified to match `queued` AND `active`:
`.where(JobItem.admission_state.in_([AdmissionState.ACTIVE.value, AdmissionState.QUEUED.value]))`

This is independently confirmed by a council evaluation.

**Expected**: Both `_get_processing_job_for_instance()` AND `_finalize_job_db_sync()` Step 1 modified.
**Found**: Only `_get_processing_job_for_instance()` modification planned; `_finalize_job_db_sync()` Step 1 NOT mentioned anywhere in the plan.

---

### Previously-Found Issues (Verification)

1. **✅ FIXED** — Missing 6th entry point (PAUSED cascade-resume at `manager.py:3356`): Confirmed present in Phase 3 Task 7, with detailed context. The entry point at `manager.py:3356` was verified to exist in code (`source="cascade_resume"`).

2. **⚠️ NOT FULLY FIXED** — Stuck `queued` JobItem leak: The fix direction is correct (finalize-on-completion fallback) but incomplete (see Blocking Issue 1 above). Only half the chain is addressed.

3. **✅ FIXED** — `list_pending_by_queue` missing `job_type` filter: Confirmed in Phase 1 Task 0 as hard prerequisite. Verified against code: `list_pending_by_queue()` (`repository.py:703-723`) currently has NO `job_type` filter — the plan's fix (add `.where(JobItem.job_type != "message")`) is correct and necessary.

---

### Notes (Non-Blocking)

- Phase sequence is sound: Phase 0 (prototype) → Phase 1 (bridge) → Phase 2 (serialization) → Phase 3 (entry points) → Phase 4 (facade collapse) → Phase 5 (cutover). Dependencies are correctly ordered.
- AD-6 (partial facade collapse — retain report Tasks) is well-reasoned. The 6 backend code paths were verified to exist and branch on `kind != "job"`.
- RF1 (cross-system guard load-bearing concern) is well-addressed: Phase 0 Gate 2 load-tests it, Phase 2 Task 6 is conditional optimization. Scope exception (not frozen backend) is explicit.
- Feature flag strategy is sound: default OFF, flag-checked at entry points, removed in Phase 5.
- Entry point enumeration is thorough. Verified all 6 entry points exist at the cited file:line references.
- The `_admitted_task_carve_out_sql` correctness analysis is sound — verified the NULL-safe carve-out handles message-Jobs correctly.

---

## Iteration 002 (2026-07-03 04:58)

**Verdict: APPROVED**

### Verification of Previously-Blocking Issue

**Issue #1 (BLOCKING — `_finalize_job_db_sync` Step 1 not modified): ✅ FULLY FIXED**

The plan now includes BOTH parts in Phase 1 Task 6b:
- **Part A**: `_get_processing_job_for_instance()` (line 615) — change `admission_state` filter from `active` only to `IN ('queued', 'active')`
- **Part B**: `_finalize_job_db_sync()` Step 1 UPDATE (line 2941) — change WHERE clause from `.where(JobItem.admission_state == AdmissionState.ACTIVE.value)` to `.where(JobItem.admission_state.in_([AdmissionState.ACTIVE.value, AdmissionState.QUEUED.value]))`

Council verification confirmed the failure trace is technically exact:
1. Without Part B: lookup finds `queued` JobItem → UPDATE matches 0 rows (`== ACTIVE` filter) → disambiguation SELECT finds row → `InvalidTransitionError` → caught at line 1633 → silent return → Steps 2+3 skipped
2. With both parts: lookup finds `queued` JobItem → UPDATE matches (`in_([ACTIVE, QUEUED])`) → flips to `done` → Steps 2+3 run → correct finalization

### Independent Verification (Council — 4 Claims)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | Two-part fix: both parts required | **CONFIRMED** | Verified failure trace against actual code (lines 2941, 2946-2974, 1633). Plan includes both Part A and Part B. |
| 2 | `list_pending_by_queue` missing `job_type` filter | **CONFIRMED** | `repository.py:715-721` — no `job_type` filter. Without fix, poll loop double-dispatches message-Jobs. |
| 3 | Cross-system guard carve-out correctness | **CONFIRMED** | `_admitted_task_carve_out_sql` NULL-safe JSON extract + `NOT EXISTS` correctly handles same-TX JobItem+Task. |
| 4 | AD-6 partial facade collapse retains reports | **CONFIRMED** | All 7 backend paths group `kind` into `("turn"\|"report")` vs `{"job"}` — none distinguishes turn from report. |

### Notes (Non-Blocking)

1. **Bug description wording (Claim 1)**: The plan states "permanent leak with no recovery" for the stuck `queued` JobItem. The council found that without ANY fix, the instance actually finalizes correctly via the `job_id=None` path (`_finalize_job_db_sync` skips Step 1 and runs Steps 2+3 unconditionally). The actual bug is a JobItem leak, not an instance leak. However, implementing ONLY Part A (without Part B) WOULD cause an instance regression — routing a `queued` JobItem into Step 1, failing the UPDATE, and preventing Steps 2+3. The plan correctly includes both parts. This is a wording imprecision, not a blocking issue.

2. **Report-safe behavior verification (Claim 4)**: Recommend Phase 4 add an explicit test invoking cancel/retry/delete/restore MCP tools on a `kind="report"` work_id to confirm `request_cancel` semantics don't cause a hard error on `process_report`/`send_report` Task rows.

3. **RF1 load test**: Phase 0 Gate 2 must prove `claim_pending_task` p99 latency ≤ current baseline + 5ms before Phase 3. If not, Phase 2 Task 6 (guard optimization) becomes mandatory. The plan correctly flags this.

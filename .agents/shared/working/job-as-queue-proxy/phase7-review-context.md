# Phase 7a + 7b Review — Pre-Council Context

## Commits
- `e067ca90` — Phase 7a: remove kill-switch, legacy paths, dead code
- `dfab3e6d` — Phase 7b: remove JobStatus enum from production code

## Verified Facts

### Checkpoint 1: Zero JobStatus.X.value in production ✅ CONFIRMED
`grep -rn "JobStatus\.\(PENDING\|PROCESSING\|...\)\.value" daemon/` → 0 matches.
Only 2 comment/docstring references remain (work_status.py, job_state_machine.py).

### Checkpoint 2: Kill-switch removal ✅ CONFIRMED
`grep "use_virtual_job_resolver" daemon/` → 0 production matches.
`_use_resolver()` in jobs_streaming.py is a stub returning True.
`_resolve()` ignores the parameter.

### Checkpoint 3: API backward compat ✅ MOSTLY OK
- `_ADMISSION_TO_LEGACY_STATUS` map: {queued→pending, active→processing, done→completed, dead→dead_letter}
- `_VALID_LEGACY_STATUSES`: all 7 legacy values including "paused"
- JobStatus shim: `enum.Enum("JobStatus", {...}, type=str)` + `is_valid` classmethod → WORKS
- Import test confirms: JobStatus.PENDING.value == "pending", is_valid("bogus") == False

### Checkpoint 5: Dead code claim ⚠️ INACCURATE
The review request says "removed methods (start_job, start_job_atomic) had no callers."
But `start_job` and `start_job_atomic` STILL EXIST in repository.py (lines 1196, 1287, 1306)
and have ACTIVE callers in job_queue_service.py and job_processor.py.
Only `_LegacyJobItemRecord` class and the migration script were actually removed.

### Checkpoint 6: Test compat ⚠️ CONCERN
214 JobStatus references in tests/ — all go through the shim. Shim works.
BUT: test_resume_flow_redesign.py:72 has a DUPLICATE import: `JobStatus, JobStatus`
AND: test_job_continue_concurrency_gate.py:56 sets `svc.use_virtual_job_resolver = False`
expecting the legacy path — but the property was REMOVED. This test's semantics are stale.

## Pre-Council Findings

### W1. Stale alias in work_resolver.py
`work_resolver.py:90` imports `_ADMISSION_TO_LEGACY_STATUS as _ADMISSION_STATE_TO_STATUS` (old name).
Cosmetic but confusing — the old name is retained purely as an alias at the import site.

### W2. Stale test: test_job_continue_concurrency_gate.py:56
Sets `svc.use_virtual_job_resolver = False` on a mock, expecting the legacy `get_job` path.
The property was removed in Phase 7a; tools now always call `get_work`. The mock likely
returns a truthy falsy value for `get_work` (AsyncMock default), so the test may pass by
accident. The test's documented intent is wrong.

### W3. Duplicate import in test_resume_flow_redesign.py:72
`from ... import AdmissionState, JobItem, JobRepository, JobStatus, JobStatus`
Duplicate `JobStatus` — harmless (Python deduplicates) but sloppy.

### S1. Bug FIX (not regression): queue counts
job_queue_mgmt_service.py now reads `counts.get(AdmissionState.ACTIVE.value, 0)`.
Old code read `counts.get(JobStatus.PROCESSING.value, 0)`.
count_jobs_by_admission returns {"queued", "active", "done", "dead"} keys.
Old code was BUGGY (always returned 0 for active_jobs/pending_jobs).
New code is CORRECT. This is a latent bug fix.

### S2. Stale docstring in tools/job_queue.py:86
References `USE_VIRTUAL_JOB_RESOLVER=ON` which no longer exists in config.

## Questions for Council
1. Are there any OTHER stale references to removed config/flags/enums that could cause runtime errors?
2. Is the JobStatus enum shim (dynamic enum.Enum creation) fully compatible with all test patterns (iteration, isinstance, comparison)?
3. Are there callers of `start_job` / `start_job_atomic` that break due to the removal of the JobStatus parameter?
4. Does the API backward compat hold for ALL response paths (job_get, job_list, retry, cancel, DLQ replay)?
5. Any edge cases in the `_ADMISSION_TO_LEGACY_STATUS` lossy mapping (done→completed hides failed/cancelled)?

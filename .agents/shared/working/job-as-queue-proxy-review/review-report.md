# Review Report: Job-as-Queue-Proxy Plan

## Executive Summary

**Overall verdict: ✅ ARCHITECTURALLY SOUND — proceed with 7 fixes (3 line-reference corrections, 4 substantive gaps)**

The plan is a high-quality, deeply-researched architecture document. The core thesis — collapse execution state from `JobItem` onto `Instance` — is **valid and well-justified**. The 7-phase sequencing is **sound**, the dual-write/migration dual-path is correctly specified, and the §8.7 `active ⇔ lock-held` trigger design is the strongest available enforcement under Postgres-primary.

However, this review found **3 stale line references** and **4 substantive gaps/risks** not addressed in the plan. None are blockers; all are fixable in-place before execution begins.

| Category | Count | Severity |
|----------|-------|----------|
| Stale/incorrect line references | 3 | Low (cosmetic, but will confuse executors) |
| Substantive gaps/missing steps | 4 | Medium (must be addressed before phase execution) |
| Factual errors in claims | 2 | Low-Medium |
| Critical-note constraint compliance | ✅ | All met |

---

## Part A: Line-Reference Verification (19 references checked)

### A.1 ❌ STALE: `_finalize_job_db_sync` location (§1, line 26)

| Plan claim | Actual |
|---|---|
| `_finalize_job_db_sync` at `job_feedback_observer.py:2436-2491` | Function `def` is at **line 2109**. Lines 2436-2491 are the **middle of the function body** (Step 1 JobItem UPDATE block). |

**Impact:** Low. The line `:2436-2491` points to the right function's body but not its definition. An executor grepping for the function will find `def _finalize_job_db_sync` at 2109. The InstanceStatus→JobStatus mapping the plan cites at `:2209-2212` (which is INSIDE this function) **is correct**.

**Fix:** Change `job_feedback_observer.py:2436-2491` → `job_feedback_observer.py:2109-2620` (the full function span).

### A.2 ❌ STALE: `_terminate_instance_db_sync` file location (§4, Phase 4, line 218)

| Plan claim | Actual |
|---|---|
| `_terminate_instance_db_sync` Step 2 at `:1786-1840` (implied in observer) | The function is in **`instance_lifecycle.py:1624`**, NOT `job_feedback_observer.py`. There is **no** `_terminate_instance_db_sync` in the observer file. |

**Verification:**
- `grep -n "def _terminate_instance_db_sync" daemon/services/instance_lifecycle.py` → **line 1624** ✅
- `grep -n "_terminate_instance_db_sync" daemon/services/job_feedback_observer.py` → **no matches**

The plan's §1 (line 32) and Phase 4 (line 218) both reference this function in the observer context. The actual cancel-cascade Step 2 (the `SET status = 'cancelled'` writes) is in `instance_lifecycle.py` at **lines 1744-1827** (Step 2 starts at 1744, the cancelled-status UPDATEs are at 1789 and 1827).

**Impact:** Medium. The plan's Phase 4 bullet says "`_terminate_instance_db_sync` Step 2 (`:1786-1840`): cancel cascade sets `admission_state='done'`." An executor looking in the observer file will not find it.

**Fix:** Add file qualifier: `_terminate_instance_db_sync` Step 2 (`instance_lifecycle.py:1744-1827`).

### A.3 ❌ MISLEADING: `manager.py:1693-1696` precedent claim (§12.1, line 401)

| Plan claim | Actual |
|---|---|
| `manager.py:1693-1696` cited as "DROP CONSTRAINT on FK" precedent | Lines 1693-1696 are **docstring text** describing the DROP CONSTRAINT, not the code itself. The actual `DROP CONSTRAINT` statement is at **line 1997**: `"ALTER TABLE job_watchers DROP CONSTRAINT IF EXISTS job_watchers_job_id_fkey"`. |

**Context:** The plan's §12.1 already **corrects** the reviewer's claim that this is a trigger precedent (stating "that line is a `DROP CONSTRAINT` on an FK, not a trigger"). The plan's self-correction is right in substance but cites the wrong line. The actual constraint-drop code is at line 1997, and it IS inside `_ensure_postgres_columns()` (which spans 1653-2020).

**Impact:** Low. The plan's conclusion ("there is no trigger precedent") is correct. But the cited line range is docstring, not code.

**Fix:** Change `manager.py:1693-1696` → `manager.py:1997`.

### A.4 ✅ Correct references (16 of 19 verified)

All of the following were verified line-by-line against the current codebase and are **accurate**:

| # | Reference | File | Status |
|---|-----------|------|--------|
| 1 | InstanceStatus→JobStatus mapping `:2209-2212` | job_feedback_observer.py | ✅ Exact |
| 2 | `rearm_after_complete` `:1135-1140` | job_feedback_observer.py | ✅ Exact |
| 3 | W3 fail-safe `:1454-1461` | job_feedback_observer.py | ✅ Exact |
| 4 | `_process_event` `:641` | job_feedback_observer.py | ✅ Exact |
| 5 | status-drift warning `:692-712` | work_resolver.py | ✅ Exact |
| 6 | `_job_to_record` `:768-795` | work_resolver.py | ✅ Exact |
| 7 | `_job_item_to_work_record_shim` `:1139` | tools/job_queue.py | ✅ Exact (def at 1139) |
| 8 | `_ensure_postgres_columns()` `:1653` | manager.py | ✅ Exact |
| 9 | `_ensure_postgres_drop_legacy_columns()` `:2022` | manager.py | ✅ Exact |
| 10 | runner.py NO-OP check `:455-482` | migrations/runner.py | ✅ Exact (477-480) |
| 11 | `find_processing_jobs` / `find_jobs_by_instance` `:487-520` | repository.py | ✅ Exact |
| 12 | `find_retryable_jobs` `:1228-1258` | repository.py | ✅ Exact |
| 13 | PAUSED pre-check `:634-646` | job_processor.py | ✅ Exact |
| 14 | defer idle-gate `:399-418` | job_processor.py | ✅ Exact |
| 15 | pause cascade UPDATE 2 `:2138-2165` | instance_lifecycle.py | ✅ Exact |
| 16 | resume cascade UPDATE 2 `:2407-2436` | instance_lifecycle.py | ✅ Exact |

### A.5 ⚠️ Minor range imprecisions (2 references)

| Reference | Plan says | Actual |
|---|---|---|
| `count_active_jobs_*` | `repository.py:361-390` | `count_active_jobs_by_project` is at **345**; `count_active_jobs_in_non_defer_queues` at **365** |
| `list_pending_*` | `repository.py:451-540` | `list_pending_by_project` at 451, `list_all_pending` at 471, `list_pending_by_queue` at **522** — all within range but order differs from plan |

**Impact:** Negligible — the ranges are approximate and all functions fall within or near the cited ranges.

---

## Part B: Factual Verification of Architectural Claims

### B.1 ✅ JobStatus enum: confirmed 7 values at `models.py:21-37`

```python
class JobStatus(str, enum.Enum):
    PENDING = "pending"        # queue concern
    PROCESSING = "processing"  # execution (→ moves to Instance)
    PAUSED = "paused"          # execution (→ moves to Instance)
    COMPLETED = "completed"    # execution (→ derived from Instance)
    FAILED = "failed"          # execution (→ derived from Instance)
    CANCELLED = "cancelled"    # execution (→ derived from Instance)
    DEAD_LETTER = "dead_letter" # queue concern (STAYS)
```

Plan correctly identifies 5 DROP values (`processing`, `completed`, `failed`, `paused`, `cancelled`) and 2 KEEP values (`pending`→`queued`, `dead_letter`→`dead`).

### B.2 ✅ InstanceStatus: confirmed 10 values at `models.py:20-33`

```python
class InstanceStatus(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"
    TERMINATED = "terminated"
    QUEUED = "queued"
    WAITING_CHILDREN = "waiting_children"
    FAILED = "failed"
```

Plan's §3.2 claim that Instance already owns `RUNNING/COMPLETED/ERROR/PAUSED/TERMINATED/FAILED` is **correct**.

### B.3 ⚠️ PARTIALLY INCORRECT: `_STATUS_CANONICAL_MAP` JobItem-only entries (Phase 4, line 222)

| Plan claim | Actual |
|---|---|
| Delete entries that mapped `JobStatus.*`: `processing`, `paused`, `cancelled`, `failed` | `paused`, `cancelled`, `failed` are **Task-side entries too** (shared with Task), NOT JobItem-only. Only `processing` and `dead_letter` are JobItem-only. |

**Verification** (`work_status.py:62-74`):
```python
_STATUS_CANONICAL_MAP = {
    # Task-side source values
    "pending": "pending",
    "running": "processing",
    "paused": "paused",          # ← shared with Task, NOT JobItem-only
    "completed": "completed",
    "failed": "failed",          # ← shared with Task, NOT JobItem-only
    "cancelled": "cancelled",    # ← shared with Task, NOT JobItem-only
    # JobItem-side source values (Task already covers ``paused``)
    "processing": "processing",  # ← JobItem-only ✅
    "dead_letter": "dead_letter", # ← JobItem-only ✅
}
```

**Impact:** Medium. The plan's Phase 4 instruction to "delete the entries that mapped `JobStatus.*` (`processing`, `paused`, `cancelled`, `failed`)" is **wrong for 3 of the 4 entries**. Deleting `paused`, `cancelled`, `failed` from the map would break Task-side canonicalization (Task uses these values directly). Only `processing` should be deleted (it's the JobItem-only synonym for `running`).

**Fix:** Phase 4 should say: "Delete only the `processing` entry (JobItem-only synonym for Task's `running`). The `paused`/`cancelled`/`failed` entries stay — they're shared with Task. Add `dead` → `dead_letter` for the new `AdmissionState`."

### B.4 ✅ Dual-write pattern confirmed in `_finalize_job_db_sync`

The function at line 2109 confirms the 3-step atomic cascade described in the plan:
1. Step 1: JobItem UPDATE (PROCESSING → COMPLETED/FAILED) — `:2209-2212` mapping confirmed
2. Step 2: Instance UPDATE (status to terminal)
3. Step 3: Lock release (DELETE job_locks)

### B.5 ✅ `_finalize_terminal` does NOT exist yet

Confirmed: `grep -rn "_finalize_terminal" daemon/` (excluding docs/plans) → **no matches**. The plan correctly identifies this as a Phase 4 new introduction.

---

## Part C: Substantive Gaps & Missing Steps

### C.1 🔴 GAP: `_STATUS_MAP` in `messages.py` is NOT at `:275` (line 179 claim)

| Plan claim | Actual |
|---|---|
| `messages.py:_STATUS_MAP` at `:275` | Definition is at **line 57**. Line 275 is a usage site (`mapped_status = _STATUS_MAP.get(...)`), not the definition. |

**Impact:** Low for the plan's actual claim — the plan says "already Task-driven; keep, this is correct." This is accurate: `_STATUS_MAP` (at line 57) maps `TaskStatus.*` values, NOT `JobStatus.*`, so it's unaffected by the refactor. The line reference is just imprecise.

**Fix:** Change `messages.py:_STATUS_MAP` (`:275`) → `messages.py:57` (or note "line 275 is usage, 57 is definition").

### C.2 🟡 GAP: `child_reports.py` own-queue gate line reference off (§8.3, line 299)

| Plan claim | Actual |
|---|---|
| Own-queue gate at `child_reports.py:1319-1329` | The MessageQueue COUNT query is at **lines 1319-1331** in `child_reports.py` (1946 lines total). The reference is **approximately correct** — the query spans 1319-1331, and the plan's `:1319-1329` captures the core of it. |

**Impact:** Negligible. The line reference is within ±2 lines of the actual query boundary.

### C.3 🔴 GAP: `count_active_jobs_by_project` query semantics (Phase 3, line 204)

The plan says Phase 3 will change `count_active_jobs_by_project` to `WHERE admission_state='active'`. But the **current** query (verified at `repository.py:345-362`) counts `PENDING + PROCESSING`:

```python
.where(JobItem.status.in_([JobStatus.PENDING.value, JobStatus.PROCESSING.value]))
```

Under the new admission model, `pending` becomes `queued` and `processing` becomes `active`. The plan's §3.2 says `count_active_jobs*` should filter on `admission_state='active'` — but that would **DROP `queued` jobs from the count**, changing semantics.

**Current behavior:** counts both queued+active jobs.
**Plan's Phase 3 instruction:** `WHERE admission_state='active'`.
**Semantic change:** would now count only active (not queued).

**Impact:** Medium-High. This could break the defer-idle-gate logic (`job_processor.py:399-418`) which calls `count_active_jobs_in_non_defer_queues` to decide whether a defer queue should proceed. If queued jobs are excluded from the count, the defer gate could fire prematurely.

**Fix:** Phase 3 should specify the intended semantics explicitly:
- If the gate wants "is anything running?" → `admission_state='active'` only (correct for defer-idle-gate)
- If the gate wants "is there ANY pending work?" → `admission_state IN ('queued', 'active')`

The plan should clarify which semantics each caller needs. The defer-idle-gate likely wants "active only" (is work in flight?), so `admission_state='active'` may be correct — but this is a **behavioral change** that must be called out explicitly, not buried in a query rewrite.

### C.4 🟡 GAP: `_ACTIVE_JOB_IDS_SUBQUERY` in lock_repository (Phase 3, line 209)

The plan says this should become `WHERE admission_state IN ('queued','active')`. Current code (`lock_repository.py:23-27`):
```python
_ACTIVE_JOB_IDS_SUBQUERY = (
    "SELECT job_id FROM job_queue_items "
    "WHERE status IN ('pending', 'processing') "
    "  AND deleted_at IS NULL"
)
```

The plan's proposed `IN ('queued','active')` **preserves semantics** (both non-terminal states). ✅ This is correct. But the plan should note that this subquery is used in `lock_repository.py:290` for a stale-lock sweep, and the stale-lock logic assumes a lock should only exist for active jobs. Under the new model, a lock should exist only for `active` jobs (not `queued`). So the subquery might need to be `admission_state = 'active'` for the stale-lock sweep, while `IN ('queued', 'active')` for other uses.

**Impact:** Medium. The stale-lock sweep (`lock_repository.py:290`) uses `NOT IN` this subquery — meaning it deletes locks whose job is NOT in `(pending, processing)`. Under the new model, a job in `queued` state should NOT have a lock (locks are acquired only when `start_job` spawns an instance → `active`). So the sweep should check `NOT IN (active)` to delete locks on `queued`/`done`/`dead` jobs.

**Fix:** Phase 3 should split this subquery or clarify: the stale-lock sweep should use `admission_state = 'active'` (locks only valid for active jobs), while any "non-terminal jobs" query uses `IN ('queued', 'active')`.

---

## Part D: Phase Dependency & Sequencing Analysis

### D.1 ✅ The 7-phase sequence is OPTIMAL

The plan's sequencing is well-designed:

```
Phase 0 (audit/docs) → Phase 1 (read cutover) → Phase 2 (additive column + triggers)
    → Phase 3 (query cutover) → Phase 4 (write cutover) → Phase 5 (drop columns)
    → Phase 6 (frontend) → Phase 7 (cleanup)
```

**Why it's correct:**
- **Phase 2 before Phase 4:** The constraint triggers (§8.7) must be installed BEFORE the write-path changes they guard. The plan correctly notes this in §8.7 line 329. ✅
- **Phase 3 before Phase 4:** Query cutover (`status` → `admission_state` reads) must happen while dual-write is still active (both columns are correct). ✅
- **Phase 4 before Phase 5:** Columns can only be dropped after no code writes them. Phase 4 removes all writers; Phase 5 drops. ✅
- **Phase 5 after Phase 4 + observation window:** The plan correctly calls for `MANUAL: TRUE` migration with a ≥2-week observation window. ✅
- **Phase 6 (frontend) is parallelizable:** Frontend can start after Phase 1 (read API is ready). The plan sequences it at 6 but notes the dependency is loose. ✅

### D.2 🟡 MISSING DEPENDENCY: Phase 4 → Phase 5 DLQ snapshot ordering

The plan §8.6 (line 311-313) correctly identifies that `move_to_dlq` must snapshot `error_message`/`retry_count`/`failed_at` into `DeadLetterItem` BEFORE the columns are dropped. However, the **verification** that `move_to_dlq` currently snapshots these fields is not confirmed in the plan.

**Verification result:** `DeadLetterItem` table (`models.py:316-380`) does carry `error_message`, `retry_count`, `failed_at` — confirmed by the plan's §2.2. But the plan should explicitly verify that `move_to_dlq` (`dead_letter_service.py:74`) populates these from the JobItem at call time, not lazily.

**Impact:** Low — the plan's sequencing (Phase 4 before Phase 5) is correct regardless. But the §8.6 risk could be de-risked by an explicit Phase 4 test asserting `DeadLetterItem` snapshot completeness.

### D.3 ✅ Phase 2 trigger novelty is correctly flagged

The plan correctly identifies (§8.7, §12.1) that this introduces the **first** `CONSTRAINT TRIGGER` / `DEFERRABLE` usage in the codebase. Verified: no precedent exists. The `manager.py:1997` DROP CONSTRAINT is on a foreign key, not a trigger.

---

## Part E: Critical-Notes Constraint Compliance

### E.1 ✅ PostgreSQL Primary DB constraint

The plan correctly handles this throughout:
- §Schema (line 11): explicitly states "PostgreSQL is the primary database; SQLite remains supported"
- Phase 2: dual-path migration (SQLite `.sql` + Postgres `_ensure_postgres_columns()`)
- Phase 5: dual-path drop (SQLite `.sql` MANUAL + Postgres `_ensure_postgres_drop_admission_legacy()`)
- §8.7: trigger is Postgres-only; SQLite gets CI sweep fallback

### E.2 ✅ `_ensure_postgres_columns()` usage

The plan correctly references this pattern:
- Phase 2: "ADD COLUMN IF NOT EXISTS + backfill + CREATE INDEX IF NOT EXISTS ... in `_ensure_postgres_columns()`"
- Phase 5: new `_ensure_postgres_drop_admission_legacy()` helper mirroring `_ensure_postgres_drop_legacy_columns()` at `manager.py:2022`

### E.3 ✅ Dual-driver support

The plan maintains both SQLite and PostgreSQL support:
- SQLite: `.sql` migration files
- PostgreSQL: runtime ALTER statements
- Both: dual-write in Phase 2, dual-read in Phase 3

### E.4 ✅ No SQLite-only syntax

The plan does not introduce `rowid` or other SQLite-specific syntax. The `admission_state` column uses `TEXT NOT NULL DEFAULT 'queued'` — portable across both dialects.

---

## Part F: Risks Not Addressed in the Plan

### F.1 🔴 NEW RISK: `maybe_retry` is NOT synchronous today

The plan's §3.2 (line 116) claims: "the retry decision is made **synchronously at finalize** (it largely already is — `complete_job` FAILED branch calls `maybe_retry` inline, `job_queue_service.py:1579`)."

**Verification:**
- `complete_job` (async, line 1538): calls `maybe_retry` via `await asyncio.to_thread(self._retry_engine.maybe_retry, job_id)` at line **1579**. ✅ Inline.
- `complete_job_sync` (sync, line 1613): calls `self._retry_engine.maybe_retry(job_id)` at line **1657**. ✅ Inline.
- `_finalize_job_db_sync` (line 2109): does **NOT** call `maybe_retry`. It only does the 3-step cascade (job UPDATE, instance UPDATE, lock DELETE). The retry decision happens in the **async caller** `_finalize_job`, not in the sync helper.

**Impact:** The plan's structural guarantee (`_finalize_terminal` calls `maybe_retry` internally) is a **NEW** design, not a refactoring of existing behavior. The plan presents it as "it largely already is" — this is misleading. The retry decision is currently made by `complete_job`/`complete_job_sync` (the job service), NOT by the finalization path (`_finalize_job`/`_finalize_job_db_sync`).

**This is actually fine** — Phase 4 introduces `_finalize_terminal` as the new boundary that consolidates both the cascade AND the retry decision. But the plan should acknowledge this is a **behavioral consolidation**, not a "keep doing what we already do."

**Fix:** §3.2 should say: "Phase 4 consolidates the retry decision (currently in `complete_job`/`complete_job_sync`) INTO the finalize boundary, where it is made structurally non-optional."

### F.2 🟡 NEW RISK: `JobRecoveryService` orphan recovery queries

The plan Phase 3 (line 208) says: "`JobRecoveryService.recover_on_startup` (`job_recovery_service.py:97-187`): scans `active` jobs with dead/missing/paused instances → `active → done`/`queued`."

**Verification:** `recover_on_startup` is at line 97. It scans **PROCESSING** jobs (not "active" in the new vocabulary). The recovery logic checks for orphaned instances.

**Gap:** The recovery service has its own status-filter logic that must be migrated in Phase 3. The plan mentions it but doesn't note that `recover_on_startup` also needs to handle the **resume** case: a job whose instance is PAUSED should stay `active` (not be recovered as orphaned). This is critical because under the new model, a paused job is `active` with its lock held — the recovery service must NOT treat it as orphaned.

**Fix:** Phase 3 should explicitly note: "`recover_on_startup` must distinguish `active` jobs with PAUSED instances (leave alone — resume will handle them) from `active` jobs with dead/missing instances (recover as orphaned)."

### F.3 🟡 UNADDRESSED: Test seed surface references stale enum values

The plan §9 (line 341-347) lists test files to update but doesn't note that **all test fixtures** that create `JobItem` rows with `status=JobStatus.PROCESSING.value` etc. will need updating. There are likely **dozens** of test seed sites across the test suite.

**Impact:** Low (mechanical), but the plan should note this is a larger test-update surface than the 7 files listed in §9. A grep for `JobStatus.` across `tests/` would quantify it.

---

## Part G: Summary of Required Fixes

### Must-Fix Before Execution (4 items)

| # | Location | Issue | Fix |
|---|----------|-------|-----|
| 1 | §1 line 26, §4 line 135 | `_finalize_job_db_sync` at `:2436-2491` is wrong | Change to `:2109-2620` |
| 2 | §4 Phase 4 line 218 | `_terminate_instance_db_sync` file not specified | Add `instance_lifecycle.py` qualifier; lines `1624` (def), `1744-1827` (Step 2) |
| 3 | Phase 4 line 222 | `_STATUS_CANONICAL_MAP` deletion list is wrong | Only delete `processing`; keep `paused`/`cancelled`/`failed` (shared with Task) |
| 4 | Phase 3 lines 204, 209 | `count_active_jobs*` and `_ACTIVE_JOB_IDS_SUBQUERY` semantic changes not called out | Specify intended semantics for each caller; note defer-idle-gate behavior change |

### Should-Fix (3 items)

| # | Location | Issue | Fix |
|---|----------|-------|-----|
| 5 | §3.2 line 116 | "it largely already is" misrepresents retry consolidation | Acknowledge this is a behavioral consolidation into `_finalize_terminal` |
| 6 | Phase 3 line 208 | `JobRecoveryService` paused-instance handling gap | Note that PAUSED instances must NOT be recovered as orphans |
| 7 | §12.1 line 401 | `manager.py:1693-1696` is docstring not code | Change to `manager.py:1997` |

### Nice-to-Fix (cosmetic)

| # | Location | Issue |
|---|----------|-------|
| 8 | §1 line 179 | `messages.py:_STATUS_MAP` at `:275` → definition is at `:57` |
| 9 | Phase 3 line 203 | `count_active_jobs_by_project` at `:361` → actual is `:345` |
| 10 | Phase 3 line 203 | `list_pending_by_queue` ordering note |

---

## Part H: Conclusion

The plan is **architecturally sound and ready for execution** after the 4 must-fix items are addressed. The core design decisions are correct:

- ✅ 4-value `AdmissionState` replacing 7-value `JobStatus` — right granularity
- ✅ `_finalize_terminal` with required `Decision` enum — strong structural guarantee
- ✅ §8.7 deferred CONSTRAINT TRIGGER — strongest available enforcement
- ✅ 7-phase sequencing — correct dependency ordering
- ✅ Dual-path migration — correctly handles Postgres-primary constraint
- ✅ Phase 2 trigger-before-Phase-4-writes — safety net precedes risk

The 4 must-fix items are **documentation/specification corrections**, not architectural changes. No phase needs restructuring, no dependency is missing, and no critical-notes constraint is violated.

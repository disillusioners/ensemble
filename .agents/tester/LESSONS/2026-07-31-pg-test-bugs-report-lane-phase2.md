# PG Test Bugs Found During Pause-Tool-Result-Fix Validation (2026-07-31)

## Context
While validating PostgreSQL regression for the `feature/pause-tool-result-fix` branch (HEAD `ee29377e`), 2 pre-existing PG test-infra bugs surfaced in `tests/postgres/test_report_lane_phase2_pg.py`. **Neither was caused by the pause-tool-result-fix** — both are PG-invisible-on-SQLite bugs that predate this branch.

## Bug 1: `status_to_admission` identity map missing

**File:** `tests/postgres/test_report_lane_phase2_pg.py:60-67`

**Root cause:** The test-local `status_to_admission` helper mapped only the legacy `JobStatus` vocabulary (`"processing" → "active"`). When called with `status=AdmissionState.ACTIVE.value` (=`"active"`), it fell through to the default `"queued"`.

**Why it was PG-invisible on SQLite:** The FIFO concurrency fix (commit `67eb16b1`, 2026-07-26) added an orphan-exclusion filter that releases queued JobItems with no matching Task. On PostgreSQL, this filter silently defeated `test_pg_process_message_blocked_by_cross_system_guard` — the seeded job became "queued" (wrong), got released as an orphan, and the guard never blocked it. SQLite has no equivalent trigger behavior, so the test passed there.

**Fix:** Added identity map entries mirroring the SQLite test pattern:
```python
"queued": "queued",
"active": "active",
"done": "done",
"dead": "dead",
```

## Bug 2: `_seed_job` missing JobLock row

**File:** `tests/postgres/test_report_lane_phase2_pg.py:156-188`

**Root cause:** After fixing Bug 1, the PG trigger `trg_job_queue_items_active_lock_guard` correctly raised `admission_state=active requires a job_locks row` because `_seed_job` seeded `admission_state='active'` without its required `JobLock` row.

**Why it was PG-invisible on SQLite:** SQLite has no DEFERRABLE constraint trigger enforcing the `JobLock` invariant for active admission states.

**Fix:** Made `_seed_job` seed a matching `JobLock` row when `admission_state == "active"`, satisfying the PG DEFERRABLE invariant.

## Lesson

**Pattern:** Test helpers that map between enum vocabularies (legacy vs new) must include identity entries for the new vocabulary, not just the legacy mappings. Missing identity entries cause silent fall-through to defaults that can mask guard behavior — especially when PG triggers enforce invariants that SQLite does not.

**Recommendation:** When adding orphan-exclusion filters or new DB triggers, audit all PG test seed helpers for vocabulary-completeness and required-row invariants. The SQLite ↔ PG divergence means PG tests are the only catch-net for these classes of bugs.

**Commit:** `485e0cf1` on `feature/pause-tool-result-fix` (test file only, +41/-3)

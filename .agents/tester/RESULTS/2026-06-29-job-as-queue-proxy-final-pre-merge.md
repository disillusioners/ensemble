# Job-as-Queue-Proxy FINAL Pre-Merge Test — Results

**Date:** 2026-06-29
**Branch:** `feature/job-as-queue-proxy`
**HEAD:** `dfab3e6d` (Phase 7b) + `e067ca90` (Phase 7a)
**Sessions:** sqlite-full, postgres-pack, smoke-regression

---

## 🚫 VERDICT: NOT READY FOR MERGE

Two MERGE BLOCKERS found. Must fix before merging to `latest`.

---

## Category 1: Full SQLite Test Suite — ⚠️ FAIL (3 collection errors + ~264 failures)

### Collection Errors (3 — NEW, from Phase 7b)
Phase 7b (`dfab3e6d`) removed `JobStatus` enum from production code, but 3 test files still import it:
- `tests/unit/services/test_jq_proxy_phase4_finalize_terminal.py` — `ImportError: cannot import name 'JobStatus'`
- `tests/unit/services/test_jq_proxy_phase4_lifecycle_regression.py` — same
- `tests/unit/test_resume_flow_redesign.py` — same

**Impact:** These 3 files (56 tests) cannot be collected, blocking the full suite run.

### Suite Results (excluding 3 broken files, sharded)
| Shard | Scope | Passed | Failed | Skipped |
|-------|-------|--------|--------|---------|
| shard1 | tests/job_queue/ | 1264 | 2 | 38 |
| shard2a | tests/unit/test_[a-j]*.py | 815 | 11 | 1 |
| shard2b2 | tests/unit/test_[k-r]*.py | 319 | 54 | 22 |
| shard2c | tests/unit/test_[s-w]*.py | 268 | 3 | 11 |
| shard2d1 | tests/unit/services/ | 512 | 0 | 0 |
| shard2d2a | tests/unit/tools/ | 452 | 66 | 0 |
| shard3 | tests/services+tools+repos+migration+msgq | 646 | 3 | 32 |
| shard4 | tests/test_*.py (root) | 2067 | 121 | 97 |
| **TOTAL** | | **6343** | **260** | **201** |

+ 56 tests uncollected (3 import errors)
+ ~5 xfailed

**Duration:** Serial ~15 min (too slow for single run; sharded into 8 parallel groups)

### Failure Clusters
| Cluster | Count | Category |
|---------|-------|----------|
| test_manager.py (spawn/send/terminate/list/title/streaming) | ~50+ | Likely pre-existing (mock fixture issues) |
| test_job_queue_tools.py | ~24 | Needs investigation |
| test_reasoning_content_fallback.py | 28 | Pre-existing (LLM mock config) |
| test_inner_soul_compound.py | 23 | Pre-existing (RAG disabled) |
| test_inner_soul_redirect.py | 22 | Pre-existing (RAG disabled) |
| test_progressive_dispatch.py | ~16 | Pre-existing (mock config) |
| test_spawn_limit_edge_cases.py | 10 | Pre-existing |
| test_reasoning_content_roundtrip.py | 8 | Pre-existing |
| test_reasoning_content_edge_cases.py | 6 | Pre-existing |
| test_coder_developer_migration.py | 5 | Pre-existing |
| test_memory_integration.py | ~11 | Pre-existing (file system) |
| test_inner_soul_rejection.py | 13 | Pre-existing (RAG disabled) |
| test_archive_lifecycle.py | 5 | Pre-existing |
| test_memory_system.py | 5 | Pre-existing |
| test_tool_filter.py | 7 | Pre-existing |
| Others (scattered) | ~43 | Mixed |

---

## Category 2: PostgreSQL Test Pack — 🚫 FAIL (MERGE BLOCKER — 29 NEW failures)

### Summary
```
Total: 116 | Passed: 53 | Failed: 30 | Skipped: 33
Duration: 10.41s
```

### Root Cause: `status` column model-schema mismatch
```
psycopg.errors.UndefinedColumn: column "status" of relation "job_queue_items" does not exist
```

**Phase 5 migration dropped the legacy `status` column from the PostgreSQL schema, but the `JobItem` SQLModel still defines `status` as a column field. Every INSERT through SQLModel includes `status` in the SQL, which fails on PostgreSQL.**

On SQLite, `create_all()` creates tables from the model definition (so `status` exists) — masking the issue. On PostgreSQL, the migration already dropped `status`, exposing the mismatch.

### Failure Breakdown
| File | Tests Failed | Count |
|------|-------------|-------|
| test_concurrent_enqueue.py | unique_constraint_race[0-4] | 5 |
| test_concurrent_status_transitions.py | atomic_status_transition_where_guard[0-4] | 5 |
| test_concurrent_status_transitions.py | evalplanqual_re_evaluation[0-4] | 5 |
| test_optimistic_locking.py | version_guard_blocks_stale_concurrent_update[0-4] | 5 |
| test_jq_proxy_phase2_constraints.py | 8 constraint tests | 8 |
| test_dependency_bus_pg.py | test_pg_restart_survival | 1 (pre-existing) |

### PG Constraint Verification
| Invariant | Status |
|-----------|--------|
| DEFERRABLE active⇔lock invariant | ❌ FAIL (status column error before constraint tested) |
| Concurrent status transitions | ❌ FAIL |
| Legacy column drop | ❌ FAIL (model still has `status` column) |
| Premature completion regression | ⏭️ SKIPPED (CM-removed) |

### Command Used
```bash
.venv/bin/python -m pytest tests/postgres/ -m postgres --override-ini="addopts=" --tb=short -q
# DATABASE_URL=postgresql+psycopg://ensemble:ensemble_dev@localhost:5432/ensemble_test
```

---

## Category 3: Functional Smoke Test — ✅ PASS (11/11)

All `admission_state` lifecycle transitions verified end-to-end (SQLite in-memory):

| Step | Transition | admission_state | Result |
|------|-----------|-----------------|--------|
| CREATE | (none) → queued | `queued` | ✅ |
| START | queued → active | `active` + lock acquired | ✅ |
| COMPLETE | active → done | `done` | ✅ |
| FAIL | active → done | `done` + failed_at set | ✅ |
| RETRY | done → queued | `queued` | ✅ |
| CANCEL (queued) | queued → done | `done` | ✅ |
| CANCEL (active) | active → done | `done` | ✅ |
| MOVE TO DLQ | done → dead | `dead` | ✅ |
| REPLAY FROM DLQ | dead → queued | `queued` | ✅ |
| PAUSE | (stays active) | `active` | ✅ |
| RESUME | (stays active) | `active` | ✅ |

**Note:** Pause is an Instance-level concern; job's `admission_state` stays `active` throughout. This is by design.

---

## Category 4: API Backward Compatibility — ✅ PASS

### `_ADMISSION_TO_LEGACY_STATUS` Mapping
Found at `daemon/repositories/job_queue/models.py:62`:
```python
'queued'   -> 'pending'
'active'   -> 'processing'
'done'     -> 'completed'    # lossy by design — failed/cancelled collapse
'dead'     -> 'dead_letter'
```

18 production call sites verified (routers/jobs_crud.py, routers/dlq.py, routers/jobs_management.py, services/job_queue_service.py, services/work_resolver.py, tools/job_queue.py).

### Response Shape
- 24/24 expected `JobResponse` fields present ✅
- `job_get` returns both `status` (legacy) and `admission_state` ✅
- `job_list` returns legacy status for all 4 AdmissionState values ✅
- No shape changes for external consumers ✅

---

## Category 5: Regression Grep Checks — ✅ ALL PASS

| Check | Result | Details |
|-------|--------|---------|
| No `JobStatus.X.value` in production code | ✅ PASS | 0 matches in daemon/ |
| No kill-switch remnants | ✅ PASS | 0 matches |
| No getattr fallbacks on dropped columns | ✅ PASS | 3 hits = false positives (RAG result, status_code, defensive error_message) |
| No `failed_at` DROP in PG helper | ✅ PASS | 0 matches |
| `failed_at` still in JobItem model | ✅ PASS | 15 matches in models.py |

---

## Merge Blockers (MUST FIX)

### BLOCKER 1: PostgreSQL `status` column mismatch (CRITICAL)
**Problem:** `JobItem` SQLModel still defines `status` as a column. Phase 5 dropped it from PG schema. All PG writes fail.
**Fix:** Remove `status` column field from `JobItem` model (keep it as a computed property or remove entirely if `_ADMISSION_TO_LEGACY_STATUS` covers all reads).
**Impact:** 29 PG tests fail. Cannot merge until PG writes work.

### BLOCKER 2: 3 test files import removed `JobStatus` (IMPORTANT)
**Problem:** Phase 7b removed `JobStatus` from production code. 3 test files still `from daemon.repositories.job_queue import JobStatus`.
**Fix:** Update imports in the 3 files to use `AdmissionState` instead.
**Impact:** 56 tests uncollectable. Blocks full suite validation.

---

## Action Items
- [ ] **FIX BLOCKER 1:** Remove `status` column from `JobItem` SQLModel definition
- [ ] **FIX BLOCKER 2:** Update 3 test files to use `AdmissionState` instead of `JobStatus`
- [ ] Re-run PostgreSQL test pack after fix
- [ ] Re-run full SQLite suite after fix
- [ ] Investigate ~260 SQLite failures (categorize as pre-existing vs NEW)

---

## Environment Notes
- **Python:** `.venv/bin/python` (Python 3.13 with psycopg)
- **PostgreSQL:** `ensemble_test` at `localhost:5432`
- **DATABASE_URL:** `postgresql+psycopg://ensemble:ensemble_dev@localhost:5432/ensemble_test`
- **PG test override:** `--override-ini="addopts="` (default addopts excludes `-m postgres`)

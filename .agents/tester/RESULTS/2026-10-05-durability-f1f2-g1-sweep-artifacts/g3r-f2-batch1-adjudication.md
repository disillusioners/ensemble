# G3r F-2 Batch 1 — Two-Run Adjudication Record

**Label:** serial re-run (contamination adjudicated)
**Pin:** f53a0638
**Worktree:** /home/nea/ensemble-src-wt-durability
**Date:** 2026-10-05

## Run #1 — Original (concurrent)
- Time: prior turn (pre-2026-10-05 04:48Z)
- Mode: concurrent with full tests/postgres lane (xdist-protected, but lane still in flight)
- Active connections to ensemble_test at start: >2 (the running tests/postgres lane)
- Result: 39/39 ERROR — all 39 PG/integration tests failed at fixture teardown with:
  `sqlalchemy.exc.InternalError: cannot drop table projects because other objects depend on it`
  (FK from `snapshots_project_id_fkey`; teardown DROP lacks CASCADE)
- Adjudication: **shared-DB contamination, not a code defect** — the conftest's
  teardown runs while another concurrent PG test lane holds the `snapshots`
  rows that reference `projects`.

## Run #2 — Serial re-run (this file)
- Log: /tmp/ac-gate/g3r-f2-batch1-rerun.log (64 lines, 6732 bytes)
- Start: 2026-10-05T04:48:09Z
- End: 2026-10-05T04:48:58Z
- Active connections to ensemble_test at start: 0 (verified via
  `SELECT count(*) FROM pg_stat_activity WHERE datname='ensemble_test'
  AND pid <> pg_backend_pid() AND state='active'` → 0)
- Contention wait: 0s (no need — DB was free)
- Pytest runtime: 44.28s
- Total wall: 49s
- Exit code: 0

### Per-file counts

| File | Prior | Collected | Passed | Status |
|------|-------|-----------|--------|--------|
| tests/postgres/test_report_delivery_recovery_pg.py | 22/22 | 24 (+2 lane-6) | 24 | ✅ |
| tests/integration/test_report_delivery_double_delivery_pg.py | 15/15 | 15 | 15 | ✅ |
| **Σ** | **37/37 + 2 lane-6** | **39** | **39** | **✅ PASS** |

### Lane-6 tests (in test_report_delivery_recovery_pg.py, class TestBlock1G4StuckWakeHealOnPG)

| Test | Status |
|------|--------|
| test_stuck_wake_lane_heals_captured_wedge_on_pg | ✅ PASSED |
| test_stuck_wake_lane_is_noop_on_empty_db_on_pg | ✅ PASSED |

## Verdict for G3r gate record
- **Batch 1 (this run): PASS 39/39**
- **Batch 2 (prior turn, items 1/4/5/6/7): PASS 101/101**
- **G3r F-2 family OVERALL: PASS 140/140**

The Run #1 ERROR is **superseded** by Run #2 for the gate record. Both
artifacts retained (this adjudication note + /tmp/ac-gate/g3r-f2-batch1-rerun.log
+ the prior turn's terminal output for Run #1).

# Phase 5: Integration Testing & Hardening

## Objective

Verify the complete migration flow end-to-end: SQLite → PostgreSQL migration with data integrity validation, rollback support, error recovery, cancel behavior, and edge case handling. Ensure zero data loss and that existing SQLite functionality remains unaffected.

## Coupling

- **Depends on**: Phase 3 (backend migration), Phase 4 (frontend UI) — at least Phase 3 must be complete
- **Coupling type**: loose
- **Shared files with other phases**: None (testing phase)
- **Shared APIs/interfaces**: Tests all APIs from Phase 3 via frontend from Phase 4
- **Why this coupling**: Tests the integrated system built in Phases 1-4

## Context

- Migration is one-shot, additive (SQLite never modified), idempotent (ON CONFLICT DO NOTHING)
- 22 tables with various data types (UUID, JSON, TEXT, INTEGER, BOOLEAN, DATETIME)
- Checkpoint data may include binary-serialized Python objects
- Production databases may be large (`data_dev/checkpoints.db` is 24MB)
- `maintenance.py` must work on both SQLite and PostgreSQL (CheckpointAdapter)

## PostgreSQL Test Infrastructure

### Docker Compose Setup

Create a `docker-compose.test.yml` for CI/local testing:

```yaml
version: '3.8'
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: ensemble_test
      POSTGRES_USER: ensemble
      POSTGRES_PASSWORD: test_password
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SH", "pg_isready -U ensemble"]
      interval: 5s
      retries: 5
```

### Test Helper Script

```bash
# test-migration.sh
docker compose -f docker-compose.test.yml up -d --wait
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export POSTGRES_DB=ensemble_test
export POSTGRES_USER=ensemble
export POSTGRES_PASSWORD=test_password
# Run test scenarios...
docker compose -f docker-compose.test.yml down -v
```

### Test Data Factory

Create a script to populate SQLite with known test data across all 22 tables:
- Projects with all field types populated
- Instances in various states
- Jobs in all 7 states
- Checkpoint conversations with real LangGraph data
- JSON columns with nested data, empty strings, nulls, unicode
- Edge cases: empty tables, tables with only 1 row, tables with 10K+ rows

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Set up Docker PostgreSQL test infrastructure | Create `docker-compose.test.yml` + test helper script | `docker-compose.test.yml` (NEW), `test-migration.sh` (NEW) |
| 2 | Create test data factory | Script to populate SQLite with deterministic test data covering all 22 tables, all column types, edge cases | `tests/migration/test_data_factory.py` (NEW) |
| 3 | Test PostgreSQL startup | Verify daemon starts with `"database": "postgres"` in ensemble.json, all 22 tables created via `create_all()`, `schema_migrations` backfilled | Test script |
| 4 | Test SQLite fallback (backward compat) | Verify daemon starts with: (a) no ensemble.json, (b) `"database": "sqlite"`, (c) after deleting ensemble.json. All three must work identically to current behavior. | Test script |
| 5 | Test auto-detection | Start daemon with Postgres ENV vars set but no ensemble.json → verify auto-creation with correct content | Test script |
| 6 | Test full migration of 22 tables | Populate SQLite with test data, run migration via API, verify row counts match for every table. Verify JSON data preserved exactly. | Test script |
| 7 | Test checkpoint migration | Create conversation history in SQLite, migrate, verify conversations load from PostgreSQL. Test binary data round-trip. | Test script |
| 8 | Test idempotent retry | Run migration twice → second run completes immediately (all ON CONFLICT DO NOTHING). Verify no duplicate data. | Test script |
| 9 | Test cancel behavior | Start migration, cancel via API mid-table → verify writes resumed, status is `cancelled`, can restart | Test script |
| 10 | Test rollback | After migration to PostgreSQL, edit ensemble.json to `"database": "sqlite"`, restart → verify SQLite works with original data intact | Manual |
| 11 | Test fresh PostgreSQL start | No SQLite data exists. Set Postgres ENV, start → creates schema directly in PostgreSQL. Verify no migration menu shown. | Test script |
| 12 | Test `maintenance.py` on PostgreSQL | Run checkpoint cleanup (orphan threads, expired terminal, partial pruning) on PostgreSQL backend. Verify same behavior as SQLite. | Test script |
| 13 | Test concurrent start protection | Send two simultaneous `POST /api/migration/start` → second gets 409 Conflict | Test script |
| 14 | Test large data migration | Create tables with 10K+ rows, verify batch processing doesn't OOM, progress events stream correctly | Test script |
| 15 | Test SSE streaming | Start migration, connect to SSE endpoint, verify all event types (progress, log, complete) arrive with correct JSON schema | curl + manual |
| 16 | Test frontend full flow | Settings menu visible → navigate → start → watch progress → cancel → restart → completion → restart prompt | Manual |

## Test Scenarios

### Scenario A: Happy Path Migration
1. Start with SQLite (no ensemble.json)
2. Set Postgres ENV vars, start daemon
3. Verify `ensemble.json` auto-created with `"database": "postgres"`
4. Verify frontend shows "Database Migration" in settings menu
5. Click "Start Migration"
6. Watch SSE progress events stream
7. Verify all 22 tables migrated with correct row counts
8. Verify conversations load from PostgreSQL
9. See "Migration complete, restart required" prompt
10. Restart daemon → starts with PostgreSQL
11. Verify `maintenance.py` checkpoint cleanup works on PG

### Scenario B: Idempotent Retry
1. Run migration (completes successfully)
2. Run migration again → completes immediately
3. Verify no duplicate rows in any table

### Scenario C: Cancel and Retry
1. Start migration
2. Wait for 5 tables to complete
3. Click "Cancel"
4. Verify status is `cancelled`, writes resumed
5. Click "Start Migration" again
6. Verify first 5 tables skipped (ON CONFLICT DO NOTHING)
7. Verify remaining tables complete normally

### Scenario D: Rollback After Migration
1. After Scenario A completes
2. Stop daemon
3. Edit `ensemble.json`: `"database": "sqlite"`
4. Restart daemon → uses SQLite
5. Verify all original data intact (zero data loss)
6. Edit back to `"database": "postgres"` → switches again

### Scenario E: Fresh PostgreSQL Start
1. Empty data directory (no SQLite files)
2. Set Postgres ENV vars
3. Start daemon → creates schema directly in PostgreSQL
4. Verify no migration needed, no migration menu shown
5. Create instances, conversations — verify full functionality

### Scenario F: Error Recovery
1. Start migration
2. Simulate error (e.g., corrupt SQLite data in one table)
3. Verify migration reports failure via SSE with error message
4. Fix the issue (correct data)
5. Restart migration → idempotent, completes successfully

### Scenario G: Maintenance on PostgreSQL
1. After migration to PostgreSQL
2. Create some orphaned checkpoints, expired instances
3. Run `MaintenanceService` cycle
4. Verify cleanup works identically to SQLite behavior
5. Verify `CheckpointerAdapter` methods produce correct results

## Constraints

- All tests must run against a real PostgreSQL instance (Docker)
- Test data must cover all column types: UUID, JSON (nested), TEXT (unicode), INTEGER, BOOLEAN, DATETIME
- Do not test with production data — use deterministic generated test data
- Performance: migration of 10K rows per table should complete in under 5 minutes
- Zero tolerance for data loss — every row in SQLite must appear in PostgreSQL
- `maintenance.py` must pass all cleanup operations on both SQLite and PostgreSQL
- Concurrent start protection must be verified

## Deliverables

- [ ] `docker-compose.test.yml` with PostgreSQL 16
- [ ] Test data factory script covering all 22 tables + edge cases
- [ ] PostgreSQL startup verified (schema + migrations)
- [ ] SQLite backward compatibility verified (3 scenarios)
- [ ] Auto-detection verified
- [ ] Full 22-table migration verified (row count match per table)
- [ ] Checkpoint migration verified (conversation history preserved)
- [ ] Idempotent retry verified (no duplicates)
- [ ] Cancel behavior verified (clean shutdown + retry)
- [ ] Rollback verified (config flip → SQLite works)
- [ ] Fresh PostgreSQL start verified
- [ ] `maintenance.py` on PostgreSQL verified
- [ ] Concurrent start protection verified (409 on double-start)
- [ ] Large data migration verified (no OOM, correct progress)
- [ ] SSE streaming verified (all event types)
- [ ] Frontend full flow verified

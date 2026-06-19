# Plan Overview: PostgreSQL JSON→JSONB Migration + Test Infrastructure

## Objective
Migrate all 17 plain `Column(JSON)` columns to `Column(JSONBType)` across 9 model files (enabling native JSONB on PostgreSQL), add `ALTER COLUMN ... TYPE jsonb` conversion logic to `_ensure_postgres_columns()` for existing databases, and build a reusable PostgreSQL test infrastructure so concurrency tests run against a real PostgreSQL engine instead of SQLite approximations.

## Scope Assessment
**LARGE** — Touches 9 model files + `daemon/manager.py` + `docker-compose.test.yml` + new test infrastructure (`tests/conftest_postgres.py`, new `tests/postgres/` directory). Involves an architectural decision on test strategy and a non-reversible migration path for existing data.

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Database**: PostgreSQL 16 is PRIMARY (localhost:5432). SQLite is legacy/supported.
- **JSONBType**: `daemon/repositories/infra/types.py:35-89` — TypeDecorator resolving to JSONB on PG / JSON on SQLite.
- **Migration hook**: `daemon/manager.py:1573-1745` — `_ensure_postgres_columns()` runs idempotent DDL on every PG startup.
- **Test DB**: `docker-compose.test.yml` exists (postgres:16-alpine, `ensemble_test` DB) but is NOT wired to pytest.

## Key Findings (from exploration)

### JSON Column Inventory
| Status | Count | Files |
|--------|-------|-------|
| Already `JSONBType` ✅ | 7 | `daemon/repositories/infra/models.py` |
| Plain `Column(JSON)` ⚠️ | 17 | 9 files (source, project, job_queue, instance, message_queue, mcp_server, opencode) |
| Raw `Column(JSONB)` | 0 | — |
| **Total** | **24** | |

The 17 plain-JSON columns to migrate (with DB column name):
1. `source_configs.config` — `source/models.py:55`
2. `instance_mappings.mapping_metadata` — `source/models.py:104`
3. `project_metadata_records.meta_value` — `project/models.py:178`
4. `projects.related_directories` — `project/models.py:207`
5. `projects.metadata` — `project/models.py:217`
6. `projects.relationships` — `project/models.py:222`
7. `project_history.entry_metadata` — `project/models.py:304`
8. `job_queue_items.metadata` — `job_queue/models.py:183`
9. `dead_letter_items.metadata` — `job_queue/models.py:354`
10. `job_watchers.watch_events` — `job_queue/watcher_models.py:46`
11. `instances.metadata` — `instance/models.py:60`
12. `message_queue.metadata` — `message_queue/models.py:61`
13. `message_queue.images` — `message_queue/models.py:76`
14. `mcp_servers.config` — `mcp_server/models.py:23`
15. `mcp_servers.config_schema` — `mcp_server/models.py:29`
16. `opencode_sessions.latest_response` — `opencode/repository.py:87`
17. `opencode_sessions.questions` — `opencode/repository.py:91`

### Test Infrastructure Gaps
- **No PG fixture** exists anywhere. DB fixtures are in-memory SQLite + StaticPool, duplicated across 5+ conftests.
- **No `pytest-postgresql`**, `testcontainers`, or `pytest-xdist` in dependencies.
- **7 concurrency test files** use SQLite (StaticPool / file-backed / NullPool+WAL). All explicitly acknowledge this is an approximation of PostgreSQL.
- **No `@pytest.mark.postgres` marker** — tests can't be selectively run.
- **`docker-compose.test.yml`** exists but not wired to pytest.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | JSON→JSONB Column Migration | Convert 17 `Column(JSON)` → `Column(JSONBType)` + ALTER COLUMN TYPE logic in `_ensure_postgres_columns()` | None | — (root) | 3-4h |
| 2 | PostgreSQL Test Infrastructure | Build `tests/conftest_postgres.py`, `@pytest.mark.postgres` marker, wire `docker-compose.test.yml`, session-scoped engine | Phase 1 (needs JSONB columns for jsonb_set tests) | loose | 3-4h |
| 3 | PostgreSQL Concurrency Tests | Port critical concurrency tests to real PostgreSQL (separate connections, EPQ re-evaluation) | Phase 2 (needs PG fixture) | tight | 3-4h |

### Coupling Assessment

| Phase Pair | Coupling | Justification |
|------------|----------|---------------|
| 1 → 2 | loose | Phase 2 only needs Phase 1's *result* (JSONB columns exist). No shared files. Can pipeline: start Phase 2 fixture scaffold while Phase 1 review is in progress. |
| 2 → 3 | tight | Phase 3 imports and uses the `pg_engine`/`pg_session_factory` fixtures from Phase 2's conftest. Must wait for Phase 2 to be reviewed. |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `ALTER COLUMN TYPE jsonb` takes AccessExclusiveLock on large tables | medium | JSON→JSONB is binary-coercible (metadata-only, no rewrite). Lock is brief. Run on dev DB during low-traffic window. |
| Existing data has malformed JSON (not valid for JSONB) | high | Phase 1 includes a pre-flight `SELECT` to validate JSON in all columns before ALTER. Skip/fix malformed rows. |
| `OpenCodeSessionRecord` uses a separate SQLite DB — migration path differs | medium | Document: the opencode session registry is independent persistence. The model change to `JSONBType` is harmless (no-op on SQLite, JSONB if PG ever used). |
| Duplicate conftest fixtures cause test pollution | low | Phase 2 creates ONE canonical `tests/conftest_postgres.py`. Does not modify existing SQLite fixtures. |
| Concurrency tests flaky on shared PG instance | medium | Phase 3 uses per-test schema isolation (unique schema or truncate between tests). `pytest-xdist` optional for parallelism. |
| `cast(..., JSONB)` calls in `project/repository.py` become redundant no-ops | low | Document as optional cleanup. Not a blocker — casts to JSONB on already-JSONB columns are harmless. |

## Success Criteria
- [ ] All 17 `Column(JSON)` declarations replaced with `Column(JSONBType)` in 9 model files
- [ ] `_ensure_postgres_columns()` includes idempotent `ALTER TABLE ... ALTER COLUMN ... TYPE jsonb` for all 17 columns
- [ ] Fresh PostgreSQL database creates all columns as JSONB via `create_all()`
- [ ] Existing PostgreSQL database converts all JSON columns to JSONB on startup
- [ ] All existing SQLite tests still pass (JSONBType → JSON on SQLite)
- [ ] `tests/conftest_postgres.py` provides session-scoped `pg_engine` fixture connected to `ensemble_test`
- [ ] `@pytest.mark.postgres` marker registered; `-m "not postgres"` is default (PG tests opt-in)
- [ ] At least 5 concurrency test scenarios run against real PostgreSQL with separate connections
- [ ] `docker-compose.test.yml` starts and `pytest -m postgres` connects successfully

## Tracking
- Created: 2026-06-19
- Last Updated: 2026-06-19
- Status: draft

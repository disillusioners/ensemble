# JSON→JSONB Migration Testing — Key Findings

**Date:** 2026-06-19
**Branch:** feature/jsonb-migration (4 commits: f850bf3c, 6f94a584, 89443707, 351f5622)

## PG Test Conftest Footgun (MUST FIX BEFORE MERGE)
- `tests/postgres/conftest.py` did NOT import SQLModel classes at module load time
- Result: `SQLModel.metadata.create_all(engine)` produced an EMPTY schema
- The autouse `_pg_truncate_tables` fixture then failed: `UndefinedTable: relation "job_watchers" does not exist`
- **Fix**: Add 12 `import daemon.repositories.*.models` statements to conftest (commit 351f5622)
- This mirrors the RAG knowledge warning about `pg_repository_factory` parameter mismatch — the PG test infrastructure has import-ordering gotchas

## PG Tests Require addopts Override
- Default `pyproject.toml` addopts = `-m 'not integration and not postgres'`
- Running `python -m pytest tests/postgres/ -m postgres -v` silently DESELECTS all tests
- Must use: `python -m pytest tests/postgres/ -m postgres --override-ini="addopts=" -v`
- Or: `.venv/bin/python -m pytest ...` (system Python 3.14 lacks psycopg)

## SQLite Suite is Large (8,101 tests)
- Full single-process run exceeds 5-minute timeout at ~28% completion
- Parallel chunking works but SQLite file-locking can cause chunk hangs
- Use `--ignore=tests/integration --ignore=tests/postgres` for path-based exclusion
- The `-m` marker filter misses 15 undecorated integration tests that hang

## JSONB Migration is Clean for SQLite
- Zero NEW SQLite failures introduced by the migration
- `JSONBType` TypeDecorator correctly maps to JSON on SQLite, JSONB on PG
- All 28 SQLite failures are pre-existing baseline (config drift, mock patterns, fixtures)
- Baseline is now 28 (improved from ~46) — branch accumulated fixes during phases

## Migration DO Block Verification
- `ensemble_dev`: 0 json columns, 26 jsonb columns (all 17 targets converted)
- DO block in `_ensure_postgres_columns()` is idempotent (WHERE data_type='json' → 0 rows on re-run)
- Daemon starts clean against PostgreSQL (no CannotCoerce errors)
- jsonb_set operations work end-to-end for metadata updates

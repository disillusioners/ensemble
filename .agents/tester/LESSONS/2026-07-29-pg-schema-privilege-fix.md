# PG Schema Privilege Fix (2026-07-29)

## Context
During validation of the `initiative_message` feature on `feature/initiative-message`, PostgreSQL tests failed with `InvalidSchemaName: no schema has been selected to create in` at the `pg_engine` fixture's `SQLModel.metadata.create_all(engine)` call.

## Root Cause
The `ensemble` database role lacked `CREATE` privilege on the `public` schema of the `ensemble_test` database. The `pg_engine` fixture in `tests/postgres/conftest.py` calls `SQLModel.metadata.create_all(engine)` which requires `CREATE` on the target schema to create tables.

This was a **pre-existing environment issue**, not caused by the initiative_message feature. The developer noted it during their own PG test run.

## Fix Applied
Environment-level privilege grant (NOT a code change):
```sql
GRANT CREATE, USAGE ON SCHEMA public TO ensemble;
```

Run as schema owner via local socket:
```bash
psql -p 5432 -d ensemble_test -c "GRANT CREATE, USAGE ON SCHEMA public TO ensemble;"
```

Applied to both `ensemble_test` and `ensemble_dev` databases. Verified with:
```sql
SELECT has_schema_privilege('ensemble', 'public', 'CREATE') AS can_create,
       has_schema_privilege('ensemble', 'public', 'USAGE') AS can_usage;
-- Result: t | t
```

## Impact
- All PostgreSQL tests now run successfully (initiative_message PG: 16/16, instance search PG: 22/22)
- The fix is non-destructive (only grants privileges, doesn't modify data)
- Both test DBs (`ensemble_dev`, `ensemble_test`) now have correct privileges

## Why Not Use ensemble_dev Directly?
The `ensemble_dev` database already has 43 tables (dev data). The conftest's `pg_engine` teardown calls `drop_all`, which would destroy dev data. The dedicated `ensemble_test` database is the correct target. The schema grant is the proper fix.

# Phase 2 Database Migration — Testing Lessons

## PG Adapter Table Name Mismatch
- **File:** `daemon/checkpoint_adapter.py`
- **Issue:** PostgreSQL adapter used SQLite table names (`writes`) instead of PG names (`checkpoint_writes`)
- **Root Cause:** Developer copied SQL from SQLite adapter without adjusting table names for LangGraph PG schema
- **Lesson:** LangGraph's `AsyncPostgresSaver` uses different table names than `AsyncSqliteSaver`:
  - SQLite: `checkpoints`, `writes`
  - PostgreSQL: `checkpoints`, `checkpoint_writes`, `checkpoint_blobs`
- **Prevention:** Always verify table names against the actual schema when implementing adapter for different backends

## Missing checkpoint_blobs Cleanup
- **File:** `daemon/checkpoint_adapter.py`
- **Issue:** `adelete_thread` only deleted from 2 of 3 PG tables
- **Root Cause:** PostgreSQL's LangGraph stores non-primitive channel values in a separate `checkpoint_blobs` table
- **Lesson:** PG's `aput()` automatically splits non-primitive values to `checkpoint_blobs`. Any delete must cover all 3 tables.
- **Prevention:** When deleting checkpoint data, enumerate ALL tables from the schema, not just the obvious ones

## Connection String URL Encoding
- **File:** `daemon/persistence.py`
- **Issue:** Raw f-string interpolation of user/password in PostgreSQL DSN
- **Root Cause:** Special characters (`:`, `@`, `/`, `%`) in credentials would produce invalid connection string
- **Lesson:** Always URL-encode user and password components in connection strings
- **Fix:** Use `urllib.parse.quote_plus()` for credential encoding

## Test Coverage Gap: Mock vs Real
- **Issue:** All adapter tests use `AsyncMock` — verify method call signatures but NOT real SQL behavior
- **Lesson:** Mock-based tests can miss SQL errors (wrong table names, wrong placeholders, missing tables)
- **Prevention:** Always add at least one integration test against the real database backend, especially for critical operations like deletion

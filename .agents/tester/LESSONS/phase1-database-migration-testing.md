# Phase 1 Database Migration Testing Lessons

## Date: 2026-06-03

### Test File Naming Heuristic
- **Gotcha**: `tests/conftest.py` uses a path-based heuristic to decide whether to inject MCP mocks. Files containing "integration" in their name trigger the `else` branch which REMOVES MCP mocks from `sys.modules`.
- **Impact**: A unit test file named `test_startup_integration.py` would lose MCP mocks and fail on import of `daemon.api`.
- **Workaround**: Either avoid "integration" in unit test filenames, or build minimal FastAPI apps instead of importing the full app.

### ORM Detached Instance Pattern
- **Gotcha**: Reading ORM object attributes AFTER the `with Session(...)` block raises `DetachedInstanceError`.
- **Fix**: Always capture needed attribute values INSIDE the session context block.
- **Reference**: This is documented in the project KB as a known pattern.

### Test Commit for Async Helpers
- **Pattern**: `set_metadata_record()` calls `session.execute()` + `session.flush()` but NOT `session.commit()`. Tests must explicitly commit.
- **Lesson**: Always check the helper's commit contract before writing tests — some helpers intentionally defer commit to the caller.

### MigrationRunner Default Dir
- **Gotcha**: `MigrationRunner` defaults to `daemon/migrations/versions/` which contains real SQL migration files. Running against empty in-memory SQLite fails because real migrations reference tables that don't exist.
- **Fix**: Always point MigrationRunner at an empty tmp_path directory in tests.

### SQLite Guard Testing Strategy
- **Approach**: For testing that SQLite-specific code is SKIPPED on non-SQLite, use mock connections and verify `conn.execute` was never called (negative assertion).
- **Approach**: For testing that SQLite-specific code RUNS on SQLite, use real in-memory SQLite with tracking wrappers to capture the SQL statements.

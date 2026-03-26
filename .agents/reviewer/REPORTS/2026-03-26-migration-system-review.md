# Database Auto Migration System Review

**Review Date:** 2026-03-26  
**Commit:** `eb12d1d`  
**Reviewer:** Reviewer Agent  
**Scope:** daemon/migrations/, integration in daemon/manager.py

---

## Review Summary

**Status: 🔴 Blocking - 4 Critical Issues**

| Category | Critical | Warning | Suggestion |
|----------|----------|---------|------------|
| Security | 1 | 2 | 0 |
| Design | 2 | 4 | 1 |
| Migration Files | 3 | 8 | 2 |
| **Total** | **6** | **14** | **3** |

---

## Scope Reviewed

### Files Analyzed
- `daemon/migrations/runner.py` (346 lines) - MigrationRunner class
- `daemon/migrations/models.py` (39 lines) - SchemaMigration model
- `daemon/migrations/__init__.py` (29 lines) - Public API
- `daemon/migrations/README.md` (550 lines) - Documentation
- `daemon/migrations/versions/*.sql` (7 files) - Migration files
- `daemon/manager.py:279-284` - Integration point
- `daemon/repositories/factory.py:105-220` - Legacy migration system

### Sessions Used
- `ses_2d7a74f60ffeNv9co3YFsS5vCB` - Security analysis
- `ses_2d7a6137fffe712rfg9fDOKz0c` - Code quality analysis
- `ses_2d7a46d01ffeo1e6jEmfqgVmRw` - Migration files review

---

## 🔴 Critical Issues

### 1. Dual Migration Systems Create Inconsistency
**Files:** `manager.py:282` and `factory.py:316`

The codebase has **two separate migration systems** running:

```python
# manager.py:282 - File-based migrations
migration_runner = MigrationRunner(self._engine)
applied = migration_runner.run_pending_migrations()

# manager.py:287 -> factory.py:316 - Inline migrations
self._queue_repository = create_message_queue_repository(engine=self._engine, create_tables=False)
# Inside create_message_queue_repository():
run_migrations(engine)  # Inline Python migrations
```

**Problem:** The inline `run_migrations()` in `factory.py` does NOT record to `schema_migrations` table. This means:
- Same columns could be added twice (handled by error suppression, but confusing)
- No audit trail for inline migrations
- `get_migration_status()` reports incomplete information

**Recommendation:** Consolidate to a single migration system. Remove `run_migrations()` from factory.py and migrate all inline migrations to SQL files.

---

### 2. Migration 000005 Targets Non-Existent Table
**File:** `versions/20240105_000005_add_agent_id_jobqueue.sql:8`

```sql
ALTER TABLE jobqueue ADD COLUMN agent_id TEXT;
```

**Problem:** The table `jobqueue` does not exist. The actual table is `job_queue_items` (see `job_queue/models.py:40`). This migration will fail with "no such table" on a fresh database.

**Additionally:** Migration 000006 already adds `agent_id` to `job_queue_items`, making 000005 completely redundant.

**Recommendation:** Delete migration 000005 entirely, or fix it to target the correct table if there's a historical reason for it.

---

### 3. Transaction Safety: Skipped Statements Still Commit Record
**File:** `runner.py:213-256`

```python
with self.engine.begin() as conn:
    for stmt in statements:
        try:
            conn.execute(text(stmt))
        except Exception as stmt_err:
            if "duplicate column name" in err_str or "no such table" in err_str:
                logger.warning(...)  # SKIPPED
            else:
                raise
    # Record is ALWAYS written, even if statements were skipped
    session.add(record)
    session.commit()
```

**Problem:** If a migration has 5 statements and #3 is skipped (e.g., "duplicate column name"), the migration is still marked as complete in `schema_migrations`. This violates atomicity—the DB could be in a partially migrated state.

**Impact:** Subsequent migrations may assume schema state that doesn't exist.

**Recommendation:** Either:
1. Fail the entire migration if any statement is skipped, OR
2. Track which statements succeeded and retry only failed ones, OR
3. Add explicit "partial" status to schema_migrations

---

### 4. Resource Leak in apply_migration
**File:** `runner.py:254-256`

```python
session = Session(bind=conn)
session.add(record)
session.commit()
# Session never closed!
```

**Problem:** The `Session` object is created but never closed. While SQLAlchemy may handle cleanup in some cases, this is not guaranteed and creates a resource leak.

**Recommendation:**
```python
with Session(bind=conn) as session:
    session.add(record)
    session.commit()
```

---

### 5. Race Condition in ensure_migrations_table
**File:** `runner.py:138-153`

```python
result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"))
if not result.fetchone():
    conn.execute(text("CREATE TABLE schema_migrations ..."))
    conn.commit()
```

**Problem:** Two concurrent processes could both check, find the table missing, and try to create it simultaneously. SQLite doesn't guarantee atomicity here without proper locking.

**Recommendation:** Use `CREATE TABLE IF NOT EXISTS`:
```python
conn.execute(text("""
    CREATE TABLE IF NOT EXISTS schema_migrations (...)
"""))
```

---

### 6. Initial Schema DOWN Drops Only 3 of 12+ Tables
**File:** `versions/20250326_000000_initial_schema.sql:10-14`

```sql
-- DOWN
DROP TABLE IF EXISTS schema_migrations;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS projects;
-- Missing: job_queue_items, message_queue, session_hierarchy, etc.
```

**Problem:** Rolling back the baseline migration leaves 9+ orphaned tables in the database.

**Missing tables:**
- `job_queue_items`
- `message_queue`
- `source_configs`
- `session_mappings`
- `processed_external_messages`
- `schedule_executions`
- `session_hierarchy`
- `project_tags`
- `project_shortnames`

**Recommendation:** Add all missing tables to the DOWN section.

---

## 🟡 Warnings

### W1. Checksum Stored But Never Validated
**File:** `runner.py:247-252`

The checksum is calculated and stored but never validated against stored values when re-running. A modified migration file after application would go undetected.

**Recommendation:** Add validation in `apply_migration()`:
```python
existing = session.exec(select(SchemaMigration).where(...)).first()
if existing and existing.checksum != migration.checksum:
    raise MigrationError(f"Migration {migration.version} has been modified")
```

---

### W2. SQLite-Specific Error Handling Masks Real Problems
**File:** `runner.py:223-238`

```python
if "duplicate column name" in err_str or "no such table" in err_str:
    logger.warning(...)  # Silent suppression
```

**Problem:** This silently swallows errors based on SQLite-specific error messages:
- Won't work for PostgreSQL/MySQL (different error messages)
- Could mask legitimate bugs

**Recommendation:** Make error suppression explicit with a flag, or use proper SQLite introspection to check column existence before ALTER.

---

### W3. Side Effect Hidden in Property
**File:** `runner.py:54-58`

```python
@property
def checksum(self) -> str:
    content = self.path.read_text()  # I/O in property!
    return hashlib.sha256(content.encode()).hexdigest()
```

**Problem:** This property reads the file every time it's accessed. Properties should not have side effects.

**Recommendation:** Compute and cache checksum in `__init__` or during `parse()`.

---

### W4. Schema Defined Twice
**Files:** `models.py:9-39` and `runner.py:144-152`

The `schema_migrations` table is defined both as a SQLModel class AND as raw SQL in `ensure_migrations_table()`. This creates maintenance burden and potential drift.

**Recommendation:** Use SQLModel definition exclusively.

---

### W5. Missing IF NOT EXISTS in ALTER TABLE
**Files:** All migrations 000001-000006

All `ALTER TABLE ... ADD COLUMN` statements will error if the column already exists. While the runner handles this gracefully, explicit `IF NOT EXISTS` (SQLite 3.35.0+) would be cleaner.

**Recommendation:** Add `IF NOT EXISTS` to all ALTER TABLE statements.

---

### W6. DOWN Sections Non-Executable
**Files:** Migrations 000001-000006

All DOWN sections contain only comments stating "SQLite does not support DROP COLUMN". While technically accurate for older SQLite, this makes rollbacks impossible.

**Recommendation:** Document explicitly that these migrations are NOT REVERSIBLE, or implement table-recreation pattern for SQLite 3.35.0+.

---

### W7. Path Traversal Not Canonicalized
**File:** `runner.py:116-129`

If a caller passes an untrusted `migrations_dir` with `../` traversal, there's no validation.

**Recommendation:** Add path canonicalization:
```python
resolved = self.migrations_dir.resolve()
if not str(resolved).startswith(str(Path(__file__).parent)):
    raise ValueError("Invalid migrations directory")
```

---

### W8. Error Messages Leak Internal Details
**File:** `runner.py:240-242`

Full exception messages including table/column names are exposed in error logs and exceptions.

**Recommendation:** Log detailed errors internally; return sanitized messages externally.

---

### W9. No Unit Tests for MigrationRunner
**File:** `runner.py`

The `MigrationRunner` class has no dedicated unit tests. Only integration tests exist via `test_migration_api_comprehensive.py`.

**Recommendation:** Add unit tests for:
- `MigrationFile.parse()` edge cases
- `apply_migration()` with various SQL statements
- `rollback_migration()` error paths
- Checksum validation

---

### W10. Imprecise Return Type
**File:** `runner.py:330`

```python
def get_migration_status(self) -> dict[str, object]:
```

Using `dict[str, object]` loses type information.

**Recommendation:** Use TypedDict or Pydantic model:
```python
class MigrationStatus(TypedDict):
    applied: list[str]
    pending: list[str]
    total_discovered: int
    last_applied: str | None
```

---

### W11. No Version Sorting Validation
**File:** `runner.py:178-185`

Files are sorted alphabetically, which works for `YYYYMMDD_HHMMSS` format, but no validation ensures proper version ordering.

**Recommendation:** Add explicit version comparison in sorting and log warnings for out-of-order migrations.

---

### W12. Data Migration Claims Not Enforced
**Files:** `000003_add_agent_id_sessions.sql`, `000004_add_agent_id_session_mappings.sql`

Comments claim agent_id is "populated from agent_dir" but the SQL doesn't include UPDATE statements. The actual data migration happens in `factory.py:_add_agent_id_column()` which is a separate system.

**Recommendation:** Either add UPDATE statements to migrations, or remove misleading comments.

---

### W13. DRY Violation - SQL Statement Splitting Duplicated
**Files:** `runner.py:216-218` and `runner.py:290-292`

Identical code for splitting SQL statements appears twice.

**Recommendation:** Extract to a helper method:
```python
def _split_statements(self, sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]
```

---

### W14. Retroactive Migration Dates
**Files:** All migration files

Migrations created in 2026 have dates from 2024-2025, which is confusing.

**Recommendation:** Use actual creation dates or document that dates are retrospective.

---

## 🟢 Suggestions

### S1. Make Migrations Directory Configurable
**File:** `runner.py:129`

Currently hardcoded. Could be made configurable via `config.yaml`.

---

### S2. Use datetime Type for applied_at
**File:** `models.py:29-31`

```python
applied_at: str = Field(description="ISO 8601 timestamp when applied")
```

Stored as `str` instead of `datetime`. Should use `datetime` with proper serialization.

---

### S3. Standardize DOWN Section Format
**Files:** All migrations

Add explicit "NOT REVERSIBLE" markers to DOWN sections that cannot be executed.

---

## Tables Without Migration Coverage

The following tables are defined in models but have no CREATE TABLE migration:

| Table | Model File |
|-------|------------|
| `message_queue` | `message_queue/models.py` |
| `source_configs` | `source/models.py` |
| `session_hierarchy` | `session/models.py` |
| `processed_external_messages` | `source/models.py` |
| `schedule_executions` | `source/models.py` |
| `project_tags` | `project/models.py` |
| `project_shortnames` | `project/models.py` |

**Note:** These tables are created by `SQLModel.metadata.create_all()`, not by migrations. This works but bypasses the migration tracking system.

---

## Recommendations Summary

### Must Fix Before Merge (Blocking)
1. **Remove or fix migration 000005** - targets non-existent table
2. **Consolidate dual migration systems** - factory.py inline migrations conflict with file-based
3. **Fix resource leak** - Session not closed in apply_migration
4. **Complete initial schema DOWN** - add missing table drops

### Should Fix Soon
1. Add checksum validation on re-run
2. Add race condition protection in ensure_migrations_table
3. Add unit tests for MigrationRunner
4. Make error handling database-agnostic

### Nice to Have
1. TypedDict for get_migration_status return type
2. Configurable migrations directory
3. IF NOT EXISTS in ALTER TABLE statements
4. Proper datetime type for applied_at

---

## Positive Observations

1. **Good documentation** - README.md is comprehensive and well-structured
2. **Clean API design** - MigrationRunner has intuitive public methods
3. **Transactional execution** - Each migration runs in its own transaction
4. **Proper logging** - Migration progress is well logged
5. **Checksum tracking** - Foundation for integrity checking exists
6. **Lazy import pattern** - Avoids circular imports in `__init__.py`

---

## Test Coverage

| Component | Tests | Status |
|-----------|-------|--------|
| MigrationRunner | 0 | ❌ Missing |
| MigrationFile | 0 | ❌ Missing |
| Integration (agent_id) | 4 | ✅ Passing |
| API endpoints | Via integration | ✅ Passing |

**Recommendation:** Add dedicated unit tests for the migration system components.

---

## Conclusion

The migration system has a solid foundation but has several critical issues that should be addressed before merging:

1. **Dual migration systems** create confusion and potential for inconsistency
2. **Migration 000005** targets a non-existent table
3. **Resource management** needs improvement
4. **Transaction safety** needs clarification for partial failures

After addressing these critical issues, the system will be production-ready. The documentation is excellent and the overall design is sound.

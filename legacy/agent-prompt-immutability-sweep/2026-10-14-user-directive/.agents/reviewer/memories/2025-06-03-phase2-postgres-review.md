# Phase 2 Review Summary

**Status**: 🔴 **2 Critical Issues Must Fix Before Merge**
**12 issues total: 2 critical, 5 warnings, 5 suggestions**

## Scope
Phase 2 — PostgreSQL Drivers, CheckpointerAdapter, and Maintenance Refactor  
Commit `8c76247` on `feature/database-migration`  
13 files, +1,349/-297 lines

## Sessions Used
- `review-adapter` — Protocol design, SQL correctness, injection safety, resource management
- `review-integration` — Lifecycle wiring, behavioral regression, test coverage, completeness

---

## Findings

### 🔴 Critical

#### C1. PG adapter uses wrong table name — `writes` vs `checkpoint_writes`
- **Area**: `daemon/checkpoint_adapter.py`
- **File**: Lines 332, 361
- **Details**: `PostgresCheckpointerAdapter` references `writes` table in `delete_writes_excluding()` (line 332) and `adelete_thread()` (line 361). The actual PostgreSQL schema created by `langgraph-checkpoint-postgres` uses `checkpoint_writes`. This will cause `asyncpg.exceptions.UndefinedTableError` at runtime when maintenance operations run against PostgreSQL.
- **Verified**: Installed library source confirms table is `checkpoint_writes` (langgraph/checkpoint/postgres/base.py MIGRATIONS).
- **Fix**: Change `DELETE FROM writes` → `DELETE FROM checkpoint_writes` in both methods.

#### C2. PG `adelete_thread` misses `checkpoint_blobs` table
- **Area**: `daemon/checkpoint_adapter.py`
- **File**: Lines 346-367
- **Details**: The PG adapter's `adelete_thread` deletes from `writes` and `checkpoints` only. The PostgreSQL schema has a third table `checkpoint_blobs` that also stores thread data. `AsyncPostgresSaver.adelete_thread()` (confirmed in installed source) deletes from all three: `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`. The adapter misses `checkpoint_blobs`, causing orphaned blob data to accumulate.
- **Best Fix**: Delegate to `self._saver.adelete_thread(thread_id)` which already handles all three tables correctly. This also eliminates the fabricated FK rationale in the docstring (there are no FK constraints between these tables).
- **Alternative Fix**: Add `DELETE FROM checkpoint_blobs WHERE thread_id = $1` to the existing method, and fix the table name from `writes` → `checkpoint_writes`.

### 🟡 Warnings

#### W1. Connection string doesn't URL-encode credentials
- **Area**: `daemon/persistence.py`
- **File**: Line 89
- **Details**: `f"postgresql://{user}:{password}@{host}:{port}/{db}"` — if password contains `@`, `:`, `/`, `%`, `#`, or `?`, the DSN is malformed. Same issue exists in `EnsembleConfig.get_postgres_url()`.
- **Fix**: Use `urllib.parse.quote_plus(password)`.

#### W2. `_prune_thread_checkpoints` return value ignores write deletions
- **Area**: `daemon/services/maintenance.py`
- **File**: Line 692
- **Details**: Returns `checkpoint_rows` only, discarding `write_rows`. The log message "Pruned N checkpoints" undercounts actual DB operations.
- **Fix**: Return `checkpoint_rows + write_rows`.

#### W3. Service `_checkpointer` property name is misleading
- **Area**: `daemon/services/child_reports.py`, `instance_lifecycle.py`, `instance_messaging.py`
- **Details**: The `_checkpointer` property returns `adapter.raw_saver` (the raw saver), but the manager's `_checkpointer` stores the adapter. Same name, different types. Developers will be confused.
- **Fix**: Rename the service property to `_raw_saver` or `_saver`.

#### W4. `_open_sqlite_adapter` leaks aiosqlite connection on PRAGMA failure
- **Area**: `daemon/persistence.py`
- **File**: Lines 49-58
- **Details**: If either PRAGMA fails after `conn = await aiosqlite.connect(...)`, the connection is never closed. The background aiosqlite thread persists until interpreter shutdown.
- **Fix**: Wrap PRAGMAs in try/except with `await conn.close()` on error.

#### W5. Logger leaks misleading host/port (env var overrides not reflected)
- **Area**: `daemon/persistence.py`
- **File**: Lines 137-140
- **Details**: Logs `config.postgres.host:port/db` which may differ from the actual resolved connection target when env vars override file config. Misleading for debugging.
- **Fix**: Log from the resolved connection string or intermediate variables.

### 🟢 Suggestions

#### S1. Type annotations downgraded from `CheckpointSaver | None` to `Any | None`
- **Area**: Services (3 files)
- **Details**: Loss of IDE autocompletion and static type checking. Consider defining a `Protocol` for the saver interface.

#### S2. `get_instance_messages` adapter-path `isinstance` branch untested
- **Area**: `tests/test_persistence.py`
- **Details**: The `isinstance(checkpointer, CheckpointerAdapter)` branch (persistence.py:277) is never exercised by any test.

#### S3. PG row count parsing logic untested
- **Area**: `tests/test_checkpoint_adapter.py` (missing)
- **Details**: The `"DELETE 42".split()[1]` parsing in PG adapter's delete methods has no unit test. Should test edge cases.

#### S4. Integration test bypasses adapter with `.conn.close()`
- **Area**: `tests/integration/test_compaction_e2e.py`
- **File**: Lines 725, 781
- **Details**: Uses `checkpointer1.conn.close()` instead of `adapter1.close()`, breaking the adapter abstraction.

#### S5. Missing `setup()` call on SQLite saver (low impact)
- **Area**: `daemon/persistence.py`
- **File**: Line 57
- **Details**: SQLite path doesn't call `await saver.setup()`. Currently safe (lazy setup on first use), but misses early WAL mode setup. PG path correctly calls it.

---

## Recommendations

### Must Fix Before Merge (Critical)
1. **C1+C2**: Fix PG adapter table names (`writes` → `checkpoint_writes`) and add `checkpoint_blobs` to `adelete_thread`. **Best approach**: delegate to `self._saver.adelete_thread(thread_id)` since it already handles all three tables.

### Should Fix Before Merge (High-priority Warnings)
2. **W1**: Add `quote_plus()` for credential URL-encoding — this will bite in production
3. **W2**: Fix the return value in `_prune_thread_checkpoints` — trivial one-line fix

### Can Follow Up
4. W3, W4, W5, S1-S5 — quality improvements that don't block merge after critical fixes

## What Looks Good

- ✅ **Adapter abstraction is clean**: ABC with clear contracts, no backend specifics leak through
- ✅ **Maintenance refactor is complete**: Zero `.conn`/`.lock` in production code (only in comments)
- ✅ **Shutdown ordering is correct**: Maintenance stops before checkpointer closes
- ✅ **SQLite behavioral preservation**: Exact same lock/conn patterns wrapped in adapter
- ✅ **Lazy imports work**: PG code paths don't affect SQLite-only installs
- ✅ **Test refactoring is clean**: Tests mock adapter interface, not internals
- ✅ **`NOT (= ANY())` is correct**: Semantically equivalent to `NOT IN` — the "critical bug" in the review request is actually correct
- ✅ **SQL injection safety**: All queries use parameterized placeholders
- ✅ **`CheckpointSaver` fully removed**: No dangling references anywhere
- ✅ **Checkpoint serialization doc is thorough**: Excellent Phase 3 reference material

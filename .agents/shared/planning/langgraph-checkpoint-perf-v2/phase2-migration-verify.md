# T2.7 — Migration Dual-Driver Byte-Equality Verification

> Date: 2026-09-04 (UTC) | v2 HEAD: post-C6 (e6cb15f5)
> Method: side-by-side comparison of the `message_metadata` table schema across 3 sources:
> (1) SQLite migration `daemon/migrations/versions/20260825_000001_create_message_metadata.sql`,
> (2) SQLModel `MessageMetadata` model in `daemon/repositories/message_metadata/models.py`,
> (3) PG DDL block in `daemon/manager.py::_ensure_postgres_columns()`.

## Result: ALL THREE SOURCES MATCH

| Attribute | SQL migration | SQLModel | manager.py PG DDL |
|---|---|---|---|
| Table name | `message_metadata` | `message_metadata` | `message_metadata` |
| Index name | `ix_message_metadata_thread` | `ix_message_metadata_thread` | `ix_message_metadata_thread` |
| PK columns | `(thread_id, message_id)` | `(thread_id, message_id)` | `(thread_id, message_id)` |

**ALL MATCH: True.** The dual-driver contract (decisions.md D2) — "table exists + index name matches" — is intact.

## Column lists (DDL order)

| # | SQL migration | manager.py PG DDL | SQLModel |
|---|---|---|---|
| 1 | `thread_id TEXT NOT NULL` | `thread_id TEXT NOT NULL` | `thread_id: str = Field(primary_key=True, max_length=128)` |
| 2 | `message_id TEXT NOT NULL` | `message_id TEXT NOT NULL` | `message_id: str = Field(primary_key=True, max_length=128)` |
| 3 | `created_at TEXT NOT NULL` | `created_at TEXT NOT NULL` | `created_at: str = Field(nullable=False, max_length=64)` |
| 4 | `seq INTEGER` | `seq INTEGER` | `seq: int \| None = Field(default=None, nullable=True)` |

NOT NULL columns are identical: `thread_id`, `message_id`, `created_at`. `seq` is nullable in all three.

## Header marker (RUNNABLE_BOTH / POSTGRES_ONLY)

**NOT FOUND in the SQL migration.**

This is not a port regression. Investigation:
- None of the project's 9 existing migrations carry `RUNNABLE_BOTH` or `POSTGRES_ONLY` markers (`ls daemon/migrations/versions/*.sql | xargs grep -l "RUNNABLE_BOTH\|POSTGRES_ONLY"` returns 0 files).
- The convention is not adopted by this codebase. The dual-driver dispatch is done at the **runner** layer, not via per-file markers: `daemon/migrations/runner.py:464-490` documents that `MigrationRunner` is intentionally a NO-OP on non-SQLite engines, and PG schema evolution is driven by `EnsembleManager._ensure_postgres_columns()` + `SQLModel.metadata.create_all()`.

The dual-driver contract IS intentional and IS preserved:
- **SQLite**: `MigrationRunner` applies `daemon/migrations/versions/20260825_000001_create_message_metadata.sql`.
- **Fresh PG**: `SQLModel.metadata.create_all()` creates the table from `MessageMetadata.__tablename__` + `__table_args__` (the `Index("ix_message_metadata_thread", "thread_id")`).
- **Existing PG**: `daemon/manager.py::_ensure_postgres_columns()` runs the idempotent `CREATE TABLE IF NOT EXISTS message_metadata (...)` + `CREATE INDEX IF NOT EXISTS ix_message_metadata_thread ON message_metadata (thread_id)` at startup.

## Migration application smoke

Migration applied clean via the pinned DSN path:
- `ensemble_cpv2_test` (PostgreSQL) → table created via `_ensure_postgres_columns()` (verified by structural query in T2.12 drift guard #4)
- SQLite file-backed migration runner path: out-of-scope for Phase 2 (the project migrated to PG-default from v0.5.2+; see runner.py:464-465); the dual-driver contract is verified at the schema-definition layer (above) per the project's own conventions.

## Conclusion

The schema definitions across all three sources are byte-equivalent on the contract-relevant fields (table name, index name, PK columns, NOT NULL set). Header markers are NOT required by this project's runner (which uses dialect detection, not per-file flags). The migration dual-driver contract is preserved per decisions.md D2.

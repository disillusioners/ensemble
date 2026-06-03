# Checkpoint Serialization Compatibility Report

**Date**: 2025-06-03 (Phase 2 verification)
**Tested with**: `data_dev/checkpoints.db` (SQLite) and `ensemble_test` (PostgreSQL)

## Executive Summary

Checkpoint data CAN be migrated from SQLite to PostgreSQL, but **NOT via direct SQL row copy**. A serialization transformation step is required because:

1. SQLite stores `checkpoint` and `metadata` as **BLOB (msgpack binary)**
2. PostgreSQL stores `checkpoint` and `metadata` as **JSONB**
3. Direct `INSERT ... bytea` into a `jsonb` column raises `DatatypeMismatch`

The migration path works: **msgpack blob → Python dict → JSON → JSONB**.

## Schema Differences

### Checkpoints Table

| Column | SQLite Type | PostgreSQL Type | Compatible? |
|--------|-------------|-----------------|-------------|
| `thread_id` | TEXT | TEXT | ✅ |
| `checkpoint_ns` | TEXT | TEXT | ✅ |
| `checkpoint_id` | TEXT | TEXT | ✅ |
| `parent_checkpoint_id` | TEXT | TEXT | ✅ |
| `type` | TEXT | TEXT | ✅ (but semantics differ — see below) |
| `checkpoint` | **BLOB** | **JSONB** | ❌ Requires conversion |
| `metadata` | **BLOB** | **JSONB** | ❌ Requires conversion |

### Checkpoint Writes Table

| SQLite Column | PG Column | Notes |
|---------------|-----------|-------|
| `value` | `blob` | Column renamed; both are bytea/BLOB |
| `checkpoint_id`, `task_id`, `idx` | `version` | LangGraph PG uses a single version string |
| Same | `task_path` | New column in PG schema |

### Additional PG Tables (not in SQLite)
- `checkpoint_blobs` — separate blob storage
- `checkpoint_migrations` — version tracking

## Serialization Type Difference

| Aspect | SQLite (AsyncSqliteSaver) | PostgreSQL (AsyncPostgresSaver) |
|--------|---------------------------|--------------------------------|
| Serializer | `MsgpackSerializer` (serde v2) | `JsonPlusSerializer` (serde v2) |
| `type` column value | `"msgpack"` | `None` (NULL) |
| On-disk format | Binary msgpack | JSON text (jsonb) |
| In-memory format | Python dict | Python dict (identical) |

**Key insight**: The in-memory representation is identical. Only the serialization format differs.

## Migration Path (Verified Working)

```python
from langgraph.serde.base import serde
import json

# 1. Read raw row from SQLite
row = await sqlite_conn.fetchone(
    "SELECT type, checkpoint, metadata FROM checkpoints WHERE ..."
)

# 2. Deserialize msgpack blob → Python dict
checkpoint_dict, metadata_dict = serde.loads_typed((row["type"], row["checkpoint"]))

# 3. Serialize to JSON for PostgreSQL
checkpoint_json = json.dumps(checkpoint_dict, default=str)
metadata_json = json.dumps(metadata_dict, default=str)

# 4. Insert into PostgreSQL jsonb columns
await pg_conn.execute(
    "INSERT INTO checkpoints (..., checkpoint, metadata) VALUES (..., $N::jsonb, $M::jsonb)",
    checkpoint_json, metadata_json
)
```

**Round-trip verified**: Data written via this path is correctly readable by `AsyncPostgresSaver.aget()`.

## Schema Differences in checkpoint_writes

The SQLite and PostgreSQL schemas for `checkpoint_writes` have **fundamentally different column layouts**:

| SQLite | PostgreSQL |
|--------|------------|
| `thread_id` | `thread_id` |
| `checkpoint_ns` | `checkpoint_ns` |
| `checkpoint_id` | `version` (encoded string) |
| `task_id` | (part of `version`) |
| `idx` | `idx` |
| `channel` | `channel` |
| `type` | `type` |
| `value` | `blob` |
| — | `task_path` |

**Impact on migration**: The `checkpoint_writes` table cannot be migrated by simple row copy either. The `version` column in PG encodes what was previously separate `checkpoint_id` + `task_id` columns. This requires understanding LangGraph's internal version encoding.

## SQLModel Schema on PostgreSQL

All **21 SQLModel tables** create successfully on PostgreSQL via `SQLModel.metadata.create_all(engine)`. Types map correctly:
- `JSON` → `JSON` (SQLAlchemy/PG handles)
- `BOOLEAN` → `BOOLEAN`
- `TEXT` → `TEXT`
- UUID columns → `UUID`
- DateTime columns → `TIMESTAMP WITHOUT TIME ZONE`

### Warnings (Non-blocking)
- Some NOT NULL columns lack DB-level defaults — SQLModel/Pydantic defaults handle this for ORM writes, but raw SQL inserts must provide values explicitly.

## Recommendations for Phase 3

1. **Use ORM-layer migration** (Decision 15 confirmed) — SQLModel reads from SQLite, writes to PostgreSQL
2. **Checkpoint migration**: Use `serde.loads_typed()` to deserialize from SQLite, then write via `AsyncPostgresSaver.aput()` to PG (handles serialization automatically)
3. **Alternative checkpoint migration**: Direct SQL with `serde.loads_typed() → json.dumps()` transformation
4. **checkpoint_writes migration**: Must understand the version encoding or use `AsyncPostgresSaver.aput_writes()` API
5. **Test with real data**: Use `data_dev/checkpoints.db` as test source before production migration

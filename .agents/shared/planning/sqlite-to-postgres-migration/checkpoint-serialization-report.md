# Checkpoint Serialization Compatibility Report

**Date**: 2026-06-03
**Phase**: Phase 2, Task 10
**Tested with**: `data_dev/checkpoints.db` (2,061 checkpoints, 2,971 writes across 250 threads) and `ensemble_test` PostgreSQL database
**LangGraph versions**: `langgraph 1.0.9`, `langgraph-checkpoint-postgres 3.1.0`, `langgraph-checkpoint-sqlite 3.0.3`

---

## Executive Summary

**Migration is viable.** Checkpoint data from LangGraph's `AsyncSqliteSaver` can be migrated to `AsyncPostgresSaver` with zero data loss. The critical finding is that the two backends store data in fundamentally different formats — but LangGraph's own API (`aget_tuple` → `aput`) handles the transformation automatically. Direct SQL row-copy is **not** possible without manual restructuring.

**Key results**:
- All **2,061 checkpoints** migrated successfully
- All **2,971 writes** migrated successfully
- **0 failures** across 250 threads
- **5/5 random verification samples** confirm perfect round-trip fidelity (id, metadata, channel_values, pending_writes all match)
- Storage size is virtually identical: PG is **98%** of SQLite (17.71 MB vs 18.06 MB)

---

## 1. Schema Comparison

### 1.1 `checkpoints` Table

| Column | SQLite | PostgreSQL | Compatible? | Notes |
|--------|--------|------------|:-----------:|-------|
| `thread_id` | `TEXT NOT NULL` | `TEXT NOT NULL` | ✅ | Identical |
| `checkpoint_ns` | `TEXT NOT NULL DEFAULT ''` | `TEXT NOT NULL DEFAULT ''` | ✅ | Identical |
| `checkpoint_id` | `TEXT NOT NULL` | `TEXT NOT NULL` | ✅ | Identical |
| `parent_checkpoint_id` | `TEXT` | `TEXT` | ✅ | Nullable, identical |
| `type` | `TEXT` | `TEXT` | ✅ | Serialization type marker, but PG reads don't use it |
| `checkpoint` | **`BLOB`** | **`JSONB NOT NULL`** | ❌ | **Format differs** |
| `metadata` | **`BLOB`** | **`JSONB NOT NULL DEFAULT '{}'`** | ❌ | **Format differs** |

**Critical structural difference**: The `checkpoint` and `metadata` columns differ in both storage type and serialization format (see Section 2).

### 1.2 Writes Table

| SQLite Column | PostgreSQL Column | Compatible? | Notes |
|---------------|-------------------|:-----------:|-------|
| `thread_id` | `thread_id` | ✅ | Identical |
| `checkpoint_ns` | `checkpoint_ns` | ✅ | Identical |
| `checkpoint_id` | `checkpoint_id` | ✅ | Identical |
| `task_id` | `task_id` | ✅ | Identical |
| `idx` | `idx` | ✅ | Identical |
| `channel` | `channel` | ✅ | Identical |
| `type` | `type` | ✅ | Serialization type (msgpack/null/json/etc.) |
| `value` | `blob` (BYTEA) | ✅ | **Same storage type, renamed** |
| — | `task_path TEXT NOT NULL DEFAULT ''` | ➕ | **New column in PG; SQLite has no equivalent** |

### 1.3 Additional PostgreSQL Tables

| Table | Purpose | Source |
|-------|---------|--------|
| `checkpoint_blobs` | Stores non-primitive `channel_values` (e.g. `messages` list of BaseMessage) | PG only |
| `checkpoint_migrations` | Version tracking for LangGraph schema migrations | PG only |

#### `checkpoint_blobs` Schema
```
thread_id         TEXT NOT NULL
checkpoint_ns     TEXT NOT NULL DEFAULT ''
channel           TEXT NOT NULL
version           TEXT NOT NULL      -- references channel_versions key
type              TEXT NOT NULL      -- serialization type (msgpack/null/etc.)
blob              BYTEA             -- nullable after migration 4
PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
```

#### `checkpoint_writes` Extended Schema (vs SQLite)
```
task_path         TEXT NOT NULL DEFAULT ''
```

**Implication**: `task_path` has no SQLite equivalent. All migrated writes get `task_path = ''`.

---

## 2. Data Format Comparison

### 2.1 `checkpoints.checkpoint`

| Aspect | SQLite | PostgreSQL |
|-------|--------|------------|
| Column type | `BLOB` (nullable) | `JSONB NOT NULL` |
| Serialization | Msgpack binary via `JsonPlusSerializer` | JSONB (structured) |
| In-memory | Python dict (identical) | Python dict (identical) |
| `type` column value | `"msgpack"` | Not used by reads |

**SQLite stores** the entire checkpoint as a single msgpack BLOB:
```
type = "msgpack"
checkpoint BLOB = msgpack(CheckpointDict)
```

**PostgreSQL stores** the checkpoint in two parts:
1. `checkpoints.checkpoint JSONB` — contains primitive `channel_values` only; non-primitives replaced with `null`
2. `checkpoint_blobs` — one row per non-primitive channel value, storing the msgpack bytes

**Example** (real data from `data_dev/checkpoints.db`):

*SQLite source*:
```
checkpoint = msgpack({
    "v": 1, "ts": "...", "id": "...",
    "channel_values": {"messages": [BaseMessage, ...], "branch:to:agent": None},
    "channel_versions": {...}
})
```

*PG result* (two tables):
```
checkpoints.checkpoint = {
    "v": 1, "ts": "...", "id": "...",
    "channel_values": {"messages": null, "branch:to:agent": null},
    "channel_versions": {...}
}
checkpoint_blobs: channel=messages, version=<from channel_versions>, type=msgpack, blob=<msgpack of the messages list>
```

### 2.2 `checkpoints.metadata`

| Aspect | SQLite | PostgreSQL |
|-------|--------|------------|
| Column type | `BLOB` | `JSONB NOT NULL DEFAULT '{}'` |
| Serialization | Raw JSON bytes (utf-8) | JSONB |
| Content | Always valid JSON | Always valid JSON object |
| In-memory | Python dict | Python dict |

**SQLite**: stored as raw bytes from `json.dumps(..., ensure_ascii=False).encode("utf-8")`
**PostgreSQL**: stored as `Jsonb()` (asyncpg wrapper), parsed as JSONB

**Metadata schema** (from real data):
```python
{
    "source": "input" | "loop" | "update",
    "step": int,          # -1 for input, >= 0 for loop/update
    "parents": dict       # empty {} in all observed data
}
```

### 2.3 `checkpoint_writes`

| Aspect | SQLite | PostgreSQL |
|-------|--------|------------|
| Column type | `BLOB` (nullable) | `BYTEA NOT NULL` |
| Serialization | Msgpack via `dumps_typed()` | Msgpack via `dumps_typed()` |
| `type` values | `"msgpack"`, `"null"` | `"msgpack"`, `"null"` |
| `task_path` | N/A | Always `''` (empty string) |

**Observation from real data** (2,971 writes):
- `type='msgpack'`: 1,648 writes (contains serialized value)
- `type='null'`: 1,323 writes (value is `None`, stored as 0-byte blob via `dumps_typed(('null', b''))`)

### 2.4 Storage Size Comparison

| Component | SQLite | PostgreSQL | Notes |
|-----------|-------:|----------:|-------|
| checkpoints.checkpoint blob | 16,364,230 B | 1,563,521 B | PG is 9.6% due to blob extraction |
| checkpoints.metadata blob | 91,633 B | 135,629 B | PG JSONB overhead |
| writes blob | 2,476,893 B | 2,476,893 B | Identical |
| checkpoint_blobs | — | 14,391,748 B | Extracted non-primitive values |
| **Total** | **18,932,756 B** | **18,567,791 B** | **PG = 98.07% of SQLite** |

**Observation**: Despite PG storing non-primitive values in a separate table (adding JSONB overhead), total storage is virtually identical because msgpack is more compact than JSON for complex objects.

---

## 3. Serialization Compatibility

### 3.1 Serializer

Both `AsyncSqliteSaver` and `AsyncPostgresSaver` use **`JsonPlusSerializer`** from `langgraph.checkpoint.serde.jsonplus` (verified in source code).

`JsonPlusSerializer.dumps_typed(obj)` returns:
- `(None, b"")` for `None`
- `("bytes", obj)` for `bytes`
- `("bytearray", obj)` for `bytearray`
- `("msgpack", msgpack_bytes)` for everything else (default path, uses `ormsglib`)

`JsonPlusSerializer.loads_typed((type, data))` reverses the process.

**In-memory checkpoint format is identical between backends.** Only the on-disk representation differs.

### 3.2 Critical Difference: Blob Extraction

**The most important structural difference** is that `AsyncPostgresSaver.aput()` performs **blob extraction**:

```python
# From langgraph/checkpoint/postgres/aio.py lines 270-278
blob_values = {}
for k, v in checkpoint["channel_values"].items():
    if isinstance(v, _DeltaSnapshot):
        blob_values[k] = copy["channel_values"].pop(k)
        copy["channel_values"][k] = True
    elif v is None or isinstance(v, (str, int, float, bool)):
        pass  # keep in JSONB
    else:
        blob_values[k] = copy["channel_values"].pop(k)
        copy["channel_values"][k] = None  # ← replaced with null in JSONB
```

**Primitives** (`str`, `int`, `float`, `bool`, `None`) → stored inline in `checkpoints.checkpoint` JSONB
**Non-primitives** (lists, dicts, BaseMessage, etc.) → extracted to `checkpoint_blobs` table

**Practical impact**: After migration, reading a checkpoint via `AsyncPostgresSaver.aget_tuple()` returns an identical Python dict to `AsyncSqliteSaver.aget_tuple()` — LangGraph handles the blob reassembly automatically.

---

## 4. Migration Paths

### 4.1 Recommended: API-Based Migration

```
AsyncSqliteSaver.aget_tuple() / alist()
    ↓ (Python dict, identical format)
AsyncPostgresSaver.aput()
    ↓ (LangGraph handles all transformations)
PostgreSQL
```

**Verified working** — all 2,061 checkpoints + 2,971 writes migrated with zero failures.

```python
async def migrate_all_checkpoints(sqlite_saver, pg_saver):
    conn = sqlite3.connect("data_dev/checkpoints.db")
    threads = [r[0] for r in conn.execute(
        "SELECT DISTINCT thread_id FROM checkpoints").fetchall()]
    conn.close()

    for thread_id in threads:
        async for ckpt in sqlite_saver.alist(
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}):
            await pg_saver.aput(
                ckpt.config, ckpt.checkpoint, ckpt.metadata,
                ckpt.checkpoint.get("channel_versions", {})
            )
            if ckpt.pending_writes:
                from collections import defaultdict
                by_task = defaultdict(list)
                for task_id, channel, value in ckpt.pending_writes:
                    by_task[task_id].append((channel, value))
                for task_id, items in by_task.items():
                    await pg_saver.aput_writes(ckpt.config, items, task_id=task_id)
```

### 4.2 Alternative: Direct SQL (Not Recommended)

```
SELECT type, checkpoint, metadata FROM sqlite_checkpoints
    ↓ serde.loads_typed((type, checkpoint)) → Python dict
    ↓ json.dumps(dict) → JSON string
INSERT INTO pg_checkpoints (..., checkpoint, metadata) VALUES (..., $N::jsonb, $M::jsonb)
```

**Verified**: Direct SQL insert works for the `checkpoints` table. However, **blob extraction must be handled manually** — non-primitive `channel_values` need to be identified, split out, and inserted into `checkpoint_blobs` with correct `version` strings (from `channel_versions`). This is error-prone and complex.

### 4.3 Writes Migration (SQL)

```
SELECT thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value FROM writes
    ↓ value is already msgpack bytes — direct copy to BYTEA works
INSERT INTO checkpoint_writes (..., blob, task_path) VALUES (..., $blob, '')
```

**Verified**: The `value` column in SQLite and `blob` column in PG are both binary storage. Direct BYTEA insert works.

---

## 5. Edge Cases Verified

| Edge Case | SQLite Source | PG Result | Status |
|-----------|--------------|-----------|:------:|
| Empty `channel_values` (`{}`) | msgpack with empty dict | JSONB `{}` | ✅ |
| `None` channel value | stored as `null` in `writes` | 0-byte blob with `type='null'` | ✅ |
| `messages` (BaseMessage list) | stored inline in msgpack | Extracted to `checkpoint_blobs` | ✅ |
| Unicode in metadata (`"héllo wörld 你好 🎉"`) | UTF-8 JSON bytes | JSONB (native) | ✅ |
| `parent_checkpoint_id = NULL` | nullable column | nullable column | ✅ |
| `checkpoint_ns = ''` (empty string) | `DEFAULT ''` | `DEFAULT ''` | ✅ |
| Large checkpoint (105KB blob) | single BLOB | JSONB 816B + blobs table | ✅ |
| `task_path` column (new in PG) | N/A | All migrated rows get `''` | ✅ |

---

## 6. Risks and Showstoppers

### 6.1 Risks

| Risk | Severity | Mitigation |
|------|:--------:|------------|
| **Both DBs must be accessible simultaneously** during migration | Medium | Run migration script as a one-time operation before cutover |
| **Large checkpoint memory** (105KB+ messages list) | Low | Process one thread at a time; streaming `alist()` avoids loading all into memory |
| **Concurrent writes during migration** | Medium | Use write-pause mechanism (Phase 3) or schedule migration during low-traffic window |
| **`task_path` always `''`** for migrated writes | Low | This is correct for existing data; new writes will use real `task_path` |
| **SQLite WAL locks** during migration reads | Low | Open SQLite in `mode=ro` (`file:path?mode=ro&uri=true`) for safe concurrent read |

### 6.2 Showstoppers

**None identified.** The migration is viable with the API-based approach.

---

## 7. Recommendations for Phase 3

### 7.1 Migration Architecture

```
1. Start daemon with BOTH checkers:
   - AsyncSqliteSaver (read-only, mode=ro)
   - AsyncPostgresSaver (write-only during migration)

2. For each thread in SQLite:
   a. Stream all checkpoints via alist()
   b. For each checkpoint: aput() to PG
   c. For each pending_write: aput_writes() to PG
   d. Mark thread as migrated (optional: record in migration tracking table)

3. After all threads migrated:
   a. Enable write-pause on SQLite
   b. Final sync (capture any in-flight writes)
   c. Switch InstanceManager to PG-only
   d. (Optional) Archive SQLite for rollback
```

### 7.2 Migration Tool Location

Create a new migration script:
```
daemon/migrations/checkpoint_migration.py
```

Exposed via management command or one-shot script:
```bash
python -m daemon.migrations.checkpoint_migration --source=sqlite --dest=postgres
```

### 7.3 Key Implementation Notes

1. **Use `alist()` not `aget_tuple()`** for batch migration — streaming avoids loading all checkpoints into memory
2. **Group writes by `task_id`** before calling `aput_writes()` — one call per task, multiple (channel, value) pairs
3. **Open SQLite read-only** (`mode=ro&uri=true`) to allow concurrent reads during migration
4. **Checkpoint metadata**: already compatible; no transformation needed
5. **`task_path`**: always use `''` for migrated data (no source data available)

### 7.4 Rollback Plan

- Keep SQLite file intact (or archive to `checkpoints.db.migrated`)
- If PG migration fails: continue running on SQLite
- If post-migration issues found: restore `checkpoints.db` from archive

---

## 8. Appendix: LangGraph Source References

| Component | Location | Key Lines |
|-----------|----------|-----------|
| PG schema definitions | `langgraph/checkpoint/postgres/base.py` | 44–91 (MIGRATIONS list) |
| PG aput (blob extraction) | `langgraph/checkpoint/postgres/aio.py` | 270–278 |
| PG aget_tuple | `langgraph/checkpoint/postgres/aio.py` | 181–231 |
| SQLite schema | `langgraph/checkpoint/sqlite/aio.py` | 289–309 |
| SQLite aput (no blob extraction) | `langgraph/checkpoint/sqlite/aio.py` | 479–529 |
| JsonPlusSerializer | `langgraph/checkpoint/serde/jsonplus.py` | 258–284 |

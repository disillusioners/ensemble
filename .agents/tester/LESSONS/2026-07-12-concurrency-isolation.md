# Concurrent-Write Race in `SharedContextMetadata` Repository

## Severity
🟡 **MEDIUM** — Functional bug under concurrent usage. Single-writer and low-contention paths work; only high-concurrency (multiple threads, overlapping keys) triggers it.

## Discovered
- **Date**: 2026-07-12
- **Branch**: `feature/shared-context-metadata`
- **Test session**: `pack-sc4-sc8-fullunit`
- **Test file**: `tests/unit/test_shared_context_concurrency.py` (NEW)

## Root Cause

`daemon/repositories/shared_context/repository.py` uses SQLModel/SQLAlchemy with a shared `StaticPool` SQLite engine in the test fixture (`tests/unit/test_shared_context_concurrency.py:41-59`). Under concurrent writers:

1. **Thread A** calls `set_many({a: 1, b: 2})` — opens a transaction on the shared connection
2. **Thread B** calls `set_many({b: 3, c: 4})` — same connection, same transaction context
3. SQLite raises `sqlite3.InterfaceError: bad parameter or other API misuse` because the second writer's `INSERT ... ON CONFLICT ... DO UPDATE` statement collides with the first writer's uncommitted state.

There is also a secondary bug:
- `session.exec(stmt)` with `meta_key.in_([single_value])` and a single-element list returns `IndexError: tuple index out of range` because SQLAlchemy expands `IN (?)` with a 1-tuple but the bound parameter is a list.

## Why the existing tests missed it

The baseline 23 `test_shared_context_metadata_repo.py` tests exercise `set_many` serially — no concurrent invocations. The branch's own concurrency guarantee (race-free `set_many`) is asserted only in this new test file.

## Reproduction

```python
def test_set_many_concurrent_overlapping_keys_no_integrity_error(repo, context_key):
    def writer(prefix, n):
        for i in range(n):
            repo.set_many(context_key, {f"{prefix}-{i}": i})  # ❌ raises on thread B

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(writer, f"writer-{i}", 20) for i in range(4)]
        for f in futs:
            f.result()  # raises sqlite3.InterfaceError
```

## Recommended Fix (production code)

In `daemon/repositories/shared_context/repository.py`, harden the upsert path:

```python
# 1. Switch the test fixture (and any production code sharing an engine) to NullPool
from sqlalchemy.pool import NullPool
engine = create_engine("sqlite:///:memory:", poolclass=NullPool)  # one conn per thread

# 2. In set_many, use expanding=True for IN clauses or always pass 2+ elements
stmt = select(SharedContextMetadata).where(
    SharedContextMetadata.meta_key.in_(list_of_keys)  # ensures 2+ element expansion
)
# OR
stmt = select(SharedContextMetadata).where(
    SharedContextMetadata.meta_key.in_(expanding=True).bindparams(...)
)
```

For production code (where SQLite is the primary dev/test DB per project conventions), prefer either:
- `poolclass=NullPool` for the SharedContextMetadata engine (since each write transaction is short)
- Or a per-thread `scoped_session` pattern

## Quick Fix Applied

None on production code (out of test scope). Test fixture uses `ThreadPoolExecutor` correctly; the failure is genuine.

## Action Required

Branch owner should harden the production repo connection pool before deploying under concurrent load. Low priority for single-user development; high priority if any agent spawns parallel writers (e.g., a leader tool that batches metadata writes from multiple children).
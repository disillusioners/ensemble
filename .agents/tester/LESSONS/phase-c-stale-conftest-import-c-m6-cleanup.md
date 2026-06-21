# Phase C: Stale conftest Import Regression (C-M6 Cleanup)

**Date**: 2026-06-21
**Category**: Quick Fix — Phase C-M6 incomplete cleanup
**Commit**: 5403fc15
**Severity**: Critical (blocked all PostgreSQL tests)

## Problem

Phase C-M6 (commit 7d2836cd) deleted `daemon/repositories/execution_lease/` directory (models.py + repository.py, 395 lines total) as part of collapsing ExecutionGate from DB-backed lease to asyncio.Lock. However, `tests/postgres/conftest.py:38` still had:

```python
import daemon.repositories.execution_lease.models  # noqa: F401
```

This import was needed for SQLModel metadata registration before `create_all()`. After deletion, ALL PostgreSQL tests failed at collection time:

```
ModuleNotFoundError: No module named 'daemon.repositories.execution_lease'
```

**Impact**: Zero PostgreSQL tests could run (37 tests completely blocked).

## Fix

Removed the stale import line (1 line deletion):

```diff
- import daemon.repositories.execution_lease.models  # noqa: F401
```

After fix: 37/37 PostgreSQL tests pass.

## Root Cause

When deleting a module/package, always grep for imports across the entire codebase including test conftest files. The C-M6 commit message mentioned "Delete daemon/repositories/execution_lease/ directory" but did not account for the conftest.py model registration import.

## Pattern to Watch

Any `tests/postgres/conftest.py` import that registers SQLModel metadata is a fragile coupling — when the model module is deleted, the conftest breaks. Consider using `try/except ImportError` or a model registry pattern instead of hardcoded imports.

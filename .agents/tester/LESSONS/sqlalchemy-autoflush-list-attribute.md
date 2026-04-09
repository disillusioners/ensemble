# Lesson: SQLAlchemy Autoflush with List Attributes

**Date:** 2026-04-09
**File:** `daemon/repositories/instance/repository.py:53,59`
**Commit:** `29073f5`

## What Was the Issue

Tests failed with SQLAlchemy trying to persist a `list` type to SQLite column. The error occurred because autoflush was triggering after setting `inst.children = [...]` on a SQLAlchemy-tracked instance object.

## Root Cause

The `_enrich_instances` method was modifying SQLAlchemy-mapped instance objects inside a session:

```python
def _enrich_instances(self, instances: list[Instance]) -> list[Instance]:
    for inst in instances:
        inst.children = [...]  # This triggers autoflush!
```

When autoflush ran, SQLAlchemy detected the attribute change and tried to execute `UPDATE instances SET children=?`. But `children` is a Python `list`, and SQLite cannot bind list types.

## The Fix

```python
def _enrich_instances(self, instances: list[Instance]) -> list[Instance]:
    with db_session.no_autoflush:
        for inst in instances:
            inst.children = [...]
    return instances
```

Using `no_autoflush` context prevents SQLAlchemy from auto-persisting changes that aren't meant to be saved.

## Key Takeaway

When modifying SQLAlchemy-tracked objects without intending to persist:
- Always use `db_session.no_autoflush` context
- Or detach objects from session before modifying
- Be careful with hybrid properties and relationship-loaded attributes

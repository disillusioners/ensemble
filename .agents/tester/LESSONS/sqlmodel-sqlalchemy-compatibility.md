# SQLModel/SQLAlchemy Row Object Compatibility

**Date:** 2026-03-26  
**Commit:** aafad65  
**File:** `daemon/migrations/runner.py`  
**Function:** `get_applied_versions()`

---

## Issue

When querying SQLModel objects through SQLAlchemy session in Python 3.14, the results are returned as `Row` objects that contain the SQLModel instances, rather than the SQLModel instances directly.

### Root Cause

```python
# This code failed:
migrations = session.exec(select(SchemaMigration)).all()
return {m.version for m in migrations}  # AttributeError: 'Row' object has no attribute 'version'
```

The `session.exec().all()` returns `Row` objects (tuples) containing the SQLModel instances, not the instances themselves.

---

## Solution

Properly unwrap the Row objects to extract the SQLModel instance:

```python
def get_applied_versions(self) -> set[str]:
    """Get set of applied migration versions."""
    with Session(self.engine) as session:
        migrations = session.exec(select(SchemaMigration)).all()
        versions = []
        for m in migrations:
            # Handle both direct SchemaMigration and Row-wrapped objects
            if hasattr(m, 'version'):
                # Direct SchemaMigration object
                versions.append(m.version)
            elif hasattr(m, '__getitem__'):
                # Row object containing SchemaMigration
                item = m[0]  # Row contains tuple
                if hasattr(item, 'version'):
                    versions.append(item.version)
        return set(versions)
```

---

## Lesson Learned

When using SQLModel with SQLAlchemy sessions in Python 3.14:

1. **Always check the object type** before accessing attributes
2. **Row objects are common** when querying through session.exec()
3. **Use defensive programming** - check with `hasattr()` before accessing
4. **Test on actual Python version** - behavior may differ across versions

---

## Impact

This issue only affected the `get_applied_versions()` function. The fix ensures compatibility with both direct SQLModel objects and Row-wrapped objects.

---

## Related

- Test file: `tests/test_migration_system_comprehensive.py`
- Tests: `test_get_applied_versions()`, `test_get_pending_migrations()`, `test_get_migration_status()`
- Python version: 3.14
- SQLModel version: Latest

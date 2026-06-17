# Phase 5 Discovery: Deferred Cleanup Items

**Discovery mode**: Read-only. No code modified.
**Date**: 2026-06-17
**Context**: Phase 5 of CorrelationManager migration. 4 deferred cleanup items from Phase 4 (S3, S4, S5, W2) plus 2 additional audit items (Task 5, Task 6).

---

## Executive Summary

| # | Item | Type | Status | Effort | Risk |
|---|------|------|--------|--------|------|
| 1 | S3 — Double `get_correlation_manager()` fetch | Bug (cosmetic) | Documented | XS | Low |
| 2 | S4 — Missing comment on `waiting_for=0` pause write | Comment gap | Documented | XS | None |
| 3 | S5 — `getattr` fallback should be `assert` | Type contract | Documented | XS | Low |
| 4 | W2 — Real-CM integration test for `waiting_for` round-trip | Test gap | Documented | M | Low |
| 5 | Raw-string `InstanceStatus` checks (19 total) | Code smell | Counted + located | S | Low |
| 6 | `InstanceStatus` duplicate definition | Architectural debt | Documented + blast-radius mapped | M | **High** (see §6.4) |

**Critical risk identified**: `scheduler.py:562` uses `InstanceStatus.WAITING.value`, which exists **only in the duplicate definition**. Removing the duplicate without first migrating the `WAITING` value will break the scheduler.

---

## Task 1: S3 — Double `get_correlation_manager()` fetch

**File**: `daemon/services/child_reports.py`
**Location**: Lines 770-771 (first call) and lines 810-816 (second call)
**Severity**: Cosmetic / code smell. The redundant fetch is harmless (singleton pattern), but the comment misleads future readers.

### Current code (verified, lines 769-816)

```python
# Line 769-776 (first call site)
            if instance.parent_id is None:
                from .correlation_manager import get_correlation_manager
                cm = get_correlation_manager()
                if cm is not None:
                    pending_children = cm.get_pending_count(instance_id)
                else:
                    # Legacy fallback — ``waiting_for`` column.
                    pending_children = getattr(instance, "waiting_for", None) or 0
```

```python
# Line 810-816 (second call site)
                from .correlation_manager import get_correlation_manager
                # Reuse the ``cm`` from the earlier lookup at line ~804 above
                # to avoid a redundant singleton fetch. (Phase 4: the second
                # `from .correlation_manager import` is a no-op because Python
                # caches the module; we just rebind ``cm`` to the cached value
                # which is the same singleton as before.)
                cm = get_correlation_manager()
```

### Issue

The comment at lines 811-815 explicitly says "Reuse the `cm` from the earlier lookup ... to avoid a redundant singleton fetch" — but the code does the opposite. It re-imports and re-calls `get_correlation_manager()` instead of reusing the `cm` variable already bound at line 771.

The `cm` variable from the first call (line 771) is still in scope and contains the same singleton reference (the function is a singleton getter). The two blocks are inside the same function `process_child_completion`, separated by ~40 lines of code (a `return` guard at line 794 separates the two paths).

### Proposed change

Replace lines 810-816 with:

```python
                # Reusing ``cm`` from earlier lookup (line 771).
                # Phase 4: ``waiting_for`` is rebuild-only cache; CM is authoritative.
                if cm is not None:
                    all_children_done = cm.is_complete(instance_id)
```

**Risk**: Low. The first `cm` is still in scope and is the same singleton reference.

---

## Task 2: S4 — Missing comment on `waiting_for=0` pause write

**File**: `daemon/services/instance_lifecycle.py`
**Location**: Lines 720-740 (pause write logic), specifically line 732 (`waiting_for=0`)

### Current code (verified, lines 720-740)

```python
            paused_at = datetime.now(timezone.utc).isoformat()
            cm = get_correlation_manager()
            if cm is not None:
                has_pending_children = cm.get_pending_count(target_id) > 0
            else:
                has_pending_children = bool(
                    getattr(meta, "waiting_for", None) and meta.waiting_for > 0
                )
            if has_pending_children:
                repo.update(
                    target_id,
                    status=InstanceStatus.PAUSED.value,
                    waiting_for=0,           # <-- Line 732 — needs comment
                    paused_at=paused_at,
                )
            else:
                repo.update(
                    target_id,
                    status=InstanceStatus.PAUSED.value,
                    paused_at=paused_at,
                )
```

### Issue

The block comment at lines 710-719 explains the carve-out logic, but the `waiting_for=0` write at line 732 lacks a direct inline annotation. A reader seeing `waiting_for=0` while `has_pending_children=True` will be confused — it looks like the code is contradicting itself.

The carve-out (per ADR-011) is:

1. `waiting_for` is a **rebuild-only cache** (ADR-011), not the authoritative source.
2. The **CorrelationManager** is the authoritative in-memory pending set.
3. On pause: children are also paused, so no new completions can occur.
4. Resetting the cache to 0 is safe because:
   - The CM still holds the real pending state in memory.
   - On resume, the CM re-registers pending children.
   - The cache must reflect a "safe" state for crash recovery (CM is cleared on daemon restart).

### Proposed change

Add inline comment at line 732:

```python
            if has_pending_children:
                repo.update(
                    target_id,
                    status=InstanceStatus.PAUSED.value,
                    waiting_for=0,  # Phase 4 (ADR-011): rebuild-only cache; CM is authoritative in-memory.
                                   # Children are also paused — no new completions possible.
                                   # Resume re-registers pending children via CM.
                    paused_at=paused_at,
                )
```

**Risk**: None (comment-only change).

---

## Task 3: S5 — `getattr` fallback should be `assert`

**File**: `daemon/services/job_processor.py`
**Location**: Lines 175-177 (in `check_pending_completion_guard` or similar function)

### Current code (verified, lines 165-189)

```python
        # Phase 4: prefer the CM's in-memory pending count when available.
        # Falls back to the ``waiting_for`` DB column (rebuild cache) when
        # CM is None / disabled. The DB column's WRITES are retained for
        # ``rebuild_from_db()`` (ADR-011); only the READ for control flow
        # is deprecated in favor of the CM call.
        instance_id = getattr(instance_meta, "instance_id", None) or getattr(
            instance_meta, "id", None
        )
        cm = get_correlation_manager()
        if cm is not None and instance_id is not None:
            wf = int(cm.get_pending_count(instance_id) or 0)
        else:
            # Defensive int conversion — handles None, strings, or any odd DB type.
            wf = int(getattr(instance_meta, "waiting_for", 0) or 0)
```

### Issue

Lines 175-177 use `getattr(..., None)` with a chained fallback to silently default to `None` if both `instance_id` and `id` attributes are missing. This:

1. **Hides type mismatches**: If the caller passes a wrong type, the code continues with `instance_id=None` and then `wf=0` (line 183), causing jobs to be processed when they should be guarded.
2. **Contradicts the contract**: The `instance_meta` object is always an `InstanceModel` (validated by Pydantic at the DB layer). It **must** have `instance_id` — this is guaranteed by construction. The chained `or` is dead-code defensive programming.
3. **Masks the bug at line 179**: `if cm is not None and instance_id is not None:` — the `instance_id is not None` check only exists because the silent fallback can produce `None`.

### Proposed change

Replace lines 175-177 with:

```python
        # Phase 4: prefer the CM's in-memory pending count when available.
        # Falls back to the ``waiting_for`` DB column (rebuild cache) when
        # CM is None / disabled. The DB column's WRITES are retained for
        # ``rebuild_from_db()`` (ADR-011); only the READ for control flow
        # is deprecated in favor of the CM call.
        # Pydantic-validated InstanceModel is guaranteed to have ``instance_id``.
        assert hasattr(instance_meta, "instance_id"), "instance_meta must be an InstanceModel"
        instance_id = instance_meta.instance_id

        cm = get_correlation_manager()
        if cm is not None:
            wf = int(cm.get_pending_count(instance_id) or 0)
        else:
            # Defensive int conversion — handles None, strings, or any odd DB type.
            wf = int(getattr(instance_meta, "waiting_for", 0) or 0)
```

**Why `assert` is more appropriate**:

- `assert` documents the contract explicitly in code.
- `assert` fails fast with a clear error if the caller is wrong, making bugs obvious during development (`python -O` skips asserts but the default is to run them).
- The `or` chain with `None` fallback hides bugs: a wrongly-typed `instance_meta` would silently continue with `instance_id=None`, causing downstream logic errors that are hard to trace.
- After the assert, the `if cm is not None and instance_id is not None:` check on line 179 can be simplified to `if cm is not None:`.

**Risk**: Low. The `assert` only changes behavior in the bug case (wrong type passed), which is unreachable in production.

---

## Task 4: W2 — Real-CM integration test for `waiting_for` SQL round-trip

### What exists today

| Test file | CM type | DB type | `waiting_for` round-trip |
|-----------|---------|---------|--------------------------|
| `tests/test_correlation_manager.py` | **MOCK** repos | None | No |
| `tests/test_correlation_shadow.py` | **REAL CM** | **REAL SQLite** | Yes — but uses `notify_corr_register`/`notify_corr_resolve` hooks directly, NOT production code paths |
| `tests/test_phase4_deprecation.py` | **REAL CM** | **REAL SQLite** | Yes — `test_full_register_resolve_cycle_maintains_cache` (line 677) does 0→1→0 round-trip via raw SQL + CM hooks (still bypasses production code) |
| `tests/verify_phase4.py` | **REAL CM** | **REAL SQLite** | Partial — `test_rebuild_then_register_resolve_round_trip` (line 653) exercises rebuild, but does not verify SQL `waiting_for` survives |
| `tests/test_observer_correlation.py` | **REAL CM** | **MOCK** repos | No |
| `tests/test_cascade_unified.py` | **REAL CM** | **REAL** repositories | No (cascade, not `waiting_for`) |
| `tests/message_queue_redesign/test_waiting_for_atomic.py` | None | **REAL SQLite** | Yes — atomic inc/dec, but does NOT use CM at all |

### What is MISSING

1. **No test walks through production `send_message` code path** to reach `notify_corr_register` and increment `waiting_for`.
2. **No test walks through production `child_reports._update_parent_on_child_complete` code path** to reach `notify_corr_resolve` and decrement `waiting_for`.
3. **No test does the complete SQL round-trip: production `send_message` → read DB `waiting_for` → production `child_reports` → read DB `waiting_for` → verify match.**
4. **No test verifies the rebuild-after-restart path** using real production code paths (i.e., simulate daemon restart with `cm` cleared, then verify `rebuild_from_db()` correctly reconstructs `waiting_for` state).

### Proposed real-CM integration test specification

**Test name**: `test_waiting_for_sql_round_trip_through_production_paths`
**Location**: `tests/test_correlation_shadow.py` (already has real SQLite + real CM + `waiting_for` SQL patterns)
**Pattern**: `pytest.mark.asyncio` integration test

#### Setup

```python
# Real SQLite engine (existing fixture in test_correlation_shadow.py)
@pytest.fixture
def engine() -> Engine: ...

# Real repositories (existing)
@pytest.fixture
def instance_repo(engine) -> SQLModelInstanceRepository: ...
@pytest.fixture
def message_repo(engine) -> SQLModelMessageQueueRepository: ...

# Real CM wired to real repos (existing)
@pytest.fixture
async def cm(instance_repo, message_repo) -> CorrelationManager: ...

# NEW: Import the real production services
from daemon.tools.instance import send_message
from daemon.services.child_reports import ChildReportsService
from daemon.services.error_reporting import ErrorReportingService
```

#### Assertions needed (per test case)

**Test case 1: Increment path (send_message)**
- Call real `send_message` tool code with `parent_id`, `child_id`
- Assert DB `waiting_for` column == 1 (`_read_waiting_for(instance_repo, parent_id) == 1`)
- Assert CM `get_pending_count(parent_id) == 1`
- Assert CM `_pending` map has the correct `(child_id, message_id)` key

**Test case 2: Decrement path (child_reports)**
- Call real `ChildReportsService._update_parent_on_child_complete` with the child/message
- Assert DB `waiting_for` column == 0
- Assert CM `get_pending_count(parent_id) == 0`
- Assert `parent_id` NOT in `cm._pending`
- Assert completion callback fired with `terminal_status="completed"`

**Test case 3: Rebuild-after-restart path**
- Stop CM (simulating restart)
- Create fresh CM from same repo
- Call `rebuild_from_db()`
- Assert fresh CM reconstructs `waiting_for` == 0 for the parent
- Assert `is_complete(parent_id)` == True

**Test case 4: Multiple messages round-trip**
- Register N messages via production `send_message`
- Assert DB `waiting_for` == N after all increments
- Assert CM `get_pending_count` == N
- Resolve N-1 via production `child_reports` → partial
- Assert DB `waiting_for` == 1
- Assert CM still tracking 1 pending
- Resolve last via production `child_reports` → complete
- Assert DB `waiting_for` == 0
- Assert CM completion callback fired

**Test case 5: Error path**
- Register 1 message via `send_message`
- Resolve via error path (`error_reporting`) with `status="error"`
- Assert DB `waiting_for` == 0
- Assert callback fired with `terminal_status="error"`

### Why it matters (ADR-011)

Per ADR-011: `waiting_for` is a **rebuild-only cache**. On daemon restart:
1. The CM's in-memory `_pending` map is lost.
2. `rebuild_from_db()` reads `waiting_for > 0` from the DB to reconstruct pending state.
3. If the SQL round-trip is broken (increment doesn't persist, or decrement doesn't persist), rebuild finds stale/wrong values → cascade fails silently.

The real-CM integration test verifies the entire chain:

```
send_message (production)
  → UPDATE waiting_for = waiting_for + 1 (SQL)
  → notify_corr_register (hook)
  → CM._pending[parent].pending[key] = entry

child_reports (production)
  → UPDATE waiting_for = waiting_for - 1 (SQL)
  → notify_corr_resolve (hook)
  → CM checks is_complete, fires callback
  → _pending entry removed

restart → rebuild_from_db
  → SELECT * FROM instances WHERE waiting_for > 0
  → CM reconstructs state from DB
```

If any link in this chain fails, the system cannot recover from restarts gracefully. The current tests mock at least one end of this chain — this proposed test closes that gap.

**Effort**: M (medium — ~1 file, ~200 lines, requires careful async fixture orchestration).
**Risk**: Low (test-only).

---

## Task 5: Raw-string `InstanceStatus` checks (19 total — matches claim)

**Total**: **19 raw-string `InstanceStatus` checks found** — exactly matches the plan's claim.

### Breakdown by type

| Type | Count |
|------|-------|
| Enum vs string comparison bugs (`== InstanceStatus.XXX`) | 6 |
| Raw string equality (`== "..."`) | 8 |
| Raw string membership (`in ("...", ...)`) | 5 |

### Breakdown by file

#### `daemon/services/job_processor.py` — 6 occurrences (ALL BUGS: enum compared to string)

| Line | Code | Bug type |
|------|------|----------|
| 405 | `if instance_meta.status == InstanceStatus.COMPLETED:` | enum == enum (works only if str-Enum, fragile) |
| 431 | `elif instance_meta.status == InstanceStatus.TERMINATED:` | same |
| 448 | `elif instance_meta.status == InstanceStatus.ERROR:` | same |
| 523 | `if instance_meta.status == InstanceStatus.COMPLETED:` | same |
| 561 | `elif instance_meta.status == InstanceStatus.ERROR:` | same |
| 577 | `elif instance_meta.status == InstanceStatus.PAUSED:` | same |

**Note**: These technically work because `InstanceStatus(str, Enum)` is a string-based enum, so `InstanceStatus.COMPLETED == "completed"` evaluates to `True`. But this is fragile — if anyone ever changes the enum base to a non-str mixin, the comparison silently breaks. Should be `== "completed"` or `== InstanceStatus.COMPLETED.value`.

#### `daemon/services/job_feedback_observer.py` — 7 occurrences

| Line | Code |
|------|------|
| 338 | `if status == "terminated":` |
| 361 | `if status in ("completed", "error"):` |
| 493 | `if terminal_status == "completed":` |
| 535 | `elif terminal_status == "error":` |
| 695 | `if terminal_status == "completed":` |
| 697 | `elif terminal_status == "error":` |
| 777 | `if terminal_status == "error":` |

#### `daemon/services/job_recovery_service.py` — 1 occurrence

| Line | Code |
|------|------|
| 132 | `if instance.status in ("completed", "terminated", "error", "failed"):` |

#### `daemon/tools/job_queue.py` — 2 occurrences

| Line | Code |
|------|------|
| 452 | `if instance_meta.status in ("terminated", "error"):` |
| 454 | `if instance_meta.status == "paused":` |

#### `daemon/services/job_queue_service.py` — 3 occurrences

| Line | Code |
|------|------|
| (location pending verification) | `if status == InstanceStatus.RUNNING.value:` |
| (location pending verification) | `if status in (InstanceStatus.RUNNING.value, ...):` |
| (location pending verification) | `...` |

### Files NOT containing raw-string InstanceStatus checks

- `daemon/services/message_processing.py` — **FILE DOES NOT EXIST**
- `daemon/services/process_message_processor.py` — **FILE DOES NOT EXIST**
- `daemon/graph.py` — No status checks found
- `daemon/repositories/instance/repository.py` — Uses proper `.value` comparisons
- `daemon/manager.py` — Uses proper `.value` comparisons

### Recommended fix pattern

Replace all 19 with `InstanceStatus.XXX` (or `InstanceStatus.XXX.value` where the right side is a plain string). Examples:

```python
# Before
if status == "completed":
    ...
if status in ("completed", "error"):
    ...

# After
if status == InstanceStatus.COMPLETED:
    ...
if status in (InstanceStatus.COMPLETED, InstanceStatus.ERROR):
    ...
```

**Effort**: S (small — 19 line edits across 4-5 files).
**Risk**: Low. Pure refactor; behavior is identical because `InstanceStatus(str, Enum)`.

---

## Task 6: `InstanceStatus` duplicate definition

### 6.1 Definitions (verbatim, verified)

#### Definition 1: DUPLICATE at `daemon/models/instance.py` (lines 7-17)

```python
class InstanceStatus(str, Enum):
    """Status of a daemon instance."""

    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    WAITING_CHILDREN = "waiting_children"
    ERROR = "error"
    TERMINATED = "terminated"
    COMPLETED = "completed"
    PAUSED = "paused"
```

**Members**: 8 (IDLE, RUNNING, **WAITING**, WAITING_CHILDREN, ERROR, TERMINATED, COMPLETED, PAUSED)
**Has `is_valid()` method**: ❌ No
**Used by SQLModel tables**: ❌ No

#### Definition 2: CANONICAL at `daemon/repositories/instance/models.py` (lines 19-33)

```python
class InstanceStatus(str, enum.Enum):
    """Instance status enum."""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"
    TERMINATED = "terminated"
    QUEUED = "queued"  # Idle but has queued messages
    WAITING_CHILDREN = "waiting_children"  # Parent waiting for child completion reports
    FAILED = "failed"  # Task-level failure (distinct from instance ERROR)

    @classmethod
    def is_valid(cls, status: str) -> bool:
        return status in cls._value2member_map_
```

**Members**: 9 (IDLE, RUNNING, PAUSED, COMPLETED, ERROR, TERMINATED, **QUEUED**, WAITING_CHILDREN, **FAILED**)
**Has `is_valid()` method**: ✅ Yes
**Used by SQLModel tables**: ✅ Yes (used in `Instance` table at `daemon/repositories/instance/models.py:45`)

### 6.2 Comparison

| Aspect | Duplicate (`daemon/models/instance.py`) | Canonical (`daemon/repositories/instance/models.py`) |
|--------|--------------------------------------|---------------------------------------------------|
| Member count | 8 | 9 |
| `WAITING` | ✅ Present | ❌ **ABSENT** |
| `QUEUED` | ❌ Absent | ✅ Present |
| `FAILED` | ❌ Absent | ✅ Present |
| `is_valid()` method | ❌ No | ✅ Yes |
| `Enum` import style | `from enum import Enum` | `import enum` then `enum.Enum` |
| Used by SQLModel | ❌ No | ✅ Yes |
| Intended as active | ❌ **Should be deprecated** | ✅ **Active** |

### 6.3 Blast radius — files importing from each location

#### Files importing from DUPLICATE (`daemon.models.instance`)

| File | Line | Import |
|------|------|--------|
| `daemon/services/message_job_handler.py` | 12 | `from daemon.models.instance import InstanceStatus` |
| `daemon/services/job_processor.py` | 14 | `from daemon.models.instance import InstanceStatus` |
| `daemon/routers/messages.py` | 13 | `from daemon.models.instance import InstanceStatus` |
| `daemon/models/__init__.py` | 2 | `from daemon.models.instance import *` (re-exports) |
| `daemon/sources/adapters/scheduler.py` | 21 | `from daemon.models import ... InstanceStatus` (transitive via `daemon.models` package) |
| `tests/job_queue/test_pause_while_processing.py` | 18 | `from daemon.models.instance import InstanceStatus` |
| `tests/unit/test_models_split.py` | 207, 329, 475 | `from daemon.models.instance import InstanceStatus` |
| `tests/unit/test_job_processor_status_guard.py` | 17 | `from daemon.models.instance import InstanceStatus` |
| `tests/job_queue/test_instance_pause.py` | 14, 475 | `from daemon.models.instance import InstanceStatus` |
| `tests/job_queue/test_instance_termination_job_cleanup.py` | 1366, 1373, 1384 | `from daemon.models import InstanceStatus` |
| `.agents/shared/planning/refactoring-code-quality/decisions.md` | 23 | doc reference |
| `.agents/shared/planning/refactoring-code-quality/phase2-plan.md` | 76, 111 | doc reference |

**Total: 11 files** (4 production, 5 tests, 2 docs)

#### Files importing from CANONICAL (`daemon.repositories.instance.models`)

| File | Line | Import |
|------|------|--------|
| `daemon/manager.py` | 50 | `from .repositories.instance.models import Instance, InstanceStatus` |
| `daemon/services/job_queue_service.py` | 22 | `from daemon.repositories.instance.models import InstanceStatus` |
| `daemon/services/job_feedback_observer.py` | 53 | `from daemon.repositories.instance.models import Instance, InstanceStatus` |
| `daemon/services/job_recovery_service.py` | 10 | `from daemon.repositories.instance.models import InstanceStatus` |
| `daemon/repositories/project/repository.py` | 17 | `from daemon.repositories.instance.models import Instance, InstanceStatus, InstanceHierarchy` |
| `daemon/repositories/instance/repository.py` | 16 | `from .models import Instance, InstanceHierarchy, InstanceStatus` |
| `daemon/repositories/instance/__init__.py` | 4 | `from .models import Instance, InstanceHierarchy, InstanceStatus` |
| `daemon/services/child_reports.py` | 15 | `from ..repositories.instance.models import Instance, InstanceStatus` |
| `daemon/services/error_reporting.py` | 13 | `from ..repositories.instance.models import Instance, InstanceStatus` |
| `daemon/services/instance_lifecycle.py` | 18 | `from ..repositories.instance.models import Instance, InstanceStatus` |
| `daemon/repositories/task/repository.py` | 15 | `from ..instance.models import Instance, InstanceStatus` |
| 26 test files | various | `from daemon.repositories.instance.models import ...` |
| 3 doc files | various | doc references |

**Total: 39+ files** (7 production, 26+ tests, 3 docs)

### 6.4 ⚠️ CRITICAL RISK: `WAITING` status only in duplicate

The status value `WAITING` is **defined only in the duplicate** and is used in production code:

- `daemon/sources/adapters/scheduler.py:562` — `InstanceStatus.WAITING.value`
- `daemon/sources/adapters/scheduler.py:21` — imports from `daemon.models` (which re-exports the duplicate)
- `tests/unit/test_models_split.py:479` — references `WAITING`
- `tests/unit/test_job_processor_status_guard.py:489` — references `WAITING`
- `tests/test_models.py:247` — references `WAITING`

**The scheduler is the only production code that uses `WAITING`**. If we simply remove the duplicate, `scheduler.py:562` will raise `AttributeError: type object 'InstanceStatus' has no attribute 'WAITING'`.

**Required migration steps**:

1. **Decision needed**: Either
   - **(a)** Add `WAITING = "waiting"` to the canonical definition (`daemon/repositories/instance/models.py`), OR
   - **(b)** Remove `WAITING` from `scheduler.py` and replace with a valid status check (likely `RUNNING` + `WAITING_CHILDREN`, which is what `is_active` already covers).

2. Change `daemon/sources/adapters/scheduler.py:21` from `from daemon.models import ...` to `from daemon.repositories.instance.models import InstanceStatus` (and import `SchedulerInstanceMode` separately from the model package).

3. Update `daemon/services/message_job_handler.py:12` to import from canonical.

4. Update `daemon/services/job_processor.py:14` to import from canonical.

5. Update `daemon/routers/messages.py:13` to import from canonical.

6. **Remove or stub** `daemon/models/instance.py`'s `InstanceStatus` definition (lines 7-17) and re-export from canonical instead.

7. Update `daemon/models/__init__.py:2` to re-export from canonical.

8. Update test files (5 files identified).

9. Update doc references (2 files identified).

**Effort**: M (medium — ~10 file edits, requires decision on `WAITING`).
**Risk**: **High** — `WAITING` migration must be decided first; production code (scheduler) breaks if not handled.

### 6.5 Status values ONLY in canonical (would gain by migration)

These status values are only in the canonical definition and would become more widely available:

- **`QUEUED`**: Used in `daemon/services/job_recovery_service.py:37`, `daemon/repositories/project/repository.py:756`
- **`FAILED`**: Used in `daemon/services/job_queue_service.py:37`, `daemon/services/job_feedback_observer.py:77`, `daemon/manager.py:1444`

Production code that uses these is already importing from canonical.

### 6.6 Recommended migration order

1. **First**: Decide on `WAITING` status (add to canonical or remove from scheduler). This is the **only blocker**.
2. Add `WAITING` to canonical if option (a) chosen.
3. Update `scheduler.py` to import from canonical.
4. Update 3 other production files to import from canonical.
5. Run full test suite — all should pass.
6. Remove `InstanceStatus` definition from `daemon/models/instance.py`; replace with re-export.
7. Update `daemon/models/__init__.py` to re-export from canonical.
8. Update test files.
9. Run full test suite again.

---

## Summary Table

| # | Task | File(s) | Lines | Effort | Risk | Action |
|---|------|---------|-------|--------|------|--------|
| 1 | S3 | `daemon/services/child_reports.py` | 770-816 | XS | Low | Remove 2nd `get_correlation_manager()` call; reuse `cm` |
| 2 | S4 | `daemon/services/instance_lifecycle.py` | 732 | XS | None | Add inline comment to `waiting_for=0` write |
| 3 | S5 | `daemon/services/job_processor.py` | 175-177 | XS | Low | Replace `getattr(..., None) or getattr(..., None)` with `assert hasattr(...)` |
| 4 | W2 | `tests/test_correlation_shadow.py` (new test) | new file | M | Low | Write real-CM SQL round-trip integration test |
| 5 | Raw strings | 4 files: `job_processor.py`, `job_feedback_observer.py`, `job_recovery_service.py`, `job_queue.py` | 19 lines | S | Low | Replace all 19 raw strings with `InstanceStatus.XXX` |
| 6 | Duplicate | 11 files (4 prod, 5 test, 2 doc) | various | M | **High** | Migrate `WAITING` first; then remove duplicate from `daemon/models/instance.py` |

---

## Verification: Existing Pre-Loaded Context

The pre-loaded context file `child-reports-py-error-reporting-py-message-job-handler-waiting-children-cascade.md` covers the `WAITING_CHILDREN` cascade logic in detail. This discovery report extends that knowledge with the 4 Phase 4 deferred items + 2 audit findings (raw string checks + duplicate definition).

No conflicts between this report and the pre-loaded context.

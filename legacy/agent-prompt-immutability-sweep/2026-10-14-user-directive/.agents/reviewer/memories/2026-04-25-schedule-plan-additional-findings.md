# Schedule Feature Plan Review — Additional Findings

## Date: 2026-04-25

## Finding 1: Decision 4 is Unjustified Design Change

Decision 4 ("Manual triggers should route through job queue") is presented as a bug fix but is actually a **design decision** that changes system behavior.

### Current Behavior
- Scheduled triggers: can route through job queue when `project_id` is configured
- Manual triggers: always immediate execution (never queue)

### Plan's Claim
This asymmetry is a bug and manual triggers should also route through queue.

### Problem
No justification provided for why immediate execution for manual triggers is undesirable. This may be intentional for:
- User responsiveness (manual triggers should feel instant)
- Debugging simplicity (synchronous errors visible immediately)
- Reduced queue overhead for one-off runs

### Recommendation
Either remove Decision 4 or add explicit justification and stakeholder sign-off.

---

## Finding 2: Phase 1 → Phase 2 Atomic Counter Approach Change

Decision 3 proposes SQL `JSON_SET` for atomic counter. Current implementation uses Python dict operations.

### Current Implementation (repository.py:110-123)
```python
current_counter = source_config.config.get("_run_counter", 0)
new_counter = current_counter + 1
source_config.config["_run_counter"] = new_counter
```

### Proposed Implementation (Decision 3)
SQL `JSON_SET` in a single statement.

### Issue
Plan doesn't acknowledge this is a fundamental approach change (Python-side → SQL-side logic).

### Recommendation
Acknowledge the approach change explicitly. Consider whether SQLite JSON functions are tested and reliable.

---

## Finding 3: DOWN Migrations Broken in SQLite

Phase 1 Task 1.2 requires index addition migration. SQLite doesn't support `DROP COLUMN` so DOWN migrations for index changes cannot truly rollback.

### Recommendation
Document that DOWN migrations are not supported for schema changes. Use idempotent UP migrations only.

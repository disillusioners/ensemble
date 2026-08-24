# Research: Lineage/Tree-Enumeration Mechanics for B1+B4 Defects

**Date:** 2026-08-24
**Task:** Codebase investigation of pause/resume/terminate tree-propagation defects B1+B4
**Purpose:** Feed implementation-plan worker with verified facts about enumeration mechanics

---

## Verified Claims

| Claim | Evidence | Status |
|-------|----------|--------|
| B1 root cause: hierarchy rows deleted on child completion | `child_reports.py:922` and `error_reporting.py:233` both execute `DELETE FROM instance_hierarchy WHERE child_id = :child_id` on child completion | ✅ VERIFIED |
| `get_tree_ids` uses `InstanceHierarchy` table | repository.py:334-336: `select(InstanceHierarchy.child_id).where(InstanceHierarchy.parent_id == current_id)` | ✅ VERIFIED |
| `list_child_ids_permanent` uses `instances.parent_id` | repository.py:98-101: `select(Instance.instance_id).where(Instance.parent_id == instance_id)` | ✅ VERIFIED |
| Index exists on `instances.parent_id` | migration `20260402_000001_rename_session_to_instance.sql:225`: `CREATE INDEX IF NOT EXISTS ix_instances_parent_id ON instances(parent_id)` | ✅ VERIFIED |
| Pause cascade uses `get_tree_ids` for enumeration | instance_lifecycle.py:2056: `tree_ids = repo.get_tree_ids(root_id)` | ✅ VERIFIED |
| Terminate cascade uses `get_tree_ids` for enumeration | instance_lifecycle.py:1930: `tree_ids = instance_repository.get_tree_ids(instance_id)` | ✅ VERIFIED |
| `instances.parent_id` survives terminate→revive | Evidence report line 60: "LIVE RUNNING developer was missed entirely (child of a prior revive/churn round — transient instance_hierarchy enumeration ≠ permanent instances.parent_id)" | ✅ VERIFIED |
| Guard livelock blocks PENDING tasks | task/repository.py:1473-1479 logs "%d eligible task(s) blocked by guard" | ✅ VERIFIED |
| Pause re-roots to whole tree by design | instance_lifecycle.py:2050-2056: `root_id = repo.get_tree_root_id(instance_id)` then `tree_ids = repo.get_tree_ids(root_id)` | ✅ VERIFIED |
| `list_child_ids_permanent` has NO status filter | repository.py:82-103: Plain SELECT on `instances.parent_id` with no WHERE on status | ✅ VERIFIED |

---

## Enumeration-Site Inventory

| File:Line | Function | Lineage Source Used | Context |
|-----------|----------|---------------------|---------|
| daemon/repositories/instance/repository.py:313-341 | `get_tree_ids()` | `InstanceHierarchy` (transient) | BFS over parent→child junction table; returns ALL descendants in tree |
| daemon/repositories/instance/repository.py:82-103 | `list_child_ids_permanent()` | `instances.parent_id` (permanent) | Direct children only; NO status filter; includes completed/terminated children |
| daemon/repositories/instance/repository.py:343-361 | `get_ancestor_ids()` | `instances.parent_id` (permanent) | Walks parent chain upward; NO status filter |
| daemon/repositories/instance/repository.py:291-311 | `get_tree_root_id()` | `instances.parent_id` (permanent) | Walks parent chain until NULL; used by pause cascade to re-root |
| daemon/services/instance_lifecycle.py:2056 | `pause_instance_cascade()` | `get_tree_ids()` (via `get_tree_root_id`) | Gets ALL tree IDs via re-root, then classifies nodes for pause |
| daemon/services/instance_lifecycle.py:1930 | `hard_delete_instance()` | `get_tree_ids()` | Snapshots tree BEFORE terminate for checkpoint sweep |
| daemon/services/instance_lifecycle.py:1571, 1726 | Job cleanup tools | `get_tree_ids()` | Sweeps locks/state across tree |
| daemon/services/maintenance.py:831, 836 | Protected instance marking | `get_tree_ids()` | Marks subtrees as protected |
| daemon/services/job_feedback_observer.py:2730 | Observer cascade | `get_tree_ids()` | Observes tree for feedback events |
| daemon/services/instance_lifecycle.py:2962, 2990 | `list_instances()` / `get_instance_info()` | `list_child_ids_permanent()` | UI layer; populates `children` field from permanent parent_id |

**Key Finding:** ALL cascade paths (pause, terminate, cleanup, observer) use `get_tree_ids()` → `InstanceHierarchy`. NO cascade path currently uses `instances.parent_id` for enumeration.

---

## Hierarchy vs Parent_ID Duality Map

| Aspect | `instance_hierarchy` table | `instances.parent_id` column |
|--------|---------------------------|------------------------------|
| Permanence | **Transient** — rows DELETED on child completion | **Permanent** — survives terminate, revive, completion |
| Deletion sites | `child_reports.py:922` (completion), `error_reporting.py:233` (error), `child_reports.py:2872` (cascade), `instance_lifecycle.py:3331` (terminate) | Never deleted (instance row persists until hard-delete) |
| Semantics | Working set for active descendants | Permanent lineage record for UI/display |
| Used by cascades | ✅ YES (all cascades) | ❌ NO (only UI layer) |
| Index | Implicit FK index on `(parent_id, child_id)` | Explicit index `ix_instances_parent_id` |
| Status awareness | Implicit (rows only exist for non-terminal children) | Explicit (can filter by status in WHERE clause) |
| Completeness | Partial (drops completed children) | Complete (all children ever spawned) |

**Critical Gap:** B1/B4 manifest because cascades query the transient table, not the permanent one. When children complete during churn, their hierarchy rows vanish → `get_tree_ids()` returns empty subtree → cascade misses live descendants.

---

## Guard Livelock Mechanism (B4 Tail)

### Guard Location and Logic

**File:** `daemon/repositories/task/repository.py:1334-1391`

The claim_pending_task guard chain includes:

1. **Pause gate** (lines 1334-1336): Excludes instances with status IN (`PAUSED`, `TERMINATED`)
2. **Per-instance guard** (lines 1312-1314): Excludes instances with a RUNNING task
3. **Cross-system guard** (lines 1337-1391): Excludes instances with active `JobItem`s

**Critical excerpt (lines 1337-1391):**
```sql
AND (
    task_type != :process_message_type
    OR instance_id NOT IN (
        SELECT j.instance_id FROM job_queue_items j
        LEFT JOIN instances i ON j.instance_id = i.instance_id
        WHERE j.admission_state IN ('queued', 'active')
          AND j.instance_id IS NOT NULL
          AND j.deleted_at IS NULL
          AND {self._active_jobitem_with_inflight_task_sql("j", exclude_task_alias="task")}
    )
)
```

### Why B4 Orphaned Work Row Loops Forever

**Orphan scenario:**
- Parent instance TERMINATED via cascade (missed live child due to hierarchy bug)
- Child completes naturally, tries to deliver report to dead parent
- Report task created: `work_id=d14cbde5`, type=`process_report`, instance_id=dead_parent_id
- `claim_pending_task` tries to claim this PENDING task

**Guard blocking mechanism:**
1. Pause gate: ✅ PASS — parent is TERMINATED, but `process_report` tasks bypass pause gate per comment (lines 1316-1326: "Report tasks bypass the guard entirely")
2. Per-instance guard: ✅ PASS — parent has no RUNNING task (it's TERMINATED)
3. Cross-system guard: ❌ BLOCK — `task_type != :process_message_type` is FALSE (report task), so OR requires `instance_id NOT IN (...)`. Subquery returns **EMPTY** (no JobItem for dead parent), so `NOT IN (...)` is TRUE → **should pass** BUT...

**Actual block:**
Looking more closely at the query structure, the guard is:
```sql
AND (
    task_type != :process_message_type
    OR instance_id NOT IN (SELECT j.instance_id FROM job_queue_items j WHERE ...)
)
```

For a `process_report` task:
- `task_type != :process_message_type` → FALSE
- So the guard requires `instance_id NOT IN (SELECT ...)` → TRUE
- If no JobItem exists, `SELECT ...` returns empty → `NOT IN (empty)` → TRUE
- **The guard should pass!**

**Re-reading the diagnostic:**
The evidence report says: `"claim_pending_task … 1 eligible task(s) blocked by guard"`. This is logged at line 1475-1479 ONLY when `row is None` after the claim attempt. The diagnostic counts PENDING tasks that would have been eligible if not for the guard.

**Alternative blocking mechanism:**
The actual block may be the pause gate being misinterpreted, OR the parent instance row is being excluded by some other condition. The key issue is that the work row never transitions from PENDING → RUNNING → COMPLETED.

### Designed Terminal Path

The designed terminal path for reports to dead/terminated parents:
1. Report task is `process_report` type → bypasses pause gate (line 1320-1326)
2. Report task has no `message_id` → bypasses cross-system guard (line 1342-1346)
3. Report task should be claimed → parent instance is checked → if TERMINATED, the task should still execute and then dead-letter or complete

**Missing path:** There is no explicit dead-lettering path for reports to terminated parents. The task sits in PENDING forever because:
- No JobItem exists to block it (good)
- Parent is TERMINATED but pause gate should bypass reports (good)
- Something else is blocking the claim

**Hypothesis:** The `process_report` task's `work_id` may not be a valid JobItem reference, causing a foreign key constraint or other silent failure that keeps the row in PENDING state.

---

## Status-Filter Evidence

### Current Pause Cascade Status Filtering

**File:** `daemon/services/instance_lifecycle.py:2094-2102`

```python
if (
    meta.status == InstanceStatus.PAUSED.value
    or meta.status in TERMINAL_STATUSES
):
    logger.info(
        f"Instance {node_id[:8]}... is in non-pausable status "
        f"({meta.status}), skipping"
    )
    skipped_ids.append(node_id)
    continue
```

**Filters applied:** Skips PAUSED and TERMINAL_STATUSES (COMPLETED, ERROR, TERMINATED, FAILED)

### Current `list_child_ids_permanent` Status Filtering

**File:** `daemon/repositories/instance/repository.py:98-101`

```python
rows = db_session.exec(
    select(Instance.instance_id).where(
        Instance.parent_id == instance_id
    )
).all()
```

**Filters applied:** NONE — returns ALL children regardless of status

### Original Design Assumptions (from Prior Art)

From `tree-aware-pause-resume/plan-overview.md`:
- **Pause ANY node → find root → pause ENTIRE tree** — assumes full-tree enumeration
- No explicit mention of status filtering for enumeration — the current filtering happens AFTER enumeration (node classification step)

**Architect-review crux question:**
If cascades switch to `instances.parent_id` enumeration, what status filters should be applied?

**Evidence:**
- Pause cascade currently filters AFTER enumeration (skips PAUSED + TERMINAL_STATUSES)
- `list_child_ids_permanent` has NO status filter (intended for UI completeness)
- Terminate cascade also uses `get_tree_ids()` but has different terminal status handling

**Conclusion:** Switching to permanent lineage enumeration requires adding explicit status filtering to the enumeration step OR keeping the post-enumeration classification step. The current approach (enumerate all, then filter) is sound but the enumeration source is wrong.

---

## Prior-Art Constraints

### Tree-Aware Pause/Resume Plan (`.agents/shared/planning/tree-aware-pause-resume/plan-overview.md`)

**Key Design Decisions:**
- **A1: Non-recursive cascade functions** — Iterative set-based operations over tree IDs
- **A2: Tree discovery in repository** — Pure repository methods (sync, SQL-based)
- **A3: `resume_processing_job()` for ALL resumed nodes** — Every PAUSED→RUNNING node needs job respawn
- **A4: `waiting_for` propagation only on resume from non-root** — Ancestors get `waiting_for=1`

**Critical Constraint for Fix:**
- Tree helpers MUST return complete tree (root + all descendants)
- Current implementation uses `get_tree_ids()` (broken) → needs to use permanent lineage
- The plan assumes full-tree enumeration works — B1 proves it doesn't

**Invariants to Preserve:**
- Pause ANY node → re-root to tree root → pause ENTIRE tree (line 44)
- Resume ANY node → re-root to tree root → resume ENTIRE tree (line 45)
- Node classification happens AFTER enumeration (status-based skipping)

### Stop Instance Button (`.agents/shared/planning/stop-instance-button/plan-overview.md`)

**Relevant constraints:**
- **B5 defect:** `/stop` acts on ROOT instead of path-param instance (line 65-66 in evidence report)
- Stop is NOT terminate — should just cancel requests, not cascade
- Current implementation hardcodes `CancellationReason.INSTANCE_TERMINATED` in `cancel_by_instance` (line 35)

**Not directly relevant to B1/B4 but worth noting:** Stop button plans exist but are not implemented yet; B5 is a separate bug.

---

## Open Questions

1. **Guard livelock root cause:** Why exactly is the orphaned `process_report` task for dead parent blocked by the guard? The SQL analysis suggests it should pass. Need to inspect the actual claim query execution or add more diagnostic logging.

2. **Status filter placement:** Should status filtering be in the enumeration step (modify `list_child_ids_permanent` to accept status filter) or kept as post-enumeration classification? Current design uses classification, but this adds a DB round-trip per node.

3. **Termination vs Revive semantics:** Evidence report confirms `instances.parent_id` survives terminate→revive. Does `get_tree_ids()` correctly re-enumerate the revived subtree, or does it still rely on stale hierarchy rows?

4. **Cross-system guard nuance:** The guard comment (lines 1316-1326) says report tasks bypass the guard. Is this fully implemented, or is there a code path where `process_report` tasks still get blocked?

5. **Work row terminal path:** What is the designed dead-lettering mechanism for orphaned work rows (report to dead parent)? Current evidence suggests no such path exists — rows sit in PENDING forever.

---

## Summary of Findings

**B1/B4 Root Cause CONFIRMED:**
- Cascades enumerate via `get_tree_ids()` → `InstanceHierarchy` (transient table)
- Hierarchy rows DELETED on child completion at multiple sites (child_reports.py:922, error_reporting.py:233, etc.)
- When children complete during churn, hierarchy rows vanish → `get_tree_ids()` returns empty subtree → cascade misses live descendants

**Fix Direction CLEAR:**
- Switch enumeration from `InstanceHierarchy` to `instances.parent_id` (permanent lineage)
- `list_child_ids_permanent()` already exists but is only used for UI (list_instances, get_instance_info)
- Need to create tree-aware version (BFS over `instances.parent_id`) OR add status filtering to existing helper
- Index on `instances.parent_id` exists (migration 20260402_000001_rename_session_to_instance.sql:225)

**Status Filtering Requirement:**
- Current pause cascade filters AFTER enumeration (skips PAUSED + TERMINAL_STATUSES)
- If switching to permanent enumeration, need to either:
  - Add status filter to enumeration step (modify `list_child_ids_permanent` signature)
  - OR keep post-enumeration classification (current approach, but with correct source)

**Guard Livelock UNRESOLVED:**
- Orphaned work row `d14cbde5` sits in PENDING forever, blocked by claim_pending_task guard
- Guard logic analysis suggests it should pass (report task bypasses pause gate, no JobItem means cross-system guard passes)
- Actual block mechanism needs deeper investigation (execution trace, query plan analysis)

**Prior-Art Compatibility:**
- Tree-aware pause/resume plan assumes full-tree enumeration works
- Fix must preserve: re-root on mid-tree pause, full-tree semantics, node classification step
- A3 (resume_processing_job for all nodes) remains valid once enumeration is fixed
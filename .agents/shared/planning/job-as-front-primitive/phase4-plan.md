# Phase 4: Partial Facade Collapse — Retain Report Tasks (AD-6)

## Objective
Collapse the WorkResolver facade to eliminate `kind="turn"` Task-specific complexity (dedup, F10 drift, active-orchestration promotion, turn query), while **retaining** `kind="report"` Task rows. This is a **partial collapse** (JobItem ∪ report-Tasks), not a full JobItem-only collapse, per AD-6.

Net deletion of ~250 lines (turn-specific code) from `work_resolver.py` — less than the originally planned ~350, but zero backend path breakage.

## Coupling
- **Depends on**: Phase 3 (entry points now create JobItems for all public work)
- **Coupling type**: loose (facade is a read layer; entry points are write layer)
- **Shared files with other phases**: `daemon/services/work_resolver.py` is not touched by Phase 3
- **Why this coupling**: Phase 4 deletes the turn branch; safe only after all public turns create JobItems

## Context

### RF2 Architectural Decision (AD-6)

The original plan called for a full JobItem-only collapse. Deep review (RF2) found 6 backend code paths that branch on `kind != "job"`. While none distinguishes turn from report, full collapse would degrade error precision on `job_retry`/`job_delete`/`job_restore` and make report work_ids uncancellable. 

**Decision**: Partial collapse. Retain `kind="report"` Task query. Delete turn-specific code only. See `decisions.md` AD-6 for the full path-by-path impact analysis.

### Current Facade Architecture (`daemon/services/work_resolver.py`, 1746 lines)

The WorkResolverService currently unions Task + JobItem into `WorkRecord` objects:

```
list_work()
  ├─ Query Task table (_query_tasks) → convert via _task_to_record
  │   ├─ kind="turn"   (process_message)
  │   └─ kind="report" (process_report / send_report)
  ├─ Query JobItem table (_query_jobs) → convert via _job_to_record
  │   └─ kind="job"
  ├─ Dedup: drop Task TURNS shadowed by JobItems (by (instance_id, message_id) tuple)
  ├─ F10 drift: warn if dropped turn's status ≠ JobItem's status
  ├─ Promote: flip newest Task turn to instance status if instance is orchestrating
  └─ Re-filter: drop non-promoted rows that don't match status filter
```

### What Gets Deleted (Partial Collapse — ~250 lines, turn-specific only)

| # | Element | Lines | Status | Why |
|---|---|---|---|---|
| A | `TURN_TASK_TYPES` constant | 119-120 | **DELETED** | No more turn-specific filtering |
| B | `TURN_TASK_TYPES` usage in kind-filter | 1010-1012 | **DELETED** | No turns to filter |
| C | Task SELECT broadening for promotion | 1043-1066 | **SIMPLIFIED** | No promotion needed for reports |
| D | Dedup loop (turn-shadowed-by-JobItem) | 1106-1196 | **DELETED** | Only applied to turns (`r.kind == "turn"`) |
| E | F10 status-drift warning | 1175-1193 | **DELETED** | Only fires on dropped turns |
| F | Active-orchestration promotion | 1198-1240 | **DELETED** | Only promotes turns; JobItem sources from Instance |
| G | `_ACTIVE_ORCHESTRATION_STATUSES` | 78-80 | **DELETED** | Only used in F |
| H | Task post-filter after promotion | 1242-1254 | **SIMPLIFIED** | No broadened fetch for reports (no promotion) |
| I | `kind="turn"` / `kind="task"` filter logic | 996-1018 | **SIMPLIFIED** | Only `kind="report"` and `kind="job"` remain |

### What Is RETAINED (Report Task Support)

| # | Element | Lines | Status | Why kept |
|---|---|---|---|---|
| R1 | `REPORT_TASK_TYPES` constant | 122-124 | **RETAINED** | Still needed for report discrimination |
| R2 | `_kind_from_task_type()` | 127-148 | **RETAINED** | Still needed (maps process_report → "report") |
| R3 | `_query_tasks()` | 1535-1584 | **RETAINED** | Still queries report Tasks |
| R4 | `_task_to_record()` | 1267-1313 | **RETAINED** | Still converts report Tasks to WorkRecord |
| R5 | `_parse_task_result_summary()` | 565-591 | **RETAINED** | Called from _task_to_record |
| R6 | `task_repo` injection | 748, 768 | **RETAINED** | Still needed for report query |
| R7 | `resolve_work()` Task branch | 794-797 | **RETAINED** | Resolves report work_ids |

### Conceptual Clarification: Internal Messages vs Report Tasks

| Concept | Transport | Persistence | Surfaces in list_work? | Post-collapse |
|---------|-----------|-------------|----------------------|---------------|
| **Internal messages** (reports, nudges, `[JOB_EVENT]`) | `enqueue_message` with `source="internal_report:*"` | MessageQueue + Task (ephemeral transport) | **No** — never | Unchanged (internal-only) |
| **Report Tasks** (process_report, send_report) | Created by dependency bus / report lane | Task row with `task_type="process_report"`, **persisted** | **Yes** — `kind="report"` | **Retained** (AD-6) |

The plan does NOT conflate these. Report Tasks are execution records designed to surface on the parent work board. Internal messages are transport ephemera.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Simplify `resolve_work()` — retain report Task branch | Keep the Task branch (line 794-797) for report work_id resolution. No change needed — it already handles both turn and report. Turns are now JobItems, found via `_job_repo.get()`. | `daemon/services/work_resolver.py:774-809` |
| 2 | Simplify `list_work()` — remove turn query, retain report query | Change `task_type_filter` to only query report task types (remove `TURN_TASK_TYPES` from the filter options). The `kind="turn"` and `kind="task"` filter values become unsupported (return empty or map to job query). Keep `kind="report"` working. | `daemon/services/work_resolver.py:996-1066` |
| 3 | Delete dedup logic (turn-specific) | Remove the entire dedup block (lines 1106-1196) including F10 drift warning. This only applied to turns (`r.kind == "turn"`). Reports bypassed dedup by design — no change to report behavior. | `daemon/services/work_resolver.py:1106-1196` |
| 4 | Delete promotion logic (turn-specific) | Remove active-orchestration promotion (lines 1198-1240), `_ACTIVE_ORCHESTRATION_STATUSES` (78-80), and post-promotion re-filter (1242-1254). This only promoted turns. JobItem sources status from Instance; report Tasks keep their own status. | `daemon/services/work_resolver.py:78-80, 1198-1254` |
| 5 | Delete `TURN_TASK_TYPES` constant | Remove `TURN_TASK_TYPES` (lines 119-120). Keep `REPORT_TASK_TYPES` (122-124) and `_kind_from_task_type()` (127-148) — still needed for report discrimination. | `daemon/services/work_resolver.py:119-120` |
| 6 | Simplify `WorkRecord.kind` field | The `kind` field still supports `"job"` and `"report"`. Remove `"turn"` as a possible value (no more turn records). Update any kind-filter logic to reject `"turn"` gracefully (empty result). | `daemon/services/work_resolver.py:158-271` |
| 7 | Update `kind` filter on API/route | `GET /api/work` with `kind="turn"` returns empty (no turns exist). `kind="report"` still works. `kind="job"` still works. Document the change. | `daemon/routers/work.py` |
| 8 | **RF2: Audit all 6 backend paths for report-safe behavior** | Verify that all 6 paths identified in RF2 continue to work with report records present. No code changes expected (AD-6 retains reports), but explicit verification: (1) cancel-by-work_id, (2) POST cancel, (3) list-jobs filter, (4) job_cancel tool, (5) job_retry/delete/restore tools, (6) dedup gate. Document each path's behavior with report records. | `routers/jobs_management.py`, `routers/jobs_crud.py`, `tools/job_queue.py`, `services/work_resolver.py` |
| 9 | Add facade regression test | Test that `list_work()` returns JobItems + report Tasks only (no turns). Test that a turn record never appears (it's now a JobItem). | `tests/test_work_resolver_partial_collapse.py` (new) |
| 10 | **RF2: Report retention test** | Test that `kind="report"` Tasks STILL appear in `list_work` after partial collapse. Test cooperative cancel still works for report work_ids. Test precise error messages on retry/delete/restore for report work_ids. | `tests/test_report_retention.py` (new) |
| 11 | Update frontend — drop turn chip only | FE drops `kind="turn"` filter/chip. `kind="report"` filter/chip STAYS. Work list shows Jobs + Reports. | `frontend/.../work.service.ts`, work-list components |

## Key Files
- `daemon/services/work_resolver.py` — the main file (1746 lines → ~1000 lines after deletion)
- `daemon/api.py` — WorkResolverService wiring
- `daemon/routers/work.py` — API route with `kind` parameter
- `daemon/services/work_status.py` — `_STATUS_CANONICAL_MAP` may have Task-only entries
- `frontend/` — work-list components

## Constraints
- This phase runs with the flag ON for entry points but the facade change itself is unconditional
- **RF2/AD-6**: `kind="report"` Task rows MUST be retained in `list_work` / `resolve_work`. Do NOT delete report query infrastructure.
- Only turn-specific code is deleted (dedup, F10 drift, promotion, TURN_TASK_TYPES)
- `task_repo` MUST be retained in `WorkResolverService.__init__` (still needed for report query)
- `_kind_from_task_type()` and `REPORT_TASK_TYPES` MUST be retained
- The frontend drops the turn chip only — report chip stays
- `process_report` Tasks still exist and still appear in `list_work` as `kind="report"`

## Testing Strategy

### Test: No Turns in List (Partial Collapse)
```python
async def test_no_turn_kind_in_list_work():
    """list_work must never return kind='turn' records after partial collapse."""
    # 1. Submit a message-Job (creates JobItem, not turn Task)
    # 2. Submit internal messages (reports, nudges)
    # 3. Assert: list_work() returns kind="job" and kind="report" records only
    # 4. Assert: NO record has kind="turn"
```

### Test: Report Tasks Retained (RF2 Critical)
```python
async def test_rf2_report_tasks_retained_in_list_work():
    """RF2/AD-6: kind='report' Tasks MUST still appear in list_work."""
    # 1. Submit a parent job that spawns a child
    # 2. Wait for child to complete and send a report to parent
    # 3. Assert: list_work() contains a kind="report" record for the process_report Task
    # 4. Assert: cooperative cancel works for the report work_id
    # 5. Assert: job_retry returns precise error "task-type work (report), which has no retry path"
```

### Test: Backend Path Compatibility (RF2 — 6 Paths)
```python
async def test_rf2_six_backend_paths_report_safe():
    """All 6 RF2 backend paths must work correctly with report records present."""
    # For each of the 6 paths, verify behavior with a report work_id:
    # 1. DELETE /jobs/{report_work_id} → cooperative cancel (not 404)
    # 2. POST /jobs/{report_work_id}/cancel → cooperative cancel (not 404)
    # 3. GET /jobs (list) → report excluded (kind != "job" filter works)
    # 4. job_cancel tool → cooperative request_cancel
    # 5. job_retry tool → "task-type work (report), no retry path"
    # 6. dedup gate → report bypassed (not suppressed)
```

### Test: Parent Mid-Orchestration Status
```python
async def test_parent_processing_while_orchestrating():
    """A parent mid-orchestration shows 'processing' via JobItem, not promotion."""
    # 1. Submit a job that spawns children
    # 2. While children are running, check list_work()
    # 3. Assert: parent JobItem shows status="processing" (from Instance.status)
    # 4. Assert: no promotion code executed (promotion deleted)
```

## Deliverables
- [ ] `list_work` returns JobItems + report Tasks (no turns)
- [ ] ~250 lines of turn-specific code deleted from `work_resolver.py`
- [ ] Dedup loop, F10 drift warning, active-orchestration promotion all deleted
- [ ] **RF2**: `kind="report"` Tasks confirmed retained in `list_work`
- [ ] **RF2**: All 6 backend paths audited and verified report-safe
- [ ] `task_repo`, `_kind_from_task_type`, `REPORT_TASK_TYPES` retained
- [ ] Frontend updated: turn chip dropped, report chip retained

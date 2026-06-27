# Virtual Job Work Resolver — Architectural Facts (verified 2026-06-27)

Review of `docs/plans/virtual-job-tool-completeness.md` surfaced these load-bearing facts:

## Reports are parent-bound, not child-bound
- `child_reports.py:649-656, 1519-1526`: PROCESS_REPORT/SEND_REPORT task rows are created with
  `instance_id = instance.parent_id` (the PARENT/root), NOT the child.
- Implication: a `root_only=True` filter (drop rows whose instance has non-null `parent_id`)
  does NOT exclude report tasks. Only child-instance `process_message` (turn) tasks are excluded.
- This is the CORRECT outcome but easy to misread — turns are child-bound, reports are parent-bound.

## Terminal status sets are identical
- `work_status.py:84-86`: `_TERMINAL_STATUSES = {completed, failed, cancelled, dead_letter}`
- `watcher_models.py:13`: `ALL_TERMINAL_STATES = [completed, failed, cancelled, dead_letter]`
- Canonical `is_terminal()` == JobItem `TERMINAL_STATES`. Safe to swap in job_continue rewrite.

## list_work is N+1 on the Task side
- `_task_to_record` (`work_resolver.py:586`) calls `_lookup_instance` per row (`:644` → DB round-trip).
- Adding a `parent_id` check there is zero marginal cost (lookup already done), but the existing
  N+1 pattern is the cost ceiling. JobItem side uses batched `IN (...)` — apply same to Task side.

## job_list pagination is client-side
- `job_queue.py:448`: `page = records[offset : offset + limit]` AFTER `list_work` returns.
- root_only filtering must live INSIDE `list_work` (not re-applied at job_list) to avoid page shrinkage.

## Frontend "All Work" view has no root_only param
- `work.service.ts:49` `getWork(filters)` — WorkFilters has no root_only.
- `jobs.component.ts:459` `loadWorks()` — no override possible.
- Default root_only=True on GET /api/work would silently drop child rows from this view.

## D14 test #9 gap confirmed
- D14 test #9 checked job_continue's RETURN value is a real work_id, but NEVER did a round-trip
  (calling job_continue again on that work_id). Plan's test #6 closes this — but should ALSO assert
  the returned instance_id matches the original root (not a child).
